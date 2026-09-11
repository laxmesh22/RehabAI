import unittest
from edge.exercises.state_machine import ExerciseMachine
from agent.safety.engine import evaluate_safety
from edge.pipeline import VisionPipeline
from edge.simulation import SimulatedPatient


class MachineTests(unittest.TestCase):
    def feed(self, engine, angles, lean=2, confidence=.95, t0=0):
        result = None
        t = t0
        for angle in angles:
            t += .15
            result = engine.update(t, angle, lean, confidence)
        return result, t

    def test_counts_complete_cycle(self):
        engine = ExerciseMachine(target=80, goal=2)
        result, _ = self.feed(engine, [8, 12, 40, 70, 85, 70, 30, 10])
        self.assertEqual(result['reps'], 1)
        self.assertEqual(result['phase'], 'rest')

    def test_incomplete_does_not_count(self):
        engine = ExerciseMachine(target=80, goal=2)
        result, _ = self.feed(engine, [8, 40, 70, 40, 10])
        self.assertEqual(result['reps'], 0)

    def test_lean_rejects_rep(self):
        engine = ExerciseMachine(target=80, goal=2)
        _, t = self.feed(engine, [8, 40, 85])
        result = engine.update(t + .15, 70, 18, .95)
        self.assertFalse(result['valid'])
        self.assertIn('Torso compensation', result['feedback'])


class SafetyTests(unittest.TestCase):
    def test_allow_clean_sample(self):
        self.assertEqual(evaluate_safety({'pose_confidence': .9, 'torso_lean': 3}).level, 'ALLOW')

    def test_warn_on_lean(self):
        self.assertEqual(evaluate_safety({'pose_confidence': .9, 'torso_lean': 14}).level, 'WARN')

    def test_block_not_overridable(self):
        decision = evaluate_safety({'pose_confidence': .9, 'torso_lean': 3}, {'pain_during': 9})
        self.assertEqual(decision.level, 'BLOCK')
        self.assertTrue(decision.halt_exercise)

    def test_pause_on_lost_person(self):
        self.assertEqual(evaluate_safety({'pose_confidence': .9, 'torso_lean': 0, 'people': 0}).level, 'PAUSE')


class PipelineSimulationTests(unittest.TestCase):
    def test_simulation_is_labelled_and_measures_abduction(self):
        pipeline = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'simulation')
        patient = SimulatedPatient('right', 'abduction')
        patient.set_angle(90)
        row = pipeline.process_snapshot(patient.skeleton(1.0))
        self.assertEqual(row['source'], 'simulation')
        self.assertGreater(row['shoulder_angle'], 70)
        self.assertTrue(row['calibration']['simulation'])
        self.assertFalse(row['calibration']['camera_ok'])

    def test_lean_fault_emits_compensation_coaching(self):
        pipeline = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'simulation')
        patient = SimulatedPatient('right', 'abduction')
        patient.set_angle(40)
        patient.set_fault('lean')
        row = pipeline.process_snapshot(patient.skeleton(1.0))
        self.assertGreater(row['torso_lean'], 10)
        self.assertIn('Torso compensation', row['feedback'])
