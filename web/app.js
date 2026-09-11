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
  const mode = modeLabel(state.health);
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
      tabEl.innerHTML = `<div class="panel">${table(['Date','Source','Abd','Flex','Rest','Move','Dress','Groom','Over','Back'], assessments.map(a => [
        new Date(a.created_at).toLocaleDateString(), a.source, deg(a.abduction_max), deg(a.flexion_max),
        a.pain_rest == null ? '—' : a.pain_rest + '/10',
        a.pain_movement == null ? '—' : a.pain_movement + '/10',
        a.difficulty_dressing ?? '—', a.difficulty_grooming ?? '—',
        a.difficulty_overhead ?? '—', a.difficulty_behind_back ?? '—'
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
    consent_recording: true, kind: assessment ? 'assessment' : 'rehab'
  });
  goto('#/live/' + session.id);
}

async function renderLive(sessionId, opts = {}) {
  const [session, script] = await Promise.all([api('sessions/' + sessionId), api('intake/script')]);
  closeLive();
  const voice = window.RehabIntake || {};
  const fields = script.fields || [];
  const needsIntake = session.kind === 'assessment' && !session.intake?.confirmed;
  let language = session.intake?.language || 'en-IN';
  let index = Math.max(0, fields.findIndex(f => !(session.intake?.fields || {})[f.id]));
  let pending = null;
  let listening = false;
  let paused = false;
  let phase = needsIntake ? 'intake' : 'measure';
  let painAfter = null;

  const current = () => fields[index];
  const promptOf = f => (language === 'hi-IN' ? f.prompt_hi : f.prompt_en);
  const confirmOf = (f, value) => (language === 'hi-IN' ? f.confirm_hi : f.confirm_en).replace('{value}', value);

  shell({
    header: `<p class="eyebrow">Live ${session.source === 'live' ? 'RealSense' : 'simulation'} + arm IMU</p>
      <h1 class="serif">${session.exercise_id.replaceAll('_',' ')}</h1>`,
    body: `<div class="checks" id="calib"></div>
      <div class="grid-live">
        <div class="view">
          <div class="view-label" id="view-label">${session.source === 'live' ? 'LIVE RGB-D · POSE OVERLAY' : 'SYNTHETIC RGB-D + ARM IMU · NOT LIVE SENSORS'}</div>
          <canvas id="skel" width="640" height="480"></canvas>
        </div>
        <div>
          <div class="intake-panel" id="intake-panel">
            <p class="eyebrow">Voice intake · not a diagnosis</p>
            <p class="empty">${script.disclaimer}</p>
            <div class="row">
              <button class="ghost" id="lang-en" type="button">English</button>
              <button class="ghost" id="lang-hi" type="button">हिन्दी</button>
            </div>
            <div class="intake-progress" id="intake-progress"></div>
            <h2 id="intake-q"></h2>
            <p class="intake-status" id="intake-status"></p>
            <p class="intake-status" id="heard"></p>
            <div id="chips"></div>
            <div class="row">
              <button class="primary mic-btn" id="mic" type="button">Speak</button>
              <button class="primary" id="yes" type="button" hidden>Yes, save</button>
              <button class="ghost" id="no" type="button" hidden>No, again</button>
            </div>
          </div>
          <div id="measure-block">
            <div class="metrics">
              <div class="metric"><span>Current ROM</span><b id="ang">—°</b></div>
              <div class="metric"><span>Target</span><b>${session.target}°</b></div>
              <div class="metric"><span>Reps</span><b id="reps" data-goal="${session.goal}">0 / ${session.goal}</b></div>
              <div class="metric"><span>Torso lean</span><b id="lean">—°</b></div>
              <div class="metric"><span>Tracking</span><b id="conf">—</b></div>
              <div class="metric"><span>Quality</span><b id="cov">—</b></div>
              <div class="metric"><span>Arm IMU</span><b id="imu">—</b></div>
              <div class="metric"><span>Fused ROM</span><b id="fused">—</b></div>
            </div>
            <p class="feedback" id="fb">Stand in the marked area. Hold a relaxed posture.</p>
            <p id="sub" class="empty"></p>
            <div class="row">
              <button class="primary" id="confirm">Confirm tracking & start</button>
              <button class="danger" id="finish">Stop & save</button>
            </div>
            ${session.source === 'simulation' ? `<div class="row" style="margin-top:12px">
              <span class="eyebrow">Simulation controls</span>
              <button class="ghost" data-fault="none">Normal</button>
              <button class="ghost" data-fault="lean">Lean sideways</button>
              <button class="ghost" data-fault="occlusion">Tracking lost</button>
              <button class="ghost" data-fault="imu_drop">Drop IMU</button>
            </div>` : ''}
          </div>
          <div class="intake-panel" id="debrief-panel" hidden>
            <p class="eyebrow">After this session</p>
            <h2>Pain after movement, 0 to 10</h2>
            <p class="intake-status" id="debrief-status">Tap a number or speak. There is no default score.</p>
            <div id="debrief-chips"></div>
            <div class="row">
              <button class="primary mic-btn" id="debrief-mic" type="button">Speak</button>
              <button class="danger" id="debrief-save" type="button">Save session</button>
            </div>
          </div>
        </div>
      </div>`
  }, '#/patients');
  liveCanvas = $('#skel');

  function showPhase() {
    $('#intake-panel').hidden = phase !== 'intake';
    $('#measure-block').hidden = phase !== 'measure';
    $('#debrief-panel').hidden = phase !== 'debrief';
  }

  function paintChips(host, max, kind) {
    const labels = kind === 'function'
      ? ['0 none', '1 mild', '2 moderate', '3 severe', '4 unable']
      : Array.from({ length: max + 1 }, (_, i) => String(i));
    host.innerHTML = `<div class="scale-chips">${labels.map((label, i) => `<button type="button" data-val="${i}">${label}</button>`).join('')}</div>`;
  }

  function paintIntake(speakPrompt) {
    const f = current();
    if (!f) return;
    $('#lang-en').classList.toggle('primary', language === 'en-IN');
    $('#lang-hi').classList.toggle('primary', language === 'hi-IN');
    $('#intake-q').textContent = paused ? 'Session paused' : promptOf(f);
    $('#intake-status').textContent = paused
      ? 'Please stop. Rest the arm. A physiotherapist should review before you continue.'
      : pending != null
        ? confirmOf(f, pending)
        : (voice.canListen && voice.canListen() ? 'Tap a number or press Speak.' : 'Tap a number. Voice needs Chrome or Edge on this workstation.');
    $('#intake-progress').innerHTML = fields.map((item, i) => {
      const saved = Boolean((session.intake?.fields || {})[item.id]);
      return `<span class="${saved ? 'on' : ''} ${i === index && !saved ? 'now' : ''}"></span>`;
    }).join('');
    paintChips($('#chips'), f.max, f.kind);
    $('#yes').hidden = pending == null || paused;
    $('#no').hidden = pending == null || paused;
    $('#mic').disabled = paused;
    $('#chips').querySelectorAll('button').forEach(btn => {
      btn.disabled = paused;
      btn.addEventListener('click', () => saveField(Number(btn.dataset.val), 'tap'));
    });
    if (speakPrompt && !paused && pending == null) voice.speak?.(promptOf(f), language);
  }

  async function saveField(value, source, transcript) {
    if (paused) return;
    const f = current();
    const res = await api(`sessions/${sessionId}/intake`, {
      field: f.id, value, source, transcript: transcript || undefined, language
    });
    session.intake = res.intake;
    pending = null;
    $('#heard').textContent = '';
    if (res.intake.confirmed) {
      phase = 'measure';
      showPhase();
      voice.speak?.('Thank you. Stand on the mark. This is not a diagnosis. We will measure movement.', language);
      return;
    }
    index = Math.max(0, fields.findIndex(item => !(res.intake.fields || {})[item.id]));
    paintIntake(true);
  }

  async function hearIntake() {
    if (!voice.listen || paused || listening) return;
    listening = true;
    $('#mic').classList.add('hot');
    $('#heard').textContent = 'Listening…';
    try {
      const text = await voice.listen(language);
      $('#heard').textContent = text ? ('Heard: ' + text) : 'No speech captured.';
      if (!text) return;
      const parsed = await api('intake/parse', { field: current().id, text, awaiting_confirm: pending != null });
      if (parsed.intent === 'safety_pause') {
        paused = true;
        pending = null;
        paintIntake(false);
        voice.speak?.(parsed.spoken, language);
        return;
      }
      if (parsed.intent === 'confirm_yes' && pending != null) {
        await saveField(pending, 'voice', text);
        return;
      }
      if (parsed.intent === 'confirm_no') {
        pending = null;
        paintIntake(true);
        return;
      }
      if (parsed.intent === 'number') {
        pending = parsed.parsed_value;
        paintIntake(false);
        voice.speak?.(confirmOf(current(), pending), language);
        return;
      }
      $('#intake-status').textContent = parsed.spoken;
    } catch (err) {
      $('#heard').textContent = err.message;
    } finally {
      listening = false;
      $('#mic').classList.remove('hot');
    }
  }

  function openDebrief() {
    phase = 'debrief';
    painAfter = null;
    showPhase();
    paintChips($('#debrief-chips'), 10, 'pain');
    $('#debrief-chips').querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
      painAfter = Number(btn.dataset.val);
      $('#debrief-chips').querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
      $('#debrief-status').textContent = 'Pain after ' + painAfter + ' out of 10. Press Save session to store it.';
    }));
    voice.speak?.('Pain after this session, zero to ten?', language);
  }

  async function saveSession() {
    if (!Number.isInteger(painAfter) || painAfter < 0 || painAfter > 10) {
      $('#debrief-status').textContent = 'Choose pain after from 0 to 10. Nothing is filled in for you.';
      return;
    }
    await api(`sessions/${sessionId}/finish`, {
      pain_after: painAfter,
      create_assessment: session.kind === 'assessment',
    });
    closeLive();
    state.tab = 'assessment';
    goto('#/patients/' + session.patient_id);
  }

  showPhase();
  if (phase === 'intake') paintIntake(true);
  else $('#intake-panel').hidden = true;
  const voiceOk = Boolean(voice.canListen && voice.canListen());
  if ($('#mic')) $('#mic').hidden = !voiceOk;
  if ($('#debrief-mic')) $('#debrief-mic').hidden = !voiceOk;

  $('#lang-en').addEventListener('click', () => { language = 'en-IN'; paintIntake(true); });
  $('#lang-hi').addEventListener('click', () => { language = 'hi-IN'; paintIntake(true); });
  $('#mic').addEventListener('click', hearIntake);
  $('#yes').addEventListener('click', () => pending != null && saveField(pending, 'voice'));
  $('#no').addEventListener('click', () => { pending = null; paintIntake(true); });
  $('#confirm').addEventListener('click', async () => {
    try { await api(`sessions/${sessionId}/confirm`, {}); }
    catch (e) { $('#sub').textContent = e.message; }
  });
  $('#finish').addEventListener('click', openDebrief);
  $('#debrief-save').addEventListener('click', () => saveSession().catch(err => { $('#debrief-status').textContent = err.message; }));
  $('#debrief-mic').addEventListener('click', async () => {
    if (!voice.listen) return;
    $('#debrief-status').textContent = 'Listening…';
    try {
      const text = await voice.listen(language);
      const parsed = await api('intake/parse', { field: 'pain_movement', text, awaiting_confirm: false });
      if (parsed.intent === 'safety_pause') {
        $('#debrief-status').textContent = parsed.spoken;
        return;
      }
      if (parsed.intent === 'number') {
        painAfter = parsed.parsed_value;
        $('#debrief-status').textContent = 'Pain after ' + painAfter + ' out of 10. Press Save session to store it.';
        $('#debrief-chips').querySelectorAll('button').forEach(b => b.classList.toggle('active', Number(b.dataset.val) === painAfter));
        voice.speak?.('Pain after ' + painAfter + ' out of 10.', language);
      } else {
        $('#debrief-status').textContent = parsed.spoken;
      }
    } catch (err) {
      $('#debrief-status').textContent = err.message;
    }
  });
  document.querySelectorAll('[data-fault]').forEach(btn => btn.addEventListener('click', () => api(`sessions/${sessionId}/fault`, { fault: btn.dataset.fault })));
  connectLive(sessionId);
}

function connectLive(sessionId) {
  closeLive({ keepVoice: true });
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/api/ws/sessions/${sessionId}?token=${encodeURIComponent(state.token)}`);
  ws.onmessage = ev => applyTelemetry(JSON.parse(ev.data));
  ws.onclose = () => { wsTimer = setTimeout(() => connectLive(sessionId), 800); };
}

