from dataclasses import replace
import json

import pytest

from app.services import source_concept_resolver_service as service
from test_phase45_sc1_source_concept_resolver import _eligible_llm_edges,_FakeLLMProvider,_cache_config


class MeteredProvider(_FakeLLMProvider):
    async def complete_json(self,*args,**kwargs):
        response=await super().complete_json(*args,**kwargs)
        self.last_usage={'prompt_tokens':100,'completion_tokens':50}
        return response


def config(tmp_path):
    return _cache_config(tmp_path,model_label='gpt-test',max_budget_usd=10,
        task_budget_path=str(tmp_path/'budget.json'),input_price_per_million=0.4,
        output_price_per_million=1.6,semantic_cache_reuse=True)


@pytest.mark.parametrize('change',['none','decision','confidence','duplicate','missing','context'])
def test_release_replays_selected_question_and_actual_cache_answer(tmp_path,monkeypatch,change):
    import copy
    from app.services.production_pixiv_release_provenance import verify_selected_judgment_sources
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=config(tmp_path)
    rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    ledger=json.loads((tmp_path/'budget.json').read_text())
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:pytest.fail('release must be offline'))
    rows=copy.deepcopy(rows)
    if change=='decision':rows[0]['decision']='cannot_link'
    elif change=='confidence':rows[0]['confidence']=0.1
    elif change=='duplicate':rows.append(rows[0])
    elif change=='missing':rows=[]
    elif change=='context':signals=[replace(s,work_context_key='changed-input') for s in signals]
    if change=='none':
        assert verify_selected_judgment_sources(edges,signals,rows,cfg,ledger)['judgment_count']==1
    else:
        with pytest.raises(ValueError,match='semantic_'):
            verify_selected_judgment_sources(edges,signals,rows,cfg,ledger)
    assert provider.calls==1


@pytest.mark.parametrize('failure_point',['record','pair_index','settlement'])
@pytest.mark.parametrize('reuse',['exact','same_input'])
def test_valid_provider_response_survives_local_cache_or_settlement_failure(tmp_path,monkeypatch,failure_point,reuse):
    from app.services.source_concept_budget import AdjudicationBudget
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=config(tmp_path);write=service._atomic_write_json;settle=AdjudicationBudget.settle
    def unavailable(path,payload):
        if (failure_point=='record' and path.parent.name=='records') or (failure_point=='pair_index' and path.parent.parent.name=='pair-index'):
            raise PermissionError('local cache publication unavailable')
        return write(path,payload)
    monkeypatch.setattr(service,'_atomic_write_json',unavailable)
    if failure_point=='settlement':
        monkeypatch.setattr(AdjudicationBudget,'settle',lambda *a,**k:(_ for _ in ()).throw(PermissionError('local settlement unavailable')))
    with pytest.raises(RuntimeError,match='adjudication_valid_response_persistence_failed'):
        service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    ledger=json.loads((tmp_path/'budget.json').read_text())
    assert provider.calls==1 and len(ledger['calls'])==1 and ledger['calls'][0]['status']=='reserved'
    recovered=next((service._cache_root(cfg)/'response-recovery').glob('*.json'));saved=recovered.read_bytes()
    response=json.loads(saved)
    assert not response['error_state'] and response['budget_response']['usage']=={'prompt_tokens':100,'completion_tokens':50}
    monkeypatch.setattr(service,'_atomic_write_json',write);monkeypatch.setattr(AdjudicationBudget,'settle',settle)
    if reuse=='same_input':
        signals=[replace(s,signal_key='new:'+s.signal_key) for s in signals]
        edges=[replace(e,edge_key='new:'+e.edge_key,left_signal_key='new:'+e.left_signal_key,right_signal_key='new:'+e.right_signal_key) for e in edges]
    for _ in range(2):
        rows,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
        assert receipt['error_count']==0 and receipt['new_provider_call_count']==0 and len(rows)==1 and not rows[0]['error_state']
    ledger=json.loads((tmp_path/'budget.json').read_text())
    assert provider.calls==1 and len(ledger['calls'])==1 and ledger['calls'][0]['status']=='success'
    assert ledger['calls'][0]['charged_microusd']==120 and recovered.read_bytes()==saved


