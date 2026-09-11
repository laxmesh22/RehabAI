(function (global) {
  /**
   * Mixamo / Ready Player Me guide (same bone names as hmthanh/3d-human-model).
   * Bones come from RehabAI telemetry. The LLM never poses the mesh.
   * Optional photogrammetry: drop a Mixamo-rigged file at /ui/models/guide.glb
   * Do not commit someone else's likeness.
   */
  const DEG = Math.PI / 180;
  let canvas, ctx, raf, latest = null, spoken = '', glbHint = false;
  let shownArm = 14, shownTorso = 0, shownPatientArm = 0, shownPatientTorso = 0;

  function mount(selector) {
    stop();
    canvas = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    tick();
    fetch('/ui/models/guide.glb', { method: 'HEAD' }).then(res => { glbHint = res.ok; }).catch(() => {});
  }

  function update(guide) {
    latest = guide || null;
  }

  function setCaption(text) {
    if (text) spoken = String(text);
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    latest = null;
    spoken = '';
    canvas = null;
    ctx = null;
  }

  function tick() {
    raf = requestAnimationFrame(tick);
    if (!canvas || !ctx) return;
    draw(latest);
  }

  function ident() { return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }
  function mul(a, b) {
    const o = new Array(16);
    for (let i = 0; i < 4; i++) {
      for (let j = 0; j < 4; j++) {
        o[i * 4 + j] = a[i * 4] * b[j] + a[i * 4 + 1] * b[4 + j] + a[i * 4 + 2] * b[8 + j] + a[i * 4 + 3] * b[12 + j];
      }
    }
    return o;
  }
  function T(x, y, z) { const m = ident(); m[3] = x; m[7] = y; m[11] = z; return m; }
  function Rx(a) { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, 0, c, -s, 0, 0, s, c, 0, 0, 0, 0, 1]; }
  function Ry(a) { const c = Math.cos(a), s = Math.sin(a); return [c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1]; }
  function Rz(a) { const c = Math.cos(a), s = Math.sin(a); return [c, -s, 0, 0, s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }
  function euler(rx, ry, rz) { return mul(Rx(rx), mul(Ry(ry), Rz(rz))); }
  function xf(m, p) {
    return [
      m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3],
      m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7],
      m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11],
    ];
  }
  function boneR(bones, name) {
    const b = (bones && bones[name]) || { rx: 0, ry: 0, rz: 0 };
    return euler((b.rx || 0) * DEG, (b.ry || 0) * DEG, (b.rz || 0) * DEG);
  }

  function draw(guide) {
    const w = canvas.width, h = canvas.height;
    ctx.fillStyle = '#10241f';
    ctx.fillRect(0, 0, w, h);
    const g = guide || {
      side: 'right', movement: 'abduction', guide_arm_deg: 14, guide_torso_deg: 0,
      patient_arm_deg: 0, patient_torso_deg: 0, cue: '', safety: 'ALLOW',
      bones: {}, patient_bones: {},
    };
    const blocked = g.safety === 'BLOCK' || g.safety === 'PAUSE';
    const k = blocked ? 0.22 : 0.18;
    shownArm += ((g.guide_arm_deg || 0) - shownArm) * k;
    shownTorso += ((g.guide_torso_deg || 0) - shownTorso) * k;
    shownPatientArm += ((g.patient_arm_deg || 0) - shownPatientArm) * 0.22;
    shownPatientTorso += ((g.patient_torso_deg || 0) - shownPatientTorso) * 0.22;
    const faces = [];
    addFloor(faces);
    const side = g.side || 'right';
    const movement = g.movement || 'abduction';
    addFigure(faces, poseBones(g.bones, side, movement, shownArm, shownTorso), side, false, blocked);
    if (Math.abs(shownPatientTorso) > 8 || Math.abs((g.patient_arm_deg || 0) - (g.guide_arm_deg || 0)) > 18) {
      addFigure(faces, poseBones(g.patient_bones, side, movement, shownPatientArm, shownPatientTorso), side, true, false);
    }
    faces.sort((a, b) => b.z - a.z);
    for (const face of faces) paintFace(face, w, h);
    ctx.fillStyle = 'rgba(13,31,27,0.78)';
    ctx.fillRect(0, h - 64, w, 64);
    ctx.fillStyle = blocked ? '#e7b0a4' : '#e7f0ea';
    ctx.font = '16px Fraunces, Georgia, serif';
    wrapText(spoken || g.cue || 'Copy this movement. Keep the trunk quiet.', 16, h - 38, w - 32, 18);
    ctx.fillStyle = '#9db5ab';
    ctx.font = '12px Outfit, Segoe UI, sans-serif';
    ctx.fillText(
      'Guide ' + Math.round(shownArm) + '°  ·  you ' + Math.round(g.patient_arm_deg || 0) + '°  ·  ' + (g.movement || 'abduction') + (glbHint ? '  ·  GLB ready' : ''),
      16, h - 14
    );
  }

  function armEuler(side, movement, armDeg) {
    const sign = side === 'right' ? 1 : -1;
    if (movement === 'flexion') return { rx: -armDeg, ry: 0, rz: 0 };
    if (movement === 'elevation') return { rx: -armDeg * 0.35, ry: 0, rz: sign * armDeg * 0.75 };
    return { rx: 0, ry: 0, rz: sign * armDeg };
  }

  function poseBones(bones, side, movement, armDeg, torsoDeg) {
    const out = Object.assign({}, bones || {});
    out.Spine = { rx: 0, ry: 0, rz: torsoDeg * 0.55 };
    out.Spine1 = { rx: 0, ry: 0, rz: torsoDeg * 0.45 };
    const name = side === 'left' ? 'LeftArm' : 'RightArm';
    out[name] = armEuler(side, movement, armDeg);
    return out;
  }

  function addFloor(faces) {
    const ring = [];
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * Math.PI * 2;
      ring.push([Math.cos(a) * 0.55, 0.02, Math.sin(a) * 0.32]);
    }
    faces.push({ pts: ring, z: 2.8, fill: '#16362e' });
  }

  function addFigure(faces, bones, side, ghost, blocked) {
    const hips = mul(T(0, 0.96, 0), boneR(bones, 'Hips'));
    const spine = mul(hips, mul(T(0, 0.16, 0), boneR(bones, 'Spine')));
    const spine1 = mul(spine, mul(T(0, 0.12, 0), boneR(bones, 'Spine1')));
    const spine2 = mul(spine1, mul(T(0, 0.12, 0), boneR(bones, 'Spine2')));
    const neck = mul(spine2, mul(T(0, 0.08, 0), boneR(bones, 'Neck')));
    const head = mul(neck, mul(T(0, 0.14, 0), boneR(bones, 'Head')));
    const pine = ghost ? '#c9892a' : '#6fa394';
    const lime = ghost ? '#e7b0a4' : (blocked ? '#e7b0a4' : '#e6ff7a');
    const skin = ghost ? '#d7c4b8' : '#f3f7f1';
    box(faces, hips, 0, -0.06, 0, 0.16, 0.08, 0.09, pine);
    box(faces, spine, 0, 0.02, 0, 0.13, 0.14, 0.08, pine);
    box(faces, spine2, 0, 0.0, 0, 0.17, 0.12, 0.09, pine);
    box(faces, head, 0, 0.02, 0.02, 0.08, 0.09, 0.09, skin);
    const lSh = mul(spine2, mul(T(-0.19, 0.05, 0), boneR(bones, 'LeftShoulder')));
    const rSh = mul(spine2, mul(T(0.19, 0.05, 0), boneR(bones, 'RightShoulder')));
    addArm(faces, lSh, bones, 'LeftArm', 'LeftForeArm', side === 'left' ? lime : pine, side === 'left');
    addArm(faces, rSh, bones, 'RightArm', 'RightForeArm', side === 'right' ? lime : pine, side === 'right');
    const lHip = mul(hips, mul(T(-0.09, -0.08, 0), boneR(bones, 'LeftUpLeg')));
    const rHip = mul(hips, mul(T(0.09, -0.08, 0), boneR(bones, 'RightUpLeg')));
    addLeg(faces, lHip, bones, 'LeftUpLeg', 'LeftLeg', pine);
    addLeg(faces, rHip, bones, 'RightUpLeg', 'RightLeg', pine);
  }

  function addArm(faces, shoulder, bones, armName, forearmName, color, active) {
    const arm = mul(shoulder, boneR(bones, armName));
    const thick = active ? 0.06 : 0.048;
    box(faces, arm, 0, -0.14, 0, thick, 0.14, thick, color);
    const elbow = mul(arm, mul(T(0, -0.28, 0), boneR(bones, forearmName)));
    box(faces, elbow, 0, -0.13, 0, thick * 0.85, 0.13, thick * 0.85, color);
    box(faces, mul(elbow, T(0, -0.27, 0)), 0, 0, 0, 0.04, 0.035, 0.03, color);
  }

  function addLeg(faces, hip, bones, upName, lowName, color) {
    const up = mul(hip, boneR(bones, upName));
    box(faces, up, 0, -0.18, 0, 0.055, 0.18, 0.055, color);
    const knee = mul(up, mul(T(0, -0.36, 0), boneR(bones, lowName)));
    box(faces, knee, 0, -0.18, 0, 0.048, 0.18, 0.048, color);
    box(faces, mul(knee, T(0, -0.36, 0.04)), 0, 0, 0, 0.05, 0.03, 0.09, color);
  }

  function box(faces, m, cx, cy, cz, hx, hy, hz, color) {
    const tm = mul(m, T(cx, cy, cz));
    const c = [
      [-hx, -hy, -hz], [hx, -hy, -hz], [hx, hy, -hz], [-hx, hy, -hz],
      [-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz],
    ].map(p => xf(tm, p));
    const idx = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [0, 3, 7, 4], [1, 2, 6, 5]];
    for (const f of idx) {
      const pts = f.map(i => c[i]);
      const z = (pts[0][2] + pts[1][2] + pts[2][2] + pts[3][2]) / 4;
      const n = normal(pts[0], pts[1], pts[2]);
      const light = Math.max(0.62, Math.min(1.25, n[0] * 0.4 + n[1] * 0.85 + n[2] * 0.5));
      faces.push({ pts, z, fill: shade(color, light) });
    }
  }

  function normal(a, b, c) {
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const len = Math.hypot(n[0], n[1], n[2]) || 1;
    return [n[0] / len, n[1] / len, n[2] / len];
  }

  function shade(hex, t) {
    const n = parseInt(hex.slice(1), 16);
    const r = Math.min(255, ((n >> 16) & 255) * t);
    const g = Math.min(255, ((n >> 8) & 255) * t);
    const b = Math.min(255, (n & 255) * t);
    return 'rgb(' + (r | 0) + ',' + (g | 0) + ',' + (b | 0) + ')';
  }

  function paintFace(face, w, h) {
    const pts = face.pts.map(project);
    if (pts.some(p => !p)) return;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    ctx.fillStyle = face.fill;
    ctx.fill();
  }

  function project(p) {
    const lookY = 1.05, yaw = 0.38, pitch = -0.08, dist = 2.85;
    const dx = p[0], dy = p[1] - lookY, dz = p[2];
    const cy = Math.cos(-yaw), sy = Math.sin(-yaw);
    let x = dx * cy - dz * sy;
    let z = dx * sy + dz * cy;
    const cp = Math.cos(-pitch), sp = Math.sin(-pitch);
    const y = dy * cp - z * sp;
    z = dy * sp + z * cp + dist;
    if (z < 0.2) return null;
    const f = 520 / z;
    return [canvas.width * 0.5 + x * f, canvas.height * 0.62 - y * f];
  }

  function wrapText(text, x, y, max, line) {
    const words = String(text).split(' ');
    let row = '', yy = y;
    for (const word of words) {
      const next = row ? row + ' ' + word : word;
      if (ctx.measureText(next).width > max && row) {
        ctx.fillText(row, x, yy);
        row = word;
        yy += line;
      } else row = next;
    }
    if (row) ctx.fillText(row, x, yy);
  }

  global.RehabGuide = { mount, update, stop, setCaption };
})(window);
