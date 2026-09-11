"""Pose-model-independent named landmarks. MediaPipe Tasks is the first provider."""
import hashlib
from pathlib import Path
from typing import Protocol
from edge.contracts import Landmark2D, PoseResult

JOINTS = {'nose': 0, 'left_shoulder': 11, 'right_shoulder': 12, 'left_elbow': 13,
          'right_elbow': 14, 'left_wrist': 15, 'right_wrist': 16, 'left_hip': 23, 'right_hip': 24}


class PoseEstimator(Protocol):
    def predict(self, frame, timestamp_ms: int) -> PoseResult: ...
    def close(self) -> None: ...


class MediaPipePoseEstimator:
    def __init__(self, model_path, confidence=.65):
        path = Path(model_path)
        if not path.is_file():
            raise ValueError('Provide an existing MediaPipe pose_landmarker .task model file')
        import mediapipe as mp
        self.mp = mp
        options = mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_poses=2, min_pose_detection_confidence=confidence,
            min_pose_presence_confidence=confidence, min_tracking_confidence=confidence)
        self.detector = mp.tasks.vision.PoseLandmarker.create_from_options(options)
        self.model_version = 'mediapipe:'+path.name+':'+hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        self.last_timestamp = -1

    def predict(self, frame, timestamp_ms):
        if timestamp_ms <= self.last_timestamp:
            raise ValueError('Pose timestamps must increase')
        self.last_timestamp = timestamp_ms
        result = self.detector.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=frame), timestamp_ms)
        people = len(result.pose_landmarks)
        if people != 1:
            return PoseResult({}, people, self.model_version)
        landmarks = result.pose_landmarks[0]
        return PoseResult({name: Landmark2D(landmarks[i].x, landmarks[i].y,
                            min(landmarks[i].visibility, landmarks[i].presence))
                           for name, i in JOINTS.items()}, people, self.model_version)

    def close(self):
        self.detector.close()
