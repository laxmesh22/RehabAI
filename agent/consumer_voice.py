"""Consumer Talk agent: one voice path for questionnaire → OCR report → personalized dashboard.

Deterministic parsing stays authoritative. Claude cannot invent scores.
"""
from __future__ import annotations

from typing import Any

from backend.intake import FIELD_BY_ID, SCRIPT, apply_confirmed_value, empty_intake
from backend.memory import (
    intake_complete, load_memory, next_intake_field, report_phase_complete,
    save_memory, skip_report_phase,
)
from backend.voice import parse_questionnaire_reply
from agent.live_voice import _wants_end, _wants_pause
from agent.retrieval import patient_memory_for_agent, sanitize_memory_slice


def prompt_for(field_id: str, language: str) -> str:
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
        return (
            'आपका डैशबोर्ड तैयार है। सेशन शुरू करना हो तो बोलिए।'
            if hindi else
            'Your dashboard is ready. Say start session when you want to measure.'
        )
    if intake_complete(intake) and not report_phase_complete(memory):
        return (
            'प्रश्नावली पूरी। रिपोर्ट फोटो अपलोड करें या स्किप बोलें। यह निदान नहीं है।'
            if hindi else
            'Questionnaire complete. Upload a report photo for OCR, or say skip. This is not a diagnosis.'
        )
    field = next_intake_field(intake) or SCRIPT[0]['id']
    opener = 'मैं रिहैबएआई हूँ। एक वॉइस एजेंट — दर्द, कामकाज, फिर रिपोर्ट। यह निदान नहीं है। ' if hindi else (
        'I am RehabAI, your single voice agent. I will record pain and function, then a report photo. This is not a diagnosis. '
    )
    return opener + prompt_for(field, language)


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
        bits.append(f'मूवमेंट दर्द {pain}/10' if hindi else f'movement pain {pain} out of 10')
    if abd is not None:
        bits.append(f'स्टोर्ड एब्डक्शन {round(abd)}°' if hindi else f'stored abduction {round(abd)}°')
    if memory_slice.get('ocr_abduction') is not None:
        bits.append(f'OCR एब्डक्शन {memory_slice["ocr_abduction"]}°' if hindi else f'OCR abduction {memory_slice["ocr_abduction"]}°')
    if memory_slice.get('last_source') == 'simulation':
        bits.append('यह सिमुलेशन लेबल है' if hindi else 'labelled simulation')
    if not bits:
        return (
            'रिकॉर्ड तैयार है। यह निदान नहीं है। डैशबोर्ड खोल रहा हूँ।'
            if hindi else
            'Your record is ready. This is not a diagnosis. Opening your dashboard.'
        )
    joined = ', '.join(bits)
    return (
        f'रिकॉर्ड तैयार। {joined}. डैशबोर्ड खोल रहा हूँ। यह निदान नहीं है।'
        if hindi else
        f'Record ready. {joined}. Opening your dashboard. This is not a diagnosis.'
    )


