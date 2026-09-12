from collections import deque

from edge.contracts import Joint3D


class SkeletonEMA:
    def __init__(self, alpha=.4, max_gap=.5):
        if not 0 < alpha <= 1:
            raise ValueError('alpha must be in (0, 1]')
        self.alpha, self.max_gap = alpha, max_gap
        self.previous = {}
        self.timestamp = None

    def reset(self):
        self.previous = {}
        self.timestamp = None

    def update(self, points, timestamp):
        if self.timestamp is not None:
            if timestamp <= self.timestamp:
                raise ValueError('Filter timestamps must increase')
            if timestamp-self.timestamp > self.max_gap:
                self.previous = {}
        result = {}
        for name, joint in points.items():
            previous = self.previous.get(name)
            xyz = tuple(self.alpha*v+(1-self.alpha)*p for v, p in zip(joint.xyz, previous.xyz)) if previous else joint.xyz
            result[name] = Joint3D(xyz, joint.confidence)
        # Missing joints are dropped immediately, not carried through occlusion.
        self.previous, self.timestamp = result, timestamp
        return result


class AngleMovingAverage:
    """Short window MA for phone ROM (vendor-style); does not invent angles."""

    def __init__(self, window: int = 5):
        if window < 1:
            raise ValueError('window must be >= 1')
        self.window = int(window)
        self._buf: deque[float] = deque(maxlen=self.window)

    def reset(self):
        self._buf.clear()

    def update(self, angle: float | None) -> float | None:
        if angle is None:
            return None
        self._buf.append(float(angle))
        return sum(self._buf) / len(self._buf)
