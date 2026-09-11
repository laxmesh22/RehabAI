const $ = (sel, el = document) => el.querySelector(sel);
const app = $('#app');
const state = { token: localStorage.getItem('rehabai_token'), user: null, health: null, tab: 'overview' };
let ws, wsTimer, liveCanvas;

const routes = {
  clinician: [
    ['#/dashboard', 'Dashboard'],
    ['#/patients', 'Patients'],
    ['#/alerts', 'Alerts'],
    ['#/reports', 'Reports'],
    ['#/assistant', 'AI Assistant'],
    ['#/appointments', 'Appointments'],
    ['#/recordings', 'Recordings'],
    ['#/staff', 'Staff'],
    ['#/settings', 'Settings'],
  ],
  patient: [
    ['#/home', 'Home'],
    ['#/progress', 'Progress'],
    ['#/appointments', 'Appointments'],
  ],
};

async function api(path, body, method) {
  const options = { headers: {} };
  if (state.token) options.headers.Authorization = 'Bearer ' + state.token;
  if (body !== undefined || method === 'POST') {
    options.method = method || 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body || {});
  }
  const res = await fetch('/api/' + path.replace(/^\//, ''), options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map(e => e.msg || e).join('; ') : (d || data.error || data.message || res.statusText));
  }
  return data;
}

function detail(err) {
  if (Array.isArray(err)) return err.map(e => e.msg).join('; ');
  return typeof err === 'string' ? err : JSON.stringify(err);
}

window.addEventListener('unhandledrejection', () => {});

function goto(hash) {
  location.hash = hash;
}

async function route() {
  try {
    const hash = location.hash || (state.user?.role === 'PATIENT' ? '#/home' : '#/dashboard');
    if (!state.token) return renderLogin();
    const live = hash.match(/^#\/live\/([^/]+)/);
    if (live) return await renderLive(live[1]);
    const patient = hash.match(/^#\/patients\/([^/]+)/);
    if (patient) return await renderPatient(patient[1]);
    const rec = hash.match(/^#\/recordings\/([^/]+)/);
    if (rec) return await renderRecording(rec[1]);
    const report = hash.match(/^#\/reports\/([^/]+)/);
    if (report) return await renderReport(report[1]);
    const map = {
      '#/dashboard': renderDashboard,
      '#/patients': renderPatients,
      '#/alerts': renderAlerts,
      '#/reports': renderReports,
      '#/assistant': renderAssistant,
      '#/appointments': renderAppointments,
      '#/recordings': renderRecordings,
      '#/staff': renderStaff,
      '#/settings': renderSettings,
      '#/home': renderPatientHome,
      '#/progress': renderPatientProgress,
    };
    const view = map[hash] || (state.user?.role === 'PATIENT' ? renderPatientHome : renderDashboard);
    await view();
  } catch (e) {
    app.innerHTML = `<p class="error">${e.message}</p>`;
  }
}

function shell(content, active) {
  const nav = (state.user.role === 'PATIENT' ? routes.patient : routes.clinician)
    .filter(item => state.user.role === 'ADMIN' || item[0] !== '#/staff' || state.user.role === 'ADMIN')
    .map(([href, label]) => `<a class="${active === href ? 'active' : ''}" href="${href}">${label}</a>`).join('');
  const mode = state.health?.source === 'live' ? 'Live camera · unvalidated' : 'Simulation mode';
  app.innerHTML = `<div class="app-shell">
    <aside class="rail">
      <div class="brand"><strong>RehabAI</strong><span>studio</span></div>
      ${nav}
      <div class="spacer"></div>
      <div class="who">${state.user.full_name}<br>${state.user.role}</div>
      <button class="nav" id="logout">Sign out</button>
    </aside>
    <div class="main">
      <div class="topbar">
        <div>${content.header || ''}</div>
        <div class="mode-pill ${state.health?.source === 'live' ? 'live' : ''}">${mode}</div>
      </div>
      ${content.body}
    </div>
  </div>`;
  $('#logout')?.addEventListener('click', () => { localStorage.removeItem('rehabai_token'); state.token = null; goto('#/login'); renderLogin(); });
}

function renderLogin() {
  app.innerHTML = `<div class="login-shell">
    <section class="login-hero">
      <div>
        <p class="eyebrow">MEDHA · PS 8</p>
        <h1>A digital twin of the shoulder journey.</h1>
        <p>RehabAI measures movement, guides rehabilitation, and keeps a longitudinal record. The clinician stays responsible for diagnosis and treatment.</p>
      </div>
      <p>Intel RealSense · Jetson Orin Nano · FastAPI · AI supervisor</p>
    </section>
    <section class="login-panel">
      <p class="eyebrow">Sign in</p>
      <h2>Hospital workstation</h2>
      <form id="login">
        <label class="field">Email <input name="email" value="priya.mehta@hospital.local" autocomplete="username"></label>
        <label class="field">Password <input name="password" type="password" value="rehabai-demo" autocomplete="current-password"></label>
        <button class="primary" type="submit">Enter clinic</button>
        <p class="error" id="err" hidden></p>
      </form>
      <div class="accounts">
        <button data-email="priya.mehta@hospital.local">Physiotherapist · Priya Mehta</button>
        <button data-email="ananya.sharma@demo.local">Patient · Ananya Sharma (demo)</button>
        <button data-email="dr.rao@hospital.local">Doctor · Vikram Rao</button>
        <button data-email="admin@hospital.local">Admin · Maya Iyer</button>
      </div>
    </section>
  </div>`;
  const form = $('#login');
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(form));
      const res = await api('auth/login', body);
      state.token = res.token; state.user = res.user;
      localStorage.setItem('rehabai_token', res.token);
      goto(res.user.role === 'PATIENT' ? '#/home' : '#/dashboard');
      await route();
    } catch (e) {
      $('#err').hidden = false; $('#err').textContent = e.message;
    }
  });
  document.querySelectorAll('.accounts button').forEach(btn => btn.addEventListener('click', () => {
    form.email.value = btn.dataset.email;
    form.password.value = 'rehabai-demo';
  }));
}

