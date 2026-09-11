"""Neutral-pose checks before an assessment may start."""
import math


def evaluate_calibration(snapshot, min_confidence=.65, min_distance=.8, max_distance=3.2):
    required = ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip', 'left_elbow', 'right_elbow')
    points = snapshot.get('points') or {}
    people = snapshot.get('people', 0)
    simulation = bool(snapshot.get('simulation'))
    pose_ok = people == 1 and all(name in points and points[name].confidence >= min_confidence for name in required)
    distance = snapshot.get('distance')
    if distance is None and 'left_shoulder' in points:
        distance = abs(points['left_shoulder'].xyz[2])
    distance_ok = distance is not None and min_distance <= distance <= max_distance
    if simulation:
        camera_ok, depth_ok = False, False
    else:
        camera_ok = bool(snapshot.get('camera_ok', True)) and people >= 0
        depth_ok = bool(snapshot.get('depth_ok', True)) and pose_ok
    imu = snapshot.get('imu')
    imu_enabled = imu is not None
    imu_ok = bool(imu and imu.get('ok'))
    imu_simulation = bool(imu and imu.get('simulation'))
    imu_required = bool(snapshot.get('imu_required'))
    ready = pose_ok and distance_ok and (simulation or (camera_ok and depth_ok))
    if imu_required and not simulation:
        ready = ready and imu_ok
    return {
        'camera_ok': camera_ok,
        'depth_ok': depth_ok,
        'pose_ok': pose_ok,
        'distance_ok': bool(distance_ok),
        'imu_ok': imu_ok,
        'imu_enabled': imu_enabled,
        'imu_simulation': imu_simulation,
        'distance_m': None if distance is None else round(float(distance), 2),
        'people': people,
        'simulation': simulation,
        'ready': ready,
        'message': _message(simulation, camera_ok, depth_ok, pose_ok, distance_ok, people,
                            imu_ok, imu_enabled, imu_required, imu_simulation),
    }


def _message(simulation, camera_ok, depth_ok, pose_ok, distance_ok, people,
             imu_ok=False, imu_enabled=False, imu_required=False, imu_simulation=False):
    if simulation:
        if pose_ok and distance_ok:
            if imu_enabled and imu_ok:
                return 'Simulation ready. Synthetic RGB-D and arm IMU. These are not live sensors.'
            return 'Simulation ready. Synthetic skeleton is visible. This is not a live camera.'
        return 'Simulation is not yet showing a full upper body.'
    if not camera_ok:
        return 'Camera not ready.'
    if not depth_ok:
        return 'Depth is unavailable. Assessment cannot start.'
    if imu_required and not imu_ok:
        return 'Arm IMU is not streaming. Dual-sensor live mode cannot start.'
    if people != 1:
        return 'Exactly one person must be fully visible.'
    if not pose_ok:
        return 'Upper-body landmarks are incomplete or low-confidence.'
    if not distance_ok:
        return 'Stand on the marked distance marker and face the camera.'
    if imu_enabled and imu_ok and not imu_simulation:
        return 'Tracking quality is acceptable. RealSense and arm IMU are live and unvalidated.'
    return 'Tracking quality is acceptable. You may start the assessment.'


def smoothness(angles):
    if len(angles) < 4:
        return None
    jerks = [angles[i] - 3 * angles[i - 1] + 3 * angles[i - 2] - angles[i - 3] for i in range(3, len(angles))]
    rms = math.sqrt(sum(v * v for v in jerks) / len(jerks))
    return round(max(0.0, 100 - rms * 8), 1)
