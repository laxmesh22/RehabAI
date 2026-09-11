# RehabAI voice intake workflow

## Purpose

The intake captures patient identity, patient-reported pain and function, and the measurement session in one patient-scoped record. It supports clinical workflow but does not diagnose adhesive capsulitis.

The **Talk** dock is a live spoken agent on authenticated pages. It answers in the current scene (intake, measurement, clinic, or assistant) and is not a diagnosis service.

## End-to-end workflow

1. **Clinician signs in.** JWT and role checks control access to patient records.
2. **Register the patient.** The clinician types and visually confirms full name, MRN, date of birth, sex, affected shoulder, and any clinician-entered diagnosis. Identifiers are not sent to the language model.
3. **Start an assessment.** RehabAI creates a session tied to the selected patient and labels the measurement source as `simulation` or `live`.
4. **Ask the questionnaire.** The voice interface asks six questions in English or Hindi: pain at rest, pain during movement, and difficulty dressing, grooming, reaching overhead, and reaching behind the back.
5. **Capture speech.** The browser records a short 16 kHz mono WAV. Intake speech-to-text order is Sarvam Saaras, then ElevenLabs Scribe, then Windows Speech. Talk uses ElevenLabs Scribe first. Score buttons remain available.
6. **Parse and confirm.** Deterministic rules accept explicit numbers, supported function labels, yes/no confirmations, and stop phrases. Claude receives only the question ID, allowed range, language, and ambiguous utterance. It does not receive the patient name, MRN, video, or stored history. Claude cannot create a numeric score; an unclear score is asked again.
7. **Safety gate.** Stop or high-risk phrases return `PAUSE`. The questionnaire must be complete and every value must be confirmed before the movement trial can begin.
8. **Talk during the session.** One tap starts a continuous call. RehabAI listens, answers, then listens again until the caller taps End or says they are done. `/api/voice/agent` transcribes with ElevenLabs Scribe first (then Sarvam), then intake parse / supervisor / live reply, then **Sarvam Subh (`shubh`)** TTS. The browser plays that audio; if TTS is missing it uses built-in speech. Switching English/Hindi during intake cancels the previous prompt so voices do not overlap. Step 3 is an allowlisted in-page browser action. The model never receives the DOM or identifiers.
9. **Run the measurement.** RealSense, pose, and IMU processing remain independent of Sarvam, ElevenLabs, and Claude. The LLM cannot calculate ROM, count repetitions, or override a safety block.
10. **Save the session.** Confirmed questionnaire answers and source metadata are stored with the session. Assessment output retains live, simulation, or demo labels.
11. **Export the patient record.** Authorized users can download `rehabai-<patient_id>.json` or `rehabai-<patient_id>.xlsx`. Exports include demographics, questionnaire responses, assessments, sessions, ROM, pain, compensation events, source labels, and a data dictionary. Export actions are audit logged.

## Provider data boundary

| Service | Data sent | Data not sent |
|---|---|---|
| Sarvam STT | Short audio clip and selected language | Database record, prior sessions, raw camera video |
| ElevenLabs STT | Short audio clip and language code | Patient identity, measurements, history |
| Sarvam TTS / ElevenLabs TTS | Spoken prompt or coach reply text | Patient identity, video |
| Claude (intake) | Question ID, allowed range, selected language, ambiguous transcript | Name, MRN, date of birth, raw audio, video, ROM history |
| Claude (live Talk) | Sanitized session metrics and the utterance | Name, MRN, `patient_id`, video, raw audio |

## Failure behavior

- If Sarvam STT fails, ElevenLabs Scribe is tried next, then local WAV transcription. Talk tries ElevenLabs Scribe first.
- If Sarvam TTS fails, ElevenLabs is tried; if that fails, the browser uses built-in speech synthesis.
- Intake language clicks cancel the previous spoken prompt. The already-selected language does not start a second voice.
- If Claude fails, deterministic parsing or a short live template is used.
- If every speech path fails, score buttons remain available and store the same structured fields.
- Missing or unconfirmed answers block the assessment. No default score is inserted.
- `REHABAI_STT=sarvam|elevenlabs` and `REHABAI_TTS=sarvam|elevenlabs` force order. Default TTS is **Sarvam Bulbul with speaker `shubh` (Subh)** for intake prompts, Talk greeting, Talk replies, and coach cues. ElevenLabs TTS is fallback only.

## Local configuration

Copy `.env.example` to `.env`, insert rotated provider keys, and start the product with `Start-RehabAI.ps1`. The launcher reads `.env` into the server process without printing values. Sarvam STT uses `saaras:v4` (latest). Sarvam TTS uses `bulbul:v3` (latest documented REST model) with speaker `shubh` (API id for Subh; `subh` in `.env` is accepted as an alias). Check `/api/health`: `voice.sarvam_configured`, `voice.sarvam_stt_model`, `voice.sarvam_tts_model`, `voice.sarvam_tts_speaker`, `voice.elevenlabs_configured`, and `voice.claude_configured` without expecting keys in the JSON.
