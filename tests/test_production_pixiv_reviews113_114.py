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
 # Schema revalidation and actual raw provenance are separate. Runtime reuse
 # now requires the retained original file, including a paid failed sibling.
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

def test_role_cache_ticket_and_projection_cannot_replace_missing_real_raw(tmp_path):
 import app.services.production_pixiv_role_extraction as roles
 from test_production_pixiv_role_extraction import Provider,multiple_units,task_budget
 from app.services.source_name_candidate_extraction_service import extraction_messages
 from app.services.source_concept_budget import AdjudicationBudgetBlocked
 provider=Provider();budget=task_budget(tmp_path,provider);unit=multiple_units()[0];cache=tmp_path/'roles'
 wrapped=roles.BudgetedExtractionProvider(provider,budget,cache,[unit])
 asyncio.run(wrapped.complete_chat(extraction_messages([unit.unit_group])))
 for raw in (cache/'raw').glob('*.json'):raw.unlink()
 debits=budget.path.read_bytes()
 with pytest.raises(AdjudicationBudgetBlocked,match='original_source_not_admitted'):
  roles.extract_production_roles([unit],provider=provider,budget=budget,cache_dir=cache)
 assert budget.path.read_bytes()==debits and len(provider.calls)==1

# Real original requests and tickets; source bytes remain retained in the review pack.
ACTUAL_CORRECTION03_PAIR_CHAIN = '{"records":[{"key":"e801aa30c49042d688cfad106045d7f9a6a54189e2fbff19","sha256":"210d6782649da9a6660ad1fd488317fbfedc8dcff31cbd7f7ebc3f9a1a6a1f5e","record":{"adjudication_policy_version":"source_concept_budget_driven_adjudication_v1","cache_key":"e801aa30c49042d688cfad106045d7f9a6a54189e2fbff19","cache_policy_version":"source_concept_llm_adjudication_cache_v1","compatible_for_exact_reuse":true,"confidence":0.95,"created_at":"2026-09-12T20:06:19.671102+00:00","decision":"same","decision_schema_version":"source_concept_pair_decision_schema_v1","error_state":null,"input_signal_summary":{"left":{"canonical_key":"ブルアカ","context_reason":null,"display_value":"ブルアカ","origin_type":"pixiv_tag_observation","provider":"pixiv","role_hint":"work","signal_key":"production_pixiv:pixiv_tag_observation:v2:0240889566cd5ff6d94b4d77360cb50f","source_kind":"pixiv_tag","status":"active","surface_key":"ブルアカ","trust_tier":"medium","work_context_key":null},"right":{"canonical_key":"ブルーアーカイブ","context_reason":null,"display_value":"ブルーアーカイブ","origin_type":"pixiv_tag_observation","provider":"pixiv","role_hint":"work","signal_key":"production_pixiv:pixiv_tag_observation:v2:f1c187ad9c0efe694278857179b6047a","source_kind":"pixiv_tag","status":"active","surface_key":"ブルーアーカイブ","trust_tier":"medium","work_context_key":null}},"left_signal_key":"production_pixiv:pixiv_tag_observation:v2:0240889566cd5ff6d94b4d77360cb50f","model_label":"gpt-4.1-mini","pair_identity":"25ebf072c82d7e298205c6fb7c8fb7ec0ef14469","pair_payload_hash":"93462435bdea5e7a87e867e0166a6878bf1e60fe","prompt_template_version":"production_pixiv_pair_semantic_identity_v2","provider_label":"primary_openai","provider_mode":"primary_openai","provider_model":"gpt-4.1-mini","provider_policy_version":"primary_openai_compatible_no_fallback_v1","raw_private_payload_label":"source-concept-llm-cache-private-record","reason_code":"ブルアカ is a common abbreviation for ブルーアーカイブ, both refer to the same work","redacted_public_summary":{"decision":"same","provider_label":"primary_openai","provider_model":"gpt-4.1-mini","source_layer_only":true},"resolver_decision":"must_link","resolver_version":"source_concept_resolver_core_v7_alias_union_approval","reused_from_cache_key":"fffaa4a12e451c7d396d857ca0c3e01359342c9a380d0f1a","right_signal_key":"production_pixiv:pixiv_tag_observation:v2:f1c187ad9c0efe694278857179b6047a","run_id":"production-pixiv:dc9c93f4742cc5ea47fb1b028594f8b6","source_layer_only":true}},{"key":"fffaa4a12e451c7d396d857ca0c3e01359342c9a380d0f1a","sha256":"2c6083f46fb6a7e0945e763bd0691cc90bf0dad01cccc9369740bc6b0025d59a","record":{"adjudication_policy_version":"source_concept_budget_driven_adjudication_v1","cache_key":"fffaa4a12e451c7d396d857ca0c3e01359342c9a380d0f1a","cache_policy_version":"source_concept_llm_adjudication_cache_v1","compatible_for_exact_reuse":true,"confidence":0.95,"created_at":"2026-09-12T18:17:12.663120+00:00","decision":"same","decision_schema_version":"source_concept_pair_decision_schema_v1","error_state":null,"input_signal_summary":{"left":{"canonical_key":"ブルアカ","context_reason":null,"display_value":"ブルアカ","origin_type":"pixiv_tag_observation","provider":"pixiv","role_hint":"work","signal_key":"production_pixiv:pixiv_tag_observation:v2:77252e2b5858be3d8e06e59a3f9dc883","source_kind":"pixiv_tag","status":"active","surface_key":"ブルアカ","trust_tier":"medium","work_context_key":null},"right":{"canonical_key":"ブルーアーカイブ","context_reason":null,"display_value":"ブルーアーカイブ","origin_type":"pixiv_tag_observation","provider":"pixiv","role_hint":"work","signal_key":"production_pixiv:pixiv_tag_observation:v2:e188a264b87578180c442230d8ff80ef","source_kind":"pixiv_tag","status":"active","surface_key":"ブルーアーカイブ","trust_tier":"medium","work_context_key":null}},"left_signal_key":"production_pixiv:pixiv_tag_observation:v2:77252e2b5858be3d8e06e59a3f9dc883","model_label":"gpt-4.1-mini","pair_identity":"76c5d087d70f335d7dcb7d2d8b637634ca7383b7","pair_payload_hash":"0654dbe73fea1fd2432ca3575667938b4d04cc21","prompt_template_version":"production_pixiv_pair_semantic_identity_v2","provider_label":"primary_openai","provider_mode":"primary_openai","provider_model":"gpt-4.1-mini","provider_policy_version":"primary_openai_compatible_no_fallback_v1","raw_private_payload_label":"source-concept-llm-cache-private-record","reason_code":"ブルアカ is a common abbreviation for ブルーアーカイブ, both refer to the same work","redacted_public_summary":{"decision":"same","provider_label":"primary_openai","provider_model":"gpt-4.1-mini","source_layer_only":true},"resolver_decision":"must_link","resolver_version":"source_concept_resolver_core_v7_alias_union_approval","reused_from_cache_key":"c68195bd6b6e7df6d6ca6b4d47736aa996230dfb664cfaa3","right_signal_key":"production_pixiv:pixiv_tag_observation:v2:e188a264b87578180c442230d8ff80ef","run_id":"production-pixiv:dc9c93f4742cc5ea47fb1b028594f8b6","source_layer_only":true}},{"key":"c68195bd6b6e7df6d6ca6b4d47736aa996230dfb664cfaa3","sha256":"3e16b66cad7d8f927ba2e24a51fc6c560f697caba6f41a26feadbd3ca04cd83a","record":{"adjudication_policy_version":"source_concept_budget_driven_adjudication_v1","budget_response":{"attempt":2,"key":"decision-input:05c1b1b6c8b9b0b12a9dc5961f3d6c02686ebad5:prompt:production_pixiv_pair_semantic_identity_v2","reservation":"5821c91ba91c40b9aa2e79d1a999f7c2","usage":{"completion_tokens":37,"prompt_tokens":526,"total_tokens":563}},"cache_key":"c68195bd6b6e7df6d6ca6b4d47736aa996230dfb664cfaa3","cache_policy_version":"source_concept_llm_adjudication_cache_v1","compatible_for_exact_reuse":true,"confidence":0.95,"created_at":"2026-09-12T17:35:34.963811+00:00","decision":"same","decision_schema_version":"source_concept_pair_decision_schema_v1","error_state":null,"input_signal_summary":{"left":{"canonical_key":"ブルアカ","context_reason":null,"display_value":"ブルアカ","origin_type":"pixiv_tag_observation","provider":"pixiv","role_hint":"work","signal_key":"production_pixiv:pixiv_tag_observation:v2:03467ad3235891a0e105c89dd0ff89d0","source_kind":"pixiv_tag","status":"active","surface_key":"ブルアカ","trust_tier":"medium","work_context_key":null},"right":{"canonical_key":"ブルーアーカイブ","context_reason":null,"display_value":"ブルーアーカイブ","origin_type":"pixiv_tag_observation","provider":"pixiv","role_hint":"work","signal_key":"production_pixiv:pixiv_tag_observation:v2:e6a54fa9e09903894c8140f00826af49","source_kind":"pixiv_tag","status":"active","surface_key":"ブルーアーカイブ","trust_tier":"medium","work_context_key":null}},"left_signal_key":"production_pixiv:pixiv_tag_observation:v2:03467ad3235891a0e105c89dd0ff89d0","model_label":"gpt-4.1-mini","pair_identity":"51286d292cae7669d22d37548d5669e0543c36d8","pair_payload_hash":"82d20b0c4ca46b4952cb71e70266d81d863b2cd8","prompt_template_version":"production_pixiv_pair_semantic_identity_v2","provider_label":"primary_openai","provider_mode":"primary_openai","provider_model":"gpt-4.1-mini","provider_policy_version":"primary_openai_compatible_no_fallback_v1","raw_private_payload_label":"source-concept-llm-cache-private-record","reason_code":"ブルアカ is a common abbreviation for ブルーアーカイブ, both refer to the same work","redacted_public_summary":{"decision":"same","provider_label":"primary_openai","provider_model":"gpt-4.1-mini","source_layer_only":true},"resolver_decision":"must_link","resolver_version":"source_concept_resolver_core_v7_alias_union_approval","right_signal_key":"production_pixiv:pixiv_tag_observation:v2:e6a54fa9e09903894c8140f00826af49","run_id":"production-pixiv:dc9c93f4742cc5ea47fb1b028594f8b6","source_layer_only":true}}],"original_ledger_rows":[{"business_valid":false,"charged_microusd":2133,"id":"06827cdc36214f33a9a06b4aa574252b","input_token_ceiling":2931,"key":"decision-input:05c1b1b6c8b9b0b12a9dc5961f3d6c02686ebad5:prompt:production_pixiv_pair_semantic_identity_v2","logical_keys":["decision-input:05c1b1b6c8b9b0b12a9dc5961f3d6c02686ebad5"],"output_token_ceiling":600,"reserved_microusd":2133,"status":"failed","usage":null,"usage_known":false},{"business_valid":true,"charged_microusd":270,"id":"5821c91ba91c40b9aa2e79d1a999f7c2","input_token_ceiling":2931,"key":"decision-input:05c1b1b6c8b9b0b12a9dc5961f3d6c02686ebad5:prompt:production_pixiv_pair_semantic_identity_v2","logical_keys":["decision-input:05c1b1b6c8b9b0b12a9dc5961f3d6c02686ebad5"],"output_token_ceiling":600,"reserved_microusd":2133,"status":"success","usage":{"completion_tokens":37,"prompt_tokens":526},"usage_known":true}],"admission_key":"decision-input:05c1b1b6c8b9b0b12a9dc5961f3d6c02686ebad5:prompt:production_pixiv_pair_semantic_identity_v2"}'


