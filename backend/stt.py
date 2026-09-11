"""Offline number/function transcription on Windows. Does not invent scores."""
from __future__ import annotations
import subprocess
import tempfile
import wave
from pathlib import Path

WORDS = (
    'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
    'none', 'mild', 'moderate', 'severe', 'unable',
    'yes', 'no', 'stop', 'pause',
)


def transcribe_wav_bytes(data: bytes) -> dict:
    if not data or len(data) < 44 or data[:4] != b'RIFF':
        return {'transcript': '', 'engine': 'none', 'error': 'not_wav'}
    with tempfile.TemporaryDirectory() as folder:
        wav_path = Path(folder) / 'utterance.wav'
        wav_path.write_bytes(data)
        if not _valid_pcm_wav(wav_path):
            return {'transcript': '', 'engine': 'none', 'error': 'not_pcm_wav'}
        text = _windows_recognize(wav_path)
        return {'transcript': text, 'engine': 'windows-speech', 'error': None}


def _valid_pcm_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), 'rb') as handle:
            return handle.getnchannels() >= 1 and handle.getsampwidth() == 2 and handle.getnframes() > 0
    except (wave.Error, EOFError, OSError):
        return False


def _windows_recognize(wav_path: Path) -> str:
    script = wav_path.parent / 'recognize.ps1'
    words = ','.join("'{0}'".format(word.replace("'", "''")) for word in WORDS)
    script.write_text(
        f"""
Add-Type -AssemblyName System.Speech
$path = '{str(wav_path).replace(chr(39), chr(39)+chr(39))}'
try {{
  $culture = New-Object System.Globalization.CultureInfo 'en-US'
  $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine $culture
}} catch {{
  $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
}}
try {{
  $engine.SetInputToWaveFile($path)
  $choices = New-Object System.Speech.Recognition.Choices
  foreach ($w in @({words})) {{ [void]$choices.Add($w) }}
  $builder = New-Object System.Speech.Recognition.GrammarBuilder
  $builder.Culture = $engine.RecognizerInfo.Culture
  [void]$builder.Append($choices)
  $engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar $builder))
  $engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(2)
  $engine.BabbleTimeout = [TimeSpan]::FromSeconds(1)
  $result = $engine.Recognize([TimeSpan]::FromSeconds(8))
  if ($result -and $result.Text) {{ $result.Text.Trim() }}
}} finally {{
  if ($engine) {{ $engine.Dispose() }}
}}
""",
        encoding='utf-8',
    )
    try:
        completed = subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
            capture_output=True, text=True, timeout=25, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ''
    if completed.returncode != 0:
        return ''
    return (completed.stdout or '').strip()
