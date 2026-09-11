# RehabAI — agent handoff

## User request and scope
MEDHA PS 8 adhesive capsulitis assessment support — not a diagnosis service.

## Product decisions
- **Hospital studio** at `/` unchanged for clinicians.
- **Consumer app** (`/?consumer=1#/app`) is a **talk-first two-slide flow**:
  1. **Talk** — brand + giant centered Talk orb only (no scripted 0/6 card, no long pretext). User taps Talk; Claude decides the next spoken question from missing PS slots. Numbers are parsed deterministically from the patient’s words — never invented. After slots: report photo OCR or skip.
  2. **You (dashboard)** — personalized record from voice scores + OCR + stored ROM/pain charts.
- OCR extracts only clearly printed numbers (`backend/ocr.py`). Never invents ROM/pain.
- Memory: `storage/patients/{id}/memory.json` includes `report_phase` and `reports[]`.
- Railway host: https://rehabai-api-production.up.railway.app (josbahu@gmail.com · project rehabai).
- APK release: https://github.com/laxmesh22/RehabAI/releases/tag/consumer-apk-v0.1.0

## This stage (actual changes)
- `web/app.js` — minimal Talk hero with `#voice-dock.talk-orb`; OCR panel only when report phase; no auto-start.
- `web/style.css` — talk-orb / talk-hero styles; removed questionnaire card UI.
- `web/voice_agent.js` — live captions on orb screen; reveal report panel on `await_report`.
- `agent/consumer_voice.py` — autonomous Claude next-turn planner; short fallback asks; short greeting.
- Tests updated for `consumer-fallback` / autonomous engines.
- Cache-bust `?v=26` / `style.css?v=nura13`.

## Verification
- Run: `python -m unittest tests.test_consumer tests.test_voice -v`

## Unresolved / next
1. Redeploy Railway after push so production serves the Talk-orb UI.
2. Rebuild APK workflow when phones should pick up UI.
3. Optional: install tesseract on Railway for offline OCR (Claude vision is fallback).
