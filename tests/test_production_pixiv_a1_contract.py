"""A1 证据和候选启动边界；真实 Git 仓库验证。"""
from types import SimpleNamespace
import subprocess
from scripts import violet_production_control as control
from scripts.phase_contracts import check_phase_contract, get_contract


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
    (tmp_path/'unexpected.py').write_text('print(2)\n',encoding='utf-8')
    assert not control._pinned_candidate_worktree(config)
    (tmp_path/'unexpected.py').unlink()
    (tmp_path/'run.py').write_text('print(2)\n',encoding='utf-8')
    assert not control._pinned_candidate_worktree(config)
    (tmp_path/'run.py').write_text('print(1)\n',encoding='utf-8')
    config.profile_data['candidate_head']='f'*40
    assert not control._pinned_candidate_worktree(config)
