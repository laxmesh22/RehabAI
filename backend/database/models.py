from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship, sessionmaker
from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, JSON, create_engine, UniqueConstraint)
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Hospital(Base):
    __tablename__ = 'hospitals'
    id = mapped_column(String, primary_key=True)
    name = mapped_column(String, nullable=False)
    created_at = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = 'users'
    id = mapped_column(String, primary_key=True)
    hospital_id = mapped_column(String, ForeignKey('hospitals.id'), nullable=False)
    email = mapped_column(String, unique=True, nullable=False)
    password_hash = mapped_column(String, nullable=False)
    full_name = mapped_column(String, nullable=False)
    role = mapped_column(String, nullable=False)  # ADMIN | DOCTOR | PHYSIOTHERAPIST | PATIENT
    is_active = mapped_column(Boolean, default=True)
    created_at = mapped_column(DateTime, default=utcnow)


class Patient(Base):
    __tablename__ = 'patients'
    id = mapped_column(String, primary_key=True)
    hospital_id = mapped_column(String, ForeignKey('hospitals.id'), nullable=False)
    user_id = mapped_column(String, ForeignKey('users.id'), nullable=True)
    mrn = mapped_column(String, unique=True, nullable=False)
    full_name = mapped_column(String, nullable=False)
    date_of_birth = mapped_column(String, nullable=True)
    sex = mapped_column(String, nullable=True)
    affected_side = mapped_column(String, nullable=False)
    clinician_diagnosis = mapped_column(String, nullable=True)  # clinician-entered, never inferred
    assigned_physio_id = mapped_column(String, ForeignKey('users.id'), nullable=True)
    assigned_doctor_id = mapped_column(String, ForeignKey('users.id'), nullable=True)
    is_demo = mapped_column(Boolean, default=False)
    created_at = mapped_column(DateTime, default=utcnow)


class Assessment(Base):
    __tablename__ = 'assessments'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    clinician_id = mapped_column(String, ForeignKey('users.id'), nullable=True)
    created_at = mapped_column(DateTime, default=utcnow)
    source = mapped_column(String, nullable=False)
    model_version = mapped_column(String, nullable=False)
    affected_side = mapped_column(String, nullable=False)
    flexion_max = mapped_column(Float, nullable=True)
    abduction_max = mapped_column(Float, nullable=True)
    elevation_max = mapped_column(Float, nullable=True)
    avg_confidence = mapped_column(Float, nullable=True)
    peak_velocity = mapped_column(Float, nullable=True)
    smoothness = mapped_column(Float, nullable=True)
    torso_compensation = mapped_column(Float, nullable=True)
    shoulder_asymmetry = mapped_column(Float, nullable=True)
    attempts = mapped_column(Integer, default=1)
    pain_rest = mapped_column(Integer, nullable=True)
    pain_movement = mapped_column(Integer, nullable=True)
    difficulty_dressing = mapped_column(Integer, nullable=True)
    difficulty_grooming = mapped_column(Integer, nullable=True)
    difficulty_overhead = mapped_column(Integer, nullable=True)
    difficulty_behind_back = mapped_column(Integer, nullable=True)
    tracking_quality = mapped_column(Float, nullable=True)
    status = mapped_column(String, default='complete')
    is_demo = mapped_column(Boolean, default=False)
    notes = mapped_column(Text, nullable=True)


class ROMMeasurement(Base):
    __tablename__ = 'rom_measurements'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    assessment_id = mapped_column(String, ForeignKey('assessments.id'), nullable=True)
    session_id = mapped_column(String, ForeignKey('sessions.id'), nullable=True)
    movement = mapped_column(String, nullable=False)
    value = mapped_column(Float, nullable=False)
    confidence = mapped_column(Float, nullable=False)
    valid = mapped_column(Boolean, nullable=False)
    recorded_at = mapped_column(DateTime, default=utcnow)
    source = mapped_column(String, nullable=False)
    model_version = mapped_column(String, nullable=False)


class PainScore(Base):
    __tablename__ = 'pain_scores'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    assessment_id = mapped_column(String, ForeignKey('assessments.id'), nullable=True)
    session_id = mapped_column(String, ForeignKey('sessions.id'), nullable=True)
    rest = mapped_column(Integer, nullable=True)
    movement = mapped_column(Integer, nullable=True)
    context = mapped_column(String, nullable=True)
    recorded_at = mapped_column(DateTime, default=utcnow)


class Session(Base):
    __tablename__ = 'sessions'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    clinician_id = mapped_column(String, ForeignKey('users.id'), nullable=True)
    exercise_id = mapped_column(String, nullable=False)
    side = mapped_column(String, nullable=False)
    started_at = mapped_column(DateTime, default=utcnow)
    ended_at = mapped_column(DateTime, nullable=True)
    source = mapped_column(String, nullable=False)
    model_version = mapped_column(String, nullable=False)
    consent_recording = mapped_column(Boolean, default=False)
    target = mapped_column(Float, nullable=False)
    goal = mapped_column(Integer, nullable=False)
    reps = mapped_column(Integer, default=0)
    invalid_reps = mapped_column(Integer, default=0)
    peak_angle = mapped_column(Float, nullable=True)
    coverage = mapped_column(Float, nullable=True)
    pain_before = mapped_column(Integer, nullable=True)
    pain_after = mapped_column(Integer, nullable=True)
    kind = mapped_column(String, default='rehab')  # assessment | rehab
    intake = mapped_column(JSON, nullable=True)
    safety_outcome = mapped_column(String, default='ALLOW')
    status = mapped_column(String, default='active')
    duration_s = mapped_column(Float, nullable=True)
    is_demo = mapped_column(Boolean, default=False)


