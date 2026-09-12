from dataclasses import make_dataclass,replace
import json
import pytest

from app.services.production_pixiv_role_extraction import plan_role_extraction,extract_production_roles
from app.services.production_pixiv_semantics import build_semantic_vocabulary,adapt_production_semantics
from app.services.source_concept_budget import AdjudicationBudget
from app.services.source_concept_resolver_service import build_source_concept_signal_drafts
from test_production_pixiv_semantics import source


class Provider:
    model='gpt-4.1-mini'
    def __init__(self):self.calls=[];self.last_usage={}
    def is_available(self):return True
    def get_provider_name(self):return 'test-primary'
    async def complete_chat(self,messages,**kwargs):
        groups=json.loads(messages[1]['content'])['records'];self.calls.append(groups)
        self.last_usage={'prompt_tokens':100,'completion_tokens':100}
        return json.dumps({'records':[{'group_key':group['group_key'],'provider':'pixiv','verdict':'name_candidate_found',
            'candidates':[{'raw_value':group['tags'][0]['raw_tag'],'display_name':group['tags'][0]['raw_tag'],
                'normalized_value':group['tags'][0]['raw_tag'],'role':'character','status':'active_candidate','confidence':0.9,
                'source_field':'pixiv_tag','extraction_action':'direct_name'}],'rejected_summary':{}} for group in groups]})


def consumer(count):
    signals=build_source_concept_signal_drafts([source('MysteryName',str(12345678+i)) for i in range(count)])
    return make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'test-input')


