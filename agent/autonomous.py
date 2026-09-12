"""Autonomous LLM-in-the-loop voice turn.

The model decides what to say. The backend only:
- transcribes
- extracts values that are actually in the utterance
- enforces BLOCK / pause
- refuses invented numbers and diagnoses

There is no scripted question list and no FAQ retrieval for speech.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, VOICE_TIMEOUT_S
from backend.intake import FIELD_BY_ID, INTAKE_FIELDS, NUMBER_WORDS, parse_utterance
from backend.profile import PROFILE_FIELDS, parse_age, parse_name, parse_side
from agent.live_voice import RED_FLAG_RE
from agent.studio_browser import STUDIO_ACTIONS, normalize_action

ACTIONS = list(STUDIO_ACTIONS) + ['await_report', 'skip_report']

_NAME_CUED = re.compile(
    r'(my name is|i am|i\'m|this is|call me|मेरा नाम|मैं हूँ)',
    re.I,
)
_NAME_STRONG = re.compile(r'(my name is|call me|मेरा नाम)', re.I)
_NUMBERISH = set(NUMBER_WORDS) | {
    'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
    'seventeen', 'eighteen', 'nineteen',
    'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety',
    'hundred',
}
_AGE_CUED = re.compile(r'\b(age|years?\s+old|साल)\b', re.I)
_AGE_ONLY = re.compile(
    r'^\s*(?:i am |i\'m |age(?:\s+is)?\s*)?\d{1,3}(?:\s*years?\s*old)?\s*[.!]?\s*$',
    re.I,
)
_NAME_BLOCK = re.compile(
    r'\b(pain|hurt|hurts|hurting|shoulder|degree|degrees|year|years|old|'
    r'left|right|lift|move|moving|movement|stop|pause|report|session|'
    r'click|clicking|numb|number|hello|hi|hey|rehab|diagnosis|frozen|'
    r'thank|thanks|please|listen|start|history|not|in|my|the|and)\b',
    re.I,
)
_FIELD_CUES = {
    'pain_rest': re.compile(r'\b(at rest|resting|आराम)\b', re.I),
    'pain_movement': re.compile(r'\b(mov(?:e|es|ed|ing|ement)?|raising|lift|हिला|उठा)\b', re.I),
    'difficulty_dressing': re.compile(r'\b(dress|shirt|blouse|कपड़)\b', re.I),
    'difficulty_grooming': re.compile(r'\b(groom|hair|wash face|बाल|सज)\b', re.I),
    'difficulty_overhead': re.compile(r'\b(overhead|shelf|ऊपर|overhead)\b', re.I),
    'difficulty_behind_back': re.compile(r'\b(behind|back|zipper|पीठ)\b', re.I),
}

SYSTEM = (
    'You are RehabAI, a live voice physiotherapist assistant in an ongoing phone call. '
    'This is NOT a questionnaire, NOT a FAQ bot, and NOT a diagnosis service. '
    'Have a natural conversation. Cover still-needed goals when it fits, in any order, one thought at a time. '
    'You may answer questions, coach, or navigate. Never invent name, age, pain, function, ROM, or reps. '
    'save_field/save_value only when that value was clearly in the caller\'s last utterance. '
    'If they report numbness, sudden severe pain, or ask to stop: action=pause. '
    'Never say they have frozen shoulder. Never name medication. '
    'Spoken under 30 words. Match the language. '
    'Do not read a questionnaire or remaining-field list. '
    'Return JSON only: spoken, action, save_field, save_value, demo_target.'
)


def missing_goals(profile: dict[str, Any], intake: dict[str, Any], report_phase: str) -> list[str]:
    goals = []
    for fid in PROFILE_FIELDS:
        if profile.get(fid) in (None, ''):
            goals.append(fid)
    for fid in INTAKE_FIELDS:
        if not isinstance(intake.get(fid), int):
            spec = FIELD_BY_ID[fid]
            goals.append(f"{fid} ({spec['min']}-{spec['max']})")
    if report_phase == 'needed' and not goals:
        goals.append('optional clinic report photo, or skip')
    return goals


def _token_is_numberish(token: str) -> bool:
    for piece in re.split(r'[\s\-]+', (token or '').lower()):
        if piece and piece in _NUMBERISH:
            return True
    return False


def _clean_name(name: str | None) -> str | None:
    if not name or name.lower() in _NUMBERISH:
        return None
    parts = []
    for token in str(name).split():
        if _token_is_numberish(token):
            return None
        if _NAME_BLOCK.search(token):
            break
        if len(token) < 2:
            return None
        parts.append(token)
    if not 1 <= len(parts) <= 4:
        return None
    return ' '.join(parts)


def extract_volunteered(
    text: str,
    profile: dict[str, Any],
    intake: dict[str, Any],
) -> list[tuple[str, str, Any]]:
    """Pull only values present in the utterance. Never invent."""
    raw = (text or '').strip()
    if not raw:
        return []
    if RED_FLAG_RE.search(raw):
        return [('safety', 'pause', True)]
    updates: list[tuple[str, str, Any]] = []
    missing_profile = [fid for fid in PROFILE_FIELDS if profile.get(fid) in (None, '')]
    missing_intake = [fid for fid in INTAKE_FIELDS if not isinstance(intake.get(fid), int)]

    if 'affected_side' in missing_profile:
        side = parse_side(raw)
        if side:
            updates.append(('profile', 'affected_side', side))

    if 'age' in missing_profile:
        age = parse_age(raw)
        if age is not None and (_AGE_CUED.search(raw) or _AGE_ONLY.match(raw)):
            updates.append(('profile', 'age', age))

    if 'full_name' in missing_profile:
        lowered = raw.lower().strip()
        if lowered not in NUMBER_WORDS and not re.fullmatch(r'\d+', lowered):
            strong = bool(_NAME_STRONG.search(raw))
            cued = strong or bool(_NAME_CUED.search(raw))
            if cued or (missing_profile and missing_profile[0] == 'full_name'):
                # "I am 45" / "I am forty five" is age, not a name.
                if not strong and (parse_age(raw) is not None or _AGE_CUED.search(raw)):
                    name = None
                else:
                    name = _clean_name(parse_name(raw))
                if name:
                    updates.append(('profile', 'full_name', name))

    cued_intake = None
    for fid, cue in _FIELD_CUES.items():
        if fid in missing_intake and cue.search(raw):
            parsed = parse_utterance(raw, fid)
            if parsed.get('intent') == 'number' and parsed.get('parsed_value') is not None:
                updates.append(('intake', fid, int(parsed['parsed_value'])))
                cued_intake = fid
                break
    if cued_intake is None and missing_intake and not missing_profile:
        parsed = parse_utterance(raw, missing_intake[0])
        if parsed.get('intent') == 'number' and parsed.get('parsed_value') is not None:
            updates.append(('intake', missing_intake[0], int(parsed['parsed_value'])))
        elif parsed.get('intent') == 'safety_pause':
            updates.append(('safety', 'pause', True))
    return updates


def value_in_transcript(field: str, value: Any, text: str) -> bool:
    raw = (text or '').strip()
    if value is None or not raw:
        return False
    if field in PROFILE_FIELDS:
        if field == 'full_name':
            name = _clean_name(parse_name(raw))
            return bool(name) and str(value).lower() in raw.lower()
        if field == 'age':
            try:
                want = int(value)
            except (TypeError, ValueError):
                return False
            if parse_age(raw) != want:
                return False
            return bool(_AGE_CUED.search(raw) or _AGE_ONLY.match(raw))
        if field == 'affected_side':
            return parse_side(raw) == str(value)
        return False
    if field in INTAKE_FIELDS:
        parsed = parse_utterance(raw, field)
        try:
            return parsed.get('intent') == 'number' and int(parsed.get('parsed_value')) == int(value)
        except (TypeError, ValueError):
            return False
    return False


def fallback_spoken(
    language: str,
    *,
    missing: list[str],
    saved: list[tuple[str, str, Any]],
    first_name: str = '',
) -> str:
    """Last resort if Claude is down. Not a questionnaire and not a FAQ."""
    hindi = language == 'hi-IN'
    bits = []
    if saved and not any(kind == 'safety' for kind, _f, _v in saved):
        last = saved[-1][2]
        bits.append(f'{last}।' if hindi else f'{last}.')
    bits.append('मैं सुन रहा हूँ। यह निदान नहीं है।' if hindi else 'I am listening. This is not a diagnosis.')
    return ' '.join(bits)[:280]


async def claude_voice_turn(payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any] | None:
    if not ANTHROPIC_API_KEY:
        return None
    schema = {
        'type': 'object',
        'properties': {
            'spoken': {'type': 'string'},
            'action': {'type': 'string', 'enum': ACTIONS},
            'save_field': {'type': ['string', 'null']},
            'save_value': {'type': ['string', 'number', 'null']},
            'demo_target': {'type': ['number', 'null']},
        },
        'required': ['spoken', 'action', 'save_field', 'save_value', 'demo_target'],
        'additionalProperties': False,
    }
    user = dict(payload or {})
    if not str(user.get('patient_said') or '').strip():
        user['patient_said'] = '(call started — greet and listen; do not read a questionnaire)'
    try:
        async with httpx.AsyncClient(timeout=min(timeout or 8.0, VOICE_TIMEOUT_S)) as client:
            response = await client.post(
                f'{ANTHROPIC_BASE_URL}/v1/messages',
                headers={
                    'x-api-key': ANTHROPIC_API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': ANTHROPIC_MODEL,
                    'max_tokens': 180,
                    'system': SYSTEM,
                    'messages': [{
                        'role': 'user',
                        'content': json.dumps(user, ensure_ascii=False, default=str)[:8000],
                    }],
                    'output_config': {'format': {'type': 'json_schema', 'schema': schema}},
                },
            )
        response.raise_for_status()
        raw = ''
        for item in (response.json().get('content') or []):
            if isinstance(item, dict) and item.get('type') == 'text':
                raw += str(item.get('text') or '')
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.strip('`')
            if raw.startswith('json'):
                raw = raw[4:].strip()
        data = json.loads(raw)
        spoken = ' '.join(str(data.get('spoken') or '').split())[:280]
        if not spoken:
            return None
        action = normalize_action(data.get('action'))
        if data.get('action') in ('await_report', 'skip_report'):
            action = str(data.get('action'))
        save_field = data.get('save_field')
        if save_field not in PROFILE_FIELDS and save_field not in INTAKE_FIELDS:
            save_field = None
        return {
            'spoken': spoken,
            'action': action or 'none',
            'save_field': save_field,
            'save_value': data.get('save_value'),
            'demo_target': data.get('demo_target'),
            'engine': 'llm-loop',
        }
    except Exception:
        return None
