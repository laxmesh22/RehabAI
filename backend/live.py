"""Optional RealSense + COCO-17 pose adapter. Hardware validation is still required."""
import math
from pathlib import Path
import statistics
import threading
import time
from backend.engine import arm_angle, angle_degrees


class RealSensePose:
    def __init__(self, model_path, device='cpu'):
        if not Path(model_path).is_file():
            raise ValueError('Supply an existing local pose model with --model; automatic downloads are disabled')
        import pyrealsense2 as rs
        import numpy as np
        from ultralytics import YOLO
        self.rs, self.np = rs, np
        self.model = YOLO(model_path, task='pose')
        self.device = device
        self.version = Path(model_path).name
        self.lock = threading.Lock()
        self.latest = None
        self.error = 'Waiting for camera frames'
        self.stopped = threading.Event()
        self.pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        self.pipeline.start(config)
        self.align = rs.align(rs.stream.color)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while not self.stopped.is_set():
                frames = self.align.process(self.pipeline.wait_for_frames(2000))
                depth, color = frames.get_depth_frame(), frames.get_color_frame()
                if not depth or not color:
                    continue
                captured = time.monotonic()
                result = self.model.predict(self.np.asanyarray(color.get_data()), device=self.device,
                                            verbose=False, conf=.5)[0]
                if result.keypoints is None or len(result.keypoints.xy) != 1:
                    with self.lock:
                        self.latest = None
                        self.error = 'Exactly one person must be visible'
                    continue
                xy = result.keypoints.xy[0].cpu().tolist()
                scores = result.keypoints.conf[0].cpu().tolist()
                if len(xy) != 17:
                    raise ValueError('Adapter requires a COCO-17 body pose model')
                intrinsics = depth.profile.as_video_stream_profile().intrinsics
                points = {}
                for index in (5, 6, 7, 8, 11, 12):
                    x, y = map(lambda v: int(round(v)), xy[index])
                    if scores[index] < .65 or not (2 <= x < 638 and 2 <= y < 478):
                        continue
                    distances = [depth.get_distance(x+dx, y+dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
                    distances = [v for v in distances if .25 < v < 5 and math.isfinite(v)]
                    if len(distances) < 5 or max(distances)-min(distances) > .2:
                        continue
                    points[index] = self.rs.rs2_deproject_pixel_to_point(intrinsics, [x, y], statistics.median(distances))
                with self.lock:
                    self.latest = (captured, points, scores)
                    self.error = ''
        except Exception as exc:
            with self.lock:
                self.latest = None
                self.error = 'Camera processing stopped: '+str(exc)
        finally:
            self.pipeline.stop()

    def sample(self, side):
        with self.lock:
            frame, error = self.latest, self.error
        if frame is None or time.monotonic()-frame[0] > 1:
            return dict(angle=0, lean=0, confidence=0, message=error or 'Camera frame is stale')
        captured, points, scores = frame
        shoulder, elbow = (5, 7) if side == 'left' else (6, 8)
        required = (5, 6, 11, 12, elbow)
        if not all(i in points for i in required):
            return dict(angle=0, lean=0, confidence=0, message='Shoulders, hips and selected elbow must be visible')
        shoulder_mid = [(a+b)/2 for a, b in zip(points[5], points[6])]
        hip_mid = [(a+b)/2 for a, b in zip(points[11], points[12])]
        torso = [h-s for h, s in zip(hip_mid, shoulder_mid)]
        # Camera must be level; camera +Y is the vertical reference in this prototype.
        virtual_hip = [s+t for s, t in zip(points[shoulder], torso)]
        try:
            angle = arm_angle(points[shoulder], points[elbow], virtual_hip)
            lean = angle_degrees(torso, [0, 1, 0])
        except ValueError:
            return dict(angle=0, lean=0, confidence=0, message='Invalid body geometry')
        return dict(angle=angle, lean=lean, confidence=min(scores[i] for i in required),
                    message='', captured=captured)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=5)
