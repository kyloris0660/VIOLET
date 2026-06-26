const stateBadge = document.getElementById('stateBadge');
const primaryMessage = document.getElementById('primaryMessage');
const checklistEl = document.getElementById('checklist');
const advancedDiagnostics = document.getElementById('advancedDiagnostics');
const lastChecked = document.getElementById('lastChecked');
const detailPanelTitle = document.getElementById('detailPanelTitle');

const fields = {
  appPort: document.getElementById('appPortInput'),
  dbHost: document.getElementById('dbHostInput'),
  dbPort: document.getElementById('dbPortInput'),
  dbName: document.getElementById('dbNameInput'),
  dbUser: document.getElementById('dbUserInput'),
  dbPassword: document.getElementById('dbPasswordInput'),
  clearDbPassword: document.getElementById('clearDbPasswordInput')
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
  openManualSync: document.getElementById('openManualSyncButton'),
  stop: document.getElementById('stopButton'),
  restart: document.getElementById('restartButton'),
  copyDiagnostics: document.getElementById('copyDiagnosticsButton')
};

let latestPayload = null;
let initialFieldValues = {};
let statusPollTimer = null;

const stateLabels = {
  no_profile: '无生产配置',
  profile_error: '配置错误',
  profile_incomplete: '配置不完整',
  ready: '就绪',
  passed: '就绪',
  blocked: '已阻塞',
  starting: '正在启动',
  running: '运行中',
  unhealthy: '不健康',
  stopped: '已停止',
  error: '错误',
  opened: '运行中',
  discovered: '配置不完整',
  cancelled: '配置不完整'
};

const groupLabels = {
  'Production Profile': '生产配置',
  Environment: '环境',
  Storage: '存储',
  Database: '数据库',
  Schema: '架构',
  Port: '端口',
  'Safety Flags': '安全开关',
  'Startup Policy': '启动策略',
  Health: '健康'
};

const checklistLabels = {
  'Production profile': '生产配置',
  'Profile environment': '配置环境',
  'Profile error': '配置错误',
  'Profile storage': '配置存储',
  'Production environment': '生产环境',
  'Debug disabled': 'Debug 已关闭',
  'Python runtime': 'Python 运行时',
  'Storage root': '存储根目录',
  'Storage safety': '存储安全',
  'Production settings': '生产设置',
  'Settings import safety': '设置导入安全',
  'Database profile': '数据库配置',
  'Database port': '数据库端口',
  'Read-only DB check': '只读数据库检查',
  'Health schema check': '健康架构检查',
  'App port': '应用端口',
  'Port ownership': '端口归属',
  'Destructive E2E': '破坏性 E2E',
  'Real E2E': '真实 E2E',
  'Startup automation': '启动自动化',
  'Auth policy': '认证策略',
  'Write policy': '写入策略',
  'Safe startup mode': '安全启动模式'
};

const valueLabels = {
  Missing: '缺失',
  configured: '已配置',
  missing: '缺失',
  blocked: '已阻塞',
  OK: 'OK',
  Unhealthy: '不健康',
  'Not OK': '未通过',
  'Not checked': '未检查',
  'production-default': 'production-default'
};

