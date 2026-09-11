"""Claude-driven consumer voice agent for MEDHA PS 8 shoulder assessment support.

Claude plans spoken questions from missing PS slots (same configured ANTHROPIC_MODEL).
Numeric scores stay deterministic from the patient's words — never invented by the LLM.
Latency opts that do *not* downgrade the model: one-turn number save, single STT engine.
Set REHABAI_CONSUMER_CLAUDE=0 only to force local prompt fallback.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, VOICE_TIMEOUT_S
from backend.intake import FIELD_BY_ID, INTAKE_FIELDS, SCRIPT, apply_confirmed_value, empty_intake, parse_utterance
from backend.memory import (
    intake_complete, load_memory, next_intake_field, report_phase_complete,
    save_memory, skip_report_phase,
)
from agent.live_voice import _wants_end, _wants_pause
from agent.retrieval import patient_memory_for_agent, sanitize_memory_slice

# Claude ON by default when a key exists. Opt out with REHABAI_CONSUMER_CLAUDE=0.
_CONSUMER_CLAUDE_FLAG = os.environ.get('REHABAI_CONSUMER_CLAUDE', '1').strip().lower()
CONSUMER_CLAUDE = _CONSUMER_CLAUDE_FLAG not in ('0', 'false', 'no', 'off')

PS_GOALS = (
    'pain_rest (0-10)',
    'pain_movement (0-10)',
    'difficulty_dressing (0-4)',
    'difficulty_grooming (0-4)',
    'difficulty_overhead (0-4)',
    'difficulty_behind_back (0-4)',
    'optional clinic report photo for OCR',
)

SYSTEM = (
    'You are RehabAI, the autonomous voice agent inside a patient phone app for MEDHA PS 8 '
    '(adhesive capsulitis assessment *support* and guided rehab). '
    'This is NOT a diagnosis service. Never name a disease as confirmed. Never invent ROM, pain, or function scores. '
    'Decide the next spoken question dynamically from what is still missing in the slot list. '
    'Speak briefly but naturally (under 28 words). Match the requested language. '
    'When you need a number, ask in plain language; do not invent it. '
    'When enough slots are filled, set need_report true so the app can collect a report photo. '
    'Return JSON only: '
    '{"spoken":"...","focus_field":"pain_rest|pain_movement|difficulty_dressing|difficulty_grooming|'
    'difficulty_overhead|difficulty_behind_back|null","need_report":false,"open_dashboard":false}.'
)

_SHORT_ASK = {
    'pain_rest': ('Pain at rest, zero to ten?', 'आराम में दर्द, शून्य से दस?'),
    'pain_movement': ('Pain while moving the arm, zero to ten?', 'हाथ हिलाते दर्द, शून्य से दस?'),
    'difficulty_dressing': ('Dressing: zero none to four unable?', 'कपड़े: शून्य आसान, चार नहीं?'),
    'difficulty_grooming': ('Grooming: zero to four?', 'सजावट: शून्य से चार?'),
    'difficulty_overhead': ('Reaching overhead: zero to four?', 'ऊपर पहुँचना: शून्य से चार?'),
    'difficulty_behind_back': ('Behind the back: zero to four?', 'पीठ पीछे: शून्य से चार?'),
}


def prompt_for(field_id: str, language: str) -> str:
    pair = _SHORT_ASK.get(field_id)
    if pair:
        return pair[1] if language == 'hi-IN' else pair[0]
    spec = FIELD_BY_ID[field_id]
    return spec['prompt_hi'] if language == 'hi-IN' else spec['prompt_en']


def confirm_for(field_id: str, value: int, language: str) -> str:
    spec = FIELD_BY_ID[field_id]
    template = spec['confirm_hi'] if language == 'hi-IN' else spec['confirm_en']
    return template.format(value=value)


def greeting_prompt(language: str, memory: dict[str, Any] | None = None) -> str:
    hindi = language == 'hi-IN'
    memory = memory or {}
    intake = memory.get('intake') or empty_intake()
    if intake_complete(intake) and report_phase_complete(memory):
        return 'डैशबोर्ड तैयार है।' if hindi else 'Your dashboard is ready.'
    if intake_complete(intake):
        return 'रिपोर्ट फोटो या स्किप।' if hindi else 'Upload a report photo, or say skip.'
    return 'बात शुरू करें।' if hindi else 'Tap Talk — I will ask what we still need.'


def recap_from_memory(memory_slice: dict[str, Any], language: str) -> str:
    hindi = language == 'hi-IN'
    scores = memory_slice.get('intake_scores') or {}
    abd = (memory_slice.get('stored_abduction') or {}).get('current')
    if abd is None:
        abd = memory_slice.get('last_peak_abduction')
    pain = scores.get('pain_movement')
    if pain is None:
        pain = (memory_slice.get('stored_pain_movement') or {}).get('current')
    bits = []
    if pain is not None:
        bits.append(f'pain {pain}/10' if not hindi else f'दर्द {pain}/10')
    if abd is not None:
        bits.append(f'abduction {round(abd)}°' if not hindi else f'एब्डक्शन {round(abd)}°')
    if memory_slice.get('ocr_abduction') is not None:
        bits.append(f'OCR {memory_slice["ocr_abduction"]}°')
    if not bits:
        return (
            'रिकॉर्ड तैयार। डैशबोर्ड। यह निदान नहीं है।'
            if hindi else
            'Record ready. Opening dashboard. This is not a diagnosis.'
        )
    return (
        f'रिकॉर्ड: {", ".join(bits)}. डैशबोर्ड। यह निदान नहीं है।'
        if hindi else
        f'Record: {", ".join(bits)}. Opening dashboard. This is not a diagnosis.'
    )


def _wants_skip_report(text: str) -> bool:
    lowered = (text or '').lower()
    return any(p in lowered for p in (
        'skip', 'no report', 'without report', 'later', 'not now',
        'स्किप', 'बाद में', 'रिपोर्ट नहीं',
    ))


def _missing_slots(intake: dict[str, Any]) -> list[str]:
    fields = (intake or {}).get('fields') or {}
    return [fid for fid in INTAKE_FIELDS if fid not in fields]


def _fallback_ask(field_id: str, language: str) -> dict[str, Any]:
    return {
        'spoken': prompt_for(field_id, language),
        'focus_field': field_id,
        'need_report': False,
        'open_dashboard': False,
        'engine': 'consumer-fast',
    }


def _reask(field_id: str, language: str) -> str:
    hindi = language == 'hi-IN'
    base = prompt_for(field_id, language)
    return ('फिर से — ' if hindi else 'Again — ') + base


async def _claude_next_turn(
    *,
    language: str,
    missing: list[str],
    filled: dict[str, Any],
    transcript: str,
    memory_slice: dict[str, Any],
) -> dict[str, Any] | None:
    if not CONSUMER_CLAUDE or not ANTHROPIC_API_KEY:
        return None
    payload = {
        'language': language,
        'missing_slots': missing,
        'filled_scores': filled,
        'patient_said': (transcript or '')[:400],
        'stored_metrics': {
            'abduction': memory_slice.get('stored_abduction'),
            'pain_movement': memory_slice.get('stored_pain_movement'),
            'ocr_abduction': memory_slice.get('ocr_abduction'),
        },
        'ps_goals': list(PS_GOALS),
        'model': ANTHROPIC_MODEL,
        'rules': [
            'Ask only for missing slots or report upload.',
            'Never invent a number.',
            'Do not diagnose frozen shoulder.',
            'Keep spoken under 28 words.',
            'If the patient was unclear, briefly re-ask the focused missing slot.',
        ],
    }
    body = {
        'model': ANTHROPIC_MODEL,
        'max_tokens': 220,
        'system': SYSTEM,
        'messages': [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
    }
    try:
        async with httpx.AsyncClient(timeout=min(12.0, VOICE_TIMEOUT_S)) as client:
            response = await client.post(
                f'{ANTHROPIC_BASE_URL}/v1/messages',
                headers={
                    'x-api-key': ANTHROPIC_API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json=body,
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
        focus = data.get('focus_field')
        if focus not in INTAKE_FIELDS:
            focus = missing[0] if missing else None
        if not spoken:
            return None
        return {
            'spoken': spoken,
            'focus_field': focus,
            'need_report': bool(data.get('need_report')) and not missing,
            'open_dashboard': bool(data.get('open_dashboard')) and not missing,
            'engine': 'consumer-autonomous',
        }
    except Exception:
        return None


async def _plan_next(
    *,
    language: str,
    missing: list[str],
    filled: dict[str, Any],
    transcript: str,
    memory_slice: dict[str, Any],
    prefix: str = '',
) -> dict[str, Any]:
    if not missing:
        hindi = language == 'hi-IN'
        return {
            'spoken': prefix + (
                'हो गया। रिपोर्ट फोटो या स्किप।'
                if hindi else
                'Got it. Upload a report photo, or say skip.'
            ),
            'focus_field': None,
            'need_report': True,
            'open_dashboard': False,
            'engine': 'consumer-intake-done',
        }
    plan = await _claude_next_turn(
        language=language, missing=missing, filled=filled,
        transcript=transcript, memory_slice=memory_slice,
    )
    if not plan:
        plan = _fallback_ask(missing[0], language)
    if prefix:
        plan = {**plan, 'spoken': prefix + plan['spoken']}
    return plan


async def consumer_reply(
    transcript: str,
    *,
    patient_id: str,
    language: str = 'en-IN',
    db=None,
    pending_value: int | None = None,
    awaiting_confirm: bool = False,
    intake_field: str | None = None,
) -> dict[str, Any]:
    spoken_language = language if language in ('en-IN', 'hi-IN') else 'en-IN'
    hindi = spoken_language == 'hi-IN'
    text = (transcript or '').strip()[:500]
    memory = load_memory(patient_id)
    intake = memory.get('intake') or empty_intake()
    memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)

    if _wants_pause(text):
        spoken = 'रुकिए। हाथ आराम दें।' if hindi else 'Please pause and rest the arm.'
        return {
            'spoken': spoken, 'action': 'pause', 'engine': 'consumer-safety',
            'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'safety',
        }

    if intake_complete(intake) and not report_phase_complete(memory):
        if _wants_skip_report(text) or (text and any(p in text.lower() for p in ('dashboard', 'home', 'डैशबोर्ड', 'होम'))):
            skip_report_phase(patient_id)
            memory = load_memory(patient_id)
            memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)
            return {
                'spoken': recap_from_memory(memory_slice, spoken_language),
                'action': 'open_home', 'engine': 'consumer-report-skipped',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        return {
            'spoken': (
                'रिपोर्ट फोटो अपलोड करें, या स्किप कहें।'
                if hindi else
                'Upload a report photo for OCR, or say skip.'
            ),
            'action': 'await_report', 'engine': 'consumer-report',
            'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'report',
        }

    if intake_complete(intake) and report_phase_complete(memory):
        if _wants_end(text):
            return {
                'spoken': 'ठीक है।' if hindi else 'Alright.',
                'action': 'end', 'engine': 'consumer-end',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        lowered = text.lower()
        if any(p in lowered for p in ('start session', 'start rehab', 'begin session', 'सेशन शुरू', 'शुरू करो')):
            return {
                'spoken': 'सेशन शुरू।' if hindi else 'Starting your session.',
                'action': 'start_session', 'engine': 'consumer-action',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        if not text:
            return {
                'spoken': greeting_prompt(spoken_language, memory),
                'action': 'none', 'engine': 'consumer-ready',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        return {
            'spoken': recap_from_memory(memory_slice, spoken_language),
            'action': 'open_home', 'engine': 'consumer-complete',
            'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
        }

    missing = _missing_slots(intake)
    field = intake_field or next_intake_field(intake) or (missing[0] if missing else SCRIPT[0]['id'])
    awaiting = bool(awaiting_confirm and pending_value is not None)
    filled = {fid: intake.get(fid) for fid in INTAKE_FIELDS if isinstance(intake.get(fid), int)}

    # Opening turn — Claude plans the first ask when configured; local fallback otherwise
    if not text and not awaiting:
        plan = await _plan_next(
            language=spoken_language, missing=missing, filled=filled,
            transcript='', memory_slice=memory_slice,
        )
        return {
            'spoken': plan['spoken'],
            'action': 'await_report' if plan.get('need_report') else 'none',
            'engine': plan.get('engine') or 'consumer-fast',
            'intake': intake,
            'intake_field': plan.get('focus_field') or field,
            'awaiting_confirm': False,
            'pending_value': None,
            'parsed': None,
            'memory': memory_slice,
            'phase': 'report' if plan.get('need_report') else 'questionnaire',
        }

    # Deterministic parse only — never invent numbers via LLM
    parsed = parse_utterance(text, field, awaiting)
    parsed['engine'] = 'deterministic'
    intent = parsed.get('intent')

    if intent == 'safety_pause':
        return {
            'spoken': parsed.get('spoken'), 'action': 'pause',
            'engine': 'consumer-safety',
            'intake': intake, 'parsed': parsed, 'memory': memory_slice, 'phase': 'safety',
        }

    async def _after_save(value: int, source_text: str) -> dict[str, Any]:
        nonlocal intake, memory, memory_slice, missing, filled
        intake = apply_confirmed_value(intake, field, int(value), 'voice', source_text)
        memory['intake'] = intake
        save_memory(patient_id, memory)
        memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)
        missing = _missing_slots(intake)
        filled = {fid: intake.get(fid) for fid in INTAKE_FIELDS if isinstance(intake.get(fid), int)}
        prefix = (f'{value}. ' if not hindi else f'{value}. ')
        plan = await _plan_next(
            language=spoken_language, missing=missing, filled=filled,
            transcript=source_text, memory_slice=memory_slice, prefix=prefix,
        )
        return {
            'spoken': plan['spoken'],
            'action': 'await_report' if plan.get('need_report') else 'none',
            'engine': plan.get('engine') or 'consumer-next',
            'intake': intake,
            'intake_field': plan.get('focus_field') or (missing[0] if missing else None),
            'awaiting_confirm': False,
            'pending_value': None,
            'parsed': parsed,
            'memory': memory_slice,
            'phase': 'report' if plan.get('need_report') else 'questionnaire',
        }

    # Legacy confirm path (client still sending awaiting_confirm)
    if awaiting and intent == 'confirm_yes':
        return await _after_save(int(pending_value), text)

    if awaiting and intent == 'confirm_no':
        plan = await _claude_next_turn(
            language=spoken_language, missing=missing, filled=filled,
            transcript=text, memory_slice=memory_slice,
        )
        spoken = (plan or {}).get('spoken') or (
            'ठीक है, नंबर फिर से।' if hindi else 'Okay, say the number again.'
        )
        return {
            'spoken': spoken, 'action': 'none',
            'engine': (plan or {}).get('engine') or 'consumer-retry',
            'intake': intake, 'intake_field': field, 'awaiting_confirm': False,
            'pending_value': None, 'parsed': parsed, 'memory': memory_slice, 'phase': 'questionnaire',
        }

    # Clear number → save + Claude asks next (still one patient turn; model not skipped)
    if intent == 'number' and parsed.get('parsed_value') is not None:
        return await _after_save(int(parsed['parsed_value']), text)

    # Unclear — Claude rephrases; local short reask only if Claude unavailable
    plan = await _claude_next_turn(
        language=spoken_language, missing=missing or [field], filled=filled,
        transcript=text, memory_slice=memory_slice,
    )
    if plan:
        return {
            'spoken': plan['spoken'],
            'action': 'none',
            'engine': plan.get('engine') or 'consumer-autonomous',
            'intake': intake,
            'intake_field': plan.get('focus_field') or field,
            'awaiting_confirm': False,
            'pending_value': None,
            'parsed': parsed,
            'memory': memory_slice,
            'phase': 'questionnaire',
        }
    return {
        'spoken': _reask(field, spoken_language),
        'action': 'none',
        'engine': 'consumer-reask',
        'intake': intake,
        'intake_field': field,
        'awaiting_confirm': False,
        'pending_value': None,
        'parsed': parsed,
        'memory': memory_slice,
        'phase': 'questionnaire',
    }
