"""Efficient live OpenCV + MediaPipe shoulder detection with clear FPS + angles.

Demo window only — product ROM/reps still live in RehabAI VisionPipeline.
"""
from __future__ import annotations

import math
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor' / 'shoulder_tracker'
sys.path.insert(0, str(VENDOR))

try:
    import cv2
    import mediapipe as mp
except ModuleNotFoundError:
    sys.exit('Need .venv with opencv + mediapipe (Python 3.12)')

from shoulder_rom_tracker import (  # noqa: E402
    angle_from_vertical,
    get_landmarks,
    remap_laterality_for_mirror,
    shoulder_elevation_angle,
)

WIN = 'RehabAI · OpenCV MediaPipe Pose'
WIDTH, HEIGHT = 1280, 720  # match Shoulder Tracker demo for clearer angles
SMOOTH = 5
MODEL_COMPLEXITY = 1  # 0=fast, 1=balanced accuracy


def _ema(buf: deque, value: float) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return float('nan') if not buf else sum(buf) / len(buf)
    buf.append(float(value))
    return sum(buf) / len(buf)


def _fmt(v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return '—'
    return f'{v:5.1f}°'


def _pick_side(abd_l: float, abd_r: float) -> str:
    def score(v: float) -> float:
        return -1.0 if (v is None or math.isnan(v)) else v
    return 'left' if score(abd_l) >= score(abd_r) else 'right'


def _panel(img, x, y, w, h, alpha=0.55):
    overlay = img.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), (18, 18, 18), -1)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)
    cv2.rectangle(img, (x, y), (x + w, y + h), (70, 70, 70), 1)


