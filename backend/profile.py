"""Spoken profile slots for the consumer Talk agent. Identity is collected for personalization only — not diagnosis."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

PROFILE_FIELDS = ('full_name', 'age', 'affected_side')

_NAME_PREFIX = re.compile(
    r'^\s*(?:my name is|i am|i\'m|this is|call me|myself|मेरा नाम|मेरा नाम है|मैं)\s+',
    re.I,
)
_NON_NAME = re.compile(r'[^A-Za-z\u0900-\u097F\s\'.-]')
_AGE_RE = re.compile(
    r'(?:\b(?:i am|i\'m|age(?:\s+is)?|years?\s+old|साल)\s*)?(\d{1,3})\b',
    re.I,
)
_LEFT_RE = re.compile(r'\b(left|l|बाएँ|बाएं|बायें|बायीं|बाया)\b', re.I)
_RIGHT_RE = re.compile(r'\b(right|r|दाएँ|दाएं|दायें|दायीं|दाया)\b', re.I)

PROMPTS = {
    'full_name': (
        'What is your full name?',
        'आपका पूरा नाम क्या है?',
    ),
    'age': (
        'How old are you?',
        'आपकी उम्र कितनी है?',
    ),
    'affected_side': (
        'Which shoulder needs rehab — left or right?',
        'किस कंधे का इलाज — बायाँ या दायाँ?',
    ),
}


def empty_profile() -> dict[str, Any]:
    return {
        'full_name': None,
        'age': None,
        'affected_side': None,
        'fields': {},
        'complete': False,
    }


def normalize_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    row = empty_profile()
    if not isinstance(raw, dict):
        return row
    row.update({k: raw.get(k, row.get(k)) for k in row})
    fields = raw.get('fields') if isinstance(raw.get('fields'), dict) else {}
    row['fields'] = fields
    for fid in PROFILE_FIELDS:
        if row.get(fid) in (None, '') and fid in fields:
            val = fields[fid].get('value') if isinstance(fields[fid], dict) else fields[fid]
            row[fid] = val
    row['complete'] = bool(raw.get('complete')) or all(
        row.get(fid) not in (None, '') for fid in PROFILE_FIELDS
    )
    return row


def next_profile_field(profile: dict[str, Any] | None) -> str | None:
    row = normalize_profile(profile)
    for fid in PROFILE_FIELDS:
        if row.get(fid) in (None, ''):
            return fid
    return None


def profile_complete(profile: dict[str, Any] | None) -> bool:
    return next_profile_field(profile) is None


def prompt_for(field_id: str, language: str) -> str:
    pair = PROMPTS.get(field_id) or ('Please continue.', 'जारी रखें।')
    return pair[1] if language == 'hi-IN' else pair[0]


def parse_name(text: str) -> str | None:
    raw = ' '.join((text or '').split()).strip(' .,!')
    if not raw or len(raw) < 2:
        return None
    cleaned = _NAME_PREFIX.sub('', raw).strip(' .,!')
    cleaned = _NON_NAME.sub('', cleaned)
    cleaned = ' '.join(cleaned.split())
    if len(cleaned) < 2 or cleaned.lower() in {
        'yes', 'no', 'ok', 'okay', 'skip', 'haan', 'nahi', 'हाँ', 'नहीं',
    }:
        return None
    if re.fullmatch(r'\d+', cleaned):
        return None
    parts = cleaned.split()
    if len(parts) > 5:
        parts = parts[:5]
    return ' '.join(p[:1].upper() + p[1:] for p in parts)


def parse_age(text: str) -> int | None:
    match = _AGE_RE.search(text or '')
    if not match:
        return None
    age = int(match.group(1))
    if 12 <= age <= 100:
        return age
    return None


def parse_side(text: str) -> str | None:
    left = bool(_LEFT_RE.search(text or ''))
    right = bool(_RIGHT_RE.search(text or ''))
    if left and not right:
        return 'left'
    if right and not left:
        return 'right'
    return None


def parse_profile_utterance(text: str, field_id: str) -> dict[str, Any]:
    """Deterministic profile parse — never invent identity."""
    if field_id == 'full_name':
        value = parse_name(text)
        if value is None:
            return {'intent': 'unclear', 'spoken': None, 'parsed_value': None}
        return {'intent': 'profile_value', 'parsed_value': value, 'spoken': value}
    if field_id == 'age':
        value = parse_age(text)
        if value is None:
            return {'intent': 'unclear', 'spoken': None, 'parsed_value': None}
        return {'intent': 'profile_value', 'parsed_value': value, 'spoken': str(value)}
    if field_id == 'affected_side':
        value = parse_side(text)
        if value is None:
            return {'intent': 'unclear', 'spoken': None, 'parsed_value': None}
        return {'intent': 'profile_value', 'parsed_value': value, 'spoken': value}
    return {'intent': 'unclear', 'spoken': None, 'parsed_value': None}


def apply_profile_value(profile: dict[str, Any] | None, field_id: str, value: Any, source_text: str = '') -> dict[str, Any]:
    row = normalize_profile(profile)
    if field_id not in PROFILE_FIELDS:
        return row
    row[field_id] = value
    fields = dict(row.get('fields') or {})
    fields[field_id] = {'value': value, 'source': 'voice', 'utterance': (source_text or '')[:120]}
    row['fields'] = fields
    row['complete'] = next_profile_field(row) is None
    return row


def display_first_name(profile: dict[str, Any] | None, fallback: str = 'there') -> str:
    name = (normalize_profile(profile).get('full_name') or '').strip()
    if not name:
        return fallback
    return name.split()[0]


def age_to_dob(age: int) -> str:
    year = date.today().year - int(age)
    return f'{year}-01-01'


def apply_profile_to_patient(db, patient, profile: dict[str, Any]) -> None:
    """Persist spoken profile onto Patient / User. Does not invent clinical labels."""
    row = normalize_profile(profile)
    name = row.get('full_name')
    if name:
        patient.full_name = str(name)[:80]
        user = getattr(patient, 'user', None)
        if user is None and getattr(patient, 'user_id', None):
            from backend.database.models import User
            user = db.get(User, patient.user_id)
        if user is not None:
            user.full_name = str(name)[:80]
    if isinstance(row.get('age'), int):
        patient.date_of_birth = age_to_dob(row['age'])
    if row.get('affected_side') in ('left', 'right'):
        patient.affected_side = row['affected_side']
