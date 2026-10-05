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
import stat
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


def _deployment_registry(source_registry: dict, profile: dict) -> dict:
    """The sole deployment delta; generation and verification share this rule."""
    registry = json.loads(json.dumps(source_registry))
    registry['candidate_profile']['private_file'] = PROFILE.removeprefix('.local_manifests/')
    for name in ('production-profile.json', 'production-candidate-profile.json',
                 'violet-production-launcher-state.json', 'violet-production-launcher-start.lock'):
        registry.get('private_files', {}).pop('production_launcher/' + name, None)
    stable_fields = {key: value for key, value in profile.items() if key != 'candidate_head'}
    registry['candidate_profile']['stable_fields_sha256'] = hashlib.sha256(
        json.dumps(stable_fields, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')).encode('utf-8')).hexdigest()
    return registry


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
    # Development-only sealed launcher files stay in their original checkout.
    # The deployment's own production profile and live launcher receipts use
    # the existing dynamic profile/process contract instead of stale hashes.
    registry = _deployment_registry(registry, profile)
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
    return {"business_source_head": source_head, "business_root": str(root), "deployment_head": deployment_head,
            "stable_root": str(destination), "changed_files": changed,
            "application_source_bytes_unchanged": True,
            "source_registry_blob": _git(root, "rev-parse", source_head + ":" + REGISTRY).decode().strip(),
            "deployment_registry_blob": blob,
            "profile_sha256": hashlib.sha256((destination / PROFILE).read_bytes()).hexdigest(),
            "original_profile_sha256": hashlib.sha256(before).hexdigest(),
            "production_profile_changed": False, "production_promoted": False,
            "database_writes": 0}


def verify_deployment_runtime_binding(root: Path, candidate: str, binding: Mapping) -> dict:
    """Prove fixed runtime bytes natively, rather than trusting a snapshot receipt.

    The business HEAD keeps its own evidence identity. A deployment HEAD is
    admitted only as its direct child with the exact registry relocation used
    above, the same sealed production configuration and no application drift.
    """
    from scripts import trusted_git
    try:
        root = Path(root)
        trusted_git._assert_no_alias_components(root)
        root = root.resolve(strict=True)
        if (not isinstance(binding, Mapping) or not re.fullmatch('[0-9a-f]{40}', candidate)
                or binding.get('business_source_head') != candidate
                or binding.get('business_root') != str(root)):
            raise ValueError('deployment_business_source_changed')
        deployed = binding.get('deployment_head')
        if not isinstance(deployed, str) or not re.fullmatch('[0-9a-f]{40}', deployed):
            raise ValueError('deployment_head_invalid')
        supplied = binding.get('stable_root')
        if not isinstance(supplied, str) or not Path(supplied).is_absolute():
            raise ValueError('deployment_root_invalid')
        destination = Path(supplied)
        trusted_git._assert_no_alias_components(destination)
        destination = destination.resolve(strict=True)
        if (not destination.is_dir() or destination.parent != root.parent or destination == root
                or not destination.name.startswith('production-pixiv-a2-stable-')):
            raise ValueError('deployment_root_not_fixed_sibling')
        common = _git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()
        other = _git(destination, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()
        if not Path(common).is_absolute() or Path(common).resolve(strict=True) != Path(other).resolve(strict=True):
            raise ValueError('deployment_repository_changed')
        if (_git(destination, 'rev-parse', 'HEAD').decode().strip() != deployed
                or _git(root, 'rev-list', '--parents', '-n', '1', deployed).decode().split() != [deployed, candidate]
                or _git(root, 'diff', '--name-only', '-z', candidate, deployed) != (REGISTRY+'\0').encode()):
            raise ValueError('deployment_not_exact_relocation_child')
        for head, key in ((candidate, 'source_registry_blob'), (deployed, 'deployment_registry_blob')):
            if _git(root, 'rev-parse', head+':'+REGISTRY).decode().strip() != binding.get(key):
                raise ValueError('deployment_registry_blob_changed')
        def sealed_bytes(path):
            _, metadata = trusted_git._assert_no_alias_components(path)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError('deployment_file_alias_rejected')
            raw = path.read_bytes()
            if len(raw) != metadata.st_size:
                raise ValueError('deployment_file_changed_during_read')
            return raw
        original_raw = sealed_bytes(root/PROFILE)
        deployed_raw = sealed_bytes(destination/PROFILE)
        if (hashlib.sha256(original_raw).hexdigest() != binding.get('original_profile_sha256')
                or hashlib.sha256(deployed_raw).hexdigest() != binding.get('profile_sha256')):
            raise ValueError('deployment_profile_digest_changed')
        source_profile = json.loads(original_raw)
        profile = json.loads(deployed_raw)
        expected = {**source_profile, 'repo_root':str(destination), 'candidate_head':deployed}
        if (profile != expected or profile.get('profile_id') != 'production-default'
                or profile.get('env') != 'production' or profile.get('pixiv_product_enabled') is not True
                or profile.get('pixiv_product_apply_enabled') is not False):
            raise ValueError('deployment_production_profile_changed')
        source_registry = json.loads(_git(root, 'show', candidate+':'+REGISTRY))
        expected_registry = _json_bytes(_deployment_registry(source_registry, profile))
        committed_registry = _git(root, 'show', deployed+':'+REGISTRY)
        if (committed_registry != expected_registry
                or sealed_bytes(destination/REGISTRY).replace(b'\r\n', b'\n') != committed_registry):
            raise ValueError('deployment_registry_not_exact_relocation')
        if (not trusted_git.candidate_behavior_carry_forward(root, candidate)
                or not trusted_git.candidate_behavior_carry_forward(destination, deployed)):
            raise ValueError('deployment_application_or_ignored_input_drift')
        # A fixed checkout is immutable, including documentation. The source
        # checkout may subsequently contain a registered delivery projection.
        if _git(destination, 'diff', '--name-only', '-z', deployed):
            raise ValueError('deployment_tracked_bytes_changed')
        return {'business_source_head':candidate, 'runtime_head':deployed,
                'runtime_root':str(destination), 'application_source_bytes_unchanged':True}
    except trusted_git.TrustedGitError as exc:
        raise ValueError('deployment_native_path_or_git_identity_invalid:'+str(exc)) from exc
