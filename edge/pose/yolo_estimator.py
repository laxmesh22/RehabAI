"""Optional COCO-17 adapter. The rest of the pipeline only sees named joints."""
import hashlib
from pathlib import Path
from edge.contracts import Landmark2D, PoseResult

COCO = {'nose': 0, 'left_shoulder': 5, 'right_shoulder': 6, 'left_elbow': 7,
        'right_elbow': 8, 'left_wrist': 9, 'right_wrist': 10, 'left_hip': 11, 'right_hip': 12}


class UltralyticsPoseEstimator:
    def __init__(self, model_path, device='cpu', confidence=.65):
        path = Path(model_path)
        if not path.is_file():
            raise ValueError('Supply existing local COCO-17 pose weights; downloads are disabled')
        from ultralytics import YOLO
        self.model = YOLO(str(path), task='pose')
        self.device, self.confidence = device, confidence
        self.model_version = 'yolo:'+path.name+':'+hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    def predict(self, frame, timestamp_ms=0):
        result = self.model.predict(frame, device=self.device, verbose=False, conf=self.confidence)[0]
        if result.keypoints is None:
            return PoseResult({}, 0, self.model_version)
        people = len(result.keypoints.xy)
        if people != 1:
            return PoseResult({}, people, self.model_version)
        xy = result.keypoints.xy[0].cpu().tolist()
        scores = result.keypoints.conf[0].cpu().tolist()
        height, width = frame.shape[:2]
        landmarks = {}
        for name, index in COCO.items():
            if index >= len(xy):
                continue
            x, y = xy[index]
            landmarks[name] = Landmark2D(x/width, y/height, float(scores[index]))
        return PoseResult(landmarks, 1, self.model_version)

    def close(self):
        self.model = None
