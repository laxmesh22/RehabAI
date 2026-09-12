import base64
import html
import json
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from backend.access import can_treat, can_view_patient, load_patient
from backend.auth import create_token, current_user, hash_password, new_id, require_roles, verify_password, decode_token
from backend.config import (
    DATABASE_URL, IMU_PLACEMENTS, IMU_REQUIRED, IMU_SERIAL, IMU_TRANSPORT, JWT_SECRET_IS_DEFAULT,
    LLM_BASE_URL, MEASUREMENT_SOURCE, PHONE_FRAME_MAX_BYTES, PHONE_INFERENCE_URL, POSE_KIND,
    POSE_MODEL, VOICE_STT,
)
from backend.database.models import (
    AIReport, Alert, Appointment, Assessment, AuditLog, ClinicalNote, CompensationEvent,
    Consent, ExerciseResult, PainScore, Patient, ROMMeasurement, Recording, RehabPlan,
    Session as RehabSession, SessionMetric, User, utcnow,
)
from backend.database.session import get_db
from backend.services.live_hub import HUB, PhoneFrameRateLimit
from backend.services.persist import persist_finished_session
from edge.imu.device import describe_imu
from edge.overlay import opencv_available
from agent.orchestrator import run_supervisor
from agent.guide_caption import caption_guide
from agent.exercise_coach import adaptive_coach
from agent.knowledge import public_ai_status
from agent.session_intel import record_session_insight, remember_concern
from agent.tools.clinical import (
    calculate_patient_progress, get_approved_exercise_library, get_pain_history, get_rom_history,
    get_compensation_events, get_session_history,
)
from edge.exercises.library import get_exercise
from backend.intake import (
    INTAKE_FIELDS, apply_confirmed_value, empty_intake, merge_finish_intake,
    missing_intake_fields, scores_from_intake, script_payload,
)
from backend.voice import (
    VoiceProviderError, parse_questionnaire_reply, synthesize_speech, transcribe_audio, voice_status,
)
from agent.live_voice import live_reply, sanitize_context
from agent.consumer_voice import consumer_reply, greeting_prompt
from backend.memory import (
    append_report, load_memory, merge_session_into_memory, report_phase_complete,
    save_memory, skip_report_phase, skip_talk_phase,
)
from backend.ocr import run_report_ocr
from agent.retrieval import patient_memory_for_agent
from backend.profile import display_first_name, normalize_profile, profile_complete
from edge.phone_capture import phone_pose_available
from backend.exports import build_patient_record, patient_record_json, patient_record_xlsx

router = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


class PatientBody(BaseModel):
    full_name: str
    mrn: str
    affected_side: str = 'right'
    date_of_birth: str | None = None
    sex: str | None = None
    clinician_diagnosis: str | None = None
    assigned_physio_id: str | None = None
    assigned_doctor_id: str | None = None


class AssessmentBody(BaseModel):
    affected_side: str = 'right'
    flexion_max: float | None = None
    abduction_max: float | None = None
    pain_rest: int = Field(ge=0, le=10)
    pain_movement: int = Field(ge=0, le=10)
    difficulty_dressing: int = Field(default=0, ge=0, le=4)
    difficulty_grooming: int = Field(default=0, ge=0, le=4)
    difficulty_overhead: int = Field(default=0, ge=0, le=4)
    difficulty_behind_back: int = Field(default=0, ge=0, le=4)
    torso_compensation: float | None = None
    avg_confidence: float | None = None
    smoothness: float | None = None
    session_id: str | None = None
    notes: str | None = None


class SessionStartBody(BaseModel):
    patient_id: str
    exercise_id: str = 'shoulder_abduction'
    side: str = 'right'
    target: float = Field(default=80, ge=40, le=150)
    goal: int = Field(default=5, ge=1, le=20)
    pain_before: int | None = Field(default=None, ge=0, le=10)
    consent_recording: bool = False
    kind: str = 'rehab'
    capture: str = 'auto'  # auto | simulation | phone


class IntakeParseBody(BaseModel):
    field: str
    text: str
    awaiting_confirm: bool = False
    language: str = 'en-IN'


class VoiceSpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    language: str = 'en-IN'


class SessionIntakeBody(BaseModel):
    field: str | None = None
    value: int | None = None
    source: str = 'tap'
    transcript: str | None = None
    language: str | None = None
    confirmed: bool | None = None
    pain_rest: int | None = Field(default=None, ge=0, le=10)
    pain_movement: int | None = Field(default=None, ge=0, le=10)
    difficulty_dressing: int | None = Field(default=None, ge=0, le=4)
    difficulty_grooming: int | None = Field(default=None, ge=0, le=4)
    difficulty_overhead: int | None = Field(default=None, ge=0, le=4)
    difficulty_behind_back: int | None = Field(default=None, ge=0, le=4)


class SessionFinishBody(BaseModel):
    pain_after: int = Field(ge=0, le=10)
    create_assessment: bool = False
    pain_rest: int | None = Field(default=None, ge=0, le=10)
    pain_movement: int | None = Field(default=None, ge=0, le=10)
    difficulty_dressing: int | None = Field(default=None, ge=0, le=4)
    difficulty_grooming: int | None = Field(default=None, ge=0, le=4)
    difficulty_overhead: int | None = Field(default=None, ge=0, le=4)
    difficulty_behind_back: int | None = Field(default=None, ge=0, le=4)
    notes: str | None = None


class AgentBody(BaseModel):
    patient_id: str
    question: str


class NoteBody(BaseModel):
    body: str


class FaultBody(BaseModel):
    fault: str


class StaffBody(BaseModel):
    email: str
    full_name: str
    role: str
    password: str


class GuideCaptionBody(BaseModel):
    cue: str = ''
    phase: str | None = None
    safety: str | None = None
    movement: str | None = None


class GuideCoachBody(BaseModel):
    language: str = 'en-IN'
    exercise: str | None = None
    movement: str | None = None
    phase: str | None = None
    safety: str | None = None
    target: float | None = None
    angle: float | None = None
    peak: float | None = None
    reps: int | None = None
    goal: int | None = None
    feedback: str = ''
    patient_said: str = ''
    guide_cue: str = ''
    avatar_demo: str | None = None


def audit(db, user, action, resource, resource_id, detail=None):
    db.add(AuditLog(id=new_id('AUD-'), actor_id=None if user is None else user.id,
                    action=action, resource=resource, resource_id=resource_id, detail=detail or {}))


@router.get('/health')
def health():
    from backend.database.session import engine
    from sqlalchemy import inspect
    tables = []
    try:
        tables = inspect(engine).get_table_names()
    except Exception:
        tables = []
    return {
        'status': 'ok',
        'product': 'RehabAI',
        'source': MEASUREMENT_SOURCE,
        'live_available': MEASUREMENT_SOURCE == 'live',
        'llm_available': bool(LLM_BASE_URL),
        'security': {'jwt_secret_is_default': JWT_SECRET_IS_DEFAULT},
        'database': DATABASE_URL.split(':', 1)[0],
        'tables': sorted(tables),
        'opencv': opencv_available(),
        'frame_encoder': 'opencv' if opencv_available() else 'pillow',
        'pipeline': 'edge.pipeline.VisionPipeline',
        'imu': describe_imu(MEASUREMENT_SOURCE, IMU_TRANSPORT, IMU_SERIAL, IMU_REQUIRED, IMU_PLACEMENTS),
        'sensors': ['realsense', 'arm_imu'],
        'guide': {
            'rig': 'follow_avatar',
            'driven_by': 'telemetry_not_llm',
            'optional_glb': '/ui/models/guide.glb',
            'source_repo': 'hmthanh/3d-human-model',
        },
        'voice': voice_status(),
        'ai': public_ai_status(),
        'phone_pose_available': phone_pose_available(),
        'pose_kind': POSE_KIND,
        'phone_inference_url_configured': bool(PHONE_INFERENCE_URL),
        'consumer_app': True,
    }


