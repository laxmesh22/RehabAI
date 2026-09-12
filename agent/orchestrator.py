"""One supervisor agent. Stored measurements and tools first; the LLM never sees the database."""
import json
from agent.prompts.supervisor import SYSTEM_PROMPT
from agent.tools.clinical import (
    calculate_patient_progress, compare_sessions, draft_progress_report,
    draft_rehab_plan, get_assessment_history, get_compensation_events, get_latest_assessment,
    get_patient_profile, get_rom_history, get_session_history,
)
from agent.retrieval.grounding import grounded_or_fallback
from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


def classify(question):
    q = question.lower()
    if 'plan' in q and ('draft' in q or 'rehab' in q or 'exercise' in q):
        return 'rehab_planning'
    if 'report' in q or 'progress summary' in q or 'generate a progress' in q:
        return 'report_generation'
    if 'compar' in q:
        return 'progress_analysis'
    if 'restrict' in q or 'limited' in q or 'most restricted' in q:
        return 'progress_analysis'
    if 'compensation' in q:
        return 'progress_analysis'
    if 'educat' in q or 'explain' in q and 'exercise' in q:
        return 'patient_education'
    return 'progress_analysis'


def _public_profile(profile):
    if not isinstance(profile, dict):
        return {}
    return {
        'affected_side': profile.get('affected_side'),
        'is_demo': profile.get('is_demo'),
        'has_clinician_diagnosis_on_file': bool(profile.get('clinician_diagnosis')),
    }


def _drop_identity(value):
    if isinstance(value, dict):
        return {
            key: _drop_identity(item)
            for key, item in value.items()
            if key not in ('patient_id', 'full_name', 'mrn', 'email', 'patient_name')
        }
    if isinstance(value, list):
        return [_drop_identity(item) for item in value]
    return value


def _llm_tools(tool_results):
    safe = _drop_identity(tool_results)
    if isinstance(safe, dict) and 'profile' in safe:
        safe['profile'] = _public_profile(safe.get('profile') if isinstance(safe.get('profile'), dict) else {})
    return safe


def run_supervisor(db, patient_id, question, actor_role):
    if actor_role == 'PATIENT' and any(word in question.lower() for word in ('draft', 'approve', 'plan', 'alert')):
        # Patients may ask about their progress, not change treatment.
        pass
    profile = get_patient_profile(db, patient_id)
    if profile.get('error'):
        return {'intent': 'error', 'patient_id': patient_id, 'summary': 'Patient record was not found.',
                'measured_changes': {}, 'alerts': [], 'recommendation': None, 'requires_approval': False,
                'llm_used': False, 'rag_used': False, 'citations': []}
    intent = classify(question)
    tool_results = {
        'profile': profile,
        'progress': calculate_patient_progress(db, patient_id),
        'latest_assessment': get_latest_assessment(db, patient_id),
        'assessments': get_assessment_history(db, patient_id),
        'rom': get_rom_history(db, patient_id),
        'sessions': get_session_history(db, patient_id),
        'compensation': get_compensation_events(db, patient_id),
    }
    drafted = None
    if intent == 'report_generation' and actor_role in ('PHYSIOTHERAPIST', 'DOCTOR', 'ADMIN'):
        drafted = draft_progress_report(db, patient_id)
        tool_results['draft_report'] = drafted
    if intent == 'rehab_planning' and actor_role in ('PHYSIOTHERAPIST', 'DOCTOR'):
        drafted = draft_rehab_plan(db, patient_id)
        tool_results['draft_plan'] = drafted
    if 'last two' in question.lower() or 'previous session' in question.lower() or 'last two assessments' in question.lower():
        sessions = tool_results['sessions']
        if len(sessions) >= 2:
            tool_results['session_compare'] = compare_sessions(db, sessions[-2]['id'], sessions[-1]['id'])
        assessments = tool_results['assessments']
        if len(assessments) >= 2:
            tool_results['assessment_compare'] = {
                'baseline': assessments[0],
                'previous': assessments[-2],
                'current': assessments[-1],
            }
    summary = deterministic_summary(question, intent, tool_results)
    llm_summary = maybe_llm(question, tool_results)
    if llm_summary:
        summary = llm_summary
    progress = tool_results['progress']
    changes = {}
    if not progress.get('error'):
        changes = {k: progress[k] for k in ('abduction', 'flexion', 'pain_movement', 'torso_compensation') if progress.get(k)}
    return {
        'intent': intent,
        'patient_id': patient_id,
        'summary': summary,
        'measured_changes': changes,
        'alerts': [],
        'recommendation': 'continue clinician-approved plan' if intent != 'rehab_planning' else 'physiotherapist approval required before any plan change',
        'requires_approval': intent in ('rehab_planning', 'report_generation'),
        'draft': drafted,
        'llm_used': bool(llm_summary),
        'rag_used': False,
        'citations': [],
        'tool_trace': list(tool_results.keys()),
    }


