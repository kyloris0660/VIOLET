const os = require('os');
const path = require('path');

const DEFAULT_PROFILE_ID = 'production-default';
const MAX_CONTROLLER_ERROR_LENGTH = 800;

const allowedCommands = new Set([
  'profile-status',
  'profile-discover',
  'profile-init',
  'profile-repair',
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

function controllerArgs(controllerScript, command, extraArgs = [], profileId = DEFAULT_PROFILE_ID) {
  return [
    controllerScript,
    command,
    '--profile',
    profileId,
    '--json',
    ...extraArgs.filter((value) => value !== undefined && value !== null)
  ];
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function replaceBothPathForms(text, rawPath, replacement) {
  if (!rawPath) return text;
  const forms = new Set([String(rawPath), String(rawPath).replace(/\\/g, '/')]);
  let result = text;
  for (const form of forms) {
    if (form) {
      result = result.replace(new RegExp(escapeRegExp(form), 'gi'), replacement);
    }
  }
  return result;
}

function sanitizeControllerStderr(stderr, { repoRoot = '' } = {}) {
  let text = String(stderr || '').trim();
  if (!text) return '';
  const home = os.homedir();
  const redactionRoots = [
    repoRoot ? path.join(repoRoot, '.local_manifests', 'production_launcher', 'production-profile.json') : '',
    repoRoot ? path.join(repoRoot, '.local_manifests', 'production_launcher') : '',
    repoRoot,
    home
  ].sort((left, right) => String(right).length - String(left).length);
  for (const root of redactionRoots) {
    text = replaceBothPathForms(text, root, root === home ? '[home]' : '[repo-local]');
  }
  text = text.replace(/\[repo-local\][\\/]+\.local_manifests[\\/]+production_launcher(?:[\\/]+production-profile\.json)?/gi, '[repo-local]');
  text = text.replace(/(^|[^\w])\.local_manifests[\\/]+production_launcher(?:[\\/]+production-profile\.json)?/gi, '$1[profile-path]');
  text = text.replace(/(^|[^A-Za-z])([A-Za-z]:[\\/][^\s'"`<>|]+)/g, '$1[path]');
  text = text.replace(/\/(?:Users|home)\/[^\s'"`<>|]+/g, '[path]');
  text = text.replace(/\b(Bearer)\s+[A-Za-z0-9._~+/=-]+/gi, '$1 [redacted]');
  text = text.replace(/\b(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*[^\s'"`]+/gi, '$1=[redacted]');
  text = text.replace(/\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})\b/g, '[token]');
  if (text.length > MAX_CONTROLLER_ERROR_LENGTH) {
    text = `${text.slice(0, MAX_CONTROLLER_ERROR_LENGTH)}...`;
  }
  return text;
}

function controllerErrorPayload(code, message, { exitCode = null, stderr = '', repoRoot = '' } = {}) {
  const payload = {
    ok: false,
    status: 'error',
    message,
    errors: [code],
    data: {
      controller_error_code: code
    }
  };
  if (exitCode !== null && exitCode !== undefined) {
    payload.exitCode = exitCode;
  }
  const sanitized = sanitizeControllerStderr(stderr, { repoRoot });
  if (sanitized) {
    payload.controllerError = sanitized;
    payload.data.controller_error = sanitized;
  }
  return payload;
}

function normalizeControllerOutput(stdout, stderr, exitCode, { repoRoot = '' } = {}) {
  const output = String(stdout || '').trim();
  if (!output) {
    return controllerErrorPayload('controller_empty_stdout', 'Controller did not return JSON output.', {
      exitCode,
      stderr,
      repoRoot
    });
  }
  let payload;
  try {
    payload = JSON.parse(output);
  } catch (error) {
    return controllerErrorPayload('controller_non_json_output', 'Controller returned non-JSON output.', {
      exitCode,
      stderr,
      repoRoot
    });
  }
  payload.exitCode = exitCode;
  const sanitized = sanitizeControllerStderr(stderr, { repoRoot });
  if (sanitized) {
    payload.controllerError = sanitized;
    payload.data = payload.data || {};
    payload.data.controller_error = sanitized;
  }
  if (exitCode !== 0 && payload.ok !== false) {
    payload.ok = false;
    payload.status = payload.status || 'error';
    payload.message = payload.message || 'Controller command failed.';
    payload.errors = payload.errors && payload.errors.length ? payload.errors : ['controller_exit_nonzero'];
  }
  return payload;
}

function profileUpdatePayload(form = {}) {
  const payload = {};
  if (form.storageRoot !== undefined) payload.storage_root = form.storageRoot;
  if (form.python !== undefined) payload.python = form.python;
  if (form.repoRoot !== undefined) payload.repo_root = form.repoRoot;
  if (form.appPort !== undefined) payload.app_port = form.appPort;
  const db = {};
  if (form.dbHost !== undefined) db.host = form.dbHost;
  if (form.dbPort !== undefined) db.port = form.dbPort;
  if (form.dbName !== undefined) db.name = form.dbName;
  if (form.dbUser !== undefined) db.user = form.dbUser;
  if (form.dbPassword !== undefined) db.password = form.dbPassword;
  if (Object.keys(db).length) payload.db = db;
  return payload;
}

function runController({
  command,
  extraArgs = [],
  stdinJson,
  spawnImpl,
  python,
  controllerScript,
  repoRoot,
  profileId = DEFAULT_PROFILE_ID,
  env = process.env
}) {
  if (!allowedCommands.has(command)) {
    return Promise.reject(new Error(`Unsupported launcher command: ${command}`));
  }
  const args = controllerArgs(controllerScript, command, extraArgs, profileId);
  let child;
  try {
    child = spawnImpl(python, args, {
      cwd: repoRoot,
      windowsHide: true,
      env: { ...env },
      stdio: ['pipe', 'pipe', 'pipe']
    });
  } catch (error) {
    return Promise.resolve(controllerErrorPayload('controller_spawn_failed', 'Unable to start the Python controller.', {
      stderr: error && error.message ? error.message : String(error),
      repoRoot
    }));
  }

  let stdout = '';
  let stderr = '';
  let settled = false;

  const finish = (payload) => {
    if (!settled) {
      settled = true;
      return payload;
    }
    return null;
  };

  child.stdout.on('data', (chunk) => {
    stdout += chunk.toString();
  });
  child.stderr.on('data', (chunk) => {
    stderr += chunk.toString();
  });

  return new Promise((resolve) => {
    child.on('error', (error) => {
      const payload = finish(controllerErrorPayload('controller_spawn_failed', 'Unable to start the Python controller.', {
        stderr: error && error.message ? error.message : String(error),
        repoRoot
      }));
      if (payload) resolve(payload);
    });
    child.on('close', (code) => {
      const payload = finish(normalizeControllerOutput(stdout, stderr, code, { repoRoot }));
      if (payload) resolve(payload);
    });
    if (stdinJson !== undefined) {
      child.stdin.write(JSON.stringify(stdinJson));
    }
    child.stdin.end();
  });
}

module.exports = {
  allowedCommands,
  controllerArgs,
  controllerErrorPayload,
  normalizeControllerOutput,
  profileUpdatePayload,
  runController,
  sanitizeControllerStderr
};
