"""Load the protocol knowledge base and merge approved library exercises."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from edge.exercises.library import approved_library

KB_PATH = Path(__file__).resolve().parent / 'knowledge_base.json'

EXERCISE_HI = {
    'shoulder_abduction': 'कैमरे की ओर खड़े हों। प्रभावित हाथ को बगल से ऊपर उठाएँ, फिर धीरे नीचे लाएँ। धड़ सीधा रखें। तेज दर्द हो तो रुकें।',
    'shoulder_flexion': 'प्रभावित भुजा दिखे। हाथ को आगे ऊपर उठाएँ, फिर धीरे नीचे लाएँ। पीछे न झुकें।',
    'assisted_flexion': 'सहायक हाथ या छड़ी से प्रभावित भुजा को आराम तक आगे उठाएँ। दर्द में न खींचें।',
    'wand_flexion': 'छड़ी दोनों हाथों से पकड़ें और आराम तक दोनों भुजाएँ आगे उठाएँ।',
    'pendulum': 'सहारे से थोड़ा आगे झुकें, हाथ लटकने दें, छोटे आरामदायक घेरे बनाएँ। कंधा न उठाएँ।',
    'wall_climb': 'दीवार की ओर मुख। उंगलियाँ आराम तक ऊपर चलाएँ, फिर नीचे। कंधा कान तक न उचकाएँ।',
    'external_rotation': 'कोहनी बगल में रखें और अग्रभुजा को आराम तक बाहर घुमाएँ।',
    'wand_er': 'छड़ी पकड़ें, दोनों कोहनियाँ बगल में, अग्रभुजाएँ आराम तक बाहर घुमाएँ।',
}


def _exercise_chunk(item: dict[str, Any]) -> dict[str, Any]:
    safety = ' '.join(item.get('safety_conditions') or [])
    return {
        'id': 'ex-' + str(item.get('exercise_id')),
        'category': 'exercise',
        'title': item.get('name') or item.get('exercise_id'),
        'stage_relevance': ['freezing', 'frozen', 'thawing'],
        'movement': item.get('movement') or item.get('exercise_id'),
        'exercise_id': item.get('exercise_id'),
        'content': (
            f"{item.get('name')}: {item.get('instructions')} "
            f"Start: {item.get('start_position')}. "
            f"Peak: {item.get('peak_condition')}. "
            f"Safety: {safety}. "
            f"Demo target in the library is {item.get('target_range')} degrees for a typical comfortable session, "
            f"not a diagnosis threshold. Stay inside clinician-approved comfort."
        ),
        'content_hi': EXERCISE_HI.get(str(item.get('exercise_id')), item.get('instructions') or ''),
    }


@lru_cache(maxsize=1)
def load_chunks() -> tuple[dict[str, Any], ...]:
    raw = json.loads(KB_PATH.read_text(encoding='utf-8'))
    chunks = [dict(row) for row in (raw.get('chunks') or []) if isinstance(row, dict) and row.get('id')]
    seen = {row['id'] for row in chunks}
    for item in approved_library():
        row = _exercise_chunk(item)
        if row['id'] not in seen:
            chunks.append(row)
            seen.add(row['id'])
    return tuple(chunks)


def chunk_count() -> int:
    return len(load_chunks())


def reset_chunk_cache() -> None:
    load_chunks.cache_clear()


def public_ai_status() -> dict[str, Any]:
    from backend.config import ANTHROPIC_API_KEY, LLM_BASE_URL
    return {
        'llm_loop': True,
        'rag': False,
        'knowledge_chunks': chunk_count(),
        'retrieval': 'unused-for-speech',
        'claude_configured': bool(ANTHROPIC_API_KEY),
        'local_llm': bool(LLM_BASE_URL),
        'role': 'autonomous_voice_llm_not_diagnosis',
        'cannot': ['invent_rom', 'override_block', 'confirm_diagnosis'],
    }
