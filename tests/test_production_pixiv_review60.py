"""Release provenance and bounded-input regressions from the current review."""
import copy
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest


@pytest.mark.parametrize('change', ['missing_call', 'wrong_key', 'duplicate_call', 'reserved'])
def test_legacy_pair_source_requires_unique_settled_matching_call(tmp_path, monkeypatch, change):
    from test_production_pixiv_adjudication import config, MeteredProvider, _eligible_llm_edges, service
    from app.services.production_pixiv_release_provenance import verify_selected_judgment_sources
    signals, edges = _eligible_llm_edges(1)
    provider = MeteredProvider()
    monkeypatch.setattr(service, 'primary_openai_provider_from_settings', lambda: (provider, {}))
    cfg = config(tmp_path)
    rows, _ = service.run_bounded_llm_adjudication(edges, signals=signals, config=cfg)
    record_path = Path(cfg.durable_cache_dir) / 'records' / (rows[0]['cache_key'] + '.json')
    record = json.loads(record_path.read_text())
    record.pop('budget_response')
    record_path.write_text(json.dumps(record), encoding='utf-8')
    ledger = json.loads((tmp_path / 'budget.json').read_text())
    assert verify_selected_judgment_sources(edges, signals, rows, cfg, ledger)['judgment_count'] == 1
    if change == 'missing_call':
        ledger['calls'] = []
    elif change == 'wrong_key':
        ledger['calls'][0]['key'] = 'unrelated'
    elif change == 'duplicate_call':
        ledger['calls'].append({**ledger['calls'][0], 'id': 'other-ticket'})
    else:
        ledger['calls'][0]['status'] = 'reserved'
    with pytest.raises(ValueError, match='semantic_.*attempt'):
        verify_selected_judgment_sources(edges, signals, rows, cfg, ledger)
    assert provider.calls == 1


@pytest.mark.parametrize('listed_fourth', [False, True])
def test_terminal_cannot_hide_or_normalize_fourth_logical_attempt(tmp_path, monkeypatch, listed_fourth):
    from test_production_pixiv_role_coverage import partial_facts, Responses
    from app.services.production_pixiv_role_extraction import (
        BudgetedExtractionProvider, plan_role_coverage_repair, repair_missing_role_coverage,
        summarize_role_response_coverage)
    from app.services.production_pixiv_release_inputs import verify_role_completion
    value, vocab, facts, _, budget = partial_facts(tmp_path)
    unit = plan_role_coverage_repair(value, vocab, facts)[0][0]
    logical = BudgetedExtractionProvider.logical_keys([replace(unit.unit_group,
        data_type_label='Requested unresolved raw tags: ' + json.dumps(['MysteryMissing']))])[0]
    for number in range(2):
        ticket = budget.reserve('extra-' + str(number), [], logical_keys=[logical])
        budget.settle(ticket, {}, success=False)
    facts = repair_missing_role_coverage(value, vocab, facts, provider=Responses(lambda g: ([], [])),
        budget=budget, cache_dir=tmp_path / 'roles')
    ledger = json.loads(budget.path.read_text())
    monkeypatch.setattr('app.services.production_pixiv_service.production_consumer', lambda _: value)
    verify_role_completion([], vocab, facts, ledger)
    answer = facts['role_terminal_targets']['aggregate-12345678']['MysteryMissing']
    # Reproduce an already retained historical over-limit call; no new dispatch.
    prior = next(row for row in ledger['calls'] if row['id'] == answer['attempt_ids'][0])
    ledger['calls'].append({**prior, 'id': 'historical-fourth'})
    if listed_fourth:
        answer['attempt_ids'].append('historical-fourth')
    facts['role_response_coverage'] = summarize_role_response_coverage(value, vocab, facts)
    with pytest.raises(ValueError, match='semantic_terminal'):
        verify_role_completion([], vocab, facts, ledger)


def test_untrusted_page_count_does_not_allocate_remote_domain(tmp_path, monkeypatch):
    from scripts import run_production_pixiv_a2_metadata as runner
    from test_production_pixiv_metadata_runner import payload
    row = json.loads(payload())[0]
    row[2]['page_count'] = 10**9
    path = tmp_path / 'raw.json'
    path.write_text(json.dumps([row]), encoding='utf-8')
    def forbidden_range(*args):
        raise AssertionError('untrusted_page_count_expansion')
    monkeypatch.setattr(runner, 'range', forbidden_range, raising=False)
    assert runner.valid_raw_payload(path, '123456789') is False
    assert runner.valid_raw_payload(path, '123456789', [0]) is True


