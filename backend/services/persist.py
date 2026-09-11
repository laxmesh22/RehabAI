"""Write pipeline results into structured tables. Video stays on disk, never in SQL."""
from backend.auth import new_id
from backend.database.models import (
    Alert, Assessment, CompensationEvent, ExerciseResult, PainScore, ROMMeasurement,
    Recording, SessionMetric, utcnow,
)
from edge.calibration import smoothness


def compact_sample(row):
    return {
        'timestamp': row.get('timestamp'),
        'shoulder_angle': row.get('shoulder_angle'),
        'torso_lean': row.get('torso_lean'),
        'pose_confidence': row.get('pose_confidence'),
        'rep': row.get('rep'),
        'exercise_phase': row.get('exercise_phase'),
        'valid': row.get('valid'),
        'feedback': row.get('feedback'),
        'source': row.get('source'),
        'safety': (row.get('safety') or {}).get('level'),
    }


def persist_finished_session(db, session_row, patient, summary, history, finish_body, user):
    session_row.ended_at = utcnow()
    session_row.reps = summary['reps']
    session_row.invalid_reps = summary['invalid_reps']
    session_row.peak_angle = round(summary['peak'], 1)
    session_row.coverage = summary['coverage']
    session_row.pain_after = finish_body.pain_after
    session_row.safety_outcome = summary['safety']['level']
    session_row.status = 'blocked' if summary['safety']['level'] == 'BLOCK' else 'complete'
    session_row.duration_s = summary['duration']
    session_row.model_version = summary['model_version']
    session_row.source = summary['source']

    sampled = [compact_sample(row) for i, row in enumerate(history) if i % 4 == 0]
    angles = [s['shoulder_angle'] for s in sampled if s.get('valid') and s.get('shoulder_angle') is not None]
    db.add(SessionMetric(id=new_id('SM-'), session_id=session_row.id, payload={
        'summary': {
            'reps': session_row.reps, 'peak': session_row.peak_angle, 'coverage': session_row.coverage,
            'safety': session_row.safety_outcome, 'source': session_row.source,
            'smoothness': smoothness(angles),
        },
        'sample_count': len(history),
        'samples': sampled,
    }))
    db.add(ExerciseResult(id=new_id('ER-'), patient_id=patient.id, session_id=session_row.id,
                          exercise_id=session_row.exercise_id, reps_valid=session_row.reps,
                          reps_invalid=session_row.invalid_reps, peak=session_row.peak_angle,
                          compensation_count=len(summary['events'])))
    db.add(PainScore(id=new_id('PAIN-'), patient_id=patient.id, session_id=session_row.id,
                     rest=finish_body.pain_rest, movement=finish_body.pain_after, context='session'))
    movement = _movement(session_row.exercise_id)
    valid_rom = session_row.coverage is not None and session_row.coverage >= 50 and (session_row.peak_angle or 0) > 0
    if valid_rom:
        db.add(ROMMeasurement(id=new_id('ROM-'), patient_id=patient.id, session_id=session_row.id, movement=movement,
                              value=session_row.peak_angle, confidence=min(1.0, (session_row.coverage or 0) / 100),
                              valid=True, source=session_row.source, model_version=session_row.model_version))
    for event in summary['events']:
        db.add(CompensationEvent(id=new_id('CE-'), patient_id=patient.id, session_id=session_row.id,
                                 type=event['type'], value=event['value'], threshold=event['threshold']))
    if session_row.consent_recording and summary.get('telemetry_path'):
        db.add(Recording(id=new_id('REC-'), patient_id=patient.id, session_id=session_row.id,
                         telemetry_path=summary['telemetry_path'],
                         skeleton_path=summary.get('skeleton_path'),
                         kind='telemetry'))
    if summary['safety']['level'] in ('BLOCK', 'CLINICIAN_REVIEW'):
        db.add(Alert(id=new_id('AL-'), patient_id=patient.id, type='session_safety',
                     severity='critical' if summary['safety']['level'] == 'BLOCK' else 'warning',
                     message='Session '+session_row.id+' ended with safety '+summary['safety']['level']))
    assessment = None
    if finish_body.create_assessment and valid_rom and session_row.reps >= 1:
        assessment = Assessment(
            id=new_id('A-'), patient_id=patient.id,
            clinician_id=None if user.role == 'PATIENT' else user.id,
            source=session_row.source, model_version=session_row.model_version, affected_side=session_row.side,
            flexion_max=session_row.peak_angle if movement == 'flexion' else None,
            abduction_max=session_row.peak_angle if movement == 'abduction' else None,
            avg_confidence=min(1.0, (session_row.coverage or 0) / 100),
            smoothness=smoothness(angles),
            torso_compensation=max((e['value'] for e in summary['events']), default=None),
            pain_rest=finish_body.pain_rest, pain_movement=finish_body.pain_after,
            difficulty_dressing=finish_body.difficulty_dressing, difficulty_grooming=finish_body.difficulty_grooming,
            difficulty_overhead=finish_body.difficulty_overhead, difficulty_behind_back=finish_body.difficulty_behind_back,
            notes=finish_body.notes, is_demo=session_row.is_demo, tracking_quality=session_row.coverage,
        )
        db.add(assessment)
        if valid_rom:
            db.add(ROMMeasurement(id=new_id('ROM-'), patient_id=patient.id, assessment_id=assessment.id,
                                  movement=movement, value=session_row.peak_angle,
                                  confidence=min(1.0, (session_row.coverage or 0) / 100), valid=True,
                                  source=session_row.source, model_version=session_row.model_version))
    return assessment


def _movement(exercise_id):
    if 'abduction' in exercise_id:
        return 'abduction'
    if 'flexion' in exercise_id or exercise_id == 'wall_climb':
        return 'flexion'
    return 'elevation'
