"""Optional LLM rephrase of a guide cue. Never returns bone angles."""
from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

BLOCKLIST = ('diagnos', 'frozen shoulder', 'you have', 'ignore pain', 'push through',
             'override', 'block', 'degree', '°')


def caption_guide(payload):
    cue = (payload.get('cue') or '').strip()[:160]
    if not cue:
        cue = 'Copy this arm path. Keep your trunk quiet.'
    safety = payload.get('safety') or 'ALLOW'
    if safety in ('BLOCK', 'PAUSE'):
        return {'caption': cue, 'llm_used': False, 'bones_from_llm': False, 'driven_by': 'telemetry_not_llm'}
    rewritten = _maybe_rephrase(cue, payload)
    if not rewritten or _unsafe(rewritten):
        return {'caption': cue, 'llm_used': False, 'bones_from_llm': False, 'driven_by': 'telemetry_not_llm'}
    return {'caption': rewritten.strip()[:120], 'llm_used': True, 'bones_from_llm': False, 'driven_by': 'telemetry_not_llm'}


def _unsafe(text):
    lowered = text.lower()
    return any(word in lowered for word in BLOCKLIST)


def _maybe_rephrase(cue, payload):
    if not LLM_BASE_URL:
        return None
    try:
        import httpx
        body = {
            'model': LLM_MODEL,
            'temperature': 0,
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        'Rephrase one physiotherapy cue in at most 12 words. '
                        'Do not mention degrees, diagnosis, disease, or 3D bones. '
                        'Do not tell the patient to ignore pain or override a stop. '
                        'Return only the cue.'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f"phase={payload.get('phase')} safety={payload.get('safety')} "
                        f"movement={payload.get('movement')} cue={cue}"
                    ),
                },
            ],
        }
        headers = {'Content-Type': 'application/json'}
        if LLM_API_KEY:
            headers['Authorization'] = 'Bearer ' + LLM_API_KEY
        response = httpx.post(LLM_BASE_URL.rstrip('/') + '/v1/chat/completions', json=body, headers=headers, timeout=8)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception:
        return None
