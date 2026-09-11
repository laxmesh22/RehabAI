(function (global) {
  const Rec = global.SpeechRecognition || global.webkitSpeechRecognition;
  let recognition = null;
  let speaking = null;

  function canListen() {
    return Boolean(Rec);
  }

  function stopVoice() {
    if (recognition) {
      try { recognition.onend = null; recognition.onerror = null; recognition.stop(); } catch {}
      recognition = null;
    }
    if (global.speechSynthesis) global.speechSynthesis.cancel();
    speaking = null;
  }

  function pickVoice(lang) {
    const voices = global.speechSynthesis ? global.speechSynthesis.getVoices() : [];
    return voices.find(v => v.lang === lang)
      || voices.find(v => v.lang && v.lang.toLowerCase().startsWith(lang.slice(0, 2).toLowerCase()))
      || null;
  }

  function speak(text, lang) {
    stopListenOnly();
    if (!global.speechSynthesis || !text) return Promise.resolve();
    return new Promise(resolve => {
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = lang || 'en-IN';
      utter.rate = 0.95;
      const voice = pickVoice(utter.lang);
      if (voice) utter.voice = voice;
      utter.onend = () => { speaking = null; resolve(); };
      utter.onerror = () => { speaking = null; resolve(); };
      speaking = utter;
      global.speechSynthesis.speak(utter);
    });
  }

  function stopListenOnly() {
    if (recognition) {
      try { recognition.onend = null; recognition.onerror = null; recognition.stop(); } catch {}
      recognition = null;
    }
  }

  function listen(lang, timeoutMs) {
    if (!Rec) return Promise.reject(new Error('Voice capture needs Chrome or Edge on this workstation.'));
    stopListenOnly();
    return new Promise((resolve, reject) => {
      const rec = new Rec();
      recognition = rec;
      rec.lang = lang || 'en-IN';
      rec.interimResults = true;
      rec.continuous = false;
      rec.maxAlternatives = 1;
      let finalText = '';
      const timer = setTimeout(() => {
        try { rec.stop(); } catch {}
      }, timeoutMs || 8000);
      rec.onresult = ev => {
        let interim = '';
        for (let i = ev.resultIndex; i < ev.results.length; i += 1) {
          const piece = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) finalText += piece;
          else interim += piece;
        }
        if (typeof rec.ontranscript === 'function') rec.ontranscript((finalText || interim).trim());
      };
      rec.onerror = ev => {
        clearTimeout(timer);
        recognition = null;
        reject(new Error(ev.error === 'not-allowed' ? 'Microphone permission was denied.' : 'Voice capture failed.'));
      };
      rec.onend = () => {
        clearTimeout(timer);
        recognition = null;
        resolve((finalText || '').trim());
      };
      rec.start();
    });
  }

  global.RehabIntake = { canListen, speak, listen, stopVoice, pickVoice };
})(window);
