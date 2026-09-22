"""Current A2 entrypoint counterexamples, independent of provider execution."""
import copy
import json
import sys
from pathlib import Path
import pytest
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'backend'),str(root/'tests')]
from test_production_pixiv_review63 import browser
from test_production_pixiv_a2_evidence import quality_fixture,workload_launch_fixture
from scripts.production_pixiv_a2_evidence import recompute_quality,verify_browser_actions,verify_launcher_action

@pytest.mark.parametrize('mode',['exact','recovery','semantic','legacy','prior'])
def test_pair_cache_reads_reject_resolved_escape(tmp_path,monkeypatch,mode):
    from app.services import source_concept_resolver_service as resolver
    from app.services.production_pixiv_pair_correction import verify_correction_prior_sources
    cache=tmp_path/'cache';cache.mkdir()
    relative={'exact':'records/key.json','recovery':'response-recovery/key.json',
        'semantic':'records/key.json','legacy':'key.json','prior':'records/key.json'}[mode]
    target=cache/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('{}')
    outside=tmp_path/'outside.json';outside.write_text('{}')
    original=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda path,*a,**kw: original(outside if path==target else path,*a,**kw))
    config=resolver.LLMAdjudicationConfig(durable_cache_dir=str(cache),semantic_cache_reuse=True)
    with pytest.raises(ValueError,match='outside_root'):
        if mode in {'exact','recovery'}:
            resolver._load_exact_cache_record(cache,metadata={'cache_key':'key','pair_identity':'pair'},config=config)
        elif mode=='semantic':resolver._compatible_decision_cache(config)
        elif mode=='legacy':resolver._legacy_cache_record(legacy_dirs=[cache],legacy_fingerprint='key')
        else:verify_correction_prior_sources([{'left_signal_key':'a','right_signal_key':'b','cache_key':'key'}],config,{'calls':[]})

def test_explicit_legacy_cache_root_remains_supported(tmp_path):
    from app.services import source_concept_resolver_service as resolver
    legacy=tmp_path/'explicit-history';legacy.mkdir()
    record={'decision':'must_link'}
    (legacy/'key.json').write_text(json.dumps(record))
    assert resolver._legacy_cache_record(legacy_dirs=[legacy],legacy_fingerprint='key')==record

@pytest.mark.skipif(sys.platform!='win32',reason='Windows extended path spelling')
@pytest.mark.parametrize('outside',[False,True])
def test_cache_boundary_normalizes_extended_windows_spelling_after_resolution(tmp_path,monkeypatch,outside):
    from app.services.source_concept_resolver_service import checked_cache_path
    cache=tmp_path/'cache';cache.mkdir()
    target=cache/'units'/'new.json'
    actual=(tmp_path/'outside'/'new.json') if outside else target
    extended=Path('\\\\?\\'+str(actual))
    original=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda path,*a,**kw:extended if path==target else original(path,*a,**kw))
    if outside:
        with pytest.raises(ValueError,match='outside_root'):checked_cache_path(cache,target)
    else:assert checked_cache_path(cache,target)==extended

def test_must_link_shared_wrong_extra_is_rejected():
    value=quality_fixture()[0]
    for row in value['queries'].values():row['ids'].append(999)
    oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}],
        'identity_precision_controls':[{'names':['a','b'],'forbidden_media_ids':[999],
            'source_evidence':{'kind':'independent_fixture','media_id':999,'roles':['unrelated_identity']}}]}
    with pytest.raises(ValueError):recompute_quality(value,oracle)

def test_old_tag_missing_page_navigation_is_rejected():
    value=browser()
    value['old_tag'].pop('url')
    with pytest.raises(ValueError):verify_browser_actions(value,launch=workload_launch_fixture())

def test_empty_suggestion_content_is_rejected():
    value=browser();value['old_tag'].update(url=value['base_url']+'/?q=1girl',attempt_id=value['attempt_id'])
    value['suggestion_display']={'url':value['base_url']+'/media/20','request_url':value['base_url']+'/api/media/20'}
    with pytest.raises(ValueError):verify_browser_actions(value,launch=workload_launch_fixture())

