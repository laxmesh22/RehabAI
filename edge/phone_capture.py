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

_OPENCV_KINDS = frozenset({'opencv', 'shoulder_tracker'})


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
            kind = (pose_kind or POSE_KIND).strip().lower()
            if kind in _OPENCV_KINDS:
                from edge.pose.opencv_mediapipe_estimator import OpenCVMediaPipePoseEstimator
                self.estimator = OpenCVMediaPipePoseEstimator()
            else:
                path = Path(model_path or POSE_MODEL)
                if not path.is_file():
                    raise ValueError(
                        'Phone capture requires REHABAI_POSE_MODEL, REHABAI_PHONE_INFERENCE_URL, '
                        'or REHABAI_POSE_KIND=opencv'
                    )
                if kind == 'mediapipe':
                    from edge.pose.pose_estimator import MediaPipePoseEstimator
                    self.estimator = MediaPipePoseEstimator(str(path))
                elif kind == 'yolo':
                    from edge.pose.yolo_estimator import UltralyticsPoseEstimator
                    self.estimator = UltralyticsPoseEstimator(str(path), device=device or POSE_DEVICE)
                else:
                    raise ValueError('Unknown pose backend (use opencv, mediapipe, or yolo)')
            self.version = self.estimator.model_version
        self._t0 = time.monotonic()
        self._last_stamp = -1

    def snapshot_from_jpeg(self, jpeg_bytes: bytes, elapsed: float | None = None):
        t0 = time.perf_counter()
        rgb = _decode_jpeg_rgb(jpeg_bytes)
        ts = elapsed if elapsed is not None else (time.monotonic() - self._t0)
        stamp = max(self._last_stamp + 1, int(ts * 1000))
        self._last_stamp = stamp
        pose = self._hosted_pose(jpeg_bytes) if self.client else self.estimator.predict(rgb, stamp)
        self.version = pose.model_version
        points = _planar_points(pose.landmarks, rgb.shape[1], rgb.shape[0])
        framing_ok = _framing_ok(pose.landmarks)
        from edge.pose.opencv_mediapipe_estimator import landmark_quality
        quality = landmark_quality(pose.landmarks)
        inference_ms = getattr(self.estimator, 'last_inference_ms', None) if self.estimator else None
        if inference_ms is None and getattr(self, '_hosted_inference_ms', None) is not None:
            inference_ms = self._hosted_inference_ms
        total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        quality = {
            **quality,
            'inference_ms': inference_ms,
            'pipeline_ms': total_ms,
            'frame_w': int(rgb.shape[1]),
            'frame_h': int(rgb.shape[0]),
        }
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
            'pose_quality': quality,
            'sensors': {'camera': 'phone', 'depth': 'not_available', 'imu': 'off'},
        }

    def _hosted_pose(self, jpeg_bytes: bytes) -> PoseResult:
        t0 = time.perf_counter()
        try:
            response = self.client.post(
                self.inference_url + '/v1/pose', content=jpeg_bytes,
                headers={'Content-Type': 'image/jpeg'},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ValueError('Hosted phone pose inference is unavailable') from exc
        self._hosted_inference_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if payload.get('inference_ms') is not None:
            try:
                self._hosted_inference_ms = float(payload['inference_ms'])
            except (TypeError, ValueError):
                pass
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
    """True when phone RGB can be measured (never invents readiness)."""
    if PHONE_INFERENCE_URL:
        return _inference_url_ready(PHONE_INFERENCE_URL)
    if bool(POSE_MODEL) and Path(POSE_MODEL).is_file():
        return True
    kind = (POSE_KIND or '').strip().lower()
    if kind in _OPENCV_KINDS:
        from edge.pose.opencv_mediapipe_estimator import opencv_mediapipe_available
        return opencv_mediapipe_available()
    return False


def _inference_url_ready(url: str) -> bool:
    """Hosted / sidecar must report ready — a bare URL is not enough."""
    try:
        import httpx
        response = httpx.get(url.rstrip('/') + '/health', timeout=1.5)
        if response.status_code != 200:
            return False
        payload = response.json()
        if payload.get('opencv_pose_ready') is True:
            return True
        if payload.get('status') == 'ok' and payload.get('model_configured') is True:
            return True
        return payload.get('status') == 'ok' and bool(payload.get('pose_kind'))
    except Exception:
        return False


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


# Soft floor for planar embedding; framing/reps use a higher gate.
_PLANAR_MIN_CONF = 0.35
# Phone RGB is noisier than RGB-D; align with OpenCV min visibility (~0.4–0.55).
_FRAMING_MIN_CONF = 0.55


def _planar_points(landmarks2d, width: int = 1, height: int = 1):
    """Embed normalized 2D landmarks in a plane for deterministic 2D angle math."""
    from edge.contracts import Joint3D
    points = {}
    for name, lm in (landmarks2d or {}).items():
        conf = getattr(lm, 'confidence', 0) or 0
        if conf < _PLANAR_MIN_CONF:
            continue
        # Normalize both axes by image height so portrait/landscape aspect ratio
        # does not distort angles.
        x_n = (lm.x - 0.5) * (float(width) / max(1.0, float(height)))
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
        0.02 <= landmarks2d[name].x <= 0.98 and 0.02 <= landmarks2d[name].y <= 0.98
        and landmarks2d[name].confidence >= _FRAMING_MIN_CONF
        for name in required
    )
    shoulder_span = abs(landmarks2d['left_shoulder'].x - landmarks2d['right_shoulder'].x)
    torso_height = abs(
        (landmarks2d['left_hip'].y + landmarks2d['right_hip'].y) / 2
        - (landmarks2d['left_shoulder'].y + landmarks2d['right_shoulder'].y) / 2
    )
    return visible and 0.06 <= shoulder_span <= 0.85 and 0.06 <= torso_height <= 0.85
