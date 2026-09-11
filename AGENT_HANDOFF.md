# RehabAI — agent handoff

## User request and scope
MEDHA PS 8 adhesive capsulitis assessment support — not a diagnosis service.

## What’s left / open
- Rebuild consumer APK when phones should pick up UI + FollowAvatar.
- Optional: vendor Three.js offline for APK (currently CDN `unpkg.com/three@0.160.1`).
- Optional Mixamo `web/models/guide.glb` still not wired (procedural FollowAvatar is the coach).

## Product decisions
- Consumer Talk: Claude agent + deterministic scores + personalized dashboard.
- **3D coach** replaced with rehab.ai **FollowAvatar** (Three.js mannequin + motion maths + goniometer). Live telemetry angle overlays on the arc; LLM never poses the mesh.
- Source dumps kept as `*.txt` in repo root for reference; runtime is `web/motion.js`, `web/follow_avatar.js`, `web/guide_follow.js`.

## This stage
- Ported `motion.ts` / `FollowAvatar.ts` / React view → vanilla ES modules.
- Session layout: guide panel is a host div (not canvas-2d).
- `tests/test_motion.py` covers motion maths.
- Cache `?v=29` / `nura15`.

## Verification
- `python -m unittest tests.test_motion tests.test_consumer -v`
- `node --check web/motion.js web/follow_avatar.js web/guide_follow.js`

## Next
Redeploy Railway after push so production loads FollowAvatar.
