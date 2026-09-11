"""Convert structured sensor events into coaching copy. No LLM per frame."""

PHRASES = {
    'raise': 'Raise your arm slowly.',
    'target': "Good. You reached today's target.",
    'lean': 'Torso compensation detected. Keep your body upright.',
    'side': 'Try not to lean to the side.',
    'slow': 'Slow down slightly.',
    'reposition': 'Please reposition yourself and repeat the movement.',
    'stop': 'Please stop. This session requires physiotherapist review.',
    'pause': 'Tracking paused. Return to the start position when the skeleton is stable.',
    'imu': 'Arm IMU signal lost. Keep the strap on the upper arm.',
    'disagree': 'Camera and arm IMU disagree. Stay in view and keep the sensor snug.',
}


def coach_message(sample, safety):
    if safety.level == 'BLOCK':
        return PHRASES['stop']
    if 'imu_lost' in safety.reasons and (safety.level == 'PAUSE' or sample.get('imu_required')):
        return PHRASES['imu']
    if safety.level == 'PAUSE':
        return PHRASES['pause'] if sample.get('valid') is False else PHRASES['reposition']
    if 'torso_compensation' in safety.reasons or 'severe_torso_compensation' in safety.reasons:
        return PHRASES['lean']
    if 'sensor_disagreement' in safety.reasons:
        return PHRASES['disagree']
    if 'imu_lost' in safety.reasons:
        return PHRASES['imu']
    feedback = sample.get('feedback') or PHRASES['raise']
    if sample.get('phase') == 'ascending' and abs(sample.get('velocity', 0)) > 80:
        return PHRASES['slow']
    return feedback