const messageLabels = {
  'No production profile exists. Create one before running production preflight.': '尚未创建生产配置。请先创建或修复生产配置，再运行启动前检查。',
  'Production profile has an ID or JSON mismatch. Repair it before preflight.': '生产配置 ID 或 JSON 不匹配。请先修复配置。',
  'Production profile is incomplete.': '生产配置不完整。',
  'Production profile is complete enough for preflight.': '生产配置已足够运行启动前检查。',
  'Production profile discovery completed.': '生产配置发现已完成。',
  'Production profile repaired from local evidence.': '已根据本机记录修复生产配置。',
  'Production preflight passed.': '启动前检查已通过。',
  'Production preflight blocked startup.': '启动前检查阻止了启动。',
  'Production server is running.': '生产服务正在运行。',
  'Production server process is managed, but health is unavailable or failing.': '生产服务进程由启动器管理，但健康检查不可用或失败。',
  'Production server is stopped.': '生产服务已停止。',
  'Existing launcher-managed production process is unhealthy. Start is blocked until health is restored or the process is stopped.': '已有启动器管理的生产进程不健康。请修复健康问题，或先停止该进程后再启动。',
  'Production process started, but identity or health verification failed. The launcher attempted to stop the newly started process.': '生产进程已启动，但身份或健康校验失败。启动器已尝试停止本次新启动的进程。',
  'Production server started but did not return health before timeout. The launcher attempted to stop the newly started process.': '生产服务启动后未在超时前返回健康状态。启动器已尝试停止本次新启动的进程。',
  'Production server started and health check passed.': '生产服务已启动，健康检查通过。',
  'Public-safe diagnostic summary copied.': '已复制公开安全的诊断摘要。',
  'No message returned.': '没有返回消息。',
  'Production profile exists.': '生产配置已存在。',
  'Production profile has no structural or profile id errors.': '生产配置结构和 ID 检查通过。',
  'Production profile path is local and ignored.': '生产配置位于本机 ignored 路径。',
  'Production profile declares env=production.': '生产配置声明 env=production。',
  'Development .env is not used for production. Create or repair the production profile.': '生产启动不使用 development .env。请创建或修复生产配置。',
  'Production profile is missing storage root.': '生产配置缺少存储根目录。',
  'Production storage root is invalid or unsafe.': '生产存储根目录无效或不安全。',
  'Database check is skipped until production profile and storage gates pass.': '数据库检查会等生产配置和存储检查通过后再运行。',
  'Production profile must disable startup automation flags.': '生产配置必须关闭启动自动化开关。',
  'Production profile must explicitly preserve the production auth policy.': '生产配置必须明确保留生产认证策略。',
  'Production profile preserves production auth policy.': '生产配置已保留生产认证策略。',
  '请先停止生产服务，再修改生产配置。': '请先停止生产服务，再修改生产配置。',
  'Target port must be free or verified as launcher-managed.': '目标端口必须空闲，或确认由启动器管理。',
  'APP_PORT must be an integer between 1 and 65535.': 'APP_PORT 必须是 1 到 65535 之间的整数。',
  'Database port must be an integer between 1 and 65535.': '数据库端口必须是 1 到 65535 之间的整数。',
  'Safe startup blocks schema migration, cleanup, import/tagging/sync jobs, and background workers.': '安全启动会阻止架构迁移、清理、导入/打标/同步任务和后台 worker。'
};

function stateClass(label) {
  if (label === '就绪' || label === '运行中') return 'state state-green';
  if (label === '已阻塞' || label === '错误') return 'state state-red';
  if (label === '配置不完整' || label === '无生产配置' || label === '配置错误' || label === '正在启动' || label === '不健康') {
    return 'state state-yellow';
  }
  return 'state state-gray';
}

function localizeValue(value) {
  const text = String(value || '');
  return valueLabels[text] || text || '未检查';
}

function localizeMessage(message) {
  const text = String(message || '');
  return messageLabels[text] || text;
}

function localizeGroup(group) {
  return groupLabels[group] || group || '启动策略';
}

function localizeChecklistLabel(label) {
  return checklistLabels[label] || label || '检查项';
}

function setBusy(isBusy) {
  Object.values(buttons).forEach((button) => {
    button.disabled = isBusy;
  });
}

function deriveState(payload) {
  const data = payload && payload.data ? payload.data : {};
  if (payload && payload.status === 'error') return '错误';
  if (payload && payload.status === 'no_profile') return '无生产配置';
  if (data.profile && data.profile.exists === false) return '无生产配置';
  if (payload && payload.status === 'profile_error') return '配置错误';
  if (payload && (payload.status === 'profile_incomplete' || payload.status === 'discovered' || payload.status === 'cancelled')) {
    return '配置不完整';
  }
  if (payload && payload.status === 'running') return '运行中';
  if (payload && payload.status === 'unhealthy') return '不健康';
  if (payload && payload.status === 'stopped') return '已停止';
  if (payload && payload.status === 'blocked') return '已阻塞';
  return stateLabels[payload && payload.status] || '未检查';
}

function updateSummary(payload) {
  const data = payload.data || {};
  const profile = data.profile || {};
  summaries.profile.textContent = profile.exists === false ? '缺失' : (profile.profile_id || 'production-default');
  summaries.env.textContent = profile.env || data.env || 'production';
  summaries.storage.textContent = localizeValue(profile.storage_root_status || data.storage_root_status || 'Not checked');
  summaries.db.textContent = profile.db && profile.db.name ? profile.db.name : (data.db_name || '未检查');
  summaries.health.textContent = data.health_ok === true ? 'OK' : (payload.status === 'unhealthy' ? '不健康' : '未通过');

  if (profile.app_port && !fields.appPort.value) fields.appPort.value = String(profile.app_port);
  if (profile.db) {
    if (profile.db.port && !fields.dbPort.value) fields.dbPort.value = String(profile.db.port);
    if (profile.db.name && !fields.dbName.value) fields.dbName.value = String(profile.db.name);
    if (profile.db.user && !fields.dbUser.value) fields.dbUser.value = String(profile.db.user);
  }
}

