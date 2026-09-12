"""Minimal GPU/CPU pose inference service for phone JPEG frames.

Run separately from the hospital backend so model inference can live on the GPU
server without coupling RealSense/Jetson capture to the LLM service.
"""
from __future__ import annotations

import hmac
import threading

from fastapi import Body, FastAPI, Header, HTTPException

from backend.config import (
    PHONE_FRAME_MAX_BYTES, PHONE_INFERENCE_API_KEY, POSE_DEVICE, POSE_KIND, POSE_MODEL,
)
from edge.phone_capture import _decode_jpeg_rgb

app = FastAPI(title='RehabAI Pose Inference', version='0.1.0')
_lock = threading.Lock()
_infer_lock = threading.Lock()
_estimator = None
_timestamp_ms = 0


def _get_estimator():
    global _estimator
    if _estimator is not None:
        return _estimator
    with _lock:
        if _estimator is not None:
            return _estimator
        kind = POSE_KIND.strip().lower()
        if kind in ('opencv', 'shoulder_tracker'):
            from edge.pose.opencv_mediapipe_estimator import OpenCVMediaPipePoseEstimator
            _estimator = OpenCVMediaPipePoseEstimator()
        elif kind == 'mediapipe':
            if not POSE_MODEL:
                raise RuntimeError('REHABAI_POSE_MODEL is not configured')
            from edge.pose.pose_estimator import MediaPipePoseEstimator
            _estimator = MediaPipePoseEstimator(POSE_MODEL)
        elif kind == 'yolo':
            if not POSE_MODEL:
                raise RuntimeError('REHABAI_POSE_MODEL is not configured')
            from edge.pose.yolo_estimator import UltralyticsPoseEstimator
            _estimator = UltralyticsPoseEstimator(POSE_MODEL, device=POSE_DEVICE)
        else:
            raise RuntimeError('REHABAI_POSE_KIND must be opencv, mediapipe, or yolo')
        return _estimator


def _authorise(value: str | None):
    if PHONE_INFERENCE_API_KEY and not hmac.compare_digest(value or '', PHONE_INFERENCE_API_KEY):
        raise HTTPException(401, 'Invalid inference service key')


@app.get('/health')
def health():
    kind = POSE_KIND.strip().lower()
    opencv_ready = False
    if kind in ('opencv', 'shoulder_tracker'):
        from edge.pose.opencv_mediapipe_estimator import opencv_mediapipe_available
        opencv_ready = opencv_mediapipe_available()
    configured = bool(POSE_MODEL) or opencv_ready
    return {
        'status': 'ok' if configured else 'not_configured',
        'model_configured': bool(POSE_MODEL),
        'opencv_pose_ready': opencv_ready,
        'pose_kind': POSE_KIND,
        'device': POSE_DEVICE,
        'raw_frames_stored': False,
    }


@app.post('/v1/pose')
def infer_pose(
    frame: bytes = Body(media_type='image/jpeg'),
    x_rehabai_inference_key: str | None = Header(default=None),
):
    _authorise(x_rehabai_inference_key)
    if not frame or len(frame) > PHONE_FRAME_MAX_BYTES:
        raise HTTPException(413, 'JPEG is empty or exceeds the configured limit')
    global _timestamp_ms
    try:
        rgb = _decode_jpeg_rgb(frame)
        # MediaPipe video mode requires strictly increasing timestamps and model
        # implementations are not assumed to be thread-safe.
        with _infer_lock:
            _timestamp_ms += 1
            result = _get_estimator().predict(rgb, _timestamp_ms)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        'people': result.people,
        'model_version': result.model_version,
        'landmarks': {
            name: {'x': point.x, 'y': point.y, 'confidence': point.confidence}
            for name, point in result.landmarks.items()
        },
    }
