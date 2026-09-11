import uuid
from datetime import datetime, timedelta
from backend.auth import hash_password
from backend.config import DEMO_PASSWORD
from sqlalchemy import select
from backend.database.models import (
    AIReport, Alert, Appointment, Assessment, AuditLog, ClinicalNote, CompensationEvent,
    Consent, ExerciseCatalog, ExerciseResult, Hospital, PainScore, Patient, ROMMeasurement,
    Recording, RehabPlan, Session, SessionMetric, User, utcnow,
)
from edge.exercises.library import approved_library


def seed_if_empty(db):
    if db.get(Hospital, 'H1'):
        _ensure_exercises(db)
        _ensure_session_metrics(db)
        return False
    db.add(Hospital(id='H1', name='City Rehabilitation Centre (demo)'))
    admin = User(id='U-admin', hospital_id='H1', email='admin@hospital.local',
                 password_hash=hash_password(DEMO_PASSWORD), full_name='Maya Iyer', role='ADMIN')
    doctor = User(id='U-doctor', hospital_id='H1', email='dr.rao@hospital.local',
                  password_hash=hash_password(DEMO_PASSWORD), full_name='Dr Vikram Rao', role='DOCTOR')
    physio = User(id='U-physio', hospital_id='H1', email='priya.mehta@hospital.local',
                  password_hash=hash_password(DEMO_PASSWORD), full_name='Priya Mehta', role='PHYSIOTHERAPIST')
    patient_user = User(id='U-patient', hospital_id='H1', email='ananya.sharma@demo.local',
                        password_hash=hash_password(DEMO_PASSWORD), full_name='Ananya Sharma', role='PATIENT')
    db.add_all([admin, doctor, physio, patient_user])
    db.add(Patient(id='P102', hospital_id='H1', user_id='U-patient', mrn='DEMO-102',
                   full_name='Ananya Sharma', date_of_birth='1979-04-12', sex='F',
                   affected_side='right',
                   clinician_diagnosis='Adhesive capsulitis under clinician review — not camera-confirmed',
                   assigned_physio_id='U-physio', assigned_doctor_id='U-doctor', is_demo=True))
    _ensure_exercises(db)
    baseline_at = utcnow() - timedelta(days=42)
    current_at = utcnow() - timedelta(days=1)
    db.add(Assessment(
        id='A-baseline', patient_id='P102', clinician_id='U-physio', created_at=baseline_at,
        source='simulation', model_version='synthetic-v1', affected_side='right',
        flexion_max=94, abduction_max=72, elevation_max=94, avg_confidence=0.91,
        peak_velocity=42, smoothness=58, torso_compensation=16.4, shoulder_asymmetry=18,
        attempts=3, pain_rest=4, pain_movement=7, difficulty_dressing=3, difficulty_grooming=2,
        difficulty_overhead=4, difficulty_behind_back=4, tracking_quality=88, is_demo=True,
        notes='Seeded baseline for the demo journey. Synthetic measurements, not a live capture.',
    ))
    db.add(Assessment(
        id='A-current', patient_id='P102', clinician_id='U-physio', created_at=current_at,
        source='simulation', model_version='synthetic-v1', affected_side='right',
        flexion_max=121, abduction_max=103, elevation_max=121, avg_confidence=0.94,
        peak_velocity=38, smoothness=76, torso_compensation=6.1, shoulder_asymmetry=9,
        attempts=2, pain_rest=2, pain_movement=4, difficulty_dressing=1, difficulty_grooming=1,
        difficulty_overhead=2, difficulty_behind_back=2, tracking_quality=93, is_demo=True,
        notes='Seeded follow-up for the demo journey. Synthetic measurements, not a live capture.',
    ))
    for movement, baseline, current in (('abduction', 72, 103), ('flexion', 94, 121)):
        db.add(ROMMeasurement(id='ROM-b-'+movement, patient_id='P102', assessment_id='A-baseline',
                              movement=movement, value=baseline, confidence=0.91, valid=True,
                              recorded_at=baseline_at, source='simulation', model_version='synthetic-v1'))
        db.add(ROMMeasurement(id='ROM-c-'+movement, patient_id='P102', assessment_id='A-current',
                              movement=movement, value=current, confidence=0.94, valid=True,
                              recorded_at=current_at, source='simulation', model_version='synthetic-v1'))
    db.add(PainScore(id='PAIN-b', patient_id='P102', assessment_id='A-baseline', rest=4, movement=7,
                     context='baseline_assessment', recorded_at=baseline_at))
    db.add(PainScore(id='PAIN-c', patient_id='P102', assessment_id='A-current', rest=2, movement=4,
                     context='followup_assessment', recorded_at=current_at))
    db.add(CompensationEvent(id='CE-b', patient_id='P102', assessment_id='A-baseline',
                             type='torso_compensation', value=16.4, threshold=10, created_at=baseline_at))
    db.add(CompensationEvent(id='CE-c', patient_id='P102', assessment_id='A-current',
                             type='torso_compensation', value=6.1, threshold=10, created_at=current_at))
    plan_exercises = [
        {'exercise_id': 'pendulum', 'sets': 1, 'reps': 10},
        {'exercise_id': 'assisted_flexion', 'sets': 2, 'reps': 8},
        {'exercise_id': 'shoulder_abduction', 'sets': 2, 'reps': 8},
        {'exercise_id': 'wall_climb', 'sets': 2, 'reps': 6},
    ]
    db.add(RehabPlan(id='PLAN-102', patient_id='P102', clinician_id='U-physio', status='approved',
                     exercises=plan_exercises, created_by_ai=True,
                     created_at=baseline_at + timedelta(days=1),
                     approved_at=baseline_at + timedelta(days=1), approved_by='U-physio',
                     notes='Clinician-approved demonstration plan. AI drafted, physiotherapist accepted.'))
    for week in range(6):
        when = baseline_at + timedelta(days=7 * (week + 1))
        abduction = 72 + (103 - 72) * (week + 1) / 6
        flexion = 94 + (121 - 94) * (week + 1) / 6
        sid = f'SESS-{week+1:02d}'
        db.add(Session(
            id=sid, patient_id='P102', clinician_id='U-physio', exercise_id='shoulder_abduction',
            side='right', started_at=when, ended_at=when + timedelta(minutes=12),
            source='simulation', model_version='synthetic-v1', consent_recording=True,
            target=80 + week * 4, goal=8, reps=8, invalid_reps=1 if week < 2 else 0,
            peak_angle=round(abduction, 1), coverage=90 + week, pain_before=7 - week // 2,
            pain_after=max(3, 6 - week // 2), safety_outcome='ALLOW', status='complete',
            duration_s=720, is_demo=True,
        ))
        db.add(ExerciseResult(id='ER-'+sid, patient_id='P102', session_id=sid, exercise_id='shoulder_abduction',
                              reps_valid=8, reps_invalid=1 if week < 2 else 0, peak=round(abduction, 1),
                              compensation_count=4 - week if week < 4 else 1, created_at=when))
        db.add(ROMMeasurement(id='ROM-s-'+sid, patient_id='P102', session_id=sid, movement='abduction',
                              value=round(abduction, 1), confidence=0.93, valid=True, recorded_at=when,
                              source='simulation', model_version='synthetic-v1'))
        db.add(PainScore(id='PAIN-s-'+sid, patient_id='P102', session_id=sid, rest=max(2, 4 - week // 2),
                         movement=max(3, 7 - week), context='session', recorded_at=when))
        if week < 3:
            db.add(CompensationEvent(id='CE-'+sid, patient_id='P102', session_id=sid, type='torso_compensation',
                                     value=round(16.4 - week * 3, 1), threshold=10, created_at=when))
        db.add(Recording(id='REC-'+sid, patient_id='P102', session_id=sid, kind='telemetry',
                         telemetry_path=f'seed://P102/{sid}/telemetry.json'))
    db.add(ClinicalNote(id='NOTE-1', patient_id='P102', author_id='U-physio',
                        body='Baseline visit: marked night pain, limited abduction, visible trunk lean during elevation. Home pendulum and assisted flexion agreed. Camera measurements are supportive only.',
                        created_at=baseline_at))
    db.add(ClinicalNote(id='NOTE-2', patient_id='P102', author_id='U-physio',
                        body='Six-week review: patient reports easier dressing. Abduction and flexion improved on repeated simulated assessments. Continue current plan; no independent diagnosis from the camera.',
                        created_at=current_at))
    db.add(Consent(id='CON-rec', patient_id='P102', kind='recording', granted=True, created_at=baseline_at))
    db.add(Consent(id='CON-data', patient_id='P102', kind='data_processing', granted=True, created_at=baseline_at))
    db.add(Alert(id='AL-comp', patient_id='P102', type='frequent_compensation', severity='warning',
                message='Early sessions showed repeated torso compensation above 10°. Later sessions improved. Demo/seeded alert.',
                created_at=baseline_at + timedelta(days=10), acknowledged=True, acknowledged_by='U-physio'))
    db.add(Alert(id='AL-review', patient_id='P102', type='progress_review', severity='info',
                message='Follow-up assessment is ready for clinician review (demo patient P102).',
                created_at=current_at, acknowledged=False))
    db.add(Appointment(id='APPT-1', patient_id='P102', clinician_id='U-physio',
                       scheduled_for=utcnow() + timedelta(days=7), reason='Progress review', status='scheduled'))
    db.add(AIReport(
        id='RPT-seed', patient_id='P102', kind='progress', requires_approval=False,
        approved_by='U-physio', approved_at=current_at, created_at=current_at, model='deterministic-supervisor-v1',
        payload={
            'intent': 'progress_analysis',
            'patient_id': 'P102',
            'summary': 'Seeded comparison: abduction 72° → 103° and flexion 94° → 121° on synthetic assessments, with pain during movement 7/10 → 4/10. Not a live camera record.',
            'measured_changes': {
                'abduction': {'baseline': 72, 'current': 103, 'change': 31},
                'flexion': {'baseline': 94, 'current': 121, 'change': 27},
            },
            'alerts': [],
            'recommendation': 'continue clinician-approved plan',
            'requires_approval': False,
            'source_disclaimer': 'Values come from labelled demo/synthetic records.',
        },
    ))
    db.add(AuditLog(id=uuid.uuid4().hex, actor_id='U-admin', action='seed_demo', resource='hospital',
                    resource_id='H1', detail={'note': 'Deterministic demo records inserted'}))
    _ensure_session_metrics(db)
    return True


def _ensure_exercises(db):
    for spec in approved_library():
        if db.get(ExerciseCatalog, spec['exercise_id']) is None:
            db.add(ExerciseCatalog(exercise_id=spec['exercise_id'], name=spec['name'], spec=spec))


def _ensure_session_metrics(db):
    existing = {m.session_id for m in db.scalars(select(SessionMetric)).all()}
    for sess in db.scalars(select(Session)).all():
        if sess.id in existing:
            continue
        db.add(SessionMetric(id='SM-'+sess.id, session_id=sess.id, payload={
            'summary': {
                'reps': sess.reps, 'peak': sess.peak_angle, 'coverage': sess.coverage,
                'safety': sess.safety_outcome, 'source': sess.source,
            },
            'sample_count': 0,
            'samples': [],
            'seeded': True,
        }))
