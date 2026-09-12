# RehabAI — agent handoff

## User request and scope
APK home dashboard does not show OpenCV / Phone camera.

## Cause
Phone camera only appears when `phone_pose_available` is true. Railway reports true; clinic LAN fallback `http://172.16.6.7:8000` reports false (`opencv: false`). APK was sticky-saving the first reachable host (often LAN), so home showed Simulation only.

## This stage
- `resolveApiOrigin` probes all candidates and prefers hosts with `phone_pose_available` (Railway over LAN without OpenCV).
- Home: label **Phone camera · OpenCV**; if pose offline, show API host + **Use Railway (OpenCV)** / **Reset API host**.
- Cache `app.js?v=63`. Sync mobile/www.

## Verification
- Railway `/api/health`: `phone_pose_available: true`.
- LAN `172.16.6.7:8000`: `phone_pose_available: false`.
- APK rebuilt: `releases/RehabAI-consumer-20260912-0917.apk` (+ `RehabAI-consumer-debug.apk`).

## Unresolved
- Clinic LAN still lacks OpenCV/MediaPipe in the running Python — fix local `.venv` / Start-RehabAI if offline phone pose is required.

## Next
- Install new APK → home should show **Phone camera · OpenCV** when using Railway.
- If still Simulation only: tap **Use Railway (OpenCV)** or Reset API host (clears sticky LAN).