function applyTelemetry(row) {
  if (row.type === 'ended') return;
  const cal = row.calibration || {};
  const imu = row.imu || {};
  const imuChip = !imu.enabled || imu.source === 'off' ? 'Off' : (!imu.ok ? false : (imu.simulation ? 'SIM' : true));
  const checks = [
    ['Camera', cal.simulation ? 'SIM' : cal.camera_ok],
    ['Depth', cal.simulation ? 'SIM' : cal.depth_ok],
    ['Pose', cal.pose_ok],
    ['Distance', cal.distance_ok],
    ['Arm IMU', imuChip],
  ];
  const calib = $('#calib');
  if (calib) calib.innerHTML = checks.map(([n, ok]) => `<div class="check ${ok===true || ok==='SIM' ? 'on' : 'off'}"><strong>${n}</strong><div>${ok === 'SIM' ? 'Simulation' : ok === 'Off' ? 'Off' : ok ? 'OK' : 'Not ready'}</div></div>`).join('');
  if ($('#ang')) $('#ang').textContent = row.valid ? Math.round(row.shoulder_angle) + '°' : '—';
  if ($('#lean')) $('#lean').textContent = Math.round(row.torso_lean || 0) + '°';
  if ($('#reps')) $('#reps').textContent = `${row.rep || 0} / ${$('#reps').dataset.goal}`;
  if ($('#conf')) $('#conf').textContent = Math.round((row.pose_confidence || 0) * 100) + '%';
  if ($('#cov')) $('#cov').textContent = (row.coverage ?? '—') + '%';
  if ($('#imu')) $('#imu').textContent = imuChip === 'SIM' ? 'SIM' : (imu.ok ? Math.round(imu.gyro_norm || 0) + '°/s' : 'lost');
  if ($('#fused')) $('#fused').textContent = imu.fused_angle != null ? Math.round(imu.fused_angle) + '°' : '—';
  if ($('#fb')) $('#fb').textContent = row.feedback || '';
  if ($('#sub')) $('#sub').textContent = (row.safety?.level ? 'Safety ' + row.safety.level : '') + (row.source === 'simulation' ? ' · synthetic RGB-D + IMU' : ' · live stream');
  if ($('#view-label')) $('#view-label').textContent = streamCaption(row);
  drawSkeleton(row);
}

