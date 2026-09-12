"""Grounded spoken reply from retrieved protocol chunks + measured numbers only."""
from __future__ import annotations

from typing import Any

from agent.retrieval.grounding import fallback_spoken, grounded_or_fallback
from agent.retrieval.knowledge import action_gate, retrieve_for_turn
from agent.session_intel import memory_rag_slice, pacing_hint, rom_trend


def metrics_for_grounding(ctx: dict[str, Any] | None) -> dict[str, Any]:
    ctx = ctx or {}
    return {
        'angle': ctx.get('shoulder_angle') or ctx.get('angle') or ctx.get('measured_angle'),
        'peak': ctx.get('peak') or ctx.get('measured_peak'),
        'target': ctx.get('target') or ctx.get('session_target'),
        'reps': ctx.get('reps'),
        'goal': ctx.get('goal'),
        'lean': ctx.get('torso_lean'),
        'pain': ctx.get('pain') or ctx.get('pain_after'),
        'coverage': ctx.get('coverage'),
    }


def rag_context_from_memory(memory_slice: dict[str, Any] | None) -> dict[str, Any]:
    memory_slice = memory_slice or {}
    rag = memory_rag_slice({'rag_summary': memory_slice.get('rag_summary')}) if 'rag_summary' in memory_slice else memory_rag_slice(memory_slice)
    if memory_slice.get('pacing_hint') or memory_slice.get('rom_trend'):
        rag = {
            'pacing_hint': memory_slice.get('pacing_hint') or rag.get('pacing_hint'),
            'rom_trend': memory_slice.get('rom_trend') or rag.get('rom_trend'),
            'concerns': memory_slice.get('concerns') or rag.get('concerns') or [],
        }
    peaks = []
    for item in memory_slice.get('session_summaries') or []:
        if isinstance(item, dict) and item.get('peak') is not None:
            peaks.append(item.get('peak'))
    if memory_slice.get('last_peak_abduction') is not None:
        peaks.append(memory_slice.get('last_peak_abduction'))
    trend = rag.get('rom_trend') if rag.get('rom_trend') not in (None, 'unknown') else rom_trend(peaks)
    pain = (memory_slice.get('stored_pain_movement') or {}).get('current')
    if pain is None:
        pain = memory_slice.get('last_pain_after')
    hint = rag.get('pacing_hint') if rag.get('pacing_hint') not in (None, 'unknown') else pacing_hint(pain, trend)
    return {'pacing_hint': hint, 'rom_trend': trend, 'concerns': rag.get('concerns') or []}


def pack_retrieval(
    *,
    patient_text: str,
    ctx: dict[str, Any] | None = None,
    memory_slice: dict[str, Any] | None = None,
    scene: str | None = None,
    k: int = 3,
) -> dict[str, Any]:
    ctx = ctx or {}
    rag = rag_context_from_memory(memory_slice)
    movement = ctx.get('movement') or ctx.get('exercise') or ctx.get('avatar_demo')
    retrieved = retrieve_for_turn(
        patient_text=patient_text,
        movement=str(movement) if movement else None,
        action_category=action_gate(ctx.get('safety')),
        pacing_hint=rag.get('pacing_hint'),
        session_trend=rag.get('rom_trend'),
        scene=scene or ctx.get('scene'),
        k=k,
    )
    retrieved['pacing_hint'] = rag.get('pacing_hint')
    retrieved['rom_trend'] = rag.get('rom_trend')
    retrieved['concerns'] = rag.get('concerns')
    return retrieved


def speak_retrieved(retrieved: dict[str, Any], *, hindi: bool, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    spoken, grounded, reason = grounded_or_fallback(
        fallback_spoken(retrieved.get('chunks'), hindi=hindi),
        retrieved.get('chunks'),
        metrics,
        hindi=hindi,
    )
    return {
        'spoken': spoken,
        'grounded': grounded,
        'ungrounded_reason': reason,
        'intent': retrieved.get('intent'),
        'citations': retrieved.get('citations') or [],
        'rag_used': True,
        'chunks': retrieved.get('chunks') or [],
        'pacing_hint': retrieved.get('pacing_hint'),
        'rom_trend': retrieved.get('rom_trend'),
    }
