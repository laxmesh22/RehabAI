"""Shared measurement pipeline. Pose model, camera, and IMU are replaceable."""
from edge.biomechanics.angles import (
    calculate_abduction, calculate_elbow_bend, calculate_elevation,
    calculate_flexion, compensation,
)
from edge.calibration import evaluate_calibration
from edge.depth.filtering import SkeletonEMA
from edge.exercises.library import get_exercise
from edge.exercises.state_machine import ExerciseMachine
from edge.feedback.coach import coach_message
from edge.imu.features import ImuWindow
from edge.imu.fusion import ComplementaryFusion
from edge.overlay import jpeg_b64, overlay_spec, render_rgb
from edge.streaming.telemetry import build_telemetry
from agent.safety.engine import evaluate_safety

# Camera optical frame: X right, Y down, Z forward. Forward toward the camera is -Z.
CAMERA_VERTICAL_BASIS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0))

MOVEMENT_FN = {
    'abduction': calculate_abduction,
    'flexion': calculate_flexion,
    'elevation': calculate_elevation,
}


def features(points, side, movement, reference_basis):
    angle = MOVEMENT_FN[movement](points, side)
    if -12 <= angle < 0:
        angle = 0.0
    comps = compensation(points, reference_basis)
    elbow = None
    try:
        elbow = calculate_elbow_bend(points, side)
    except (KeyError, ValueError):
        pass
    needed = (side+'_shoulder', side+'_elbow', 'left_shoulder', 'right_shoulder', 'left_hip', 'right_hip')
    confidence = min(points[name].confidence for name in needed)
    return {
        'angle': angle,
        'torso_lean': abs(comps['torso_lean']),
        'lateral_lean': comps['lateral_lean'],
        'forward_lean': comps['forward_lean'],
        'elbow_bend': elbow,
        'confidence': confidence,
        'elevation': calculate_elevation(points, side),
    }


