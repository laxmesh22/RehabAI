import math
import unittest
from backend.engine import SessionEngine, arm_angle


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = SessionEngine(target=80, goal=2)
        self.t = 0

    def feed(self, angles, confidence=.95, lean=0):
        for angle in angles:
            self.t += .15
            result = self.engine.update(self.t, angle, lean, confidence)
        return result

    def test_geometry_reference_angles(self):
        for elbow, expected in [([0, 1, 0], 0), ([1, 0, 0], 90), ([0, -1, 0], 180)]:
            self.assertAlmostEqual(arm_angle([0, 0, 0], elbow, [0, 1, 0]), expected)

    def test_degenerate_geometry_rejected(self):
        with self.assertRaises(ValueError):
            arm_angle([0, 0, 0], [0, 0, 0], [0, 1, 0])

    def test_full_cycle_counts_once(self):
        result = self.feed([0, 35, 65, 85, 85, 68, 30, 10, 0])
        self.assertEqual(result['reps'], 1)
        self.assertFalse(result['complete'])

    def test_start_above_target_does_not_count(self):
        self.assertEqual(self.feed([90, 95, 60, 10])['reps'], 0)

    def test_incomplete_cycle_does_not_count(self):
        self.assertEqual(self.feed([0, 35, 65, 30, 10])['reps'], 0)

    def test_occlusion_discards_partial_rep(self):
        self.feed([0, 35, 85])
        self.feed([85], confidence=.1)
        result = self.feed([65, 10])
        self.assertEqual(result['reps'], 0)
        self.assertEqual(self.feed([35, 85, 60, 10])['reps'], 1)

    def test_lean_excluded_from_peak(self):
        result = self.feed([140], lean=25)
        self.assertEqual(result['peak'], 0)
        self.assertFalse(result['valid'])

    def test_gap_discards_partial_rep(self):
        self.feed([0, 35, 85])
        self.t += 2
        self.assertFalse(self.feed([50])['valid'])
        self.assertEqual(self.feed([10])['reps'], 0)

    def test_out_of_order_rejected(self):
        self.feed([0])
        with self.assertRaises(ValueError):
            self.engine.update(self.t, 0, 0, .9)

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):
            self.feed([math.nan])

    def test_out_of_range_invalid(self):
        self.assertFalse(self.feed([200])['valid'])

    def test_goal_completes(self):
        self.feed([0, 35, 85, 60, 10])
        self.assertTrue(self.feed([35, 85, 60, 10])['complete'])


if __name__ == '__main__':
    unittest.main()
