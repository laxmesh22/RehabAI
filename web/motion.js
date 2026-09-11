/** Pure motion maths for the follow-along model. Ported from rehab.ai motion.ts — no three.js. */

export const EXERCISES = {
  wand_er: { id: 'wand_er', plane: 'horizontal', minTarget: 10, maxTarget: 90, defaultTarget: 45, prop: 'wand', continuous: false },
  er: { id: 'er', plane: 'horizontal', minTarget: 10, maxTarget: 90, defaultTarget: 40, prop: null, continuous: false },
  flexion: { id: 'flexion', plane: 'sagittal', minTarget: 20, maxTarget: 170, defaultTarget: 120, prop: null, continuous: false },
  abduction: { id: 'abduction', plane: 'frontal', minTarget: 20, maxTarget: 170, defaultTarget: 90, prop: null, continuous: false },
  wand_flexion: { id: 'wand_flexion', plane: 'sagittal', minTarget: 20, maxTarget: 165, defaultTarget: 110, prop: 'wand', continuous: false },
  wall_walk: { id: 'wall_walk', plane: 'sagittal', minTarget: 30, maxTarget: 125, defaultTarget: 100, prop: 'wall', continuous: false },
  pendulum: { id: 'pendulum', plane: 'none', minTarget: 0, maxTarget: 0, defaultTarget: 0, prop: 'table', continuous: true },
};

export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
export const clamp01 = (x) => clamp(x, 0, 1);
export const deg2rad = (d) => (d * Math.PI) / 180;
export const rad2deg = (r) => (r * 180) / Math.PI;
export const ease = (x) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(x));

export function clampTarget(id, target) {
  const s = EXERCISES[id];
  return s.continuous ? 0 : clamp(target, s.minTarget, s.maxTarget);
}

export function timingFor(target, speedDegPerS = 18, holdS = 5, restS = 1.5) {
  const moveS = Math.max(2.5, target / Math.max(1, speedDegPerS));
  return { moveS, holdS: Math.max(0, holdS), returnS: moveS, restS: Math.max(0, restS) };
}

export const cycleLength = (t) => t.moveS + t.holdS + t.returnS + t.restS;

export function cycleAt(elapsed, t) {
  const total = cycleLength(t);
  const rep = Math.floor(elapsed / total);
  let x = elapsed - rep * total;
  if (x < t.moveS) return { rep, phase: 'move', progress: ease(x / t.moveS), remaining: t.moveS - x };
  x -= t.moveS;
  if (x < t.holdS) return { rep, phase: 'hold', progress: 1, remaining: t.holdS - x };
  x -= t.holdS;
  if (x < t.returnS) return { rep, phase: 'return', progress: 1 - ease(x / t.returnS), remaining: t.returnS - x };
  x -= t.returnS;
  return { rep, phase: 'rest', progress: 0, remaining: t.restS - x };
}

export function staircase(p, steps) {
  const x = clamp01(p) * steps;
  const i = Math.floor(x);
  if (i >= steps) return 1;
  return (i + ease(x - i)) / steps;
}

export function elevationDir(elevationDeg, planeDeg, sideSign) {
  const e = deg2rad(clamp(elevationDeg, 0, 175));
  const p = deg2rad(planeDeg);
  return [sideSign * Math.sin(e) * Math.cos(p), -Math.cos(e), Math.sin(e) * Math.sin(p)];
}

export function sagittalIK(z, y, upper, fore) {
  const reach = upper + fore - 1e-4;
  const d = clamp(Math.hypot(z, y), Math.abs(upper - fore) + 1e-4, reach);
  const toTarget = Math.atan2(z, -y);
  const a = Math.acos(clamp((upper * upper + d * d - fore * fore) / (2 * upper * d), -1, 1));
  const g = Math.acos(clamp((upper * upper + fore * fore - d * d) / (2 * upper * fore), -1, 1));
  return { elevation: rad2deg(toTarget - a), elbowFlex: rad2deg(Math.PI - g) };
}

export function wallHeightFor(targetElevation, wallZ, upper, fore) {
  const maxH = Math.sqrt(Math.max(0, (upper + fore - 1e-3) ** 2 - wallZ * wallZ));
  let lo = -0.3;
  let hi = maxH;
  if (sagittalIK(wallZ, hi, upper, fore).elevation <= targetElevation) {
    return { height: hi, elevation: sagittalIK(wallZ, hi, upper, fore).elevation };
  }
  for (let i = 0; i < 40; i += 1) {
    const mid = (lo + hi) / 2;
    if (sagittalIK(wallZ, mid, upper, fore).elevation < targetElevation) lo = mid;
    else hi = mid;
  }
  return { height: (lo + hi) / 2, elevation: sagittalIK(wallZ, (lo + hi) / 2, upper, fore).elevation };
}
