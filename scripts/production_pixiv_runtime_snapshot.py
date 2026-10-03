"""Separate candidate preparation from a fixed, source-bound production runtime.

Deployment snapshots retain the exact application bytes of the selected source
commit. Their only Git delta binds the existing ignored-input registry to the
new code directory. No profile, anchor, process or database is promoted here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

REGISTRY = "docs/state/production-pixiv-a2-ignored-inputs.json"
PROFILE = ".local_manifests/production_launcher/production-profile.json"
CANDIDATE_PROFILE = ".local_manifests/production_launcher/production-candidate-profile.json"


def _git(root: Path, *args: str, data: bytes | None = None, env=None) -> bytes:
    from scripts.trusted_git import resolve_trusted_git_executable,trusted_git_environment
    executable=resolve_trusted_git_executable(repo_root=root)
    environment=trusted_git_environment()
    if env is not None:
        # Only the owned alternate index is a control override. Inherited Git
        # directory, replacement-object and config variables stay scrubbed.
        index=Path(env['GIT_INDEX_FILE']).resolve()
        if not index.is_relative_to((root/'.local_manifests/pixiv-a2').resolve()):
            raise ValueError('deployment_index_outside_owned_evidence')
        environment['GIT_INDEX_FILE']=str(index)
    return subprocess.check_output(
        [str(executable.path), "-C", str(root), *args], input=data, env=environment,
        stderr=subprocess.PIPE, timeout=30,
    )


def _json_bytes(value: Mapping) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # New snapshots never overwrite an earlier configuration or user artifact.
    with path.open("xb") as stream:
        stream.write(content)


def freeze_candidate_profile(root: Path, candidate: str, production_profile: Path) -> dict:
    """Write a distinct verification profile; never advance production-default."""
    root = root.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("invalid_candidate")
    if _git(root, "rev-parse", candidate + "^{commit}").decode().strip() != candidate:
        raise ValueError("candidate_commit_missing")
    before = production_profile.read_bytes()
    source = json.loads(before)
    if source.get("profile_id") != "production-default":
        raise ValueError("production_profile_identity")
    profile = {**source, "profile_id": "production-candidate", "repo_root": str(root),
               "candidate_head": candidate, "pixiv_product_apply_enabled": False}
    destination = root / CANDIDATE_PROFILE
    _atomic_new(destination, _json_bytes(profile))
    if production_profile.read_bytes() != before:
        raise ValueError("production_profile_changed_during_freeze")
    return {"candidate_head": candidate, "candidate_profile": CANDIDATE_PROFILE,
            "candidate_profile_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "production_profile_sha256": hashlib.sha256(before).hexdigest(),
            "production_profile_changed": False, "production_promoted": False}


def prepare_deployment_snapshot(root: Path, source_head: str, destination: Path,
                                production_profile: Path) -> dict:
    """Materialize fixed source bytes with an explicit relocation-only Git receipt."""
    root = root.resolve(strict=True)
    destination = destination.resolve()
    if (destination.parent != root.parent or destination.exists()
            or not destination.name.startswith("production-pixiv-a2-stable-")):
        raise ValueError("deployment_destination_must_be_new_sibling")
    if not re.fullmatch(r"[0-9a-f]{40}", source_head):
        raise ValueError("invalid_source_head")
    if _git(root, "rev-parse", source_head + "^{commit}").decode().strip() != source_head:
        raise ValueError("deployment_source_commit_missing")
    before = production_profile.read_bytes()
    source_profile = json.loads(before)
    if (source_profile.get("profile_id") != "production-default"
            or source_profile.get("env") != "production"
            or source_profile.get("pixiv_product_apply_enabled") is not False):
        raise ValueError("deployment_source_profile_identity")
    registry = json.loads(_git(root, "show", source_head + ":" + REGISTRY))
    profile = {**source_profile, "repo_root": str(destination)}
    registry['candidate_profile']['private_file']=PROFILE.removeprefix('.local_manifests/')
    # Development-only sealed launcher files stay in their original checkout.
    # The deployment's own production profile and live launcher receipts use
    # the existing dynamic profile/process contract instead of stale hashes.
    for name in ('production-profile.json','production-candidate-profile.json',
                 'violet-production-launcher-state.json','violet-production-launcher-start.lock'):
        registry.get('private_files',{}).pop('production_launcher/'+name,None)
    stable_fields = {key: value for key, value in profile.items() if key != "candidate_head"}
    registry["candidate_profile"]["stable_fields_sha256"] = hashlib.sha256(
        json.dumps(stable_fields, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode()
    ).hexdigest()
    relocation_bytes = _json_bytes(registry)
    blob = _git(root, "hash-object", "-w", "--stdin", data=relocation_bytes).decode().strip()
    identity = _git(root, "show", "-s", "--format=%cn%x00%ce", source_head).decode('utf-8').strip().split('\0')
    if len(identity) != 2 or any(not value or '\n' in value or '\r' in value for value in identity):
        raise ValueError('deployment_source_commit_identity_invalid')
    # An alternate index prevents staging, resetting or altering the user's tree.
    temp_root = root / ".local_manifests/pixiv-a2"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deployment-index-", dir=temp_root) as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index")}
        _git(root, "read-tree", source_head, env=env)
        _git(root, "update-index", "--cacheinfo", "100644," + blob + "," + REGISTRY, env=env)
        tree = _git(root, "write-tree", env=env).decode().strip()
        deployment_head = _git(
            root, '-c', 'user.name='+identity[0], '-c', 'user.email='+identity[1],
            "commit-tree", tree, "-p", source_head,
            data=("Bind fixed deployment directory for " + source_head + "\n").encode(), env=env,
        ).decode().strip()
    changed = _git(root, "diff", "--name-only", source_head, deployment_head).decode().splitlines()
    if changed != [REGISTRY]:
        raise ValueError("deployment_must_change_only_ignored_input_path_binding")
    _git(root, "worktree", "add", "--detach", str(destination), deployment_head)
    profile["candidate_head"] = deployment_head
    _atomic_new(destination / PROFILE, _json_bytes(profile))
    for relative, expected in registry.get("repository_files", {}).items():
        original = root / relative
        raw = original.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("deployment_registered_input_changed:" + relative)
        _atomic_new(destination / relative, raw)
    if production_profile.read_bytes() != before:
        raise ValueError("production_profile_changed_during_deployment_preparation")
    return {"business_source_head": source_head, "deployment_head": deployment_head,
            "stable_root": str(destination), "changed_files": changed,
            "application_source_bytes_unchanged": True,
            "source_registry_blob": _git(root, "rev-parse", source_head + ":" + REGISTRY).decode().strip(),
            "deployment_registry_blob": blob,
            "profile_sha256": hashlib.sha256((destination / PROFILE).read_bytes()).hexdigest(),
            "original_profile_sha256": hashlib.sha256(before).hexdigest(),
            "production_profile_changed": False, "production_promoted": False,
            "database_writes": 0}