def test_unwritable_response_recovery_stops_and_preserves_inflight_admission(tmp_path,monkeypatch):
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=config(tmp_path);write=service._atomic_write_json
    monkeypatch.setattr(service,'_atomic_write_json',lambda *a,**k:(_ for _ in ()).throw(PermissionError('all cache writes unavailable')))
    with pytest.raises(RuntimeError,match='adjudication_valid_response_recovery_persistence_failed'):
        service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    original=(tmp_path/'budget.json').read_bytes()
    assert provider.calls==1 and json.loads(original)['calls'][0]['status']=='reserved'
    monkeypatch.setattr(service,'_atomic_write_json',write)
    rows,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    assert provider.calls==1 and not rows and receipt['remaining_missing_pair_count']==1
    assert receipt['budget_blocked_pairs'] and (tmp_path/'budget.json').read_bytes()==original


@pytest.mark.parametrize('semantic',[False,True])
def test_cache_read_io_failure_does_not_become_another_paid_miss(tmp_path,monkeypatch,semantic):
    from pathlib import Path
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=replace(config(tmp_path),semantic_cache_reuse=semantic)
    service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    original=(tmp_path/'budget.json').read_bytes();read_text=Path.read_text
    def denied(path,*args,**kwargs):
        if path.parent.name=='records':raise PermissionError('temporarily unreadable saved answer')
        return read_text(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',denied)
    with pytest.raises(RuntimeError,match='adjudication_cache_read_failed'):
        service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    assert provider.calls==1 and (tmp_path/'budget.json').read_bytes()==original


def test_semantic_identity_prompt_revision_retains_old_answer_cost_and_reuses_new_answer(tmp_path,monkeypatch):
    signals,edges=_eligible_llm_edges(1)
    class PromptAware(MeteredProvider):
        async def complete_json(self,messages,**kwargs):
            self.calls+=1;self.last_usage={'prompt_tokens':100,'completion_tokens':50}
            instructions=messages[0]['content']
            revised='Judge semantic identity, not string equality' in instructions
            if revised:
                assert 'source role hints character and person are compatible' in instructions
                assert 'A shared work or co-occurrence alone does not establish sameness' in instructions
                assert 'needs_review when identity evidence is insufficient or ambiguous' in instructions
            return {'decision':'must_link' if revised else 'cannot_link','confidence':0.9}
    provider=PromptAware()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=config(tmp_path)
    before,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    old_record=next((service._cache_root(cfg)/'records').glob('*.json'));old_bytes=old_record.read_bytes()
    old_call=json.loads((tmp_path/'budget.json').read_text())['calls'][0]
    revised=replace(cfg,prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    after,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=revised)
    assert before[0]['decision']=='cannot_link' and after[0]['decision']=='must_link'
    assert old_record.read_bytes()==old_bytes
    calls=json.loads((tmp_path/'budget.json').read_text())['calls']
    assert calls[0]==old_call and len(calls)==2 and calls[1]['logical_keys']==[old_call['key']]
    assert calls[1]['key']!=old_call['key'] and receipt['task_budget']['charged_or_reserved_usd']==0.00024
    again,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=revised)
    assert again[0]['decision']=='must_link' and receipt['cache_hits']==1 and provider.calls==2


@pytest.mark.parametrize('decision',['cannot_link','needs_review'])
def test_revised_prompt_keeps_negative_and_unknown_answers_without_reasking(tmp_path,monkeypatch,decision):
    signals,edges=_eligible_llm_edges(1)
    class Answer(MeteredProvider):
        async def complete_json(self,messages,**kwargs):
            self.calls+=1;self.last_usage={'prompt_tokens':100,'completion_tokens':50}
            return {'decision':decision,'confidence':0.65}
    provider=Answer();monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    for _ in range(2):
        rows,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
        assert rows[0]['decision']==decision and receipt['error_count']==0
    assert provider.calls==1 and receipt['task_budget']['call_count']==1


