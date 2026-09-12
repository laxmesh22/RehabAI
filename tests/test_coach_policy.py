import unittest

from agent.coach_policy import above_target, clamp_demo_target


class CoachPolicyTests(unittest.TestCase):
    def test_clamp_never_above_peak(self):
        self.assertEqual(clamp_demo_target(140, 120, 90), 120.0)

    def test_clamp_rejects_below_session_target(self):
        self.assertIsNone(clamp_demo_target(80, 120, 90))

    def test_above_target_margin(self):
        self.assertTrue(above_target(100, 90))
        self.assertFalse(above_target(95, 90))


if __name__ == '__main__':
    unittest.main()
