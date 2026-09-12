"""Rolling session trend from stored measurements. Never names a disease stage as the patient's diagnosis."""
from __future__ import annotations

from typing import Any

from backend.memory import load_memory, save_memory


def rom_trend(peaks: list[Any]) -> str:
    values = []
    for item in peaks:
        try:
            if item is None or item == '':
                continue
            values.append(float(item))
        except (TypeError, ValueError):
            continue
    if len(values) < 2:
        return 'unknown'
    recent = values[-3:]
    delta = recent[-1] - recent[0]
    if delta >= 8:
        return 'improving'
    if delta <= -8:
        return 'declining'
    if len(recent) >= 3 and (max(recent) - min(recent)) < 6:
        return 'plateau'
    return 'stable'


def pacing_hint(pain: Any, trend: str) -> str:
    try:
        pain_n = None if pain is None or pain == '' else float(pain)
    except (TypeError, ValueError):
        pain_n = None
    if pain_n is not None and pain_n >= 7:
        return 'pain_led'
    if trend == 'declining' and pain_n is not None and pain_n >= 5:
        return 'pain_led'
    if trend == 'plateau':
        return 'stiffness_led'
    if trend == 'improving' and (pain_n is None or pain_n <= 4):
        return 'recovering'
    if pain_n is not None and pain_n >= 5:
        return 'pain_led'
    return 'unknown'


def memory_rag_slice(raw: dict[str, Any] | None) -> dict[str, Any]:
    row = (raw or {}).get('rag_summary') if isinstance(raw, dict) else None
    if not isinstance(row, dict):
        row = {}
    concerns = [str(item)[:40] for item in (row.get('concerns') or []) if item][:6]
    return {
        'pacing_hint': row.get('pacing_hint') if row.get('pacing_hint') in ('pain_led', 'stiffness_led', 'recovering') else 'unknown',
        'rom_trend': row.get('rom_trend') if row.get('rom_trend') in ('improving', 'declining', 'plateau', 'stable', 'unknown') else 'unknown',
        'concerns': concerns,
    }


def record_session_insight(
    patient_id: str,
    summary: dict[str, Any] | None,
    *,
    pain_after: int | None = None,
    movement: str | None = None,
    exercise_id: str | None = None,
    language: str = 'en-IN',
) -> dict[str, Any]:
    """Measured recap after a saved session. No FAQ text. No LLM wait on the finish path."""
    memory = load_memory(patient_id)
    peaks = [item.get('peak') for item in (memory.get('session_summaries') or []) if isinstance(item, dict)]
    if summary and summary.get('peak') is not None:
        last = peaks[-1] if peaks else None
        try:
            already = last is not None and abs(float(last) - float(summary['peak'])) < 0.05
        except (TypeError, ValueError):
            already = False
        if not already:
            peaks.append(summary.get('peak'))
    trend = rom_trend(peaks)
    pain = pain_after if pain_after is not None else memory.get('last_pain_after')
    hint = pacing_hint(pain, trend)
    hindi = language == 'hi-IN'
    peak = None if not summary else summary.get('peak')
    reps = None if not summary else summary.get('reps')
    source = None if not summary else summary.get('source')
    bits = []
    if peak is not None:
        bits.append(f"peak {int(round(float(peak)))}°" if not hindi else f"शिखर {int(round(float(peak)))}°")
    if reps is not None:
        bits.append(f"{reps} reps" if not hindi else f"{reps} रेप्स")
    if pain is not None:
        bits.append(f"pain after {pain}/10" if not hindi else f"बाद दर्द {pain}/10")
    if trend != 'unknown':
        bits.append(f"recent ROM {trend}" if not hindi else f"रेंज {trend}")
    if source == 'simulation':
        bits.append('labelled simulation' if not hindi else 'सिमुलेशन')
    elif source in ('live', 'phone'):
        bits.append('unvalidated live measure' if not hindi else 'अमान्यकृत माप')
    if hindi:
        tail = ' यह निदान नहीं है।'
    else:
        tail = ' This is not a diagnosis. Ask a physiotherapist about anything not measured here.'
    spoken = (('; '.join(bits) + '.' if bits else 'No new measurements were stored.') + tail)
    rag_summary = {
        'pacing_hint': hint,
        'rom_trend': trend,
        'concerns': memory_rag_slice(memory).get('concerns') or [],
        'last_citations': [],
        'last_spoken': spoken[:280],
    }
    memory['rag_summary'] = rag_summary
    save_memory(patient_id, memory)
    return {
        'spoken': spoken[:400],
        'pacing_hint': hint,
        'rom_trend': trend,
        'citations': [],
        'rag_used': False,
        'llm_used': False,
        'source': source,
        'not_a_diagnosis': True,
    }


def remember_concern(patient_id: str, intent: str, text: str) -> None:
    if intent not in ('SAFETY_CONCERN', 'ENCOURAGEMENT_NEEDED', 'GENERAL_QUESTION'):
        return
    memory = load_memory(patient_id)
    rag = dict(memory.get('rag_summary') or {}) if isinstance(memory.get('rag_summary'), dict) else {}
    concerns = [str(item) for item in (rag.get('concerns') or []) if item]
    label = (text or '')[:48].strip()
    if label and label not in concerns:
        concerns.append(label)
    rag['concerns'] = concerns[-6:]
    intents = [str(item) for item in (rag.get('last_intents') or []) if item]
    intents.append(intent)
    rag['last_intents'] = intents[-8:]
    memory['rag_summary'] = rag
    save_memory(patient_id, memory)
