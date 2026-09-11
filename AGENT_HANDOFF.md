# RehabAI — agent handoff

## User request and scope
MEDHA PS 8 adhesive capsulitis assessment support — not a diagnosis service.

## Product decisions
- **Hospital studio** at `/` unchanged for clinicians.
- **Consumer app** (`/?consumer=1#/app`) is a **two-slide agentic flow**:
  1. **Agent** — one voice agent runs the six-question SCRIPT, then asks for a report photo (OCR) or skip.
  2. **Dashboard** — personalized record from voice scores + OCR metrics + stored ROM/pain charts.
- OCR extracts only clearly printed numbers (`backend/ocr.py`). Never invents ROM/pain.
- Memory: `storage/patients/{id}/memory.json` includes `report_phase` and `reports[]`.
- Railway host: https://rehabai-api-production.up.railway.app (josbahu@gmail.com · project rehabai).
- APK release: https://github.com/laxmesh22/RehabAI/releases/tag/consumer-apk-v0.1.0

## Verification
- `python -m unittest tests.test_ocr tests.test_consumer tests.test_voice -v` — OK.
- Cache-bust `?v=25` / `style.css?v=nura12`.

## Next
1. Redeploy Railway after this push.
2. Rebuild APK workflow when consumer URL/UI should ship to phones.
3. Optional: install tesseract on Railway for offline OCR (Claude vision is fallback).
