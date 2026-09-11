# RehabAI — agent handoff

## User request and scope
MEDHA PS 8 adhesive capsulitis assessment support — not a diagnosis service.

## Product decisions
- **Hospital studio** at `/` unchanged for clinicians.
- **Consumer app** (`/?consumer=1#/app`) is a **talk-first two-slide flow**:
  1. **Talk** — giant centered Talk orb. Fast local agent: short prompts from missing PS slots; deterministic number parse; **auto-save + next question in one turn** (no confirm round-trip). Numbers never invented.
  2. **You (dashboard)** — personalized record from voice scores + OCR + stored ROM/pain charts.
- Consumer latency defaults: single STT engine (`fast=True`), **browser TTS** (`speak=0`), no Claude on hot path (`REHABAI_CONSUMER_CLAUDE=1` enables optional freer wording).
- OCR extracts only clearly printed numbers. Railway: https://rehabai-api-production.up.railway.app
- APK: https://github.com/laxmesh22/RehabAI/releases/tag/consumer-apk-v0.1.0

## This stage (actual changes)
- `agent/consumer_voice.py` — local-first fast path; auto-accept clear numbers; Claude optional via env.
- `backend/routers.py` + `backend/voice.py` — consumer STT `fast=True`, 6s STT timeout.
- `web/voice_agent.js` — consumer `speak=0`, tighter VAD / shorter settle delays.
- Tests updated for one-turn advance. Cache `?v=27` / `nura14`.

## Verification
- `python -m unittest tests.test_consumer tests.test_voice -v` — OK.

## Next
1. Redeploy Railway after this push (`railway up -y -d -s rehabai-api` with `.railwayignore`).
2. Rebuild APK when phones should pick up UI/voice changes.
