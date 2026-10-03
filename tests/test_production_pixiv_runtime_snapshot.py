import hashlib
import json
import subprocess

import pytest

from scripts.production_pixiv_runtime_snapshot import (
    CANDIDATE_PROFILE, PROFILE, REGISTRY,
    freeze_candidate_profile, prepare_deployment_snapshot,
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
