from dataclasses import make_dataclass, replace
import json

import pytest

from app.services.production_pixiv_role_extraction import (
    ROLE_SCHEMA, COVERAGE_REPAIR_ORIGIN, complete_contextual_production_roles,
    plan_role_coverage_repair, repair_missing_role_coverage, role_target_coverage,
)
from app.services.production_pixiv_semantics import adapt_production_semantics, build_semantic_vocabulary
from app.services.source_concept_budget import AdjudicationBudgetBlocked
from app.services.source_concept_resolver_service import build_source_concept_signal_drafts
from test_production_pixiv_role_extraction import Provider, task_budget
from test_production_pixiv_semantics import source


def context(names, work='12345678'):
    signals=build_source_concept_signal_drafts([replace(source(name,work),
        evidence_payload={'work_id':work,'page_index':0,'aggregate_fingerprint':'aggregate-'+work})
        for name in names])
    return make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'coverage-input-'+work)


def candidate(name,role='character'):
    return {'raw_value':name,'role':role,'confidence':0.9,
        'status':'needs_review' if role=='unknown_name_like' else 'active_candidate',
        'source_field':'source_tag_observation','extraction_action':'direct_name'}


class Responses(Provider):
    def __init__(self, answer):
        super().__init__();self.answer=answer

    async def complete_chat(self,messages,**kwargs):
        groups=json.loads(messages[1]['content'])['records'];self.calls.append(groups)
        self.last_usage={'prompt_tokens':100,'completion_tokens':100}
        records=[]
        for group in groups:
            names,dispositions=self.answer(group)
            records.append({'group_key':group['group_key'],'provider':'pixiv',
                'verdict':'multiple_candidates_found' if names else 'no_explicit_name',
                'candidates':names,'target_dispositions':dispositions,'rejected_summary':{}})
        return json.dumps({'records':records})


def partial_facts(tmp_path,names=None):
    value=context(names or ['MysteryKnown','MysteryMissing','MysteryUnknown'])
    vocabulary=build_semantic_vocabulary([])
    provider=Responses(lambda group:([candidate('MysteryKnown'),
        candidate('MysteryUnknown','unknown_name_like')],[]))
    budget=task_budget(tmp_path,provider)
    facts=complete_contextual_production_roles(value,vocabulary,{'schema_version':ROLE_SCHEMA,'records':{}},
        provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    return value,vocabulary,facts,provider,budget


def test_parsed_partial_record_does_not_claim_all_requested_names_answered(tmp_path):
    value,vocabulary,facts,provider,budget=partial_facts(tmp_path)
    summary=facts['completion_summary']
    assert summary['parsed_response_units']==1
    assert summary['units_with_unaccounted_targets']==1
    assert summary['unaccounted_requested_tag_occurrences']==1
    units,mapping,plan=plan_role_coverage_repair(value,vocabulary,facts)
    assert len(units)==1 and units[0].raw_values==('MysteryMissing',)
    assert {tag['raw_tag'] for tag in units[0].unit_group.tags}=={'MysteryKnown','MysteryMissing','MysteryUnknown'}
    assert units[0].unit_group.data_origin==COVERAGE_REPAIR_ORIGIN
    assert plan['requested_tag_occurrences']==1 and len(provider.calls)==1


def test_repair_preserves_answered_names_and_reuses_exact_cache_without_repay(tmp_path):
    value,vocabulary,facts,old_provider,budget=partial_facts(tmp_path)
    old_serialized=json.dumps(facts,sort_keys=True)
    def answer(group):
        assert json.loads(group['data_type_label'].split(': ',1)[1])==['MysteryMissing']
        # Even an unsolicited conflicting sibling must not replace the prior answer.
        return [candidate('MysteryMissing','work_title'),candidate('MysteryKnown','work_title')],[]
    provider=Responses(answer)
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,
        cache_dir=tmp_path/'roles')
    roles={row.raw_value:row.role_hint for row in adapt_production_semantics(value,vocabulary,result).signals}
    assert roles=={'MysteryKnown':'character','MysteryMissing':'work','MysteryUnknown':'unknown'}
    assert result['role_response_coverage']['counts']=={'candidate':3,'unaccounted':0}
    assert json.dumps(facts,sort_keys=True)==old_serialized
    assert len(provider.calls)==1 and budget.summary()['call_count']==2
    cached=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,
        cache_dir=tmp_path/'roles',batch_size=8)
    assert cached['coverage_repair_summary']['prior_valid_unit_cache_reused']==1 and len(provider.calls)==1
    assert cached['coverage_repair_summary']['selected_units']==0
    resumed=repair_missing_role_coverage(value,vocabulary,result,provider=provider,budget=budget,
        cache_dir=tmp_path/'roles')
    assert resumed['coverage_repair_summary']['selected_units']==0 and len(provider.calls)==1
    # The same raw spelling in a different artwork cannot inherit this repair.
    other=context(['MysteryMissing'],'87654321')
    assert adapt_production_semantics(other,vocabulary,result).signals[0].role_hint=='unknown'


