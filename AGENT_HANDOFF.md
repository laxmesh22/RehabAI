# RehabAI — agent handoff

## User request and scope
MEDHA PS 8: AI-assisted adhesive capsulitis assessment and guided rehabilitation. Not a diagnosis service. Jetson + RealSense + arm IMU + optional GPU server. Continue this checkout; do not restart scaffolding.

## Product decisions
- Hospital studio on FastAPI (`backend.main`) is the demo product.
- **Consumer APK** (`mobile/`, Capacitor) is Talk-first at `/?consumer=1#/app`, pointed at the Railway HTTPS host for sharing.
- Simulation and live sources stay labelled. Phone RGB never mixes with simulation angles.
- Patient memory is `storage/patients/{id}/memory.json` (ephemeral on Railway without a volume) plus SQL.
- Voice: Sarvam Subh TTS; Talk STT prefers ElevenLabs then Sarvam; skips Windows offline STT on the live path.

## Current status (2026-09-11, Railway + shareable APK)

### Hosted backend (Railway · josbahu@gmail.com)
- Project: **rehabai** · Service: **rehabai-api**
- URL: https://rehabai-api-production.up.railway.app
- Consumer: https://rehabai-api-production.up.railway.app/?consumer=1#/app
- Domain target port: **8080** (Railway `PORT`)
- Voice keys loaded from local `.env` into Railway variables (not in git)

### Shareable APK
- GitHub release: https://github.com/laxmesh22/RehabAI/releases/tag/consumer-apk-v0.1.0
- Local copy: `releases/RehabAI-consumer-debug.apk` (~3.8 MB)
- Capacitor `server.url` → Railway consumer shell
- CI workflow: `.github/workflows/build-apk.yml`

### Verification
- Railway `/api/health` → 200, `source=simulation`, voice providers configured
- Browser: Railway consumer greets Ananya and shows Talk dock
- Local `gradlew assembleDebug` blocked on this PC by TLS MITM to Maven; CI build succeeded
- Hardware / clinical accuracy NOT verified

## How to run locally
```
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Redeploy Railway from repo root (unset SSL_CERT_FILE if CLI fails):
```
railway up -y -d -s rehabai-api
```

## Next actions
1. Share the GitHub release APK or the Railway consumer URL.
2. Optional: attach a Railway volume for persistent `storage/` + SQLite.
3. Jetson RealSense + pose model path remains local/hardware work.

## Continuation
Keep simulation vs live distinct. Never report hardware/clinical validation from simulation.
