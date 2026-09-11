# RehabAI product notes

RehabAI is a hospital workstation for adhesive capsulitis **assessment support** and **guided rehabilitation**.

It does not diagnose frozen shoulder from the camera.

It does:

- estimate upper-arm elevation relative to the torso from RGB-D pose
- count repetitions with a deterministic state machine
- flag torso compensation
- store a longitudinal record
- let a physiotherapist review progress and approve AI drafts

Demo patient P102 is synthetic seed data for the judge walkthrough. Live hardware must be labelled live and must fail closed when the camera is absent.