class SessionMetric(Base):
    __tablename__ = 'session_metrics'
    id = mapped_column(String, primary_key=True)
    session_id = mapped_column(String, ForeignKey('sessions.id'), nullable=False)
    payload = mapped_column(JSON, nullable=False)


class ExerciseCatalog(Base):
    __tablename__ = 'exercises'
    exercise_id = mapped_column(String, primary_key=True)
    name = mapped_column(String, nullable=False)
    spec = mapped_column(JSON, nullable=False)


class RehabPlan(Base):
    __tablename__ = 'rehab_plans'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    clinician_id = mapped_column(String, ForeignKey('users.id'), nullable=True)
    status = mapped_column(String, default='draft')  # draft | approved | active
    exercises = mapped_column(JSON, nullable=False)
    created_by_ai = mapped_column(Boolean, default=False)
    created_at = mapped_column(DateTime, default=utcnow)
    approved_at = mapped_column(DateTime, nullable=True)
    approved_by = mapped_column(String, ForeignKey('users.id'), nullable=True)
    notes = mapped_column(Text, nullable=True)


class ExerciseResult(Base):
    __tablename__ = 'exercise_results'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    session_id = mapped_column(String, ForeignKey('sessions.id'), nullable=False)
    exercise_id = mapped_column(String, nullable=False)
    reps_valid = mapped_column(Integer, default=0)
    reps_invalid = mapped_column(Integer, default=0)
    peak = mapped_column(Float, nullable=True)
    compensation_count = mapped_column(Integer, default=0)
    created_at = mapped_column(DateTime, default=utcnow)


class CompensationEvent(Base):
    __tablename__ = 'compensation_events'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    session_id = mapped_column(String, ForeignKey('sessions.id'), nullable=True)
    assessment_id = mapped_column(String, ForeignKey('assessments.id'), nullable=True)
    type = mapped_column(String, nullable=False)
    value = mapped_column(Float, nullable=False)
    threshold = mapped_column(Float, nullable=False)
    created_at = mapped_column(DateTime, default=utcnow)


class Recording(Base):
    __tablename__ = 'recordings'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    session_id = mapped_column(String, ForeignKey('sessions.id'), nullable=False)
    consent_id = mapped_column(String, ForeignKey('consents.id'), nullable=True)
    telemetry_path = mapped_column(String, nullable=True)
    video_path = mapped_column(String, nullable=True)
    skeleton_path = mapped_column(String, nullable=True)
    kind = mapped_column(String, default='telemetry')
    created_at = mapped_column(DateTime, default=utcnow)


class ClinicalNote(Base):
    __tablename__ = 'clinical_notes'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    author_id = mapped_column(String, ForeignKey('users.id'), nullable=False)
    body = mapped_column(Text, nullable=False)
    created_at = mapped_column(DateTime, default=utcnow)


class AIReport(Base):
    __tablename__ = 'ai_reports'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    kind = mapped_column(String, nullable=False)
    payload = mapped_column(JSON, nullable=False)
    requires_approval = mapped_column(Boolean, default=True)
    approved_by = mapped_column(String, ForeignKey('users.id'), nullable=True)
    approved_at = mapped_column(DateTime, nullable=True)
    created_at = mapped_column(DateTime, default=utcnow)
    model = mapped_column(String, nullable=True)


class Alert(Base):
    __tablename__ = 'alerts'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=True)
    type = mapped_column(String, nullable=False)
    severity = mapped_column(String, nullable=False)
    message = mapped_column(String, nullable=False)
    created_at = mapped_column(DateTime, default=utcnow)
    acknowledged = mapped_column(Boolean, default=False)
    acknowledged_by = mapped_column(String, ForeignKey('users.id'), nullable=True)


class Consent(Base):
    __tablename__ = 'consents'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    kind = mapped_column(String, nullable=False)
    granted = mapped_column(Boolean, nullable=False)
    created_at = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id = mapped_column(String, primary_key=True)
    actor_id = mapped_column(String, nullable=True)
    action = mapped_column(String, nullable=False)
    resource = mapped_column(String, nullable=False)
    resource_id = mapped_column(String, nullable=True)
    detail = mapped_column(JSON, nullable=True)
    created_at = mapped_column(DateTime, default=utcnow)


class Appointment(Base):
    __tablename__ = 'appointments'
    id = mapped_column(String, primary_key=True)
    patient_id = mapped_column(String, ForeignKey('patients.id'), nullable=False)
    clinician_id = mapped_column(String, ForeignKey('users.id'), nullable=True)
    scheduled_for = mapped_column(DateTime, nullable=False)
    reason = mapped_column(String, nullable=True)
    status = mapped_column(String, default='scheduled')
