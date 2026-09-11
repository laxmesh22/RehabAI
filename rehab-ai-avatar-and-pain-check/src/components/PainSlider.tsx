// The pain check: a row of tappable emoji buttons, 0 (no pain) to 10 (worst pain). Used after
// every exercise (pain_after) and before a session starts (pain_before) -- see the
// exercise_sessions fields in CLAUDE.md.
//
// Buttons, not a drag slider: a shoulder-pain patient may not have the fine motor control (or the
// free hand) to drag a thumb precisely, but tapping one big circle is easy on a phone or a clinic
// tablet either hand can reach. Colour bands the buttons into the same low/moderate/high
// irritability tiers the rules engine already uses to gate exercise intensity (CLAUDE.md,
// "Clinical logic") -- so a reading of 7+ being coral is that existing clinical threshold, not a
// new decorative rule for this screen. The one-off text alert below (.pain-slider__flag) is the
// true "functional alert" the design language reserves coral for; the button colour just carries
// the same, already-established boundary.
import { useRef } from 'react'
import { PAIN_MIN, PAIN_MAX, snapPain, labelForPain, tierForPain, emojiForPain, shouldFlag, flagMessage, type PainMoment } from './painScale'

export interface PainSliderProps {
  /** Current value, 0-10. null means the patient hasn't answered yet. */
  value: number | null
  onChange: (value: number) => void
  /** The other reading to compare against (pain_before when this is pain_after, or yesterday's reading). */
  previousValue?: number | null
  /** Which check this is -- changes the wording of the flag note. Default 'after'. */
  moment?: PainMoment
  /** Heading above the buttons. Defaults to a moment-appropriate question. */
  question?: string
  className?: string
  id?: string
}

const DEFAULT_QUESTION: Record<PainMoment, string> = {
  before: 'How much pain right now?',
  after: 'How much pain during that exercise?',
}

const SCALE = Array.from({ length: PAIN_MAX - PAIN_MIN + 1 }, (_, i) => PAIN_MIN + i)

export function PainSlider({ value, onChange, previousValue = null, moment = 'after', question, className, id }: PainSliderProps) {
  const groupRef = useRef<HTMLDivElement>(null)
  const answered = value !== null
  const qId = id ?? 'pain-slider'

  const select = (v: number) => onChange(snapPain(v))

  const onKeyDown = (e: React.KeyboardEvent, v: number) => {
    let next: number | null = null
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = Math.min(PAIN_MAX, v + 1)
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = Math.max(PAIN_MIN, v - 1)
    else if (e.key === 'Home') next = PAIN_MIN
    else if (e.key === 'End') next = PAIN_MAX
    else if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); select(v); return }
    else return
    e.preventDefault()
    select(next)
    // move focus to the newly-selected button
    const btn = groupRef.current?.querySelector<HTMLButtonElement>(`[data-pain-value="${next}"]`)
    btn?.focus()
  }

  const flagged = answered && shouldFlag(value, previousValue)

  return (
    <div className={`pain-slider${className ? ` ${className}` : ''}`}>
      <p className="pain-slider__question" id={`${qId}-label`}>{question ?? DEFAULT_QUESTION[moment]}</p>
      <div className="pain-slider__row" role="radiogroup" aria-labelledby={`${qId}-label`} ref={groupRef}>
        {SCALE.map((v) => {
          const selected = value === v
          // roving tabindex: the selected button is tabbable, or 0 if nothing is answered yet
          const tabbable = answered ? selected : v === PAIN_MIN
          return (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={`${v} out of 10, ${labelForPain(v)}`}
              data-pain-value={v}
              data-tier={tierForPain(v)}
              className={`pain-slider__btn${selected ? ' is-selected' : ''}`}
              tabIndex={tabbable ? 0 : -1}
              onClick={() => select(v)}
              onKeyDown={(e: React.KeyboardEvent) => onKeyDown(e, v)}
            >
              <span className="pain-slider__emoji" aria-hidden="true">{emojiForPain(v)}</span>
              <span className="pain-slider__num">{v}</span>
            </button>
          )
        })}
      </div>
      <p className="pain-slider__scale-ends">
        <span>No pain</span>
        <span>Worst pain</span>
      </p>
      <p className="pain-slider__value-label">
        {answered ? `${value} — ${labelForPain(value)}` : 'Tap a face to answer'}
      </p>
      {flagged && (
        <p className="pain-slider__flag" role="status">{flagMessage(moment)}</p>
      )}
    </div>
  )
}
