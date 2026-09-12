/** Bridge: RehabGuide API → FollowAvatar (telemetry-driven live angle).
 * Source of truth: rehab-ai-avatar-and-pain-check/src/avatar (package zip).
 * LLM/coach may update caption + demo_target only — never bone angles.
 */
import { createFollowAvatar } from './follow_avatar.js?v=35';
import { EXERCISES } from './motion.js?v=34';

const MAP = {
  shoulder_abduction: 'abduction',
  abduction: 'abduction',
  shoulder_flexion: 'flexion',
  flexion: 'flexion',
  assisted_flexion: 'wand_flexion',
  wand_flexion: 'wand_flexion',
  wall_climb: 'wall_walk',
  wall_walk: 'wall_walk',
  pendulum: 'pendulum',
  wand_er: 'wand_er',
  er: 'er',
  external_rotation: 'er',
};

function mapExercise(id) {
  const key = String(id || 'abduction').toLowerCase();
  if (MAP[key]) return MAP[key];
  if (key.includes('wand') && key.includes('er')) return 'wand_er';
  if (key.includes('wand') && key.includes('flex')) return 'wand_flexion';
  if (key.includes('assist') && key.includes('flex')) return 'wand_flexion';
  if (key.includes('abduct')) return 'abduction';
  if (key.includes('wall')) return 'wall_walk';
  if (key.includes('pendulum')) return 'pendulum';
  if (key.includes('flex')) return 'flexion';
  if (key.includes('er') || key.includes('rotation')) return 'er';
  return 'abduction';
}

function resolveDemoId(hint) {
  if (hint) return mapExercise(hint);
  return mapExercise(
    window.RehabGuideAvatar
    || window.RehabGuideExercise
    || 'abduction',
  );
}

let host = null;
let stage = null;
let avatar = null;
let captionEl = null;
let statusEl = null;
let lastCue = '';
let lockedDemo = null;
const bridgeListeners = { phase: new Set(), rep: new Set(), angle: new Set() };
const avatarUnsubs = [];

function setStatus(text, bad) {
  if (!host) return;
  if (!statusEl) {
    statusEl = document.createElement('div');
    statusEl.className = 'guide-status';
    host.appendChild(statusEl);
  }
  statusEl.hidden = !text;
  statusEl.textContent = text || '';
  statusEl.classList.toggle('bad', Boolean(bad));
}

function ensureCaption() {
  if (!host || captionEl) return;
  captionEl = document.createElement('div');
  captionEl.className = 'guide-caption';
  host.appendChild(captionEl);
}

/** Match package preview: size stage before WebGL create so ResizeObserver is not stuck at 1×1. */
function prepareStageSize() {
  if (!host || !stage) return;
  const parent = host.parentElement;
  const availW = Math.max(
    host.clientWidth || 0,
    parent?.clientWidth || 0,
    320,
  );
  const availH = Math.max(
    host.clientHeight || 0,
    Math.round(availW * 0.75),
    360,
  );
  host.style.minHeight = `${availH}px`;
  stage.style.position = 'absolute';
  stage.style.inset = '0';
  stage.style.width = '100%';
  stage.style.height = '100%';
  stage.style.minHeight = `${availH}px`;
  void stage.offsetHeight;
}

function clearAvatarHooks() {
  while (avatarUnsubs.length) {
    try { avatarUnsubs.pop()(); } catch (_) { /* ignore */ }
  }
}

function wireAvatarEvents() {
  clearAvatarHooks();
  if (!avatar?.on) return;
  for (const event of Object.keys(bridgeListeners)) {
    const off = avatar.on(event, (value) => {
      for (const cb of bridgeListeners[event]) {
        try { cb(value); } catch (_) { /* ignore */ }
      }
    });
    if (typeof off === 'function') avatarUnsubs.push(off);
  }
}

function on(event, cb) {
  if (!bridgeListeners[event] || typeof cb !== 'function') return () => {};
  bridgeListeners[event].add(cb);
  return () => bridgeListeners[event].delete(cb);
}

function getState() {
  if (!avatar) {
    return { phase: null, reps: null, demo_angle: null, target: window.RehabGuideTarget ?? null };
  }
  return {
    phase: avatar.phase ?? null,
    reps: avatar.reps ?? null,
    demo_angle: avatar.angle ?? null,
    target: avatar.effectiveTarget ?? window.RehabGuideTarget ?? null,
  };
}

