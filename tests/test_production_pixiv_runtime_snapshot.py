import hashlib
import json
import subprocess

import pytest

from scripts.production_pixiv_runtime_snapshot import (
    CANDIDATE_PROFILE, PROFILE, REGISTRY,
    freeze_candidate_profile, prepare_deployment_snapshot,
    verify_deployment_runtime_binding,
)


def repository(tmp_path):
    root = tmp_path / "development"
    root.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()
    git("init", "-q")
    git("config", "user.name", "Runtime fixture")
    git("config", "user.email", "runtime@example.invalid")
    git("config", "core.longpaths", "true")
    (root / ".gitattributes").write_bytes(b"* text eol=lf\n")
    (root / ".gitignore").write_bytes(b".local_manifests/\ndata/settings.json\n")
    (root / "run.py").write_bytes(b"print('fixed application')\n")
    (root / 'scripts').mkdir()
    (root / 'scripts/violet_production_control.py').write_bytes(b'# fixed controller\n')
    registry = root / REGISTRY
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"repository_files": {}, "candidate_profile": {
        "private_file": "production_launcher/production-profile.json",
        "previous_full_sha256": "0" * 64, "stable_fields_sha256": "1" * 64}}))
    git("add", ".")
    git("commit", "-qm", "Application source")
    head = git("rev-parse", "HEAD")
    profile = root / PROFILE
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({"profile_id": "production-default", "env": "production",
        "repo_root": str(root), "candidate_head": head,
        "pixiv_product_apply_enabled": False, "db": {"name": "blombooru"}}))
    return root, head, profile, git


def test_freeze_cannot_modify_or_promote_production_entry(tmp_path):
    root, head, production, git = repository(tmp_path)
    before = production.read_bytes()
    result = freeze_candidate_profile(root, head, production)
    assert production.read_bytes() == before
    assert json.loads((root / CANDIDATE_PROFILE).read_bytes())["profile_id"] == "production-candidate"
    assert result["production_profile_changed"] is False
    assert result["production_promoted"] is False
    assert git("diff", "--name-only") == ""


def test_fixed_snapshot_retains_application_bytes_and_refuses_drift(tmp_path):
    root, head, production, git = repository(tmp_path)
    before = production.read_bytes()
    destination = root.parent / "production-pixiv-a2-stable-fixture"
    receipt = prepare_deployment_snapshot(root, head, destination, production)
    assert production.read_bytes() == before
    assert (destination / "run.py").read_bytes() == (root / "run.py").read_bytes()
    profile = json.loads((destination / PROFILE).read_bytes())
    assert profile["repo_root"] == str(destination)
    assert profile["candidate_head"] == receipt["deployment_head"]
    assert git("diff", "--name-only", head, receipt["deployment_head"]) == REGISTRY
    assert git("rev-parse", "HEAD") == head
    # The normal launcher uses this existing executable drift gate.
    from scripts import violet_production_control as control
    config = control.resolve_config(repo_root=destination, profile_id="production-default")
    assert control._pinned_candidate_worktree(config)
    (destination / "run.py").write_text("print('unreviewed drift')\n")
    assert not control._pinned_candidate_worktree(config)


def test_existing_deployment_and_candidate_configuration_are_preserved(tmp_path):
    root, head, production, git = repository(tmp_path)
    freeze_candidate_profile(root, head, production)
    with pytest.raises(FileExistsError):
        freeze_candidate_profile(root, head, production)
    with pytest.raises(ValueError, match="new_sibling"):
        prepare_deployment_snapshot(root, head, root, production)
    assert hashlib.sha256(production.read_bytes()).hexdigest()


def test_snapshot_git_ignores_path_shadow_and_inherited_git_controls(tmp_path,monkeypatch):
    from scripts.production_pixiv_runtime_snapshot import _git
    root,head,production,git=repository(tmp_path)
    shadow=tmp_path/'shadow';shadow.mkdir()
    (shadow/'git.cmd').write_text('@echo SHADOW GIT MUST NOT RUN\n',encoding='utf-8')
    monkeypatch.setenv('PATH',str(shadow))
    monkeypatch.setenv('GIT_DIR',str(shadow/'different-repository'))
    assert _git(root,'rev-parse','HEAD').decode().strip()==head


