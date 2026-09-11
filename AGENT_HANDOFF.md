# RehabAI — agent handoff

## User request and scope
MEDHA PS 8: AI-assisted adhesive capsulitis assessment and guided rehabilitation. Not a diagnosis service. Jetson + RealSense + arm IMU + optional GPU server. Continue this checkout; do not restart scaffolding.

## Product decisions
- Hospital studio on FastAPI (`backend.main`) is the demo product. Original loopback station (`backend.server` :8765) is preserved.
- **Consumer APK** (`mobile/`, Capacitor) is a Talk-first patient shell at `/?consumer=1#/app`. Same CSS. No clinician rail. Auto-login demo patient Ananya (P102).
- Phone RGB is a separate `phone_rgb_2d` capture profile. It has no measured depth/distance and currently supports frontal-plane abduction/elevation only. It may use a local pose model or `ml/pose_service.py` on the GPU server.
- Simulation and live sources are labelled. Live never silently becomes synthetic. A live RealSense session never receives a simulated IMU. Phone RGB never mixes with simulation angles.
- Joint angles are deterministic vector math. Camera ROM counts reps. Arm IMU supplies rate/quality. LLM is optional and cannot override BLOCK.
- IMU is **not** a frozen-shoulder vs healthy classifier (Liu et al. 2024 is a reference for wearable kinematics, not for diagnosis in this product).
- Video/JPEG is not stored in SQL. Structured metrics go to SQLite (default) or PostgreSQL via `DATABASE_URL`.
- Patient memory for the consumer agent is `storage/patients/{id}/memory.json` (gitignored) plus SQL ROM/pain. Not a hosted vector DB. Claude never receives MRN/name/video.
- Voice intake collects patient-reported pain/function. It does not diagnose. Numbers are parsed or tapped, never invented.
- Speech-to-text: intake uses Sarvam first (`saaras:v4`); the Talk agent uses **ElevenLabs Scribe first** (unless `REHABAI_STT=sarvam`), then Sarvam. Spoken UI uses **Sarvam Bulbul v3 speaker shubh (Subh)**. ElevenLabs TTS is fallback only. Talk STT skips the slow Windows offline recognizer.
- Consumer Talk (`scene=consumer`) asks the six SCRIPT questions, confirms each score, writes memory.json, then `open_home` to the personal dashboard.
- Dashboard KPIs and charts use stored ROM and pain series. P102 seed stays labelled demo/synthetic. Progress is native degrees and pain scores, not a recovery percentage.
- Step 3 of each turn is an allowlisted in-page browser action. The model never receives the DOM, accessibility tree, or identifiers.
- 3D patient guide uses Mixamo / Ready Player Me bone names. Bones are driven by telemetry, not the LLM.
- Assessment intake is mandatory before Confirm tracking. Consumer assessment sessions reuse confirmed memory intake. Home-patient **Start session** rehab path skips six-question UI when memory is already complete.

## Current status (2026-09-11, phone capture and hosted-pose pass)

### Connected
- `#/app` Talk-first consumer UI; `#/app/home` personalized charts; Capacitor project under `mobile/` with mic/camera permissions and cleartext demo server URL.
- Talk loop hardened: generation token, mic kept across turns, TTS/autoplay failures no longer end the call, settle delay after Subh to avoid echo STT, language chips restart instead of hang-up, debounced `ensureTalking` auto-start.
- `POST /api/voice/agent` STT is fail-soft (`allow_offline=False`); empty STT never invents scores.
- Consumer Home offers separate **Start with phone camera** and **Run labelled simulation demo** actions. The phone screen requests camera permission, uploads sequential JPEGs with backpressure, overlays returned landmarks, and stops camera tracks when leaving.
- `edge/phone_capture.py` no longer fabricates 1.6 m depth. Phone calibration uses upper-body framing, and deterministic safety does not block only because monocular RGB lacks depth. RealSense sessions still require depth.
- `ml/pose_service.py` is an optional internal GPU inference endpoint. `scripts/train_pose.py` trains, evaluates a test split, exports ONNX, and writes an unvalidated deployment manifest. No model was trained because this checkout has no annotated dataset or model weights.
- Live `auto` capture now fails closed when live is configured without a model. Patient rehab sessions require an approved-plan exercise. Concurrent calibration and phone finish/ingest races are guarded.
- Health no longer exposes the configured demo password. `Start-RehabAI.ps1` preserves configured source, generates an ephemeral JWT secret when needed, and supports `-Phone` LAN binding.
- WebSocket JWTs moved from URL query strings to the protocol header, session streams now enforce patient-record access, and the launcher disables URL access logs.
- Cache-bust: `style.css?v=nura11`, scripts `?v=25`.

### Verification
- `python -m pytest -q` — 119 passed. Python/JavaScript/PowerShell syntax checks and `git diff --check` passed (line-ending warnings only).
- Android manifest contains the needed camera/microphone/network permissions. Gradle ran with JDK 17 and the installed SDK, but stopped because the local Android SDK 34 licence is not accepted. No new APK was produced.
- No pose model is configured and `mediapipe`, `ultralytics`, and `cv2` are absent from the current Python runtime. The hosted endpoint and browser upload path are tested with fakes; phone hardware, Jetson, D455i, live microphone quality, model accuracy, clinical accuracy, and APK installation are NOT verified. Seed P102 is still synthetic demo data.

## How to run
```
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
Hospital: http://127.0.0.1:8000 — demo password `rehabai-demo`.

Consumer web: http://127.0.0.1:8000/?consumer=1#/app

APK: see [docs/CONSUMER_APK.md](docs/CONSUMER_APK.md). `npm run build:apk` selects JDK 17 and the standard Android SDK path; SDK 34 licence acceptance is still required on this machine.

Voice providers: local ignored `.env`. Do not commit `.env`. Keys pasted in chat should be rotated after the demo.

Live camera: `REHABAI_SOURCE=live` and `REHABAI_POSE_MODEL` to a local pose file. Phone capture uses either that local model or `REHABAI_PHONE_INFERENCE_URL`. Live IMU: `REHABAI_IMU_TRANSPORT=udp`.

## Next actions
1. Accept the Android SDK 34 licence in Android Studio SDK Manager, then run `cd mobile; npm run build:apk` and install the generated debug APK.
2. Supply a real pose model. For hosted inference, install `requirements-edge.txt`, configure model/kind/device/service key, run `python -m uvicorn ml.pose_service:app --host 0.0.0.0 --port 8010`, and point the hospital backend at it with `REHABAI_PHONE_INFERENCE_URL`.
3. Test a physical phone in portrait and landscape; verify permission, framing, latency, reps, stop behavior and labels. Compare phone angles with a goniometer before mixing phone 2D trends with D455i 3D.
4. On Jetson, prove RealSense RGB-D and a local pose model with a strapped arm IMU.

## Continuation
Inspect files and tests; keep simulation vs live distinct. Update this file after each stage. Never report hardware/clinical validation from simulation.
