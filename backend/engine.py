"""Deterministic exercise analysis; independent of camera/model providers."""
import math
from dataclasses import dataclass, field


def angle_degrees(a, b):
    if len(a) != 3 or len(b) != 3 or not all(math.isfinite(v) for v in (*a, *b)):
        raise ValueError('Expected finite 3D vectors')
    denom = math.sqrt(sum(v*v for v in a) * sum(v*v for v in b))
    if denom < 1e-8:
        raise ValueError('Degenerate landmarks')
    return math.degrees(math.acos(max(-1, min(1, sum(x*y for x, y in zip(a, b))/denom))))


def arm_angle(shoulder, elbow, hip):
    if any(len(point) != 3 for point in (shoulder, elbow, hip)):
        raise ValueError('Expected 3D landmarks')
    return angle_degrees([e-s for e, s in zip(elbow, shoulder)],
                         [h-s for h, s in zip(hip, shoulder)])


@dataclass
class SessionEngine:
    target: float = 80
    goal: int = 5
    phase: str = 'reacquire'
    reps: int = 0
    peak: float = 0
    valid: int = 0
    total: int = 0
    last_timestamp: float = -1
    history: list = field(default_factory=list)

    def update(self, timestamp, angle, lean, confidence):
        if not all(math.isfinite(v) for v in (timestamp, angle, lean, confidence)):
            raise ValueError('Non-finite sample')
        if timestamp <= self.last_timestamp:
            raise ValueError('Samples must be in timestamp order')
        gap = self.last_timestamp >= 0 and timestamp-self.last_timestamp > 1.5
        self.last_timestamp = timestamp
        self.total += 1
        good = 0 <= angle <= 180 and 0 <= confidence <= 1 and confidence >= .65 and abs(lean) <= 15
        if not good or gap:
            # Discard partial repetition. Reacquire the lowered position first.
            self.phase = 'reacquire'
            message = 'Tracking interrupted. Lower your arm to reset.' if gap or confidence < .65 else 'Keep your trunk upright; lower your arm to reset.'
        else:
            self.valid += 1
            self.peak = max(self.peak, angle)
            if self.phase == 'reacquire':
                if angle <= 20:
                    self.phase = 'ready'
            elif self.phase == 'ready' and angle >= 30:
                self.phase = 'raising'
            elif self.phase == 'raising' and angle >= self.target:
                self.phase = 'target'
            elif self.phase == 'raising' and angle <= 20:
                self.phase = 'ready'
            elif self.phase == 'target' and angle < self.target-8:
                self.phase = 'lowering'
            elif self.phase == 'lowering' and angle <= 20:
                self.reps += 1
                self.phase = 'ready'
            message = {'ready': 'Ready for the next repetition.', 'raising': 'Raise slowly within your comfortable range.',
                       'target': 'Target reached. Lower slowly.', 'lowering': 'Return to the starting position.',
                       'reacquire': 'Lower your arm to reset tracking.'}[self.phase]
        row = dict(timestamp=round(timestamp, 3), angle=round(angle, 1), lean=round(lean, 1), confidence=round(confidence, 2), valid=good and not gap)
        self.history.append(row)
        return dict(**row, reps=self.reps, phase=self.phase, peak=round(self.peak, 1),
                    coverage=round(100*self.valid/self.total), feedback=message, complete=self.reps >= self.goal)
