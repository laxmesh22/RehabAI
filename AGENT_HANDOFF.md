# RehabAI — agent handoff

## User request and scope
MEDHA PS 8 adhesive capsulitis assessment support — not a diagnosis service.

## Product decisions
- **Hospital studio** at `/` unchanged for clinicians.
- **Consumer app** (`/?consumer=1#/app`) talk-first two-slide flow.
- **Claude is the voice agent brain** (`ANTHROPIC_MODEL`, default `claude-sonnet-5`): plans next spoken questions / rephrases from missing PS slots. Opt out only with `REHABAI_CONSUMER_CLAUDE=0`.
- **Numbers stay deterministic** from patient words — Claude never invents scores.
- Latency opts that do **not** downgrade Claude: one-turn number save (skip confirm), single STT engine, tighter VAD. Cloud TTS (Sarvam) restored for voice quality.
- Railway: https://rehabai-api-production.up.railway.app

## This stage
- Restored Claude-on-by-default after a speed pass had disabled it.
- Restored cloud TTS (`speak=1`).
- Cache `?v=28`.

## Verification
- `python -m unittest tests.test_consumer tests.test_voice -v`

## Next
Redeploy Railway after push.
