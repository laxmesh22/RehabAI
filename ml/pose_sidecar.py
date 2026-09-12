"""Stdlib OpenCV/MediaPipe pose sidecar for phone JPEG frames.

Runs on the Shoulder Tracker Python 3.12 venv (MediaPipe has no 3.13 wheels).
The hospital FastAPI process can stay on another interpreter and call this via
REHABAI_PHONE_INFERENCE_URL — hardware inference stays independent of the LLM.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge.phone_capture import _decode_jpeg_rgb  # noqa: E402
from edge.pose.opencv_mediapipe_estimator import (  # noqa: E402
    OpenCVMediaPipePoseEstimator,
    opencv_mediapipe_available,
)

_lock = threading.Lock()
_estimator: OpenCVMediaPipePoseEstimator | None = None
_timestamp_ms = 0
_max_bytes = int(os.environ.get('REHABAI_PHONE_FRAME_MAX_BYTES', str(4 * 1024 * 1024)))


def _get_estimator() -> OpenCVMediaPipePoseEstimator:
    global _estimator
    if _estimator is not None:
        return _estimator
    with _lock:
        if _estimator is None:
            _estimator = OpenCVMediaPipePoseEstimator()
        return _estimator


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write('pose-sidecar: ' + (fmt % args) + '\n')

    def _send(self, code: int, payload: dict | None = None, raw: bytes | None = None, content_type: str = 'application/json') -> None:
        body = raw if raw is not None else json.dumps(payload or {}).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split('?', 1)[0] not in ('/health', '/'):
            self._send(404, {'error': 'not_found'})
            return
        ready = opencv_mediapipe_available()
        self._send(200, {
            'status': 'ok' if ready else 'not_configured',
            'opencv_pose_ready': ready,
            'pose_kind': 'opencv',
            'model_configured': False,
            'device': os.environ.get('REHABAI_POSE_DEVICE', 'cpu'),
            'raw_frames_stored': False,
            'engine': 'stdlib-sidecar',
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split('?', 1)[0] != '/v1/pose':
            self._send(404, {'error': 'not_found'})
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > _max_bytes:
            self._send(413, {'error': 'JPEG is empty or exceeds the configured limit'})
            return
        frame = self.rfile.read(length)
        global _timestamp_ms
        try:
            import time as _time
            t0 = _time.perf_counter()
            rgb = _decode_jpeg_rgb(frame)
            with _lock:
                _timestamp_ms += 1
                estimator = _get_estimator()
                result = estimator.predict(rgb, _timestamp_ms)
                quality = dict(estimator.last_quality)
                inference_ms = estimator.last_inference_ms
            quality['inference_ms'] = inference_ms
            quality['pipeline_ms'] = round((_time.perf_counter() - t0) * 1000.0, 1)
        except ValueError as exc:
            self._send(400, {'error': str(exc)})
            return
        except Exception as exc:
            self._send(503, {'error': str(exc)})
            return
        self._send(200, {
            'people': result.people,
            'model_version': result.model_version,
            'inference_ms': inference_ms,
            'pose_quality': quality,
            'landmarks': {
                name: {'x': point.x, 'y': point.y, 'confidence': point.confidence}
                for name, point in result.landmarks.items()
            },
        })


def main() -> None:
    host = os.environ.get('REHABAI_POSE_SIDECAR_HOST', '127.0.0.1')
    port = int(os.environ.get('REHABAI_POSE_SIDECAR_PORT', '8091'))
    if not opencv_mediapipe_available():
        raise SystemExit('OpenCV MediaPipe not available in this interpreter')
    # Warm the model before accepting traffic so the first POST cannot wedge the lock.
    _get_estimator()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f'RehabAI OpenCV pose sidecar on http://{host}:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if _estimator is not None:
            _estimator.close()


if __name__ == '__main__':
    main()
