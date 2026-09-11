from edge.biomechanics.angles import torso_basis, angle_between, calculate_elevation


class Calibration:
    """Collect a stable neutral posture. Missing data resets the acquisition window."""
    def __init__(self, samples=30):
        self.samples = samples
        self.reset()

    def reset(self):
        self.reference = None
        self.candidates = []

    def update(self, points, side):
        required = ['left_shoulder', 'right_shoulder', 'left_hip', 'right_hip', side+'_elbow']
        pose_ok = all(name in points for name in required)
        distance_ok = pose_ok and all(.7 <= points[name].xyz[2] <= 3.5 for name in required)
        checks = dict(pose=pose_ok, depth=pose_ok, distance=bool(distance_ok))
        if not all(checks.values()):
            self.candidates.clear()
            return checks
        if self.reference is None:
            basis = torso_basis(points)
            if calculate_elevation(points, side) > 20 or angle_between(basis[1], (0, 1, 0)) > 15:
                self.candidates.clear()
                return checks
            if self.candidates and angle_between(basis[1], self.candidates[0][1]) > 3:
                self.candidates.clear()
            self.candidates.append(basis)
            if len(self.candidates) >= self.samples:
                self.reference = basis
        return checks