def test_deployment_rebinds_candidate_registry_to_its_own_production_profile(tmp_path):
    root,head,production,git=repository(tmp_path)
    registry=json.loads((root/REGISTRY).read_bytes())
    registry['candidate_profile']['private_file']=CANDIDATE_PROFILE.removeprefix('.local_manifests/')
    registry['private_files']={'production_launcher/production-profile.json':hashlib.sha256(production.read_bytes()).hexdigest()}
    (root/REGISTRY).write_text(json.dumps(registry),encoding='utf-8')
    git('add',REGISTRY);git('commit','-qm','Separate candidate profile');head=git('rev-parse','HEAD')
    before=production.read_bytes();destination=root.parent/'production-pixiv-a2-stable-separated'
    receipt=prepare_deployment_snapshot(root,head,destination,production)
    from scripts import violet_production_control as control
    config=control.resolve_config(repo_root=destination,profile_id='production-default')
    assert control._pinned_candidate_worktree(config)
    deployed=json.loads((destination/REGISTRY).read_bytes())
    assert deployed['candidate_profile']['private_file']==PROFILE.removeprefix('.local_manifests/')
    assert not deployed['private_files'] and production.read_bytes()==before
    assert git('diff','--name-only',head,receipt['deployment_head'])==REGISTRY


def test_snapshot_uses_source_commit_identity_without_changing_git_configuration(tmp_path):
    root, head, production, git = repository(tmp_path)
    git('config', '--unset', 'user.name'); git('config', '--unset', 'user.email')
    before = (root/'.git/config').read_bytes()
    destination = root.parent/'production-pixiv-a2-stable-no-author-config'
    receipt = prepare_deployment_snapshot(root, head, destination, production)
    assert git('show', '-s', '--format=%cn%x00%ce', receipt['deployment_head']) == git('show', '-s', '--format=%cn%x00%ce', head)
    assert (root/'.git/config').read_bytes() == before


