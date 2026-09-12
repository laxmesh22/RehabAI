const $ = (sel, el = document) => el.querySelector(sel);
const app = $('#app');
const state = { token: localStorage.getItem('rehabai_token'), user: null, health: null, tab: 'overview' };
let ws, wsTimer, liveCanvas, guideCaptionKey = '', spokenOverride = '', lastCoachCue = '';
let adaptiveCoachKey = '', adaptiveCoachAt = 0, adaptiveCoachBusy = false;
let phoneMediaStream = null, phoneFrameTimer = null, phoneFrameUploading = false, phoneCaptureCanvas = null;

/** Packaged APK sets REHABAI_API_ORIGIN so UI loads offline while API hits Railway/LAN. */
function apiOrigin() {
  return String(window.REHABAI_API_ORIGIN || '').replace(/\/$/, '');
}
function apiUrl(path) {
  const p = path.startsWith('/') ? path : '/' + path;
  return apiOrigin() + p;
}
function uiUrl(file) {
  const name = String(file || '').replace(/^\.?\/?/, '').replace(/^ui\//, '');
  const base = String(window.REHABAI_UI_BASE || '/ui/').replace(/\/?$/, '/');
  return base + name;
}
window.RehabApiUrl = apiUrl;

function apiCandidateOrigins() {
  const primary = apiOrigin();
  const extras = Array.isArray(window.REHABAI_API_FALLBACKS) ? window.REHABAI_API_FALLBACKS : [];
  const saved = localStorage.getItem('rehabai_api_origin') || '';
  const list = [saved, primary, ...extras]
    .map(v => String(v || '').replace(/\/$/, ''))
    .filter(Boolean);
  return [...new Set(list)];
}

async function probeApiOrigin(origin, ms = 3500) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(origin.replace(/\/$/, '') + '/api/health', { signal: ctrl.signal, cache: 'no-store' });
    if (!res.ok) return null;
    const data = await res.json().catch(() => null);
    return data && data.status === 'ok' ? data : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function scoreApiOrigin(origin, data) {
  if (!data || data.status !== 'ok') return -1;
  let score = 10;
  // Prefer hosts that can run phone OpenCV/MediaPipe (otherwise APK only shows Simulation).
  if (data.phone_pose_available) score += 100;
  if (String(origin).startsWith('https://')) score += 8;
  if (/railway\.app/i.test(origin)) score += 20;
  return score;
}

async function resolveApiOrigin() {
  const candidates = apiCandidateOrigins();
  if (!candidates.length) {
    // Browser on same host as API
    const data = await probeApiOrigin(location.origin);
    if (data) {
      state.health = data;
      return '';
    }
    return null;
  }
  // Probe every candidate so a sticky LAN URL without OpenCV does not hide Phone camera.
  const probed = await Promise.all(candidates.map(async (origin) => {
    const data = await probeApiOrigin(origin, 4500);
    return { origin, data, score: scoreApiOrigin(origin, data) };
  }));
  probed.sort((a, b) => b.score - a.score);
  const best = probed.find(row => row.score >= 0);
  if (!best) return null;
  window.REHABAI_API_ORIGIN = best.origin;
  localStorage.setItem('rehabai_api_origin', best.origin);
  state.health = best.data;
  return best.origin;
}

function renderOfflineGate(tried) {
  const list = (tried || []).map(u => `<li><code>${escapeHtml(u)}</code></li>`).join('')
    || '<li><code>https://rehabai-api-production.up.railway.app</code></li>';
  app.innerHTML = `<div class="offline-gate">
    <p class="eyebrow">Connection</p>
    <h1>Cannot reach PhysioBuDDY server</h1>
    <p class="lede">This phone could not resolve or reach the API host (<code>ERR_NAME_NOT_RESOLVED</code> / offline). The app UI is local — only the server link failed.</p>
    <ol class="offline-steps">
      <li>Prefer the <strong>installed APK</strong> (not a Railway link in Chrome).</li>
      <li>On Android: Settings → Network → Private DNS → <strong>Off</strong>, then retry.</li>
      <li>Same Wi‑Fi as the clinic PC: open LAN URL or tap Retry (uses PC fallback).</li>
      <li>Mobile data: try another network; some carriers fail <code>*.up.railway.app</code> DNS.</li>
    </ol>
    <p class="sub">Tried:</p>
    <ul class="offline-tried">${list}</ul>
    <div class="offline-actions">
      <button type="button" class="primary" id="offline-retry">Retry connection</button>
      <button type="button" class="ghost" id="offline-lan">Use clinic Wi‑Fi API</button>
    </div>
    <p class="empty">Not a diagnosis service. Demo measurements stay labelled.</p>
  </div>`;
  $('#offline-retry')?.addEventListener('click', () => location.reload());
  $('#offline-lan')?.addEventListener('click', () => {
    const lan = (window.REHABAI_API_FALLBACKS || []).find(u => /^http:\/\//.test(u));
    if (lan) {
      localStorage.setItem('rehabai_api_origin', lan.replace(/\/$/, ''));
      window.REHABAI_API_ORIGIN = lan.replace(/\/$/, '');
    }
    location.reload();
  });
}

const routes = {
  clinician: [
    ['#/dashboard', 'Dashboard'],
    ['#/patients', 'Patients'],
    ['#/alerts', 'Alerts'],
    ['#/reports', 'Reports'],
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

const NAV_ICON = {
  Dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/></svg>',
  Patients: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="3.2"/><path d="M5 19c.8-3.2 3.4-5 7-5s6.2 1.8 7 5"/></svg>',
  Home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 11.5 12 5l8 6.5V20H4z"/></svg>',
  Progress: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 17 9 11l4 3 7-9"/><path d="M4 20h16"/></svg>',
  Alerts: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
  Reports: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 3h8l4 4v14H7z"/><path d="M15 3v5h5"/></svg>',
  Appointments: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/></svg>',
  Recordings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8"/><path d="M10 9.5v5l5-2.5z"/></svg>',
  Staff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="2.4"/><circle cx="16" cy="9" r="2"/><path d="M4.5 19c.6-2.8 2.6-4.2 4.8-4.2s4.2 1.4 4.8 4.2M14 15.2c1.8.1 3.4 1.2 4 3.8"/></svg>',
  Settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M12 4v2M12 18v2M4 12h2M18 12h2M6.2 6.2l1.5 1.5M16.3 16.3l1.5 1.5M17.8 6.2l-1.5 1.5M7.7 16.3l-1.5 1.5"/></svg>',
  Logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H10"/><path d="M10 12h9M16 8l4 4-4 4"/></svg>',
};

function brandMark(solid, lg) {
  const blades = `<svg viewBox="0 0 32 32" fill="currentColor"><g transform="translate(16,16) rotate(45)"><rect x="-3.35" y="-14.3" width="6.7" height="10.7" rx="3.35"/><rect x="-3.35" y="3.6" width="6.7" height="10.7" rx="3.35"/><rect x="-14.3" y="-3.35" width="10.7" height="6.7" rx="3.35"/><rect x="3.6" y="-3.35" width="10.7" height="6.7" rx="3.35"/></g></svg>`;
  return `<span class="mark ${solid ? 'solid' : ''} ${lg ? 'lg' : ''}" aria-hidden="true">${blades}</span>`;
}

function helloLine() {
  const hour = new Date().getHours();
  const part = hour < 12 ? 'Good Morning' : hour < 17 ? 'Good Afternoon' : 'Good Evening';
  const name = (state.user?.full_name || 'there').split(' ')[0];
  return part + ', ' + name;
}

function initials() {
  return (state.user?.full_name || 'P').split(/\s+/).map(p => p[0]).slice(0, 2).join('').toUpperCase();
}

async function api(path, body, method) {
  const options = { headers: {} };
  if (state.token) options.headers.Authorization = 'Bearer ' + state.token;
  if (body !== undefined || method === 'POST') {
    options.method = method || 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body || {});
  }
  const res = await fetch(apiUrl('/api/' + path.replace(/^\//, '')), options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map(e => e.msg || e).join('; ') : (d || data.error || data.message || res.statusText));
  }
  return data;
}

async function transcribeWav(wav, language) {
  const body = new FormData();
  body.append('audio', new Blob([wav], { type: 'audio/wav' }), 'utterance.wav');
  body.append('language', language || 'en-IN');
  const headers = {};
  if (state.token) headers.Authorization = 'Bearer ' + state.token;
  const res = await fetch(apiUrl('/api/intake/transcribe'), { method: 'POST', headers, body });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Local speech failed. Tap a number instead.');
  }
  return data.transcript || '';
}

async function synthesizeSpeech(text, language, signal) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.token) headers.Authorization = 'Bearer ' + state.token;
  const res = await fetch(apiUrl('/api/intake/speak'), {
    method: 'POST',
    headers,
    body: JSON.stringify({ text, language: language || 'en-IN' }),
    signal,
  });
  if (!res.ok) throw new Error('Server voice is unavailable');
  return await res.blob();
}

window.RehabIntake?.configure?.({ synthesize: synthesizeSpeech });

async function downloadAuthorized(path, filename) {
  const headers = {};
  if (state.token) headers.Authorization = 'Bearer ' + state.token;
  const res = await fetch(apiUrl('/api/' + path.replace(/^\//, '')), { headers });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Export failed');
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function detail(err) {
  if (Array.isArray(err)) return err.map(e => e.msg).join('; ');
  return typeof err === 'string' ? err : JSON.stringify(err);
}

window.addEventListener('unhandledrejection', () => {});

function goto(hash) {
  location.hash = hash;
}

function exercisePickerHtml(exercises, selectedId, mode) {
  const compact = mode === 'chips';
  return `<div class="exercise-picker ${compact ? 'exercise-chips' : ''}" id="exercise-picker" role="listbox" aria-label="Choose exercise">
    ${(exercises || []).map(ex => `<button type="button" role="option" data-exercise="${escapeHtml(ex.exercise_id)}"
      data-avatar="${escapeHtml(ex.avatar_demo || '')}" data-target="${Number(ex.target_range) || 80}"
      class="${ex.exercise_id === selectedId ? 'is-selected' : ''}" aria-selected="${ex.exercise_id === selectedId}">
      <strong>${escapeHtml(ex.name)}</strong>
      ${compact ? '' : `<span>${escapeHtml(ex.instructions || '')}</span>`}
    </button>`).join('')}
  </div>`;
}

function mountGuide(selector, meta) {
  if (meta?.exercise) window.RehabGuideExercise = meta.exercise;
  if (meta?.avatar) window.RehabGuideAvatar = meta.avatar;
  if (meta?.target != null) window.RehabGuideTarget = meta.target;
  if (meta?.side) window.RehabGuideSide = meta.side;
  const tryMount = (n) => {
    if (window.RehabGuide?.mount) {
      window.RehabGuide.mount(selector);
      return;
    }
    if (n < 40) setTimeout(() => tryMount(n + 1), 50);
  };
  if (window.RehabGuide?.mount) {
    tryMount(0);
    return;
  }
  // Lazy-load Three.js FollowAvatar only when a guide is needed (keeps dashboard light).
  if (!window.__rehabGuideLoading) {
    window.__rehabGuideLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.type = 'module';
      s.src = uiUrl('guide_follow.js') + '?v=62';
      s.onload = () => resolve();
      s.onerror = () => reject(new Error('guide_follow failed'));
      document.head.appendChild(s);
    });
  }
  window.__rehabGuideLoading.then(() => tryMount(0)).catch(() => {
    const el = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (el) {
      el.innerHTML = '<div class="guide-status bad">3D guide failed to load.</div>';
    }
  });
}

async function loadExerciseLibrary() {
  if (window.__rehabExercises) return window.__rehabExercises;
  try {
    window.__rehabExercises = await api('exercises');
  } catch {
    window.__rehabExercises = [];
  }
  return window.__rehabExercises;
}

function bindExercisePicker(root, onPick) {
  const box = root?.querySelector?.('#exercise-picker') || $('#exercise-picker');
  if (!box) return;
  box.querySelectorAll('[data-exercise]').forEach(btn => {
    btn.addEventListener('click', () => {
      box.querySelectorAll('[data-exercise]').forEach(b => {
        b.classList.toggle('is-selected', b === btn);
        b.setAttribute('aria-selected', b === btn ? 'true' : 'false');
      });
      onPick?.({
        exercise_id: btn.dataset.exercise,
        avatar_demo: btn.dataset.avatar,
        target_range: Number(btn.dataset.target) || 80,
      });
    });
  });
}

function isConsumerMode() {
  const hash = location.hash || '';
  if (hash.startsWith('#/app')) return true;
  if (new URLSearchParams(location.search).get('consumer') === '1') return true;
  if (window.Capacitor || window.capacitor) return true;
  return Boolean(state.consumer);
}

window.RehabStudioActions = {
  apply(action) {
    if (action === 'pause') return window.RehabLiveControl?.pause?.();
    if (isConsumerMode()) {
      if (action === 'open_home' || action === 'open_history') {
        const goHome = () => {
          goto('#/app/home');
          if (action === 'open_history') {
            setTimeout(() => document.getElementById('dash-history')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }), 250);
          }
        };
        return goHome();
      }
      if (action === 'start_assessment') return goto('#/app/baseline');
      if (action === 'start_session') return document.getElementById('go')?.click() || startConsumerSession();
    }
    const routes = {
      open_patients: '#/patients',
      open_settings: '#/settings',
      open_home: isConsumerMode() ? '#/app/home' : '#/home',
      open_history: '#/app/home',
    };
    if (routes[action]) return goto(routes[action]);
    if (action === 'confirm_tracking') return $('#confirm')?.click();
    if (action === 'start_session') return $('#go')?.click();
  },
};

async function route() {
  try {
    if (isConsumerMode()) {
      if (localStorage.getItem('rehabai_signed_out') === '1' && !state.token) {
        return renderConsumerSignedOut();
      }
      await ensureConsumerLogin();
      if (!state.token) return renderConsumerSignedOut();
      const hash = location.hash || '#/app';
      if (hash.startsWith('#/live/')) {
        const live = hash.match(/^#\/live\/([^/]+)/);
        return await renderLive(live[1]);
      }
      if (hash === '#/app/baseline') return await renderConsumerBaseline();
      if (hash === '#/app/home' || hash === '#/app/account') return await renderConsumerHome();
      return await renderConsumerTalk();
    }
    const hash = location.hash || (state.user?.role === 'PATIENT' ? '#/home' : '#/dashboard');
    if (!state.token) return renderLogin();
    window.RehabVoiceContext = { scene: 'clinic' };
    const live = hash.match(/^#\/live\/([^/]+)/);
    if (live) return await renderLive(live[1]);
    const startRehab = hash.match(/^#\/patients\/([^/]+)\/start-rehab$/);
    if (startRehab) return await renderStartRehabSession(startRehab[1], false);
    const startAssess = hash.match(/^#\/patients\/([^/]+)\/start-assess$/);
    if (startAssess) return await renderStartRehabSession(startAssess[1], true);
    const patient = hash.match(/^#\/patients\/([^/]+)$/);
    if (patient) return await renderPatient(patient[1]);
    const rec = hash.match(/^#\/recordings\/([^/]+)/);
    if (rec) return await renderRecording(rec[1]);
    const report = hash.match(/^#\/reports\/([^/]+)/);
    if (report) return await renderReport(report[1]);
    if (hash === '#/assistant') {
      location.hash = state.user?.role === 'PATIENT' ? '#/home' : '#/dashboard';
      return;
    }
    const map = {
      '#/dashboard': renderDashboard,
      '#/patients': renderPatients,
      '#/alerts': renderAlerts,
      '#/reports': renderReports,
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
    app.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

function shell(content, active) {
  if (isConsumerMode()) {
    return consumerShell(content, active);
  }
  const nav = (state.user.role === 'PATIENT' ? routes.patient : routes.clinician)
    .filter(item => state.user.role === 'ADMIN' || item[0] !== '#/staff' || state.user.role === 'ADMIN')
    .map(([href, label]) => {
      const on = active === href;
      return `<a class="${on ? 'active' : ''}" href="${href}">${NAV_ICON[label] || ''}<span>${label}</span><i class="nav-arrow">${on ? '⌄' : '›'}</i></a>`;
    }).join('');
  const mode = modeLabel(state.health);
  const searchIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3-3"/></svg>';
  const userIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="3.2"/><path d="M5 19c.8-3.2 3.4-5 7-5s6.2 1.8 7 5"/></svg>';
  app.innerHTML = `<div class="app-shell">
    <aside class="rail">
      <div class="brand">${brandMark(true)}<strong>PhysioBuDDY</strong></div>
      ${nav}
      <div class="spacer"></div>
      <div class="profile-cta">
        <span class="tag">${mode.includes('LIVE') ? 'Live' : 'Incomplete'}</span>
        <h3>Your clinic profile</h3>
        <p>The more PhysioBuDDY knows, the better it can personalise the stored record. ${mode}.</p>
        <button class="primary" id="logout" type="button">Sign out →</button>
      </div>
    </aside>
    <div class="stage">
      <div class="chrome">
        <label class="search-wrap">${searchIcon}<input id="top-search" placeholder="Search reports, patients, sessions…" aria-label="Search"></label>
        <div class="chrome-actions">
          <a class="ghost-icon" href="#/alerts" aria-label="Alerts">${NAV_ICON.Alerts}</a>
          <div class="ghost-icon" title="${escapeHtml(state.user.full_name)}">${userIcon}</div>
        </div>
      </div>
      ${content.header ? `<div class="page-head">${content.header}</div>` : ''}
      ${content.body}
    </div>
  </div>
  <button type="button" class="voice-dock" id="voice-dock" aria-label="Talk to PhysioBuDDY">
    <span class="voice-dock-mic" aria-hidden="true"></span>
    <span class="voice-dock-copy"><strong id="voice-dock-title">Talk</strong><em id="voice-dock-hint">Tap to start · talks until you end</em></span>
  </button>`;
  window.RehabVoiceAgent?.bindDock?.();
  $('#logout')?.addEventListener('click', () => { localStorage.removeItem('rehabai_token'); state.token = null; goto('#/login'); renderLogin(); });
  $('#top-search')?.addEventListener('keydown', async ev => {
    if (ev.key !== 'Enter') return;
    const q = ev.target.value.trim().toLowerCase();
    if (!q) return goto('#/patients');
    try {
      const rows = await api('patients');
      const hit = rows.find(p => [p.full_name, p.mrn, p.id].some(v => String(v || '').toLowerCase().includes(q)));
      const next = hit ? '#/patients/' + hit.id : '#/patients';
      if (location.hash === next) await route();
      else goto(next);
    } catch {}
  });
}

function consumerSignOut() {
  window.RehabVoiceAgent?.endTalk?.();
  localStorage.removeItem('rehabai_token');
  localStorage.setItem('rehabai_signed_out', '1');
  state.token = null;
  state.user = null;
  state.consumerPatientId = null;
  goto('#/app');
  renderConsumerSignedOut();
}

function renderConsumerSignedOut() {
  state.consumer = true;
  app.innerHTML = `<div class="consumer-app talk-focus">
    <header class="consumer-top">
      <div class="brand">${brandMark(true)}<strong>PhysioBuDDY</strong></div>
    </header>
    <main class="consumer-main">
      <section class="talk-hero signed-out-hero">
        <p class="eyebrow">Signed out</p>
        <h1>See you soon</h1>
        <p class="lede talk-lede">Your Talk answers stay on this device’s server profile until you continue. This is not a diagnosis.</p>
        <button type="button" class="primary start-giant" id="consumer-continue">Continue</button>
        <p class="empty">Local session cleared. Continue restores your saved account on this device, or starts a new empty profile.</p>
      </section>
    </main>
  </div>`;
  $('#consumer-continue')?.addEventListener('click', async () => {
    localStorage.removeItem('rehabai_signed_out');
    await ensureConsumerLogin(true);
    goto('#/app');
    await route();
  });
}

function consumerShell(content, active) {
  const homeOn = active === '#/app/home' || active === '#/live';
  const step = content.step != null ? content.step : (homeOn ? 3 : 1);
  const account = content.account || null;
  const initials = account?.initials || 'R';
  const label = account?.name || 'You';
  app.innerHTML = `<div class="consumer-app talk-focus">
    <header class="consumer-top">
      <div class="brand">${brandMark(true)}<strong>PhysioBuDDY</strong></div>
      <div class="consumer-top-right">
        <nav class="consumer-steps" aria-label="Flow">
          <a class="${step === 1 ? 'on' : ''}" href="#/app"><span>1</span> Talk</a>
          <i></i>
          <a class="${step === 2 ? 'on' : ''}" href="#/app/baseline"><span>2</span> Measure</a>
          <i></i>
          <a class="${step === 3 ? 'on' : ''}" href="#/app/home"><span>3</span> Home</a>
        </nav>
        ${account ? `<div class="account-menu">
          <button type="button" class="account-chip" id="account-chip" aria-expanded="false" aria-haspopup="true">
            <span class="account-avatar" aria-hidden="true">${escapeHtml(initials)}</span>
            <span class="account-name">${escapeHtml(label)}</span>
          </button>
          <div class="account-dropdown" id="account-dropdown" hidden>
            <p class="account-meta">${escapeHtml(account.email || 'Patient')}</p>
            <p class="account-meta soft">${escapeHtml(account.side || '—')} shoulder${account.age != null ? ' · ' + account.age + ' yrs' : ''}</p>
            <button type="button" class="ghost" id="account-retalk">Update via Talk</button>
            <button type="button" class="primary" id="consumer-logout">Sign out</button>
          </div>
        </div>` : ''}
      </div>
    </header>
    <main class="consumer-main">${content.body || ''}</main>
  </div>`;
  $('#account-chip')?.addEventListener('click', ev => {
    ev.stopPropagation();
    const menu = $('#account-dropdown');
    if (!menu) return;
    const open = menu.hidden;
    menu.hidden = !open;
    $('#account-chip')?.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  $('#consumer-logout')?.addEventListener('click', () => consumerSignOut());
  $('#account-retalk')?.addEventListener('click', async () => {
    try {
      await api('consumer/retalk', {});
    } catch (e) {
      /* still open Talk so the user can try again */
    }
    goto('#/app');
    await route();
  });
  document.addEventListener('click', () => {
    const menu = $('#account-dropdown');
    if (menu) menu.hidden = true;
  }, { once: true });
}

async function renderConsumerTalk() {
  const me = await api('consumer/me');
  state.consumerPatientId = me.patient_id;
  // Keep Talk available after intake — dashboard voice (start session / history) and retalk.
  // Auto-redirect made the orb look broken for completed profiles.
  window.RehabVoiceLang = window.RehabVoiceLang || 'en-IN';
  window.RehabVoiceContext = {
    scene: 'consumer',
    patient_id: me.patient_id,
    intake_field: null,
    awaiting_confirm: false,
    pending_value: null,
  };
  const ready = Boolean(me.intake_complete && me.report_complete);
  const phase = me.phase || (me.intake_complete ? 'report' : 'questionnaire');
  const readyHint = me.display_name
    ? `${me.display_name}, tap Talk — say start session or show history.`
    : 'Tap Talk — say start session or show history.';
  consumerShell({
    step: 1,
    body: `<section class="talk-hero">
      <h1>PhysioBuDDY</h1>
      <button type="button" class="talk-orb" id="voice-dock" aria-label="Start Talk">
        <span class="talk-orb-ring" aria-hidden="true"></span>
        <span class="talk-orb-core" aria-hidden="true"></span>
        <strong id="voice-dock-title">Talk</strong>
        <em id="voice-dock-hint" class="sr-only" hidden></em>
      </button>
      <p class="talk-live" id="consumer-prompt" ${ready ? '' : 'hidden'}>${ready ? escapeHtml(readyHint) : ''}</p>
      <p class="talk-live sub" id="consumer-status" hidden></p>
      <div class="talk-lang" role="group" aria-label="Language">
        <button type="button" id="lang-en" class="is-on">EN</button>
        <button type="button" id="lang-hi">हि</button>
      </div>
      <button type="button" class="talk-skip" id="talk-skip-home">Skip to home</button>
      <p class="talk-skip-hint">Skip Talk and open sessions · phone camera / OpenCV</p>
      <div class="agent-report compact ${phase === 'report' && !ready ? 'hot show' : ''}" id="report-panel" ${phase === 'report' && !ready ? '' : 'hidden'}>
        <label class="report-upload">
          <input type="file" id="report-file" accept="image/*" capture="environment" hidden>
          <span>Upload report</span>
        </label>
        <label class="cloud-ocr-consent">
          <input type="checkbox" id="report-cloud-consent">
          <span>If local OCR cannot read it, I consent to sending this image to the configured Claude service for text extraction.</span>
        </label>
        <button class="ghost" id="report-skip" type="button">Skip</button>
        <p class="intake-status" id="report-status"></p>
      </div>
      ${ready ? '<p class="empty"><a href="#/app/home">Back to You</a> · or update answers from the account menu</p>' : ''}
    </section>`,
  }, '#/app');
  window.RehabVoiceAgent?.bindDock?.();
  const showLive = (spoken, status) => {
    const prompt = document.getElementById('consumer-prompt');
    const st = document.getElementById('consumer-status');
    if (prompt && spoken) {
      prompt.hidden = false;
      prompt.textContent = spoken;
    }
    if (st && status) {
      st.hidden = false;
      st.textContent = status;
    }
  };
  window.RehabConsumerLive = { showLive };
  loadExerciseLibrary().catch(() => {});
  const paintLang = () => {
    const hi = window.RehabVoiceLang === 'hi-IN';
    $('#lang-en')?.classList.toggle('is-on', !hi);
    $('#lang-hi')?.classList.toggle('is-on', hi);
  };
  paintLang();
  $('#lang-en')?.addEventListener('click', () => {
    window.RehabVoiceLang = 'en-IN';
    paintLang();
    if (window.RehabVoiceAgent?.isTalking?.()) window.RehabVoiceAgent.restart?.();
  });
  $('#lang-hi')?.addEventListener('click', () => {
    window.RehabVoiceLang = 'hi-IN';
    paintLang();
    if (window.RehabVoiceAgent?.isTalking?.()) window.RehabVoiceAgent.restart?.();
  });
  $('#talk-skip-home')?.addEventListener('click', async () => {
    const btn = $('#talk-skip-home');
    if (btn) btn.disabled = true;
    try {
      window.RehabVoiceAgent?.endTalk?.();
      await api('consumer/talk-skip', {});
      goto('#/app/home');
    } catch (e) {
      if (btn) btn.disabled = false;
      const st = $('#consumer-status');
      if (st) {
        st.hidden = false;
        st.textContent = e.message || 'Could not skip Talk';
      }
    }
  });
  $('#report-skip')?.addEventListener('click', async () => {
    try {
      await api('consumer/report-skip', {});
      goto('#/app/home');
    } catch (e) {
      $('#report-status').textContent = e.message;
    }
  });
  $('#report-file')?.addEventListener('change', async ev => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    $('#report-status').textContent = 'Scanning…';
    try {
      const body = new FormData();
      body.append('file', file, file.name || 'report.jpg');
      body.append('cloud_ocr_consent', $('#report-cloud-consent')?.checked ? '1' : '0');
      const headers = {};
      if (state.token) headers.Authorization = 'Bearer ' + state.token;
      const res = await fetch(apiUrl('/api/consumer/report-ocr'), { method: 'POST', headers, body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'OCR failed');
      $('#report-status').textContent = data.spoken || 'Saved';
      setTimeout(() => goto('#/app/home'), 500);
    } catch (e) {
      $('#report-status').textContent = e.message || 'OCR failed';
    }
  });
  // Do not auto-start — user taps the big Talk orb.
}

// What the camera can actually measure, and from where. A single 2D camera reads a
// forward raise only from the side, so flexion asks the patient to turn.
const BASELINE_STEPS = {
  abduction: {
    name: 'Arm out to the side',
    stance: 'Stand facing the camera, arms relaxed.',
    how: 'Raise the affected arm out to the side as far as is comfortable, then lower it slowly.',
    note: 'Front view · frontal-plane angle · 2D, no depth · unvalidated',
  },
  flexion: {
    name: 'Arm forward',
    stance: 'Turn so the affected shoulder faces the camera.',
    how: 'Raise the affected arm forward as far as is comfortable, then lower it slowly.',
    note: 'Side view · arm elevation in the image plane · not an isolated flexion angle · unvalidated',
  },
};

function baselineCapture(me) {
  return (me.phone_pose_available || state.health?.phone_pose_available) ? 'phone' : 'simulation';
}

async function startConsumerAssessment(exerciseId, capture) {
  const me = await api('consumer/me');
  state.consumerPatientId = me.patient_id;
  const baseline = me.baseline || {};
  const exercise = exerciseId || baseline.next_exercise_id;
  if (!exercise) return goto('#/app/home');
  const side = (me.affected_side || me.profile?.affected_side) === 'left' ? 'left' : 'right';
  await startLive(me.patient_id, exercise, true, capture || baselineCapture(me), { side, goal: 3 });
}

async function renderConsumerBaseline() {
  window.RehabVoiceAgent?.endTalk?.();
  const me = await api('consumer/me');
  state.consumerPatientId = me.patient_id;
  if (!(me.intake_complete && me.report_complete)) {
    location.hash = '#/app';
    return renderConsumerTalk();
  }
  const baseline = me.baseline || {};
  const scores = (me.memory && me.memory.intake_scores) || {};
  const side = me.affected_side || me.profile?.affected_side || '—';
  const name = me.display_name || 'there';
  const initials = String(me.profile?.full_name || state.user?.full_name || 'R')
    .split(/\s+/).map(p => p[0]).slice(0, 2).join('').toUpperCase() || 'R';
  const account = { name, initials, email: state.user?.email || '', age: me.age, side: String(side) };
  const capture = baselineCapture(me);
  const deg = v => (v == null ? '—' : Math.round(v) + '°');
  const measured = `<div class="dash-snapshot" aria-label="Measured so far">
      <article><span>Abduction</span><strong>${deg(baseline.abduction_deg)}</strong><em>${baseline.abduction_deg == null ? 'Not measured' : 'Camera'}</em></article>
      <article><span>Forward raise</span><strong>${deg(baseline.flexion_deg)}</strong><em>${baseline.flexion_deg == null ? 'Not measured' : 'Camera, side view'}</em></article>
      <article><span>Pain on move</span><strong>${scores.pain_movement == null ? '—' : scores.pain_movement + '/10'}</strong><em>Your words</em></article>
    </div>`;

  if (baseline.complete) {
    consumerShell({
      step: 2,
      account,
      body: `<section class="dash-slide dash-home baseline-slide">
        <div class="dash-hero">
          <p class="eyebrow">Baseline recorded · measured values only · not a diagnosis</p>
          <h1>Here is your starting point</h1>
          <p class="lede">${escapeHtml(name)}, these are the numbers the camera measured on your ${escapeHtml(String(side))} shoulder today. They are a starting point to compare against, not a diagnosis of any condition.</p>
          ${measured}
          <div class="dash-start">
            <button class="primary start-giant" id="go" type="button">Start rehab session</button>
            <p class="empty dash-sim-note">${capture === 'phone'
              ? 'Phone camera · OpenCV + MediaPipe · 2D RGB · unvalidated'
              : 'OpenCV pose offline on this API — Simulation only (labelled).'}</p>
            <p class="empty"><a href="#/app/home">Back to You</a> · <button type="button" class="linkish" id="baseline-redo">Measure again</button></p>
          </div>
        </div>
      </section>`,
    }, '#/app/baseline');
    $('#go')?.addEventListener('click', () => startConsumerSession(capture).catch(err => {
      app.insertAdjacentHTML('afterbegin', `<p class="error">${escapeHtml(err.message)}</p>`);
    }));
    $('#baseline-redo')?.addEventListener('click', () => startConsumerAssessment('shoulder_abduction', capture).catch(err => {
      app.insertAdjacentHTML('afterbegin', `<p class="error">${escapeHtml(err.message)}</p>`);
    }));
    return;
  }

  const movement = baseline.next_movement || 'abduction';
  const step = BASELINE_STEPS[movement] || BASELINE_STEPS.abduction;
  const stepNo = movement === 'abduction' ? 1 : 2;
  consumerShell({
    step: 2,
    account,
    body: `<section class="dash-slide dash-home baseline-slide">
      <div class="dash-hero">
        <p class="eyebrow">Before we start · measurement ${stepNo} of 2 · not a diagnosis</p>
        <h1>${escapeHtml(step.name)}</h1>
        <p class="lede">${escapeHtml(name)}, we measure your ${escapeHtml(String(side))} shoulder once before the first rehab session, so later sessions have something to compare against.</p>
        ${measured}
        <div class="dash-start">
          <ol class="baseline-steps">
            <li>${escapeHtml(step.stance)}</li>
            <li>Make sure your shoulders and hips stay in the picture.</li>
            <li>${escapeHtml(step.how)}</li>
            <li>Stop and say pause if it hurts. Nothing is measured through pain.</li>
          </ol>
          <button class="primary start-giant" id="baseline-go" type="button">Open camera &amp; measure</button>
          <p class="empty dash-sim-note">${escapeHtml(step.note)}</p>
          ${capture === 'phone' ? '' : '<p class="empty dash-sim-note">OpenCV pose is offline on this API, so this will be a labelled simulation and not a measurement of you.</p>'}
          <p class="empty"><button type="button" class="linkish" id="baseline-skip">Skip the measurement</button> · nothing will be recorded</p>
          <p class="intake-status" id="baseline-status"></p>
        </div>
      </div>
    </section>`,
  }, '#/app/baseline');
  $('#baseline-go')?.addEventListener('click', () => {
    $('#baseline-go').disabled = true;
    startConsumerAssessment(baseline.next_exercise_id, capture).catch(err => {
      $('#baseline-go').disabled = false;
      $('#baseline-status').textContent = err.message || 'Could not open the camera session.';
    });
  });
  $('#baseline-skip')?.addEventListener('click', async () => {
    try {
      await api('consumer/baseline-skip', {});
      goto('#/app/home');
    } catch (err) {
      $('#baseline-status').textContent = err.message || 'Could not skip the measurement.';
    }
  });
}

async function renderConsumerHome() {
  window.RehabVoiceAgent?.endTalk?.();
  const [me, exercises] = await Promise.all([
    api('consumer/me'),
    loadExerciseLibrary(),
  ]);
  state.consumerPatientId = me.patient_id;
  if (!(me.intake_complete && me.report_complete)) {
    location.hash = '#/app';
    return renderConsumerTalk();
  }
  // Talk is done but the camera has not measured anything yet.
  if (!me.home_ready) {
    location.hash = '#/app/baseline';
    return renderConsumerBaseline();
  }
  window.RehabVoiceContext = { scene: 'consumer', patient_id: me.patient_id };
  const progress = me.progress && !me.progress.error ? me.progress : {};
  const scores = (me.memory && me.memory.intake_scores) || {};
  const abdSeries = (me.abduction_series || []).map(r => r.value).filter(v => v != null).slice(-12);
  const painMove = (me.pain_series || []).map(r => r.movement).filter(v => v != null).slice(-12);
  const abdNow = progress.abduction?.current ?? me.memory?.ocr_abduction ?? me.memory?.last_peak_abduction;
  const painNow = progress.pain_movement?.current ?? scores.pain_movement ?? me.memory?.ocr_pain;
  const demo = Boolean(me.is_demo);
  const name = me.display_name || 'there';
  const side = me.affected_side || me.profile?.affected_side || '—';
  const age = me.age ?? me.profile?.age;
  const recent = me.recent_sessions || [];
  const summaries = me.session_summaries || me.memory?.session_summaries || [];
  let selectedExercise = exercises.find(e => e.exercise_id === 'shoulder_abduction')?.exercise_id
    || exercises[0]?.exercise_id
    || 'shoulder_abduction';
  const phonePose = Boolean(me.phone_pose_available || state.health?.phone_pose_available);
  let selectedCapture = phonePose ? 'phone' : 'simulation';
  const apiHost = apiOrigin() || location.origin;
  const selected = (exercises || []).find(e => e.exercise_id === selectedExercise) || {};
  const showCharts = abdSeries.length > 1 || painMove.length > 1;
  const historyRows = (recent.length ? recent.slice().reverse() : []).slice(0, 8).map(s => {
      const when = (s.started_at || s.created_at || '').slice(0, 16).replace('T', ' ');
      const peak = s.peak_angle != null ? Math.round(s.peak_angle) + '°' : '—';
      const src = s.source || 'stored';
      return `<li><strong>${escapeHtml(s.exercise_id || s.kind || 'session')}</strong><span>${escapeHtml(when || '—')}</span><em>${escapeHtml(String(peak))} · ${escapeHtml(src)}</em></li>`;
    }).join('')
    || summaries.slice().reverse().slice(0, 8).map(s => {
      const peak = s.peak != null ? Math.round(s.peak) + '°' : '—';
      return `<li><strong>${escapeHtml(s.movement || 'session')}</strong><span>${escapeHtml(s.session_id || 'saved')}</span><em>${escapeHtml(String(peak))} · ${escapeHtml(s.source || 'stored')}</em></li>`;
    }).join('');
  const fullName = me.profile?.full_name || state.user?.full_name || name;
  const email = state.user?.email || '';
  const account = {
    name,
    initials: String(fullName).split(/\s+/).map(p => p[0]).slice(0, 2).join('').toUpperCase() || 'R',
    email,
    age,
    side: String(side),
  };
  consumerShell({
    step: 3,
    account,
    body: `<section class="dash-slide dash-home">
      <div class="dash-hero">
        <div class="dash-identity">
          <span class="account-avatar lg" aria-hidden="true">${escapeHtml(account.initials)}</span>
          <div>
            <p class="eyebrow">${demo ? 'Signed in · demo labelled' : 'Signed in'}</p>
            <h1>${escapeHtml(fullName)}</h1>
            <p class="lede">${escapeHtml(email)} · ${me.talk_skipped ? 'Talk skipped — start a session below.' : 'built from your Talk answers.'} Not a diagnosis.</p>
          </div>
        </div>
        <div class="dash-profile" aria-label="Your profile">
          <span>${age != null ? age + ' yrs' : 'Age —'}</span>
          <span>${escapeHtml(String(side))} shoulder</span>
          <span>Pain move ${painNow == null ? '—' : painNow + '/10'}</span>
          <span>Dress ${scores.difficulty_dressing == null ? '—' : scores.difficulty_dressing + '/4'}</span>
        </div>
        <div class="dash-snapshot" aria-label="Your snapshot">
          <article>
            <span>Abduction</span>
            <strong>${abdNow == null ? '—' : Math.round(abdNow) + '°'}</strong>
            <em>${demo ? 'Demo / stored' : 'Stored'}</em>
          </article>
          <article>
            <span>Pain on move</span>
            <strong>${painNow == null ? '—' : painNow + '/10'}</strong>
            <em>Voice</em>
          </article>
          <article>
            <span>Rest pain</span>
            <strong>${scores.pain_rest == null ? '—' : scores.pain_rest + '/10'}</strong>
            <em>Voice</em>
          </article>
        </div>
        <div class="dash-start">
          <p class="eyebrow">New session</p>
          ${exercisePickerHtml(exercises, selectedExercise, 'chips')}
          <p class="dash-pick-meta" id="dash-pick-meta">${escapeHtml(selected.name || 'Exercise')} · target ${Number(selected.target_range) || '—'}°</p>
          <div class="capture-choice" aria-label="Measurement source">
            ${phonePose ? '<button type="button" data-capture="phone" class="is-selected">Phone camera · OpenCV</button>' : ''}
            <button type="button" data-capture="simulation" class="${phonePose ? '' : 'is-selected'}">Simulation</button>
          </div>
          <button class="primary start-giant" id="go" type="button">Start &amp; record session</button>
          <p class="empty dash-sim-note" id="capture-note">${phonePose
            ? 'Phone camera · OpenCV + MediaPipe (Shoulder Tracker) · 2D RGB · not diagnosis'
            : 'OpenCV pose offline on this API — Simulation only (labelled).'}</p>
          ${phonePose ? '' : `<p class="empty dash-sim-note">API: <code>${escapeHtml(apiHost)}</code> · phone pose not ready.
            <button type="button" class="ghost" id="dash-use-railway">Use Railway (OpenCV)</button>
            <button type="button" class="ghost" id="dash-clear-api">Reset API host</button></p>`}
        </div>
      </div>
      <section class="dash-history" id="dash-history" aria-label="Past sessions">
        <h2>Past sessions</h2>
        <p class="sub">Stored measurements only — not a diagnosis.</p>
        ${historyRows
          ? `<ul class="session-list">${historyRows}</ul>`
          : '<p class="empty">No sessions yet. Start one above — it will show up here.</p>'}
      </section>
      ${showCharts ? `<section class="dash-progress" aria-label="Progress">
        <h2>Progress</h2>
        <p class="sub">Recent stored values only.</p>
        <div class="person-charts">
          <div class="panel">
            <div class="panel-head"><div><h2>Abduction</h2><p class="sub">${seriesCaption(me.abduction_series || [])}</p></div></div>
            ${chart(abdSeries, '#4d6848', true)}
          </div>
          <div class="panel mint">
            <div class="panel-head"><div><h2>Pain</h2><p class="sub">${painMove.length} scores</p></div></div>
            ${barChart(painMove, '#4d6848')}
          </div>
        </div>
        ${me.memory?.ocr_abduction != null ? `<p class="empty">OCR abduction ${me.memory.ocr_abduction}° · ${me.memory.ocr_engine || 'ocr'}</p>` : ''}
      </section>` : `<p class="empty dash-empty-progress">Progress charts appear after more stored sessions.</p>`}
    </section>`,
  }, '#/app/home');
  const meta = $('#dash-pick-meta');
  bindExercisePicker(document, pick => {
    selectedExercise = pick.exercise_id;
    const row = (exercises || []).find(e => e.exercise_id === pick.exercise_id) || {};
    if (meta) meta.textContent = `${row.name || pick.exercise_id} · target ${row.target_range ?? pick.target_range ?? '—'}°`;
  });
  document.querySelectorAll('[data-capture]').forEach(button => button.addEventListener('click', () => {
    selectedCapture = button.dataset.capture;
    document.querySelectorAll('[data-capture]').forEach(item => item.classList.toggle('is-selected', item === button));
    const note = $('#capture-note');
    if (note) note.textContent = selectedCapture === 'phone'
      ? 'Phone camera · OpenCV + MediaPipe (Shoulder Tracker) · 2D RGB · not diagnosis'
      : 'Simulation selected · synthetic measurements (labelled).';
  }));
  $('#dash-use-railway')?.addEventListener('click', () => {
    const cloud = 'https://rehabai-api-production.up.railway.app';
    localStorage.setItem('rehabai_api_origin', cloud);
    window.REHABAI_API_ORIGIN = cloud;
    // Hosted DB is separate from clinic LAN — clear local session so login/bootstrap retries.
    localStorage.removeItem('rehabai_token');
    state.token = null;
    state.user = null;
    location.hash = '#/app';
    location.reload();
  });
  $('#dash-clear-api')?.addEventListener('click', () => {
    localStorage.removeItem('rehabai_api_origin');
    localStorage.removeItem('rehabai_token');
    state.token = null;
    state.user = null;
    location.reload();
  });
  $('#go')?.addEventListener('click', () => startConsumerSession(selectedCapture, selectedExercise));
}

function renderLogin() {
  app.innerHTML = `<div class="login-shell">
    <section class="login-hero">
      <div>
        ${brandMark(false, true)}
        <p class="eyebrow" style="margin-top:22px">MEDHA · PS 8</p>
        <h1>A digital twin of the <em>shoulder journey</em>.</h1>
        <p>PhysioBuDDY measures movement, guides rehabilitation, and keeps a longitudinal record. The clinician stays responsible for diagnosis and treatment.</p>
      </div>
      <p>Intel RealSense · Jetson · arm IMU · FastAPI</p>
    </section>
    <section class="login-panel">
      <div class="login-card">
        <p class="eyebrow">Sign in</p>
        <h2>Hospital workstation</h2>
        <form id="login">
          <label class="field">Email <input name="email" value="priya.mehta@hospital.local" autocomplete="username"></label>
          <label class="field">Password <input name="password" type="password" value="" autocomplete="current-password"></label>
          <button class="primary" type="submit">Enter clinic</button>
          <p class="error" id="err" hidden></p>
        </form>
        <div class="accounts">
          <button data-email="priya.mehta@hospital.local">Physiotherapist · Priya Mehta</button>
          <button data-email="dr.rao@hospital.local">Doctor · Vikram Rao</button>
          <button data-email="admin@hospital.local">Admin · Maya Iyer</button>
        </div>
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
    form.password.value = '';
    form.password.focus();
  }));
}

async function renderDashboard() {
  const [data, appts] = await Promise.all([api('dashboard'), api('appointments').catch(() => [])]);
  const first = data.focus_patient || (data.recent_patients || [])[0];
  const progress = data.progress && !data.progress.error ? data.progress : {};
  const rom = data.abduction_series || [];
  const flexRom = data.flexion_series || [];
  const pain = data.pain_series || [];
  const abd = rom.map(r => r.value).filter(v => v != null);
  const painMove = pain.map(r => r.movement).filter(v => v != null);
  const abdNow = progress.abduction?.current;
  const painNow = progress.pain_movement?.current;
  const flexNow = progress.flexion?.current;
  const adherence = data.average_adherence == null ? null : Math.round(data.average_adherence);
  const demo = Boolean(data.demo_records_present || first?.is_demo);
  const abdStatus = measureStatus(rom, demo);
  const painStatus = painNow == null ? 'No score' : 'Reported';
  const adhStatus = adherence == null ? 'No sessions' : 'Goal rate';
  const insight = (data.alerts && data.alerts[0]?.message)
    || (first ? `${first.full_name}: ${data.series_note || 'stored measurements only.'}` : 'No stored patient summary yet.');
  const delta = progress.abduction?.change;
  const tip = delta == null ? 'No stored change yet' : `${fmtDelta(delta)} abduction across ${progress.point_count?.abduction || abd.length} stored points`;
  const bio = [
    ['Abduction', abdNow == null ? '—' : Math.round(abdNow) + '°', abdStatus, demo],
    ['Flexion', flexNow == null ? '—' : Math.round(flexNow) + '°', measureStatus(flexRom, demo), demo],
    ['Pain (move)', painNow == null ? '—' : painNow + '/10', painStatus, false],
    ['Sessions today', String(data.sessions_completed_today ?? '—'), 'Saved', false],
    ['Need review', String(data.patients_requiring_review ?? 0), data.patients_requiring_review ? 'Open' : 'Clear', Boolean(data.patients_requiring_review)],
  ];
  const pct = (v, cap) => v == null ? 0 : Math.max(0, Math.min(100, Math.round(v / cap * 100)));
  shell({
    header: '',
    body: `<div class="nura-top">
      <div>
        <div class="hello">
          <h1>${escapeHtml(helloLine())}</h1>
          <p>Clinic summary from stored sessions. Simulation and demo seed stay labelled.</p>
        </div>
        <div class="bares">
          ${bare(abdStatus, abdNow == null ? '—' : Math.round(abdNow), '°', 'Abduction')}
          ${bare(adhStatus, adherence == null ? '—' : adherence, '', 'Adherence')}
          ${bare('Today', data.sessions_completed_today ?? '—', '', 'Sessions done')}
          ${bare(painStatus, painNow == null ? '—' : painNow, '/10', 'Pain on movement')}
        </div>
        ${segBar(tip, delta, 40)}
      </div>
      <div class="insight">
        <h2>Stored record</h2>
        <div class="orb"></div>
        <p>${escapeHtml(insight)} This is not a diagnosis.</p>
      </div>
      <div class="score-card">
        <div class="label">Adherence</div>
        <div class="led">${adherence == null ? '—' : adherence}<span class="den">100</span></div>
        <span class="tag-dark">${adhStatus}</span>
      </div>
    </div>
    <div class="nura-mid">
      <div class="panel" style="position:relative">
        <div class="panel-head"><div><h2>Abduction trend</h2><p class="sub">${escapeHtml(first ? first.full_name : 'No patient')} · ${escapeHtml(seriesCaption(rom, data.series_note))}</p></div><a class="go" href="#/patients">↗</a></div>
        ${chart(abd, '#4d6848', true)}
        <div class="pills">
          <span class="pill">Latest ${abdNow == null ? '—' : Math.round(abdNow) + '°'}</span>
          <span class="pill">Baseline ${progress.abduction?.baseline ?? '—'}°</span>
          <span class="pill">${abd.length} points</span>
        </div>
      </div>
      <div class="panel mint">
        <div class="panel-head"><div><h2>Pain analysis</h2><p class="sub">${seriesCaption(pain.filter(r => r.movement != null), '0–10 patient-reported movement or session pain')}</p></div><a class="go" href="#/patients">↗</a></div>
        ${barChart(painMove, '#4d6848')}
        <div class="pills">
          <span class="pill">Latest ${painNow == null ? '—' : painNow + '/10'}</span>
          <span class="pill">Rest ${progress.pain_rest?.current ?? '—'}</span>
          <span class="pill">${painMove.length} points</span>
        </div>
      </div>
      <div class="panel bio">
        <div class="panel-head"><div><h2>Measurement overview</h2><p class="sub">Same stored ROM and pain series as the patient record. Not bloodwork.</p></div><a class="go" href="#/patients">↗</a></div>
        ${bio.map(r => `<div class="bio-row"><b>${r[0]}</b><span class="val">${r[1]}</span><span class="chip ${r[3] ? 'on' : ''}">${r[2]}</span></div>`).join('')}
      </div>
    </div>
    <div class="nura-bottom">
      <div class="panel">
        <div class="panel-head"><div><h2>Movement systems</h2><p class="sub">Share of a 90° / 10-pain / 100-adherence display scale. Not a clinical target or diagnosis.</p></div><a class="go" href="#/patients">↗</a></div>
        <div class="rings">
          ${ring('Abduction', abdNow == null ? null : pct(abdNow, 90))}
          ${ring('Flexion', flexNow == null ? null : pct(flexNow, 90))}
          ${ring('Comfort', painNow == null ? null : Math.round((10 - painNow) * 10))}
          ${ring('Adherence', adherence)}
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><div><h2>App patient logins</h2><p class="sub">Consumer APK / Talk accounts that signed in today. Linked to hospital caseload.</p></div><a class="go" href="#/patients">↗</a></div>
        <div class="bares" style="margin-bottom:12px">
          ${bare('App', data.consumer_patients ?? 0, '', 'App accounts')}
          ${bare('Today', data.consumer_signups_today ?? 0, '', 'New signups')}
          ${bare('Logins', data.consumer_logins_today ?? 0, '', 'Login events')}
        </div>
        ${(data.recent_consumer_logins || []).slice(0, 6).map(row => `
          <div class="up-item"><i class="dot"></i><div>
            <strong><a href="#/patients/${encodeURIComponent(row.patient_id)}">${escapeHtml(row.full_name)}</a></strong>
            <span>${escapeHtml(row.action)} · ${escapeHtml(new Date(row.at).toLocaleString())}${row.email ? ' · ' + escapeHtml(row.email) : ''}</span>
          </div></div>`).join('') || '<p class="empty">No consumer logins yet today.</p>'}
      </div>
      <div class="panel">
        <div class="panel-head"><div><h2>Upcoming</h2><p class="sub">Your upcoming appointments, tests, and reminders.</p></div><a class="go" href="#/appointments">↗</a></div>
        ${(appts || []).slice(0, 3).map(a => `<div class="up-item"><i class="dot"></i><div><strong>${escapeHtml(a.reason || 'Visit')}</strong><span>${escapeHtml(new Date(a.scheduled_for).toLocaleString())} · ${escapeHtml(a.patient_id)}</span></div></div>`).join('') || '<p class="empty">No upcoming items.</p>'}
      </div>
      <div class="panel">
        <div class="panel-head"><div><h2>Recent patients</h2><p class="sub">Includes app logins and clinic records.</p></div></div>
        ${patientTable(data.recent_patients)}
      </div>
    </div>`
  }, '#/dashboard');
}

async function renderPatients() {
  const [rows, dash] = await Promise.all([api('patients'), api('dashboard').catch(() => ({}))]);
  const first = rows[0];
  const consumerCount = rows.filter(p => p.account_source === 'consumer').length;
  shell({
    header: `<div class="row"><div><h1>Patients</h1><p>Clinic registrations and PhysioBuDDY app accounts share this hospital caseload. App logins appear with a Consumer badge.</p></div>${state.user.role !== 'PATIENT' ? '<button class="primary" id="new-patient" type="button">Register patient</button>' : ''}</div>`,
    body: `<div class="stat-row">
        <div><strong>${rows.length}</strong><span>Patients</span></div>
        <div><strong>${consumerCount}</strong><span>App accounts</span></div>
        <div><strong>${dash.consumer_signups_today ?? '—'}</strong><span>App signups today</span></div>
        <div><strong>${dash.consumer_logins_today ?? '—'}</strong><span>App logins today</span></div>
      </div>
      <form class="panel intake-register" id="patient-form" hidden>
        <p class="eyebrow">Step 1 · patient registration</p>
        <h2>Identity and clinical assignment</h2>
        <p class="sub">Enter identifiers carefully. Voice begins after the record is created, so names and MRNs are confirmed visually.</p>
        <div class="form-grid">
          <label class="field"><span>Full name</span><input name="full_name" required maxlength="100"></label>
          <label class="field"><span>Medical record number</span><input name="mrn" required maxlength="40"></label>
          <label class="field"><span>Date of birth</span><input name="date_of_birth" type="date"></label>
          <label class="field"><span>Sex</span><select name="sex"><option value="">Not recorded</option><option>Female</option><option>Male</option><option>Other</option></select></label>
          <label class="field"><span>Affected shoulder</span><select name="affected_side"><option value="right">Right</option><option value="left">Left</option></select></label>
          <label class="field"><span>Clinician-entered diagnosis</span><input name="clinician_diagnosis" placeholder="Optional; never inferred by AI"></label>
        </div>
        <div class="row"><button class="primary" type="submit">Create record</button><button class="ghost" id="cancel-patient" type="button">Cancel</button><span class="error" id="patient-form-error"></span></div>
      </form>
      <div class="filters" id="patient-filters">
        <button class="active" type="button" data-filter="all">All</button>
        <button type="button" data-filter="consumer">App / Talk</button>
        <button type="button" data-filter="clinic">Clinic</button>
        <button type="button" data-filter="demo">Demo</button>
      </div>
      <div class="records">
        <div class="panel" id="patient-table-wrap">${patientTable(rows)}
          ${state.user.role === 'ADMIN' ? '<p class="empty">Admins can view records but do not start treatment.</p>' : ''}
        </div>
        <div class="panel mint">
          <h2>${escapeHtml(first ? first.full_name : 'Caseload')}</h2>
          <p class="sub">${escapeHtml(first ? first.mrn + ' · ' + first.affected_side + ' shoulder' : 'No patient loaded')}</p>
          <p>${first ? (first.account_source === 'consumer' ? 'PhysioBuDDY app account — Talk and phone sessions sync here.' : 'Open the record to start assessment or review stored ROM and pain.') : ''}</p>
          ${first ? `<p style="margin-top:16px"><a class="primary" href="#/patients/${first.id}" style="display:inline-block;text-decoration:none">Open record →</a></p>` : ''}
        </div>
      </div>`
  }, '#/patients');
  const form = $('#patient-form');
  $('#new-patient')?.addEventListener('click', () => { form.hidden = false; form.querySelector('[name="full_name"]').focus(); });
  $('#cancel-patient')?.addEventListener('click', () => { form.hidden = true; form.reset(); });
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form).entries());
    try {
      const patient = await api('patients', values);
      state.tab = 'overview';
      goto('#/patients/' + patient.id);
    } catch (err) {
      $('#patient-form-error').textContent = err.message;
    }
  });
  const wrap = $('#patient-table-wrap');
  document.querySelectorAll('#patient-filters button').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#patient-filters button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const filter = btn.dataset.filter;
      const filtered = rows.filter(p => {
        if (filter === 'consumer') return p.account_source === 'consumer';
        if (filter === 'clinic') return p.account_source !== 'consumer' && !p.is_demo;
        if (filter === 'demo') return p.is_demo;
        return true;
      });
      if (wrap) {
        wrap.innerHTML = patientTable(filtered)
          + (state.user.role === 'ADMIN' ? '<p class="empty">Admins can view records but do not start treatment.</p>' : '');
      }
    });
  });
}

async function renderPatient(id) {
  const [patient, assessments, sessions, rom, pain, reports, notes, recordings, plans, exercises] = await Promise.all([
    api('patients/' + id),
    api(`patients/${id}/assessments`),
    api(`patients/${id}/sessions`),
    api(`patients/${id}/rom`),
    api(`patients/${id}/pain`),
    api('reports?patient_id=' + id),
    api(`patients/${id}/notes`),
    api(`patients/${id}/recordings`),
    api('plans?patient_id=' + id),
    loadExerciseLibrary(),
  ]);
  state.tab = state.tab || 'overview';
  const p = patient.progress || {};
  const abdProtocol = p.series_sources?.abduction;
  const flexProtocol = p.series_sources?.flexion;
  const abdRows = rom.filter(r => r.movement === 'abduction' && (!abdProtocol || r.source === abdProtocol));
  const flexRows = rom.filter(r => r.movement === 'flexion' && (!flexProtocol || r.source === flexProtocol));
  const tabs = ['overview','assessment','ROM','pain','exercises','sessions','recordings','progress','AI','notes','reports'];
  shell({
    header: `<p class="eyebrow">${patient.is_demo ? 'DEMO PATIENT' : (patient.account_source === 'consumer' ? 'APP / TALK PATIENT' : 'PATIENT')} · ${escapeHtml(patient.mrn)}</p>
      <h1>${escapeHtml(patient.full_name)}</h1>
      <p>${escapeHtml(patient.clinician_diagnosis || '')} · Affected ${escapeHtml(patient.affected_side)} shoulder
        ${patient.account_source === 'consumer' ? ' · PhysioBuDDY app login' : ''}
        ${patient.last_login_at ? ' · Last login ' + escapeHtml(new Date(patient.last_login_at).toLocaleString()) : ''}</p>`,
    body: `<div class="panel" style="margin-bottom:16px">
        <div class="row">
          ${state.user.role !== 'ADMIN' ? `<button class="primary" id="start-assess">Start assessment</button>
          <button class="ghost" id="start-rehab">Start rehab session</button>` : ''}
          <button class="ghost" id="export-json" type="button">Export JSON</button>
          <button class="ghost" id="export-xlsx" type="button">Export Excel</button>
          <span class="empty" id="export-status"></span>
        </div>
      </div>
      <div class="tabs">${tabs.map(t => `<button data-tab="${t}" class="${state.tab===t?'active':''}">${t}</button>`).join('')}</div>
      <div id="tab"></div>`
  }, '#/patients');
  const tabEl = $('#tab');
  const paint = () => {
    if (state.tab === 'overview') {
      const abdSeries = abdRows.map(r => r.value);
      const painSeries = pain.map(r => r.movement).filter(v => v != null);
      const abdNow = p.abduction?.current;
      const painNow = p.pain_movement?.current;
      const demo = Boolean(patient.is_demo || p.demo_records_present);
      tabEl.innerHTML = `<div class="bares">
          ${bare(measureStatus(abdRows, demo), abdNow == null ? '—' : Math.round(abdNow), '°', 'Abduction')}
          ${bare('Stored', p.flexion?.current == null ? '—' : Math.round(p.flexion.current), '°', 'Flexion')}
          ${bare(painNow == null ? 'No score' : 'Reported', painNow == null ? '—' : painNow, '/10', 'Pain now')}
          ${bare('Saved', sessions.filter(s => s.status==='complete').length, '', 'Sessions')}
        </div>
        <div class="nura-mid">
          <div class="panel"><div class="panel-head"><div><h2>Abduction</h2><p class="sub">${seriesCaption(abdRows, 'Stored degrees')}</p></div></div>${chart(abdSeries, '#4d6848', true)}</div>
          <div class="panel mint"><div class="panel-head"><div><h2>Pain</h2><p class="sub">${painSeries.length} stored scores</p></div></div>${barChart(painSeries, '#4d6848')}</div>
          <div class="score-card">
            <div class="label">Abduction</div>
            <div class="led">${abdNow == null ? '—' : Math.round(abdNow)}<span class="den">°</span></div>
            <p class="hint">Latest stored range of motion. Not a health score. Torso compensation ${p.torso_compensation?.current ?? '—'}°.</p>
          </div>
        </div>
        <div class="panel" style="margin-top:14px">
          <p>Baseline abduction ${p.abduction?.baseline ?? '—'}° → current ${p.abduction?.current ?? '—'}° (${fmtDelta(p.abduction?.change)}) from ${p.point_count?.abduction || abdSeries.length} stored points.</p>
          <p>${escapeHtml(p.comparison_note || '')}</p>
          <p>No recovery percentage is computed. Changes are shown in degrees and pain scores only.</p>
          ${demo ? '<p class="badge demo">Includes labelled demo / synthetic records</p>' : ''}
          ${patient.account_source === 'consumer' ? `<div class="panel" style="margin-top:14px"><h2>App activity</h2><p class="sub">Login and Talk events from the consumer app.</p>${
            (patient.recent_activity || []).length
              ? table(['When', 'Action', 'Detail'], patient.recent_activity.map(a => [
                  new Date(a.at).toLocaleString(),
                  a.action,
                  a.detail?.source || a.resource || '—',
                ]))
              : '<p class="empty">No app activity logged yet.</p>'
          }</div>` : ''}
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
        <div class="panel"><h2>Abduction over time</h2><p class="sub">${seriesCaption(abdRows)}</p>${chart(abdRows.map(r => r.value), '#7aa24a')}</div>
        <div class="panel"><h2>Flexion over time</h2><p class="sub">${seriesCaption(flexRows)}</p>${chart(flexRows.map(r => r.value), '#2f6b4f')}</div>
      </div>`;
    } else if (state.tab === 'pain') {
      const painMove = pain.map(r => r.movement).filter(v => v != null);
      tabEl.innerHTML = `<div class="panel"><h2>Pain during movement</h2><p class="sub">${painMove.length} stored scores</p>${chart(painMove, '#b94a32')}</div>`;
    } else if (state.tab === 'sessions' || state.tab === 'exercises') {
      tabEl.innerHTML = `<div class="panel">
        <h2>Approved exercises & sessions</h2>
        <p class="sub">Pick an exercise when you start a rehab session — the 3D guide follows that demo.</p>
        <div class="row" style="margin-bottom:12px">
          <button class="primary" type="button" id="tab-start-rehab">Start rehab session</button>
        </div>
        ${table(['Session','Exercise','Reps','Peak','Source'], sessions.map(s => [
          s.id, s.exercise_id, `${s.reps}/${s.goal}`, deg(s.peak_angle), s.source
        ]))}
      </div>`;
      $('#tab-start-rehab')?.addEventListener('click', () => goto(`#/patients/${id}/start-rehab`));
    } else if (state.tab === 'recordings') {
      tabEl.innerHTML = `<div class="panel">${recordings.map(r => `<div class="history-item"><a href="#/recordings/${encodeURIComponent(r.id)}">${escapeHtml(r.session_id)}</a> · ${escapeHtml(r.kind)}</div>`).join('') || '<p class="empty">No consented recordings.</p>'}</div>`;
    } else if (state.tab === 'AI') {
      window.RehabVoiceContext = { scene: 'assistant', patient_id: id };
      tabEl.innerHTML = assistantPanel(id);
      bindAssistant(id);
    } else if (state.tab === 'notes') {
      tabEl.innerHTML = `<div class="panel">${notes.map(n => `<p><strong>${escapeHtml(n.author)}</strong> · ${escapeHtml(new Date(n.created_at).toLocaleString())}<br>${escapeHtml(n.body)}</p>`).join('')}</div>`;
    } else if (state.tab === 'reports') {
      tabEl.innerHTML = `<div class="panel">${reports.map(r => `<p><a href="#/reports/${encodeURIComponent(r.id)}">${escapeHtml(r.kind)} · ${escapeHtml(r.id)}</a> ${r.requires_approval ? '<span class="badge warn">pending</span>' : '<span class="badge">approved</span>'}</p>`).join('')}</div>`;
    }
  };
  paint();
  document.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', () => { state.tab = btn.dataset.tab; document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b===btn)); paint(); }));
  $('#start-assess')?.addEventListener('click', () => goto(`#/patients/${id}/start-assess`));
  $('#start-rehab')?.addEventListener('click', () => goto(`#/patients/${id}/start-rehab`));
  $('#export-json')?.addEventListener('click', async () => {
    try { $('#export-status').textContent = 'Preparing JSON…'; await downloadAuthorized(`patients/${id}/export.json`, `rehabai-${id}.json`); $('#export-status').textContent = 'JSON exported.'; }
    catch (err) { $('#export-status').textContent = err.message; }
  });
  $('#export-xlsx')?.addEventListener('click', async () => {
    try { $('#export-status').textContent = 'Preparing Excel…'; await downloadAuthorized(`patients/${id}/export.xlsx`, `rehabai-${id}.xlsx`); $('#export-status').textContent = 'Excel exported.'; }
    catch (err) { $('#export-status').textContent = err.message; }
  });
}

async function renderStartRehabSession(patientId, assessment) {
  const [patient, exercises] = await Promise.all([
    api('patients/' + patientId),
    loadExerciseLibrary(),
  ]);
  const list = exercises || [];
  let selected = list.find(e => e.exercise_id === 'shoulder_abduction') || list[0];
  if (!selected) {
    shell({
      header: `<h1>No exercises</h1>`,
      body: `<div class="panel"><p class="error">Exercise library is empty.</p>
        <button class="ghost" type="button" id="back-patient">Back</button></div>`,
    }, '#/patients');
    $('#back-patient')?.addEventListener('click', () => goto(`#/patients/${patientId}`));
    return;
  }
  const side = patient.affected_side === 'left' ? 'left' : 'right';
  const title = assessment ? 'Start assessment' : 'Start rehab session';
  const remountGuide = () => {
    const demo = selected.avatar_demo || selected.exercise_id;
    const target = Number(selected.target_range) || 90;
    mountGuide('#guide3d', {
      exercise: selected.exercise_id,
      avatar: demo,
      target,
      side,
    });
    const host = $('#guide3d');
    if (host) {
      host.dataset.exercise = selected.exercise_id;
      host.dataset.avatar = demo;
      host.dataset.target = String(target);
      host.dataset.side = side;
    }
    const meta = $('#ex-meta');
    if (meta) {
      meta.innerHTML = `<strong>${escapeHtml(selected.name)}</strong>
        <span>3D demo · ${escapeHtml(demo.replaceAll('_', ' '))} · target ${target}°</span>
        <p>${escapeHtml(selected.instructions || '')}</p>`;
    }
  };

  shell({
    header: `<p class="eyebrow">${escapeHtml(patient.full_name)} · ${escapeHtml(patient.mrn)}</p>
      <h1>${title}</h1>
      <p>Choose the exercise first. The 3D guide demonstrates that movement — not a diagnosis.</p>`,
    body: `<div class="start-rehab-layout">
        <div class="panel start-rehab-pick">
          <p class="eyebrow">Choose exercise · 3D guide follows this demo</p>
          ${exercisePickerHtml(list, selected.exercise_id)}
          <div id="ex-meta" class="ex-meta"></div>
          <div class="row">
            <button class="primary" type="button" id="confirm-start">${assessment ? 'Continue to assessment' : 'Continue to rehab session'}</button>
            <button class="ghost" type="button" id="back-patient">Back to patient</button>
          </div>
        </div>
        <div class="view guide start-rehab-guide">
          <div class="view-label">3D GUIDE PREVIEW · PACKAGE DEMO</div>
          <div id="guide3d" class="guide-host"
            data-exercise="${selected.exercise_id}"
            data-avatar="${selected.avatar_demo || selected.exercise_id}"
            data-target="${selected.target_range || 90}"
            data-side="${side}"></div>
        </div>
      </div>`,
  }, '#/patients');

  bindExercisePicker(document, pick => {
    selected = list.find(e => e.exercise_id === pick.exercise_id) || selected;
    remountGuide();
  });
  remountGuide();
  $('#back-patient')?.addEventListener('click', () => {
    window.RehabGuide?.stop?.();
    goto(`#/patients/${patientId}`);
  });
  $('#confirm-start')?.addEventListener('click', async () => {
    window.RehabGuide?.stop?.();
    await startLive(patientId, selected.exercise_id, assessment, 'auto', {
      side,
      target: Number(selected.target_range) || undefined,
    });
  });
}

async function startLive(patientId, exercise, assessment, capture = 'auto', extras = {}) {
  try {
    const lib = await loadExerciseLibrary();
    const spec = (lib || []).find(e => e.exercise_id === exercise) || {};
    const target = Number(extras.target || spec.target_range || 80);
    const session = await api('sessions', {
      patient_id: patientId,
      exercise_id: exercise,
      side: extras.side || 'right',
      target,
      goal: extras.goal || 5,
      consent_recording: false,
      kind: assessment ? 'assessment' : 'rehab',
      capture,
    });
    goto('#/live/' + session.id);
  } catch (e) {
    const rows = await api('sessions?patient_id=' + patientId).catch(() => []);
    const open = rows.find(s => s.status === 'calibrating' || s.status === 'running');
    if (open) return goto('#/live/' + open.id);
    app.insertAdjacentHTML('afterbegin', `<p class="error">${escapeHtml(e.message)}</p>`);
  }
}

async function renderLive(sessionId, opts = {}) {
  const [session, script, exercises] = await Promise.all([
    api('sessions/' + sessionId), api('intake/script'), loadExerciseLibrary(),
  ]);
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
  let painCheck = null;
  const exSpec = (exercises || []).find(e => e.exercise_id === session.exercise_id) || {};
  const avatarDemo = exSpec.avatar_demo || session.exercise_id;
  // A forward raise has no angle in a frontal 2D view, so it is measured side-on.
  const sagittalPhone = session.source === 'phone' && exSpec.phone_plane === 'sagittal';

  const current = () => fields[index];
  const promptOf = f => (language === 'hi-IN' ? f.prompt_hi : f.prompt_en);
  const confirmOf = (f, value) => (language === 'hi-IN' ? f.confirm_hi : f.confirm_en).replace('{value}', value);
  const consumer = isConsumerMode();
  const measureMetrics = `<div class="metrics live-metrics">
              <div class="metric"><span>Current ROM</span><b id="ang">—°</b></div>
              <div class="metric"><span>Target</span><b id="target-deg">${session.target}°</b></div>
              <div class="metric"><span>Reps</span><b id="reps" data-goal="${session.goal}">0 / ${session.goal}</b></div>
              <div class="metric"><span>Torso lean</span><b id="lean">—°</b></div>
              <div class="metric"><span>Tracking</span><b id="conf">—</b></div>
              <div class="metric"><span>Quality</span><b id="cov">—</b></div>
              <div class="metric"><span>Arm IMU</span><b id="imu">—</b></div>
              <div class="metric"><span>Fused ROM</span><b id="fused">—°</b></div>
            </div>`;
  const liveBody = `<div class="checks" id="calib"></div>
      <div class="grid-live guide-first ${consumer ? 'consumer-live' : ''}">
        <div class="view guide">
          <div class="view-label">3D GUIDE · FOLLOW ALONG</div>
          <div id="guide3d" class="guide-host" data-exercise="${session.exercise_id}" data-avatar="${avatarDemo}" data-target="${session.target}" data-side="${session.side || 'right'}"></div>
        </div>
        <div class="live-side">
          <div class="intake-panel" id="intake-panel">
            <p class="eyebrow">Voice intake · ${script.voice?.stt || 'local speech'} · not a diagnosis</p>
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
            <div class="coach-strip" id="coach-strip">
              <p class="eyebrow">Conservative AI coach · measured numbers + 3D guide · not a diagnosis</p>
              <p class="coach-note" id="coach-note">Tap Coach to talk while the 3D model demonstrates. Say pause if it hurts.</p>
              <div class="row">
                <button class="primary" type="button" id="coach-talk">Talk with coach</button>
                <button type="button" class="ghost" id="voice-dock" hidden aria-hidden="true">
                  <strong id="voice-dock-title">Coach</strong>
                  <em id="voice-dock-hint">inline</em>
                </button>
              </div>
            </div>
            ${measureMetrics}
            <p class="feedback" id="fb">${sagittalPhone
              ? 'Turn so the affected shoulder faces the camera, then hold a relaxed posture.'
              : 'Stand in the marked area. Hold a relaxed posture.'}</p>
            <p id="sub" class="empty"></p>
            ${session.source === 'phone' ? `<div class="phone-capture-controls">
              <div class="phone-stage">
                <video id="phone-camera" class="phone-preview phone-mirror" playsinline muted></video>
                <canvas id="skel" class="phone-overlay phone-mirror" width="640" height="480" aria-hidden="true"></canvas>
              </div>
              <button class="primary" id="phone-camera-start" type="button">Enable phone camera</button>
              <p class="intake-status" id="phone-camera-status">${sagittalPhone
                ? 'Stand side-on so the affected shoulder faces the camera. Frames stay transient; only landmarks and measurements are stored.'
                : 'Camera frames stay transient. Only landmarks and measurements are stored.'}</p>
            </div>` : ''}
            <div class="row live-actions">
              <button class="primary" id="confirm">Confirm tracking & start</button>
              <button class="danger" id="finish">Stop & save</button>
              ${consumer ? '<a class="ghost" href="#/app/home">Back to You</a>' : ''}
            </div>
            ${session.source === 'simulation' ? `<div class="row sim-faults">
              <span class="eyebrow">Simulation</span>
              <button class="ghost" data-fault="none">Normal</button>
              <button class="ghost" data-fault="lean">Lean</button>
              <button class="ghost" data-fault="occlusion">Lost</button>
              <button class="ghost" data-fault="imu_drop">IMU drop</button>
            </div>` : ''}
          </div>
          <div class="intake-panel" id="debrief-panel" hidden>
            <p class="eyebrow">${escapeHtml(exSpec.name || session.exercise_id.replaceAll('_', ' '))} done</p>
            <div id="debrief-metrics" class="metrics" style="margin-bottom:16px"></div>
            <div id="debrief-pain"></div>
            <p class="intake-status" id="debrief-status">Tap a face. There is no default score.</p>
            <div class="row">
              <button class="primary mic-btn" id="debrief-mic" type="button">Speak</button>
              <button class="danger" id="debrief-save" type="button">Save session</button>
            </div>
          </div>
          <div class="intake-panel" id="recap-panel" hidden></div>
        </div>
      </div>`;

  if (consumer) {
    const meName = (state.user?.full_name || 'You').split(' ')[0];
    const initials = String(state.user?.full_name || 'R').split(/\s+/).map(p => p[0]).slice(0, 2).join('').toUpperCase();
    consumerShell({
      step: session.kind === 'assessment' ? 2 : 3,
      account: {
        name: meName,
        initials,
        email: state.user?.email || '',
        side: session.side,
      },
      body: `<section class="live-consumer">
        <p class="eyebrow">${session.source === 'simulation' ? 'Simulation' : session.source === 'phone' ? 'Phone RGB' : 'Live'} · ${escapeHtml(session.exercise_id.replaceAll('_', ' '))}</p>
        ${liveBody}
      </section>`,
    }, '#/live');
  } else {
    shell({
      header: `<p class="eyebrow">${session.source === 'live' ? 'Live RealSense + arm IMU' : session.source === 'phone' ? 'Live phone RGB · 2D pose' : 'Simulation · synthetic RGB-D + arm IMU'}</p>
      <h1>${session.exercise_id.replaceAll('_',' ')}</h1>`,
      body: liveBody,
    }, '#/patients');
  }
  // The pose the server is actually scoring, drawn over the live camera.
  liveCanvas = $('#skel');
  mountGuide('#guide3d', {
    exercise: session.exercise_id,
    avatar: avatarDemo,
    target: session.target,
    side: session.side || 'right',
  });

  function showPhase() {
    $('#intake-panel').hidden = phase !== 'intake';
    $('#measure-block').hidden = phase !== 'measure';
    $('#debrief-panel').hidden = phase !== 'debrief';
    if ($('#recap-panel')) $('#recap-panel').hidden = phase !== 'recap';
    window.RehabVoiceLang = language;
    const field = current();
    window.RehabVoiceContext = {
      scene: phase === 'intake' ? 'intake' : (phase === 'measure' ? 'measure' : phase),
      phase,
      language,
      exercise: session.exercise_id,
      movement: exSpec.movement || session.exercise_id,
      avatar_demo: avatarDemo,
      goal: session.goal,
      target: window.RehabGuideTarget != null ? window.RehabGuideTarget : session.target,
      demo_target: window.RehabGuideTarget != null ? window.RehabGuideTarget : session.target,
      coach_mode: phase === 'measure' ? 'session' : undefined,
      session_patient_id: session.patient_id,
      intake_field: phase === 'intake' && field ? field.id : undefined,
      question: phase === 'intake' && field ? promptOf(field) : undefined,
      awaiting_confirm: phase === 'intake' && pending != null,
    };
    const dockTitle = document.getElementById('voice-dock-title');
    const dockHint = document.getElementById('voice-dock-hint');
    if (phase === 'measure') {
      if (dockTitle) dockTitle.textContent = 'Coach';
      if (dockHint) dockHint.textContent = 'Talk while the 3D guide moves · not a diagnosis';
    }
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
    window.RehabVoiceLang = language;
    window.RehabVoiceContext = {
      scene: 'intake',
      phase: 'intake',
      language,
      exercise: session.exercise_id,
      session_patient_id: session.patient_id,
      intake_field: f.id,
      question: promptOf(f),
      awaiting_confirm: pending != null,
    };
    if (speakPrompt && phase === 'intake' && !paused && pending == null && !listening && !window.RehabVoiceAgent?.isTalking?.()) {
      voice.speak?.(promptOf(f), language);
    }
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
    $('#mic').textContent = 'Listening…';
    $('#heard').textContent = 'Listening… speak a number, then pause.';
    try {
      const text = await voice.listen(language, {
        onTranscript: spoken => { $('#heard').textContent = 'Heard: ' + spoken; },
        onStatus: msg => { $('#heard').textContent = msg; },
        transcribe: transcribeWav,
      });
      $('#heard').textContent = text ? ('Heard: ' + text) : 'No speech captured. Tap a number, or press Speak and say it again.';
      if (!text) return;
      const parsed = await api('intake/parse', { field: current().id, text, awaiting_confirm: pending != null, language });
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
      $('#heard').textContent = err.message || 'Voice capture failed. Tap a number instead.';
    } finally {
      listening = false;
      $('#mic').classList.remove('hot');
      $('#mic').textContent = 'Speak';
    }
  }

  function openDebrief() {
    stopPhoneCamera();
    phase = 'debrief';
    painAfter = null;
    showPhase();
    const tel = window.RehabLiveLast || {};
    if ($('#debrief-metrics')) {
      $('#debrief-metrics').innerHTML = [
        ['Peak / last ROM', tel.peak != null && Number.isFinite(Number(tel.peak))
          ? Math.round(Number(tel.peak)) + '°'
          : (tel.valid ? Math.round(tel.shoulder_angle || 0) + '°' : '—')],
        ['Reps', (tel.rep || 0) + ' / ' + session.goal],
        ['Torso lean', Math.round(tel.torso_lean || 0) + '°'],
        ['Safety', tel.safety?.level || '—'],
      ].map(([k, v]) => `<div class="metric"><span>${k}</span><b>${v}</b></div>`).join('');
    }
    const host = $('#debrief-pain');
    if (host && window.RehabPain?.renderPainCheck) {
      painCheck = window.RehabPain.renderPainCheck(host, {
        moment: 'after',
        previousValue: null,
        onChange: (v) => {
          painAfter = v;
          $('#debrief-status').textContent = 'Pain after ' + painAfter + ' out of 10. Press Save session to store it.';
        },
      });
    } else if (host) {
      host.innerHTML = '<div id="debrief-chips"></div>';
      paintChips($('#debrief-chips'), 10, 'pain');
      $('#debrief-chips').querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
        painAfter = Number(btn.dataset.val);
        $('#debrief-chips').querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
        $('#debrief-status').textContent = 'Pain after ' + painAfter + ' out of 10. Press Save session to store it.';
      }));
    }
    voice.speak?.('Pain after this session, zero to ten?', language);
  }

  async function saveSession() {
    if (!Number.isInteger(painAfter) || painAfter < 0 || painAfter > 10) {
      $('#debrief-status').textContent = 'Choose pain after from 0 to 10. Nothing is filled in for you.';
      return;
    }
    const saved = await api(`sessions/${sessionId}/finish`, {
      pain_after: painAfter,
      create_assessment: session.kind === 'assessment',
    });
    closeLive({ keepGuide: true });
    phase = 'recap';
    showPhase();
    paintRecap(saved, painAfter);
  }

  function paintRecap(saved, pain) {
    const s = saved.session || {};
    const sum = saved.summary || {};
    const source = s.source === 'live' ? 'live RGB-D sensors · unvalidated'
      : s.source === 'phone' ? 'phone RGB · 2D-derived · no depth · unvalidated'
      : 'synthetic RGB-D + IMU';
    const peak = s.peak_angle != null ? s.peak_angle : sum.peak;
    const baseline = saved.baseline || {};
    const baselineStep = isConsumerMode() && s.kind === 'assessment';
    const nextStep = baselineStep && !baseline.complete ? (BASELINE_STEPS[baseline.next_movement] || null) : null;
    $('#recap-panel').innerHTML = `
      <p class="eyebrow">${baselineStep ? 'Baseline saved' : 'Session saved'} · ${source} · not a diagnosis</p>
      <h2>Stored measurements</h2>
      <div class="metrics">
        <div class="metric"><span>Peak ROM</span><b>${peak == null ? '—' : Math.round(peak) + '°'}</b></div>
        <div class="metric"><span>Valid reps</span><b>${s.reps ?? 0}</b></div>
        <div class="metric"><span>Coverage</span><b>${s.coverage == null ? '—' : s.coverage + '%'}</b></div>
        <div class="metric"><span>Pain after</span><b>${pain}/10</b></div>
        <div class="metric"><span>Safety</span><b>${s.safety_outcome || (sum.safety && sum.safety.level) || '—'}</b></div>
        <div class="metric"><span>Assessment</span><b>${saved.assessment_id ? 'Yes' : 'No'}</b></div>
      </div>
      <p class="empty">These values are written to the patient record. The camera does not diagnose adhesive capsulitis.</p>
      ${saved.insight && saved.insight.spoken ? `<div class="ai-insight">
        <p class="eyebrow">AI session note · measured values · not a diagnosis</p>
        <p>${escapeHtml(saved.insight.spoken)}</p>
      </div>` : ''}
      ${nextStep ? `<p class="empty">One movement left. Next: ${escapeHtml(nextStep.name.toLowerCase())} — ${escapeHtml(nextStep.stance.toLowerCase())}</p>` : ''}
      <div class="row">
        <button class="primary" id="recap-patient" type="button">${nextStep
          ? 'Measure ' + escapeHtml(nextStep.name.toLowerCase())
          : baselineStep ? 'See my baseline' : isConsumerMode() ? 'Back to You' : 'Open patient record'}</button>
        ${isConsumerMode() ? '' : '<button class="ghost" id="recap-xlsx" type="button">Export Excel</button>'}
      </div>
      <p class="intake-status" id="recap-status"></p>`;
    $('#recap-patient')?.addEventListener('click', () => {
      closeLive();
      if (isConsumerMode()) {
        goto(baselineStep ? '#/app/baseline' : '#/app/home');
        return;
      }
      state.tab = 'overview';
      goto('#/patients/' + session.patient_id);
    });
    $('#recap-xlsx')?.addEventListener('click', async () => {
      try {
        $('#recap-status').textContent = 'Preparing Excel…';
        await downloadAuthorized(`patients/${session.patient_id}/export.xlsx`, `rehabai-${session.patient_id}.xlsx`);
        $('#recap-status').textContent = 'Excel exported.';
      } catch (err) {
        $('#recap-status').textContent = err.message;
      }
    });
  }

  showPhase();
  if (phase === 'intake') paintIntake(true);
  else $('#intake-panel').hidden = true;
  const voiceOk = Boolean(voice.canListen && voice.canListen());
  if ($('#mic')) $('#mic').hidden = !voiceOk;
  if ($('#debrief-mic')) $('#debrief-mic').hidden = !voiceOk;

  window.RehabIntakeOnParsed = (parsed, transcript) => {
    if (phase !== 'intake' || paused || !parsed) return;
    if (parsed.intent === 'safety_pause') {
      paused = true;
      pending = null;
      paintIntake(false);
      return;
    }
    if (parsed.intent === 'confirm_yes' && pending != null) {
      saveField(pending, 'voice', transcript);
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
    }
  };
  function setIntakeLanguage(next) {
    const changed = language !== next;
    language = next;
    window.RehabVoiceLang = language;
    paintIntake(changed);
  }
  $('#lang-en').addEventListener('click', () => setIntakeLanguage('en-IN'));
  $('#lang-hi').addEventListener('click', () => setIntakeLanguage('hi-IN'));
  $('#mic').addEventListener('click', hearIntake);
  $('#yes').addEventListener('click', () => pending != null && saveField(pending, 'voice'));
  $('#no').addEventListener('click', () => { pending = null; paintIntake(true); });
  $('#confirm').addEventListener('click', async () => {
    try { await api(`sessions/${sessionId}/confirm`, {}); }
    catch (e) { $('#sub').textContent = e.message; }
  });
  $('#coach-talk')?.addEventListener('click', () => {
    window.RehabVoiceAgent?.bindDock?.();
    window.RehabVoiceAgent?.talk?.();
  });
  $('#finish').addEventListener('click', openDebrief);

  window.RehabLiveControl = {
    pause() {
      paused = true;
      api(`sessions/${sessionId}/pause`, {}).catch(() => {});
      if (phase === 'measure') openDebrief();
      else if (phase === 'intake') paintIntake(false);
    },
  };
  $('#debrief-save').addEventListener('click', () => saveSession().catch(err => { $('#debrief-status').textContent = err.message; }));
  $('#debrief-mic').addEventListener('click', async () => {
    if (!voice.listen) return;
    $('#debrief-mic').classList.add('hot');
    $('#debrief-mic').textContent = 'Listening…';
    $('#debrief-status').textContent = 'Listening… speak a number from zero to ten.';
    try {
      const text = await voice.listen(language, {
        onTranscript: spoken => { $('#debrief-status').textContent = 'Heard: ' + spoken; },
        onStatus: msg => { $('#debrief-status').textContent = msg; },
        transcribe: transcribeWav,
      });
      if (!text) {
        $('#debrief-status').textContent = 'No speech captured. Tap a number, or press Speak and say it again.';
        return;
      }
      const parsed = await api('intake/parse', { field: 'pain_movement', text, awaiting_confirm: false, language });
      if (parsed.intent === 'safety_pause') {
        $('#debrief-status').textContent = parsed.spoken;
        return;
      }
      if (parsed.intent === 'number') {
        painAfter = parsed.parsed_value;
        painCheck?.setValue?.(painAfter);
        $('#debrief-status').textContent = 'Pain after ' + painAfter + ' out of 10. Press Save session to store it.';
        $('#debrief-chips')?.querySelectorAll?.('button')?.forEach?.(b => b.classList.toggle('active', Number(b.dataset.val) === painAfter));
        voice.speak?.('Pain after ' + painAfter + ' out of 10.', language);
      } else {
        $('#debrief-status').textContent = parsed.spoken;
      }
    } catch (err) {
      $('#debrief-status').textContent = err.message || 'Voice capture failed. Tap a number instead.';
    } finally {
      $('#debrief-mic').classList.remove('hot');
      $('#debrief-mic').textContent = 'Speak';
    }
  });
  document.querySelectorAll('[data-fault]').forEach(btn => btn.addEventListener('click', () => api(`sessions/${sessionId}/fault`, { fault: btn.dataset.fault })));
  $('#phone-camera-start')?.addEventListener('click', () => startPhoneCamera(sessionId));
  // Opening the session was the user's gesture; do not make them tap twice.
  // If the browser refuses, startPhoneCamera leaves the manual button in place.
  if (session.source === 'phone') startPhoneCamera(sessionId);
  connectLive(sessionId);
}

function connectLive(sessionId) {
  closeLive({ keepVoice: true, keepGuide: true, keepPhone: true });
  const origin = apiOrigin();
  let proto;
  let host;
  if (origin) {
    const u = new URL(origin);
    proto = u.protocol === 'https:' ? 'wss' : 'ws';
    host = u.host;
  } else {
    proto = location.protocol === 'https:' ? 'wss' : 'ws';
    host = location.host;
  }
  ws = new WebSocket(`${proto}://${host}/api/ws/sessions/${sessionId}`, ['rehabai', state.token]);
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
    ['Depth', cal.simulation ? 'SIM' : (cal.depth_required === false ? 'N/A' : cal.depth_ok)],
    ['Pose', cal.pose_ok],
    [String(cal.capture_profile || '').startsWith('phone_rgb_2d') ? 'Framing' : 'Distance', cal.distance_ok],
    ['Arm IMU', imuChip],
  ];
  const calib = $('#calib');
  if (calib) calib.innerHTML = checks.map(([n, ok]) => `<div class="check ${ok===true || ok==='SIM' || ok==='N/A' || ok==='Off' ? 'on' : 'off'}"><strong>${n}</strong><div>${ok === 'SIM' ? 'Simulation' : ok === 'N/A' ? 'Not used' : ok === 'Off' ? 'Off' : ok ? 'OK' : 'Not ready'}</div></div>`).join('');
  if ($('#ang')) $('#ang').textContent = row.valid ? Math.round(row.shoulder_angle) + '°' : '—';
  if ($('#lean')) $('#lean').textContent = Math.round(row.torso_lean || 0) + '°';
  if ($('#reps')) $('#reps').textContent = `${row.rep || 0} / ${$('#reps').dataset.goal}`;
  if ($('#conf')) $('#conf').textContent = Math.round((row.pose_confidence || 0) * 100) + '%';
  if ($('#cov')) $('#cov').textContent = (row.coverage ?? '—') + '%';
  if ($('#imu')) $('#imu').textContent = imuChip === 'SIM' ? 'SIM' : (imu.ok ? Math.round(imu.gyro_norm || 0) + '°/s' : 'lost');
  if ($('#fused')) $('#fused').textContent = imu.fused_angle != null ? Math.round(imu.fused_angle) + '°' : '—';
  if ($('#fb')) $('#fb').textContent = row.feedback || '';
  if ($('#sub')) $('#sub').textContent = (row.safety?.level ? 'Safety ' + row.safety.level : '') + (row.source === 'simulation' ? ' · synthetic RGB-D + IMU' : row.source === 'phone' ? ' · phone RGB · 2D-derived · no depth · unvalidated' : ' · live RGB-D stream');
  if ($('#view-label')) $('#view-label').textContent = streamCaption(row);
  const phoneStatus = $('#phone-camera-status');
  if (phoneStatus && row.source === 'phone') {
    const q = row.pose_quality || {};
    const parts = [];
    if (row.valid) parts.push(`Angle ${Math.round(row.shoulder_angle || 0)}°`);
    if (q.mean_visibility != null) parts.push(`vis ${(q.mean_visibility * 100).toFixed(0)}%`);
    if (q.joint_count != null) parts.push(`${q.joint_count} joints`);
    if (q.inference_ms != null) parts.push(`${Math.round(q.inference_ms)} ms`);
    if (q.frame_w && q.frame_h) parts.push(`${q.frame_w}×${q.frame_h}`);
    phoneStatus.textContent = parts.length
      ? `Live phone RGB · ${parts.join(' · ')} · 2D-derived, not diagnosis`
      : 'Live phone RGB is being analysed. Raw frames are not saved.';
  }
  drawSkeleton(row);
  if (row.guide && window.RehabGuide) {
    const guide = {
      ...row.guide,
      target: row.guide.target_deg ?? row.guide.target ?? window.RehabGuideTarget,
      angle: row.shoulder_angle,
    };
    window.RehabGuide.update(guide);
    window.RehabGuide.setCaption(spokenOverride || row.guide.cue || '');
    maybeGuideCaption(row.guide);
  }
  window.RehabLiveLast = row;
  maybeSpeakCoach(row);
  maybeAdaptiveCoach(row);
}

function maybeSpeakCoach(row) {
  if ($('#measure-block')?.hidden) return;
  if (window.RehabVoiceAgent?.isTalking?.()) return;
  const safety = row.safety?.level || row.guide?.safety || '';
  // Adaptive coach owns ALLOW cues; pipeline voice only for stop/pause.
  if (safety !== 'BLOCK' && safety !== 'PAUSE') return;
  const cue = (row.feedback || '').trim();
  if (!cue || cue === lastCoachCue) return;
  lastCoachCue = cue;
  window.RehabIntake?.speak?.(cue, window.RehabVoiceLang || 'en-IN');
}

function maybeAdaptiveCoach(row) {
  if ($('#measure-block')?.hidden) return;
  if (adaptiveCoachBusy) return;
  if (!row.valid && !(row.safety?.level === 'BLOCK' || row.safety?.level === 'PAUSE')) return;
  const talking = Boolean(window.RehabVoiceAgent?.isTalking?.());
  const safety = row.safety?.level || row.guide?.safety || 'ALLOW';
  const peak = row.peak != null ? Number(row.peak) : null;
  const angle = row.valid ? Number(row.shoulder_angle) : null;
  const target = Number(window.RehabGuideTarget ?? row.guide?.target_deg ?? row.guide?.target ?? 0) || 0;
  const phase = row.guide?.phase || row.exercise_phase || '';
  const above = peak != null && target > 0 && peak >= target + 8;
  const bucket = Math.round((peak ?? angle ?? 0) / 15);
  const key = [safety, phase, above ? 'above' : 'track', bucket].join('|');
  const now = Date.now();
  const minGap = above || safety !== 'ALLOW' ? 7000 : 12000;
  if (key === adaptiveCoachKey && adaptiveCoachAt && (now - adaptiveCoachAt) < minGap) return;
  if (!above && safety === 'ALLOW' && phase !== 'peak' && phase !== 'ascending' && adaptiveCoachAt && (now - adaptiveCoachAt) < 12000) return;
  adaptiveCoachKey = key;
  adaptiveCoachAt = now;
  adaptiveCoachBusy = true;
  api('guide/coach', {
    language: window.RehabVoiceLang || 'en-IN',
    exercise: window.RehabGuideExercise || row.exercise || row.exercise_id,
    movement: row.guide?.movement,
    phase,
    safety,
    target: target || null,
    angle: angle != null && Number.isFinite(angle) ? angle : null,
    peak: peak != null && Number.isFinite(peak) ? peak : null,
    reps: row.rep,
    goal: Number($('#reps')?.dataset?.goal) || null,
    feedback: (row.feedback || '').slice(0, 160),
    patient_said: (window.RehabLastPatientSaid || '').slice(0, 200),
    guide_cue: (row.guide?.cue || '').slice(0, 120),
    avatar_demo: window.RehabGuideAvatar || undefined,
    avatar_phase: window.RehabCoachAvatar?.getState?.()?.avatar_phase,
    avatar_reps: window.RehabCoachAvatar?.getState?.()?.avatar_reps,
    avatar_demo_angle: window.RehabCoachAvatar?.getState?.()?.avatar_demo_angle,
  }).then(res => {
    const spoken = (res.spoken || '').trim();
    if (window.RehabCoachAvatar?.applyCoachReply) {
      window.RehabCoachAvatar.applyCoachReply(res);
    } else {
      if (spoken) {
        spokenOverride = spoken;
        window.RehabGuide?.setCaption(spoken);
        const note = $('#coach-note');
        if (note) note.textContent = spoken;
      }
      if (res.demo_target != null && Number.isFinite(Number(res.demo_target))) {
        window.RehabGuide?.setDemoTarget?.(Number(res.demo_target));
        window.RehabGuideTarget = Number(res.demo_target);
        if ($('#target-deg')) $('#target-deg').textContent = Math.round(Number(res.demo_target)) + '°';
      }
    }
    if (spoken) {
      spokenOverride = spoken;
      // While Talk is active, only update caption/demo — avoid competing TTS.
      if (!talking && spoken !== lastCoachCue) {
        lastCoachCue = spoken;
        window.RehabIntake?.speak?.(spoken, window.RehabVoiceLang || 'en-IN');
      } else if (!talking) {
        lastCoachCue = spoken;
      }
    }
  }).catch(() => {}).finally(() => { adaptiveCoachBusy = false; });
}

function maybeGuideCaption(guide) {
  // Live measure uses adaptive coach (/guide/coach); keep caption endpoint for other surfaces.
  if (!$('#measure-block')?.hidden) return;
  if (!state.health?.llm_available || !guide) return;
  const key = [guide.phase, guide.safety, guide.cue].join('|');
  if (key === guideCaptionKey) return;
  guideCaptionKey = key;
  spokenOverride = '';
  api('guide/caption', {
    cue: guide.cue, phase: guide.phase, safety: guide.safety, movement: guide.movement,
  }).then(res => {
    if (res.caption && !res.bones_from_llm) {
      spokenOverride = res.caption;
      window.RehabGuide?.setCaption(res.caption);
    }
  }).catch(() => {});
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
  if (cam === 'phone') return 'PHONE RGB · 2D POSE · NO DEPTH · UNVALIDATED';
  return imu === 'live' ? 'LIVE RGB-D + ARM IMU · UNVALIDATED' : 'LIVE RGB-D · POSE OVERLAY';
}

function drawSkeleton(row) {
  const canvas = liveCanvas || $('#skel');
  if (!canvas) return;
  if ($('#phone-camera')) sizePoseOverlay();
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  if (row.frame_jpeg) {
    const img = new Image();
    img.onload = () => { ctx.drawImage(img, 0, 0, w, h); };
    img.src = 'data:image/jpeg;base64,' + row.frame_jpeg;
    return;
  }
  if ($('#phone-camera')) ctx.clearRect(0, 0, w, h);
  else { ctx.fillStyle = '#10241f'; ctx.fillRect(0, 0, w, h); }
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

async function startPhoneCamera(sessionId) {
  const status = $('#phone-camera-status');
  const button = $('#phone-camera-start');
  if (!navigator.mediaDevices?.getUserMedia) {
    if (status) status.textContent = 'This device does not provide browser camera access.';
    return;
  }
  stopPhoneCamera();
  try {
    if (status) status.textContent = 'Requesting camera permission…';
    phoneMediaStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: 'user',
        width: { ideal: 1280 },
        height: { ideal: 720 },
        frameRate: { ideal: 24, max: 30 },
      },
      audio: false,
    });
    const video = $('#phone-camera');
    if (!video) throw new Error('Phone camera view is unavailable');
    video.srcObject = phoneMediaStream;
    await video.play();
    sizePoseOverlay();
    if (button) { button.textContent = 'Camera active'; button.disabled = true; }
    if (status) status.textContent = 'Live phone RGB is being analysed. Raw frames are not saved.';
    phoneCaptureCanvas = document.createElement('canvas');
    schedulePhoneFrame(sessionId, 0);
  } catch (err) {
    stopPhoneCamera();
    if (button) { button.textContent = 'Enable phone camera'; button.disabled = false; }
    if (status) status.textContent = err.message || 'Camera permission was not granted. Tap Enable phone camera.';
  }
}

function sizePoseOverlay() {
  const canvas = liveCanvas || $('#skel');
  const video = $('#phone-camera');
  if (!canvas || !video) return;
  const box = video.getBoundingClientRect();
  const w = Math.round(box.width) || video.videoWidth || 640;
  const h = Math.round(box.height) || video.videoHeight || 480;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
}

function schedulePhoneFrame(sessionId, delay = 120) {
  if (!phoneMediaStream) return;
  clearTimeout(phoneFrameTimer);
  phoneFrameTimer = setTimeout(() => uploadPhoneFrame(sessionId), delay);
}

async function uploadPhoneFrame(sessionId) {
  if (!phoneMediaStream || phoneFrameUploading) return schedulePhoneFrame(sessionId);
  const video = $('#phone-camera');
  if (!video || video.readyState < 2 || !video.videoWidth) return schedulePhoneFrame(sessionId);
  phoneFrameUploading = true;
  try {
    const maxWidth = 960;
    const scale = Math.min(1, maxWidth / video.videoWidth);
    phoneCaptureCanvas.width = Math.round(video.videoWidth * scale);
    phoneCaptureCanvas.height = Math.round(video.videoHeight * scale);
    phoneCaptureCanvas.getContext('2d').drawImage(video, 0, 0, phoneCaptureCanvas.width, phoneCaptureCanvas.height);
    const blob = await new Promise(resolve => phoneCaptureCanvas.toBlob(resolve, 'image/jpeg', 0.82));
    if (!blob) throw new Error('Could not encode the camera frame');
    const body = new FormData();
    body.append('frame', blob, 'frame.jpg');
    const headers = state.token ? { Authorization: 'Bearer ' + state.token } : {};
    const response = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/phone-frame`), {
      method: 'POST', headers, body,
    });
    if (!response.ok && response.status !== 429) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || 'Phone pose inference failed');
    }
  } catch (err) {
    const status = $('#phone-camera-status');
    if (status) status.textContent = err.message || 'Phone frame upload failed.';
  } finally {
    phoneFrameUploading = false;
    schedulePhoneFrame(sessionId);
  }
}

function stopPhoneCamera() {
  clearTimeout(phoneFrameTimer);
  phoneFrameTimer = null;
  phoneFrameUploading = false;
  if (phoneMediaStream) phoneMediaStream.getTracks().forEach(track => track.stop());
  phoneMediaStream = null;
  phoneCaptureCanvas = null;
  const video = $('#phone-camera');
  if (video) video.srcObject = null;
}

function closeLive(opts = {}) {
  if (!opts.keepPhone) stopPhoneCamera();
  window.RehabLiveControl = null;
  const inCall = Boolean(window.RehabVoiceAgent?.isTalking?.());
  if (!opts.keepVoice && !inCall) {
    window.RehabIntake?.stopVoice?.();
    window.RehabIntakeOnParsed = null;
    window.RehabVoiceContext = null;
  }
  if (!opts.keepGuide) {
    window.RehabGuide?.stop?.();
    guideCaptionKey = '';
    spokenOverride = '';
    lastCoachCue = '';
    adaptiveCoachKey = '';
    adaptiveCoachAt = 0;
    adaptiveCoachBusy = false;
    window.RehabLiveLast = null;
  }
  if (wsTimer) clearTimeout(wsTimer);
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
}

async function renderAlerts() {
  const rows = await api('alerts');
  shell({ header: `<h1>Alerts</h1><p>Open items from stored sessions.</p>`, body: `<div class="panel">${alertList(rows)}</div>` }, '#/alerts');
}

async function renderReports() {
  const rows = await api('reports');
  shell({ header: `<h1>Reports</h1><p>Generated from stored measurements. Not a diagnosis.</p>`, body: `<div class="panel">${rows.map(r => `<p><a href="#/reports/${encodeURIComponent(r.id)}">${escapeHtml(r.patient_id)} · ${escapeHtml(r.kind)}</a></p>`).join('')}</div>` }, '#/reports');
}

async function renderReport(id) {
  const row = await api('reports/' + id);
  const preview = await api(`reports/${id}/preview`);
  shell({ header: `<h1>Report</h1>`, body: `<div class="panel">${preview.html}
    ${row.requires_approval && state.user.role !== 'ADMIN' && state.user.role !== 'PATIENT' ? '<button class="primary" id="approve">Approve</button>' : ''}</div>` }, '#/reports');
  $('#approve')?.addEventListener('click', async () => { await api(`reports/${id}/approve`, {}); renderReport(id); });
}

function assistantPanel(patientId) {
  return `<div class="panel">
    <p class="empty">Talk with PhysioBuDDY. No scripted questions. It uses live and stored measurements only — never invents ROM or a diagnosis.</p>
    <div class="chat" id="chat"></div>
    <div class="row">
      <input id="q" style="flex:1" placeholder="Speak with Talk, or type anything…">
      <button class="primary" id="ask">Ask</button>
      <button class="ghost" id="talk-ask" type="button">Talk</button>
    </div>
  </div>`;
}

function bindAssistant(patientId) {
  const ask = async q => {
    const question = (q || '').trim();
    if (!question) {
      window.RehabVoiceAgent?.talk?.();
      return;
    }
    $('#chat').insertAdjacentHTML('beforeend', `<div class="bubble me">${escapeHtml(question)}</div>`);
    const res = await api('agent/query', { patient_id: patientId, question: question });
    $('#chat').insertAdjacentHTML('beforeend', `<div class="bubble">${escapeHtml(res.summary || '')}
      <div class="ai-meta">${res.llm_used ? 'Live LLM' : 'Stored measurements'}</div>
    </div>`);
  };
  $('#ask')?.addEventListener('click', () => ask($('#q').value));
  $('#talk-ask')?.addEventListener('click', () => window.RehabVoiceAgent?.talk?.());
}

async function renderAppointments() {
  const rows = await api('appointments');
  shell({ header: `<h1>Appointments</h1>`, body: `<div class="panel">${table(['When','Patient','Reason','Status'], rows.map(r => [new Date(r.scheduled_for).toLocaleString(), r.patient_id, r.reason, r.status]))}</div>` }, '#/appointments');
}

async function renderRecordings() {
  const patients = await api('patients');
  const lists = await Promise.all(patients.map(p => api(`patients/${p.id}/recordings`)));
  const rows = lists.flat();
  shell({ header: `<h1>Recordings</h1><p>Consented session pointers. Video binaries are not stored in SQL.</p>`, body: `<div class="panel">${rows.map(r => `<p><a href="#/recordings/${encodeURIComponent(r.id)}">${escapeHtml(r.patient_id)} · ${escapeHtml(r.session_id)}</a></p>`).join('') || '<p class="empty">No recordings.</p>'}</div>` }, '#/recordings');
}

async function renderRecording(id) {
  const row = await api('recordings/' + id);
  shell({ header: `<h1>Recording replay</h1>`, body: `<div class="panel">
    <p>${row.seeded ? 'Seeded demo pointer — no binary video is stored.' : 'Telemetry replay from consented session. Video binaries are stored on disk, not in the database.'}</p>
    <div class="grid-live guide-first">
      <div class="view guide"><div id="guide3d" class="guide-host" data-exercise="shoulder_abduction" data-target="90" data-side="right"></div></div>
    </div>
    <p id="fb" class="feedback"></p></div>` }, '#/recordings');
  liveCanvas = null;
  mountGuide('#guide3d', { exercise: 'shoulder_abduction', target: 90, side: 'right' });
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
  shell({ header: `<h1>Staff</h1>`, body: `<div class="panel">${table(['Name','Email','Role'], rows.map(u => [u.full_name, u.email, u.role]))}<p class="empty">Admins manage accounts. They do not automatically modify treatment plans.</p></div>` }, '#/staff');
}

async function renderSettings() {
  shell({ header: `<h1>Settings</h1><p>Station configuration. Source is chosen at server start.</p>`, body: `<div class="panel">
    <p>Measurement source is selected at server start (<code>REHABAI_SOURCE=simulation|live</code>).</p>
    <p>The station uses <strong>RealSense RGB-D</strong> for 3D ROM and an <strong>arm IMU</strong> for rate and movement quality. IMU packets are not used to diagnose frozen shoulder.</p>
    <p>Live mode never silently falls back to synthetic values. A live camera never receives a simulated IMU.</p>
    <p>Talk is LLM-in-the-loop from stored measurements and live metrics, not a FAQ. Claude never overrides BLOCK. ROM, reps and BLOCK safety continue if the LLM is down.</p>
    <p>The Talk button starts a live call. PhysioBuDDY keeps listening and answering until you tap again or say you are done. Replies use Sarvam first for speed; ElevenLabs is skipped after a failed paid-voice attempt. Step 3 is an allowlisted in-page browser action (Playwright-style selectors). The model never receives the page or identifiers.</p>
    <p>The 3D coach is a procedural Three.js follow-along mannequin (rehab.ai motion + goniometer arc). It demonstrates the approved target for the selected exercise and mirrors the affected side. Live patient angle from telemetry is shown on the arc — the LLM never poses the mesh. Optional Mixamo <code>web/models/guide.glb</code> remains unsupported in this build; do not commit someone else's photogrammetry.</p>
  </div>` }, '#/settings');
}

async function renderPatientHome() {
  const me = state.user;
  const patients = await api('patients');
  const patient = patients[0];
  const progress = patient ? await api(`patients/${patient.id}/progress`) : {};
  const appts = await api('appointments');
  shell({
    header: `<h1>${helloLine()}</h1><p>Keep today’s movement gentle. Your physiotherapist stays in charge of the plan. This screen only shows measured sessions.</p>`,
    body: `<section class="patient-home">
      <button class="primary start-giant" id="go">Start session</button>
      <div class="kpi-row" style="margin-top:22px">
        ${kpi('Abduction', progress.abduction ? progress.abduction.current + '°' : '—', 'Stored')}
        ${kpi('Pain', progress.pain_movement ? progress.pain_movement.current + '/10' : '—', 'Reported')}
        ${kpi('Next visit', appts[0] ? new Date(appts[0].scheduled_for).toLocaleDateString() : '—', 'Diary')}
      </div>
    </section>`
  }, '#/home');
  $('#go')?.addEventListener('click', () => startLive(patient.id, 'shoulder_abduction', false));
}

async function ensureConsumerLogin(force) {
  state.consumer = true;
  if (!force && localStorage.getItem('rehabai_signed_out') === '1') {
    state.token = null;
    state.user = null;
    return;
  }
  if (state.token) {
    try {
      state.user = await api('me');
      if (state.user?.role === 'PATIENT') {
        try { await api('auth/touch', {}); } catch { /* non-blocking */ }
        return;
      }
    } catch {
      /* fall through */
    }
    state.token = null;
    state.user = null;
    localStorage.removeItem('rehabai_token');
  }
  const savedRaw = localStorage.getItem('rehabai_patient_creds');
  if (savedRaw) {
    try {
      const saved = JSON.parse(savedRaw);
      if (saved?.email && saved?.password) {
        const res = await api('auth/login', { email: saved.email, password: saved.password });
        if (res.user?.role === 'PATIENT') {
          state.token = res.token;
          state.user = res.user;
          localStorage.setItem('rehabai_token', res.token);
          localStorage.removeItem('rehabai_signed_out');
          return;
        }
      }
    } catch {
      localStorage.removeItem('rehabai_patient_creds');
    }
  }
  const res = await api('consumer/bootstrap', { full_name: 'New patient' });
  state.token = res.token;
  state.user = res.user;
  localStorage.setItem('rehabai_token', res.token);
  localStorage.setItem('rehabai_patient_creds', JSON.stringify({
    email: res.email,
    password: res.password,
  }));
  localStorage.removeItem('rehabai_signed_out');
}

async function startConsumerSession(capture, exerciseId) {
  const me = await api('consumer/me');
  state.consumerPatientId = me.patient_id;
  const side = (me.affected_side || me.profile?.affected_side) === 'left' ? 'left' : 'right';
  await startLive(
    me.patient_id,
    exerciseId || 'shoulder_abduction',
    false,
    capture,
    { side },
  );
}

async function renderPatientProgress() {
  const patients = await api('patients');
  if (patients[0]) { state.tab = 'progress'; return renderPatient(patients[0].id); }
}

function stat(label, value) { return kpi(label, value); }
function kpi(label, value, tag) {
  return `<div class="kpi"><span>${label}</span><strong>${value}</strong>${tag ? `<em>${tag}</em>` : ''}</div>`;
}
function bare(status, value, unit, caption) {
  return `<div class="bare"><em>${status}</em><strong>${value}${unit ? `<small>${unit}</small>` : ''}</strong><span>${caption}</span></div>`;
}
function ring(label, pct) {
  const missing = pct == null || Number.isNaN(Number(pct));
  const p = missing ? 0 : Math.max(0, Math.min(100, Number(pct)));
  const r = 26, c = 2 * Math.PI * r, dash = missing ? 0 : c * p / 100;
  return `<div class="ring"><div class="ring-dial">
    <svg viewBox="0 0 72 72">
      <circle cx="36" cy="36" r="${r}" fill="none" stroke="#e4ebe3" stroke-width="5"/>
      <circle cx="36" cy="36" r="${r}" fill="none" stroke="#161616" stroke-width="5" stroke-linecap="round"
        stroke-dasharray="${dash} ${c}" transform="rotate(-90 36 36)"/>
    </svg>
    <strong>${missing ? '—' : Math.round(p) + '%'}</strong>
  </div><span>${label}</span></div>`;
}
function measureStatus(rows, demo) {
  if (!rows || !rows.length) return 'No ROM';
  if (demo) return 'Demo seed';
  const last = rows[rows.length - 1] || {};
  if (last.source === 'live') return 'Live stored';
  if (last.source === 'simulation') return 'Simulation';
  return 'Stored';
}
function seriesCaption(rows, fallback) {
  const list = rows || [];
  if (!list.length) return fallback || 'No stored points';
  const first = list[0].recorded_at ? new Date(list[0].recorded_at).toLocaleDateString() : '';
  const last = list[list.length - 1].recorded_at ? new Date(list[list.length - 1].recorded_at).toLocaleDateString() : '';
  const liveN = list.filter(r => r.source === 'live').length;
  const simN = list.filter(r => r.source === 'simulation').length;
  const bits = [`${list.length} stored`];
  if (liveN) bits.push(liveN + ' live');
  if (simN) bits.push(simN + ' simulation');
  if (first && last) bits.push(first === last ? first : first + ' → ' + last);
  return bits.join(' · ');
}
function segBar(label, change, span) {
  const range = span == null ? 40 : span;
  let on = -1;
  if (change != null && Number.isFinite(Number(change))) {
    const t = (Number(change) + range) / (2 * range);
    on = Math.max(0, Math.min(8, Math.round(t * 8)));
  }
  const bits = Array.from({ length: 9 }, (_, i) => `<i class="${i === on ? 'on' : ''}"></i>`).join('');
  return `<div class="segbar">${bits}<span class="seg-tip">${label}</span></div>`;
}
function deg(v) { return v == null ? '—' : Math.round(v) + '°'; }
function fmtDelta(v) { return v == null ? '—' : (v > 0 ? '+' : '') + Math.round(v) + '°'; }
function patientTable(rows) {
  return table(['Patient', 'MRN', 'Side', 'Source', 'Last login'], (rows || []).map(p => [
    trustedHtml(`<a href="#/patients/${encodeURIComponent(p.id)}">${escapeHtml(p.full_name)}</a>`),
    p.mrn,
    p.affected_side,
    p.is_demo
      ? trustedHtml('<span class="badge demo">demo</span>')
      : (p.account_source === 'consumer'
        ? trustedHtml('<span class="badge">app / talk</span>')
        : 'clinic'),
    p.last_login_at ? new Date(p.last_login_at).toLocaleString() : (p.created_at ? ('Joined ' + new Date(p.created_at).toLocaleDateString()) : '—'),
  ]));
}
function alertList(rows) {
  if (!rows?.length) return '<p class="empty">No open alerts.</p>';
  return rows.map(a => `<p><span class="badge ${a.severity==='critical'?'crit':'warn'}">${escapeHtml(a.severity)}</span> ${escapeHtml(a.message)}</p>`).join('');
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[ch]);
}
function trustedHtml(value) { return { __rehabHtml: String(value) }; }
function table(headers, rows) {
  const cell = value => value && typeof value === 'object' && value.__rehabHtml != null
    ? value.__rehabHtml : escapeHtml(value);
  return `<table><thead><tr>${headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${cell(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function chart(values, color, area) {
  const nums = (values || []).filter(v => v != null && !Number.isNaN(Number(v))).map(Number);
  if (!nums.length) return '<p class="empty">No stored points.</p>';
  const w = 420, h = 168, max = Math.max(...nums, 1), min = Math.min(...nums, 0);
  const pts = nums.map((v, i) => {
    const x = i * (w - 28) / Math.max(1, nums.length - 1) + 14;
    const y = h - 22 - ((v - min) / (max - min || 1)) * (h - 40);
    return [x, y];
  });
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) d += ` L ${pts[i][0]} ${pts[i][1]}`;
  const last = pts[pts.length - 1];
  const fill = `${d} L ${last[0]} ${h - 8} L ${pts[0][0]} ${h - 8} Z`;
  const grid = [42, 84, 126].map(y => `<line x1="14" x2="${w - 14}" y1="${y}" y2="${y}"/>`).join('');
  const dots = pts.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="3.2" fill="${color}"/>`).join('');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <g stroke="#d5e0d6" stroke-dasharray="3 7" stroke-width="1">${grid}</g>
    ${area !== false ? `<path d="${fill}" fill="${color}" fill-opacity=".18"/>` : ''}
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    ${dots}
  </svg>`;
}
function barChart(values, color) {
  if (!values.length) return '<p class="empty">No stored points.</p>';
  const w = 420, h = 168, max = Math.max(...values, 1);
  const n = values.length;
  const gap = (w - 28) / n;
  const bw = Math.max(4, gap * 0.42);
  const bars = values.map((v, i) => {
    const bh = Math.max(4, (v / max) * (h - 36));
    const x = 14 + i * gap + (gap - bw) / 2;
    const y = h - 16 - bh;
    return `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="3" fill="${color}" fill-opacity=".32"/>`;
  }).join('');
  const pts = values.map((v, i) => {
    const x = 14 + i * gap + gap / 2;
    const y = h - 16 - Math.max(4, (v / max) * (h - 36));
    return [x, y];
  });
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0] + ' ' + p[1]).join(' ');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${bars}
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round"/></svg>`;
}

async function boot() {
  const packaged = Boolean(window.REHABAI_PACKAGED || apiOrigin());
  const tried = apiCandidateOrigins();
  const resolved = await resolveApiOrigin();
  if (packaged && resolved === null && tried.length) {
    renderOfflineGate(tried);
    return;
  }
  if (!state.health) {
    try { state.health = await fetch(apiUrl('/api/health')).then(r => r.json()); } catch {}
  }
  if (isConsumerMode()) {
    state.consumer = true;
    if (!location.hash || location.hash === '#/' || location.hash === '#/login') {
      location.hash = '#/app';
    }
  }
  if (state.token) {
    try { state.user = await api('me'); } catch { state.token = null; localStorage.removeItem('rehabai_token'); }
  }
  window.addEventListener('hashchange', () => { route(); });
  await route();
}
boot();
