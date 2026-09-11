"""Explicit RealSense lifecycle; no webcam or synthetic fallback."""
import time
from edge.contracts import RGBDFrame


class CameraError(RuntimeError):
    pass


class RealSenseCamera:
    def __init__(self, width=640, height=480, fps=30, serial=None, timeout_ms=2000, sdk=None):
        if sdk is None:
            try:
                import pyrealsense2 as sdk
            except ImportError as exc:
                raise CameraError('Install the RealSense Python SDK for this device environment') from exc
        self.rs = sdk
        self.width, self.height, self.fps = width, height, fps
        self.serial, self.timeout_ms = serial, timeout_ms
        self.pipeline = None
        self.profile = None
        self.intrinsics = None
        self._last_number = None

    def start(self):
        if self.pipeline is not None:
            return self
        pipeline = self.rs.pipeline()
        config = self.rs.config()
        if self.serial:
            config.enable_device(self.serial)
        config.enable_stream(self.rs.stream.depth, self.width, self.height, self.rs.format.z16, self.fps)
        config.enable_stream(self.rs.stream.color, self.width, self.height, self.rs.format.rgb8, self.fps)
        try:
            self.profile = pipeline.start(config)
            self.align = self.rs.align(self.rs.stream.color)
        except Exception as exc:
            try:
                pipeline.stop()
            except Exception:
                pass
            raise CameraError('RealSense failed to start: '+str(exc)) from exc
        self.pipeline = pipeline
        self._last_number = None
        return self

    def read(self):
        if self.pipeline is None:
            raise CameraError('Camera is not running')
        try:
            import numpy as np
            frames = self.pipeline.wait_for_frames(self.timeout_ms)
            depth_raw, color_raw = frames.get_depth_frame(), frames.get_color_frame()
            if not depth_raw or not color_raw:
                raise CameraError('Synchronized RGB/depth pair is missing')
            if depth_raw.get_frame_timestamp_domain() != color_raw.get_frame_timestamp_domain():
                raise CameraError('RGB and depth timestamps have different clock domains')
            if abs(depth_raw.get_timestamp()-color_raw.get_timestamp()) > 1000/self.fps:
                raise CameraError('RGB and depth synchronization exceeded one frame interval')
            aligned = self.align.process(frames)
            depth, color = aligned.get_depth_frame(), aligned.get_color_frame()
            if not depth or not color:
                raise CameraError('Aligned depth or color frame is missing')
            number = color.get_frame_number()
            if self._last_number is not None and number <= self._last_number:
                raise CameraError('Repeated or out-of-order camera frame')
            self._last_number = number
            self.intrinsics = depth.profile.as_video_stream_profile().intrinsics
            return RGBDFrame(np.asanyarray(color.get_data()).copy(), depth, self.intrinsics,
                             time.monotonic(), color.get_timestamp(), number)
        except Exception as exc:
            self.close()
            if isinstance(exc, CameraError):
                raise
            raise CameraError('Camera disconnected or capture failed: '+str(exc)) from exc

    def close(self):
        pipeline, self.pipeline = self.pipeline, None
        self.profile = None
        self.intrinsics = None
        if pipeline is not None:
            try:
                pipeline.stop()
            except Exception:
                pass  # unplugged devices may also fail during cleanup

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