@pytest.mark.parametrize('semantic', [False, True])
@pytest.mark.parametrize('production_prompt', [False, True])
def test_pair_source_retains_legacy_and_current_exact_call_identity(tmp_path, monkeypatch, semantic, production_prompt):
    from test_production_pixiv_adjudication import config, MeteredProvider, _eligible_llm_edges, service
    from app.services.production_pixiv_release_provenance import verify_selected_judgment_sources
    signals, edges = _eligible_llm_edges(1); provider = MeteredProvider()
    monkeypatch.setattr(service, 'primary_openai_provider_from_settings', lambda: (provider, {}))
    cfg = replace(config(tmp_path), semantic_cache_reuse=semantic)
    if production_prompt:cfg = replace(cfg, prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    rows, _ = service.run_bounded_llm_adjudication(edges, signals=signals, config=cfg)
    ledger = json.loads((tmp_path / 'budget.json').read_text())
    assert verify_selected_judgment_sources(edges, signals, rows, cfg, ledger)['sources'][0]['source_attempt_id'] == ledger['calls'][0]['id']
    path = Path(cfg.durable_cache_dir) / 'records' / (rows[0]['cache_key'] + '.json')
    record = json.loads(path.read_text());record.pop('budget_response')
    path.write_text(json.dumps(record), encoding='utf-8')
    assert verify_selected_judgment_sources(edges, signals, rows, cfg, ledger)['sources'][0]['source_attempt_id'] == ledger['calls'][0]['id']
    assert provider.calls == 1


def test_completed_answer_does_not_turn_retained_four_attempts_into_active_terminal(tmp_path, monkeypatch):
    from test_production_pixiv_role_coverage import partial_facts
    from app.services.production_pixiv_role_extraction import BudgetedExtractionProvider, _original_completion_questions, summarize_role_response_coverage
    from app.services.production_pixiv_release_inputs import verify_role_completion
    value, vocab, facts, provider, budget = partial_facts(tmp_path, ['MysteryKnown'])
    originals, _ = _original_completion_questions(value, vocab, facts, require_complete=True)
    unit = next(iter(originals.values()))
    logical = BudgetedExtractionProvider.logical_keys([replace(unit.unit_group,
        data_type_label='Requested unresolved raw tags: ' + json.dumps(['MysteryKnown']))])[0]
    ledger = json.loads(budget.path.read_text());original = ledger['calls'][0]
    assert logical in original['logical_keys']
    ledger['calls'].extend({**original, 'id': 'historical-' + str(i)} for i in range(3))
    facts['role_terminal_targets'] = {'aggregate-12345678': {'MysteryKnown': {
        'logical_key': logical, 'attempt_ids': [row['id'] for row in ledger['calls']],
        'identity_confirmed': False, 'reason_code': 'three_prior_logical_attempts_exhausted'}}}
    facts['role_response_coverage'] = summarize_role_response_coverage(value, vocab, facts)
    monkeypatch.setattr('app.services.production_pixiv_service.production_consumer', lambda _: value)
    result = verify_role_completion([], vocab, facts, ledger)
    assert result['counts']['candidate'] == 1 and result['counts'].get('attempt_limit_reached', 0) == 0
    assert len(facts['role_terminal_targets']['aggregate-12345678']['MysteryKnown']['attempt_ids']) == 4
    assert len(provider.calls) == 1


def public_result(candidate):
    return {'contract_id': 'production_pixiv_a2_v1', 'target_met': True,
        'safe_to_merge': False, 'route_approved': False, 'project_lead_acceptance': 'pending',
        'candidate_head': candidate, 'coverage': {'counts': {'metadata_complete': 6}, 'media_count': 6},
        'production': {'active_runs': 1, 'duplicate_support_count': 0, 'bound_media': 6},
        'budget': {'cap_usd': 30, 'charged_or_reserved_usd': 1},
        'quality': {'failed_cases': 0, 'case_count': 80},
        'workload': {'query_count': 240, 'failed_queries': 0},
        'browser': {'originals_loaded': 3, 'thumbnails_loaded': 3},
        'launcher': {'new_process': True, 'apply_enabled': False}, 'validation': {}, 'recovery': {}}


def state_repo(tmp_path):
    from scripts import production_pixiv_a2_state
    root = tmp_path / 'repo'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
            *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    git('init')
    (root / 'run.py').write_text('VERSION=1\n')
    state = json.loads((Path(__file__).resolve().parents[1] / 'docs/state/current-phase.json').read_text(encoding='utf-8'))
    for link in state['durable_links']:
        path = root / link['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Fixture\n')
    git('add', '.')
    git('commit', '-m', 'base')
    head = git('rev-parse', 'HEAD')
    state.update(target_met=True, candidate_head=head, result_path='docs/reports/production-pixiv-a2-summary.json')
    target = root / state['result_path']
    target.write_text(json.dumps(public_result(head)), encoding='utf-8')
    git('add', state['result_path']); git('commit', '-m', 'document result')
    production_pixiv_a2_state.validate(state, root)
    return root, state, git


@pytest.mark.parametrize('change', ['behavior_commit', 'dirty_behavior', 'state_candidate', 'parent_path', 'absolute_path'])
def test_completion_state_cannot_relabel_old_or_external_result(tmp_path, change):
    from scripts.production_pixiv_a2_state import validate
    from scripts.check_documentation_state import DocumentationStateError
    root, state, git = state_repo(tmp_path)
    if change in ('behavior_commit', 'dirty_behavior'):
        (root / 'run.py').write_text('VERSION=2\n')
        if change == 'behavior_commit':
            git('add', 'run.py'); git('commit', '-m', 'behavior')
            state['candidate_head'] = git('rev-parse', 'HEAD')
    elif change == 'state_candidate':
        state['candidate_head'] = 'b' * 40
    else:
        outside = tmp_path / 'outside.json'
        outside.write_text(json.dumps(public_result(state['candidate_head'])))
        state['result_path'] = '../outside.json' if change == 'parent_path' else str(outside)
    with pytest.raises(DocumentationStateError, match='pixiv_a2_(result|redaction)'):
        validate(state, root)
