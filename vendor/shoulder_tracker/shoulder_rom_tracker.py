#!/usr/bin/env python3
"""
Real-time shoulder range-of-motion (ROM) tracker for frozen-shoulder
(adhesive capsulitis) physiotherapy assessment.

Uses OpenCV + MediaPipe Pose. No GPU and no internet required at runtime
(the pose model ships inside the mediapipe wheel).

Requires Python 3.12 (MediaPipe has no wheels for 3.13/3.14).

Install / run from this project folder:
    .\\.venv\\Scripts\\python.exe shoulder_rom_tracker.py

Keys:
    Q / ESC  end session, save summary, quit
    S        save summary now (keep running)
    R        reset the current session counters
    A        track left / right / auto arm
    M        toggle abduction <-> flexion (rep-tracking angle)
    H        toggle mirrored (selfie) view
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import cv2
    import mediapipe as mp
except ModuleNotFoundError:
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    sys.exit(
        "Missing opencv-python / mediapipe in this interpreter "
        f"(Python {pyver}).\n"
        "MediaPipe does not support Python 3.13+.\n"
        "Use the project venv:\n"
        "  .\\.venv\\Scripts\\python.exe shoulder_rom_tracker.py\n"
        "Or in Cursor: select interpreter "
        ".venv\\Scripts\\python.exe  (Python 3.12)."
    )


# ---------------------------------------------------------------------------
# Tunable constants — change these during live testing
# ---------------------------------------------------------------------------

# Camera
CAMERA_INDEX = 0
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
MIRROR_VIEW = True  # selfie view; MediaPipe still labels anatomically

# Pose
MODEL_COMPLEXITY = 0  # 0=fast CPU, 1=balanced, 2=accurate (slower)
MIN_DETECTION_CONFIDENCE = 0.5
MIN_TRACKING_CONFIDENCE = 0.5
MIN_LANDMARK_VISIBILITY = 0.5

# Which movement drives rep counting / primary HUD number
# "abduction" = arm raise out to the side (face the camera)
# "flexion"   = forward raise (stand in profile, or use 3D sagittal estimate)
MOVEMENT_MODE = "abduction"

# Which arm to track: "left", "right", or "auto"
ARM_SIDE = "auto"
AUTO_ARM_HYSTERESIS_DEG = 12.0
AUTO_ARM_HOLD_FRAMES = 8

# Angle smoothing (simple moving average, in frames)
SMOOTHING_WINDOW = 5

# Clinical target used for "Raise higher" / "Good hold" feedback
TARGET_ANGLE_DEG = 90.0
HOLD_VELOCITY_DEG_PER_SEC = 12.0  # |dAngle/dt| below this counts as a hold
HOLD_MIN_FRAMES = 8

# Compensation: torso lean / shoulder hike while the arm is raised
COMPENSATION_TILT_THRESHOLD_DEG = 15.0
COMPENSATION_SHOULDER_HIKE_DEG = 15.0
COMPENSATION_ARM_ACTIVE_DEG = 30.0  # ignore lean while the arm is at rest

# Rep detection (hysteresis on the smoothed primary angle)
REP_LIFT_THRESHOLD_DEG = 35.0  # leave idle / start a raise
REP_MIN_PEAK_DEG = 45.0  # peak must reach this to count
REP_PEAK_DROP_DEG = 12.0  # fall this far from peak => lowering
REP_RESET_THRESHOLD_DEG = 28.0  # must drop here to complete the cycle

# Output
SESSION_OUTPUT_DIR = "session_output"


Point2 = Tuple[float, float]
Point3 = Tuple[float, float, float]
Point = Sequence[float]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def calculate_angle(a: Point, b: Point, c: Point) -> float:
    """Interior angle at point b formed by a-b-c, in degrees (2D or 3D)."""
    if a is None or b is None or c is None:
        return float("nan")

    dims = 3 if (len(a) >= 3 and len(b) >= 3 and len(c) >= 3) else 2
    v1 = [a[i] - b[i] for i in range(dims)]
    v2 = [c[i] - b[i] for i in range(dims)]
    n1 = math.sqrt(sum(v * v for v in v1))
    n2 = math.sqrt(sum(v * v for v in v2))
    if n1 < 1e-8 or n2 < 1e-8:
        return float("nan")
    dot = sum(p * q for p, q in zip(v1, v2))
    cosang = max(-1.0, min(1.0, dot / (n1 * n2)))
    return math.degrees(math.acos(cosang))


def angle_from_vertical(origin: Point, tip: Point) -> float:
    """
    Angle between origin→tip and upright vertical, in degrees.

    Image coordinates have y increasing downward, so up is (0, -1).
    MediaPipe world coordinates share that y convention, so the same
    up vector works for both.
    """
    if origin is None or tip is None:
        return float("nan")
    vx = tip[0] - origin[0]
    vy = tip[1] - origin[1]
    n = math.sqrt(vx * vx + vy * vy)
    if n < 1e-8:
        return float("nan")
    up_x, up_y = 0.0, -1.0
    cosang = max(-1.0, min(1.0, (vx * up_x + vy * up_y) / n))
    return math.degrees(math.acos(cosang))


def _valid(angle: float) -> bool:
    return angle == angle  # False for NaN


# ---------------------------------------------------------------------------
# Landmarks
# ---------------------------------------------------------------------------

_MP_POSE = mp.solutions.pose
_MP_DRAW = mp.solutions.drawing_utils
_MP_STYLES = mp.solutions.drawing_styles

_LM = _MP_POSE.PoseLandmark

# (name, mediapipe enum)
_LANDMARK_NAMES = {
    "left_shoulder": _LM.LEFT_SHOULDER,
    "right_shoulder": _LM.RIGHT_SHOULDER,
    "left_elbow": _LM.LEFT_ELBOW,
    "right_elbow": _LM.RIGHT_ELBOW,
    "left_wrist": _LM.LEFT_WRIST,
    "right_wrist": _LM.RIGHT_WRIST,
    "left_hip": _LM.LEFT_HIP,
    "right_hip": _LM.RIGHT_HIP,
    "left_index": _LM.LEFT_INDEX,
    "right_index": _LM.RIGHT_INDEX,
    "nose": _LM.NOSE,
}


@dataclass
class Landmark:
    x: float
    y: float
    z: float
    vis: float
    px: int
    py: int

    def xy(self) -> Point2:
        return (self.x, self.y)

    def xyz(self) -> Point3:
        return (self.x, self.y, self.z)

    def pixel(self) -> Tuple[int, int]:
        return (self.px, self.py)

    def sagittal(self) -> Point2:
        """(z, y) — sagittal / flexion plane from world-ish depth."""
        return (self.z, self.y)

    def coronal(self) -> Point2:
        """(x, y) — coronal / abduction plane."""
        return (self.x, self.y)


def _to_landmark(lm, w: int, h: int) -> Landmark:
    return Landmark(
        x=float(lm.x),
        y=float(lm.y),
        z=float(lm.z),
        vis=float(getattr(lm, "visibility", 1.0) or 0.0),
        px=int(lm.x * w),
        py=int(lm.y * h),
    )


def get_landmarks(
    pose_results,
    frame_width: int,
    frame_height: int,
    min_visibility: float = MIN_LANDMARK_VISIBILITY,
) -> Optional[Dict[str, Landmark]]:
    """
    Extract named body landmarks from a MediaPipe Pose result.

    Returns None if pose is missing. Individual points with low visibility
    are still returned (callers should check `.vis`).
    """
    if pose_results is None or pose_results.pose_landmarks is None:
        return None

    lms = pose_results.pose_landmarks.landmark
    world = None
    if pose_results.pose_world_landmarks is not None:
        world = pose_results.pose_world_landmarks.landmark

    out: Dict[str, Landmark] = {}
    for name, idx in _LANDMARK_NAMES.items():
        img_lm = lms[idx]
        item = _to_landmark(img_lm, frame_width, frame_height)
        # Prefer world x/y/z for 3D plane math when available; keep image
        # pixels and visibility from the 2D landmark.
        if world is not None:
            wlm = world[idx]
            item.x, item.y, item.z = float(wlm.x), float(wlm.y), float(wlm.z)
            # Recompute pixels from the image landmark, not world metres.
            item.px = int(img_lm.x * frame_width)
            item.py = int(img_lm.y * frame_height)
            # Stash image-normalized coords on the object for 2D overlay math
            # by packing them into a parallel Landmark under "*_image" keys.
        out[name] = item
        out[name + "_image"] = Landmark(
            x=float(img_lm.x),
            y=float(img_lm.y),
            z=float(img_lm.z),
            vis=item.vis,
            px=item.px,
            py=item.py,
        )
    return out


def remap_laterality_for_mirror(lms: Dict[str, Landmark]) -> Dict[str, Landmark]:
    """
    After a selfie-style horizontal flip, MediaPipe's left/right labels are
    swapped relative to the patient's body. Rename keys so 'right' still
    means the patient's right arm.
    """
    swapped: Dict[str, Landmark] = {}
    for key, value in lms.items():
        if key.startswith("left_"):
            swapped["right_" + key[5:]] = value
        elif key.startswith("right_"):
            swapped["left_" + key[6:]] = value
        else:
            swapped[key] = value
    return swapped


def _ok(lm: Optional[Landmark], min_vis: float = MIN_LANDMARK_VISIBILITY) -> bool:
    return lm is not None and lm.vis >= min_vis


# ---------------------------------------------------------------------------
# Shoulder angles
# ---------------------------------------------------------------------------

def shoulder_elevation_angle(
    hip: Landmark,
    shoulder: Landmark,
    elbow: Landmark,
    plane: str,
) -> float:
    """
    Clinical elevation angle at the shoulder:

        0°   = arm hanging along the torso
        90°  = arm out (abduction) or forward (flexion)
        180° = arm overhead

    Computed as the angle hip–shoulder–elbow in the requested plane.
    """
    if plane == "sagittal":
        a, b, c = hip.sagittal(), shoulder.sagittal(), elbow.sagittal()
    else:
        a, b, c = hip.coronal(), shoulder.coronal(), elbow.coronal()
    return calculate_angle(a, b, c)


def image_elevation_angle(
    hip_img: Landmark,
    shoulder_img: Landmark,
    elbow_img: Landmark,
) -> float:
    """Same elevation angle using 2D image coordinates (in-plane to camera)."""
    return calculate_angle(hip_img.xy(), shoulder_img.xy(), elbow_img.xy())


# ---------------------------------------------------------------------------
# Compensation
# ---------------------------------------------------------------------------

def detect_compensation(
    torso_tilt_deg: float,
    shoulder_hike_deg: float,
    arm_angle_deg: float,
    tilt_threshold: float = COMPENSATION_TILT_THRESHOLD_DEG,
    hike_threshold: float = COMPENSATION_SHOULDER_HIKE_DEG,
    arm_active_deg: float = COMPENSATION_ARM_ACTIVE_DEG,
) -> Tuple[bool, str]:
    """
    Flag compensatory trunk lean or shoulder shrug while the arm is raising.

    Returns (is_compensating, reason).
    """
    if not _valid(arm_angle_deg) or arm_angle_deg < arm_active_deg:
        return False, ""

    lean = _valid(torso_tilt_deg) and torso_tilt_deg > tilt_threshold
    hike = _valid(shoulder_hike_deg) and shoulder_hike_deg > hike_threshold

    if lean and hike:
        return True, "lean + shrug"
    if lean:
        return True, "torso lean"
    if hike:
        return True, "shoulder shrug"
    return False, ""


def _midpoint(a: Landmark, b: Landmark) -> Optional[Landmark]:
    if a is None or b is None:
        return None
    return Landmark(
        x=(a.x + b.x) / 2.0,
        y=(a.y + b.y) / 2.0,
        z=(a.z + b.z) / 2.0,
        vis=min(a.vis, b.vis),
        px=int((a.px + b.px) / 2),
        py=int((a.py + b.py) / 2),
    )


# ---------------------------------------------------------------------------
# Rep tracking
# ---------------------------------------------------------------------------

@dataclass
class RepRecord:
    index: int
    peak_angle: float
    movement: str
    arm: str
    compensated: bool
    duration_s: float


@dataclass
class RepState:
    phase: str = "idle"  # idle | raising | lowering
    count: int = 0
    peak_angle: float = 0.0
    raise_started_at: float = 0.0
    current_compensated: bool = False
    reps: List[RepRecord] = field(default_factory=list)
    last_completed_at: float = 0.0


def track_reps(
    state: RepState,
    angle: float,
    compensated: bool,
    movement: str,
    arm: str,
    now: float,
    lift_threshold: float = REP_LIFT_THRESHOLD_DEG,
    min_peak: float = REP_MIN_PEAK_DEG,
    peak_drop: float = REP_PEAK_DROP_DEG,
    reset_threshold: float = REP_RESET_THRESHOLD_DEG,
) -> RepState:
    """
    Detect a full raise-and-lower cycle via peak/trough hysteresis.

    idle     --(angle > lift)--> raising
    raising  --(angle < peak - drop)--> lowering
    lowering --(angle < reset AND peak >= min)--> idle + count
    """
    if not _valid(angle):
        return state

    if compensated:
        state.current_compensated = True

    if state.phase == "idle":
        if angle > lift_threshold:
            state.phase = "raising"
            state.peak_angle = angle
            state.raise_started_at = now
            state.current_compensated = compensated
        return state

    if state.phase == "raising":
        if angle > state.peak_angle:
            state.peak_angle = angle
        if angle < state.peak_angle - peak_drop and state.peak_angle >= min_peak:
            state.phase = "lowering"
        elif angle < reset_threshold:
            # False start — never reached a real peak
            state.phase = "idle"
            state.peak_angle = 0.0
            state.current_compensated = False
        return state

    if state.phase == "lowering":
        if angle > state.peak_angle:
            # Started going up again before finishing the lower
            state.peak_angle = angle
            state.phase = "raising"
            return state
        if angle < reset_threshold:
            if state.peak_angle >= min_peak:
                state.count += 1
                state.reps.append(
                    RepRecord(
                        index=state.count,
                        peak_angle=round(state.peak_angle, 1),
                        movement=movement,
                        arm=arm,
                        compensated=state.current_compensated,
                        duration_s=round(max(0.0, now - state.raise_started_at), 2),
                    )
                )
                state.last_completed_at = now
            state.phase = "idle"
            state.peak_angle = 0.0
            state.current_compensated = False
        return state

    return state


# ---------------------------------------------------------------------------
# Session I/O
# ---------------------------------------------------------------------------

def save_session(
    session: Dict,
    output_dir: str = SESSION_OUTPUT_DIR,
) -> Tuple[str, str]:
    """
    Write one session as a JSON file plus a CSV row.

    Returns (json_path, csv_path).
    """
    os.makedirs(output_dir, exist_ok=True)
    stamp = session.get("timestamp") or datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )
    json_path = os.path.join(output_dir, f"session_{stamp}.json")
    csv_path = os.path.join(output_dir, "sessions.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)

    row = {
        "timestamp": session.get("timestamp"),
        "started_at": session.get("started_at"),
        "duration_sec": session.get("duration_sec"),
        "movement_mode": session.get("movement_mode"),
        "arm_side": session.get("arm_side"),
        "rep_count": session.get("rep_count"),
        "max_angle": session.get("max_angle"),
        "average_peak_angle": session.get("average_peak_angle"),
        "max_angle_per_rep": json.dumps(session.get("max_angle_per_rep", [])),
        "compensation_events": session.get("compensation_events"),
        "compensation_frames": session.get("compensation_frames"),
        "target_angle_deg": session.get("target_angle_deg"),
    }
    write_header = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    return json_path, csv_path


def build_session_summary(
    started_at: datetime,
    ended_at: datetime,
    movement_mode: str,
    arm_side: str,
    rep_state: RepState,
    session_max_angle: float,
    compensation_events: int,
    compensation_frames: int,
) -> Dict:
    peaks = [r.peak_angle for r in rep_state.reps]
    avg = round(sum(peaks) / len(peaks), 1) if peaks else 0.0
    duration = (ended_at - started_at).total_seconds()
    stamp = ended_at.strftime("%Y%m%d_%H%M%S")
    return {
        "timestamp": stamp,
        "started_at": started_at.isoformat(timespec="seconds"),
        "ended_at": ended_at.isoformat(timespec="seconds"),
        "duration_sec": round(duration, 1),
        "movement_mode": movement_mode,
        "arm_side": arm_side,
        "rep_count": rep_state.count,
        "max_angle": round(session_max_angle, 1) if _valid(session_max_angle) else 0.0,
        "average_peak_angle": avg,
        "max_angle_per_rep": peaks,
        "reps": [
            {
                "index": r.index,
                "peak_angle": r.peak_angle,
                "movement": r.movement,
                "arm": r.arm,
                "compensated": r.compensated,
                "duration_s": r.duration_s,
            }
            for r in rep_state.reps
        ],
        "compensation_events": compensation_events,
        "compensation_frames": compensation_frames,
        "target_angle_deg": TARGET_ANGLE_DEG,
        "thresholds": {
            "compensation_tilt_deg": COMPENSATION_TILT_THRESHOLD_DEG,
            "compensation_shoulder_hike_deg": COMPENSATION_SHOULDER_HIKE_DEG,
            "rep_lift_threshold_deg": REP_LIFT_THRESHOLD_DEG,
            "rep_min_peak_deg": REP_MIN_PEAK_DEG,
            "smoothing_window": SMOOTHING_WINDOW,
        },
    }


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

_CYAN = (255, 220, 80)
_GREEN = (80, 220, 120)
_ORANGE = (40, 160, 255)
_RED = (60, 60, 255)
_WHITE = (245, 245, 245)
_DIM = (40, 40, 40)
_YELLOW = (0, 220, 255)


def _put(
    img,
    text: str,
    org: Tuple[int, int],
    color=_WHITE,
    scale: float = 0.7,
    thick: int = 2,
) -> None:
    cv2.putText(
        img,
        text,
        org,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        _DIM,
        thick + 2,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        text,
        org,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thick,
        cv2.LINE_AA,
    )


def _panel(img, x: int, y: int, w: int, h: int, alpha: float = 0.55) -> None:
    overlay = img.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)
    cv2.rectangle(img, (x, y), (x + w, y + h), (80, 80, 80), 1)


def _arm_points(lms: Dict[str, Landmark], side: str):
    s = lms.get(f"{side}_shoulder_image")
    e = lms.get(f"{side}_elbow_image")
    w = lms.get(f"{side}_wrist_image")
    h = lms.get(f"{side}_hip_image")
    return s, e, w, h


def draw_overlay(
    frame,
    pose_results,
    lms: Optional[Dict[str, Landmark]],
    arm_side: str,
    movement_mode: str,
    abduction_deg: float,
    flexion_deg: float,
    primary_deg: float,
    torso_tilt_deg: float,
    shoulder_hike_deg: float,
    compensating: bool,
    compensation_reason: str,
    feedback: str,
    rep_state: RepState,
    fps: float,
) -> None:
    """Draw pose skeleton, tracked-arm highlight, and HUD."""
    h, w = frame.shape[:2]

    if pose_results is not None and pose_results.pose_landmarks is not None:
        _MP_DRAW.draw_landmarks(
            frame,
            pose_results.pose_landmarks,
            _MP_POSE.POSE_CONNECTIONS,
            landmark_drawing_spec=_MP_STYLES.get_default_pose_landmarks_style(),
        )

    if lms is not None and arm_side in ("left", "right"):
        sh, el, wr, hip = _arm_points(lms, arm_side)
        color = _RED if compensating else _YELLOW
        if _ok(sh) and _ok(el):
            cv2.line(frame, sh.pixel(), el.pixel(), color, 6, cv2.LINE_AA)
        if _ok(el) and _ok(wr):
            cv2.line(frame, el.pixel(), wr.pixel(), color, 6, cv2.LINE_AA)
        if _ok(sh) and _ok(hip):
            cv2.line(frame, hip.pixel(), sh.pixel(), _WHITE, 3, cv2.LINE_AA)
            # Vertical reference through the hip (compensation visual)
            cv2.line(
                frame,
                (hip.px, hip.py),
                (hip.px, max(0, hip.py - 180)),
                (160, 160, 160),
                1,
                cv2.LINE_AA,
            )
        for pt in (sh, el, wr, hip):
            if _ok(pt):
                cv2.circle(frame, pt.pixel(), 8, color, -1, cv2.LINE_AA)

        # Live angle next to the shoulder joint
        if _ok(sh) and _valid(primary_deg):
            label = f"{primary_deg:.0f} deg"
            lx = min(w - 140, max(10, sh.px + 18))
            ly = max(28, sh.py - 18)
            _put(frame, label, (lx, ly), color, scale=0.9, thick=2)

    # HUD panels (drawn after skeleton so text stays readable)
    _panel(frame, 16, 16, 430, 210)
    title = "Shoulder ROM  |  Frozen Shoulder"
    _put(frame, title, (28, 46), _CYAN, 0.65, 2)

    mode_txt = f"Mode: {movement_mode.upper()}   Arm: {arm_side.upper()}"
    _put(frame, mode_txt, (28, 78), _WHITE, 0.55, 1)

    abd_txt = "--" if not _valid(abduction_deg) else f"{abduction_deg:5.1f}"
    flx_txt = "--" if not _valid(flexion_deg) else f"{flexion_deg:5.1f}"
    tilt_txt = "--" if not _valid(torso_tilt_deg) else f"{torso_tilt_deg:4.1f}"
    _put(frame, f"Abduction : {abd_txt} deg", (28, 110), _WHITE, 0.6, 2)
    _put(frame, f"Flexion   : {flx_txt} deg", (28, 140), _WHITE, 0.6, 2)
    tilt_color = _RED if compensating else _WHITE
    _put(frame, f"Torso tilt: {tilt_txt} deg", (28, 170), tilt_color, 0.6, 2)
    _put(frame, f"Reps: {rep_state.count}    Phase: {rep_state.phase}", (28, 200), _GREEN, 0.6, 2)

    # Target / session max on the right
    _panel(frame, w - 286, 16, 270, 110)
    peak_now = rep_state.peak_angle if rep_state.phase != "idle" else 0.0
    _put(frame, f"Target : {TARGET_ANGLE_DEG:.0f} deg", (w - 270, 48), _WHITE, 0.55, 1)
    _put(frame, f"Peak   : {peak_now:.0f} deg", (w - 270, 78), _CYAN, 0.55, 1)
    _put(frame, f"FPS    : {fps:.0f}", (w - 270, 108), _WHITE, 0.55, 1)

    # Feedback banner
    banner_color = _RED if compensating else (_GREEN if "Good" in feedback or "Nice" in feedback else _ORANGE)
    _panel(frame, 16, h - 78, w - 32, 58, alpha=0.65)
    _put(frame, feedback, (32, h - 40), banner_color, 0.85, 2)

    if compensating:
        reason = compensation_reason or "lean"
        _put(
            frame,
            f"COMPENSATION DETECTED ({reason})",
            (28, 248),
            _RED,
            0.7,
            2,
        )

    help_line = "Q quit+save   S save   R reset   A arm   M mode   H mirror"
    _put(frame, help_line, (16, h - 88), (180, 180, 180), 0.45, 1)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def live_feedback(
    primary_deg: float,
    compensating: bool,
    phase: str,
    hold_frames: int,
    just_completed_s: float,
    now: float,
) -> str:
    if compensating:
        return "Compensation detected — keep torso still"
    if not _valid(primary_deg):
        return "Stand in view  |  face camera (abd) or side-on (flex)"
    if phase == "idle":
        if 0 < now - just_completed_s < 1.6:
            return "Nice rep — repeat"
        return "Ready — raise your arm"
    if phase == "raising":
        if primary_deg >= TARGET_ANGLE_DEG and hold_frames >= HOLD_MIN_FRAMES:
            return "Good hold"
        if primary_deg >= TARGET_ANGLE_DEG:
            return "Good height — hold"
        remaining = TARGET_ANGLE_DEG - primary_deg
        if remaining > 8:
            return "Raise higher"
        return "Almost there — a little higher"
    if phase == "lowering":
        return "Lower with control"
    return "Ready — raise your arm"


# ---------------------------------------------------------------------------
# Arm selection
# ---------------------------------------------------------------------------

def choose_arm(
    left_angle: float,
    right_angle: float,
    current: str,
    leading: Dict[str, int],
) -> str:
    """Pick the moving arm. Hysteresis avoids flicker in AUTO mode."""
    l_ok = _valid(left_angle)
    r_ok = _valid(right_angle)
    if not l_ok and not r_ok:
        return current if current in ("left", "right") else "right"
    if l_ok and not r_ok:
        return "left"
    if r_ok and not l_ok:
        return "right"

    if left_angle > right_angle + AUTO_ARM_HYSTERESIS_DEG:
        leading["left"] = leading.get("left", 0) + 1
        leading["right"] = 0
    elif right_angle > left_angle + AUTO_ARM_HYSTERESIS_DEG:
        leading["right"] = leading.get("right", 0) + 1
        leading["left"] = 0
    else:
        leading["left"] = 0
        leading["right"] = 0
        return current if current in ("left", "right") else (
            "left" if left_angle >= right_angle else "right"
        )

    if leading.get("left", 0) >= AUTO_ARM_HOLD_FRAMES:
        return "left"
    if leading.get("right", 0) >= AUTO_ARM_HOLD_FRAMES:
        return "right"
    return current if current in ("left", "right") else (
        "left" if left_angle >= right_angle else "right"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _open_camera(index: int) -> cv2.VideoCapture:
    backends: List[int] = []
    if sys.platform.startswith("win"):
        backends.append(cv2.CAP_DSHOW)
    backends.extend([cv2.CAP_ANY, cv2.CAP_MSMF] if sys.platform.startswith("win") else [cv2.CAP_ANY])

    last = None
    tried = []
    for backend in backends:
        cap = cv2.VideoCapture(index, backend)
        last = cap
        tried.append(backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, 30)
            return cap
        cap.release()

    # Final fallback: default constructor
    cap = cv2.VideoCapture(index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        return cap
    raise RuntimeError(
        f"Could not open webcam (index={index}). Close other camera apps and retry."
    )


def _smooth(buf: Deque[float], value: float) -> float:
    if not _valid(value):
        return value
    buf.append(value)
    return sum(buf) / len(buf)


def _cycle_arm(current: str) -> str:
    order = ["auto", "right", "left"]
    try:
        return order[(order.index(current) + 1) % len(order)]
    except ValueError:
        return "auto"


def run(camera: int, arm_side: str, movement_mode: str, mirror: bool) -> None:
    cap = _open_camera(camera)

    pose = _MP_POSE.Pose(
        static_image_mode=False,
        model_complexity=MODEL_COMPLEXITY,
        smooth_landmarks=True,
        enable_segmentation=False,
        min_detection_confidence=MIN_DETECTION_CONFIDENCE,
        min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
    )

    abd_buf: Deque[float] = deque(maxlen=SMOOTHING_WINDOW)
    flx_buf: Deque[float] = deque(maxlen=SMOOTHING_WINDOW)
    tilt_buf: Deque[float] = deque(maxlen=SMOOTHING_WINDOW)

    rep_state = RepState()
    auto_leading: Dict[str, int] = {"left": 0, "right": 0}
    tracked_arm = "right" if arm_side == "auto" else arm_side

    started_at = datetime.now().astimezone()
    session_max = 0.0
    compensation_events = 0
    compensation_frames = 0
    was_compensating = False
    hold_frames = 0
    prev_primary = float("nan")
    prev_t = time.perf_counter()
    fps = 0.0

    win = "Shoulder ROM Tracker"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    print("Shoulder ROM tracker running. Press Q to save and quit.")
    print(f"  movement={movement_mode}  arm={arm_side}  mirror={mirror}")

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("Frame grab failed — stopping.")
                break

            if mirror:
                frame = cv2.flip(frame, 1)

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            results = pose.process(rgb)
            rgb.flags.writeable = True

            now = time.perf_counter()
            dt = max(1e-3, now - prev_t)
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else 1.0 / dt
            prev_t = now

            lms = get_landmarks(results, w, h)
            if lms is not None and mirror:
                lms = remap_laterality_for_mirror(lms)

            abduction = float("nan")
            flexion = float("nan")
            torso_tilt = float("nan")
            hike = float("nan")
            compensating = False
            reason = ""

            if lms is not None:
                # Per-arm 2D (camera-plane) elevation — used for AUTO and
                # as the abduction reading when facing the camera.
                left_2d = float("nan")
                right_2d = float("nan")
                if _ok(lms["left_shoulder_image"]) and _ok(lms["left_elbow_image"]) and _ok(lms["left_hip_image"]):
                    left_2d = image_elevation_angle(
                        lms["left_hip_image"],
                        lms["left_shoulder_image"],
                        lms["left_elbow_image"],
                    )
                if _ok(lms["right_shoulder_image"]) and _ok(lms["right_elbow_image"]) and _ok(lms["right_hip_image"]):
                    right_2d = image_elevation_angle(
                        lms["right_hip_image"],
                        lms["right_shoulder_image"],
                        lms["right_elbow_image"],
                    )

                if arm_side == "auto":
                    tracked_arm = choose_arm(left_2d, right_2d, tracked_arm, auto_leading)
                else:
                    tracked_arm = arm_side

                sh = lms.get(f"{tracked_arm}_shoulder")
                el = lms.get(f"{tracked_arm}_elbow")
                hip = lms.get(f"{tracked_arm}_hip")
                sh_i = lms.get(f"{tracked_arm}_shoulder_image")
                el_i = lms.get(f"{tracked_arm}_elbow_image")
                hip_i = lms.get(f"{tracked_arm}_hip_image")
                other = "left" if tracked_arm == "right" else "right"
                sh_other_i = lms.get(f"{other}_shoulder_image")

                # Abduction: coronal plane from world landmarks, fallback 2D
                if _ok(sh) and _ok(el) and _ok(hip):
                    abduction = shoulder_elevation_angle(hip, sh, el, "coronal")
                    flexion = shoulder_elevation_angle(hip, sh, el, "sagittal")
                if _ok(sh_i) and _ok(el_i) and _ok(hip_i):
                    img_ang = image_elevation_angle(hip_i, sh_i, el_i)
                    # Image-plane angle is the reliable abduction cue when
                    # the patient faces the camera; keep 3D sagittal for flexion.
                    if _valid(img_ang):
                        abduction = img_ang

                abduction = _smooth(abd_buf, abduction)
                flexion = _smooth(flx_buf, flexion)

                # Torso tilt: mid-hip → mid-shoulder vs vertical (image plane)
                mid_sh = _midpoint(lms.get("left_shoulder_image"), lms.get("right_shoulder_image"))
                mid_hip = _midpoint(lms.get("left_hip_image"), lms.get("right_hip_image"))
                if mid_sh is not None and mid_hip is not None:
                    torso_tilt = angle_from_vertical(mid_hip.xy(), mid_sh.xy())
                    torso_tilt = _smooth(tilt_buf, torso_tilt)

                # Shoulder hike: line between shoulders vs horizontal
                if _ok(sh_i) and _ok(sh_other_i):
                    dx = sh_i.x - sh_other_i.x
                    dy = sh_i.y - sh_other_i.y
                    n = math.sqrt(dx * dx + dy * dy)
                    if n > 1e-8:
                        # working shoulder higher than the other in image y
                        hike = abs(math.degrees(math.atan2(dy, dx)))
                        if hike > 90:
                            hike = 180 - hike

            primary = abduction if movement_mode == "abduction" else flexion
            if _valid(primary) and primary > session_max:
                session_max = primary

            velocity = 0.0
            if _valid(primary) and _valid(prev_primary):
                velocity = (primary - prev_primary) / dt
            prev_primary = primary

            if _valid(primary) and primary >= TARGET_ANGLE_DEG and abs(velocity) <= HOLD_VELOCITY_DEG_PER_SEC:
                hold_frames += 1
            else:
                hold_frames = 0

            compensating, reason = detect_compensation(
                torso_tilt,
                hike,
                primary if _valid(primary) else 0.0,
            )
            if compensating:
                compensation_frames += 1
                if not was_compensating:
                    compensation_events += 1
            was_compensating = compensating

            if _valid(primary):
                track_reps(
                    rep_state,
                    primary,
                    compensating,
                    movement_mode,
                    tracked_arm,
                    now,
                )

            feedback = live_feedback(
                primary,
                compensating,
                rep_state.phase,
                hold_frames,
                rep_state.last_completed_at,
                now,
            )

            draw_overlay(
                frame,
                results,
                lms,
                tracked_arm,
                movement_mode,
                abduction,
                flexion,
                primary,
                torso_tilt,
                hike,
                compensating,
                reason,
                feedback,
                rep_state,
                fps,
            )

            cv2.imshow(win, frame)
            key = cv2.waitKey(1) & 0xFF

            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (ord("s"), ord("S")):
                summary = build_session_summary(
                    started_at,
                    datetime.now().astimezone(),
                    movement_mode,
                    tracked_arm if arm_side == "auto" else arm_side,
                    rep_state,
                    session_max,
                    compensation_events,
                    compensation_frames,
                )
                jp, cp = save_session(summary)
                print(f"Saved session:\n  {jp}\n  {cp}")
            if key in (ord("r"), ord("R")):
                rep_state = RepState()
                session_max = 0.0
                compensation_events = 0
                compensation_frames = 0
                was_compensating = False
                hold_frames = 0
                started_at = datetime.now().astimezone()
                abd_buf.clear()
                flx_buf.clear()
                tilt_buf.clear()
                print("Session reset.")
            if key in (ord("a"), ord("A")):
                arm_side = _cycle_arm(arm_side)
                auto_leading = {"left": 0, "right": 0}
                print(f"Arm tracking: {arm_side}")
            if key in (ord("m"), ord("M")):
                movement_mode = "flexion" if movement_mode == "abduction" else "abduction"
                print(f"Movement mode: {movement_mode}")
            if key in (ord("h"), ord("H")):
                mirror = not mirror
                print(f"Mirror view: {mirror}")
    finally:
        ended_at = datetime.now().astimezone()
        summary = build_session_summary(
            started_at,
            ended_at,
            movement_mode,
            tracked_arm if arm_side == "auto" else arm_side,
            rep_state,
            session_max,
            compensation_events,
            compensation_frames,
        )
        json_path, csv_path = save_session(summary)
        print("\n===== SESSION SUMMARY =====")
        print(json.dumps(summary, indent=2))
        print(f"\nWrote:\n  {json_path}\n  {csv_path}")
        pose.close()
        cap.release()
        cv2.destroyAllWindows()


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Real-time shoulder ROM tracker")
    p.add_argument("--camera", type=int, default=CAMERA_INDEX)
    p.add_argument("--arm", choices=["left", "right", "auto"], default=ARM_SIDE)
    p.add_argument(
        "--mode",
        choices=["abduction", "flexion"],
        default=MOVEMENT_MODE,
        help="Primary angle used for reps and feedback",
    )
    p.add_argument("--no-mirror", action="store_true", help="Disable selfie-style mirror")
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    run(
        camera=args.camera,
        arm_side=args.arm,
        movement_mode=args.mode,
        mirror=MIRROR_VIEW and not args.no_mirror,
    )


if __name__ == "__main__":
    main()
