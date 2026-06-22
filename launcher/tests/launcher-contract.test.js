const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const main = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
const controllerRunner = fs.readFileSync(path.join(root, 'controller-runner.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'renderer', 'index.html'), 'utf8');
const renderer = fs.readFileSync(path.join(root, 'renderer', 'renderer.js'), 'utf8');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));

assert.strictEqual(pkg.scripts.start, 'electron .');
assert(pkg.scripts.test.includes('controller-runner.test.js'));
assert(pkg.scripts.test.includes('renderer-behavior.test.js'));
assert.strictEqual(pkg.scripts.lint, 'node tests/lint.js');

for (const command of [
  'profile-status',
  'profile-discover',
  'profile-init',
  'profile-update',
  'preflight',
  'test-db',
  'start',
  'stop',
  'restart',
  'diagnostic-summary'
]) {
  assert(controllerRunner.includes(`'${command}'`), `controller-runner.js must allow ${command}`);
}

assert(main.includes('violet_production_control.py'), 'Electron must call the Python control plane.');
assert(controllerRunner.includes('--profile'), 'Electron controller calls must pass a production profile.');
assert(main.includes('--stdin-json'), 'Electron profile save must use stdin JSON.');
assert(!main.includes('--db-password'), 'Electron must not pass DB password on argv.');
assert(!main.includes('VIOLET_STORAGE_ROOT='), 'Electron must not construct production env itself.');
assert(html.includes('id="checklist"'), 'Main screen must include a checklist container.');
assert(html.includes('id="advancedPanel"'), 'Advanced diagnostics must be present.');
assert(!html.includes('<details id="advancedPanel" class="advanced" open'), 'Advanced diagnostics must be collapsed by default.');
assert(renderer.includes('renderChecklist'), 'Renderer must render human-readable checklist items.');
assert(renderer.includes('JSON.stringify(payload, null, 2)'), 'Raw JSON must be limited to advanced diagnostics.');

console.log('launcher contract checks passed');
