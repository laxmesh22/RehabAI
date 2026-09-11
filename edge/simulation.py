"""Explicit synthetic kinematics. Never labelled as a live camera measurement."""
import math
from edge.contracts import Joint3D, Landmark2D


JOINT_NAMES = ('nose', 'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow',
               'left_wrist', 'right_wrist', 'left_hip', 'right_hip')


def _rot_z(point, degrees):
    rad = math.radians(degrees)
    x, y, z = point
    c, s = math.cos(rad), math.sin(rad)
    return (x * c - y * s, x * s + y * c, z)


def _rot_x(point, degrees):
    rad = math.radians(degrees)
    x, y, z = point
    c, s = math.cos(rad), math.sin(rad)
    return (x, y * c - z * s, y * s + z * c)


def project(xyz, fx=600, fy=600, cx=320, cy=240):
    x, y, z = xyz
    if z <= 1e-6:
        raise ValueError('Cannot project a point at or behind the camera')
    return x * fx / z + cx, y * fy / z + cy


class SimulatedPatient:
    """Upper-body 3D skeleton in RealSense-like camera coordinates (metres, Y down)."""

    source = 'simulation'
    model_version = 'synthetic-v1'

    def __init__(self, side='right', movement='abduction', distance=1.8):
        if side not in ('left', 'right') or movement not in ('abduction', 'flexion', 'elevation'):
            raise ValueError('Unsupported simulated protocol')
        self.side, self.movement, self.distance = side, movement, distance
        self.fault = 'none'
        self.angle = 0.0

    def set_fault(self, fault):
        if fault not in ('none', 'lean', 'occlusion', 'lost'):
            raise ValueError('Unknown simulation fault')
        self.fault = fault

    def set_angle(self, angle):
        self.angle = max(0.0, min(160.0, float(angle)))

    def skeleton(self, timestamp):
        if self.fault == 'lost':
            return {'people': 0, 'points': {}, 'landmarks2d': {}, 'confidence': 0.0,
                    'source': self.source, 'model_version': self.model_version, 'timestamp': timestamp}
        lean = 18.0 if self.fault == 'lean' else 1.5
        confidence = 0.2 if self.fault == 'occlusion' else 0.96
        z = self.distance
        ls, rs = (0.18, -0.15, z), (-0.18, -0.15, z)
        lh, rh = (0.12, 0.38, z), (-0.12, 0.38, z)
        nose = (0.0, -0.38, z)
        if lean:
            ls, rs, lh, rh, nose = [_rot_z(p, lean if self.side == 'right' else -lean) for p in (ls, rs, lh, rh, nose)]
        hanging = (0.0, 0.28, 0.0)
        if self.movement == 'abduction':
            outward = (-0.28, 0.0, 0.0) if self.side == 'right' else (0.28, 0.0, 0.0)
            offset = _blend(hanging, outward, self.angle / 90.0)
        elif self.movement == 'flexion':
            offset = _blend(hanging, (0.0, 0.0, -0.28), self.angle / 90.0)
        else:
            offset = _blend(hanging, (0.0, 0.0, -0.18), self.angle / 40.0)
        shoulder = rs if self.side == 'right' else ls
        elbow = tuple(s + o for s, o in zip(shoulder, offset))
        wrist_off = tuple(v * 0.85 for v in offset)
        wrist = tuple(e + w for e, w in zip(elbow, wrist_off))
        other_shoulder = ls if self.side == 'right' else rs
        other_elbow = tuple(s + h for s, h in zip(other_shoulder, hanging))
        other_wrist = tuple(e + h for e, h in zip(other_elbow, (0.0, 0.22, 0.0)))
        xyz = {
            'nose': nose, 'left_shoulder': ls, 'right_shoulder': rs,
            'left_hip': lh, 'right_hip': rh,
        }
        if self.side == 'right':
            xyz.update(right_elbow=elbow, right_wrist=wrist, left_elbow=other_elbow, left_wrist=other_wrist)
        else:
            xyz.update(left_elbow=elbow, left_wrist=wrist, right_elbow=other_elbow, right_wrist=other_wrist)
        points, landmarks = {}, {}
        for name in JOINT_NAMES:
            joint_conf = confidence if name.endswith(self.side) or name in ('nose', 'left_shoulder', 'right_shoulder', 'left_hip', 'right_hip') else min(confidence, 0.9)
            if self.fault == 'occlusion' and name.endswith('_elbow'):
                continue
            points[name] = Joint3D(xyz[name], joint_conf)
            u, v = project(xyz[name])
            landmarks[name] = Landmark2D(u / 640, v / 480, joint_conf)
        return {'people': 1, 'points': points, 'landmarks2d': landmarks, 'confidence': confidence,
                'source': self.source, 'model_version': self.model_version, 'timestamp': timestamp,
                'distance': abs(z), 'camera_ok': False, 'depth_ok': False, 'simulation': True}


def _blend(a, b, t):
    t = max(0.0, min(1.3, t))
    return tuple(x + (y - x) * t for x, y in zip(a, b))
