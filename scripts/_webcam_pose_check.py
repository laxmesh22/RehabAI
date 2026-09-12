"""Grab real webcam frames and run them through the OpenCV/MediaPipe pose stack.

Run with the Python 3.12 venv:  .venv\\Scripts\\python.exe scripts\\_webcam_pose_check.py
Proves the estimator sees a person on this machine. It says nothing about angle
accuracy — that needs validation against goniometry.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2

from edge.biomechanics.angles import calculate_abduction
from edge.phone_capture import _planar_points
from edge.pose.opencv_mediapipe_estimator import (
    OpenCVMediaPipePoseEstimator,
    opencv_mediapipe_available,
)


def main() -> int:
    if not opencv_mediapipe_available():
        print('FAIL: OpenCV MediaPipe not importable here')
        return 1
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print('SKIP: no webcam available (device busy or absent)')
        return 0
    estimator = OpenCVMediaPipePoseEstimator()
    seen = 0
    try:
        for i in range(40):
            ok, bgr = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            result = estimator.predict(rgb, i * 33)
            if result.people != 1:
                continue
            seen += 1
            if seen == 1:
                print('frame size:', bgr.shape[1], 'x', bgr.shape[0], '| model:', result.model_version)
                print('landmarks:', len(result.landmarks))
            points = _planar_points(result.landmarks)
            try:
                right = round(calculate_abduction(points, 'right'), 1)
                left = round(calculate_abduction(points, 'left'), 1)
            except (KeyError, ValueError) as exc:
                print('  person detected, angle unavailable:', exc)
                continue
            conf = round(min(p.confidence for p in result.landmarks.values()), 2)
            print(f'  person detected: right abduction {right}deg | left {left}deg | min conf {conf}')
            if seen >= 5:
                break
    finally:
        cap.release()
        estimator.close()
    if seen == 0:
        print('No person detected in 40 frames (stand in view of the camera to test this properly).')
        print('The stack ran without error, so the pipeline itself is wired up.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
