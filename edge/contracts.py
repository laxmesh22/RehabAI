from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Landmark2D:
    x: float  # normalized image coordinates
    y: float
    confidence: float


@dataclass(frozen=True)
class PoseResult:
    landmarks: dict[str, Landmark2D]
    people: int
    model_version: str


@dataclass(frozen=True)
class RGBDFrame:
    rgb: Any
    depth: Any
    intrinsics: Any
    timestamp: float  # monotonic host timestamp, seconds
    device_timestamp_ms: float
    frame_number: int


@dataclass(frozen=True)
class Joint3D:
    xyz: tuple[float, float, float]  # camera coordinates in metres
    confidence: float
