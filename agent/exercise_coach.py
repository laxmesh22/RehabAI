"""Adaptive exercise coach. Claude watches *measured* session metrics only.

Conservative session companion for the 3D FollowAvatar demo. Never invents ROM/pain.
May acknowledge when measured peak exceeds the demo target and sync demo_target ≤ peak.
Speech is LLM-in-the-loop, not FAQ retrieval.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from agent.coach_policy import CONSERVATIVE_RULES, above_target, clamp_demo_target, num
from agent.live_voice import RED_FLAG_RE
from agent.retrieval.grounding import grounded_or_fallback
from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, VOICE_TIMEOUT_S

SYSTEM = (
    'You are RehabAI, a conservative real-time coach watching a shoulder follow-along session. '
    'A 3D avatar demonstrates the approved exercise; you coach with words and optional demo_target sync. '
    + ' '.join(CONSERVATIVE_RULES) + ' '
    'Do not read a script or FAQ. Coach only from measured angle, peak, target, reps, lean, and what they said. '
    'If they ask what is normal and you lack a measured number, defer to the physiotherapist. '
    'Keep spoken under 24 words. Match the language. '
    'Return JSON only: '
    '{"spoken":"...","demo_target":null_or_number,"note":"optional clinician note"}.'
)


def _metrics(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        'angle': payload.get('shoulder_angle') or payload.get('angle') or payload.get('measured_angle'),
        'peak': payload.get('peak') or payload.get('measured_peak'),
        'target': payload.get('target') or payload.get('session_target'),
        'reps': payload.get('reps'),
        'goal': payload.get('goal'),
        'lean': payload.get('torso_lean'),
        'coverage': payload.get('coverage'),
    }


def _pack(
    spoken: str,
    *,
    demo_target: float | None = None,
    note: str | None = None,
    engine: str = 'coach-deterministic',
    llm_used: bool = False,
) -> dict[str, Any]:
    return {
        'spoken': spoken,
        'demo_target': demo_target,
        'note': note,
        'engine': engine,
        'llm_used': llm_used,
        'intent': None,
        'citations': [],
        'rag_used': False,
    }


def _deterministic_cue(payload: dict[str, Any], hindi: bool) -> dict[str, Any]:
    safety = str(payload.get('safety') or 'ALLOW').upper()
    angle = num(payload.get('angle'))
    peak = num(payload.get('peak')) or angle
    target = num(payload.get('target')) or 0
    said = (payload.get('patient_said') or '').lower()

    if safety in ('BLOCK', 'PAUSE') or RED_FLAG_RE.search(said):
        spoken = 'रुकिए। हाथ आराम दें।' if hindi else 'Please stop and rest the arm.'
        return _pack(spoken, engine='coach-safety')

    if any(tok in said for tok in ('hurt', 'pain', 'दर्द', 'stop', 'pause', 'रुक')):
        spoken = (
            'दर्द है तो रुकें। गाइड का इंतज़ार करें।'
            if hindi else
            'If it hurts, pause. Wait with the guide — do not push higher.'
        )
        return _pack(spoken, note='Patient reported pain/stop in talk.', engine='coach-pain')

    if above_target(peak, target):
        demo_target = clamp_demo_target(min(peak, (target or 0) + 40), peak, target)
        spoken = (
            f'मापा गया शिखर {round(peak)}°। यह निदान नहीं है।'
            if hindi else
            f'Measured peak {round(peak)}°. This is not a diagnosis.'
        )
        return _pack(
            spoken,
            demo_target=demo_target,
            note=f'Measured peak {round(peak)}° exceeded session target {round(target)}°.',
        )

    spoken = 'मैं सुन रहा हूँ। यह निदान नहीं है।' if hindi else 'I am listening. This is not a diagnosis.'
    return _pack(spoken)


async def adaptive_coach(payload: dict[str, Any]) -> dict[str, Any]:
    language = payload.get('language') if payload.get('language') in ('en-IN', 'hi-IN') else 'en-IN'
    hindi = language == 'hi-IN'
    safety = str(payload.get('safety') or 'ALLOW').upper()
    fallback = _deterministic_cue(payload, hindi)

    if (
        safety in ('BLOCK', 'PAUSE')
        or fallback.get('engine') in ('coach-safety', 'coach-pain')
        or not ANTHROPIC_API_KEY
    ):
        return fallback

    body = {
        'model': ANTHROPIC_MODEL,
        'max_tokens': 140,
        'system': SYSTEM,
        'messages': [{
            'role': 'user',
            'content': json.dumps({
                'language': language,
                'exercise': payload.get('exercise'),
                'movement': payload.get('movement'),
                'avatar_demo': payload.get('avatar_demo') or payload.get('exercise'),
                'phase': payload.get('phase'),
                'safety': safety,
                'session_target': num(payload.get('target')),
                'measured_angle': num(payload.get('angle')),
                'measured_peak': num(payload.get('peak')),
                'reps': payload.get('reps'),
                'goal': payload.get('goal'),
                'pipeline_feedback': (payload.get('feedback') or '')[:160],
                'patient_said': (payload.get('patient_said') or '')[:200],
                'guide_cue': (payload.get('guide_cue') or '')[:120],
                'rules': CONSERVATIVE_RULES,
            }, ensure_ascii=False),
        }],
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
        spoken = ' '.join(str(data.get('spoken') or '').split())[:220]
        if not spoken:
            return fallback
        spoken, grounded, _reason = grounded_or_fallback(
            spoken, [], _metrics(payload), hindi=hindi,
        )
        if not grounded:
            return fallback
        peak = num(payload.get('peak')) or num(payload.get('angle'))
        target = num(payload.get('target'))
        demo = clamp_demo_target(data.get('demo_target'), peak, target)
        return _pack(
            spoken,
            demo_target=demo,
            note=(str(data.get('note') or '')[:200] or None),
            engine='coach-claude' if grounded else 'coach-deterministic',
            llm_used=grounded,
        )
    except Exception:
        return fallback
