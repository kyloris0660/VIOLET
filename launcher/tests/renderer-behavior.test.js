const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const renderer = fs.readFileSync(path.join(root, 'renderer', 'renderer.js'), 'utf8');

const profileMissingIndex = renderer.indexOf("data.profile && data.profile.exists === false");
const stoppedIndex = renderer.indexOf("payload && payload.status === 'stopped'");
assert(profileMissingIndex !== -1, 'Renderer must check missing profile state.');
assert(stoppedIndex !== -1, 'Renderer must handle stopped status.');
assert(profileMissingIndex < stoppedIndex, 'Missing profile must take precedence over generic Stopped.');

assert(renderer.includes("profilePayload.status === 'no_profile'"), 'Boot must keep No Production Profile visible.');
assert(renderer.includes("profilePayload.status === 'profile_incomplete'"), 'Boot must keep Profile Incomplete visible.');
assert(renderer.includes("profilePayload.status === 'profile_error'"), 'Boot must keep Profile Error visible.');
assert(renderer.includes("run('profile-repair')"), 'Create / Repair button must invoke profile repair.');
assert(!renderer.includes("db_user_configured ? 'postgres' : ''"), 'Renderer must not default custom DB users to postgres.');
assert(renderer.includes('inferred.db_user'), 'Renderer must populate DB user from public-safe profile discovery.');
assert(renderer.includes('initialFieldValues'), 'Renderer must track initial profile form values.');
assert(renderer.includes("trimmed !== String(initialFieldValues[key]"), 'Renderer must send only changed non-empty profile fields.');
const discoverIndex = renderer.indexOf("const discovered = await run('profile-discover')");
const statusIndex = renderer.indexOf("const profilePayload = await run('profile-status')");
const firstRememberAfterDiscover = renderer.indexOf('rememberInitialFields();', discoverIndex);
assert(discoverIndex !== -1 && statusIndex !== -1, 'Boot must discover then check profile status.');
assert(
  firstRememberAfterDiscover === -1 || firstRememberAfterDiscover > statusIndex,
  'Inferred no-profile fields must not be marked saved before a profile file exists.'
);
assert(renderer.includes('clearDbPassword'), 'Renderer must expose an explicit DB password clear control.');
assert(renderer.includes("payload.dbPassword = ''"), 'Renderer must send an explicit empty DB password only when clearing is requested.');
assert(renderer.includes("return '不健康'"), 'Renderer must map unhealthy status to zh-CN Unhealthy.');
assert(renderer.includes('const diagnosticPayload = await window.violetLauncher.copyDiagnostics()'), 'Copy diagnostics must capture diagnostics separately.');
assert(renderer.includes('advancedDiagnostics.textContent = JSON.stringify(diagnosticPayload, null, 2)'), 'Copy diagnostics must update advanced diagnostics only.');
assert(!renderer.includes('applyPayload(await window.violetLauncher.copyDiagnostics())'), 'Copy diagnostics must not replace the current launcher state.');
assert(renderer.includes("const browserPayload = await window.violetLauncher.run('open-browser')"), 'Open browser must capture its minimal payload separately.');
assert(renderer.includes('已打开浏览器，当前生产状态保持不变。'), 'Open browser success must preserve the current status view.');
assert(!renderer.includes("buttons.openBrowser.addEventListener('click', () => run('open-browser'))"), 'Open browser must not replace the current launcher state on success.');

console.log('renderer behavior tests passed');
