const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const repo = path.resolve(root, '..');
const main = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
const controllerRunner = fs.readFileSync(path.join(root, 'controller-runner.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'renderer', 'index.html'), 'utf8');
const renderer = fs.readFileSync(path.join(root, 'renderer', 'renderer.js'), 'utf8');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const gitignore = fs.readFileSync(path.join(repo, '.gitignore'), 'utf8');

assert.strictEqual(pkg.scripts.start, 'electron .');
assert(pkg.scripts.test.includes('controller-runner.test.js'));
assert(pkg.scripts.test.includes('renderer-behavior.test.js'));
assert.strictEqual(pkg.scripts.lint, 'node tests/lint.js');
assert(pkg.scripts.package.includes('electron-builder --win portable'));
assert(pkg.scripts.package.includes('install_production_launcher_root_entry.ps1'));
assert.strictEqual(pkg.build.productName, 'V.I.O.L.E.T. Production Launcher');
assert.strictEqual(pkg.build.win.icon, 'assets/violet.ico');
assert(pkg.build.files.includes('assets/**/*'));
assert(fs.existsSync(path.join(root, 'assets', 'violet.ico')));
assert(fs.existsSync(path.join(repo, 'scripts', 'setup_launcher_npm_proxy.ps1')));
assert(fs.existsSync(path.join(repo, 'scripts', 'install_production_launcher_root_entry.ps1')));
assert(gitignore.includes('launcher/dist/'));
assert(gitignore.includes('launcher/out/'));
assert(gitignore.includes('launcher/.npmrc'));
assert(gitignore.includes('/V.I.O.L.E.T. Production Launcher.exe'));

for (const command of [
  'profile-status',
  'profile-discover',
  'profile-init',
  'profile-repair',
  'profile-update',
  'preflight',
  'test-db',
  'start',
  'stop',
  'restart',
  'open-browser',
  'diagnostic-summary'
]) {
  assert(controllerRunner.includes(`'${command}'`), `controller-runner.js must allow ${command}`);
}

assert(main.includes('violet_production_control.py'), 'Electron must call the Python control plane.');
assert(controllerRunner.includes('--profile'), 'Electron controller calls must pass a production profile.');
assert(main.includes('--stdin-json'), 'Electron profile save must use stdin JSON.');
assert(!main.includes('--db-password'), 'Electron must not pass DB password on argv.');
assert(!main.includes('VIOLET_STORAGE_ROOT='), 'Electron must not construct production env itself.');
assert(main.includes('launcher-runtime.json'), 'Packaged launcher must read local ignored runtime config.');
assert(main.includes('VIOLET_LAUNCHER_RUNTIME'), 'Packaged launcher must support an explicit runtime config path.');
assert(main.includes('PORTABLE_EXECUTABLE_DIR'), 'Packaged launcher must resolve repo root from electron-builder portable location.');
assert(main.includes('runtimeConfig.repo_root'), 'Runtime config must be able to pin canonical repo root.');
assert(main.includes('runtimeConfig.python'), 'Runtime config must be able to pin canonical Python.');
assert(main.includes("assets', 'violet.ico'"), 'BrowserWindow must use the V.I.O.L.E.T. icon.');
assert(main.includes('setAppUserModelId'), 'Windows taskbar identity must be set for the launcher.');
assert(html.includes('id="checklist"'), 'Main screen must include a checklist container.');
assert(html.includes('id="detailPanelTitle"'), 'Runtime/checklist panel title must be updateable.');
assert(html.includes('id="advancedPanel"'), 'Advanced diagnostics must be present.');
assert(!html.includes('<details id="advancedPanel" class="advanced" open'), 'Advanced diagnostics must be collapsed by default.');
assert(html.includes('V.I.O.L.E.T. 生产启动器'), 'Launcher visible title must be zh-CN first.');
assert(html.includes('创建 / 修复生产配置'), 'Create/repair button must be zh-CN first.');
assert(html.includes('选择生产存储根目录'), 'Storage selection button must be zh-CN first.');
assert(html.includes('运行启动前检查'), 'Preflight button must be zh-CN first.');
assert(html.includes('清除已保存 DB 访问值'), 'DB password clearing must be an explicit zh-CN control.');
assert(html.includes('显示高级诊断'), 'Advanced diagnostics label must be zh-CN first.');
assert(renderer.includes('renderChecklist'), 'Renderer must render human-readable checklist items.');
assert(renderer.includes('JSON.stringify(payload, null, 2)'), 'Raw JSON must be limited to advanced diagnostics.');

console.log('launcher contract checks passed');
