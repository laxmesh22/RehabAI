"""Baseline assessment between Talk and the first rehab session.

These are geometry and wiring tests. They say nothing about how accurate a phone
camera is on a real shoulder — that needs separate validation against goniometry.
"""
import unittest

from backend.services.persist import is_valid_rom
from edge.biomechanics.angles import calculate_flexion, calculate_sagittal_elevation
from edge.calibration import evaluate_calibration
from edge.contracts import Landmark2D
from edge.exercises.library import BASELINE_EXERCISES, get_exercise, phone_trackable
from edge.phone_capture import _framing_ok, _planar_points, _sagittal_framing_ok


def facing_camera(confidence=0.95):
    return {
        'left_shoulder': Landmark2D(.38, .30, confidence),
        'right_shoulder': Landmark2D(.62, .30, confidence),
        'left_elbow': Landmark2D(.30, .48, confidence),
        'right_elbow': Landmark2D(.70, .48, confidence),
        'left_hip': Landmark2D(.42, .68, confidence),
        'right_hip': Landmark2D(.58, .68, confidence),
    }


def side_on(elbow_x=.50, elbow_y=.48, confidence=0.85):
    """Turned 90 degrees: the shoulders and hips nearly overlap in the image."""
    return {
        'left_shoulder': Landmark2D(.50, .30, confidence),
        'right_shoulder': Landmark2D(.52, .30, confidence),
        'left_elbow': Landmark2D(elbow_x, elbow_y, confidence),
        'right_elbow': Landmark2D(elbow_x, elbow_y, confidence),
        'left_hip': Landmark2D(.50, .68, confidence),
        'right_hip': Landmark2D(.52, .68, confidence),
    }


class SagittalMeasurementTests(unittest.TestCase):
    def test_frontal_view_cannot_resolve_a_forward_raise(self):
        """The reason flexion needs a side view.

        Frontal 2D landmarks all sit on one plane, so the torso forward axis is
        perpendicular to the image and the flexion projection collapses: every arm
        position reads as exactly 0 or 180 degrees, carrying no magnitude at all.
        """
        for elbow in (Landmark2D(.70, .48, .95), Landmark2D(.70, .28, .95), Landmark2D(.63, .12, .95)):
            points = _planar_points({**facing_camera(), 'right_elbow': elbow})
            self.assertIn(round(abs(calculate_flexion(points, 'right')), 6), (0.0, 180.0))

    def test_side_view_elevation_tracks_a_forward_raise(self):
        at_rest = _planar_points(side_on(elbow_x=.50, elbow_y=.48))
        forward = _planar_points(side_on(elbow_x=.66, elbow_y=.30))
        overhead = _planar_points(side_on(elbow_x=.51, elbow_y=.12))
        rest_deg = calculate_sagittal_elevation(at_rest, 'right')
        forward_deg = calculate_sagittal_elevation(forward, 'right')
        overhead_deg = calculate_sagittal_elevation(overhead, 'right')
        self.assertLess(rest_deg, 15)
        self.assertTrue(60 < forward_deg < 120, forward_deg)
        self.assertGreater(overhead_deg, 160)

    def test_sagittal_framing_requires_the_patient_to_actually_turn(self):
        self.assertTrue(_sagittal_framing_ok(side_on()))
        # Still facing the camera: the shoulders are far apart, so refuse to measure.
        self.assertFalse(_sagittal_framing_ok(facing_camera()))
        # And a turned stance is not valid framing for a frontal measurement.
        self.assertFalse(_framing_ok(side_on()))

    def test_sagittal_calibration_accepts_occluded_far_side_joints(self):
        landmarks = side_on(confidence=0.5)
        snapshot = {
            'people': 1, 'points': _planar_points(landmarks), 'simulation': False,
            'camera_ok': True, 'depth_ok': False, 'depth_required': False, 'distance': None,
            'framing_ok': _sagittal_framing_ok(landmarks),
            'capture_profile': 'phone_rgb_2d_sagittal',
        }
        result = evaluate_calibration(snapshot)
        self.assertTrue(result['ready'], result['message'])
        self.assertIsNone(result['distance_m'])
        self.assertIn('not an isolated flexion angle', result['message'])
        # The same low-confidence side view is not good enough for a frontal measurement.
        frontal = evaluate_calibration({**snapshot, 'capture_profile': 'phone_rgb_2d'})
        self.assertFalse(frontal['ready'])

    def test_pipeline_uses_the_image_plane_only_for_sagittal_phone_sessions(self):
        from edge.pipeline import VisionPipeline
        self.assertEqual(VisionPipeline('T', 'shoulder_flexion', 'right', 120, 3, 'phone').plane, 'sagittal')
        self.assertEqual(VisionPipeline('T', 'shoulder_abduction', 'right', 90, 3, 'phone').plane, 'frontal')
        # RGB-D keeps true 3D flexion.
        self.assertEqual(VisionPipeline('T', 'shoulder_flexion', 'right', 120, 3, 'simulation').plane, 'frontal')

    def test_phone_tracking_allows_sagittal_exercises_but_not_rotation(self):
        self.assertTrue(phone_trackable('shoulder_abduction'))
        self.assertTrue(phone_trackable('shoulder_flexion'))
        self.assertFalse(phone_trackable('external_rotation'))
        self.assertEqual(get_exercise('shoulder_abduction')['phone_plane'], 'frontal')
        self.assertEqual(get_exercise('shoulder_flexion')['phone_plane'], 'sagittal')
        self.assertEqual(BASELINE_EXERCISES['abduction'], 'shoulder_abduction')
        self.assertEqual(BASELINE_EXERCISES['flexion'], 'shoulder_flexion')


