import copy
import hashlib
import json
import subprocess
import pytest


def test_existing_state_projection_carry_forward_already_allows_current_phase(tmp_path):
    from scripts.trusted_git import candidate_behavior_carry_forward
    def git(*args):return subprocess.check_output(['git','-C',str(tmp_path),*args],text=True).strip()
    git('init','-q');git('config','user.name','fixture');git('config','user.email','fixture@example.invalid')
    p=tmp_path/'docs/state/current-phase.json';p.parent.mkdir(parents=True)
    p.write_text('{"target_met":false}');(tmp_path/'run.py').write_text('value=1')
    git('add','.');git('commit','-qm','before');head=git('rev-parse','HEAD')
    p.write_text('{"target_met":true}')
    assert candidate_behavior_carry_forward(tmp_path,head)
    git('add','.');git('commit','-qm','state projection')
    assert candidate_behavior_carry_forward(tmp_path,head)
    (tmp_path/'run.py').write_text('value=2')
    assert not candidate_behavior_carry_forward(tmp_path,head)


@pytest.mark.parametrize('change',['unrelated','opposite','missing_cooccurrence'])
def test_precision_source_binds_names_to_original_tags(tmp_path,change):
    from scripts.production_pixiv_precision_evidence import verify_precision_sources
    from test_production_pixiv_precision_evidence import fixture
    controls=fixture()[1]
    source={'identity':{'current_database':'db','system_identifier':'system'},'metadata':[
        {'media_id':i,'provider':'pixiv','source_work_id':str(i),'source_page_index':0,
            'raw_metadata_json':{'id':i,'page_count':1,'user':{'id':1},'title':'title',
                'tags':{1:['Alpha'],2:['Beta'],3:['Alpha','Beta']}[i]}} for i in (1,2,3)]}
    def freeze():
        p=tmp_path/'source.json';p.write_text(json.dumps(source));digest=hashlib.sha256(p.read_bytes()).hexdigest()
        controls.update(independent_source=p.name,source_evidence_sha256=digest)
        for c in controls['identity_separations']+controls['cooccurrence_controls']:c['source_evidence_sha256']=digest
    freeze();verify_precision_sources(controls,tmp_path,database='db',system_identifier='system')
    if change=='unrelated':source['metadata'][0]['raw_metadata_json']['tags']=['Unrelated']
    elif change=='opposite':source['metadata'][0]['raw_metadata_json']['tags']=['Alpha','Beta']
    else:source['metadata'][2]['raw_metadata_json']['tags']=['Alpha']
    freeze()
    with pytest.raises(ValueError,match='source_.*name_relation'):
        verify_precision_sources(controls,tmp_path,database='db',system_identifier='system')


def test_cooccurrence_cannot_be_satisfied_by_one_merged_identity():
    from scripts.production_pixiv_precision_evidence import recompute_precision
    from test_production_pixiv_precision_evidence import fixture
    quality,controls=fixture();controls['identity_separations']=[]
    assert recompute_precision(quality,controls)['case_count']==1
    for row in quality['projection_rows']:row[3]=10
    quality['queries']={q:{'status_code':200,'ids':ids,'total':len(ids)} for q,ids in
        [('"Alpha"',[1,2,3]),('"Beta"',[1,2,3]),('"Alpha" "Beta"',[1,2,3]),('"Alpha" -"Beta"',[]),('"Beta" -"Alpha"',[])]}
    with pytest.raises(ValueError,match='cooccurrence_identities'):
        recompute_precision(quality,controls)


@pytest.mark.parametrize('change',['head','database','system','clock','result','actual_result','missing'])
def test_source_performance_requires_actual_execution_and_results(change):
    from scripts.production_pixiv_a2_evidence import recompute_workload
    from test_production_pixiv_a2_evidence import workload_fixture,workload_launch_fixture
    value,baseline,cases=workload_fixture();row=value['source_layer_measurements'][0]
    kwargs={'launch':workload_launch_fixture(),'system_identifier':'system','source_results':{'0':[],'1':[]}}
    assert recompute_workload(value,baseline,cases,**kwargs)[0]['p95_ms']==1
    if change in {'head','database','system'}:row['execution'][{'head':'candidate_head','database':'database','system':'system_identifier'}[change]]='other'
    elif change=='clock':row['ms']=0.000001
    elif change=='result':row['ids']=[123]
    elif change=='actual_result':kwargs['source_results']['0']=[456]
    else:row.pop('execution')
    with pytest.raises(ValueError,match='workload_source_'):
        recompute_workload(value,baseline,cases,**kwargs)


@pytest.mark.parametrize('change',['command','version','binary','canary','arguments','none'])
def test_provider_command_cannot_be_selected_by_private_canary(tmp_path,monkeypatch,change):
    from scripts import production_pixiv_metadata_entrypoint as gate
    exe=tmp_path/'gallery-dl.exe';exe.write_bytes(b'fixture binary, never execute')
    auth={'route_viable':True,'entrypoint':{'command':[str(exe)],'version':'1.32.1','mode':'external_executable_mode'}}
    anchor={'accepted_canary_fingerprint':hashlib.sha256(json.dumps(auth,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'entrypoint':copy.deepcopy(auth['entrypoint']),'files':[{'path':str(exe),'sha256':hashlib.sha256(exe.read_bytes()).hexdigest()}]}
    directory=tmp_path/'docs/state';directory.mkdir(parents=True)
    private=tmp_path/'.local_manifests/pixiv-a2';private.mkdir(parents=True)
    raw=json.dumps(anchor).encode();(private/'identity.json').write_bytes(raw)
    (directory/'production-pixiv-a2-metadata-entrypoint.json').write_text(json.dumps({
        'private_identity_file':'identity.json','private_identity_sha256':hashlib.sha256(raw).hexdigest()}))
    monkeypatch.setattr(gate,'ROOT',tmp_path)
    prefix=gate.verify_metadata_entrypoint(auth)
    command=[*prefix,'--dump-json','--no-download','https://www.pixiv.net/artworks/123']
    if change=='command':auth['entrypoint']['command']=['arbitrary.exe']
    elif change=='version':auth['entrypoint']['version']='other'
    elif change=='binary':exe.write_bytes(b'changed')
    elif change=='canary':auth['unrelated_new_authority']=True
    elif change=='arguments':command.insert(-1,'--exec=arbitrary')
    if change=='none':assert gate.verify_metadata_entrypoint(auth,command=command)==prefix
    else:
        with pytest.raises(ValueError,match='metadata_entrypoint'):
            gate.verify_metadata_entrypoint(auth,command=command)
