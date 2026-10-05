"""Trusted normal-entry roots and fail-closed actual reservation overrun proofs."""
import hashlib,json,subprocess
from pathlib import Path
import pytest
from test_trusted_git import _new_repo,_bootstrap_git
from test_source_concept_task_budget import ledger

@pytest.mark.parametrize('control',['path_result','inherited_git_dir'])
def test_launcher_root_cannot_follow_untrusted_common_directory(tmp_path,monkeypatch,control):
 from scripts.production_pixiv_a2_evidence import configured_launcher_root
 repo=_new_repo(tmp_path);foreign=tmp_path/'foreign';(foreign/'.git').mkdir(parents=True)
 original=subprocess.check_output
 def false_git(args,*a,**kw):
  if args[0]=='git':return str(foreign/'.git')+'\n'
  return original(args,*a,**kw)
 monkeypatch.setattr(subprocess,'check_output',false_git)
 if control=='inherited_git_dir':monkeypatch.setenv('GIT_DIR',str(foreign/'.git'))
 assert configured_launcher_root(repo)==repo

def test_launcher_root_uses_verified_main_root_for_real_linked_worktree(tmp_path):
 from scripts.production_pixiv_a2_evidence import configured_launcher_root
 repo=_new_repo(tmp_path);linked=tmp_path/'linked';_bootstrap_git(repo,'worktree','add','-b','linked',str(linked))
 assert configured_launcher_root(linked)==repo

