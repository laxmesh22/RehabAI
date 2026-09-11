// Screen shown right after each exercise in a guided session, before moving to the next one.
// Wraps PainSlider with the submit step and writes to exercise_sessions.pain_after (CLAUDE.md).
import { useState } from 'react'
import { PainSlider } from './PainSlider'

export interface ExercisePainCheckProps {
  exerciseLabel: string          // e.g. "Wand external rotation" -- shown in the heading
  painBefore?: number | null     // this session's pain_before, for the "worse than before" flag
  onSubmit: (painAfter: number) => void
  className?: string
}

export function ExercisePainCheck({ exerciseLabel, painBefore = null, onSubmit, className }: ExercisePainCheckProps) {
  const [value, setValue] = useState<number | null>(null)

  return (
    <div className={`exercise-pain-check${className ? ` ${className}` : ''}`}>
      <p className="exercise-pain-check__eyebrow">{exerciseLabel} done</p>
      <PainSlider
        id="exercise-pain-check"
        value={value}
        onChange={setValue}
        previousValue={painBefore}
        moment="after"
      />
      <button
        type="button"
        className="exercise-pain-check__continue"
        disabled={value === null}
        onClick={() => { if (value !== null) onSubmit(value) }}
      >
        Continue
      </button>
    </div>
  )
}
