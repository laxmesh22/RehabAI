"""Slot-filling intake for pain and function. Not a diagnosis. Numbers are parsed, not invented."""
from __future__ import annotations

import re
from copy import deepcopy

PAIN_FIELDS = ('pain_rest', 'pain_movement')
FUNCTION_FIELDS = (
    'difficulty_dressing',
    'difficulty_grooming',
    'difficulty_overhead',
    'difficulty_behind_back',
)
INTAKE_FIELDS = PAIN_FIELDS + FUNCTION_FIELDS

FUNCTION_SCALE = (
    (0, 'none'),
    (1, 'mild'),
    (2, 'moderate'),
    (3, 'severe'),
    (4, 'unable'),
)

SAFETY_RE = re.compile(
    r'\b(stop|pause|enough|too much pain|hurts a lot|sharp pain|cannot continue|'
    r'रुक|रुको|बहुत दर्द|बहुत दर्द हो रहा)\b',
    re.I,
)
YES_RE = re.compile(
    r'^\s*(yes|yeah|yep|correct|right|confirm|ok|okay|haan|ha|sahi|हाँ|हां|सही)\s*[.!]?\s*$',
    re.I,
)
NO_RE = re.compile(
    r'^\s*(no|nope|wrong|incorrect|nahi|nahin|नहीं|गलत)\s*[.!]?\s*$',
    re.I,
)

NUMBER_WORDS = {
    'zero': 0, 'oh': 0, 'nil': 0, 'none': 0,
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'शून्य': 0, 'एक': 1, 'दो': 2, 'तीन': 3, 'चार': 4,
    'पांच': 5, 'पाँच': 5, 'छह': 6, 'छे': 6, 'सात': 7,
    'आठ': 8, 'नौ': 9, 'दस': 10,
}

FUNCTION_PHRASES = (
    (0, re.compile(r'\b(no difficulty|no problem|not difficult|none|easy|कोई दिक्कत नहीं|आसान)\b', re.I)),
    (1, re.compile(r'\b(mild|a little|little|slight|thoda|थोड़ा|थोड़ा)\b', re.I)),
    (2, re.compile(r'\b(moderate|some|medium|thik thaak|ठीक ठाक)\b', re.I)),
    (3, re.compile(r'\b(severe|a lot|very difficult|hard|bahut|बहुत मुश्किल)\b', re.I)),
    (4, re.compile(r'\b(unable|cannot|can\'t|impossible|not able|नहीं हो पाता|असमर्थ)\b', re.I)),
)

SCRIPT = (
    {
        'id': 'pain_rest',
        'kind': 'pain',
        'min': 0,
        'max': 10,
        'prompt_en': 'Pain at rest, arm hanging, no movement. Zero is none. Ten is the worst pain. What number from zero to ten?',
        'prompt_hi': 'आराम में दर्द, हाथ नीचे, बिना हिलाए। शून्य मतलब दर्द नहीं, दस मतलब सबसे ज्यादा दर्द। शून्य से दस तक कौन सा नंबर?',
        'confirm_en': 'Pain at rest {value} out of 10. Is that right?',
        'confirm_hi': 'आराम में दर्द {value} में से 10. सही है?',
    },
    {
        'id': 'pain_movement',
        'kind': 'pain',
        'min': 0,
        'max': 10,
        'prompt_en': 'Worst pain today while raising the arm. Zero to ten?',
        'prompt_hi': 'आज हाथ उठाते समय सबसे ज्यादा दर्द कितना? शून्य से दस?',
        'confirm_en': 'Pain during movement {value} out of 10. Is that right?',
        'confirm_hi': 'हिलाते समय दर्द {value} में से 10. सही है?',
    },
    {
        'id': 'difficulty_dressing',
        'kind': 'function',
        'min': 0,
        'max': 4,
        'prompt_en': 'Putting on a shirt or blouse. Zero none, one mild, two moderate, three severe, four unable.',
        'prompt_hi': 'कमीज पहनना कितना मुश्किल? शून्य नहीं, एक हल्का, दो मध्यम, तीन ज्यादा, चार नहीं हो पाता.',
        'confirm_en': 'Dressing difficulty {value} out of 4. Is that right?',
        'confirm_hi': 'कपड़े पहनने में कठिनाई {value} में से 4. सही है?',
    },
    {
        'id': 'difficulty_grooming',
        'kind': 'function',
        'min': 0,
        'max': 4,
        'prompt_en': 'Brushing hair or washing your face. Zero none through four unable.',
        'prompt_hi': 'बाल बनाना या चेहरा धोना कितना मुश्किल? शून्य से चार.',
        'confirm_en': 'Grooming difficulty {value} out of 4. Is that right?',
        'confirm_hi': 'सजने में कठिनाई {value} में से 4. सही है?',
    },
    {
        'id': 'difficulty_overhead',
        'kind': 'function',
        'min': 0,
        'max': 4,
        'prompt_en': 'Reaching a high shelf. Zero none through four unable.',
        'prompt_hi': 'ऊपर की शेल्फ तक पहुँचना कितना मुश्किल? शून्य से चार.',
        'confirm_en': 'Overhead reach difficulty {value} out of 4. Is that right?',
        'confirm_hi': 'ऊपर पहुँचने में कठिनाई {value} में से 4. सही है?',
    },
    {
        'id': 'difficulty_behind_back',
        'kind': 'function',
        'min': 0,
        'max': 4,
        'prompt_en': 'Reaching behind your back, like a zipper. Zero none through four unable.',
        'prompt_hi': 'पीठ पीछे पहुँचना, जैसे ज़िपर, कितना मुश्किल? शून्य से चार.',
        'confirm_en': 'Behind-back difficulty {value} out of 4. Is that right?',
        'confirm_hi': 'पीठ पीछे कठिनाई {value} में से 4. सही है?',
    },
)

