"""Autonomous consumer voice loop. Claude talks; numbers are parsed from speech only."""
from __future__ import annotations

import os
import re
from typing import Any

from backend.config import ANTHROPIC_API_KEY
from backend.intake import INTAKE_FIELDS, apply_confirmed_value, empty_intake
from backend.memory import (
    baseline_complete, intake_complete, load_memory, next_baseline_movement,
    report_phase_complete, save_memory, skip_report_phase,
)
from backend.profile import (
    PROFILE_FIELDS, apply_profile_to_patient, apply_profile_value, display_first_name,
    next_profile_field, normalize_profile, profile_complete,
)
from agent.autonomous import (
    claude_voice_turn, extract_volunteered, fallback_spoken, missing_goals, value_in_transcript,
)
from agent.live_voice import RED_FLAG_RE, _wants_end, _wants_pause
from agent.retrieval import patient_memory_for_agent, sanitize_memory_slice
from agent.retrieval.grounding import grounded_or_fallback
from agent.studio_browser import match_action

_CONSUMER_CLAUDE_FLAG = os.environ.get('REHABAI_CONSUMER_CLAUDE', '1').strip().lower()
CONSUMER_CLAUDE = _CONSUMER_CLAUDE_FLAG not in ('0', 'false', 'no', 'off')


def greeting_prompt(language: str, memory: dict[str, Any] | None = None) -> str:
    hindi = language == 'hi-IN'
    return 'मैं सुन रहा हूँ। यह निदान नहीं है।' if hindi else 'I am listening. This is not a diagnosis.'


def _talk_turns(memory: dict[str, Any]) -> list[dict[str, Any]]:
    rows = memory.get('talk_turns')
    return rows if isinstance(rows, list) else []


def _remember_turn(memory: dict[str, Any], patient_id: str, turn: dict[str, Any]) -> None:
    rows = _talk_turns(memory)
    rows.append(turn)
    memory['talk_turns'] = rows[-16:]
    save_memory(patient_id, memory)


def _persist_profile(db, patient_id: str, profile: dict[str, Any]) -> None:
    if db is None or not profile_complete(profile):
        return
    try:
        from backend.database.models import Patient
        patient = db.get(Patient, patient_id)
        if patient is None:
            return
        apply_profile_to_patient(db, patient, profile)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _phase(profile, intake, memory) -> str:
    if not profile_complete(profile):
        return 'profile'
    if not intake_complete(intake):
        return 'questionnaire'
    if not report_phase_complete(memory):
        return 'report'
    if not baseline_complete(memory):
        return 'baseline'
    return 'dashboard'


def _wants_skip_report(text: str) -> bool:
    lowered = (text or '').lower()
    if re.search(r'\b(skip|स्किप)\b', lowered):
        return True
    return any(p in lowered for p in (
        'no report', 'without report', 'not now',
        'बाद में', 'रिपोर्ट नहीं',
    ))


def _pack(*, spoken, action, engine, intake, profile, memory_slice, phase,
          parsed=None, intake_field=None, rag_used=False) -> dict[str, Any]:
    return {
        'spoken': spoken,
        'action': action,
        'engine': engine,
        'intake': intake,
        'profile': profile,
        'intake_field': intake_field or next_profile_field(profile) or (
            next((fid for fid in INTAKE_FIELDS if not isinstance(intake.get(fid), int)), None)
        ),
        'awaiting_confirm': False,
        'pending_value': None,
        'parsed': parsed,
        'memory': memory_slice,
        'phase': phase,
        'rag_used': rag_used,
        'citations': [],
    }


