"""A1 证据和候选启动边界；真实 Git 仓库验证。"""
from types import SimpleNamespace
import subprocess
from scripts import violet_production_control as control
from scripts.phase_contracts import check_phase_contract, get_contract
import json
import pytest
from pathlib import Path


def test_a1_contract_registered_and_missing_evidence_fails_closed():
    assert get_contract('production_pixiv_a1_v1').phase_kind=='production_pixiv_a1'
    result=check_phase_contract('production_pixiv_a1_v1',{'contract_id':'production_pixiv_a1_v1','target_met':True})
    assert not result.passed


def test_profile_enables_only_requested_pixiv_flags(tmp_path):
    profile=control._coerce_profile_payload({'pixiv_product_enabled':True,'pixiv_product_apply_enabled':True},repo_root=tmp_path)
    environment=control._profile_to_env(profile,repo_root=tmp_path)
    assert environment['SCV2_PX3_PRODUCT_INTEGRATION_ENABLED']=='true'
    assert environment['SCV2_PX3_PRODUCT_APPLY_ENABLED']=='true'
    assert environment['SCV2_PX3_SYNTHETIC_UI_ENABLED']=='false'
    assert all(environment[key]=='false' for key in control.AUTOMATION_FLAGS)


def test_production_candidate_pin_rejects_behavior_and_untracked_drift(tmp_path):
    def git(*args):
        return subprocess.check_output(['git','-C',str(tmp_path),*args],text=True).strip()
    git('init','-q')
    git('config','user.name','A1 regression')
    git('config','user.email','a1-regression@example.invalid')
    (tmp_path/'.gitignore').write_text('.local_manifests/\n',encoding='utf-8')
    (tmp_path/'run.py').write_text('print(1)\n',encoding='utf-8')
    git('add','.')
    git('commit','-qm','fixture')
    head=git('rev-parse','HEAD')
    profile=tmp_path/'.local_manifests/production_launcher/production-profile.json'
    profile.parent.mkdir(parents=True)
    profile.write_text('{}',encoding='utf-8')
    config=SimpleNamespace(config_source='production_profile',profile_exists=True,
        profile_data={'candidate_head':head},profile_path=profile,repo_root=tmp_path,
        env={'VIOLET_CANONICAL_REPO_ROOT':str(tmp_path)})
    assert control._pinned_candidate_worktree(config)
    (tmp_path/'docs').mkdir()
    (tmp_path/'docs/report.md').write_text('报告',encoding='utf-8')
    assert control._pinned_candidate_worktree(config)
    from scripts.trusted_git import candidate_behavior_carry_forward
    for path in ('notes.md','capture.png','output.log'):
        (tmp_path/path).write_text('private artifact',encoding='utf-8')
    assert candidate_behavior_carry_forward(tmp_path,head)
    for path in ('docs/runtime.py','docs/package.json','docs/config.yaml','docs/runtime.json'):
        target = tmp_path/path
        target.write_text('{}',encoding='utf-8')
        assert not candidate_behavior_carry_forward(tmp_path,head)
        assert not control._pinned_candidate_worktree(config)
        target.unlink()
    (tmp_path/'unexpected.py').write_text('print(2)\n',encoding='utf-8')
    assert not control._pinned_candidate_worktree(config)
    (tmp_path/'unexpected.py').unlink()
    (tmp_path/'run.py').write_text('print(2)\n',encoding='utf-8')
    assert not control._pinned_candidate_worktree(config)
    (tmp_path/'run.py').write_text('print(1)\n',encoding='utf-8')
    config.profile_data['candidate_head']='f'*40
    assert not control._pinned_candidate_worktree(config)


@pytest.mark.parametrize('missing', ['unrelated_case','mapping_without_pass','command_not_selected','none'])
def test_remediation_requires_each_executed_node(tmp_path,missing):
    from scripts.check_production_pixiv_a1 import reconcile_failures, KNOWN_HISTORICAL_NODE
    old='tests/test_same.py::test_failed[old]'
    new='tests/test_same.py::test_failed[new]'
    (tmp_path/'initial.log').write_text(f'FAILED {old}\nFAILED {KNOWN_HISTORICAL_NODE}\n')
    actual='tests/test_same.py::test_unrelated' if missing=='unrelated_case' else new
    (tmp_path/'rerun.log').write_text(f'{actual} PASSED [100%]\n1 passed\n')
    command={'tests':[actual], 'argv':['python','-m','pytest',actual,'-vv']}
    if missing=='command_not_selected': command['argv']=['python','-m','pytest','tests/test_else.py']
    (tmp_path/'command.json').write_text(json.dumps(command))
    validation=dict(non_e2e={'log':'initial.log','failed':2},known_baseline_failures=[KNOWN_HISTORICAL_NODE],
        resolved_initial_failures=[old],remediation=[{'log':'rerun.log','command':'command.json'}],
        node_mappings={old:{'nodes':[new if missing!='mapping_without_pass' else new+'missing'],'reason':'parameter coverage expanded'}})
    if missing=='none':
        assert reconcile_failures(tmp_path,validation)[1] == {old}
    else:
        with pytest.raises(ValueError): reconcile_failures(tmp_path,validation)


@pytest.mark.parametrize('field',['candidate_head','production_candidate_head','both_invalid'])
def test_state_candidate_requires_evidence_and_real_commit(tmp_path,field):
    from scripts.production_pixiv_a1_state import validate
    from scripts.check_documentation_state import DocumentationStateError
    repo=Path(__file__).resolve().parents[1]
    state=json.loads((repo/'docs/state/current-phase.json').read_text(encoding='utf-8'))
    state['target_met']=True
    for link in state['durable_links']:
        destination=tmp_path/link['path']
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes((repo/link['path']).read_bytes())
    if field=='both_invalid':
        state['candidate_head']=state['production_candidate_head']='f'*40
        path=tmp_path/state['result_path']
        result=json.loads(path.read_text(encoding='utf-8'))
        result['candidate_head']='f'*40
        path.write_text(json.dumps(result),encoding='utf-8')
    else:
        state[field]='f'*40
    with pytest.raises(DocumentationStateError,match='a1_(candidate|result_evidence)'):
        validate(state,tmp_path)
