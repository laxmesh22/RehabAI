"""OCR extracts printed numbers only and never invents ROM."""
from __future__ import annotations

import unittest

from backend.ocr import extract_metrics_from_text


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


if __name__ == '__main__':
    unittest.main()
