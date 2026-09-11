"""Deterministic safety service. The language model cannot override BLOCK."""
from dataclasses import dataclass, field


@dataclass
class SafetyDecision:
    level: str
    reasons: list = field(default_factory=list)
    halt_exercise: bool = False

    def as_dict(self):
        return {'level': self.level, 'reasons': list(self.reasons), 'halt_exercise': self.halt_exercise}


def evaluate_safety(sample, context=None):
    context = context or {}
    reasons = []
    level = 'ALLOW'
    pain = context.get('pain_during')
    if pain is not None and pain >= context.get('pain_block', 8):
        reasons.append('pain_exceeds_threshold')
        level = 'BLOCK'
    if context.get('unauthorized_exercise'):
        reasons.append('unauthorized_exercise')
        level = 'BLOCK'
    if context.get('not_on_approved_plan'):
        reasons.append('exercise_not_part_of_approved_plan')
        level = 'BLOCK'
    if sample.get('camera_lost') or sample.get('source_error') == 'camera_lost':
        reasons.append('camera_lost')
        level = _raise(level, 'PAUSE')
    if sample.get('imu_lost'):
        reasons.append('imu_lost')
        if sample.get('imu_required'):
            level = _raise(level, 'PAUSE')
        else:
            level = _raise(level, 'WARN')
    if sample.get('sensor_disagreement'):
        reasons.append('sensor_disagreement')
        level = _raise(level, 'WARN')
    if sample.get('depth_unavailable'):
        reasons.append('depth_unavailable')
        level = _raise(level, 'PAUSE')
    if sample.get('people', 1) != 1:
        reasons.append('patient_exits_frame' if sample.get('people', 1) == 0 else 'multiple_people')
        level = _raise(level, 'PAUSE')
    confidence = sample.get('pose_confidence', sample.get('confidence', 1))
    if confidence < context.get('confidence_limit', .65):
        reasons.append('low_pose_confidence')
        level = _raise(level, 'PAUSE')
    lean = abs(sample.get('torso_lean', sample.get('lean', 0)))
    if lean >= context.get('severe_lean', 20):
        reasons.append('severe_torso_compensation')
        level = _raise(level, 'BLOCK')
    elif lean > context.get('lean_limit', 10):
        reasons.append('torso_compensation')
        level = _raise(level, 'WARN')
    if sample.get('invalid_reps', 0) >= context.get('invalid_rep_limit', 5):
        reasons.append('repeated_invalid_reps')
        level = _raise(level, 'CLINICIAN_REVIEW')
    baseline = context.get('baseline_rom')
    current = context.get('current_rom')
    if baseline and current is not None and baseline - current >= context.get('rom_drop', 25):
        reasons.append('large_sudden_rom_reduction')
        level = _raise(level, 'CLINICIAN_REVIEW')
    halt = level in ('BLOCK', 'PAUSE')
    if level == 'BLOCK':
        reasons.append('session_requires_physiotherapist_review')
    return SafetyDecision(level, reasons, halt)


def _rank(level):
    return {'ALLOW': 0, 'WARN': 1, 'PAUSE': 2, 'CLINICIAN_REVIEW': 3, 'BLOCK': 4}[level]


def _raise(current, candidate):
    return candidate if _rank(candidate) > _rank(current) else current