async function renderDashboard() {
  const data = await api('dashboard');
  shell({
    header: `<p class="eyebrow">Clinic today</p><h1 class="serif">Shoulder studio</h1>`,
    body: `<div class="cards">
      ${stat('Today’s patients', data.today_patients)}
      ${stat('Assessments today', data.assessments_today)}
      ${stat('Sessions completed', data.sessions_completed_today)}
      ${stat('Need review', data.patients_requiring_review)}
      ${stat('Adherence', data.average_adherence == null ? '—' : data.average_adherence + '%')}
    </div>
    <div class="grid-2" style="margin-top:18px">
      <div class="panel"><h2>Recent patients</h2>${patientTable(data.recent_patients)}</div>
      <div class="panel"><h2>Clinical alerts</h2>${alertList(data.alerts)}</div>
    </div>`
  }, '#/dashboard');
}

async function renderPatients() {
  const rows = await api('patients');
  shell({
    header: `<p class="eyebrow">Caseload</p><h1 class="serif">Patients</h1>`,
    body: `<div class="panel">${patientTable(rows)}
      ${state.user.role === 'ADMIN' ? '<p class="empty">Admins can view records but do not start treatment.</p>' : ''}
    </div>`
  }, '#/patients');
}

async function renderPatient(id) {
  const [patient, assessments, sessions, rom, pain, reports, notes, recordings, plans] = await Promise.all([
    api('patients/' + id),
    api(`patients/${id}/assessments`),
    api(`patients/${id}/sessions`),
    api(`patients/${id}/rom`),
    api(`patients/${id}/pain`),
    api('reports?patient_id=' + id),
    api(`patients/${id}/notes`),
    api(`patients/${id}/recordings`),
    api('plans?patient_id=' + id),
  ]);
  state.tab = state.tab || 'overview';
  const p = patient.progress || {};
  const tabs = ['overview','assessment','ROM','pain','exercises','sessions','recordings','progress','AI','notes','reports'];
  shell({
    header: `<p class="eyebrow">${patient.is_demo ? 'DEMO PATIENT' : 'PATIENT'} · ${patient.mrn}</p>
      <h1 class="serif">${patient.full_name}</h1>
      <p>${patient.clinician_diagnosis || ''} · Affected ${patient.affected_side} shoulder</p>`,
    body: `<div class="row" style="margin-bottom:16px">
        ${state.user.role !== 'ADMIN' ? `<button class="primary" id="start-assess">Start assessment</button>
        <button class="ghost" id="start-rehab">Start rehab session</button>` : ''}
      </div>
      <div class="tabs">${tabs.map(t => `<button data-tab="${t}" class="${state.tab===t?'active':''}">${t}</button>`).join('')}</div>
      <div id="tab"></div>`
  }, '#/patients');
  const tabEl = $('#tab');
  const paint = () => {
    if (state.tab === 'overview') {
      tabEl.innerHTML = `<div class="cards">
        ${stat('Pain now', p.pain_movement ? p.pain_movement.current + '/10' : '—')}
        ${stat('Abduction', p.abduction ? p.abduction.current + '°' : '—')}
        ${stat('Flexion', p.flexion ? p.flexion.current + '°' : '—')}
        ${stat('Compensation', p.torso_compensation ? p.torso_compensation.current + '°' : '—')}
        ${stat('Sessions', sessions.filter(s => s.status==='complete').length)}
      </div>
      <div class="panel" style="margin-top:16px">
        <p>Baseline abduction ${p.abduction?.baseline ?? '—'}° → current ${p.abduction?.current ?? '—'}° (${fmtDelta(p.abduction?.change)})</p>
        <p>No recovery percentage is computed. Changes are shown in degrees and pain scores only.</p>
        ${p.demo_records_present ? '<p class="badge demo">Includes labelled demo / synthetic records</p>' : ''}
      </div>`;
    } else if (state.tab === 'assessment') {
      tabEl.innerHTML = `<div class="panel">${table(['Date','Source','Abd','Flex','Pain','Lean'], assessments.map(a => [
        new Date(a.created_at).toLocaleDateString(), a.source, deg(a.abduction_max), deg(a.flexion_max),
        a.pain_movement + '/10', deg(a.torso_compensation)
      ]))}</div>`;
    } else if (state.tab === 'ROM' || state.tab === 'progress') {
      tabEl.innerHTML = `<div class="grid-2">
        <div class="panel"><h2>Abduction over time</h2>${chart(rom.filter(r => r.movement==='abduction').map(r => r.value), '#7aa24a')}</div>
        <div class="panel"><h2>Flexion over time</h2>${chart(rom.filter(r => r.movement==='flexion').map(r => r.value), '#2f6b4f')}</div>
      </div>`;
    } else if (state.tab === 'pain') {
      tabEl.innerHTML = `<div class="panel"><h2>Pain during movement</h2>${chart(pain.map(r => r.movement), '#b94a32')}</div>`;
    } else if (state.tab === 'sessions' || state.tab === 'exercises') {
      tabEl.innerHTML = `<div class="panel">${table(['Session','Exercise','Reps','Peak','Source'], sessions.map(s => [
        s.id, s.exercise_id, `${s.reps}/${s.goal}`, deg(s.peak_angle), s.source
      ]))}</div>`;
    } else if (state.tab === 'recordings') {
      tabEl.innerHTML = `<div class="panel">${recordings.map(r => `<div class="history-item"><a href="#/recordings/${r.id}">${r.session_id}</a> · ${r.kind}</div>`).join('') || '<p class="empty">No consented recordings.</p>'}</div>`;
    } else if (state.tab === 'AI') {
      tabEl.innerHTML = assistantPanel(id);
      bindAssistant(id);
    } else if (state.tab === 'notes') {
      tabEl.innerHTML = `<div class="panel">${notes.map(n => `<p><strong>${n.author}</strong> · ${new Date(n.created_at).toLocaleString()}<br>${n.body}</p>`).join('')}</div>`;
    } else if (state.tab === 'reports') {
      tabEl.innerHTML = `<div class="panel">${reports.map(r => `<p><a href="#/reports/${r.id}">${r.kind} · ${r.id}</a> ${r.requires_approval ? '<span class="badge warn">pending</span>' : '<span class="badge">approved</span>'}</p>`).join('')}</div>`;
    }
  };
  paint();
  document.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', () => { state.tab = btn.dataset.tab; document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b===btn)); paint(); }));
  $('#start-assess')?.addEventListener('click', () => startLive(id, 'shoulder_abduction', true));
  $('#start-rehab')?.addEventListener('click', () => startLive(id, 'shoulder_abduction', false));
}

