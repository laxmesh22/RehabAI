"""Consumer Talk agent, memory JSON, and phone-frame fail-closed behaviour."""
from __future__ import annotations

import io
import json
import tempfile
import time
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

    async def test_consumer_opens_without_scripted_questions(self):
        from agent import consumer_voice
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply('', patient_id='P-test', language='en-IN')
        self.assertEqual(row['engine'], 'consumer-autonomous')
        self.assertRegex(row['spoken'].lower(), r'listen|सुन')
        self.assertEqual(row['intake_field'], 'full_name')
        self.assertEqual(row['phase'], 'profile')
        self.assertIsNone(row['parsed'])
        self.assertIn('not a diagnosis', row['spoken'].lower())
        self.assertNotRegex(row['spoken'], r'\b(?:ROM|recovery)\s*\d')
        self.assertNotIn('tell me your name', row['spoken'].lower())

    async def test_numbness_pauses_before_claude(self):
        from agent import consumer_voice
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(consumer_voice, 'claude_voice_turn') as mock:
            row = await consumer_voice.consumer_reply(
                'I have numbness in the hand', patient_id='P-num', language='en-IN',
            )
            mock.assert_not_called()
        self.assertEqual(row['action'], 'pause')
        self.assertEqual(row['engine'], 'consumer-safety')

    async def test_consumer_profile_then_pain(self):
        from agent import consumer_voice
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            a = await consumer_voice.consumer_reply('My name is Ravi Kumar', patient_id='P-prof', language='en-IN')
            self.assertEqual(a['intake_field'], 'age')
            b = await consumer_voice.consumer_reply('forty five', patient_id='P-prof', language='en-IN')
            # "forty five" may not parse — use digits
            if b['intake_field'] == 'age':
                b = await consumer_voice.consumer_reply('45', patient_id='P-prof', language='en-IN')
            self.assertEqual(b['intake_field'], 'affected_side')
            c = await consumer_voice.consumer_reply('right shoulder', patient_id='P-prof', language='en-IN')
        self.assertEqual(c['phase'], 'questionnaire')
        self.assertEqual(c['intake_field'], 'pain_rest')
        self.assertRegex(c['spoken'].lower(), r'listen|सुन')
        self.assertNotIn('Now pain and function', c['spoken'])

    async def test_consumer_saves_number_and_advances_in_one_turn(self):
        from agent import consumer_voice
        from backend.profile import apply_profile_value, empty_profile
        profile = empty_profile()
        profile = apply_profile_value(profile, 'full_name', 'Ravi', 'Ravi')
        profile = apply_profile_value(profile, 'age', 45, '45')
        profile = apply_profile_value(profile, 'affected_side', 'right', 'right')
        memory_mod.save_memory('P-test', {'profile': profile})
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply('four', patient_id='P-test', language='en-IN')
        self.assertFalse(row.get('awaiting_confirm'))
        self.assertEqual(row['intake']['pain_rest'], 4)
        self.assertEqual(row['intake_field'], 'pain_movement')
        self.assertIn('4', row['spoken'])

    async def test_consumer_completes_to_report_phase(self):
        from agent import consumer_voice
        from backend.intake import INTAKE_FIELDS, apply_confirmed_value
        from backend.profile import apply_profile_value, empty_profile
        intake = empty_intake()
        for i, fid in enumerate(INTAKE_FIELDS):
            intake = apply_confirmed_value(intake, fid, min(i, 4 if 'difficulty' in fid else 10), 'voice')
        profile = empty_profile()
        profile = apply_profile_value(profile, 'full_name', 'Ravi', 'Ravi')
        profile = apply_profile_value(profile, 'age', 40, '40')
        profile = apply_profile_value(profile, 'affected_side', 'right', 'right')
        memory_mod.save_memory('P-done', {'intake': intake, 'profile': profile, 'report_phase': 'needed'})
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply('', patient_id='P-done', language='en-IN')
            self.assertEqual(row['action'], 'await_report')
            self.assertEqual(row['phase'], 'report')
            skip = await consumer_voice.consumer_reply('skip', patient_id='P-done', language='en-IN')
        # Skipping the report hands off to the camera baseline, not the dashboard.
        self.assertEqual(skip['action'], 'start_assessment')
        self.assertEqual(skip['phase'], 'baseline')
        self.assertIn('not a diagnosis', skip['spoken'].lower())

    async def test_consumer_dashboard_history_action(self):
        from agent import consumer_voice
        from backend.intake import INTAKE_FIELDS, apply_confirmed_value
        from backend.profile import apply_profile_value, empty_profile
        intake = empty_intake()
        for i, fid in enumerate(INTAKE_FIELDS):
            intake = apply_confirmed_value(intake, fid, min(i, 4 if 'difficulty' in fid else 10), 'voice')
        profile = apply_profile_value(apply_profile_value(apply_profile_value(
            empty_profile(), 'full_name', 'Ravi', 'Ravi'), 'age', 40, '40'), 'affected_side', 'left', 'left')
        memory_mod.save_memory('P-hist', {
            'intake': intake, 'profile': profile, 'report_phase': 'skipped',
            'baseline': {'phase': 'done', 'abduction_deg': 70, 'flexion_deg': 96},
            'session_summaries': [{'peak': 70, 'source': 'simulation', 'movement': 'abduction'}],
        })
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply('show history', patient_id='P-hist', language='en-IN')
        self.assertEqual(row['action'], 'open_history')
        self.assertIn('not a diagnosis', row['spoken'].lower())
        self.assertNotIn('you have frozen', row['spoken'].lower())

    async def test_baseline_is_measured_before_the_dashboard_or_rehab(self):
        from agent import consumer_voice
        from backend.intake import INTAKE_FIELDS, apply_confirmed_value
        from backend.profile import apply_profile_value, empty_profile
        intake = empty_intake()
        for i, fid in enumerate(INTAKE_FIELDS):
            intake = apply_confirmed_value(intake, fid, min(i, 4 if 'difficulty' in fid else 10), 'voice')
        profile = apply_profile_value(apply_profile_value(apply_profile_value(
            empty_profile(), 'full_name', 'Ravi', 'Ravi'), 'age', 40, '40'), 'affected_side', 'right', 'right')
        memory_mod.save_memory('P-base', {
            'intake': intake, 'profile': profile, 'report_phase': 'skipped',
        })
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            # Asking for a rehab session first still routes through the measurement.
            row = await consumer_voice.consumer_reply('start session', patient_id='P-base', language='en-IN')
            self.assertEqual(row['action'], 'start_assessment')
            self.assertEqual(row['phase'], 'baseline')
            # Opening Talk with no speech hands straight off to the camera.
            opened = await consumer_voice.consumer_reply(
                '', patient_id='P-base', language='en-IN', opening=True,
            )
            self.assertEqual(opened['action'], 'start_assessment')
            # Abduction measured, flexion still missing.
            memory_mod.record_baseline_measurement('P-base', 'abduction', 84.0, 'SESSION_A', 'phone')
            mid = await consumer_voice.consumer_reply('start session', patient_id='P-base', language='en-IN')
            self.assertEqual(mid['action'], 'start_assessment')
            self.assertEqual(
                memory_mod.next_baseline_movement(memory_mod.load_memory('P-base')['baseline']), 'flexion',
            )
            # Both measured — rehab is now reachable.
            memory_mod.record_baseline_measurement('P-base', 'flexion', 101.0, 'SESSION_B', 'phone')
            done = await consumer_voice.consumer_reply('start session', patient_id='P-base', language='en-IN')
        self.assertEqual(done['action'], 'start_session')
        self.assertEqual(done['phase'], 'dashboard')

    async def test_baseline_never_invents_a_missing_peak(self):
        memory_mod.save_memory('P-nopeak', {})
        memory_mod.record_baseline_measurement('P-nopeak', 'abduction', None, 'SESSION_X', 'phone')
        row = memory_mod.load_memory('P-nopeak')['baseline']
        self.assertIsNone(row['abduction_deg'])
        self.assertEqual(row['phase'], 'needed')
        self.assertEqual(row['session_ids'], [])
        self.assertFalse(memory_mod.baseline_complete(memory_mod.load_memory('P-nopeak')))

    async def test_llm_turn_is_spoken_instead_of_script(self):
        from agent import consumer_voice
        fake = {
            'spoken': 'Thanks, I am here with you. How does the shoulder feel today?',
            'action': 'none',
            'engine': 'llm-loop',
        }
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(consumer_voice, 'claude_voice_turn', return_value=fake):
            row = await consumer_voice.consumer_reply('', patient_id='P-llm', language='en-IN')
        self.assertEqual(row['spoken'], fake['spoken'])
        self.assertEqual(row['engine'], 'llm-loop')
        self.assertNotIn('tell me your name', row['spoken'].lower())


