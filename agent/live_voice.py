"""Live spoken coach. Claude talks from session metrics only. It cannot diagnose or invent ROM."""
from __future__ import annotations

import json
from typing import Any

import httpx

from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, VOICE_TIMEOUT_S
from agent.studio_browser import STUDIO_ACTIONS, match_action, normalize_action

ALLOWED_CONTEXT = (
    'scene', 'language', 'exercise', 'movement', 'source', 'safety', 'phase',
    'shoulder_angle', 'torso_lean', 'reps', 'goal', 'target', 'coverage',
    'feedback', 'intake_field', 'question', 'valid', 'fused_angle', 'awaiting_confirm',
    'last_transcript', 'last_spoken', 'pending_value',
)

END_MARKERS = (
    "that's all", 'thats all', 'goodbye', 'good bye', 'stop talking',
    'end conversation', 'hang up', 'बात खत्म', 'बस इतना',
)

SYSTEM = (
    'You are RehabAI, a live voice at a shoulder rehabilitation workstation. '
    'Speak like a calm physiotherapist assistant in an ongoing call, not a chatbot. '
    'This is not a diagnosis service. Never name a disease as confirmed. Never invent ROM, reps, pain scores, or IMU values. '
    'Use only numbers present in the session context. If a number is missing, say it was not measured. '
    'If safety is BLOCK or PAUSE, tell the patient to stop and rest; do not encourage more movement. '
    'Keep spoken replies under 28 words. Match the requested language. '
    'Do not ask for name, MRN, or other identifiers. '
    'Set action to end only when the caller clearly finishes the conversation. '
    'Set a workstation action only when they clearly ask to open that screen or start tracking.'
)


def sanitize_context(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in ALLOWED_CONTEXT:
        if key not in raw or raw[key] is None or raw[key] == '':
            continue
        value = raw[key]
        if isinstance(value, str) and _looks_like_identity(value):
            continue
        out[key] = value
    return out


def _looks_like_identity(value: str) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in ('patient_id', 'full_name', ' mrn', 'mrn:', '@hospital', '@demo'))


