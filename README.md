# RehabAI

Hospital-oriented prototype for **AI-assisted adhesive capsulitis assessment and guided physiotherapy**. It is a decision-support product, not a diagnosis service.

The unique record it keeps is a **digital biomechanical twin of the shoulder rehabilitation journey**: ROM, compensation, reps, pain, and session history stored over time. A supervisor agent can reason only over those stored values.

## What to run for the hackathon demo

Python 3.10+ with FastAPI / SQLAlchemy / PyJWT (already present in this workspace):

```powershell
.\Start-RehabAI.ps1
```

or:

```sh
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**

## Patient registration, voice questionnaire, and exports

The clinician workflow starts on **Patients → Register patient**. Identifiers are typed and visually confirmed. Starting an assessment then opens a six-question English/Hindi voice intake for pain and daily function. Every captured value requires confirmation; no answer is filled automatically.

Sarvam Saaras, ElevenLabs, and Claude run from the FastAPI server so provider keys never enter browser JavaScript. Copy `.env.example` to `.env`, add newly rotated keys, then run `Start-RehabAI.ps1`. The launcher loads `.env` without printing it. `/api/health` reports the active speech and clarification providers without exposing credentials.

After sign-in, tap **Talk** once. RehabAI keeps listening and answering until you tap again or say you are done. It cannot invent ROM or override a BLOCK.

```powershell
Copy-Item .env.example .env
notepad .env
.\Start-RehabAI.ps1
```

The patient profile has **Export JSON** and **Export Excel** actions. Both exports are access controlled, audit logged, and retain `simulation`, `live`, and `demo / synthetic` labels. See [the voice intake workflow](docs/VOICE_INTAKE_WORKFLOW.md) for the provider data boundary and failure behavior.

Demo accounts (password `rehabai-demo`):

| Role | Email |
|---|---|
| Physiotherapist | `priya.mehta@hospital.local` |
| Patient | `ananya.sharma@demo.local` |
| Doctor | `dr.rao@hospital.local` |
| Admin | `admin@hospital.local` |

Demo patient **Ananya Sharma (P102)** is labelled demo/synthetic. Baseline abduction 72°, flexion 94°, pain 7/10; later abduction 103°, flexion 121°, pain 4/10. Those values are seed data, not a live capture.

### Suggested live demo

1. Sign in as Priya Mehta.
2. Open Ananya Sharma, or **Register patient** first.
3. Start assessment.
4. Complete and confirm the six pain/function questions (Speak or tap). Measurement is locked until this is done.
5. Wait until Pose and Distance read OK (Camera/Depth stay **Simulation**).
6. Confirm tracking.
7. Watch abduction climb. The **3D guide** (lime arm) is what to copy; it is not the patient. Cues are spoken when they change.
8. Click **Lean sideways** — coaching should say to keep the trunk upright, and the guide trunk stays vertical.
9. Return to Normal, complete a repetition.
10. Stop & save, enter pain after, then read the stored session recap.
11. Open Progress, then ask the AI: `Compare this assessment with the previous session.`
12. Export the patient record as JSON or Excel and open Reports.

The original loopback patient station still works:

```sh
python -m backend.server
```

http://127.0.0.1:8765 — schematic simulation only.

## Edge measurement (Jetson)

Hardware is **not** assumed present. Simulation never reports itself as a live camera. A live RealSense session never receives a simulated IMU.

Standalone prototype (synthetic skeleton + synthetic arm IMU + real angle math):

```sh
python -m edge.prototype --source simulation --seconds 12
```

Live RealSense + pose (fails closed if the camera or model is missing):

```sh
set REHABAI_SOURCE=live
set REHABAI_POSE_MODEL=C:\path\to\pose.task
set REHABAI_POSE_KIND=mediapipe
set REHABAI_IMU_TRANSPORT=udp
set REHABAI_IMU_REQUIRED=1
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Arm IMU JSON at 100 Hz to UDP `127.0.0.1:8766` (or `REHABAI_IMU_TRANSPORT=serial` with `REHABAI_IMU_SERIAL`). Accel m/s², gyro deg/s. Default simulation mode already runs **both** a synthetic RGB-D skeleton and a synthetic upper-arm IMU; they are labelled simulation.

Optional edge packages: `pip install -r requirements-edge.txt` after matching JetPack / RealSense / CUDA. Do not auto-download weights.

### Phone RGB capture

OpenCV is an image-processing library, not the trainable model. The phone sends short-lived JPEG frames; a MediaPipe or YOLO pose model runs either inside FastAPI or on the separate GPU pose service. Only normalized landmarks and session metrics are retained by default.

Local model on the FastAPI host:

```powershell
$env:REHABAI_POSE_MODEL='C:\models\pose_landmarker.task'
$env:REHABAI_POSE_KIND='mediapipe'
.\Start-RehabAI.ps1 -Phone
```

Hosted model on the GPU server:

```powershell
# GPU server
$env:REHABAI_POSE_MODEL='C:\models\best.pt'
$env:REHABAI_POSE_KIND='yolo'
$env:REHABAI_POSE_DEVICE='0'
$env:REHABAI_PHONE_INFERENCE_API_KEY='use-a-random-internal-key'
python -m uvicorn ml.pose_service:app --host 0.0.0.0 --port 8010

# Hospital backend
$env:REHABAI_PHONE_INFERENCE_URL='http://gpu-server:8010'
$env:REHABAI_PHONE_INFERENCE_API_KEY='use-the-same-internal-key'
.\Start-RehabAI.ps1 -Phone
```

Phone measurements are explicitly labelled `phone_rgb_2d`: there is no measured depth or distance. This stage supports frontal-plane abduction/elevation only. Train from an annotated, subject-disjoint dataset with `scripts/train_pose.py`; its output remains unvalidated until held-out evaluation and physical comparison against a reference measurement are completed.

If the GPU/LLM server is down, ROM, reps, compensation and safety still run. Set `LLM_BASE_URL` only when a local OpenAI-compatible endpoint exists.

## Next.js console (optional)

```sh
cd frontend
npm install
npm run dev
```

http://localhost:3000 proxies `/api` to port 8000. The complete split-screen studio used for judging is the FastAPI app on port 8000.

## Tests

```sh
python -m unittest discover -s tests -v
```

These tests do **not** validate camera accuracy, Jetson latency, or clinical performance.

## Architecture

RealSense + arm IMU → Jetson edge pipeline, or phone RGB → local/hosted pose model → FastAPI → SQLite (default) or PostgreSQL → clinician/patient studio + supervisor agent.

See `docs/ARCHITECTURE.md` and `AGENT_HANDOFF.md`.

## Safety and clinical limits

- Joint angles are deterministic vector geometry, not LLM output. Repetition counting uses camera ROM; the IMU contributes rate and quality, not a disease label.
- The 3D coach uses Mixamo bones from telemetry. An optional LLM may rephrase the cue only. It cannot override BLOCK or set joint angles.
- Safety ALLOW / WARN / PAUSE / BLOCK / CLINICIAN_REVIEW is deterministic. The agent cannot override BLOCK.
- Invalid or low-confidence ROM is not used in progress analysis.
- No automatic diagnosis of adhesive capsulitis.
- No automatic treatment change. Draft plans require physiotherapist approval.
- Recording requires consent. Video is not stored in PostgreSQL.
- Admins manage staff; they cannot start treatment sessions.
