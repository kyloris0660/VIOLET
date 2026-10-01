"""Real Git counterexamples for the bounded post-full source contract."""
import hashlib
import json
import subprocess

import pytest

from scripts import production_pixiv_a2_post_full_fix as contract
from scripts.trusted_git import resolve_trusted_git_executable, trusted_git_environment


def _fixture(tmp_path, monkeypatch):
    repo = tmp_path / 'registered-repo'
    repo.mkdir()
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    def run(*args):
        return subprocess.check_output([str(git.path), '-C', str(repo), *args],
            env=trusted_git_environment(), text=True, encoding='utf-8').strip()
    run('init', '-q'); run('config', 'user.email', 'test@example.invalid')
    run('config', 'user.name', 'Bounded source test'); run('config', 'core.autocrlf', 'false')
    before = {}
    for name in contract.ALLOWED_FILES:
        path = repo / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'original source\n')
        before[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    run('add', '.'); run('commit', '-qm', 'baseline')
    base = run('rev-parse', 'HEAD'); monkeypatch.setattr(contract, 'BASELINE', base)
    files = {}
    for name in contract.ALLOWED_FILES:
        path = repo / name; path.write_bytes(b'bounded corrected source\n')
        files[name] = {'before_sha256': before[name],
                       'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    registry = {'schema_version': 'violet.production-pixiv-a2.post-full-fix.v1',
        'baseline_head': base, 'authorization': 'owner-20260929-section-2.2-impact-verification',
        'scope': 'verified-launcher-runtime-metadata', 'files': files}
    registry_path = repo / contract.REGISTRY
    registry_path.write_text(json.dumps(registry), encoding='utf-8')
    run('add', '.'); run('commit', '-qm', 'registered correction')
    return repo, run, base, run('rev-parse', 'HEAD'), registry, registry_path


def test_post_full_source_contract_preserves_actual_full_baseline(tmp_path, monkeypatch):
    repo, _, base, candidate, _, _ = _fixture(tmp_path, monkeypatch)
    result = contract.verify_registered_delta(repo, base, candidate)
    assert result['baseline_head'] == base and result['candidate_head'] == candidate
    assert result['registered_files'] == sorted(contract.ALLOWED_FILES)


@pytest.mark.parametrize('change', ['extra_runtime', 'extra_config', 'wrong_before', 'wrong_after',
                                   'missing_file', 'wrong_scope', 'uncommitted_registry', 'live_source'])
def test_post_full_source_contract_rejects_unregistered_or_changed_inputs(tmp_path, monkeypatch, change):
    repo, run, base, candidate, registry, registry_path = _fixture(tmp_path, monkeypatch)
    name = 'scripts/trusted_git.py'
    if change in {'extra_runtime', 'extra_config'}:
        path = repo / ('scripts/extra.py' if change == 'extra_runtime' else 'docs/state/unregistered.json')
        path.write_bytes(b'unknown behavior\n')
    elif change in {'wrong_before', 'wrong_after'}:
        registry['files'][name]['before_sha256' if change == 'wrong_before' else 'after_sha256'] = '0' * 64
    elif change == 'missing_file':
        del registry['files'][name]
    elif change == 'wrong_scope':
        registry['scope'] = 'all future changes'
    elif change == 'uncommitted_registry':
        registry['scope'] = 'uncommitted replacement'
    elif change == 'live_source':
        (repo / name).write_bytes(b'changed after candidate freeze\n')
    if change not in {'extra_runtime', 'extra_config', 'live_source'}:
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
    if change not in {'uncommitted_registry', 'live_source'}:
        run('add', '.'); run('commit', '-qm', 'counterexample'); candidate = run('rev-parse', 'HEAD')
    with pytest.raises(ValueError):
        contract.verify_registered_delta(repo, base, candidate)
