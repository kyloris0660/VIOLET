const stateBadge = document.getElementById('stateBadge');
const primaryMessage = document.getElementById('primaryMessage');
const checklistEl = document.getElementById('checklist');
const advancedDiagnostics = document.getElementById('advancedDiagnostics');
const lastChecked = document.getElementById('lastChecked');

const fields = {
  appPort: document.getElementById('appPortInput'),
  dbHost: document.getElementById('dbHostInput'),
  dbPort: document.getElementById('dbPortInput'),
  dbName: document.getElementById('dbNameInput'),
  dbUser: document.getElementById('dbUserInput'),
  dbPassword: document.getElementById('dbPasswordInput')
};

const summaries = {
  profile: document.getElementById('profileSummary'),
  env: document.getElementById('envSummary'),
  storage: document.getElementById('storageSummary'),
  db: document.getElementById('dbSummary'),
  health: document.getElementById('healthSummary')
};

const buttons = {
  createProfile: document.getElementById('createProfileButton'),
  selectStorage: document.getElementById('selectStorageButton'),
  saveProfile: document.getElementById('saveProfileButton'),
  testDb: document.getElementById('testDbButton'),
  preflight: document.getElementById('preflightButton'),
  start: document.getElementById('startButton'),
  openBrowser: document.getElementById('openBrowserButton'),
  stop: document.getElementById('stopButton'),
  restart: document.getElementById('restartButton'),
  copyDiagnostics: document.getElementById('copyDiagnosticsButton')
};

let latestPayload = null;
let initialFieldValues = {};

const stateLabels = {
  no_profile: 'No Production Profile',
  profile_incomplete: 'Profile Incomplete',
  ready: 'Ready',
  passed: 'Ready',
  blocked: 'Blocked',
  starting: 'Starting',
  running: 'Running',
  unhealthy: 'Unhealthy',
  stopped: 'Stopped',
  error: 'Error',
  opened: 'Running',
  discovered: 'Profile Incomplete',
  cancelled: 'Profile Incomplete'
};

function stateClass(label) {
  if (label === 'Ready' || label === 'Running') return 'state state-green';
  if (label === 'Blocked' || label === 'Error') return 'state state-red';
  if (label === 'Profile Incomplete' || label === 'No Production Profile' || label === 'Starting' || label === 'Unhealthy') {
    return 'state state-yellow';
  }
  return 'state state-gray';
}

function setBusy(isBusy) {
  Object.values(buttons).forEach((button) => {
    button.disabled = isBusy;
  });
}

function deriveState(payload) {
  const data = payload && payload.data ? payload.data : {};
  if (payload && payload.status === 'error') return 'Error';
  if (payload && payload.status === 'no_profile') return 'No Production Profile';
  if (data.profile && data.profile.exists === false) return 'No Production Profile';
  if (payload && (payload.status === 'profile_incomplete' || payload.status === 'discovered' || payload.status === 'cancelled')) {
    return 'Profile Incomplete';
  }
  if (payload && payload.status === 'running') return 'Running';
  if (payload && payload.status === 'unhealthy') return 'Unhealthy';
  if (payload && payload.status === 'stopped') return 'Stopped';
  if (payload && payload.status === 'blocked') return 'Blocked';
  return stateLabels[payload && payload.status] || 'Not Checked';
}

function updateSummary(payload) {
  const data = payload.data || {};
  const profile = data.profile || {};
  summaries.profile.textContent = profile.exists === false ? 'Missing' : (profile.profile_id || 'production-default');
  summaries.env.textContent = profile.env || data.env || 'production';
  summaries.storage.textContent = profile.storage_root_status || data.storage_root_status || 'Not checked';
  summaries.db.textContent = profile.db && profile.db.name ? profile.db.name : (data.db_name || 'Not checked');
  summaries.health.textContent = data.health_ok === true ? 'OK' : (payload.status === 'unhealthy' ? 'Unhealthy' : 'Not OK');

  if (profile.app_port && !fields.appPort.value) fields.appPort.value = String(profile.app_port);
  if (profile.db) {
    if (profile.db.port && !fields.dbPort.value) fields.dbPort.value = String(profile.db.port);
    if (profile.db.name && !fields.dbName.value) fields.dbName.value = String(profile.db.name);
    if (profile.db.user && !fields.dbUser.value) fields.dbUser.value = String(profile.db.user);
  }
}

