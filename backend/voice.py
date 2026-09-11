"""Server-side voice providers for the patient questionnaire.

The browser never receives provider keys. Deterministic parsing and safety phrases
remain authoritative; Claude may clarify an otherwise unknown utterance only.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from backend.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL,
    ELEVENLABS_API_KEY,
    ELEVENLABS_BASE_URL,
    ELEVENLABS_STT_MODEL,
    ELEVENLABS_TTS_MODEL,
    ELEVENLABS_VOICE_ID,
    SARVAM_API_KEY,
    SARVAM_BASE_URL,
    SARVAM_STT_MODEL,
    SARVAM_TTS_MODEL,
    SARVAM_TTS_SPEAKER,
    VOICE_STT,
    VOICE_TIMEOUT_S,
    VOICE_TTS,
)
from backend.intake import FIELD_BY_ID, parse_utterance
from backend.stt import transcribe_wav_bytes


class VoiceProviderError(RuntimeError):
    """A provider failed without exposing credentials or raw response bodies."""


_ELEVENLABS_TTS_SKIP = False


def skip_elevenlabs_tts() -> None:
    global _ELEVENLABS_TTS_SKIP
    _ELEVENLABS_TTS_SKIP = True


def voice_status() -> dict[str, Any]:
    from agent.studio_browser import playwright_status
    stt_order = stt_engines()
    tts_order_names = tts_engines()
    return {
        'sarvam_configured': bool(SARVAM_API_KEY),
        'claude_configured': bool(ANTHROPIC_API_KEY),
        'elevenlabs_configured': bool(ELEVENLABS_API_KEY),
        'stt': stt_order[0] if stt_order else 'offline-windows-speech',
        'tts': tts_order_names[0] if tts_order_names else 'browser-speech',
        'stt_order': stt_order + ['offline-windows-speech'],
        'tts_order': tts_order_names or ['browser-speech'],
        'clarification': 'claude-live-agent' if ANTHROPIC_API_KEY else 'deterministic-only',
        'conversation': 'continuous_until_end',
        'browser_use': playwright_status(),
        'sarvam_stt_model': SARVAM_STT_MODEL if SARVAM_API_KEY else None,
        'sarvam_tts_model': SARVAM_TTS_MODEL if SARVAM_API_KEY else None,
        'sarvam_tts_speaker': SARVAM_TTS_SPEAKER if SARVAM_API_KEY else None,
        'elevenlabs_tts_model': ELEVENLABS_TTS_MODEL if ELEVENLABS_API_KEY and not _ELEVENLABS_TTS_SKIP else None,
        'anthropic_model': ANTHROPIC_MODEL if ANTHROPIC_API_KEY else None,
        'privacy': 'utterance_and_session_metrics_only_no_patient_identifiers_or_video',
        'talk_tts': tts_order_names[0] if tts_order_names else 'browser-speech',
    }


def stt_engines() -> list[str]:
    available = []
    if SARVAM_API_KEY:
        available.append('sarvam-saaras')
    if ELEVENLABS_API_KEY:
        available.append('elevenlabs-scribe')
    if VOICE_STT == 'elevenlabs':
        return [name for name in ('elevenlabs-scribe', 'sarvam-saaras') if name in available]
    if VOICE_STT == 'sarvam':
        return [name for name in ('sarvam-saaras', 'elevenlabs-scribe') if name in available]
    return available


def tts_engines() -> list[str]:
    sarvam = ['sarvam-bulbul'] if SARVAM_API_KEY else []
    eleven = ['elevenlabs'] if ELEVENLABS_API_KEY and not _ELEVENLABS_TTS_SKIP else []
    if VOICE_TTS == 'elevenlabs':
        return eleven + sarvam
    return sarvam + eleven


async def transcribe_audio(
    data: bytes,
    filename: str = 'utterance.wav',
    content_type: str = 'audio/wav',
    language: str = 'en-IN',
    fast: bool = False,
    prefer: str | None = None,
    allow_offline: bool = True,
) -> dict[str, Any]:
    """Cloud STT in configured order, then optional offline WAV recognizer. Empty audio never invents words."""
    if not data or len(data) < 64:
        return {'transcript': '', 'engine': 'none', 'provider_fallback': False, 'error': 'empty_audio'}
    last_error = None
    engines = stt_engines()
    if prefer == 'elevenlabs' and 'elevenlabs-scribe' in engines:
        engines = ['elevenlabs-scribe'] + [name for name in engines if name != 'elevenlabs-scribe']
    elif prefer == 'sarvam' and 'sarvam-saaras' in engines:
        engines = ['sarvam-saaras'] + [name for name in engines if name != 'sarvam-saaras']
    stt_timeout = min(6.0 if fast else 10.0, VOICE_TIMEOUT_S)
    if fast:
        engines = engines[:1]
    for engine in engines:
        try:
            if engine == 'sarvam-saaras':
                row = await _sarvam_stt(data, filename, content_type, language, timeout_s=stt_timeout)
            else:
                row = await _elevenlabs_stt(data, filename, content_type, language, timeout_s=stt_timeout)
            if (row.get('transcript') or '').strip():
                return row
        except (httpx.HTTPError, ValueError, TypeError, VoiceProviderError, OSError):
            last_error = engine + '_unavailable'
            continue
    if not allow_offline:
        return {
            'transcript': '',
            'engine': 'none',
            'provider_fallback': bool(engines),
            'provider_error': last_error or 'empty_transcript',
        }
    # Windows Speech can block for tens of seconds — only used when cloud returns nothing.
    fallback = transcribe_wav_bytes(data)
    return {
        **fallback,
        'provider_fallback': bool(stt_engines()),
        'provider_error': last_error,
    }


async def synthesize_speech(text: str, language: str = 'en-IN') -> tuple[bytes, str]:
    clean = ' '.join((text or '').split())
    if not clean or len(clean) > 600:
        raise VoiceProviderError('Speech text must contain 1 to 600 characters')
    last_error = 'No text-to-speech provider is configured'
    for engine in tts_engines():
        try:
            if engine == 'elevenlabs':
                return await _elevenlabs_tts(clean, language)
            return await _sarvam_tts(clean, language)
        except VoiceProviderError as exc:
            last_error = str(exc)
            continue
    raise VoiceProviderError(last_error)


async def _sarvam_stt(data, filename, content_type, language, *, timeout_s: float | None = None):
    async with httpx.AsyncClient(timeout=timeout_s or min(10.0, VOICE_TIMEOUT_S)) as client:
        response = await client.post(
            f'{SARVAM_BASE_URL}/speech-to-text',
            headers={'api-subscription-key': SARVAM_API_KEY},
            files={'file': (filename or 'utterance.wav', data, content_type or 'audio/wav')},
            data={
                'model': SARVAM_STT_MODEL,
                'mode': 'transcribe',
                'language_code': language if language in ('en-IN', 'hi-IN') else 'unknown',
            },
        )
    response.raise_for_status()
    payload = response.json()
    return {
        'transcript': str(payload.get('transcript') or '').strip(),
        'engine': 'sarvam-saaras',
        'language_code': payload.get('language_code'),
        'language_probability': payload.get('language_probability'),
        'request_id': payload.get('request_id'),
        'provider_fallback': False,
    }


async def _elevenlabs_stt(data, filename, content_type, language, *, timeout_s: float | None = None):
    async with httpx.AsyncClient(timeout=timeout_s or min(10.0, VOICE_TIMEOUT_S)) as client:
        response = await client.post(
            f'{ELEVENLABS_BASE_URL}/v1/speech-to-text',
            headers={'xi-api-key': ELEVENLABS_API_KEY},
            files={'file': (filename or 'utterance.wav', data, content_type or 'audio/wav')},
            data={'model_id': ELEVENLABS_STT_MODEL, 'language_code': language[:2] if language else 'en'},
        )
    response.raise_for_status()
    payload = response.json()
    return {
        'transcript': str(payload.get('text') or payload.get('transcript') or '').strip(),
        'engine': 'elevenlabs-scribe',
        'language_code': payload.get('language_code') or language,
        'provider_fallback': False,
    }


async def _sarvam_tts(text: str, language: str) -> tuple[bytes, str]:
    try:
        async with httpx.AsyncClient(timeout=min(10.0, VOICE_TIMEOUT_S)) as client:
            response = await client.post(
                f'{SARVAM_BASE_URL}/text-to-speech',
                headers={
                    'api-subscription-key': SARVAM_API_KEY,
                    'content-type': 'application/json',
                },
                json={
                    'text': text,
                    'language_code': language if language in ('en-IN', 'hi-IN') else 'en-IN',
                    'model': SARVAM_TTS_MODEL,
                    'speaker': SARVAM_TTS_SPEAKER,
                    'pace': 1.0,
                    'speech_sample_rate': 24000,
                },
            )
        response.raise_for_status()
        payload = response.json()
        encoded = (payload.get('audios') or [None])[0]
        if not encoded:
            raise VoiceProviderError('Sarvam returned no audio')
        return base64.b64decode(encoded, validate=True), 'audio/wav'
    except VoiceProviderError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise VoiceProviderError('Sarvam text-to-speech is unavailable') from exc


async def _elevenlabs_tts(text: str, language: str) -> tuple[bytes, str]:
    try:
        async with httpx.AsyncClient(timeout=min(10.0, VOICE_TIMEOUT_S)) as client:
            response = await client.post(
                f'{ELEVENLABS_BASE_URL}/v1/text-to-speech/{ELEVENLABS_VOICE_ID}',
                headers={
                    'xi-api-key': ELEVENLABS_API_KEY,
                    'accept': 'audio/mpeg',
                    'content-type': 'application/json',
                },
                json={
                    'text': text,
                    'model_id': ELEVENLABS_TTS_MODEL,
                    'voice_settings': {'stability': 0.42, 'similarity_boost': 0.75},
                },
            )
        if response.status_code in (401, 402, 403):
            skip_elevenlabs_tts()
            raise VoiceProviderError('ElevenLabs voice is unavailable on this plan')
        response.raise_for_status()
        audio = response.content
        if not audio:
            raise VoiceProviderError('ElevenLabs returned no audio')
        return audio, 'audio/mpeg'
    except VoiceProviderError:
        raise
    except httpx.HTTPStatusError as exc:
        code = getattr(exc.response, 'status_code', 0)
        if code in (401, 402, 403):
            skip_elevenlabs_tts()
            raise VoiceProviderError('ElevenLabs voice is unavailable on this plan') from exc
        raise VoiceProviderError('ElevenLabs text-to-speech is unavailable') from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise VoiceProviderError('ElevenLabs text-to-speech is unavailable') from exc


async def parse_questionnaire_reply(
    text: str,
    field: str,
    awaiting_confirm: bool = False,
    language: str = 'en-IN',
) -> dict[str, Any]:
    """Parse locally first; Claude is a constrained clarification fallback only."""
    deterministic = parse_utterance(text, field, awaiting_confirm)
    deterministic['engine'] = 'deterministic'
    if deterministic.get('intent') != 'unknown' or not ANTHROPIC_API_KEY:
        return deterministic

    spec = FIELD_BY_ID[field]
    maximum = int(spec['max'])
    prompt = {
        'field': field,
        'question': spec['prompt_hi'] if language == 'hi-IN' else spec['prompt_en'],
        'allowed_integer_min': 0,
        'allowed_integer_max': maximum,
        'function_labels': ['none', 'mild', 'moderate', 'severe', 'unable'] if maximum == 4 else [],
        'awaiting_confirmation': awaiting_confirm,
        'patient_utterance': (text or '')[:500],
    }
    schema = {
        'type': 'object',
        'properties': {
            'intent': {'type': 'string', 'enum': ['number', 'confirm_yes', 'confirm_no', 'safety_pause', 'unknown']},
            'parsed_value': {'type': ['integer', 'null']},
            'spoken': {'type': 'string'},
        },
        'required': ['intent', 'parsed_value', 'spoken'],
        'additionalProperties': False,
    }
    system = (
        'You clarify one rehabilitation questionnaire reply. This is data capture, not diagnosis. '
        'Extract a number only when the patient explicitly states a valid number or listed function label. '
        'Never infer a score from pain adjectives. If unclear, return unknown and ask a short neutral follow-up. '
        'If the patient asks to stop or reports danger, return safety_pause. Use the question language.'
    )
    try:
        async with httpx.AsyncClient(timeout=VOICE_TIMEOUT_S) as client:
            response = await client.post(
                f'{ANTHROPIC_BASE_URL}/v1/messages',
                headers={
                    'x-api-key': ANTHROPIC_API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': ANTHROPIC_MODEL,
                    'max_tokens': 180,
                    'system': system,
                    'messages': [{'role': 'user', 'content': json.dumps(prompt, ensure_ascii=False)}],
                    'output_config': {'format': {'type': 'json_schema', 'schema': schema}},
                },
            )
        response.raise_for_status()
        payload = response.json()
        blocks = payload.get('content') or []
        block = next((item for item in blocks if item.get('type') == 'text'), None)
        result = json.loads((block or {}).get('text') or '{}')
    except (httpx.HTTPError, ValueError, TypeError, StopIteration):
        return {**deterministic, 'engine': 'deterministic-fallback', 'provider_error': 'claude_unavailable'}

    intent = result.get('intent')
    value = result.get('parsed_value')
    # The deterministic parser already accepts every supported explicit number
    # and function label. A numeric result created only by Claude would be an
    # inferred clinical score, so it is rejected and the patient is asked again.
    if intent == 'number':
        intent, value = 'unknown', None
    if intent != 'number':
        value = None
    if intent == 'safety_pause':
        return {
            'intent': 'safety_pause', 'parsed_value': None, 'needs_confirm': False,
            'safety': 'PAUSE', 'spoken': str(result.get('spoken') or 'Please stop and ask the physiotherapist for help.'),
            'engine': 'claude-structured',
        }
    if intent not in ('number', 'confirm_yes', 'confirm_no', 'unknown'):
        intent = 'unknown'
    return {
        'intent': intent,
        'parsed_value': value,
        'needs_confirm': intent == 'number',
        'safety': 'ALLOW',
        'spoken': str(result.get('spoken') or deterministic.get('spoken') or '')[:300],
        'engine': 'claude-structured',
    }