def test_explicit_unknown_and_non_name_are_accounted_without_identity_invention(tmp_path):
    value,vocabulary,facts,_,budget=partial_facts(tmp_path,
        ['MysteryKnown','MysteryMissing','MysteryUnknown','MysteryDescription'])
    def answer(group):
        requested=json.loads(group['data_type_label'].split(': ',1)[1])
        assert set(requested)=={'MysteryMissing','MysteryDescription'}
        return [],[{'raw_value':raw,'disposition':'unknown' if raw=='MysteryMissing' else 'non_name',
                    'reason_code':'insufficient_identity' if raw=='MysteryMissing' else 'descriptive_term'} for raw in requested]
    provider=Responses(answer)
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,
        cache_dir=tmp_path/'roles')
    by_name={row.raw_value:row for row in adapt_production_semantics(value,vocabulary,result).signals}
    assert by_name['MysteryMissing'].role_hint=='unknown'
    assert by_name['MysteryDescription'].status=='rejected'
    assert by_name['MysteryKnown'].role_hint=='character'
    assert result['role_response_coverage']['counts']=={'candidate':2,'non_name':1,'unknown':1,'unaccounted':0}


def test_repair_cache_shortcut_settles_saved_response_before_replanning(tmp_path,monkeypatch):
    value,vocabulary,facts,_,budget=partial_facts(tmp_path)
    provider=Responses(lambda group:([candidate('MysteryMissing')],[]))
    real_settle=budget.settle
    monkeypatch.setattr(budget,'settle',lambda *args,**kwargs:False)
    repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert any(row['status']=='reserved' for row in json.loads(budget.path.read_text())['calls'])
    monkeypatch.setattr(budget,'settle',real_settle)
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert result['coverage_repair_summary']['selected_units']==0 and len(provider.calls)==1
    assert not any(row['status']=='reserved' for row in json.loads(budget.path.read_text())['calls'])
    charge=budget.summary()['charged_or_reserved_usd']
    repair_missing_role_coverage(value,vocabulary,result,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert budget.summary()['charged_or_reserved_usd']==charge and len(provider.calls)==1


def test_existing_compatible_non_name_answer_accounts_original_target_without_call(tmp_path):
    from app.services.production_pixiv_role_extraction import plan_role_extraction,extract_production_roles
    value,vocabulary,facts,_,budget=partial_facts(tmp_path)
    negative=Responses(lambda group:([],[]))
    units=plan_role_extraction(context(['MysteryMissing']),vocabulary)[0]
    single=extract_production_roles(units,provider=negative,budget=budget,cache_dir=tmp_path/'single')
    facts={**facts,'records':single['records']}
    provider=Responses(lambda group:pytest.fail('compatible non-name must not be paid again'))
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert result['role_response_coverage']['requested_tag_occurrences']==3
    assert result['role_response_coverage']['counts']=={'candidate':2,'non_name':1,'unaccounted':0}
    answer=result['role_reused_target_answers']['aggregate-12345678']['MysteryMissing']
    assert answer['response_fingerprint'] and answer['new_provider_calls']==0
    assert answer['original_context_response_claimed_complete'] is False
    from app.services.production_pixiv_release_inputs import verify_role_completion
    from unittest.mock import patch
    with patch('app.services.production_pixiv_service.production_consumer',return_value=value):
        assert verify_role_completion([],vocabulary,result,json.loads(budget.path.read_text()))==result['role_response_coverage']
        with pytest.raises(ValueError,match='role_target_processing_incomplete'):
            verify_role_completion([],vocabulary,facts,json.loads(budget.path.read_text()))


def test_still_partial_repair_requests_only_missing_targets_and_retains_answers(tmp_path):
    value,vocabulary,facts,_,budget=partial_facts(tmp_path,
        ['MysteryKnown','MysteryMissing','MysteryUnknown','MysteryOther'])
    provider=Responses(lambda group:([candidate('MysteryMissing')],[]))
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,
        cache_dir=tmp_path/'roles')
    assert result['role_response_coverage']['counts']['unaccounted']==1
    assert result['role_response_coverage']['unaccounted_targets'][0]['raw_tags']==['MysteryOther']
    assert result['role_response_coverage']['unaccounted_targets'][0]['repair_attempted'] is True
    assert plan_role_coverage_repair(value,vocabulary,result)[0][0].raw_values==('MysteryOther',)
    remaining=Responses(lambda group:([candidate('MysteryOther')],[]))
    resumed=repair_missing_role_coverage(value,vocabulary,result,provider=remaining,budget=budget,
        cache_dir=tmp_path/'roles')
    assert resumed['role_response_coverage']['counts']['unaccounted']==0 and len(provider.calls)==1
    assert len(remaining.calls)==1
    assert json.loads(remaining.calls[0][0]['data_type_label'].split(': ',1)[1])==['MysteryOther']
    roles={row.raw_value:row.role_hint for row in adapt_production_semantics(value,vocabulary,resumed).signals}
    assert roles['MysteryMissing']==roles['MysteryOther']=='character'
    assert roles['MysteryUnknown']=='unknown'
    assert not plan_role_coverage_repair(value,vocabulary,resumed)[0]


