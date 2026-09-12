"""OpenCV + MediaPipe Solutions pose (Shoulder Tracker stack).

Uses the classic ``mp.solutions.pose`` pipeline that ships its model inside the
mediapipe wheel — no ``.task`` / ``.pt`` file required. Landmark names match
``Landmark2D`` joints used by VisionPipeline.

Adapted from ``vendor/shoulder_tracker/shoulder_rom_tracker.py`` (OpenCV +
MediaPipe Pose). ROM / reps stay in the existing RehabAI pipeline; this module
only provides 2D landmarks. Requires Python ≤3.12 for current MediaPipe wheels.
"""
from __future__ import annotations

import os
import time

from edge.contracts import Landmark2D, PoseResult

# Same joint set as Tasks / YOLO adapters.
JOINT_ENUM_NAMES = (
    'nose',
    'left_shoulder',
    'right_shoulder',
    'left_elbow',
    'right_elbow',
    'left_wrist',
    'right_wrist',
    'left_hip',
    'right_hip',
)


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def opencv_mediapipe_available() -> bool:
    try:
        import cv2  # noqa: F401
        import mediapipe  # noqa: F401
        return True
    except ImportError:
        return False


def landmark_quality(landmarks: dict[str, Landmark2D]) -> dict:
    """Compact tracking-quality stats for telemetry (not a clinical score)."""
    if not landmarks:
        return {
            'joint_count': 0,
            'mean_visibility': 0.0,
            'min_visibility': 0.0,
            'torso_ok': False,
            'wrists_ok': False,
        }
    confs = [float(item.confidence) for item in landmarks.values()]
    torso = ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip')
    wrists = ('left_wrist', 'right_wrist')
    return {
        'joint_count': len(landmarks),
        'mean_visibility': round(sum(confs) / len(confs), 3),
        'min_visibility': round(min(confs), 3),
        'torso_ok': all(name in landmarks and landmarks[name].confidence >= 0.5 for name in torso),
        'wrists_ok': all(name in landmarks and landmarks[name].confidence >= 0.4 for name in wrists),
    }


class OpenCVMediaPipePoseEstimator:
    """CPU pose from MediaPipe Solutions + OpenCV JPEG decode path upstream."""

    def __init__(
        self,
        *,
        model_complexity: int | None = None,
        min_detection_confidence: float | None = None,
        min_tracking_confidence: float | None = None,
        min_landmark_visibility: float | None = None,
        max_side: int | None = None,
    ):
        if not opencv_mediapipe_available():
            raise RuntimeError(
                'OpenCV MediaPipe pose requires opencv-python(-headless) and mediapipe '
                '(Python 3.12 recommended; MediaPipe has no 3.13+ wheels)'
            )
        import mediapipe as mp

        complexity = (
            int(model_complexity)
            if model_complexity is not None
            else _env_int('REHABAI_OPENCV_POSE_COMPLEXITY', 1, 0, 2)
        )
        det = (
            float(min_detection_confidence)
            if min_detection_confidence is not None
            else _env_float('REHABAI_OPENCV_POSE_DET_CONF', 0.5, 0.1, 0.95)
        )
        track = (
            float(min_tracking_confidence)
            if min_tracking_confidence is not None
            else _env_float('REHABAI_OPENCV_POSE_TRACK_CONF', 0.5, 0.1, 0.95)
        )
        self._min_vis = (
            float(min_landmark_visibility)
            if min_landmark_visibility is not None
            else _env_float('REHABAI_OPENCV_POSE_MIN_VIS', 0.4, 0.1, 0.95)
        )
        # Downscale long side before MediaPipe when frames are large (CPU speed).
        self._max_side = (
            int(max_side)
            if max_side is not None
            else _env_int('REHABAI_OPENCV_POSE_MAX_SIDE', 960, 320, 1920)
        )

        self._mp = mp
        self._pose = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=complexity,
            smooth_landmarks=True,
            enable_segmentation=False,
            min_detection_confidence=det,
            min_tracking_confidence=track,
        )
        lm = mp.solutions.pose.PoseLandmark
        self._indices = {
            'nose': lm.NOSE,
            'left_shoulder': lm.LEFT_SHOULDER,
            'right_shoulder': lm.RIGHT_SHOULDER,
            'left_elbow': lm.LEFT_ELBOW,
            'right_elbow': lm.RIGHT_ELBOW,
            'left_wrist': lm.LEFT_WRIST,
            'right_wrist': lm.RIGHT_WRIST,
            'left_hip': lm.LEFT_HIP,
            'right_hip': lm.RIGHT_HIP,
        }
        self.model_version = f'mediapipe-solutions:pose:c{complexity}'
        self.last_inference_ms = 0.0
        self.last_quality: dict = landmark_quality({})

    def predict(self, frame, timestamp_ms: int) -> PoseResult:
        # Solutions Pose does not use VIDEO timestamps; keep signature for adapters.
        _ = timestamp_ms
        t0 = time.perf_counter()
        if frame is None or getattr(frame, 'ndim', 0) != 3:
            self.last_inference_ms = 0.0
            self.last_quality = landmark_quality({})
            return PoseResult({}, 0, self.model_version)

        rgb = frame if frame.flags['C_CONTIGUOUS'] else frame.copy()
        h, w = rgb.shape[:2]
        scale = 1.0
        if self._max_side > 0 and max(h, w) > self._max_side:
            scale = self._max_side / float(max(h, w))
            import cv2
            rgb = cv2.resize(
                rgb,
                (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        result = self._pose.process(rgb)
        self.last_inference_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if result.pose_landmarks is None:
            self.last_quality = landmark_quality({})
            return PoseResult({}, 0, self.model_version)

        landmarks = {}
        for name, enum in self._indices.items():
            lm = result.pose_landmarks.landmark[enum]
            conf = float(getattr(lm, 'visibility', 0.0) or 0.0)
            if conf < self._min_vis:
                continue
            landmarks[name] = Landmark2D(float(lm.x), float(lm.y), conf)
        self.last_quality = landmark_quality(landmarks)
        people = 1 if landmarks else 0
        return PoseResult(landmarks, people, self.model_version)

    def close(self) -> None:
        try:
            self._pose.close()
        except Exception:
            pass