async def live_reply(transcript: str, context: dict[str, Any], language: str = 'en-IN') -> dict[str, Any]:
    ctx = sanitize_context(context)
    spoken_language = language if language in ('en-IN', 'hi-IN') else 'en-IN'
    text = (transcript or '').strip()[:500]
    if ctx.get('safety') in ('BLOCK', 'PAUSE'):
        spoken = (
            'Please stop. Rest the arm. A physiotherapist should review before you continue.'
            if spoken_language != 'hi-IN'
            else 'रुकिए। हाथ आराम दें। फिजियोथेरेपिस्ट की सलाह लें।'
        )
        return {'spoken': spoken, 'action': 'pause', 'engine': 'safety', 'context': ctx}

    if not text:
        spoken = 'I did not catch that. Please say it again.' if spoken_language != 'hi-IN' else 'सुनाई नहीं दिया। फिर से कहिए।'
        return {'spoken': spoken, 'action': 'none', 'engine': 'empty', 'context': ctx}

    if _wants_end(text):
        spoken = 'Alright. I am here if you need me again.' if spoken_language != 'hi-IN' else 'ठीक है। फिर जरूरत हो तो बोलिए।'
        return {'spoken': spoken, 'action': 'end', 'engine': 'end-phrase', 'context': ctx}

    quick = _fast_reply(text, ctx, spoken_language)
    if quick:
        return {**quick, 'context': ctx}

    if not ANTHROPIC_API_KEY:
        return {'spoken': _template(text, ctx, spoken_language), 'action': 'none', 'engine': 'deterministic-live', 'context': ctx}

    user = {
        'utterance': text,
        'language': spoken_language,
        'session': ctx,
        'rules': ['no_diagnosis', 'no_invented_measurements', 'cannot_override_BLOCK'],
    }
    schema = {
        'type': 'object',
        'properties': {
            'spoken': {'type': 'string'},
            'action': {'type': 'string', 'enum': list(STUDIO_ACTIONS)},
        },
        'required': ['spoken', 'action'],
        'additionalProperties': False,
    }
    try:
        async with httpx.AsyncClient(timeout=min(5.0, VOICE_TIMEOUT_S)) as client:
            response = await client.post(
                f'{ANTHROPIC_BASE_URL}/v1/messages',
                headers={
                    'x-api-key': ANTHROPIC_API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': ANTHROPIC_MODEL,
                    'max_tokens': 60,
                    'system': SYSTEM,
                    'messages': [{'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}],
                    'output_config': {'format': {'type': 'json_schema', 'schema': schema}},
                },
            )
        response.raise_for_status()
        payload = response.json()
        blocks = payload.get('content') or []
        block = next((item for item in blocks if item.get('type') == 'text'), None)
        result = json.loads((block or {}).get('text') or '{}')
        spoken = str(result.get('spoken') or '').strip()[:280]
        action = normalize_action(result.get('action'))
        if not spoken:
            spoken = _template(text, ctx, spoken_language)
        if ctx.get('safety') == 'BLOCK':
            action = 'pause'
        return {'spoken': spoken, 'action': action, 'engine': 'claude-live-agent', 'context': ctx}
    except (httpx.HTTPError, ValueError, TypeError, StopIteration, json.JSONDecodeError):
        return {
            'spoken': _template(text, ctx, spoken_language),
            'action': 'none',
            'engine': 'deterministic-live',
            'context': ctx,
            'provider_error': 'claude_unavailable',
        }


def _fast_reply(text: str, ctx: dict[str, Any], language: str) -> dict[str, Any] | None:
    hindi = language == 'hi-IN'
    if _wants_pause(text):
        spoken = (
            'Please pause and rest the arm. Tell a physiotherapist if the pain stays high.'
            if not hindi else 'रुकिए और हाथ आराम दें। दर्द बना रहे तो फिजियोथेरेपिस्ट को बताएं।'
        )
        return {'spoken': spoken, 'action': 'pause', 'engine': 'safety-phrase'}
    action = match_action(text)
    if _can_answer_from_metrics(text, ctx) or ctx.get('scene') == 'measure':
        return {'spoken': _metrics_spoken(ctx, hindi), 'action': action, 'engine': 'metrics'}
    if action != 'none':
        spoken = _action_ack(action, hindi)
        return {'spoken': spoken, 'action': action, 'engine': 'keyword-action'}
    return None


def _can_answer_from_metrics(text: str, ctx: dict[str, Any]) -> bool:
    lowered = ' '.join((text or '').lower().split())
    keys = (
        'how am i', 'how high', 'what angle', 'my angle', 'range of motion',
        'rep count', 'how many reps', 'torso lean', 'am i doing', 'my arm',
        'kitna', 'how is my', 'shoulder angle',
    )
    if not any(key in lowered for key in keys):
        return False
    return any(ctx.get(field) not in (None, '', False) for field in (
        'shoulder_angle', 'reps', 'torso_lean', 'feedback', 'fused_angle', 'coverage',
    ))


def _metrics_spoken(ctx: dict[str, Any], hindi: bool) -> str:
    bits = []
    if ctx.get('valid') and ctx.get('shoulder_angle') is not None:
        bits.append(f"{int(ctx['shoulder_angle'])} degrees" if not hindi else f"{int(ctx['shoulder_angle'])} डिग्री")
    if ctx.get('reps') is not None:
        bits.append(f"{ctx['reps']} reps" if not hindi else f"{ctx['reps']} रेप्स")
    if ctx.get('torso_lean') is not None:
        bits.append(f"lean {int(ctx['torso_lean'])} degrees" if not hindi else f"धड़ {int(ctx['torso_lean'])} डिग्री")
    if ctx.get('feedback'):
        bits.append(str(ctx['feedback']))
    if ctx.get('source') == 'simulation':
        bits.append('labelled simulation, not a live camera' if not hindi else 'यह सिमुलेशन है, लाइव कैमरा नहीं')
    if bits:
        spoken = '. '.join(bit.rstrip(' .') for bit in bits[:4]) + '.'
        return spoken[:280]
    if hindi:
        return 'इस स्क्रीन पर लाइव माप नहीं है। सेशन खोलकर मापें। यह निदान नहीं है।'
    return 'There is no live measurement on this screen. Open a session to measure. This is not a diagnosis.'


def _action_ack(action: str, hindi: bool) -> str:
    english = {
        'open_patients': 'Opening the patient list.',
        'open_settings': 'Opening settings.',
        'open_home': 'Opening your dashboard.',
        'confirm_tracking': 'Confirming tracking.',
        'start_session': 'Starting the session.',
    }
    hindi_map = {
        'open_patients': 'मरीज सूची खोल रहा हूँ।',
        'open_settings': 'सेटिंग खोल रहा हूँ।',
        'open_home': 'आपका डैशबोर्ड खोल रहा हूँ।',
        'confirm_tracking': 'ट्रैकिंग कन्फर्म कर रहा हूँ।',
        'start_session': 'सेशन शुरू कर रहा हूँ।',
    }
    return (hindi_map if hindi else english).get(action, 'Okay.')


def _template(text: str, ctx: dict[str, Any], language: str) -> str:
    hindi = language == 'hi-IN'
    feedback = ctx.get('feedback')
    scene = ctx.get('scene') or 'clinic'
    if scene == 'measure' and feedback:
        return str(feedback)
    if ctx.get('source') == 'simulation':
        note = ' यह सिमुलेशन है, लाइव कैमरा नहीं।' if hindi else ' This is a labelled simulation, not a live camera.'
    else:
        note = ''
    if hindi:
        return 'मैं रिहैबएआई हूँ। यह निदान नहीं है। गाइड आर्म की नकल करें और धड़ सीधा रखें।' + note
    return 'I am RehabAI. This is not a diagnosis. Copy the guide arm and keep your trunk quiet.' + note


def _wants_end(text: str) -> bool:
    lowered = (text or '').lower()
    return any(marker in lowered for marker in END_MARKERS)


def _wants_pause(text: str) -> bool:
    lowered = ' '.join((text or '').lower().split())
    if _wants_end(lowered):
        return False
    if lowered in ('stop', 'pause', 'रुकिए', 'रुको', 'रुक जाओ'):
        return True
    return any(marker in lowered for marker in (
        'it hurts', 'too painful', 'too much pain', 'please pause', 'pause now',
        'दर्द हो रहा', 'बहुत दर्द',
    ))
