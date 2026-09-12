import asyncio
import unittest

from agent.exercise_coach import _deterministic_cue, adaptive_coach


class ExerciseCoachTests(unittest.TestCase):
    def test_above_target_raises_demo_to_measured_peak_band(self):
        out = _deterministic_cue({
            'safety': 'ALLOW',
            'angle': 118,
            'peak': 122,
            'target': 90,
        }, hindi=False)
        self.assertIn('122', out['spoken'])
        self.assertEqual(out['demo_target'], 122.0)
        self.assertFalse(out['llm_used'])

    def test_safety_block_stops_without_demo_change(self):
        out = _deterministic_cue({
            'safety': 'BLOCK',
            'angle': 40,
            'peak': 40,
            'target': 90,
        }, hindi=False)
        self.assertIn('stop', out['spoken'].lower())
        self.assertIsNone(out['demo_target'])

    def test_below_target_stays_listen_without_script(self):
        out = _deterministic_cue({
            'safety': 'ALLOW',
            'angle': 30,
            'peak': 35,
            'target': 90,
        }, hindi=False)
        self.assertIn('listening', out['spoken'].lower())
        self.assertNotIn('match the 3d guide', out['spoken'].lower())
        self.assertIsNone(out['demo_target'])

    def test_adaptive_coach_falls_back_without_api_key(self):
        out = asyncio.run(adaptive_coach({
            'language': 'en-IN',
            'safety': 'ALLOW',
            'angle': 100,
            'peak': 105,
            'target': 90,
        }))
        self.assertTrue(out['spoken'])
        self.assertIn(out['engine'], ('coach-deterministic', 'coach-claude', 'coach-safety', 'coach-pain'))
        if out['demo_target'] is not None:
            self.assertLessEqual(out['demo_target'], 105)

    def test_numbness_skips_claude_and_does_not_raise_demo(self):
        from unittest.mock import patch
        from agent import exercise_coach
        with patch.object(exercise_coach, 'ANTHROPIC_API_KEY', 'configured'):
            out = asyncio.run(adaptive_coach({
                'language': 'en-IN',
                'safety': 'ALLOW',
                'angle': 100,
                'peak': 122,
                'target': 90,
                'patient_said': 'I have numbness',
            }))
        self.assertEqual(out['engine'], 'coach-safety')
        self.assertIsNone(out['demo_target'])
        self.assertFalse(out['llm_used'])


if __name__ == '__main__':
    unittest.main()
