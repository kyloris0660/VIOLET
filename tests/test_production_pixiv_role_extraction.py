from dataclasses import make_dataclass,replace
import json

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
    replay=extract_contextual_production_roles(first,vocabulary,empty,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    assert replay['context_summary']['cache_hits']==1 and len(provider.calls)==1
    adapted=adapt_production_semantics(first,vocabulary,result)
    assert next(s for s in adapted.signals if s.raw_value=='MysteryName').role_hint=='character'
    other=adapt_production_semantics(contextual_input('different-aggregate','DifferentWork'),vocabulary,result)
    assert next(s for s in other.signals if s.raw_value=='MysteryName').role_hint=='unknown'


def test_generic_external_taxonomy_does_not_reject_pixiv_name_before_context():
    vocabulary=build_semantic_vocabulary([], [{'raw_tag':'MysteryName','status':'resolved',
        'candidate_namespace':'general','source_summary':{'selected_reason':'external_tag_category_lookup'}}])
    units,_=plan_role_extraction(consumer(1),vocabulary)
    assert len(units)==1 and units[0].llm_required