async function startLive(patientId, exercise, assessment) {
  const session = await api('sessions', {
    patient_id: patientId, exercise_id: exercise, side: 'right', target: 80, goal: 5,
    pain_before: 4, consent_recording: true, kind: assessment ? 'assessment' : 'rehab'
  });
  goto('#/live/' + session.id);
}

async function renderLive(sessionId, opts = {}) {
  const session = await api('sessions/' + sessionId);
  closeLive();
  shell({
    header: `<p class="eyebrow">Live ${session.source === 'live' ? 'camera' : 'simulation'}</p>
      <h1 class="serif">${session.exercise_id.replaceAll('_',' ')}</h1>`,
    body: `<div class="checks" id="calib"></div>
      <div class="grid-live">
        <div class="view">
          <div class="view-label" id="view-label">${session.source === 'live' ? 'LIVE RGB-D · POSE OVERLAY' : 'SYNTHETIC SKELETON · NOT A CAMERA FEED'}</div>
          <canvas id="skel" width="640" height="480"></canvas>
        </div>
        <div>
          <div class="metrics">
            <div class="metric"><span>Current ROM</span><b id="ang">—°</b></div>
            <div class="metric"><span>Target</span><b>${session.target}°</b></div>
            <div class="metric"><span>Reps</span><b id="reps" data-goal="${session.goal}">0 / ${session.goal}</b></div>
            <div class="metric"><span>Torso lean</span><b id="lean">—°</b></div>
            <div class="metric"><span>Tracking</span><b id="conf">—</b></div>
            <div class="metric"><span>Quality</span><b id="cov">—</b></div>
          </div>
          <p class="feedback" id="fb">Stand in the marked area. Hold a relaxed posture.</p>
          <p id="sub" class="empty"></p>
          <div class="row">
            <label class="field" style="margin:0">Pain after <input id="pain-after" type="number" min="0" max="10" value="4" style="width:72px"></label>
            <button class="primary" id="confirm">Confirm tracking & start</button>
            <button class="danger" id="finish">Stop & save</button>
          </div>
          ${session.source === 'simulation' ? `<div class="row" style="margin-top:12px">
            <span class="eyebrow">Simulation controls</span>
            <button class="ghost" data-fault="none">Normal</button>
            <button class="ghost" data-fault="lean">Lean sideways</button>
            <button class="ghost" data-fault="occlusion">Tracking lost</button>
          </div>` : ''}
        </div>
      </div>`
  }, '#/patients');
  liveCanvas = $('#skel');
  $('#confirm').addEventListener('click', async () => {
    try { await api(`sessions/${sessionId}/confirm`, {}); }
    catch (e) { $('#sub').textContent = e.message; }
  });
  $('#finish').addEventListener('click', async () => {
    const pain = Number($('#pain-after').value);
    if (!Number.isInteger(pain) || pain < 0 || pain > 10) { $('#sub').textContent = 'Enter pain 0–10 before saving.'; return; }
    const result = await api(`sessions/${sessionId}/finish`, {
      pain_after: pain, create_assessment: true, pain_rest: Math.max(0, pain - 2),
      difficulty_overhead: 2, difficulty_behind_back: 2, difficulty_dressing: 1, difficulty_grooming: 1
    });
    closeLive();
    state.tab = 'progress';
    goto('#/patients/' + session.patient_id);
  });
  document.querySelectorAll('[data-fault]').forEach(btn => btn.addEventListener('click', () => api(`sessions/${sessionId}/fault`, { fault: btn.dataset.fault })));
  connectLive(sessionId);
}

