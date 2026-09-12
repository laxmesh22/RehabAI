"""Post-generation check: clinical claims must trace to retrieved chunks or measured numbers."""
from __future__ import annotations

import re
from typing import Any

_NUMBER = re.compile(r'\d+(?:\.\d+)?')
_ALWAYS_NEVER = re.compile(r'\b(?:always|never|guarantee|cured|cure you)\b', re.I)
_MEDS = (
    'ibuprofen', 'paracetamol', 'acetaminophen', 'aspirin', 'diclofenac',
    'nsaid', 'steroid', 'injection', 'cortisone', 'morphine', 'tramadol',
    'antibiotic', 'tablet', 'capsule',
)
_DIAGNOSIS = (
    'you have frozen', 'you have adhesive', 'you are in the freezing',
    'you are in the frozen', 'you are in the thawing',
    'diagnosed with', 'this confirms', 'camera shows you have',
)


def _numbers_from(text: str) -> set[str]:
    return {m.group(0).lstrip('0') or '0' for m in _NUMBER.finditer(text or '')}


def allowed_numbers(chunks: list[dict[str, Any]] | None, metrics: dict[str, Any] | None = None) -> set[str]:
    # 1 and 3 appear in "1 slow breath" / "3D guide" hedges, not as clinical scores.
    allowed = {'1', '3'}
    for chunk in chunks or []:
        allowed |= _numbers_from(str(chunk.get('content') or ''))
        allowed |= _numbers_from(str(chunk.get('content_hi') or ''))
    metrics = metrics or {}
    pain_keys = ('pain', 'pain_after', 'pain_rest', 'pain_movement', 'last_pain', 'last_pain_after')
    if any(metrics.get(key) not in (None, '') for key in pain_keys):
        allowed |= {str(n) for n in range(0, 11)}
    for value in metrics.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            allowed.add(str(int(round(value))))
        elif isinstance(value, str):
            allowed |= _numbers_from(value)
    return allowed


def ungrounded_reason(spoken: str, chunks: list[dict[str, Any]] | None, metrics: dict[str, Any] | None = None) -> str | None:
    text = spoken or ''
    lowered = text.lower()
    if any(phrase in lowered for phrase in _DIAGNOSIS):
        return 'diagnosis_claim'
    if any(word in lowered for word in _MEDS):
        return 'medication_claim'
    blob = ' '.join(
        str((chunk or {}).get('content') or '') + ' ' + str((chunk or {}).get('content_hi') or '')
        for chunk in (chunks or [])
    ).lower()
    if _ALWAYS_NEVER.search(text) and not _ALWAYS_NEVER.search(blob):
        return 'absolute_claim'
    allowed = allowed_numbers(chunks, metrics)
    for raw in _NUMBER.findall(text):
        key = raw.lstrip('0') or '0'
        if key not in allowed and raw not in allowed:
            return 'invented_number'
    return None


def fallback_spoken(chunks: list[dict[str, Any]] | None, hindi: bool = False) -> str:
    if hindi:
        return 'मैं उस संख्या का अनुमान नहीं लगाऊँगा। यह निदान नहीं है।'
    return 'I will not guess that number. This is not a diagnosis.'


def grounded_or_fallback(
    spoken: str,
    chunks: list[dict[str, Any]] | None,
    metrics: dict[str, Any] | None = None,
    *,
    hindi: bool = False,
) -> tuple[str, bool, str | None]:
    text = ' '.join((spoken or '').split()).strip()
    if not text:
        return fallback_spoken(chunks, hindi), False, 'empty'
    reason = ungrounded_reason(text, chunks, metrics)
    if reason:
        return fallback_spoken(chunks, hindi), False, reason
    return text[:280], True, None
