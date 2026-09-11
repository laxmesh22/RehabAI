"""Structured tools. The language model never queries SQL itself."""
from sqlalchemy import select
from backend.database.models import (
    AIReport, Alert, Assessment, ClinicalNote, CompensationEvent, ExerciseResult,
    PainScore, Patient, ROMMeasurement, Recording, RehabPlan, Session, User, utcnow,
)
from backend.auth import new_id
from edge.exercises.library import approved_library, get_exercise


def _patient(db, patient_id):
    patient = db.get(Patient, patient_id)
    if patient is None:
        return None
    return patient


def get_patient_profile(db, patient_id):
    patient = _patient(db, patient_id)
    if not patient:
        return {'error': 'patient_not_found'}
    physio = db.get(User, patient.assigned_physio_id) if patient.assigned_physio_id else None
    doctor = db.get(User, patient.assigned_doctor_id) if patient.assigned_doctor_id else None
    return {
        'patient_id': patient.id,
        'mrn': patient.mrn,
        'full_name': patient.full_name,
        'affected_side': patient.affected_side,
        'clinician_diagnosis': patient.clinician_diagnosis,
        'is_demo': patient.is_demo,
        'physiotherapist': None if physio is None else physio.full_name,
        'doctor': None if doctor is None else doctor.full_name,
    }


def get_latest_assessment(db, patient_id):
    row = db.scalars(select(Assessment).where(Assessment.patient_id == patient_id).order_by(Assessment.created_at.desc())).first()
    return None if row is None else _assessment(row)


def get_assessment_history(db, patient_id):
    rows = db.scalars(select(Assessment).where(Assessment.patient_id == patient_id).order_by(Assessment.created_at)).all()
    return [_assessment(row) for row in rows]


def _assessment(row):
    return {
        'id': row.id,
        'created_at': row.created_at.isoformat() + 'Z',
        'source': row.source,
        'model_version': row.model_version,
        'affected_side': row.affected_side,
        'flexion_max': row.flexion_max,
        'abduction_max': row.abduction_max,
        'avg_confidence': row.avg_confidence,
        'smoothness': row.smoothness,
        'torso_compensation': row.torso_compensation,
        'pain_rest': row.pain_rest,
        'pain_movement': row.pain_movement,
        'difficulty_dressing': row.difficulty_dressing,
        'difficulty_grooming': row.difficulty_grooming,
        'difficulty_overhead': row.difficulty_overhead,
        'difficulty_behind_back': row.difficulty_behind_back,
        'is_demo': row.is_demo,
        'status': row.status,
    }


def get_rom_history(db, patient_id):
    rows = db.scalars(select(ROMMeasurement).where(ROMMeasurement.patient_id == patient_id,
                                                   ROMMeasurement.valid.is_(True)).order_by(ROMMeasurement.recorded_at)).all()
    return [{'movement': r.movement, 'value': r.value, 'confidence': r.confidence, 'valid': r.valid,
             'recorded_at': r.recorded_at.isoformat() + 'Z', 'source': r.source, 'model_version': r.model_version}
            for r in rows]


def get_pain_history(db, patient_id):
    rows = db.scalars(select(PainScore).where(PainScore.patient_id == patient_id).order_by(PainScore.recorded_at)).all()
    return [{'rest': r.rest, 'movement': r.movement, 'context': r.context,
             'recorded_at': r.recorded_at.isoformat() + 'Z'} for r in rows]


def get_session_history(db, patient_id):
    rows = db.scalars(select(Session).where(Session.patient_id == patient_id).order_by(Session.started_at)).all()
    return [_session(r) for r in rows]


def _session(row):
    return {
        'id': row.id, 'exercise_id': row.exercise_id, 'side': row.side, 'status': row.status,
        'started_at': row.started_at.isoformat() + 'Z',
        'ended_at': None if row.ended_at is None else row.ended_at.isoformat() + 'Z',
        'source': row.source, 'model_version': row.model_version, 'reps': row.reps,
        'invalid_reps': row.invalid_reps, 'peak_angle': row.peak_angle, 'coverage': row.coverage,
        'pain_before': row.pain_before, 'pain_after': row.pain_after, 'target': row.target,
        'goal': row.goal, 'safety_outcome': row.safety_outcome, 'is_demo': row.is_demo,
        'consent_recording': row.consent_recording,
    }


def get_exercise_results(db, patient_id):
    rows = db.scalars(select(ExerciseResult).where(ExerciseResult.patient_id == patient_id).order_by(ExerciseResult.created_at)).all()
    return [{'session_id': r.session_id, 'exercise_id': r.exercise_id, 'reps_valid': r.reps_valid,
             'reps_invalid': r.reps_invalid, 'peak': r.peak, 'compensation_count': r.compensation_count,
             'created_at': r.created_at.isoformat() + 'Z'} for r in rows]