class AutonomousExtractTests(unittest.TestCase):
    def test_four_is_not_a_name(self):
        from agent.autonomous import extract_volunteered
        from backend.intake import empty_intake
        from backend.profile import apply_profile_value, empty_profile
        empty = extract_volunteered('four', empty_profile(), empty_intake())
        self.assertEqual(empty, [])
        profile = apply_profile_value(apply_profile_value(apply_profile_value(
            empty_profile(), 'full_name', 'Ravi', 'Ravi'), 'age', 45, '45'), 'affected_side', 'right', 'right')
        updates = extract_volunteered('four', profile, empty_intake())
        kinds = [kind for kind, _f, _v in updates]
        self.assertNotIn('profile', kinds)
        self.assertEqual(updates[0][1], 'pain_rest')
        self.assertEqual(updates[0][2], 4)

    def test_cued_name_and_side_from_speech(self):
        from agent.autonomous import extract_volunteered
        from backend.intake import empty_intake
        from backend.profile import empty_profile
        updates = extract_volunteered('My name is Ravi Kumar, right shoulder', empty_profile(), empty_intake())
        fields = {field: value for _k, field, value in updates}
        self.assertIn('Ravi', str(fields.get('full_name')))
        self.assertEqual(fields.get('affected_side'), 'right')
        self.assertNotIn('Shoulder', str(fields.get('full_name')))

    def test_pain_sentence_is_not_a_name(self):
        from agent.autonomous import extract_volunteered
        from backend.intake import empty_intake
        from backend.profile import empty_profile
        updates = extract_volunteered('my right shoulder hurts', empty_profile(), empty_intake())
        fields = {field: value for _k, field, value in updates}
        self.assertNotIn('full_name', fields)
        self.assertEqual(fields.get('affected_side'), 'right')

    def test_age_not_taken_from_rom_sentence(self):
        from agent.autonomous import extract_volunteered
        from backend.intake import empty_intake
        from backend.profile import apply_profile_value, empty_profile
        profile = apply_profile_value(empty_profile(), 'full_name', 'Ravi', 'Ravi')
        updates = extract_volunteered('I reached 90 degrees', profile, empty_intake())
        fields = {field: value for _k, field, value in updates}
        self.assertNotIn('age', fields)
        aged = extract_volunteered('45', profile, empty_intake())
        self.assertEqual({field: value for _k, field, value in aged}.get('age'), 45)

    def test_i_am_forty_five_is_not_a_name(self):
        from agent.autonomous import extract_volunteered
        from backend.intake import empty_intake
        from backend.profile import empty_profile
        updates = extract_volunteered('I am forty five', empty_profile(), empty_intake())
        fields = {field: value for _k, field, value in updates}
        self.assertNotIn('full_name', fields)
        named = extract_volunteered('I am Ravi', empty_profile(), empty_intake())
        self.assertEqual({field: value for _k, field, value in named}.get('full_name'), 'Ravi')


class ClaudeLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_voice_turn_sends_the_utterance(self):
        from agent import autonomous
        capture = {}

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {'content': [{'type': 'text', 'text': json.dumps({
                    'spoken': 'I am here. How does the shoulder feel?',
                    'action': 'none',
                    'save_field': None,
                    'save_value': None,
                    'demo_target': None,
                })}]}

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                capture['json'] = json
                capture['url'] = url
                return _Resp()

        with patch.object(autonomous, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(autonomous.httpx, 'AsyncClient', _Client):
            row = await autonomous.claude_voice_turn({
                'language': 'en-IN',
                'patient_said': 'my right shoulder hurts',
            })
        self.assertEqual(row['engine'], 'llm-loop')
        self.assertIn('shoulder', row['spoken'].lower())
        self.assertIn('system', capture['json'])
        body = capture['json']['messages'][0]['content']
        self.assertIn('my right shoulder hurts', body)
        self.assertNotIn('Tell me your name', row['spoken'])


class PhoneFrameTests(unittest.TestCase):
    def test_phone_frame_without_pose_model_fails_closed(self):
        from backend.services.live_hub import LiveHub
        hub = LiveHub()
        # Pinned off, so a pose sidecar running on the developer's machine cannot
        # turn this into a passing test of the wrong path.
        with patch('backend.services.live_hub.POSE_MODEL', ''), \
             patch('edge.phone_capture.POSE_MODEL', ''), \
             patch('edge.phone_capture.phone_pose_available', return_value=False):
            with self.assertRaises(ValueError) as ctx:
                hub.ingest_phone_frame('missing', b'not-a-jpeg')
            self.assertIn('REHABAI_POSE', str(ctx.exception))

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
        boot = cls.client.post('/api/consumer/bootstrap', json={'full_name': 'Consumer Test'})
        assert boot.status_code == 200, boot.text
        payload = boot.json()
        cls.token = payload['token']
        cls.patient_id = payload['patient_id']
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
        self.assertEqual(me['patient_id'], self.patient_id)
        self.assertFalse(me['is_demo'])
        self.assertIn('memory', me)
        body = {
            'language': 'en-IN',
            'speak': '0',
            'patient_id': self.patient_id,
            'context': json.dumps({'scene': 'consumer'}),
            'text': '',
        }
        from agent import consumer_voice
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            res = self.client.post('/api/voice/agent', headers=self.headers, data=body)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get('spoken'))
        self.assertIn(data.get('engine'), {
            'consumer-fallback', 'consumer-autonomous', 'consumer-prompt', 'consumer-report',
            'consumer-ready', 'consumer-complete', 'consumer-fast', 'consumer-next', 'consumer-reask',
            'consumer-intake-done', 'consumer-profile', 'consumer-profile-next', 'consumer-profile-reask',
            'consumer-profile-done', 'consumer-history', 'consumer-action', 'llm-loop',
        })
        self.assertEqual(data['tts_engine'], 'browser-speech')
        # Numbers must never be invented into spoken on empty open turn without patient words.
        self.assertNotRegex(data['spoken'], r'\b(?:ROM|recovery)\s*\d')

    def test_talk_skip_unlocks_home(self):
        before = self.client.get('/api/consumer/me', headers=self.headers).json()
        self.assertFalse(before.get('home_ready'))
        skip = self.client.post('/api/consumer/talk-skip', headers=self.headers, json={})
        self.assertEqual(skip.status_code, 200, skip.text)
        body = skip.json()
        self.assertTrue(body.get('talk_skipped'))
        self.assertEqual(body.get('action'), 'open_home')
        me = self.client.get('/api/consumer/me', headers=self.headers).json()
        self.assertTrue(me.get('talk_skipped'))
        self.assertTrue(me.get('home_ready'))
        self.assertTrue(me.get('intake_complete'))
        self.assertTrue(me.get('report_complete'))
        self.assertEqual(me.get('phase'), 'dashboard')

    def fresh_consumer(self):
        """Own account per test — these flows mutate the patient record."""
        boot = self.client.post('/api/consumer/bootstrap', json={'full_name': 'Baseline Test'})
        self.assertEqual(boot.status_code, 200, boot.text)
        row = boot.json()
        return row['patient_id'], {'Authorization': 'Bearer ' + row['token']}

    def talked_consumer(self):
        from backend.intake import INTAKE_FIELDS, apply_confirmed_value
        from backend.profile import apply_profile_value, empty_profile
        patient_id, headers = self.fresh_consumer()
        intake = empty_intake()
        for i, fid in enumerate(INTAKE_FIELDS):
            intake = apply_confirmed_value(intake, fid, min(i, 4 if 'difficulty' in fid else 10), 'voice')
        profile = apply_profile_value(apply_profile_value(apply_profile_value(
            empty_profile(), 'full_name', 'Ravi', 'Ravi'), 'age', 41, '41'), 'affected_side', 'right', 'right')
        memory_mod.save_memory(patient_id, {
            'intake': intake, 'profile': profile, 'report_phase': 'skipped',
        })
        return patient_id, headers

    def test_assessment_session_records_a_baseline_and_unlocks_home(self):
        patient_id, headers = self.talked_consumer()
        me = self.client.get('/api/consumer/me', headers=headers).json()
        self.assertFalse(me['home_ready'])
        self.assertEqual(me['phase'], 'baseline')
        self.assertEqual(me['baseline']['next_movement'], 'abduction')
        self.assertEqual(me['baseline']['next_exercise_id'], 'shoulder_abduction')

        peaks = {}
        for movement, exercise in (('abduction', 'shoulder_abduction'), ('flexion', 'shoulder_flexion')):
            start = self.client.post('/api/sessions', headers=headers, json={
                'patient_id': patient_id, 'exercise_id': exercise, 'side': 'right',
                'goal': 1, 'kind': 'assessment', 'capture': 'simulation',
            })
            self.assertEqual(start.status_code, 200, start.text)
            sid = start.json()['id']
            # Intake answered in Talk carries onto the assessment; it is not re-asked.
            self.assertTrue(start.json()['intake']['confirmed'])
            self.client.post(f'/api/sessions/{sid}/confirm', headers=headers, json={})
            time.sleep(0.6)
            finish = self.client.post(f'/api/sessions/{sid}/finish', headers=headers, json={
                'pain_after': 4, 'create_assessment': True,
            })
            self.assertEqual(finish.status_code, 200, finish.text)
            peaks[movement] = finish.json()['session']['peak_angle']
            self.assertEqual(finish.json()['baseline'][f'{movement}_deg'], peaks[movement])

        after = self.client.get('/api/consumer/me', headers=headers).json()
        self.assertTrue(after['home_ready'])
        self.assertEqual(after['phase'], 'dashboard')
        self.assertTrue(after['baseline']['complete'])
        self.assertIsNone(after['baseline']['next_movement'])
        for movement in ('abduction', 'flexion'):
            self.assertEqual(after['baseline'][f'{movement}_deg'], peaks[movement])

    def test_sagittal_flexion_is_allowed_on_the_phone_camera(self):
        patient_id, headers = self.talked_consumer()
        with patch('backend.routers.phone_pose_available', return_value=True), \
             patch('edge.phone_capture.phone_pose_available', return_value=True), \
             patch('edge.phone_capture.PhonePoseEstimator'):
            for exercise, status in (('shoulder_flexion', 200), ('external_rotation', 400)):
                res = self.client.post('/api/sessions', headers=headers, json={
                    'patient_id': patient_id, 'exercise_id': exercise, 'side': 'right',
                    'goal': 1, 'kind': 'assessment', 'capture': 'phone',
                })
                self.assertEqual(res.status_code, status, res.text)

    def test_baseline_skip_records_no_range(self):
        _patient_id, headers = self.talked_consumer()
        skip = self.client.post('/api/consumer/baseline-skip', headers=headers, json={})
        self.assertEqual(skip.status_code, 200, skip.text)
        body = skip.json()
        self.assertEqual(body['action'], 'open_home')
        self.assertEqual(body['baseline']['phase'], 'skipped')
        self.assertIsNone(body['baseline']['abduction_deg'])
        self.assertIsNone(body['baseline']['flexion_deg'])
        me = self.client.get('/api/consumer/me', headers=headers).json()
        self.assertTrue(me['home_ready'])
        self.assertEqual(me['phase'], 'dashboard')

    def test_phone_frame_endpoint_without_model(self):
        start = self.client.post('/api/sessions', headers=self.headers, json={
            'patient_id': self.patient_id, 'exercise_id': 'shoulder_abduction',
            'kind': 'rehab', 'consent_recording': False, 'capture': 'simulation',
        })
        self.assertEqual(start.status_code, 200)
        sid = start.json()['id']
        fake = io.BytesIO(b'\xff\xd8\xff\xd9')
        # The router binds this at import time, so patch it there.
        with patch('backend.routers.phone_pose_available', return_value=False):
            res = self.client.post(
                f'/api/sessions/{sid}/phone-frame',
                headers=self.headers,
                files={'frame': ('frame.jpg', fake, 'image/jpeg')},
            )
        self.assertEqual(res.status_code, 400)
        detail = res.json()['detail']
        self.assertTrue(
            'POSE_MODEL' in detail or 'Simulation' in detail or 'opencv' in detail.lower()
            or 'pose' in detail.lower()
        )


if __name__ == '__main__':
    unittest.main()
