// rehab.ai follow-along model: a procedural three.js mannequin that demonstrates an exercise
// up to THIS patient's approved target, mirrored to their side, with a goniometer arc at the joint.
import * as THREE from 'three'
import {
  EXERCISES, type ExerciseId, type Phase, type Side,
  cycleAt, timingFor, staircase, elevationDir, sagittalIK, wallHeightFor, clampTarget, deg2rad,
} from './motion'

export type { ExerciseId, Phase, Side } from './motion'
export type View = 'auto' | 'front' | 'side' | 'three-quarter'

export interface AvatarOptions {
  exercise: ExerciseId
  /** The patient's affected shoulder. */
  affectedSide: Side
  /** Approved target in degrees (clamped to what the exercise allows). */
  target: number
  holdS?: number
  restS?: number
  /** Average movement speed cap, degrees per second. */
  speedDegPerS?: number
  /**
   * Mirror view (default true). The patient faces the screen, so the model moves the arm that
   * appears on the same side of the screen as the patient's affected arm, like a mirror.
   */
  mirror?: boolean
  view?: View
  showArc?: boolean
  playing?: boolean
}

type EventMap = { angle: number; phase: Phase; rep: number }
type Listener<K extends keyof EventMap> = (value: EventMap[K]) => void

export interface FollowAvatar {
  setOptions(patch: Partial<AvatarOptions>): void
  play(): void
  pause(): void
  /** Restart the current exercise from rest. */
  reset(): void
  /** Show the patient's own live angle on the arc (null hides it). */
  setLiveAngle(deg: number | null): void
  on<K extends keyof EventMap>(event: K, cb: Listener<K>): () => void
  readonly angle: number
  readonly reps: number
  readonly phase: Phase
  /** Target actually used after clamping (e.g. wall walk is limited by reach). */
  readonly effectiveTarget: number
  dispose(): void
}

// ---- proportions (metres) and colours (rehab.ai design language) ----
const UPPER = 0.29, FORE = 0.26, HAND_R = 0.045, HAND_OFFSET = FORE + 0.02
const SH_X = 0.19, SH_Y = 0.5, HIP_Y = 0.95
const WALL_HAND_Z = 0.4
const PENDULUM_LEAN = 55, PENDULUM_PERIOD = 4, PENDULUM_RADIUS = 0.2

const C = {
  bg: 0xeef2ef, body: 0xd3d9d5, arm: 0x6e9e93, teal: 0x2c6e62, ink: 0x1f2b29,
  ground: 0xe1e8e4, prop: 0xf7f8f4, stick: 0x3e4a47,
}

const DOWN = new THREE.Vector3(0, -1, 0)
const UP = new THREE.Vector3(0, 1, 0)
const FORWARD = new THREE.Vector3(0, 0, 1)

const std = (color: number, roughness = 0.85) => new THREE.MeshStandardMaterial({ color, roughness, metalness: 0 })
const overlay = (color: number, opacity: number) =>
  new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, depthTest: false, depthWrite: false, side: THREE.DoubleSide })

const sideSign = (s: Side) => (s === 'left' ? 1 : -1)     // model faces +z, so its left arm is at +x
const other = (s: Side): Side => (s === 'left' ? 'right' : 'left')

// ---------------------------------------------------------------------------------------------
class Arm {
  readonly shoulder = new THREE.Group()
  readonly elbow = new THREE.Group()
  readonly hand: THREE.Mesh
  private readonly meshes: THREE.Mesh[]
  private readonly q1 = new THREE.Quaternion()
  private readonly qt = new THREE.Quaternion()
  private readonly d = new THREE.Vector3()

