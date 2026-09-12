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
      avatar_demo: extra.avatar_demo || global.RehabGuideAvatar,
      source: tel.source || extra.source,
      safety,
      phase: extra.phase || (tel.guide && tel.guide.phase) || tel.exercise_phase,
      shoulder_angle: tel.valid ? Math.round(tel.shoulder_angle || 0) : undefined,
      peak: tel.peak != null ? Math.round(tel.peak) : undefined,
      torso_lean: tel.torso_lean != null ? Math.round(tel.torso_lean) : undefined,
      reps: tel.rep,
      goal: extra.goal,
      target: extra.target != null ? extra.target : (global.RehabGuideTarget || undefined),
      demo_target: global.RehabGuideTarget,
      coverage: tel.coverage,
      feedback: tel.feedback,
      guide_cue: (tel.guide && tel.guide.cue) || extra.guide_cue || undefined,
      avatar_phase: extra.avatar_phase,
      avatar_reps: extra.avatar_reps,
      avatar_demo_angle: extra.avatar_demo_angle,
      coach_mode: extra.coach_mode || (scene === 'measure' ? 'session' : undefined),
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

  function applyCoachDemo(data) {
    if (data == null) return;
    if (global.RehabCoachAvatar?.applyCoachReply) {
      global.RehabCoachAvatar.applyCoachReply(data, { noteDemo: true });
      return;
    }
    if (data.demo_target == null) return;
    const deg = Number(data.demo_target);
    if (!Number.isFinite(deg)) return;
    global.RehabGuide?.setDemoTarget?.(deg);
    global.RehabGuideTarget = deg;
    const el = document.getElementById('coach-note');
    if (el) el.textContent = `3D demo target synced to measured ability · ${Math.round(deg)}°`;
  }

  function setHint(text) {
    const dock = document.getElementById('voice-dock');
    if (talking) dock?.classList.add('hot');
    const el = document.getElementById('voice-dock-hint');
    if (el) el.textContent = text || 'Tap to start · talks until you end';
    // Consumer orb hides the hint; mirror status onto the visible talk line.
    const status = document.getElementById('consumer-status');
    if (status && text) {
      status.hidden = false;
      status.textContent = text;
    }
  }

  function setTitle(text) {
    const el = document.getElementById('voice-dock-title');
    if (el) el.textContent = text || 'Talk';
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function applyStudioAction(action) {
    if (!action || action === 'none' || action === 'end') return;
    if (typeof global.RehabStudioActions?.apply === 'function') {
      global.RehabStudioActions.apply(action);
      return;
    }
    const hash = {
      open_patients: '#/patients',
      open_settings: '#/settings',
      open_home: '#/app/home',
      open_history: '#/app/home',
    }[action];
    if (hash) location.hash = hash;
    if (action === 'confirm_tracking') document.getElementById('confirm')?.click();
    if (action === 'start_session') document.getElementById('go')?.click();
  }

  async function playReply(data) {
    const spoken = data.spoken || '';
    if (!spoken) return;
    if (data.tts_error || data.tts_engine === 'browser-speech') {
      setHint(data.tts_error
        ? String(data.tts_error).slice(0, 120)
        : 'Browser voice (server TTS unavailable)');
    }
    try {
      if (data.audio_base64 && data.media_type && global.RehabIntake?.playBlob) {
        const binary = atob(data.audio_base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        await global.RehabIntake.playBlob(new Blob([bytes], { type: data.media_type }), { keepMic: true });
        return;
      }
      // Same pinned browser voice every fallback — never a random OS speaker.
      await global.RehabIntake?.speak?.(spoken, language(), { local: true, keepMic: true, rate: 1.05 });
    } catch (err) {
      setHint(spoken.slice(0, 80));
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
    const api = typeof global.RehabApiUrl === 'function'
      ? global.RehabApiUrl('/api/voice/agent')
      : '/api/voice/agent';
    const res = await fetch(api, { method: 'POST', headers: authHeaders(), body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'string' ? detail : 'Voice agent failed');
    }
    return data;
  }

  function revealReportPanel(message) {
    const panel = document.getElementById('report-panel');
    if (!panel) return;
    panel.hidden = false;
    panel.classList.add('hot', 'show');
    const status = document.getElementById('report-status');
    if (status && message) status.textContent = message;
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
    if (prompt && data.spoken) {
      prompt.hidden = false;
      prompt.textContent = data.spoken;
    }
    if (status) {
      status.hidden = false;
      if (data.transcript) status.textContent = 'You: ' + data.transcript;
      else if (data.phase === 'report' || data.action === 'await_report') status.textContent = 'Report photo or skip';
      else status.textContent = 'Your turn — reply when ready';
    }
    if (typeof global.RehabConsumerLive?.showLive === 'function') {
      global.RehabConsumerLive.showLive(data.spoken, status?.textContent);
    }
    if (data.action === 'await_report' || data.phase === 'report') {
      revealReportPanel(data.spoken || 'Upload a report photo, or say skip.');
    }
  }

  function shouldEndCall(data) {
    return data.action === 'end'
      || data.action === 'pause'
      || data.safety === 'BLOCK'
      || data.action === 'open_home'
      || data.action === 'open_history'
      || data.action === 'start_session';
  }

  async function conversationLoop(gen) {
    const intake = global.RehabIntake;
    talking = true;
    lastTurn = { transcript: '', spoken: '' };
    document.getElementById('voice-dock')?.classList.add('hot');
    setTitle('Consult');
    setHint('Starting…');
    try {
      if (!intake?.listenWav) {
        setHint('Voice is not ready on this page');
        return;
      }
      await intake.unlockAudio?.();
      await intake.readyVoices?.();
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
      // Short settle only — do not put the user on a countdown clock.
      await sleep(280);
      if (shouldEndCall(first)) {
        applyStudioAction(first.action);
        talking = false;
        return;
      }

      let hardErrors = 0;
      while (talking && gen === callGen) {
        setHint('Your turn — reply when ready');
        let wav;
        try {
          wav = await intake.listenWav(language(), {
            // Wait indefinitely for the first word; only cap once they start speaking.
            idleMs: 0,
            timeoutMs: 45000,
            // End the turn after a natural pause (not a fixed question timer).
            silenceMs: 1200,
            minSpeechMs: 280,
            keepMic: true,
            waitingHint: 'Your turn — reply when ready',
            onStatus: setHint,
          });
        } catch (err) {
          hardErrors += 1;
          setHint(err.message || 'Mic issue. Trying again…');
          if (hardErrors >= 3) break;
          await sleep(220);
          continue;
        }
        if (!talking || gen !== callGen) break;
        if (!wav || wav.byteLength < 1600 || !intake.hasSpeech?.(wav)) {
          // Keep waiting quietly — no timed “I am listening” prompts.
          continue;
        }
        hardErrors = 0;
        setHint('Answering…');
        let data;
        try {
          data = await askServer(wav);
        } catch (err) {
          hardErrors += 1;
          setHint(err.message || 'Server voice failed. Trying again…');
          if (hardErrors >= 3) break;
          await sleep(220);
          continue;
        }
        if (!talking || gen !== callGen) break;
        lastTurn = { transcript: data.transcript || '', spoken: data.spoken || '' };
        global.RehabLastPatientSaid = lastTurn.transcript || '';
        setHint(data.transcript ? ('You: ' + data.transcript) : (data.spoken || 'Your turn — reply when ready'));
        const coachLine = document.getElementById('coach-note');
        if (coachLine && data.spoken && sessionContext().scene === 'measure') {
          coachLine.textContent = data.spoken;
        }
        syncConsumerFromReply(data);
        if (data.parsed && typeof global.RehabIntakeOnParsed === 'function') {
          global.RehabIntakeOnParsed(data.parsed, data.transcript);
        }
        applyCoachDemo(data);
        // Defer navigation actions until after TTS so Talk does not vanish mid-sentence.
        if (data.action && !shouldEndCall(data)) {
          applyStudioAction(data.action);
        }
        if (data.action === 'await_report' || data.phase === 'report') {
          revealReportPanel(data.spoken || 'Upload a report photo, or say skip.');
        }
        if (data.spoken) await playReply(data);
        if (!talking || gen !== callGen) break;
        await sleep(250);
        if (shouldEndCall(data)) {
          applyStudioAction(data.action);
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
        setHint(sessionContext().scene === 'consumer'
          ? 'Tap — talk in your own words'
          : sessionContext().scene === 'measure'
            ? 'Tap Coach · talk while the 3D guide moves'
            : 'Tap to start · talks until you end');
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
    // Latch before the async loop so a second tap ends Talk instead of
    // starting a second greeting with a different speaker.
    talking = true;
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
