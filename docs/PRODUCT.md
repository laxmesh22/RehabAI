# RehabAI product notes

RehabAI is a hospital workstation for adhesive capsulitis **assessment support** and **guided rehabilitation**.

It does not diagnose frozen shoulder from the camera.

It does:

- estimate upper-arm elevation relative to the torso from RGB-D pose
- read an **upper-arm IMU** for angular rate and movement-quality features (smoothness, gyro/accel stats)
- fuse camera ROM with IMU rate (complementary filter). Camera ROM remains the repetition source of truth
- count repetitions with a deterministic state machine
- flag torso compensation and camera/IMU disagreement
- store a longitudinal record
- let a physiotherapist review progress and approve AI drafts
- show a Mixamo-rigged **guide avatar** the patient copies. Bones follow measured/session telemetry. The LLM does not pose the mesh and does not diagnose.

The IMU path follows the same idea as Liu et al. (Sensors 2024) — wearable kinematics on the arm — but RehabAI **does not classify frozen shoulder vs healthy**. IMU features are quality metrics, not a diagnosis.

Demo patient P102 is synthetic seed data for the judge walkthrough. Dashboard and patient charts plot **stored ROM and pain points**, including sessions saved after a trial. Seed stays labelled demo/synthetic. Live hardware must be labelled live and must fail closed when the camera is absent. A live RealSense session never receives a simulated IMU.

## Consumer Android APK

A Capacitor patient shell (`mobile/`) reuses the same mint UI. Entry is Talk-first (`/?consumer=1#/app`): the voice agent asks the six pain/function questions, stores answers in `storage/patients/{id}/memory.json`, then opens a personal dashboard. **Start session** uses the existing OpenCV/pose pipeline (`REHABAI_POSE_KIND=opencv` by default). On hosts without MediaPipe (e.g. Python 3.13), `Start-RehabAI.ps1` starts a local pose sidecar. Without pose (local or `REHABAI_PHONE_INFERENCE_URL`), phone frames are rejected and sessions stay labelled simulation. See [docs/CONSUMER_APK.md](CONSUMER_APK.md).
