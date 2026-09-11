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
    ready = pose_ok and distance_ok and (simulation or (camera_ok and depth_ok))
    return {
        'camera_ok': camera_ok,
        'depth_ok': depth_ok,
        'pose_ok': pose_ok,
        'distance_ok': bool(distance_ok),
        'distance_m': None if distance is None else round(float(distance), 2),
        'people': people,
        'simulation': simulation,
        'ready': ready,
        'message': _message(simulation, camera_ok, depth_ok, pose_ok, distance_ok, people),
    }


def _message(simulation, camera_ok, depth_ok, pose_ok, distance_ok, people):
    if simulation:
        if pose_ok and distance_ok:
            return 'Simulation ready. Synthetic skeleton is visible. This is not a live camera.'
        return 'Simulation is not yet showing a full upper body.'
    if not camera_ok:
        return 'Camera not ready.'
    if not depth_ok:
        return 'Depth is unavailable. Assessment cannot start.'
    if people != 1:
        return 'Exactly one person must be fully visible.'
    if not pose_ok:
        return 'Upper-body landmarks are incomplete or low-confidence.'
    if not distance_ok:
        return 'Stand on the marked distance marker and face the camera.'
    return 'Tracking quality is acceptable. You may start the assessment.'


def smoothness(angles):
    if len(angles) < 4:
        return None
    jerks = [angles[i] - 3 * angles[i - 1] + 3 * angles[i - 2] - angles[i - 3] for i in range(3, len(angles))]
    rms = math.sqrt(sum(v * v for v in jerks) / len(jerks))
    return round(max(0.0, 100 - rms * 8), 1)
