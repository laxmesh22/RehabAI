// Pure logic for the 0-10 pain check: a row of tappable emoji buttons, not a drag slider (drag
// controls are hard to hit precisely for someone with a stiff, painful shoulder -- a direct tap is
// the more accessible input here). No DOM/React here, so it can be unit-tested on its own. Matches
// the pain_before / pain_after fields in the data contract (CLAUDE.md, "Guided session") and the
// worsening rule in the rules engine ("pain up 2 points for three days" => alert; here we surface
// the same idea per-reading so the patient gets an immediate, honest signal instead of a silent
// number).

export type PainMoment = 'before' | 'after'

export interface PainAnchor {
  value: number      // 0-10
  label: string      // shown under the slider at this anchor
}

// Six anchors, same spacing and wording as the Wong-Baker style scale already used elsewhere in
// the plan (SPADI / pain-onset language): plain words, sentence case, no medical jargon.
export const PAIN_ANCHORS: PainAnchor[] = [
  { value: 0,  label: 'No pain' },
  { value: 2,  label: 'Mild' },
  { value: 4,  label: 'Moderate' },
  { value: 6,  label: 'Fairly severe' },
  { value: 8,  label: 'Severe' },
  { value: 10, label: 'Worst pain' },
]

export const PAIN_MIN = 0
export const PAIN_MAX = 10

export const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x))

/** Rounds to the nearest whole point on the 0-10 scale. Reports are integers, like the paper scale. */
export function snapPain(value: number): number {
  return clamp(Math.round(value), PAIN_MIN, PAIN_MAX)
}

/** Nearest labelled anchor for a given value, for the caption under the slider. */
export function labelForPain(value: number): string {
  const v = clamp(value, PAIN_MIN, PAIN_MAX)
  let best = PAIN_ANCHORS[0]
  let bestDist = Infinity
  for (const a of PAIN_ANCHORS) {
    const d = Math.abs(a.value - v)
    if (d < bestDist) { bestDist = d; best = a }
  }
  return best.label
}

export type PainTier = 'low' | 'moderate' | 'high'

/**
 * Which irritability tier a reading falls in. Same thresholds as the APTA/JOSPT tiers already
 * used to gate exercise intensity elsewhere in rehab.ai (CLAUDE.md, "Clinical logic"):
 * low <= 3, moderate 4-6, high >= 7. Reused here as the colour band for the button bar, so the
 * colour is encoding the same clinical threshold the rules engine already acts on -- not a new,
 * decorative rule invented for this screen.
 */
export function tierForPain(value: number): PainTier {
  const v = snapPain(value)
  if (v <= 3) return 'low'
  if (v <= 6) return 'moderate'
  return 'high'
}

// One emoji per point on the 0-10 scale. A fixed table (not generated), so the progression can be
// eyeballed and adjusted directly rather than reasoned about through a formula.
const PAIN_EMOJI: string[] = ['😄', '🙂', '🙂', '😐', '😐', '😕', '😕', '🙁', '😣', '😖', '😭']

export function emojiForPain(value: number): string {
  return PAIN_EMOJI[snapPain(value)]
}

/**
 * Whether this reading should raise a plain-language flag right on the check screen, instead of
 * only surfacing later in the physio queue. Two cases, both already in the rules engine:
 *  - the reading itself is severe (>= 8), or
 *  - it jumped by 2 or more points since the last reading we have for comparison
 *    (pain_before -> pain_after in the same exercise, or today's reading vs. the last one shown).
 * This never changes what gets stored or who gets alerted (the backend rules engine still owns
 * that); it only decides whether the patient sees an immediate, honest note on-screen.
 */
export function shouldFlag(current: number, previous?: number | null): boolean {
  const v = snapPain(current)
  if (v >= 8) return true
  if (previous == null) return false
  return v - snapPain(previous) >= 2
}

export function flagMessage(moment: PainMoment): string {
  return moment === 'after'
    ? 'That is more pain than before. Stop here for today and tell your physiotherapist.'
    : 'This is a lot of pain right now. Check the red-flag questions before starting.'
}
