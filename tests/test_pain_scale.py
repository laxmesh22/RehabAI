"""Pain scale helpers (ported from rehab-ai-avatar-and-pain-check)."""
from __future__ import annotations

import unittest

PAIN_ANCHORS = [
    (0, 'No pain'), (2, 'Mild'), (4, 'Moderate'),
    (6, 'Fairly severe'), (8, 'Severe'), (10, 'Worst pain'),
]
PAIN_EMOJI = ['😄', '🙂', '🙂', '😐', '😐', '😕', '😕', '🙁', '😣', '😖', '😭']


def snap_pain(value):
    return max(0, min(10, round(value)))


def label_for_pain(value):
    v = max(0, min(10, value))
    best = PAIN_ANCHORS[0]
    best_dist = float('inf')
    for a in PAIN_ANCHORS:
        d = abs(a[0] - v)
        if d < best_dist:
            best_dist = d
            best = a
    return best[1]


def tier_for_pain(value):
    v = snap_pain(value)
    if v <= 3:
        return 'low'
    if v <= 6:
        return 'moderate'
    return 'high'


def should_flag(current, previous=None):
    v = snap_pain(current)
    if v >= 8:
        return True
    if previous is None:
        return False
    return v - snap_pain(previous) >= 2


class PainScaleTests(unittest.TestCase):
    def test_snap_and_tiers(self):
        self.assertEqual(snap_pain(3.4), 3)
        self.assertEqual(snap_pain(3.6), 4)
        self.assertEqual(tier_for_pain(0), 'low')
        self.assertEqual(tier_for_pain(6), 'moderate')
        self.assertEqual(tier_for_pain(7), 'high')
        self.assertEqual(label_for_pain(6), 'Fairly severe')

    def test_flags(self):
        self.assertTrue(should_flag(8))
        self.assertFalse(should_flag(7))
        self.assertTrue(should_flag(6, 4))
        self.assertFalse(should_flag(5, 4))
        self.assertFalse(should_flag(3, 6))

    def test_library_avatar_mapping(self):
        from edge.exercises.library import EXERCISES, avatar_demo_for
        demos = {avatar_demo_for(eid) for eid in EXERCISES}
        for needed in ('abduction', 'flexion', 'wand_flexion', 'pendulum', 'wall_walk', 'er', 'wand_er'):
            self.assertIn(needed, demos)
        self.assertEqual(avatar_demo_for('assisted_flexion'), 'wand_flexion')
        self.assertEqual(avatar_demo_for('wall_climb'), 'wall_walk')


if __name__ == '__main__':
    unittest.main()
