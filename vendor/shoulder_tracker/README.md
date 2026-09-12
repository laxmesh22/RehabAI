# Shoulder Tracker reference (OpenCV + MediaPipe Pose)

This folder holds the pretrained detection stack used by RehabAI phone capture.

## Product integration (do not run ROM from this demo in the API)

| Piece | Role |
|-------|------|
| `shoulder_rom_tracker.py` | Standalone demo only (own HUD / ROM). **Not** imported by FastAPI. |
| `.wheels/` | Offline `mediapipe` + `opencv` wheels for Python **3.12** |
| Repo `edge/pose/opencv_mediapipe_estimator.py` | Same MediaPipe Solutions landmarks, adapted for RehabAI (default complexity 1; env tunables) |
| `REHABAI_POSE_KIND=opencv` | Selects that estimator (no `.task` / `.pt` file) |
| `edge/pipeline.py` | RehabAI ROM / reps / safety + phone angle MA (keeps simulation vs phone labelled) |

## Bootstrap on a laptop (Python 3.13 host)

```powershell
.\scripts\Ensure-OpenCVPose.ps1
.\Start-RehabAI.ps1 -Phone
.\.venv\Scripts\python.exe scripts\smoke_opencv_pose.py
```

`Start-RehabAI.ps1` either runs pose **in-process** (when the API Python can import mediapipe) or starts `ml.pose_sidecar` on `:8091` and sets `REHABAI_PHONE_INFERENCE_URL`.

## Railway / Docker

`Dockerfile` uses `python:3.12-slim` + `requirements-cloud.txt` (`opencv-python-headless`, `mediapipe==0.10.21`) with `REHABAI_POSE_KIND=opencv` — in-process phone pose, no sidecar.

## Phone / APK

Consumer UI offers **Phone camera** when `/api/health` reports `phone_pose_available: true`. Frames POST to `/api/sessions/{id}/phone-frame` as JPEG. Measurements stay `phone_rgb_2d` (no depth) — not a diagnosis.
