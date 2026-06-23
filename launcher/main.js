const { app, BrowserWindow, clipboard, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  allowedCommands,
  profileUpdatePayload,
  runController: runPythonController
} = require('./controller-runner');

function looksLikeRepoRoot(candidate) {
  return fs.existsSync(path.join(candidate, 'run.py')) &&
    fs.existsSync(path.join(candidate, 'scripts', 'violet_production_control.py'));
}

function uniquePaths(candidates) {
  const seen = new Set();
  return candidates
    .filter(Boolean)
    .map((candidate) => path.resolve(candidate))
    .filter((candidate) => {
      const key = candidate.toLowerCase();
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function runtimeConfigCandidates() {
  const portableDir = process.env.PORTABLE_EXECUTABLE_DIR;
  const portableFileDir = process.env.PORTABLE_EXECUTABLE_FILE
    ? path.dirname(process.env.PORTABLE_EXECUTABLE_FILE)
    : '';
  const defaultCanonicalRoot = path.join(os.homedir(), 'Documents', 'AnimeLocalBooru');
  return uniquePaths([
    process.env.VIOLET_LAUNCHER_RUNTIME,
    portableFileDir ? path.join(portableFileDir, '.local_manifests', 'production_launcher', 'launcher-runtime.json') : '',
    portableDir ? path.join(portableDir, '..', '..', '.local_manifests', 'production_launcher', 'launcher-runtime.json') : '',
    portableDir ? path.join(portableDir, '..', '.local_manifests', 'production_launcher', 'launcher-runtime.json') : '',
    path.join(defaultCanonicalRoot, '.local_manifests', 'production_launcher', 'launcher-runtime.json'),
    path.join(process.cwd(), '.local_manifests', 'production_launcher', 'launcher-runtime.json'),
    path.join(__dirname, '..', '..', '.local_manifests', 'production_launcher', 'launcher-runtime.json'),
    path.join(path.dirname(process.execPath), '..', '..', '.local_manifests', 'production_launcher', 'launcher-runtime.json')
  ]);
}

function loadRuntimeConfig() {
  for (const candidate of runtimeConfigCandidates()) {
    try {
      if (!fs.existsSync(candidate)) {
        continue;
      }
      const parsed = JSON.parse(fs.readFileSync(candidate, 'utf8'));
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed;
      }
    } catch (_error) {
      // Ignore malformed local runtime config and continue with repo-root discovery.
    }
  }
  return {};
}

const runtimeConfig = loadRuntimeConfig();

function resolveRepoRoot() {
  const portableDir = process.env.PORTABLE_EXECUTABLE_DIR;
  const portableFileDir = process.env.PORTABLE_EXECUTABLE_FILE
    ? path.dirname(process.env.PORTABLE_EXECUTABLE_FILE)
    : '';
  const defaultCanonicalRoot = path.join(os.homedir(), 'Documents', 'AnimeLocalBooru');
  const candidates = uniquePaths([
    runtimeConfig.repo_root || runtimeConfig.repoRoot,
    process.env.VIOLET_REPO_ROOT,
    portableFileDir,
    defaultCanonicalRoot,
    portableDir ? path.join(portableDir, '..', '..') : '',
    portableDir ? path.join(portableDir, '..') : '',
    process.cwd(),
    path.resolve(__dirname, '..'),
    path.resolve(__dirname, '..', '..'),
    path.resolve(path.dirname(process.execPath), '..'),
    path.resolve(path.dirname(process.execPath), '..', '..'),
    path.resolve(path.dirname(process.execPath), '..', '..', '..')
  ]);
  for (const candidate of candidates) {
    if (looksLikeRepoRoot(candidate)) {
      return candidate;
    }
  }
  return path.resolve(__dirname, '..');
}

const repoRoot = resolveRepoRoot();
const runtimeController = runtimeConfig.controller || runtimeConfig.controller_script || runtimeConfig.controllerScript;
const controllerScript = runtimeController && fs.existsSync(runtimeController)
  ? path.resolve(runtimeController)
  : path.join(repoRoot, 'scripts', 'violet_production_control.py');
const profileId = runtimeConfig.profile || runtimeConfig.profile_id || runtimeConfig.profileId || 'production-default';
const appIcon = path.join(__dirname, 'assets', 'violet.ico');

function resolvePython() {
  const candidates = [
    runtimeConfig.python,
    path.join(repoRoot, 'venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'venv', 'bin', 'python'),
    path.join(repoRoot, '.venv', 'bin', 'python')
  ].filter(Boolean);
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  return found || 'python';
}

function runController(command, extraArgs = [], options = {}) {
  if (!allowedCommands.has(command)) {
    return Promise.reject(new Error(`Unsupported launcher command: ${command}`));
  }
  return runPythonController({
    command,
    extraArgs,
    stdinJson: options.stdinJson,
    spawnImpl: spawn,
    python: resolvePython(),
    controllerScript,
    repoRoot,
    profileId,
    env: process.env
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1120,
    height: 780,
    minWidth: 920,
    minHeight: 640,
    title: 'V.I.O.L.E.T. 生产启动器',
    icon: appIcon,
    backgroundColor: '#f7f8fb',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(() => {
  app.setAppUserModelId('local.violet.production-launcher');
  ipcMain.handle('launcher:run', (_event, command, extraArgs = []) => runController(command, extraArgs));
  ipcMain.handle('launcher:save-profile', (_event, form) => runController('profile-update', ['--stdin-json'], { stdinJson: profileUpdatePayload(form || {}) }));
  ipcMain.handle('launcher:select-storage-root', async () => {
    const result = await dialog.showOpenDialog({
      title: '选择生产存储根目录',
      properties: ['openDirectory']
    });
    if (result.canceled || !result.filePaths.length) {
      return { ok: false, status: 'cancelled', message: '已取消选择存储根目录。', data: {} };
    }
    return runController('profile-update', ['--storage-root', result.filePaths[0]]);
  });
  ipcMain.handle('launcher:copy-diagnostics', async () => {
    const payload = await runController('diagnostic-summary');
    clipboard.writeText(JSON.stringify(payload, null, 2));
    return payload;
  });
  ipcMain.handle('launcher:app-info', () => ({
    repoRootName: path.basename(repoRoot),
    profileId,
    controllerScript: path.join('scripts', 'violet_production_control.py')
  }));
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
