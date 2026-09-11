import tempfile
import time
import unittest
from pathlib import Path
from backend.database.session import rebind
from backend.services.live_hub import HUB


class PlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        url = 'sqlite:///' + Path(cls.temp.name).joinpath('platform.db').as_posix()
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
        login = cls.client.post('/api/auth/login', json={'email': 'priya.mehta@hospital.local', 'password': 'rehabai-demo'})
        cls.token = login.json()['token']
        cls.headers = {'Authorization': 'Bearer ' + cls.token}

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)
        from backend.database.session import engine
        if engine is not None:
            engine.dispose()
        cls.temp.cleanup()

    def test_health_distinguishes_simulation(self):
        health = self.client.get('/api/health').json()
        self.assertEqual(health['source'], 'simulation')
        self.assertFalse(health['live_available'])
        self.assertFalse(health['llm_available'])
        self.assertIn('session_metrics', health['tables'])
        self.assertIn(health['frame_encoder'], ('pillow', 'opencv'))
        self.assertEqual(health['imu']['transport'], 'simulation')
        self.assertEqual(health['imu']['placements'], ['arm'])
        self.assertEqual(health['imu']['role'], 'kinematics_and_quality_not_diagnosis')
        self.assertFalse(health['imu']['live_available'])

    def test_patient_cannot_see_other_caseload_as_physio_demo_patient_exists(self):
        rows = self.client.get('/api/patients', headers=self.headers).json()
        self.assertTrue(any(p['id'] == 'P102' and p['is_demo'] for p in rows))

    def test_agent_compares_seeded_assessments_without_inventing(self):
        res = self.client.post('/api/agent/query', headers=self.headers,
                               json={'patient_id': 'P102', 'question': 'Compare this assessment with the previous session.'})
        body = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertIn('abduction', body['measured_changes'])
        self.assertEqual(body['measured_changes']['abduction']['baseline'], 72)
        self.assertEqual(body['measured_changes']['abduction']['current'], 103)
        self.assertFalse(body['llm_used'])
        self.assertIn('synthetic', body['summary'].lower() + ' demo')

    def test_patient_role_is_isolated(self):
        token = self.client.post('/api/auth/login', json={'email': 'ananya.sharma@demo.local', 'password': 'rehabai-demo'}).json()['token']
        rows = self.client.get('/api/patients', headers={'Authorization': 'Bearer ' + token}).json()
        self.assertEqual([p['id'] for p in rows], ['P102'])

    def test_admin_cannot_start_session(self):
        token = self.client.post('/api/auth/login', json={'email': 'admin@hospital.local', 'password': 'rehabai-demo'}).json()['token']
        res = self.client.post('/api/sessions', headers={'Authorization': 'Bearer ' + token},
                               json={'patient_id': 'P102', 'exercise_id': 'shoulder_abduction'})
        self.assertEqual(res.status_code, 403)

    def test_live_simulation_session_counts_and_stores(self):
        start = self.client.post('/api/sessions', headers=self.headers, json={
            'patient_id': 'P102', 'exercise_id': 'shoulder_abduction', 'side': 'right',
            'target': 80, 'goal': 1, 'kind': 'assessment', 'consent_recording': True,
        })
        self.assertEqual(start.status_code, 200, start.text)
        sid = start.json()['id']
        blocked = self.client.post('/api/sessions/' + sid + '/confirm', headers=self.headers, json={})
        self.assertEqual(blocked.status_code, 400, blocked.text)
        intake = self.client.post('/api/sessions/' + sid + '/intake', headers=self.headers, json={
            'source': 'tap', 'language': 'en-IN',
            'pain_rest': 3, 'pain_movement': 6,
            'difficulty_dressing': 2, 'difficulty_grooming': 1,
            'difficulty_overhead': 3, 'difficulty_behind_back': 4,
        })
        self.assertEqual(intake.status_code, 200, intake.text)
        self.assertTrue(intake.json()['intake']['confirmed'])
        self.assertEqual(intake.json()['pain_before'], 3)
        ready = False
        for _ in range(40):
            snap = self.client.get('/api/sessions/' + sid, headers=self.headers).json()
            cal = (snap.get('live') or {}).get('telemetry') or {}
            if cal.get('calibration', {}).get('ready'):
                ready = True
                imu = cal.get('imu') or {}
                self.assertEqual(imu.get('source'), 'simulation')
                self.assertTrue(imu.get('ok'))
                self.assertEqual(cal.get('sensors', {}).get('imu'), 'simulation')
                break
            time.sleep(0.1)
        self.assertTrue(ready)
        confirm = self.client.post('/api/sessions/' + sid + '/confirm', headers=self.headers, json={})
        self.assertEqual(confirm.status_code, 200, confirm.text)
        self.client.post('/api/sessions/' + sid + '/fault', headers=self.headers, json={'fault': 'lean'})
        leaned = False
        for _ in range(20):
            snap = self.client.get('/api/sessions/' + sid, headers=self.headers).json()
            tel = (snap.get('live') or {}).get('telemetry') or {}
            if tel.get('torso_lean', 0) > 10:
                leaned = True
                self.assertIn('Torso compensation', tel.get('feedback', ''))
                break
            time.sleep(0.1)
        self.assertTrue(leaned)
        self.client.post('/api/sessions/' + sid + '/fault', headers=self.headers, json={'fault': 'none'})
        reps = 0
        for _ in range(80):
            snap = self.client.get('/api/sessions/' + sid, headers=self.headers).json()
            tel = (snap.get('live') or {}).get('telemetry') or {}
            reps = tel.get('rep') or 0
            if reps >= 1:
                break
            time.sleep(0.15)
        self.assertGreaterEqual(reps, 1)
        missing = self.client.post('/api/sessions/' + sid + '/finish', headers=self.headers, json={
            'pain_after': 4, 'create_assessment': True,
            'pain_rest': None,
        })
        # intake already stored; finish should use it even if body omits function fields
        finish = missing
        self.assertEqual(finish.status_code, 200, finish.text)
        self.assertEqual(finish.json()['session']['source'], 'simulation')
        self.assertGreaterEqual(finish.json()['session']['reps'], 1)
        self.assertIsNotNone(finish.json()['assessment_id'])
        assessments = self.client.get('/api/patients/P102/assessments', headers=self.headers).json()
        latest = assessments[0]
        self.assertEqual(latest['pain_rest'], 3)
        self.assertEqual(latest['pain_movement'], 6)
        self.assertEqual(latest['difficulty_dressing'], 2)
        self.assertEqual(latest['difficulty_grooming'], 1)
        self.assertEqual(latest['difficulty_overhead'], 3)
        self.assertEqual(latest['difficulty_behind_back'], 4)
        self.assertNotEqual(latest['difficulty_dressing'], 1)
        metrics = self.client.get('/api/sessions/' + sid + '/metrics', headers=self.headers)
        self.assertEqual(metrics.status_code, 200, metrics.text)
        self.assertTrue(metrics.json())
        self.assertGreater(metrics.json()[0]['payload']['sample_count'], 0)
        self.assertTrue(metrics.json()[0]['payload']['summary']['imu']['enabled'])
        self.assertEqual(metrics.json()[0]['payload']['summary']['imu']['source'], 'simulation')
        progress = self.client.get('/api/patients/P102/progress', headers=self.headers).json()
        self.assertEqual(progress['abduction']['baseline'], 72)

    def test_assessment_finish_without_intake_is_rejected(self):
        start = self.client.post('/api/sessions', headers=self.headers, json={
            'patient_id': 'P102', 'exercise_id': 'shoulder_abduction', 'kind': 'assessment', 'goal': 1,
        })
        sid = start.json()['id']
        parsed = self.client.post('/api/intake/parse', headers=self.headers, json={
            'field': 'pain_rest', 'text': 'four out of ten',
        })
        self.assertEqual(parsed.status_code, 200, parsed.text)
        self.assertEqual(parsed.json()['parsed_value'], 4)
        finish = self.client.post('/api/sessions/' + sid + '/finish', headers=self.headers, json={
            'pain_after': 4, 'create_assessment': True,
        })
        self.assertEqual(finish.status_code, 400, finish.text)
        self.assertIn('intake', finish.json()['detail'].lower())

    def tearDown(self):
        for sid in list(HUB.sessions):
            try:
                HUB.finish(sid)
            except Exception:
                pass
