"""Deterministic torso-relative projections. These are not isolated GH angles."""
import math


def subtract(a, b):
    return tuple(x-y for x, y in zip(a, b))


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def unit(v):
    if len(v) != 3 or not all(math.isfinite(x) for x in v):
        raise ValueError('Expected finite 3D vector')
    length = math.sqrt(dot(v, v))
    if length < 1e-8:
        raise ValueError('Degenerate vector')
    return tuple(x/length for x in v)


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def angle_between(a, b):
    return math.degrees(math.acos(max(-1, min(1, dot(unit(a), unit(b))))))


def torso_basis(points):
    ls, rs = points['left_shoulder'].xyz, points['right_shoulder'].xyz
    lh, rh = points['left_hip'].xyz, points['right_hip'].xyz
    down = unit(tuple((a+b-c-d)/2 for a, b, c, d in zip(lh, rh, ls, rs)))
    right = subtract(rs, ls)
    right = unit(subtract(right, tuple(dot(right, down)*v for v in down)))
    # Facing the camera, subject-right points image-left; forward points toward camera.
    forward = unit(cross(right, down))
    return right, down, forward


def arm_vector(points, side):
    if side not in ('left', 'right'):
        raise ValueError('Unknown side')
    return subtract(points[side+'_elbow'].xyz, points[side+'_shoulder'].xyz)


def calculate_elevation(points, side):
    return angle_between(arm_vector(points, side), torso_basis(points)[1])


def projected_angle(arm, axis, down):
    x, y = dot(arm, axis), dot(arm, down)
    if math.hypot(x, y) < 1e-6:
        raise ValueError('Movement has no reliable projection into this plane')
    return math.degrees(math.atan2(x, y))


def calculate_flexion(points, side):
    _, down, forward = torso_basis(points)
    return projected_angle(arm_vector(points, side), forward, down)


def calculate_extension(points, side):
    return max(0., -calculate_flexion(points, side))


def calculate_abduction(points, side):
    right, down, _ = torso_basis(points)
    outward = right if side == 'right' else tuple(-v for v in right)
    return projected_angle(arm_vector(points, side), outward, down)


def calculate_internal_rotation(*_):
    raise NotImplementedError('Rotation requires a validated protocol; unsupported in this MVP')


def calculate_external_rotation(*_):
    raise NotImplementedError('Rotation requires a validated protocol; unsupported in this MVP')


def calculate_elbow_bend(points, side):
    elbow = points[side+'_elbow'].xyz
    return 180-angle_between(subtract(points[side+'_shoulder'].xyz, elbow), subtract(points[side+'_wrist'].xyz, elbow))


def compensation(points, reference):
    down = torso_basis(points)[1]
    right0, down0, forward0 = reference
    return {'torso_lean': angle_between(down, down0),
            'lateral_lean': projected_angle(down, right0, down0),
            'forward_lean': projected_angle(down, forward0, down0)}
