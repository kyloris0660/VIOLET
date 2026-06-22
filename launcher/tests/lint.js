const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const files = [
  'main.js',
  'preload.js',
  path.join('renderer', 'renderer.js'),
  path.join('tests', 'launcher-contract.test.js'),
  path.join('tests', 'lint.js')
];

for (const file of files) {
  const absolute = path.join(root, file);
  const source = fs.readFileSync(absolute, 'utf8');
  if (/\t/.test(source)) {
    throw new Error(`${file} contains tabs`);
  }
  const trailing = source.split(/\r?\n/).findIndex((line) => /\s+$/.test(line));
  if (trailing !== -1) {
    throw new Error(`${file} contains trailing whitespace on line ${trailing + 1}`);
  }
  const checked = spawnSync(process.execPath, ['--check', absolute], { encoding: 'utf8' });
  if (checked.status !== 0) {
    process.stderr.write(checked.stderr);
    process.stderr.write(checked.stdout);
    process.exit(checked.status);
  }
}

console.log('launcher lint checks passed');
