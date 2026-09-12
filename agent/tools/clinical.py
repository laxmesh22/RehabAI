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
             'assessment_id': r.assessment_id, 'session_id': r.session_id,
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


def _delta_from_values(values, extras=None):
    if not values:
        return None
    first, last = values[0], values[-1]
    row = {'baseline': first, 'current': last, 'change': round(last - first, 1), 'n': len(values)}
    if extras:
        row.update(extras)
    return row


def _latest_source_series(rows):
    """Choose the newest measurement protocol and compare only within that protocol."""
    if not rows:
        return [], None, []
    available = list(dict.fromkeys((row.get('source') or 'unknown') for row in rows))
    selected = rows[-1].get('source') or 'unknown'
    return [row for row in rows if (row.get('source') or 'unknown') == selected], selected, available


def calculate_patient_progress(db, patient_id):
    history = get_assessment_history(db, patient_id)
    rom = get_rom_history(db, patient_id)
    pain_rows = get_pain_history(db, patient_id)
    sessions = get_session_history(db, patient_id)
    valid = [row for row in history if row['flexion_max'] is not None or row['abduction_max'] is not None]
    abd_all = [row for row in rom if row['movement'] == 'abduction']
    flex_all = [row for row in rom if row['movement'] == 'flexion']
    abd_rom, abd_source, abd_sources = _latest_source_series(abd_all)
    flex_rom, flex_source, flex_sources = _latest_source_series(flex_all)
    pain_move = [row['movement'] for row in pain_rows if row.get('movement') is not None]
    if not pain_move:
        pain_move = [row['pain_after'] for row in sessions if row.get('pain_after') is not None]
    pain_rest = [row['rest'] for row in pain_rows if row.get('rest') is not None]
    abd = _delta_from_values([row['value'] for row in abd_rom])
    if abd is None:
        abd_assessments, abd_source, abd_sources = _latest_source_series(
            [row for row in valid if row.get('abduction_max') is not None]
        )
        abd = _delta_from_values([row['abduction_max'] for row in abd_assessments])
    flex = _delta_from_values([row['value'] for row in flex_rom])
    if flex is None:
        flex_assessments, flex_source, flex_sources = _latest_source_series(
            [row for row in valid if row.get('flexion_max') is not None]
        )
        flex = _delta_from_values([row['flexion_max'] for row in flex_assessments])
    pain = _delta_from_values(pain_move)
    rest = _delta_from_values(pain_rest)
    torso_rows, torso_source, torso_sources = _latest_source_series(
        [row for row in valid if row.get('torso_compensation') is not None]
    )
    torso = _delta_from_values([row['torso_compensation'] for row in torso_rows])
    all_sources = list(dict.fromkeys(abd_sources + flex_sources + torso_sources))
    mixed_protocols = len(all_sources) > 1
    comparison_note = (
        'Multiple measurement protocols are stored. ROM deltas use only the newest protocol for each movement.'
        if mixed_protocols else
        'ROM deltas compare measurements from the same stored protocol.'
    )
    if not abd and not flex and not pain:
        return {
            'error': 'no_valid_measurements',
            'patient_id': patient_id,
            'abduction': None,
            'flexion': None,
            'pain_movement': None,
            'pain_rest': None,
            'torso_compensation': None,
            'demo_records_present': any(row.get('is_demo') for row in history + sessions),
            'recovery_percentage': None,
            'recovery_percentage_note': 'No recovery percentage is defined. Changes are reported in native units only.',
            'series_sources': {'abduction': abd_source, 'flexion': flex_source, 'torso_compensation': torso_source},
            'rom_sources_available': {'abduction': abd_sources, 'flexion': flex_sources},
            'mixed_measurement_protocols': mixed_protocols,
            'comparison_note': comparison_note,
        }
    primary_rom = abd_rom or flex_rom
    baseline = valid[0] if valid else None
    current = valid[-1] if valid else None
    demo = any(row.get('is_demo') for row in history + sessions)
    primary_source = abd_source or flex_source
    sources = {
        'baseline': primary_source,
        'current': primary_source,
    }
    return {
        'patient_id': patient_id,
        'baseline_assessment_id': (primary_rom[0].get('assessment_id') if primary_rom else None) or (None if baseline is None else baseline['id']),
        'current_assessment_id': (primary_rom[-1].get('assessment_id') if primary_rom else None) or (None if current is None else current['id']),
        'baseline_at': (primary_rom[0]['recorded_at'] if primary_rom else None) or (None if baseline is None else baseline['created_at']),
        'current_at': (primary_rom[-1]['recorded_at'] if primary_rom else None) or (None if current is None else current['created_at']),
        'abduction': abd,
        'flexion': flex,
        'pain_movement': pain,
        'pain_rest': rest,
        'torso_compensation': torso,
        'sources': sources,
        'latest_source': sources.get('current'),
        'series_sources': {'abduction': abd_source, 'flexion': flex_source, 'torso_compensation': torso_source},
        'rom_sources_available': {'abduction': abd_sources, 'flexion': flex_sources},
        'mixed_measurement_protocols': mixed_protocols,
        'comparison_note': comparison_note,
        'point_count': {
            'abduction': 0 if abd is None else abd.get('n'),
            'flexion': 0 if flex is None else flex.get('n'),
            'pain_movement': 0 if pain is None else pain.get('n'),
        },
        'demo_records_present': demo,
        'recovery_percentage': None,
        'recovery_percentage_note': 'No recovery percentage is defined. Changes are reported in native units only.',
    }


def compare_sessions(db, session_a, session_b):
    a, b = db.get(Session, session_a), db.get(Session, session_b)
    if a is None or b is None:
        return {'error': 'session_not_found'}
    same_patient = a.patient_id == b.patient_id
    same_protocol = (a.source or 'unknown') == (b.source or 'unknown')
    same_side = a.side == b.side
    same_exercise = a.exercise_id == b.exercise_id
    comparable = same_patient and same_protocol and same_side and same_exercise
    reason = None
    if not comparable:
        differences = []
        if not same_patient:
            differences.append('patient')
        if not same_protocol:
            differences.append('measurement source')
        if not same_side:
            differences.append('side')
        if not same_exercise:
            differences.append('exercise')
        reason = 'Sessions use different ' + ', '.join(differences) + ' and are not used for a clinical progress delta.'
    return {
        'a': _session(a),
        'b': _session(b),
        'comparison_valid': comparable,
        'comparison_reason': reason,
        'peak_change': None if not comparable or a.peak_angle is None or b.peak_angle is None else round(b.peak_angle - a.peak_angle, 1),
        'pain_change': None if not comparable or a.pain_after is None or b.pain_after is None else b.pain_after - a.pain_after,
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
        'source_disclaimer': (
            'Only stored valid measurements were used. Missing values were not invented. '
            + progress.get('comparison_note', '')
        ).strip(),
        'measurement_protocols': progress.get('series_sources'),
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
