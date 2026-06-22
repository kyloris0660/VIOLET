const { app, BrowserWindow, clipboard, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const controllerScript = path.join(repoRoot, 'scripts', 'violet_production_control.py');
const profileId = 'production-default';

const allowedCommands = new Set([
  'profile-status',
  'profile-discover',
  'profile-init',
  'profile-update',
  'preflight',
  'test-db',
  'status',
  'start',
  'stop',
  'restart',
  'open-browser',
  'diagnostic-summary'
]);

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

function controllerArgs(command, extraArgs = []) {
  return [
    controllerScript,
    command,
    '--profile',
    profileId,
    '--json',
    ...extraArgs.filter((value) => value !== undefined && value !== null)
  ];
}

function runController(command, extraArgs = []) {
  if (!allowedCommands.has(command)) {
    return Promise.reject(new Error(`Unsupported launcher command: ${command}`));
  }
  const python = resolvePython();
  const child = spawn(python, controllerArgs(command, extraArgs), {
    cwd: repoRoot,
    windowsHide: true,
    env: { ...process.env }
  });

  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => {
    stdout += chunk.toString();
  });
  child.stderr.on('data', (chunk) => {
    stderr += chunk.toString();
  });

  return new Promise((resolve) => {
    child.on('close', (code) => {
      let payload;
      try {
        payload = JSON.parse(stdout || '{}');
      } catch (error) {
        payload = {
          ok: false,
          status: 'error',
          message: 'Controller returned non-JSON output.',
          errors: ['controller_non_json_output'],
          data: {}
        };
      }
      payload.exitCode = code;
      if (stderr.trim()) {
        payload.controllerError = stderr.trim().slice(0, 500);
      }
      resolve(payload);
    });
  });
}

function profileUpdateArgs(form) {
  const args = [];
  const pairs = [
    ['--app-port', form.appPort],
    ['--db-host', form.dbHost],
    ['--db-port', form.dbPort],
    ['--db-name', form.dbName],
    ['--db-user', form.dbUser],
    ['--db-password', form.dbPassword]
  ];
  for (const [flag, value] of pairs) {
    if (value !== undefined && String(value).trim() !== '') {
      args.push(flag, String(value).trim());
    }
  }
  return args;
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
  ipcMain.handle('launcher:save-profile', (_event, form) => runController('profile-update', profileUpdateArgs(form || {})));
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