def main() -> int:
    cam_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print(f'Could not open camera {cam_index}. Close Zoom/Teams/browser tabs.', flush=True)
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FPS, 30)

    pose = mp.solutions.pose.Pose(
        static_image_mode=False,
        model_complexity=MODEL_COMPLEXITY,
        smooth_landmarks=True,
        enable_segmentation=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    drawer = mp.solutions.drawing_utils
    styles = mp.solutions.drawing_styles

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 1100, 700)
    try:
        cv2.setWindowProperty(WIN, cv2.WND_PROP_TOPMOST, 1)
    except Exception:
        pass

    abd_l_buf, abd_r_buf = deque(maxlen=SMOOTH), deque(maxlen=SMOOTH)
    flx_l_buf, flx_r_buf = deque(maxlen=SMOOTH), deque(maxlen=SMOOTH)
    tilt_buf = deque(maxlen=SMOOTH)
    fps_ema = 0.0
    last = time.perf_counter()

    print('Live OpenCV + MediaPipe. Face camera. Raise arm sideways (abduction).', flush=True)
    print('Keys: Q quit · 0/1 camera index via CLI arg', flush=True)

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print('Camera frame dropped', flush=True)
                time.sleep(0.02)
                continue

            now = time.perf_counter()
            dt = max(1e-4, now - last)
            last = now
            inst_fps = 1.0 / dt
            fps_ema = inst_fps if fps_ema <= 1 else (0.85 * fps_ema + 0.15 * inst_fps)

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            result = pose.process(rgb)
            rgb.flags.writeable = True

            abd_l = abd_r = flx_l = flx_r = tilt = float('nan')
            side = '—'
            primary = float('nan')
            lms = None

            if result.pose_landmarks is not None:
                drawer.draw_landmarks(
                    frame,
                    result.pose_landmarks,
                    mp.solutions.pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=styles.get_default_pose_landmarks_style(),
                )
                lms = get_landmarks(result, w, h, min_visibility=0.5)
                if lms:
                    lms = remap_laterality_for_mirror(lms)
                    for side_name, abd_buf, flx_buf in (
                        ('left', abd_l_buf, flx_l_buf),
                        ('right', abd_r_buf, flx_r_buf),
                    ):
                        hip = lms.get(f'{side_name}_hip')
                        sh = lms.get(f'{side_name}_shoulder')
                        el = lms.get(f'{side_name}_elbow')
                        if hip and sh and el and min(hip.vis, sh.vis, el.vis) >= 0.5:
                            abd = shoulder_elevation_angle(hip, sh, el, 'coronal')
                            flx = shoulder_elevation_angle(hip, sh, el, 'sagittal')
                            if side_name == 'left':
                                abd_l = _ema(abd_buf, abd)
                                flx_l = _ema(flx_buf, flx)
                            else:
                                abd_r = _ema(abd_buf, abd)
                                flx_r = _ema(flx_buf, flx)
                        # highlight arm
                        sh_i = lms.get(f'{side_name}_shoulder_image')
                        el_i = lms.get(f'{side_name}_elbow_image')
                        wr_i = lms.get(f'{side_name}_wrist_image')
                        if sh_i and el_i and min(sh_i.vis, el_i.vis) >= 0.5:
                            cv2.line(frame, sh_i.pixel(), el_i.pixel(), (0, 220, 255), 5, cv2.LINE_AA)
                        if el_i and wr_i and min(el_i.vis, wr_i.vis) >= 0.5:
                            cv2.line(frame, el_i.pixel(), wr_i.pixel(), (0, 220, 255), 5, cv2.LINE_AA)

                    ls, rs = lms.get('left_shoulder'), lms.get('right_shoulder')
                    lh, rh = lms.get('left_hip'), lms.get('right_hip')
                    if ls and rs and lh and rh:
                        mid_sh_x = (ls.x + rs.x) / 2
                        mid_sh_y = (ls.y + rs.y) / 2
                        mid_hip_x = (lh.x + rh.x) / 2
                        mid_hip_y = (lh.y + rh.y) / 2
                        tilt = angle_from_vertical((mid_hip_x, mid_hip_y), (mid_sh_x, mid_sh_y))
                        tilt = _ema(tilt_buf, tilt)

                    side = _pick_side(abd_l, abd_r)
                    primary = abd_l if side == 'left' else abd_r

            # HUD
            mean_vis = 0.0
            joint_n = 0
            if result.pose_landmarks is not None and lms:
                vis_vals = [p.vis for p in lms.values() if hasattr(p, 'vis')]
                joint_n = len(vis_vals)
                mean_vis = (sum(vis_vals) / joint_n) if joint_n else 0.0
            _panel(frame, 12, 12, 460, 240)
            cv2.putText(frame, f'FPS  {fps_ema:5.1f}', (28, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (80, 255, 120), 2, cv2.LINE_AA)
            cv2.putText(frame, f'ABD L {_fmt(abd_l)}   R {_fmt(abd_r)}', (28, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (240, 240, 240), 2, cv2.LINE_AA)
            cv2.putText(frame, f'FLX L {_fmt(flx_l)}   R {_fmt(flx_r)}', (28, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (240, 240, 240), 2, cv2.LINE_AA)
            cv2.putText(frame, f'Torso tilt {_fmt(tilt)}', (28, 165), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (200, 200, 200), 2, cv2.LINE_AA)
            track = f'Track {side.upper()}  primary ABD {_fmt(primary)}' if side != '—' else 'NO PERSON — step into frame'
            color = (60, 220, 90) if side != '—' else (60, 60, 240)
            cv2.putText(frame, track, (28, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA)
            cv2.putText(frame, f'Quality joints {joint_n}  mean vis {mean_vis:.2f}  c{MODEL_COMPLEXITY}', (28, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 200, 220), 1, cv2.LINE_AA)
            cv2.putText(frame, 'OpenCV + MediaPipe · phone_rgb_2d style · not diagnosis · Q quit', (16, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (210, 210, 210), 1, cv2.LINE_AA)

            cv2.imshow(WIN, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord('q'), ord('Q')):
                break
    finally:
        pose.close()
        cap.release()
        cv2.destroyAllWindows()
        print('Closed.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
