# RehabAI — agent handoff

## User request and scope
MEDHA PS 8: AI-assisted adhesive capsulitis assessment and guided rehabilitation. Not a diagnosis service. Jetson + RealSense + optional GPU server. Continue this checkout; do not restart scaffolding.

## Product decisions
- Hospital studio on FastAPI (`backend.main`) is the demo product. Original loopback station (`backend.server` :8765) is preserved.
- Simulation and live sources are labelled. Live never silently becomes synthetic.
- Joint angles are deterministic vector math. LLM is optional and cannot override BLOCK.
- Video/JPEG is not stored in SQL. Structured metrics go to SQLite (default) or PostgreSQL via `DATABASE_URL`.

## Current status (2026-09-11, pipeline + database wiring)

### Connected
- Edge pipeline: simulation kinematics → EMA → angles → exercise FSM → safety → telemetry.
- Overlay/JPEG: `edge/overlay.py` prefers OpenCV; this Windows machine could not install `cv2` (RAIT firewall/SSL). Pillow encodes JPEG. Frames are labelled SYNTHETIC RGB when simulated.
- Live hub keeps a preview JPEG; WebSocket attaches it to the latest sample; consented sessions write `telemetry.json` + `skeleton.jpg` under `storage/patients/...` (gitignored).
- Finish path writes **sessions, session_metrics, exercise_results, pain_scores, rom_measurements, compensation_events, recordings, alerts, assessments, audit_logs**.
- Seed backfills `session_metrics` for demo sessions SESS-01…06.
- `GET /api/health` reports database scheme, table list, `opencv` bool, `frame_encoder`.
- `GET /api/sessions/{id}/metrics` and `/frame`.

### Verification
- Overlay + platform session tests passed, including `session_metrics` after finish.
- Full `unittest discover` was 45 tests; one flaky ConnectionReset on the old CORS test, passed on rerun.
- Hardware, Jetson latency, and clinical accuracy are NOT verified.
- `cv2` is not installed here. Pipeline still renders frames via Pillow.

## How to run
```
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
http://127.0.0.1:8000 — demo password `rehabai-demo`.

Live camera: `REHABAI_SOURCE=live` and `REHABAI_POSE_MODEL` to a local pose file. Do not auto-download.

## Next actions
1. Install `opencv-python-headless` on a network that allows PyPI; overlay will switch to cv2 automatically.
2. On Jetson, prove RealSense RGB+depth and a local pose model.
3. Optional `DATABASE_URL=postgresql+psycopg2://...` with `docker/docker-compose.yml`.
4. Refresh the browser after restart so it loads the JPEG live view.

## Continuation
Inspect files and tests; keep simulation vs live distinct. Update this file after each stage. Never report hardware/clinical validation from simulation.