function mount(selector) {
  stop();
  const el = typeof selector === 'string' ? document.querySelector(selector) : selector;
  if (!el) return;
  host = el;
  host.classList.add('guide-host');
  host.innerHTML = '';
  statusEl = null;
  captionEl = null;
  stage = document.createElement('div');
  stage.className = 'guide-stage';
  host.appendChild(stage);
  ensureCaption();
  setStatus('Loading 3D guide…');
  prepareStageSize();

  const demoHint = host.dataset.avatar || window.RehabGuideAvatar
    || host.dataset.exercise || window.RehabGuideExercise || 'abduction';
  const exercise = resolveDemoId(demoHint);
  lockedDemo = exercise;
  const side = (host.dataset.side || window.RehabGuideSide || 'right') === 'left' ? 'left' : 'right';
  const fallbackTarget = EXERCISES[exercise]?.defaultTarget ?? 90;
  const rawTarget = Number(host.dataset.target || window.RehabGuideTarget || fallbackTarget);
  const target = Number.isFinite(rawTarget) ? rawTarget : fallbackTarget;

  try {
    avatar = createFollowAvatar(stage, {
      exercise,
      affectedSide: side,
      target,
      holdS: 5,
      mirror: true,
      showArc: true,
      playing: true,
      view: 'auto',
    });
    wireAvatarEvents();
    setStatus('');
    const kick = () => {
      prepareStageSize();
      avatar?.forceResize?.();
    };
    requestAnimationFrame(() => {
      kick();
      requestAnimationFrame(kick);
    });
    window.addEventListener('resize', kick);
    host._guideKick = kick;
    window.RehabCoachAvatar?.bind?.();
  } catch (err) {
    setStatus('3D guide failed to start. ' + (err?.message || 'Check Three.js load.'), true);
    avatar = null;
  }
  if (lastCue && captionEl) captionEl.textContent = lastCue;
}

function update(guide) {
  if (!avatar || !guide) return;
  // Prefer session exercise / avatar demo over telemetry "movement" so wand_* is not collapsed to er/flexion.
  const demo = resolveDemoId(
    guide.exercise_id
    || guide.exercise
    || window.RehabGuideAvatar
    || window.RehabGuideExercise
    || lockedDemo
    || guide.movement,
  );
  lockedDemo = demo;
  const side = guide.side === 'left' ? 'left' : (guide.side === 'right' ? 'right' : (window.RehabGuideSide || 'right'));
  const target = guide.target_deg ?? guide.target ?? avatar.effectiveTarget;
  avatar.setOptions({
    exercise: demo,
    affectedSide: side === 'left' ? 'left' : 'right',
    target: Number(target) || avatar.effectiveTarget,
  });
  const live = guide.patient_arm_deg ?? guide.angle;
  avatar.setLiveAngle(live == null || !Number.isFinite(Number(live)) ? null : Number(live));
  if (guide.cue) setCaption(guide.cue);
  const safety = guide.safety;
  if (safety === 'BLOCK' || safety === 'PAUSE') avatar.pause();
  else avatar.play();
}

function setDemoTarget(deg) {
  if (!avatar || deg == null || !Number.isFinite(Number(deg))) return;
  avatar.setOptions({ target: Number(deg) });
}

function setCaption(text) {
  lastCue = text ? String(text) : '';
  ensureCaption();
  if (captionEl) captionEl.textContent = lastCue;
}

function stop() {
  window.RehabCoachAvatar?.unbind?.();
  clearAvatarHooks();
  if (host?._guideKick) {
    window.removeEventListener('resize', host._guideKick);
    delete host._guideKick;
  }
  if (avatar) {
    try { avatar.dispose(); } catch (_) { /* ignore */ }
    avatar = null;
  }
  if (host) {
    host.innerHTML = '';
    host = null;
  }
  stage = null;
  captionEl = null;
  statusEl = null;
  lockedDemo = null;
}

window.RehabGuide = {
  mount, update, setCaption, setDemoTarget, stop, mapExercise, on, getState, EXERCISES,
};
window.RehabFollowAvatar = { create: createFollowAvatar };