async def consumer_reply(
    transcript: str,
    *,
    patient_id: str,
    language: str = 'en-IN',
    db=None,
    pending_value: int | None = None,
    awaiting_confirm: bool = False,
    intake_field: str | None = None,
    opening: bool = False,
) -> dict[str, Any]:
    spoken_language = language if language in ('en-IN', 'hi-IN') else 'en-IN'
    hindi = spoken_language == 'hi-IN'
    text = (transcript or '').strip()[:500]
    memory = load_memory(patient_id)
    intake = memory.get('intake') or empty_intake()
    profile = normalize_profile(memory.get('profile'))
    memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)
    first_name = display_first_name(profile, '')

    if RED_FLAG_RE.search(text) or _wants_pause(text):
        return _pack(
            spoken='रुकिए। हाथ आराम दें।' if hindi else 'Please pause and rest the arm.',
            action='pause', engine='consumer-safety',
            intake=intake, profile=profile, memory_slice=memory_slice, phase='safety',
        )

    if _wants_end(text) and intake_complete(intake):
        return _pack(
            spoken='ठीक है।' if hindi else 'Alright.',
            action='end', engine='consumer-end',
            intake=intake, profile=profile, memory_slice=memory_slice, phase='dashboard',
        )

    skipped_report = False
    if intake_complete(intake) and not report_phase_complete(memory) and _wants_skip_report(text):
        skip_report_phase(patient_id)
        skipped_report = True
        memory = load_memory(patient_id)
        memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)

    nav = match_action(text)
    if nav not in ('start_session', 'start_assessment', 'open_history', 'open_home'):
        nav = 'none'

    saved = extract_volunteered(text, profile, intake)
    parsed = None
    for kind, field, value in saved:
        if kind == 'safety':
            return _pack(
                spoken='रुकिए। हाथ आराम दें।' if hindi else 'Please pause and rest the arm.',
                action='pause', engine='consumer-safety',
                intake=intake, profile=profile, memory_slice=memory_slice, phase='safety',
            )
        if kind == 'profile':
            profile = apply_profile_value(profile, field, value, text)
            memory['profile'] = profile
            _remember_turn(memory, patient_id, {'kind': 'profile', 'field': field, 'value': value, 'said': text[:120]})
            parsed = {'intent': 'profile_value', 'parsed_value': value, 'field': field}
        elif kind == 'intake':
            intake = apply_confirmed_value(intake, field, int(value), 'voice', text)
            memory['intake'] = intake
            _remember_turn(memory, patient_id, {'kind': 'clinical', 'field': field, 'value': int(value), 'said': text[:120]})
            parsed = {'intent': 'number', 'parsed_value': int(value), 'field': field, 'engine': 'deterministic'}
    if saved:
        memory = load_memory(patient_id)
        profile = normalize_profile(memory.get('profile'))
        intake = memory.get('intake') or intake
        if profile_complete(profile):
            _persist_profile(db, patient_id, profile)
        memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(memory)
        first_name = display_first_name(profile, '')

    baseline_movement = None
    if intake_complete(intake) and report_phase_complete(memory) and not baseline_complete(memory):
        baseline_movement = next_baseline_movement(memory.get('baseline'))
    missing = missing_goals(
        profile, intake, memory.get('report_phase') or 'needed', baseline_movement,
    )
    phase = _phase(profile, intake, memory)
    plan = None
    if CONSUMER_CLAUDE and ANTHROPIC_API_KEY:
        plan = await claude_voice_turn({
            'language': spoken_language,
            'scene': 'consumer',
            'first_name': first_name or None,
            'patient_said': text,
            'goals_if_natural': missing,
            'known': {
                'age': profile.get('age'),
                'side': profile.get('affected_side'),
                'intake': {fid: intake.get(fid) for fid in INTAKE_FIELDS if isinstance(intake.get(fid), int)},
                'last_peak': memory_slice.get('last_peak_abduction'),
                'last_pain': memory_slice.get('last_pain_after'),
            },
            'recent_turns': _talk_turns(memory)[-8:],
            'just_saved': [{'field': f, 'value': v} for _k, f, v in saved if _k != 'safety'],
            'rules': [
                'Do not read a script. Do not list remaining questions.',
                'Never invent a number or name.',
                'This is not a diagnosis.',
            ],
        })
        if plan and plan.get('save_field') and plan.get('save_value') is not None and text:
            field = plan['save_field']
            if value_in_transcript(field, plan['save_value'], text):
                if field in PROFILE_FIELDS and profile.get(field) in (None, ''):
                    profile = apply_profile_value(profile, field, plan['save_value'], text)
                    memory['profile'] = profile
                    save_memory(patient_id, memory)
                elif field in INTAKE_FIELDS and not isinstance(intake.get(field), int):
                    try:
                        intake = apply_confirmed_value(intake, field, int(plan['save_value']), 'voice', text)
                        memory['intake'] = intake
                        save_memory(patient_id, memory)
                    except (TypeError, ValueError):
                        pass
            plan['save_field'] = None
        if plan and plan.get('action') == 'skip_report':
            skip_report_phase(patient_id)
            plan['action'] = 'open_home'
        if plan and plan.get('action') in ('start_session', 'start_assessment') and not intake_complete(intake):
            plan['action'] = 'none'
        if plan and plan.get('action') == 'await_report' and missing:
            plan['action'] = 'none'

    spoken = (plan or {}).get('spoken') or ''
    if spoken:
        metrics = {}
        if isinstance(profile.get('age'), int):
            metrics['age'] = profile['age']
        for fid in INTAKE_FIELDS:
            if isinstance(intake.get(fid), int):
                metrics[fid] = intake[fid]
        for key in ('last_peak_abduction', 'last_peak_flexion', 'last_pain_after'):
            value = memory_slice.get(key)
            if isinstance(value, (int, float)):
                metrics[key] = value
        spoken, grounded, _reason = grounded_or_fallback(spoken, [], metrics, hindi=hindi)
        if not grounded:
            spoken = ''
    if not spoken:
        spoken = fallback_spoken(
            spoken_language, missing=missing, saved=saved, first_name=first_name,
            baseline_movement=baseline_movement,
        )
    action = (plan or {}).get('action') or 'none'
    memory_now = load_memory(patient_id)
    needs_baseline = (
        intake_complete(intake)
        and report_phase_complete(memory_now)
        and not baseline_complete(memory_now)
    )
    if skipped_report and action in ('none', 'skip_report'):
        action = 'open_home'
    if (
        action == 'none'
        and nav != 'none'
        and intake_complete(intake)
        and report_phase_complete(memory_now)
    ):
        action = nav
    if action in ('start_session', 'start_assessment') and not intake_complete(intake):
        action = 'none'
    # The camera baseline is measured before the dashboard or any rehab session.
    if needs_baseline and action in ('open_home', 'open_history', 'start_session'):
        action = 'start_assessment'
    if needs_baseline and action == 'none' and opening:
        action = 'start_assessment'
    if action == 'start_assessment' and not needs_baseline:
        action = 'open_home' if baseline_complete(memory_now) else 'none'
    if phase == 'report' and action == 'none' and not text:
        action = 'await_report'
    engine = (plan or {}).get('engine') or 'consumer-autonomous'
    _remember_turn(load_memory(patient_id), patient_id, {
        'kind': 'talk',
        'said': (text or '')[:120],
        'spoken': spoken[:160],
        'action': action,
    })
    memory_slice = patient_memory_for_agent(db, patient_id) if db is not None else sanitize_memory_slice(load_memory(patient_id))
    return _pack(
        spoken=spoken, action=action, engine=engine,
        intake=intake, profile=profile, memory_slice=memory_slice,
        phase=_phase(profile, intake, load_memory(patient_id)),
        parsed=parsed,
    )
