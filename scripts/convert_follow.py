"""Convert FollowAvatar.ts.txt → web/follow_avatar.js (no TypeScript)."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / 'FollowAvatar.ts.txt').read_text(encoding='utf-8')

# Drop header comments imports — rebuild
body = src
# Remove import type / export type / interfaces
body = re.sub(r'^import \* as THREE from \'three\'\n', '', body, flags=re.M)
body = re.sub(r"^import \{\n  EXERCISES, type ExerciseId, type Phase, type Side,\n  cycleAt, timingFor, staircase, elevationDir, sagittalIK, wallHeightFor, clampTarget, deg2rad,\n\} from './motion'\n", '', body, flags=re.M)
body = re.sub(r'^export type .*$', '', body, flags=re.M)
body = re.sub(r'export interface AvatarOptions \{.*?\n\}', '', body, flags=re.S)
body = re.sub(r'type EventMap = \{.*?\n\}', '', body, flags=re.S)
body = re.sub(r'type Listener<.*?> = .*?\n', '', body)
body = re.sub(r'export interface FollowAvatar \{.*?\n\}', '', body, flags=re.S)

# Class field type annotations
replacements = [
    (r'readonly shoulder = ', 'shoulder = '),
    (r'readonly elbow = ', 'elbow = '),
    (r'readonly hand: THREE\.Mesh', 'hand'),
    (r'private readonly meshes: THREE\.Mesh\[\]', 'meshes'),
    (r'private readonly q1 = ', 'q1 = '),
    (r'private readonly qt = ', 'qt = '),
    (r'private readonly d = ', 'd = '),
    (r'constructor\(readonly sign: number, material: THREE\.Material\)', 'constructor(sign, material)'),
    (r'pose\(dir: THREE\.Vector3 \| \[number, number, number\], twistDeg: number, elbowFlexDeg: number\)', 'pose(dir, twistDeg, elbowFlexDeg)'),
    (r'setMaterial\(m: THREE\.Material\)', 'setMaterial(m)'),
    (r'readonly root = ', 'root = '),
    (r'readonly torso = ', 'torso = '),
    (r'readonly arms: Record<Side, Arm>', 'arms'),
    (r'constructor\(private bodyMat: THREE\.Material\)', 'constructor(bodyMat)'),
    (r'add = \(parent: THREE\.Object3D, geo: THREE\.BufferGeometry, x: number, y: number, z: number,\n\s*scale\?: \[number, number, number\], rotZ = 0\)',
     'add = (parent, geo, x, y, z, scale, rotZ = 0)'),
    (r'highlight\(side: Side, armMat: THREE\.Material\)', 'highlight(side, armMat)'),
    (r'readonly group = ', 'group = '),
    (r'private readonly fill: THREE\.Mesh<THREE\.RingGeometry, THREE\.Material>', 'fill'),
    (r'private readonly tick: THREE\.Mesh', 'tick'),
    (r'private readonly live: THREE\.Mesh', 'live'),
    (r'private readonly mat = ', 'mat = '),
    (r'private readonly z = ', 'z = '),
    (r'constructor\(readonly range: number, readonly radius: number\)', 'constructor(range, radius)'),
    (r'orient\(origin: THREE\.Vector3, xAxis: THREE\.Vector3, yAxis: THREE\.Vector3\)', 'orient(origin, xAxis, yAxis)'),
    (r'setValue\(deg: number\)', 'setValue(deg)'),
    (r'private place\(obj: THREE\.Object3D, deg: number\)', 'place(obj, deg)'),
    (r'setTarget\(deg: number\)', 'setTarget(deg)'),
    (r'setLive\(deg: number \| null\)', 'setLive(deg)'),
    (r'export function createFollowAvatar\(container: HTMLElement, initial: AvatarOptions\): FollowAvatar',
     'export function createFollowAvatar(container, initial)'),
    (r'const opts: Required<AvatarOptions> = ', 'const opts = '),
    (r'let phase: Phase = ', 'let phase = '),
    (r'let liveAngle: number \| null = null', 'let liveAngle = null'),
    (r'const listeners: \{ \[K in keyof EventMap\]: Set<Listener<K>> \} = ', 'const listeners = '),
    (r'const emit = <K extends keyof EventMap>\(k: K, v: EventMap\[K\]\) => \{ for \(const cb of listeners\[k\]\) \(cb as Listener<K>\)\(v\) \}',
     'const emit = (k, v) => { for (const cb of listeners[k]) cb(v) }'),
    (r'const armSide = \(\) => \(opts\.mirror \? other\(opts\.affectedSide\) : opts\.affectedSide\)',
     'const armSide = () => (opts.mirror ? other(opts.affectedSide) : opts.affectedSide)'),
    (r'function configure\(resetTime = true\)', 'function configure(resetTime = true)'),
    (r'function applyPose\(progress: number, t: number\)', 'function applyPose(progress, t)'),
    (r'function frame\(now: number\)', 'function frame(now)'),
    (r'setOptions\(input\)', 'setOptions(input)'),
    (r'const patch = Object\.fromEntries\(Object\.entries\(input\)\.filter\(\(\[, v\]\) => v !== undefined\)\) as Partial<AvatarOptions>',
     'const patch = Object.fromEntries(Object.entries(input).filter(([, v]) => v !== undefined))'),
    (r'const changed = \(keys: string\[\]\) => keys\.some\(\(k\) => k in patch &&\n\s*\(patch as Record<string, unknown>\)\[k\] !== \(opts as Record<string, unknown>\)\[k\]\)',
     'const changed = (keys) => keys.some((k) => k in patch && patch[k] !== opts[k])'),
    (r'\(cb as never\)', 'cb'),
    (r' as number', ''),
    (r'const other = \(s: Side\): Side =>', 'const other = (s) =>'),
    (r'const sideSign = \(s: Side\) =>', 'const sideSign = (s) =>'),
    (r'const std = \(color: number, roughness = 0\.85\) =>', 'const std = (color, roughness = 0.85) =>'),
    (r'const overlay = \(color: number, opacity: number\) =>', 'const overlay = (color, opacity) =>'),
    (r'on\(event, cb\)', 'on(event, cb)'),
    (r'setLiveAngle\(deg\)', 'setLiveAngle(deg)'),
]

for a, b in replacements:
    body = re.sub(a, b, body)

# Fix add() optional scale — if scale undefined skip
body = body.replace(
    'if (scale) m.scale.set(...scale)',
    'if (scale) m.scale.set(scale[0], scale[1], scale[2])',
)

# Fix Arm constructor: need to assign this.sign
if 'constructor(sign, material)' in body and 'this.sign = sign' not in body:
    body = body.replace(
        'constructor(sign, material) {\n    this.shoulder.position.set',
        'constructor(sign, material) {\n    this.sign = sign\n    this.shoulder.position.set',
    )

# GoniometerArc: assign range/radius
if 'constructor(range, radius) {' in body and 'this.range = range' not in body:
    body = body.replace(
        'constructor(range, radius) {\n    const ri',
        'constructor(range, radius) {\n    this.range = range\n    this.radius = radius\n    const ri',
    )

# Mannequin bodyMat
if 'constructor(bodyMat) {' in body and 'this.bodyMat = bodyMat' not in body:
    body = body.replace(
        'constructor(bodyMat) {\n    const add',
        'constructor(bodyMat) {\n    this.bodyMat = bodyMat\n    const add',
    )

header = '''/** Procedural three.js follow-along coach. Ported from rehab.ai FollowAvatar.ts */
import * as THREE from 'three';
import {
  EXERCISES, cycleAt, timingFor, staircase, elevationDir, sagittalIK, wallHeightFor, clampTarget, deg2rad,
} from './motion.js';

'''

# Clean empty lines
body = re.sub(r'\n{3,}', '\n\n', body).strip() + '\n'
(ROOT / 'web' / 'follow_avatar.js').write_text(header + body, encoding='utf-8')
print('wrote follow_avatar.js', len(body.splitlines()), 'lines')
