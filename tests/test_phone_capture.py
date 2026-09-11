import io
import unittest
from unittest.mock import patch

from PIL import Image

from edge.calibration import evaluate_calibration
from edge.contracts import Landmark2D
from edge.phone_capture import _decode_jpeg_rgb, _framing_ok, _planar_points


def jpeg(width=320, height=240):
    out = io.BytesIO()
    Image.new('RGB', (width, height), (35, 70, 50)).save(out, format='JPEG')
    return out.getvalue()


def upper_body(confidence=0.95):
    return {
        'left_shoulder': Landmark2D(.38, .30, confidence),
        'right_shoulder': Landmark2D(.62, .30, confidence),
        'left_elbow': Landmark2D(.30, .48, confidence),
        'right_elbow': Landmark2D(.70, .48, confidence),
        'left_hip': Landmark2D(.42, .68, confidence),
        'right_hip': Landmark2D(.58, .68, confidence),
    }


class PhoneCaptureTests(unittest.TestCase):
    def test_phone_calibration_is_ready_without_invented_depth_or_distance(self):
        landmarks = upper_body()
        snapshot = {
            'people': 1,
            'points': _planar_points(landmarks),
            'simulation': False,
            'camera_ok': True,
            'depth_ok': False,
            'depth_required': False,
            'distance': None,
            'framing_ok': _framing_ok(landmarks),
            'capture_profile': 'phone_rgb_2d',
        }
        result = evaluate_calibration(snapshot)
        self.assertTrue(result['ready'])
        self.assertFalse(result['depth_ok'])
        self.assertFalse(result['depth_required'])
        self.assertIsNone(result['distance_m'])
        self.assertIn('2D-derived', result['message'])

    def test_realsense_profile_still_requires_depth(self):
        landmarks = upper_body()
        result = evaluate_calibration({
            'people': 1, 'points': _planar_points(landmarks), 'simulation': False,
            'camera_ok': True, 'depth_ok': False, 'distance': 1.6,
            'capture_profile': 'realsense_rgbd',
        })
        self.assertFalse(result['ready'])
        self.assertTrue(result['depth_required'])
        self.assertIn('Depth', result['message'])

    def test_jpeg_decode_rejects_invalid_and_oversized_dimensions(self):
        with self.assertRaisesRegex(ValueError, 'not a JPEG'):
            _decode_jpeg_rgb(b'not-a-jpeg')
        with patch('edge.phone_capture.PHONE_FRAME_MAX_PIXELS', 1000):
            with self.assertRaisesRegex(ValueError, 'dimensions'):
                _decode_jpeg_rgb(jpeg())

    def test_hosted_pose_service_auth_and_payload(self):
        from fastapi.testclient import TestClient
        from edge.contracts import PoseResult
        from ml import pose_service

        class Estimator:
            def predict(self, frame, timestamp_ms):
                return PoseResult(upper_body(), 1, 'test-model:v1')

        with patch.object(pose_service, 'PHONE_INFERENCE_API_KEY', 'service-secret'), \
             patch.object(pose_service, '_get_estimator', return_value=Estimator()):
            client = TestClient(pose_service.app)
            denied = client.post('/v1/pose', content=jpeg(), headers={'Content-Type': 'image/jpeg'})
            self.assertEqual(denied.status_code, 401)
            result = client.post('/v1/pose', content=jpeg(), headers={
                'Content-Type': 'image/jpeg', 'X-RehabAI-Inference-Key': 'service-secret',
            })
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload['people'], 1)
        self.assertEqual(payload['model_version'], 'test-model:v1')
        self.assertIn('right_shoulder', payload['landmarks'])

    def test_browser_contains_real_phone_capture_loop(self):
        from pathlib import Path
        app = Path(__file__).resolve().parents[1].joinpath('web', 'app.js').read_text(encoding='utf-8')
        self.assertIn('navigator.mediaDevices.getUserMedia', app)
        self.assertIn('/phone-frame', app)
        self.assertIn("startConsumerSession('phone')", app)
        self.assertIn("['rehabai', state.token]", app)
        self.assertNotIn('?token=${encodeURIComponent(state.token)}', app)


if __name__ == '__main__':
    unittest.main()
