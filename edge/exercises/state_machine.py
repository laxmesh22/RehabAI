"""Rule-based repetition counter. Invalid tracking discards the partial rep."""
from dataclasses import dataclass, field


@dataclass
class RepEvent:
    rep: int
    peak_angle: float
    duration: float
    quality: str
    compensation_events: int


@dataclass
class ExerciseMachine:
    target: float = 80
    goal: int = 5
    rest_angle: float = 20
    raise_angle: float = 30
    hysteresis: float = 8
    lean_limit: float = 10
    confidence_limit: float = .65
    max_gap: float = 1.5
    phase: str = 'rest'
    reps: int = 0
    peak: float = 0
    cycle_peak: float = 0
    cycle_start: float = 0
    cycle_events: int = 0
    valid: int = 0
    total: int = 0
    invalid_reps: int = 0
    last_timestamp: float = -1
    completed_reps: list = field(default_factory=list)

    def update(self, timestamp, angle, lean, confidence, compensation=False):
        if timestamp <= self.last_timestamp:
            raise ValueError('Samples must be in timestamp order')
        gap = self.last_timestamp >= 0 and timestamp - self.last_timestamp > self.max_gap
        self.last_timestamp = timestamp
        self.total += 1
        tracking_ok = 0 <= angle <= 180 and 0 <= confidence <= 1 and confidence >= self.confidence_limit
        lean_ok = abs(lean) <= self.lean_limit
        if compensation:
            self.cycle_events += 1
        if not tracking_ok or gap:
            discarded = self.phase not in ('rest',)
            self.phase = 'rest'
            self.cycle_peak = 0
            self.invalid_reps += int(discarded)
            message = 'Please reposition yourself and repeat the movement.' if not tracking_ok else 'Tracking interrupted. Return to the start position.'
            valid = False
        elif not lean_ok:
            discarded = self.phase not in ('rest',)
            self.phase = 'rest'
            self.cycle_peak = 0
            self.invalid_reps += int(discarded)
            message = 'Torso compensation detected. Keep your body upright.'
            valid = False
        else:
            self.valid += 1
            valid = True
            self.peak = max(self.peak, angle)
            self.cycle_peak = max(self.cycle_peak, angle)
            if self.phase == 'rest':
                if angle <= self.rest_angle:
                    self.phase = 'ready'
                message = 'Lower your arm to the start position.' if self.phase == 'rest' else 'Ready for the next repetition.'
            elif self.phase == 'ready' and angle >= self.raise_angle:
                self.phase = 'ascending'
                self.cycle_start = timestamp
                self.cycle_peak = angle
                self.cycle_events = int(compensation)
                message = 'Raise your arm slowly.'
            elif self.phase == 'ready':
                message = 'Ready for the next repetition.'
            elif self.phase == 'ascending' and angle >= self.target:
                self.phase = 'peak'
                message = "Good. You reached today's target."
            elif self.phase == 'ascending' and angle <= self.rest_angle:
                self.phase = 'ready'
                self.invalid_reps += 1
                message = 'Movement incomplete. Start again from the lowered position.'
            elif self.phase == 'ascending':
                message = 'Raise your arm slowly.'
            elif self.phase == 'peak' and angle < self.target - self.hysteresis:
                self.phase = 'descending'
                message = 'Lower slowly to the start position.'
            elif self.phase == 'peak':
                message = "Good. You reached today's target."
            elif self.phase == 'descending' and angle <= self.rest_angle:
                self.reps += 1
                duration = timestamp - self.cycle_start
                quality = 'compensated' if self.cycle_events else 'accepted'
                self.completed_reps.append(RepEvent(self.reps, round(self.cycle_peak, 1), round(duration, 2), quality, self.cycle_events))
                self.phase = 'ready'
                self.cycle_peak = 0
                message = f'Rep {self.reps} of {self.goal} completed.'
            elif self.phase == 'descending':
                message = 'Lower slowly to the start position.'
            else:
                message = 'Follow the guided movement.'
        display_phase = {'ready': 'rest', 'rest': 'rest', 'ascending': 'ascending', 'peak': 'peak', 'descending': 'descending'}[self.phase]
        return {
            'timestamp': round(timestamp, 3),
            'angle': round(angle, 1),
            'lean': round(lean, 1),
            'confidence': round(confidence, 2),
            'valid': valid,
            'reps': self.reps,
            'invalid_reps': self.invalid_reps,
            'phase': display_phase,
            'internal_phase': self.phase,
            'peak': round(self.peak, 1),
            'cycle_peak': round(self.cycle_peak, 1),
            'coverage': round(100 * self.valid / self.total),
            'feedback': message,
            'complete': self.reps >= self.goal,
        }
