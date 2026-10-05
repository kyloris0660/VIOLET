import copy
import hashlib
import json
from dataclasses import replace

import pytest


@pytest.mark.parametrize('defect', ['missing', 'extra', 'other', 'duplicate'])
def test_role_source_binds_exact_logical_targets(tmp_path, defect):
    from app.services.production_pixiv_corrections import correction_units, signal_semantics
    from app.services.production_pixiv_role_extraction import ROLE_SCHEMA, extract_production_roles
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    from app.services.production_pixiv_semantics import adapt_production_semantics, build_semantic_vocabulary
    from test_production_pixiv_role_coverage import context, Responses, candidate
    from test_production_pixiv_role_extraction import task_budget
    value = context(['Hero']); vocab = build_semantic_vocabulary([])
    base = adapt_production_semantics(value, vocab, {'schema_version': ROLE_SCHEMA, 'records': {}})
    request = {'aggregate_fingerprint': 'aggregate-12345678', 'raw_targets': ['Hero'],
        'supersedes': {'Hero': signal_semantics(base.signals[0])},
        'conflict_evidence': ['observed conflict'], 'authorization': 'bounded fixture'}
    units, _ = correction_units(base, {}, [request])
    provider = Responses(lambda group: ([candidate('Hero')], []))
    budget = task_budget(tmp_path, provider)
    result = extract_production_roles(units, provider=provider, budget=budget, cache_dir=tmp_path/'roles')
    facts = {'schema_version': ROLE_SCHEMA, 'records': {}, 'semantic_corrections': [request],
        'correction_records': result['records']}
    ledger = json.loads(budget.path.read_text())
    assert verify_role_response_sources(value, vocab, facts, tmp_path/'roles', ledger)['record_count'] == 1
    keys = ledger['calls'][0]['logical_keys']
    ledger['calls'][0]['logical_keys'] = {'missing': [], 'extra': keys+['unrelated'],
        'other': ['different'], 'duplicate': keys+keys}[defect]
    with pytest.raises(ValueError, match='original_response_missing'):
        verify_role_response_sources(value, vocab, facts, tmp_path/'roles', ledger)
    diagnostic=verify_role_response_sources(value,vocab,facts,tmp_path/'roles',ledger,diagnostic=True)
    assert any('role_source_original_logical_keys_changed' in row['error'] for row in diagnostic['raw_errors'])


def test_duplicate_raw_cannot_expand_correction_authority():
    from app.services.production_pixiv_corrections import correction_units, signal_semantics
    from test_production_pixiv_role_coverage import context
    value = context(['Hero']); first = value.signals[0]
    second = replace(first, signal_key=first.signal_key+':other', role_hint='artist')
    value = replace(value, signals=(second, first))
    request = {'aggregate_fingerprint': 'aggregate-12345678', 'raw_targets': ['Hero'],
        'supersedes': {'Hero': signal_semantics(first)},
        'conflict_evidence': ['observed conflict'], 'authorization': 'fixture'}
    with pytest.raises(ValueError, match='duplicate_raw_target'):
        correction_units(value, {}, [request])


@pytest.fixture
def budget_case(tmp_path, monkeypatch):
    from scripts import production_pixiv_budget_authority as authority
    original = {'cap_microusd': 10000000, 'calls': [{'id': 'a', 'charged_microusd': 8, 'reserved_microusd': 10}]}
    raw = json.dumps(original).encode(); (tmp_path/'closeout43-budget-before-private.json').write_bytes(raw)
    receipt = {'previous_cap_microusd': 10000000, 'cap_microusd': 30000000,
        'authorization_source': 'owner task', 'authorization_id': 'fixed authority',
        'charged_before_microusd': 8, 'call_count_before': 1,
        'ledger_before_sha256': hashlib.sha256(raw).hexdigest(), 'recorded_at': 'fixed time'}
    anchor = tmp_path/'docs/state'; anchor.mkdir(parents=True)
    (anchor/'production-pixiv-a2-budget-authority.json').write_text(json.dumps(receipt))
    monkeypatch.setattr(authority, 'ROOT', tmp_path)
    ledger = {**original, 'cap_microusd': 30000000, 'cap_amendments': [receipt]}
    return authority, ledger, tmp_path