function renderChecklist(items) {
  detailPanelTitle.textContent = '启动前检查';
  const grouped = new Map();
  for (const item of items || []) {
    const group = item.group || 'Startup Policy';
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(item);
  }
  checklistEl.innerHTML = '';
  if (!grouped.size) {
    checklistEl.innerHTML = '<div class="check-item not-checked"><span class="dot"></span><div><div class="check-label">No checks yet</div><div class="check-message">Run profile status or preflight.</div></div></div>';
    checklistEl.querySelector('.check-label').textContent = '尚未检查';
    checklistEl.querySelector('.check-message').textContent = '运行配置状态或启动前检查。';
    return;
  }
  for (const [group, groupItems] of grouped.entries()) {
    const section = document.createElement('section');
    section.className = 'group';
    const title = document.createElement('div');
    title.className = 'group-title';
    title.textContent = localizeGroup(group);
    section.appendChild(title);
    for (const item of groupItems) {
      const row = document.createElement('div');
      row.className = `check-item ${item.status || 'not-checked'}`;
      const dot = document.createElement('span');
      dot.className = 'dot';
      const body = document.createElement('div');
      const label = document.createElement('div');
      label.className = 'check-label';
      label.textContent = localizeChecklistLabel(item.label || item.gate || 'Check');
      const message = document.createElement('div');
      message.className = 'check-message';
      message.textContent = localizeMessage(item.message || '');
      body.appendChild(label);
      body.appendChild(message);
      row.appendChild(dot);
      row.appendChild(body);
      section.appendChild(row);
    }
    checklistEl.appendChild(section);
  }
}

function boolStatus(value, positive = 'OK', negative = '未通过') {
  if (value === true) return positive;
  if (value === false) return negative;
  return '未检查';
}

function formatDuration(startedAt) {
  if (!startedAt) return '未运行';
  const started = new Date(startedAt);
  if (Number.isNaN(started.getTime())) return '未检查';
  const seconds = Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
  if (minutes > 0) return `${minutes} 分钟 ${remainingSeconds} 秒`;
  return `${remainingSeconds} 秒`;
}

function runtimeRows(payload) {
  const data = payload.data || {};
  const running = data.running === true || payload.status === 'running';
  return [
    ['健康', data.health_ok === true ? 'OK' : (payload.status === 'unhealthy' ? '不健康' : '未通过')],
    ['端口', data.port || '未检查'],
    ['PID', running && data.pid ? data.pid : '未运行'],
    ['运行时间', running ? formatDuration(data.started_at) : '未运行'],
    ['数据库', boolStatus(data.db_reachable, 'reachable', 'not reachable')],
    ['Schema', boolStatus(data.schema_compatible, 'compatible', 'not compatible')],
    ['存储', boolStatus(data.storage_configured, 'configured', 'not configured')],
    ['最近一次错误', data.last_error ? localizeMessage(data.last_error) : '无']
  ];
}

function renderRuntimeStatus(payload) {
  detailPanelTitle.textContent = '运行状态';
  checklistEl.innerHTML = '';
  const data = payload.data || {};
  const summary = document.createElement('div');
  summary.className = `runtime-summary ${payload.status === 'running' ? 'runtime-ok' : payload.status === 'unhealthy' ? 'runtime-warning' : 'runtime-stopped'}`;
  const heading = document.createElement('div');
  heading.className = 'runtime-heading';
  heading.textContent = data.running === true || payload.status === 'running'
    ? '生产服务正在运行'
    : payload.status === 'unhealthy'
      ? '生产服务不健康'
      : '生产服务已停止';
  summary.appendChild(heading);

  const grid = document.createElement('dl');
  grid.className = 'runtime-grid';
  for (const [label, value] of runtimeRows(payload)) {
    const term = document.createElement('dt');
    term.textContent = label;
    const desc = document.createElement('dd');
    desc.textContent = String(value);
    grid.appendChild(term);
    grid.appendChild(desc);
  }
  summary.appendChild(grid);
  checklistEl.appendChild(summary);
}

function hasChecklist(payload) {
  return Boolean(payload && payload.data && Array.isArray(payload.data.checklist) && payload.data.checklist.length);
}

function shouldRenderRuntime(payload) {
  if (!payload) return false;
  if (payload.status === 'running' || payload.status === 'unhealthy' || payload.status === 'stopped') return true;
  return Boolean(payload.data && payload.data.running === true);
}

function syncRuntimePolling(payload) {
  const shouldPoll = Boolean(payload && (payload.status === 'running' || payload.status === 'unhealthy' || (payload.data && payload.data.running === true)));
  if (shouldPoll && !statusPollTimer) {
    statusPollTimer = window.setInterval(async () => {
      try {
        const statusPayload = await window.violetLauncher.run('status');
        applyPayload(statusPayload);
      } catch (error) {
        applyPayload({ ok: false, status: 'error', message: error.message || '状态轮询失败。', data: (latestPayload && latestPayload.data) || {} });
      }
    }, 4000);
  }
  if (!shouldPoll && statusPollTimer) {
    window.clearInterval(statusPollTimer);
    statusPollTimer = null;
  }
}

