const { app, BrowserWindow, clipboard, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
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

function resolveRepoRoot() {
  const candidates = [
    process.env.VIOLET_REPO_ROOT,
    process.cwd(),
    path.resolve(__dirname, '..'),
    path.resolve(__dirname, '..', '..'),
    path.resolve(path.dirname(process.execPath), '..'),
    path.resolve(path.dirname(process.execPath), '..', '..'),
    path.resolve(path.dirname(process.execPath), '..', '..', '..')
  ].filter(Boolean);
  for (const candidate of candidates) {
    const resolved = path.resolve(candidate);
    if (looksLikeRepoRoot(resolved)) {
      return resolved;
    }
  }
  return path.resolve(__dirname, '..');
}

const repoRoot = resolveRepoRoot();
const controllerScript = path.join(repoRoot, 'scripts', 'violet_production_control.py');
const profileId = 'production-default';

function resolvePython() {
  const candidates = [
    path.join(repoRoot, 'venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'venv', 'bin', 'python'),
    path.join(repoRoot, '.venv', 'bin', 'python')
  ];
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
    title: 'V.I.O.L.E.T. Production Launcher',
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
  ipcMain.handle('launcher:run', (_event, command, extraArgs = []) => runController(command, extraArgs));
  ipcMain.handle('launcher:save-profile', (_event, form) => runController('profile-update', ['--stdin-json'], { stdinJson: profileUpdatePayload(form || {}) }));
  ipcMain.handle('launcher:select-storage-root', async () => {
    const result = await dialog.showOpenDialog({
      title: 'Select Production Storage Root',
      properties: ['openDirectory']
    });
    if (result.canceled || !result.filePaths.length) {
      return { ok: false, status: 'cancelled', message: 'Storage root selection cancelled.', data: {} };
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
