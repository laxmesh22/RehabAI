# RehabAI — agent handoff

## User request and scope
After Talk finishes interpreting the intake, hand off to the camera and measure a
baseline ("pre-diagnosis" screening) before the first rehab session. Then: the
camera did not open when the baseline button was clicked, and the OpenCV/MediaPipe
stack already in this repo had to actually run in that flow.

## This stage

### Baseline phase between Talk and the dashboard
- `backend/memory.py`: `BASELINE_MOVEMENTS = ('abduction', 'flexion')`, `empty_baseline`,
  `normalize_baseline`, `record_baseline_measurement`, `next_baseline_movement`,
  `baseline_complete`, `skip_baseline_phase`.
- `agent/consumer_voice.py` / `agent/autonomous.py`: new `baseline` phase; Talk ends with
  `action=start_assessment` instead of `open_home` until both movements are measured.
- `backend/routers.py`: `/api/consumer/me` exposes `baseline` and gates `home_ready`;
  finishing an `assessment` session records the baseline; `POST /api/consumer/baseline-skip`.
- `web/app.js`: `#/app/baseline` route, findings view, chained abduction -> flexion,
  pose overlay canvas on the in-app camera, 3-step nav (Talk / Measure / Home).

### Flexion is measured side-on, not from the front
A single 2D camera cannot resolve a forward raise from the front: `calculate_flexion`
projects the arm onto the torso forward axis, which is perpendicular to the image plane,
so a frontal phone view returns only 0 or 180 degrees. Rather than report a fabricated
number, `shoulder_flexion` now asks the patient to turn 90 degrees.
- `edge/biomechanics/angles.py`: `torso_down`, `calculate_sagittal_elevation`, `sagittal_compensation`.
- `edge/pipeline.py`: `plane` ('frontal' | 'sagittal'), `_sagittal_features`.
- `edge/phone_capture.py`: `_sagittal_framing_ok` (shoulders must nearly overlap, lower
  confidence floor because the far side is occluded), profile `phone_rgb_2d_sagittal`.
- `edge/calibration.py`: sagittal calibration branch and messages.

**This is unvalidated.** It is arm elevation read in the image plane, not an isolated
glenohumeral flexion angle, and it has not been checked against goniometry.

### Camera not opening — root cause and fix
Two independent causes, both fixed:

1. **No pose backend was reachable.** The API runs on Anaconda Python 3.13, which has no
   MediaPipe wheels, so `opencv_mediapipe_available()` was false and
   `phone_pose_available` was false. `baselineCapture()` therefore fell back to
   `simulation` and never requested a camera. The working stack lives in `.venv`
   (Python 3.12, mediapipe 0.10.21, opencv 4.10) and is meant to be reached over the
   sidecar, but the sidecar was not running and `REHABAI_PHONE_INFERENCE_URL` was unset.
   The uvicorn process being used had also been started by hand rather than through
   `Start-RehabAI.ps1`, which does all of this automatically, and a second stale uvicorn
   from an earlier session was still bound to port 8000 and winning connections.
   - Started `ml/pose_sidecar.py` on `.venv` at `http://127.0.0.1:8091`.
   - Persisted `REHABAI_PHONE_INFERENCE_URL=http://127.0.0.1:8091` in `.env`.
   - **Use `Start-RehabAI.ps1` to run the server.** It already ensures the sidecar,
     falls back to in-process when the API interpreter can import MediaPipe, and
     disables phone capture cleanly when neither is possible.

2. **A second tap was required.** Landing in a phone session only rendered the video;
   the user still had to press "Enable phone camera". `renderLive` now calls
   `startPhoneCamera` automatically for `source === 'phone'`, and on failure restores the
   manual button with the browser's reason.

### Bug found while verifying: invented baselines
A session where tracking never locked on still has `peak_angle = 0`, and that 0 was being
stored as a measured baseline — a fabricated frozen-shoulder reading. `is_valid_rom` was
extracted from `backend/services/persist.py` (coverage >= 50, peak > 0, safety not
PAUSE/BLOCK) and now gates the baseline write too. `measurement_plane` also moved into the
base telemetry dict so it is reported on lost-tracking frames instead of being `None`.

### Renamed the app to PhysioBuDDY
User-facing branding only: `web/index.html` title and boot text, all display strings in
`web/app.js`, the preview page, the FastAPI title and API landing pages in
`backend/main.py`, the launcher banner, and the APK name
(`mobile/capacitor.config.js`, `mobile/package.json`, `android/.../strings.xml`).

Deliberately **not** renamed, because changing them breaks running installs:
`rehabai_token` / `rehabai_api_origin` localStorage keys (renaming signs everyone out),
the `'rehabai'` WebSocket subprotocol, `REHABAI_*` environment variables, the
`X-RehabAI-Inference-Key` header, the Android `appId` `ai.rehab.consumer` (changing it
makes the APK a different app and blocks upgrades), Python module and class names,
repo/docs references, and `Start-RehabAI.ps1`'s filename.

## Verification
- `python -m pytest tests -q`: **204 passed, 1 failed**. The failure,
  `test_platform.py::test_dashboard_series_matches_stored_rom`, is pre-existing and
  unrelated (order-sensitive `_focus_patient` fallback); confirmed failing on a clean
  tree via `git stash`.
- `scripts/_e2e_phone_pose.py` against the live API: `phone_pose_available: True`,
  both baseline sessions start with `source: phone`, frames reach the real model
  (`mediapipe-solutions:pose:c0`), abduction gets `phone_rgb_2d` / plane `frontal` and
  flexion gets `phone_rgb_2d_sagittal` / plane `sagittal`. A blank test frame correctly
  yields "Exactly one person must be fully visible" and records **no** baseline.
- `scripts/_webcam_pose_check.py` on `.venv`: opens the real webcam, MediaPipe loads and
  runs 40 frames without error. No person was in frame, so no detection — run it again
  while standing in view to see live angles.
- Browser check: clicking "Open camera & measure" navigates to the live session and
  attaches a camera stream automatically (`srcObject` true, 1280px, `readyState` 4) with
  no second click.
- `node --check web/app.js`; `web/`, `vercel-web/`, `mobile/www/` byte-identical; `app.js?v=66`.
- Restarted via `Start-RehabAI.ps1 -Phone`: it starts the sidecar itself and reports
  `phone_pose_available: True`, `openapi title: PhysioBuDDY`. Note this mode runs
  **without `--reload`**, so backend edits need a restart — which is also why in-memory
  live sessions no longer get wiped mid-session.
- Stopping the server needs care: an orphaned uvicorn worker can outlive its reloader
  parent and keep port 8000 bound. Find it with
  `Get-CimInstance Win32_Process | Where-Object CommandLine -match 'uvicorn'` and
  `taskkill /PID <id> /T /F`.

## Unresolved
- Sagittal flexion is unvalidated against goniometry. Keep it labelled as such.
- No end-to-end run with a person actually in frame, so measured angles are unverified.
- `test_dashboard_series_matches_stored_rom` pre-existing order-sensitivity.
- Live RealSense/Jetson path still unvalidated; clinic live intake still uses tap chips.
- Packaged `index.html` still hardcodes the campus LAN `172.16.6.7:8000` plus Railway.
- Railway/APK: the deployed API needs its own pose backend for phone capture there.

## Next
- Stand in front of the camera and run the baseline for real; confirm abduction and the
  side-on flexion produce sane angles and that the overlay lands on the right limb.
- Commit: the tree has had extensive uncommitted changes since `7b74104`.
- Rebuild the APK once the flow is confirmed on a person.