class VisionPipeline:
    def __init__(self, session_id, exercise_id, side, target, goal, source='simulation'):
        spec = get_exercise(exercise_id)
        if not spec['tracking_supported']:
            raise ValueError(spec['name']+' cannot be tracked by the current camera protocol')
        self.session_id, self.exercise_id, self.side = session_id, exercise_id, side
        self.source = source
        self.spec = spec
        self.machine = ExerciseMachine(target=target, goal=goal, rest_angle=spec['rest_angle'],
                                       raise_angle=spec['raise_angle'], lean_limit=spec['allowed_compensation'])
        self.filter = SkeletonEMA()
        self.reference = CAMERA_VERTICAL_BASIS
        self.previous_angle = None
        self.previous_time = None
        self.events = []
        self.fusion = ComplementaryFusion()
        self.imu_window = ImuWindow()
        self.last_safety = evaluate_safety({'pose_confidence': 1, 'torso_lean': 0})

    def calibrate_reference(self, points):
        from edge.biomechanics.angles import torso_basis
        self.reference = torso_basis(points)
        return self.reference

    def process_snapshot(self, snapshot):
        calibration = evaluate_calibration(snapshot)
        extra = {'calibration': calibration, 'source': snapshot.get('source', self.source),
                 'model_version': snapshot.get('model_version', 'unknown'), 'overlay': {'points': {}, 'bones': []},
                 'sensors': _sensor_labels(snapshot, self.source)}
        people = snapshot.get('people', 0)
        points = snapshot.get('points') or {}
        landmarks = snapshot.get('landmarks2d') or {}
        if people != 1 or not points:
            sample = self.machine.update(snapshot['timestamp'], 0, 0, 0)
            imu_extra, imu_safety = self._imu_state(snapshot, None, 0.0)
            extra.update(imu_extra)
            safety = evaluate_safety({'pose_confidence': 0, 'torso_lean': 0, 'people': people,
                                      'invalid_reps': self.machine.invalid_reps, **imu_safety})
            sample['feedback'] = coach_message(sample, safety)
            extra['frame_jpeg'] = jpeg_b64(render_rgb(landmarks, self.side, snapshot.get('source', self.source),
                                                     imu_source=_imu_source(snapshot)))
            self.last_safety = safety
            return build_telemetry(self.session_id, self.exercise_id, sample, safety, {}, extra)
        filtered = self.filter.update(points, snapshot['timestamp'])
        try:
            measured = features(filtered, self.side, self.spec['movement'], self.reference)
        except ValueError:
            sample = self.machine.update(snapshot['timestamp'], 0, 0, 0)
            imu_extra, imu_safety = self._imu_state(snapshot, None, 0.0)
            extra.update(imu_extra)
            safety = evaluate_safety({'pose_confidence': 0, 'torso_lean': 0, 'people': 1,
                                      'invalid_reps': self.machine.invalid_reps, **imu_safety})
            sample['feedback'] = coach_message(sample, safety)
            extra['overlay'] = overlay_spec(landmarks, self.side)
            extra['frame_jpeg'] = jpeg_b64(render_rgb(landmarks, self.side, snapshot.get('source', self.source),
                                                     imu_source=_imu_source(snapshot)))
            self.last_safety = safety
            return build_telemetry(self.session_id, self.exercise_id, sample, safety, landmarks, extra)
        velocity = 0.0
        dt = None
        if self.previous_angle is not None:
            dt = snapshot['timestamp'] - self.previous_time
            if dt and dt > 0:
                velocity = (measured['angle'] - self.previous_angle) / dt
        imu_extra, imu_safety = self._imu_state(snapshot, measured['angle'], velocity, dt)
        extra.update(imu_extra)
        self.previous_angle, self.previous_time = measured['angle'], snapshot['timestamp']
        compensation_flag = measured['torso_lean'] > self.spec['allowed_compensation']
        if compensation_flag:
            self.events.append({'type': 'torso_compensation', 'value': round(measured['torso_lean'], 1),
                                'threshold': self.spec['allowed_compensation'], 'timestamp': snapshot['timestamp']})
        sample = self.machine.update(snapshot['timestamp'], measured['angle'], measured['torso_lean'],
                                     measured['confidence'], compensation_flag)
        sample['velocity'] = round(velocity, 1)
        safety = evaluate_safety({
            'pose_confidence': measured['confidence'],
            'torso_lean': measured['torso_lean'],
            'people': 1,
            'invalid_reps': self.machine.invalid_reps,
            'camera_lost': False,
            'depth_unavailable': (not snapshot.get('simulation')) and snapshot.get('depth_ok') is False,
            **imu_safety,
        })
        sample['feedback'] = coach_message(sample, safety)
        extra.update(overlay=overlay_spec(landmarks, self.side),
                     lateral_lean=round(measured['lateral_lean'], 1),
                     forward_lean=round(measured['forward_lean'], 1),
                     elbow_bend=None if measured['elbow_bend'] is None else round(measured['elbow_bend'], 1),
                     elevation=round(measured['elevation'], 1),
                     velocity=round(velocity, 1),
                     events=self.events[-5:])
        extra['frame_jpeg'] = jpeg_b64(render_rgb(landmarks, self.side, snapshot.get('source', self.source),
                                                 sample.get('angle'), sample.get('feedback'),
                                                 imu_source=_imu_source(snapshot)))
        self.last_safety = safety
        return build_telemetry(self.session_id, self.exercise_id, sample, safety, landmarks, extra)

    def _imu_state(self, snapshot, camera_angle, velocity, dt=None):
        imu = snapshot.get('imu')
        fused = self.fusion.update(camera_angle, imu, dt, velocity)
        quality = self.imu_window.push(imu) if imu and imu.get('ok') else self.imu_window.summary()
        enabled = imu is not None
        extra = {
            'imu': {
                'enabled': enabled,
                'ok': bool(imu and imu.get('ok')),
                'lost': bool(imu and imu.get('lost')),
                'source': 'off' if imu is None else imu.get('source', 'off'),
                'placement': None if imu is None else imu.get('placement', 'arm'),
                'simulation': bool(imu and imu.get('simulation')),
                'gyro_norm': None if not imu else imu.get('gyro_norm'),
                'accel_norm': None if not imu else imu.get('accel_norm'),
                'rate': fused['imu_rate'],
                'fused_angle': fused['fused_angle'],
                'agree': fused['agree'],
                'delta': fused['delta'],
                'quality': quality,
            }
        }
        safety = {
            'imu_lost': bool(imu and not imu.get('ok')),
            'imu_required': bool(snapshot.get('imu_required')),
            'sensor_disagreement': bool(fused['used_imu'] and not fused['agree']),
        }
        return extra, safety


def _imu_source(snapshot):
    imu = snapshot.get('imu')
    if not imu:
        return None
    return imu.get('source')


def _sensor_labels(snapshot, fallback):
    camera = snapshot.get('source', fallback)
    imu = snapshot.get('imu')
    return {
        'camera': camera,
        'imu': 'off' if imu is None else imu.get('source', 'off'),
    }
