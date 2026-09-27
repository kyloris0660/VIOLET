"""Observed A2 publication gaps, exercised at the existing admission boundary."""
import copy
from dataclasses import replace
import pytest

from app.services.production_pixiv_release_inputs import verify_t0_scope, verify_full_input
from app.services.production_pixiv_service import build_fixed_scope
from scripts.production_pixiv_a2_evidence import recompute_quality
from test_production_pixiv_a2_evidence import quality_fixture


def test_self_consistent_reduced_t0_cannot_replace_independent_anchor():
    media = [{'id': 1, 'filename': '12345678_p0.jpg'}]
    inventory = {'media': media, 'metadata': [], 'summary': {
        'identity': {'database': 'prod', 'system_identifier': 'system'},
        'media_total': 1, 'watermark': {'t0': 'T0'}}}
    with pytest.raises(ValueError, match='t0_'):
        verify_t0_scope(build_fixed_scope(media, watermark='T0'), inventory, 'prod', 'system')


@pytest.mark.parametrize('state', ['metadata_pending', 'metadata_retryable',
    'provider_identity_mismatch', 'unverified_source', 'metadata_source_conflict'])
def test_same_incomplete_live_snapshot_must_not_authorize_replacement(state):
    live = [{'work_id': '12345678', 'page_index': 0, 'disposition': 'complete'}]
    coverage = {'items': [
        {'media_id': 1, 'work_id': '12345678', 'page_index': 0,
         'disposition': 'metadata_complete', 'eligible_record_ids': [11], 'source_record_ids': [11]},
        {'media_id': 2, 'work_id': '12345678', 'page_index': 0,
         'disposition': state, 'eligible_record_ids': [], 'source_record_ids': [12]}]}
    with pytest.raises(ValueError, match='fixed_media'):
        verify_full_input(live, copy.deepcopy(live), coverage)


@pytest.mark.parametrize('defect', ['merged', 'duplicate'])
def test_creator_attachment_cannot_invent_separation_or_duplicate_account(defect):
    quality = quality_fixture()[0]
    quality['projection_rows'] += [
        ['Twin', 'artist', None, 50, 20, 'w1'],
        ['Twin', 'artist', None, 50 if defect == 'merged' else 51, 21, 'w2']]
    quality['creator_projection_rows'] = [
        {'provider': 'pixiv', 'provider_creator_id': 'left', 'concept_id': 50, 'media_id': 20},
        {'provider': 'pixiv', 'provider_creator_id': 'right',
         'concept_id': 50 if defect == 'merged' else 51, 'media_id': 21}]
    family = {'query': 'Twin', 'expected_union_media_ids': [20, 21], 'creators': [
        {'provider_creator_id': 'left', 'expected_media_ids': [20]},
        {'provider_creator_id': 'right', 'expected_media_ids': [21]}]}
    accounts = [{'provider_creator_id': name, 'concept_ids': [cid],
        'bound_media_ids': [mid], 'missing_bound_media_ids': []}
        for name, cid, mid in [('left', 50, 20), ('right', 51, 21)]]
    if defect == 'duplicate': accounts.append(copy.deepcopy(accounts[0]))
    quality['cases'].append({'category': 'bare_name_distinct_creator_accounts', 'query': 'Twin',
        'expected_account_union_media_ids': [20, 21], 'accounts': accounts, 'passed': True})
    quality['queries']['"Twin"'] = {'ids': [20, 21], 'status_code': 200, 'total': 2}
    with pytest.raises(ValueError, match='creator|summary_disagrees'):
        recompute_quality(quality, {'identity_pairs': [{'names': ['a', 'b'], 'expected': 'must_link'}]},
                          creator_oracle={'selected_families': [family]})