@pytest.mark.parametrize('field', ['authorization_source', 'authorization_id', 'charged_before_microusd',
    'call_count_before', 'ledger_before_sha256', 'original', 'old_call', 'duplicate'])
def test_budget_amendment_is_validated_before_dispatch(budget_case, field):
    authority, ledger, root = budget_case
    assert authority.authorized_task_cap(root, ledger) == 30
    if field == 'original': (root/'closeout43-budget-before-private.json').write_text('{}')
    elif field == 'old_call': ledger['calls'][0]['id'] = 'other'
    elif field == 'duplicate': ledger['cap_amendments'] *= 2
    else: ledger['cap_amendments'][0][field] = 'tampered'
    with pytest.raises(ValueError, match='budget_authority'):
        authority.authorized_task_cap(root, ledger)


def lifecycle(tmp_path):
    active = {'run_keys': ['run'], 'active_runs': 1, 'bindings': 2, 'bound_media_ids': [1, 2],
        'source_record_ids': [3, 4], 'duplicate_support_count': 0}
    empty = {'run_keys': [], 'active_runs': 0, 'bindings': 0, 'bound_media_ids': [],
        'source_record_ids': [], 'duplicate_support_count': 0}
    phases = [('copy_apply','apply',empty,active,False), ('copy_replay','apply',active,active,True),
        ('copy_rollback','rollback',active,empty,False),
        ('copy_repeated_rollback','rollback',empty,empty,True), ('copy_reapply','apply',empty,active,False)]
    recovery = {}; rows = []
    for i, (key, action, before, after, replay) in enumerate(phases):
        row = {'source_head': 'head', 'database': 'copy', 'system_identifier': 'pg', 'production': False,
            'scope_fingerprint': 'scope', 'action': action, 'operation_id': str(i),
            'started_at': f'2026-09-27T00:00:{i*2:02d}+00:00',
            'finished_at': f'2026-09-27T00:00:{i*2+1:02d}+00:00',
            'before': copy.deepcopy(before), 'after': copy.deepcopy(after), 'result': {'run_key': 'run',
            'applied': action == 'apply', 'rolled_back': action == 'rollback', 'idempotent_replay': replay}}
        recovery[key] = key+'.json'; rows.append(row)
        (tmp_path/recovery[key]).write_text(json.dumps(row))
    return recovery, rows


@pytest.mark.parametrize('defect', ['reuse_path', 'reuse_bytes', 'same_operation', 'out_of_order',
    'scope', 'run', 'state_gap', 'rollback_no_change', 'reapply_is_replay', 'none'])
def test_recovery_requires_distinct_ordered_state_transitions(tmp_path, defect):
    from scripts.check_production_pixiv_a2 import verify_recovery_lifecycle
    recovery, rows = lifecycle(tmp_path)
    if defect == 'reuse_path': recovery['copy_reapply'] = recovery['copy_apply']
    elif defect == 'reuse_bytes': rows[-1] = copy.deepcopy(rows[0])
    elif defect == 'same_operation': rows[-1]['operation_id'] = rows[0]['operation_id']
    elif defect == 'out_of_order': rows[-1]['started_at'] = rows[0]['started_at']
    elif defect == 'scope': rows[-1]['scope_fingerprint'] = 'other'
    elif defect == 'run': rows[-1]['result']['run_key'] = 'other'
    elif defect == 'state_gap': rows[-1]['before']['bindings'] = 5
    elif defect == 'rollback_no_change': rows[2]['after'] = copy.deepcopy(rows[2]['before'])
    elif defect == 'reapply_is_replay': rows[-1]['result']['idempotent_replay'] = True
    for key, row in zip(('copy_apply','copy_replay','copy_rollback','copy_repeated_rollback','copy_reapply'), rows):
        (tmp_path/(key+'.json')).write_text(json.dumps(row))
    args = dict(candidate='head', database='copy', system_identifier='pg', scope_fingerprint='scope')
    if defect == 'none': assert verify_recovery_lifecycle(tmp_path, recovery, **args)['operation_count'] == 5
    else:
        with pytest.raises(ValueError, match='recovery_'):
            verify_recovery_lifecycle(tmp_path, recovery, **args)
