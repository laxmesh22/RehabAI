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
}


def coach_message(sample, safety):
    if safety.level == 'BLOCK':
        return PHRASES['stop']
    if safety.level == 'PAUSE':
        return PHRASES['pause'] if sample.get('valid') is False else PHRASES['reposition']
    if 'torso_compensation' in safety.reasons or 'severe_torso_compensation' in safety.reasons:
        return PHRASES['lean']
    feedback = sample.get('feedback') or PHRASES['raise']
    if sample.get('phase') == 'ascending' and abs(sample.get('velocity', 0)) > 80:
        return PHRASES['slow']
    return feedback