def bound_runtime(tmp_path):
    root, _, production, git = repository(tmp_path)
    profile = json.loads(production.read_bytes())
    profile['pixiv_product_enabled'] = True
    production.write_text(json.dumps(profile), encoding='utf-8')
    registry = json.loads((root/REGISTRY).read_bytes())
    registry['candidate_profile']['stable_fields_sha256'] = hashlib.sha256(
        json.dumps({k:v for k,v in profile.items() if k!='candidate_head'},
                   ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    (root/REGISTRY).write_text(json.dumps(registry), encoding='utf-8')
    git('add',REGISTRY); git('commit','-qm','Bind real source configuration')
    head=git('rev-parse','HEAD'); profile['candidate_head']=head
    production.write_text(json.dumps(profile),encoding='utf-8')
    destination=root.parent/'production-pixiv-a2-stable-bound'
    receipt=prepare_deployment_snapshot(root,head,destination,production)
    return root,head,production,git,destination,receipt


def test_fixed_runtime_binding_recomputes_native_source_and_profile(tmp_path):
    root,head,production,git,destination,receipt=bound_runtime(tmp_path)
    result=verify_deployment_runtime_binding(root,head,receipt)
    assert result=={'business_source_head':head,'runtime_head':receipt['deployment_head'],
                   'runtime_root':str(destination),'application_source_bytes_unchanged':True}
    assert git('rev-parse','HEAD')==head
    assert hashlib.sha256(production.read_bytes()).hexdigest()==receipt['original_profile_sha256']


@pytest.mark.parametrize('change',[
    'business_head','deployment_head','source_blob','deployment_blob','profile_hash',
    'original_profile_hash','relative_root','foreign_repository','profile_database',
    'profile_apply','profile_read','profile_extra','tracked_source','ignored_executable',
    'registry_content','deployment_extra_commit','hardlinked_profile'])
def test_fixed_runtime_binding_refuses_forged_receipt_and_runtime_drift(tmp_path,change):
    root,head,production,git,destination,receipt=bound_runtime(tmp_path)
    if change=='business_head':receipt['business_source_head']='a'*40
    elif change=='deployment_head':receipt['deployment_head']=head
    elif change=='source_blob':receipt['source_registry_blob']='a'*40
    elif change=='deployment_blob':receipt['deployment_registry_blob']='a'*40
    elif change=='profile_hash':receipt['profile_sha256']='a'*64
    elif change=='original_profile_hash':receipt['original_profile_sha256']='a'*64
    elif change=='relative_root':receipt['stable_root']=destination.name
    elif change=='foreign_repository':
        foreign=tmp_path/'foreign'; foreign.mkdir()
        subprocess.check_call(['git','-C',str(foreign),'init','-q'])
        receipt['stable_root']=str(foreign)
    elif change.startswith('profile_'):
        path=destination/PROFILE; profile=json.loads(path.read_bytes())
        if change=='profile_database':profile['db']['name']='another_production'
        elif change=='profile_apply':profile['pixiv_product_apply_enabled']=True
        elif change=='profile_read':profile['pixiv_product_enabled']=False
        else:profile['unreviewed_setting']='changed'
        path.write_text(json.dumps(profile),encoding='utf-8')
        receipt['profile_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    elif change=='tracked_source':(destination/'run.py').write_text("print('changed')\n",encoding='utf-8')
    elif change=='ignored_executable':
        (destination/'.local_manifests/unregistered.py').write_text("print('startup')",encoding='utf-8')
    elif change=='registry_content':
        registry=json.loads((destination/REGISTRY).read_bytes());registry['private_files']={'other.json':'a'*64}
        (destination/REGISTRY).write_text(json.dumps(registry),encoding='utf-8')
    elif change=='deployment_extra_commit':
        subprocess.check_call(['git','-C',str(destination),'-c','user.name=Fixture',
            '-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','Unbound commit'])
        receipt['deployment_head']=subprocess.check_output(['git','-C',str(destination),'rev-parse','HEAD']).decode().strip()
    else:
        import os
        os.link(destination/PROFILE,tmp_path/'aliased-profile.json')
    with pytest.raises(ValueError):verify_deployment_runtime_binding(root,head,receipt)


def test_fixed_runtime_binding_refuses_alias_before_resolution(tmp_path,monkeypatch):
    from scripts import trusted_git
    root,head,production,git,destination,receipt=bound_runtime(tmp_path)
    original=trusted_git._assert_no_alias_components
    def reject_alias(path):
        if path==destination:raise trusted_git.TrustedGitError('fixture_alias')
        return original(path)
    monkeypatch.setattr(trusted_git,'_assert_no_alias_components',reject_alias)
    with pytest.raises(ValueError,match='alias'):verify_deployment_runtime_binding(root,head,receipt)


@pytest.mark.parametrize('change',['valid','loaded_drift','deployed_drift','wrong_commit','foreign_repository'])
def test_sibling_controller_guard_proves_git_and_exact_source(tmp_path,change):
    from scripts.trusted_git import _same_registered_controller_source
    root,head,production,git,destination,receipt=bound_runtime(tmp_path)
    loaded=root/'scripts/violet_production_control.py';deployed=destination/'scripts/violet_production_control.py'
    candidate=receipt['deployment_head']
    if change=='loaded_drift':loaded.write_bytes(b'# other loaded controller\n')
    elif change=='deployed_drift':deployed.write_bytes(b'# other deployed controller\n')
    elif change=='wrong_commit':candidate='a'*40
    elif change=='foreign_repository':
        foreign=tmp_path/'foreign';foreign.mkdir();subprocess.check_call(['git','-C',str(foreign),'init','-q'])
        loaded=foreign/'scripts/violet_production_control.py';loaded.parent.mkdir();loaded.write_bytes(deployed.read_bytes())
    assert _same_registered_controller_source(loaded,deployed,candidate)==(change=='valid')


def fixed_launcher_fixture(tmp_path,monkeypatch):
    from scripts import production_pixiv_a2_evidence as evidence
    root,head,production,git,destination,binding=bound_runtime(tmp_path)
    canonical=tmp_path/'canonical-launcher';canonical.mkdir()
    monkeypatch.setattr(evidence,'configured_launcher_root',lambda repo:canonical)
    executable=canonical/'V.I.O.L.E.T. Production Launcher.exe';executable.write_bytes(b'fixture exe')
    runtime=canonical/'.local_manifests/production_launcher/launcher-runtime.json'
    runtime.parent.mkdir(parents=True)
    controller=destination/'scripts/violet_production_control.py';profile=destination/PROFILE
    runtime.write_text(json.dumps({'repo_root':str(destination),'controller':str(controller),
        'profile':'production-default'}),encoding='utf-8')
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    process={'ProcessId':123,'ParentProcessId':45,'CreationDate':'observed at action',
        'CommandLine':f'python "{destination}/run.py"','ExecutablePath':str(tmp_path/'python.exe')}
    identity={'pid':123,'port':8012,'db_name':'blombooru','code_root':str(destination),
        'git_sha':binding['deployment_head'][:7]}
    launch={'candidate_head':head,'fixed_runtime_binding':binding,'database':'blombooru',
        'base_url':'http://127.0.0.1:8012','after_pid':123,'server_identity':identity,
        'code_root':str(destination),
        'normal_entry_invocation':{'executable':str(executable),'pid':10,'arguments':[],
            'action':'Restart','sha256':digest(executable)},
        'server_process_at_action':process,
        'profile_at_action':{'candidate_head':binding['deployment_head'],'database':'blombooru',
            'code_root':str(destination),'pixiv_product_enabled':True,
            'pixiv_product_apply_enabled':False,'sha256':digest(profile)},
        'normal_entry_provenance':{
            **{name:{'path':str(path),'sha256':digest(path)} for name,path in
                [('executable',executable),('runtime',runtime),('controller',controller),('profile',profile)]},
            'process_chain_at_action':[
                {'ProcessId':10,'ParentProcessId':1,'CreationDate':'time','CommandLine':str(executable),'ExecutablePath':str(executable)},
                {'ProcessId':45,'ParentProcessId':10,'CreationDate':'time',
                    'CommandLine':f'python "{controller}" restart --profile production-default','ExecutablePath':str(tmp_path/'python.exe')},
                process]}}
    return root,head,destination,runtime,launch


@pytest.mark.parametrize('change',['valid','business_sha_as_runtime','development_root','anchor_drift',
    'profile_sha','debug_argument','broken_chain','missing_binding'])
def test_normal_entry_accepts_only_the_proved_fixed_runtime(tmp_path,monkeypatch,change):
    from scripts.production_pixiv_a2_evidence import verify_launcher_action
    root,head,destination,runtime,launch=fixed_launcher_fixture(tmp_path,monkeypatch)
    if change=='valid':
        context=verify_launcher_action(launch,root,head)
        assert context['runtime_root']==str(destination)
        assert context['runtime_head']!=head and context['business_source_head']==head
        return
    if change=='business_sha_as_runtime':launch['profile_at_action']['candidate_head']=head
    elif change=='development_root':launch['profile_at_action']['code_root']=str(root)
    elif change=='anchor_drift':
        value=json.loads(runtime.read_bytes());value['repo_root']=str(root)
        runtime.write_text(json.dumps(value),encoding='utf-8')
        launch['normal_entry_provenance']['runtime']['sha256']=hashlib.sha256(runtime.read_bytes()).hexdigest()
    elif change=='profile_sha':launch['profile_at_action']['sha256']='a'*64
    elif change=='debug_argument':launch['normal_entry_invocation']['arguments']=['--remote-debugging-port=9423']
    elif change=='broken_chain':launch['server_process_at_action']['ParentProcessId']=999
    else:launch.pop('fixed_runtime_binding')
    with pytest.raises(ValueError):verify_launcher_action(launch,root,head)


@pytest.mark.parametrize('change',['valid','business_sha_as_runtime','wrong_runtime_sha','wrong_runtime_root',
    'wrong_database','wrong_pid','changed_after','forged_binding','missing_binding'])
def test_browser_and_quality_service_keep_business_and_runtime_identities(tmp_path,monkeypatch,change):
    import copy
    from scripts.production_pixiv_a2_service_evidence import verify_service_observation
    root,head,destination,runtime,launch=fixed_launcher_fixture(tmp_path,monkeypatch)
    observation={'candidate_head':head,'base_url':launch['base_url'],'database':launch['database'],
        'server_identity':copy.deepcopy(launch['server_identity']),
        'server_identity_after':copy.deepcopy(launch['server_identity'])}
    if change=='valid':assert verify_service_observation(observation,launch);return
    if change=='business_sha_as_runtime':observation['server_identity']['git_sha']=head[:7]
    elif change=='wrong_runtime_sha':observation['server_identity']['git_sha']='a'*7
    elif change=='wrong_runtime_root':observation['server_identity']['code_root']=str(root)
    elif change=='wrong_database':observation['server_identity']['db_name']='other_database'
    elif change=='wrong_pid':observation['server_identity']['pid']=124
    elif change=='changed_after':observation['server_identity_after']['pid']=124
    elif change=='forged_binding':launch['fixed_runtime_binding']['source_registry_blob']='a'*40
    else:launch.pop('fixed_runtime_binding')
    with pytest.raises(ValueError):verify_service_observation(observation,launch)
