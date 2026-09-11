"""Patient memory JSON for the consumer Talk agent. No video, no secrets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.config import STORAGE_DIR
from backend.intake import INTAKE_FIELDS, empty_intake


def memory_path(patient_id: str) -> Path:
    return STORAGE_DIR / 'patients' / patient_id / 'memory.json'


def default_memory() -> dict[str, Any]:
    return {
        'version': 1,
        'intake': empty_intake(),
        'last_peak_abduction': None,
        'last_peak_flexion': None,
        'last_pain_after': None,
        'last_session_id': None,
        'last_source': None,
        'session_summaries': [],
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
    summaries = row.get('session_summaries')
    base['session_summaries'] = summaries if isinstance(summaries, list) else []
    return base


def save_memory(patient_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = memory_path(patient_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = default_memory()
    if isinstance(payload, dict):
        row.update(payload)
    if not isinstance(row.get('intake'), dict):
        row['intake'] = empty_intake()
    if not isinstance(row.get('session_summaries'), list):
        row['session_summaries'] = []
    from backend.database.models import utcnow
    row['updated_at'] = utcnow().isoformat() + 'Z'
    path.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding='utf-8')
    return row


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