def get_compensation_events(db, patient_id):
    rows = db.scalars(select(CompensationEvent).where(CompensationEvent.patient_id == patient_id).order_by(CompensationEvent.created_at)).all()
    return [{'type': r.type, 'value': r.value, 'threshold': r.threshold, 'session_id': r.session_id,
             'assessment_id': r.assessment_id, 'created_at': r.created_at.isoformat() + 'Z'} for r in rows]


def get_clinician_notes(db, patient_id):
    rows = db.scalars(select(ClinicalNote).where(ClinicalNote.patient_id == patient_id).order_by(ClinicalNote.created_at)).all()
    authors = {u.id: u.full_name for u in db.scalars(select(User)).all()}
    return [{'id': r.id, 'author': authors.get(r.author_id, r.author_id), 'body': r.body,
             'created_at': r.created_at.isoformat() + 'Z'} for r in rows]


def get_current_rehab_plan(db, patient_id):
    row = db.scalars(select(RehabPlan).where(RehabPlan.patient_id == patient_id,
                                            RehabPlan.status.in_(('approved', 'active'))).order_by(RehabPlan.created_at.desc())).first()
    if row is None:
        return None
    return {'id': row.id, 'status': row.status, 'exercises': row.exercises, 'created_by_ai': row.created_by_ai,
            'approved_at': None if row.approved_at is None else row.approved_at.isoformat() + 'Z',
            'notes': row.notes}


def get_approved_exercise_library(_db=None):
    return approved_library()


def calculate_patient_progress(db, patient_id):
    history = get_assessment_history(db, patient_id)
    valid = [row for row in history if row['flexion_max'] is not None or row['abduction_max'] is not None]
    if not valid:
        return {'error': 'no_valid_assessments', 'patient_id': patient_id}
    baseline, current = valid[0], valid[-1]

    def delta(field):
        a, b = baseline.get(field), current.get(field)
        if a is None or b is None:
            return None
        return {'baseline': a, 'current': b, 'change': round(b - a, 1)}

    pain = delta('pain_movement')
    return {
        'patient_id': patient_id,
        'baseline_assessment_id': baseline['id'],
        'current_assessment_id': current['id'],
        'baseline_at': baseline['created_at'],
        'current_at': current['created_at'],
        'abduction': delta('abduction_max'),
        'flexion': delta('flexion_max'),
        'pain_movement': pain,
        'torso_compensation': delta('torso_compensation'),
        'sources': {'baseline': baseline['source'], 'current': current['source']},
        'demo_records_present': baseline['is_demo'] or current['is_demo'],
        'recovery_percentage': None,
        'recovery_percentage_note': 'No recovery percentage is defined. Changes are reported in native units only.',
    }


def compare_sessions(db, session_a, session_b):
    a, b = db.get(Session, session_a), db.get(Session, session_b)
    if a is None or b is None:
        return {'error': 'session_not_found'}
    return {
        'a': _session(a),
        'b': _session(b),
        'peak_change': None if a.peak_angle is None or b.peak_angle is None else round(b.peak_angle - a.peak_angle, 1),
        'pain_change': None if a.pain_after is None or b.pain_after is None else b.pain_after - a.pain_after,
    }


def create_alert(db, patient_id, type, severity, message):
    alert = Alert(id=new_id('AL-'), patient_id=patient_id, type=type, severity=severity, message=message)
    db.add(alert)
    db.flush()
    return {'id': alert.id, 'type': type, 'severity': severity, 'message': message}


def draft_progress_report(db, patient_id):
    progress = calculate_patient_progress(db, patient_id)
    profile = get_patient_profile(db, patient_id)
    if progress.get('error'):
        return {'error': progress['error'], 'patient_id': patient_id}
    abd, flex, pain = progress.get('abduction'), progress.get('flexion'), progress.get('pain_movement')
    bits = []
    if abd:
        bits.append(f"Abduction {abd['baseline']}° → {abd['current']}° ({abd['change']:+.0f}°)")
    if flex:
        bits.append(f"Flexion {flex['baseline']}° → {flex['current']}° ({flex['change']:+.0f}°)")
    if pain:
        bits.append(f"Pain during movement {pain['baseline']}/10 → {pain['current']}/10")
    summary = 'AI-assisted mobility summary from stored records: ' + '; '.join(bits) + '. Clinical review recommended. The camera does not confirm adhesive capsulitis.'
    payload = {
        'intent': 'progress_analysis',
        'patient_id': patient_id,
        'patient_name': profile.get('full_name'),
        'summary': summary,
        'measured_changes': {k: progress[k] for k in ('abduction', 'flexion', 'pain_movement', 'torso_compensation') if progress.get(k)},
        'alerts': [],
        'recommendation': 'continue clinician-approved plan',
        'requires_approval': True,
        'source_disclaimer': 'Only stored valid measurements were used. Missing values were not invented.',
        'demo_records_present': progress.get('demo_records_present'),
    }
    report = AIReport(id=new_id('RPT-'), patient_id=patient_id, kind='progress', payload=payload,
                      requires_approval=True, model='deterministic-supervisor-v1')
    db.add(report)
    db.flush()
    payload['report_id'] = report.id
    return payload


