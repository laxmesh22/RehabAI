"""Complementary fusion of camera ROM and arm-IMU rate. IMU never diagnoses."""

DISAGREE_DEG = 25.0
ALPHA = 0.85


def elevation_rate_dps(imu, camera_velocity=None):
    if not imu or not imu.get('ok'):
        return None
    components = (imu['gx'], imu['gy'], imu['gz'])
    rate = max(components, key=lambda v: abs(v))
    if camera_velocity is not None and abs(camera_velocity) > 8 and rate * camera_velocity < 0:
        rate = -rate
    return rate


class ComplementaryFusion:
    def __init__(self, alpha=ALPHA, disagree_deg=DISAGREE_DEG):
        self.alpha = alpha
        self.disagree_deg = disagree_deg
        self.fused = None

    def update(self, camera_angle, imu, dt, camera_velocity=None):
        rate = elevation_rate_dps(imu, camera_velocity)
        imu_ok = bool(imu and imu.get('ok') and rate is not None)
        if camera_angle is None:
            return {
                'fused_angle': None if not imu_ok else self.fused,
                'imu_rate': rate,
                'agree': True,
                'delta': None,
                'used_imu': False,
            }
        if self.fused is None or dt is None or dt <= 0 or not imu_ok:
            self.fused = camera_angle
            delta = None
            agree = True
        else:
            predicted = self.fused + rate * dt
            self.fused = self.alpha * predicted + (1.0 - self.alpha) * camera_angle
            delta = abs(self.fused - camera_angle)
            agree = delta <= self.disagree_deg
            if not agree:
                self.fused = camera_angle
        return {
            'fused_angle': None if self.fused is None else round(self.fused, 1),
            'imu_rate': None if rate is None else round(rate, 1),
            'agree': agree,
            'delta': None if delta is None else round(delta, 1),
            'used_imu': imu_ok,
        }