  constructor(readonly sign: number, material: THREE.Material) {
    this.shoulder.position.set(sign * SH_X, SH_Y, 0)
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.058, 24, 16), material)
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.045, UPPER - 0.09, 8, 16), material)
    upper.position.y = -UPPER / 2
    this.elbow.position.y = -UPPER
    const fore = new THREE.Mesh(new THREE.CapsuleGeometry(0.04, FORE - 0.08, 8, 16), material)
    fore.position.y = -FORE / 2
    this.hand = new THREE.Mesh(new THREE.SphereGeometry(HAND_R, 20, 14), material)
    this.hand.position.y = -HAND_OFFSET
    this.shoulder.add(ball, upper, this.elbow)
    this.elbow.add(fore, this.hand)
    this.meshes = [ball, upper, fore, this.hand]
    for (const m of this.meshes) m.castShadow = true
  }

  /** dir: upper-arm direction in the trunk frame. twist: rotation about the upper arm (+ = toward +x). */
  pose(dir: THREE.Vector3 | [number, number, number], twistDeg: number, elbowFlexDeg: number) {
    if (Array.isArray(dir)) this.d.set(dir[0], dir[1], dir[2])
    else this.d.copy(dir)
    this.d.normalize()
    this.q1.setFromUnitVectors(DOWN, this.d)
    this.qt.setFromAxisAngle(UP, deg2rad(twistDeg))
    this.shoulder.quaternion.copy(this.q1).multiply(this.qt)
    this.elbow.rotation.set(-deg2rad(elbowFlexDeg), 0, 0)   // flexion brings the forearm forward
  }

  relax() {
    this.pose(elevationDir(6, 0, this.sign), 0, 8)
  }

  setMaterial(m: THREE.Material) {
    for (const mesh of this.meshes) mesh.material = m
  }
}

// ---------------------------------------------------------------------------------------------
class Mannequin {
  readonly root = new THREE.Group()
  readonly torso = new THREE.Group()
  readonly arms: Record<Side, Arm>

  constructor(private bodyMat: THREE.Material) {
    const add = (parent: THREE.Object3D, geo: THREE.BufferGeometry, x: number, y: number, z: number,
                 scale?: [number, number, number], rotZ = 0) => {
      const m = new THREE.Mesh(geo, bodyMat)
      m.position.set(x, y, z)
      if (scale) m.scale.set(...scale)
      m.rotation.z = rotZ
      m.castShadow = true
      parent.add(m)
      return m
    }
    // legs and feet stay with the root
    for (const s of [1, -1]) {
      add(this.root, new THREE.CapsuleGeometry(0.068, 0.724, 8, 16), s * 0.095, 0.49, 0)
      add(this.root, new THREE.BoxGeometry(0.1, 0.06, 0.25), s * 0.1, 0.03, 0.06)
    }
    // trunk pivots at the hips (used for the pendulum lean)
    this.torso.position.y = HIP_Y
    this.root.add(this.torso)
    add(this.torso, new THREE.CapsuleGeometry(0.115, 0.13, 8, 16), 0, 0.02, 0, [1, 1, 0.8], Math.PI / 2)
    add(this.torso, new THREE.CapsuleGeometry(0.13, 0.16, 8, 16), 0, 0.17, 0, [1.05, 1, 0.78])
    add(this.torso, new THREE.CapsuleGeometry(0.15, 0.14, 8, 16), 0, 0.36, 0, [1.2, 1, 0.75])
    add(this.torso, new THREE.CylinderGeometry(0.045, 0.05, 0.1, 16), 0, 0.62, 0)
    add(this.torso, new THREE.SphereGeometry(0.105, 32, 20), 0, 0.76, 0.01, [0.92, 1.05, 0.98])
    this.arms = { left: new Arm(1, bodyMat), right: new Arm(-1, bodyMat) }
    this.torso.add(this.arms.left.shoulder, this.arms.right.shoulder)
  }

  highlight(side: Side, armMat: THREE.Material) {
    this.arms[side].setMaterial(armMat)
    this.arms[other(side)].setMaterial(this.bodyMat)
  }
}

// ---------------------------------------------------------------------------------------------
/** The goniometer arc: faint track, teal fill to the current angle, ink tick at the target. */
class GoniometerArc {
  readonly group = new THREE.Group()
  private readonly fill: THREE.Mesh<THREE.RingGeometry, THREE.Material>
  private readonly tick: THREE.Mesh
  private readonly live: THREE.Mesh
  private readonly mat = new THREE.Matrix4()
  private readonly z = new THREE.Vector3()

