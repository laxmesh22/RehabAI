"""Optional Jetson capture path. Missing hardware fails closed; it never becomes simulation."""
from edge.calibration import evaluate_calibration
from edge.camera.realsense_camera import RealSenseCamera
from edge.depth.projection import reconstruct


class LiveCapture:
    def __init__(self, model_path, pose_kind='mediapipe', device='cpu', deproject=None):
        self.camera = RealSenseCamera().start()
        self.deproject = deproject
        if self.deproject is None:
            self.deproject = self.camera.rs.rs2_deproject_pixel_to_point
        if pose_kind == 'mediapipe':
            from edge.pose.pose_estimator import MediaPipePoseEstimator
            self.estimator = MediaPipePoseEstimator(model_path)
        elif pose_kind == 'yolo':
            from edge.pose.yolo_estimator import UltralyticsPoseEstimator
            self.estimator = UltralyticsPoseEstimator(model_path, device=device)
        else:
            self.camera.close()
            raise ValueError('Unknown pose backend')
        self.version = self.estimator.model_version

    def snapshot(self):
        frame = self.camera.read()
        pose = self.estimator.predict(frame.rgb, max(1, int(frame.device_timestamp_ms)))
        points = reconstruct(pose, frame, self.deproject)
        landmarks = pose.landmarks
        distance = None
        if 'left_shoulder' in points and 'right_shoulder' in points:
            distance = (abs(points['left_shoulder'].xyz[2]) + abs(points['right_shoulder'].xyz[2])) / 2
        payload = {
            'people': pose.people,
            'points': points,
            'landmarks2d': landmarks,
            'confidence': min((item.confidence for item in points.values()), default=0),
            'source': 'live',
            'model_version': self.version,
            'timestamp': frame.timestamp,
            'distance': distance,
            'camera_ok': True,
            'depth_ok': bool(points),
            'simulation': False,
            'rgb': frame.rgb,
        }
        payload['calibration'] = evaluate_calibration(payload)
        return payload

    def close(self):
        self.estimator.close()
        self.camera.close()
