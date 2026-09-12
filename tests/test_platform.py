import tempfile
import time
import unittest
import io
import zipfile
from pathlib import Path
from backend.database.session import rebind
from backend.services.live_hub import HUB


def _register_patient(client, headers, name='Clinic Patient'):
    mrn = 'MRN-' + str(time.time_ns())
    created = client.post('/api/patients', headers=headers, json={
        'full_name': name, 'mrn': mrn, 'date_of_birth': '1980-01-15',
        'sex': 'F', 'affected_side': 'right', 'clinician_diagnosis': '',
    })
    assert created.status_code == 200, created.text
    return created.json()['id']


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
        cls.patient_id = _register_patient(cls.client, cls.headers)

    def tearDown(self):
        for sid in list(getattr(HUB, 'sessions', {}) or {}):
            try:
                HUB.finish(sid)
            except Exception:
                pass

    @classmethod
    def tearDownClass(cls):
        for sid in list(getattr(HUB, 'sessions', {}) or {}):
            try:
                HUB.finish(sid)
            except Exception:
                pass
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
        self.assertEqual(health['guide']['driven_by'], 'telemetry_not_llm')
        self.assertEqual(health['guide']['rig'], 'follow_avatar')
        self.assertIn('voice', health)
        self.assertIn(health['voice']['stt'], ('sarvam-saaras', 'elevenlabs-scribe', 'offline-windows-speech'))
        self.assertIn(health['voice']['tts'], ('elevenlabs', 'sarvam-bulbul', 'browser-speech'))
        self.assertIn('offline-windows-speech', health['voice']['stt_order'])
        self.assertEqual(health['voice']['conversation'], 'continuous_until_end')
        self.assertEqual(health['voice']['browser_use']['privacy'], 'no_dom_or_patient_identifiers_to_llm')

    def test_seed_has_no_fabricated_demo_patient(self):
        rows = self.client.get('/api/patients', headers=self.headers).json()
        self.assertFalse(any(p.get('is_demo') for p in rows))
        self.assertFalse(any(p.get('id') == 'P102' for p in rows))
        denied = self.client.post('/api/auth/login', json={
            'email': 'ananya.sharma@demo.local', 'password': 'rehabai-demo',
        })
        self.assertEqual(denied.status_code, 401)

    def test_dashboard_series_matches_stored_rom(self):
        pid = self.patient_id
        for abd, flex, rest, move in ((72, 94, 4, 7), (103, 121, 2, 4)):
            res = self.client.post(f'/api/patients/{pid}/assessment', headers=self.headers, json={
                'affected_side': 'right', 'abduction_max': abd, 'flexion_max': flex,
                'pain_rest': rest, 'pain_movement': move, 'avg_confidence': 0.9,
                'notes': 'Test-entered values',
            })
            self.assertEqual(res.status_code, 200, res.text)
        dash = self.client.get('/api/dashboard', headers=self.headers).json()
        self.assertTrue(dash.get('focus_patient'))
        self.assertTrue(dash.get('abduction_series'))
        focus_id = dash['focus_patient']['id']
        rom = self.client.get(f'/api/patients/{focus_id}/rom', headers=self.headers).json()
        abd = [row['value'] for row in rom if row['movement'] == 'abduction']
        self.assertEqual([row['value'] for row in dash['abduction_series']], abd)
        self.assertEqual(dash['progress']['abduction']['baseline'], abd[0])
        self.assertEqual(dash['progress']['abduction']['current'], abd[-1])
        pain = self.client.get(f'/api/patients/{focus_id}/pain', headers=self.headers).json()
        self.assertEqual(len(dash['pain_series']), len(pain))

    def test_agent_compares_stored_assessments_without_inventing(self):
        pid = _register_patient(self.client, self.headers, 'Compare Patient')
        for abd, flex, rest, move in ((72, 94, 4, 7), (103, 121, 2, 4)):
            res = self.client.post(f'/api/patients/{pid}/assessment', headers=self.headers, json={
                'affected_side': 'right', 'abduction_max': abd, 'flexion_max': flex,
                'pain_rest': rest, 'pain_movement': move, 'avg_confidence': 0.91,
            })
            self.assertEqual(res.status_code, 200, res.text)
        from unittest.mock import patch
        with patch('agent.orchestrator.ANTHROPIC_API_KEY', ''), patch('agent.orchestrator.LLM_BASE_URL', ''):
            res = self.client.post('/api/agent/query', headers=self.headers,
                                   json={'patient_id': pid, 'question': 'Compare this assessment with the previous session.'})
        body = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertIn('abduction', body['measured_changes'])
        self.assertEqual(body['measured_changes']['abduction']['baseline'], 72)
        self.assertEqual(body['measured_changes']['abduction']['current'], 103)
        self.assertFalse(body['llm_used'])
        self.assertFalse(body.get('rag_used'))
        self.assertFalse(body.get('citations'))

    def test_patient_role_is_isolated(self):
        boot = self.client.post('/api/consumer/bootstrap', json={'full_name': 'Isolated Patient'})
        self.assertEqual(boot.status_code, 200, boot.text)
        token = boot.json()['token']
        pid = boot.json()['patient_id']
        rows = self.client.get('/api/patients', headers={'Authorization': 'Bearer ' + token}).json()
        self.assertEqual([p['id'] for p in rows], [pid])
        self.assertFalse(rows[0].get('is_demo'))

    def test_consumer_bootstrap_appears_on_admin_caseload(self):
        boot = self.client.post('/api/consumer/bootstrap', json={'full_name': 'App Signup Visible'})
        self.assertEqual(boot.status_code, 200, boot.text)
        pid = boot.json()['patient_id']
        email = boot.json()['email']
        password = boot.json()['password']

        admin_token = self.client.post(
            '/api/auth/login',
            json={'email': 'admin@hospital.local', 'password': 'rehabai-demo'},
        ).json()['token']
        admin_headers = {'Authorization': 'Bearer ' + admin_token}
        patients = self.client.get('/api/patients', headers=admin_headers).json()
        hit = next(p for p in patients if p['id'] == pid)
        self.assertEqual(hit['account_source'], 'consumer')
        self.assertTrue(hit['has_app_login'])
        self.assertEqual(hit['full_name'], 'App Signup Visible')
        self.assertIsNotNone(hit.get('last_login_at'))

        dash = self.client.get('/api/dashboard', headers=admin_headers).json()
        self.assertGreaterEqual(dash.get('consumer_patients', 0), 1)
        self.assertTrue(any(row['patient_id'] == pid for row in dash.get('recent_consumer_logins') or []))

        # Password login is audited and refreshes last_login for the admin feed.
        again = self.client.post('/api/auth/login', json={'email': email, 'password': password})
        self.assertEqual(again.status_code, 200, again.text)
        detail = self.client.get(f'/api/patients/{pid}', headers=admin_headers).json()
        self.assertEqual(detail['account_source'], 'consumer')
        self.assertTrue(any(a['action'] in ('login', 'consumer_bootstrap') for a in detail.get('recent_activity') or []))

        touch = self.client.post('/api/auth/touch', headers={'Authorization': 'Bearer ' + boot.json()['token']}, json={})
        self.assertEqual(touch.status_code, 200, touch.text)
        self.assertEqual(touch.json().get('patient_id'), pid)
    def test_patient_registration_and_exports(self):
        mrn = 'MRN-VOICE-' + str(time.time_ns())
        created = self.client.post('/api/patients', headers=self.headers, json={
            'full_name': 'Voice Intake Test', 'mrn': mrn, 'date_of_birth': '1985-04-03',
            'sex': 'Female', 'affected_side': 'left', 'clinician_diagnosis': '',
        })
        self.assertEqual(created.status_code, 200, created.text)
        patient_id = created.json()['id']
        json_export = self.client.get(f'/api/patients/{patient_id}/export.json', headers=self.headers)
        self.assertEqual(json_export.status_code, 200, json_export.text)
        payload = json_export.json()
        self.assertEqual(payload['schema_version'], 'rehabai.patient-export.v1')
        self.assertEqual(payload['patient']['mrn'], mrn)
        self.assertIn('questionnaire_sessions', payload)
        xlsx = self.client.get(f'/api/patients/{patient_id}/export.xlsx', headers=self.headers)
        self.assertEqual(xlsx.status_code, 200, xlsx.text)
        self.assertTrue(xlsx.content.startswith(b'PK'))
        with zipfile.ZipFile(io.BytesIO(xlsx.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('xl/worksheets/sheet8.xml', archive.namelist())

    def test_admin_cannot_start_session(self):
        token = self.client.post('/api/auth/login', json={'email': 'admin@hospital.local', 'password': 'rehabai-demo'}).json()['token']
        res = self.client.post('/api/sessions', headers={'Authorization': 'Bearer ' + token},
                               json={'patient_id': self.patient_id, 'exercise_id': 'shoulder_abduction'})
        self.assertEqual(res.status_code, 403)

    def test_live_simulation_session_counts_and_stores(self):
        pid = self.patient_id
        start = self.client.post('/api/sessions', headers=self.headers, json={
            'patient_id': pid, 'exercise_id': 'shoulder_abduction', 'side': 'right',
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
                self.assertEqual(cal.get('guide', {}).get('driven_by'), 'telemetry_not_llm')
                self.assertEqual(cal['guide']['guide_torso_deg'], 0)
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
        finish = missing
        self.assertEqual(finish.status_code, 200, finish.text)
        self.assertEqual(finish.json()['session']['source'], 'simulation')
        self.assertGreaterEqual(finish.json()['session']['reps'], 1)
        self.assertIsNotNone(finish.json()['assessment_id'])
        self.assertIn('peak', finish.json()['summary'])
        self.assertEqual(finish.json()['summary']['source'], 'simulation')
        assessments = self.client.get(f'/api/patients/{pid}/assessments', headers=self.headers).json()
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
        progress = self.client.get(f'/api/patients/{pid}/progress', headers=self.headers).json()
        rom = self.client.get(f'/api/patients/{pid}/rom', headers=self.headers).json()
        abd = [row for row in rom if row['movement'] == 'abduction']
        pain = self.client.get(f'/api/patients/{pid}/pain', headers=self.headers).json()
        self.assertEqual(progress['abduction']['current'], abd[-1]['value'])
        self.assertEqual(abd[-1]['value'], finish.json()['session']['peak_angle'])
        self.assertEqual(pain[-1]['movement'], 4)
        dash = self.client.get('/api/dashboard', headers=self.headers).json()
        # Focus patient may be another registered row; assert this patient's series via direct APIs.
        self.assertTrue(abd)

    def test_assessment_finish_without_intake_is_rejected(self):
        start = self.client.post('/api/sessions', headers=self.headers, json={
            'patient_id': self.patient_id, 'exercise_id': 'shoulder_abduction', 'kind': 'assessment', 'goal': 1,
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

    def test_new_session_abandons_previous_calibrating_row(self):
        payload = {
            'patient_id': self.patient_id, 'exercise_id': 'shoulder_abduction',
            'side': 'right', 'target': 80, 'goal': 1, 'kind': 'rehab',
            'consent_recording': False, 'capture': 'simulation',
        }
        first = self.client.post('/api/sessions', headers=self.headers, json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        aid = first.json()['id']
        second = self.client.post('/api/sessions', headers=self.headers, json=payload)
        self.assertEqual(second.status_code, 200, second.text)
        old = self.client.get('/api/sessions/' + aid, headers=self.headers).json()
        self.assertEqual(old['status'], 'failed')
        self.assertIsNone(old.get('live'))
        self.assertNotIn(aid, HUB.sessions)
        bid = second.json()['id']
        self.assertIn(bid, HUB.sessions)
        paused = self.client.post('/api/sessions/' + bid + '/pause', headers=self.headers, json={})
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()['status'], 'paused')
        self.assertIn(bid, HUB.sessions)

    def test_guide_caption_stays_deterministic_without_llm(self):
        res = self.client.post('/api/guide/caption', headers=self.headers, json={
            'cue': 'Raise your arm slowly.', 'phase': 'ascending', 'safety': 'ALLOW', 'movement': 'abduction',
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()['caption'], 'Raise your arm slowly.')
        self.assertFalse(res.json()['llm_used'])
        self.assertFalse(res.json()['bones_from_llm'])

    def test_voice_agent_strips_identity_and_honours_block(self):
        import json
        blocked = self.client.post('/api/voice/agent', headers=self.headers, data={
            'text': 'keep going',
            'language': 'en-IN',
            'speak': '0',
            'context': json.dumps({'scene': 'measure', 'safety': 'BLOCK', 'patient_id': self.patient_id, 'mrn': 'secret'}),
        })
        self.assertEqual(blocked.status_code, 200, blocked.text)
        body = blocked.json()
        self.assertEqual(body['action'], 'pause')
        self.assertIn('stop', body['spoken'].lower())
        self.assertNotIn(self.patient_id, body['spoken'])
        self.assertIsNone(body.get('audio_base64'))
        self.assertEqual(body.get('tts_engine'), 'browser-speech')

        scored = self.client.post('/api/voice/agent', headers=self.headers, data={
            'text': 'four',
            'language': 'en-IN',
            'speak': '0',
            'context': json.dumps({'scene': 'intake', 'intake_field': 'pain_rest'}),
        })
        self.assertEqual(scored.status_code, 200, scored.text)
        parsed = scored.json()['parsed']
        self.assertEqual(parsed['parsed_value'], 4)
        self.assertEqual(parsed['engine'], 'deterministic')
        self.assertEqual(parsed['intent'], 'number')

        ended = self.client.post('/api/voice/agent', headers=self.headers, data={
            'text': 'goodbye that is all',
            'language': 'en-IN',
            'speak': '0',
            'context': json.dumps({'scene': 'clinic'}),
        })
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(ended.json()['action'], 'end')


if __name__ == '__main__':
    unittest.main()
