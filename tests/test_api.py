import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from backend import server


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.original_data = server.DATA
        server.DATA = Path(cls.temp.name)
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:'+str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        server.DATA = cls.original_data
        cls.temp.cleanup()

    def setUp(self):
        server.ACTIVE.clear()

    def request(self, path, data=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        request = urllib.request.Request(self.base+path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    def start(self, **overrides):
        config = dict(exercise='abduction', side='right', target=80, goal=3, pain=1)
        config.update(overrides)
        return self.request('/api/sessions/start', config)

    def test_session_sample_save_export_roundtrip(self):
        session = self.start()
        self.assertEqual(self.request('/api/active')['id'], session['id'])
        sample = self.request('/api/sessions/sample', session)
        self.assertIn('feedback', sample)
        report = self.request('/api/sessions/finish', dict(**session, pain=2))
        self.assertEqual(report['source'], 'simulation')
        self.assertEqual(report['pain_after'], 2)
        self.assertIsNone(self.request('/api/active'))
        self.assertTrue(any(r['id'] == session['id'] for r in self.request('/api/sessions')))

    def test_bad_configuration_rejected(self):
        for config in [dict(target=500), dict(goal=1.5), dict(pain=-1), dict(side='other'), dict(target=float('nan'))]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.start(**config)
            self.assertEqual(raised.exception.code, 400)

    def test_cross_origin_write_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/sessions/start', {}, origin='https://unrelated.example')
        self.assertEqual(raised.exception.code, 403)

    def test_one_session_at_a_time(self):
        self.start()
        with self.assertRaises(urllib.error.HTTPError):
            self.start()

    def test_unknown_session_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/sessions/sample', dict(id='unknown'))

    def test_health_honest_about_simulation(self):
        health = self.request('/api/health')
        self.assertFalse(health['live_available'])
        self.assertEqual(health['source'], 'simulation')


if __name__ == '__main__':
    unittest.main()
