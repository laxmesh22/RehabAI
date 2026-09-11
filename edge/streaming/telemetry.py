"""Compact session telemetry. Raw video is never placed in PostgreSQL."""


def landmark_payload(landmarks2d):
    payload = {}
    for name, landmark in landmarks2d.items():
        payload[name] = {'x': round(landmark.x, 4), 'y': round(landmark.y, 4), 'c': round(landmark.confidence, 3)}
    return payload


def build_telemetry(session_id, exercise, sample, safety, landmarks2d, extra=None):
    payload = {
        'session_id': session_id,
        'timestamp': sample['timestamp'],
        'exercise': exercise,
        'exercise_phase': sample.get('phase'),
        'shoulder_angle': sample.get('angle'),
        'torso_lean': sample.get('lean'),
        'pose_confidence': sample.get('confidence'),
        'rep': sample.get('reps'),
        'compensation': abs(sample.get('lean', 0)) > 10 or not sample.get('valid', True) and 'lean' in (sample.get('feedback') or '').lower(),
        'valid': sample.get('valid'),
        'peak': sample.get('peak'),
        'coverage': sample.get('coverage'),
        'feedback': sample.get('feedback'),
        'safety': safety.as_dict() if hasattr(safety, 'as_dict') else safety,
        'landmarks_2d': landmark_payload(landmarks2d or {}),
    }
    if extra:
        payload.update(extra)
    return payload