@pytest.mark.parametrize('confidence,conflict',[(0.7,True),(0.8,True),('high',False)])
def test_semantic_cache_compares_effective_confidence_independent_of_filename(tmp_path,monkeypatch,confidence,conflict):
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=config(tmp_path)
    service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    directory=service._cache_root(cfg)/'records'
    original=next(directory.glob('*.json'))
    record=json.loads(original.read_text(encoding='utf-8'))
    record.update(decision='must_link',confidence=0.9)
    original.write_text(json.dumps(record),encoding='utf-8')
    duplicate={**record,'confidence':confidence}
    key=service._decision_input_key(record['input_signal_summary'])
    other=directory/'000-duplicate.json'
    other.write_text(json.dumps(duplicate),encoding='utf-8')
    assert (service._compatible_decision_cache(cfg)[key] is None) is conflict
    other.rename(directory/'zzz-duplicate.json')
    assert (service._compatible_decision_cache(cfg)[key] is None) is conflict
    assert provider.calls==1


def test_new_occurrence_reuses_same_decision_input_without_provider_or_budget_reset(tmp_path,monkeypatch):
    signals,edges=_eligible_llm_edges(1)
    provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{'provider_mode':'primary_openai'}))
    first,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert receipt['task_budget']['charged_or_reserved_usd']==0.00012
    later=[replace(s,signal_key='new:'+s.signal_key) for s in signals]
    later_edges=[replace(e,edge_key='new:'+e.edge_key,left_signal_key='new:'+e.left_signal_key,
        right_signal_key='new:'+e.right_signal_key) for e in edges]
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(_ for _ in ()).throw(AssertionError('duplicate paid call')))
    second,receipt=service.run_bounded_llm_adjudication(later_edges,signals=later,config=config(tmp_path))
    assert len(first)==len(second)==1 and provider.calls==1
    assert receipt['same_decision_input_cache_hit_count']==1 and receipt['new_provider_call_count']==0
    assert second[0]['left_signal_key'].startswith('new:')
    assert receipt['task_budget']['call_count']==1


def test_changed_role_or_context_cannot_reuse_semantic_judgment(tmp_path,monkeypatch):
    signals,edges=_eligible_llm_edges(1)
    provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{'provider_mode':'primary_openai'}))
    service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    changed=[replace(s,work_context_key='different-franchise') for s in signals]
    _,receipt=service.run_bounded_llm_adjudication(edges,signals=changed,config=config(tmp_path))
    assert provider.calls==2 and receipt['task_budget']['call_count']==2
    assert receipt['same_decision_input_cache_hit_count']==0


def test_actual_model_mismatch_blocks_before_dispatch(tmp_path,monkeypatch):
    signals,edges=_eligible_llm_edges(1)
    provider=MeteredProvider();provider.model='different-model'
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    with pytest.raises(RuntimeError,match='actual_model_mismatch'):
        service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert provider.calls==0


def test_paid_pair_cache_recovers_unsettled_attempt_once(tmp_path,monkeypatch):
    from app.services.source_concept_budget import AdjudicationBudget
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    settle=AdjudicationBudget.settle
    def crash(*args,**kwargs):raise SystemExit('crash after durable pair cache')
    monkeypatch.setattr(AdjudicationBudget,'settle',crash)
    with pytest.raises(SystemExit):service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    monkeypatch.setattr(AdjudicationBudget,'settle',settle)
    judgments,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert receipt['cache_hits']==1 and provider.calls==1
    assert receipt['task_budget']['charged_or_reserved_usd']==0.00012
    _,again=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert again['task_budget']==receipt['task_budget'] and provider.calls==1


def test_existing_named_confidence_remains_compatible_with_budget_gate(tmp_path,monkeypatch):
    signals,edges=_eligible_llm_edges(1)
    class NamedConfidence(MeteredProvider):
        async def complete_json(self,*args,**kwargs):
            response=await super().complete_json(*args,**kwargs)
            return {**response,'confidence':'high'}
    provider=NamedConfidence()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{'provider_mode':'primary_openai'}))
    judgments,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert receipt['error_count']==0 and judgments[0]['confidence']==0.9


