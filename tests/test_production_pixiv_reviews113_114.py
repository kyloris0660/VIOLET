"""Prove paid correction admission and cached role projections before dispatch."""
import asyncio,copy,json
from dataclasses import replace
import pytest

@pytest.mark.parametrize('chain',[False,True])
@pytest.mark.parametrize('outcome',['valid','missing','failed','revoked','ambiguous','unrelated','overrun'])
def test_correction_prior_legacy_requires_one_exact_eligible_paid_source(tmp_path,monkeypatch,chain,outcome):
 from test_production_pixiv_adjudication import config,MeteredProvider,_eligible_llm_edges,service
 from app.services.production_pixiv_pair_correction import plan_corrected_pairs
 signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
 monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
 cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
 rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
 cache=service._cache_root(cfg)/'records'/(rows[0]['cache_key']+'.json')
 original=json.loads(cache.read_text());original.pop('budget_response')
 if chain:
  key='a'*48;source={**original,'cache_key':key}
  (cache.parent/(key+'.json')).write_text(json.dumps(source),encoding='utf-8')
  original['reused_from_cache_key']=key
 cache.write_text(json.dumps(original),encoding='utf-8')
 ledger=json.loads((tmp_path/'budget.json').read_text());call=ledger['calls'][0]
 if outcome=='missing':ledger['calls']=[]
 elif outcome=='failed':call['status']='failed'
 elif outcome=='revoked':call['business_valid']=False
 elif outcome=='ambiguous':ledger['calls'].append({**copy.deepcopy(call),'id':'another-paid-answer'})
 elif outcome=='unrelated':call['key']='decision-input:unrelated:prompt:'+cfg.prompt_version
 elif outcome=='overrun':call['reserved_microusd']=0
 before={p:p.read_bytes() for p in cache.parent.glob('*.json')};debits=(tmp_path/'budget.json').read_bytes()
 changed=[replace(s,work_context_key='actual-correction') for s in signals]
 if outcome=='valid':
  _,plan=plan_corrected_pairs(edges,changed,rows,cfg,ledger)
  assert plan['verified_prior_judgment_count']==1 and plan['dispatchable_call_ceiling']==1
 else:
  with pytest.raises(ValueError,match='correction_prior_'):plan_corrected_pairs(edges,changed,rows,cfg,ledger)
 assert provider.calls==1 and (tmp_path/'budget.json').read_bytes()==debits
 assert before=={p:p.read_bytes() for p in cache.parent.glob('*.json')}

@pytest.mark.parametrize('outcome',['unrelated','overrun','revoked'])
def test_correction_prior_envelope_cannot_borrow_or_overrun_a_paid_call(tmp_path,monkeypatch,outcome):
 from test_production_pixiv_adjudication import config,MeteredProvider,_eligible_llm_edges,service
 from app.services.production_pixiv_pair_correction import verify_correction_prior_sources
 signals,edges=_eligible_llm_edges(1);provider=MeteredProvider();cfg=config(tmp_path)
 monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
 rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
 cache=service._cache_root(cfg)/'records'/(rows[0]['cache_key']+'.json');saved=json.loads(cache.read_text())
 ledger=json.loads((tmp_path/'budget.json').read_text());call=ledger['calls'][0]
 if outcome=='unrelated':
  call['key']='unrelated-question';saved['budget_response']['key']=call['key']
  cache.write_text(json.dumps(saved),encoding='utf-8')
 elif outcome=='overrun':call['reserved_microusd']=0
 else:call['business_valid']=False
 with pytest.raises(ValueError,match='correction_prior_'):verify_correction_prior_sources(rows,cfg,ledger)
 assert provider.calls==1

@pytest.mark.parametrize('route',['read','extract'])
@pytest.mark.parametrize('mutation',['valid','verdict','candidates','name'])
def test_role_cache_projection_is_revalidated_from_original_answer_before_use(tmp_path,route,mutation):
 import app.services.production_pixiv_role_extraction as roles
 from test_production_pixiv_role_extraction import Provider,multiple_units,task_budget
 from app.services.source_name_candidate_extraction_service import extraction_messages
 provider=Provider();budget=task_budget(tmp_path,provider);unit=multiple_units()[0];cache=tmp_path/'roles'
 wrapped=roles.BudgetedExtractionProvider(provider,budget,cache,[unit])
 asyncio.run(wrapped.complete_chat(extraction_messages([unit.unit_group])))
 path=roles._unit_path(cache,unit);original=json.loads(path.read_text());changed=copy.deepcopy(original)
 assert roles._adapt_response_record(original['validated_response'],unit)==original['validated_response']
 if mutation=='verdict':changed['verdict']='no_candidate'
 elif mutation=='candidates':changed['candidates']=[]
 elif mutation=='name':
  changed['candidates'][0].update(candidate_role='work_title',display_name='Invented',normalized_value='Invented')
 path.write_text(json.dumps(changed),encoding='utf-8');retained=path.read_bytes();debits=budget.path.read_bytes()
 for raw in (cache/'raw').glob('*.json'):raw.unlink()
 if route=='read':actual=roles._read_unit_cache(path,unit,provider.model)[0]
 else:actual=roles.extract_production_roles([unit],provider=provider,budget=budget,cache_dir=cache)['records'][unit.extraction_key]
 assert actual['verdict']==original['verdict'] and actual['candidates']==original['candidates']
 if mutation=='valid':assert actual==original
 assert actual['validated_response']==original['validated_response'] and actual['budget_response']==original['budget_response']
 assert path.read_bytes()==retained and budget.path.read_bytes()==debits and len(provider.calls)==1

@pytest.mark.parametrize('mutation',['missing','invalid_group'])
def test_paid_role_cache_without_a_valid_original_answer_is_refused_before_dispatch(tmp_path,mutation):
 import app.services.production_pixiv_role_extraction as roles
 from test_production_pixiv_role_extraction import Provider,multiple_units,task_budget
 from app.services.source_name_candidate_extraction_service import extraction_messages,SourceNameCandidateExtractionError
 provider=Provider();budget=task_budget(tmp_path,provider);unit=multiple_units()[0];cache=tmp_path/'roles'
 wrapped=roles.BudgetedExtractionProvider(provider,budget,cache,[unit])
 asyncio.run(wrapped.complete_chat(extraction_messages([unit.unit_group])))
 path=roles._unit_path(cache,unit);record=json.loads(path.read_text())
 if mutation=='missing':record.pop('validated_response')
 else:record['validated_response']['group_key']='foreign-original-question'
 path.write_text(json.dumps(record),encoding='utf-8');retained=path.read_bytes()
 for raw in (cache/'raw').glob('*.json'):raw.unlink()
 with pytest.raises((ValueError,SourceNameCandidateExtractionError)):
  roles.extract_production_roles([unit],provider=provider,budget=budget,cache_dir=cache)
 assert path.read_bytes()==retained and len(provider.calls)==1
