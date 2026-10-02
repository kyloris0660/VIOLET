"""Real Git counterexamples for the bounded post-full source contract."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import production_pixiv_a2_post_full_fix as contract
from scripts.trusted_git import resolve_trusted_git_executable, trusted_git_environment


def _literal_query_sources():
    after = (Path(__file__).resolve().parents[1] / contract.LITERAL_QUERY_FILE).read_bytes().replace(b'\r\n', b'\n')
    start = after.index(b'    peer_link=aliased(SourceConceptSignalLink)\n')
    end = after.index(b'    accepted_elsewhere=exists().where(and_(', start)
    before = after[:start] + after[end:]
    before = before.replace(
        b'other_link.link_status.in_(VISIBLE_SOURCE_CONCEPT_STATUSES),other_concept.status.in_(VISIBLE_SOURCE_CONCEPT_STATUSES),~unassigned_placeholder,',
        b"other_link.link_status=='active',other_concept.status=='active',")
    assert hashlib.sha256(before).hexdigest() == contract.LITERAL_QUERY_BEFORE_SHA256
    assert hashlib.sha256(after).hexdigest() == contract.LITERAL_QUERY_AFTER_SHA256
    return before, after


def _release_gate_sources(name):
    after = (Path(__file__).resolve().parents[1] / name).read_bytes().replace(b'\r\n', b'\n')
    if name == 'backend/app/services/production_pixiv_release_provenance.py':
        before = after.replace(b" or call.get('business_valid',True) is not True", b'')
        before = before.replace(b" and call.get('business_valid',True) is True", b'')
    else:
        current = b"""        trusted = resolve_trusted_git_executable(repo_root=repo_root)
        def git(*args):
            result = run_trusted_git_text(repo_root, args, git=trusted, timeout=10)
            if result.returncode != 0:
                raise TrustedGitError('candidate_carry_git_operation_failed')
            return result.stdout.strip()
"""
        historical = b"""    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo_root), *args],
            text=True, encoding='utf-8', stderr=subprocess.DEVNULL, timeout=10).strip()
