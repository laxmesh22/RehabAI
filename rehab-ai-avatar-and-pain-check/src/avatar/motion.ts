// Pure motion maths for the follow-along model. No three.js here, so it can be unit-tested.

export type Side = 'left' | 'right'
export type Phase = 'move' | 'hold' | 'return' | 'rest'
export type Plane = 'sagittal' | 'frontal' | 'horizontal' | 'none'
export type ExerciseId =
  | 'wand_er' | 'er' | 'flexion' | 'abduction' | 'wand_flexion' | 'wall_walk' | 'pendulum'

export interface ExerciseSpec {
  id: ExerciseId
  plane: Plane               // plane the goniometer arc is drawn in
  minTarget: number          // degrees
  maxTarget: number          // degrees
  defaultTarget: number      // degrees
  prop: 'wand' | 'wall' | 'table' | null
  continuous: boolean        // pendulum swings continuously, no hold
}

export const EXERCISES: Record<ExerciseId, ExerciseSpec> = {
  wand_er:      { id: 'wand_er',      plane: 'horizontal', minTarget: 10, maxTarget: 90,  defaultTarget: 45,  prop: 'wand',  continuous: false },
  er:           { id: 'er',           plane: 'horizontal', minTarget: 10, maxTarget: 90,  defaultTarget: 40,  prop: null,    continuous: false },
  flexion:      { id: 'flexion',      plane: 'sagittal',   minTarget: 20, maxTarget: 170, defaultTarget: 120, prop: null,    continuous: false },
  abduction:    { id: 'abduction',    plane: 'frontal',    minTarget: 20, maxTarget: 170, defaultTarget: 90,  prop: null,    continuous: false },
  wand_flexion: { id: 'wand_flexion', plane: 'sagittal',   minTarget: 20, maxTarget: 165, defaultTarget: 110, prop: 'wand',  continuous: false },
  wall_walk:    { id: 'wall_walk',    plane: 'sagittal',   minTarget: 30, maxTarget: 125, defaultTarget: 100, prop: 'wall',  continuous: false },
  pendulum:     { id: 'pendulum',     plane: 'none',       minTarget: 0,  maxTarget: 0,   defaultTarget: 0,   prop: 'table', continuous: true },
}

export const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x))
export const clamp01 = (x: number) => clamp(x, 0, 1)
export const deg2rad = (d: number) => (d * Math.PI) / 180
export const rad2deg = (r: number) => (r * 180) / Math.PI

/** Slow in, slow out. */
export const ease = (x: number) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(x))

export function clampTarget(id: ExerciseId, target: number): number {
  const s = EXERCISES[id]
  return s.continuous ? 0 : clamp(target, s.minTarget, s.maxTarget)
}

export interface Timing { moveS: number; holdS: number; returnS: number; restS: number }

/** Movement speed is capped so the demo never moves faster than speedDegPerS on average. */
export function timingFor(target: number, speedDegPerS = 18, holdS = 5, restS = 1.5): Timing {
  const moveS = Math.max(2.5, target / Math.max(1, speedDegPerS))
  return { moveS, holdS: Math.max(0, holdS), returnS: moveS, restS: Math.max(0, restS) }
}

export const cycleLength = (t: Timing) => t.moveS + t.holdS + t.returnS + t.restS

/** Where we are inside one repetition. progress is 0 at rest, 1 at the target. */
export function cycleAt(elapsed: number, t: Timing): { rep: number; phase: Phase; progress: number; remaining: number } {
  const total = cycleLength(t)
  const rep = Math.floor(elapsed / total)
  let x = elapsed - rep * total
  if (x < t.moveS) return { rep, phase: 'move', progress: ease(x / t.moveS), remaining: t.moveS - x }
  x -= t.moveS
  if (x < t.holdS) return { rep, phase: 'hold', progress: 1, remaining: t.holdS - x }
  x -= t.holdS
  if (x < t.returnS) return { rep, phase: 'return', progress: 1 - ease(x / t.returnS), remaining: t.returnS - x }
  x -= t.returnS
  return { rep, phase: 'rest', progress: 0, remaining: t.restS - x }
}

/** Stepped rise used for the wall walk: the fingers climb in `steps` small moves. */
export function staircase(p: number, steps: number): number {
  const x = clamp01(p) * steps
  const i = Math.floor(x)
  if (i >= steps) return 1
  return (i + ease(x - i)) / steps
}

/**
 * Upper-arm direction in the trunk frame (x = model's left, y = up, z = forward).
 * elevation: 0 = hanging, 90 = horizontal, 180 = overhead.
 * plane: 0 = out to the side (abduction), 90 = straight forward (flexion).
 * sideSign: +1 for the model's left arm, -1 for its right arm.
 */
export function elevationDir(elevationDeg: number, planeDeg: number, sideSign: number): [number, number, number] {
  const e = deg2rad(clamp(elevationDeg, 0, 175))
  const p = deg2rad(planeDeg)
  return [sideSign * Math.sin(e) * Math.cos(p), -Math.cos(e), Math.sin(e) * Math.sin(p)]
}

/**
 * Two-bone IK in the sagittal plane, relative to the shoulder.
 * z = forward, y = up. Returns upper-arm elevation and elbow flexion in degrees.
 */
export function sagittalIK(z: number, y: number, upper: number, fore: number): { elevation: number; elbowFlex: number } {
  const reach = upper + fore - 1e-4
  const d = clamp(Math.hypot(z, y), Math.abs(upper - fore) + 1e-4, reach)
  const toTarget = Math.atan2(z, -y)                  // angle from straight down toward forward
  const a = Math.acos(clamp((upper * upper + d * d - fore * fore) / (2 * upper * d), -1, 1))
  const g = Math.acos(clamp((upper * upper + fore * fore - d * d) / (2 * upper * fore), -1, 1))
  return { elevation: rad2deg(toTarget - a), elbowFlex: rad2deg(Math.PI - g) }
}

/** Hand height (relative to the shoulder) on a wall at distance wallZ that gives this shoulder elevation. */
export function wallHeightFor(targetElevation: number, wallZ: number, upper: number, fore: number): { height: number; elevation: number } {
  const maxH = Math.sqrt(Math.max(0, (upper + fore - 1e-3) ** 2 - wallZ * wallZ))
  let lo = -0.3, hi = maxH
  if (sagittalIK(wallZ, hi, upper, fore).elevation <= targetElevation) {
    return { height: hi, elevation: sagittalIK(wallZ, hi, upper, fore).elevation }
  }
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2
    if (sagittalIK(wallZ, mid, upper, fore).elevation < targetElevation) lo = mid
    else hi = mid
  }
  return { height: (lo + hi) / 2, elevation: sagittalIK(wallZ, (lo + hi) / 2, upper, fore).elevation }
}