def test_cached_unsolicited_sibling_cannot_prevent_the_missing_target_retry(tmp_path):
    value,vocabulary,facts,_,budget=partial_facts(tmp_path)
    partial=Responses(lambda group:([candidate('MysteryKnown')],[]))
    first=repair_missing_role_coverage(value,vocabulary,facts,provider=partial,budget=budget,cache_dir=tmp_path/'roles')
    assert first['role_response_coverage']['counts']['unaccounted']==1
    raw_before={str(p):p.read_bytes() for p in (tmp_path/'roles/raw').rglob('*.json')}
    provider=Responses(lambda group:([candidate('MysteryMissing')],[]))
    result=repair_missing_role_coverage(value,vocabulary,first,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    # The original completion and invalid repair are the first two attempts;
    # one final bounded repair closes the actual missing target.
    assert len(provider.calls)==1 and result['role_response_coverage']['counts']['unaccounted']==0
    assert all(__import__('pathlib').Path(p).read_bytes()==b for p,b in raw_before.items())


def test_invalid_candidate_sibling_is_preserved_and_only_invalid_target_is_repaired(tmp_path):
    value=context(['MysteryGood','MysteryBad']);vocab=build_semantic_vocabulary([])
    provider=Responses(lambda group:([candidate('MysteryGood'),candidate('MysteryBad','invented_role')],[]))
    budget=task_budget(tmp_path,provider)
    facts=complete_contextual_production_roles(value,vocab,{'schema_version':ROLE_SCHEMA,'records':{}},
        provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert len(facts['completion_records'])==1
    units,_,_=plan_role_coverage_repair(value,vocab,facts)
    assert units[0].raw_values==('MysteryBad',)
    old_raw={p.name:p.read_bytes() for p in (tmp_path/'roles/raw').glob('*.json')}
    repaired=repair_missing_role_coverage(value,vocab,facts,provider=Responses(
        lambda group:([candidate('MysteryBad')],[])),budget=budget,cache_dir=tmp_path/'roles')
    assert repaired['role_response_coverage']['counts']['unaccounted']==0
    assert all((tmp_path/'roles/raw'/name).read_bytes()==data for name,data in old_raw.items())
    ledger=json.loads(budget.path.read_text())
    assert ledger['calls'][0]['business_valid'] is False
    assert budget.summary()['charged_or_reserved_usd']>0


def test_compound_tag_keeps_reported_character_prefix_and_does_not_promote_outfit_suffix(tmp_path):
    value=context(['FictionalHero:EveningOutfit','pose-description'])
    vocabulary=build_semantic_vocabulary([])
    provider=Responses(lambda group:([
        {**candidate('FictionalHero'),'source_field':'provider_tag'},
        {**candidate('EveningOutfit','work_title'),'source_field':'provider_tag','extraction_action':'parenthetical_split'}],
        [{'raw_value':'pose-description','disposition':'non_name','reason_code':'descriptive'}]))
    budget=task_budget(tmp_path,provider)
    facts=complete_contextual_production_roles(value,vocabulary,{'schema_version':ROLE_SCHEMA,'records':{}},
        provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    record=next(iter(facts['completion_records'].values()))
    assert any(c['raw_value']=='FictionalHero:EveningOutfit' and c['candidate_role']=='character' for c in record['candidates'])
    assert not any(c['candidate_role']=='work_title' for c in record['candidates'])
    assert plan_role_coverage_repair(value,vocabulary,facts)[0]==[]
    assert json.loads(budget.path.read_text())['calls'][0]['business_valid'] is False


def test_nested_target_candidate_replays_without_invented_confidence_or_new_call(tmp_path):
    from app.services.production_pixiv_role_extraction import _adapt_response_record, _read_unit_cache, _unit_path
    value,vocabulary,facts,_,budget=partial_facts(tmp_path)
    units,_,_=plan_role_coverage_repair(value,vocabulary,facts)
    unit=units[0]
    provider=Responses(lambda group:([candidate('UnrequestedSibling')],[{
        'raw_value':'MysteryMissing','disposition':'candidate','reason_code':'structured_character_tag',
        'candidate':{'raw_value':'MysteryMissing','display_name':'MysteryMissing',
            'normalized_value':'MysteryMissing','role':'character','status':'active_candidate'}}]))
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    record=next(iter(result['coverage_repair_records'].values()))
    nested=next(c for c in record['candidates'] if c['raw_value']=='MysteryMissing')
    assert nested['candidate_role']=='character' and nested['candidate_status']=='needs_review'
    assert nested['confidence']==0.0
    assert nested['evidence_payload']['candidate_status_guard']['candidate_confidence_invalid_downgraded']
    before=budget.summary()
    cached,_=_read_unit_cache(_unit_path(tmp_path/'roles',unit),unit,'gpt-4.1-mini')
    assert role_target_coverage(unit,cached)['fully_accounted']
    assert _adapt_response_record(cached['validated_response'],unit)==cached['validated_response']
    assert budget.summary()==before and len(provider.calls)==1


@pytest.mark.parametrize('disposition,nested_raw,duplicate',[
    ('candidate','AnotherTag',False),('non_name','MysteryMissing',False),('candidate','MysteryMissing',True)])
def test_nested_candidate_cannot_escape_literal_target_or_contradict_disposition(tmp_path,disposition,nested_raw,duplicate):
    from app.services.production_pixiv_role_extraction import _adapt_response_record
    value,vocabulary,facts,_,_=partial_facts(tmp_path)
    unit=plan_role_coverage_repair(value,vocabulary,facts)[0][0]
    answer={'raw_value':'MysteryMissing','disposition':disposition,'candidate':candidate(nested_raw)}
    rows=[answer,*([{'raw_value':'MysteryMissing','disposition':'unknown','reason_code':'uncertain'}] if duplicate else [])]
    adapted=_adapt_response_record({'candidates':[],'target_dispositions':rows},unit)
    assert adapted['candidates']==[]


def test_invalid_nested_candidate_does_not_poison_valid_cached_siblings(tmp_path):
    from app.services.production_pixiv_role_extraction import _revalidate_cached_response
    value,vocabulary,facts,_,_=partial_facts(tmp_path)
    unit=plan_role_coverage_repair(value,vocabulary,facts)[0][0]
    previous={'verdict':'multiple_candidates_found','candidates':[candidate('MysteryKnown')],
        'validated_response':{'group_key':unit.unit_group.group_key,'provider':'pixiv',
            'verdict':'multiple_candidates_found','candidates':[candidate('MysteryKnown')],
            'rejected_summary':{},'target_dispositions':[{'raw_value':'MysteryMissing','disposition':'candidate',
                'candidate':candidate('MysteryMissing','invalid-role')}]}}
    assert _revalidate_cached_response(previous,unit)==previous


def test_exhausted_target_does_not_stop_other_missing_targets_or_reset_attempts(tmp_path):
    from dataclasses import replace
    from app.services.production_pixiv_role_extraction import BudgetedExtractionProvider
    value,vocabulary,facts,_,budget=partial_facts(tmp_path,
        ['MysteryKnown','MysteryMissing','MysteryUnknown','MysteryOther'])
    unit=plan_role_coverage_repair(value,vocabulary,facts)[0][0]
    group=replace(unit.unit_group,data_type_label='Requested unresolved raw tags: '+json.dumps(['MysteryMissing']))
    keys=BudgetedExtractionProvider.logical_keys([group])
    # The original partial request already charged one logical attempt.
    for i in range(2):
        ticket=budget.reserve('failed-'+str(i),[],max_output_tokens=100,logical_keys=keys)
        budget.settle(ticket,{},success=False)
    provider=Responses(lambda group:([candidate('MysteryOther')],[]))
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert len(provider.calls)==1
    assert json.loads(provider.calls[0][0]['data_type_label'].split(': ',1)[1])==['MysteryOther']
    assert result['role_response_coverage']['counts']['attempt_limit_reached']==1
    assert result['role_response_coverage']['counts']['unaccounted']==0
    assert result['role_response_coverage']['requested_tag_occurrences']==4
    assert result['role_terminal_targets']['aggregate-12345678']['MysteryMissing']['identity_confirmed'] is False


def test_target_accounting_rejects_aggregate_counts_duplicates_and_unsupported_claims(tmp_path):
    value,vocabulary,facts,_,_=partial_facts(tmp_path)
    unit=plan_role_coverage_repair(value,vocabulary,facts)[0][0]
    record={'candidates':[],'validated_response':{'rejected_summary':{'descriptive_general_count':1},
        'target_dispositions':[{'raw_value':'Absent','disposition':'non_name','reason_code':'not_a_name'},
            {'raw_value':'MysteryMissing','disposition':'candidate','reason_code':'claimed_without_candidate'}]}}
    assert role_target_coverage(unit,record)['missing_raw_tags']==['MysteryMissing']
    record['validated_response']['target_dispositions']=[
        {'raw_value':'MysteryMissing','disposition':'unknown','reason_code':'uncertain'},
        {'raw_value':'MysteryMissing','disposition':'non_name','reason_code':'contradiction'}]
    record['verdict']='no_explicit_name'
    assert role_target_coverage(unit,record)['missing_raw_tags']==['MysteryMissing']


@pytest.mark.parametrize('verdict',['no_explicit_name','rejected_general_only','rejected_popularity_or_meta_only'])
def test_whole_group_non_name_answer_is_already_accounted_and_never_reasked(tmp_path,verdict):
    value,vocabulary,facts,_,_=partial_facts(tmp_path)
    parent=next(iter(facts['completion_records']))
    facts['completion_records'][parent].update(candidates=[],verdict=verdict)
    assert plan_role_coverage_repair(value,vocabulary,facts)[0]==[]


def test_repair_requires_exact_original_question_identity(tmp_path):
    value,vocabulary,facts,_,_=partial_facts(tmp_path)
    facts['completion_by_aggregate']['aggregate-12345678']='different-original-question'
    with pytest.raises(ValueError,match='original_role_question_reconstruction_changed'):
        plan_role_coverage_repair(value,vocabulary,facts)


def test_already_grounded_context_does_not_require_a_new_or_invented_old_question(tmp_path):
    from app.services.production_pixiv_role_extraction import summarize_role_response_coverage
    value,vocabulary,facts,provider,_=partial_facts(tmp_path)
    template=next(iter(facts['completion_records'].values()))['candidates'][0]
    facts['context_by_aggregate']={'aggregate-12345678':'resolved-context'}
    facts['context_records']={'resolved-context':{'candidates':[
        {**template,'raw_value':signal.raw_value,'display_name':signal.raw_value,
            'normalized_value':signal.raw_value,'canonical_key':signal.raw_value.casefold(),
            'candidate_role':'character'} for signal in value.signals]}}
    units,_,plan=plan_role_coverage_repair(value,vocabulary,facts)
    assert not units and plan['currently_grounded_aggregates_without_new_question']==1
    coverage=summarize_role_response_coverage(value,vocabulary,facts)
    assert coverage['currently_grounded_aggregates_without_new_question']==1
    assert coverage['requested_tag_occurrences']==0 and len(provider.calls)==1


def test_shared_budget_block_preserves_original_answers_and_missing_target_accounting(tmp_path,monkeypatch):
    value,vocabulary,facts,_,budget=partial_facts(tmp_path)
    provider=Responses(lambda group:([candidate('MysteryMissing')],[]))
    def blocked(*args,**kwargs):raise AdjudicationBudgetBlocked('shared_cap_reached')
    monkeypatch.setattr(budget,'reserve',blocked)
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=provider,budget=budget,
        cache_dir=tmp_path/'roles')
    assert not provider.calls and result['completion_records']==facts['completion_records']
    assert result['coverage_repair_summary']['blocked']=='shared_cap_reached'
    assert result['role_response_coverage']['counts']['unaccounted']==1


def test_bounded_repair_prioritizes_current_missing_names_without_dropping_other_questions(tmp_path):
    lower=context(['MysteryKnown','MysteryLow'],'12345678')
    higher=context(['MysteryKnown','MysteryHighA','MysteryHighB','MysteryHighC'],'87654321')
    value=replace(lower,signals=tuple(lower.signals)+tuple(higher.signals))
    vocabulary=build_semantic_vocabulary([])
    provider=Responses(lambda group:([candidate('MysteryKnown')],[]))
    budget=task_budget(tmp_path,provider)
    facts=complete_contextual_production_roles(value,vocabulary,{'schema_version':ROLE_SCHEMA,'records':{}},
        provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    units,_,plan=plan_role_coverage_repair(value,vocabulary,facts)
    assert len(units)==2 and plan['requested_tag_occurrences']==4
    assert set(units[0].raw_values)=={'MysteryHighA','MysteryHighB','MysteryHighC'}
    repair_provider=Responses(lambda group:([candidate(raw) for raw in
        json.loads(group['data_type_label'].split(': ',1)[1])],[]))
    result=repair_missing_role_coverage(value,vocabulary,facts,provider=repair_provider,budget=budget,
        cache_dir=tmp_path/'roles',unit_limit=1)
    assert result['role_response_coverage']['counts']['unaccounted']==1
    remaining,_,remaining_plan=plan_role_coverage_repair(value,vocabulary,result)
    assert remaining[0].raw_values==('MysteryLow',) and remaining_plan['already_attempted_aggregates']==1


def test_repair_wire_contract_removes_conflicting_output_restriction_and_preserves_old_prompt(tmp_path):
    from app.services.production_pixiv_role_extraction import _production_messages,COMPLETION_ORIGIN,COMPLETION_PROMPT
    from app.services.source_name_candidate_extraction_service import extraction_messages
    value,vocabulary,facts,_,_=partial_facts(tmp_path)
    unit=plan_role_coverage_repair(value,vocabulary,facts)[0][0]
    messages=_production_messages(extraction_messages([unit.unit_group]))
    assert 'Only include ambiguous_items or error_code when needed.' not in messages[0]['content']
    payload=json.loads(messages[1]['content'])
    assert payload['records'][0]['requested_raw_tags']==['MysteryMissing']
    assert 'target_dispositions' in payload['required_record_fields']
    assert _production_messages(messages)==messages
    old=extraction_messages([replace(unit.unit_group,data_origin=COMPLETION_ORIGIN)])
    adapted=_production_messages(old)
    assert adapted[0]['content']==old[0]['content']+'\n'+COMPLETION_PROMPT
    assert 'required_record_fields' not in json.loads(adapted[1]['content'])
