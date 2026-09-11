# Architecture

```mermaid
flowchart LR
  Patient --> RealSense
  RealSense -->|USB| Jetson
  Demo[SimulatedPatient labelled simulation] --> Pipeline
  Jetson --> Capture[edge/live_source.py]
  Capture --> Pipeline[edge/pipeline.py]
  Pipeline --> Hub[backend/services/live_hub.py]
  Hub -->|WebSocket telemetry| Studio[web/ clinician + patient]
  Hub --> API[FastAPI backend/main.py]
  API --> DB[(SQLite or PostgreSQL)]
  API --> Agent[agent/orchestrator.py]
  Agent -->|optional| LLM[GPU server OpenAI-compatible]
  Agent --> Tools[agent/tools/clinical.py]
  Tools --> DB
  Safety[agent/safety/engine.py] --> Pipeline
```

## Runtime contract

One hospital platform process (`backend.main`) plus one optional original patient station (`backend.server`). Measurement source is selected at process start (`REHABAI_SOURCE=simulation|live`). Live mode never falls back to synthetic values. The LLM is optional; ROM, reps and BLOCK safety continue without it.

The original station at port 8765 still uses `backend/engine.py` and a schematic arm. The hospital studio uses `edge/` geometry, exercise state machines and WebSocket telemetry.

## Edge pipeline

1. Capture RGB + aligned depth, or an explicit `SimulatedPatient`.
2. `PoseEstimator.predict(frame)` (MediaPipe or YOLO adapter).
3. Depth neighbourhood + deprojection to 3D (`edge/depth/projection.py`).
4. EMA smoothing (`edge/depth/filtering.py`).
5. Deterministic angles (`edge/biomechanics/angles.py`) — not an LLM.
6. Exercise state machine (`edge/exercises/state_machine.py`).
7. Compensation events and `evaluate_safety`.
8. Compact telemetry several times per second. Raw video is not written to the database.

## API (hospital platform)

JWT roles: ADMIN, DOCTOR, PHYSIOTHERAPIST, PATIENT. Patients see only their record. Physiotherapists and doctors see assigned patients. Admins manage staff and may view records but cannot start treatment sessions.

Principal routes live under `/api/`: `auth/login`, `patients`, `sessions`, `agent/query`, `alerts`, `reports`, `exercises`, `plans`, WebSocket `/api/ws/sessions/{id}`.

## Agent

One supervisor. Tools query SQLAlchemy; the language model never receives a database connection or raw video. If `LLM_BASE_URL` is unset, a deterministic summary is produced from tool JSON.

## Storage

Completed session telemetry may be written to `storage/patients/{id}/sessions/{session}/telemetry.json` when recording consent is granted. PostgreSQL holds structured records only.

## Not verified in this workspace

RealSense streaming, Jetson latency, pose model accuracy, depth-to-angle error versus goniometer, and any clinical outcome.
