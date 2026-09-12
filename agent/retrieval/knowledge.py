"""Multi-signal protocol RAG. Lexical TF-IDF cosine — no video, no extra embedding service."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from agent.knowledge import load_chunks

INTENTS = (
    'SAFETY_CONCERN',
    'ENCOURAGEMENT_NEEDED',
    'GENERAL_QUESTION',
    'EXERCISE_FEEDBACK',
    'CLINICAL_REVIEW',
)

INTENT_CATEGORIES = {
    'SAFETY_CONCERN': ('safety', 'gating'),
    'ENCOURAGEMENT_NEEDED': ('encouragement', 'education', 'faq'),
    'GENERAL_QUESTION': ('faq', 'education', 'pacing'),
    'EXERCISE_FEEDBACK': ('exercise', 'safety', 'gating', 'pacing'),
    'CLINICAL_REVIEW': ('pacing', 'education', 'faq', 'encouragement', 'exercise'),
}

PACING_TO_STAGE = {
    'pain_led': 'freezing',
    'stiffness_led': 'frozen',
    'recovering': 'thawing',
}

_SAFETY = (
    'numb', 'numbness', 'tingling', 'shooting', 'stabbing', 'sudden severe',
    'cannot move', "can't move", 'unable to move', 'something feels wrong',
    'swollen', 'swelling', 'fever', 'chest pain', 'dizzy', 'faint', 'surgery',
    'operated', 'post-op', 'postop', 'fracture', 'dislocated', 'locking',
    'giving way', 'सुन्न', 'झुनझुनी', 'तेज दर्द', 'ऑपरेशन', 'सर्जरी', 'बेहोश',
    'सीने', 'सूजन',
)
_ENCOURAGE = (
    "isn't working", 'not working', 'not improving', 'no improvement',
    'give up', 'frustrated', 'plateau', 'pointless', 'useless', 'hopeless',
    'stuck', 'same as last', 'नहीं हो रहा', 'सुधार नहीं',
    'बेकार', 'निराश',
)
_QUESTION = (
    'why', 'how long', 'is it normal', 'normal to', 'clicking', 'cracking',
    'click', 'pulling', 'should i', 'what is frozen', 'adhesive', 'stages',
    'timeline', 'how many weeks', 'worse today', 'hurt more', 'क्यों',
    'कितने दिन', 'क्लिक', 'कड़क', 'सामान्य', 'कितना समय',
)
_CLINICAL = (
    'compare', 'progress', 'restricted', 'compensation', 'draft plan',
    'rehab plan', 'generate a progress', 'most limited', 'report',
)

_TOKEN = re.compile(r'[a-z0-9]+|[ऀ-ॿ]+', re.I)


def classify_intent(text: str, *, scene: str | None = None) -> str:
    lowered = ' '.join((text or '').lower().split())
    if any(token in lowered for token in _SAFETY):
        return 'SAFETY_CONCERN'
    if any(token in lowered for token in _ENCOURAGE):
        return 'ENCOURAGEMENT_NEEDED'
    if any(token in lowered for token in _CLINICAL) and (scene or '') in ('assistant', 'clinic', ''):
        return 'CLINICAL_REVIEW'
    if any(token in lowered for token in _QUESTION) or lowered.endswith('?'):
        return 'GENERAL_QUESTION'
    if (scene or '') == 'measure':
        return 'EXERCISE_FEEDBACK'
    if lowered:
        return 'EXERCISE_FEEDBACK' if scene == 'consumer' else 'GENERAL_QUESTION'
    return 'EXERCISE_FEEDBACK'


def action_gate(safety: str | None) -> str:
    level = str(safety or 'ALLOW').upper()
    if level in ('BLOCK', 'PAUSE', 'STOP'):
        return 'STOP'
    if level in ('WARN', 'CAUTION', 'CLINICIAN_REVIEW'):
        return 'CAUTION'
    return 'CONTINUE'


def build_retrieval_query(
    current_movement: str | None = None,
    action_category: str | None = None,
    patient_text: str | None = None,
    stage: str | None = None,
    session_trend: str | None = None,
) -> str:
    parts = []
    if current_movement:
        parts.append(f'current movement {current_movement}')
    gate = (action_category or 'CONTINUE').upper()
    parts.append(f'action {gate}')
    if gate == 'STOP':
        parts.append('stop rest arm red flag safety pause')
    elif gate == 'CAUTION':
        parts.append('caution smaller arc trunk quiet')
    if patient_text:
        parts.append(patient_text.strip()[:400])
    if stage:
        parts.append(f'stage {stage} pacing')
    if session_trend:
        parts.append(f'session trend {session_trend}')
        if session_trend == 'plateau':
            parts.append('plateau normal consistency do not force')
    return ' '.join(parts).strip() or 'gentle shoulder rehabilitation coaching'


def _tokens(text: str) -> list[str]:
    words = [m.group(0).lower() for m in _TOKEN.finditer(text or '')]
    words = [w for w in words if len(w) > 1]
    grams = [a + '_' + b for a, b in zip(words, words[1:])]
    return words + grams


def _doc_text(chunk: dict[str, Any]) -> str:
    tags = ' '.join(chunk.get('stage_relevance') or [])
    return ' '.join(str(part or '') for part in (
        chunk.get('id'), chunk.get('title'), chunk.get('category'),
        chunk.get('movement'), chunk.get('exercise_id'), tags,
        chunk.get('content'), chunk.get('content_hi'),
    ))


def _tfidf(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(tokens)
    n = max(1, sum(counts.values()))
    return {tok: (count / n) * idf.get(tok, 0.0) for tok, count in counts.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b[k] for k in a if k in b)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_INDEX: dict[str, Any] | None = None


def _index() -> dict[str, Any]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    chunks = list(load_chunks())
    tokenized = [_tokens(_doc_text(row)) for row in chunks]
    df: Counter[str] = Counter()
    for toks in tokenized:
        df.update(set(toks))
    n = max(1, len(tokenized))
    idf = {tok: math.log((n + 1) / (df[tok] + 1)) + 1.0 for tok in df}
    vectors = [_tfidf(toks, idf) for toks in tokenized]
    _INDEX = {'chunks': chunks, 'idf': idf, 'vectors': vectors}
    return _INDEX


def reset_index() -> None:
    global _INDEX
    _INDEX = None
    from agent.knowledge import reset_chunk_cache
    reset_chunk_cache()


def retrieve(
    query: str,
    *,
    intent: str | None = None,
    movement: str | None = None,
    pacing_hint: str | None = None,
    k: int = 3,
) -> list[dict[str, Any]]:
    store = _index()
    qvec = _tfidf(_tokens(query), store['idf'])
    allowed = INTENT_CATEGORIES.get(intent or '', None)
    stage = PACING_TO_STAGE.get(pacing_hint or '', None)
    move = (movement or '').lower()
    scored = []
    for chunk, vec in zip(store['chunks'], store['vectors']):
        score = _cosine(qvec, vec)
        if allowed and chunk.get('category') not in allowed:
            score *= 0.35
        if move and str(chunk.get('movement') or chunk.get('exercise_id') or '').lower() == move:
            score += 0.12
        if stage and stage in (chunk.get('stage_relevance') or []):
            score += 0.08
        if intent == 'SAFETY_CONCERN' and chunk.get('category') == 'safety':
            score += 0.2
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    out = []
    for score, chunk in scored[: max(1, min(k, 5))]:
        if score <= 0 and out:
            break
        row = {
            'id': chunk.get('id'),
            'title': chunk.get('title'),
            'category': chunk.get('category'),
            'score': round(float(score), 4),
            'content': chunk.get('content'),
            'content_hi': chunk.get('content_hi'),
            'stage_relevance': list(chunk.get('stage_relevance') or []),
            'movement': chunk.get('movement'),
        }
        out.append(row)
    return out


def retrieve_for_turn(
    *,
    patient_text: str = '',
    intent: str | None = None,
    movement: str | None = None,
    action_category: str | None = None,
    pacing_hint: str | None = None,
    session_trend: str | None = None,
    scene: str | None = None,
    k: int = 3,
) -> dict[str, Any]:
    resolved_intent = intent or classify_intent(patient_text, scene=scene)
    stage = PACING_TO_STAGE.get(pacing_hint or '', None)
    query = build_retrieval_query(
        current_movement=movement,
        action_category=action_category,
        patient_text=patient_text,
        stage=stage,
        session_trend=session_trend,
    )
    chunks = retrieve(
        query,
        intent=resolved_intent,
        movement=movement,
        pacing_hint=pacing_hint,
        k=k,
    )
    return {
        'intent': resolved_intent,
        'query': query,
        'chunks': chunks,
        'citations': [{'id': row['id'], 'title': row['title'], 'category': row['category']} for row in chunks],
    }
