(function (global) {
  const Rec = global.SpeechRecognition || global.webkitSpeechRecognition;
  let recognition = null;
  let speaking = null;
  let micStream = null;
  let remoteSynthesize = null;
  let remoteAudio = null;
  let remoteAudioUrl = null;
  let activeRecordStop = null;
  let sharedAudioCtx = null;
  let speakGen = 0;
  let speakAbort = null;

  function configure(options) {
    remoteSynthesize = options && typeof options.synthesize === 'function' ? options.synthesize : null;
  }

  function canListen() {
    return Boolean(Rec) || Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  function stopPlayback() {
    speakGen += 1;
    if (speakAbort) {
      try { speakAbort.abort(); } catch {}
      speakAbort = null;
    }
    if (global.speechSynthesis) global.speechSynthesis.cancel();
    if (remoteAudio) {
      try { remoteAudio.pause(); remoteAudio.src = ''; } catch {}
      remoteAudio = null;
    }
    if (remoteAudioUrl) {
      URL.revokeObjectURL(remoteAudioUrl);
      remoteAudioUrl = null;
    }
    speaking = null;
  }

  function stopVoice() {
    if (typeof activeRecordStop === 'function') {
      try { activeRecordStop(); } catch {}
      activeRecordStop = null;
    }
    stopListenOnly(true);
    stopPlayback();
    releaseMic();
    if (sharedAudioCtx) {
      try { sharedAudioCtx.close(); } catch {}
      sharedAudioCtx = null;
    }
  }

  function releaseMic() {
    if (!micStream) return;
    micStream.getTracks().forEach(track => track.stop());
    micStream = null;
  }

  function pickVoice(lang) {
    const voices = global.speechSynthesis ? global.speechSynthesis.getVoices() : [];
    return voices.find(v => v.lang === lang)
      || voices.find(v => v.lang && v.lang.toLowerCase().startsWith(lang.slice(0, 2).toLowerCase()))
      || null;
  }

  async function unlockAudio() {
    try {
      const AudioCtx = global.AudioContext || global.webkitAudioContext;
      if (!AudioCtx) return;
      if (!sharedAudioCtx || sharedAudioCtx.state === 'closed') {
        sharedAudioCtx = new AudioCtx();
      }
      if (sharedAudioCtx.state === 'suspended') await sharedAudioCtx.resume();
      // Silent buffer kickstarts autoplay policy after a user gesture.
      const buffer = sharedAudioCtx.createBuffer(1, 1, 22050);
      const src = sharedAudioCtx.createBufferSource();
      src.buffer = buffer;
      src.connect(sharedAudioCtx.destination);
      src.start(0);
    } catch {
      /* ignore */
    }
    try {
      if (global.speechSynthesis) global.speechSynthesis.resume();
    } catch {
      /* ignore */
    }
  }

  async function speak(text, lang, opts) {
    const options = opts || {};
    stopListenOnly(false);
    stopPlayback();
    if (!text) return;
    const gen = speakGen;
    const localOnly = Boolean(options.local);
    const ac = new AbortController();
    speakAbort = ac;
    if (!localOnly && remoteSynthesize) {
      try {
        const blob = await remoteSynthesize(text, lang || 'en-IN', ac.signal);
        if (gen !== speakGen) return;
        if (blob && blob.size) {
          await playOwnedBlob(blob, gen);
          return;
        }
      } catch (err) {
        if (gen !== speakGen || (err && err.name === 'AbortError')) return;
      }
    }
    if (gen !== speakGen) return;
    if (!global.speechSynthesis) return;
    return new Promise(resolve => {
      if (gen !== speakGen) { resolve(); return; }
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = lang || 'en-IN';
      utter.rate = 0.95;
      const voice = pickVoice(utter.lang);
      if (voice) utter.voice = voice;
      utter.onend = () => { if (speaking === utter) speaking = null; resolve(); };
      utter.onerror = () => { if (speaking === utter) speaking = null; resolve(); };
      speaking = utter;
      try {
        global.speechSynthesis.resume();
        global.speechSynthesis.speak(utter);
      } catch {
        resolve();
      }
    });
  }

  function stopListenOnly(fromUser) {
    if (!recognition) {
      if (fromUser) releaseMic();
      return;
    }
    try {
      recognition._rehabStopped = true;
      recognition.onend = null;
      recognition.onerror = null;
      recognition.stop();
    } catch {}
    recognition = null;
    if (fromUser) releaseMic();
  }

  function voiceErrorMessage(code) {
    if (code === 'not-allowed') {
      return 'Microphone permission was denied. Tap Talk after allowing the mic.';
    }
    if (code === 'service-not-allowed') {
      return 'Voice needs Chrome or Edge at http://127.0.0.1:8000.';
    }
    if (code === 'audio-capture') {
      return 'No microphone was found.';
    }
    if (code === 'network') {
      return 'Cloud speech is blocked. Keep talking — server STT will try again.';
    }
    if (code === 'language-not-supported') {
      return 'That speech language is not installed. Try English.';
    }
    return 'Voice capture failed (' + (code || 'unknown') + ').';
  }

  function hush() {
    stopListenOnly(false);
    stopPlayback();
    return new Promise(resolve => setTimeout(resolve, 80));
  }

  async function ensureMic() {
    if (micStream && micStream.getTracks().some(track => track.readyState === 'live')) {
      return micStream;
    }
    releaseMic();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw Object.assign(new Error('This browser cannot open a microphone.'), { code: 'audio-capture' });
    }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      return micStream;
    } catch (err) {
      const denied = err && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError');
      throw Object.assign(
        new Error(denied
          ? 'Microphone permission was denied. Allow the mic, then tap Talk.'
          : 'No microphone was found.'),
        { code: denied ? 'not-allowed' : 'audio-capture' }
      );
    }
  }

  function listenOnce(lang, timeoutMs, onTranscript) {
    if (!Rec) {
      return Promise.reject(Object.assign(
        new Error('Voice capture needs Chrome or Edge on this workstation.'),
        { code: 'no-api' }
      ));
    }
    return new Promise((resolve, reject) => {
      const rec = new Rec();
      recognition = rec;
      rec.lang = lang || 'en-IN';
      rec.interimResults = true;
      rec.continuous = true;
      rec.maxAlternatives = 3;
      rec._rehabStopped = false;
      let finalText = '';
      let settled = false;
      const finish = (ok, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (recognition === rec) recognition = null;
        if (ok) resolve(value);
        else reject(value);
      };
      const timer = setTimeout(() => {
        rec._rehabStopped = true;
        try { rec.stop(); } catch {}
      }, timeoutMs || 12000);
      rec.onresult = ev => {
        let interim = '';
        for (let i = ev.resultIndex; i < ev.results.length; i += 1) {
          const piece = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) finalText += (finalText ? ' ' : '') + piece;
          else interim += piece;
        }
        const shown = (finalText || interim).trim();
        if (shown && typeof onTranscript === 'function') onTranscript(shown);
        if (finalText.trim()) {
          rec._rehabStopped = true;
          try { rec.stop(); } catch {}
        }
      };
      rec.onerror = ev => {
        const code = ev.error;
        if (code === 'no-speech' || code === 'aborted') return;
        finish(false, Object.assign(new Error(voiceErrorMessage(code)), { code }));
      };
      rec.onend = () => finish(true, (finalText || '').trim());
      try {
        rec.start();
      } catch (err) {
        finish(false, Object.assign(
          new Error(voiceErrorMessage(err && err.name === 'NotAllowedError' ? 'not-allowed' : 'audio-capture')),
          { code: 'audio-capture' }
        ));
      }
    });
  }

  function flatten(chunks) {
    let total = 0;
    for (let i = 0; i < chunks.length; i += 1) total += chunks[i].length;
    const out = new Float32Array(total);
    let offset = 0;
    for (let i = 0; i < chunks.length; i += 1) {
      out.set(chunks[i], offset);
      offset += chunks[i].length;
    }
    return out;
  }

  function resample(input, fromRate, toRate) {
    if (!input.length || fromRate === toRate) return input;
    const ratio = fromRate / toRate;
    const out = new Float32Array(Math.max(1, Math.round(input.length / ratio)));
    for (let i = 0; i < out.length; i += 1) {
      const x = i * ratio;
      const i0 = Math.min(Math.floor(x), input.length - 1);
      const i1 = Math.min(i0 + 1, input.length - 1);
      const frac = x - i0;
      out[i] = input[i0] * (1 - frac) + input[i1] * frac;
    }
    return out;
  }

  function encodeWav(float32, sampleRate) {
    const pcm = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i += 1) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    const buffer = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buffer);
    const ascii = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
    };
    ascii(0, 'RIFF');
    view.setUint32(4, 36 + pcm.length * 2, true);
    ascii(8, 'WAVE');
    ascii(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    ascii(36, 'data');
    view.setUint32(40, pcm.length * 2, true);
    new Int16Array(buffer, 44).set(pcm);
    return buffer;
  }

  function pcmRmsFromWav(wav) {
    if (!wav || wav.byteLength < 48) return 0;
    const view = new DataView(wav);
    if (view.getUint32(0, false) !== 0x52494646) return 0; // RIFF
    const samples = new Int16Array(wav, 44);
    if (!samples.length) return 0;
    let sum = 0;
    const step = Math.max(1, Math.floor(samples.length / 4000));
    let n = 0;
    for (let i = 0; i < samples.length; i += step) {
      const v = samples[i] / 32768;
      sum += v * v;
      n += 1;
    }
    return Math.sqrt(sum / Math.max(1, n));
  }

  function hasSpeech(wav) {
    return pcmRmsFromWav(wav) >= 0.018;
  }

  async function audioContextForMic() {
    const AudioCtx = global.AudioContext || global.webkitAudioContext;
    if (!AudioCtx) throw new Error('Web Audio is unavailable in this browser.');
    if (!sharedAudioCtx || sharedAudioCtx.state === 'closed') {
      sharedAudioCtx = new AudioCtx();
    }
    if (sharedAudioCtx.state === 'suspended') {
      try { await sharedAudioCtx.resume(); } catch {}
    }
    return sharedAudioCtx;
  }

  function recordUtterance(stream, maxMs, silenceMs, minSpeechMs) {
    let resolveWav;
    const done = new Promise(resolve => { resolveWav = resolve; });
    let stopped = false;
    let heard = false;
    let silentFrames = 0;
    let speechMs = 0;
    let startedAt = 0;
    const chunks = [];
    let ctx;
    let source;
    let proc;
    let mute;
    let timer;
    const silenceLimit = silenceMs || 550;
    const minSpeech = minSpeechMs || 320;

    (async () => {
      try {
        ctx = await audioContextForMic();
        source = ctx.createMediaStreamSource(stream);
        proc = ctx.createScriptProcessor(2048, 1, 1);
        mute = ctx.createGain();
        mute.gain.value = 0;
        const frameMs = (2048 / ctx.sampleRate) * 1000;
        startedAt = performance.now();
        proc.onaudioprocess = ev => {
          if (stopped) return;
          const data = new Float32Array(ev.inputBuffer.getChannelData(0));
          chunks.push(data);
          const elapsed = performance.now() - startedAt;
          // Ignore the first 180ms — mic open / TTS bleed spikes.
          if (elapsed < 180) return;
          let sum = 0;
          for (let i = 0; i < data.length; i += 1) sum += data[i] * data[i];
          const rms = Math.sqrt(sum / Math.max(1, data.length));
          if (rms > 0.012) {
            heard = true;
            speechMs += frameMs;
            silentFrames = 0;
          } else if (heard) {
            silentFrames += 1;
          }
          if (heard && speechMs >= minSpeech && silentFrames * frameMs > silenceLimit) stop();
        };
        source.connect(proc);
        proc.connect(mute);
        mute.connect(ctx.destination);
        timer = setTimeout(stop, maxMs || 6000);
      } catch (err) {
        resolveWav(encodeWav(new Float32Array(0), 16000));
      }
    })();

    function stop() {
      if (stopped) return;
      stopped = true;
      clearTimeout(timer);
      try { proc && proc.disconnect(); source && source.disconnect(); mute && mute.disconnect(); } catch {}
      const merged = flatten(chunks);
      const rate = (ctx && ctx.sampleRate) || 48000;
      const resampled = resample(merged, rate, 16000);
      resolveWav(encodeWav(resampled, 16000));
    }

    return { stop, done };
  }

  async function playOwnedBlob(blob, gen) {
    if (!blob || !blob.size || gen !== speakGen) return;
    if (remoteAudioUrl) URL.revokeObjectURL(remoteAudioUrl);
    remoteAudioUrl = URL.createObjectURL(blob);
    const audio = new Audio(remoteAudioUrl);
    audio.setAttribute('playsinline', 'true');
    remoteAudio = audio;
    try {
      await new Promise((resolve, reject) => {
        audio.onended = resolve;
        audio.onerror = () => reject(new Error('Audio playback failed'));
        const playPromise = audio.play();
        if (playPromise && typeof playPromise.then === 'function') {
          playPromise.catch(reject);
        }
      });
    } finally {
      if (remoteAudio === audio) remoteAudio = null;
      if (gen === speakGen && remoteAudioUrl) {
        URL.revokeObjectURL(remoteAudioUrl);
        remoteAudioUrl = null;
      }
    }
  }

  async function playBlob(blob, opts) {
    const options = opts || {};
    // Never release the mic mid-call — that is a common intermittent Talk failure.
    stopListenOnly(Boolean(options.releaseMic));
    stopPlayback();
    const gen = speakGen;
    await playOwnedBlob(blob, gen);
  }

  async function listenWav(lang, opts) {
    const options = opts || {};
    if (!options.keepMic) await hush();
    else stopListenOnly(false);
    try {
      await ensureMic();
      if (options.onStatus) options.onStatus('Listening…');
      const recorder = recordUtterance(
        micStream,
        options.timeoutMs || 6000,
        options.silenceMs || 550,
        options.minSpeechMs || 320,
      );
      activeRecordStop = recorder.stop;
      return await recorder.done;
    } finally {
      activeRecordStop = null;
      if (!options.keepMic) releaseMic();
    }
  }

  async function listen(lang, opts) {
    const options = opts || {};
    const timeoutMs = options.timeoutMs || 7000;
    await hush();
    try {
      await ensureMic();
      const recorder = recordUtterance(micStream, timeoutMs, 550, 320);
      const skipCloud = Boolean(global.sessionStorage && sessionStorage.getItem('rehabai_voice') === 'local');
      let cloud = '';
      if (!skipCloud && Rec) {
        try {
          cloud = await listenOnce(lang || 'en-IN', 4000, options.onTranscript);
        } catch (err) {
          if (err && (err.code === 'network' || err.code === 'language-not-supported')) {
            try { sessionStorage.setItem('rehabai_voice', 'local'); } catch {}
            if (options.onStatus) {
              options.onStatus('Chrome speech is blocked. Keep talking — this PC will transcribe.');
            }
          } else if (err && err.code !== 'no-speech' && err.code !== 'aborted') {
            recorder.stop();
            throw err;
          }
        }
      } else if (options.onStatus) {
        options.onStatus('Listening on this PC… say a number, then pause.');
      }
      if (cloud) {
        recorder.stop();
        await recorder.done.catch(() => {});
        return cloud;
      }
      const wav = await recorder.done;
      if (typeof options.transcribe !== 'function') {
        throw Object.assign(new Error(voiceErrorMessage('network')), { code: 'network' });
      }
      return ((await options.transcribe(wav, lang || 'en-IN')) || '').trim();
    } finally {
      releaseMic();
    }
  }

  global.RehabIntake = {
    canListen,
    speak,
    listen,
    listenWav,
    playBlob,
    stopVoice,
    stopPlayback,
    pickVoice,
    voiceErrorMessage,
    configure,
    unlockAudio,
    hasSpeech,
  };
})(window);