function connectLive(sessionId) {
  closeLive();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/api/ws/sessions/${sessionId}?token=${encodeURIComponent(state.token)}`);
  ws.onmessage = ev => applyTelemetry(JSON.parse(ev.data));
  ws.onclose = () => { wsTimer = setTimeout(() => connectLive(sessionId), 800); };
}

function applyTelemetry(row) {
  if (row.type === 'ended') return;
  const cal = row.calibration || {};
  const checks = [
    ['Camera', cal.simulation ? 'SIM' : cal.camera_ok],
    ['Depth', cal.simulation ? 'SIM' : cal.depth_ok],
    ['Pose', cal.pose_ok],
    ['Distance', cal.distance_ok],
  ];
  const calib = $('#calib');
  if (calib) calib.innerHTML = checks.map(([n, ok]) => `<div class="check ${ok===true || ok==='SIM' ? 'on' : 'off'}"><strong>${n}</strong><div>${ok === 'SIM' ? 'Simulation' : ok ? 'OK' : 'Not ready'}</div></div>`).join('');
  if ($('#ang')) $('#ang').textContent = row.valid ? Math.round(row.shoulder_angle) + '°' : '—';
  if ($('#lean')) $('#lean').textContent = Math.round(row.torso_lean || 0) + '°';
  if ($('#reps')) $('#reps').textContent = `${row.rep || 0} / ${$('#reps').dataset.goal}`;
  if ($('#conf')) $('#conf').textContent = Math.round((row.pose_confidence || 0) * 100) + '%';
  if ($('#cov')) $('#cov').textContent = (row.coverage ?? '—') + '%';
  if ($('#fb')) $('#fb').textContent = row.feedback || '';
  if ($('#sub')) $('#sub').textContent = (row.safety?.level ? 'Safety ' + row.safety.level : '') + (row.source === 'simulation' ? ' · synthetic stream' : ' · live stream');
  drawSkeleton(row);
}

function drawSkeleton(row) {
  const canvas = liveCanvas || $('#skel');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  if (row.frame_jpeg) {
    const img = new Image();
    img.onload = () => { ctx.drawImage(img, 0, 0, w, h); };
    img.src = 'data:image/jpeg;base64,' + row.frame_jpeg;
    return;
  }
  ctx.fillStyle = '#10241f';
  ctx.fillRect(0, 0, w, h);
  const pts = (row.overlay && row.overlay.points) || {};
  const bones = (row.overlay && row.overlay.bones) || [];
  ctx.lineWidth = 6; ctx.lineCap = 'round';
  for (const bone of bones) {
    const a = pts[bone.a], b = pts[bone.b];
    if (!a || !b) continue;
    ctx.strokeStyle = bone.active ? '#d7f56a' : '#7f9b90';
    ctx.beginPath();
    ctx.moveTo(a[0] * w, a[1] * h);
    ctx.lineTo(b[0] * w, b[1] * h);
    ctx.stroke();
  }
  for (const [name, p] of Object.entries(pts)) {
    ctx.fillStyle = name.includes('right') ? '#d7f56a' : '#e7f0ea';
    ctx.beginPath();
    ctx.arc(p[0] * w, p[1] * h, 7, 0, Math.PI * 2);
    ctx.fill();
  }
}

function closeLive() {
  if (wsTimer) clearTimeout(wsTimer);
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
}

async function renderAlerts() {
  const rows = await api('alerts');
  shell({ header: `<h1 class="serif">Alerts</h1>`, body: `<div class="panel">${alertList(rows)}</div>` }, '#/alerts');
}

async function renderReports() {
  const rows = await api('reports');
  shell({ header: `<h1 class="serif">Reports</h1>`, body: `<div class="panel">${rows.map(r => `<p><a href="#/reports/${r.id}">${r.patient_id} · ${r.kind}</a></p>`).join('')}</div>` }, '#/reports');
}

async function renderReport(id) {
  const row = await api('reports/' + id);
  const preview = await api(`reports/${id}/preview`);
  shell({ header: `<h1 class="serif">Report</h1>`, body: `<div class="panel">${preview.html}
    ${row.requires_approval && state.user.role !== 'ADMIN' && state.user.role !== 'PATIENT' ? '<button class="primary" id="approve">Approve</button>' : ''}</div>` }, '#/reports');
  $('#approve')?.addEventListener('click', async () => { await api(`reports/${id}/approve`, {}); renderReport(id); });
}

async function renderAssistant() {
  const patients = await api('patients');
  const id = patients[0]?.id || 'P102';
  shell({ header: `<h1 class="serif">AI assistant</h1>`, body: assistantPanel(id) }, '#/assistant');
  bindAssistant(id);
}

function assistantPanel(patientId) {
  return `<div class="panel">
    <p class="empty">Answers use stored measurements only. The model cannot invent ROM values or confirm a diagnosis.</p>
    <div class="chat" id="chat"></div>
    <div class="row">
      <input id="q" style="flex:1" value="Compare this assessment with the previous session.">
      <button class="primary" id="ask">Ask</button>
    </div>
    <div class="row" style="margin-top:8px">
      <button class="ghost" data-q="How has this patient improved?">Improvement</button>
      <button class="ghost" data-q="What movement is currently most restricted?">Restriction</button>
      <button class="ghost" data-q="Show sessions with high torso compensation.">Compensation</button>
      <button class="ghost" data-q="Generate a progress summary.">Report</button>
      <button class="ghost" data-q="Draft today's rehabilitation plan.">Draft plan</button>
    </div>
  </div>`;
}

function bindAssistant(patientId) {
  const ask = async q => {
    $('#chat').insertAdjacentHTML('beforeend', `<div class="bubble me">${q}</div>`);
    const res = await api('agent/query', { patient_id: patientId, question: q });
    $('#chat').insertAdjacentHTML('beforeend', `<div class="bubble">${res.summary}</div>`);
  };
  $('#ask')?.addEventListener('click', () => ask($('#q').value));
  document.querySelectorAll('[data-q]').forEach(b => b.addEventListener('click', () => ask(b.dataset.q)));
}

async function renderAppointments() {
  const rows = await api('appointments');
  shell({ header: `<h1 class="serif">Appointments</h1>`, body: `<div class="panel">${table(['When','Patient','Reason','Status'], rows.map(r => [new Date(r.scheduled_for).toLocaleString(), r.patient_id, r.reason, r.status]))}</div>` }, '#/appointments');
}

async function renderRecordings() {
  const patients = await api('patients');
  const lists = await Promise.all(patients.map(p => api(`patients/${p.id}/recordings`)));
  const rows = lists.flat();
  shell({ header: `<h1 class="serif">Recordings</h1>`, body: `<div class="panel">${rows.map(r => `<p><a href="#/recordings/${r.id}">${r.patient_id} · ${r.session_id}</a></p>`).join('') || '<p class="empty">No recordings.</p>'}</div>` }, '#/recordings');
}

async function renderRecording(id) {
  const row = await api('recordings/' + id);
  shell({ header: `<h1 class="serif">Recording replay</h1>`, body: `<div class="panel">
    <p>${row.seeded ? 'Seeded demo pointer — no binary video is stored.' : 'Telemetry replay from consented session. Video binaries are stored on disk, not in the database.'}</p>
    <div class="view"><canvas id="skel" width="640" height="480"></canvas></div>
    <p id="fb" class="feedback"></p></div>` }, '#/recordings');
  liveCanvas = $('#skel');
  const samples = row.samples || [];
  let i = 0;
  const tick = () => {
    if (!samples[i]) return;
    applyTelemetry(samples[i]);
    i += 1;
    setTimeout(tick, 80);
  };
  tick();
}

async function renderStaff() {
  if (state.user.role !== 'ADMIN') return renderDashboard();
  const rows = await api('staff');
  shell({ header: `<h1 class="serif">Staff</h1>`, body: `<div class="panel">${table(['Name','Email','Role'], rows.map(u => [u.full_name, u.email, u.role]))}<p class="empty">Admins manage accounts. They do not automatically modify treatment plans.</p></div>` }, '#/staff');
}

async function renderSettings() {
  shell({ header: `<h1 class="serif">Settings</h1>`, body: `<div class="panel">
    <p>Measurement source is selected at server start (<code>REHABAI_SOURCE=simulation|live</code>).</p>
    <p>Live mode never silently falls back to synthetic values.</p>
    <p>LLM server is optional. ROM, reps and safety continue if the GPU/LLM host is down.</p>
  </div>` }, '#/settings');
}

async function renderPatientHome() {
  const me = state.user;
  const patients = await api('patients');
  const patient = patients[0];
  const progress = patient ? await api(`patients/${patient.id}/progress`) : {};
  const appts = await api('appointments');
  shell({
    header: ``,
    body: `<section class="patient-home">
      <p class="eyebrow">Hello</p>
      <h1>${me.full_name.split(' ')[0]}, keep today’s movement gentle.</h1>
      <p>Your physiotherapist stays in charge of the plan. This screen only shows your measured sessions.</p>
      <button class="primary start-giant" id="go">Start session</button>
      <div class="cards" style="margin-top:22px">
        ${stat('Abduction', progress.abduction ? progress.abduction.current + '°' : '—')}
        ${stat('Pain', progress.pain_movement ? progress.pain_movement.current + '/10' : '—')}
        ${stat('Next visit', appts[0] ? new Date(appts[0].scheduled_for).toLocaleDateString() : '—')}
      </div>
    </section>`
  }, '#/home');
  $('#go')?.addEventListener('click', () => startLive(patient.id, 'shoulder_abduction', true));
}

async function renderPatientProgress() {
  const patients = await api('patients');
  if (patients[0]) { state.tab = 'progress'; return renderPatient(patients[0].id); }
}

function stat(label, value) { return `<div class="card"><span>${label}</span><strong>${value}</strong></div>`; }
function deg(v) { return v == null ? '—' : Math.round(v) + '°'; }
function fmtDelta(v) { return v == null ? '—' : (v > 0 ? '+' : '') + Math.round(v) + '°'; }
function patientTable(rows) {
  return table(['Patient','MRN','Side','Record'], (rows || []).map(p => [
    `<a href="#/patients/${p.id}">${p.full_name}</a>`, p.mrn, p.affected_side, p.is_demo ? '<span class="badge demo">demo</span>' : 'clinical'
  ]));
}
function alertList(rows) {
  if (!rows?.length) return '<p class="empty">No open alerts.</p>';
  return rows.map(a => `<p><span class="badge ${a.severity==='critical'?'crit':'warn'}">${a.severity}</span> ${a.message}</p>`).join('');
}
function table(headers, rows) {
  return `<table><thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function chart(values, color) {
  if (!values.length) return '<p class="empty">No stored points.</p>';
  const w = 420, h = 160, max = Math.max(...values, 1), min = Math.min(...values, 0);
  const pts = values.map((v, i) => {
    const x = i * (w - 20) / Math.max(1, values.length - 1) + 10;
    const y = h - 15 - ((v - min) / (max - min || 1)) * (h - 30);
    return x + ',' + y;
  }).join(' ');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${color}" stroke-width="3" points="${pts}"/></svg>`;
}

async function boot() {
  try { state.health = await fetch('/api/health').then(r => r.json()); } catch {}
  if (state.token) {
    try { state.user = await api('me'); } catch { state.token = null; localStorage.removeItem('rehabai_token'); }
  }
  window.addEventListener('hashchange', () => { route(); });
  await route();
}
boot();