@pytest.mark.parametrize('disposition', ['non_name', 'unknown'])
def test_explicit_correction_outcome_survives_parenthetical_heuristics(tmp_path, disposition):
    from app.services.production_pixiv_corrections import correction_units, signal_semantics
    from app.services.production_pixiv_role_extraction import ROLE_SCHEMA, extract_production_roles, role_target_coverage
    from app.services.production_pixiv_semantics import adapt_production_semantics, build_semantic_vocabulary
    from test_production_pixiv_role_coverage import context, Responses
    from test_production_pixiv_role_extraction import task_budget
    value = context(['Deity(World)']); vocabulary = build_semantic_vocabulary([])
    base = adapt_production_semantics(value, vocabulary, {'schema_version': ROLE_SCHEMA, 'records': {}})
    signal = base.signals[0]
    request = {'aggregate_fingerprint': 'aggregate-12345678', 'raw_targets': [signal.raw_value],
        'supersedes': {signal.raw_value: signal_semantics(signal)},
        'conflict_evidence': ['actual class-member identity conflict'], 'authorization': 'bounded owner task',
        'parenthetical_role_correction': {'authorization': 'bounded owner task',
            'retain_context_source': True, 'targets': [{
                'aggregate_fingerprint': 'aggregate-12345678', 'signal_key': signal.signal_key,
                'raw_value': signal.raw_value, 'parenthetical_base': signal.parenthetical_base,
                'parenthetical_context': signal.parenthetical_context,
                'supersedes': signal_semantics(signal)}]}}
    units, _ = correction_units(base, {}, [request])
    provider = Responses(lambda group: ([], [{'raw_value': signal.raw_value,
        'disposition': disposition, 'reason_code': 'class_or_uncertain'}]))
    result = extract_production_roles(units, provider=provider, budget=task_budget(tmp_path, provider),
                                      cache_dir=tmp_path/'roles')
    record = result['records'][units[0].extraction_key]
    assert role_target_coverage(units[0], record)['outcomes'][signal.raw_value]['disposition'] == disposition
    facts = {'schema_version': ROLE_SCHEMA, 'records': {}, 'semantic_corrections': [request],
             'correction_records': result['records']}
    corrected = adapt_production_semantics(value, vocabulary, facts).signals[0]
    assert corrected.role_hint == 'unknown'
    assert corrected.status == ('rejected' if disposition == 'non_name' else 'needs_review')
    assert corrected.parenthetical_context == signal.parenthetical_context
    assert corrected.parenthetical_base == signal.parenthetical_base
    assert len(provider.calls) == 1
    from app.services.source_concept_resolver_service import resolve_source_concepts, LLMAdjudicationConfig
    resolved=resolve_source_concepts((corrected,),run_id='bounded-parenthetical',
        llm_config=LLMAdjudicationConfig(enabled=False,max_calls=0))
    assert all(c.status!='active' for c in resolved.concepts)
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    import json
    assert verify_role_response_sources(value,vocabulary,facts,tmp_path/'roles',
        json.loads((tmp_path/'budget.json').read_text()))['record_count']==1


@pytest.mark.parametrize('defect',['absent_authority','different_signal','different_context',
    'different_aggregate','old_semantics','no_conflict','strong_role','artist','extra_target'])
def test_parenthetical_correction_admission_rejects_out_of_scope_or_protected_facts(defect):
    from app.services.production_pixiv_corrections import correction_units,signal_semantics
    from app.services.production_pixiv_role_extraction import ROLE_SCHEMA
    from app.services.production_pixiv_semantics import adapt_production_semantics,build_semantic_vocabulary
    from test_production_pixiv_role_coverage import context
    base=adapt_production_semantics(context(['Deity(World)']),build_semantic_vocabulary([]),
        {'schema_version':ROLE_SCHEMA,'records':{}})
    signal=base.signals[0]
    assert signal.parenthetical_context
    if defect=='strong_role':signal=replace(signal,evidence_payload={**signal.evidence_payload,'production_role_hint_evidence':['independent']})
    if defect=='artist':signal=replace(signal,role_hint='artist')
    base=replace(base,signals=(signal,))
    target={'aggregate_fingerprint':signal.evidence_payload['aggregate_fingerprint'],
        'signal_key':signal.signal_key,'raw_value':signal.raw_value,
        'parenthetical_base':signal.parenthetical_base,'parenthetical_context':signal.parenthetical_context,
        'supersedes':signal_semantics(signal)}
    request={'aggregate_fingerprint':target['aggregate_fingerprint'],'raw_targets':[signal.raw_value],
        'supersedes':{signal.raw_value:signal_semantics(signal)},'conflict_evidence':['observed conflict'],
        'authorization':'bounded owner task','parenthetical_role_correction':{
            'authorization':'bounded owner task','retain_context_source':True,'targets':[target]}}
    if defect=='absent_authority':request.pop('parenthetical_role_correction')
    elif defect=='different_signal':target['signal_key']='other'
    elif defect=='different_context':target['parenthetical_context']='other'
    elif defect=='different_aggregate':target['aggregate_fingerprint']='other'
    elif defect=='old_semantics':target['supersedes']={}
    elif defect=='no_conflict':request['conflict_evidence']=[]
    elif defect=='extra_target':request['parenthetical_role_correction']['targets'].append({**target,'raw_value':'Other'})
    with pytest.raises(ValueError,match='semantic_correction'):
        correction_units(base,{},[request])


