import unittest
from edge.biomechanics.angles import (
    calculate_abduction, calculate_elbow_bend, calculate_elevation, calculate_extension,
    calculate_external_rotation, calculate_flexion, compensation, torso_basis,
)
from edge.contracts import Joint3D


def body(right_offset, lean=0):
    ls, rs = (0.18, -0.15, 1.8), (-0.18, -0.15, 1.8)
    if lean:
        import math
        r = math.radians(lean)
        def rot(p):
            x, y, z = p
            return (x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r), z)
        ls, rs = rot(ls), rot(rs)
        lh, rh = rot((0.12, 0.38, 1.8)), rot((-0.12, 0.38, 1.8))
    else:
        lh, rh = (0.12, 0.38, 1.8), (-0.12, 0.38, 1.8)
    shoulder = rs
    elbow = tuple(s + o for s, o in zip(shoulder, right_offset))
    wrist = tuple(e + o * .8 for e, o in zip(elbow, right_offset))
    return {
        'left_shoulder': Joint3D(ls, 1), 'right_shoulder': Joint3D(rs, 1),
        'left_hip': Joint3D(lh, 1), 'right_hip': Joint3D(rh, 1),
        'right_elbow': Joint3D(elbow, 1), 'right_wrist': Joint3D(wrist, 1),
        'left_elbow': Joint3D((0.18, 0.13, 1.8), 1), 'left_wrist': Joint3D((0.18, 0.35, 1.8), 1),
    }


class AngleTests(unittest.TestCase):
    def test_hanging_arm_near_zero_abduction(self):
        self.assertLess(abs(calculate_abduction(body((0, 0.28, 0)), 'right')), 8)

    def test_sideways_arm_near_ninety_abduction(self):
        self.assertAlmostEqual(calculate_abduction(body((-0.28, 0, 0)), 'right'), 90, delta=8)

    def test_forward_arm_near_ninety_flexion(self):
        self.assertAlmostEqual(calculate_flexion(body((0, 0, -0.28)), 'right'), 90, delta=8)

    def test_elevation_tracks_raise(self):
        down = calculate_elevation(body((0, 0.28, 0)), 'right')
        out = calculate_elevation(body((-0.28, 0, 0)), 'right')
        self.assertLess(down, 20)
        self.assertGreater(out, 70)

    def test_extension_is_negative_flexion_floor(self):
        self.assertGreaterEqual(calculate_extension(body((0, 0.28, 0)), 'right'), 0)

    def test_rotation_not_claimed(self):
        with self.assertRaises(NotImplementedError):
            calculate_external_rotation()

    def test_straight_elbow_near_zero_bend(self):
        self.assertLess(calculate_elbow_bend(body((0, 0.28, 0)), 'right'), 20)

    def test_upright_torso_small_lean_against_camera_vertical(self):
        reference = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0))
        lean = compensation(body((0, 0.28, 0)), reference)['torso_lean']
        self.assertLess(lean, 8)

    def test_leaned_torso_exceeds_ten_degrees(self):
        reference = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0))
        lean = compensation(body((0, 0.28, 0), lean=18), reference)['torso_lean']
        self.assertGreater(lean, 10)
