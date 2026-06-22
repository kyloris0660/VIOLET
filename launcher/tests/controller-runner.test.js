const assert = require('assert');
const { EventEmitter } = require('events');
const path = require('path');
const {
  controllerArgs,
  normalizeControllerOutput,
  profileUpdatePayload,
  runController,
  sanitizeControllerStderr
} = require('../controller-runner');

const repoRoot = 'C:\\Users\\kyloris\\.codex\\worktrees\\2d4a\\AnimeLocalBooru';
const controllerScript = path.join(repoRoot, 'scripts', 'violet_production_control.py');

function fakeChild({ stdout = '', stderr = '', code = 0 } = {}) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.stdin = {
    written: '',
    write(chunk) {
      this.written += chunk;
    },
    end() {}
  };
  process.nextTick(() => {
    if (stdout) child.stdout.emit('data', Buffer.from(stdout));
    if (stderr) child.stderr.emit('data', Buffer.from(stderr));
    child.emit('close', code);
  });
  return child;
}

async function main() {
  const payload = profileUpdatePayload({ dbPassword: 'secret-db-password', appPort: '8123' });
  const args = controllerArgs(controllerScript, 'profile-update', ['--stdin-json']);
  assert.deepStrictEqual(args.filter((item) => item === '--db-password'), []);
  assert(args.includes('--stdin-json'));
  assert.strictEqual(payload.db.password, 'secret-db-password');
  assert.strictEqual(payload.app_port, '8123');

  const emptyPayload = normalizeControllerOutput('', 'Traceback at C:\\Users\\kyloris\\.codex\\worktrees\\2d4a\\AnimeLocalBooru\\.local_manifests\\production_launcher\\production-profile.json token=sk-secret-token', 1, { repoRoot });
  const emptySerialized = JSON.stringify(emptyPayload);
  assert.strictEqual(emptyPayload.status, 'error');
  assert(emptyPayload.errors.includes('controller_empty_stdout'));
  assert(!emptySerialized.includes('production-profile.json'));
  assert(!emptySerialized.includes('sk-secret-token'));
  assert(!emptySerialized.includes(repoRoot));

  const redacted = sanitizeControllerStderr(`password=hunter2 Bearer ghp_123456789abcdef ${repoRoot}\\scripts\\violet_production_control.py`, { repoRoot });
  assert(!redacted.includes('hunter2'));
  assert(!redacted.includes('ghp_123456789abcdef'));
  assert(!redacted.includes(repoRoot));

  const spawnFailure = await runController({
    command: 'profile-status',
    spawnImpl() {
      throw new Error('ENOENT C:\\private\\missing-python.exe token=sk-dev-secret');
    },
    python: 'C:\\private\\missing-python.exe',
    controllerScript,
    repoRoot
  });
  let serialized = JSON.stringify(spawnFailure);
  assert.strictEqual(spawnFailure.status, 'error');
  assert(spawnFailure.errors.includes('controller_spawn_failed'));
  assert(!serialized.includes('sk-dev-secret'));
  assert(!serialized.includes('C:\\private\\missing-python.exe'));

  const crashPayload = await runController({
    command: 'profile-status',
    spawnImpl() {
      return fakeChild({
        stdout: '',
        stderr: `Traceback from ${repoRoot}\\scripts\\violet_production_control.py api_key=secret-value`,
        code: 1
      });
    },
    python: 'python',
    controllerScript,
    repoRoot
  });
  serialized = JSON.stringify(crashPayload);
  assert.strictEqual(crashPayload.status, 'error');
  assert(crashPayload.errors.includes('controller_empty_stdout'));
  assert(!serialized.includes('secret-value'));
  assert(!serialized.includes(repoRoot));

  console.log('controller runner tests passed');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