function applyPayload(payload) {
  latestPayload = payload;
  const label = deriveState(payload);
  stateBadge.textContent = label;
  stateBadge.className = stateClass(label);
  primaryMessage.textContent = localizeMessage(payload.message || 'No message returned.');
  updateSummary(payload);
  if (shouldRenderRuntime(payload) && !hasChecklist(payload)) {
    renderRuntimeStatus(payload);
  } else {
    renderChecklist((payload.data && payload.data.checklist) || []);
  }
  advancedDiagnostics.textContent = JSON.stringify(payload, null, 2);
  lastChecked.textContent = new Date().toLocaleTimeString();
  syncRuntimePolling(payload);
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

async function startWithAutomaticPreflight() {
  setBusy(true);
  try {
    const current = latestPayload || {};
    const currentData = current.data || {};
    if (current.status === 'running' || currentData.running === true) {
      const statusPayload = await window.violetLauncher.run('status');
      applyPayload(statusPayload);
      return statusPayload;
    }

    applyPayload({
      ok: false,
      status: 'starting',
      message: '正在进行启动前检查...',
      data: currentData
    });
    const preflightPayload = await window.violetLauncher.run('preflight');
    applyPayload(preflightPayload);
    if (!preflightPayload || preflightPayload.ok !== true) {
      return preflightPayload;
    }

    applyPayload({
      ok: false,
      status: 'starting',
      message: '启动前检查已通过，正在启动生产服务...',
      data: preflightPayload.data || {}
    });
    const startPayload = await window.violetLauncher.run('start');
    applyPayload(startPayload);
    return startPayload;
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
    dbPassword: fields.dbPassword.value,
    clearDbPassword: fields.clearDbPassword.checked
  };
}

function rememberInitialFields() {
  initialFieldValues = currentFieldValues();
}

function formPayload() {
  const current = currentFieldValues();
  const payload = {};
  for (const [key, value] of Object.entries(current)) {
    if (key === 'clearDbPassword') {
      continue;
    }
    if (key === 'dbPassword' && current.clearDbPassword) {
      payload.dbPassword = '';
      continue;
    }
    const trimmed = String(value || '').trim();
    if (trimmed && trimmed !== String(initialFieldValues[key] || '').trim()) {
      payload[key] = trimmed;
    }
  }
  return payload;
}

buttons.createProfile.addEventListener('click', () => run('profile-repair'));
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
      fields.clearDbPassword.checked = false;
      rememberInitialFields();
    }
  } finally {
    setBusy(false);
  }
});
buttons.testDb.addEventListener('click', () => run('test-db'));
buttons.preflight.addEventListener('click', () => run('preflight'));
buttons.start.addEventListener('click', () => startWithAutomaticPreflight());
buttons.openBrowser.addEventListener('click', async () => {
  setBusy(true);
  try {
    const browserPayload = await window.violetLauncher.run('open-browser');
    if (browserPayload && browserPayload.ok === false) {
      applyPayload(browserPayload);
      return;
    }
    advancedDiagnostics.textContent = JSON.stringify(browserPayload, null, 2);
    primaryMessage.textContent = '已打开浏览器，当前生产状态保持不变。';
  } finally {
    setBusy(false);
  }
});
buttons.openManualSync.addEventListener('click', async () => {
  setBusy(true);
  try {
    const manualSyncPayload = await window.violetLauncher.run('open-manual-sync');
    if (manualSyncPayload && manualSyncPayload.ok === false) {
      applyPayload(manualSyncPayload);
      return;
    }
    advancedDiagnostics.textContent = JSON.stringify(manualSyncPayload, null, 2);
    primaryMessage.textContent = '已打开手动同步入口，当前生产状态保持不变。';
  } finally {
    setBusy(false);
  }
});
buttons.stop.addEventListener('click', () => run('stop'));
buttons.restart.addEventListener('click', () => run('restart'));
buttons.copyDiagnostics.addEventListener('click', async () => {
  setBusy(true);
  try {
    const diagnosticPayload = await window.violetLauncher.copyDiagnostics();
    advancedDiagnostics.textContent = JSON.stringify(diagnosticPayload, null, 2);
    primaryMessage.textContent = '已复制公开安全的诊断摘要。';
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
  const profilePayload = await run('profile-status');
  if (profilePayload.status === 'no_profile' || profilePayload.status === 'profile_incomplete' || profilePayload.status === 'profile_error') {
    if (profilePayload.data && profilePayload.data.profile && profilePayload.data.profile.exists) {
      rememberInitialFields();
    }
    return;
  }
  rememberInitialFields();
  await run('status');
}

boot();
