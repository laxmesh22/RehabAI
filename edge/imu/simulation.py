"""Synthetic arm IMU derived from the same kinematics as SimulatedPatient."""
import math
from edge.imu.packets import lost_packet, packet


class SimulatedArmImu:
    """Upper-arm IMU in a body frame. Always labelled simulation."""

    source = 'simulation'
    placement = 'arm'

    def __init__(self, placement='arm'):
        self.placement = placement
        self.prev_angle = None
        self.prev_time = None

    def read_from_angle(self, timestamp, angle_deg, side='right', drop=False):
        if drop:
            self.prev_angle, self.prev_time = angle_deg, timestamp
            return lost_packet('simulation', self.placement)
        dt = 0.1 if self.prev_time is None else max(1e-3, timestamp - self.prev_time)
        omega = 0.0 if self.prev_angle is None else (angle_deg - self.prev_angle) / dt
        self.prev_angle, self.prev_time = angle_deg, timestamp
        theta = math.radians(angle_deg)
        # Lateral upper-arm strap: hanging ≈ gravity along z; abduction tilts into x.
        sign = -1.0 if side == 'right' else 1.0
        ax = 9.81 * math.sin(theta) * sign
        ay = 0.15
        az = 9.81 * math.cos(theta)
        gx, gy, gz = 0.0, 0.0, omega
        return packet('simulation', self.placement, ax, ay, az, gx, gy, gz, timestamp)

    def close(self):
        return None
