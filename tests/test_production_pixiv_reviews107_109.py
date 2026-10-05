"""Actual local-source counterexamples for role cache, history, and original debit rows."""
import asyncio,copy,hashlib,json,sys
from pathlib import Path
from xml.etree import ElementTree as ET
import pytest
from test_production_pixiv_role_extraction import Provider,multiple_units,task_budget

@pytest.mark.parametrize('route',['recover','unit_hit'])
@pytest.mark.parametrize('outcome',['failed','revoked'])
def test_role_runtime_never_publishes_or_reuses_revoked_paid_source(tmp_path,route,outcome):
 import app.services.production_pixiv_role_extraction as roles
 from app.services.source_concept_budget import AdjudicationBudgetBlocked
 from app.services.source_name_candidate_extraction_service import extraction_messages
 provider=Provider();budget=task_budget(tmp_path,provider);units=multiple_units()[:1]
 first=roles.BudgetedExtractionProvider(provider,budget,tmp_path/'first',units)
 messages=extraction_messages([units[0].unit_group]);asyncio.run(first.complete_chat(messages))
 saved=json.loads(next((tmp_path/'first/raw').glob('*.json')).read_text())
 state=json.loads(budget.path.read_text());state['calls'][0]['business_valid']=False
 if outcome=='failed':state['calls'][0]['status']='failed'
 budget.path.write_text(json.dumps(state),encoding='utf-8');before=budget.path.read_bytes()
 wrapped=roles.BudgetedExtractionProvider(provider,budget,tmp_path/'recovery',units)
 if route=='recover':
  import shutil
  destination=tmp_path/'recovery/raw';destination.mkdir(parents=True)
  for path in (tmp_path/'first/raw').glob('*.json'):shutil.copyfile(path,destination/path.name)
 with pytest.raises(AdjudicationBudgetBlocked):
  if route=='recover':wrapped.recover_saved(saved)
  else:asyncio.run(first.complete_chat(messages))
 assert not roles._unit_path(tmp_path/'recovery',units[0]).exists()
 assert len(provider.calls)==1 and budget.path.read_bytes()==before

def _authority_case(tmp_path,monkeypatch):
 from scripts import production_pixiv_budget_authority as authority
 original={'cap_microusd':10000000,'calls':[{'id':'original','key':'fixed-question','status':'failed',
  'usage':{'prompt_tokens':1,'completion_tokens':1},'logical_keys':['original-target'],
  'business_valid':False,'reserved_microusd':2,'charged_microusd':2}]}
 raw=json.dumps(original).encode();(tmp_path/'closeout43-budget-before-private.json').write_bytes(raw)
 approved={'previous_cap_microusd':10000000,'cap_microusd':30000000,'call_count_before':1,
  'charged_before_microusd':2,'ledger_before_sha256':hashlib.sha256(raw).hexdigest()}
 state=tmp_path/'docs/state';state.mkdir(parents=True)
 (state/'production-pixiv-a2-budget-authority.json').write_text(json.dumps(approved),encoding='utf-8')
 monkeypatch.setattr(authority,'ROOT',tmp_path)
 ledger={**copy.deepcopy(original),'cap_microusd':30000000,'cap_amendments':[approved]}
 return authority,ledger

@pytest.mark.parametrize('field',['key','status','usage','logical_keys','business_valid'])
def test_task_cap_cannot_accept_rewritten_original_call_fields(tmp_path,monkeypatch,field):
 authority,ledger=_authority_case(tmp_path,monkeypatch)
 assert authority.authorized_task_cap(tmp_path,ledger)==30
 ledger['calls'][0][field]={'key':'other-question','status':'success','usage':{'prompt_tokens':2,'completion_tokens':0},
  'logical_keys':['other-target'],'business_valid':True}[field]
 with pytest.raises(ValueError,match='budget_authority'):authority.authorized_task_cap(tmp_path,ledger)

def test_task_cap_allows_only_one_way_validity_revocation(tmp_path,monkeypatch):
 authority,ledger=_authority_case(tmp_path,monkeypatch)
 assert authority.authorized_task_cap(tmp_path,ledger)==30

@pytest.mark.parametrize('changed',['true_to_false','absent_to_false','false_to_true','false_to_one','usage_type','extra_field'])
def test_original_call_changes_preserve_exact_types_and_only_revoke_trust(tmp_path,monkeypatch,changed):
 authority,ledger=_authority_case(tmp_path,monkeypatch)
 old=copy.deepcopy(ledger['calls'][0]);new=copy.deepcopy(old)
 if changed in ('true_to_false','absent_to_false'):
  if changed=='true_to_false':old['business_valid']=True
  else:old.pop('business_valid')
  new['business_valid']=False;new['business_validation_reason']='current_schema_revalidation_failed'
  assert authority._original_call_preserved(old,new,{})
 else:
  if changed=='false_to_true':new['business_valid']=True
  elif changed=='false_to_one':new['business_valid']=1
  elif changed=='usage_type':new['usage']['prompt_tokens']=True
  else:new['invented_recovery']=True
  assert not authority._original_call_preserved(old,new,{})

