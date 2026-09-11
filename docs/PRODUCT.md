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

The IMU path follows the same idea as Liu et al. (Sensors 2024) — wearable kinematics on the arm — but RehabAI **does not classify frozen shoulder vs healthy**. IMU features are quality metrics, not a diagnosis.

Demo patient P102 is synthetic seed data for the judge walkthrough. Live hardware must be labelled live and must fail closed when the camera is absent. A live RealSense session never receives a simulated IMU.
