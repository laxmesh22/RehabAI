import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.knowledge import chunk_count, load_chunks
from agent.orchestrator import run_supervisor
from agent.retrieval.grounding import grounded_or_fallback, ungrounded_reason
from agent.retrieval.knowledge import (
    build_retrieval_query, classify_intent, retrieve, retrieve_for_turn,
)
from agent.session_intel import pacing_hint, rom_trend
from backend import memory as memory_mod
from backend.intake import INTAKE_FIELDS, apply_confirmed_value, empty_intake
from backend.profile import apply_profile_value, empty_profile


class KnowledgeBaseTests(unittest.TestCase):
    def test_chunks_include_education_safety_faq_and_exercises(self):
        chunks = load_chunks()
        cats = {row.get('category') for row in chunks}
        self.assertGreaterEqual(chunk_count(), 20)
        for needed in ('education', 'safety', 'faq', 'encouragement', 'pacing', 'exercise'):
            self.assertIn(needed, cats)
        blob = ' '.join(str(row.get('content') or '') for row in chunks).lower()
        self.assertNotIn('you have frozen shoulder', blob)
        from agent.knowledge import public_ai_status
        status = public_ai_status()
        self.assertTrue(status['llm_loop'])
        self.assertFalse(status['rag'])
        self.assertGreaterEqual(status['knowledge_chunks'], 20)


class RetrievalTests(unittest.TestCase):
    def test_intent_routes_safety_faq_and_encouragement(self):
        self.assertEqual(classify_intent('I have numbness and sudden shooting pain'), 'SAFETY_CONCERN')
        self.assertEqual(classify_intent('is clicking or cracking normal?'), 'GENERAL_QUESTION')
        self.assertEqual(classify_intent('this is not improving, I want to give up'), 'ENCOURAGEMENT_NEEDED')
        self.assertEqual(classify_intent('keep going', scene='measure'), 'EXERCISE_FEEDBACK')

    def test_build_query_includes_movement_gate_and_trend(self):
        query = build_retrieval_query(
            current_movement='abduction',
            action_category='STOP',
            patient_text='numbness in the hand',
            stage='freezing',
            session_trend='plateau',
        )
        self.assertIn('abduction', query)
        self.assertIn('STOP', query)
        self.assertIn('numbness', query)
        self.assertIn('plateau', query)

    def test_clicking_retrieves_faq_chunk(self):
        row = retrieve_for_turn(patient_text='is clicking or cracking normal during this movement', k=3)
        self.assertEqual(row['intent'], 'GENERAL_QUESTION')
        ids = [item['id'] for item in row['chunks']]
        self.assertTrue(any('click' in str(item_id) for item_id in ids) or any(
            'click' in (item.get('title') or '').lower() for item in row['chunks']
        ), ids)

    def test_red_flag_retrieves_safety_chunk(self):
        row = retrieve_for_turn(
            patient_text='sudden severe pain and numbness down the arm',
            action_category='STOP',
            k=3,
        )
        self.assertEqual(row['intent'], 'SAFETY_CONCERN')
        cats = [item['category'] for item in row['chunks']]
        self.assertIn('safety', cats)

    def test_plateau_retrieves_adherence_or_pacing(self):
        hits = retrieve(
            build_retrieval_query(session_trend='plateau', patient_text='ROM has not changed for three sessions'),
            intent='ENCOURAGEMENT_NEEDED',
            pacing_hint='stiffness_led',
            k=3,
        )
        ids = {item['id'] for item in hits}
        self.assertTrue(ids & {'encourage-plateau', 'faq-not-improving', 'pacing-frozen', 'encourage-consistency'})


class GroundingTests(unittest.TestCase):
    def test_rejects_medication_and_invented_numbers(self):
        chunks = [{'content': 'Gentle movement is preferred. This is not a diagnosis.', 'content_hi': ''}]
        self.assertEqual(ungrounded_reason('Take ibuprofen twice a day.', chunks), 'medication_claim')
        self.assertEqual(ungrounded_reason('Your true ROM is 173 degrees.', chunks, {'peak': 90}), 'invented_number')
        self.assertEqual(ungrounded_reason('You have frozen shoulder stage two.', chunks), 'diagnosis_claim')
        spoken, ok, reason = grounded_or_fallback('Peak 90 degrees is what was measured.', chunks, {'peak': 90})
        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertIn('90', spoken)

    def test_pain_score_is_not_a_free_hedge(self):
        chunks = [{'content': 'Gentle movement is preferred. This is not a diagnosis.', 'content_hi': ''}]
        self.assertEqual(
            ungrounded_reason('Your pain today is about 4 out of 10.', chunks),
            'invented_number',
        )
        spoken, ok, reason = grounded_or_fallback('Take 1 slow breath.', chunks)
        self.assertTrue(ok, reason)
        spoken, ok, reason = grounded_or_fallback(
            'Pain at rest 4 out of 10 was stored.', chunks, {'pain_rest': 4},
        )
        self.assertTrue(ok, reason)
        self.assertIn('4', spoken)

    def test_fallback_does_not_diagnose(self):
        chunks = [row for row in load_chunks() if row['id'] == 'faq-clicking']
        spoken, ok, _reason = grounded_or_fallback('', chunks, hindi=False)
        self.assertFalse(ok)
        self.assertIn('not a diagnosis', spoken.lower())
        self.assertNotIn('you have frozen', spoken.lower())
        self.assertNotIn('click', spoken.lower())


