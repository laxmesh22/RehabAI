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

    def test_planar_points_preserve_frame_aspect_ratio(self):
        landmarks = {'right_elbow': Landmark2D(.75, .50, .95)}
        landscape = _planar_points(landmarks, width=1920, height=1080)['right_elbow'].xyz
        portrait = _planar_points(landmarks, width=1080, height=1920)['right_elbow'].xyz
        self.assertGreater(landscape[0], portrait[0])
        self.assertEqual(landscape[1], portrait[1])

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
        self.assertIn('data-capture="phone"', app)
        self.assertIn('startConsumerSession(selectedCapture', app)
        self.assertIn("['rehabai', state.token]", app)
        self.assertNotIn('?token=${encodeURIComponent(state.token)}', app)

    def test_opencv_kind_available_without_model_file(self):
        from edge import phone_capture

        with patch.object(phone_capture, 'PHONE_INFERENCE_URL', ''), \
             patch.object(phone_capture, 'POSE_MODEL', ''), \
             patch.object(phone_capture, 'POSE_KIND', 'opencv'), \
             patch('edge.pose.opencv_mediapipe_estimator.opencv_mediapipe_available', return_value=True):
            self.assertTrue(phone_capture.phone_pose_available())

        with patch.object(phone_capture, 'PHONE_INFERENCE_URL', ''), \
             patch.object(phone_capture, 'POSE_MODEL', ''), \
             patch.object(phone_capture, 'POSE_KIND', 'opencv'), \
             patch('edge.pose.opencv_mediapipe_estimator.opencv_mediapipe_available', return_value=False):
            self.assertFalse(phone_capture.phone_pose_available())

    def test_inference_url_requires_ready_health(self):
        from edge import phone_capture

        class FakeResp:
            status_code = 200

            def json(self):
                return {'status': 'ok', 'opencv_pose_ready': True}

        with patch.object(phone_capture, 'PHONE_INFERENCE_URL', 'http://127.0.0.1:8091'), \
             patch('httpx.get', return_value=FakeResp()):
            self.assertTrue(phone_capture.phone_pose_available())

        class DeadResp:
            status_code = 500

            def json(self):
                return {}

        with patch.object(phone_capture, 'PHONE_INFERENCE_URL', 'http://127.0.0.1:8091'), \
             patch('httpx.get', return_value=DeadResp()):
            self.assertFalse(phone_capture.phone_pose_available())

    def test_phone_estimator_builds_opencv_backend_without_model(self):
        from edge.contracts import PoseResult
        from edge.phone_capture import PhonePoseEstimator

        class FakeEstimator:
            model_version = 'mediapipe-solutions:pose:c1'
            last_inference_ms = 12.5

            def predict(self, frame, timestamp_ms):
                return PoseResult(upper_body(), 1, self.model_version)

            def close(self):
                pass

        with patch('edge.phone_capture.PHONE_INFERENCE_URL', ''), \
             patch('edge.phone_capture.POSE_KIND', 'opencv'), \
             patch(
                 'edge.pose.opencv_mediapipe_estimator.OpenCVMediaPipePoseEstimator',
                 return_value=FakeEstimator(),
             ):
            estimator = PhonePoseEstimator()
            snap = estimator.snapshot_from_jpeg(jpeg())
            estimator.close()
        self.assertEqual(snap['source'], 'phone')
        self.assertFalse(snap['simulation'])
        self.assertFalse(snap['depth_ok'])
        self.assertEqual(snap['people'], 1)
        self.assertIn('right_shoulder', snap['landmarks2d'])
        self.assertIn('pose_quality', snap)
        self.assertGreaterEqual(snap['pose_quality']['joint_count'], 6)
        self.assertEqual(snap['pose_quality']['inference_ms'], 12.5)
        self.assertTrue(snap['framing_ok'])

    def test_framing_accepts_moderate_phone_visibility(self):
        landmarks = upper_body(confidence=0.58)
        self.assertTrue(_framing_ok(landmarks))
        weak = upper_body(confidence=0.40)
        self.assertFalse(_framing_ok(weak))

    def test_landmark_quality_and_angle_ma(self):
        from edge.depth.filtering import AngleMovingAverage
        from edge.pose.opencv_mediapipe_estimator import landmark_quality

        q = landmark_quality(upper_body())
        self.assertEqual(q['joint_count'], 6)
        self.assertTrue(q['torso_ok'])
        ma = AngleMovingAverage(window=3)
        self.assertAlmostEqual(ma.update(10), 10)
        self.assertAlmostEqual(ma.update(20), 15)
        self.assertAlmostEqual(ma.update(30), 20)

    def test_phone_pipeline_uses_softer_tracking_gates(self):
        from edge.pipeline import VisionPipeline

        phone = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'phone')
        sim = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'simulation')
        self.assertEqual(phone.machine.confidence_limit, 0.55)
        self.assertEqual(sim.machine.confidence_limit, 0.65)
        self.assertIsNotNone(phone.angle_filter)
        self.assertIsNone(sim.angle_filter)


if __name__ == '__main__':
    unittest.main()
