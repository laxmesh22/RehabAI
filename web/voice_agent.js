(function (global) {
  let talking = false;
  let callGen = 0;
  let lastTurn = { transcript: '', spoken: '' };
  let consumerState = {
    intake_field: null,
    awaiting_confirm: false,
    pending_value: null,
  };

  function authHeaders() {
    const token = localStorage.getItem('rehabai_token');
    return token ? { Authorization: 'Bearer ' + token } : {};
  }

  function language() {
    return global.RehabVoiceLang || 'en-IN';
  }

  function patientId() {
    const extra = global.RehabVoiceContext || {};
    if (extra.patient_id) return extra.patient_id;
    if (extra.session_patient_id) return extra.session_patient_id;
    const patient = location.hash.match(/^#\/patients\/([^/]+)/);
    return (patient && patient[1]) || '';
  }

  function sessionContext() {
    const tel = global.RehabLiveLast || {};
    const extra = global.RehabVoiceContext || {};
    const hash = location.hash || '';
    let scene = extra.scene;
    if (!scene) {
      if (hash.startsWith('#/app')) scene = 'consumer';
      else if (hash.startsWith('#/live')) scene = extra.phase === 'intake' ? 'intake' : 'measure';
      else if (hash.startsWith('#/assistant')) scene = 'assistant';
      else scene = 'clinic';
    }
    const safety = (tel.safety && tel.safety.level) || extra.safety || '';
    const awaiting = scene === 'consumer'
      ? Boolean(consumerState.awaiting_confirm)
      : Boolean(extra.awaiting_confirm);
    return {
      scene,
      language: language(),
      exercise: extra.exercise || tel.exercise_id || tel.exercise,
      movement: extra.movement || (tel.guide && tel.guide.movement),
      source: tel.source || extra.source,
      safety,
      phase: extra.phase || (tel.guide && tel.guide.phase),
      shoulder_angle: tel.valid ? Math.round(tel.shoulder_angle || 0) : undefined,
      torso_lean: tel.torso_lean != null ? Math.round(tel.torso_lean) : undefined,
      reps: tel.rep,
      goal: extra.goal,
      target: extra.target,
      coverage: tel.coverage,
      feedback: tel.feedback,
      fused_angle: tel.imu && tel.imu.fused_angle != null ? Math.round(tel.imu.fused_angle) : undefined,
      intake_field: scene === 'consumer' ? (consumerState.intake_field || extra.intake_field) : extra.intake_field,
      question: extra.question,
      valid: tel.valid,
      awaiting_confirm: awaiting,
      pending_value: scene === 'consumer' ? consumerState.pending_value : extra.pending_value,
      last_transcript: lastTurn.transcript,
      last_spoken: lastTurn.spoken,
    };
  }

  function setHint(text) {
    const dock = document.getElementById('voice-dock');
    if (talking) dock?.classList.add('hot');
    const el = document.getElementById('voice-dock-hint');
    if (el) el.textContent = text || 'Tap to start · talks until you end';
  }

  function setTitle(text) {
    const el = document.getElementById('voice-dock-title');
    if (el) el.textContent = text || 'Talk';
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function applyStudioAction(action) {
    if (!action || action === 'none' || action === 'pause' || action === 'end') return;
    if (typeof global.RehabStudioActions?.apply === 'function') {
      global.RehabStudioActions.apply(action);
      return;
    }
    const hash = {
      open_patients: '#/patients',
      open_settings: '#/settings',
      open_home: '#/app/home',
    }[action];
    if (hash) location.hash = hash;
    if (action === 'confirm_tracking') document.getElementById('confirm')?.click();
    if (action === 'start_session') document.getElementById('go')?.click();
  }

  async function playReply(data) {
    const spoken = data.spoken || '';
    if (!spoken) return;
    try {
      if (data.audio_base64 && data.media_type && global.RehabIntake?.playBlob) {
        const binary = atob(data.audio_base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        await global.RehabIntake.playBlob(new Blob([bytes], { type: data.media_type }), { keepMic: true });
        return;
      }
      await global.RehabIntake?.speak?.(spoken, language(), { keepMic: true });
    } catch (err) {
      // Autoplay or TTS failure must not kill the call — show text and continue.
      setHint(spoken.slice(0, 80));
      try {
        await global.RehabIntake?.speak?.(spoken, language(), { local: true, keepMic: true });
      } catch {
        /* text already on screen */
      }
    }
  }

  async function askServer(wav) {
    const body = new FormData();
    body.append('language', language());
    body.append('speak', '1');
    body.append('context', JSON.stringify(sessionContext()));
    const pid = patientId();
    if (pid) body.append('patient_id', pid);
    if (wav && wav.byteLength) {
      body.append('audio', new Blob([wav], { type: 'audio/wav' }), 'talk.wav');
    }
    const res = await fetch('/api/voice/agent', { method: 'POST', headers: authHeaders(), body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'string' ? detail : 'Voice agent failed');
    }
    return data;
  }

  function greeting() {
    const scene = sessionContext().scene;
    const hi = language() === 'hi-IN';
    if (scene === 'consumer') {
      return hi
        ? 'मैं रिहैबएआई हूँ। यह निदान नहीं है। दर्द और कामकाज के सवाल पूछूँगा।'
        : 'I am RehabAI. This is not a diagnosis. I will ask about pain and daily function.';
    }
    if (scene === 'intake') return hi ? 'मैं सुन रहा हूँ। जवाब बोलिए।' : 'I am listening. Answer in your own words.';
    if (scene === 'measure') return hi ? 'मैं साथ हूँ। रुकना हो तो बोलिए।' : 'I am with you. Say if you need to pause.';
    if (scene === 'assistant') return hi ? 'स्टोर माप पर जवाब दूंगा।' : 'I will answer from stored measurements only.';
    return hi ? 'रिहैबएआई सुन रहा है।' : 'RehabAI is listening. Talk until you are done.';
  }

  function syncConsumerFromReply(data) {
    if (sessionContext().scene !== 'consumer') return;
    if (data.intake_field) consumerState.intake_field = data.intake_field;
    if ('awaiting_confirm' in data) consumerState.awaiting_confirm = Boolean(data.awaiting_confirm);
    if ('pending_value' in data) {
      consumerState.pending_value = data.pending_value == null ? null : data.pending_value;
    }
    if (global.RehabVoiceContext) {
      global.RehabVoiceContext.intake_field = consumerState.intake_field;
      global.RehabVoiceContext.awaiting_confirm = consumerState.awaiting_confirm;
      global.RehabVoiceContext.pending_value = consumerState.pending_value;
    }
    const prompt = document.getElementById('consumer-prompt');
    const status = document.getElementById('consumer-status');
    if (prompt && data.spoken) prompt.textContent = data.spoken;
    if (status) {
      if (data.transcript) status.textContent = 'You: ' + data.transcript;
      else if (data.stt_engine && data.stt_engine !== 'typed') status.textContent = 'Listening for your answer…';
    }
  }

  function shouldEndCall(data) {
    return data.action === 'end'
      || data.action === 'pause'
      || data.safety === 'BLOCK'
      || data.action === 'open_home';
  }

  async function conversationLoop(gen) {
    const intake = global.RehabIntake;
    if (!intake?.listenWav) {
      setHint('Voice is not ready on this page');
      return;
    }
    talking = true;
    lastTurn = { transcript: '', spoken: '' };
    document.getElementById('voice-dock')?.classList.add('hot');
    setTitle('In call');
    setHint('Starting…');
    try {
      await intake.unlockAudio?.();
      const scene = sessionContext().scene;
      if (scene === 'consumer') {
        let first;
        try {
          first = await askServer(null);
        } catch (err) {
          setHint(err.message || 'Could not start Talk. Tap again.');
          return;
        }
        if (gen !== callGen) return;
        syncConsumerFromReply(first);
        if (first.spoken) await playReply(first);
        if (gen !== callGen || !talking) return;
        // Let speaker audio decay so STT does not hear Subh / browser TTS.
        await sleep(450);
        if (first.action === 'open_home') {
          applyStudioAction('open_home');
          return;
        }
      } else {
        try {
          await intake.speak?.(greeting(), language(), { keepMic: true });
        } catch {
          setHint(greeting());
        }
        if (gen !== callGen || !talking) return;
        await sleep(350);
      }

      let emptyTurns = 0;
      let hardErrors = 0;
      while (talking && gen === callGen) {
        setHint('Listening…');
        let wav;
        try {
          wav = await intake.listenWav(language(), {
            timeoutMs: 6000,
            silenceMs: 550,
            minSpeechMs: 320,
            keepMic: true,
            onStatus: setHint,
          });
        } catch (err) {
          hardErrors += 1;
          setHint(err.message || 'Mic issue. Trying again…');
          if (hardErrors >= 3) break;
          await sleep(400);
          continue;
        }
        if (!talking || gen !== callGen) break;
        if (!wav || wav.byteLength < 1600 || !intake.hasSpeech?.(wav)) {
          emptyTurns += 1;
          if (emptyTurns === 2) {
            try {
              await intake.speak?.(
                language() === 'hi-IN' ? 'मैं सुन रहा हूँ। नंबर बोलिए।' : 'I am listening. Please say a number.',
                language(),
                { keepMic: true },
              );
            } catch { /* ignore */ }
            await sleep(300);
            emptyTurns = 0;
          }
          continue;
        }
        emptyTurns = 0;
        hardErrors = 0;
        setHint('Answering…');
        let data;
        try {
          data = await askServer(wav);
        } catch (err) {
          hardErrors += 1;
          setHint(err.message || 'Server voice failed. Trying again…');
          if (hardErrors >= 3) break;
          await sleep(500);
          continue;
        }
        if (!talking || gen !== callGen) break;
        lastTurn = { transcript: data.transcript || '', spoken: data.spoken || '' };
        setHint(data.transcript ? ('You: ' + data.transcript) : (data.spoken || 'Listening…'));
        syncConsumerFromReply(data);
        if (data.parsed && typeof global.RehabIntakeOnParsed === 'function') {
          global.RehabIntakeOnParsed(data.parsed, data.transcript);
        }
        applyStudioAction(data.action);
        if (data.action === 'await_report') {
          document.getElementById('report-panel')?.classList.add('hot');
          document.getElementById('report-status') && (document.getElementById('report-status').textContent =
            'Upload a report photo, or tap Skip report.');
        }
        if (data.spoken) await playReply(data);
        if (!talking || gen !== callGen) break;
        await sleep(400);
        if (shouldEndCall(data)) {
          talking = false;
          break;
        }
      }
    } catch (err) {
      if (talking && gen === callGen) setHint(err.message || 'Talk failed. Tap to try again.');
    } finally {
      if (gen === callGen) {
        talking = false;
        intake.stopVoice?.();
        document.getElementById('voice-dock')?.classList.remove('hot');
        setTitle('Talk');
        setHint('Tap to start · talks until you end');
      }
    }
  }

  function endTalk() {
    if (!talking) return;
    talking = false;
    callGen += 1;
    global.RehabIntake?.stopVoice?.();
    setHint('Ending…');
    document.getElementById('voice-dock')?.classList.remove('hot');
    setTitle('Talk');
    setTimeout(() => setHint('Tap to start · talks until you end'), 200);
  }

  function talk(opts) {
    const options = opts || {};
    if (talking) {
      endTalk();
      if (!options.restart) return;
    }
    callGen += 1;
    const gen = callGen;
    conversationLoop(gen);
  }

  function ensureTalking() {
    if (talking) return;
    talk();
  }

  function restart() {
    talk({ restart: true });
  }

  function bindDock() {
    const btn = document.getElementById('voice-dock');
    if (!btn || btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => { talk(); });
  }

  global.RehabVoiceAgent = {
    talk,
    endTalk,
    restart,
    ensureTalking,
    bindDock,
    sessionContext,
    isTalking: () => talking,
  };
})(window);
