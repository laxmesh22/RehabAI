# RehabAI — agent handoff

## User request and scope
MEDHA PS 8 adhesive capsulitis assessment support — not a diagnosis service.

## This stage
Analysed and kept folder `rehab-ai-avatar-and-pain-check/` (avatar + pain check package).

### Exercises wired to FollowAvatar
Catalog in `edge/exercises/library.py` now includes `avatar_demo` for every entry:
- shoulder_abduction → abduction
- shoulder_flexion → flexion
- assisted_flexion / wand_flexion → wand_flexion
- wall_climb → wall_walk
- pendulum → pendulum
- external_rotation → er
- wand_er → wand_er

UI: exercise picker on patient + consumer dashboard; live session mounts the matching 3D demo + target.

### Pain check
Ported emoji 0–10 pain UI (`web/pain_scale.js`) into session debrief (replaces number chips).

## Verification
- `python -m unittest tests.test_pain_scale tests.test_motion tests.test_consumer -v`
- Cache `?v=31` / `nura17`

## Next
Redeploy Railway; rebuild APK when needed.
