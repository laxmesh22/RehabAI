# RehabAI — agent handoff

## User request and scope
MEDHA PS 8: AI-assisted adhesive capsulitis assessment and guided rehabilitation. Not a diagnosis service. Jetson + RealSense + arm IMU + optional GPU server. Continue this checkout; do not restart scaffolding.

## Product decisions
- Hospital studio on FastAPI (`backend.main`) is the demo product. Original loopback station (`backend.server` :8765) is preserved.
- Simulation and live sources are labelled. Live never silently becomes synthetic. A live RealSense session never receives a simulated IMU.
- Joint angles are deterministic vector math. Camera ROM counts reps. Arm IMU supplies rate/quality. LLM is optional and cannot override BLOCK.
- IMU is **not** a frozen-shoulder vs healthy classifier (Liu et al. 2024 is a reference for wearable kinematics, not for diagnosis in this product).
- Video/JPEG is not stored in SQL. Structured metrics go to SQLite (default) or PostgreSQL via `DATABASE_URL`.
- Voice intake collects patient-reported pain/function. It does not diagnose. Numbers are parsed or tapped, never invented.

## Current status (2026-09-11, dual sensor: RealSense + arm IMU)

### Connected
- Edge pipeline: simulation kinematics → EMA → camera angles → complementary IMU fusion → exercise FSM → safety → telemetry.
- Default simulation now attaches `SimulatedArmImu` (upper arm). Overlay/studio labelled **synthetic RGB-D + arm IMU**.
- Live IMU: `REHABAI_IMU_TRANSPORT=udp|serial`. Missing live IMU is `imu_ok: false`, never filled with synthetic packets. `REHABAI_IMU_REQUIRED=1` blocks calibration until IMU streams.
- Overlay/JPEG: Pillow on this Windows machine; OpenCV used if installed.
- Finish path writes sessions, session_metrics (including IMU summary), exercise_results, pain_scores, rom_measurements, compensation_events, recordings, alerts, assessments, audit_logs.
- Assessment intake and voice path unchanged.

### Verification
- `tests.test_imu` plus overlay/platform IMU assertions.
- Hardware, Jetson latency, IMU-camera calibration, and clinical accuracy are NOT verified.

## How to run
```
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
http://127.0.0.1:8000 — demo password `rehabai-demo`.

Live camera: `REHABAI_SOURCE=live` and `REHABAI_POSE_MODEL` to a local pose file. Live IMU: `REHABAI_IMU_TRANSPORT=udp` and JSON packets to port 8766. Do not auto-download pose weights.

## Next actions
1. On Jetson, prove RealSense RGB+depth and a local pose model together with a strapped arm IMU.
2. Optional: wrist IMU (`REHABAI_IMU_PLACEMENTS=arm,wrist`) after the arm path is honest on hardware.
3. Optional: swap browser STT/TTS for Sarvam Saaras + Bulbul when keys are provided.
4. Install `opencv-python-headless` on a network that allows PyPI.

## Continuation
Inspect files and tests; keep simulation vs live distinct. Update this file after each stage. Never report hardware/clinical validation from simulation.
