"""OCR extracts printed numbers only and never invents ROM."""
from __future__ import annotations

import unittest
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from backend.ocr import extract_metrics_from_text, run_report_ocr


class OcrParseTests(unittest.TestCase):
    def test_extracts_labelled_numbers(self):
        text = 'Shoulder abduction: 92 degrees. Flexion 110 deg. Pain 4/10.'
        row = extract_metrics_from_text(text)
        self.assertEqual(row['abduction_deg'], 92)
        self.assertEqual(row['flexion_deg'], 110)
        self.assertEqual(row['pain_score'], 4)

    def test_does_not_invent_missing_values(self):
        row = extract_metrics_from_text('Patient feels better today.')
        self.assertIsNone(row['abduction_deg'])
        self.assertIsNone(row['flexion_deg'])
        self.assertIsNone(row['pain_score'])

    def test_rejects_non_image_upload(self):
        import asyncio
        with self.assertRaisesRegex(ValueError, 'valid JPEG'):
            asyncio.run(run_report_ocr(b'not an image'))


class OcrPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_ocr_requires_explicit_consent(self):
        out = io.BytesIO()
        Image.new('RGB', (64, 64), 'white').save(out, format='JPEG')
        fake_tesseract = SimpleNamespace(image_to_string=lambda _image: '')
        with patch.dict('sys.modules', {'pytesseract': fake_tesseract}), \
             patch('backend.ocr.ANTHROPIC_API_KEY', 'configured'), \
             patch('backend.ocr._claude_transcribe', new=AsyncMock(return_value='Pain 4/10')) as cloud:
            local_only = await run_report_ocr(out.getvalue(), '..\\private.jpg', allow_cloud=False)
            self.assertFalse(local_only['cloud_used'])
            self.assertEqual(local_only['text'], '')
            cloud.assert_not_awaited()
            consented = await run_report_ocr(out.getvalue(), '..\\private.jpg', allow_cloud=True)
            self.assertTrue(consented['cloud_used'])
            self.assertEqual(consented['metrics']['pain_score'], 4)
            cloud.assert_awaited_once()
            self.assertEqual(consented['filename'], 'private.jpg')


if __name__ == '__main__':
    unittest.main()
