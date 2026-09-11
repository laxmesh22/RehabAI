# rehab.ai — 3D follow-along model + pain check

Two pieces built so far for the frozen-shoulder rehab hackathon project:

1. **`src/avatar/`** — a three.js model that demonstrates each exercise so the patient has
   something to visually follow, with a live goniometer arc and camera-driven angle input.
2. **`src/components/`** — the frown-to-smiley pain check (a row of tappable emoji buttons)
   shown after each exercise.

## See it working without unzipping anything

Two live pages, already online, no setup required:

- **Follow-along model:** https://claude.ai/code/artifact/7676e2bd-60b1-4c8f-aff9-0ac218356cb9
- **Pain check:** https://claude.ai/code/artifact/68e009c8-5c10-4481-94df-79672cd21619

## See it working from the zip, without a build step

Open either file directly in a browser (double-click it, or drag it into a browser window):

- `preview/follow-avatar.html` — needs internet access once, to load three.js from a CDN.
- `preview/pain-slider-demo.html` — fully offline, no dependencies at all.

Both are the same interactive demos as the live links above, just as local files.

## Using the real source in a React + three.js app

```tsx
import { FollowAvatarView } from './avatar/FollowAvatarView'
import { PainSlider } from './components/PainSlider'
import './components/PainSlider.css'

<FollowAvatarView exercise="wand_er" affectedSide="left" target={45} liveAngle={bandAngle} onRep={n => ...} />

<PainSlider value={pain} onChange={setPain} previousValue={painBefore} moment="after" />
```

`FollowAvatarView.tsx` expects a real `three` package (`npm install three`) — it is not bundled
here, since your actual project will pull it from npm normally. The standalone preview HTML loads
three.js from a CDN instead, purely so it runs with no build step.

## What's not built yet

- Internal rotation and cross-body adduction exercises (not yet added to the avatar).
- The real-time voice coaching loop (in planning — ask for the current spec).
- Wiring `PainSlider`/`ExercisePainCheck` into an actual guided-session screen sequence.

## Tests

`*.test.ts` files run standalone with `tsx` (`npx tsx src/avatar/motion.test.ts`, etc.) — pure
logic, no framework needed to check them.