def _wants_skip_report(text: str) -> bool:
    lowered = (text or '').lower()
    return any(p in lowered for p in (
        'skip', 'no report', 'without report', 'later', 'not now',
        'स्किप', 'बाद में', 'रिपोर्ट नहीं',
    ))


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
        spoken = (
            'रुकिए और हाथ आराम दें। जरूरत हो तो फिजियोथेरेपिस्ट से बात करें।'
            if hindi else
            'Please pause and rest the arm. Talk to a physiotherapist if pain stays high.'
        )
        return {
            'spoken': spoken, 'action': 'pause', 'engine': 'consumer-safety',
            'intake': intake, 'parsed': None, 'memory': memory_slice,
            'phase': 'safety',
        }

    # Phase 2: questionnaire done → report OCR or skip → dashboard
    if intake_complete(intake) and not report_phase_complete(memory):
        if _wants_skip_report(text) or (text and any(p in text.lower() for p in ('dashboard', 'home', 'डैशबोर्ड', 'होम'))):
            skip_report_phase(patient_id)
            memory = load_memory(patient_id)
            memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)
            spoken = recap_from_memory(memory_slice, spoken_language)
            return {
                'spoken': spoken, 'action': 'open_home', 'engine': 'consumer-report-skipped',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        spoken = (
            'रिपोर्ट की फोटो अपलोड करें। OCR केवल छपे नंबर पढ़ेगा। स्किप कहें तो डैशबोर्ड खुल जाएगा।'
            if hindi else
            'Upload a report photo. OCR reads printed numbers only. Say skip to open your dashboard.'
        )
        return {
            'spoken': spoken, 'action': 'await_report', 'engine': 'consumer-report',
            'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'report',
        }

    if intake_complete(intake) and report_phase_complete(memory):
        if _wants_end(text):
            spoken = 'ठीक है। फिर जरूरत हो तो बोलिए।' if hindi else 'Alright. I am here if you need me again.'
            return {
                'spoken': spoken, 'action': 'end', 'engine': 'consumer-end',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        lowered = text.lower()
        if any(p in lowered for p in ('start session', 'start rehab', 'begin session', 'सेशन शुरू', 'शुरू करो')):
            spoken = 'सेशन शुरू कर रहा हूँ।' if hindi else 'Starting your session.'
            return {
                'spoken': spoken, 'action': 'start_session', 'engine': 'consumer-action',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        if any(p in lowered for p in ('dashboard', 'home', 'open home', 'डैशबोर्ड', 'होम')) or not text:
            spoken = greeting_prompt(spoken_language, memory) if not text else recap_from_memory(memory_slice, spoken_language)
            action = 'none' if not text else 'open_home'
            return {
                'spoken': spoken, 'action': action, 'engine': 'consumer-ready',
                'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
            }
        spoken = recap_from_memory(memory_slice, spoken_language)
        return {
            'spoken': spoken, 'action': 'open_home', 'engine': 'consumer-complete',
            'intake': intake, 'parsed': None, 'memory': memory_slice, 'phase': 'dashboard',
        }

    field = intake_field or next_intake_field(intake) or SCRIPT[0]['id']
    awaiting = bool(awaiting_confirm and pending_value is not None)

    if not text:
        if awaiting:
            spoken = confirm_for(field, int(pending_value), spoken_language)
        elif not intake_field and not any((intake.get('fields') or {}).values()):
            spoken = greeting_prompt(spoken_language, memory)
        else:
            spoken = prompt_for(field, spoken_language)
        return {
            'spoken': spoken,
            'action': 'none',
            'engine': 'consumer-prompt',
            'intake': intake,
            'intake_field': field,
            'awaiting_confirm': awaiting,
            'pending_value': pending_value if awaiting else None,
            'parsed': None,
            'memory': memory_slice,
            'phase': 'questionnaire',
        }

    parsed = await parse_questionnaire_reply(text, field, awaiting, spoken_language)
    intent = parsed.get('intent')

    if intent == 'safety_pause':
        return {
            'spoken': parsed.get('spoken'), 'action': 'pause', 'engine': parsed.get('engine') or 'consumer-safety',
            'intake': intake, 'parsed': parsed, 'memory': memory_slice, 'phase': 'safety',
        }

    if awaiting and intent == 'confirm_yes':
        intake = apply_confirmed_value(intake, field, int(pending_value), 'voice', text)
        memory['intake'] = intake
        save_memory(patient_id, memory)
        memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)
        if intake_complete(intake):
            spoken = (
                'सवाल पूरे। अब रिपोर्ट फोटो अपलोड करें, या स्किप बोलें।'
                if hindi else
                'Questions saved. Next, upload a report photo for OCR, or say skip.'
            )
            return {
                'spoken': spoken, 'action': 'await_report', 'engine': 'consumer-intake-done',
                'intake': intake, 'parsed': parsed, 'memory': memory_slice, 'phase': 'report',
            }
        nxt = next_intake_field(intake)
        spoken = ('सेव हो गया। ' if hindi else 'Saved. ') + prompt_for(nxt, spoken_language)
        return {
            'spoken': spoken, 'action': 'none', 'engine': 'consumer-next',
            'intake': intake, 'intake_field': nxt, 'awaiting_confirm': False,
            'pending_value': None, 'parsed': parsed, 'memory': memory_slice, 'phase': 'questionnaire',
        }

    if awaiting and intent == 'confirm_no':
        spoken = ('ठीक है। नंबर फिर से बोलिए। ' if hindi else 'Okay. Say the number again. ') + prompt_for(field, spoken_language)
        return {
            'spoken': spoken, 'action': 'none', 'engine': 'consumer-retry',
            'intake': intake, 'intake_field': field, 'awaiting_confirm': False,
            'pending_value': None, 'parsed': parsed, 'memory': memory_slice, 'phase': 'questionnaire',
        }

    if intent == 'number' and parsed.get('parsed_value') is not None:
        value = int(parsed['parsed_value'])
        spoken = confirm_for(field, value, spoken_language)
        return {
            'spoken': spoken, 'action': 'none', 'engine': parsed.get('engine') or 'consumer-confirm',
            'intake': intake, 'intake_field': field, 'awaiting_confirm': True,
            'pending_value': value, 'parsed': parsed, 'memory': memory_slice, 'phase': 'questionnaire',
        }

    spoken = parsed.get('spoken') or (
        'नंबर समझ नहीं आया। फिर से बोलिए।' if hindi else 'I did not catch a number. Please say it again.'
    )
    return {
        'spoken': spoken, 'action': 'none', 'engine': parsed.get('engine') or 'consumer-unknown',
        'intake': intake, 'intake_field': field, 'awaiting_confirm': awaiting,
        'pending_value': pending_value if awaiting else None, 'parsed': parsed, 'memory': memory_slice,
        'phase': 'questionnaire',
    }
