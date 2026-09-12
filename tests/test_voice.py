import base64
import io
import json
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from backend import voice
from backend.config import _sarvam_speaker


def _wav(seconds=0.1, rate=16000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b'\x00\x00' * int(seconds * rate))
    return out.getvalue()


class FakeResponse:
    def __init__(self, payload, status=200, content=b''):
        self.payload = payload
        self.status_code = status
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise voice.httpx.HTTPStatusError('provider failed', request=None, response=None)

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response, capture):
        self.response = response
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.capture.update({'url': url, **kwargs})
        return self.response


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        voice.reset_tts_state()

    def tearDown(self):
        voice.reset_tts_state()
    async def test_explicit_number_stays_deterministic(self):
        with patch.object(voice, 'ANTHROPIC_API_KEY', 'configured'):
            row = await voice.parse_questionnaire_reply('four out of ten', 'pain_rest')
        self.assertEqual(row['parsed_value'], 4)
        self.assertEqual(row['engine'], 'deterministic')

    async def test_claude_cannot_invent_a_numeric_score(self):
        capture = {}
        payload = {'content': [{'type': 'text', 'text': json.dumps({
            'intent': 'number', 'parsed_value': 8, 'spoken': 'Eight.',
        })}]}
        fake = FakeClient(FakeResponse(payload), capture)
        with patch.object(voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            row = await voice.parse_questionnaire_reply('it feels very bad', 'pain_rest')
        self.assertEqual(row['intent'], 'unknown')
        self.assertIsNone(row['parsed_value'])
        sent = json.loads(capture['json']['messages'][0]['content'])
        self.assertNotIn('patient_id', sent)
        self.assertNotIn('full_name', sent)

    async def test_claude_can_clarify_confirmation_without_patient_identity(self):
        capture = {}
        payload = {'content': [{'type': 'text', 'text': json.dumps({
            'intent': 'confirm_yes', 'parsed_value': None, 'spoken': 'Saved.',
        })}]}
        fake = FakeClient(FakeResponse(payload), capture)
        with patch.object(voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            row = await voice.parse_questionnaire_reply('that is correct please', 'pain_rest', True)
        self.assertEqual(row['intent'], 'confirm_yes')
        self.assertEqual(row['engine'], 'claude-structured')

    async def test_sarvam_stt_request_and_response(self):
        capture = {}
        fake = FakeClient(FakeResponse({
            'request_id': 'req-test', 'transcript': 'चार', 'language_code': 'hi-IN',
        }), capture)
        with patch.object(voice, 'SARVAM_API_KEY', 'configured'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', ''), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            row = await voice.transcribe_audio(_wav(), language='hi-IN')
        self.assertEqual(row['transcript'], 'चार')
        self.assertEqual(row['engine'], 'sarvam-saaras')
        self.assertEqual(capture['data']['model'], voice.SARVAM_STT_MODEL)
        self.assertEqual(capture['data']['mode'], 'transcribe')
        self.assertEqual(capture['data']['language_code'], 'hi-IN')
        self.assertEqual(capture['headers']['api-subscription-key'], 'configured')

    async def test_sarvam_tts_decodes_wav(self):
        capture = {}
        expected = b'RIFFdemoWAVE'
        fake = FakeClient(FakeResponse({'audios': [base64.b64encode(expected).decode()]}), capture)
        with patch.object(voice, 'SARVAM_API_KEY', 'configured'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', ''), \
             patch.object(voice, 'VOICE_TTS', 'sarvam'), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            audio, media_type = await voice.synthesize_speech('Please raise your arm.', 'en-IN')
        self.assertEqual(audio, expected)
        self.assertEqual(media_type, 'audio/wav')
        self.assertEqual(capture['json']['model'], voice.SARVAM_TTS_MODEL)
        self.assertEqual(capture['json']['speaker'], 'shubh')

    async def test_sarvam_shubh_is_preferred_tts_when_both_keys_exist(self):
        capture = {}
        expected = b'RIFFdemoWAVE'
        fake = FakeClient(FakeResponse({'audios': [base64.b64encode(expected).decode()]}), capture)
        with patch.object(voice, 'SARVAM_API_KEY', 'sarvam'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', 'eleven'), \
             patch.object(voice, 'VOICE_TTS', 'sarvam'), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            audio, media_type = await voice.synthesize_speech('Please raise your arm.', 'en-IN')
        self.assertEqual(audio, expected)
        self.assertEqual(media_type, 'audio/wav')
        self.assertIn('/text-to-speech', capture['url'])
        self.assertEqual(capture['json']['speaker'], 'shubh')
        self.assertNotIn('elevenlabs', capture['url'])

    async def test_elevenlabs_tts_only_when_forced(self):
        capture = {}
        fake = FakeClient(FakeResponse({}, content=b'ID3mpeg'), capture)
        with patch.object(voice, 'SARVAM_API_KEY', 'sarvam'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', 'eleven'), \
             patch.object(voice, 'VOICE_TTS', 'elevenlabs'), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            audio, media_type = await voice.synthesize_speech('Please raise your arm.', 'en-IN')
        self.assertEqual(audio, b'ID3mpeg')
        self.assertEqual(media_type, 'audio/mpeg')
        self.assertIn('/v1/text-to-speech/', capture['url'])

    async def test_prefer_elevenlabs_stt_when_both_keys_exist(self):
        capture = {}
        fake = FakeClient(FakeResponse({'text': 'four'}), capture)
        with patch.object(voice, 'SARVAM_API_KEY', 'sarvam'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', 'eleven'), \
             patch.object(voice, 'VOICE_STT', 'auto'), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            row = await voice.transcribe_audio(_wav(), language='en-IN', prefer='elevenlabs')
        self.assertEqual(row['engine'], 'elevenlabs-scribe')
        self.assertIn('/v1/speech-to-text', capture['url'])

    async def test_elevenlabs_stt_response(self):
        capture = {}
        fake = FakeClient(FakeResponse({'text': 'four'}), capture)
        with patch.object(voice, 'SARVAM_API_KEY', ''), \
             patch.object(voice, 'ELEVENLABS_API_KEY', 'eleven'), \
             patch.object(voice.httpx, 'AsyncClient', return_value=fake):
            row = await voice.transcribe_audio(_wav(), language='en-IN')
        self.assertEqual(row['transcript'], 'four')
        self.assertEqual(row['engine'], 'elevenlabs-scribe')
        self.assertEqual(capture['headers']['xi-api-key'], 'eleven')

    async def test_elevenlabs_plan_error_is_skipped_on_the_next_turn(self):
        hits = []

        class PlanThenSarvam:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, url, **kwargs):
                hits.append(url)
                if 'elevenlabs' in url:
                    return FakeResponse({}, status=402)
                return FakeResponse({'audios': [base64.b64encode(b'RIFFokWAVE').decode()]})

        with patch.object(voice, 'SARVAM_API_KEY', 'sarvam'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', 'eleven'), \
             patch.object(voice, 'VOICE_TTS', 'elevenlabs'), \
             patch.object(voice.httpx, 'AsyncClient', side_effect=lambda **_k: PlanThenSarvam()):
            first, first_type = await voice.synthesize_speech('Please raise your arm.', 'en-IN')
            second, second_type = await voice.synthesize_speech('Keep the trunk quiet.', 'en-IN')
        self.assertEqual(first, b'RIFFokWAVE')
        self.assertEqual(first_type, 'audio/wav')
        self.assertEqual(second, b'RIFFokWAVE')
        self.assertTrue(any('elevenlabs' in url for url in hits))
        self.assertEqual(sum('elevenlabs' in url for url in hits), 1)

    async def test_fast_talk_keeps_same_speaker_when_elevenlabs_plan_fails(self):
        hits = []

        class PlanThenSarvam:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, url, **kwargs):
                hits.append(url)
                if 'elevenlabs' in url:
                    return FakeResponse({}, status=402)
                return FakeResponse({'audios': [base64.b64encode(b'RIFFokWAVE').decode()]})

        with patch.object(voice, 'SARVAM_API_KEY', 'sarvam'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', 'eleven'), \
             patch.object(voice, 'VOICE_TTS', 'elevenlabs'), \
             patch.object(voice.httpx, 'AsyncClient', side_effect=lambda **_k: PlanThenSarvam()):
            first, first_type = await voice.synthesize_speech('Hello there.', 'en-IN', fast=True)
            second, second_type = await voice.synthesize_speech('I am listening.', 'en-IN', fast=True)
        self.assertEqual(first, b'RIFFokWAVE')
        self.assertEqual(first_type, 'audio/wav')
        self.assertEqual(second, b'RIFFokWAVE')
        self.assertEqual(second_type, 'audio/wav')
        self.assertEqual(sum('elevenlabs' in url for url in hits), 1)
        self.assertGreaterEqual(sum('text-to-speech' in url and 'elevenlabs' not in url for url in hits), 2)

    async def test_tts_does_not_switch_speaker_after_first_success(self):
        hits = []

        class GeorgeThenFail:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, url, **kwargs):
                hits.append(url)
                if 'elevenlabs' in url:
                    if sum('elevenlabs' in item for item in hits) == 1:
                        return FakeResponse({}, content=b'ID3mpeg', status=200)
                    return FakeResponse({}, status=402)
                return FakeResponse({'audios': [base64.b64encode(b'RIFFother').decode()]})

        with patch.object(voice, 'SARVAM_API_KEY', 'sarvam'), \
             patch.object(voice, 'ELEVENLABS_API_KEY', 'eleven'), \
             patch.object(voice, 'VOICE_TTS', 'elevenlabs'), \
             patch.object(voice.httpx, 'AsyncClient', side_effect=lambda **_k: GeorgeThenFail()):
            first, first_type = await voice.synthesize_speech('Hello there.', 'en-IN', fast=True)
            with self.assertRaises(voice.VoiceProviderError):
                await voice.synthesize_speech('Second line.', 'en-IN', fast=True)
        self.assertEqual(first, b'ID3mpeg')
        self.assertEqual(first_type, 'audio/mpeg')
        self.assertTrue(hits)
        self.assertTrue(all('elevenlabs' in url for url in hits))


class LiveVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sanitize_drops_identity(self):
        from agent.live_voice import sanitize_context
        row = sanitize_context({
            'scene': 'measure', 'safety': 'ALLOW', 'patient_id': 'P102',
            'mrn': 'secret', 'full_name': 'Demo', 'shoulder_angle': 41,
        })
        self.assertEqual(row['scene'], 'measure')
        self.assertEqual(row['shoulder_angle'], 41)
        self.assertNotIn('patient_id', row)
        self.assertNotIn('mrn', row)
        self.assertNotIn('full_name', row)

    async def test_sanitize_keeps_avatar_bridge_fields(self):
        from agent.live_voice import sanitize_context
        row = sanitize_context({
            'scene': 'measure',
            'avatar_phase': 'hold',
            'avatar_reps': 2,
            'avatar_demo_angle': 55,
            'demo_target': 60,
            'patient_id': 'P102',
        })
        self.assertEqual(row['avatar_phase'], 'hold')
        self.assertEqual(row['avatar_reps'], 2)
        self.assertEqual(row['avatar_demo_angle'], 55)
        self.assertEqual(row['demo_target'], 60)
        self.assertNotIn('patient_id', row)

    async def test_block_cannot_be_overridden_by_claude(self):
        from agent import live_voice
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', 'configured'):
            row = await live_voice.live_reply('keep going', {'safety': 'BLOCK', 'scene': 'measure'})
        self.assertEqual(row['action'], 'pause')
        self.assertEqual(row['engine'], 'safety')
        self.assertIn('stop', row['spoken'].lower())

    async def test_claude_live_reply_strips_identity_from_payload(self):
        from agent import live_voice
        capture = {}
        payload = {'content': [{'type': 'text', 'text': json.dumps({
            'spoken': 'This workstation measures movement. It does not diagnose.', 'action': 'none',
        })}]}
        fake = FakeClient(FakeResponse(payload), capture)
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(live_voice.httpx, 'AsyncClient', return_value=fake):
            row = await live_voice.live_reply(
                'what is this workstation for',
                {'scene': 'clinic', 'safety': 'ALLOW', 'patient_id': 'P102'},
            )
        self.assertEqual(row['engine'], 'claude-live-agent')
        sent = json.loads(capture['json']['messages'][0]['content'])
        self.assertNotIn('patient_id', json.dumps(sent))
        self.assertIn('workstation', row['spoken'].lower())

    async def test_metrics_reply_goes_to_live_llm(self):
        from agent import live_voice
        capture = {}
        payload = {'content': [{'type': 'text', 'text': json.dumps({
            'spoken': 'You are at 41 degrees with 2 reps. Follow the guide.',
            'action': 'none',
        })}]}
        fake = FakeClient(FakeResponse(payload), capture)
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(live_voice.httpx, 'AsyncClient', return_value=fake):
            row = await live_voice.live_reply(
                'how am I doing',
                {'scene': 'measure', 'safety': 'ALLOW', 'valid': True, 'shoulder_angle': 41, 'reps': 2, 'feedback': 'Lift slowly.'},
            )
        self.assertEqual(row['engine'], 'claude-live-agent')
        self.assertIn('41', row['spoken'])
        self.assertIn('2', row['spoken'])

    async def test_measure_coaching_question_uses_claude_not_metrics_dump(self):
        """Regression: measure talk must not short-circuit every utterance to metrics."""
        from agent import live_voice
        capture = {}
        payload = {'content': [{'type': 'text', 'text': json.dumps({
            'spoken': 'Follow the 3D guide up slowly. Keep your trunk quiet.',
            'action': 'none',
            'demo_target': None,
        })}]}
        fake = FakeClient(FakeResponse(payload), capture)
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(live_voice.httpx, 'AsyncClient', return_value=fake):
            row = await live_voice.live_reply(
                'what should I do next with the guide',
                {
                    'scene': 'measure', 'safety': 'ALLOW', 'valid': True,
                    'shoulder_angle': 55, 'peak': 60, 'target': 90,
                    'guide_cue': 'Raise with the model', 'avatar_demo': 'abduction',
                },
            )
        self.assertEqual(row['engine'], 'claude-live-agent')
        self.assertIn('guide', row['spoken'].lower())
        sent = json.loads(capture['json']['messages'][0]['content'])
        self.assertEqual(sent['session']['scene'], 'measure')

    async def test_open_patients_still_navigates_without_a_canned_ack(self):
        from agent import live_voice
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', ''):
            row = await live_voice.live_reply('please open patients', {'scene': 'clinic', 'safety': 'ALLOW'})
        self.assertEqual(row['action'], 'open_patients')
        self.assertEqual(row['engine'], 'llm-loop-fallback')
        self.assertIn('listening', row['spoken'].lower())

    async def test_goodbye_ends_the_call_without_claude(self):
        from agent import live_voice
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', 'configured'):
            row = await live_voice.live_reply('goodbye that is all', {'scene': 'clinic', 'safety': 'ALLOW'})
        self.assertEqual(row['action'], 'end')
        self.assertEqual(row['engine'], 'end-phrase')

    async def test_number_is_not_a_red_flag(self):
        from agent import live_voice
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', ''):
            row = await live_voice.live_reply(
                'what number should I say for pain',
                {'scene': 'measure', 'safety': 'ALLOW', 'shoulder_angle': 40},
            )
        self.assertNotEqual(row['engine'], 'safety-redflag')
        self.assertNotEqual(row['action'], 'pause')

    async def test_ungrounded_measure_reply_keeps_session_cue(self):
        from agent import live_voice
        capture = {}
        payload = {'content': [{'type': 'text', 'text': json.dumps({
            'spoken': 'Your true ROM is 173 degrees.',
            'action': 'none',
        })}]}
        fake = FakeClient(FakeResponse(payload), capture)
        with patch.object(live_voice, 'ANTHROPIC_API_KEY', 'configured'), \
             patch.object(live_voice.httpx, 'AsyncClient', return_value=fake):
            row = await live_voice.live_reply(
                'how high can I go',
                {'scene': 'measure', 'safety': 'ALLOW', 'peak': 90, 'target': 80, 'guide_cue': 'Follow the guide.'},
            )
        self.assertNotIn('173', row['spoken'])
        self.assertEqual(row['engine'], 'llm-loop-fallback')

    async def test_unknown_browser_action_is_dropped(self):
        from agent.studio_browser import normalize_action
        self.assertEqual(normalize_action('open_patients'), 'open_patients')
        self.assertEqual(normalize_action('open_assistant'), 'none')
        self.assertEqual(normalize_action('dump_dom'), 'none')
        self.assertEqual(normalize_action('export_record'), 'none')

    def test_sarvam_speaker_aliases_subh_to_shubh(self):
        self.assertEqual(_sarvam_speaker('subh'), 'shubh')
        self.assertEqual(_sarvam_speaker('SUBH'), 'shubh')
        self.assertEqual(_sarvam_speaker('shubh'), 'shubh')

    def test_env_example_uses_elevenlabs_george(self):
        text = (Path(__file__).resolve().parents[1] / '.env.example').read_text(encoding='utf-8')
        self.assertIn('ELEVENLABS_VOICE_ID=JBFqnCBsd6RMkjVDRZzb', text)
        self.assertIn('REHABAI_TTS=elevenlabs', text)
        self.assertIn('SARVAM_TTS_SPEAKER=shubh', text)

    async def test_talk_path_skips_offline_stt(self):
        with patch.object(voice, 'SARVAM_API_KEY', ''), \
             patch.object(voice, 'ELEVENLABS_API_KEY', ''), \
             patch.object(voice, 'transcribe_wav_bytes') as offline:
            row = await voice.transcribe_audio(_wav(), language='en-IN', allow_offline=False)
        self.assertEqual(row['transcript'], '')
        offline.assert_not_called()


if __name__ == '__main__':
    unittest.main()