@pytest.mark.parametrize('response_count',[0,1,2])
def test_budgeted_pair_accepts_only_single_answer_list_and_reuses_it(tmp_path,monkeypatch,response_count):
    signals,edges=_eligible_llm_edges(1)
    class ListAnswer(MeteredProvider):
        async def complete_json(self,*args,**kwargs):
            response=await super().complete_json(*args,**kwargs)
            return [response]*response_count
    provider=ListAnswer()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    judgments,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert receipt['error_count']==(0 if response_count==1 else 1)
    if response_count==1:
        assert judgments[0]['error_state'] is None
        again,reused=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
        assert provider.calls==1 and reused['cache_hits']==1
        assert reused['task_budget']==receipt['task_budget']
        assert again[0]['decision']==judgments[0]['decision']


def test_failed_call_unknown_usage_is_charged_and_success_cache_survives(tmp_path,monkeypatch):
    signals,edges=_eligible_llm_edges(2)
    provider=MeteredProvider(fail_on_call=2)
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{'provider_mode':'primary_openai'}))
    judgments,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert receipt['new_provider_call_count']==2 and receipt['new_provider_success_count']==1
    assert receipt['task_budget']['unknown_usage_count']==1
    assert receipt['task_budget']['charged_or_reserved_usd']>0.00012
    _,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    assert provider.calls==3 and receipt['cache_hits']==1


@pytest.mark.parametrize('status',[401,403,429])
def test_authentication_or_rate_limit_pauses_later_pairs_but_reuses_saved_success(tmp_path,monkeypatch,status):
    from app.services.llm_translation_provider import LLMHTTPStatusError
    signals,edges=_eligible_llm_edges(4)
    provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    selected=service.select_llm_adjudication_edges(edges,signals=signals,config=config(tmp_path))
    service.run_bounded_llm_adjudication(selected[-1:],signals=signals,config=config(tmp_path))
    class Rejected(MeteredProvider):
        async def complete_json(self,*args,**kwargs):
            self.calls+=1
            raise LLMHTTPStatusError(status,'test upstream rejection')
    rejected=Rejected()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(rejected,{}))
    shared=replace(config(tmp_path),provider_pause_state={})
    judgments,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=shared)
    assert rejected.calls==1 and receipt['new_provider_call_count']==1
    assert receipt['cache_hits']==1 and receipt['error_count']==1
    assert len(receipt['budget_blocked_pairs'])==2
    assert receipt['task_budget']['call_count']==2 and receipt['task_budget']['unknown_usage_count']==1
    assert sum(row.get('cache_status')=='hit' for row in judgments)==1
    later,later_edges=_eligible_llm_edges(5)
    _,following=service.run_bounded_llm_adjudication(later_edges,signals=later,config=shared)
    assert rejected.calls==1 and following['new_provider_call_count']==0
    assert following['provider_pause_reason']=='adjudication_provider_authentication_or_rate_limit'


@pytest.mark.parametrize('success_between',[False,True])
def test_three_consecutive_transport_failures_pause_without_charging_unattempted_pairs(tmp_path,monkeypatch,success_between):
    from app.services.llm_translation_provider import LLMTransportError
    signals,edges=_eligible_llm_edges(8)
    class Unstable(MeteredProvider):
        async def complete_json(self,*args,**kwargs):
            if success_between and self.calls==2:
                return await super().complete_json(*args,**kwargs)
            self.calls+=1
            raise LLMTransportError('test transport failure')
    provider=Unstable()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    _,receipt=service.run_bounded_llm_adjudication(edges,signals=signals,config=config(tmp_path))
    expected=6 if success_between else 3
    assert provider.calls==expected==receipt['task_budget']['call_count']
    assert receipt['new_provider_success_count']==int(success_between)
    assert len(receipt['budget_blocked_pairs'])==8-expected
    assert receipt['provider_pause_reason']=='adjudication_provider_repeated_transport_failure'
