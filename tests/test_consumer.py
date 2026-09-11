"""Consumer Talk agent, memory JSON, and phone-frame fail-closed behaviour."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import memory as memory_mod
from backend.intake import empty_intake
from agent.studio_browser import normalize_action
from agent.retrieval import sanitize_memory_slice


class ConsumerMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)
        self.patcher = patch.object(memory_mod, 'STORAGE_DIR', self.storage)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_memory_round_trip_without_identity(self):
        row = memory_mod.save_memory('P102', {
            'intake': {
                **empty_intake(),
                'pain_rest': 3,
                'fields': {'pain_rest': {'value': 3, 'source': 'voice'}},
            },
            'last_peak_abduction': 88,
            'last_source': 'simulation',
        })
        loaded = memory_mod.load_memory('P102')
        self.assertEqual(loaded['last_peak_abduction'], 88)
        self.assertEqual(loaded['intake']['pain_rest'], 3)
        self.assertTrue((self.storage / 'patients' / 'P102' / 'memory.json').is_file())
        safe = sanitize_memory_slice(loaded)
        blob = json.dumps(safe)
        self.assertNotIn('full_name', blob)
        self.assertNotIn('mrn', blob)
        self.assertNotIn('P102', blob)
        self.assertEqual(safe['last_peak_abduction'], 88)

    def test_open_assistant_still_rejected(self):
        self.assertEqual(normalize_action('open_assistant'), 'none')
        self.assertEqual(normalize_action('open_home'), 'open_home')


class ConsumerVoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)
        self.patcher = patch.object(memory_mod, 'STORAGE_DIR', self.storage)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_consumer_asks_first_question_without_inventing(self):
        from agent import consumer_voice
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply('', patient_id='P-test', language='en-IN')
        self.assertEqual(row['engine'], 'consumer-fast')
        self.assertIn('Pain at rest', row['spoken'])
        self.assertEqual(row['intake_field'], 'pain_rest')
        self.assertIsNone(row['parsed'])
        self.assertNotIn('diagnosis', row['spoken'].lower())

    async def test_consumer_saves_number_and_advances_in_one_turn(self):
        from agent import consumer_voice
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply('four', patient_id='P-test', language='en-IN')
        self.assertFalse(row.get('awaiting_confirm'))
        self.assertEqual(row['intake']['pain_rest'], 4)
        self.assertEqual(row['intake_field'], 'pain_movement')
        self.assertIn('4', row['spoken'])
        self.assertTrue('moving' in row['spoken'].lower() or 'Pain while' in row['spoken'])

    async def test_consumer_completes_to_report_phase(self):
        from agent import consumer_voice
        from backend.intake import INTAKE_FIELDS, apply_confirmed_value
        intake = empty_intake()
        for i, fid in enumerate(INTAKE_FIELDS):
            intake = apply_confirmed_value(intake, fid, min(i, 4 if 'difficulty' in fid else 10), 'voice')
        memory_mod.save_memory('P-done', {'intake': intake, 'report_phase': 'needed'})
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply('', patient_id='P-done', language='en-IN')
        self.assertEqual(row['action'], 'await_report')
        self.assertEqual(row['phase'], 'report')
        skip = await consumer_voice.consumer_reply('skip', patient_id='P-done', language='en-IN')
        self.assertEqual(skip['action'], 'open_home')
        self.assertIn('not a diagnosis', skip['spoken'].lower())


class PhoneFrameTests(unittest.TestCase):
    def test_phone_frame_without_pose_model_fails_closed(self):
        from backend.services.live_hub import LiveHub
        hub = LiveHub()
        with patch('backend.services.live_hub.POSE_MODEL', ''), \
             patch('edge.phone_capture.POSE_MODEL', ''):
            with self.assertRaises(ValueError) as ctx:
                hub.ingest_phone_frame('missing', b'not-a-jpeg')
            self.assertIn('REHABAI_POSE_MODEL', str(ctx.exception))

    def test_simulation_rejects_phone_frames(self):
        from backend.services.live_hub import LiveHub
        hub = LiveHub()
        with patch('backend.services.live_hub.POSE_MODEL', str(Path(__file__).resolve())), \
             patch('edge.phone_capture.phone_pose_available', return_value=True):
            # Start simulation session directly.
            with patch('backend.services.live_hub.MEASUREMENT_SOURCE', 'simulation'):
                hub.start('S1', 'P102', 'shoulder_abduction', 'right', 80, 5, 'simulation', False)
            with self.assertRaises(ValueError) as ctx:
                hub.ingest_phone_frame('S1', b'\xff\xd8\xff')
            self.assertIn('Simulation', str(ctx.exception))
            hub.finish('S1')


class ConsumerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        url = 'sqlite:///' + Path(cls.temp.name).joinpath('consumer.db').as_posix()
        from backend.database.session import rebind
        rebind(url)
        from backend.database.session import init_db, SessionLocal
        from backend.seed import seed_if_empty
        init_db()
        db = SessionLocal()
        try:
            seed_if_empty(db)
            db.commit()
        finally:
            db.close()
        from fastapi.testclient import TestClient
        from backend.main import app
        cls._cm = TestClient(app)
        cls.client = cls._cm.__enter__()
        login = cls.client.post('/api/auth/login', json={
            'email': 'ananya.sharma@demo.local', 'password': 'rehabai-demo',
        })
        cls.token = login.json()['token']
        cls.headers = {'Authorization': 'Bearer ' + cls.token}

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)
        from backend.database.session import engine
        if engine is not None:
            engine.dispose()
        cls.temp.cleanup()

    def test_consumer_me_and_voice_agent(self):
        me = self.client.get('/api/consumer/me', headers=self.headers).json()
        self.assertEqual(me['patient_id'], 'P102')
        self.assertTrue(me['is_demo'])
        self.assertIn('memory', me)
        body = {
            'language': 'en-IN',
            'speak': '0',
            'patient_id': 'P102',
            'context': json.dumps({'scene': 'consumer'}),
            'text': '',
        }
        res = self.client.post('/api/voice/agent', headers=self.headers, data=body)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get('spoken'))
        self.assertIn(data.get('engine'), {
            'consumer-fallback', 'consumer-autonomous', 'consumer-prompt', 'consumer-report',
            'consumer-ready', 'consumer-complete', 'consumer-fast', 'consumer-next', 'consumer-reask',
            'consumer-intake-done',
        })
        self.assertEqual(data['tts_engine'], 'browser-speech')
        # Numbers must never be invented into spoken on empty open turn without patient words.
        self.assertNotRegex(data['spoken'], r'\b(?:ROM|recovery)\s*\d')

    def test_phone_frame_endpoint_without_model(self):
        start = self.client.post('/api/sessions', headers=self.headers, json={
            'patient_id': 'P102', 'exercise_id': 'shoulder_abduction',
            'kind': 'rehab', 'consent_recording': False, 'capture': 'simulation',
        })
        self.assertEqual(start.status_code, 200)
        sid = start.json()['id']
        fake = io.BytesIO(b'\xff\xd8\xff\xd9')
        res = self.client.post(
            f'/api/sessions/{sid}/phone-frame',
            headers=self.headers,
            files={'frame': ('frame.jpg', fake, 'image/jpeg')},
        )
        self.assertEqual(res.status_code, 400)
        detail = res.json()['detail']
        self.assertTrue('POSE_MODEL' in detail or 'Simulation' in detail)


if __name__ == '__main__':
    unittest.main()