def draft_rehab_plan(db, patient_id):
    library = {item['exercise_id']: item for item in approved_library()}
    latest = get_latest_assessment(db, patient_id)
    plan = get_current_rehab_plan(db, patient_id)
    pain = None if latest is None else latest.get('pain_movement')
    candidates = []
    for exercise_id in ('pendulum', 'assisted_flexion', 'shoulder_abduction', 'wall_climb'):
        spec = library[exercise_id]
        if pain is not None and pain >= 8 and exercise_id in ('shoulder_abduction', 'wall_climb'):
            continue
        candidates.append({'exercise_id': exercise_id, 'name': spec['name'], 'target_range': spec['target_range']})
    if not candidates:
        candidates = [{'exercise_id': 'pendulum', 'name': library['pendulum']['name'], 'target_range': library['pendulum']['target_range']}]
    payload = {
        'intent': 'rehab_planning',
        'patient_id': patient_id,
        'status': 'draft',
        'exercises': candidates,
        'based_on_assessment': None if latest is None else latest['id'],
        'current_plan': None if plan is None else plan['id'],
        'requires_approval': True,
        'note': 'Draft only. A physiotherapist must approve before this changes treatment.',
    }
    row = RehabPlan(id=new_id('PLAN-'), patient_id=patient_id, status='draft', exercises=candidates,
                    created_by_ai=True, notes=payload['note'])
    db.add(row)
    db.flush()
    payload['plan_id'] = row.id
    return payload


def request_clinician_approval(db, resource_kind, resource_id):
    return {'status': 'pending_approval', 'resource_kind': resource_kind, 'resource_id': resource_id,
            'message': 'Queued for physiotherapist review. Treatment is unchanged until approval.'}


def save_ai_observation(db, patient_id, observation):
    report = AIReport(id=new_id('OBS-'), patient_id=patient_id, kind='observation',
                      payload={'observation': observation, 'patient_id': patient_id},
                      requires_approval=False, model='deterministic-supervisor-v1')
    db.add(report)
    db.flush()
    return {'id': report.id}


TOOL_MAP = {
    'get_patient_profile': lambda db, args: get_patient_profile(db, args['patient_id']),
    'get_latest_assessment': lambda db, args: get_latest_assessment(db, args['patient_id']),
    'get_assessment_history': lambda db, args: get_assessment_history(db, args['patient_id']),
    'get_rom_history': lambda db, args: get_rom_history(db, args['patient_id']),
    'get_pain_history': lambda db, args: get_pain_history(db, args['patient_id']),
    'get_session_history': lambda db, args: get_session_history(db, args['patient_id']),
    'get_exercise_results': lambda db, args: get_exercise_results(db, args['patient_id']),
    'get_compensation_events': lambda db, args: get_compensation_events(db, args['patient_id']),
    'get_clinician_notes': lambda db, args: get_clinician_notes(db, args['patient_id']),
    'get_current_rehab_plan': lambda db, args: get_current_rehab_plan(db, args['patient_id']),
    'get_approved_exercise_library': lambda db, args: get_approved_exercise_library(db),
    'calculate_patient_progress': lambda db, args: calculate_patient_progress(db, args['patient_id']),
    'compare_sessions': lambda db, args: compare_sessions(db, args['session_a'], args['session_b']),
    'create_alert': lambda db, args: create_alert(db, args['patient_id'], args['type'], args['severity'], args['message']),
    'draft_progress_report': lambda db, args: draft_progress_report(db, args['patient_id']),
    'draft_rehab_plan': lambda db, args: draft_rehab_plan(db, args['patient_id']),
    'request_clinician_approval': lambda db, args: request_clinician_approval(db, args['resource_kind'], args['resource_id']),
    'save_ai_observation': lambda db, args: save_ai_observation(db, args['patient_id'], args['observation']),
}
