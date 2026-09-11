import json
import socket
import unittest
from agent.safety.engine import evaluate_safety
from edge.feedback.coach import coach_message
from edge.imu.device import open_imu, resolve_transport
from edge.imu.features import ImuWindow
from edge.imu.fusion import ComplementaryFusion
from edge.imu.packets import parse_line
from edge.imu.readers import UdpImu
from edge.imu.simulation import SimulatedArmImu
from edge.overlay import overlay_banner
from edge.pipeline import VisionPipeline
from edge.simulation import SimulatedPatient


class TransportTests(unittest.TestCase):
    def test_simulation_camera_gets_synthetic_imu(self):
        self.assertEqual(resolve_transport('simulation', 'auto'), 'simulation')
        device = open_imu('simulation')
        self.assertIsInstance(device, SimulatedArmImu)

    def test_live_camera_never_gets_synthetic_imu(self):
        with self.assertRaises(ValueError):
            resolve_transport('live', 'simulation')
        self.assertEqual(resolve_transport('live', 'auto'), 'off')
        self.assertIsNone(open_imu('live', 'auto'))

    def test_live_udp_is_explicit(self):
        self.assertEqual(resolve_transport('live', 'udp'), 'udp')


class SimulatedImuTests(unittest.TestCase):
    def test_gyro_tracks_angle_change_and_stays_labelled(self):
        imu = SimulatedArmImu()
        first = imu.read_from_angle(0.0, 10, 'right')
        second = imu.read_from_angle(0.2, 50, 'right')
        self.assertTrue(first['simulation'])
        self.assertEqual(first['source'], 'simulation')
        self.assertEqual(first['placement'], 'arm')
        self.assertGreater(second['gyro_norm'], 100)
        self.assertEqual(second['gz'], 200.0)

    def test_drop_is_lost_not_live(self):
        packet = SimulatedArmImu().read_from_angle(1.0, 40, drop=True)
        self.assertFalse(packet['ok'])
        self.assertTrue(packet['lost'])
        self.assertEqual(packet['source'], 'simulation')


class FusionTests(unittest.TestCase):
    def test_agrees_when_rate_matches_camera(self):
        fusion = ComplementaryFusion()
        imu = SimulatedArmImu()
        imu.read_from_angle(0.0, 10)
        packet = imu.read_from_angle(0.1, 20)
        result = fusion.update(20, packet, 0.1, camera_velocity=100)
        self.assertTrue(result['used_imu'])
        self.assertTrue(result['agree'])
        self.assertAlmostEqual(result['fused_angle'], 20, delta=8)

    def test_disagreement_resets_to_camera(self):
        fusion = ComplementaryFusion(disagree_deg=25)
        fusion.update(90, {'ok': True, 'gx': 0, 'gy': 0, 'gz': 0}, 0.1, 0)
        wild = {'ok': True, 'gx': 0, 'gy': 0, 'gz': 400}
        result = fusion.update(90, wild, 0.1, 0)
        self.assertFalse(result['agree'])
        self.assertEqual(result['fused_angle'], 90)


class FeatureWindowTests(unittest.TestCase):
    def test_quality_needs_several_ok_samples(self):
        window = ImuWindow(size=8)
        imu = SimulatedArmImu()
        summary = None
        for i in range(6):
            summary = window.push(imu.read_from_angle(i * 0.1, 10 + i * 8))
        self.assertIsNotNone(summary)
        self.assertGreaterEqual(summary['n'], 3)
        self.assertIn('gyro_peak', summary)


class PipelineImuTests(unittest.TestCase):
    def test_dual_simulation_is_labelled_not_classified(self):
        pipeline = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'simulation')
        patient = SimulatedPatient('right', 'abduction')
        imu = SimulatedArmImu()
        patient.set_angle(80)
        snap = patient.skeleton(1.0)
        snap['imu'] = imu.read_from_angle(1.0, 80, 'right')
        row = pipeline.process_snapshot(snap)
        self.assertEqual(row['source'], 'simulation')
        self.assertEqual(row['sensors']['camera'], 'simulation')
        self.assertEqual(row['sensors']['imu'], 'simulation')
        self.assertTrue(row['imu']['ok'])
        self.assertTrue(row['imu']['simulation'])
        self.assertEqual(row['imu']['placement'], 'arm')
        self.assertGreater(row['shoulder_angle'], 70)

    def test_required_imu_loss_pauses(self):
        pipeline = VisionPipeline('T', 'shoulder_abduction', 'right', 80, 5, 'simulation')
        patient = SimulatedPatient('right', 'abduction')
        patient.set_angle(50)
        snap = patient.skeleton(1.0)
        snap['imu'] = SimulatedArmImu().read_from_angle(1.0, 50, drop=True)
        snap['imu_required'] = True
        row = pipeline.process_snapshot(snap)
        self.assertEqual(row['safety']['level'], 'PAUSE')
        self.assertIn('imu_lost', row['safety']['reasons'])
        self.assertIn('Arm IMU', row['feedback'])


class SafetyImuTests(unittest.TestCase):
    def test_optional_imu_loss_is_warn(self):
        decision = evaluate_safety({'pose_confidence': .9, 'torso_lean': 3, 'imu_lost': True})
        self.assertEqual(decision.level, 'WARN')
        self.assertIn('Arm IMU', coach_message({}, decision))

    def test_required_imu_loss_is_pause(self):
        decision = evaluate_safety({'pose_confidence': .9, 'torso_lean': 3, 'imu_lost': True, 'imu_required': True})
        self.assertEqual(decision.level, 'PAUSE')


class PacketAndUdpTests(unittest.TestCase):
    def test_json_and_csv_parse(self):
        json_packet = parse_line('{"ax":0,"ay":0,"az":9.8,"gx":1,"gy":2,"gz":3,"placement":"arm"}')
        self.assertEqual(json_packet['source'], 'live')
        self.assertAlmostEqual(json_packet['az'], 9.8)
        csv_packet = parse_line('0 0 9.8 1 2 3 arm')
        self.assertEqual(csv_packet['placement'], 'arm')

    def test_udp_roundtrip_is_live(self):
        imu = UdpImu('127.0.0.1', 0)
        try:
            port = imu.sock.getsockname()[1]
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sender.sendto(json.dumps({'ax': 0, 'ay': 0, 'az': 9.81, 'gx': 0, 'gy': 0, 'gz': 12}).encode(),
                          ('127.0.0.1', port))
            sender.close()
            packet = imu.read(1.0)
            self.assertTrue(packet['ok'])
            self.assertEqual(packet['source'], 'live')
            self.assertFalse(packet['simulation'])
            self.assertAlmostEqual(packet['gz'], 12)
        finally:
            imu.close()


class BannerTests(unittest.TestCase):
    def test_dual_simulation_banner(self):
        self.assertIn('NOT LIVE', overlay_banner('simulation', 'simulation'))
        self.assertIn('ARM IMU', overlay_banner('live', 'live'))
        self.assertNotIn('ARM IMU', overlay_banner('live', None))
