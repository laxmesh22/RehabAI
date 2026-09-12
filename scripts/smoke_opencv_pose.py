"""Smoke-test Shoulder Tracker OpenCV+MediaPipe landmarks through RehabAI adapters."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('REHABAI_POSE_KIND', 'opencv')
os.environ['REHABAI_PHONE_INFERENCE_URL'] = ''  # force in-process for this smoke test


def main() -> int:
    import numpy as np

    from edge.phone_capture import PhonePoseEstimator, phone_pose_available
    from edge.pose.opencv_mediapipe_estimator import opencv_mediapipe_available

    print('opencv_mediapipe_available', opencv_mediapipe_available())
    print('phone_pose_available', phone_pose_available())
    if not opencv_mediapipe_available():
        print('FAIL: install Python 3.12 .venv with mediapipe (scripts/Ensure-OpenCVPose.ps1)')
        return 1

    # Synthetic RGB frame large enough for phone_capture limits.
    rgb = np.zeros((480, 640, 3), dtype=np.uint8)
    rgb[:] = (48, 52, 60)
    # crude person blob so MediaPipe may or may not detect — still validates the stack loads.
    rgb[80:400, 220:420] = (180, 160, 140)

    import cv2
    ok, buf = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    assert ok
    jpeg = buf.tobytes()

    est = PhonePoseEstimator()
    try:
        snap = est.snapshot_from_jpeg(jpeg)
    finally:
        est.close()

    print('source', snap.get('source'))
    print('model_version', snap.get('model_version'))
    print('capture_profile', snap.get('capture_profile'))
    print('people', snap.get('people'))
    print('landmarks', sorted((snap.get('landmarks2d') or {}).keys()))
    print('simulation', snap.get('simulation'))
    if snap.get('source') != 'phone' or snap.get('simulation'):
        print('FAIL: expected labelled phone (non-simulation) snapshot')
        return 1
    if 'mediapipe-solutions' not in str(snap.get('model_version') or ''):
        print('FAIL: expected mediapipe-solutions model_version')
        return 1
    print('OK: Shoulder Tracker OpenCV+MediaPipe path wired into PhonePoseEstimator')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
