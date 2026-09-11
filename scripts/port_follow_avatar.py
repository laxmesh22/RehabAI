"""Port rehab.ai FollowAvatar TypeScript dumps into vanilla ES modules for RehabAI web/."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'


def strip_ts(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.startswith('export type ') or line.startswith('import type '):
            continue
        if re.match(r'^\s*type [A-Za-z]', line):
            continue
        out.append(line)
    text = '\n'.join(out)
    # Drop interface blocks
    text = re.sub(r'export interface \w+[^{]*\{.*?\n\}', '', text, flags=re.S)
    text = re.sub(r'interface \w+[^{]*\{.*?\n\}', '', text, flags=re.S)
    # Remove explicit type annotations on params / vars (best-effort)
    text = re.sub(r':\s*Required<AvatarOptions>', '', text)
    text = re.sub(r':\s*Partial<AvatarOptions>', '', text)
    text = re.sub(r':\s*AvatarOptions', '', text)
    text = re.sub(r':\s*HTMLElement', '', text)
    text = re.sub(r':\s*FollowAvatar', '', text)
    text = re.sub(r':\s*Phase\b', '', text)
    text = re.sub(r':\s*Side\b', '', text)
    text = re.sub(r':\s*View\b', '', text)
    text = re.sub(r':\s*ExerciseId\b', '', text)
    text = re.sub(r':\s*number\s*\|\s*null', '', text)
    text = re.sub(r':\s*number\b', '', text)
    text = re.sub(r':\s*boolean\b', '', text)
    text = re.sub(r':\s*string\b', '', text)
    text = re.sub(r':\s*THREE\.[A-Za-z0-9_<>,\s|\[\]]+', '', text)
    text = re.sub(r':\s*Record<[^>]+>', '', text)
    text = re.sub(r':\s*Set<[^>]+>', '', text)
    text = re.sub(r':\s*Listener<[^>]+>', '', text)
    text = re.sub(r':\s*\{[^}]*\}', '', text)
    text = re.sub(r'<K extends keyof EventMap>', '', text)
    text = re.sub(r'<K extends keyof EventMap>\([^)]*\)', lambda m: m.group(0), text)
    text = re.sub(r' as never', '', text)
    text = re.sub(r' as number', '', text)
    text = re.sub(r' as [A-Za-z][A-Za-z0-9_<>\[\]| ]*', '', text)
    text = re.sub(r'readonly ', '', text)
    text = re.sub(r'private ', '', text)
    text = re.sub(r'public ', '', text)
    text = re.sub(r'\bimplements \w+', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + '\n'


def main():
    motion_ts = (ROOT / 'motion.ts.txt').read_text(encoding='utf-8')
    follow_ts = (ROOT / 'FollowAvatar.ts.txt').read_text(encoding='utf-8')

    motion_js = strip_ts(motion_ts)
    motion_js = motion_js.replace("import * as THREE from 'three'\n", '')
    # Keep exports as ES module
    (WEB / 'motion.js').write_text(
        '/** Pure motion maths for the follow-along model. Ported from rehab.ai motion.ts */\n' + motion_js,
        encoding='utf-8',
    )

    follow_js = strip_ts(follow_ts)
    follow_js = follow_js.replace(
        "import * as THREE from 'three'",
        "import * as THREE from 'three'",
    )
    follow_js = re.sub(
        r"from './motion'",
        "from './motion.js'",
        follow_js,
    )
    # Fix broken emit / on signatures after stripping
    follow_js = follow_js.replace(
        'const emit = (k, v) => { for (const cb of listeners[k]) (cb)(v) }',
        'const emit = (k, v) => { for (const cb of listeners[k]) cb(v) }',
    )
    follow_js = follow_js.replace(
        "const listeners = { angle: new Set(), phase: new Set(), rep: new Set() }",
        "const listeners = { angle: new Set(), phase: new Set(), rep: new Set() }",
    )
    # constructor(readonly sign -> constructor(sign
    follow_js = re.sub(r'constructor\(readonly (\w+)', r'constructor(\1', follow_js)
    follow_js = re.sub(r'constructor\(private (\w+)', r'constructor(\1', follow_js)
    follow_js = re.sub(r'constructor\(readonly (\w+), readonly (\w+)', r'constructor(\1, \2', follow_js)

    (WEB / 'follow_avatar.js').write_text(
        '/** Procedural three.js follow-along coach. Ported from rehab.ai FollowAvatar.ts */\n' + follow_js,
        encoding='utf-8',
    )

    # Bridge exposing window.RehabGuide compatible API
    bridge = r'''/** Bridge: RehabGuide API → FollowAvatar (telemetry-driven live angle). */
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
  ensureCaption();
  const exercise = mapExercise(host.dataset.exercise || window.RehabGuideExercise || 'abduction');
  const side = (host.dataset.side || window.RehabGuideSide || 'right') === 'left' ? 'left' : 'right';
  const target = Number(host.dataset.target || window.RehabGuideTarget || EXERCISES[exercise].defaultTarget);
  avatar = createFollowAvatar(host, {
    exercise,
    affectedSide: side,
    target: Number.isFinite(target) ? target : EXERCISES[exercise].defaultTarget,
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
}

function setCaption(text) {
  lastCue = text ? String(text) : '';
  ensureCaption();
  if (captionEl) captionEl.textContent = lastCue;
}

function stop() {
  if (avatar) {
    try { avatar.dispose(); } catch (_) {}
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
'''
    (WEB / 'guide_follow.js').write_text(bridge, encoding='utf-8')
    print('Wrote web/motion.js, web/follow_avatar.js, web/guide_follow.js')


if __name__ == '__main__':
    main()
