import unittest
from agent.guide_caption import caption_guide
from agent.safety.engine import evaluate_safety
from edge.exercises.library import get_exercise
from edge.guide_pose import build_guide, mixamo_rotations
from edge.pipeline import VisionPipeline
from edge.simulation import SimulatedPatient


class GuidePoseTests(unittest.TestCase):
    def test_right_abduction_uses_mixamo_right_arm(self):
        bones = mixamo_rotations('right', 'abduction', 90, 0)
        self.assertEqual(bones['RightArm']['rz'], 90)
        self.assertEqual(bones['RightArm']['rx'], 0)
        self.assertEqual(bones['Spine']['rz'], 0)

    def test_left_abduction_mirrors(self):
        bones = mixamo_rotations('left', 'abduction', 90, 0)
        self.assertEqual(bones['LeftArm']['rz'], -90)

    def test_flexion_raises_forward(self):
        bones = mixamo_rotations('right', 'flexion', 80, 0)
        self.assertEqual(bones['RightArm']['rx'], -80)

    def test_guide_torso_stays_upright_when_patient_leans(self):
        spec = get_exercise('shoulder_abduction')
        sample = {'phase': 'ascending', 'angle': 50, 'lean': 18, 'valid': True, 'feedback': 'Keep upright.'}
        safety = evaluate_safety({'pose_confidence': 0.9, 'torso_lean': 18})
        guide = build_guide(spec, 'right', sample, safety, 80)
        self.assertEqual(guide['guide_torso_deg'], 0.0)
        self.assertGreater(guide['patient_torso_deg'], 10)
        self.assertEqual(guide['driven_by'], 'telemetry_not_llm')
        self.assertEqual(guide['rig'], 'mixamo')
        self.assertIn('RightArm', guide['bones'])

    def test_pipeline_emits_guide_payload(self):
        pipeline = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'simulation')
        patient = SimulatedPatient('right', 'abduction')
        row = None
        t = 0.0
        for angle in (8, 12, 18, 32, 55, 85):
            t += 0.2
            patient.set_angle(angle)
            row = pipeline.process_snapshot(patient.skeleton(t))
        self.assertIn('guide', row)
        self.assertEqual(row['guide']['driven_by'], 'telemetry_not_llm')
        self.assertGreater(row['guide']['guide_arm_deg'], 40)
        self.assertEqual(row['guide']['bones']['RightArm']['rz'], row['guide']['guide_arm_deg'])

    def test_caption_does_not_invent_bones_without_llm(self):
        out = caption_guide({'cue': 'Raise your arm slowly.', 'phase': 'ascending', 'safety': 'ALLOW'})
        self.assertEqual(out['caption'], 'Raise your arm slowly.')
        self.assertFalse(out['llm_used'])
        self.assertFalse(out['bones_from_llm'])
        self.assertEqual(out['driven_by'], 'telemetry_not_llm')

    def test_block_keeps_deterministic_stop_cue(self):
        cue = 'Please stop. This session requires physiotherapist review.'
        out = caption_guide({'cue': cue, 'phase': 'rest', 'safety': 'BLOCK'})
        self.assertEqual(out['caption'], cue)
        self.assertFalse(out['llm_used'])
