import base64
import json
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from backend.access import can_treat, can_view_patient, load_patient
from backend.auth import create_token, current_user, hash_password, new_id, require_roles, verify_password, decode_token
from backend.config import (
    DATABASE_URL, IMU_PLACEMENTS, IMU_REQUIRED, IMU_SERIAL, IMU_TRANSPORT, JWT_SECRET_IS_DEFAULT,
    LLM_BASE_URL, MEASUREMENT_SOURCE, PHONE_FRAME_MAX_BYTES, POSE_MODEL, VOICE_STT,
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
from agent.tools.clinical import (
    calculate_patient_progress, get_approved_exercise_library, get_pain_history, get_rom_history,
    get_compensation_events, get_session_history,
)
from edge.exercises.library import get_exercise
from backend.intake import (
    INTAKE_FIELDS, apply_confirmed_value, empty_intake, merge_finish_intake,
    missing_intake_fields, script_payload,
)
from backend.voice import (
    VoiceProviderError, parse_questionnaire_reply, synthesize_speech, transcribe_audio, voice_status,
)
from agent.live_voice import live_reply, sanitize_context
from agent.consumer_voice import consumer_reply, greeting_prompt
from backend.memory import (
    append_report, load_memory, merge_session_into_memory, report_phase_complete,
    save_memory, skip_report_phase,
)
from backend.ocr import run_report_ocr
from agent.retrieval import patient_memory_for_agent
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
            'rig': 'mixamo',
            'driven_by': 'telemetry_not_llm',
            'optional_glb': '/ui/models/guide.glb',
            'source_repo': 'hmthanh/3d-human-model',
        },
        'voice': voice_status(),
        'phone_pose_available': phone_pose_available(),
        'consumer_app': True,
    }


@router.post('/guide/caption')
def guide_caption(body: GuideCaptionBody, user: User = Depends(current_user)):
    return caption_guide(body.model_dump())


