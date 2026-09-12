# RehabAI consumer Android APK

Talk-first patient shell. Same mint UI as the hospital studio, without the clinician rail.

This is **not** a diagnosis service. Phone camera ROM is available when OpenCV MediaPipe pose is ready (`REHABAI_POSE_KIND=opencv` via local `.venv` / pose sidecar, a `REHABAI_POSE_MODEL`, or hosted `REHABAI_PHONE_INFERENCE_URL`). The UI offers phone and simulation as separate choices; neither silently becomes the other.

## Hosted backend (Railway)

Account: `josbahu@gmail.com` · Project: **rehabai** · Service: **rehabai-api**

Public URL:

```text
https://rehabai-api-production.up.railway.app
```

Consumer Talk entry:

```text
https://rehabai-api-production.up.railway.app/?consumer=1#/app
```

Demo password: `rehabai-demo` (patient `ananya.sharma@demo.local`).

Provider and pose-service keys stay in server variables (from local `.env`). Never put keys in the APK.

Redeploy from the repo root:

```powershell
$env:SSL_CERT_FILE=$null
railway up -y -d -s rehabai-api
```

## Shareable APK

The Capacitor app loads the Railway URL (HTTPS). Build:

```powershell
cd mobile
npm install
npm run cap:sync
npm run build:apk
```

Accept the Android SDK 34 licence through Android Studio's SDK Manager before the first build. The build helper selects an installed Microsoft JDK 17 and the standard Android SDK path when those environment variables are unset.

APK path:

```text
mobile/android/app/build/outputs/apk/debug/app-debug.apk
```

Copy for sharing:

```text
releases/RehabAI-consumer-debug.apk
```

Install on Android by enabling “Install unknown apps” for your file manager / browser.

## Local emulator override

```powershell
$env:REHABAI_SERVER_URL='http://10.0.2.2:8000/?consumer=1#/app'
cd mobile
npm run cap:sync
```

For a physical phone on the same trusted Wi-Fi:

```powershell
# Repository root
.\Start-RehabAI.ps1 -Phone

# Before syncing/building the APK
$env:REHABAI_SERVER_URL='http://192.168.x.x:8000/?consumer=1#/app'
cd mobile
npm run cap:sync
```

## Permissions

`AndroidManifest.xml` includes `INTERNET`, `RECORD_AUDIO`, `CAMERA`, `MODIFY_AUDIO_SETTINGS`. Cleartext is enabled for the LAN demo build; use HTTPS for deployment.

## Phone measurement limits

The app asks for camera permission only after the user starts a phone session and presses **Enable phone camera**. It uploads bounded JPEG frames sequentially and stops tracks when the session closes. The backend stores measurements and landmarks, not the uploaded raw frames.

Phone results are labelled 2D-derived, with depth shown as **Not used**. The current implementation supports frontal-plane shoulder abduction/elevation only. It has not been physically or clinically validated.

## Memory

`storage/patients/{id}/memory.json` stores intake and short session summaries for the voice agent. On Railway the filesystem is ephemeral unless a volume is attached — seed data is recreated on boot.