@router.post('/guide/caption')
def guide_caption(body: GuideCaptionBody, user: User = Depends(current_user)):
    return caption_guide(body.model_dump())


@router.post('/guide/coach')
async def guide_coach(body: GuideCoachBody, user: User = Depends(current_user)):
    """Adaptive spoken coach from measured session metrics only."""
    return await adaptive_coach(body.model_dump())


@router.post('/auth/login')
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.email == body.email)).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, 'Unknown email or password')
    _record_login(db, user, source='password')
    return {'token': create_token(user), 'user': _user(user)}


class RegisterPatientBody(BaseModel):
    full_name: str | None = None
    email: str | None = None
    password: str | None = None


@router.post('/consumer/bootstrap')
def consumer_bootstrap(body: RegisterPatientBody | None = None, db: Session = Depends(get_db)):
    """Create an empty patient account (no seed ROM/pain). Used by the consumer app."""
    from backend.seed import create_empty_patient_account
    payload = body or RegisterPatientBody()
    try:
        created = create_empty_patient_account(
            db,
            full_name=(payload.full_name or 'New patient').strip() or 'New patient',
            email=(payload.email or '').strip() or None,
            password=(payload.password or '').strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    user = created['user']
    patient = created['patient']
    audit(db, user, 'consumer_bootstrap', 'patient', patient.id, {
        'email': created['email'],
        'source': 'consumer_app',
        'hospital_id': patient.hospital_id,
    })
    _record_login(db, user, source='consumer_bootstrap', patient_id=patient.id)
    db.commit()
    return {
        'token': create_token(user),
        'user': _user(user),
        'patient_id': patient.id,
        'email': created['email'],
        'password': created['password'],
        'is_demo': False,
    }


@router.post('/auth/touch')
def auth_touch(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Record patient app open when JWT is reused (no password re-entry)."""
    patient = None
    if user.role == 'PATIENT':
        patient = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
    now = utcnow()
    previous = user.last_login_at
    should_audit = previous is None or (now - previous).total_seconds() >= 1800
    user.last_login_at = now
    if should_audit:
        detail = {'source': 'app_resume', 'role': user.role}
        if patient is not None:
            detail['patient_id'] = patient.id
        audit(db, user, 'login', 'patient' if patient else 'user',
              patient.id if patient else user.id, detail)
    return {
        'ok': True,
        'last_login_at': user.last_login_at.isoformat() + 'Z',
        'audited': should_audit,
        'patient_id': None if patient is None else patient.id,
    }


@router.get('/me')
def me(user: User = Depends(current_user)):
    return _user(user)


@router.get('/dashboard')
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    patients = _visible_patients(db, user)
    ids = [p.id for p in patients]
    today = utcnow().date()
    sessions = db.scalars(select(RehabSession).where(RehabSession.patient_id.in_(ids))).all() if ids else []
    assessments = db.scalars(select(Assessment).where(Assessment.patient_id.in_(ids))).all() if ids else []
    alerts = db.scalars(select(Alert).where(Alert.acknowledged.is_(False)).order_by(Alert.created_at.desc())).all()
    visible_alerts = [a for a in alerts if a.patient_id is None or a.patient_id in ids]
    completed_today = [s for s in sessions if s.ended_at and s.ended_at.date() == today]
    assessments_today = [a for a in assessments if a.created_at.date() == today]
    review = [a for a in visible_alerts if a.severity in ('warning', 'critical')]
    adherence = []
    for session in sessions:
        if session.status == 'complete' and session.goal:
            adherence.append(min(1.0, session.reps / session.goal))
    focus = _focus_patient(patients, sessions)
    rom = get_rom_history(db, focus.id) if focus else []
    pain_series = get_pain_history(db, focus.id) if focus else []
    progress = calculate_patient_progress(db, focus.id) if focus else {}
    series_sources = progress.get('series_sources') or {}
    abd_source = series_sources.get('abduction')
    flex_source = series_sources.get('flexion')
    abd_series = [row for row in rom if row.get('movement') == 'abduction' and
                  (abd_source is None or row.get('source') == abd_source)]
    flex_series = [row for row in rom if row.get('movement') == 'flexion' and
                   (flex_source is None or row.get('source') == flex_source)]
    ordered = _patients_by_activity(patients, sessions, assessments, db)
    if focus:
        ordered = [focus] + [p for p in ordered if p.id != focus.id]
    consumer_patients = [p for p in patients if p.user_id]
    consumer_logins_today = _consumer_login_events(db, user.hospital_id, since=today)
    return {
        'today_patients': len({s.patient_id for s in sessions if s.started_at.date() == today}),
        'assessments_today': len(assessments_today),
        'sessions_completed_today': len(completed_today),
        'patients_requiring_review': len({a.patient_id for a in review if a.patient_id}),
        'average_adherence': None if not adherence else round(100 * sum(adherence) / len(adherence), 1),
        'consumer_patients': len(consumer_patients),
        'consumer_signups_today': sum(
            1 for p in consumer_patients
            if p.created_at and p.created_at.date() == today
        ),
        'consumer_logins_today': len(consumer_logins_today),
        'recent_patients': [_patient(p, db) for p in ordered[:8]],
        'recent_consumer_logins': consumer_logins_today[:12],
        'focus_patient': None if focus is None else _patient(focus, db),
        'abduction_series': abd_series,
        'flexion_series': flex_series,
        'pain_series': pain_series,
        'progress': progress,
        'demo_records_present': bool(focus and (focus.is_demo or progress.get('demo_records_present'))),
        'series_note': _series_note(focus, rom),
        'alerts': [_alert(a) for a in visible_alerts[:8]],
        'source': MEASUREMENT_SOURCE,
    }


@router.get('/patients')
def list_patients(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_patient(p, db) for p in _visible_patients(db, user)]


@router.post('/patients')
def create_patient(body: PatientBody, user: User = Depends(require_roles('PHYSIOTHERAPIST', 'DOCTOR', 'ADMIN')),
                   db: Session = Depends(get_db)):
    if body.affected_side not in ('left', 'right'):
        raise HTTPException(400, 'affected_side must be left or right')
    if db.scalars(select(Patient).where(Patient.mrn == body.mrn)).first():
        raise HTTPException(400, 'MRN already exists')
    physio = body.assigned_physio_id or (user.id if user.role == 'PHYSIOTHERAPIST' else None)
    patient = Patient(id=new_id('P'), hospital_id=user.hospital_id, mrn=body.mrn, full_name=body.full_name,
                      date_of_birth=body.date_of_birth, sex=body.sex, affected_side=body.affected_side,
                      clinician_diagnosis=body.clinician_diagnosis, assigned_physio_id=physio,
                      assigned_doctor_id=body.assigned_doctor_id or (user.id if user.role == 'DOCTOR' else None),
                      is_demo=False)
    db.add(patient)
    audit(db, user, 'create_patient', 'patient', patient.id)
    db.flush()
    return _patient(patient, db)


@router.get('/patients/{patient_id}')
def get_patient(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patient = load_patient(db, user, patient_id)
    audit(db, user, 'view_patient', 'patient', patient_id)
    progress = calculate_patient_progress(db, patient_id)
    return {
        **_patient(patient, db),
        'progress': progress,
        'recent_activity': _patient_activity(db, patient, limit=12),
    }


@router.get('/patients/{patient_id}/export.json')
def export_patient_json(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patient = load_patient(db, user, patient_id)
    record = build_patient_record(db, patient)
    audit(db, user, 'export_patient_json', 'patient', patient_id)
    return Response(
        content=patient_record_json(record),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="rehabai-{patient.id}.json"'},
    )


@router.get('/patients/{patient_id}/export.xlsx')
def export_patient_xlsx(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patient = load_patient(db, user, patient_id)
    record = build_patient_record(db, patient)
    audit(db, user, 'export_patient_xlsx', 'patient', patient_id)
    return Response(
        content=patient_record_xlsx(record),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="rehabai-{patient.id}.xlsx"'},
    )


@router.post('/patients/{patient_id}/assessment')
def create_assessment(patient_id: str, body: AssessmentBody,
                      user: User = Depends(require_roles('PHYSIOTHERAPIST', 'DOCTOR')),
                      db: Session = Depends(get_db)):
    patient = load_patient(db, user, patient_id)
    if not can_treat(user, patient):
        raise HTTPException(403, 'Not authorised to record assessments')
    source, model, flexion, abduction, compensation, confidence, smoothness = 'manual', 'manual-entry', body.flexion_max, body.abduction_max, body.torso_compensation, body.avg_confidence, body.smoothness
    if body.session_id:
        session = db.get(RehabSession, body.session_id)
        if session is None or session.patient_id != patient_id:
            raise HTTPException(400, 'Session does not belong to this patient')
        source, model = session.source, session.model_version
        if session.exercise_id.endswith('abduction') or session.exercise_id == 'shoulder_abduction':
            abduction = session.peak_angle if abduction is None else abduction
        if 'flexion' in session.exercise_id or session.exercise_id == 'wall_climb':
            flexion = session.peak_angle if flexion is None else flexion
        compensation = compensation if compensation is not None else _session_comp(db, session.id)
        confidence = confidence if confidence is not None else (session.coverage or 0) / 100
    if confidence is not None and confidence < 0.65:
        raise HTTPException(400, 'Measurement confidence is too low to store as an assessment ROM value')
    row = Assessment(
        id=new_id('A-'), patient_id=patient_id, clinician_id=user.id, source=source, model_version=model,
        affected_side=body.affected_side, flexion_max=flexion, abduction_max=abduction,
        avg_confidence=confidence, smoothness=smoothness, torso_compensation=compensation,
        pain_rest=body.pain_rest, pain_movement=body.pain_movement,
        difficulty_dressing=body.difficulty_dressing, difficulty_grooming=body.difficulty_grooming,
        difficulty_overhead=body.difficulty_overhead, difficulty_behind_back=body.difficulty_behind_back,
        notes=body.notes, is_demo=patient.is_demo,
    )
    db.add(row)
    db.add(PainScore(id=new_id('PAIN-'), patient_id=patient_id, assessment_id=row.id,
                     rest=body.pain_rest, movement=body.pain_movement, context='assessment'))
    for movement, value in (('flexion', flexion), ('abduction', abduction)):
        if value is None:
            continue
        db.add(ROMMeasurement(id=new_id('ROM-'), patient_id=patient_id, assessment_id=row.id, movement=movement,
                              value=value, confidence=confidence or 0, valid=True, source=source, model_version=model))
    audit(db, user, 'create_assessment', 'assessment', row.id)
    db.flush()
    return _assessment(row)


@router.get('/patients/{patient_id}/assessments')
def list_assessments(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    rows = db.scalars(select(Assessment).where(Assessment.patient_id == patient_id).order_by(Assessment.created_at.desc())).all()
    return [_assessment(r) for r in rows]


@router.get('/patients/{patient_id}/progress')
def progress(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    return calculate_patient_progress(db, patient_id)


@router.get('/patients/{patient_id}/rom')
def rom_history(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    return get_rom_history(db, patient_id)


@router.get('/patients/{patient_id}/pain')
def pain_history(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    return get_pain_history(db, patient_id)


@router.get('/patients/{patient_id}/compensation')
def compensation_history(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    return get_compensation_events(db, patient_id)


@router.get('/patients/{patient_id}/sessions')
def patient_sessions(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    return get_session_history(db, patient_id)


@router.get('/patients/{patient_id}/recordings')
def recordings(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    rows = db.scalars(select(Recording).where(Recording.patient_id == patient_id).order_by(Recording.created_at.desc())).all()
    return [_recording(r) for r in rows]


@router.get('/patients/{patient_id}/notes')
def notes(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    rows = db.scalars(select(ClinicalNote).where(ClinicalNote.patient_id == patient_id).order_by(ClinicalNote.created_at.desc())).all()
    names = {u.id: u.full_name for u in db.scalars(select(User)).all()}
    return [{'id': r.id, 'author': names.get(r.author_id, r.author_id), 'body': r.body,
             'created_at': r.created_at.isoformat() + 'Z'} for r in rows]


@router.post('/patients/{patient_id}/notes')
def add_note(patient_id: str, body: NoteBody, user: User = Depends(require_roles('PHYSIOTHERAPIST', 'DOCTOR')),
             db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    row = ClinicalNote(id=new_id('NOTE-'), patient_id=patient_id, author_id=user.id, body=body.body)
    db.add(row)
    audit(db, user, 'add_note', 'clinical_note', row.id)
    db.flush()
    return {'id': row.id}


def _mark_abandoned_sessions(db, keep_id: str | None = None):
    for oid in HUB.take_abandoned():
        if oid == keep_id:
            continue
        old = db.get(RehabSession, oid)
        if old is not None and old.status in ('calibrating', 'running'):
            old.status = 'failed'
            old.ended_at = utcnow()


@router.get('/sessions')
def list_sessions(patient_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(RehabSession).order_by(RehabSession.started_at.desc())).all()
    visible = {p.id for p in _visible_patients(db, user)}
    rows = [r for r in rows if r.patient_id in visible and (patient_id is None or r.patient_id == patient_id)]
    return [_session(r) for r in rows]


@router.post('/sessions')
def start_session(body: SessionStartBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patient = load_patient(db, user, body.patient_id)
    if user.role == 'ADMIN':
        raise HTTPException(403, 'Administrators do not start treatment sessions')
    if body.kind not in ('assessment', 'rehab'):
        raise HTTPException(400, 'kind must be assessment or rehab')
    spec = get_exercise(body.exercise_id)
    if not spec['tracking_supported']:
        raise HTTPException(400, spec['name']+' is in the library but camera tracking is not enabled')
    if body.side not in ('left', 'right'):
        raise HTTPException(400, 'Invalid side')
    capture = (body.capture or 'auto').strip().lower()
    if capture not in ('auto', 'simulation', 'phone'):
        raise HTTPException(400, 'capture must be auto, simulation, or phone')
    source = MEASUREMENT_SOURCE
    if capture == 'simulation':
        source = 'simulation'
    elif capture == 'phone':
        if not phone_pose_available():
            raise HTTPException(400, 'Phone capture requires REHABAI_POSE_MODEL or REHABAI_PHONE_INFERENCE_URL')
        source = 'phone'
    elif capture == 'auto' and MEASUREMENT_SOURCE == 'live' and not POSE_MODEL:
        raise HTTPException(400, 'Live capture is configured but REHABAI_POSE_MODEL is unavailable')
    if source == 'phone' and spec.get('movement') not in ('abduction', 'elevation'):
        raise HTTPException(400, 'Phone RGB currently supports frontal-plane abduction/elevation only')
    plan = db.scalars(select(RehabPlan).where(RehabPlan.patient_id == patient.id,
                                             RehabPlan.status.in_(('approved', 'active')))).first()
    if user.role == 'PATIENT' and body.kind == 'rehab':
        allowed = {
            item.get('exercise_id') for item in ((plan.exercises if plan else None) or [])
            if isinstance(item, dict)
        }
        if body.exercise_id not in allowed:
            raise HTTPException(403, 'This exercise is not in the patient approved rehabilitation plan')
    sid = 'SESSION_' + uuid.uuid4().hex[:8].upper()
    row = RehabSession(
        id=sid, patient_id=patient.id, clinician_id=None if user.role == 'PATIENT' else user.id,
        exercise_id=body.exercise_id, side=body.side, source=source, model_version='pending',
        consent_recording=body.consent_recording, target=body.target, goal=body.goal,
        pain_before=body.pain_before, status='calibrating', is_demo=patient.is_demo or source == 'simulation',
        kind='assessment' if body.kind == 'assessment' else 'rehab', intake=None,
    )
    # Consumer assessment sessions can start with memory intake already confirmed.
    if row.kind == 'assessment':
        mem = load_memory(patient.id)
        intake = mem.get('intake')
        if isinstance(intake, dict) and intake.get('confirmed'):
            row.intake = intake
            if isinstance(intake.get('pain_rest'), int):
                row.pain_before = intake['pain_rest']
    db.add(row)
    if body.consent_recording:
        db.add(Consent(id=new_id('CON-'), patient_id=patient.id, kind='recording', granted=True))
    try:
        HUB.start(sid, patient.id, body.exercise_id, body.side, body.target, body.goal,
                  source, body.consent_recording)
    except Exception as exc:
        row.status = 'failed'
        _mark_abandoned_sessions(db, sid)
        raise HTTPException(400, str(exc)) from exc
    _mark_abandoned_sessions(db, sid)
    audit(db, user, 'start_session', 'session', sid, {'exercise': body.exercise_id, 'source': source})
    db.flush()
    return {'id': sid, 'source': source, 'status': 'calibrating', 'kind': row.kind,
            'plan_id': None if plan is None else plan.id, 'exercise': spec, 'intake': row.intake,
            'phone_capture': source == 'phone'}


@router.get('/sessions/{session_id}')
def get_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    live = HUB.latest(session_id)
    return {**_session(row), 'live': live}


@router.get('/intake/script')
def intake_script(_user: User = Depends(current_user)):
    return {**script_payload(), 'voice': voice_status()}


@router.post('/intake/parse')
async def intake_parse(body: IntakeParseBody, _user: User = Depends(current_user)):
    try:
        return await parse_questionnaire_reply(body.text, body.field, body.awaiting_confirm, body.language)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/intake/transcribe')
async def intake_transcribe(audio: UploadFile = File(...), language: str = Form('en-IN'),
                            _user: User = Depends(current_user)):
    data = await audio.read(2_000_001)
    if len(data) > 2_000_000:
        raise HTTPException(400, 'Audio clip is too large')
    result = await transcribe_audio(data, audio.filename or 'utterance.wav', audio.content_type or 'audio/wav', language)
    return {
        'transcript': result.get('transcript') or '',
        'engine': result.get('engine'),
        'empty': not bool(result.get('transcript')),
        'language_code': result.get('language_code'),
        'provider_fallback': bool(result.get('provider_fallback')),
        'provider_error': result.get('provider_error'),
    }


@router.post('/intake/speak')
async def intake_speak(body: VoiceSpeakBody, _user: User = Depends(current_user)):
    try:
        audio, media_type = await synthesize_speech(body.text, body.language, fast=True)
    except VoiceProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(content=audio, media_type=media_type, headers={'Cache-Control': 'no-store'})


@router.post('/voice/agent')
async def voice_agent(
    audio: UploadFile | None = File(default=None),
    text: str = Form(''),
    context: str = Form('{}'),
    language: str = Form('en-IN'),
    patient_id: str = Form(''),
    speak: str = Form('1'),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        raw_ctx = json.loads(context or '{}')
    except json.JSONDecodeError:
        raw_ctx = {}
    ctx = sanitize_context(raw_ctx)
    ctx['language'] = language if language in ('en-IN', 'hi-IN') else 'en-IN'
    stt = {'transcript': (text or '').strip(), 'engine': 'typed', 'provider_fallback': False}
    if audio is not None:
        data = await audio.read(2_000_001)
        if len(data) > 2_000_000:
            raise HTTPException(400, 'Audio clip is too large')
        if data:
            try:
                # Interactive Talk: always single-engine STT (no cascade / offline).
                prefer = 'sarvam' if (VOICE_STT == 'sarvam' or language in ('en-IN', 'hi-IN')) else 'elevenlabs'
                if VOICE_STT == 'elevenlabs':
                    prefer = 'elevenlabs'
                stt = await transcribe_audio(
                    data, audio.filename or 'utterance.wav', audio.content_type or 'audio/wav', language,
                    fast=True, prefer=prefer, allow_offline=False,
                )
            except Exception:
                stt = {
                    'transcript': '',
                    'engine': 'none',
                    'provider_fallback': True,
                    'provider_error': 'stt_exception',
                }
    transcript = (stt.get('transcript') or text or '').strip()
    parsed = None
    pending_value = ctx.get('pending_value')
    if isinstance(pending_value, str) and pending_value.isdigit():
        pending_value = int(pending_value)
    if not isinstance(pending_value, int):
        pending_value = None

    if ctx.get('scene') == 'consumer':
        if not patient_id and user.role == 'PATIENT':
            mine = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
            patient_id = mine.id if mine else ''
        if not patient_id:
            raise HTTPException(400, 'patient_id is required for the consumer voice agent')
        load_patient(db, user, patient_id)
        talk = await consumer_reply(
            transcript,
            patient_id=patient_id,
            language=ctx['language'],
            db=db,
            pending_value=pending_value,
            awaiting_confirm=bool(ctx.get('awaiting_confirm')),
            intake_field=str(ctx['intake_field']) if ctx.get('intake_field') else None,
        )
        parsed = talk.get('parsed')
    else:
        field = ctx.get('intake_field')
        if field and transcript:
            try:
                parsed = await parse_questionnaire_reply(
                    transcript, str(field), bool(ctx.get('awaiting_confirm')), language,
                )
            except KeyError:
                parsed = None
        if parsed and parsed.get('intent') == 'safety_pause':
            talk = {
                'spoken': parsed.get('spoken') or (
                    'Please pause and rest the arm.' if ctx.get('language') != 'hi-IN' else 'रुकिए। हाथ आराम दें।'
                ),
                'action': 'pause',
                'engine': parsed.get('engine') or 'intake-safety',
            }
        elif ctx.get('scene') == 'assistant' and patient_id:
            load_patient(db, user, patient_id)
            memory_slice = patient_memory_for_agent(db, patient_id)
            talk = await live_reply(transcript, ctx, language, memory_slice=memory_slice)
        else:
            memory_slice = None
            if patient_id:
                try:
                    load_patient(db, user, patient_id)
                    memory_slice = patient_memory_for_agent(db, patient_id)
                except HTTPException:
                    memory_slice = None
            talk = await live_reply(transcript, ctx, language, memory_slice=memory_slice)
            if patient_id and talk.get('intent'):
                remember_concern(patient_id, str(talk.get('intent')), transcript)

    spoken = talk.get('spoken') or 'I am listening. This is not a diagnosis.'
    audio_b64 = None
    media_type = None
    tts_engine = 'browser-speech'
    tts_error = None
    # Default Talk TTS is Sarvam Bulbul speaker shubh (Subh). speak=0 skips TTS for tests.
    speak_flag = str(speak if speak is not None else '1').strip().lower()
    want_tts = speak_flag not in ('0', 'false', 'no', 'off', 'browser')
    if want_tts:
        try:
            wav, media_type = await synthesize_speech(spoken, language, fast=True)
            if len(wav) <= 900_000:
                audio_b64 = base64.b64encode(wav).decode()
                tts_engine = 'elevenlabs-george' if media_type == 'audio/mpeg' else 'sarvam-subh'
            else:
                media_type = None
                tts_error = 'Sarvam audio too large; browser speech used'
        except VoiceProviderError as exc:
            media_type = None
            tts_error = str(exc)
    return {
        'transcript': transcript,
        'spoken': spoken,
        'action': talk.get('action') or 'none',
        'engine': talk.get('engine'),
        'demo_target': talk.get('demo_target'),
        'stt_engine': stt.get('engine'),
        'tts_engine': tts_engine if audio_b64 else 'browser-speech',
        'tts_error': tts_error,
        'audio_base64': audio_b64,
        'media_type': media_type,
        'parsed': parsed,
        'provider_fallback': bool(stt.get('provider_fallback')),
        'safety': ctx.get('safety'),
        'intake_field': talk.get('intake_field'),
        'awaiting_confirm': bool(talk.get('awaiting_confirm')),
        'pending_value': talk.get('pending_value'),
        'intake': talk.get('intake'),
        'memory': talk.get('memory'),
        'phase': talk.get('phase'),
        'intent': talk.get('intent'),
        'citations': talk.get('citations') or [],
        'rag_used': bool(talk.get('rag_used')),
        'pacing_hint': talk.get('pacing_hint'),
    }


@router.post('/sessions/{session_id}/intake')
def save_session_intake(session_id: str, body: SessionIntakeBody, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    if row.status in ('complete', 'blocked'):
        raise HTTPException(400, 'Intake cannot be changed after the session is saved')
    intake = row.intake or empty_intake()
    if body.language:
        intake['language'] = body.language
    try:
        if body.field is not None:
            if body.value is None:
                raise HTTPException(400, 'value is required when field is set')
            intake = apply_confirmed_value(intake, body.field, body.value, body.source, body.transcript)
        for fid in INTAKE_FIELDS:
            value = getattr(body, fid)
            if value is None:
                continue
            intake = apply_confirmed_value(intake, fid, value, body.source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.language:
        intake['language'] = body.language
    row.intake = intake
    flag_modified(row, 'intake')
    if isinstance(intake.get('pain_rest'), int):
        row.pain_before = intake['pain_rest']
    audit(db, user, 'save_intake', 'session', row.id, {'confirmed': intake.get('confirmed'), 'source': intake.get('source')})
    db.flush()
    return {'id': row.id, 'intake': row.intake, 'pain_before': row.pain_before, 'kind': row.kind}


@router.get('/sessions/{session_id}/metrics')
def session_metrics(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    metrics = db.scalars(select(SessionMetric).where(SessionMetric.session_id == session_id)).all()
    return [{'id': m.id, 'payload': m.payload} for m in metrics]


@router.get('/sessions/{session_id}/frame')
def session_frame(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    jpeg = HUB.preview_jpeg(session_id)
    if not jpeg:
        raise HTTPException(404, 'No preview frame yet')
    return Response(content=jpeg, media_type='image/jpeg')


@router.post('/sessions/{session_id}/phone-frame')
async def session_phone_frame(
    session_id: str,
    frame: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    if not phone_pose_available():
        raise HTTPException(400, 'Phone capture requires REHABAI_POSE_MODEL or REHABAI_PHONE_INFERENCE_URL')
    if row.source != 'phone':
        raise HTTPException(400, 'Only phone capture sessions can ingest phone frames')
    if (frame.content_type or '').lower() not in ('image/jpeg', 'image/jpg'):
        raise HTTPException(415, 'Phone frame must use image/jpeg')
    data = await frame.read(PHONE_FRAME_MAX_BYTES + 1)
    if not data or len(data) > PHONE_FRAME_MAX_BYTES:
        raise HTTPException(400, f'Phone frame must be a JPEG under {PHONE_FRAME_MAX_BYTES} bytes')
    try:
        telemetry = await run_in_threadpool(HUB.ingest_phone_frame, session_id, data)
    except KeyError as exc:
        raise HTTPException(404, 'Live session is not active') from exc
    except PhoneFrameRateLimit as exc:
        raise HTTPException(429, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        'ok': True,
        'source': 'phone',
        'shoulder_angle': telemetry.get('shoulder_angle'),
        'valid': telemetry.get('valid'),
        'rep': telemetry.get('rep'),
        'safety': (telemetry.get('safety') or {}).get('level'),
        'feedback': telemetry.get('feedback'),
    }


@router.get('/consumer/me')
def consumer_me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != 'PATIENT':
        raise HTTPException(403, 'Consumer home is for the signed-in patient only')
    patient = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
    if patient is None:
        raise HTTPException(404, 'No patient record for this account')
    load_patient(db, user, patient.id)
    progress = calculate_patient_progress(db, patient.id)
    rom = get_rom_history(db, patient.id) or []
    pain = get_pain_history(db, patient.id) or []
    mem = load_memory(patient.id)
    memory = patient_memory_for_agent(db, patient.id)
    profile = normalize_profile(mem.get('profile'))
    intake_row = mem.get('intake') or {}
    intake_done = bool(intake_row.get('confirmed')) or (
        len(missing_intake_fields(scores_from_intake(intake_row))) == 0
        and bool(intake_row.get('fields'))
    )
    report_done = report_phase_complete(mem)
    talk_skipped = bool(mem.get('talk_skipped'))
    profile_done = profile_complete(profile) or intake_done  # legacy unlock
    sessions = get_session_history(db, patient.id) or []
    summaries = list(mem.get('session_summaries') or [])[-10:]
    spoken_name = display_first_name(profile, '')
    display = spoken_name or (user.full_name or '').split(' ')[0] or 'there'
    age = profile.get('age')
    if age is None and patient.date_of_birth:
        try:
            year = int(str(patient.date_of_birth)[:4])
            age = max(0, utcnow().year - year)
        except (TypeError, ValueError):
            age = None
    home_ready = bool(intake_done and report_done)
    phase = (
        'dashboard' if home_ready else
        'report' if intake_done else
        'questionnaire' if profile_done else
        'profile'
    )
    return {
        'patient_id': patient.id,
        'is_demo': bool(patient.is_demo),
        'affected_side': profile.get('affected_side') or patient.affected_side,
        'age': age,
        'profile': {
            'complete': profile_done,
            'full_name': profile.get('full_name') or patient.full_name,
            'age': age,
            'affected_side': profile.get('affected_side') or patient.affected_side,
        },
        'progress': progress,
        'abduction_series': [r for r in rom if r.get('movement') == 'abduction' and (
            (progress.get('series_sources') or {}).get('abduction') is None or
            r.get('source') == (progress.get('series_sources') or {}).get('abduction')
        )],
        'flexion_series': [r for r in rom if r.get('movement') == 'flexion' and (
            (progress.get('series_sources') or {}).get('flexion') is None or
            r.get('source') == (progress.get('series_sources') or {}).get('flexion')
        )],
        'pain_series': pain,
        'memory': memory,
        'session_summaries': summaries,
        'recent_sessions': sessions[:10],
        'intake_complete': intake_done,
        'report_complete': report_done,
        'profile_complete': profile_done,
        'talk_skipped': talk_skipped,
        'home_ready': home_ready,
        'phase': phase,
        'display_name': display,
        'phone_pose_available': phone_pose_available(),
        'greeting': greeting_prompt('en-IN', mem),
        'disclaimer': 'This is not a diagnosis. Voice answers and OCR printouts only.',
    }


@router.post('/consumer/report-ocr')
async def consumer_report_ocr(
    file: UploadFile = File(...),
    cloud_ocr_consent: str = Form('0'),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role != 'PATIENT':
        raise HTTPException(403, 'Only the signed-in patient can upload a report')
    patient = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
    if patient is None:
        raise HTTPException(404, 'No patient record for this account')
    data = await file.read(8_000_001)
    if not data or len(data) > 8_000_000:
        raise HTTPException(400, 'Report image must be under 8 MB')
    consent = cloud_ocr_consent.strip().lower() in ('1', 'true', 'yes', 'on')
    try:
        result = await run_report_ocr(
            data,
            file.filename or 'report.jpg',
            file.content_type or 'image/jpeg',
            allow_cloud=consent,
        )
    except ValueError as exc:
        raise HTTPException(415, str(exc)) from exc
    if not result.get('text') and result.get('cloud_available') and not consent:
        raise HTTPException(
            422,
            'Local OCR could not read this report. Select cloud OCR consent to send this image to the configured Claude service, or skip the report.',
        )
    # Persist structured OCR only — not the raw image binary in SQL.
    report = {
        'engine': result.get('engine'),
        'metrics': result.get('metrics') or {},
        'text_excerpt': (result.get('text') or '')[:500],
        'filename': result.get('filename'),
        'cloud_used': bool(result.get('cloud_used')),
        'disclaimer': result.get('disclaimer'),
    }
    mem = append_report(patient.id, report, phase='done')
    audit(db, user, 'consumer_report_ocr', 'patient', patient.id, {
        'engine': report['engine'], 'cloud_used': report['cloud_used'], 'raw_image_stored': False,
    })
    return {
        'ok': True,
        'report': report,
        'report_phase': mem.get('report_phase'),
        'action': 'open_home',
        'spoken': (
            'Report scanned. Opening your personalized dashboard. This is not a diagnosis.'
            if (report.get('metrics') or {}).get('abduction_deg') is not None
            or (report.get('metrics') or {}).get('pain_score') is not None
            else 'Report saved. Opening your dashboard. Printed numbers were limited or unclear.'
        ),
        'memory': patient_memory_for_agent(db, patient.id),
    }


@router.post('/consumer/report-skip')
def consumer_report_skip(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != 'PATIENT':
        raise HTTPException(403, 'Only the signed-in patient can skip report upload')
    patient = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
    if patient is None:
        raise HTTPException(404, 'No patient record for this account')
    mem = skip_report_phase(patient.id)
    return {
        'ok': True,
        'report_phase': mem.get('report_phase'),
        'action': 'open_home',
        'spoken': 'Skipping report upload. Opening your dashboard. This is not a diagnosis.',
    }


@router.post('/consumer/talk-skip')
def consumer_talk_skip(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Skip Talk intake/report and open the consumer home (sessions / phone OpenCV)."""
    if user.role != 'PATIENT':
        raise HTTPException(403, 'Only the signed-in patient can skip Talk')
    patient = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
    if patient is None:
        raise HTTPException(404, 'No patient record for this account')
    mem = skip_talk_phase(patient.id)
    audit(db, user, 'consumer_talk_skip', 'patient', patient.id, {'source': 'talk_skip'})
    db.commit()
    return {
        'ok': True,
        'action': 'open_home',
        'talk_skipped': True,
        'intake_complete': True,
        'report_complete': report_phase_complete(mem),
        'spoken': 'Skipping Talk. Opening your home dashboard so you can start a session.',
    }


@router.post('/consumer/retalk')
def consumer_retalk(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Re-open clinical Talk without wiping the spoken profile name/age/side."""
    if user.role != 'PATIENT':
        raise HTTPException(403, 'Only the signed-in patient can restart Talk')
    patient = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
    if patient is None:
        raise HTTPException(404, 'No patient record for this account')
    mem = load_memory(patient.id)
    mem['intake'] = empty_intake()
    mem['report_phase'] = 'needed'
    mem['talk_skipped'] = False
    save_memory(patient.id, mem)
    audit(db, user, 'consumer_retalk', 'patient', patient.id)
    db.commit()
    return {
        'ok': True,
        'patient_id': patient.id,
        'phase': 'questionnaire' if profile_complete(normalize_profile(mem.get('profile'))) else 'profile',
    }


@router.post('/sessions/{session_id}/confirm')
def confirm_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    if row.kind == 'assessment':
        intake = row.intake or {}
        if not intake.get('confirmed'):
            raise HTTPException(400, 'Complete pain and function intake before starting the movement trial')
    try:
        HUB.confirm(session_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row.status = 'running'
    return {'id': session_id, 'status': 'running'}


@router.post('/sessions/{session_id}/pause')
def pause_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    try:
        HUB.pause(session_id)
    except KeyError as exc:
        raise HTTPException(404, 'Live session is not active') from exc
    return {'id': session_id, 'status': 'paused'}


@router.post('/sessions/{session_id}/fault')
def session_fault(session_id: str, body: FaultBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    try:
        HUB.set_fault(session_id, body.fault)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'fault': body.fault}


@router.post('/sessions/{session_id}/finish')
def finish_session(session_id: str, body: SessionFinishBody, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    if row.status in ('complete', 'blocked'):
        raise HTTPException(400, 'Session is already saved')
    patient = load_patient(db, user, row.patient_id)
    scores = merge_finish_intake(row.intake, body)
    if body.create_assessment:
        missing = missing_intake_fields(scores)
        if missing:
            raise HTTPException(
                400,
                'Complete pain and function intake before saving an assessment. Missing: ' + ', '.join(missing),
            )
        body = body.model_copy(update=scores)
    else:
        body = body.model_copy(update={key: scores.get(key) for key in INTAKE_FIELDS if scores.get(key) is not None})
    try:
        summary, history = HUB.finish(session_id)
    except KeyError as exc:
        raise HTTPException(404, 'Live session is not active') from exc
    assessment = persist_finished_session(db, row, patient, summary, history, body, user)
    summary_for_memory = dict(summary)
    summary_for_memory['pain_after'] = body.pain_after
    movement = 'flexion' if 'flexion' in (row.exercise_id or '') else 'abduction'
    merge_session_into_memory(patient.id, summary_for_memory, row.id, movement)
    insight = record_session_insight(
        patient.id,
        summary_for_memory,
        pain_after=body.pain_after,
        movement=movement,
        exercise_id=row.exercise_id,
    )
    audit(db, user, 'finish_session', 'session', row.id, {'reps': row.reps, 'source': row.source})
    db.flush()
    return {'session': _session(row), 'summary': summary, 'assessment_id': None if assessment is None else assessment.id,
            'sample_count': len(history), 'insight': insight}


@router.get('/exercises')
def exercises():
    return get_approved_exercise_library()


@router.get('/alerts')
def alerts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = {p.id for p in _visible_patients(db, user)}
    rows = db.scalars(select(Alert).order_by(Alert.created_at.desc())).all()
    return [_alert(a) for a in rows if a.patient_id is None or a.patient_id in ids]


@router.post('/alerts/{alert_id}/ack')
def ack_alert(alert_id: str, user: User = Depends(require_roles('PHYSIOTHERAPIST', 'DOCTOR', 'ADMIN')),
              db: Session = Depends(get_db)):
    row = db.get(Alert, alert_id)
    if row is None:
        raise HTTPException(404, 'Alert not found')
    if row.patient_id:
        load_patient(db, user, row.patient_id)
    row.acknowledged = True
    row.acknowledged_by = user.id
    return _alert(row)


@router.post('/agent/query')
def agent_query(body: AgentBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, body.patient_id)
    result = run_supervisor(db, body.patient_id, body.question, user.role)
    audit(db, user, 'agent_query', 'patient', body.patient_id, {'intent': result.get('intent')})
    return result


@router.get('/reports')
def reports(patient_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = {p.id for p in _visible_patients(db, user)}
    rows = db.scalars(select(AIReport).order_by(AIReport.created_at.desc())).all()
    rows = [r for r in rows if r.patient_id in ids and (patient_id is None or r.patient_id == patient_id)]
    return [_report(r) for r in rows]


@router.get('/reports/{report_id}')
def get_report(report_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(AIReport, report_id)
    if row is None:
        raise HTTPException(404, 'Report not found')
    load_patient(db, user, row.patient_id)
    return _report(row)


@router.get('/reports/{report_id}/preview')
def preview_report(report_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(AIReport, report_id)
    if row is None:
        raise HTTPException(404, 'Report not found')
    patient = load_patient(db, user, row.patient_id)
    return {'html': render_report_html(patient, row)}


@router.post('/reports/{report_id}/approve')
def approve_report(report_id: str, user: User = Depends(require_roles('PHYSIOTHERAPIST', 'DOCTOR')),
                   db: Session = Depends(get_db)):
    row = db.get(AIReport, report_id)
    if row is None:
        raise HTTPException(404, 'Report not found')
    load_patient(db, user, row.patient_id)
    row.approved_by = user.id
    row.approved_at = utcnow()
    row.requires_approval = False
    audit(db, user, 'approve_report', 'ai_report', report_id)
    return _report(row)


@router.get('/plans')
def plans(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    load_patient(db, user, patient_id)
    rows = db.scalars(select(RehabPlan).where(RehabPlan.patient_id == patient_id).order_by(RehabPlan.created_at.desc())).all()
    return [{'id': r.id, 'status': r.status, 'exercises': r.exercises, 'created_by_ai': r.created_by_ai,
             'created_at': r.created_at.isoformat() + 'Z',
             'approved_at': None if r.approved_at is None else r.approved_at.isoformat() + 'Z',
             'notes': r.notes} for r in rows]


@router.post('/plans/{plan_id}/approve')
def approve_plan(plan_id: str, user: User = Depends(require_roles('PHYSIOTHERAPIST', 'DOCTOR')),
                 db: Session = Depends(get_db)):
    row = db.get(RehabPlan, plan_id)
    if row is None:
        raise HTTPException(404, 'Plan not found')
    load_patient(db, user, row.patient_id)
    row.status = 'approved'
    row.approved_by = user.id
    row.approved_at = utcnow()
    audit(db, user, 'approve_plan', 'rehab_plan', plan_id)
    return {'id': row.id, 'status': row.status}


@router.get('/appointments')
def appointments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = {p.id for p in _visible_patients(db, user)}
    rows = db.scalars(select(Appointment).order_by(Appointment.scheduled_for)).all()
    return [{'id': r.id, 'patient_id': r.patient_id, 'clinician_id': r.clinician_id,
             'scheduled_for': r.scheduled_for.isoformat() + 'Z', 'reason': r.reason, 'status': r.status}
            for r in rows if r.patient_id in ids]


@router.get('/staff')
def staff(user: User = Depends(require_roles('ADMIN')), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.hospital_id == user.hospital_id)).all()
    return [_user(u) for u in rows]


@router.post('/staff')
def create_staff(body: StaffBody, user: User = Depends(require_roles('ADMIN')), db: Session = Depends(get_db)):
    if body.role not in ('ADMIN', 'DOCTOR', 'PHYSIOTHERAPIST'):
        raise HTTPException(400, 'Unsupported staff role')
    if db.scalars(select(User).where(User.email == body.email)).first():
        raise HTTPException(400, 'Email already exists')
    row = User(id=new_id('U-'), hospital_id=user.hospital_id, email=body.email, full_name=body.full_name,
               role=body.role, password_hash=hash_password(body.password))
    db.add(row)
    audit(db, user, 'create_staff', 'user', row.id, {'role': body.role})
    db.flush()
    return _user(row)


@router.get('/recordings/{recording_id}')
def get_recording(recording_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Recording, recording_id)
    if row is None:
        raise HTTPException(404, 'Recording not found')
    load_patient(db, user, row.patient_id)
    samples = []
    path = row.telemetry_path
    if path and not path.startswith('seed://'):
        from pathlib import Path
        file = Path(path)
        if file.is_file():
            import json
            samples = json.loads(file.read_text()).get('samples', [])
    return {**_recording(row), 'samples': samples, 'seeded': bool(path and path.startswith('seed://'))}


@router.websocket('/ws/sessions/{session_id}')
async def session_ws(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    import asyncio
    protocols = [item.strip() for item in websocket.headers.get('sec-websocket-protocol', '').split(',') if item.strip()]
    protocol_auth = len(protocols) >= 2 and protocols[0] == 'rehabai'
    credential = protocols[1] if protocol_auth else token
    try:
        payload = decode_token(credential or '')
    except HTTPException:
        await websocket.close(code=4401)
        return
    user = db.get(User, payload.get('sub'))
    row = db.get(RehabSession, session_id)
    patient = None if row is None else db.get(Patient, row.patient_id)
    if user is None or not user.is_active or not can_view_patient(user, patient):
        await websocket.close(code=4403)
        return
    await websocket.accept(subprotocol='rehabai' if protocol_auth else None)
    sent = 0
    try:
        while True:
            try:
                history, next_sequence = HUB.history_since(session_id, sent)
            except KeyError:
                await websocket.send_json({'type': 'ended'})
                break
            for item in history:
                await websocket.send_json(item)
            sent = next_sequence
            await asyncio.sleep(0.08)
    except WebSocketDisconnect:
        return


def _visible_patients(db, user):
    rows = db.scalars(select(Patient).where(Patient.hospital_id == user.hospital_id).order_by(Patient.full_name)).all()
    return [p for p in rows if can_view_patient(user, p)]


def _focus_patient(patients, sessions):
    if not patients:
        return None
    by_id = {p.id: p for p in patients}
    complete = [s for s in sessions if s.status in ('complete', 'blocked') and s.patient_id in by_id]
    complete.sort(key=lambda s: (s.ended_at or s.started_at).isoformat() if (s.ended_at or s.started_at) else '', reverse=True)
    for session in complete:
        patient = by_id.get(session.patient_id)
        if patient is not None and not patient.is_demo:
            return patient
    if complete:
        return by_id.get(complete[0].patient_id) or patients[0]
    return patients[0]


def _patients_by_activity(patients, sessions, assessments, db=None):
    last = {}
    for session in sessions:
        stamp = session.ended_at or session.started_at
        if stamp and (session.patient_id not in last or stamp > last[session.patient_id]):
            last[session.patient_id] = stamp
    for row in assessments:
        stamp = row.created_at
        if stamp and (row.patient_id not in last or stamp > last[row.patient_id]):
            last[row.patient_id] = stamp
    if db is not None:
        for patient in patients:
            if not patient.user_id:
                continue
            account = db.get(User, patient.user_id)
            stamp = None if account is None else account.last_login_at
            if stamp and (patient.id not in last or stamp > last[patient.id]):
                last[patient.id] = stamp
            created = patient.created_at
            if created and (patient.id not in last or created > last[patient.id]):
                last[patient.id] = created

    def key(patient):
        stamp = last.get(patient.id)
        return stamp.isoformat() if stamp is not None else ''

    return sorted(patients, key=key, reverse=True)


def _series_note(patient, rom):
    if patient is None:
        return 'No stored measurements yet.'
    live_n = sum(1 for row in rom if row.get('source') == 'live')
    sim_n = sum(1 for row in rom if row.get('source') == 'simulation')
    if patient.is_demo:
        return 'Demo/synthetic seed plus any later stored sessions. Not a live camera record unless labelled live.'
    if live_n:
        extra = f' · {sim_n} simulation' if sim_n else ''
        return f'{live_n} live stored points{extra}. Not a diagnosis.'
    if sim_n:
        return f'{sim_n} labelled simulation points. Not live sensors.'
    return 'Stored measurements only. Missing values are not invented.'


def _user(user):
    return {
        'id': user.id,
        'email': user.email,
        'full_name': user.full_name,
        'role': user.role,
        'hospital_id': user.hospital_id,
        'last_login_at': None if user.last_login_at is None else user.last_login_at.isoformat() + 'Z',
    }


def _patient(patient, db=None):
    account = None
    if db is not None and patient.user_id:
        account = db.get(User, patient.user_id)
    last_login = None if account is None else account.last_login_at
    return {
        'id': patient.id,
        'mrn': patient.mrn,
        'full_name': patient.full_name,
        'affected_side': patient.affected_side,
        'date_of_birth': patient.date_of_birth,
        'sex': patient.sex,
        'clinician_diagnosis': patient.clinician_diagnosis,
        'assigned_physio_id': patient.assigned_physio_id,
        'assigned_doctor_id': patient.assigned_doctor_id,
        'is_demo': patient.is_demo,
        'user_id': patient.user_id,
        'has_app_login': bool(patient.user_id),
        'account_source': 'consumer' if patient.user_id else 'clinic',
        'email': None if account is None else account.email,
        'created_at': None if patient.created_at is None else patient.created_at.isoformat() + 'Z',
        'last_login_at': None if last_login is None else last_login.isoformat() + 'Z',
    }


def _record_login(db, user, *, source: str, patient_id: str | None = None):
    user.last_login_at = utcnow()
    pid = patient_id
    if pid is None and user.role == 'PATIENT':
        linked = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
        pid = None if linked is None else linked.id
    detail = {'source': source, 'role': user.role}
    if pid:
        detail['patient_id'] = pid
    audit(db, user, 'login', 'patient' if pid else 'user', pid or user.id, detail)


def _consumer_login_events(db, hospital_id: str, since):
    """Recent consumer bootstrap/login events for the hospital dashboard."""
    from datetime import datetime, time
    start = datetime.combine(since, time.min)
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.action.in_(('login', 'consumer_bootstrap')))
        .where(AuditLog.created_at >= start)
        .order_by(AuditLog.created_at.desc())
        .limit(40)
    ).all()
    patients = {
        p.id: p
        for p in db.scalars(select(Patient).where(Patient.hospital_id == hospital_id)).all()
    }
    users = {u.id: u for u in db.scalars(select(User).where(User.hospital_id == hospital_id)).all()}
    out = []
    for row in rows:
        detail = row.detail or {}
        pid = detail.get('patient_id') or (row.resource_id if row.resource == 'patient' else None)
        patient = patients.get(pid) if pid else None
        actor = users.get(row.actor_id) if row.actor_id else None
        if patient is None and actor and actor.role == 'PATIENT':
            patient = next((p for p in patients.values() if p.user_id == actor.id), None)
        if patient is None or patient.hospital_id != hospital_id:
            continue
        if not patient.user_id and row.action != 'consumer_bootstrap':
            continue
        out.append({
            'action': row.action,
            'at': row.created_at.isoformat() + 'Z',
            'patient_id': patient.id,
            'full_name': patient.full_name,
            'mrn': patient.mrn,
            'source': detail.get('source') or row.action,
            'email': None if actor is None else actor.email,
        })
    return out


def _patient_activity(db, patient, limit: int = 12):
    q = (
        select(AuditLog)
        .where(
            (AuditLog.resource_id == patient.id)
            | (
                (AuditLog.actor_id == patient.user_id)
                & (AuditLog.action.in_(('login', 'consumer_bootstrap', 'consumer_retalk', 'consumer_report_ocr')))
            )
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    rows = db.scalars(q).all() if patient else []
    return [
        {
            'action': row.action,
            'resource': row.resource,
            'at': row.created_at.isoformat() + 'Z',
            'detail': row.detail or {},
        }
        for row in rows
    ]


def _assessment(row):
    return {'id': row.id, 'patient_id': row.patient_id, 'created_at': row.created_at.isoformat() + 'Z',
            'source': row.source, 'model_version': row.model_version, 'affected_side': row.affected_side,
            'flexion_max': row.flexion_max, 'abduction_max': row.abduction_max, 'avg_confidence': row.avg_confidence,
            'smoothness': row.smoothness, 'torso_compensation': row.torso_compensation, 'pain_rest': row.pain_rest,
            'pain_movement': row.pain_movement, 'difficulty_dressing': row.difficulty_dressing,
            'difficulty_grooming': row.difficulty_grooming, 'difficulty_overhead': row.difficulty_overhead,
            'difficulty_behind_back': row.difficulty_behind_back, 'is_demo': row.is_demo, 'notes': row.notes}


def _session(row):
    return {'id': row.id, 'patient_id': row.patient_id, 'exercise_id': row.exercise_id, 'side': row.side,
            'started_at': row.started_at.isoformat() + 'Z',
            'ended_at': None if row.ended_at is None else row.ended_at.isoformat() + 'Z',
            'source': row.source, 'model_version': row.model_version, 'reps': row.reps, 'invalid_reps': row.invalid_reps,
            'peak_angle': row.peak_angle, 'coverage': row.coverage, 'target': row.target, 'goal': row.goal,
            'pain_before': row.pain_before, 'pain_after': row.pain_after, 'status': row.status,
            'safety_outcome': row.safety_outcome, 'is_demo': row.is_demo, 'consent_recording': row.consent_recording,
            'kind': row.kind or 'rehab', 'intake': row.intake}


def _alert(row):
    return {'id': row.id, 'patient_id': row.patient_id, 'type': row.type, 'severity': row.severity,
            'message': row.message, 'created_at': row.created_at.isoformat() + 'Z', 'acknowledged': row.acknowledged}


def _recording(row):
    return {'id': row.id, 'patient_id': row.patient_id, 'session_id': row.session_id, 'kind': row.kind,
            'telemetry_path': row.telemetry_path, 'video_path': row.video_path, 'skeleton_path': row.skeleton_path,
            'created_at': row.created_at.isoformat() + 'Z'}


def _report(row):
    return {'id': row.id, 'patient_id': row.patient_id, 'kind': row.kind, 'payload': row.payload,
            'requires_approval': row.requires_approval, 'approved_by': row.approved_by,
            'approved_at': None if row.approved_at is None else row.approved_at.isoformat() + 'Z',
            'created_at': row.created_at.isoformat() + 'Z', 'model': row.model}


def _session_comp(db, session_id):
    rows = db.scalars(select(CompensationEvent).where(CompensationEvent.session_id == session_id)).all()
    if not rows:
        return None
    return max(r.value for r in rows)


def render_report_html(patient, report):
    payload = report.payload or {}
    changes = payload.get('measured_changes') or {}
    rows = ''.join(
        f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>"
        for k, v in changes.items()
    )
    demo = 'DEMO / SYNTHETIC RECORDS INCLUDED' if patient.is_demo or payload.get('demo_records_present') else 'LIVE AND STORED CLINICAL RECORDS'
    return f"""<!doctype html><html><body style="font-family:Georgia,serif;padding:32px;color:#17302c">
    <p style="letter-spacing:2px;font-size:12px">{demo}</p>
    <h1>RehabAI progress report</h1>
    <p>Patient {html.escape(str(patient.full_name))} ({html.escape(str(patient.id))}) · Affected side {html.escape(str(patient.affected_side))}</p>
    <p>{html.escape(str(payload.get('summary','')))}</p>
    <table border="1" cellpadding="8">{rows}</table>
    <p>Recommendation: {html.escape(str(payload.get('recommendation','')))}</p>
    <p>Clinician comments: ______________________________</p>
    <p>Approval: {'Approved' if report.approved_by else 'Pending clinician approval'}</p>
    <p style="font-size:13px">AI-assisted documentation. Not a diagnosis. Camera measurements estimate arm-to-trunk orientation.</p>
    </body></html>"""
