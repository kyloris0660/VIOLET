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

console.log('renderer behavior tests passed');
