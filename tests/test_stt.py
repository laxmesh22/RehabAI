import io
import unittest
import wave
from backend.stt import transcribe_wav_bytes


def _silent_wav(seconds=0.4, rate=16000):
    frames = int(rate * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b'\x00\x00' * frames)
    return buffer.getvalue()


class SttTests(unittest.TestCase):
    def test_rejects_non_wav_without_inventing(self):
        row = transcribe_wav_bytes(b'not a wave')
        self.assertEqual(row['transcript'], '')
        self.assertEqual(row['engine'], 'none')

    def test_silent_wav_does_not_invent_a_score(self):
        row = transcribe_wav_bytes(_silent_wav())
        self.assertEqual(row['transcript'], '')
        self.assertIn(row['engine'], ('windows-speech', 'none'))