class BaselineValidityTests(unittest.TestCase):
    """A baseline may only come from a session that actually measured something."""

    class FakeRow:
        def __init__(self, coverage, peak):
            self.coverage = coverage
            self.peak_angle = peak

    def summary(self, level='OK'):
        return {'safety': {'level': level}}

    def test_lost_tracking_is_not_a_measurement(self):
        # MediaPipe saw no one: peak stays 0 and safety pauses. Storing 0 degrees
        # here would report a frozen shoulder that was never measured.
        self.assertFalse(is_valid_rom(self.FakeRow(0, 0.0), self.summary('PAUSE')))
        self.assertFalse(is_valid_rom(self.FakeRow(100, 0.0), self.summary()))
        self.assertFalse(is_valid_rom(self.FakeRow(None, 84.0), self.summary()))

    def test_patchy_tracking_is_not_a_measurement(self):
        self.assertFalse(is_valid_rom(self.FakeRow(31, 84.0), self.summary()))

    def test_blocked_session_is_not_a_measurement(self):
        self.assertFalse(is_valid_rom(self.FakeRow(90, 84.0), self.summary('BLOCK')))

    def test_a_tracked_session_is_a_measurement(self):
        self.assertTrue(is_valid_rom(self.FakeRow(90, 84.0), self.summary()))


class BaselineMemoryTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest.mock import patch
        self._dir = tempfile.TemporaryDirectory()
        self._patch = patch('backend.memory.STORAGE_DIR', __import__('pathlib').Path(self._dir.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._dir.cleanup()

    def test_baseline_order_is_abduction_then_flexion(self):
        from backend.memory import (
            baseline_complete, load_memory, next_baseline_movement, record_baseline_measurement,
        )
        self.assertEqual(next_baseline_movement(load_memory('P1')['baseline']), 'abduction')
        record_baseline_measurement('P1', 'abduction', 84.4, 'SESSION_A', 'phone')
        self.assertEqual(next_baseline_movement(load_memory('P1')['baseline']), 'flexion')
        self.assertFalse(baseline_complete(load_memory('P1')))
        record_baseline_measurement('P1', 'flexion', 101.2, 'SESSION_B', 'phone')
        row = load_memory('P1')
        self.assertTrue(baseline_complete(row))
        self.assertEqual(row['baseline']['phase'], 'done')
        self.assertEqual(row['baseline']['abduction_deg'], 84.4)
        self.assertEqual(row['baseline']['flexion_deg'], 101.2)
        self.assertEqual(row['baseline']['session_ids'], ['SESSION_A', 'SESSION_B'])

    def test_skipping_records_no_measurement(self):
        from backend.memory import baseline_complete, load_memory, skip_baseline_phase, skip_talk_phase
        skip_baseline_phase('P2')
        row = load_memory('P2')
        self.assertTrue(baseline_complete(row))
        self.assertEqual(row['baseline']['phase'], 'skipped')
        self.assertIsNone(row['baseline']['abduction_deg'])
        self.assertIsNone(row['baseline']['flexion_deg'])
        # The Talk escape hatch skips the camera step too, and still invents nothing.
        skip_talk_phase('P3')
        self.assertEqual(load_memory('P3')['baseline']['phase'], 'skipped')
        self.assertIsNone(load_memory('P3')['baseline']['abduction_deg'])

    def test_corrupt_baseline_falls_back_without_crashing(self):
        from backend.memory import normalize_baseline
        row = normalize_baseline({'phase': 'bogus', 'abduction_deg': 'ninety', 'session_ids': 'nope'})
        self.assertEqual(row['phase'], 'needed')
        self.assertIsNone(row['abduction_deg'])
        self.assertEqual(row['session_ids'], [])


if __name__ == '__main__':
    unittest.main()
