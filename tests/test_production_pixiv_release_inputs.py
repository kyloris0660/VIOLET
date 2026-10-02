import copy
import json
import pytest

from app.services.production_pixiv_release_inputs import (
    verify_t0_scope, verify_full_input, semantic_input_identity, verify_semantic_manifest)
from app.services.production_pixiv_service import build_fixed_scope


def successful_contextual_facts(tmp_path):
    from test_production_pixiv_role_coverage import partial_facts
    value, vocabulary, facts, provider, budget = partial_facts(tmp_path, ['MysteryKnown', 'MysteryUnknown'])
    assert all(call['status'] == 'success' and call['business_valid'] is True
               for call in json.loads(budget.path.read_text())['calls'])
    return value, vocabulary, facts, provider, budget


def successful_role_facts(tmp_path):
    from test_production_pixiv_role_extraction import Provider, task_budget
    from test_production_pixiv_role_coverage import context
    from app.services.production_pixiv_semantics import build_semantic_vocabulary
    from app.services.production_pixiv_role_extraction import plan_role_extraction, extract_production_roles
    value = context(['MysteryName'])
    vocabulary = build_semantic_vocabulary([])
    units, _ = plan_role_extraction(value, vocabulary)
    provider = Provider()
    budget = task_budget(tmp_path, provider)
    facts = extract_production_roles(units, provider=provider, budget=budget, cache_dir=tmp_path / 'roles')
    return value, vocabulary, facts, provider, budget


@pytest.mark.parametrize('changed',['none','candidate','context','raw'])
def test_role_release_replays_original_response_and_full_context(tmp_path,changed):
    import json
    from test_production_pixiv_role_coverage import partial_facts
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value,vocabulary,facts,provider,budget=successful_contextual_facts(tmp_path)
    if changed=='candidate':next(iter(facts['completion_records'].values()))['candidates'][0]['confidence']=0.01
    elif changed=='context':
        from dataclasses import replace
        value=replace(value,signals=value.signals[:-1])
    elif changed=='raw':
        path=next((tmp_path/'roles'/'raw').glob('*.json'))
        raw=json.loads(path.read_text());raw['content']=json.dumps({'records':[]});path.write_text(json.dumps(raw))
    if changed=='none':
        result=verify_role_response_sources(value,vocabulary,facts,tmp_path/'roles',json.loads(budget.path.read_text()))
        assert result['record_count']==1 and result['new_provider_calls']==0
    else:
        with pytest.raises(ValueError):verify_role_response_sources(value,vocabulary,facts,tmp_path/'roles',json.loads(budget.path.read_text()))
    assert len(provider.calls)==1


def test_scope_recomputed_from_independent_t0_rejects_self_consistent_truncation():
    media=[{'id':1,'filename':'12345678_p0.jpg'},{'id':2,'filename':'22222222_p0.jpg'}]
    inventory={'media':media,'metadata':[], 'summary':{'identity':{'database':'prod','system_identifier':'system'},
        'media_total':2,'watermark':{'t0':'T0'}}}
    scope=build_fixed_scope(media,watermark='T0')
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    anchor={'schema_version':'violet.production-pixiv-t0-anchor.v1',
        'identity':inventory['summary']['identity'], 'watermark':inventory['summary']['watermark'],
        'media_count':2,'media_fingerprint':canonical_fingerprint(media),
        'metadata_fingerprint':canonical_fingerprint([]),'scope_fingerprint':scope['canonical_fingerprint']}
    verify_t0_scope(scope,inventory,'prod','system',anchor=anchor)
    shortened=build_fixed_scope(media[:1],watermark='T0')
    with pytest.raises(ValueError,match='t0_'):
        verify_t0_scope(shortened,inventory,'prod','system',anchor=anchor)
    reduced=copy.deepcopy(inventory);reduced['media']=media[:1];reduced['summary']['media_total']=1
    with pytest.raises(ValueError,match='t0_'):
        verify_t0_scope(shortened,reduced,'prod','system',anchor=anchor)