@pytest.mark.parametrize('outcome',['failed','revoked','unbacked','valid'])
def test_extract_unit_cache_checks_original_call_without_raw_recovery(tmp_path,outcome):
 import app.services.production_pixiv_role_extraction as roles
 from app.services.source_concept_budget import AdjudicationBudgetBlocked
 from app.services.source_name_candidate_extraction_service import extraction_messages
 provider=Provider();budget=task_budget(tmp_path,provider);units=multiple_units()[:1]
 cache=tmp_path/'roles';first=roles.BudgetedExtractionProvider(provider,budget,cache,units)
 asyncio.run(first.complete_chat(extraction_messages([units[0].unit_group])))
 for path in (cache/'raw').glob('*.json'):path.unlink()
 state=json.loads(budget.path.read_text())
 if outcome=='failed':state['calls'][0]['status']='failed'
 elif outcome=='revoked':state['calls'][0]['business_valid']=False
 elif outcome=='unbacked':
  path=roles._unit_path(cache,units[0]);value=json.loads(path.read_text());value.pop('budget_response')
  path.write_text(json.dumps(value),encoding='utf-8')
 budget.path.write_text(json.dumps(state),encoding='utf-8');before=budget.path.read_bytes()
 # Even a valid paid ticket cannot authenticate a cached projection after
 # its actual original answer bytes have been removed.
 with pytest.raises(AdjudicationBudgetBlocked):roles.extract_production_roles(units,provider=provider,budget=budget,cache_dir=cache)
 assert len(provider.calls)==1 and budget.path.read_bytes()==before

@pytest.mark.parametrize('shortcut',['first','second'])
def test_coverage_repair_cache_shortcuts_require_actual_business_valid_source(tmp_path,monkeypatch,shortcut):
 import app.services.production_pixiv_role_extraction as roles
 from app.services.source_concept_budget import AdjudicationBudgetBlocked
 from app.services.source_name_candidate_extraction_service import extraction_messages
 provider=Provider();budget=task_budget(tmp_path,provider);unit=multiple_units()[0];cache=tmp_path/'roles'
 first=roles.BudgetedExtractionProvider(provider,budget,cache,[unit])
 asyncio.run(first.complete_chat(extraction_messages([unit.unit_group])))
 for path in (cache/'raw').glob('*.json'):path.unlink()
 state=json.loads(budget.path.read_text());state['calls'][0]['business_valid']=False
 budget.path.write_text(json.dumps(state),encoding='utf-8');before=budget.path.read_bytes()
 full=([unit],{'aggregate':unit.extraction_key},{'parent_extraction_keys':{unit.extraction_key:'parent'}})
 plans=iter([([],{},{}),full,full] if shortcut=='second' else [full])
 monkeypatch.setattr(roles,'plan_role_coverage_repair',lambda *a:next(plans))
 with pytest.raises(AdjudicationBudgetBlocked):
  roles.repair_missing_role_coverage(None,None,{},provider=provider,budget=budget,cache_dir=cache)
 assert len(provider.calls)==1 and budget.path.read_bytes()==before

def test_partial_reply_is_retained_without_complete_cache_or_lost_denominator(tmp_path):
 import app.services.production_pixiv_role_extraction as roles
 from test_production_pixiv_role_coverage import context,Responses,candidate
 from app.services.production_pixiv_semantics import build_semantic_vocabulary
 provider=Responses(lambda group:([candidate('KnownName')],[]));budget=task_budget(tmp_path,provider)
 value=context(['KnownName','MissingName']);cache=tmp_path/'roles';vocab=build_semantic_vocabulary([])
 empty={'schema_version':roles.ROLE_SCHEMA,'records':{}}
 result=roles.complete_contextual_production_roles(value,vocab,empty,provider=provider,budget=budget,cache_dir=cache)
 assert result['completion_summary']['unaccounted_requested_tag_occurrences']==1
 assert result['completion_summary']['blocked'] is None
 record=next(iter(result['completion_records'].values()));assert record['source_admission']=='validated_original_unit'
 unit=next(u for u in roles.plan_contextual_role_completion(value,vocab,empty)[0] if u.extraction_key==record['extraction_key'])
 assert roles.role_target_coverage(unit,record)['missing_raw_tags']==['MissingName']
 assert roles._unit_path(cache,unit).is_file()
 assert len(provider.calls)==1
 charged=json.loads(budget.path.read_bytes())['calls'][0]
 assert charged['status']=='failed' and charged['business_valid'] is False
 raw=next((cache/'raw').glob('*.json'));original=raw.read_bytes();ledger=budget.path.read_bytes()
 again=roles.complete_contextual_production_roles(value,vocab,empty,provider=provider,budget=budget,cache_dir=cache)
 assert again['completion_records']==result['completion_records']
 assert len(provider.calls)==1 and budget.path.read_bytes()==ledger and raw.read_bytes()==original

