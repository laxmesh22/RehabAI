"""Patient memory JSON for the consumer Talk agent. No video, no secrets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.config import STORAGE_DIR
from backend.intake import INTAKE_FIELDS, empty_intake
from backend.profile import empty_profile, normalize_profile


def memory_path(patient_id: str) -> Path:
    return STORAGE_DIR / 'patients' / patient_id / 'memory.json'


# Measured before any rehab session, in this order.
BASELINE_MOVEMENTS = ('abduction', 'flexion')


def empty_baseline() -> dict[str, Any]:
    return {
        'phase': 'needed',  # needed | done | skipped
        'abduction_deg': None,
        'flexion_deg': None,
        'source': None,
        'measured_at': None,
        'session_ids': [],
    }


def normalize_baseline(raw: Any) -> dict[str, Any]:
    row = empty_baseline()
    if not isinstance(raw, dict):
        return row
    phase = raw.get('phase')
    row['phase'] = phase if phase in ('needed', 'done', 'skipped') else 'needed'
    for key in ('abduction_deg', 'flexion_deg'):
        value = raw.get(key)
        try:
            row[key] = None if value is None else round(float(value), 1)
        except (TypeError, ValueError):
            row[key] = None
    row['source'] = raw.get('source') if isinstance(raw.get('source'), str) else None
    row['measured_at'] = raw.get('measured_at') if isinstance(raw.get('measured_at'), str) else None
    ids = raw.get('session_ids')
    row['session_ids'] = [str(item) for item in ids][-6:] if isinstance(ids, list) else []
    return row


def default_memory() -> dict[str, Any]:
    return {
        'version': 2,
        'profile': empty_profile(),
        'intake': empty_intake(),
        'report_phase': 'needed',  # needed | done | skipped
        'baseline': empty_baseline(),
        'talk_skipped': False,
        'reports': [],
        'last_peak_abduction': None,
        'last_peak_flexion': None,
        'last_pain_after': None,
        'last_session_id': None,
        'last_source': None,
        'session_summaries': [],
        'talk_turns': [],
        'rag_summary': {
            'pacing_hint': 'unknown',
            'rom_trend': 'unknown',
            'concerns': [],
            'last_intents': [],
        },
        'updated_at': None,
    }


def load_memory(patient_id: str) -> dict[str, Any]:
    path = memory_path(patient_id)
    if not path.is_file():
        return default_memory()
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default_memory()
    if not isinstance(row, dict):
        return default_memory()
    base = default_memory()
    base.update({k: row.get(k, base.get(k)) for k in base})
    intake = row.get('intake') if isinstance(row.get('intake'), dict) else empty_intake()
    base['intake'] = {**empty_intake(), **intake}
    base['profile'] = normalize_profile(row.get('profile') if isinstance(row.get('profile'), dict) else {})
    summaries = row.get('session_summaries')
    base['session_summaries'] = summaries if isinstance(summaries, list) else []
    turns = row.get('talk_turns')
    base['talk_turns'] = turns if isinstance(turns, list) else []
    reports = row.get('reports')
    base['reports'] = reports if isinstance(reports, list) else []
    phase = row.get('report_phase')
    base['report_phase'] = phase if phase in ('needed', 'done', 'skipped') else 'needed'
    base['baseline'] = normalize_baseline(row.get('baseline'))
    return base


def save_memory(patient_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = memory_path(patient_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = default_memory()
    if isinstance(payload, dict):
        row.update(payload)
    if not isinstance(row.get('intake'), dict):
        row['intake'] = empty_intake()
    row['profile'] = normalize_profile(row.get('profile') if isinstance(row.get('profile'), dict) else {})
    if not isinstance(row.get('session_summaries'), list):
        row['session_summaries'] = []
    if not isinstance(row.get('talk_turns'), list):
        row['talk_turns'] = []
    if not isinstance(row.get('reports'), list):
        row['reports'] = []
    if not isinstance(row.get('rag_summary'), dict):
        row['rag_summary'] = default_memory()['rag_summary']
    if row.get('report_phase') not in ('needed', 'done', 'skipped'):
        row['report_phase'] = 'needed'
    row['baseline'] = normalize_baseline(row.get('baseline'))
    from backend.database.models import utcnow
    row['updated_at'] = utcnow().isoformat() + 'Z'
    path.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding='utf-8')
    return row


def append_report(patient_id: str, report: dict[str, Any], phase: str = 'done') -> dict[str, Any]:
    row = load_memory(patient_id)
    reports = list(row.get('reports') or [])
    reports.append(report)
    row['reports'] = reports[-10:]
    row['report_phase'] = phase if phase in ('needed', 'done', 'skipped') else 'done'
    metrics = (report or {}).get('metrics') or {}
    if metrics.get('abduction_deg') is not None:
        row['last_peak_abduction'] = metrics['abduction_deg']
    if metrics.get('flexion_deg') is not None:
        row['last_peak_flexion'] = metrics['flexion_deg']
    return save_memory(patient_id, row)


def skip_report_phase(patient_id: str) -> dict[str, Any]:
    row = load_memory(patient_id)
    row['report_phase'] = 'skipped'
    return save_memory(patient_id, row)


def skip_talk_phase(patient_id: str) -> dict[str, Any]:
    """Unlock consumer home without completing Talk. Does not invent pain/ROM values."""
    row = load_memory(patient_id)
    intake = row.get('intake') or empty_intake()
    intake['confirmed'] = True
    intake['source'] = 'talk_skip'
    row['intake'] = intake
    row['report_phase'] = 'skipped'
    baseline = normalize_baseline(row.get('baseline'))
    baseline['phase'] = 'skipped'
    row['baseline'] = baseline
    row['talk_skipped'] = True
    return save_memory(patient_id, row)


def skip_baseline_phase(patient_id: str) -> dict[str, Any]:
    """Let a patient reach the dashboard without a camera baseline. Measures nothing."""
    row = load_memory(patient_id)
    baseline = normalize_baseline(row.get('baseline'))
    baseline['phase'] = 'skipped'
    row['baseline'] = baseline
    return save_memory(patient_id, row)


def record_baseline_measurement(
    patient_id: str,
    movement: str,
    peak: Any,
    session_id: str,
    source: str | None = None,
) -> dict[str, Any]:
    """Store one measured baseline movement. A missing peak is never filled in."""
    row = load_memory(patient_id)
    baseline = normalize_baseline(row.get('baseline'))
    key = 'flexion_deg' if movement == 'flexion' else 'abduction_deg'
    try:
        value = None if peak is None else round(float(peak), 1)
    except (TypeError, ValueError):
        value = None
    if value is not None:
        baseline[key] = value
        if source:
            baseline['source'] = source
        from backend.database.models import utcnow
        baseline['measured_at'] = utcnow().isoformat() + 'Z'
        ids = [item for item in baseline['session_ids'] if item != session_id]
        ids.append(session_id)
        baseline['session_ids'] = ids[-6:]
    if baseline['phase'] != 'skipped' and next_baseline_movement(baseline) is None:
        baseline['phase'] = 'done'
    row['baseline'] = baseline
    return save_memory(patient_id, row)


def next_baseline_movement(baseline: dict[str, Any] | None) -> str | None:
    """Which baseline movement still has no measured value."""
    row = normalize_baseline(baseline)
    for movement in BASELINE_MOVEMENTS:
        if row['{}_deg'.format(movement)] is None:
            return movement
    return None


def baseline_complete(memory: dict[str, Any] | None) -> bool:
    baseline = normalize_baseline((memory or {}).get('baseline'))
    return baseline['phase'] in ('done', 'skipped') or next_baseline_movement(baseline) is None


def report_phase_complete(memory: dict[str, Any] | None) -> bool:
    return (memory or {}).get('report_phase') in ('done', 'skipped')


def next_intake_field(intake: dict[str, Any] | None) -> str | None:
    fields = (intake or {}).get('fields') or {}
    for fid in INTAKE_FIELDS:
        if fid not in fields:
            return fid
    return None


def intake_complete(intake: dict[str, Any] | None) -> bool:
    return bool((intake or {}).get('confirmed')) or next_intake_field(intake) is None


def merge_session_into_memory(patient_id: str, summary: dict[str, Any], session_id: str, movement: str = 'abduction') -> dict[str, Any]:
    row = load_memory(patient_id)
    peak = summary.get('peak')
    source = summary.get('source')
    if movement == 'flexion' and peak is not None:
        row['last_peak_flexion'] = peak
    elif peak is not None:
        row['last_peak_abduction'] = peak
    if summary.get('pain_after') is not None:
        row['last_pain_after'] = summary.get('pain_after')
    row['last_session_id'] = session_id
    row['last_source'] = source
    note = {
        'session_id': session_id,
        'reps': summary.get('reps'),
        'peak': peak,
        'source': source,
        'safety': (summary.get('safety') or {}).get('level') if isinstance(summary.get('safety'), dict) else summary.get('safety'),
        'movement': movement,
    }
    summaries = list(row.get('session_summaries') or [])
    summaries.append(note)
    row['session_summaries'] = summaries[-20:]
    return save_memory(patient_id, row)