def test_legacy_raw_requires_matching_paid_attempt_even_without_reservation_field(tmp_path):
    import json
    from test_production_pixiv_role_coverage import partial_facts
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value,vocab,facts,provider,budget=successful_contextual_facts(tmp_path)
    for path in (tmp_path/'roles'/'raw').glob('*.json'):
        saved=json.loads(path.read_text());saved.pop('budget_response',None)
        path.write_text(json.dumps(saved),encoding='utf-8')
    ledger=json.loads(budget.path.read_text())
    assert verify_role_response_sources(value,vocab,facts,tmp_path/'roles',ledger)['new_provider_calls']==0
    with pytest.raises(ValueError,match='legacy_role_source_attempt_missing'):
        verify_role_response_sources(value,vocab,facts,tmp_path/'roles',{'calls':[]})
    assert len(provider.calls)==1


def test_release_replays_inherited_partial_answers_without_borrowing_other_questions(tmp_path):
    import json
    from test_production_pixiv_role_coverage import partial_facts,Responses,candidate
    from app.services.production_pixiv_role_extraction import repair_missing_role_coverage
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value,vocab,facts,_,budget=partial_facts(tmp_path,['MysteryKnown','MysteryMissing','MysteryUnknown','MysteryDescription'])
    first=Responses(lambda group:([candidate('MysteryKnown')],[
        {'raw_value':'MysteryDescription','disposition':'non_name','reason_code':'description'}]))
    facts=repair_missing_role_coverage(value,vocab,facts,provider=first,budget=budget,cache_dir=tmp_path/'roles',batch_size=1)
    second=Responses(lambda group:([candidate('MysteryMissing')],[]))
    facts=repair_missing_role_coverage(value,vocab,facts,provider=second,budget=budget,cache_dir=tmp_path/'roles',batch_size=1)
    inherited=[r for r in facts['coverage_repair_records'].values() if r.get('inherited_valid_response_keys')]
    assert inherited and len(first.calls)==len(second.calls)==1
    ledger=json.loads(budget.path.read_text())
    # Partial siblings remain in storage, but their invalid batches cannot
    # supply production-release evidence, even through inheritance.
    assert any(call['business_valid'] is False for call in ledger['calls'])
    retained = budget.path.read_bytes()
    with pytest.raises(ValueError,match='semantic_.*role_'):
        verify_role_response_sources(value,vocab,facts,tmp_path/'roles',ledger)
    assert budget.path.read_bytes() == retained


def test_full_live_input_rejects_subset_revision_change_and_page_mismatch():
    live=[{'work_id':'12345678','page_index':0,'disposition':'complete','revision':2},
          {'work_id':'22222222','page_index':1,'disposition':'complete','revision':3}]
    coverage={'items':[{'media_id':i,'work_id':r['work_id'],'page_index':r['page_index'],
        'disposition':'metadata_complete','source_record_ids':[i],'eligible_record_ids':[i]}
        for i,r in enumerate(live,1)]}
    verify_full_input(live,live,coverage)
    for changed in (live[:1], [{**live[0],'revision':1},live[1]], []):
        with pytest.raises(ValueError,match='snapshot_changed'):
            verify_full_input(changed,live,coverage)
    with pytest.raises(ValueError,match='work_page_media'):
        verify_full_input(live,live,{'items':coverage['items'][:1]})