@pytest.mark.parametrize('change',['valid','source_failed','source_revoked','source_usage','source_ticket',
 'wrong_source_question','source_answer','source_model','source_prompt','cycle','missing_source','child_ticket',
 'source_reason','source_cache_policy','source_identity','source_attempt','source_escape','conflicting_copy',
 'current_model','current_prompt','legacy_ambiguous'])
def test_runtime_reuse_follows_actual_paid_source_without_rewriting_history(tmp_path,change):
 from test_production_pixiv_adjudication import config,service
 from app.services.source_concept_budget import AdjudicationBudget
 actual=json.loads(ACTUAL_CORRECTION03_PAIR_CHAIN)
 cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION,model_label='gpt-4.1-mini')
 root=service._cache_root(cfg);(root/'records').mkdir(parents=True)
 for entry in actual['records']:
  (root/'records'/(entry['key']+'.json')).write_text(json.dumps(entry['record'],ensure_ascii=False),encoding='utf-8')
 current=actual['records'][0]['record'];terminal=actual['records'][-1]['record']
 book=AdjudicationBudget(cfg.task_budget_path,model=cfg.model_label,cap_usd=cfg.max_budget_usd,
  input_per_million=cfg.input_price_per_million,output_per_million=cfg.output_price_per_million)
 state={**book.identity,'calls':actual['original_ledger_rows']}
 original_success=state['calls'][1]
 if change=='source_failed':original_success['status']='failed';original_success['business_valid']=False
 elif change=='source_revoked':original_success['business_valid']=False
 elif change=='source_usage':terminal['budget_response']['usage']['prompt_tokens']+=1
 elif change=='source_ticket':terminal['budget_response']['reservation']=state['calls'][0]['id']
 elif change=='wrong_source_question':terminal['input_signal_summary']['left']['work_context_key']='unrelated-original-question'
 elif change=='source_answer':terminal['decision']='different'
 elif change=='source_model':terminal['provider_model']='another-model'
 elif change=='source_prompt':terminal['prompt_template_version']='another-prompt'
 elif change=='cycle':terminal['reused_from_cache_key']=current['cache_key']
 elif change=='missing_source':(root/'records'/(terminal['cache_key']+'.json')).unlink()
 elif change=='child_ticket':current['budget_response']={**terminal['budget_response'],'reservation':state['calls'][0]['id']}
 elif change=='source_reason':terminal['reason_code']='another-answer'
 elif change=='source_cache_policy':terminal['cache_policy_version']='another-policy'
 elif change=='source_identity':terminal['cache_key']='a'*48
 elif change=='source_attempt':terminal['budget_response']['attempt']=1
 elif change=='source_escape':current['reused_from_cache_key']='../../outside-the-cache'
 elif change=='conflicting_copy':
  duplicate=tmp_path/'other-cache';(duplicate/'records').mkdir(parents=True)
  changed={**terminal,'reason_code':'conflicting-source'}
  (duplicate/'records'/(terminal['cache_key']+'.json')).write_text(json.dumps(changed),encoding='utf-8')
  cfg=replace(cfg,semantic_cache_dirs=(str(duplicate),))
 elif change=='current_model':current['provider_model']='another-model'
 elif change=='current_prompt':current['prompt_template_version']='another-prompt'
 elif change=='legacy_ambiguous':terminal.pop('budget_response')
 if change!='missing_source':
  (root/'records'/(actual['records'][-1]['key']+'.json')).write_text(json.dumps(terminal,ensure_ascii=False),encoding='utf-8')
 (root/'records'/(current['cache_key']+'.json')).write_text(json.dumps(current,ensure_ascii=False),encoding='utf-8')
 book.path.write_text(json.dumps(state),encoding='utf-8')
 before=book.path.read_bytes();raw_before={p:p.read_bytes() for p in (root/'records').glob('*.json')}
 if change=='valid':service._admit_budgeted_cached_judgment(book,current,admission_key=actual['admission_key'],config=cfg)
 else:
  with pytest.raises((ValueError,RuntimeError)):
   service._admit_budgeted_cached_judgment(book,current,admission_key=actual['admission_key'],config=cfg)
 assert book.path.read_bytes()==before and raw_before=={p:p.read_bytes() for p in (root/'records').glob('*.json')}
 assert json.loads(before)['calls'][0]['status']=='failed' and json.loads(before)['calls'][0]['charged_microusd']==2133
