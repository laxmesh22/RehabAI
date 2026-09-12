import unittest
from types import SimpleNamespace

from agent.tools.clinical import _latest_source_series, compare_sessions
from backend.auth import hash_password, verify_password
from backend.routers import render_report_html
from backend.services.live_hub import LiveHub


class IntegrationGuardTests(unittest.TestCase):
    def test_password_check_is_safe_for_valid_and_malformed_hashes(self):
        stored = hash_password('correct')
        self.assertTrue(verify_password('correct', stored))
        self.assertFalse(verify_password('wrong', stored))
        self.assertFalse(verify_password('anything', 'malformed'))

    def test_progress_series_uses_only_latest_measurement_protocol(self):
        rows = [
            {'source': 'simulation', 'value': 72},
            {'source': 'simulation', 'value': 103},
            {'source': 'phone', 'value': 81},
            {'source': 'phone', 'value': 89},
        ]
        selected, source, available = _latest_source_series(rows)
        self.assertEqual(source, 'phone')
        self.assertEqual([row['value'] for row in selected], [81, 89])
        self.assertEqual(available, ['simulation', 'phone'])

    def test_session_delta_rejects_different_capture_sources(self):
        def row(session_id, source):
            return SimpleNamespace(
                id=session_id, patient_id='P1', exercise_id='shoulder_abduction', side='right',
                status='complete', started_at=SimpleNamespace(isoformat=lambda: '2026-01-01T00:00:00'),
                ended_at=None, source=source, model_version='v1', reps=5, invalid_reps=0,
                peak_angle=90 if source == 'simulation' else 95, coverage=95, pain_before=5,
                pain_after=4, target=90, goal=5, safety_outcome='ALLOW', is_demo=True,
                consent_recording=False,
            )
        rows = {'A': row('A', 'simulation'), 'B': row('B', 'phone')}
        result = compare_sessions(SimpleNamespace(get=lambda _model, key: rows.get(key)), 'A', 'B')
        self.assertFalse(result['comparison_valid'])
        self.assertIsNone(result['peak_change'])
        self.assertIn('measurement source', result['comparison_reason'])

    def test_live_history_cursor_handles_trimmed_prefix(self):
        hub = LiveHub()
        hub.sessions['S1'] = {
            'history': [{'sample': 1000}, {'sample': 1001}, {'sample': 1002}],
            'history_offset': 1000,
            'preview_b64': None,
        }
        items, next_sequence = hub.history_since('S1', 1001)
        self.assertEqual(items, [{'sample': 1001}, {'sample': 1002}])
        self.assertEqual(next_sequence, 1003)

    def test_report_html_escapes_stored_text(self):
        patient = SimpleNamespace(
            full_name='<img src=x onerror=alert(1)>', id='P1', affected_side='right', is_demo=False,
        )
        report = SimpleNamespace(
            payload={'summary': '<script>alert(1)</script>', 'measured_changes': {'x': '<b>bad</b>'}},
            approved_by=None,
        )
        output = render_report_html(patient, report)
        self.assertNotIn('<script>', output)
        self.assertNotIn('<img src=x', output)
        self.assertIn('&lt;script&gt;', output)


if __name__ == '__main__':
    unittest.main()
