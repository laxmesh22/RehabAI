/** Bridge: RehabGuide API → FollowAvatar (telemetry-driven live angle). */
import { createFollowAvatar } from './follow_avatar.js';
import { EXERCISES } from './motion.js';

const MAP = {
  shoulder_abduction: 'abduction',
  abduction: 'abduction',
  flexion: 'flexion',
  assisted_flexion: 'flexion',
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
  if (key.includes('abduct')) return 'abduction';
  if (key.includes('flex')) return 'flexion';
  if (key.includes('wall')) return 'wall_walk';
  if (key.includes('pendulum')) return 'pendulum';
  if (key.includes('wand') && key.includes('er')) return 'wand_er';
  if (key.includes('er') || key.includes('rotation')) return 'er';
  return 'abduction';
}

let host = null;
let avatar = null;
let captionEl = null;
let lastCue = '';

function ensureCaption() {
  if (!host || captionEl) return;
  captionEl = document.createElement('div');
  captionEl.className = 'guide-caption';
  host.appendChild(captionEl);
}

function mount(selector) {
  stop();
  const el = typeof selector === 'string' ? document.querySelector(selector) : selector;
  if (!el) return;
  host = el;
  host.classList.add('guide-host');
  host.innerHTML = '';
  const stage = document.createElement('div');
  stage.className = 'guide-stage';
  host.appendChild(stage);
  ensureCaption();
  const exercise = mapExercise(host.dataset.exercise || window.RehabGuideExercise || 'abduction');
  const side = (host.dataset.side || window.RehabGuideSide || 'right') === 'left' ? 'left' : 'right';
  const rawTarget = Number(host.dataset.target || window.RehabGuideTarget || EXERCISES[exercise].defaultTarget);
  avatar = createFollowAvatar(stage, {
    exercise,
    affectedSide: side,
    target: Number.isFinite(rawTarget) ? rawTarget : EXERCISES[exercise].defaultTarget,
    mirror: true,
    showArc: true,
    playing: true,
    view: 'auto',
  });
  if (lastCue && captionEl) captionEl.textContent = lastCue;
}

function update(guide) {
  if (!avatar || !guide) return;
  const movement = mapExercise(guide.movement || guide.exercise_id || guide.exercise);
  const side = guide.side === 'left' ? 'left' : 'right';
  const target = guide.target_deg ?? guide.target ?? avatar.effectiveTarget;
  avatar.setOptions({
    exercise: movement,
    affectedSide: side,
    target: Number(target) || avatar.effectiveTarget,
  });
  const live = guide.patient_arm_deg;
  avatar.setLiveAngle(live == null || !Number.isFinite(Number(live)) ? null : Number(live));
  if (guide.cue) setCaption(guide.cue);
  const safety = guide.safety;
  if (safety === 'BLOCK' || safety === 'PAUSE') avatar.pause();
  else avatar.play();
}

function setCaption(text) {
  lastCue = text ? String(text) : '';
  ensureCaption();
  if (captionEl) captionEl.textContent = lastCue;
}

function stop() {
  if (avatar) {
    try { avatar.dispose(); } catch (_) { /* ignore */ }
    avatar = null;
  }
  if (host) {
    host.innerHTML = '';
    host = null;
  }
  captionEl = null;
}

window.RehabGuide = { mount, update, setCaption, stop, mapExercise };
window.RehabFollowAvatar = { create: createFollowAvatar };