  constructor(readonly range: number, readonly radius: number) {
    const ri = radius - 0.018, ro = radius + 0.018
    const track = new THREE.Mesh(new THREE.RingGeometry(ri, ro, range, 1, 0, deg2rad(range)), overlay(C.teal, 0.16))
    this.fill = new THREE.Mesh(new THREE.RingGeometry(ri, ro, range, 1, 0, deg2rad(range)), overlay(C.teal, 0.95))
    this.tick = new THREE.Mesh(new THREE.BoxGeometry(0.085, 0.01, 0.01), overlay(C.ink, 0.99))
    this.live = new THREE.Mesh(new THREE.SphereGeometry(0.017, 16, 12), overlay(C.ink, 0.99))
    this.live.visible = false
    track.renderOrder = 10; this.fill.renderOrder = 11; this.tick.renderOrder = 12; this.live.renderOrder = 12
    this.group.add(track, this.fill, this.tick, this.live)
    this.setValue(0)
  }

  /** Arc angle 0 points along xAxis and grows toward yAxis. */
  orient(origin: THREE.Vector3, xAxis: THREE.Vector3, yAxis: THREE.Vector3) {
    this.z.crossVectors(xAxis, yAxis)
    this.mat.makeBasis(xAxis, yAxis, this.z)
    this.group.quaternion.setFromRotationMatrix(this.mat)
    this.group.position.copy(origin)
  }

  setValue(deg: number) {
    const n = Math.round(Math.min(this.range, Math.max(0, deg)))
    this.fill.geometry.setDrawRange(0, n * 6)      // 1 ring segment per degree, 6 indices each
  }

  private place(obj: THREE.Object3D, deg: number) {
    const a = deg2rad(Math.min(this.range, Math.max(0, deg)))
    obj.position.set(Math.cos(a) * this.radius, Math.sin(a) * this.radius, 0)
    obj.rotation.set(0, 0, a)
  }

  setTarget(deg: number) { this.place(this.tick, deg) }

  setLive(deg: number | null) {
    this.live.visible = deg != null && Number.isFinite(deg)
    if (this.live.visible) this.place(this.live, deg as number)
  }
}

