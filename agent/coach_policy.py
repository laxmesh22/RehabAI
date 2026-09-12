"""Shared conservative coaching policy for RehabAI.

Aligned with measurement-first rehab tooling (e.g. PhysiApp-style reporting) and
College of Physiotherapists AI principles: AI supports, does not replace clinical
judgement; never invent measurements; escalate pain/safety; clinician owns diagnosis.
"""
from __future__ import annotations

from typing import Any

CONSERVATIVE_RULES = [
    'This is assessment support and guided practice — not a diagnosis service.',
    'Never invent ROM, pain, reps, lean, or IMU values. Use only measured numbers in context.',
    'Never confirm a disease name. Prefer "your clinician" / "physiotherapist review".',
    'If safety is BLOCK or PAUSE: stop movement, rest the arm, do not encourage more ROM.',
    'If the patient reports pain or asks to stop: pause, rest, do not push higher.',
    'One short cue at a time. Prefer hold/return/rest over aggressive progression.',
    'The 3D guide demonstrates the approved movement; coach the patient to follow it calmly.',
    'If measured_peak clearly exceeds session_target and safety is ALLOW: acknowledge measured ability, '
    'suggest controlled hold or return; demo_target may rise only up to measured_peak, never above it.',
    'Do not prescribe a new exercise program. Stay on the current session exercise.',
]


def num(value: Any) -> float | None:
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_demo_target(demo: Any, peak: Any, target: Any) -> float | None:
    """Allow demo raise only when measured peak exceeds session target; never above peak."""
    d = num(demo)
    p = num(peak)
    t = num(target)
    if d is None or p is None:
        return None
    d = round(min(d, p), 1)
    if t is not None and d < t:
        return None
    if t is not None and p < t + 8:
        return None
    return d


def above_target(peak: Any, target: Any, margin: float = 8.0) -> bool:
    p = num(peak)
    t = num(target)
    return p is not None and t is not None and t > 0 and p >= t + margin
