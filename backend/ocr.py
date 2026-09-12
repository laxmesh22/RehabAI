"""Report OCR for the consumer agent. Extracts only clearly present numbers — never invents ROM or pain."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import httpx

from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, VOICE_TIMEOUT_S


ROM_RE = re.compile(
    r'\b(?:abduction|abd|flexion|flex)\s*(?:angle|rom)?\s*[:=]?\s*(\d{1,3})\s*(?:°|deg|degrees)?\b',
    re.I,
)
ABD_RE = re.compile(r'\babduction\b[^0-9]{0,24}(\d{1,3})\s*(?:°|deg)?', re.I)
FLEX_RE = re.compile(r'\bflexion\b[^0-9]{0,24}(\d{1,3})\s*(?:°|deg)?', re.I)
PAIN_RE = re.compile(r'\bpain\b[^0-9]{0,24}(\d{1,2})\s*(?:/10)?', re.I)


def extract_metrics_from_text(text: str) -> dict[str, Any]:
    """Parse only explicit numbers from OCR text. Missing values stay None."""
    clean = ' '.join((text or '').split())
    out = {
        'abduction_deg': None,
        'flexion_deg': None,
        'pain_score': None,
        'raw_hits': [],
    }
    if not clean:
        return out
    abd = ABD_RE.search(clean)
    flex = FLEX_RE.search(clean)
    pain = PAIN_RE.search(clean)
    if abd:
        value = int(abd.group(1))
        if 0 <= value <= 180:
            out['abduction_deg'] = value
            out['raw_hits'].append(f'abduction={value}')
    if flex:
        value = int(flex.group(1))
        if 0 <= value <= 180:
            out['flexion_deg'] = value
            out['raw_hits'].append(f'flexion={value}')
    if pain:
        value = int(pain.group(1))
        if 0 <= value <= 10:
            out['pain_score'] = value
            out['raw_hits'].append(f'pain={value}')
    # Generic ROM pairs if labelled elsewhere
    for match in ROM_RE.finditer(clean):
        label = match.group(0).lower()
        value = int(match.group(1))
        if 'abd' in label and out['abduction_deg'] is None and 0 <= value <= 180:
            out['abduction_deg'] = value
            out['raw_hits'].append(f'abduction={value}')
        if 'flex' in label and out['flexion_deg'] is None and 0 <= value <= 180:
            out['flexion_deg'] = value
            out['raw_hits'].append(f'flexion={value}')
    return out


def _validate_image(image_bytes: bytes) -> str:
    """Validate decoded pixels and derive the media type from the bytes, not the upload header."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            image_format = (image.format or '').upper()
            image.verify()
    except Exception as exc:
        raise ValueError('Report upload must be a valid JPEG, PNG, WebP, or GIF image') from exc
    if width <= 0 or height <= 0 or width * height > 12_000_000:
        raise ValueError('Report image dimensions are too large')
    media_types = {
        'JPEG': 'image/jpeg',
        'PNG': 'image/png',
        'WEBP': 'image/webp',
        'GIF': 'image/gif',
    }
    if image_format not in media_types:
        raise ValueError('Report upload must be a JPEG, PNG, WebP, or GIF image')
    return media_types[image_format]


async def run_report_ocr(
    image_bytes: bytes,
    filename: str = 'report.jpg',
    content_type: str = 'image/jpeg',
    *,
    allow_cloud: bool = False,
) -> dict[str, Any]:
    """OCR a clinician/patient report image. Never invents clinical numbers."""
    if not image_bytes:
        return {
            'ok': False,
            'engine': 'none',
            'text': '',
            'metrics': extract_metrics_from_text(''),
            'error': 'empty_image',
        }
    actual_media_type = _validate_image(image_bytes)
    safe_filename = Path(filename or 'report.jpg').name[:120] or 'report.jpg'
    text = ''
    engine = 'none'
    error = None
    cloud_used = False

    # 1) Local tesseract if installed
    try:
        from PIL import Image
        import pytesseract  # type: ignore
        image = Image.open(io.BytesIO(image_bytes))
        text = (pytesseract.image_to_string(image) or '').strip()
        if text:
            engine = 'tesseract'
    except Exception:
        text = ''

    # 2) Claude vision transcription only (no invented numbers in JSON)
    if not text and ANTHROPIC_API_KEY and allow_cloud:
        try:
            text = await _claude_transcribe(image_bytes, actual_media_type)
            engine = 'claude-vision-transcribe' if text else 'claude-empty'
            cloud_used = True
        except Exception as exc:
            error = 'claude_ocr_unavailable'
            text = ''

    metrics = extract_metrics_from_text(text)
    return {
        'ok': True,
        'engine': engine,
        'text': text[:4000],
        'metrics': metrics,
        'filename': safe_filename,
        'content_type': actual_media_type,
        'cloud_available': bool(ANTHROPIC_API_KEY),
        'cloud_used': cloud_used,
        'error': error,
        'disclaimer': 'OCR extracts printed numbers only. This is not a diagnosis. Raw report images are not stored.',
    }


async def _claude_transcribe(image_bytes: bytes, content_type: str) -> str:
    media = content_type if content_type in ('image/jpeg', 'image/png', 'image/webp', 'image/gif') else 'image/jpeg'
    b64 = base64.b64encode(image_bytes).decode('ascii')
    payload = {
        'model': ANTHROPIC_MODEL,
        'max_tokens': 600,
        'messages': [{
            'role': 'user',
            'content': [
                {
                    'type': 'image',
                    'source': {'type': 'base64', 'media_type': media, 'data': b64},
                },
                {
                    'type': 'text',
                    'text': (
                        'Transcribe the visible text from this medical or rehab report image. '
                        'Return plain text only. Do not invent missing values. '
                        'If a number is unclear, omit it.'
                    ),
                },
            ],
        }],
    }
    async with httpx.AsyncClient(timeout=min(45.0, max(20.0, VOICE_TIMEOUT_S))) as client:
        response = await client.post(
            f'{ANTHROPIC_BASE_URL}/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json=payload,
        )
    response.raise_for_status()
    body = response.json()
    chunks = []
    for item in body.get('content') or []:
        if isinstance(item, dict) and item.get('type') == 'text':
            chunks.append(str(item.get('text') or ''))
    return '\n'.join(chunks).strip()