// ---------------------------------------------------------------------------------------------
export function createFollowAvatar(container: HTMLElement, initial: AvatarOptions): FollowAvatar {
  const opts: Required<AvatarOptions> = {
    holdS: 5, restS: 1.5, speedDegPerS: 18, mirror: true, view: 'auto', showArc: true, playing: true,
    ...initial,
  }

  // renderer, scene, camera
  const renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.height = '100%'
  renderer.domElement.setAttribute('role', 'img')
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(C.bg)
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 50)

  scene.add(new THREE.HemisphereLight(0xffffff, 0xc5cfca, 1.9))
  const sun = new THREE.DirectionalLight(0xffffff, 2.3)
  sun.position.set(2.5, 4.5, 3.5)
  sun.castShadow = true
  sun.shadow.mapSize.set(1024, 1024)
  Object.assign(sun.shadow.camera, { left: -1.6, right: 1.6, top: 2.2, bottom: -0.6, near: 0.5, far: 12 })
  sun.shadow.camera.updateProjectionMatrix()
  sun.shadow.bias = -0.0005
  sun.shadow.normalBias = 0.02
  sun.target.position.set(0, 0.9, 0)
  scene.add(sun, sun.target)

  const ground = new THREE.Mesh(new THREE.CircleGeometry(1.7, 64), std(C.ground, 1))
  ground.rotation.x = -Math.PI / 2
  ground.receiveShadow = true
  scene.add(ground)

  const bodyMat = std(C.body)
  const armMat = std(C.arm, 0.7)
  const man = new Mannequin(bodyMat)
  scene.add(man.root)

  // props
  const stick = new THREE.Mesh(new THREE.CylinderGeometry(0.013, 0.013, 1, 12), std(C.stick, 0.6))
  stick.castShadow = true
  scene.add(stick)

  const wall = new THREE.Group()
  const wallFace = WALL_HAND_Z + HAND_R
  const wallSlab = new THREE.Mesh(new THREE.BoxGeometry(1.4, 2.3, 0.05), std(C.prop, 0.95))
  wallSlab.position.set(0, 1.15, wallFace + 0.025)
  wallSlab.receiveShadow = true
  const wallLine = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.012, 0.004), std(C.teal, 0.6))
  wall.add(wallSlab, wallLine)
  scene.add(wall)

  const table = new THREE.Group()
  const tableTop = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.05, 0.45), std(C.prop, 0.9))
  const tableLeg = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.05, 1, 16), std(C.stick, 0.7))
  tableTop.castShadow = tableTop.receiveShadow = true
  tableLeg.castShadow = true
  table.add(tableTop, tableLeg)
  scene.add(table)

  // arcs: one per plane, parented to the trunk so they follow it
  const arcShoulder = new GoniometerArc(180, 0.36)
  const arcER = new GoniometerArc(90, 0.25)
  man.torso.add(arcShoulder.group, arcER.group)

  // state
  let elapsed = 0
  let playing = opts.playing
  let angle = 0
  let reps = 0
  let phase: Phase = 'rest'
  let lastRep = -1
  let lastEmittedAngle = NaN
  let effectiveTarget = 0
  let wallTargetHeight = 0
  let liveAngle: number | null = null
  const listeners: { [K in keyof EventMap]: Set<Listener<K>> } = { angle: new Set(), phase: new Set(), rep: new Set() }
  const emit = <K extends keyof EventMap>(k: K, v: EventMap[K]) => { for (const cb of listeners[k]) (cb as Listener<K>)(v) }

  const armSide = () => (opts.mirror ? other(opts.affectedSide) : opts.affectedSide)
  const tmpA = new THREE.Vector3(), tmpB = new THREE.Vector3(), tmpC = new THREE.Vector3()
  const invTorso = new THREE.Quaternion()

  function worldToTrunkDir(x: number, y: number, z: number) {
    invTorso.copy(man.torso.quaternion).invert()
    return tmpC.set(x, y, z).normalize().applyQuaternion(invTorso).clone()
  }

  function configure(resetTime = true) {
    const spec = EXERCISES[opts.exercise]
    const side = armSide()
    const s = sideSign(side)
    effectiveTarget = clampTarget(opts.exercise, opts.target)
    if (opts.exercise === 'wall_walk') {
      const w = wallHeightFor(effectiveTarget, WALL_HAND_Z, UPPER, HAND_OFFSET)
      wallTargetHeight = w.height
      effectiveTarget = Math.round(w.elevation)
    }
    man.highlight(side, armMat)
    man.torso.rotation.set(opts.exercise === 'pendulum' ? deg2rad(PENDULUM_LEAN) : 0, 0, 0)

    stick.visible = spec.prop === 'wand'
    wall.visible = spec.prop === 'wall'
    table.visible = spec.prop === 'table'
    wallLine.position.set(s * SH_X, HIP_Y + SH_Y + wallTargetHeight, wallFace - 0.003)

    // arc placement in the trunk frame
    const shoulderPos = new THREE.Vector3(s * SH_X, SH_Y, 0)
    arcShoulder.group.visible = opts.showArc && (spec.plane === 'sagittal' || spec.plane === 'frontal')
    arcER.group.visible = opts.showArc && spec.plane === 'horizontal'
    if (spec.plane === 'sagittal') {
      arcShoulder.orient(shoulderPos.clone().add(new THREE.Vector3(s * 0.08, 0, 0)), DOWN, FORWARD)
    } else if (spec.plane === 'frontal') {
      arcShoulder.orient(shoulderPos.clone().add(new THREE.Vector3(0, 0, 0.08)), DOWN, new THREE.Vector3(s, 0, 0))
    } else if (spec.plane === 'horizontal') {
      arcER.orient(new THREE.Vector3(s * SH_X, SH_Y - UPPER - 0.03, 0), FORWARD, new THREE.Vector3(s, 0, 0))
    }
    arcShoulder.setTarget(effectiveTarget)
    arcER.setTarget(effectiveTarget)
    arcShoulder.setLive(liveAngle)
    arcER.setLive(liveAngle)

    if (resetTime) {
      elapsed = 0
      lastRep = -1
      reps = 0
      applyPose(0, 0)
      if (spec.prop === 'table') placeTable()
    }
  }

  function placeTable() {
    man.root.updateMatrixWorld(true)
    const hand = man.arms[other(armSide())].hand.getWorldPosition(tmpA)
    const topY = hand.y - HAND_R - 0.025
    tableTop.position.set(hand.x, topY, hand.z + 0.1)
    tableLeg.scale.y = Math.max(0.1, topY - 0.025)
    tableLeg.position.set(hand.x, (topY - 0.025) / 2, hand.z + 0.1)
  }

  /** progress 0..1 toward the target; t = seconds into the exercise (for continuous motion). */
  function applyPose(progress: number, t: number) {
    const side = armSide()
    const s = sideSign(side)
    const moving = man.arms[side]
    const helper = man.arms[other(side)]
    const target = effectiveTarget

    switch (opts.exercise) {
      case 'er': {
        angle = progress * target
        moving.pose(elevationDir(0, 0, s), s * angle, 90)
        helper.relax()
        break
      }
      case 'wand_er': {
        angle = progress * target
        // both forearms turn the same way, so the stick slides sideways at constant length
        moving.pose(elevationDir(0, 0, s), s * angle, 90)
        helper.pose(elevationDir(0, 0, -s), s * angle, 90)
        break
      }
      case 'flexion': {
        angle = progress * target
        moving.pose(elevationDir(angle, 90, s), 0, 6)
        helper.relax()
        break
      }
      case 'abduction': {
        angle = progress * target
        moving.pose(elevationDir(angle, 0, s), 0, 6)
        helper.relax()
        break
      }
      case 'wand_flexion': {
        angle = progress * target
        moving.pose(elevationDir(angle, 90, s), 0, 8)
        helper.pose(elevationDir(angle, 90, -s), 0, 8)
        break
      }
      case 'wall_walk': {
        const h0 = -0.22
        const p = phase === 'move' ? staircase(progress, 6) : progress
        const h = h0 + (wallTargetHeight - h0) * p
        const ik = sagittalIK(WALL_HAND_Z, h, UPPER, HAND_OFFSET)
        angle = Math.max(0, ik.elevation)
        moving.pose(elevationDir(ik.elevation, 90, s), 0, ik.elbowFlex)
        helper.relax()
        break
      }
      case 'pendulum': {
        const phi = (2 * Math.PI * t) / PENDULUM_PERIOD
        const r = PENDULUM_RADIUS * (0.6 + 0.4 * Math.min(1, t / 3))   // circles grow gently at the start
        moving.pose(worldToTrunkDir(r * Math.cos(phi), -1, r * Math.sin(phi)), 0, 6)
        helper.pose(worldToTrunkDir(-s * 0.05, -0.72, 0.62), 0, 15)
        angle = NaN
        break
      }
    }

    if (stick.visible) {
      man.root.updateMatrixWorld(true)
      moving.hand.getWorldPosition(tmpA)
      helper.hand.getWorldPosition(tmpB)
      const len = tmpA.distanceTo(tmpB)
      stick.position.addVectors(tmpA, tmpB).multiplyScalar(0.5)
      stick.quaternion.setFromUnitVectors(UP, tmpB.sub(tmpA).normalize())
      stick.scale.set(1, len + 0.45, 1)
    }
    if (Number.isFinite(angle)) {
      arcShoulder.setValue(angle)
      arcER.setValue(angle)
    }
  }

  // camera
  const camPos = new THREE.Vector3(0, 1.35, 3.6)
  const camLook = new THREE.Vector3(0, 1.1, 0)
  const wantPos = new THREE.Vector3()
  const wantLook = new THREE.Vector3()
  let aspect = 1
  const reduceMotion = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  let snapCamera = true

  function cameraGoal() {
    const spec = EXERCISES[opts.exercise]
    const s = sideSign(armSide())
    let view = opts.view
    if (view === 'auto') {
      view = spec.plane === 'sagittal' ? 'side' : spec.plane === 'frontal' ? 'front' : spec.plane === 'none' ? 'three-quarter' : 'front'
    }
    if (spec.prop === 'wall') view = 'side'           // the wall would hide the model from the front
    if (spec.plane === 'horizontal' && opts.view === 'auto') {
      wantPos.set(s * 0.6, 2.45, 2.55); wantLook.set(0, 1.05, 0.15)       // from above, to see the rotation
    } else if (view === 'front') {
      wantPos.set(0, 1.35, 3.6); wantLook.set(0, 1.12, 0)
    } else if (view === 'side') {
      wantPos.set(s * 3.4, 1.4, 0.45); wantLook.set(0, 1.15, 0.12)
    } else {
      wantPos.set(s * 2.3, 1.6, 2.8); wantLook.set(0, 1.05, 0.15)
    }
    if (aspect < 1) {                              // portrait phones: step back so the model fits
      tmpA.subVectors(wantPos, wantLook).multiplyScalar(Math.pow(1 / aspect, 0.85))
      wantPos.copy(wantLook).add(tmpA)
    }
  }

  function resize() {
    const w = Math.max(1, container.clientWidth)
    const h = Math.max(1, container.clientHeight)
    aspect = w / h
    renderer.setSize(w, h, false)
    camera.aspect = aspect
    camera.updateProjectionMatrix()
  }
  const ro = new ResizeObserver(resize)
  ro.observe(container)
  resize()

  // loop
  let raf = 0
  let last = performance.now()
  function frame(now: number) {
    raf = requestAnimationFrame(frame)
    const dt = Math.min(0.05, (now - last) / 1000)
    last = now
    if (playing) elapsed += dt

    const spec = EXERCISES[opts.exercise]
    if (spec.continuous) {
      const rep = Math.floor(elapsed / PENDULUM_PERIOD)
      if (rep !== lastRep) { if (lastRep >= 0) { reps = rep; emit('rep', reps) } lastRep = rep }
      if (phase !== 'move') { phase = 'move'; emit('phase', phase) }
      applyPose(0, elapsed)
    } else {
      const timing = timingFor(effectiveTarget, opts.speedDegPerS, opts.holdS, opts.restS)
      const c = cycleAt(elapsed, timing)
      if (c.phase !== phase) { phase = c.phase; emit('phase', phase) }
      if (c.rep !== lastRep) { if (lastRep >= 0) { reps = c.rep; emit('rep', reps) } lastRep = c.rep }
      applyPose(c.progress, elapsed)
    }
    if (Number.isFinite(angle) && Math.round(angle) !== lastEmittedAngle) {
      lastEmittedAngle = Math.round(angle)
      emit('angle', lastEmittedAngle)
    }

    cameraGoal()
    const k = snapCamera || reduceMotion ? 1 : 1 - Math.exp(-dt * 5)
    camPos.lerp(wantPos, k)
    camLook.lerp(wantLook, k)
    snapCamera = false
    camera.position.copy(camPos)
    camera.lookAt(camLook)
    renderer.render(scene, camera)
  }

  configure()
  raf = requestAnimationFrame(frame)

  return {
    setOptions(input) {
      const patch = Object.fromEntries(Object.entries(input).filter(([, v]) => v !== undefined)) as Partial<AvatarOptions>
      const changed = (keys: string[]) => keys.some((k) => k in patch &&
        (patch as Record<string, unknown>)[k] !== (opts as Record<string, unknown>)[k])
      const restart = changed(['exercise', 'affectedSide', 'mirror'])
      const soft = changed(['target', 'holdS', 'restS', 'speedDegPerS', 'showArc'])
      Object.assign(opts, patch)
      if ('playing' in patch && patch.playing !== undefined) playing = patch.playing
      if (restart) configure(true)
      else if (soft) configure(false)
    },
    play() { playing = true },
    pause() { playing = false },
    reset() { configure() },
    setLiveAngle(deg) {
      liveAngle = deg
      arcShoulder.setLive(deg)
      arcER.setLive(deg)
    },
    on(event, cb) {
      listeners[event].add(cb as never)
      return () => listeners[event].delete(cb as never)
    },
    get angle() { return angle },
    get reps() { return reps },
    get phase() { return phase },
    get effectiveTarget() { return effectiveTarget },
    dispose() {
      cancelAnimationFrame(raf)
      ro.disconnect()
      scene.traverse((o) => {
        const m = o as THREE.Mesh
        if (m.geometry) m.geometry.dispose()
        const mat = m.material as THREE.Material | THREE.Material[] | undefined
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
        else mat?.dispose()
      })
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