def deterministic_summary(question, intent, tools, retrieved=None):
    profile = tools['profile']
    progress = tools['progress']
    demo = ' Demo/synthetic records are included and labelled.' if profile.get('is_demo') or progress.get('demo_records_present') else ''
    if progress.get('error'):
        return 'No valid stored assessments are available for this patient. Missing measurements were not estimated.' + demo
    q = question.lower()
    if 'restrict' in q or 'limited' in q:
        options = []
        for name in ('abduction', 'flexion'):
            block = progress.get(name)
            if block:
                options.append((block['current'], name, block))
        if not options:
            return 'No valid ROM values are stored, so the most restricted movement cannot be determined.' + demo
        options.sort()
        name, block = options[0][1], options[0][2]
        return f"The more restricted recorded movement is {name} at {block['current']}° (baseline {block['baseline']}°).{demo}"
    if 'compensation' in q:
        events = tools.get('compensation') or []
        high = [e for e in events if e['value'] > e['threshold']]
        if not high:
            return 'No stored compensation events exceed their thresholds.' + demo
        session_ids = sorted({e['session_id'] for e in high if e['session_id']})
        latest = high[-1]
        return (f"{len(high)} stored events exceed the compensation threshold. "
                f"Latest: {latest['type']} {latest['value']}° (threshold {latest['threshold']}°). "
                f"Sessions: {', '.join(session_ids) or 'assessment-linked only'}.{demo}")
    if intent == 'rehab_planning' and tools.get('draft_plan'):
        names = ', '.join(item['name'] for item in tools['draft_plan']['exercises'])
        return f"Draft plan uses only approved library exercises: {names}. Treatment is unchanged until a physiotherapist approves." + demo
    if intent == 'report_generation' and tools.get('draft_report'):
        return tools['draft_report']['summary'] + demo
    bits = []
    for label, key in (('Abduction', 'abduction'), ('Flexion', 'flexion'), ('Pain during movement', 'pain_movement'),
                       ('Trunk compensation', 'torso_compensation')):
        block = progress.get(key)
        if not block:
            continue
        unit = '/10' if 'pain' in key else '°'
        bits.append(f"{label} {block['baseline']}{unit} → {block['current']}{unit} ({block['change']:+.0f})")
    compare = tools.get('assessment_compare')
    extra = ''
    if compare:
        extra = f" Compared stored assessments {compare['previous']['id']} and {compare['current']['id']}."
    return ('Stored valid measurements for this patient: ' + '; '.join(bits) + '.'
            + extra + ' The camera does not diagnose adhesive capsulitis. Clinical review remains required.'
            + demo)


def _metrics_from_tools(tool_results):
    metrics = {}

    def walk(obj):
        if isinstance(obj, dict):
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj[:12]:
                walk(item)
        elif isinstance(obj, bool):
            return
        elif isinstance(obj, (int, float)):
            metrics[f'n{len(metrics)}'] = obj

    walk(tool_results)
    return metrics


def maybe_llm(question, tool_results):
    safe = _llm_tools(tool_results)
    payload = json.dumps({'question': question, 'tools': safe}, default=str)[:12000]
    text = _claude_summary(payload)
    if not text and LLM_BASE_URL:
        try:
            import httpx
            body = {
                'model': LLM_MODEL,
                'temperature': 0,
                'messages': [
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': payload},
                ],
            }
            headers = {'Content-Type': 'application/json'}
            if LLM_API_KEY:
                headers['Authorization'] = 'Bearer ' + LLM_API_KEY
            response = httpx.post(LLM_BASE_URL.rstrip('/') + '/v1/chat/completions', json=body, headers=headers, timeout=20)
            response.raise_for_status()
            text = response.json()['choices'][0]['message']['content']
        except Exception:
            text = None
    if not text:
        return None
    spoken, ok, _reason = grounded_or_fallback(text, [], _metrics_from_tools(tool_results))
    return spoken if ok else None


def _claude_summary(payload: str):
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import httpx
        response = httpx.post(
            f'{ANTHROPIC_BASE_URL}/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': ANTHROPIC_MODEL,
                'max_tokens': 280,
                'system': SYSTEM_PROMPT,
                'messages': [{'role': 'user', 'content': payload}],
            },
            timeout=12,
        )
        response.raise_for_status()
        raw = ''
        for item in (response.json().get('content') or []):
            if isinstance(item, dict) and item.get('type') == 'text':
                raw += str(item.get('text') or '')
        return raw.strip()[:1200] or None
    except Exception:
        return None
