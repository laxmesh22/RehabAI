"""Unit tests for follow-along motion maths (ported from motion.test.ts)."""
from __future__ import annotations

import math
import unittest


def clamp(x, lo, hi):
    return min(hi, max(lo, x))


def clamp01(x):
    return clamp(x, 0, 1)


def deg2rad(d):
    return (d * math.pi) / 180


def rad2deg(r):
    return (r * 180) / math.pi


def ease(x):
    return 0.5 - 0.5 * math.cos(math.pi * clamp01(x))


EXERCISES = {
    'er': {'minTarget': 10, 'maxTarget': 90, 'continuous': False},
    'pendulum': {'minTarget': 0, 'maxTarget': 0, 'continuous': True},
}


def clamp_target(eid, target):
    s = EXERCISES[eid]
    return 0 if s['continuous'] else clamp(target, s['minTarget'], s['maxTarget'])


def timing_for(target, speed=18, hold_s=5, rest_s=1.5):
    move_s = max(2.5, target / max(1, speed))
    return {'moveS': move_s, 'holdS': max(0, hold_s), 'returnS': move_s, 'restS': max(0, rest_s)}


def cycle_length(t):
    return t['moveS'] + t['holdS'] + t['returnS'] + t['restS']


def cycle_at(elapsed, t):
    total = cycle_length(t)
    rep = math.floor(elapsed / total)
    x = elapsed - rep * total
    if x < t['moveS']:
        return {'rep': rep, 'phase': 'move', 'progress': ease(x / t['moveS'])}
    x -= t['moveS']
    if x < t['holdS']:
        return {'rep': rep, 'phase': 'hold', 'progress': 1}
    x -= t['holdS']
    if x < t['returnS']:
        return {'rep': rep, 'phase': 'return', 'progress': 1 - ease(x / t['returnS'])}
    return {'rep': rep, 'phase': 'rest', 'progress': 0}


def staircase(p, steps):
    x = clamp01(p) * steps
    i = math.floor(x)
    if i >= steps:
        return 1
    return (i + ease(x - i)) / steps


def elevation_dir(elevation_deg, plane_deg, side_sign):
    e = deg2rad(clamp(elevation_deg, 0, 175))
    p = deg2rad(plane_deg)
    return (side_sign * math.sin(e) * math.cos(p), -math.cos(e), math.sin(e) * math.sin(p))


def sagittal_ik(z, y, upper, fore):
    reach = upper + fore - 1e-4
    d = clamp(math.hypot(z, y), abs(upper - fore) + 1e-4, reach)
    to_target = math.atan2(z, -y)
    a = math.acos(clamp((upper * upper + d * d - fore * fore) / (2 * upper * d), -1, 1))
    g = math.acos(clamp((upper * upper + fore * fore - d * d) / (2 * upper * fore), -1, 1))
    return {'elevation': rad2deg(to_target - a), 'elbowFlex': rad2deg(math.pi - g)}


def wall_height_for(target_elevation, wall_z, upper, fore):
    max_h = math.sqrt(max(0, (upper + fore - 1e-3) ** 2 - wall_z * wall_z))
    lo, hi = -0.3, max_h
    if sagittal_ik(wall_z, hi, upper, fore)['elevation'] <= target_elevation:
        return {'height': hi, 'elevation': sagittal_ik(wall_z, hi, upper, fore)['elevation']}
    for _ in range(40):
        mid = (lo + hi) / 2
        if sagittal_ik(wall_z, mid, upper, fore)['elevation'] < target_elevation:
            lo = mid
        else:
            hi = mid
    return {'height': (lo + hi) / 2, 'elevation': sagittal_ik(wall_z, (lo + hi) / 2, upper, fore)['elevation']}


class MotionMathTests(unittest.TestCase):
    def test_cycle_and_timing(self):
        t = timing_for(45, 18, 5, 1.5)
        self.assertAlmostEqual(t['moveS'], 2.5)
        self.assertAlmostEqual(cycle_at(0, t)['progress'], 0)
        self.assertAlmostEqual(cycle_at(t['moveS'] / 2, t)['progress'], 0.5, places=5)
        self.assertEqual(cycle_at(t['moveS'] + 1, t)['phase'], 'hold')
        self.assertAlmostEqual(cycle_at(t['moveS'] + t['holdS'] + t['returnS'], t)['progress'], 0)
        self.assertEqual(cycle_at(cycle_length(t) + 0.01, t)['rep'], 1)

    def test_elevation_and_ik(self):
        d = elevation_dir(0, 0, 1)
        self.assertAlmostEqual(d[0], 0)
        self.assertAlmostEqual(d[1], -1)
        self.assertAlmostEqual(d[2], 0)
        d = elevation_dir(90, 90, 1)
        self.assertAlmostEqual(d[1], 0, places=9)
        self.assertAlmostEqual(d[2], 1)
        ik = sagittal_ik(0.57, 0, 0.29, 0.28)
        self.assertAlmostEqual(ik['elevation'], 90, delta=2)
        self.assertAlmostEqual(ik['elbowFlex'], 0, delta=3)
        ik = sagittal_ik(0.40, -0.2, 0.29, 0.28)
        self.assertGreater(ik['elbowFlex'], 20)
        self.assertLess(ik['elevation'], 60)

    def test_wall_and_clamp(self):
        w = wall_height_for(100, 0.40, 0.29, 0.28)
        self.assertAlmostEqual(w['elevation'], 100, delta=0.01)
        wmax = wall_height_for(170, 0.40, 0.29, 0.28)
        self.assertLessEqual(wmax['elevation'], 140)
        self.assertEqual(clamp_target('er', 200), 90)
        self.assertEqual(clamp_target('pendulum', 50), 0)
        self.assertEqual(staircase(1, 6), 1)
        self.assertEqual(staircase(0, 6), 0)


if __name__ == '__main__':
    unittest.main()
