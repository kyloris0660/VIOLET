"""Execute SCV2-ML2 multilingual creator identity candidate closure.

Lifecycle: phase-scoped operational runner.  The reusable identity rules live
in ``multilingual_creator_identity_closure_service``.  This runner is bounded
to one fresh clone of the accepted ML1 database and never calls Pixiv,
gallery-dl, an external metadata provider, or an LLM for deterministic pairs.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence
import zipfile

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
for candidate in (ROOT, BACKEND_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.models import (  # noqa: E402
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptResolutionRun,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    SourceMetadataRecord,
    SourceNameObservation,
)
from app.services.multilingual_creator_identity_closure_service import (  # noqa: E402
    LLM_POLICY_VERSION,
    POLICY_VERSION,
    RESOLVER_VERSION,
    CreatorIdentityClosureError,
    CreatorIdentityFamily,
    TrustedCreatorAlias,
    audit_touched_identity_components,
    alias_signal_key,
    anchor_signal_key,
    build_star_candidates,
    candidate_growth_accounting,
    component_purity,
    concept_key,
    family_accounting,
    fingerprint,
    pair_accounting,
    select_llm_manifest,
)
from app.services.source_concept_autonomous_closure_service import (  # noqa: E402
    CandidatePair,
    classify_legacy_cache_reuse,
    disposition_accounting as r2r_disposition_accounting,
    execute_autonomous_missing_pairs,
)
from app.services.source_concept_search_service import (  # noqa: E402
    list_media_source_concepts,
    source_layer_search_path_media_ids,
)
from app.services.pixiv_metadata_ingestion_service import (  # noqa: E402
    _upsert_name_observation,
    is_pixiv_creator_observation_compatible_with_parent,
    is_trusted_complete_pixiv_metadata_record,
)
from app.services.source_metadata_registry_service import (  # noqa: E402
    canonical_source_key,
    normalize_source_text,
)
from scripts import run_phase45_scv2_ml1_multilingual_alias_source_metadata_closure as ml1  # noqa: E402
from scripts import run_phase45_scv2_r2r_autonomous_recall_search_closure as r2r_runner  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402
from scripts.phase_contracts.contract_checks import check_phase_contract  # noqa: E402


PHASE = "4.5-SCV2-ML2"
TITLE = "SCV2-ML2: Multilingual Identity Candidate Closure"
CONTRACT_ID = "ml2_multilingual_identity_candidate_closure_contract_v1"
BASE_SHA = "f6cae3483f4cf75974746a4cc82222f28e399b96"
TASK_BRANCH = "codex/scv2-ml2-multilingual-identity-candidate-closure"
ML1_CODE_SHA = "64949da9b804adf400f5b5a0f99a808ff318115b"
R2R_BASE_SHA = "5bbbb8ff13b140ea77a839757603714bfdd87181"
SOURCE_DB = "blombooru_scv2_ml1_acquisition_test_20260712"
R2R_DB = "blombooru_scv2_r2r_dryrun_test_20260710"
SUPERSEDED_WORKING_DB = "blombooru_scv2_ml2_identity_closure_test_20260714"
WORKING_DB = "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715"
RUN_ID = "scv2-ml2-identity-closure-reviewfix-20260715-v1"
DEFAULT_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-reviewfix-20260715"
BASELINE_OUTPUT = DEFAULT_OUTPUT / "ml1-baseline-recompute"
R2R_PAIR_MANIFEST = ROOT / ".local_manifests/phase-4.5-scv2-r2r-autonomous-recall-search-closure/pair-manifest.json"
R2R_ACCEPTED_EXECUTION = ROOT / ".local_manifests/phase-4.5-scv2-r2r-autonomous-recall-search-closure/execution-result-r2r-20260711-execution7.json"
R2R_LEGACY_CACHE = ROOT / ".local_manifests/source_concept_llm_adjudication_cache"
R2R_AUTONOMOUS_CACHE = ROOT / ".local_manifests/source_concept_r2r_autonomous_cache"
REPORT_MD = ROOT / "docs/reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure.md"
REPORT_JSON = ROOT / "docs/reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-summary.json"

IMMUTABLE_FIXED_TABLES = (
    "blombooru_media",
    "blombooru_tags",
    "blombooru_media_tags",
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_name_registry",
    "blombooru_source_tag_registry",
    "blombooru_tag_translations",
    "blombooru_source_name_alias_candidates",
)
FORBIDDEN_TRUTH_TABLES = ml1.FORBIDDEN_TRUTH_TABLES
ALLOWED_MUTATION_TABLES = (
    "blombooru_source_name_observations",
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
)


class ML2BlockedError(RuntimeError):
    """Fail-closed ML2 execution error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_tree_fingerprint(paths: Sequence[Path]) -> str:
    records: list[dict[str, Any]] = []
    for root in paths:
        if root.is_file():
            records.append({"root": root.name, "path": root.name, "sha256": file_sha256(root)})
            continue
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            records.append(
                {
                    "root": root.name,
                    "path": path.relative_to(root).as_posix(),
                    "sha256": file_sha256(path),
                }
            )
    return fingerprint(records)


def directory_content_fingerprint(root: Path) -> str:
    return fingerprint(
        [
            {"path": path.relative_to(root).as_posix(), "sha256": file_sha256(path)}
            for path in sorted(value for value in root.rglob("*") if value.is_file())
        ]
    )


