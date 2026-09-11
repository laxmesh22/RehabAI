"""Clinician-approved demonstration library. Not a prescribed protocol.

avatar_demo maps each catalog id to the FollowAvatar exercise id in web/motion.js.
"""

EXERCISES = {
    'shoulder_abduction': {
        'exercise_id': 'shoulder_abduction',
        'name': 'Shoulder abduction',
        'avatar_demo': 'abduction',
        'instructions': 'Stand facing the camera. Raise the affected arm out to the side, then lower it slowly.',
        'target_joint': 'shoulder',
        'movement': 'abduction',
        'target_range': 80,
        'allowed_compensation': 10,
        'start_position': 'arm relaxed beside the body',
        'peak_condition': 'arm reaches the session target without leaving the frontal plane',
        'completion_condition': 'return to the lowered start position',
        'safety_conditions': ['stop if pain increases sharply', 'keep the trunk upright'],
        'tracking_supported': True,
        'rest_angle': 20,
        'raise_angle': 30,
    },
    'shoulder_flexion': {
        'exercise_id': 'shoulder_flexion',
        'name': 'Shoulder flexion',
        'avatar_demo': 'flexion',
        'instructions': 'Turn slightly so the affected side is visible. Raise the arm forward, then lower it slowly.',
        'target_joint': 'shoulder',
        'movement': 'flexion',
        'target_range': 90,
        'allowed_compensation': 10,
        'start_position': 'arm relaxed beside the body',
        'peak_condition': 'arm reaches the session target in the sagittal plane',
        'completion_condition': 'return to the lowered start position',
        'safety_conditions': ['stop if pain increases sharply', 'avoid leaning backward'],
        'tracking_supported': True,
        'rest_angle': 20,
        'raise_angle': 30,
    },
    'assisted_flexion': {
        'exercise_id': 'assisted_flexion',
        'name': 'Assisted shoulder flexion',
        'avatar_demo': 'wand_flexion',
        'instructions': 'Use a stick or the opposite hand to support the affected arm and raise it forward within comfort.',
        'target_joint': 'shoulder',
        'movement': 'flexion',
        'target_range': 70,
        'allowed_compensation': 12,
        'start_position': 'both arms lowered',
        'peak_condition': 'comfortable assisted elevation',
        'completion_condition': 'return to the start position',
        'safety_conditions': ['do not pull through pain', 'keep the trunk quiet'],
        'tracking_supported': True,
        'rest_angle': 20,
        'raise_angle': 28,
    },
    'wand_flexion': {
        'exercise_id': 'wand_flexion',
        'name': 'Wand flexion',
        'avatar_demo': 'wand_flexion',
        'instructions': 'Hold a stick with both hands and raise both arms forward together within comfort.',
        'target_joint': 'shoulder',
        'movement': 'flexion',
        'target_range': 110,
        'allowed_compensation': 10,
        'start_position': 'stick held across the thighs',
        'peak_condition': 'comfortable forward elevation with the wand',
        'completion_condition': 'return the wand to the start',
        'safety_conditions': ['do not force through sharp pain', 'keep the trunk quiet'],
        'tracking_supported': True,
        'rest_angle': 20,
        'raise_angle': 30,
    },
    'pendulum': {
        'exercise_id': 'pendulum',
        'name': 'Pendulum exercise',
        'avatar_demo': 'pendulum',
        'instructions': 'Lean forward slightly with support, let the affected arm hang, and make small relaxed circles.',
        'target_joint': 'shoulder',
        'movement': 'elevation',
        'target_range': 35,
        'allowed_compensation': 18,
        'start_position': 'arm hanging',
        'peak_condition': 'small oscillation away from rest',
        'completion_condition': 'return toward hanging',
        'safety_conditions': ['keep circles small', 'do not actively hoist the shoulder'],
        'tracking_supported': True,
        'rest_angle': 12,
        'raise_angle': 18,
    },
    'wall_climb': {
        'exercise_id': 'wall_climb',
        'name': 'Wall climb',
        'avatar_demo': 'wall_walk',
        'instructions': 'Face a wall. Walk the fingers upward, pause at a comfortable height, then walk back down.',
        'target_joint': 'shoulder',
        'movement': 'flexion',
        'target_range': 90,
        'allowed_compensation': 10,
        'start_position': 'hand at waist height on the wall',
        'peak_condition': 'highest comfortable finger walk',
        'completion_condition': 'walk the hand back down',
        'safety_conditions': ['do not shrug the shoulder to the ear', 'stop below a painful end range'],
        'tracking_supported': True,
        'rest_angle': 25,
        'raise_angle': 35,
    },
    'external_rotation': {
        'exercise_id': 'external_rotation',
        'name': 'External rotation',
        'avatar_demo': 'er',
        'instructions': 'Elbow at the side, rotate the forearm outward within comfort.',
        'target_joint': 'shoulder',
        'movement': 'external_rotation',
        'target_range': 45,
        'allowed_compensation': 8,
        'start_position': 'elbow flexed, forearm across the abdomen',
        'peak_condition': 'comfortable outward rotation',
        'completion_condition': 'return to the start position',
        'safety_conditions': ['keep the elbow at the side', 'do not compensate with the trunk'],
        'tracking_supported': True,
        'rest_angle': 0,
        'raise_angle': 10,
    },
    'wand_er': {
        'exercise_id': 'wand_er',
        'name': 'Wand external rotation',
        'avatar_demo': 'wand_er',
        'instructions': 'Hold a stick with both hands, elbows at the sides, and rotate the forearms outward together.',
        'target_joint': 'shoulder',
        'movement': 'external_rotation',
        'target_range': 45,
        'allowed_compensation': 8,
        'start_position': 'elbows flexed, stick across the abdomen',
        'peak_condition': 'comfortable outward rotation with the wand',
        'completion_condition': 'return the wand to the start',
        'safety_conditions': ['keep both elbows at the sides', 'stop before sharp pain'],
        'tracking_supported': True,
        'rest_angle': 0,
        'raise_angle': 10,
    },
}


def get_exercise(exercise_id):
    exercise = EXERCISES.get(exercise_id)
    if not exercise:
        raise KeyError('Unknown exercise: ' + exercise_id)
    return dict(exercise)


def approved_library():
    return [dict(item) for item in EXERCISES.values()]


def avatar_demo_for(exercise_id):
    row = EXERCISES.get(exercise_id) or {}
    return row.get('avatar_demo') or 'abduction'
