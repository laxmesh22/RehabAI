"""Mixamo-rigged guide pose from telemetry. The LLM never sets bone angles."""
import math

# Ready Player Me / Mixamo names used by https://github.com/hmthanh/3d-human-model
MIXAMO_CORE = (
    'Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head',
    'LeftShoulder', 'LeftArm', 'LeftForeArm', 'LeftHand',
    'RightShoulder', 'RightArm', 'RightForeArm', 'RightHand',
    'LeftUpLeg', 'LeftLeg', 'LeftFoot',
    'RightUpLeg', 'RightLeg', 'RightFoot',
)
ARM_BONE = {'right': 'RightArm', 'left': 'LeftArm'}
FOREARM_BONE = {'right': 'RightForeArm', 'left': 'LeftForeArm'}


def build_guide(spec, side, sample, safety, target):
    phase = sample.get('phase') or 'rest'
    patient_arm = float(sample.get('angle') or 0)
    patient_torso = float(sample.get('lean') if sample.get('lean') is not None else sample.get('torso_lean') or 0)
    valid = bool(sample.get('valid'))
    level = safety.level if hasattr(safety, 'level') else (safety or {}).get('level') or 'ALLOW'
    movement = spec.get('movement') or 'abduction'
    demo = _demo_arm(spec, phase, patient_arm, valid, target, level, sample.get('timestamp') or 0)
    cue = sample.get('feedback') or 'Copy this movement. Keep the trunk quiet.'
    return {
        'rig': 'mixamo',
        'source_repo': 'hmthanh/3d-human-model',
        'driven_by': 'telemetry_not_llm',
        'role': 'exercise_guide',
        'side': side,
        'movement': movement,
        'guide_arm_deg': round(demo, 1),
        'guide_torso_deg': 0.0,
        'patient_arm_deg': round(patient_arm, 1),
        'patient_torso_deg': round(patient_torso, 1),
        'phase': phase,
        'safety': level,
        'cue': cue,
        'bones': mixamo_rotations(side, movement, demo, 0.0),
        'patient_bones': mixamo_rotations(side, movement, patient_arm, patient_torso),
    }


def mixamo_rotations(side, movement, arm_deg, torso_deg):
    """Y-up, character faces +Z. Degrees. Rest pose is arms hanging, not T-pose."""
    bones = {name: _r(0, 0, 0) for name in MIXAMO_CORE}
    lean = float(torso_deg)
    bones['Spine'] = _r(0, 0, round(lean * 0.55, 1))
    bones['Spine1'] = _r(0, 0, round(lean * 0.45, 1))
    arm = ARM_BONE.get(side, 'RightArm')
    forearm = FOREARM_BONE.get(side, 'RightForeArm')
    rx, ry, rz = _arm_euler(side, movement, arm_deg)
    bones[arm] = _r(rx, ry, rz)
    bones[forearm] = _r(0, 0, 0)
    return bones


def _arm_euler(side, movement, arm_deg):
    deg = float(arm_deg)
    sign = 1.0 if side == 'right' else -1.0
    if movement == 'flexion':
        return -deg, 0.0, 0.0
    if movement == 'elevation':
        return round(-deg * 0.35, 1), 0.0, round(sign * deg * 0.75, 1)
    return 0.0, 0.0, sign * deg


def _demo_arm(spec, phase, patient_arm, valid, target, level, timestamp=0):
    rest = float(spec.get('rest_angle') or 12)
    goal = float(target or spec.get('target_range') or 80)
    if level == 'BLOCK':
        return min(rest, 16.0)
    if level == 'PAUSE' and not valid:
        return min(rest, 16.0)
    if not valid and patient_arm > rest:
        return min(goal, max(patient_arm, rest))
    if phase in ('rest',):
        if valid:
            return _idle_demo(rest, goal, timestamp)
        return min(rest, 14.0)
    if phase == 'ready':
        return min(patient_arm, float(spec.get('raise_angle') or 30)) if valid else 16.0
    if phase in ('ascending', 'peak'):
        if valid:
            return min(goal, max(patient_arm, rest))
        return min(goal, max(rest + 20.0, 40.0))
    if valid:
        return max(min(rest, 14.0), patient_arm)
    return min(rest, 14.0)


def _idle_demo(rest, goal, timestamp):
    hold = min(rest, 14.0)
    peak = min(52.0, max(36.0, goal * 0.45))
    cycle = 0.5 * (1.0 + math.sin(float(timestamp) * 1.4))
    return hold + cycle * (peak - hold)


def _r(rx, ry, rz):
    return {'rx': round(float(rx), 1), 'ry': round(float(ry), 1), 'rz': round(float(rz), 1)}