def test_release_replays_legacy_v1_repair_without_changing_current_prompt(tmp_path,monkeypatch):
    import json
    from test_production_pixiv_role_coverage import partial_facts,Responses,candidate
    from app.services import production_pixiv_role_extraction as roles
    from app.services.production_pixiv_release_provenance import replay_role_request_messages,verify_role_response_sources
    from app.services.source_name_candidate_extraction_service import SourceCandidateInputGroup
    value,vocab,facts,_,budget=partial_facts(tmp_path,['MysteryKnown','MysteryMissing'])
    provider=Responses(lambda group:([candidate('MysteryMissing')],[]))
    current_adapter=roles._production_messages
    def legacy_adapter(messages):
        payload=json.loads(messages[1]['content'])
        if not any(r['data_origin']=='production_pixiv_role_coverage_repair_v1' for r in payload['records']):
            return current_adapter(messages)
        # The extraction request carries prompt records, not constructor data.
        groups=[SourceCandidateInputGroup(group_key=r['group_key'],provider=r['provider'],
            tags=tuple(r['tags']),data_origin=r['data_origin'],data_type_label=r['data_type_label'],
            source_work_id_present=r['source_work_id_present']) for r in payload['records']]
        return replay_role_request_messages(groups)
    with monkeypatch.context() as patch:
        patch.setattr(roles,'COVERAGE_REPAIR_ORIGIN','production_pixiv_role_coverage_repair_v1')
        patch.setattr(roles,'_production_messages',legacy_adapter)
        facts=roles.repair_missing_role_coverage(value,vocab,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles',batch_size=1)
    assert roles.COVERAGE_REPAIR_ORIGIN=='production_pixiv_role_coverage_repair_v2'
    # Replaying a historical prompt cannot rehabilitate a failed original
    # batch. Keep that paid response and reject it at the current release gate.
    retained = budget.path.read_bytes()
    with pytest.raises(ValueError,match='semantic_.*role_'):
        verify_role_response_sources(value,vocab,facts,tmp_path/'roles',json.loads(retained))
    assert budget.path.read_bytes()==retained and len(provider.calls)==1


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('outcome', ['failed', 'recovered_invalid'])
def test_role_release_rejects_actual_failed_or_recovered_invalid_call(tmp_path, monkeypatch, legacy, outcome):
    from app.services.source_concept_budget import AdjudicationBudget
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    if outcome == 'failed':
        settle = AdjudicationBudget.settle
        def settle_failed(self, reservation, usage, *, success):
            return settle(self, reservation, usage, success=False)
        monkeypatch.setattr(AdjudicationBudget, 'settle', settle_failed)
    value, vocabulary, facts, provider, budget = successful_role_facts(tmp_path)
    before = json.loads(budget.path.read_text())
    assert len(before['calls']) == len(provider.calls) == 1
    call = before['calls'][0]
    if outcome == 'recovered_invalid':
        assert call['status'] == 'success' and call['business_valid'] is True
        budget.recover_response(key=call['key'], usage=call['usage'], business_valid=False, reservation=call['id'])
    retained = budget.path.read_bytes()
    ledger = json.loads(retained)
    current = ledger['calls'][0]
    assert current['status'] == ('failed' if outcome == 'failed' else 'success')
    assert current['business_valid'] is False and current['charged_microusd'] == call['charged_microusd']
    if legacy:
        for path in (tmp_path / 'roles' / 'raw').glob('*.json'):
            raw = json.loads(path.read_text())
            raw.pop('budget_response', None)
            path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(ValueError, match='semantic_.*role_'):
        verify_role_response_sources(value, vocabulary, facts, tmp_path / 'roles', ledger)
    assert budget.path.read_bytes() == retained and len(provider.calls) == 1


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('validity', ['absent', True, False, None, 0, 1, 'true'])
def test_role_release_requires_exact_business_validity_with_legacy_absence(tmp_path, legacy, validity):
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value, vocabulary, facts, provider, budget = successful_role_facts(tmp_path)
    retained = budget.path.read_bytes()
    ledger = json.loads(retained)
    if validity == 'absent': ledger['calls'][0].pop('business_valid')
    else: ledger['calls'][0]['business_valid'] = validity
    if legacy:
        for path in (tmp_path / 'roles' / 'raw').glob('*.json'):
            saved = json.loads(path.read_text()); saved.pop('budget_response', None)
            path.write_text(json.dumps(saved), encoding='utf-8')
    if validity == 'absent' or validity is True:
        proof = verify_role_response_sources(value, vocabulary, facts, tmp_path / 'roles', ledger)
        assert proof['record_count'] == 1 and proof['new_provider_calls'] == 0
    else:
        with pytest.raises(ValueError, match='semantic_.*role_'):
            verify_role_response_sources(value, vocabulary, facts, tmp_path / 'roles', ledger)
    assert budget.path.read_bytes() == retained and len(provider.calls) == 1


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('status', ['reserved', 'failed', 'unknown'])
def test_role_release_never_accepts_a_non_success_call(tmp_path, legacy, status):
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value, vocabulary, facts, provider, budget = successful_role_facts(tmp_path)
    retained = budget.path.read_bytes(); ledger = json.loads(retained)
    ledger['calls'][0]['status'] = status
    if legacy:
        for path in (tmp_path / 'roles' / 'raw').glob('*.json'):
            saved = json.loads(path.read_text()); saved.pop('budget_response', None)
            path.write_text(json.dumps(saved), encoding='utf-8')
    with pytest.raises(ValueError, match='semantic_.*role_'):
        verify_role_response_sources(value, vocabulary, facts, tmp_path / 'roles', ledger)
    assert budget.path.read_bytes() == retained and len(provider.calls) == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_failed_role_history_cannot_supply_sources_or_poison_a_valid_response(tmp_path, legacy):
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value, vocabulary, facts, provider, budget = successful_role_facts(tmp_path)
    retained = budget.path.read_bytes(); ledger = json.loads(retained)
    valid = ledger['calls'][0]
    ledger['calls'].append({**valid, 'id': 'retained-failed', 'status': 'failed', 'business_valid': False})
    path = next((tmp_path / 'roles' / 'raw').glob('*.json'))
    saved = json.loads(path.read_text())
    failed = copy.deepcopy(saved)
    failed['budget_response']['reservation'] = 'retained-failed'
    attempts = path.parent / 'attempts'; attempts.mkdir(exist_ok=True)
    (attempts / 'retained-failed.json').write_text(json.dumps(failed), encoding='utf-8')
    if legacy:
        saved.pop('budget_response'); path.write_text(json.dumps(saved), encoding='utf-8')
    proof = verify_role_response_sources(value, vocabulary, facts, tmp_path / 'roles', ledger)
    assert proof['record_count'] == 1
    assert {ticket for row in proof['records'] for source in row['sources'] for ticket in source['attempts']} == {valid['id']}
    assert budget.path.read_bytes() == retained and len(provider.calls) == 1


def test_semantic_release_binds_current_sources_roles_context_versions_and_judgments():
    from app.services.production_pixiv_release_inputs import semantic_versions
    versions=semantic_versions()
    aggregates=[{'source_revision':4,'canonical_fingerprint':'aggregate'}]; vocabulary={'aliases':['test']}
    facts={'schema_version':versions['role_schema'], 'records':{'legacy':{'candidates':[],
        'schema_version':versions['role_schema'],'extractor_version':versions['extractor'],
        'prompt_version':versions['prompt'],'decision_schema':versions['extraction_schema'],'model':versions['model']}}}
    judgments=[{'decision':'needs_review','error_state':None}]
    from app.services.production_pixiv_qualification import load_qualification_authority
    facts['identity_qualification']=load_qualification_authority()
    manifest={'input_identity':semantic_input_identity(aggregates,vocabulary,facts,judgments),'candidate_head':'a'*40,
        'processing':{'remaining_missing_pair_count':0,'error_count':0,'selected_pair_count':1,'judgment_count':1}}
    # Compatible inherited results require a current completed processing
    # receipt, not invented local provider calls for accepted old caches.
    with pytest.raises(ValueError,match='original_source_replay_required'):
        verify_semantic_manifest(manifest,aggregates,vocabulary,facts,judgments,'a'*40)
    stale_pair_prompt=copy.deepcopy(manifest)
    stale_pair_prompt['input_identity']['versions']['pair_prompt']='source_concept_llm_pair_adjudication_v1'
    with pytest.raises(ValueError,match='input_version_or_candidate'):
        verify_semantic_manifest(stale_pair_prompt,aggregates,vocabulary,facts,judgments,'a'*40)
    for key in ('aggregates','role_facts','judgments','versions'):
        stale=copy.deepcopy(manifest);stale['input_identity'][key]='old'
        with pytest.raises(ValueError,match='input_version_or_candidate'):
            verify_semantic_manifest(stale,aggregates,vocabulary,facts,judgments,'a'*40)
    with pytest.raises(ValueError,match='role_artifact_required'):
        semantic_input_identity(aggregates,vocabulary,{},judgments)
    with pytest.raises(ValueError,match='input_version_or_candidate'):
        verify_semantic_manifest(manifest,aggregates,vocabulary,facts,judgments,'b'*40)
    manifest['processing']['remaining_missing_pair_count']=1
    with pytest.raises(ValueError,match='processing_incomplete'):
        verify_semantic_manifest(manifest,aggregates,vocabulary,facts,judgments,'a'*40)