function modeLabel(health) {
  if (health?.source === 'live') {
    return health?.imu?.live_available ? 'Live RealSense + arm IMU · unvalidated' : 'Live camera · unvalidated';
  }
  return health?.imu?.transport === 'off' ? 'Simulation mode' : 'Simulation · RGB-D + arm IMU';
}

function streamCaption(row) {
  const cam = row?.sensors?.camera || row?.source;
  const imu = row?.sensors?.imu || row?.imu?.source;
  if (cam === 'simulation') {
    return imu === 'simulation' ? 'SYNTHETIC RGB-D + ARM IMU · NOT LIVE SENSORS' : 'SYNTHETIC SKELETON · NOT A CAMERA FEED';
  }
  return imu === 'live' ? 'LIVE RGB-D + ARM IMU · UNVALIDATED' : 'LIVE RGB-D · POSE OVERLAY';
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

function closeLive(opts = {}) {
  if (!opts.keepVoice) window.RehabIntake?.stopVoice?.();
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
    <p>The station uses <strong>RealSense RGB-D</strong> for 3D ROM and an <strong>arm IMU</strong> for rate and movement quality. IMU packets are not used to diagnose frozen shoulder.</p>
    <p>Live mode never silently falls back to synthetic values. A live camera never receives a simulated IMU.</p>
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