def test_unbound_normal_executable_is_rejected(tmp_path):
    launch={'after_pid':123,'database':'prod',
        'normal_entry_invocation':{'executable':str(tmp_path/'V.I.O.L.E.T. Production Launcher.exe'),'arguments':[],'action':'Restart','sha256':'a'*64},
        'server_process_at_action':{'ProcessId':123,'ParentProcessId':45,'CreationDate':'time','CommandLine':'python run.py'},
        'profile_at_action':{'candidate_head':'b'*40,'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False,'database':'prod','code_root':str(tmp_path),'sha256':'c'*64}}
    with pytest.raises(ValueError):verify_launcher_action(launch,tmp_path,'b'*40)

@pytest.mark.parametrize('directory',['raw','raw/attempts','response-recovery'])
def test_role_replay_rejects_resolved_outside_cache(tmp_path,monkeypatch,directory):
    from app.services.production_pixiv_role_extraction import BudgetedExtractionProvider
    cache=tmp_path/'cache';target=cache/directory;target.mkdir(parents=True)
    raw=target/'record.json';raw.write_text('{}')
    outside=tmp_path/'outside.json';outside.write_text('{}')
    original=Path.resolve
    def resolve(path,*args,**kwargs):
        return original(outside,*args,**kwargs) if path==raw else original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',resolve)
    provider=object.__new__(BudgetedExtractionProvider)
    provider.cache_dir=cache;provider.units={};provider.model='gpt-4.1-mini'
    with pytest.raises(ValueError,match='outside'):provider.replay_saved_raw()


def bind_launcher_fixture(launch,tmp_path,monkeypatch):
    import hashlib
    from scripts import production_pixiv_a2_evidence as evidence
    monkeypatch.setattr(evidence,'configured_launcher_root',lambda repo:tmp_path)
    executable=tmp_path/'V.I.O.L.E.T. Production Launcher.exe'
    runtime=tmp_path/'.local_manifests/production_launcher/launcher-runtime.json'
    profile=runtime.with_name('production-profile.json');controller=tmp_path/'scripts/violet_production_control.py'
    runtime.parent.mkdir(parents=True,exist_ok=True);controller.parent.mkdir(parents=True,exist_ok=True)
    executable.write_bytes(b'fixture-executable');controller.write_text('# fixture')
    profile.write_text(json.dumps({'candidate_head':'b'*40,'repo_root':str(tmp_path),'db':{'name':'prod'},
        'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False}))
    runtime.write_text(json.dumps({'repo_root':str(tmp_path),'controller':str(controller),'profile':'production-default'}))
    files={name:{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        for name,path in [('executable',executable),('runtime',runtime),('profile',profile),('controller',controller)]}
    launch['normal_entry_invocation'].update(pid=10,sha256=files['executable']['sha256'])
    launch['profile_at_action']['sha256']=files['profile']['sha256']
    launch['server_process_at_action']['ExecutablePath']=str(tmp_path/'python.exe')
    # Portable original -> unpacked Electron -> controller -> server. The
    # retained IDs need not be alive during later evidence verification.
    chain=[{'ProcessId':10,'ParentProcessId':1,'CreationDate':'time','CommandLine':str(executable),'ExecutablePath':str(executable)},
        {'ProcessId':20,'ParentProcessId':10,'CreationDate':'time','CommandLine':'unpacked','ExecutablePath':str(tmp_path/'portable/launcher.exe')},
        {'ProcessId':45,'ParentProcessId':20,'CreationDate':'time','CommandLine':f'python "{controller}" restart --profile production-default','ExecutablePath':str(tmp_path/'python.exe')},launch['server_process_at_action']]
    launch['normal_entry_provenance']={**files,'process_chain_at_action':chain}
    return launch


@pytest.mark.parametrize('change',['valid','different_exe','false_hash','foreign_runtime','broken_parent','missing_controller','missing_chain'])
def test_normal_launcher_config_and_portable_process_chain(tmp_path,monkeypatch,change):
    launch={'after_pid':123,'database':'prod',
        'normal_entry_invocation':{'executable':str(tmp_path/'V.I.O.L.E.T. Production Launcher.exe'),'arguments':[],'action':'Restart','sha256':'a'*64},
        'server_process_at_action':{'ProcessId':123,'ParentProcessId':45,'CreationDate':'time','CommandLine':'python run.py'},
        'profile_at_action':{'candidate_head':'b'*40,'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False,'database':'prod','code_root':str(tmp_path),'sha256':'c'*64}}
    bind_launcher_fixture(launch,tmp_path,monkeypatch)
    if change=='valid':assert verify_launcher_action(launch,tmp_path,'b'*40);return
    e=launch['normal_entry_provenance']
    if change=='different_exe':launch['normal_entry_invocation']['executable']=str(tmp_path/'other/V.I.O.L.E.T. Production Launcher.exe')
    elif change=='false_hash':e['executable']['sha256']='a'*64;launch['normal_entry_invocation']['sha256']='a'*64
    elif change=='foreign_runtime':e['runtime']['path']=str(tmp_path/'other.json')
    elif change=='broken_parent':e['process_chain_at_action'][2]['ParentProcessId']=500
    elif change=='missing_controller':e['process_chain_at_action'][2]['CommandLine']='unrelated command'
    else:e['process_chain_at_action']=[]
    with pytest.raises(ValueError):verify_launcher_action(launch,tmp_path,'b'*40)


@pytest.mark.parametrize('change',['valid','wrong_page','wrong_query','other_attempt','hidden','wrong_content','accepted','wrong_media'])
def test_browser_navigation_and_visible_suggestion_controls(change):
    value=browser()
    if change=='valid':assert verify_browser_actions(value,launch=workload_launch_fixture());return
    if change=='wrong_page':value['old_tag']['url']=value['base_url']+'/media/20?q=1girl'
    elif change=='wrong_query':value['old_tag']['url']=value['base_url']+'/?q=other'
    elif change=='other_attempt':value['old_tag']['attempt_id']='different'
    elif change=='hidden':value['suggestion_display']['observed_items'][0]['visible']=False
    elif change=='wrong_content':value['suggestion_display']['observed_items'][0]['text']='other'
    elif change=='accepted':value['suggestion_display']['api_items'][0]['is_suggestion']=False
    else:value['suggestion_display']['media_id']=21
    with pytest.raises(ValueError):verify_browser_actions(value,launch=workload_launch_fixture())


def test_precision_keeps_legitimate_mixed_search_extras():
    value=quality_fixture()[0]
    for row in value['queries'].values():row['ids'].append(12)
    oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}],
        'identity_precision_controls':[{'names':['a','b'],'forbidden_media_ids':[999],'source_evidence':'independent unrelated fixture'}]}
    assert recompute_quality(value,oracle)['failed_cases']==0


@pytest.mark.parametrize('kind',['raw','question-reconstruction','valid_relative_root'])
def test_release_role_reader_checks_its_actual_cache_boundary(tmp_path,monkeypatch,kind):
    from test_production_pixiv_role_coverage import partial_facts
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value,vocabulary,facts,provider,budget=partial_facts(tmp_path)
    cache=tmp_path/'roles';raw=next((cache/'raw').glob('*.json'))
    if kind=='valid_relative_root':
        monkeypatch.chdir(tmp_path)
        assert verify_role_response_sources(value,vocabulary,facts,Path('roles'),json.loads(budget.path.read_text()))['record_count']==1
        return
    saved=json.loads(raw.read_text());outside=tmp_path/'outside.json';outside.write_text('{}')
    target=raw if kind=='raw' else cache/'question-reconstruction'/f"{saved['input_fingerprint']}.json"
    original=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda p,*a,**kw:original(outside,*a,**kw) if p==target else original(p,*a,**kw))
    with pytest.raises(ValueError,match='outside'):
        verify_role_response_sources(value,vocabulary,facts,cache,json.loads(budget.path.read_text()))
    assert len(provider.calls)==1  # Fixture-only provider, never a real request.