@pytest.mark.parametrize('explicit',[False,True])
def test_negative_target_cannot_be_promoted_by_recovered_candidate_or_hide_model_conflict(explicit):
    from types import SimpleNamespace
    from app.services.production_pixiv_role_extraction import role_target_coverage,CORRECTION_ORIGIN
    unit=SimpleNamespace(raw_values=['Class(World)'],unit_group=SimpleNamespace(data_origin=CORRECTION_ORIGIN))
    record={'verdict':'name_candidate_found','candidates':[{'raw_value':'Class(World)',
        'candidate_role':'character','evidence_payload':{'llm_structured_extraction':explicit}}],
        'validated_response':{'target_dispositions':[{'raw_value':'Class(World)',
            'disposition':'non_name','reason_code':'generic_class'}]}}
    coverage=role_target_coverage(unit,record)
    if explicit:assert coverage['missing_raw_tags']==unit.raw_values
    else:assert coverage['outcomes']['Class(World)']['disposition']=='non_name'


@pytest.mark.parametrize('role', ['character', 'work_title'])
def test_parenthetical_reported_base_reaches_actual_correction_without_inventing_role(tmp_path, role):
    from app.services.production_pixiv_corrections import correction_units, signal_semantics
    from app.services.production_pixiv_role_extraction import ROLE_SCHEMA, extract_production_roles
    from app.services.production_pixiv_semantics import adapt_production_semantics, build_semantic_vocabulary
    from test_production_pixiv_role_coverage import context, Responses
    from test_production_pixiv_role_extraction import task_budget
    value = context(['Deity(World)'])
    vocabulary = build_semantic_vocabulary([])
    base = adapt_production_semantics(value, vocabulary, {'schema_version': ROLE_SCHEMA, 'records': {}})
    signal = base.signals[0]
    target = {'aggregate_fingerprint': signal.evidence_payload['aggregate_fingerprint'],
        'signal_key': signal.signal_key, 'raw_value': signal.raw_value,
        'parenthetical_base': signal.parenthetical_base, 'parenthetical_context': signal.parenthetical_context,
        'supersedes': signal_semantics(signal)}
    request = {'aggregate_fingerprint': target['aggregate_fingerprint'], 'raw_targets': [signal.raw_value],
        'supersedes': {signal.raw_value: signal_semantics(signal)}, 'conflict_evidence': ['observed conflict'],
        'authorization': 'bounded owner task', 'parenthetical_role_correction': {
            'authorization': 'bounded owner task', 'retain_context_source': True, 'targets': [target]}}
    units, _ = correction_units(base, {}, [request])
    candidate = {'raw_value': 'Deity', 'role': role, 'status': 'active_candidate', 'confidence': .8,
                 'source_field': 'normal_tag', 'extraction_action': 'parenthetical_split'}
    provider = Responses(lambda group: ([candidate], [{'raw_value': signal.raw_value,
        'disposition': 'candidate', 'reason_code': 'parenthetical_split'}]))
    budget = task_budget(tmp_path, provider)
    result = extract_production_roles(units, provider=provider, budget=budget, cache_dir=tmp_path/'roles')
    facts = {'schema_version': ROLE_SCHEMA, 'records': {}, 'semantic_corrections': [request],
             'correction_records': result['records']}
    corrected = adapt_production_semantics(value, vocabulary, facts).signals[0]
    assert corrected.role_hint == ('work' if role == 'work_title' else role)
    assert corrected.parenthetical_context == signal.parenthetical_context
    record = result['records'][units[0].extraction_key]
    answer = record['validated_response']['candidates'][0]
    assert answer['raw_value'] == signal.raw_value
    assert answer['production_original_extracted_span'] == 'Deity'
    assert len(provider.calls) == 1
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    import json
    assert verify_role_response_sources(value, vocabulary, facts, tmp_path/'roles',
        json.loads((tmp_path/'budget.json').read_text()))['record_count'] == 1


@pytest.mark.parametrize('defect', ['other_origin', 'bare_also_observed', 'two_contexts', 'wrong_context', 'no_disposition'])
def test_parenthetical_response_binding_does_not_guess_ambiguous_or_unrequested_source(defect):
    from types import SimpleNamespace
    from app.services.production_pixiv_role_extraction import _adapt_response_record, CORRECTION_ORIGIN
    tags = [{'raw_tag': 'Deity(World)'}]
    if defect == 'bare_also_observed': tags.append({'raw_tag': 'Deity'})
    if defect == 'two_contexts': tags.append({'raw_tag': 'Deity(Other)'})
    group = SimpleNamespace(tags=tags, data_origin='other' if defect == 'other_origin' else CORRECTION_ORIGIN)
    candidate = {'raw_value': 'Deity', 'role': 'character', 'extraction_action': 'parenthetical_split'}
    if defect == 'wrong_context': candidate['work_context'] = 'Other'
    row = {'candidates': [candidate], 'target_dispositions': [] if defect == 'no_disposition' else [
        {'raw_value': 'Deity(World)', 'disposition': 'candidate', 'reason_code': 'split'}]}
    assert _adapt_response_record(row, SimpleNamespace(unit_group=group))['candidates'][0]['raw_value'] == 'Deity'
