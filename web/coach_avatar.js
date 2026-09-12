/**
 * Coach ↔ 3D FollowAvatar bridge.
 * Mesh pose stays telemetry-driven. LLM only updates caption + clamped demo_target.
 */
(function (global) {
  const state = {
    avatar_phase: null,
    avatar_reps: null,
    avatar_demo_angle: null,
    guide_caption: null,
    bound: false,
  };
  const unsubs = [];

  function publish() {
    const ctx = global.RehabVoiceContext || (global.RehabVoiceContext = {});
    ctx.avatar_phase = state.avatar_phase;
    ctx.avatar_reps = state.avatar_reps;
    ctx.avatar_demo_angle = state.avatar_demo_angle;
    ctx.guide_cue = state.guide_caption || ctx.guide_cue;
    ctx.demo_target = global.RehabGuideTarget;
    global.dispatchEvent?.(new CustomEvent('rehabai-coach-avatar', { detail: { ...state } }));
  }

  function bind() {
    const guide = global.RehabGuide;
    if (!guide?.on || state.bound) {
      // Retry briefly while FollowAvatar mounts.
      if (!state.bound && !bind._tries) bind._tries = 0;
      if (!state.bound && bind._tries < 40) {
        bind._tries += 1;
        setTimeout(bind, 80);
      }
      return;
    }
    state.bound = true;
    unsubs.push(guide.on('phase', (phase) => {
      state.avatar_phase = phase;
      publish();
    }));
    unsubs.push(guide.on('rep', (reps) => {
      state.avatar_reps = reps;
      publish();
    }));
    unsubs.push(guide.on('angle', (deg) => {
      if (deg != null && Number.isFinite(Number(deg))) {
        state.avatar_demo_angle = Math.round(Number(deg));
        publish();
      }
    }));
    const snap = guide.getState?.();
    if (snap) {
      state.avatar_phase = snap.phase ?? state.avatar_phase;
      state.avatar_reps = snap.reps ?? state.avatar_reps;
      state.avatar_demo_angle = snap.demo_angle ?? state.avatar_demo_angle;
    }
    publish();
  }

  function unbind() {
    while (unsubs.length) {
      try { unsubs.pop()(); } catch { /* ignore */ }
    }
    state.bound = false;
    bind._tries = 0;
  }

  /**
   * Apply an LLM / adaptive-coach reply to the 3D guide (caption + demo target only).
   * Never sets bone angles or live patient pose.
   */
  function applyCoachReply(data, opts) {
    const options = opts || {};
    const guide = global.RehabGuide;
    if (!data || !guide) return { applied: false };
    const spoken = (data.spoken || data.caption || '').trim();
    if (spoken) {
      state.guide_caption = spoken;
      guide.setCaption?.(spoken);
      const note = document.getElementById('coach-note');
      if (note) note.textContent = spoken;
    }
    if (data.demo_target != null && Number.isFinite(Number(data.demo_target))) {
      const deg = Number(data.demo_target);
      guide.setDemoTarget?.(deg);
      global.RehabGuideTarget = deg;
      const el = document.getElementById('target-deg');
      if (el) el.textContent = Math.round(deg) + '°';
      const sync = document.getElementById('coach-note');
      if (sync && options.noteDemo) {
        sync.textContent = `3D demo target synced to measured ability · ${Math.round(deg)}°`;
      }
    }
    publish();
    return {
      applied: true,
      bones_from_llm: false,
      driven_by: 'telemetry_not_llm',
      caption: spoken || null,
      demo_target: global.RehabGuideTarget ?? null,
    };
  }

  function getState() {
    return { ...state, demo_target: global.RehabGuideTarget ?? null };
  }

  global.RehabCoachAvatar = {
    bind,
    unbind,
    applyCoachReply,
    getState,
    publish,
  };
})(window);
