"""Bootstrap hospital staff accounts only — no fabricated patient clinical data."""
import secrets
import uuid
from pathlib import Path

from sqlalchemy import select

from backend.auth import hash_password, new_id
from backend.config import DEMO_PASSWORD, STORAGE_DIR
from backend.database.models import (
    AIReport, Alert, Appointment, Assessment, AuditLog, ClinicalNote, CompensationEvent,
    Consent, ExerciseCatalog, ExerciseResult, Hospital, PainScore, Patient, ROMMeasurement,
    Recording, RehabPlan, Session, SessionMetric, User, utcnow,
)
from edge.exercises.library import approved_library

DEMO_PATIENT_EMAIL = 'ananya.sharma@demo.local'
DEMO_PATIENT_ID = 'P102'
DEMO_PATIENT_USER_ID = 'U-patient'


def seed_if_empty(db):
    """Ensure hospital + staff exist. Never insert fabricated ROM/pain/session rows."""
    if db.get(Hospital, 'H1'):
        _ensure_exercises(db)
        _purge_fabricated_demo_patient(db)
        _ensure_session_metrics(db)
        return False
    db.add(Hospital(id='H1', name='City Rehabilitation Centre'))
    admin = User(
        id='U-admin', hospital_id='H1', email='admin@hospital.local',
        password_hash=hash_password(DEMO_PASSWORD), full_name='Maya Iyer', role='ADMIN',
    )
    doctor = User(
        id='U-doctor', hospital_id='H1', email='dr.rao@hospital.local',
        password_hash=hash_password(DEMO_PASSWORD), full_name='Dr Vikram Rao', role='DOCTOR',
    )
    physio = User(
        id='U-physio', hospital_id='H1', email='priya.mehta@hospital.local',
        password_hash=hash_password(DEMO_PASSWORD), full_name='Priya Mehta', role='PHYSIOTHERAPIST',
    )
    db.add_all([admin, doctor, physio])
    _ensure_exercises(db)
    db.add(AuditLog(
        id=uuid.uuid4().hex, actor_id='U-admin', action='seed_hospital',
        resource='hospital', resource_id='H1',
        detail={'note': 'Hospital and staff accounts only; no patient seed data'},
    ))
    _ensure_session_metrics(db)
    return True


def create_empty_patient_account(db, *, full_name='New patient', email=None, password=None):
    """Create a PATIENT user + empty Patient row with no measurements."""
    hospital = db.get(Hospital, 'H1')
    if hospital is None:
        raise RuntimeError('Hospital is not initialised')
    plain = password or secrets.token_urlsafe(18)
    mail = (email or f'patient-{new_id("")}@local.rehabai').strip().lower()
    if db.scalars(select(User).where(User.email == mail)).first():
        raise ValueError('Email already registered')
    user = User(
        id=new_id('U'),
        hospital_id='H1',
        email=mail,
        password_hash=hash_password(plain),
        full_name=(full_name or 'New patient').strip() or 'New patient',
        role='PATIENT',
    )
    patient = Patient(
        id=new_id('P'),
        hospital_id='H1',
        user_id=user.id,
        mrn='MRN-' + new_id('').upper(),
        full_name=user.full_name,
        affected_side='right',
        assigned_physio_id='U-physio',
        assigned_doctor_id='U-doctor',
        is_demo=False,
    )
    db.add_all([user, patient])
    db.flush()
    # Empty starter plan so consumer rehab sessions are allowed — no ROM/pain seed.
    db.add(RehabPlan(
        id=new_id('PLAN-'),
        patient_id=patient.id,
        clinician_id='U-physio',
        status='approved',
        exercises=[
            {'exercise_id': 'pendulum', 'sets': 1, 'reps': 10},
            {'exercise_id': 'assisted_flexion', 'sets': 2, 'reps': 8},
            {'exercise_id': 'shoulder_abduction', 'sets': 2, 'reps': 8},
            {'exercise_id': 'wall_climb', 'sets': 2, 'reps': 6},
        ],
        created_by_ai=False,
        approved_at=utcnow(),
        approved_by='U-physio',
        notes='Starter plan for self-guided sessions. No fabricated measurements.',
    ))
    return {'user': user, 'patient': patient, 'email': mail, 'password': plain}


def _purge_fabricated_demo_patient(db):
    """Remove the old Ananya / P102 seed profile and any is_demo patients from prior installs."""
    patients = list(db.scalars(select(Patient).where(
        (Patient.is_demo.is_(True)) | (Patient.id == DEMO_PATIENT_ID)
    )).all())
    if not patients:
        # Still drop orphaned demo login if patient already deleted.
        orphan = db.get(User, DEMO_PATIENT_USER_ID) or db.scalars(
            select(User).where(User.email == DEMO_PATIENT_EMAIL)
        ).first()
        if orphan is not None:
            db.delete(orphan)
        return
    for patient in patients:
        pid = patient.id
        uid = patient.user_id
        for model in (
            SessionMetric, Recording, ExerciseResult, CompensationEvent, PainScore,
            ROMMeasurement, ClinicalNote, Consent, Alert, Appointment, AIReport, RehabPlan,
        ):
            if model is SessionMetric:
                session_ids = [s.id for s in db.scalars(select(Session).where(Session.patient_id == pid)).all()]
                for sid in session_ids:
                    for row in db.scalars(select(SessionMetric).where(SessionMetric.session_id == sid)).all():
                        db.delete(row)
                continue
            for row in db.scalars(select(model).where(model.patient_id == pid)).all():
                db.delete(row)
        for row in db.scalars(select(Assessment).where(Assessment.patient_id == pid)).all():
            db.delete(row)
        for row in db.scalars(select(Session).where(Session.patient_id == pid)).all():
            db.delete(row)
        db.delete(patient)
        if uid:
            user = db.get(User, uid)
            if user is not None:
                db.delete(user)
        mem = Path(STORAGE_DIR) / 'patients' / pid / 'memory.json'
        if mem.is_file():
            try:
                mem.unlink()
            except OSError:
                pass
    # Named demo login leftover
    leftover = db.scalars(select(User).where(User.email == DEMO_PATIENT_EMAIL)).first()
    if leftover is not None:
        db.delete(leftover)


def _ensure_exercises(db):
    for spec in approved_library():
        if db.get(ExerciseCatalog, spec['exercise_id']) is None:
            db.add(ExerciseCatalog(exercise_id=spec['exercise_id'], name=spec['name'], spec=spec))


def _ensure_session_metrics(db):
    existing = {m.session_id for m in db.scalars(select(SessionMetric)).all()}
    for sess in db.scalars(select(Session)).all():
        if sess.id in existing:
            continue
        db.add(SessionMetric(id='SM-' + sess.id, session_id=sess.id, payload={
            'summary': {
                'reps': sess.reps, 'peak': sess.peak_angle, 'coverage': sess.coverage,
                'safety': sess.safety_outcome, 'source': sess.source,
            },
            'sample_count': 0,
            'samples': [],
            'seeded': False,
        }))