FIELD_BY_ID = {item['id']: item for item in SCRIPT}


def script_payload():
    return {
        'disclaimer': 'This is not a diagnosis. These answers are stored as patient-reported scores.',
        'fields': [dict(item) for item in SCRIPT],
        'function_scale': [{'value': value, 'label': label} for value, label in FUNCTION_SCALE],
    }


def field_spec(field_id):
    spec = FIELD_BY_ID.get(field_id)
    if spec is None:
        raise KeyError('Unknown intake field: ' + str(field_id))
    return spec


def parse_utterance(text, field_id, awaiting_confirm=False):
    spec = field_spec(field_id)
    raw = (text or '').strip()
    if not raw:
        return _result(field_id, 'unknown', raw, None, 0, 'No speech was captured. Please tap a number or try again.')
    if SAFETY_RE.search(raw):
        return _result(
            field_id, 'safety_pause', raw, None, 1,
            'Please stop. Rest the arm. A physiotherapist should review before you continue.',
        )
    if awaiting_confirm:
        if YES_RE.match(raw):
            return _result(field_id, 'confirm_yes', raw, None, 0.95, 'Saved.')
        if NO_RE.match(raw):
            return _result(field_id, 'confirm_no', raw, None, 0.95, 'Okay. Say or tap the number again.')
    value = None
    confidence = 0.0
    if spec['kind'] == 'function':
        value, confidence = _function_value(raw)
    if value is None:
        value, confidence = _number_value(raw, spec['min'], spec['max'])
    if value is None:
        return _result(
            field_id, 'unknown', raw, None, 0,
            'I did not catch a number. Tap a score or say it again.',
        )
    return _result(
        field_id, 'number', raw, value, confidence,
        _confirm_prompt(spec, value, 'en'),
        needs_confirm=True,
    )


def _confirm_prompt(spec, value, language):
    template = spec['confirm_hi'] if language == 'hi-IN' else spec['confirm_en']
    return template.format(value=value)


def _result(field_id, intent, raw, value, confidence, spoken, needs_confirm=False):
    return {
        'field': field_id,
        'intent': intent,
        'raw_transcript': raw,
        'parsed_value': value,
        'confidence': round(confidence, 2),
        'needs_confirm': needs_confirm,
        'spoken': spoken,
        'safety': 'PAUSE' if intent == 'safety_pause' else 'ALLOW',
    }


def _number_value(text, minimum, maximum):
    lowered = text.lower()
    found = []
    for match in re.finditer(r'\b(10|[0-9])\b', lowered):
        found.append(int(match.group(1)))
    for word, number in NUMBER_WORDS.items():
        if re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', lowered):
            found.append(number)
    in_range = [n for n in found if minimum <= n <= maximum]
    if not in_range:
        return None, 0.0
    value = in_range[0]
    confidence = 0.9 if len(in_range) == 1 else 0.7
    return value, confidence


def _function_value(text):
    for value, pattern in FUNCTION_PHRASES:
        if pattern.search(text):
            return value, 0.8
    return None, 0.0


def empty_intake():
    return {
        'confirmed': False,
        'language': 'en-IN',
        'source': None,
        'fields': {},
        'safety': 'ALLOW',
    }


def apply_confirmed_value(intake, field_id, value, source, transcript=None):
    spec = field_spec(field_id)
    if not isinstance(value, int) or value < spec['min'] or value > spec['max']:
        raise ValueError(spec['id'] + ' must be an integer from %s to %s' % (spec['min'], spec['max']))
    row = deepcopy(intake) if intake else empty_intake()
    entry = {'value': value, 'source': source}
    if transcript:
        entry['transcript'] = transcript
    row.setdefault('fields', {})[field_id] = entry
    row[field_id] = value
    sources = {item.get('source') for item in row['fields'].values() if item.get('source')}
    if sources == {'voice'}:
        row['source'] = 'voice'
    elif sources == {'tap'}:
        row['source'] = 'tap'
    elif sources:
        row['source'] = 'mixed'
    row['confirmed'] = all(fid in row.get('fields', {}) for fid in INTAKE_FIELDS)
    return row


def scores_from_intake(intake):
    if not intake:
        return {}
    return {fid: intake[fid] for fid in INTAKE_FIELDS if isinstance(intake.get(fid), int)}


def merge_finish_intake(stored, body):
    merged = scores_from_intake(stored)
    for fid in INTAKE_FIELDS:
        supplied = getattr(body, fid, None)
        if supplied is not None:
            merged[fid] = supplied
    return merged


def missing_intake_fields(scores):
    return [fid for fid in INTAKE_FIELDS if not isinstance(scores.get(fid), int)]