"""
        before = after.replace(current, b'')
        marker = b'    """Local launcher and A1 evidence share the same candidate drift boundary."""\n'
        before = before.replace(marker, marker + historical)
        before = before.replace(b'drift=inspect_worktree_drift(trusted,repo_root,',
            b'drift=inspect_worktree_drift(resolve_trusted_git_executable(repo_root=repo_root),repo_root,')
        before = before.replace(
            b"referenced = run_trusted_git_text(repo_root, ['grep','-l','-F',path,'--',\n                'backend','frontend','scripts','run.py'], git=trusted, timeout=10)",
            b"referenced = subprocess.run(['git','-C',str(repo_root),'grep','-l','-F',path,'--',\n                'backend','frontend','scripts','run.py'], capture_output=True, timeout=10)")
    assert {key:hashlib.sha256(value).hexdigest() for key,value in
            (('before_sha256',before),('after_sha256',after))} == contract.RELEASE_GATE_SOURCE_DELTAS[name]
    return before, after


def _registered_sources(name):
    if name == contract.LITERAL_QUERY_FILE: return _literal_query_sources()
    if name in contract.RELEASE_GATE_SOURCE_DELTAS: return _release_gate_sources(name)
    return b'original source\n', b'bounded corrected source\n'


def _fixture(tmp_path, monkeypatch, semantic_files=False):
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
        path.write_bytes(_registered_sources(name)[0])
        before[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if semantic_files:
        for name in contract.REPLAY_SOURCE_FILES | {'backend/app/semantic_fixture.py'}:
            path = repo / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'unchanged source fixture\n')
    run('add', '.'); run('commit', '-qm', 'baseline')
    base = run('rev-parse', 'HEAD'); monkeypatch.setattr(contract, 'BASELINE', base)
    files = {}
    for name in contract.ALLOWED_FILES:
        path = repo / name; path.write_bytes(_registered_sources(name)[1])
        files[name] = {'before_sha256': before[name],
                       'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    registry = {'schema_version': 'violet.production-pixiv-a2.post-full-fix.v1',
        'baseline_head': base, 'authorization': 'owner-20260929-section-2.2-impact-verification',
        'scope': contract.REGISTRY_SCOPE, 'files': files}
    if semantic_files:
        # The recorded source replay predates the new query correction.
        for name in {contract.LITERAL_QUERY_FILE, *contract.RELEASE_GATE_SOURCE_DELTAS}:
            source = repo / name
            source.write_bytes(_registered_sources(name)[0])
            registry['files'][name]['after_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    registry_path = repo / contract.REGISTRY
    registry_path.write_text(json.dumps(registry), encoding='utf-8')
    run('add', '.'); run('commit', '-qm', 'registered correction')
    return repo, run, base, run('rev-parse', 'HEAD'), registry, registry_path


@pytest.mark.parametrize('change',['none','before','after','extra_runtime','missing_pending_guard','missing_placeholder_member_guard'])
def test_literal_query_delta_pins_the_entire_verified_module(change):
    before,after = _literal_query_sources()
    if change == 'before': before += b'\n# changed baseline\n'
    elif change == 'after': after += b'\n# changed result\n'
    elif change == 'extra_runtime': after += b'\nexec("unregistered")\n'
    elif change == 'missing_pending_guard': after = after.replace(b'other_link.link_status.in_(VISIBLE_SOURCE_CONCEPT_STATUSES)', b"other_link.link_status=='active'")
    elif change == 'missing_placeholder_member_guard': after = after.replace(b'        ~other_member,', b'        True,')
    if change == 'none':
        result = contract.verify_literal_query_delta(before,after)
        assert not result['behavior_neutral_claimed'] and result['current_query_and_precision_verification_required']
    else:
        with pytest.raises(ValueError,match='literal_query_delta'):
            contract.verify_literal_query_delta(before,after)


@pytest.mark.parametrize('name',sorted(contract.RELEASE_GATE_SOURCE_DELTAS))
@pytest.mark.parametrize('change',['none','before','after','extra_runtime'])
def test_release_gate_delta_pins_whole_business_validity_and_trusted_git_modules(name,change):
    before,after = _release_gate_sources(name)
    if change == 'before': before += b'\n# changed baseline\n'
    elif change == 'after': after += b'\n# changed correction\n'
    elif change == 'extra_runtime': after += b'\nexec("unregistered")\n'
    if change == 'none':
        result = contract.verify_release_gate_source_delta(name,before,after)
        assert result['current_full_native_readmission_required'] and not result['behavior_neutral_claimed']
    else:
        with pytest.raises(ValueError,match='release_gate_delta'):
            contract.verify_release_gate_source_delta(name,before,after)


def test_post_full_source_contract_preserves_actual_full_baseline(tmp_path, monkeypatch):
    repo, _, base, candidate, _, _ = _fixture(tmp_path, monkeypatch)
    result = contract.verify_registered_delta(repo, base, candidate)
    assert result['baseline_head'] == base and result['candidate_head'] == candidate
    assert result['registered_files'] == sorted(contract.ALLOWED_FILES)


@pytest.mark.parametrize('change', ['extra_runtime', 'extra_config', 'wrong_before', 'wrong_after',
                                   'missing_file', 'wrong_scope', 'legacy_scope', 'revision_only_scope', 'uncommitted_registry', 'live_source'])
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
    elif change == 'legacy_scope':
        registry['scope'] = 'verified-launcher-runtime-metadata'
    elif change == 'revision_only_scope':
        registry['scope'] = 'verified-launcher-runtime-metadata-and-live-source-revision'
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


@pytest.mark.parametrize('change', ['none', 'unfinished', 'wrong_head', 'provider', 'ledger', 'identity', 'runtime_blob', 'query_blob', 'provenance_blob', 'trusted_git_blob'])
def test_source_replay_receipt_requires_unchanged_real_git_source(tmp_path, monkeypatch, change):
    repo, run, base, prior, registry, registry_path = _fixture(tmp_path, monkeypatch, semantic_files=True)
    monkeypatch.setattr(contract, 'SOURCE_REPLAY_HEAD', prior)
    query_source = repo / contract.LITERAL_QUERY_FILE
    query_source.write_bytes(_literal_query_sources()[1])
    if change == 'query_blob': query_source.write_bytes(query_source.read_bytes() + b'\n# unregistered query change\n')
    registry['files'][contract.LITERAL_QUERY_FILE]['after_sha256'] = hashlib.sha256(query_source.read_bytes()).hexdigest()
    for name in contract.RELEASE_GATE_SOURCE_DELTAS:
        source = repo / name; source.write_bytes(_release_gate_sources(name)[1])
        if change == ('trusted_git_blob' if name == 'scripts/trusted_git.py' else 'provenance_blob'):
            source.write_bytes(source.read_bytes() + b'\n# unregistered release gate\n')
        registry['files'][name]['after_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = repo / 'scripts/production_pixiv_a2_evidence.py'
    evidence.write_bytes(b'new bounded evidence gate\n')
    registry['files']['scripts/production_pixiv_a2_evidence.py']['after_sha256'] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    if change == 'runtime_blob':
        (repo / 'backend/app/semantic_fixture.py').write_bytes(b'changed semantic source\n')
    registry_path.write_text(json.dumps(registry), encoding='utf-8')
    run('add', '.'); run('commit', '-qm', 'new bounded gate')
    candidate = run('rev-parse', 'HEAD')
    identity = {'semantic': 'approved input'}
    command = {'status':'finished', 'exit_code':0, 'source_head':prior, 'source_head_after':prior,
               'behavior_guard_after':True, 'frozen_release_candidate':True,
               'provider_dispatch_authorized_in_this_invocation':False,
               'ledger_before_sha256':'a'*64, 'ledger_after_sha256':'a'*64, 'cwd':str(repo),
               'argv':['python',str(repo/'scripts/run_production_pixiv_a2_concepts.py'),'--cache-only']}
    manifest = {'candidate_head':prior, 'input_identity':identity,
                'processing':{'selected_pair_count':1,'judgment_count':1,'error_count':0,'remaining_missing_pair_count':0}}
    if change == 'unfinished': command['status'] = 'running'
    elif change == 'wrong_head': command['source_head_after'] = candidate
    elif change == 'provider': command['provider_dispatch_authorized_in_this_invocation'] = True
    elif change == 'ledger': command['ledger_after_sha256'] = 'b'*64
    elif change == 'identity': manifest['input_identity'] = {'semantic':'changed'}
    arguments = dict(candidate=candidate, prior_head=prior, command=command, manifest=manifest,
                     approved_identity=identity, ledger_sha256='a'*64)
    if change == 'none':
        result = contract.verify_source_replay_carry_forward(repo, **arguments)
        assert result['actual_source_head'] == prior and result['candidate_head'] == candidate
        assert result['original_invocation_not_relabelled'] and result['current_full_native_readmission_required']
    else:
        with pytest.raises(ValueError): contract.verify_source_replay_carry_forward(repo, **arguments)
