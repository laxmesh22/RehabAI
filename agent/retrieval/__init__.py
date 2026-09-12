"""Retrieval is limited to structured patient metrics. Raw video is never sent to the LLM."""
from __future__ import annotations

from typing import Any

from backend.memory import load_memory
from backend.intake import INTAKE_FIELDS
from backend.profile import normalize_profile
from agent.tools.clinical import calculate_patient_progress, get_rom_history, get_pain_history
from agent.session_intel import memory_rag_slice


IDENTITY_KEYS = {
    'full_name', 'mrn', 'patient_id', 'email', 'name', 'date_of_birth', 'clinician_diagnosis',
}


def sanitize_memory_slice(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Drop identifiers and keep only numbers / labels the voice agent may speak."""
    if not isinstance(raw, dict):
        return {}
    intake = raw.get('intake') if isinstance(raw.get('intake'), dict) else {}
    scores = {fid: intake.get(fid) for fid in INTAKE_FIELDS if isinstance(intake.get(fid), int)}
    profile = normalize_profile(raw.get('profile') if isinstance(raw.get('profile'), dict) else {})
    summaries = []
    for item in (raw.get('session_summaries') or [])[-5:]:
        if not isinstance(item, dict):
            continue
        summaries.append({
            'reps': item.get('reps'),
            'peak': item.get('peak'),
            'source': item.get('source'),
            'safety': item.get('safety'),
            'movement': item.get('movement'),
        })
    return {
        'intake_scores': scores,
        'intake_confirmed': bool(intake.get('confirmed')),
        'profile_complete': bool(profile.get('complete')),
        'profile_age': profile.get('age') if isinstance(profile.get('age'), int) else None,
        'profile_side': profile.get('affected_side') if profile.get('affected_side') in ('left', 'right') else None,
        'report_phase': raw.get('report_phase') if raw.get('report_phase') in ('needed', 'done', 'skipped') else 'needed',
        'ocr_abduction': None,
        'ocr_flexion': None,
        'ocr_pain': None,
        'last_peak_abduction': raw.get('last_peak_abduction'),
        'last_peak_flexion': raw.get('last_peak_flexion'),
        'last_pain_after': raw.get('last_pain_after'),
        'last_source': raw.get('last_source'),
        'session_summaries': summaries,
        'rag_summary': memory_rag_slice(raw),
        'pacing_hint': memory_rag_slice(raw).get('pacing_hint'),
        'rom_trend': memory_rag_slice(raw).get('rom_trend'),
        'concerns': memory_rag_slice(raw).get('concerns'),
    }


def patient_memory_for_agent(db, patient_id: str) -> dict[str, Any]:
    memory = sanitize_memory_slice(load_memory(patient_id))
    progress = calculate_patient_progress(db, patient_id)
    if isinstance(progress, dict) and not progress.get('error'):
        abd = progress.get('abduction') or {}
        flex = progress.get('flexion') or {}
        pain = progress.get('pain_movement') or {}
        memory['stored_abduction'] = {
            'baseline': abd.get('baseline'),
            'current': abd.get('current'),
            'change': abd.get('change'),
        }
        memory['stored_flexion'] = {
            'baseline': flex.get('baseline'),
            'current': flex.get('current'),
        }
        memory['stored_pain_movement'] = {
            'current': pain.get('current'),
        }
    reports = load_memory(patient_id).get('reports') or []
    if reports:
        last = reports[-1] if isinstance(reports[-1], dict) else {}
        metrics = last.get('metrics') if isinstance(last.get('metrics'), dict) else {}
        memory['ocr_abduction'] = metrics.get('abduction_deg')
        memory['ocr_flexion'] = metrics.get('flexion_deg')
        memory['ocr_pain'] = metrics.get('pain_score')
        memory['ocr_engine'] = last.get('engine')
    rom = get_rom_history(db, patient_id) or []
    pain_rows = get_pain_history(db, patient_id) or []
    memory['rom_point_count'] = len(rom)
    memory['pain_point_count'] = len(pain_rows)
    # Never leak identity keys even if a caller merges badly.
    for key in list(memory.keys()):
        if key in IDENTITY_KEYS or any(token in key for token in ('name', 'mrn', 'email')):
            memory.pop(key, None)
    return memory