class SessionIntelTests(unittest.TestCase):
    def test_pacing_from_pain_and_trend(self):
        self.assertEqual(rom_trend([70, 71, 72]), 'plateau')
        self.assertEqual(rom_trend([70, 80, 90]), 'improving')
        self.assertEqual(pacing_hint(8, 'stable'), 'pain_led')
        self.assertEqual(pacing_hint(2, 'improving'), 'recovering')
        self.assertEqual(pacing_hint(3, 'plateau'), 'stiffness_led')

    def test_insight_does_not_double_count_merged_peak(self):
        from agent.session_intel import record_session_insight
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(memory_mod, 'STORAGE_DIR', Path(tmp.name)):
            memory_mod.save_memory('P-trend', {
                'session_summaries': [
                    {'peak': 90, 'movement': 'abduction'},
                    {'peak': 85, 'movement': 'abduction'},
                ],
            })
            memory_mod.merge_session_into_memory(
                'P-trend', {'peak': 80, 'source': 'simulation'}, 'S-new', 'abduction',
            )
            row = record_session_insight(
                'P-trend', {'peak': 80, 'source': 'simulation', 'reps': 3}, pain_after=3,
            )
        self.assertEqual(row['rom_trend'], 'declining')
        self.assertIn('simulation', row['spoken'].lower())


class SupervisorLlmLoopTests(unittest.TestCase):
    def test_agent_uses_stored_measurements_not_faq(self):
        class _Db:
            def get(self, *_args, **_kwargs):
                return None

        with patch('agent.orchestrator.get_patient_profile', return_value={
            'affected_side': 'right', 'is_demo': False, 'clinician_diagnosis': '',
        }), patch('agent.orchestrator.calculate_patient_progress', return_value={
            'abduction': {'baseline': 72, 'current': 103, 'change': 31},
            'flexion': {'baseline': 94, 'current': 121, 'change': 27},
            'pain_movement': {'baseline': 7, 'current': 4, 'change': -3},
            'torso_compensation': None,
            'demo_records_present': False,
        }), patch('agent.orchestrator.get_latest_assessment', return_value=None), \
             patch('agent.orchestrator.get_assessment_history', return_value=[]), \
             patch('agent.orchestrator.get_rom_history', return_value=[]), \
             patch('agent.orchestrator.get_session_history', return_value=[]), \
             patch('agent.orchestrator.get_compensation_events', return_value=[]), \
             patch('agent.orchestrator.ANTHROPIC_API_KEY', ''), \
             patch('agent.orchestrator.LLM_BASE_URL', ''):
            result = run_supervisor(_Db(), 'P-rag', 'Is clicking during abduction normal?', 'PHYSIOTHERAPIST')
        self.assertFalse(result['llm_used'])
        self.assertFalse(result['rag_used'])
        self.assertFalse(result['citations'])
        self.assertIn('103', result['summary'])
        self.assertNotIn('ibuprofen', result['summary'].lower())
        self.assertNotIn('full_name', str(result))


class ConsumerRagTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(memory_mod, 'STORAGE_DIR', Path(self.tmp.name))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_complete_profile_answers_clicking_from_protocol(self):
        from agent import consumer_voice
        intake = empty_intake()
        for i, fid in enumerate(INTAKE_FIELDS):
            intake = apply_confirmed_value(intake, fid, min(i, 4 if 'difficulty' in fid else 10), 'voice')
        profile = apply_profile_value(apply_profile_value(apply_profile_value(
            empty_profile(), 'full_name', 'Ravi', 'Ravi'), 'age', 40, '40'), 'affected_side', 'right', 'right')
        memory_mod.save_memory('P-rag', {
            'intake': intake, 'profile': profile, 'report_phase': 'skipped',
        })
        with patch.object(consumer_voice, 'ANTHROPIC_API_KEY', ''):
            row = await consumer_voice.consumer_reply(
                'is clicking or cracking normal?', patient_id='P-rag', language='en-IN',
            )
        self.assertEqual(row['engine'], 'consumer-autonomous')
        self.assertNotIn('you have frozen', row['spoken'].lower())
        self.assertNotEqual(row.get('action'), 'open_home')


class LiveRagTests(unittest.IsolatedAsyncioTestCase):
    async def test_safety_intent_pauses_even_when_allow(self):
        from agent import live_voice
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', 'configured'):
            row = await live_voice.live_reply(
                'sudden severe pain and numbness in the hand',
                {'scene': 'measure', 'safety': 'ALLOW', 'shoulder_angle': 40},
            )
        self.assertEqual(row['action'], 'pause')
        self.assertEqual(row['engine'], 'safety-redflag')
        self.assertFalse(row.get('rag_used'))


if __name__ == '__main__':
    unittest.main()
