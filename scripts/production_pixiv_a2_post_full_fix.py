"""Exact source delta and affected verification after the one authorized full run.

The full run remains evidence for its actual source. This contract permits only
the registered launcher, live source-revision, complete owned-business, and
concept-chip evidence, review102 literal-component boundary, and reviews103104
business-valid source and trusted candidate Git corrections, including the
same validity rule for retained role responses in review105.
Review106 adds strict settled-call admission before runtime exact, semantic,
or legacy cache reuse, with the original paid ticket retained on migration.
Reviews107-109 apply that admission to role units, bind the exact historical
88 nodes and preserve every original debit field under the sealed backfill.
Reviews110-111 bind the canonical launcher root to trusted Git and refuse
actual reservation/cap overruns in settlement, cache and native source proof.
Reviews113-114 bind every correction predecessor to its exact paid input and
revalidate cached role projections on every read before they shape paid pairs.
It never describes that correction as documentation-only or as another full run.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

BASELINE = 'd26bd0c5cde6865a2760a8c59b749a9fb4652ace'
REGISTRY = 'docs/state/production-pixiv-a2-post-full-fix.json'
REGISTRY_SCOPE = 'verified-launcher-runtime-metadata-live-source-revision-owned-business-chip-literal-component-and-source-git-release-gates'
LITERAL_QUERY_FILE = 'backend/app/services/source_concept_search_service.py'
LITERAL_QUERY_BEFORE_SHA256 = 'fc2b7f14691c5a536661aed9615971f116b9a9784840d85bd85bfb6d8c4d01bd'
LITERAL_QUERY_AFTER_SHA256 = '2af2abadb0a6d9671c4f8a3d6b8acfdae756b9889f665fe2884777799b75b34f'
RELEASE_GATE_SOURCE_DELTAS = {'backend/app/services/production_pixiv_role_extraction.py': {'before_sha256': '286f861a58842384ce5b862fd67fc988c703ad3fbaaf73efc6a3a2f903ff87ca', 'after_sha256': 'e95a9183efb20a7e6b8a583c788b4b9c49a74a80cb17819cb02446fe1b14bad4'}, 'scripts/production_pixiv_budget_authority.py': {'before_sha256': '2094979b1c6e1427b81513e46128d6f136c660ca205215e7d841ce182a567744', 'after_sha256': '27a20b36948d1cff9ef1c9ef4f9e5e7e109c697f1777212a657e8ed325b9686a'}, 'backend/app/services/source_concept_budget.py': {'before_sha256': '236f32512630b3c5f050eaf394bd3c5215ba8eb97daa5dd915f8273586df1c57', 'after_sha256': 'da798e3fbabad3412528d44f72a2944b8395acb86554a9a0c58bfb9aaf85209d'}, 'backend/app/services/source_concept_resolver_service.py': {'before_sha256': 'b1639c8ee41001799a6bffc7d4a87cdb3b3223e7ac88c25d5145d736041f92e3', 'after_sha256': '138386e98b08b36834be643be79340f9a29e93ef052d54da26ae232116922ac9'}, 'backend/app/services/production_pixiv_release_provenance.py': {'before_sha256': '2b73db3e16e7c54e00fccb9744489d70ca558ee5a70f3d26adec8d3cc1aabe10', 'after_sha256': '30e4b8effa83f6ed4d5b06282f9f78cc516c55cefd2b22ce07a1903ca90e11bb'}, 'scripts/trusted_git.py': {'before_sha256': '3cd8e062d3c897ba2eab8f84da3d6221de79c51f9fda5b1a5a0dd18d449ea4c1', 'after_sha256': '8126faf872ba09c42df6e68a57f304aefba30c07d54149c8a4f05191618fa7f8'}, 'scripts/production_pixiv_a2_evidence.py': {'before_sha256': 'cbbf71fe6594197e4503c30fd8cfc4f269659ce1350a79f2ed1ab84be15b8822', 'after_sha256': '57824ba98fba480009cd3a36a36c27e849f02f205084bff31108500f4f2e7be0'}, 'backend/app/services/production_pixiv_pair_correction.py': {'before_sha256': 'ca118bcded130e1043e9ebd3afe245faf1211e51e29c1731b16990b1c124841f', 'after_sha256': 'd6f830401241b117e970c92a3f3fcf1a5bc23ac1b68a77d0f74a14beb70398fb'}}
ALLOWED_FILES = frozenset({
    'backend/app/services/production_pixiv_pair_correction.py',
    'tests/test_production_pixiv_reviews113_114.py',
    'backend/app/services/production_pixiv_role_extraction.py',
    'scripts/production_pixiv_budget_authority.py',
    'docs/state/production-pixiv-a2-budget-prefix-compatibility.json',
    'tests/test_production_pixiv_role_extraction.py', 'tests/test_production_pixiv_reviews107_109.py',
    'tests/test_production_pixiv_role_coverage.py',
    'tests/test_production_pixiv_reviews110_111.py',
    'backend/app/services/source_concept_resolver_service.py',
    'backend/app/services/source_concept_budget.py', 'tests/test_source_concept_task_budget.py',
    'scripts/trusted_git.py', 'scripts/production_pixiv_a2_full_suite.py',
    'scripts/production_pixiv_a2_post_full_fix.py', 'scripts/check_production_pixiv_a2.py',
    'tests/test_trusted_git.py', 'tests/test_production_pixiv_a2_post_full_fix.py',
    'docs/state/production-pixiv-a2-required-tests.json',
    'scripts/production_pixiv_a2_evidence.py', 'tests/test_production_pixiv_a2.py',
    'tests/test_production_pixiv_a2_evidence.py', 'docs/state/production-pixiv-a2-approved-projection.json',
    'scripts/violet_production_control.py', 'tests/test_production_launcher_control.py',
    LITERAL_QUERY_FILE, 'tests/test_production_pixiv_a2_api.py',
    'docs/state/production-pixiv-a2-ignored-inputs.json',
    'backend/app/services/production_pixiv_release_provenance.py',
    'tests/test_production_pixiv_adjudication.py',
    'tests/test_production_pixiv_release_inputs.py',
    'tests/test_production_pixiv_review68.py',
    'tests/test_production_pixiv_review60.py', 'tests/test_production_pixiv_correction29.py',
    'tests/test_production_pixiv_a1_contract.py',
})
SOURCE_REPLAY_HEAD = '8aeefb5e3f785360ca8b0cd55de7674d6ea62c3f'
REPLAY_SOURCE_FILES = frozenset({
    'scripts/run_production_pixiv_a2_concepts.py', 'scripts/run_production_pixiv_a2_metadata.py',
    'scripts/run_production_pixiv_a2_product.py', 'scripts/check_python_env.py', 'scripts/trusted_git.py',
})
REPLAY_GATE_FILES = frozenset({
    'backend/app/services/production_pixiv_pair_correction.py',
    'tests/test_production_pixiv_reviews113_114.py',
    'backend/app/services/production_pixiv_role_extraction.py',
    'scripts/production_pixiv_budget_authority.py',
    'docs/state/production-pixiv-a2-budget-prefix-compatibility.json',
    'tests/test_production_pixiv_role_extraction.py', 'tests/test_production_pixiv_reviews107_109.py',
    'tests/test_production_pixiv_role_coverage.py',
    'tests/test_production_pixiv_reviews110_111.py',
    'backend/app/services/source_concept_resolver_service.py',
    'backend/app/services/source_concept_budget.py', 'tests/test_source_concept_task_budget.py',
    'docs/state/production-pixiv-a2-required-tests.json',
    'scripts/check_production_pixiv_a2.py', 'scripts/production_pixiv_a2_evidence.py',
    'scripts/production_pixiv_a2_post_full_fix.py', 'tests/test_production_pixiv_a2.py',
    'tests/test_production_pixiv_a2_evidence.py', 'tests/test_production_pixiv_a2_post_full_fix.py',
    'docs/state/production-pixiv-a2-approved-projection.json', REGISTRY,
    'scripts/violet_production_control.py', 'tests/test_production_launcher_control.py',
    LITERAL_QUERY_FILE, 'tests/test_production_pixiv_a2_api.py',
    'docs/state/production-pixiv-a2-ignored-inputs.json',
    'scripts/trusted_git.py', 'backend/app/services/production_pixiv_release_provenance.py',
    'tests/test_production_pixiv_adjudication.py', 'tests/test_trusted_git.py',
    'tests/test_production_pixiv_release_inputs.py',
    'tests/test_production_pixiv_review68.py',
    'tests/test_production_pixiv_review60.py', 'tests/test_production_pixiv_correction29.py',
    'tests/test_production_pixiv_a1_contract.py',
})


def verify_literal_query_delta(before, after):
    """Admit only the exact isolated-PG/API-verified query correction.

    These hashes pin the whole old/new module, not a selected function summary.
    Full source loaders and current query/precision gates still run natively.
    This does not make the query correction behavior-neutral.
    """
    from scripts.check_production_pixiv_a2 import require
    digest = lambda value: hashlib.sha256(value).hexdigest()
    require(digest(before) == LITERAL_QUERY_BEFORE_SHA256
            and digest(after) == LITERAL_QUERY_AFTER_SHA256,
            'post_full_unregistered_literal_query_delta')
    return {'file': LITERAL_QUERY_FILE, 'before_sha256': digest(before),
            'after_sha256': digest(after), 'behavior_neutral_claimed': False,
            'current_query_and_precision_verification_required': True}


def verify_release_gate_source_delta(name, before, after):
    """Pin the two bounded release-gate corrections as complete source modules.

    Actual B3 evidence keeps its historical validator identity. A current full
    native replay must apply the stricter validity and trusted Git checks again.
    """
    from scripts.check_production_pixiv_a2 import require
    digest = lambda value: hashlib.sha256(value).hexdigest()
    require(name in RELEASE_GATE_SOURCE_DELTAS, 'source_replay_unregistered_release_gate')
    hashes = {'before_sha256': digest(before), 'after_sha256': digest(after)}
    require(hashes == RELEASE_GATE_SOURCE_DELTAS[name], 'source_replay_release_gate_delta')
    return {'file': name, **hashes, 'behavior_neutral_claimed': False,
            'current_full_native_readmission_required': True}


def verify_source_replay_carry_forward(root, *, candidate, prior_head, command, manifest,
                                     approved_identity, ledger_sha256):
    """Keep B3's actual source receipt while admitting exact bounded gate fixes.

    This validates only unchanged source computation and input identity. The
    current product loader must still perform its real full source verification.
    """
    from scripts.check_production_pixiv_a2 import require
    from scripts.trusted_git import resolve_trusted_git_executable, run_trusted_git_bytes
    root = Path(root).resolve(strict=True)
    git = resolve_trusted_git_executable(repo_root=root)
    def operation(*args):
        result = run_trusted_git_bytes(root, args, git=git)
        require(result.returncode == 0, 'source_replay_git_operation')
        return result.stdout
    verify_registered_delta(root, BASELINE, candidate)
    require(prior_head == SOURCE_REPLAY_HEAD and candidate != prior_head
            and operation('rev-parse', 'HEAD').decode().strip() == candidate
            and operation('merge-base', prior_head, candidate).decode().strip() == prior_head,
            'source_replay_candidate_ancestry')
    require(command.get('status') == 'finished' and command.get('exit_code') == 0
            and command.get('source_head') == command.get('source_head_after') == prior_head
            and command.get('behavior_guard_after') is True
            and command.get('frozen_release_candidate') is True
            and command.get('provider_dispatch_authorized_in_this_invocation') is False
            and command.get('ledger_before_sha256') == command.get('ledger_after_sha256') == ledger_sha256
            and Path(command.get('cwd', '')).resolve() == root
            and '--cache-only' in command.get('argv', [])
            and str(root / 'scripts/run_production_pixiv_a2_concepts.py') in command.get('argv', []),
            'source_replay_actual_command')
    processing = manifest.get('processing', {})
    require(manifest.get('candidate_head') == prior_head and manifest.get('input_identity') == approved_identity
            and processing.get('selected_pair_count') == processing.get('judgment_count')
            and type(processing.get('judgment_count')) is int and processing['judgment_count'] > 0
            and processing.get('error_count') == processing.get('remaining_missing_pair_count') == 0,
            'source_replay_approved_inputs')
    changed = set(filter(None, operation('diff', '--name-only', '-z', prior_head, candidate).decode().split('\0')))
    projections = {'docs/state/current-phase.json', 'docs/reports/production-pixiv-a2-summary.json'}
    require(all(name in REPLAY_GATE_FILES | projections or (name.startswith('docs/') and name.endswith('.md'))
                for name in changed), 'source_replay_semantic_source_changed')
    backend = set(filter(None, operation('ls-tree', '-r', '--name-only', prior_head, '--', 'backend').decode().splitlines()))
    require(backend and backend == set(filter(None, operation('ls-tree', '-r', '--name-only', candidate, '--', 'backend').decode().splitlines())),
            'source_replay_backend_inventory')
    query_delta = verify_literal_query_delta(
        operation('show', prior_head + ':' + LITERAL_QUERY_FILE),
        operation('show', candidate + ':' + LITERAL_QUERY_FILE))
    gate_deltas = {name: verify_release_gate_source_delta(name,
        operation('show', prior_head + ':' + name), operation('show', candidate + ':' + name))
        for name in sorted(RELEASE_GATE_SOURCE_DELTAS)}
    blobs = {}
    for name in sorted(((backend - {LITERAL_QUERY_FILE}) | REPLAY_SOURCE_FILES)
                       - RELEASE_GATE_SOURCE_DELTAS.keys()):
        before = operation('rev-parse', prior_head + ':' + name).decode().strip()
        after = operation('rev-parse', candidate + ':' + name).decode().strip()
        require(before == after, 'source_replay_semantic_blob_changed')
        blobs[name] = before
    return {'schema_version': 'violet.production-pixiv-a2.source-replay-carry-forward.v1',
            'actual_source_head': prior_head, 'candidate_head': candidate,
            'unchanged_source_blobs': blobs, 'changed_gate_paths': sorted(changed),
            'exact_literal_query_source_delta': query_delta,
            'exact_release_gate_source_deltas': gate_deltas,
            'input_identity': approved_identity, 'ledger_sha256': ledger_sha256,
            'original_invocation_not_relabelled': True, 'current_full_native_readmission_required': True,
            'new_provider_calls': 0, 'additional_full_suite_invocations': 0}


def verify_registered_delta(root, baseline, candidate):
    from scripts.check_production_pixiv_a2 import require
    from scripts.trusted_git import (_assert_no_alias_components,
                                    resolve_trusted_git_executable, run_trusted_git_bytes)
    root = Path(root).resolve(strict=True)
    git = resolve_trusted_git_executable(repo_root=root)
    def command(*args, optional=False):
        result = run_trusted_git_bytes(root, args, git=git)
        require(result.returncode == 0 or optional, 'post_full_git_operation')
        return result.stdout if result.returncode == 0 else None
    require(baseline == BASELINE and candidate != baseline, 'post_full_registered_baseline')
    require(command('merge-base', baseline, candidate).decode().strip() == baseline,
            'post_full_baseline_ancestry')
    path = root / REGISTRY
    _assert_no_alias_components(path)
    raw = path.read_bytes()
    require(raw == command('show', candidate + ':' + REGISTRY), 'post_full_registry_live_binding')
    registry = json.loads(raw)
    require(registry.get('schema_version') == 'violet.production-pixiv-a2.post-full-fix.v1'
            and registry.get('baseline_head') == baseline
            and registry.get('authorization') == 'owner-20260929-section-2.2-impact-verification'
            and registry.get('scope') == REGISTRY_SCOPE, 'post_full_registry_scope')
    require(set(registry.get('files', {})) == ALLOWED_FILES, 'post_full_registry_file_set')
    changed = set(filter(None, command('diff', '--name-only', '-z', baseline, candidate).decode().split('\0')))
    # Existing executable documentation carry-forward permits these projections;
    # every other changed runtime, module, package, test or configuration is exact.
    projections = {'docs/state/current-phase.json', 'docs/reports/production-pixiv-a2-summary.json'}
    bounded = {p for p in changed if p not in projections
               and not (p.startswith('docs/') and p.endswith('.md'))}
    require(bounded == ALLOWED_FILES | {REGISTRY}, 'post_full_unregistered_source_delta')
    for name, evidence in registry['files'].items():
        before = command('show', baseline + ':' + name, optional=True)
        after = command('show', candidate + ':' + name)
        require(set(evidence) == {'before_sha256', 'after_sha256'}, 'post_full_delta_fields')
        digest = lambda value: hashlib.sha256(value).hexdigest() if value is not None else None
        require(digest(before) == evidence['before_sha256'] and digest(after) == evidence['after_sha256'],
                'post_full_delta_digest')
        if name == LITERAL_QUERY_FILE:
            verify_literal_query_delta(before, after)
        if name in RELEASE_GATE_SOURCE_DELTAS:
            require(digest(after) == RELEASE_GATE_SOURCE_DELTAS[name]['after_sha256'],
                    'post_full_release_gate_source_delta')
        _assert_no_alias_components(root / name)
        # Working-tree CRLF conversion is permitted only when Git's normalized
        # source is still the registered candidate blob.
        require((root / name).read_bytes().replace(b'\r\n', b'\n') == after,
                'post_full_live_source_changed')
    return {'baseline_head': baseline, 'candidate_head': candidate,
            'registered_files': sorted(ALLOWED_FILES), 'registry_sha256': hashlib.sha256(raw).hexdigest()}


def verify_post_full_fix(private, gate, *, candidate, root, baseline_result):
    from scripts.check_production_pixiv_a2 import (read, evidence_path, require,
                                                verify_required_test_command)
    from scripts.production_pixiv_a2_evidence import pytest_outcome
    from scripts.trusted_git import candidate_behavior_carry_forward
    baseline = baseline_result['source_head']
    delta = verify_registered_delta(root, baseline, candidate)
    require(candidate_behavior_carry_forward(root, candidate), 'post_full_current_behavior')
    receipt = read(private, gate['post_full_fix'])
    require(receipt.get('schema_version') == 'violet.production-pixiv-a2.post-full-fix-evidence.v1'
            and receipt.get('baseline_head') == baseline and receipt.get('candidate_head') == candidate
            and receipt.get('registry_sha256') == delta['registry_sha256']
            and receipt.get('additional_full_invocations') == 0
            and receipt.get('behavior_neutral_claimed') is False, 'post_full_evidence_identity')
    full_command = read(private, gate['command'])
    results = {}
    for kind in ('focused', 'postgresql', 'historical'):
        item = receipt[kind]
        cmd = read(private, item['command'])
        log_path = evidence_path(private, item['log'])
        xml_path = evidence_path(private, item['xml'])
        require(cmd.get('status') == 'finished' and cmd.get('source_head') == candidate
                and cmd.get('source_head_after') == candidate and cmd.get('behavior_guard_after') is True,
                'post_full_affected_source')
        require(datetime.fromisoformat(cmd['started_at']) > datetime.fromisoformat(full_command['finished_at']),
                'post_full_affected_time')
        require(hashlib.sha256(log_path.read_bytes()).hexdigest() == cmd['log_sha256'],
                'post_full_affected_log_digest')
        if kind != 'historical':
            verify_required_test_command(private, item, cmd, kind)
        else:
            verify_exact_historical_tests(root,cmd,xml_path)
        counts, failures = pytest_outcome(cmd, log_path.read_text(encoding='utf-8'), xml_path)
        require(not failures and counts['failed'] == counts['errors'] == 0 and counts['passed'] > 0
                and all(counts[k] == item[k] for k in ('passed', 'failed', 'skipped', 'errors')),
                'post_full_affected_result')
        if kind == 'historical':
            require(counts['passed'] == 88 and counts['skipped'] == 0, 'post_full_historical_coverage')
        results[kind] = counts
    return {**delta, 'affected_verification': results, 'additional_full_invocations': 0,
            'behavior_neutral_claimed': False, 'coverage_scope': 'registered source delta and affected verification'}


def verify_exact_historical_tests(root,command,xml_path):
    """Bind every original failure node, interpreter, cwd and actual XML case."""
    import sys
    from xml.etree import ElementTree as ET
    from scripts.check_production_pixiv_a2 import require
    root=Path(root).resolve(strict=True)
    manifest=json.loads((root/'docs/state/production-pixiv-a2-required-tests.json').read_text(encoding='utf-8'))
    expected=manifest['historical'];argv=command.get('argv',[])
    require(len(expected)==len(set(expected))==88,'post_full_historical_registration')
    require(len(argv)==len(expected)+5 and argv[1:3]==['-m','pytest']
        and argv[3:-2]==expected and argv[-2]=='-v' and argv[-1].startswith('--junitxml='),
        'post_full_historical_command')
    require(Path(argv[0]).resolve()==Path(sys.executable).resolve()
        and Path(command.get('cwd','')).resolve()==root,'post_full_historical_runtime')
    require(Path(argv[-1].split('=',1)[1]).resolve()==Path(xml_path).resolve(),
        'post_full_historical_xml')
    cases=list(ET.parse(xml_path).iter('testcase'))
    actual=[case.attrib['classname'].replace('.','/')+'.py::'+case.attrib['name'] for case in cases]
    require(len(actual)==len(set(actual))==88 and set(actual)==set(expected),
        'post_full_historical_exact_nodes')
    return {'exact_historical_nodes':88,'runtime_and_cwd_bound':True}