def test_forged_normal_entry_cannot_borrow_path_selected_launcher_root(tmp_path,monkeypatch):
 from scripts import production_pixiv_a2_evidence as evidence
 from test_production_pixiv_review68 import bind_launcher_fixture
 repo=_new_repo(tmp_path);foreign=tmp_path/'foreign';foreign.mkdir()
 original_root=evidence.configured_launcher_root
 launch={'after_pid':123,'database':'prod',
  'normal_entry_invocation':{'executable':str(foreign/'V.I.O.L.E.T. Production Launcher.exe'),'arguments':[],'action':'Restart','sha256':'a'*64},
  'server_process_at_action':{'ProcessId':123,'ParentProcessId':45,'CreationDate':'time','CommandLine':'python run.py'},
  'profile_at_action':{'candidate_head':'b'*40,'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False,'database':'prod','code_root':str(repo),'sha256':'c'*64}}
 bind_launcher_fixture(launch,foreign,monkeypatch);monkeypatch.setattr(evidence,'configured_launcher_root',original_root)
 controller=repo/'scripts/violet_production_control.py';controller.parent.mkdir();controller.write_text('# fixture')
 profile=repo/'.local_manifests/production_launcher/production-profile.json';profile.parent.mkdir(parents=True)
 profile.write_text(json.dumps({'candidate_head':'b'*40,'repo_root':str(repo),'db':{'name':'prod'},'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False}))
 runtime=foreign/'.local_manifests/production_launcher/launcher-runtime.json'
 runtime.write_text(json.dumps({'repo_root':str(repo),'controller':str(controller),'profile':'production-default'}))
 for name,path in [('controller',controller),('profile',profile),('runtime',runtime)]:
  launch['normal_entry_provenance'][name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 launch['profile_at_action']['sha256']=launch['normal_entry_provenance']['profile']['sha256']
 launch['normal_entry_provenance']['process_chain_at_action'][2]['CommandLine']=f'python "{controller}" restart --profile production-default'
 original=subprocess.check_output
 def false_git(args,*a,**kw):
  if args[0]=='git':return str(foreign/'.git')+'\n'
  return original(args,*a,**kw)
 monkeypatch.setattr(subprocess,'check_output',false_git)
 with pytest.raises(ValueError,match='configured_file_changed'):
  evidence.verify_normal_entry_provenance(launch,repo)

@pytest.mark.parametrize('cap',[0.002,10])
@pytest.mark.parametrize('success',[False,True])
def test_overrun_is_charged_as_failed_and_cannot_recover_publication(tmp_path,cap,success):
 from app.services.source_concept_budget import AdjudicationBudgetBlocked
 book=ledger(tmp_path,cap);ticket=book.reserve('paid',[]);usage={'prompt_tokens':100,'completion_tokens':10000}
 with pytest.raises(AdjudicationBudgetBlocked,match='usage_exceeded'):
  book.settle(ticket,usage,success=success)
 row=json.loads(book.path.read_text())['calls'][0]
 assert row['status']=='failed' and row['business_valid'] is False
 assert row['charged_microusd']==book._cost(**{'input_tokens':100,'output_tokens':10000})>row['reserved_microusd']
 retained=book.path.read_bytes()
 with pytest.raises(AdjudicationBudgetBlocked,match='usage_exceeded'):
  book.settle(ticket,usage,success=success)
 assert book.recover_response(key='paid',reservation=ticket,usage=usage,business_valid=True) is False
 with pytest.raises(AdjudicationBudgetBlocked,match='cached_response'):
  book.require_cached_response(key='paid',reservation=ticket)
 assert book.path.read_bytes()==retained

@pytest.mark.parametrize('cap',[0.002,10])
@pytest.mark.parametrize('by_reservation',[False,True])
def test_old_successful_overrun_cache_is_rejected_read_only(tmp_path,cap,by_reservation):
 from app.services.source_concept_budget import AdjudicationBudgetBlocked
 book=ledger(tmp_path,cap);ticket=book.reserve('paid',[]);usage={'prompt_tokens':100,'completion_tokens':10000}
 with pytest.raises(AdjudicationBudgetBlocked):book.settle(ticket,usage,success=True)
 state=json.loads(book.path.read_text());state['calls'][0].update(status='success',business_valid=True)
 book.path.write_text(json.dumps(state));retained=book.path.read_bytes()
 with pytest.raises(AdjudicationBudgetBlocked,match='cached_response'):
  book.require_cached_response(key='paid',reservation=ticket if by_reservation else None)
 assert book.path.read_bytes()==retained

def test_cached_success_cannot_admit_a_historical_total_cap_overrun(tmp_path):
 from app.services.source_concept_budget import AdjudicationBudgetBlocked
 book=ledger(tmp_path,10)
 for key in ('first','second'):
  ticket=book.reserve(key,[]);book.settle(ticket,{'prompt_tokens':100,'completion_tokens':50},success=True)
 state=json.loads(book.path.read_text());state['cap_microusd']=200
 book.path.write_text(json.dumps(state));book=ledger(tmp_path,0.0002);retained=book.path.read_bytes()
 with pytest.raises(AdjudicationBudgetBlocked,match='cached_response'):
  book.require_cached_response(key='first')
 assert book.path.read_bytes()==retained

@pytest.mark.parametrize('layer',['role','pair'])
@pytest.mark.parametrize('legacy',[False,True])
def test_native_paid_source_cannot_accept_a_successful_reservation_overrun(tmp_path,monkeypatch,layer,legacy):
 from app.services.production_pixiv_release_provenance import verify_role_response_sources,verify_selected_judgment_sources
 if layer=='role':
  from test_production_pixiv_release_inputs import successful_role_facts
  value,vocab,facts,provider,book=successful_role_facts(tmp_path);state=json.loads(book.path.read_text())
  if legacy:
   for path in (tmp_path/'roles/raw').glob('*.json'):
    raw=json.loads(path.read_text());raw.pop('budget_response',None);path.write_text(json.dumps(raw))
  check=lambda:verify_role_response_sources(value,vocab,facts,tmp_path/'roles',state)
 else:
  from test_production_pixiv_adjudication import MeteredProvider,config
  from test_phase45_sc1_source_concept_resolver import _eligible_llm_edges
  from app.services import source_concept_resolver_service as resolver
  signals,edges=_eligible_llm_edges(1);provider=MeteredProvider();cfg=config(tmp_path)
  monkeypatch.setattr(resolver,'primary_openai_provider_from_settings',lambda:(provider,{}))
  judgments,_=resolver.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
  state=json.loads((tmp_path/'budget.json').read_text())
  if legacy:
   for path in (resolver._cache_root(cfg)/'records').glob('*.json'):
    raw=json.loads(path.read_text());raw.pop('budget_response',None);path.write_text(json.dumps(raw))
  check=lambda:verify_selected_judgment_sources(edges,signals,judgments,cfg,state)
 for row in state['calls']:row.update(input_token_ceiling=0,output_token_ceiling=0,reserved_microusd=0)
 with pytest.raises(ValueError,match='semantic_'):check()