def test_role_extraction_reuses_units_across_changed_occurrence_counts(tmp_path):
    vocabulary=build_semantic_vocabulary([])
    first=consumer(2);units,plan=plan_role_extraction(first,vocabulary)
    assert len(units)==1 and plan['raw_string_occurrences_total']==2
    provider=Provider()
    budget=AdjudicationBudget(tmp_path/'budget.json',model=provider.model,cap_usd=10,input_per_million=0.4,output_per_million=1.6)
    facts=extract_production_roles(units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert facts['summary']['completed_units']==1 and len(provider.calls)==1
    next_units,_=plan_role_extraction(consumer(3),vocabulary)
    resumed=extract_production_roles(next_units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert resumed['summary']['cache_hits']==1 and len(provider.calls)==1
    assert budget.summary()['call_count']==1
    adapted=adapt_production_semantics(first,vocabulary,facts)
    assert all(signal.role_hint=='character' for signal in adapted.signals)
    assert all(signal.evidence_payload['production_role_extraction']['identity_equivalence_authorized'] is False for signal in adapted.signals)


def multiple_units():
    signals=build_source_concept_signal_drafts([source(name,str(12345678+i))
        for i,name in enumerate(('MysteryAlpha','MysteryBeta','MysteryGamma'))])
    value=make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'test-input')
    return plan_role_extraction(value,build_semantic_vocabulary([]))[0]


def task_budget(tmp_path,provider):
    return AdjudicationBudget(tmp_path/'budget.json',model=provider.model,cap_usd=10,input_per_million=0.4,output_per_million=1.6)


def test_role_raw_saved_before_settlement_resumes_without_repay(tmp_path,monkeypatch):
    import asyncio
    from app.services.production_pixiv_role_extraction import BudgetedExtractionProvider
    from app.services.source_name_candidate_extraction_service import extraction_messages
    provider=Provider();budget=task_budget(tmp_path,provider);units=multiple_units()[:1]
    wrapped=BudgetedExtractionProvider(provider,budget,tmp_path/'roles',units)
    settle=budget.settle
    def crash(*args,**kwargs):raise SystemExit('crash after raw and unit persistence')
    monkeypatch.setattr(budget,'settle',crash)
    with pytest.raises(SystemExit):asyncio.run(wrapped.complete_chat(extraction_messages([units[0].unit_group])))
    assert json.loads(budget.path.read_text())['calls'][0]['status']=='reserved'
    monkeypatch.setattr(budget,'settle',settle)
    resumed=extract_production_roles(units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert resumed['summary']['completed_units']==1 and len(provider.calls)==1
    assert budget.summary()['charged_or_reserved_usd']==0.0002
    extract_production_roles(units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert budget.summary()['call_count']==1


def test_paid_invalid_role_retries_only_invalid_unit_and_keeps_raw(tmp_path):
    import asyncio
    from app.services.production_pixiv_role_extraction import BudgetedExtractionProvider
    from app.services.source_name_candidate_extraction_service import extraction_messages
    class Partial(Provider):
        async def complete_chat(self,messages,**kwargs):
            answer=json.loads(await super().complete_chat(messages,**kwargs))
            if len(self.calls)==1:answer['records'][-1]['candidates'][0]['role']='invalid-role'
            return json.dumps(answer)
    provider=Partial();budget=task_budget(tmp_path,provider);units=multiple_units()[:2]
    wrapped=BudgetedExtractionProvider(provider,budget,tmp_path/'roles',units)
    messages=extraction_messages([u.unit_group for u in units])
    asyncio.run(wrapped.complete_chat(messages))
    first_raw=next((tmp_path/'roles'/'raw').glob('*.json'));original=first_raw.read_bytes()
    answer=json.loads(asyncio.run(wrapped.complete_chat(messages)))
    assert len(answer['records'])==2 and len(provider.calls)==2
    assert len(provider.calls[1])==1
    assert provider.calls[1][0]['group_key']==provider.calls[0][-1]['group_key']
    assert first_raw.read_bytes()==original
    rows=json.loads(budget.path.read_text())['calls']
    assert [r['business_valid'] for r in rows]==[False,True]
    assert budget.summary()['charged_or_reserved_usd']==0.0004


def test_two_text_workers_overlap_with_independent_usage_and_resume_without_repay(tmp_path):
    from threading import Barrier
    barrier=Barrier(2);providers=[]
    class Overlap(Provider):
        async def complete_chat(self,messages,**kwargs):
            if not self.calls:barrier.wait(timeout=5)
            return await super().complete_chat(messages,**kwargs)
    def factory():
        provider=Overlap();providers.append(provider);return provider
    baseline=Provider();budget=task_budget(tmp_path,baseline)
    facts=extract_production_roles(multiple_units(),provider=baseline,provider_factory=factory,workers=2,
        budget=budget,cache_dir=tmp_path/'roles',batch_size=1)
    assert facts['summary']['completed_units']==3 and facts['summary']['text_workers']==2
    assert sorted(len(provider.calls) for provider in providers)==[1,2]
    assert budget.summary()['call_count']==3 and budget.summary()['charged_or_reserved_usd']==0.0006
    resumed=extract_production_roles(list(reversed(multiple_units())),provider=baseline,provider_factory=factory,
        workers=2,budget=budget,cache_dir=tmp_path/'roles',batch_size=2)
    assert resumed['records']==facts['records'] and resumed['summary']['cache_hits']==3
    assert budget.summary()['call_count']==3


def test_one_worker_rate_limit_pauses_both_and_preserves_other_inflight_success(tmp_path):
    import asyncio
    from threading import Barrier
    from app.services.llm_translation_provider import LLMHTTPStatusError
    barrier=Barrier(2);providers=[]
    class Limited(Provider):
        async def complete_chat(self,messages,**kwargs):
            barrier.wait(timeout=5)
            self.calls.append(messages)
            raise LLMHTTPStatusError(429,'rate limited')
    class Successful(Provider):
        async def complete_chat(self,messages,**kwargs):
            barrier.wait(timeout=5)
            await asyncio.sleep(0.1)
            return await super().complete_chat(messages,**kwargs)
    def factory():
        provider=Limited() if not providers else Successful();providers.append(provider);return provider
    baseline=Provider();budget=task_budget(tmp_path,baseline)
    facts=extract_production_roles(multiple_units(),provider=baseline,provider_factory=factory,workers=2,
        budget=budget,cache_dir=tmp_path/'roles',batch_size=1)
    assert facts['summary']['blocked']=='role_provider_authentication_or_rate_limit'
    assert facts['summary']['completed_units']==1 and facts['summary']['remaining_units']==2
    assert sum(len(provider.calls) for provider in providers)==2
    assert budget.summary()['unknown_usage_count']==1 and budget.summary()['success_count']==1


def test_parallel_text_workers_reject_shared_mutable_provider(tmp_path):
    provider=Provider()
    with pytest.raises(ValueError,match='shared_mutable_provider_forbidden'):
        extract_production_roles(multiple_units(),provider=provider,provider_factory=lambda:provider,workers=2,
            budget=task_budget(tmp_path,provider),cache_dir=tmp_path/'roles')
    assert not provider.calls


def test_contextual_display_spelling_reuses_role_only_inside_its_metadata_group(tmp_path):
    from app.services.production_pixiv_role_extraction import extract_contextual_production_roles
    from app.services.source_concept_resolver_service import resolve_source_concepts
    class DisplaySpelling(Provider):
        async def complete_chat(self,messages,**kwargs):
            payload=json.loads(await super().complete_chat(messages,**kwargs))
            for row in payload['records']:
                row['candidates'][0].update(raw_value='MysteryName',display_name='ミステリ',normalized_value='ミステリ')
            return json.dumps(payload,ensure_ascii=False)
    def scoped(aggregate):
        inputs=[replace(source(name,'12345678'),evidence_payload={'work_id':'12345678','page_index':0,
            'aggregate_fingerprint':aggregate}) for name in ('MysteryName','ミステリ')]
        signals=build_source_concept_signal_drafts(inputs)
        return make_dataclass('ScopedConsumer',['signals','input_fingerprint'],frozen=True)(signals,'scoped')
    provider=DisplaySpelling();vocabulary=build_semantic_vocabulary([])
    facts=extract_contextual_production_roles(scoped('observed'),vocabulary,
        {'schema_version':'violet.production-pixiv-role-result.v1','records':{}},provider=provider,
        budget=task_budget(tmp_path,provider),cache_dir=tmp_path/'roles')
    adapted=adapt_production_semantics(scoped('observed'),vocabulary,facts)
    assert {signal.role_hint for signal in adapted.signals}=={'character'}
    assert len(resolve_source_concepts(adapted.signals,run_id='display-role-not-identity').concepts)==2
    unrelated=adapt_production_semantics(scoped('unrelated'),vocabulary,facts)
    assert {signal.role_hint for signal in unrelated.signals}=={'unknown'}


def test_partial_invalid_batch_never_repays_already_valid_units(tmp_path):
    class Partial(Provider):
        async def complete_chat(self,messages,**kwargs):
            content=await super().complete_chat(messages,**kwargs)
            if len(self.calls)==1:
                payload=json.loads(content)
                payload['records'][-1].pop('verdict')
                return json.dumps(payload)
            return content
    provider=Partial();units=multiple_units();budget=task_budget(tmp_path,provider)
    facts=extract_production_roles(units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert facts['summary']['completed_units']==3
    assert [len(call) for call in provider.calls]==[3,1]
    resumed=extract_production_roles(list(reversed(units)),provider=provider,budget=budget,cache_dir=tmp_path/'roles',batch_size=1)
    assert resumed['summary']['cache_hits']==3 and budget.summary()['call_count']==2


def test_auth_failure_keeps_success_and_pauses_remaining_role_batches(tmp_path):
    from app.services.llm_translation_provider import LLMHTTPStatusError
    class AuthFailure(Provider):
        async def complete_chat(self,messages,**kwargs):
            if self.calls:
                self.calls.append(json.loads(messages[1]['content'])['records'])
                raise LLMHTTPStatusError(401,'authentication failed')
            return await super().complete_chat(messages,**kwargs)
    provider=AuthFailure();budget=task_budget(tmp_path,provider)
    facts=extract_production_roles(multiple_units(),provider=provider,budget=budget,cache_dir=tmp_path/'roles',batch_size=1)
    assert facts['summary']['completed_units']==1 and len(provider.calls)==2
    assert facts['summary']['blocked']=='role_provider_authentication_or_rate_limit'
    assert budget.summary()['unknown_usage_count']==1


def test_later_case_variant_reuses_proven_original_question(tmp_path):
    vocabulary=build_semantic_vocabulary([]);provider=Provider();budget=task_budget(tmp_path,provider)
    first_units,_=plan_role_extraction(consumer(1),vocabulary)
    first=extract_production_roles(first_units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    signals=build_source_concept_signal_drafts([source('mysteryname','99999999')])
    changed=make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'variant-input')
    next_units,_=plan_role_extraction(changed,vocabulary)
    assert next_units[0].extraction_key==first_units[0].extraction_key
    result=extract_production_roles(next_units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert result['summary']['original_question_case_variant_cache_hits']==1
    assert len(provider.calls)==1 and result['records']==first['records']


def test_contextual_supplement_is_cached_and_bound_to_real_aggregate(tmp_path):
    from app.services.production_pixiv_role_extraction import extract_contextual_production_roles
    def contextual_input(aggregate,other_tag):
        inputs=[replace(source(name,'12345678'),evidence_payload={'work_id':'12345678','page_index':0,
            'aggregate_fingerprint':aggregate}) for name in ['MysteryName',other_tag]]
        signals=build_source_concept_signal_drafts(inputs)
        return make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'context-input')
    first=contextual_input('first-frozen-aggregate','WorkName')
    provider=Provider();budget=task_budget(tmp_path,provider);vocabulary=build_semantic_vocabulary([])
    empty={'schema_version':'violet.production-pixiv-role-result.v1','records':{}}
    result=extract_contextual_production_roles(first,vocabulary,empty,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert len(provider.calls)==1 and len(provider.calls[0][0]['tags'])==2
    assert result['context_summary']['completed_units']==1
    replay=extract_contextual_production_roles(first,vocabulary,empty,provider=provider,budget=budget,cache_dir=tmp_path/'roles',batch_size=8)
    assert replay['context_summary']['cache_hits']==1 and len(provider.calls)==1
    adapted=adapt_production_semantics(first,vocabulary,result)
    assert next(s for s in adapted.signals if s.raw_value=='MysteryName').role_hint=='character'
    other=adapt_production_semantics(contextual_input('different-aggregate','DifferentWork'),vocabulary,result)
    assert next(s for s in other.signals if s.raw_value=='MysteryName').role_hint=='unknown'
    # A resumed supplement may skip already grounded units; their facts must
    # remain effective instead of disappearing behind the latest batch.
    resumed=extract_contextual_production_roles(first,vocabulary,result,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert result['context_by_aggregate'].items()<=resumed['context_by_aggregate'].items()
    assert all(resumed['context_records'][k]==v for k,v in result['context_records'].items())
    assert next(s for s in adapt_production_semantics(first,vocabulary,resumed).signals if s.raw_value=='MysteryName').role_hint=='character'


def test_context_supplement_does_not_publish_unacquired_mapping(tmp_path,monkeypatch):
    from app.services import production_pixiv_role_extraction as service
    prior={'records':{},'context_records':{'old':{'kept':True}},'context_by_aggregate':{'aggregate':'old'}}
    monkeypatch.setattr(service,'plan_contextual_role_extraction',lambda *args:([],{'aggregate':'new','unanswered':'missing'},{}))
    monkeypatch.setattr(service,'extract_production_roles',lambda *args,**kwargs:{'records':{},'summary':{}})
    result=service.extract_contextual_production_roles(None,None,prior,provider=None,budget=None,cache_dir=tmp_path)
    assert result['context_records']==prior['context_records']
    assert result['context_by_aggregate']==prior['context_by_aggregate']


def test_generic_external_taxonomy_does_not_reject_pixiv_name_before_context():
    vocabulary=build_semantic_vocabulary([], [{'raw_tag':'MysteryName','status':'resolved',
        'candidate_namespace':'general','source_summary':{'selected_reason':'external_tag_category_lookup'}}])
    units,_=plan_role_extraction(consumer(1),vocabulary)
    assert len(units)==1 and units[0].llm_required


def test_contextual_work_role_survives_unknown_popularity_prefix(tmp_path):
    from app.services.production_pixiv_role_extraction import extract_contextual_production_roles
    class ContextProvider(Provider):
        async def complete_chat(self,messages,**kwargs):
            payload=json.loads(await super().complete_chat(messages,**kwargs))
            candidate=payload['records'][0]['candidates'][0]
            candidate.update(role='work_title',source_field='source_tag',confidence=0.9)
            payload['records'][0]['candidates'].append({**candidate,'role':'unknown_name_like',
                'status':'needs_review','confidence':0.68,'extraction_action':'popularity_suffix_stripped'})
            return json.dumps(payload)
    value=consumer(1)
    value=replace(value,signals=tuple(replace(signal,evidence_payload={**signal.evidence_payload,
        'aggregate_fingerprint':'frozen-role-context'}) for signal in value.signals))
    provider=ContextProvider();budget=task_budget(tmp_path,provider)
    vocabulary=build_semantic_vocabulary([])
    empty={'schema_version':'violet.production-pixiv-role-result.v1','records':{}}
    result=extract_contextual_production_roles(value,vocabulary,empty,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    adapted=adapt_production_semantics(value,vocabulary,result)
    assert adapted.signals[0].role_hint=='work'
    # A conflicting explicit character answer is a real role disagreement.
    record=next(iter(result['context_records'].values()))
    record['candidates'].append({**record['candidates'][0],'candidate_role':'character'})
    assert adapt_production_semantics(value,vocabulary,result).signals[0].role_hint=='unknown'


def test_actual_tag_field_synonyms_recover_paid_raw_without_new_request(tmp_path):
    from app.services.production_pixiv_role_extraction import _unit_path
    class TagSynonyms(Provider):
        async def complete_chat(self,messages,**kwargs):
            payload=json.loads(await super().complete_chat(messages,**kwargs))
            for row in payload['records']:
                row['candidates'][0].update(source_field='provider_tag',extraction_action='normal_tag')
            return json.dumps(payload)
    units,_=plan_role_extraction(consumer(1),build_semantic_vocabulary([]))
    provider=TagSynonyms();budget=task_budget(tmp_path,provider)
    cached=tmp_path/'roles'
    first=extract_production_roles(units,provider=provider,budget=budget,cache_dir=cached)
    assert first['summary']['completed_units']==1 and len(provider.calls)==1
    raw=next((cached/'raw').glob('*.json'))
    assert 'provider_tag' in json.loads(raw.read_text(encoding='utf-8'))['content']
    _unit_path(cached,units[0]).unlink()  # Simulate a crash after the raw reply.
    resumed=extract_production_roles(units,provider=provider,budget=budget,cache_dir=cached)
    assert resumed['summary']['paid_raw_units_recovered_locally']==1
    assert resumed['records']==first['records'] and len(provider.calls)==1


def test_tag_synonym_adapter_cannot_invent_provenance(tmp_path):
    from app.services.production_pixiv_role_extraction import BudgetedExtractionProvider
    units,_=plan_role_extraction(consumer(1),build_semantic_vocabulary([]))
    provider=Provider();wrapped=BudgetedExtractionProvider(provider,task_budget(tmp_path,provider),tmp_path/'roles',units)
    raw=json.dumps({'records':[{'group_key':units[0].unit_group.group_key,
        'candidates':[{'raw_value':'AbsentFromActualTags','source_field':'provider_tag','extraction_action':'normal_tag'}]}]})
    candidate=json.loads(wrapped.adapted_content(raw))['records'][0]['candidates'][0]
    assert candidate['source_field']=='provider_tag' and candidate['extraction_action']=='normal_tag'


@pytest.mark.parametrize('reported_origin',['provider_field','pixiv_tag'])
def test_saved_context_work_answer_recovers_actual_tag_provenance_without_repay(tmp_path,reported_origin):
    from app.services.production_pixiv_role_extraction import (
        plan_contextual_role_extraction,_unit_path,_record,validate_extraction_record,
    )
    value=consumer(1)
    value=replace(value,signals=tuple(replace(signal,evidence_payload={**signal.evidence_payload,
        'aggregate_fingerprint':'frozen-tag-provenance'}) for signal in value.signals))
    empty={'schema_version':'violet.production-pixiv-role-result.v1','records':{}}
    units,_,_=plan_contextual_role_extraction(value,build_semantic_vocabulary([]),empty)
    unit=units[0];provider=Provider();budget=task_budget(tmp_path,provider)
    response={'group_key':unit.unit_group.group_key,'provider':'pixiv','verdict':'work_candidate_found',
        'candidates':[{'raw_value':'MysteryName','role':'work_title','status':'active_candidate',
            'confidence':0.9,'source_field':reported_origin,'extraction_action':'direct_name'}],
        'rejected_summary':{}}
    verdict,candidates,*_=validate_extraction_record(response,unit.unit_group)
    assert candidates[0].candidate_role=='source_title'
    old={**_record(unit,provider.model,verdict,candidates,origin='existing_f7a_extractor_primary_model'),
        'validated_response':response}
    cached=tmp_path/'roles';path=_unit_path(cached,unit);path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old),encoding='utf-8');original=path.read_bytes()
    result=extract_production_roles(units,provider=provider,budget=budget,cache_dir=cached)
    recovered=next(iter(result['records'].values()))
    assert recovered['candidates'][0]['candidate_role']=='work_title'
    assert recovered['candidates'][0]['origin_type']=='source_tag_observation'
    assert recovered['response_adapter_version']=='production_tag_provenance_v2'
    assert path.read_bytes()==original and not provider.calls and budget.summary()['call_count']==0


def test_actual_tag_provenance_repair_does_not_promote_unobserved_provider_title(tmp_path):
    from app.services.production_pixiv_role_extraction import _adapt_response_record
    units,_=plan_role_extraction(consumer(1),build_semantic_vocabulary([]))
    row={'candidates':[{'raw_value':'UnobservedTitle','source_field':'provider_field','role':'work_title'}]}
    assert _adapt_response_record(row,units[0])==row


@pytest.mark.parametrize('role_field',['role','candidate_role'])
def test_paid_context_role_stays_unknown_and_recovers_valid_siblings_without_repay(tmp_path,role_field):
    from app.services.production_pixiv_role_extraction import _production_messages
    from app.services.source_name_candidate_extraction_service import extraction_messages
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    unit=plan_role_extraction(consumer(1),build_semantic_vocabulary([]))[0][0]
    unit=replace(unit,unit_group=replace(unit.unit_group,tags=(
        {'raw_tag':'MysteryName','source_tag_kind':'provider_tag'},
        {'raw_tag':'KnownCharacter','source_tag_kind':'provider_tag'})))
    row={'group_key':unit.unit_group.group_key,'provider':'pixiv','verdict':'multiple_candidates_found',
        'candidates':[
            {'raw_value':'MysteryName',role_field:'work_context','status':'active_candidate',
                'confidence':0.9,'source_field':'provider_tag','extraction_action':'direct_name'},
            {'raw_value':'KnownCharacter','role':'character','status':'active_candidate',
                'confidence':0.9,'source_field':'provider_tag','extraction_action':'direct_name'}],
        'rejected_summary':{}}
    provider=Provider();budget=task_budget(tmp_path,provider)
    messages=_production_messages(extraction_messages([unit.unit_group]))
    signature=canonical_fingerprint({'model':provider.model,'messages':messages,'temperature':0.0,'max_tokens':6000})
    cache=tmp_path/'roles';raw=cache/'raw'/f'{signature}.json';raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps({'model':provider.model,'input_fingerprint':signature,
        'content':json.dumps({'records':[row]})}),encoding='utf-8')
    original=raw.read_bytes()
    result=extract_production_roles([unit],provider=provider,budget=budget,cache_dir=cache)
    record=result['records'][unit.extraction_key]
    found={candidate['raw_value']:candidate for candidate in record['candidates']}
    assert found['MysteryName']['candidate_role']=='unknown_name_like'
    assert found['MysteryName']['candidate_status']=='needs_review'
    assert found['KnownCharacter']['candidate_role']=='character'
    assert record['validated_response']['candidates'][0]['production_reported_role']=='work_context'
    assert row['candidates'][0][role_field]=='work_context' and raw.read_bytes()==original
    assert result['summary']['paid_raw_units_recovered_locally']==1
    assert not provider.calls and budget.summary()['call_count']==0


def test_context_role_adapter_does_not_accept_an_unobserved_name_or_other_invalid_role():
    from app.services.production_pixiv_role_extraction import _adapt_response_record
    unit=plan_role_extraction(consumer(1),build_semantic_vocabulary([]))[0][0]
    row={'candidates':[{'raw_value':'Unobserved','role':'work_context','status':'active_candidate'},
        {'raw_value':'MysteryName','role':'invented_identity','status':'active_candidate'}]}
    assert _adapt_response_record(row,unit)==row


@pytest.mark.parametrize('size',[0,11,True])
def test_context_batch_size_is_bounded_before_any_call(tmp_path,size):
    from app.services.production_pixiv_role_extraction import extract_contextual_production_roles
    provider=Provider()
    with pytest.raises(ValueError,match='production_context_batch_size_invalid'):
        extract_contextual_production_roles(consumer(1),build_semantic_vocabulary([]),{},provider=provider,
            budget=task_budget(tmp_path,provider),cache_dir=tmp_path/'roles',batch_size=size)
    assert not provider.calls


def test_residual_completion_uses_real_context_without_repeating_partial_hints(tmp_path):
    from app.services.production_pixiv_role_extraction import complete_contextual_production_roles,plan_contextual_role_completion
    class Completion(Provider):
        async def complete_chat(self,messages,**kwargs):
            payload=json.loads(messages[1]['content'])
            assert 'partial' in messages[0]['content']
            assert all('deterministic_hints' not in group for group in payload['records'])
            assert all({tag['raw_tag'] for tag in group['tags']}=={'MysteryName','ContextWork'} for group in payload['records'])
            self.calls.append(payload['records']);self.last_usage={'prompt_tokens':100,'completion_tokens':100}
            return json.dumps({'records':[{'group_key':group['group_key'],'provider':'pixiv',
                'verdict':'multiple_candidates_found','candidates':[
                    {'raw_value':name,'role':'work_title' if name=='ContextWork' else 'character',
                        'status':'active_candidate','confidence':0.9,'source_field':'source_tag_observation',
                        'extraction_action':'direct_name'}
                    for name in json.loads(group['data_type_label'].split(': ',1)[1])],
                'rejected_summary':{}} for group in payload['records']]})
    signals=build_source_concept_signal_drafts([replace(source(name,'12345678'),
        evidence_payload={'work_id':'12345678','page_index':0,'aggregate_fingerprint':'residual-context'})
        for name in ['MysteryName','ContextWork']])
    value=make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'residual-input')
    facts={'schema_version':'violet.production-pixiv-role-result.v1','records':{},
        'context_by_aggregate':{'residual-context':'prior-question'},
        'context_records':{'prior-question':{'candidates':[],'verdict':'no_explicit_name'}}}
    provider=Completion();budget=task_budget(tmp_path,provider);vocabulary=build_semantic_vocabulary([])
    first=complete_contextual_production_roles(value,vocabulary,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert first['completion_summary']['completed_units']==1 and len(provider.calls)==1
    adapted=adapt_production_semantics(value,vocabulary,first)
    assert {s.role_hint for s in adapted.signals}=={'work','character'}
    replay=complete_contextual_production_roles(value,vocabulary,facts,provider=provider,budget=budget,cache_dir=tmp_path/'roles',batch_size=8)
    assert replay['completion_summary']['cache_hits']==1 and len(provider.calls)==1
    assert plan_contextual_role_completion(value,vocabulary,first)[0]==[]
    assert facts['context_records']['prior-question']['candidates']==[]
    unseen={'schema_version':'violet.production-pixiv-role-result.v1','records':{}}
    assert len(plan_contextual_role_completion(value,vocabulary,unseen)[0])==1
