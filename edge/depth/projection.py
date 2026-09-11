import math
import statistics
from edge.contracts import Joint3D


def reconstruct(pose, frame, deproject, threshold=.65):
    """Use measured aligned depth, never MediaPipe's inferred world coordinates."""
    points = {}
    if pose.people != 1:
        return points
    width, height = frame.intrinsics.width, frame.intrinsics.height
    for name, landmark in pose.landmarks.items():
        if not all(math.isfinite(v) for v in (landmark.x, landmark.y, landmark.confidence)):
            continue
        if not 0 <= landmark.x < 1 or not 0 <= landmark.y < 1 or not threshold <= landmark.confidence <= 1:
            continue
        x, y = round(landmark.x*(width-1)), round(landmark.y*(height-1))
        if not 1 <= x < width-1 or not 1 <= y < height-1:
            continue
        depths = [frame.depth.get_distance(x+dx, y+dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        depths = [z for z in depths if math.isfinite(z) and .25 <= z <= 5]
        if len(depths) < 5 or max(depths)-min(depths) > .15:
            continue
        xyz = tuple(deproject(frame.intrinsics, [x, y], statistics.median(depths)))
        if len(xyz) == 3 and all(math.isfinite(v) for v in xyz):
            points[name] = Joint3D(xyz, landmark.confidence)
    return points
