import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from backend.access import can_treat, can_view_patient, load_patient
from backend.auth import create_token, current_user, hash_password, new_id, require_roles, verify_password, decode_token
from backend.config import DATABASE_URL, DEMO_PASSWORD, LLM_BASE_URL, MEASUREMENT_SOURCE
from backend.database.models import (
    AIReport, Alert, Appointment, Assessment, AuditLog, ClinicalNote, CompensationEvent,
    Consent, ExerciseResult, PainScore, Patient, ROMMeasurement, Recording, RehabPlan,
    Session as RehabSession, SessionMetric, User, utcnow,
)
from backend.database.session import get_db
from backend.services.live_hub import HUB
from backend.services.persist import persist_finished_session
from edge.overlay import opencv_available
from agent.orchestrator import run_supervisor
from agent.tools.clinical import (
    calculate_patient_progress, get_approved_exercise_library, get_pain_history, get_rom_history,
    get_compensation_events, get_session_history,
)
from edge.exercises.library import get_exercise

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
    pain_before: int = Field(default=0, ge=0, le=10)
    consent_recording: bool = False
    kind: str = 'rehab'


class SessionFinishBody(BaseModel):
    pain_after: int = Field(default=0, ge=0, le=10)
    create_assessment: bool = False
    pain_rest: int | None = Field(default=None, ge=0, le=10)
    difficulty_dressing: int = 0
    difficulty_grooming: int = 0
    difficulty_overhead: int = 0
    difficulty_behind_back: int = 0
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
        'demo_password_hint': DEMO_PASSWORD,
        'database': DATABASE_URL.split(':', 1)[0],
        'tables': sorted(tables),
        'opencv': opencv_available(),
        'frame_encoder': 'opencv' if opencv_available() else 'pillow',
        'pipeline': 'edge.pipeline.VisionPipeline',
    }


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
    return {
        'today_patients': len({s.patient_id for s in sessions if s.started_at.date() == today}),
        'assessments_today': len(assessments_today),
        'sessions_completed_today': len(completed_today),
        'patients_requiring_review': len({a.patient_id for a in review if a.patient_id}),
        'average_adherence': None if not adherence else round(100 * sum(adherence) / len(adherence), 1),
        'recent_patients': [_patient(p) for p in patients[:8]],
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
    spec = get_exercise(body.exercise_id)
    if not spec['tracking_supported']:
        raise HTTPException(400, spec['name']+' is in the library but camera tracking is not enabled')
    if body.side not in ('left', 'right'):
        raise HTTPException(400, 'Invalid side')
    plan = db.scalars(select(RehabPlan).where(RehabPlan.patient_id == patient.id,
                                             RehabPlan.status.in_(('approved', 'active')))).first()
    sid = 'SESSION_' + uuid.uuid4().hex[:8].upper()
    row = RehabSession(
        id=sid, patient_id=patient.id, clinician_id=None if user.role == 'PATIENT' else user.id,
        exercise_id=body.exercise_id, side=body.side, source=MEASUREMENT_SOURCE, model_version='pending',
        consent_recording=body.consent_recording, target=body.target, goal=body.goal,
        pain_before=body.pain_before, status='calibrating', is_demo=patient.is_demo or MEASUREMENT_SOURCE == 'simulation',
    )
    db.add(row)
    if body.consent_recording:
        db.add(Consent(id=new_id('CON-'), patient_id=patient.id, kind='recording', granted=True))
    try:
        HUB.start(sid, patient.id, body.exercise_id, body.side, body.target, body.goal,
                  MEASUREMENT_SOURCE, body.consent_recording)
    except Exception as exc:
        row.status = 'failed'
        raise HTTPException(400, str(exc)) from exc
    audit(db, user, 'start_session', 'session', sid, {'exercise': body.exercise_id, 'source': MEASUREMENT_SOURCE})
    db.flush()
    return {'id': sid, 'source': MEASUREMENT_SOURCE, 'status': 'calibrating', 'plan_id': None if plan is None else plan.id,
            'exercise': spec}


@router.get('/sessions/{session_id}')
def get_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
    live = HUB.latest(session_id)
    return {**_session(row), 'live': live}


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


@router.post('/sessions/{session_id}/confirm')
def confirm_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(RehabSession, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found')
    load_patient(db, user, row.patient_id)
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
    patient = load_patient(db, user, row.patient_id)
    try:
        summary, history = HUB.finish(session_id)
    except KeyError as exc:
        raise HTTPException(404, 'Live session is not active') from exc
    assessment = persist_finished_session(db, row, patient, summary, history, body, user)
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
async def session_ws(websocket: WebSocket, session_id: str, token: str = Query(...)):
    import asyncio
    try:
        decode_token(token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
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
            'safety_outcome': row.safety_outcome, 'is_demo': row.is_demo, 'consent_recording': row.consent_recording}


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
