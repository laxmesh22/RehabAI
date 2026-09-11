"""Compact IMU quality features. Not a healthy-vs-FS classifier."""
import math
from collections import deque


class ImuWindow:
    def __init__(self, size=16):
        self.samples = deque(maxlen=size)

    def push(self, packet):
        if packet and packet.get('ok'):
            self.samples.append(packet)
        return self.summary()

    def summary(self):
        ok = [s for s in self.samples if s.get('ok')]
        if len(ok) < 3:
            return None
        acc = [s['accel_norm'] for s in ok]
        gyr = [s['gyro_norm'] for s in ok]
        return {
            'n': len(ok),
            'accel_mean': round(_mean(acc), 3),
            'accel_std': round(_std(acc), 3),
            'accel_range': round(max(acc) - min(acc), 3),
            'gyro_mean': round(_mean(gyr), 2),
            'gyro_std': round(_std(gyr), 2),
            'gyro_peak': round(max(gyr), 2),
            'smoothness': _smoothness(gyr),
        }


def _mean(values):
    return sum(values) / len(values)


def _std(values):
    mu = _mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / len(values))


def _smoothness(gyro):
    if len(gyro) < 4:
        return None
    jerks = [gyro[i] - 3 * gyro[i - 1] + 3 * gyro[i - 2] - gyro[i - 3] for i in range(3, len(gyro))]
    rms = math.sqrt(sum(v * v for v in jerks) / len(jerks))
    return round(max(0.0, 100 - rms * 0.4), 1)