def _historical_case(tmp_path,monkeypatch):
 from scripts import production_pixiv_a2_post_full_fix as contract
 from scripts import check_production_pixiv_a2 as core
 import scripts.trusted_git as trusted
 nodes=[f'tests/test_original_history.py::test_original_{i}' for i in range(88)]
 state=tmp_path/'docs/state';state.mkdir(parents=True)
 (state/'production-pixiv-a2-required-tests.json').write_text(json.dumps({'historical':nodes}),encoding='utf-8')
 monkeypatch.setattr(contract,'verify_registered_delta',lambda *a,**k:{'registry_sha256':'a'*64})
 monkeypatch.setattr(trusted,'candidate_behavior_carry_forward',lambda *a,**k:True)
 monkeypatch.setattr(core,'verify_required_test_command',lambda *a,**k:None)
 monkeypatch.setattr(core,'ROOT',tmp_path)
 receipt={'schema_version':'violet.production-pixiv-a2.post-full-fix-evidence.v1','baseline_head':'full',
  'candidate_head':'candidate','registry_sha256':'a'*64,'additional_full_invocations':0,'behavior_neutral_claimed':False}
 for kind in ('focused','postgresql','historical'):
  selected=nodes if kind=='historical' else ['tests/test_current.py::test_current']
  xml=tmp_path/(kind+'.xml');suite=ET.Element('testsuite')
  for node in selected:
   path,name=node.split('::');ET.SubElement(suite,'testcase',classname=path[:-3].replace('/','.'),name=name)
  ET.ElementTree(suite).write(xml,encoding='utf-8')
  log=(str(len(selected))+' passed in 0.01s\n').encode();(tmp_path/(kind+'.log')).write_bytes(log)
  command={'status':'finished','source_head':'candidate','source_head_after':'candidate','behavior_guard_after':True,
   'started_at':'2026-10-03T00:00:00+00:00','finished_at':'2026-10-03T00:01:00+00:00','cwd':str(tmp_path),
   'argv':[sys.executable,'-m','pytest',*selected,'-v','--junitxml='+str(xml)],'exit_code':0,'log_sha256':hashlib.sha256(log).hexdigest()}
  (tmp_path/(kind+'-command.json')).write_text(json.dumps(command),encoding='utf-8')
  receipt[kind]={'command':kind+'-command.json','log':kind+'.log','xml':kind+'.xml','passed':len(selected),'failed':0,'skipped':0,'errors':0}
 (tmp_path/'full-command.json').write_text(json.dumps({'finished_at':'2026-10-02T00:00:00+00:00'}),encoding='utf-8')
 (tmp_path/'post.json').write_text(json.dumps(receipt),encoding='utf-8')
 return contract,nodes

@pytest.mark.parametrize('change',['tests','python','cwd','xml_nodes','duplicate_nodes'])
def test_post_full_history_cannot_substitute_eighty_eight_unrelated_nodes(tmp_path,monkeypatch,change):
 contract,nodes=_historical_case(tmp_path,monkeypatch)
 path=tmp_path/'historical-command.json';cmd=json.loads(path.read_text())
 if change=='tests':cmd['argv'][3:-2]=['tests/test_unrelated.py']
 elif change=='python':cmd['argv'][0]=str(tmp_path/'other-python.exe')
 elif change=='cwd':cmd['cwd']=str(tmp_path/'elsewhere')
 else:
  tree=ET.parse(tmp_path/'historical.xml');cases=list(tree.iter('testcase'))
  if change=='xml_nodes':cases[0].set('name','test_unrelated')
  else:cases[0].set('name',cases[1].attrib['name'])
  tree.write(tmp_path/'historical.xml',encoding='utf-8')
 path.write_text(json.dumps(cmd),encoding='utf-8')
 with pytest.raises(ValueError,match='historical'):
  contract.verify_post_full_fix(tmp_path,{'post_full_fix':'post.json','command':'full-command.json'},candidate='candidate',root=tmp_path,baseline_result={'source_head':'full'})

def test_post_full_history_accepts_exact_registered_nodes_and_runtime(tmp_path,monkeypatch):
 contract,_=_historical_case(tmp_path,monkeypatch)
 value=contract.verify_post_full_fix(tmp_path,{'post_full_fix':'post.json','command':'full-command.json'},candidate='candidate',root=tmp_path,baseline_result={'source_head':'full'})
 assert value['affected_verification']['historical']['passed']==88