function renderChecklist(items) {
  const grouped = new Map();
  for (const item of items || []) {
    const group = item.group || 'Startup Policy';
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(item);
  }
  checklistEl.innerHTML = '';
  if (!grouped.size) {
    checklistEl.innerHTML = '<div class="check-item not-checked"><span class="dot"></span><div><div class="check-label">No checks yet</div><div class="check-message">Run profile status or preflight.</div></div></div>';
    return;
  }
  for (const [group, groupItems] of grouped.entries()) {
    const section = document.createElement('section');
    section.className = 'group';
    const title = document.createElement('div');
    title.className = 'group-title';
    title.textContent = group;
    section.appendChild(title);
    for (const item of groupItems) {
      const row = document.createElement('div');
      row.className = `check-item ${item.status || 'not-checked'}`;
      const dot = document.createElement('span');
      dot.className = 'dot';
      const body = document.createElement('div');
      const label = document.createElement('div');
      label.className = 'check-label';
      label.textContent = item.label || item.gate || 'Check';
      const message = document.createElement('div');
      message.className = 'check-message';
      message.textContent = item.message || '';
      body.appendChild(label);
      body.appendChild(message);
      row.appendChild(dot);
      row.appendChild(body);
      section.appendChild(row);
    }
    checklistEl.appendChild(section);
  }
}

function applyPayload(payload) {
  latestPayload = payload;
  const label = deriveState(payload);
  stateBadge.textContent = label;
  stateBadge.className = stateClass(label);
  primaryMessage.textContent = payload.message || 'No message returned.';
  updateSummary(payload);
  renderChecklist((payload.data && payload.data.checklist) || []);
  advancedDiagnostics.textContent = JSON.stringify(payload, null, 2);
  lastChecked.textContent = new Date().toLocaleTimeString();
}

async function run(command, extraArgs = []) {
  setBusy(true);
  try {
    const payload = await window.violetLauncher.run(command, extraArgs);
    applyPayload(payload);
    return payload;
  } finally {
    setBusy(false);
  }
}

function currentFieldValues() {
  return {
    appPort: fields.appPort.value,
    dbHost: fields.dbHost.value,
    dbPort: fields.dbPort.value,
    dbName: fields.dbName.value,
    dbUser: fields.dbUser.value,
    dbPassword: fields.dbPassword.value
  };
}

function rememberInitialFields() {
  initialFieldValues = currentFieldValues();
}

function formPayload() {
  const current = currentFieldValues();
  const payload = {};
  for (const [key, value] of Object.entries(current)) {
    const trimmed = String(value || '').trim();
    if (trimmed && trimmed !== String(initialFieldValues[key] || '').trim()) {
      payload[key] = trimmed;
    }
  }
  return payload;
}

buttons.createProfile.addEventListener('click', () => run('profile-init'));
buttons.selectStorage.addEventListener('click', async () => {
  setBusy(true);
  try {
    applyPayload(await window.violetLauncher.selectStorageRoot());
  } finally {
    setBusy(false);
  }
});
buttons.saveProfile.addEventListener('click', async () => {
  setBusy(true);
  try {
    const payload = await window.violetLauncher.saveProfile(formPayload());
    applyPayload(payload);
    if (payload.ok) {
      fields.dbPassword.value = '';
      rememberInitialFields();
    }
  } finally {
    setBusy(false);
  }
});
buttons.testDb.addEventListener('click', () => run('test-db'));
buttons.preflight.addEventListener('click', () => run('preflight'));
buttons.start.addEventListener('click', async () => {
  applyPayload({ ok: false, status: 'starting', message: 'Starting production...', data: (latestPayload && latestPayload.data) || {} });
  await run('start');
});
buttons.openBrowser.addEventListener('click', () => run('open-browser'));
buttons.stop.addEventListener('click', () => run('stop'));
buttons.restart.addEventListener('click', () => run('restart'));
buttons.copyDiagnostics.addEventListener('click', async () => {
  setBusy(true);
  try {
    applyPayload(await window.violetLauncher.copyDiagnostics());
    primaryMessage.textContent = 'Public-safe diagnostic summary copied.';
  } finally {
    setBusy(false);
  }
});

async function boot() {
  const discovered = await run('profile-discover');
  const data = discovered.data || {};
  const inferred = data.safe_inferred_fields || {};
  fields.appPort.value = inferred.app_port || '';
  fields.dbHost.value = inferred.db_host || '';
  fields.dbPort.value = inferred.db_port || '';
  fields.dbName.value = inferred.db_name || '';
  fields.dbUser.value = inferred.db_user || '';
  rememberInitialFields();
  const profilePayload = await run('profile-status');
  rememberInitialFields();
  if (profilePayload.status === 'no_profile' || profilePayload.status === 'profile_incomplete') {
    return;
  }
  await run('status');
}

boot();