@router.post('/auth/login')
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.email == body.email)).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, 'Unknown email or password')
    audit(db, user, 'login', 'user', user.id)
    return {'token': create_token(user), 'user': _user(user)}


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
    abd_series = [row for row in rom if row.get('movement') == 'abduction']
    flex_series = [row for row in rom if row.get('movement') == 'flexion']
    ordered = _patients_by_activity(patients, sessions, assessments)
    if focus:
        ordered = [focus] + [p for p in ordered if p.id != focus.id]
    return {
        'today_patients': len({s.patient_id for s in sessions if s.started_at.date() == today}),
        'assessments_today': len(assessments_today),
        'sessions_completed_today': len(completed_today),
        'patients_requiring_review': len({a.patient_id for a in review if a.patient_id}),
        'average_adherence': None if not adherence else round(100 * sum(adherence) / len(adherence), 1),
        'recent_patients': [_patient(p) for p in ordered[:8]],
        'focus_patient': None if focus is None else _patient(focus),
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
    return [_patient(p) for p in _visible_patients(db, user)]


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
    return _patient(patient)


@router.get('/patients/{patient_id}')
def get_patient(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patient = load_patient(db, user, patient_id)
    audit(db, user, 'view_patient', 'patient', patient_id)
    progress = calculate_patient_progress(db, patient_id)
    return {**_patient(patient), 'progress': progress}


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
    source, model, flexion, abduction, compensation, confidence, smoothness = MEASUREMENT_SOURCE, 'manual-entry', body.flexion_max, body.abduction_max, body.torso_compensation, body.avg_confidence, body.smoothness
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
        raise HTTPException(400, str(exc)) from exc
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
    data = await audio.read()
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
        audio, media_type = await synthesize_speech(body.text, body.language)
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
        data = await audio.read()
        if len(data) > 2_000_000:
            raise HTTPException(400, 'Audio clip is too large')
        if data:
            try:
                # Talk must stay interactive: skip slow Windows offline STT on the live path.
                # Consumer uses fast=True (single STT engine) to cut listen→reply latency.
                prefer = 'sarvam' if VOICE_STT == 'sarvam' else 'elevenlabs'
                consumer_fast = str(ctx.get('scene') or '') == 'consumer'
                stt = await transcribe_audio(
                    data, audio.filename or 'utterance.wav', audio.content_type or 'audio/wav', language,
                    fast=consumer_fast, prefer=prefer, allow_offline=False,
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
        if parsed and parsed.get('intent') in ('number', 'confirm_yes', 'confirm_no', 'safety_pause'):
            talk = {
                'spoken': parsed.get('spoken') or transcript,
                'action': 'pause' if parsed.get('intent') == 'safety_pause' else 'none',
                'engine': parsed.get('engine'),
            }
        elif ctx.get('scene') == 'assistant' and patient_id:
            load_patient(db, user, patient_id)
            supervisor = run_supervisor(db, patient_id, transcript or 'Summarise stored progress.', user.role)
            talk = {
                'spoken': (supervisor.get('summary') or '')[:400],
                'action': 'none',
                'engine': 'supervisor+' + ('llm' if supervisor.get('llm_used') else 'deterministic'),
            }
        else:
            talk = await live_reply(transcript, ctx, language)

    spoken = talk.get('spoken') or 'I am RehabAI. This is not a diagnosis.'
    audio_b64 = None
    media_type = None
    tts_engine = 'browser-speech'
    # Consumer Talk uses on-device speech by default — cloud TTS adds seconds per turn.
    want_tts = str(speak or '0').strip().lower() in ('1', 'true', 'yes')
    if ctx.get('scene') == 'consumer' and str(speak or '1').strip() in ('', '1', 'true', 'yes'):
        # Client sends speak=0 for speed; if it still asks for cloud TTS, keep replies short.
        spoken = ' '.join(str(spoken).split())[:180]
    if want_tts:
        try:
            wav, media_type = await synthesize_speech(spoken, language)
            if len(wav) <= 900_000:
                audio_b64 = base64.b64encode(wav).decode()
                tts_engine = 'elevenlabs' if media_type == 'audio/mpeg' else 'sarvam-shubh'
            else:
                media_type = None
        except VoiceProviderError:
            media_type = None
    return {
        'transcript': transcript,
        'spoken': spoken,
        'action': talk.get('action') or 'none',
        'engine': talk.get('engine'),
        'stt_engine': stt.get('engine'),
        'tts_engine': tts_engine if audio_b64 else 'browser-speech',
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
    data = await frame.read()
    if not data or len(data) > PHONE_FRAME_MAX_BYTES:
        raise HTTPException(400, f'Phone frame must be a JPEG under {PHONE_FRAME_MAX_BYTES} bytes')
    try:
        telemetry = HUB.ingest_phone_frame(session_id, data)
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
    intake_done = bool((mem.get('intake') or {}).get('confirmed'))
    report_done = report_phase_complete(mem)
    return {
        'patient_id': patient.id,
        'is_demo': bool(patient.is_demo),
        'affected_side': patient.affected_side,
        'progress': progress,
        'abduction_series': [r for r in rom if r.get('movement') == 'abduction'],
        'flexion_series': [r for r in rom if r.get('movement') == 'flexion'],
        'pain_series': pain,
        'memory': memory,
        'intake_complete': intake_done,
        'report_complete': report_done,
        'phase': (
            'dashboard' if intake_done and report_done else
            'report' if intake_done else
            'questionnaire'
        ),
        'display_name': (user.full_name or '').split(' ')[0] or 'there',
        'phone_pose_available': phone_pose_available(),
        'greeting': greeting_prompt('en-IN', mem),
        'disclaimer': 'This is not a diagnosis. Voice answers and OCR printouts only.',
    }


@router.post('/consumer/report-ocr')
async def consumer_report_ocr(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role != 'PATIENT':
        raise HTTPException(403, 'Only the signed-in patient can upload a report')
    patient = db.scalars(select(Patient).where(Patient.user_id == user.id)).first()
    if patient is None:
        raise HTTPException(404, 'No patient record for this account')
    data = await file.read()
    if not data or len(data) > 8_000_000:
        raise HTTPException(400, 'Report image must be under 8 MB')
    result = await run_report_ocr(data, file.filename or 'report.jpg', file.content_type or 'image/jpeg')
    # Persist structured OCR only — not the raw image binary in SQL.
    report = {
        'engine': result.get('engine'),
        'metrics': result.get('metrics') or {},
        'text_excerpt': (result.get('text') or '')[:500],
        'filename': file.filename or 'report.jpg',
        'disclaimer': result.get('disclaimer'),
    }
    mem = append_report(patient.id, report, phase='done')
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
    audit(db, user, 'finish_session', 'session', row.id, {'reps': row.reps, 'source': row.source})
    db.flush()
    return {'session': _session(row), 'summary': summary, 'assessment_id': None if assessment is None else assessment.id,
            'sample_count': len(history)}


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
                history = HUB.history(session_id)
            except KeyError:
                await websocket.send_json({'type': 'ended'})
                break
            if sent < len(history):
                for item in history[sent:]:
                    await websocket.send_json(item)
                sent = len(history)
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


def _patients_by_activity(patients, sessions, assessments):
    last = {}
    for session in sessions:
        stamp = session.ended_at or session.started_at
        if stamp and (session.patient_id not in last or stamp > last[session.patient_id]):
            last[session.patient_id] = stamp
    for row in assessments:
        stamp = row.created_at
        if stamp and (row.patient_id not in last or stamp > last[row.patient_id]):
            last[row.patient_id] = stamp

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
    return {'id': user.id, 'email': user.email, 'full_name': user.full_name, 'role': user.role,
            'hospital_id': user.hospital_id}


def _patient(patient):
    return {'id': patient.id, 'mrn': patient.mrn, 'full_name': patient.full_name, 'affected_side': patient.affected_side,
            'date_of_birth': patient.date_of_birth, 'sex': patient.sex, 'clinician_diagnosis': patient.clinician_diagnosis,
            'assigned_physio_id': patient.assigned_physio_id, 'assigned_doctor_id': patient.assigned_doctor_id,
            'is_demo': patient.is_demo}


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
    rows = ''.join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in changes.items())
    demo = 'DEMO / SYNTHETIC RECORDS INCLUDED' if patient.is_demo or payload.get('demo_records_present') else 'LIVE AND STORED CLINICAL RECORDS'
    return f"""<!doctype html><html><body style="font-family:Georgia,serif;padding:32px;color:#17302c">
    <p style="letter-spacing:2px;font-size:12px">{demo}</p>
    <h1>RehabAI progress report</h1>
    <p>Patient {patient.full_name} ({patient.id}) · Affected side {patient.affected_side}</p>
    <p>{payload.get('summary','')}</p>
    <table border="1" cellpadding="8">{rows}</table>
    <p>Recommendation: {payload.get('recommendation','')}</p>
    <p>Clinician comments: ______________________________</p>
    <p>Approval: {'Approved' if report.approved_by else 'Pending clinician approval'}</p>
    <p style="font-size:13px">AI-assisted documentation. Not a diagnosis. Camera measurements estimate arm-to-trunk orientation.</p>
    </body></html>"""