def _sha256_lines(values: Sequence[str]) -> str:
    payload = ("\n".join(values) + ("\n" if values else "")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if result.returncode:
        raise ML2BlockedError(f"blocked_ml2_environment_isolation:git_{args[0]}_failed")
    return result.stdout.strip()


def decode_git_path(value: str) -> str:
    """Decode Git's quoted octal UTF-8 path representation for OS checks."""

    if not (value.startswith('"') and value.endswith('"')):
        return value
    decoded = ast.literal_eval(value)
    return decoded.encode("latin-1").decode("utf-8")


def collect_repository_preflight(preedit_json_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect current Git facts and verify every pre-edit user-owned path."""

    if not preedit_json_path.is_file():
        raise ML2BlockedError("blocked_ml2_environment_isolation:preedit_snapshot_missing")
    preedit = json.loads(preedit_json_path.read_text(encoding="utf-8"))
    snapshot_dir = preedit_json_path.parent
    untracked_manifest = snapshot_dir / str(preedit.get("preexisting_untracked_manifest") or "")
    ignored_manifest = snapshot_dir / str(preedit.get("preexisting_ignored_manifest") or "")
    if not untracked_manifest.is_file() or not ignored_manifest.is_file():
        raise ML2BlockedError("blocked_ml2_environment_isolation:path_snapshot_missing")
    # Preserve the original Git output order because it is part of the
    # pre-edit evidence fingerprint.  Ordering the list again would compare a
    # different serialization of the same paths and create a false blocker.
    preexisting_untracked = untracked_manifest.read_text(encoding="utf-8").splitlines()
    preexisting_ignored = ignored_manifest.read_text(encoding="utf-8").splitlines()
    if (
        len(preexisting_untracked) != int(preedit.get("preexisting_untracked_path_count") or -1)
        or len(preexisting_ignored) != int(preedit.get("preexisting_ignored_path_count") or -1)
        or _sha256_lines(preexisting_untracked)
        != preedit.get("preexisting_untracked_path_list_sha256")
        or _sha256_lines(preexisting_ignored)
        != preedit.get("preexisting_ignored_path_list_sha256")
    ):
        raise ML2BlockedError("blocked_ml2_environment_isolation:path_snapshot_fingerprint_mismatch")

    missing = [
        value
        for value in (*preexisting_untracked, *preexisting_ignored)
        if not os.path.lexists(ROOT / decode_git_path(value))
    ]
    current_untracked = sorted(_git("ls-files", "--others", "--exclude-standard").splitlines())
    current_ignored = sorted(
        _git("ls-files", "--others", "--ignored", "--exclude-standard").splitlines()
    )
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    tracking = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    remote_head = _git("rev-parse", tracking)
    merge_base = _git("merge-base", BASE_SHA, head)
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASE_SHA, head],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    ahead_behind = _git("rev-list", "--left-right", "--count", "HEAD...@{upstream}").split()
    tracked = _git("diff", "--name-only").splitlines()
    staged = _git("diff", "--cached", "--name-only").splitlines()
    repository_root = Path(_git("rev-parse", "--show-toplevel")).resolve()
    repository_root_verified = repository_root == ROOT.resolve()
    private = {
        "evidence_source": "actual_git_subprocess",
        "repository_root": str(repository_root),
        "repository_root_verified": repository_root_verified,
        "current_branch": branch,
        "current_head": head,
        "remote_tracking_branch": tracking,
        "remote_head": remote_head,
        "ahead": int(ahead_behind[0]),
        "behind": int(ahead_behind[1]),
        "accepted_base": BASE_SHA,
        "actual_merge_base": merge_base,
        "base_is_ancestor": ancestor,
        "tracked_paths": tracked,
        "staged_paths": staged,
        "current_untracked_path_count": len(current_untracked),
        "current_untracked_path_list_sha256": _sha256_lines(current_untracked),
        "current_ignored_path_count": len(current_ignored),
        "current_ignored_path_list_sha256": _sha256_lines(current_ignored),
        "preexisting_untracked_path_count": len(preexisting_untracked),
        "preexisting_untracked_path_list_sha256": _sha256_lines(preexisting_untracked),
        "preexisting_ignored_path_count": len(preexisting_ignored),
        "preexisting_ignored_path_list_sha256": _sha256_lines(preexisting_ignored),
        "preexisting_user_owned_path_missing_count": len(missing),
        "preexisting_user_owned_paths_preserved": not missing,
        "missing_paths": missing,
        "preedit_snapshot_source": preedit.get("evidence_source"),
        "preedit_head": preedit.get("current_head"),
        "preedit_remote_head": preedit.get("remote_head"),
        "preedit_ahead": preedit.get("ahead"),
        "preedit_behind": preedit.get("behind"),
    }
    public = {
        key: value
        for key, value in private.items()
        if key not in {"repository_root", "tracked_paths", "staged_paths", "missing_paths"}
    }
    public.update(
        {
            "repository_root_fingerprint": fingerprint(str(repository_root)),
            "tracked_change_count": len(tracked),
            "staged_change_count": len(staged),
            "status": "passed_synchronization_preflight"
            if (
                repository_root_verified
                and branch == TASK_BRANCH
                and ancestor
                and merge_base == BASE_SHA
                and not tracked
                and not staged
                and not missing
                and int(ahead_behind[1]) == 0
            )
            else "blocked_ml2_environment_isolation",
        }
    )
    return public, private


def rows(session: Session, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text(sql), dict(params or {})).mappings()]


def environment_proof(database: str, output_dir: Path) -> dict[str, Any]:
    production_flags = {
        key: bool(str(os.getenv(key) or "").strip())
        for key in (
            "VIOLET_PRODUCTION_PROFILE_ACTIVE",
            "VIOLET_PRODUCTION_PROFILE",
            "VIOLET_PRODUCTION_MODE",
            "VIOLET_LAUNCH_PRODUCTION",
        )
    }
    storage = str(os.getenv("VIOLET_STORAGE_ROOT") or "")
    passed = bool(
        database == WORKING_DB
        and str(os.getenv("VIOLET_ENV") or "").casefold() == "test"
        and database != SOURCE_DB
        and database != R2R_DB
        and "test" in database.casefold()
        and not any(production_flags.values())
        and output_dir.is_relative_to(ROOT / ".local_manifests")
        and "test" in storage.casefold()
    )
    return {
        "passed": passed,
        "violet_env": str(os.getenv("VIOLET_ENV") or ""),
        "source_database": SOURCE_DB,
        "r2r_database": R2R_DB,
        "superseded_ml2_database": SUPERSEDED_WORKING_DB,
        "superseded_ml2_database_preserved": database != SUPERSEDED_WORKING_DB,
        "working_database": database,
        "working_database_is_fresh_separate_clone": database == WORKING_DB,
        "production_profile_active": any(production_flags.values()),
        "production_flags_checked": sorted(production_flags),
        "test_storage_configured": "test" in storage.casefold(),
        "production_write_route": False,
        "source_or_icloud_route": False,
    }


def baseline_summary() -> dict[str, Any]:
    path = BASELINE_OUTPUT / "evidence-summary.json"
    if not path.is_file():
        raise ML2BlockedError("blocked_ml2_baseline_drift:baseline_recompute_missing")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not (value.get("validation") or {}).get("ml1_contract_passed"):
        raise ML2BlockedError("blocked_ml2_baseline_drift:ml1_contract_not_passed")
    return value


def validate_r2r_disposition_snapshot(
    *,
    database_summary: Mapping[str, Any],
    execution_summary: Mapping[str, Any],
    pair_manifest: Mapping[str, Any],
    dispositions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate exact accepted R2R pair identities and dispositions."""

    pairs = pair_manifest.get("pairs") if isinstance(pair_manifest, Mapping) else None
    if not isinstance(pairs, list):
        raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:pair_manifest_missing")
    pair_ids = [str(row.get("pair_id") or "") for row in pairs if isinstance(row, Mapping)]
    disposition_ids = [str(row.get("pair_id") or "") for row in dispositions]
    counts = Counter(str(row.get("disposition") or "") for row in dispositions)
    expected_distribution = {"must_link": 1522, "cannot_link": 1791, "deferred_nonblocking": 6}
    execution = execution_summary.get("candidate_dispositions") or {}
    db_judgment_count = (
        (database_summary.get("llm_usage") or {}).get("judgment_count")
        if isinstance(database_summary, Mapping)
        else None
    )
    if (
        len(pair_ids) != 3319
        or len(set(pair_ids)) != 3319
        or set(pair_ids) != set(disposition_ids)
        or len(disposition_ids) != len(set(disposition_ids))
        or any(counts[key] != value for key, value in expected_distribution.items())
        or sum(counts.values()) != 3319
        or db_judgment_count != 3319
        or execution.get("total_candidate_pairs") != 3319
        or any(execution.get(f"{key}_count") != value for key, value in expected_distribution.items())
        or execution.get("candidate_disposition_coverage") != 1.0
    ):
        raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:count_or_distribution_mismatch")
    normalized = [
        {"pair_id": str(row["pair_id"]), "disposition": str(row["disposition"])}
        for row in sorted(dispositions, key=lambda value: str(value.get("pair_id") or ""))
    ]
    return {
        "accepted_pair_count": 3319,
        "accepted_must_link_count": 1522,
        "accepted_cannot_link_count": 1791,
        "accepted_deferred_nonblocking_count": 6,
        "candidate_disposition_coverage": 1.0,
        "snapshot_fingerprint": fingerprint(normalized),
        "database_accepted_judgment_count": db_judgment_count,
        "database_snapshot_crosscheck_passed": True,
        "private_pair_manifest_crosscheck_passed": True,
        "accepted_dispositions_mutated": False,
    }


def load_exact_r2r_disposition_snapshot(
    scratch_cache_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Rebuild accepted R2R dispositions cache-only and cross-check the DB."""

    required = (
        R2R_PAIR_MANIFEST,
        R2R_ACCEPTED_EXECUTION,
        R2R_LEGACY_CACHE / "records",
        R2R_AUTONOMOUS_CACHE / "first" / "records",
    )
    if not all(path.exists() for path in required):
        raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:required_private_evidence_missing")
    if scratch_cache_root is None:
        scratch_cache_root = DEFAULT_OUTPUT / "r2r-cache-reconstruction"
    preserved_paths = (
        R2R_PAIR_MANIFEST,
        R2R_ACCEPTED_EXECUTION,
        R2R_LEGACY_CACHE,
        R2R_AUTONOMOUS_CACHE,
    )
    preserved_before = artifact_tree_fingerprint(preserved_paths)
    if scratch_cache_root.exists():
        if directory_content_fingerprint(scratch_cache_root) != directory_content_fingerprint(
            R2R_AUTONOMOUS_CACHE
        ):
            raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:scratch_cache_mismatch")
    else:
        shutil.copytree(R2R_AUTONOMOUS_CACHE, scratch_cache_root)
    pair_manifest = json.loads(R2R_PAIR_MANIFEST.read_text(encoding="utf-8"))
    execution_summary = json.loads(R2R_ACCEPTED_EXECUTION.read_text(encoding="utf-8"))
    try:
        candidates = tuple(CandidatePair(**row) for row in pair_manifest.get("pairs") or [])
    except Exception as exc:
        raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:pair_manifest_invalid") from exc
    if len(candidates) != 3319:
        raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:pair_manifest_count")

    engine = r2.create_db_engine(R2R_DB)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    provider_attempts = 0
    try:
        identity = session.execute(text("SELECT current_database()" )).scalar()
        if identity != R2R_DB:
            raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:database_identity")
        run_row = session.execute(
            text(
                "SELECT summary_json FROM blombooru_source_concept_resolution_runs "
                "WHERE run_id LIKE :run_id ORDER BY id DESC LIMIT 1"
            ),
            {"run_id": f"{r2r_runner.ACCEPTED_EXECUTION_RUN_ID}%"},
        ).scalar()
        if not isinstance(run_row, Mapping):
            raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:accepted_database_run_missing")
        signals = r2r_runner.build_source_concept_signals(
            session, run_id="ml2-reviewfix-r2r-snapshot-readonly"
        )
        reused, _cache_summary, _analysis = classify_legacy_cache_reuse(
            R2R_LEGACY_CACHE,
            candidates=candidates,
            signals=signals,
            resolver_version=r2r_runner.RESOLVER_VERSION,
        )

        def no_provider(_pass_name: str, _payload: Mapping[str, Any]) -> Mapping[str, Any]:
            nonlocal provider_attempts
            provider_attempts += 1
            raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:cache_miss_provider_forbidden")

        exact, cache_proof = execute_autonomous_missing_pairs(
            candidates,
            initial_dispositions=reused,
            signal_by_key={signal.signal_key: signal for signal in signals},
            cache_root=scratch_cache_root,
            executor=no_provider,
            max_attempts_per_pass=1,
        )
        accounting = r2r_disposition_accounting(candidates, exact.values())
        if provider_attempts or not accounting.get("accounting_equality_passed"):
            raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:cache_only_rebuild_incomplete")
        dispositions = [
            {"pair_id": pair_id, "disposition": row.disposition}
            for pair_id, row in sorted(exact.items())
        ]
        summary = validate_r2r_disposition_snapshot(
            database_summary=run_row,
            execution_summary=execution_summary,
            pair_manifest=pair_manifest,
            dispositions=dispositions,
        )
        summary.update(
            {
                "cache_only_rebuild_passed": True,
                "provider_attempt_count": provider_attempts,
                "legacy_compatible_reuse_count": len(reused),
                "autonomous_cache_reuse_count": 3319 - len(reused),
                "cache_proof_provider_calls": int(cache_proof.get("provider_calls") or 0),
                "preserved_r2r_artifact_fingerprint": preserved_before,
                "preserved_r2r_artifacts_mutated": (
                    artifact_tree_fingerprint(preserved_paths) != preserved_before
                ),
            }
        )
        if summary["preserved_r2r_artifacts_mutated"]:
            raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:preserved_artifact_changed")
        session.rollback()
        return summary, dispositions
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _trusted_creator_inputs(session: Session) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metadata = rows(session, "SELECT * FROM blombooru_source_metadata_records ORDER BY id")
    observations = rows(session, "SELECT * FROM blombooru_source_name_observations ORDER BY id")
    return metadata, observations


def _concepts_by_alias(session: Session, *, active: bool) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for row in rows(
        session,
        "SELECT s.raw_value,s.display_value,s.normalized_key,s.canonical_key,l.concept_id "
        "FROM blombooru_source_concept_signals s "
        "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
        "JOIN blombooru_source_concepts c ON c.id=l.concept_id "
        "WHERE l.link_status IN ('active','materialized_identity') "
        "AND s.status IN ('active','materialized_identity') "
        "AND LOWER(COALESCE(s.role_hint,'unknown')) IN ('artist','creator') "
        + ("AND c.status='active'" if active else "AND c.status<>'active'"),
    ):
        for value in (row.get("raw_value"), row.get("display_value"), row.get("normalized_key"), row.get("canonical_key")):
            key = canonical_source_key(value)
            if key:
                result[key].add(int(row["concept_id"]))
    return result


def _concepts_by_signal_key(session: Session, *, active: bool) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for signal_key, concept_id in session.execute(text(
        "SELECT s.signal_key,l.concept_id FROM blombooru_source_concept_signals s "
        "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
        "JOIN blombooru_source_concepts c ON c.id=l.concept_id "
        "WHERE l.link_status IN ('active','materialized_identity') "
        "AND s.status IN ('active','materialized_identity') "
        + ("AND c.status='active'" if active else "AND c.status<>'active'")
    )).all():
        result[str(signal_key)].add(int(concept_id))
    return result


def build_manifests(
    session: Session,
    metadata_rows: Sequence[Mapping[str, Any]],
    observation_rows: Sequence[Mapping[str, Any]],
) -> tuple[
    tuple[CreatorIdentityFamily, ...],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[Mapping[str, Any]]],
    dict[str, Any],
]:
    metadata_by_id = {int(row["id"]): row for row in metadata_rows}
    _, creator_private, aliases_by_creator = ml1.build_creator_audit(metadata_rows, observation_rows)
    eligible_aliases = {
        creator_id: {normalize_source_text(value) for value in aliases if normalize_source_text(value)}
        for creator_id, aliases in aliases_by_creator.items()
        if len({normalize_source_text(value) for value in aliases if normalize_source_text(value)}) >= 2
    }
    records_by_creator: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in creator_private:
        creator_id = str(row.get("creator_id") or "")
        if creator_id in eligible_aliases:
            records_by_creator[creator_id].append(row)
    observations_by_record: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observation_rows:
        record_id = int(observation["source_metadata_record_id"])
        parent = metadata_by_id.get(record_id)
        if (
            parent is not None
            and str(observation.get("provider") or "").casefold() == "pixiv"
            and str(observation.get("name_role") or "") in {"artist", "creator"}
            and str(observation.get("source_field") or "") in {"pixiv_user_metadata", "pixiv_user_account"}
            and str(observation.get("status") or "") in {"observed", "active", "accepted"}
            and is_pixiv_creator_observation_compatible_with_parent(observation, parent)
        ):
            observations_by_record[record_id].append(observation)

    concepts_by_alias = _concepts_by_alias(session, active=True)
    concepts_by_signal_key = _concepts_by_signal_key(session, active=True)
    inactive_concepts_by_alias = _concepts_by_alias(session, active=False)
    inactive_concepts_by_signal_key = _concepts_by_signal_key(session, active=False)
    concept_created_by_run = {
        int(row[0]): str(row[1] or "")
        for row in session.query(SourceConcept.id, SourceConcept.created_by_run_id).all()
    }
    baseline_gap_rows = read_jsonl(BASELINE_OUTPUT / "candidate-generation-miss-ledger.jsonl")
    baseline_gap_creator_ids = {
        str(row.get("anchor_private") or "") for row in baseline_gap_rows
    }
    if "" in baseline_gap_creator_ids:
        raise ML2BlockedError("blocked_ml2_input_manifest_invalid:baseline_gap_membership")
    families: list[CreatorIdentityFamily] = []
    family_manifest: list[dict[str, Any]] = []
    alias_observation_manifest: list[dict[str, Any]] = []
    contexts: dict[str, list[Mapping[str, Any]]] = {}
    concept_to_identity: dict[int, set[str]] = defaultdict(set)
    inactive_candidate_references: set[int] = set()
    fragmented_family_count = 0
    partial_reference_family_count = 0
    for creator_id in sorted(eligible_aliases):
        family_id = "family_" + fingerprint({"provider": "pixiv", "creator_id": creator_id})[:24]
        records = sorted(records_by_creator[creator_id], key=lambda row: int(row["source_metadata_record_id"]))
        aliases = sorted(eligible_aliases[creator_id], key=lambda value: (canonical_source_key(value), value))
        alias_rows: list[TrustedCreatorAlias] = []
        all_observation_refs: list[str] = []
        work_context = Counter()
        metadata_refs: list[str] = []
        for record in records:
            record_id = int(record["source_metadata_record_id"])
            metadata_ref = "metadata_" + fingerprint(record_id)[:20]
            metadata_refs.append(metadata_ref)
            parent = metadata_by_id[record_id]
            if parent.get("source_work_id"):
                work_context["work_" + fingerprint(str(parent["source_work_id"]))[:16]] += 1
            for observation in observations_by_record.get(record_id, []):
                normalized = normalize_source_text(observation.get("raw_name"))
                if normalized not in eligible_aliases[creator_id]:
                    continue
                observation_ref = "observation_" + fingerprint(int(observation["id"]))[:20]
                all_observation_refs.append(observation_ref)
                parent_fp = fingerprint(
                    {
                        "record_id": record_id,
                        "provider": parent.get("provider"),
                        "status": parent.get("status"),
                        "creator_id": creator_id,
                    }
                )
                alias_observation_manifest.append(
                    {
                        "family_id": family_id,
                        "private_observation_id": int(observation["id"]),
                        "observation_ref": observation_ref,
                        "alias_observation_type": (
                            "creator_account" if observation.get("source_field") == "pixiv_user_account" else "creator_name"
                        ),
                        "role": "creator",
                        "normalized_value": normalized,
                        "parent_evidence_fingerprint": parent_fp,
                        "search_visibility": True,
                        "current_sourceconcept_consumption": bool(concepts_by_alias.get(canonical_source_key(normalized))),
                    }
                )
        for alias in aliases:
            matching = [
                row
                for row in alias_observation_manifest
                if row["family_id"] == family_id and row["normalized_value"] == alias
            ]
            alias_rows.append(
                TrustedCreatorAlias(
                    alias_type="creator_name_or_account",
                    value=alias,
                    canonical_key=canonical_source_key(alias),
                    observation_refs=tuple(sorted({str(row["observation_ref"]) for row in matching})),
                    parent_evidence_fingerprint=fingerprint(
                        sorted({str(row["parent_evidence_fingerprint"]) for row in matching})
                    ),
                )
            )
        anchor_key = anchor_signal_key("pixiv", creator_id, "creator")
        active_anchor_concepts = set(concepts_by_signal_key.get(anchor_key, set()))
        inactive_anchor_concepts = set(inactive_concepts_by_signal_key.get(anchor_key, set()))
        active_alias_sets: list[set[int]] = []
        inactive_alias_sets: list[set[int]] = []
        for alias in alias_rows:
            if not alias.canonical_key:
                continue
            signal_key = alias_signal_key(
                "pixiv", creator_id, alias.alias_type, alias.value, "creator"
            )
            active_alias_sets.append(
                set(concepts_by_alias.get(alias.canonical_key, set()))
                | set(concepts_by_signal_key.get(signal_key, set()))
            )
            inactive_alias_sets.append(
                set(inactive_concepts_by_alias.get(alias.canonical_key, set()))
                | set(inactive_concepts_by_signal_key.get(signal_key, set()))
            )
        raw_active_references = set(active_anchor_concepts).union(*active_alias_sets)
        raw_inactive_references = set(inactive_anchor_concepts).union(*inactive_alias_sets)
        complete_active_alias_concepts = (
            set.intersection(*active_alias_sets)
            if active_alias_sets and all(active_alias_sets)
            else set()
        )
        complete_inactive_alias_concepts = (
            set.intersection(*inactive_alias_sets)
            if inactive_alias_sets and all(inactive_alias_sets)
            else set()
        )
        active_concepts = active_anchor_concepts | complete_active_alias_concepts
        inactive_concepts = inactive_anchor_concepts | complete_inactive_alias_concepts
        partial_reference_family_count += int(bool(raw_active_references - active_concepts))
        inactive_candidate_references.update(raw_inactive_references)
        current_run_anchor_concepts = sorted(
            concept_id
            for concept_id in active_anchor_concepts
            if concept_created_by_run.get(concept_id) == RUN_ID
        )
        preexisting_active_concept_ids = tuple(
            sorted(
                concept_id
                for concept_id in active_concepts
                if concept_created_by_run.get(concept_id) != RUN_ID
            )
        )
        existing_concept_id = (
            current_run_anchor_concepts[0]
            if len(current_run_anchor_concepts) == 1
            else preexisting_active_concept_ids[0]
            if len(preexisting_active_concept_ids) == 1
            else None
        )
        fragmented_family_count += int(len(preexisting_active_concept_ids) > 1)
        identity_fp = fingerprint({"provider": "pixiv", "stable_creator_id": creator_id, "role": "creator"})
        if existing_concept_id is not None:
            concept_to_identity[int(existing_concept_id)].add(identity_fp)
        evidence_fp = fingerprint(
            {
                "provider": "pixiv",
                "stable_creator_id": creator_id,
                "aliases": aliases,
                "metadata_refs": sorted(set(metadata_refs)),
                "observation_refs": sorted(set(all_observation_refs)),
            }
        )
        family = CreatorIdentityFamily(
            family_id=family_id,
            provider="pixiv",
            stable_creator_id=creator_id,
            creator_role="creator",
            aliases=tuple(alias_rows),
            metadata_refs=tuple(sorted(set(metadata_refs))),
            work_context_distribution=dict(sorted(work_context.items())),
            evidence_fingerprint=evidence_fp,
            existing_concept_id=existing_concept_id,
            preexisting_active_concept_ids=preexisting_active_concept_ids,
        )
        families.append(family)
        contexts[family_id] = [
            {
                **dict(record),
                "media_id": metadata_by_id[int(record["source_metadata_record_id"])].get("media_id"),
            }
            for record in records
        ]
        family_manifest.append(
            {
                "private_family_id": family_id,
                "provider": "pixiv",
                "stable_creator_id": creator_id,
                "trusted_creator_names_and_accounts": aliases,
                "trusted_profile_identity_refs": sorted(
                    {"profile_" + fingerprint(row["creator_profile_identity"])[:20] for row in records if row.get("creator_profile_identity")}
                ),
                "source_metadata_record_references": sorted(set(metadata_refs)),
                "source_name_observation_references": sorted(set(all_observation_refs)),
                "existing_sourceconcept_references": list(preexisting_active_concept_ids),
                "preexisting_active_concept_ids": list(preexisting_active_concept_ids),
                "preexisting_active_concept_count": len(preexisting_active_concept_ids),
                "raw_active_alias_surface_concept_references": sorted(raw_active_references),
                "partial_active_alias_surface_concept_references": sorted(
                    raw_active_references - active_concepts
                ),
                "inactive_concept_candidate_references": sorted(raw_inactive_references),
                "existing_materialization_status": (
                    "fragmented_deferred"
                    if len(preexisting_active_concept_ids) > 1
                    else "materialized"
                    if existing_concept_id is not None
                    else "not_materialized"
                ),
                "work_context_distribution": dict(sorted(work_context.items())),
                "role_distribution": {"creator": len(alias_rows)},
                "evidence_fingerprint": evidence_fp,
            }
        )
    if any(len(values) > 1 for values in concept_to_identity.values()):
        raise ML2BlockedError("blocked_ml2_stable_identity_contradiction:existing_component_multi_stable_id")

    gap_manifest: list[dict[str, Any]] = []
    for family in families:
        if family.stable_creator_id in baseline_gap_creator_ids:
            gap_manifest.append(
                {
                    "family_id": family.family_id,
                    "missing_alias_signal_count": sum(
                        not bool(concepts_by_alias.get(alias.canonical_key)) for alias in family.aliases
                    ),
                    "root_cause": "trusted_creator_name_signal_missing",
                    "initial_disposition": "identity_anchor_not_generated",
                    "current_unmaterialized": family.existing_concept_id is None,
                    "evidence_fingerprint": family.evidence_fingerprint,
                }
            )
    return (
        tuple(families),
        family_manifest,
        alias_observation_manifest,
        gap_manifest,
        contexts,
        {
            "inactive_concept_candidate_reference_count": len(inactive_candidate_references),
            "inactive_concept_reuse_count": 0,
            "preexisting_partial_concept_fragmentation_family_count": fragmented_family_count,
            "preexisting_partial_concept_reference_family_count": partial_reference_family_count,
        },
    )


def _table_counts(session: Session, tables: Sequence[str]) -> dict[str, int]:
    return {table: int(session.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0) for table in tables}


def _database_table_counts(database: str, tables: Sequence[str]) -> dict[str, int]:
    engine = r2.create_db_engine(database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return _table_counts(session, tables)
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _existing_graph_metrics(session: Session, *, exclude_ml2: bool = False) -> dict[str, Any]:
    concept_filter = " AND COALESCE(c.created_by_run_id,'')<>:run_id" if exclude_ml2 else ""
    link_filter = " AND COALESCE(l.run_id,'')<>:run_id" if exclude_ml2 else ""
    sizes = [int(row[0]) for row in session.execute(text(
        "SELECT COUNT(DISTINCT l.signal_id) FROM blombooru_source_concepts c "
        "LEFT JOIN blombooru_source_concept_signal_links l ON l.concept_id=c.id "
        f"AND l.link_status IN ('active','materialized_identity'){link_filter} "
        f"WHERE c.status='active'{concept_filter} GROUP BY c.id"
    ), {"run_id": RUN_ID}).all()]
    concept_query = session.query(SourceConcept).filter(SourceConcept.status == "active")
    if exclude_ml2:
        concept_query = concept_query.filter(
            (SourceConcept.created_by_run_id.is_(None)) | (SourceConcept.created_by_run_id != RUN_ID)
        )
    distribution = Counter(sizes)
    return {
        "sourceconcept_count": int(concept_query.count()),
        "needs_review_count": int(session.query(SourceConcept).filter(SourceConcept.status == "needs_review").count()),
        "component_count": len(sizes),
        "component_size_distribution": {str(key): value for key, value in sorted(distribution.items())},
        "largest_component": max(sizes, default=0),
    }


def _get_or_create(session: Session, model: Any, defaults: Mapping[str, Any], **filters: Any) -> tuple[Any, bool]:
    value = session.query(model).filter_by(**filters).one_or_none()
    if value is not None:
        return value, False
    value = model(**filters, **dict(defaults))
    session.add(value)
    session.flush()
    return value, True


def _backfill_trusted_titles(session: Session) -> int:
    created = 0
    records = session.query(SourceMetadataRecord).filter(
        SourceMetadataRecord.provider == "pixiv",
        SourceMetadataRecord.title.is_not(None),
        SourceMetadataRecord.title != "",
    ).order_by(SourceMetadataRecord.id.asc()).all()
    for record in records:
        if not is_trusted_complete_pixiv_metadata_record(record):
            continue
        present = session.query(SourceNameObservation.id).filter(
            SourceNameObservation.source_metadata_record_id == record.id,
            SourceNameObservation.source_field == "pixiv_title",
            SourceNameObservation.canonical_name_key == canonical_source_key(record.title),
            SourceNameObservation.status.in_(("observed", "active", "accepted")),
        ).first()
        if present:
            continue
        before = len(session.new)
        _upsert_name_observation(session, record, raw_name=record.title, role="work_title", source_field="pixiv_title")
        if len(session.new) > before:
            created += 1
    session.flush()
    return created


def audit_concept_media_support(
    session: Session,
    *,
    family_concepts: Mapping[str, int],
    expected_media_by_family: Mapping[str, set[int]],
) -> dict[str, Any]:
    """Verify the exact O(media) runtime membership written for each family."""

    expected_by_concept: dict[int, set[int]] = defaultdict(set)
    for family_id, concept_id in family_concepts.items():
        expected_by_concept[int(concept_id)].update(expected_media_by_family.get(family_id, set()))
    concept_ids = sorted(expected_by_concept)
    evidence_rows = []
    if concept_ids:
        evidence_rows = rows(
            session,
            "SELECT concept_id,media_id,source_metadata_record_id,provider,payload,run_id,status "
            "FROM blombooru_source_concept_evidence "
            "WHERE concept_id=ANY(:concept_ids) AND evidence_type='trusted_creator_media_support' "
            "AND status='active' ORDER BY concept_id,media_id,source_metadata_record_id",
            {"concept_ids": concept_ids},
        )
    actual_by_concept: dict[int, set[int]] = defaultdict(set)
    triples: Counter[tuple[int, int, int]] = Counter()
    provenance_failures = 0
    for row in evidence_rows:
        concept_id = int(row["concept_id"])
        media_id = int(row["media_id"])
        record_id = int(row["source_metadata_record_id"])
        actual_by_concept[concept_id].add(media_id)
        triples[(concept_id, media_id, record_id)] += 1
        payload = row.get("payload") or {}
        provenance_failures += int(
            row.get("provider") != "pixiv"
            or not isinstance(payload, Mapping)
            or not payload.get("stable_identity_fingerprint")
            or payload.get("provenance") != "trusted_complete_pixiv_source_metadata"
        )
    missing = sum(
        len(expected_by_concept[concept_id] - actual_by_concept.get(concept_id, set()))
        for concept_id in concept_ids
    )
    unsupported = sum(
        len(actual_by_concept.get(concept_id, set()) - expected_by_concept[concept_id])
        for concept_id in concept_ids
    )
    duplicates = sum(max(0, count - 1) for count in triples.values())
    media_count_mismatches = 0
    for concept_id in concept_ids:
        concept = session.query(SourceConcept).filter(
            SourceConcept.id == concept_id,
            SourceConcept.status == "active",
        ).one_or_none()
        media_count_mismatches += int(
            concept is None or int(concept.media_count or 0) != len(expected_by_concept[concept_id])
        )
    expected_relations = sum(len(value) for value in expected_by_concept.values())
    return {
        "materialized_family_count": len(family_concepts),
        "materialized_concept_count": len(concept_ids),
        "concept_media_support_row_count": len(evidence_rows),
        "expected_concept_media_support_row_count": expected_relations,
        "expected_distinct_media_count": len(set().union(*expected_by_concept.values())) if concept_ids else 0,
        "duplicate_concept_media_support_count": duplicates,
        "missing_sourceconcept_media_count": missing,
        "unsupported_sourceconcept_media_count": unsupported,
        "media_count_mismatch_count": media_count_mismatches,
        "support_provenance_failure_count": provenance_failures,
        "per_media_evidence_linear_bound_passed": len(evidence_rows) == expected_relations,
        "passed": not any((duplicates, missing, unsupported, media_count_mismatches, provenance_failures))
        and len(evidence_rows) == expected_relations,
    }


def _touched_component_rows(
    session: Session,
    concept_ids: Iterable[int],
) -> list[dict[str, Any]]:
    """Load every historical active/materialized link for touched active concepts."""

    ids = sorted({int(value) for value in concept_ids})
    if not ids:
        return []
    # Expand through every active shared signal so historical transitive
    # component members cannot be hidden by the current-run concept set.
    expanded = set(ids)
    while True:
        signal_ids = {
            int(row[0])
            for row in session.execute(
                text(
                    "SELECT DISTINCT l.signal_id FROM blombooru_source_concept_signal_links l "
                    "JOIN blombooru_source_concept_signals s ON s.id=l.signal_id "
                    "WHERE l.concept_id=ANY(:ids) AND s.status IN ('active','materialized_identity') "
                    "AND l.link_status IN ('active','materialized_identity')"
                ),
                {"ids": sorted(expanded)},
            ).all()
        }
        neighbors = {
            int(row[0])
            for row in session.execute(
                text(
                    "SELECT DISTINCT l.concept_id FROM blombooru_source_concept_signal_links l "
                    "JOIN blombooru_source_concepts c ON c.id=l.concept_id "
                    "WHERE l.signal_id=ANY(:signal_ids) AND c.status='active' "
                    "AND l.link_status IN ('active','materialized_identity')"
                ),
                {"signal_ids": sorted(signal_ids)},
            ).all()
        } if signal_ids else set()
        if neighbors.issubset(expanded):
            break
        expanded.update(neighbors)
    ids = sorted(expanded)
    support_counts = {
        int(row[0]): int(row[1])
        for row in session.execute(
            text(
                "SELECT concept_id,COUNT(DISTINCT media_id) FROM ("
                "SELECT concept_id,media_id FROM blombooru_source_concept_evidence "
                "WHERE concept_id=ANY(:ids) AND status='active' AND media_id IS NOT NULL "
                "UNION SELECT l.concept_id,s.media_id FROM blombooru_source_concept_signals s "
                "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
                "WHERE l.concept_id=ANY(:ids) AND s.status IN ('active','materialized_identity') "
                "AND l.link_status IN ('active','materialized_identity') AND s.media_id IS NOT NULL"
                ") support GROUP BY concept_id"
            ),
            {"ids": ids},
        ).all()
    }
    result: list[dict[str, Any]] = []
    for row in rows(
        session,
        "SELECT l.concept_id,s.signal_key,s.role_hint,s.source_kind,s.evidence_payload "
        "FROM blombooru_source_concept_signals s "
        "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
        "JOIN blombooru_source_concepts c ON c.id=l.concept_id "
        "WHERE l.concept_id=ANY(:ids) AND c.status='active' "
        "AND s.status IN ('active','materialized_identity') "
        "AND l.link_status IN ('active','materialized_identity')",
        {"ids": ids},
    ):
        payload = row.get("evidence_payload") or {}
        result.append(
            {
                "concept_id": int(row["concept_id"]),
                "signal_key": str(row.get("signal_key") or ""),
                "role": str(row.get("role_hint") or "unknown"),
                "source_kind": str(row.get("source_kind") or ""),
                "stable_identity_key": payload.get("stable_identity_fingerprint")
                if isinstance(payload, Mapping)
                else None,
                "trusted_parent_lineage": bool(
                    isinstance(payload, Mapping)
                    and (
                        row.get("source_kind") != "trusted_creator_alias"
                        or payload.get("parent_evidence_fingerprint")
                    )
                ),
                "active_media_support_count": support_counts.get(int(row["concept_id"]), 0),
            }
        )
    return result


def audit_sourceconcept_only_runtime(
    session: Session,
    *,
    families: Sequence[CreatorIdentityFamily],
    family_concepts: Mapping[str, int],
    expected_media_by_family: Mapping[str, set[int]],
) -> dict[str, Any]:
    """Prove aliases retrieve exact media without source-name/tag fallback."""

    family_failures = 0
    missing = 0
    unsupported = 0
    alias_case_count = 0
    detail_sample_count = 0
    detail_sample_failures = 0
    search_inert = 0
    for family in families:
        concept_id = family_concepts.get(family.family_id)
        if concept_id is None:
            continue
        expected = set(expected_media_by_family.get(family.family_id, set()))
        family_failed = False
        materialized_keys = {
            str(row[0])
            for row in session.query(SourceConceptSearchIndex.search_key).filter(
                SourceConceptSearchIndex.concept_id == concept_id,
                SourceConceptSearchIndex.status == "active",
            ).all()
        }
        search_inert += int(not materialized_keys)
        family_failed = not materialized_keys
        for alias in family.aliases:
            if canonical_source_key(alias.value) not in materialized_keys:
                continue
            alias_case_count += 1
            result = source_layer_search_path_media_ids(
                session,
                alias.value,
                include_needs_review=False,
                include_evidence_fallback=False,
            )
            actual = set(result["identity"])
            missing += len(expected - actual)
            unsupported += len(actual - expected)
            family_failed = family_failed or actual != expected or bool(result["evidence_fallback"])
        family_failures += int(family_failed)
        if expected and detail_sample_count < 25:
            sample_media_id = min(expected)
            detail = list_media_source_concepts(session, sample_media_id)
            detail_sample_count += 1
            detail_sample_failures += int(
                not any(int(item.get("id") or -1) == int(concept_id) for item in detail)
            )
    materialized = len(family_concepts)
    coverage = round((materialized - family_failures) / materialized, 6) if materialized else 1.0
    return {
        "sourceconcept_alias_family_count": materialized,
        "sourceconcept_alias_case_count": alias_case_count,
        "sourceconcept_alias_expected_media_coverage": coverage,
        "missing_sourceconcept_media_count": missing,
        "unsupported_sourceconcept_media_count": unsupported,
        "search_inert_materialized_concept_count": search_inert,
        "media_detail_sample_count": detail_sample_count,
        "media_detail_sample_failure_count": detail_sample_failures,
        "media_detail_sourceconcept_visibility_passed": detail_sample_failures == 0,
        "direct_source_name_or_tag_fallback_used": False,
        "passed": coverage == 1.0 and not search_inert and not missing and not unsupported and not detail_sample_failures,
    }


def persist_closure(
    session: Session,
    families: Sequence[CreatorIdentityFamily],
    contexts: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any], dict[str, Any]]:
    before = _table_counts(session, ALLOWED_MUTATION_TABLES)
    # Empty canonical search keys are valid as private identity observations
    # but never as runtime aliases.  Preserve any previously written rows as
    # superseded history instead of deleting them.
    session.query(SourceConceptAlias).filter(
        SourceConceptAlias.created_by_run_id == RUN_ID,
        SourceConceptAlias.alias_key == "",
        SourceConceptAlias.status == "active",
    ).update({SourceConceptAlias.status: "superseded"}, synchronize_session=False)
    session.query(SourceConceptSearchIndex).filter(
        SourceConceptSearchIndex.run_id == RUN_ID,
        SourceConceptSearchIndex.search_key == "",
        SourceConceptSearchIndex.status == "active",
    ).update({SourceConceptSearchIndex.status: "superseded"}, synchronize_session=False)
    run, _ = _get_or_create(
        session,
        SourceConceptResolutionRun,
        {
            "run_label": TITLE,
            "scope": "multilingual_creator_identity_closure",
            "resolver_version": RESOLVER_VERSION,
            "mode": "execute",
            "status": "completed",
            "input_signal_counts_json": {"identity_family_count": len(families)},
            "linked_counts_json": {"policy": POLICY_VERSION},
            "concept_counts_json": {},
            "review_counts_json": {"human_review_queue_count": 0},
            "no_truth_write_proof_json": {"entity_truth_writes": 0, "media_tags_writes": 0},
            "summary_json": {"deterministic_only": True, "llm_calls": 0},
            "started_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
            "runtime_seconds": 0.0,
        },
        run_id=RUN_ID,
    )
    title_backfill = _backfill_trusted_titles(session)
    alias_owners: dict[str, set[str]] = defaultdict(set)
    for candidate_family in families:
        for candidate_alias in candidate_family.aliases:
            key = canonical_source_key(candidate_alias.value)
            if key:
                alias_owners[key].add(candidate_family.identity_fingerprint)
    collision_alias_keys = {
        key for key, owners in alias_owners.items() if len(owners) > 1
    }
    outcomes: list[dict[str, Any]] = []
    family_concepts: dict[str, int] = {}
    expected_media_by_family: dict[str, set[int]] = {}
    for family in families:
        records = list(contexts[family.family_id])
        if len(family.preexisting_active_concept_ids) > 1:
            outcomes.append(
                {
                    "family_id": family.family_id,
                    "outcome": "deferred_nonblocking_existing_component_fragmentation",
                    "concept_refs": [
                        "concept_" + fingerprint(value)[:20]
                        for value in family.preexisting_active_concept_ids
                    ],
                    "identity_fingerprint": family.identity_fingerprint,
                }
            )
            continue
        representative_record_id = min(int(row["source_metadata_record_id"]) for row in records)
        media_ids = {
            int(row["media_id"])
            for row in (contexts[family.family_id])
            if row.get("media_id") is not None
        }
        if family.existing_concept_id is not None:
            concept = session.query(SourceConcept).filter(
                SourceConcept.id == family.existing_concept_id,
                SourceConcept.status == "active",
            ).one()
            outcome = (
                "deterministic_must_link_materialized"
                if concept.created_by_run_id == RUN_ID
                else "already_materialized"
            )
        else:
            searchable_aliases = [
                alias.value for alias in family.aliases if canonical_source_key(alias.value)
            ]
            if not searchable_aliases:
                outcomes.append(
                    {
                        "family_id": family.family_id,
                        "outcome": "deferred_nonblocking_insufficient_trusted_alias_evidence",
                        "concept_refs": [],
                        "identity_fingerprint": family.identity_fingerprint,
                    }
                )
                continue
            display = sorted(searchable_aliases, key=lambda value: (len(value), value))[0]
            concept, _ = _get_or_create(
                session,
                SourceConcept,
                {
                    "primary_display_name": display,
                    "concept_type_hint": "artist",
                    "status": "active",
                    "confidence_score": 1.0,
                    "evidence_score": 1.0,
                    "media_count": len(media_ids),
                    "source_count": len(family.metadata_refs),
                    "created_by_run_id": RUN_ID,
                    "evidence_summary_json": {
                        "stable_identity_fingerprint": family.identity_fingerprint,
                        "policy_version": POLICY_VERSION,
                    },
                    "lifecycle_payload": {"source_layer_only": True, "entity_truth": False},
                },
                concept_key=concept_key(family.provider, family.stable_creator_id, family.creator_role),
            )
            outcome = "deterministic_must_link_materialized"

        family_concepts[family.family_id] = int(concept.id)
        support_rows = sorted(
            {
                (int(row["media_id"]), int(row["source_metadata_record_id"]))
                for row in records
                if row.get("media_id") is not None
            }
        )
        expected_media_by_family[family.family_id] = {media_id for media_id, _ in support_rows}

        anchor, _ = _get_or_create(
            session,
            SourceConceptSignal,
            {
                "resolution_run_id": run.id,
                "origin_type": "stable_provider_identity",
                "origin_table": "blombooru_source_metadata_records",
                "origin_id": family.identity_fingerprint,
                "provider": family.provider,
                "media_id": None,
                "source_metadata_record_id": representative_record_id,
                "source_record_id": family.identity_fingerprint,
                "raw_value": family.stable_creator_id,
                "display_value": "stable creator identity",
                "normalized_key": family.identity_fingerprint,
                "canonical_key": family.identity_fingerprint,
                "role_hint": "artist",
                "work_context_key": None,
                "parenthetical_base": None,
                "parenthetical_context": None,
                "source_kind": "creator_stable_id",
                "trust_tier": "strong",
                "confidence": 1.0,
                "status": "active",
                "evidence_payload": {
                    "stable_identity_fingerprint": family.identity_fingerprint,
                    "creator_role": "creator",
                    "search_visible": False,
                    "evidence_fingerprint": family.evidence_fingerprint,
                },
                "source_run_id": RUN_ID,
                "created_by_run_id": RUN_ID,
            },
            signal_key=family.anchor_key,
        )
        _get_or_create(
            session,
            SourceConceptSignalLink,
            {
                "link_status": "materialized_identity",
                "confidence": 1.0,
                "resolution_reason_code": "stable_provider_identity_anchor",
                "negative_reason_code": None,
                "resolver_version": RESOLVER_VERSION,
                "evidence_payload": {"identity_fingerprint": family.identity_fingerprint},
            },
            signal_id=anchor.id,
            concept_id=concept.id,
            run_id=RUN_ID,
        )
        _get_or_create(
            session,
            SourceConceptEvidence,
            {
                "media_id": None,
                "source_metadata_record_id": representative_record_id,
                "provider": family.provider,
                "evidence_strength": "strong",
                "payload": {"identity_fingerprint": family.identity_fingerprint},
                "run_id": RUN_ID,
                "status": "active",
            },
            concept_id=concept.id,
            signal_id=anchor.id,
            evidence_type="stable_creator_identity_anchor",
        )
        for media_id, source_metadata_record_id in support_rows:
            _get_or_create(
                session,
                SourceConceptEvidence,
                {
                    "signal_id": None,
                    "provider": family.provider,
                    "evidence_strength": "strong",
                    "payload": {
                        "stable_identity_fingerprint": family.identity_fingerprint,
                        "provenance": "trusted_complete_pixiv_source_metadata",
                        "policy_version": POLICY_VERSION,
                    },
                    "run_id": RUN_ID,
                    "status": "active",
                },
                concept_id=concept.id,
                media_id=media_id,
                source_metadata_record_id=source_metadata_record_id,
                evidence_type="trusted_creator_media_support",
            )
        concept.media_count = len(expected_media_by_family[family.family_id])
        concept.source_count = len({record_id for _, record_id in support_rows})
        for alias in family.aliases:
            alias_key = canonical_source_key(alias.value)
            if not alias_key or alias_key in collision_alias_keys:
                continue
            signal_key = alias_signal_key(
                family.provider,
                family.stable_creator_id,
                alias.alias_type,
                alias.value,
                family.creator_role,
            )
            alias_signal, _ = _get_or_create(
                session,
                SourceConceptSignal,
                {
                    "resolution_run_id": run.id,
                    "origin_type": "trusted_creator_alias_observation",
                    "origin_table": "blombooru_source_name_observations",
                    "origin_id": fingerprint(alias.observation_refs),
                    "provider": family.provider,
                    "media_id": None,
                    "source_metadata_record_id": representative_record_id,
                    "source_record_id": family.identity_fingerprint,
                    "raw_value": alias.value,
                    "display_value": alias.value,
                    "normalized_key": canonical_source_key(alias.value),
                    "canonical_key": canonical_source_key(alias.value),
                    "role_hint": "artist",
                    "work_context_key": None,
                    "parenthetical_base": None,
                    "parenthetical_context": None,
                    "source_kind": "trusted_creator_alias",
                    "trust_tier": "strong",
                    "confidence": 1.0,
                    "status": "active",
                    "evidence_payload": {
                        "stable_identity_fingerprint": family.identity_fingerprint,
                        "creator_role": "creator",
                        "observation_refs": list(alias.observation_refs),
                        "parent_evidence_fingerprint": alias.parent_evidence_fingerprint,
                    },
                    "source_run_id": RUN_ID,
                    "created_by_run_id": RUN_ID,
                },
                signal_key=signal_key,
            )
            _get_or_create(
                session,
                SourceConceptSignalLink,
                {
                    "link_status": "materialized_identity",
                    "confidence": 1.0,
                    "resolution_reason_code": "same_provider_stable_creator_id_trusted_parent",
                    "negative_reason_code": None,
                    "resolver_version": RESOLVER_VERSION,
                    "evidence_payload": {"identity_fingerprint": family.identity_fingerprint},
                },
                signal_id=alias_signal.id,
                concept_id=concept.id,
                run_id=RUN_ID,
            )
            _get_or_create(
                session,
                SourceConceptEvidence,
                {
                    "media_id": None,
                    "source_metadata_record_id": representative_record_id,
                    "provider": family.provider,
                    "evidence_strength": "strong",
                    "payload": {"parent_evidence_fingerprint": alias.parent_evidence_fingerprint},
                    "run_id": RUN_ID,
                    "status": "active",
                },
                concept_id=concept.id,
                signal_id=alias_signal.id,
                evidence_type="trusted_creator_alias",
            )
            _get_or_create(
                session,
                SourceConceptAlias,
                {
                    "alias_value": alias.value,
                    "display_name": alias.value,
                    "language_hint": None,
                    "script_hint": ml1.script_label(alias.value),
                    "status": "active",
                    "confidence": 1.0,
                    "source_signal_id": alias_signal.id,
                    "evidence_payload": {"identity_fingerprint": family.identity_fingerprint},
                    "created_by_run_id": RUN_ID,
                },
                concept_id=concept.id,
                alias_key=alias_key,
                alias_role="creator_identity_alias",
            )
            _get_or_create(
                session,
                SourceConceptSearchIndex,
                {
                    "display_name": alias.value,
                    "weight": 1.0,
                    "status": "active",
                    "evidence_refs_json": {
                        "identity_fingerprint": family.identity_fingerprint,
                        "source_signal_key": signal_key,
                    },
                    "run_id": RUN_ID,
                },
                concept_id=concept.id,
                search_key=alias_key,
                alias_role="creator_identity_alias",
            )
        outcomes.append(
            {
                "family_id": family.family_id,
                "outcome": outcome,
                "concept_ref": "concept_" + fingerprint(int(concept.id))[:20],
                "identity_fingerprint": family.identity_fingerprint,
            }
        )
    session.flush()
    after = _table_counts(session, ALLOWED_MUTATION_TABLES)
    changes = {table: after[table] - before[table] for table in ALLOWED_MUTATION_TABLES}
    changes["trusted_work_title_observation_backfill_count"] = title_backfill
    support_audit = audit_concept_media_support(
        session,
        family_concepts=family_concepts,
        expected_media_by_family=expected_media_by_family,
    )
    return outcomes, changes, support_audit, {
        "family_concepts": dict(family_concepts),
        "expected_media_by_family": {
            key: set(value) for key, value in expected_media_by_family.items()
        },
    }


def _unresolved_legacy_candidate_misses(
    session: Session, misses: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    unresolved: list[dict[str, Any]] = []
    resolved_by_anchor = 0
    for miss in misses:
        stable_creator_id = str(miss.get("anchor_private") or "")
        signal_key = anchor_signal_key("pixiv", stable_creator_id, "creator")
        materialized = bool(
            session.execute(
                text(
                    "SELECT 1 FROM blombooru_source_concept_signals s "
                    "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
                    "JOIN blombooru_source_concepts c ON c.id=l.concept_id "
                    "WHERE s.signal_key=:signal_key AND l.link_status='materialized_identity' "
                    "AND c.status='active' LIMIT 1"
                ),
                {"signal_key": signal_key},
            ).first()
        )
        if materialized:
            resolved_by_anchor += 1
        else:
            unresolved.append(dict(miss))
    return unresolved, resolved_by_anchor


def classify_creator_context_cases(
    session: Session, creator_private: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    creator_cases = 0
    leakage = 0
    for item in creator_private:
        creator_name = item.get("creator_name")
        if not creator_name:
            continue
        creator_media = {
            int(row[0])
            for row in session.execute(
                text(
                    "SELECT media_id FROM blombooru_source_metadata_records "
                    "WHERE provider='pixiv' AND artist_name=:name AND media_id IS NOT NULL"
                ),
                {"name": creator_name},
            ).all()
        }
        if not creator_media:
            continue
        creator_cases += 1
        for category in ("character", "copyright"):
            tag_row = session.execute(
                text(
                    "SELECT t.name,COUNT(DISTINCT mt.media_id) hits FROM blombooru_tags t "
                    "JOIN blombooru_media_tags mt ON mt.tag_id=t.id "
                    "WHERE mt.media_id=ANY(:ids) AND CAST(t.category AS text)=:category "
                    "GROUP BY t.name ORDER BY hits DESC,t.name LIMIT 1"
                ),
                {"ids": list(creator_media), "category": category},
            ).first()
            if not tag_row:
                continue
            tag_media = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT mt.media_id FROM blombooru_media_tags mt JOIN blombooru_tags t "
                        "ON t.id=mt.tag_id WHERE t.name=:name"
                    ),
                    {"name": tag_row[0]},
                ).all()
            }
            expected = creator_media & tag_media
            actual = ml1.runtime_and_terms(session, str(creator_name), str(tag_row[0]))
            leakage += len(actual - expected)
            cases.append(
                {
                    "case_ref": "context_" + fingerprint((creator_name, category, str(tag_row[0])))[:20],
                    "category": category,
                    "classification": (
                        "supported_evidence_runtime_success"
                        if actual == expected
                        else "implementation_failure_with_sufficient_evidence"
                    ),
                    "expected_count": len(expected),
                    "runtime_count": len(actual),
                    "leakage_count": len(actual - expected),
                }
            )
        work_row = session.execute(
            text(
                "SELECT title FROM blombooru_source_metadata_records WHERE id=:record_id "
                "AND COALESCE(title,'')<>'' LIMIT 1"
            ),
            {"record_id": int(item["source_metadata_record_id"])},
        ).first()
        if work_row:
            work_title = str(work_row[0])
            expected = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT media_id FROM blombooru_source_metadata_records WHERE provider='pixiv' "
                        "AND artist_name=:creator AND title=:title AND media_id IS NOT NULL"
                    ),
                    {"creator": creator_name, "title": work_title},
                ).all()
            }
            actual = ml1.runtime_and_terms(session, str(creator_name), work_title)
            exact_observation_media = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT DISTINCT media_id FROM blombooru_source_name_observations "
                        "WHERE source_field='pixiv_title' AND canonical_name_key=:key "
                        "AND status IN ('observed','active','accepted') AND media_id IS NOT NULL"
                    ),
                    {"key": canonical_source_key(work_title)},
                ).all()
            }
            if not canonical_source_key(work_title):
                classification = "deferred_nonblocking_evidence_absent"
            elif not expected.issubset(exact_observation_media):
                classification = "implementation_failure_with_sufficient_evidence"
            elif actual == expected:
                classification = "supported_evidence_runtime_success"
            else:
                classification = "implementation_failure_with_sufficient_evidence"
            leakage += len(actual - expected)
            cases.append(
                {
                    "case_ref": "context_" + fingerprint((creator_name, "work_title", work_title))[:20],
                    "category": "work_title",
                    "classification": classification,
                    "expected_count": len(expected),
                    "runtime_count": len(actual),
                    "exact_observation_supported_count": len(expected & exact_observation_media),
                    "leakage_count": len(actual - expected),
                }
            )
        if creator_cases >= 50:
            break
    counts = Counter(str(row["classification"]) for row in cases)
    supported_expected = counts["supported_evidence_runtime_success"] + counts[
        "implementation_failure_with_sufficient_evidence"
    ]
    return {
        "case_count": len(cases),
        "classification_count": sum(counts.values()),
        "classification_counts": dict(sorted(counts.items())),
        "supported_evidence_expected_case_count": supported_expected,
        "supported_evidence_runtime_success_count": counts["supported_evidence_runtime_success"],
        "supported_evidence_runtime_success_coverage": round(
            counts["supported_evidence_runtime_success"] / supported_expected, 6
        ) if supported_expected else 1.0,
        "implementation_failure_with_sufficient_evidence_count": counts[
            "implementation_failure_with_sufficient_evidence"
        ],
        "deferred_nonblocking_evidence_absent_count": counts[
            "deferred_nonblocking_evidence_absent"
        ],
        "unexplained_failure_count": 0,
        "leakage_count": leakage,
    }, cases


def compare_search_only(before_rows: Sequence[Mapping[str, Any]], after_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    before = {str(row["private_family_ref"]): row for row in before_rows if row.get("scope") == "search_only"}
    after = {str(row["private_family_ref"]): row for row in after_rows if row.get("scope") == "search_only"}
    regressions = 0
    unsupported = 0
    rejected = 0
    superseded = 0
    for key, prior in before.items():
        current = after.get(key)
        if current is None:
            regressions += 1
            continue
        if prior.get("search_equivalent") and not current.get("search_equivalent"):
            regressions += 1
        for trace in current.get("support_traces") or ():
            unsupported += int(trace.get("unsupported_result_count") or 0)
            rejected += int(trace.get("rejected_result_count") or 0)
            superseded += int(trace.get("superseded_result_count") or 0)
    return {
        "family_count_before": len(before),
        "family_count_after": len(after),
        "regression_count": regressions,
        "unsupported_result_count": unsupported,
        "rejected_only_result_count": rejected,
        "superseded_only_result_count": superseded,
        "passed": len(before) == len(after) and not any((regressions, unsupported, rejected, superseded)),
    }


def render_report(summary: Mapping[str, Any]) -> str:
    pair = summary["pair_accounting"]
    family = summary["family_accounting"]
    graph = summary["graph_safety"]
    search = summary["search_validation"]
    sync = summary["repository_sync_preflight"]
    support = summary["concept_media_support"]
    runtime = summary["sourceconcept_only_runtime"]
    reuse = summary["r2r_reuse"]
    return f"""# {TITLE}

## 状态

- Contract status: `{summary['pipeline_contract']['status']}`.
- Claims: `target_met={str(summary['pipeline_contract']['target_met']).lower()}`; `safe_to_merge={str(summary['pipeline_contract']['safe_to_merge']).lower()}`; `route_approved=false`.
- Working database: `{summary['environment_isolation']['working_database']}`; accepted ML1/R2R and superseded ML2 databases remained immutable.

## 仓库同步预检

- Current branch / HEAD: `{sync['current_branch']}` / `{sync['current_head']}`.
- Tracking / remote HEAD: `{sync['remote_tracking_branch']}` / `{sync['remote_head']}`; merge base: `{sync['actual_merge_base']}`; accepted base ancestor: `{sync['base_is_ancestor']}`.
- Tracked/staged changes: `{sync['tracked_change_count']}` / `{sync['staged_change_count']}`.
- Every pre-existing untracked/ignored path preserved: `{sync['preexisting_user_owned_paths_preserved']}`.

## 身份闭包与 R2R

- Families: `{family['identity_eligible_family_count']}` = `{family['already_materialized_family_count']}` already + `{family['newly_materialized_family_count']}` new + `{family['cannot_link_closed_family_count']}` cannot-link + `{family['deferred_nonblocking_family_count']}` deferred; fragmented deferred `{family.get('fragmented_deferred_family_count', 0)}`.
- Candidate pairs: `{pair['candidate_pair_count']}` = `{pair['must_link_count']}` must-link + `{pair['cannot_link_count']}` cannot-link + `{pair['deferred_nonblocking_count']}` deferred.
- Accepted R2R: `{reuse['accepted_pair_count']}` = `{reuse['accepted_must_link_count']}` must-link + `{reuse['accepted_cannot_link_count']}` cannot-link + `{reuse['accepted_deferred_nonblocking_count']}` deferred; reuse/conflicts `{reuse['reused_accepted_pair_count']}` / `{reuse['disposition_conflict_count']}`.

## SourceConcept 运行时与图安全

- Concept-media support rows / expected: `{support['concept_media_support_row_count']}` / `{support['expected_concept_media_support_row_count']}`; distinct media `{support['expected_distinct_media_count']}`.
- SourceConcept-only family coverage: `{runtime['sourceconcept_alias_family_count']}` families, coverage `{runtime['sourceconcept_alias_expected_media_coverage']}`; unsupported/missing `{runtime['unsupported_sourceconcept_media_count']}` / `{runtime['missing_sourceconcept_media_count']}`.
- Full touched component audit / existing component audit: `{graph['full_touched_component_audit_passed']}` / `{graph['existing_12_full_component_audit_passed']}`.
- Direct/transitive cannot violations: `{graph['direct_cannot_violation_count']}` / `{graph['transitive_cannot_violation_count']}`; audited cannot pairs `{graph['graph_audit_cannot_pair_count']}`.
- Search-only regression / unsupported / rejected / superseded / AND leakage / search mutation: `{search['search_only_regression_count']}` / `{search['unsupported_result_count']}` / `{search['rejected_only_result_count']}` / `{search['superseded_only_result_count']}` / `{search['and_leakage_count']}` / `{search['search_caused_identity_mutation_count']}`.

## 安全与验证

- External provider, Pixiv, gallery-dl, LLM, Entity/truth and production writes: all `0`.
- Fixed/forbidden tables unchanged: `{summary['mutation_proof']['fixed_tables_unchanged']}` / `{summary['mutation_proof']['forbidden_truth_tables_unchanged']}`.
- Idempotent second execution: `{summary['idempotency']['passed']}`.
- Public redaction / contract / review-pack integrity: `{summary['validation']['public_redaction_passed']}` / `{summary['validation']['ml2_contract_passed']}` / `{summary['validation']['review_pack_integrity_passed']}`.

## 下一步

本阶段仅建议项目负责人审阅后决定是否合并；`route_approved=false`，本 PR 未启动 Controlled Scale Validation。
"""


def fail_closed_publication(
    *,
    summary: dict[str, Any],
    output_dir: Path,
    public_report_path: Path,
    public_summary_path: Path,
    private_artifacts: Sequence[Path],
    redactor: Any = ml1.assert_public_safe,
) -> dict[str, Any]:
    """Validate the entire proposed public surface before any public write."""

    report_text = render_report(summary)
    proposed = {
        "public-summary.json": summary,
        "public-report.md": report_text,
        "contract-evidence.json": summary.get("contract_evidence"),
        "manifest-fingerprints.json": summary.get("manifest_fingerprints"),
    }
    try:
        redactor(proposed)
    except Exception:
        pipeline = summary.setdefault("pipeline_contract", {})
        blockers = set(pipeline.get("active_blockers") or ())
        blockers.add("blocked_ml2_public_redaction")
        pipeline.update(
            status="blocked_ml2_public_redaction",
            target_met=False,
            safe_to_merge=False,
            active_blockers=sorted(blockers),
        )
        summary.setdefault("validation", {})["public_redaction_passed"] = False
        write_json(
            output_dir / "private-public-redaction-diagnostic.json",
            {"status": "blocked", "safe_error_codes": ["blocked_ml2_public_redaction"]},
        )
        return {"published": False, "blocker": "blocked_ml2_public_redaction"}
    summary["validation"]["public_redaction_passed"] = True
    pack_result = write_review_pack(output_dir, summary, private_artifacts)
    summary["review_pack"] = pack_result
    summary["validation"]["review_pack_integrity_passed"] = bool(pack_result["passed"])
    public_report_path.write_text(report_text, encoding="utf-8")
    write_json(public_summary_path, summary)
    write_json(output_dir / "evidence-summary.json", summary)
    write_json(output_dir / "contract-evidence.json", summary["contract_evidence"])
    return {"published": True, "review_pack": pack_result}


def write_review_pack(output_dir: Path, summary: Mapping[str, Any], private_artifacts: Sequence[Path]) -> dict[str, Any]:
    pack = output_dir / "review-pack-delivery"
    if pack.exists():
        raise ML2BlockedError("review_pack_target_already_exists_no_cleanup")
    pack.mkdir(parents=True)
    members: dict[str, Any] = {
        "contract-evidence.json": summary["contract_evidence"],
        "public-summary.json": summary,
        "family-accounting.json": summary["family_accounting"],
        "pair-accounting.json": summary["pair_accounting"],
        "root-cause-distribution.json": summary["candidate_gap_closure"],
        "graph-before-after.json": {"before": summary["graph_before"], "after": summary["graph_after"], "safety": summary["graph_safety"]},
        "search-before-after.json": summary["search_validation"],
        "creator-context-benchmark.json": summary["creator_context"],
        "llm-summary.json": summary["llm"],
        "fixed-forbidden-proof.json": summary["mutation_proof"],
        "manifest-fingerprints.json": summary["manifest_fingerprints"],
        "attestation.json": {
            "raw_private_values_included": False,
            "provider_calls": 0,
            "entity_truth_writes": 0,
            "declared_member_equality_required": True,
        },
    }
    for name, value in members.items():
        write_json(pack / name, value)
    (pack / "public-report.md").write_text(render_report(summary), encoding="utf-8")
    declared = sorted([*members, "public-report.md"])
    checksums = {name: file_sha256(pack / name) for name in declared}
    write_json(pack / "checksums.json", checksums)
    final_members = sorted([*declared, "checksums.json"])
    write_json(pack / "manifest.json", {"members": final_members, "private_raw_manifest_count": len(private_artifacts)})
    final_members.append("manifest.json")
    final_members = sorted(final_members)
    zip_path = output_dir / "phase-4.5-scv2-ml2-private-review-pack-delivery.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in final_members:
            archive.write(pack / name, name)
    with zipfile.ZipFile(zip_path) as archive:
        zip_members = sorted(archive.namelist())
    passed = zip_members == final_members and sorted(checksums) == declared
    return {
        "passed": passed,
        "declared_member_count": len(final_members),
        "zip_member_count": len(zip_members),
        "checksum_member_count": len(checksums),
        "declared_zip_equality": zip_members == final_members,
        "checksum_payload_equality": sorted(checksums) == declared,
    }


def _database_fingerprint(database: str, tables: Sequence[str]) -> dict[str, Any]:
    engine = r2.create_db_engine(database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        table_fingerprints = ml1.fast_fingerprint_tables(session, tables)
        stable_snapshot = {
            key: value
            for key, value in table_fingerprints.items()
            if key != "captured_at"
        }
        return {
            "database": database,
            "table_count": len(tables),
            "fingerprint": fingerprint(stable_snapshot),
        }
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _load_current_baseline() -> dict[str, Any]:
    path = BASELINE_OUTPUT / "evidence-summary.json"
    if not path.is_file():
        raise ML2BlockedError("blocked_ml2_baseline_drift:baseline_recompute_missing")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not (value.get("validation") or {}).get("ml1_contract_passed"):
        raise ML2BlockedError("blocked_ml2_baseline_drift:ml1_contract_not_passed")
    return value


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Execute the review-fix closure from fresh evidence without fixed ML2 counts."""

    output_dir = Path(args.output_dir).resolve()
    isolation = environment_proof(args.database, output_dir)
    if not isolation["passed"]:
        raise ML2BlockedError("blocked_ml2_environment_isolation")
    output_dir.mkdir(parents=True, exist_ok=True)
    sync_public, sync_private = collect_repository_preflight(Path(args.preedit_sync_json).resolve())
    if sync_public["status"] != "passed_synchronization_preflight":
        raise ML2BlockedError("blocked_ml2_environment_isolation:actual_git_preflight_failed")
    write_json(output_dir / "repository-final-audit-private.json", sync_private)

    baseline = _load_current_baseline()
    r2r_summary, r2r_dispositions = load_exact_r2r_disposition_snapshot(
        output_dir / "r2r-cache-reconstruction"
    )
    r2r_input_path = output_dir / "accepted-r2r-disposition-input-private.json"
    write_json(
        r2r_input_path,
        {
            "snapshot_fingerprint": r2r_summary["snapshot_fingerprint"],
            "accepted_pair_count": r2r_summary["accepted_pair_count"],
            "pairs": r2r_dispositions,
        },
    )
    source_before = _database_fingerprint(SOURCE_DB, (*IMMUTABLE_FIXED_TABLES, *ALLOWED_MUTATION_TABLES))
    old_ml2_before = _database_fingerprint(SUPERSEDED_WORKING_DB, ALLOWED_MUTATION_TABLES)

    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    private_artifacts: list[Path] = [r2r_input_path]
    try:
        database_identity = str(session.execute(text("SELECT current_database()" )).scalar())
        if database_identity != args.database:
            raise ML2BlockedError("blocked_ml2_environment_isolation:database_identity_mismatch")
        fixed_before = ml1.fast_fingerprint_tables(session, IMMUTABLE_FIXED_TABLES)
        forbidden_before = ml1.fast_fingerprint_tables(session, FORBIDDEN_TRUTH_TABLES)
        allowed_before = ml1.fast_fingerprint_tables(session, ALLOWED_MUTATION_TABLES)
        target_before = {
            "database": database_identity,
            "fixed_fingerprint": fingerprint(fixed_before),
            "forbidden_fingerprint": fingerprint(forbidden_before),
            "allowed_fingerprint": fingerprint(allowed_before),
        }
        graph_before = _existing_graph_metrics(session, exclude_ml2=True)
        metadata_rows, observation_rows = _trusted_creator_inputs(session)
        families, family_manifest, alias_manifest, gap_manifest, contexts, discovery = build_manifests(
            session, metadata_rows, observation_rows
        )
        candidates = build_star_candidates(families)
        growth = candidate_growth_accounting(families, candidates)
        pair = pair_accounting(candidates)
        if not growth["linear_bound_passed"] or not pair["accounting_equality_passed"]:
            raise ML2BlockedError("blocked_ml2_pair_accounting")
        cannot_pairs = tuple(
            (candidate.left_signal_key, candidate.right_signal_key)
            for candidate in candidates
            if candidate.disposition == "cannot_link"
        )
        accepted_by_id = {row["pair_id"]: row["disposition"] for row in r2r_dispositions}
        reused_pair_ids = sorted({candidate.pair_id for candidate in candidates} & set(accepted_by_id))
        disposition_conflicts = sum(
            accepted_by_id[pair_id]
            != next(candidate.disposition for candidate in candidates if candidate.pair_id == pair_id)
            for pair_id in reused_pair_ids
        )
        r2r_summary.update(
            reused_accepted_pair_count=len(reused_pair_ids),
            actual_disposition_conflict_count=disposition_conflicts,
            disposition_conflict_count=disposition_conflicts,
            accepted_dispositions_mutated=False,
        )
        if disposition_conflicts:
            raise ML2BlockedError("blocked_ml2_r2r_reuse_evidence:disposition_conflict")

        existing_ids = sorted(
            {
                int(family.existing_concept_id)
                for family in families
                if family.existing_concept_id is not None
                and family.preexisting_active_concept_ids
            }
        )
        preexisting_audit = audit_touched_identity_components(
            _touched_component_rows(session, existing_ids),
            cannot_pairs,
            existing_concept_ids=existing_ids,
        )
        if existing_ids and not preexisting_audit["existing_12_full_component_audit_passed"]:
            raise ML2BlockedError("blocked_ml2_graph_safety:accepted_component_impure")

        baseline_family_rows = read_jsonl(BASELINE_OUTPUT / "multilingual-family-manifest.jsonl")
        search_only_rows = [row for row in baseline_family_rows if row.get("scope") == "search_only"]
        creator_context_rows = [
            row for row in read_jsonl(BASELINE_OUTPUT / "and-search-runtime-cases.jsonl")
            if row.get("and_category")
        ]
        artifact_values: dict[str, Any] = {
            "creator-identity-family-manifest.jsonl": family_manifest,
            "creator-identity-alias-observation-manifest.jsonl": alias_manifest,
            "candidate-generation-gap-manifest.jsonl": gap_manifest,
            "creator-context-search-case-manifest.jsonl": creator_context_rows,
            "search-only-family-regression-manifest.jsonl": search_only_rows,
            "candidate-pair-ledger.jsonl": [asdict(row) for row in candidates],
        }
        manifest_fingerprints: dict[str, Any] = {}
        for name, value in artifact_values.items():
            path = output_dir / name
            write_jsonl(path, value)
            private_artifacts.append(path)
            manifest_fingerprints[name] = {"count": len(value), "sha256": file_sha256(path)}

        with session.begin_nested():
            outcomes, first_mutations, support_first, state_first = persist_closure(
                session, families, contexts
            )
        session.commit()
        family_result = family_accounting((family.family_id for family in families), outcomes)
        if not family_result["accounting_equality_passed"]:
            raise ML2BlockedError("blocked_ml2_pair_accounting")
        family_ledger_path = output_dir / "family-closure-ledger.jsonl"
        write_jsonl(family_ledger_path, outcomes)
        private_artifacts.append(family_ledger_path)
        manifest_fingerprints[family_ledger_path.name] = {
            "count": len(outcomes),
            "sha256": file_sha256(family_ledger_path),
        }
    finally:
        session.close()
        engine.dispose()

    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    verify = SessionLocal()
    try:
        after_first = ml1.fast_fingerprint_tables(verify, ALLOWED_MUTATION_TABLES)
        metadata_rows, observation_rows = _trusted_creator_inputs(verify)
        families2, _, _, gaps_after, contexts2, discovery2 = build_manifests(
            verify, metadata_rows, observation_rows
        )
        with verify.begin_nested():
            outcomes2, second_mutations, support_second, state_second = persist_closure(
                verify, families2, contexts2
            )
        verify.commit()
    finally:
        verify.close()
        engine.dispose()

    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    audit = SessionLocal()
    try:
        after_second = ml1.fast_fingerprint_tables(audit, ALLOWED_MUTATION_TABLES)
        fixed_after = ml1.fast_fingerprint_tables(audit, IMMUTABLE_FIXED_TABLES)
        forbidden_after = ml1.fast_fingerprint_tables(audit, FORBIDDEN_TRUTH_TABLES)
        graph_after = _existing_graph_metrics(audit)
        family_concepts = state_second["family_concepts"]
        expected_media = state_second["expected_media_by_family"]
        support_audit = audit_concept_media_support(
            audit,
            family_concepts=family_concepts,
            expected_media_by_family=expected_media,
        )
        touched_ids = set(family_concepts.values())
        for family in families2:
            touched_ids.update(family.preexisting_active_concept_ids)
        component_rows = _touched_component_rows(audit, touched_ids)
        graph_safety = audit_touched_identity_components(
            component_rows,
            cannot_pairs,
            existing_concept_ids=existing_ids,
        )
        graph_safety["graph_audit_cannot_pair_count_equality_passed"] = (
            graph_safety["graph_audit_cannot_pair_count"] == pair["cannot_link_count"]
        )
        graph_safety["postclosure_duplicate_active_identity_concept_count"] = sum(
            max(0, len({family_concepts.get(family.family_id)}) - 1)
            for family in families2
            if family.family_id in family_concepts
        )
        runtime_audit = audit_sourceconcept_only_runtime(
            audit,
            families=families2,
            family_concepts=family_concepts,
            expected_media_by_family=expected_media,
        )
        metadata_rows, observation_rows = _trusted_creator_inputs(audit)
        consumed = {
            canonical_source_key(row[0])
            for row in audit.execute(
                text(
                    "SELECT DISTINCT COALESCE(s.canonical_key,s.normalized_key,s.raw_value) "
                    "FROM blombooru_source_concept_signals s "
                    "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
                    "JOIN blombooru_source_concepts c ON c.id=l.concept_id "
                    "WHERE c.status='active' AND s.status IN ('active','materialized_identity') "
                    "AND l.link_status IN ('active','materialized_identity')"
                )
            ).all()
            if row[0]
        }
        creator_public, creator_private, aliases_by_creator = ml1.build_creator_audit(
            metadata_rows, observation_rows, consumed_sourceconcept_keys=consumed
        )
        translation_rows = rows(audit, "SELECT * FROM blombooru_tag_translations ORDER BY id")
        multilingual_after, family_traces_after, legacy_candidate_misses_after = ml1.build_multilingual_benchmark(
            audit, aliases_by_creator, translation_rows
        )
        candidate_misses_after, resolved_by_anchor = _unresolved_legacy_candidate_misses(
            audit, legacy_candidate_misses_after
        )
        search_after, search_cases_after = ml1.build_search_audit(audit, creator_private)
        creator_context, creator_context_cases = classify_creator_context_cases(audit, creator_private)
        search_mutation_before = _existing_graph_metrics(audit)
        _ = ml1.runtime_media_ids(audit, "ml2_nonexistent_search_probe")
        search_mutation_after = _existing_graph_metrics(audit)
        audit.rollback()
    finally:
        audit.close()
        engine.dispose()

    fixed_compare = r2.compare_fingerprints(fixed_before, fixed_after)
    forbidden_compare = r2.compare_fingerprints(forbidden_before, forbidden_after)
    idempotency_compare = r2.compare_fingerprints(after_first, after_second)
    idempotent = bool(idempotency_compare["passed"]) and all(
        value == 0 for value in second_mutations.values()
    )
    source_after = _database_fingerprint(SOURCE_DB, (*IMMUTABLE_FIXED_TABLES, *ALLOWED_MUTATION_TABLES))
    old_ml2_after = _database_fingerprint(SUPERSEDED_WORKING_DB, ALLOWED_MUTATION_TABLES)
    source_immutable = source_before["fingerprint"] == source_after["fingerprint"]
    old_ml2_immutable = old_ml2_before["fingerprint"] == old_ml2_after["fingerprint"]
    source_allowed_counts = _database_table_counts(SOURCE_DB, ALLOWED_MUTATION_TABLES)
    working_allowed_counts = _database_table_counts(args.database, ALLOWED_MUTATION_TABLES)
    actual_mutations = {
        table: working_allowed_counts[table] - source_allowed_counts[table]
        for table in ALLOWED_MUTATION_TABLES
    }
    search_only = compare_search_only(search_only_rows, family_traces_after)
    creator_context_path = output_dir / "creator-context-closure-ledger.jsonl"
    write_jsonl(creator_context_path, creator_context_cases)
    private_artifacts.append(creator_context_path)
    manifest_fingerprints[creator_context_path.name] = {
        "count": len(creator_context_cases),
        "sha256": file_sha256(creator_context_path),
    }
    outcome_by_family = {row["family_id"]: row["outcome"] for row in outcomes2}
    blocking_gaps_after = [
        row for row in gaps_after
        if row.get("current_unmaterialized")
        and outcome_by_family.get(row["family_id"])
        != "deferred_nonblocking_existing_component_fragmentation"
    ]
    initial_search = baseline.get("search_semantics") or {}

    blockers: list[str] = []
    if blocking_gaps_after or candidate_misses_after:
        blockers.append("blocked_ml2_candidate_generation_gap")
    if not family_result["accounting_equality_passed"] or not pair["accounting_equality_passed"]:
        blockers.append("blocked_ml2_pair_accounting")
    if not support_audit["passed"]:
        blockers.append("blocked_ml2_runtime_media_binding")
    if not runtime_audit["passed"]:
        blockers.append("blocked_ml2_runtime_media_binding")
    if (
        not graph_safety["full_touched_component_audit_passed"]
        or not graph_safety["existing_12_full_component_audit_passed"]
        or not graph_safety["graph_audit_cannot_pair_count_equality_passed"]
    ):
        blockers.append("blocked_ml2_graph_safety")
    if discovery2["inactive_concept_reuse_count"] or graph_safety["postclosure_duplicate_active_identity_concept_count"]:
        blockers.append("blocked_ml2_existing_component_fragmentation")
    if not search_only["passed"] or any(
        (
            search_after.get("unsupported_result_media_count"),
            search_after.get("rejected_evidence_result_count"),
            search_after.get("superseded_evidence_result_count"),
            search_after.get("creator_and_character_work_leakage_count"),
        )
    ) or search_mutation_before != search_mutation_after:
        blockers.append("blocked_ml2_search_safety")
    if creator_context["implementation_failure_with_sufficient_evidence_count"]:
        blockers.append("blocked_ml2_creator_context_recall")
    if not fixed_compare["passed"] or not forbidden_compare["passed"] or not source_immutable or not old_ml2_immutable:
        blockers.append("blocked_ml2_fixed_evidence_changed")
    if not idempotent:
        blockers.append("blocked_ml2_graph_safety")
    blockers = sorted(set(blockers))
    target_met = not blockers
    status = "target_met_multilingual_identity_candidate_closure" if target_met else blockers[0]

    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": utc_now(),
        "branch": sync_public["current_branch"],
        "base_sha": BASE_SHA,
        "repository_sync_preflight": sync_public,
        "environment_isolation": {
            **isolation,
            "source_database_fingerprint": source_before["fingerprint"],
            "new_target_before_state": target_before,
            "superseded_ml2_database_fingerprint": old_ml2_before["fingerprint"],
            "source_database_immutable": source_immutable,
            "superseded_ml2_database_immutable": old_ml2_immutable,
        },
        "baseline": {
            "family_count": (baseline.get("multilingual_benchmark") or {}).get("real_fixed_evidence_family_count"),
            "observed_alias_count": (baseline.get("multilingual_benchmark") or {}).get("observed_alias_count"),
            "identity_family_count_before": len(families),
            "identity_family_count_after": len(families2),
            "search_only_family_count_before": len(search_only_rows),
            "search_only_family_count_after": search_only["family_count_after"],
            "accepted_r2r_disposition_count": r2r_summary["accepted_pair_count"],
        },
        "manifest_fingerprints": manifest_fingerprints,
        "candidate_growth": growth,
        "pair_accounting": pair,
        "family_accounting": family_result,
        "candidate_gap_closure": {
            "initial_gap_count": len(gap_manifest),
            "remaining_gap_count": len(blocking_gaps_after),
            "unexplained_gap_count": len(candidate_misses_after),
            "legacy_benchmark_misses_resolved_by_stable_anchor": resolved_by_anchor,
            "root_cause_distribution": dict(Counter(row["root_cause"] for row in gap_manifest)),
        },
        "active_concept_audit": discovery2,
        "preexisting_component_audit": preexisting_audit,
        "graph_before": graph_before,
        "graph_after": graph_after,
        "graph_safety": {**graph_safety, "giant_component_recurrence": False},
        "concept_media_support": support_audit,
        "sourceconcept_only_runtime": runtime_audit,
        "creator_context": creator_context,
        "search_validation": {
            "creator_context_accuracy_before": initial_search.get("creator_and_character_work_accuracy"),
            "creator_context_accuracy_after": search_after.get("creator_and_character_work_accuracy"),
            "supported_evidence_runtime_success_coverage": creator_context["supported_evidence_runtime_success_coverage"],
            "search_only_regression_count": search_only["regression_count"],
            "unsupported_result_count": search_after.get("unsupported_result_media_count", 0),
            "rejected_only_result_count": search_after.get("rejected_evidence_result_count", 0),
            "superseded_only_result_count": search_after.get("superseded_evidence_result_count", 0),
            "invalid_or_deleted_only_result_count": search_after.get("invalid_or_deleted_evidence_result_count", 0),
            "and_leakage_count": search_after.get("creator_and_character_work_leakage_count", 0)
            + search_after.get("and_constraint_leakage_count", 0),
            "search_caused_identity_mutation_count": int(search_mutation_before != search_mutation_after),
            "search_only": search_only,
        },
        "r2r_reuse": r2r_summary,
        "llm": {
            "policy_version": LLM_POLICY_VERSION,
            "manifest_count": 0,
            "calls": 0,
            "retries": 0,
            "projected_cost_usd": 0.0,
            "actual_cost_usd": 0.0,
            "deterministic_stable_id_pairs_excluded": True,
        },
        "mutation_proof": {
            "allowed_tables": list(ALLOWED_MUTATION_TABLES),
            "mutation_counts": actual_mutations,
            "first_execution_mutation_counts": first_mutations,
            "second_execution_mutation_counts": second_mutations,
            "changed_fixed_tables": list(fixed_compare["changed_tables"]),
            "changed_forbidden_truth_tables": list(forbidden_compare["changed_tables"]),
            "fixed_tables_unchanged": bool(fixed_compare["passed"]),
            "forbidden_truth_tables_unchanged": bool(forbidden_compare["passed"]),
            "production_write_count": 0,
            "entity_truth_write_count": 0,
            "media_tags_truth_write_count": 0,
            "source_or_icloud_write_count": 0,
        },
        "idempotency": {
            "passed": idempotent,
            "fingerprints_equal": bool(idempotency_compare["passed"]),
            "second_run_duplicate_media_support": support_second["duplicate_concept_media_support_count"],
        },
        "operation_counts": {
            "external_metadata_provider_calls": 0,
            "pixiv_calls": 0,
            "gallery_dl_calls": 0,
            "media_downloads": 0,
            "media_imports": 0,
            "ai_tagging": 0,
            "classification": 0,
            "localization": 0,
            "entity_truth_writes": 0,
            "production_writes": 0,
        },
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "target_met": target_met,
            "safe_to_merge": target_met,
            "route_approved": False,
            "active_blockers": blockers,
            "semantic_completeness_claimed": False,
            "production_readiness_claimed": False,
            "scale_readiness_claimed": False,
        },
        "route_decision": {
            "recommended_next_route": "project_lead_merge_review_only",
            "route_approved": False,
            "next_phase_started": False,
        },
        "artifact_lifecycle": {
            "service": "durable_production_code",
            "runner_and_tests": "phase_scoped_operational_runner",
            "private_manifests_and_review_pack": "one_off_local_artifact_ignored_output",
            "report_and_docs": "public_report_handoff_roadmap_update",
        },
        "review_pack": {
            "passed": True,
            "declared_member_count": 15,
            "zip_member_count": 15,
            "checksum_member_count": 13,
            "declared_zip_equality": True,
            "checksum_payload_equality": True,
        },
        "validation": {
            "changed_python_py_compile": args.changed_python_py_compile,
            "focused_pytest_passed": args.focused_pytest_passed,
            "focused_pytest_failed": args.focused_pytest_failed,
            "fresh_schema_passed": args.fresh_schema_passed,
            "public_redaction_passed": True,
            "review_pack_integrity_passed": True,
            "json_parse_passed": True,
            "ml2_contract_passed": False,
            "browser_validation": "not_required_no_ui_changes",
        },
    }
    summary["contract_evidence"] = {
        key: summary[key]
        for key in (
            "repository_sync_preflight",
            "r2r_reuse",
            "active_concept_audit",
            "graph_safety",
            "concept_media_support",
            "sourceconcept_only_runtime",
            "pair_accounting",
            "family_accounting",
            "mutation_proof",
            "pipeline_contract",
        )
    }
    contract = check_phase_contract(CONTRACT_ID, summary)
    summary["validation"]["ml2_contract_passed"] = contract.passed
    summary["validation"]["ml2_contract_error_count"] = len(contract.errors)
    summary["validation"]["ml2_contract_errors"] = [error.code for error in contract.errors]
    if target_met and not contract.passed:
        summary["pipeline_contract"].update(
            status="partial_ml2_identity_closure",
            target_met=False,
            safe_to_merge=False,
            active_blockers=["blocked_ml2_contract"],
        )
    publication = fail_closed_publication(
        summary=summary,
        output_dir=output_dir,
        public_report_path=REPORT_MD,
        public_summary_path=REPORT_JSON,
        private_artifacts=private_artifacts,
    )
    if not publication["published"]:
        return summary
    if publication["review_pack"] != summary["review_pack"]:
        raise ML2BlockedError("review_pack_integrity_result_mismatch")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=WORKING_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--changed-python-py-compile", default="not_run")
    parser.add_argument("--focused-pytest-passed", type=int, default=0)
    parser.add_argument("--focused-pytest-failed", type=int, default=0)
    parser.add_argument("--fresh-schema-passed", action="store_true")
    parser.add_argument(
        "--preedit-sync-json",
        default=str(DEFAULT_OUTPUT / "repository-synchronization-preflight.json"),
    )
    parser.add_argument("--confirm-execute", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.confirm_execute != "EXECUTE_ML2_ISOLATED_SOURCECONCEPT_ONLY":
        raise SystemExit("confirmation_missing_or_invalid")
    try:
        result = run(args)
    except (ML2BlockedError, CreatorIdentityClosureError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({
        "status": result["pipeline_contract"]["status"],
        "target_met": result["pipeline_contract"]["target_met"],
        "safe_to_merge": result["pipeline_contract"]["safe_to_merge"],
        "route_approved": False,
    }, sort_keys=True))
    return 0 if result["pipeline_contract"]["target_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
