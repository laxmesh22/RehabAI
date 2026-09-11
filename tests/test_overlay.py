import unittest
from edge.overlay import encode_jpeg, jpeg_b64, opencv_available, render_rgb
from edge.pipeline import VisionPipeline
from edge.simulation import SimulatedPatient


class OverlayTests(unittest.TestCase):
    def test_simulation_frame_encodes_jpeg(self):
        patient = SimulatedPatient('right', 'abduction')
        patient.set_angle(70)
        snap = patient.skeleton(1.0)
        frame = render_rgb(snap['landmarks2d'], 'right', 'simulation', 70, 'Raise slowly.')
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape[2], 3)
        jpeg = encode_jpeg(frame)
        self.assertTrue(jpeg and jpeg[:2] == b'\xff\xd8')
        self.assertTrue(jpeg_b64(frame).startswith('/9j/'))

    def test_pipeline_attaches_jpeg_without_claiming_camera(self):
        pipeline = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'simulation')
        patient = SimulatedPatient('right', 'abduction')
        patient.set_angle(80)
        row = pipeline.process_snapshot(patient.skeleton(1.0))
        self.assertEqual(row['source'], 'simulation')
        self.assertTrue(row['calibration']['simulation'])
        self.assertTrue(row.get('frame_jpeg'))

    def test_opencv_flag_is_boolean(self):
        self.assertIsInstance(opencv_available(), bool)
