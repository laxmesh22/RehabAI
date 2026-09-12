"""Live spoken session coach. Claude talks from measured metrics + 3D guide state only.

Conservative: measurement support, not diagnosis. Never invents ROM/pain.
During measure, the user can talk while the FollowAvatar demo runs; replies may sync demo_target.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from agent.coach_policy import CONSERVATIVE_RULES, clamp_demo_target, num
from agent.studio_browser import STUDIO_ACTIONS, match_action, normalize_action
from agent.retrieval.grounding import grounded_or_fallback
from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, VOICE_TIMEOUT_S

ALLOWED_CONTEXT = (
    'scene', 'language', 'exercise', 'movement', 'source', 'safety', 'phase',
    'shoulder_angle', 'peak', 'torso_lean', 'reps', 'goal', 'target', 'coverage',
    'feedback', 'intake_field', 'question', 'valid', 'fused_angle', 'awaiting_confirm',
    'last_transcript', 'last_spoken', 'pending_value',
    'guide_cue', 'demo_target', 'avatar_demo', 'coach_mode',
    'avatar_phase', 'avatar_reps', 'avatar_demo_angle',
)

END_MARKERS = (
    "that's all", 'thats all', 'goodbye', 'good bye', 'stop talking',
    'end conversation', 'hang up', 'बात खत्म', 'बस इतना',
)

MEASURE_ACTIONS = list(STUDIO_ACTIONS)

RED_FLAG_RE = re.compile(
    r'\b(numb(?:ness)?|tingling|shooting pain|sudden severe|cannot move|can\'t move)\b|'
    r'सुन्न|झुनझुनी|अचानक तेज',
    re.I,
)

SYSTEM_CLINIC = (
    'You are RehabAI, a live voice at a shoulder rehab workstation in an ongoing call. '
    'Not a chatbot, not a FAQ, not a diagnosis service. '
    + ' '.join(CONSERVATIVE_RULES) + ' '
    'Answer only from the live session metrics and what they just said. '
    'If a clinical claim is not in those metrics, say a physiotherapist should answer it. '
    'Keep spoken under 28 words. Match the language. '
    'Do not ask for identifiers. action=end only when they finish. '
    'Set a workstation action only when they clearly ask.'
)

SYSTEM_MEASURE = (
    'You are RehabAI, a live AI coach during a follow-along session. '
    'A 3D guide is on screen. Coach from measured angle, peak, target, reps, lean, '
    'avatar_phase / avatar_reps / avatar_demo_angle when present, and their words. '
    + ' '.join(CONSERVATIVE_RULES) + ' '
    'Do not read a script or FAQ. Do not invent bone poses — the mesh follows telemetry only. '
    'If they ask what is normal and you lack a measured number, defer to the physiotherapist. '
    'demo_target may rise only up to measured peak (syncs the 3D demo ceiling, not patient bones). '
    'Spoken under 32 words. Match the language. '
    'Return JSON: spoken, action, demo_target.'
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


async def live_reply(transcript: str, context: dict[str, Any], language: str = 'en-IN',
                    memory_slice: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = sanitize_context(context)
    spoken_language = language if language in ('en-IN', 'hi-IN') else 'en-IN'
    hindi = spoken_language == 'hi-IN'
    text = (transcript or '').strip()[:500]
    measure = (ctx.get('scene') or '') == 'measure' or (ctx.get('coach_mode') or '') == 'session'
    metrics = {
        'angle': ctx.get('shoulder_angle'),
        'peak': ctx.get('peak'),
        'target': ctx.get('target'),
        'reps': ctx.get('reps'),
        'goal': ctx.get('goal'),
        'lean': ctx.get('torso_lean'),
        'coverage': ctx.get('coverage'),
    }

    if ctx.get('safety') in ('BLOCK', 'PAUSE'):
        spoken = (
            'Please stop. Rest the arm. A physiotherapist should review before you continue.'
            if not hindi else
            'रुकिए। हाथ आराम दें। फिजियोथेरेपिस्ट की सलाह लें।'
        )
        return _pack(spoken, 'pause', 'safety', ctx)

    if RED_FLAG_RE.search(text):
        spoken = (
            'Please stop. Rest the arm. A physiotherapist should review before you continue.'
            if not hindi else
            'रुकिए। हाथ आराम दें। फिजियोथेरेपिस्ट की सलाह लें।'
        )
        return _pack(spoken, 'pause', 'safety-redflag', ctx)

    if _wants_end(text):
        spoken = 'Alright. I am here if you need me again.' if not hindi else 'ठीक है। फिर जरूरत हो तो बोलिए।'
        return _pack(spoken, 'end', 'end-phrase', ctx)

    if _wants_pause(text):
        spoken = (
            'Please pause and rest the arm. Tell a physiotherapist if the pain stays high.'
            if not hindi else
            'रुकिए और हाथ आराम दें। दर्द बना रहे तो फिजियोथेरेपिस्ट को बताएं।'
        )
        return _pack(spoken, 'pause', 'safety-phrase', ctx)

    action = match_action(text)

    peak = num(ctx.get('peak')) or num(ctx.get('shoulder_angle'))
    target = num(ctx.get('target'))

    if not ANTHROPIC_API_KEY:
        return _pack(
            _offline_spoken(ctx, spoken_language),
            action if action != 'none' else 'none',
            'llm-loop-fallback',
            ctx,
        )

    system = SYSTEM_MEASURE if measure else SYSTEM_CLINIC
    user = {
        'utterance': text or '(call started — greet and listen, do not read a questionnaire)',
        'language': spoken_language,
        'session': ctx,
        'known_metrics': {k: v for k, v in metrics.items() if v not in (None, '')},
        'memory': {
            'last_peak': None if not memory_slice else memory_slice.get('last_peak_abduction'),
            'last_pain': None if not memory_slice else memory_slice.get('last_pain_after'),
        },
        'rules': CONSERVATIVE_RULES,
    }
    schema = {
        'type': 'object',
        'properties': {
            'spoken': {'type': 'string'},
            'action': {'type': 'string', 'enum': MEASURE_ACTIONS},
            'demo_target': {'type': ['number', 'null']},
        },
        'required': ['spoken', 'action', 'demo_target'],
        'additionalProperties': False,
    }
    try:
        async with httpx.AsyncClient(timeout=min(8.0, VOICE_TIMEOUT_S)) as client:
            response = await client.post(
                f'{ANTHROPIC_BASE_URL}/v1/messages',
                headers={
                    'x-api-key': ANTHROPIC_API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': ANTHROPIC_MODEL,
                    'max_tokens': 90 if measure else 80,
                    'system': system,
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
        out_action = normalize_action(result.get('action'))
        if action != 'none' and out_action in ('', 'none'):
            out_action = action
        if not spoken:
            spoken = _offline_spoken(ctx, spoken_language)
        spoken, grounded, _reason = grounded_or_fallback(spoken, [], metrics, hindi=hindi)
        if not grounded:
            spoken = _offline_spoken(ctx, spoken_language)
        engine = 'claude-live-agent' if grounded else 'llm-loop-fallback'
        if ctx.get('safety') == 'BLOCK':
            out_action = 'pause'
        demo = clamp_demo_target(result.get('demo_target'), peak, target) if measure else None
        pack = _pack(spoken, out_action, engine, ctx)
        if demo is not None:
            pack['demo_target'] = demo
        return pack
    except (httpx.HTTPError, ValueError, TypeError, StopIteration, json.JSONDecodeError):
        pack = _pack(_offline_spoken(ctx, spoken_language), action if action != 'none' else 'none', 'llm-loop-fallback', ctx)
        pack['provider_error'] = 'claude_unavailable'
        return pack


def _pack(spoken: str, action: str, engine: str, ctx: dict[str, Any],
          retrieved: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'spoken': spoken,
        'action': action,
        'engine': engine,
        'context': ctx,
        'demo_target': None,
        'intent': None,
        'citations': [],
        'rag_used': False,
    }


def _offline_spoken(ctx: dict[str, Any], language: str) -> str:
    """Last resort if Claude is down. Sensor numbers only — not a FAQ."""
    hindi = language == 'hi-IN'
    bits = ['मैं सुन रहा हूँ। यह निदान नहीं है।' if hindi else 'I am listening. This is not a diagnosis.']
    peak = num(ctx.get('peak'))
    if peak is not None and ((ctx.get('scene') or '') == 'measure' or (ctx.get('coach_mode') or '') == 'session'):
        bits.append(f'शिखर {int(peak)}°।' if hindi else f'Measured peak {int(peak)}°.')
    if ctx.get('source') == 'simulation':
        bits.append('सिमुलेशन।' if hindi else 'Labelled simulation.')
    return ' '.join(bits)[:280]


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
