"""Phone RGB pose adapter. It never invents depth or silently uses simulation."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from backend.config import (
    PHONE_FRAME_MAX_PIXELS, PHONE_INFERENCE_API_KEY, PHONE_INFERENCE_TIMEOUT_S,
    PHONE_INFERENCE_URL, POSE_DEVICE, POSE_KIND, POSE_MODEL,
)
from edge.contracts import Landmark2D, PoseResult


class PhonePoseEstimator:
    """Run phone frames through a local model or the configured hosted pose service."""

    def __init__(self, model_path: str | None = None, pose_kind: str | None = None, device: str | None = None):
        self.inference_url = PHONE_INFERENCE_URL
        self.client = None
        self.estimator = None
        if self.inference_url:
            import httpx
            headers = {'X-RehabAI-Inference-Key': PHONE_INFERENCE_API_KEY} if PHONE_INFERENCE_API_KEY else {}
            self.client = httpx.Client(timeout=PHONE_INFERENCE_TIMEOUT_S, headers=headers)
            self.version = 'hosted:pending'
        else:
            path = Path(model_path or POSE_MODEL)
            if not path.is_file():
                raise ValueError('Phone capture requires REHABAI_POSE_MODEL or REHABAI_PHONE_INFERENCE_URL')
            kind = (pose_kind or POSE_KIND).strip().lower()
            if kind == 'mediapipe':
                from edge.pose.pose_estimator import MediaPipePoseEstimator
                self.estimator = MediaPipePoseEstimator(str(path))
            elif kind == 'yolo':
                from edge.pose.yolo_estimator import UltralyticsPoseEstimator
                self.estimator = UltralyticsPoseEstimator(str(path), device=device or POSE_DEVICE)
            else:
                raise ValueError('Unknown pose backend')
            self.version = self.estimator.model_version
        self._t0 = time.monotonic()
        self._last_stamp = -1

    def snapshot_from_jpeg(self, jpeg_bytes: bytes, elapsed: float | None = None):
        rgb = _decode_jpeg_rgb(jpeg_bytes)
        ts = elapsed if elapsed is not None else (time.monotonic() - self._t0)
        stamp = max(self._last_stamp + 1, int(ts * 1000))
        self._last_stamp = stamp
        pose = self._hosted_pose(jpeg_bytes) if self.client else self.estimator.predict(rgb, stamp)
        self.version = pose.model_version
        points = _planar_points(pose.landmarks)
        framing_ok = _framing_ok(pose.landmarks)
        return {
            'people': pose.people,
            'points': points,
            'landmarks2d': pose.landmarks,
            'confidence': min((item.confidence for item in points.values()), default=0),
            'source': 'phone',
            'model_version': self.version,
            'timestamp': ts,
            'distance': None,
            'camera_ok': True,
            'depth_ok': False,
            'depth_required': False,
            'framing_ok': framing_ok,
            'simulation': False,
            'rgb': rgb,
            'capture_profile': 'phone_rgb_2d',
            'measurement_geometry': 'monocular_2d_projection',
            'sensors': {'camera': 'phone', 'depth': 'not_available', 'imu': 'off'},
        }

    def _hosted_pose(self, jpeg_bytes: bytes) -> PoseResult:
        try:
            response = self.client.post(
                self.inference_url + '/v1/pose', content=jpeg_bytes,
                headers={'Content-Type': 'image/jpeg'},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ValueError('Hosted phone pose inference is unavailable') from exc
        landmarks = {}
        for name, item in (payload.get('landmarks') or {}).items():
            try:
                landmarks[name] = Landmark2D(float(item['x']), float(item['y']), float(item['confidence']))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError('Hosted pose service returned an invalid landmark payload') from exc
        return PoseResult(
            landmarks=landmarks,
            people=int(payload.get('people', 0)),
            model_version=str(payload.get('model_version') or 'hosted:unknown'),
        )

    def close(self):
        for resource in (self.estimator, self.client):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass


def phone_pose_available() -> bool:
    return bool(PHONE_INFERENCE_URL) or (bool(POSE_MODEL) and Path(POSE_MODEL).is_file())


def _decode_jpeg_rgb(jpeg_bytes: bytes):
    if len(jpeg_bytes) < 4 or jpeg_bytes[:2] != b'\xff\xd8':
        raise ValueError('Phone frame is not a JPEG')
    try:
        import cv2
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError('Could not decode JPEG')
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    except ImportError:
        from PIL import Image
        import io
        try:
            image = Image.open(io.BytesIO(jpeg_bytes))
            image.verify()
            image = Image.open(io.BytesIO(jpeg_bytes)).convert('RGB')
            rgb = np.asarray(image)
        except Exception as exc:
            raise ValueError('Could not decode JPEG') from exc
    height, width = rgb.shape[:2]
    if width < 160 or height < 120 or width * height > PHONE_FRAME_MAX_PIXELS:
        raise ValueError('Phone frame dimensions are outside the allowed range')
    return rgb


def _planar_points(landmarks2d):
    """Embed normalized 2D landmarks in a plane for deterministic 2D angle math."""
    from edge.contracts import Joint3D
    points = {}
    for name, lm in (landmarks2d or {}).items():
        conf = getattr(lm, 'confidence', 0) or 0
        if conf < 0.35:
            continue
        x_n = lm.x - 0.5
        y_n = lm.y - 0.5
        points[name] = Joint3D(
            xyz=(x_n, y_n, 1.0),
            confidence=conf,
        )
    return points


def _framing_ok(landmarks2d) -> bool:
    required = ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip', 'left_elbow', 'right_elbow')
    if not all(name in landmarks2d for name in required):
        return False
    visible = all(
        0.03 <= landmarks2d[name].x <= 0.97 and 0.03 <= landmarks2d[name].y <= 0.97
        and landmarks2d[name].confidence >= 0.65
        for name in required
    )
    shoulder_span = abs(landmarks2d['left_shoulder'].x - landmarks2d['right_shoulder'].x)
    torso_height = abs(
        (landmarks2d['left_hip'].y + landmarks2d['right_hip'].y) / 2
        - (landmarks2d['left_shoulder'].y + landmarks2d['right_shoulder'].y) / 2
    )
    return visible and 0.08 <= shoulder_span <= 0.75 and 0.08 <= torso_height <= 0.75
