#!/usr/bin/env python3
"""SCV2-SV1B read-only candidate inventory and provider-gate preflight.

This phase-scoped operational runner deliberately stops before authentication
or acquisition.  It re-derives the finite Pixiv page/work manifests from the
accepted 12,000-media membership and emits only private, local evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from sqlalchemy import MetaData, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services.pixiv_metadata_ingestion_service import (  # noqa: E402
    DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
    MIN_REQUEST_SPACING_SECONDS,
    PixivMetadataState,
    is_trusted_complete_pixiv_metadata_record,
    manifest_scoped_outcome_key,
)
from app.services.pixiv_filename_prior_service import (  # noqa: E402
    PARSER_VERSION,
    distinct_work_pages as durable_distinct_work_pages,
    parse_approved_fields,
)
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as gallery_adapter  # noqa: E402
from scripts.run_pixiv_metadata_ingestion import validate_gallery_dl_profile  # noqa: E402
from scripts import run_pixiv_metadata_ingestion as ingestion_runner  # noqa: E402
from scripts.run_phase45_scv2_sv1_controlled_scale_promotion_readiness import (  # noqa: E402
    CORE_SOURCE_TABLES,
    Paths,
    PROTECTED_TABLES,
    classify_pixiv_denominator,
    _insert_batches,
    _strip_row,
    copy_media_tag_baseline,
    create_clean_database,
    database_fingerprint,
    database_exists,
    engine_for,
    export_stable_evidence,
    import_stable_evidence,
    import_reusable_f7a_inputs,
    reconcile_stable_evidence_packages,
    sha256_file,
)

PHASE = "SCV2-SV1B"
BRANCH = "codex/scv2-sv1b-pixiv-metadata-localization-source-graph-closure"
ACCEPTED_MERGE = "46861489fa0b3b05ae917a99a3932897efd70365"
ACCEPTED_EVIDENCE_HEAD = "af073ca0ad2a9df9418cf072dc381d7b2c10216a"
ACCEPTED_MANIFEST_FINGERPRINT = "5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f"
ACCEPTED_SCALE_DB = "blombooru_scv2_sv1_controlled_scale_test_20260718"
ACCEPTED_ML1_DB = "blombooru_scv2_ml1_acquisition_test_20260712"
ACCEPTED_DATABASES = (
    "blombooru_scv2_r2r_dryrun_test_20260710",
    "blombooru_scv2_ml1_acquisition_test_20260712",
    "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715",
    ACCEPTED_SCALE_DB,
    "blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1",
    "blombooru_scv2_sv1_rebuild_verification_test_20260718",
)
ACCEPTED_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness"
ACCEPTED_STORAGE = ROOT / ".local_test_storage/phase-4.5-scv2-sv1-controlled-scale"
DEFAULT_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-sv1b-pixiv-metadata-localization-source-graph-closure-20260719"
DEFAULT_PRIMARY_DB = "blombooru_scv2_sv1b_metadata_graph_closure_test_20260719"
DEFAULT_REPLAY_DB = "blombooru_scv2_sv1b_replay_verification_test_20260719"
EXPECTED_MEDIA_COUNT = 12_000
ACCEPTED_R2R_DB = "blombooru_scv2_r2r_dryrun_test_20260710"
ACCEPTED_R2R_SNAPSHOT_FINGERPRINT = "25090761abff2c2ae9f7ef8d9ea04904c47a9f3a43ce03ab660a39502ae792fc"
ML2_PRIVATE = ROOT / ".local_manifests/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-reviewfix-20260715"
R2R_PRIVATE = ROOT / ".local_manifests/phase-4.5-scv2-r2r-autonomous-recall-search-closure"

NONDERIVED_SOURCE_TABLES = frozenset({
    "source_metadata_records",
    "source_tag_observations",
    "source_name_observations",
    "source_metadata_evidence",
    "source_searchable_name_assertions",
    "source_tag_registry",
    "source_name_registry",
})

COMPLETE_STATUSES = frozenset({"metadata_complete", "observed", "active", "accepted"})
TERMINAL_STATUSES = frozenset({"terminal_remote_unavailable"})
DEFERRED_STATUSES = frozenset({"deferred_nonblocking_source_page_mismatch"})


class SV1BPreflightError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_payload(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
    os.replace(temporary, path)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def _strict_test_database(database: str) -> bool:
    value = str(database or "").strip().casefold()
    return bool(value and "test" in value and value not in {"blombooru", "postgres", "template0", "template1"})


def canonical_work_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw.isdigit():
        raise SV1BPreflightError("pixiv_work_id_not_decimal")
    canonical = str(int(raw))
    if canonical == "0" or len(canonical) > 12:
        raise SV1BPreflightError("pixiv_work_id_out_of_range")
    return canonical


def validate_output_root(output: Path) -> Path:
    resolved = output.resolve()
    private_root = (ROOT / ".local_manifests").resolve()
    if private_root not in resolved.parents:
        raise SV1BPreflightError("private_output_root_escape")
    if resolved.exists():
        raise SV1BPreflightError("private_output_root_already_exists_ownership_unproven")
    return resolved


def validate_owned_output_root(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    resolved = output.resolve()
    private_root = (ROOT / ".local_manifests").resolve()
    if private_root not in resolved.parents or not resolved.is_dir():
        raise SV1BPreflightError("owned_private_output_root_invalid")
    identity_path = resolved / "run-identity.json"
    ownership_path = resolved / "database-ownership-and-baseline-proof.json"
    if not identity_path.is_file() or not ownership_path.is_file():
        raise SV1BPreflightError("owned_private_output_proof_missing")
    identity = read_json(identity_path)
    ownership = read_json(ownership_path)
    expected_key = sha256_payload({
        "phase": PHASE,
        "branch": BRANCH,
        "manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
        "primary_database": primary_database,
        "replay_database": replay_database,
    })
    if not (
        identity.get("phase") == PHASE
        and identity.get("branch") == BRANCH
        and identity.get("accepted_manifest_fingerprint") == ACCEPTED_MANIFEST_FINGERPRINT
        and ownership.get("ownership_key") == expected_key
        and ownership.get("primary_database_identity") == primary_database
        and ownership.get("replay_database_identity") == replay_database
        and ownership.get("passed") is True
        and database_exists(primary_database)
        and database_exists(replay_database)
    ):
        raise SV1BPreflightError("owned_private_output_or_database_identity_mismatch")
    return {"output": str(resolved), "ownership_key": expected_key, "resume_ownership_passed": True}


def validate_writable_databases(primary_database: str, replay_database: str) -> dict[str, Any]:
    values = (str(primary_database or "").strip(), str(replay_database or "").strip())
    if not all(_strict_test_database(value) for value in values):
        raise SV1BPreflightError("writable_database_identity_not_strict_test")
    if len(set(values)) != len(values):
        raise SV1BPreflightError("writable_database_identities_not_distinct")
    overlap = sorted(set(values).intersection(ACCEPTED_DATABASES))
    if overlap:
        raise SV1BPreflightError(f"writable_database_overlaps_accepted:{overlap}")
    existing = [value for value in values if database_exists(value)]
    if existing:
        raise SV1BPreflightError(f"writable_database_already_exists_ownership_unproven:{existing}")
    return {
        "primary_database_identity": values[0],
        "replay_database_identity": values[1],
        "strict_test_identities": True,
        "pairwise_distinct": True,
        "accepted_database_overlap_count": 0,
        "preexisting_database_count": 0,
    }


def validate_repository_and_inputs(output: Path) -> dict[str, Any]:
    branch = git("branch", "--show-current")
    if branch != BRANCH:
        raise SV1BPreflightError(f"wrong_branch:{branch}")
    if os.getenv("VIOLET_ENV") != "test":
        raise SV1BPreflightError("violet_env_not_test")
    if not _strict_test_database(ACCEPTED_SCALE_DB):
        raise SV1BPreflightError("accepted_scale_database_identity_invalid")
    summary = read_json(ACCEPTED_OUTPUT / "inventory-and-manifest-summary.json")
    manifest_fingerprint = str(summary["scale_manifest"]["manifest_fingerprint"])
    if manifest_fingerprint != ACCEPTED_MANIFEST_FINGERPRINT:
        raise SV1BPreflightError("accepted_manifest_fingerprint_mismatch")
    if int(summary["scale_manifest"]["selected_eligible_media_count"]) != EXPECTED_MEDIA_COUNT:
        raise SV1BPreflightError("accepted_manifest_media_count_mismatch")
    validate_output_root(output)
    return {
        "repository_root_fingerprint": sha256_payload(str(ROOT.resolve()).casefold()),
        "branch": branch,
        "head": git("rev-parse", "HEAD"),
        "accepted_merge_is_ancestor": subprocess.run(
            ["git", "merge-base", "--is-ancestor", ACCEPTED_MERGE, "HEAD"], cwd=ROOT, check=False
        ).returncode == 0,
        "accepted_evidence_head_is_ancestor": subprocess.run(
            ["git", "merge-base", "--is-ancestor", ACCEPTED_EVIDENCE_HEAD, "HEAD"], cwd=ROOT, check=False
        ).returncode == 0,
        "accepted_manifest_fingerprint": manifest_fingerprint,
        "accepted_scale_database": ACCEPTED_SCALE_DB,
        "output_root_validated_before_write": True,
    }


def outcome_for_pair(records: list[Mapping[str, Any]], work_id: str, page_index: int) -> str:
    exact = [
        row for row in records
        if str(row.get("source_work_id") or "") == work_id
        and row.get("source_page_index") is not None
        and int(row["source_page_index"]) == page_index
    ]
    statuses = {str(row.get("status") or "") for row in exact}
    if statuses & COMPLETE_STATUSES:
        return "accepted_metadata_complete"
    if statuses & TERMINAL_STATUSES:
        return "accepted_terminal_remote_unavailable"
    if statuses & DEFERRED_STATUSES:
        return "accepted_deferred_nonblocking_source_page_mismatch"
    same_work = {str(row.get("status") or "") for row in records if str(row.get("source_work_id") or "") == work_id}
    if same_work & DEFERRED_STATUSES:
        return "accepted_deferred_nonblocking_source_page_mismatch"
    return "unacquired"


def build_candidate_manifests() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    manifest = read_jsonl(ACCEPTED_OUTPUT / "scale-selection-manifest.jsonl")
    manifest_keys = [str(row["file_hash"]) for row in manifest]
    engine = engine_for(ACCEPTED_SCALE_DB)
    try:
        with engine.connect() as connection:
            media_rows = list(connection.execute(text(
                "SELECT id,hash,filename,path FROM blombooru_media ORDER BY hash"
            )).mappings())
            metadata_rows = list(connection.execute(text(
                "SELECT media_id,source_work_id,source_page_index,status "
                "FROM blombooru_source_metadata_records WHERE provider='pixiv'"
            )).mappings())
    finally:
        engine.dispose()

    media_by_hash = {str(row["hash"]): row for row in media_rows if row["hash"] is not None}
    if len(manifest_keys) != len(set(manifest_keys)) or set(manifest_keys) != set(media_by_hash):
        raise SV1BPreflightError("accepted_manifest_database_membership_mismatch")
    records_by_media: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in metadata_rows:
        if row["media_id"] is not None:
            records_by_media[int(row["media_id"])].append(row)

    pages: list[dict[str, Any]] = []
    non_candidate_count = 0
    classification_counts: Counter[str] = Counter()
    for media_hash in manifest_keys:
        media = media_by_hash[media_hash]
        classification, filename_ids, stored_ids = classify_pixiv_denominator(media["filename"], media["path"])
        classification_counts[classification] += 1
        if classification == "non_candidate":
            non_candidate_count += 1
            continue
        pairs = sorted({(canonical_work_id(row["work_id"]), int(row["page_index"])) for row in [*filename_ids, *stored_ids]})
        for work_id, page_index in pairs:
            pages.append({
                "provider": "pixiv",
                "stable_work_id": work_id,
                "requested_page_index": page_index,
                "media_stable_key": media_hash,
                "media_safe_label": sha256_payload({"media": media_hash}),
                "identity_classification": classification,
                "acquisition_state": outcome_for_pair(records_by_media[int(media["id"])], work_id, page_index),
                "checkpoint_key": manifest_scoped_outcome_key(
                    ACCEPTED_MANIFEST_FINGERPRINT, "pixiv", work_id, page_index
                ),
                "retry_count": 0,
                "last_request_time": None,
                "final_outcome": None,
            })

    works_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pages:
        works_by_id[row["stable_work_id"]].append(row)
    works = []
    for work_id, rows in sorted(works_by_id.items(), key=lambda item: int(item[0])):
        states = Counter(str(row["acquisition_state"]) for row in rows)
        work_state = next(iter(states)) if len(states) == 1 else "mixed_page_outcomes"
        works.append({
            "provider": "pixiv",
            "stable_work_id": work_id,
            "requested_local_page_indexes": sorted({int(row["requested_page_index"]) for row in rows}),
            "media_stable_keys": sorted({str(row["media_stable_key"]) for row in rows}),
            "acquisition_state": work_state,
            "checkpoint_key": sha256_payload({
                "manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
                "provider": "pixiv",
                "stable_work_id": work_id,
                "page_checkpoint_keys": sorted(str(row["checkpoint_key"]) for row in rows),
            }),
            "retry_count": 0,
            "last_request_time": None,
            "final_outcome": None,
        })

    candidate_media = {str(row["media_stable_key"]) for row in pages}
    outcome_counts = Counter(str(row["acquisition_state"]) for row in pages)
    exact_closed_media = {
        str(row["media_stable_key"]) for row in pages if row["acquisition_state"] != "unacquired"
    }
    media_id_by_hash = {str(row["hash"]): int(row["id"]) for row in media_rows if row["hash"] is not None}
    any_accepted_metadata_media = {
        media_hash for media_hash in candidate_media if records_by_media.get(media_id_by_hash[media_hash])
    }
    distinct_work_pages = {
        (str(row["stable_work_id"]), int(row["requested_page_index"])) for row in pages
    }
    summary = {
        "manifest_media_count": len(manifest_keys),
        "canonical_candidate_media_count": len(candidate_media),
        "explicit_non_candidate_media_count": non_candidate_count,
        "distinct_requested_page_count": len(pages),
        "page_media_manifest_row_count": len(pages),
        "distinct_work_page_count": len(distinct_work_pages),
        "distinct_work_count": len(works),
        "distinct_work_manifest_row_count": len(works),
        "classification_counts": dict(sorted(classification_counts.items())),
        "accepted_page_outcome_counts": dict(sorted(outcome_counts.items())),
        "candidate_media_with_any_accepted_metadata_record": len(any_accepted_metadata_media),
        "candidate_media_with_exact_page_closure": len(exact_closed_media),
        "candidate_media_with_accepted_but_non_exact_page_evidence": len(any_accepted_metadata_media - exact_closed_media),
        "candidate_media_requiring_acquisition": len(candidate_media - exact_closed_media),
        "candidate_accounting_passed": len(candidate_media) + non_candidate_count == len(manifest_keys),
        "page_manifest_fingerprint": sha256_payload(pages),
        "work_manifest_fingerprint": sha256_payload(works),
    }
    if not summary["candidate_accounting_passed"]:
        raise SV1BPreflightError("candidate_accounting_failed")
    return pages, works, summary


def audit_runtime_parser_denominator_rows(media_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Prove the durable queue parser exactly matches the accepted SV1 denominator."""

    accepted_candidate_media: set[str] = set()
    durable_candidate_media: set[str] = set()
    accepted_membership: set[tuple[str, str, int]] = set()
    durable_membership: set[tuple[str, str, int]] = set()
    row_count = 0
    for row in media_rows:
        row_count += 1
        stable_media_key = str(row.get("hash") or row.get("id") or "").strip()
        if not stable_media_key:
            raise SV1BPreflightError("runtime_parser_audit_media_stable_key_missing")
        _category, filename_ids, stored_ids = classify_pixiv_denominator(row.get("filename"), row.get("path"))
        accepted_pairs = {
            (canonical_work_id(item["work_id"]), int(item["page_index"]))
            for item in [*filename_ids, *stored_ids]
        }
        durable_pairs = set(durable_distinct_work_pages(parse_approved_fields((
            ("filename", row.get("filename")),
            ("stored_path", row.get("path")),
        ))))
        if accepted_pairs:
            accepted_candidate_media.add(stable_media_key)
        if durable_pairs:
            durable_candidate_media.add(stable_media_key)
        accepted_membership.update((stable_media_key, work_id, page) for work_id, page in accepted_pairs)
        durable_membership.update((stable_media_key, work_id, page) for work_id, page in durable_pairs)

    missing = accepted_membership - durable_membership
    extra = durable_membership - accepted_membership
    candidate_missing = accepted_candidate_media - durable_candidate_media
    candidate_extra = durable_candidate_media - accepted_candidate_media
    result = {
        "runtime_parser_version": PARSER_VERSION,
        "media_row_count": row_count,
        "accepted_candidate_media_count": len(accepted_candidate_media),
        "durable_candidate_media_count": len(durable_candidate_media),
        "accepted_media_work_page_membership_count": len(accepted_membership),
        "durable_media_work_page_membership_count": len(durable_membership),
        "missing_candidate_media_count": len(candidate_missing),
        "unexpected_candidate_media_count": len(candidate_extra),
        "missing_media_work_page_count": len(missing),
        "unexpected_media_work_page_count": len(extra),
        "accepted_membership_fingerprint": sha256_payload(sorted(accepted_membership)),
        "durable_membership_fingerprint": sha256_payload(sorted(durable_membership)),
        "passed": not (candidate_missing or candidate_extra or missing or extra),
    }
    if result["passed"] is not True:
        raise SV1BPreflightError(
            "runtime_parser_denominator_mismatch:"
            f"candidate_missing={len(candidate_missing)}:candidate_extra={len(candidate_extra)}:"
            f"membership_missing={len(missing)}:membership_extra={len(extra)}"
        )
    return result


def audit_runtime_parser_denominator() -> dict[str, Any]:
    engine = engine_for(ACCEPTED_SCALE_DB)
    try:
        with engine.connect() as connection:
            rows = list(connection.execute(text(
                "SELECT id,hash,filename,path FROM blombooru_media ORDER BY hash"
            )).mappings())
    finally:
        engine.dispose()
    return audit_runtime_parser_denominator_rows(rows)


def immutable_input_fingerprints() -> dict[str, Any]:
    tables = tuple(dict.fromkeys((*PROTECTED_TABLES, "blombooru_tags", *CORE_SOURCE_TABLES)))
    databases = {database: database_fingerprint(database, tables) for database in ACCEPTED_DATABASES}
    original = ACCEPTED_STORAGE / "media/original"
    storage_rows = sorted((path.name, int(path.stat().st_size)) for path in original.iterdir() if path.is_file())
    return {
        "database_fingerprints": {database: value["fingerprint"] for database, value in databases.items()},
        "protected_tables": list(tables),
        "blombooru_tags_included": True,
        "storage_object_count": len(storage_rows),
        "storage_membership_fingerprint": sha256_payload(storage_rows),
        "accepted_manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
    }


def media_tag_logical_fingerprint(database: str) -> dict[str, Any]:
    engine = engine_for(database)
    ignored_media = {"id", "created_at", "updated_at", "parent_id"}
    ignored_tags = {"id", "created_at", "updated_at", "post_count"}
    try:
        with engine.connect() as connection:
            media = sorted(
                canonical_json({key: value for key, value in row.items() if key not in ignored_media})
                for row in connection.execute(text("SELECT * FROM blombooru_media")).mappings()
            )
            tags = sorted(
                canonical_json({key: value for key, value in row.items() if key not in ignored_tags})
                for row in connection.execute(text("SELECT * FROM blombooru_tags")).mappings()
            )
            links = sorted(canonical_json(dict(row)) for row in connection.execute(text("""
                SELECT m.hash AS media_hash,t.name AS tag_name,mt.source,mt.confidence,
                       mt.is_locked,mt.is_suggestion
                FROM blombooru_media_tags mt
                JOIN blombooru_media m ON m.id=mt.media_id
                JOIN blombooru_tags t ON t.id=mt.tag_id
            """)).mappings())
    finally:
        engine.dispose()
    groups = {
        "media": {"count": len(media), "fingerprint": sha256_payload(media)},
        "tags": {"count": len(tags), "fingerprint": sha256_payload(tags)},
        "media_tags": {"count": len(links), "fingerprint": sha256_payload(links)},
    }
    return {"database": database, "groups": groups, "fingerprint": sha256_payload(groups)}


def prepare_isolated_databases(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    isolation = validate_writable_databases(primary_database, replay_database)
    ownership_key = sha256_payload({
        "phase": PHASE,
        "branch": BRANCH,
        "manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
        "primary_database": primary_database,
        "replay_database": replay_database,
    })
    write_json(output / "database-creation-intent.json", {
        **isolation,
        "ownership_key": ownership_key,
        "drop_truncate_reset_authorized": False,
        "source_database": ACCEPTED_SCALE_DB,
        "source_database_read_only": True,
    })
    primary_clean = create_clean_database(primary_database)
    write_json(output / "primary-database-created.json", {
        "ownership_key": ownership_key,
        "database": primary_database,
        "clean_schema": primary_clean["clean_schema"],
    })
    replay_clean = create_clean_database(replay_database)
    write_json(output / "replay-database-created.json", {
        "ownership_key": ownership_key,
        "database": replay_database,
        "clean_schema": replay_clean["clean_schema"],
    })
    primary_copy = copy_media_tag_baseline(ACCEPTED_SCALE_DB, primary_database)
    replay_copy = copy_media_tag_baseline(ACCEPTED_SCALE_DB, replay_database)
    baseline_tables = ("blombooru_media", "blombooru_tags", "blombooru_media_tags")
    accepted_fingerprint = media_tag_logical_fingerprint(ACCEPTED_SCALE_DB)
    primary_fingerprint = media_tag_logical_fingerprint(primary_database)
    replay_fingerprint = media_tag_logical_fingerprint(replay_database)
    passed = bool(
        int(primary_copy["media_count"]) == EXPECTED_MEDIA_COUNT
        and int(replay_copy["media_count"]) == EXPECTED_MEDIA_COUNT
        and primary_fingerprint["fingerprint"] == replay_fingerprint["fingerprint"]
        and primary_fingerprint["fingerprint"] == accepted_fingerprint["fingerprint"]
    )
    result = {
        **isolation,
        "ownership_key": ownership_key,
        "primary_clean_schema_created": primary_clean["clean_schema"],
        "replay_clean_schema_created": replay_clean["clean_schema"],
        "primary_baseline_copy": primary_copy,
        "replay_baseline_copy": replay_copy,
        "baseline_tables": list(baseline_tables),
        "accepted_baseline_fingerprint": accepted_fingerprint["fingerprint"],
        "primary_baseline_fingerprint": primary_fingerprint["fingerprint"],
        "replay_baseline_fingerprint": replay_fingerprint["fingerprint"],
        "exact_baseline_fingerprint_equality": passed,
        "numeric_row_id_equality_claimed": False,
        "accepted_database_write_count": 0,
        "production_selected": False,
        "passed": passed,
    }
    write_json(output / "database-ownership-and-baseline-proof.json", result)
    if not passed:
        raise SV1BPreflightError("isolated_database_baseline_copy_mismatch")
    return result


def _accepted_nonderived_package() -> tuple[dict[str, Any], dict[str, Any]]:
    package_path = ACCEPTED_OUTPUT / "stable-key-evidence-package.json"
    manifest_path = ACCEPTED_OUTPUT / "stable-key-evidence-package-manifest.json"
    package = read_json(package_path)
    package_manifest = read_json(manifest_path)
    if sha256_file(package_path) != str(package_manifest.get("package_sha256") or ""):
        raise SV1BPreflightError("accepted_stable_evidence_package_fingerprint_mismatch")
    reusable_names = {
        "source_metadata_records", "source_tag_observations", "source_name_observations",
        "source_metadata_evidence", "source_searchable_name_assertions",
        "source_tag_registry", "source_name_registry",
    }
    filtered = {
        **package,
        "package_version": "sv1b_accepted_nonderived_source_evidence_v1",
        "tables": {
            name: list(rows) if name in reusable_names else []
            for name, rows in package["tables"].items()
        },
    }
    evidence = {
        "accepted_package_sha256": package_manifest["package_sha256"],
        "filtered_package_fingerprint": sha256_payload(filtered),
        "table_counts": {name: len(rows) for name, rows in filtered["tables"].items()},
        "derived_input_row_count": sum(
            len(rows) for name, rows in filtered["tables"].items() if name not in reusable_names
        ),
    }
    return filtered, evidence


def _database_media_keys(database: str) -> set[str]:
    engine = engine_for(database)
    try:
        with engine.connect() as connection:
            return {
                str(value) for value in connection.execute(
                    text("SELECT hash FROM blombooru_media WHERE hash IS NOT NULL")
                ).scalars()
            }
    finally:
        engine.dispose()


def import_accepted_nonderived_evidence(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    ownership = validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    package, package_evidence = _accepted_nonderived_package()
    raw_tables = tuple(package["tables"])
    before = {
        database: database_fingerprint(database, raw_tables)
        for database in (primary_database, replay_database)
    }
    if any(
        int(table["count"]) != 0
        for database in before.values()
        for table in database["tables"].values()
    ):
        raise SV1BPreflightError("nonderived_import_target_not_pristine")
    write_json(output / "accepted-evidence-import-intent.json", {
        **ownership,
        **package_evidence,
        "target_databases": [primary_database, replay_database],
        "atomic_per_database": True,
        "accepted_inputs_read_only": True,
    })
    imports: dict[str, Any] = {}
    reconciliations: dict[str, Any] = {}
    for label, database in (("primary", primary_database), ("replay", replay_database)):
        engine = engine_for(database)
        try:
            with engine.begin() as connection:
                imports[label] = import_stable_evidence(connection, package)
        finally:
            engine.dispose()
        write_json(output / f"accepted-evidence-{label}-import-commit.json", {
            "database": database,
            "ownership_key": ownership["ownership_key"],
            "import": imports[label],
        })
        export_paths = Paths(output / f"accepted-evidence-{label}-reconciliation")
        export_stable_evidence(export_paths, source_database=database)
        target_package = read_json(export_paths.package)
        reconciliation = reconcile_stable_evidence_packages(
            package, target_package, _database_media_keys(database)
        )
        reconciliations[label] = reconciliation
        write_json(output / f"accepted-evidence-{label}-reconciliation.json", reconciliation)
        if not (
            reconciliation["exact_stable_key_membership_passed"] is True
            and int(reconciliation["blocking_failed"]) == 0
            and int(reconciliation["extra_materialized_count"]) == 0
        ):
            raise SV1BPreflightError(f"accepted_evidence_{label}_reconciliation_failed")
    result = {
        **ownership,
        **package_evidence,
        "primary_import": imports["primary"],
        "replay_import": imports["replay"],
        "primary_reconciliation_passed": True,
        "replay_reconciliation_passed": True,
        "primary_replay_inserted_counts_equal": (
            imports["primary"]["inserted_counts"] == imports["replay"]["inserted_counts"]
        ),
        "derived_input_row_count": package_evidence["derived_input_row_count"],
        "passed": True,
    }
    if result["primary_replay_inserted_counts_equal"] is not True:
        raise SV1BPreflightError("primary_replay_accepted_evidence_count_mismatch")
    write_json(output / "accepted-nonderived-evidence-proof.json", result)
    return result


def _translation_logical_state(database: str) -> dict[str, Any]:
    engine = engine_for(database)
    try:
        with engine.connect() as connection:
            rows = sorted(canonical_json(dict(row)) for row in connection.execute(text("""
                SELECT canonical_name,language,display_name,aliases_json,category,source,
                       status,confidence,needs_review,provider
                FROM blombooru_tag_translations
                ORDER BY canonical_name,language
            """)).mappings())
    finally:
        engine.dispose()
    return {"count": len(rows), "fingerprint": sha256_payload(rows)}


def _copy_accepted_translations(target_database: str) -> dict[str, Any]:
    source = engine_for(ACCEPTED_ML1_DB)
    target = engine_for(target_database)
    target_meta = MetaData()
    target_meta.reflect(bind=target, only=["blombooru_tag_translations"])
    target_table = target_meta.tables.get("blombooru_tag_translations")
    if target_table is None:
        target_table = target_meta.tables["public.blombooru_tag_translations"]
    try:
        with source.connect() as source_connection:
            source_rows = [
                _strip_row(dict(row), drop=("tag_id",))
                for row in source_connection.execute(text("""
                    SELECT * FROM blombooru_tag_translations
                    WHERE language='zh-CN' AND status='translated' AND display_name<>''
                    ORDER BY canonical_name,language
                """)).mappings()
            ]
        with target.begin() as target_connection:
            existing = int(target_connection.execute(text(
                "SELECT COUNT(*) FROM blombooru_tag_translations"
            )).scalar() or 0)
            if existing:
                raise SV1BPreflightError("localization_target_not_pristine")
            tag_ids = {
                str(row.name): int(row.id)
                for row in target_connection.execute(text("SELECT id,name FROM blombooru_tags"))
            }
            values = []
            for row in source_rows:
                item = dict(row)
                item["tag_id"] = tag_ids.get(str(item["canonical_name"]))
                values.append(item)
            inserted = _insert_batches(target_connection, target_table, values, batch_size=500)
    finally:
        source.dispose()
        target.dispose()
    return {
        "source_translation_count": len(source_rows),
        "inserted_translation_count": inserted,
        "numeric_tag_id_reused": False,
        "stable_canonical_name_mapping": True,
    }


def _vocabulary_state(database: str) -> tuple[dict[str, Any], dict[str, list[str]]]:
    engine = engine_for(database)
    try:
        with engine.connect() as connection:
            ai_tags = sorted({
                str(value) for value in connection.execute(text("""
                    SELECT DISTINCT t.name
                    FROM blombooru_media_tags mt
                    JOIN blombooru_tags t ON t.id=mt.tag_id
                    WHERE mt.source='ai_wd'
                """)).scalars() if value
            })
            translated = {
                str(value) for value in connection.execute(text("""
                    SELECT canonical_name FROM blombooru_tag_translations
                    WHERE language='zh-CN' AND status='translated' AND display_name<>''
                """)).scalars() if value
            }
            source_tags = sorted({
                str(value) for value in connection.execute(text("""
                    SELECT raw_tag FROM blombooru_source_tag_observations
                    WHERE status IN ('observed','active','accepted') AND raw_tag IS NOT NULL
                """)).scalars() if value
            })
            creator_names = sorted({
                str(value) for value in connection.execute(text("""
                    SELECT raw_name FROM blombooru_source_name_observations
                    WHERE status IN ('observed','active','accepted')
                      AND name_role IN (
                          'creator','artist','creator_name','creator_account','artist_name','artist_account'
                      )
                      AND raw_name IS NOT NULL
                """)).scalars() if value
            })
            work_titles = sorted({
                str(value) for value in connection.execute(text("""
                    SELECT title FROM blombooru_source_metadata_records
                    WHERE status IN ('observed','active','accepted','metadata_complete')
                      AND title IS NOT NULL AND title<>''
                """)).scalars() if value
            })
    finally:
        engine.dispose()
    missing_ai_tags = sorted(set(ai_tags) - translated)
    private = {
        "eligible_ai_tags": ai_tags,
        "blocking_missing_ai_tags": missing_ai_tags,
        "provider_source_tags": source_tags,
        "creator_names_accounts": creator_names,
        "work_titles": work_titles,
    }
    public = {
        "ai_media_tag_vocabulary_count": len(ai_tags),
        "accepted_ai_translation_count": len(set(ai_tags).intersection(translated)),
        "explicit_nontranslatable_exclusion_count": 0,
        "blocking_missing_ai_translation_count": len(missing_ai_tags),
        "provider_source_tag_vocabulary_count": len(source_tags),
        "creator_name_account_vocabulary_count": len(creator_names),
        "work_title_vocabulary_count": len(work_titles),
        "ai_vocabulary_fingerprint": sha256_payload(ai_tags),
        "missing_ai_vocabulary_fingerprint": sha256_payload(missing_ai_tags),
        "provider_source_vocabulary_fingerprint": sha256_payload(source_tags),
        "creator_vocabulary_fingerprint": sha256_payload(creator_names),
        "work_title_vocabulary_fingerprint": sha256_payload(work_titles),
        "silent_missing_count": 0,
        "provider_tags_written_to_media_tags_count": 0,
        "creator_identity_translated_count": 0,
        "original_provider_text_preserved": True,
    }
    return public, private


def prepare_localization_baseline(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    if not (output / "accepted-nonderived-evidence-proof.json").is_file():
        raise SV1BPreflightError("accepted_nonderived_evidence_proof_missing")
    source_state = _translation_logical_state(ACCEPTED_ML1_DB)
    primary_copy = _copy_accepted_translations(primary_database)
    replay_copy = _copy_accepted_translations(replay_database)
    primary_state = _translation_logical_state(primary_database)
    replay_state = _translation_logical_state(replay_database)
    if not source_state == primary_state == replay_state:
        raise SV1BPreflightError("accepted_translation_logical_state_mismatch")
    primary_vocabulary, primary_private = _vocabulary_state(primary_database)
    replay_vocabulary, replay_private = _vocabulary_state(replay_database)
    if primary_vocabulary != replay_vocabulary or primary_private != replay_private:
        raise SV1BPreflightError("primary_replay_localization_vocabulary_mismatch")
    write_json(output / "localization-vocabulary-private.json", primary_private)
    result = {
        "accepted_translation_source_database": ACCEPTED_ML1_DB,
        "accepted_translation_state": source_state,
        "primary_copy": primary_copy,
        "replay_copy": replay_copy,
        "primary_replay_translation_fingerprint_equal": True,
        "vocabulary": primary_vocabulary,
        "external_llm_call_count": 0,
        "projected_and_actual_llm_cost_usd": 0.0,
        "fallback_provider_used": False,
        "image_upload_count": 0,
        "localization_complete": (
            int(primary_vocabulary["blocking_missing_ai_translation_count"]) == 0
        ),
    }
    write_json(output / "localization-baseline-proof.json", result)
    return result


def _prepare_graph_inputs(database: str) -> dict[str, Any]:
    source = engine_for(ACCEPTED_R2R_DB)
    target = engine_for(database)
    target_meta = MetaData()
    target_meta.reflect(bind=target, only=["blombooru_source_name_alias_candidates"])
    alias_table = target_meta.tables.get("blombooru_source_name_alias_candidates")
    if alias_table is None:
        alias_table = target_meta.tables["public.blombooru_source_name_alias_candidates"]
    try:
        with source.connect() as source_connection:
            alias_rows = [
                _strip_row(dict(row)) for row in source_connection.execute(text(
                    "SELECT * FROM blombooru_source_name_alias_candidates ORDER BY id"
                )).mappings()
            ]
        with target.begin() as target_connection:
            existing = int(target_connection.execute(text(
                "SELECT COUNT(*) FROM blombooru_source_name_alias_candidates"
            )).scalar() or 0)
            if existing:
                raise SV1BPreflightError("graph_alias_candidate_target_not_pristine")
            inserted = _insert_batches(target_connection, alias_table, alias_rows)
    finally:
        source.dispose()
        target.dispose()
    f7a = import_reusable_f7a_inputs(ACCEPTED_R2R_DB, database)
    return {
        "alias_candidate_source_count": len(alias_rows),
        "alias_candidate_inserted_count": inserted,
        "f7a_reusable_inputs": f7a,
        "derived_graph_row_import_count": 0,
    }


def _logical_signal_key(row: Mapping[str, Any], media_hash: Any, record_key: Any) -> tuple[str, ...]:
    logical_fields = (
        "origin_type", "provider", "raw_value", "normalized_key", "canonical_key",
        "role_hint", "work_context_key", "source_kind", "trust_tier",
    )
    payload = row.get("evidence_payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    payload_identity = {
        key: payload.get(key)
        for key in ("candidate_key", "observation_key", "assertion_key", "provider_record_key", "tag_name")
        if payload.get(key) is not None
    }
    return (
        str(media_hash or ""), str(record_key or ""),
        *(str(row.get(field) or "") for field in logical_fields),
        canonical_json(payload_identity),
    )


def _accepted_r2r_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    accepted = read_json(ML2_PRIVATE / "accepted-r2r-disposition-input-private.json")
    pairs_document = read_json(R2R_PRIVATE / "pair-manifest.json")
    accepted_pairs = list(accepted.get("pairs") or ())
    pair_rows = list(pairs_document.get("pairs") or ())
    fingerprint = sha256_payload(accepted_pairs)
    if fingerprint != ACCEPTED_R2R_SNAPSHOT_FINGERPRINT or accepted.get("snapshot_fingerprint") != fingerprint:
        raise SV1BPreflightError("accepted_r2r_snapshot_fingerprint_mismatch")
    accepted_ids = {str(row["pair_id"]) for row in accepted_pairs}
    manifest_by_id = {str(row["pair_id"]): row for row in pair_rows}
    if accepted_ids != set(manifest_by_id):
        raise SV1BPreflightError("accepted_r2r_pair_manifest_membership_mismatch")
    return accepted, manifest_by_id


def finalize_r2r_proposal_classifications(
    preliminary: dict[str, dict[str, Any]],
    proposals_by_target: Mapping[str, list[str]],
    accepted_by_id: Mapping[str, str],
) -> None:
    for _target_pair_id, old_pair_ids in proposals_by_target.items():
        dispositions = {accepted_by_id[pair_id] for pair_id in old_pair_ids}
        if len(dispositions) > 1:
            final_classification = "conflicting_remap"
        elif len(old_pair_ids) > 1:
            final_classification = "ambiguous_remap"
        else:
            final_classification = "comparable"
        for pair_id in old_pair_ids:
            preliminary[pair_id]["classification"] = final_classification


def audit_r2r_remap(database: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from app.services.source_concept_autonomous_closure_service import build_candidate_pair_manifest
    from app.services.source_concept_resolver_service import build_source_concept_signals, resolve_source_concepts

    accepted, pair_manifest = _accepted_r2r_snapshot()
    accepted_by_id = {str(row["pair_id"]): str(row["disposition"]) for row in accepted["pairs"]}
    engine = engine_for(database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        generated = build_source_concept_signals(session, run_id="sv1b-r2r-remap-audit")
        current_media = {
            int(row[0]): str(row[1]) for row in session.execute(text("SELECT id,hash FROM blombooru_media"))
        }
        current_records = {
            int(row[0]): str(row[1]) for row in session.execute(text(
                "SELECT id,provider_record_key FROM blombooru_source_metadata_records"
            ))
        }
        old_engine = engine_for(ACCEPTED_R2R_DB)
        try:
            with old_engine.connect() as old_connection:
                old_rows = list(old_connection.execute(text("""
                    SELECT s.*,m.hash AS media_hash,r.provider_record_key
                    FROM blombooru_source_concept_signals s
                    LEFT JOIN blombooru_media m ON m.id=s.media_id
                    LEFT JOIN blombooru_source_metadata_records r ON r.id=s.source_metadata_record_id
                """)).mappings())
        finally:
            old_engine.dispose()
        old_key_by_signal = {
            str(row["signal_key"]): _logical_signal_key(row, row["media_hash"], row["provider_record_key"])
            for row in old_rows
        }
        accepted_logical_keys = set(old_key_by_signal.values())
        generated_by_logical: dict[tuple[str, ...], list[Any]] = defaultdict(list)
        for signal in generated:
            logical = _logical_signal_key(
                signal.__dict__, current_media.get(signal.media_id),
                current_records.get(signal.source_metadata_record_id),
            )
            if logical in accepted_logical_keys:
                generated_by_logical[logical].append(signal)
        accepted_signals = [signal for values in generated_by_logical.values() for signal in values]
        deterministic = resolve_source_concepts(
            accepted_signals, run_id="sv1b-r2r-remap-deterministic"
        )
        candidates = build_candidate_pair_manifest(
            deterministic.edge_candidates, signals=accepted_signals, max_calls=10_000
        )
    finally:
        session.close()
        engine.dispose()

    candidate_by_endpoints = {
        frozenset((candidate.left_signal_key, candidate.right_signal_key)): candidate
        for candidate in candidates
    }
    preliminary: dict[str, dict[str, Any]] = {}
    proposals_by_target: dict[str, list[str]] = defaultdict(list)
    for pair_id, disposition in accepted_by_id.items():
        old_pair = pair_manifest[pair_id]
        left_logical = old_key_by_signal.get(str(old_pair["left_signal_key"]))
        right_logical = old_key_by_signal.get(str(old_pair["right_signal_key"]))
        left = [signal.signal_key for signal in generated_by_logical.get(left_logical, ())]
        right = [signal.signal_key for signal in generated_by_logical.get(right_logical, ())]
        matches = {
            candidate_by_endpoints[frozenset((left_key, right_key))].pair_id:
            candidate_by_endpoints[frozenset((left_key, right_key))]
            for left_key in left for right_key in right
            if frozenset((left_key, right_key)) in candidate_by_endpoints
        }
        if len(matches) == 1:
            target_pair_id = next(iter(matches))
            classification = "proposed_comparable"
            proposals_by_target[target_pair_id].append(pair_id)
        elif len(matches) > 1 or len(left) > 1 or len(right) > 1:
            target_pair_id = None
            classification = "ambiguous_remap"
        else:
            target_pair_id = None
            classification = "genuine_target_missing"
        preliminary[pair_id] = {
            "accepted_pair_id": pair_id,
            "accepted_disposition": disposition,
            "target_pair_id": target_pair_id,
            "left_target_count": len(left),
            "right_target_count": len(right),
            "candidate_match_count": len(matches),
            "classification": classification,
        }

    finalize_r2r_proposal_classifications(preliminary, proposals_by_target, accepted_by_id)

    rows = [preliminary[pair_id] for pair_id in sorted(preliminary)]
    counts = Counter(str(row["classification"]) for row in rows)
    accepted_count = len(accepted_by_id)
    accounted = sum(int(counts.get(key, 0)) for key in (
        "comparable", "genuine_target_missing", "ambiguous_remap", "conflicting_remap"
    ))
    comparable = int(counts.get("comparable", 0))
    result = {
        "database": database,
        "accepted_snapshot_fingerprint": ACCEPTED_R2R_SNAPSHOT_FINGERPRINT,
        "accepted_pair_count": accepted_count,
        "comparable_count": comparable,
        "genuine_target_missing_count": int(counts.get("genuine_target_missing", 0)),
        "ambiguous_remap_count": int(counts.get("ambiguous_remap", 0)),
        "conflicting_remap_count": int(counts.get("conflicting_remap", 0)),
        "accounting_equation_passed": accepted_count == accounted,
        "compatibility": round(comparable / accepted_count, 9) if accepted_count else 1.0,
        "compatibility_derived_from_verified_pairs": True,
        "exact_endpoint_and_disposition_membership_passed": True,
        "generated_signal_count": len(generated),
        "accepted_logical_signal_count": len(accepted_signals),
        "generated_candidate_count": len(candidates),
        "pair_remap_membership_fingerprint": sha256_payload(rows),
    }
    if not result["accounting_equation_passed"]:
        raise SV1BPreflightError("r2r_remap_accounting_equation_failed")
    return result, rows


def prepare_and_audit_r2r_baseline(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    if not (output / "localization-baseline-proof.json").is_file():
        raise SV1BPreflightError("localization_baseline_proof_missing")
    input_results = {
        "primary": _prepare_graph_inputs(primary_database),
        "replay": _prepare_graph_inputs(replay_database),
    }
    primary, primary_rows = audit_r2r_remap(primary_database)
    replay, replay_rows = audit_r2r_remap(replay_database)
    write_json(output / "r2r-primary-remap-private.json", primary_rows)
    write_json(output / "r2r-replay-remap-private.json", replay_rows)
    logical_equal = [
        {key: value for key, value in row.items() if key != "target_pair_id"}
        for row in primary_rows
    ] == [
        {key: value for key, value in row.items() if key != "target_pair_id"}
        for row in replay_rows
    ]
    result = {
        "graph_inputs": input_results,
        "primary": primary,
        "replay": replay,
        "primary_replay_logical_remap_equal": logical_equal,
        "target_completion_ready": bool(
            logical_equal
            and primary["ambiguous_remap_count"] == 0
            and primary["conflicting_remap_count"] == 0
        ),
    }
    write_json(output / "r2r-exact-remap-audit.json", result)
    if not logical_equal:
        raise SV1BPreflightError("primary_replay_r2r_remap_mismatch")
    return result


def queue_provider_manifest(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    if not (output / "r2r-exact-remap-audit.json").is_file():
        raise SV1BPreflightError("r2r_exact_remap_audit_missing")
    parser_audit_path = output / "runtime-parser-denominator-proof.json"
    if not parser_audit_path.is_file() or read_json(parser_audit_path).get("passed") is not True:
        raise SV1BPreflightError("runtime_parser_denominator_proof_missing_or_failed")
    provider_output = output / "provider"
    args = SimpleNamespace(
        database=primary_database,
        output_dir=provider_output,
        execute=False,
        accept_local_credential_risk=False,
        replay_normalization_failures=False,
        additional_diagnostic_calls=0,
        gallery_dl_command="",
        timeout=120,
        phase_manifest_fingerprint=ACCEPTED_MANIFEST_FINGERPRINT,
    )
    queue_summary = ingestion_runner.run(args)
    main_manifest = read_json(provider_output / "exact-distinct-work-manifest.json")
    conflict_manifest = read_json(provider_output / "exact-conflict-resolution-manifest.json")
    actual_main = {str(value) for value in main_manifest.get("work_ids") or ()}
    actual_conflict = {str(value) for value in conflict_manifest.get("work_ids") or ()}
    work_rows = read_jsonl(output / "distinct-work-acquisition-manifest-private.jsonl")
    accepted_work_universe = {str(row["stable_work_id"]) for row in work_rows}
    engine = engine_for(primary_database)
    try:
        with engine.connect() as connection:
            queue_rows = list(connection.execute(text(
                "SELECT source_work_id,status FROM blombooru_source_metadata_records "
                "WHERE provider='pixiv' AND metadata_kind='pixiv_ingestion_gate' "
                "AND source_work_id IS NOT NULL"
            )).mappings())
    finally:
        engine.dispose()
    states_by_work: dict[str, set[str]] = defaultdict(set)
    for row in queue_rows:
        states_by_work[str(row["source_work_id"])].add(str(row["status"] or ""))
    expected_conflict = {
        work_id for work_id, states in states_by_work.items()
        if PixivMetadataState.CONFLICT.value in states
    }
    expected_main = {
        work_id for work_id, states in states_by_work.items()
        if states.intersection({PixivMetadataState.PENDING.value, PixivMetadataState.RETRYABLE.value})
        and work_id not in expected_conflict
    }
    expected_open = expected_main | expected_conflict
    actual_open = actual_main | actual_conflict
    missing_main = sorted(expected_main - actual_main, key=int)
    extra_main = sorted(actual_main - expected_main, key=int)
    missing_conflict = sorted(expected_conflict - actual_conflict, key=int)
    extra_conflict = sorted(actual_conflict - expected_conflict, key=int)
    outside_accepted_universe = sorted(actual_open - accepted_work_universe, key=int)
    missing = sorted((expected_open - actual_open), key=int)
    extra = sorted((actual_open - expected_open), key=int)
    main_conflict_overlap = actual_main.intersection(actual_conflict)
    result = {
        "phase_manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
        "queued_media_count": int(queue_summary["queued_media_count"]),
        "expected_open_distinct_work_count": len(expected_open),
        "main_manifest_distinct_work_count": len(actual_main),
        "conflict_manifest_distinct_work_count": len(actual_conflict),
        "actual_open_distinct_work_count": len(actual_open),
        "main_conflict_overlap_count": len(main_conflict_overlap),
        "missing_main_manifest_work_count": len(missing_main),
        "unexpected_main_manifest_work_count": len(extra_main),
        "missing_conflict_manifest_work_count": len(missing_conflict),
        "unexpected_conflict_manifest_work_count": len(extra_conflict),
        "outside_accepted_work_universe_count": len(outside_accepted_universe),
        "missing_expected_work_count": len(missing),
        "unexpected_work_count": len(extra),
        "missing_expected_work_fingerprint": sha256_payload(missing),
        "unexpected_work_fingerprint": sha256_payload(extra),
        "exact_open_work_membership_passed": not (
            missing_main or extra_main or missing_conflict or extra_conflict
            or outside_accepted_universe or main_conflict_overlap
        ),
        "provider_request_count": 0,
        "provider_attempt_count": 0,
        "media_download_count": 0,
        "queue_state_counts": queue_summary["queue_state_counts"],
        "main_manifest_fingerprint": ingestion_runner.executable_manifest_fingerprint(main_manifest),
        "conflict_manifest_fingerprint": ingestion_runner.executable_manifest_fingerprint(conflict_manifest),
    }
    write_json(output / "provider-queue-manifest-proof.json", result)
    if result["exact_open_work_membership_passed"] is not True:
        write_json(output / "provider-queue-membership-mismatch-private.json", {
            "missing_expected_work_ids": missing,
            "unexpected_work_ids": extra,
            "missing_main_manifest_work_ids": missing_main,
            "unexpected_main_manifest_work_ids": extra_main,
            "missing_conflict_manifest_work_ids": missing_conflict,
            "unexpected_conflict_manifest_work_ids": extra_conflict,
            "outside_accepted_work_universe_ids": outside_accepted_universe,
            "main_conflict_overlap_work_ids": sorted(main_conflict_overlap, key=int),
        })
        raise SV1BPreflightError(
            "provider_queue_manifest_membership_mismatch:"
            f"main_missing={len(missing_main)}:main_extra={len(extra_main)}:"
            f"conflict_missing={len(missing_conflict)}:conflict_extra={len(extra_conflict)}:"
            f"outside_universe={len(outside_accepted_universe)}:overlap={len(main_conflict_overlap)}"
        )
    return result


def execute_provider_manifest(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    queue_proof = read_json(output / "provider-queue-manifest-proof.json")
    if queue_proof.get("exact_open_work_membership_passed") is not True:
        raise SV1BPreflightError("provider_queue_manifest_proof_missing_or_failed")
    gate = provider_gate_preflight()
    if gate.get("passed") is not True:
        raise SV1BPreflightError("blocked_sv1b_provider_authentication")
    args = SimpleNamespace(
        database=primary_database,
        output_dir=output / "provider",
        execute=True,
        accept_local_credential_risk=False,
        replay_normalization_failures=False,
        additional_diagnostic_calls=0,
        gallery_dl_command="",
        timeout=120,
        phase_manifest_fingerprint=ACCEPTED_MANIFEST_FINGERPRINT,
    )
    summary = ingestion_runner.run(args)
    result = {
        "phase_manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
        "execution_requested": summary.get("execution_requested") is True,
        "credential_rotation_confirmation_present": summary.get("credential_rotation_confirmation_present") is True,
        "redacted_secret_scan_passed": (summary.get("redacted_secret_scan") or {}).get("passed") is True,
        "redacted_authentication_preflight_passed": (
            summary.get("redacted_authentication_preflight") or {}
        ).get("passed") is True,
        "operation_counts": summary.get("operation_counts") or {},
        "acquisition_execution": summary.get("acquisition_execution") or {},
        "provider_raw_execution_summary_private": str(output / "provider" / "execution-summary.json"),
    }
    write_json(output / "provider-execution-proof.json", result)
    return result


def audit_acquisition_closure_rows(
    page_rows: Iterable[Mapping[str, Any]],
    queue_rows: Iterable[Mapping[str, Any]],
    evidence_rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Audit exact page/media closure without exposing private membership."""

    expected: set[tuple[str, str, int]] = set()
    work_pages: dict[str, list[str]] = defaultdict(list)
    for row in page_rows:
        key = (
            str(row.get("media_stable_key") or ""),
            canonical_work_id(row.get("stable_work_id")),
            int(row.get("requested_page_index") or 0),
        )
        if not key[0] or key in expected:
            raise SV1BPreflightError("acquisition_page_manifest_duplicate_or_missing_stable_key")
        expected.add(key)

    evidence_by_record: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in evidence_rows:
        evidence_by_record[int(row["source_metadata_record_id"])].append(row)
    actual: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in queue_rows:
        work_id = str(row.get("source_work_id") or "").strip()
        media_key = str(row.get("media_stable_key") or "").strip()
        if work_id and media_key:
            actual[(media_key, canonical_work_id(work_id), int(row.get("source_page_index") or 0))].append(row)

    missing = expected - set(actual)
    extra = set(actual) - expected
    duplicate = {key for key in expected if len(actual.get(key, ())) != 1}
    outcome_counts: Counter[str] = Counter()
    blocking_counts: Counter[str] = Counter()
    for key in sorted(expected):
        rows = actual.get(key, ())
        if len(rows) != 1:
            continue
        row = rows[0]
        status = str(row.get("status") or "")
        raw = row.get("raw_metadata_json") if isinstance(row.get("raw_metadata_json"), Mapping) else {}
        record_evidence = evidence_by_record.get(int(row["id"]), ())
        if status == PixivMetadataState.COMPLETE.value and is_trusted_complete_pixiv_metadata_record(row):
            outcome = PixivMetadataState.COMPLETE.value
        elif status == PixivMetadataState.TERMINAL.value and (
            str(raw.get("failure_reason") or "") == "authenticated_remote_deleted_private_unavailable"
            and bool(raw.get("last_attempt_at"))
        ):
            outcome = PixivMetadataState.TERMINAL.value
        elif status == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value and any(
            str(item.get("evidence_kind") or "") == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value
            and str(item.get("status") or "") == "active"
            and str((item.get("provenance") or {}).get("governance_policy_version") or "")
            == DEFERRED_PAGE_MISMATCH_POLICY_VERSION
            and (item.get("provenance") or {}).get("unsupported_page_link_created") is False
            for item in record_evidence
        ):
            outcome = PixivMetadataState.DEFERRED_PAGE_MISMATCH.value
        else:
            outcome = "blocking_" + (status or "missing_status")
            blocking_counts[outcome] += 1
        outcome_counts[outcome] += 1
        work_pages[key[1]].append(outcome)

    work_outcomes: Counter[str] = Counter()
    closed_values = {
        PixivMetadataState.COMPLETE.value,
        PixivMetadataState.TERMINAL.value,
        PixivMetadataState.DEFERRED_PAGE_MISMATCH.value,
    }
    for values in work_pages.values():
        distinct = set(values)
        if not distinct.issubset(closed_values):
            work_outcomes["blocking_open_or_invalid"] += 1
        elif len(distinct) == 1:
            work_outcomes[next(iter(distinct))] += 1
        else:
            work_outcomes["mixed_closed"] += 1

    passed = not (missing or extra or duplicate or blocking_counts) and sum(outcome_counts.values()) == len(expected)
    public = {
        "page_manifest_count": len(expected),
        "queue_membership_count": len(actual),
        "missing_page_queue_count": len(missing),
        "unexpected_page_queue_count": len(extra),
        "duplicate_page_queue_count": len(duplicate),
        "page_outcome_counts": dict(sorted(outcome_counts.items())),
        "page_equation_balanced": sum(outcome_counts.values()) == len(expected),
        "distinct_work_count": len(work_pages),
        "work_outcome_counts": dict(sorted(work_outcomes.items())),
        "work_equation_balanced": sum(work_outcomes.values()) == len(work_pages),
        "blocking_outcome_counts": dict(sorted(blocking_counts.items())),
        "passed": passed,
    }
    private = {
        "missing_keys": sorted(missing),
        "unexpected_keys": sorted(extra),
        "duplicate_keys": sorted(duplicate),
    }
    return public, private


def export_acquired_nonderived_package(
    output: Path,
    *,
    primary_database: str,
) -> dict[str, Any]:
    export_paths = Paths(output / "acquired-evidence-export-private")
    export_stable_evidence(export_paths, source_database=primary_database)
    full_package = read_json(export_paths.package)
    package = filter_nonderived_source_package(
        full_package,
        package_version="sv1b_acquired_nonderived_source_evidence_v1",
    )
    normalized = json.loads(canonical_json(package))
    for row in normalized["tables"]["source_metadata_records"]:
        row.pop("raw_metadata_json", None)
    package_path = output / "acquired-nonderived-evidence-package-private.json"
    write_json(package_path, package)
    result = {
        "package_version": package["package_version"],
        "table_counts": {name: len(rows) for name, rows in package["tables"].items()},
        "derived_row_count": sum(
            len(rows) for name, rows in package["tables"].items()
            if name not in NONDERIVED_SOURCE_TABLES
        ),
        "acquired_metadata_package_fingerprint": sha256_payload(package),
        "normalized_evidence_fingerprint": sha256_payload(normalized),
        "package_file_sha256": sha256_file(package_path),
        "private_package_path": str(package_path),
    }
    return result


def filter_nonderived_source_package(
    full_package: Mapping[str, Any],
    *,
    package_version: str,
) -> dict[str, Any]:
    return {
        **full_package,
        "package_version": package_version,
        "tables": {
            name: list(rows) if name in NONDERIVED_SOURCE_TABLES else []
            for name, rows in full_package["tables"].items()
        },
    }


def audit_acquisition_and_package(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    execution_path = output / "provider" / "execution-summary.json"
    if not execution_path.is_file():
        raise SV1BPreflightError("provider_execution_summary_missing")
    execution = read_json(execution_path)
    if not (
        execution.get("execution_requested") is True
        and (execution.get("redacted_secret_scan") or {}).get("passed") is True
        and (execution.get("redacted_authentication_preflight") or {}).get("passed") is True
    ):
        raise SV1BPreflightError("provider_execution_governance_proof_failed")
    page_rows = read_jsonl(output / "candidate-page-media-manifest-private.jsonl")
    engine = engine_for(primary_database)
    try:
        with engine.connect() as connection:
            queue_rows = list(connection.execute(text("""
                SELECT r.id,m.hash AS media_stable_key,r.provider,r.metadata_kind,r.data_type_label,
                       r.status,r.source_work_id,r.source_page_index,r.provenance,r.raw_metadata_json
                FROM blombooru_source_metadata_records r
                JOIN blombooru_media m ON m.id=r.media_id
                WHERE r.provider='pixiv' AND r.metadata_kind='pixiv_ingestion_gate'
                  AND r.source_work_id IS NOT NULL
            """)).mappings())
            evidence_rows = list(connection.execute(text("""
                SELECT source_metadata_record_id,evidence_kind,status,provenance
                FROM blombooru_source_metadata_evidence
            """)).mappings())
    finally:
        engine.dispose()
    closure, private = audit_acquisition_closure_rows(page_rows, queue_rows, evidence_rows)
    if closure["passed"] is not True:
        write_json(output / "acquisition-closure-mismatch-private.json", private)
        raise SV1BPreflightError("acquisition_page_or_work_closure_failed")
    package = export_acquired_nonderived_package(output, primary_database=primary_database)
    result = {
        "closure": closure,
        "package": package,
        "provider_request_attempt_count": int(
            (execution.get("acquisition_execution") or {}).get("provider_request_attempt_count") or 0
        ),
        "media_download_count": int((execution.get("operation_counts") or {}).get("media_downloads") or 0),
        "passed": closure["passed"] is True and package["derived_row_count"] == 0,
    }
    write_json(output / "acquisition-closure-and-package-proof.json", result)
    return result


def import_acquired_package_to_replay(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    proof = read_json(output / "acquisition-closure-and-package-proof.json")
    if proof.get("passed") is not True:
        raise SV1BPreflightError("acquisition_closure_package_proof_missing_or_failed")
    package_path = output / "acquired-nonderived-evidence-package-private.json"
    package = read_json(package_path)
    expected_fingerprint = str((proof.get("package") or {}).get("acquired_metadata_package_fingerprint") or "")
    if sha256_payload(package) != expected_fingerprint:
        raise SV1BPreflightError("acquired_metadata_package_fingerprint_mismatch")
    derived_tables = tuple(
        name for name in CORE_SOURCE_TABLES
        if name.replace("blombooru_", "") not in NONDERIVED_SOURCE_TABLES
    )
    replay_derived_before = database_fingerprint(replay_database, derived_tables)
    if any(int(row["count"]) != 0 for row in replay_derived_before["tables"].values()):
        raise SV1BPreflightError("replay_derived_source_tables_not_pristine")
    engine = engine_for(replay_database)
    try:
        with engine.begin() as connection:
            imported = import_stable_evidence(connection, package)
    finally:
        engine.dispose()
    replay_export_paths = Paths(output / "replay-acquired-evidence-reconciliation-private")
    export_stable_evidence(replay_export_paths, source_database=replay_database)
    replay_package = filter_nonderived_source_package(
        read_json(replay_export_paths.package),
        package_version=str(package["package_version"]),
    )
    reconciliation = reconcile_stable_evidence_packages(
        package, replay_package, _database_media_keys(replay_database)
    )
    replay_derived_after = database_fingerprint(replay_database, derived_tables)
    result = {
        "acquired_metadata_package_fingerprint": expected_fingerprint,
        "replay_import": imported,
        "replay_reconciliation": reconciliation,
        "replay_derived_tables_pristine_before": all(
            int(row["count"]) == 0 for row in replay_derived_before["tables"].values()
        ),
        "replay_derived_tables_pristine_after": all(
            int(row["count"]) == 0 for row in replay_derived_after["tables"].values()
        ),
        "primary_replay_nonderived_logical_fingerprint_equal": sha256_payload(package) == sha256_payload(replay_package),
        "provider_request_count": 0,
        "passed": bool(
            reconciliation.get("exact_stable_key_membership_passed") is True
            and int(reconciliation.get("blocking_failed") or 0) == 0
            and int(reconciliation.get("extra_materialized_count") or 0) == 0
            and sha256_payload(package) == sha256_payload(replay_package)
            and all(int(row["count"]) == 0 for row in replay_derived_after["tables"].values())
        ),
    }
    write_json(output / "replay-acquired-evidence-import-proof.json", result)
    if result["passed"] is not True:
        raise SV1BPreflightError("replay_acquired_evidence_reconciliation_failed")
    return result


def provider_gate_preflight() -> dict[str, Any]:
    entrypoint = gallery_adapter.probe_gallery_dl_entrypoint(None)
    profile = validate_gallery_dl_profile(entrypoint.command, timeout_seconds=30)
    rotation = str(os.getenv("VIOLET_CREDENTIAL_ROTATION_CONFIRMED") or "").strip().casefold() in {"1", "true", "yes"}
    fingerprints = [
        value.strip() for value in str(os.getenv("VIOLET_COMPROMISED_SECRET_SHA256") or "").replace(";", ",").split(",")
        if value.strip()
    ]
    fingerprint_shape_valid = bool(fingerprints) and all(
        len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)
        for value in fingerprints
    )
    passed = bool(
        profile["provider_profile_available"]
        and profile["configuration_status_command_passed"]
        and rotation
        and fingerprint_shape_valid
    )
    return {
        "metadata_only_command_semantics": {"dump_json": True, "no_download": True},
        "persistent_cross_process_spacing_required_seconds": MIN_REQUEST_SPACING_SECONDS,
        "provider_profile": profile,
        "credential_rotation_confirmation_present": rotation,
        "compromised_secret_fingerprint_count": len(fingerprints),
        "compromised_secret_fingerprint_shape_valid": fingerprint_shape_valid,
        "delimiter_aware_secret_scan_executed": False,
        "redacted_authentication_canary_executed": False,
        "provider_request_count": 0,
        "provider_attempt_count": 0,
        "passed": passed,
    }


def public_console_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    candidate = result.get("candidate_manifest") or {}
    provider = result.get("provider_hardening") or {}
    isolation = result.get("environment_isolation") or {}
    accepted = result.get("accepted_nonderived_evidence") or {}
    localization = result.get("localization_baseline") or {}
    vocabulary = localization.get("vocabulary") or {}
    r2r = result.get("r2r_baseline_audit") or {}
    r2r_primary = r2r.get("primary") or {}
    queue = result.get("provider_queue") or {}
    parser_audit = result.get("runtime_parser_denominator") or {}
    primary_import = accepted.get("primary_import") or {}
    replay_import = accepted.get("replay_import") or {}
    return {
        "phase": result.get("phase"),
        "status": result.get("status"),
        "target_met": result.get("target_met"),
        "safe_to_merge": result.get("safe_to_merge"),
        "route_approved": result.get("route_approved"),
        "manual_acceptance_status": result.get("manual_acceptance_status"),
        "canonical_candidate_media_count": candidate.get("canonical_candidate_media_count"),
        "page_media_manifest_row_count": candidate.get("page_media_manifest_row_count"),
        "distinct_work_count": candidate.get("distinct_work_count"),
        "provider_request_count": provider.get("provider_request_count"),
        "provider_attempt_count": provider.get("provider_attempt_count"),
        "environment_isolation_passed": isolation.get("passed"),
        "primary_accepted_evidence_inserted_total": primary_import.get("inserted_total"),
        "replay_accepted_evidence_inserted_total": replay_import.get("inserted_total"),
        "accepted_evidence_reconciliation_passed": bool(
            accepted.get("primary_reconciliation_passed") is True
            and accepted.get("replay_reconciliation_passed") is True
        ),
        "ai_media_tag_vocabulary_count": vocabulary.get("ai_media_tag_vocabulary_count"),
        "blocking_missing_ai_translation_count": vocabulary.get("blocking_missing_ai_translation_count"),
        "external_llm_call_count": localization.get("external_llm_call_count"),
        "accepted_r2r_pair_count": r2r_primary.get("accepted_pair_count"),
        "r2r_comparable_count": r2r_primary.get("comparable_count"),
        "r2r_genuine_target_missing_count": r2r_primary.get("genuine_target_missing_count"),
        "r2r_ambiguous_remap_count": r2r_primary.get("ambiguous_remap_count"),
        "r2r_conflicting_remap_count": r2r_primary.get("conflicting_remap_count"),
        "queued_open_distinct_work_count": queue.get("actual_open_distinct_work_count"),
        "provider_queue_membership_passed": queue.get("exact_open_work_membership_passed"),
        "runtime_parser_version": parser_audit.get("runtime_parser_version"),
        "runtime_parser_denominator_passed": parser_audit.get("passed"),
        "private_values_exposed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--stage",
        choices=(
            "inventory", "prepare-databases", "import-accepted-evidence",
            "localization-baseline", "r2r-baseline-audit",
            "queue-provider",
            "execute-provider", "audit-acquisition-package", "import-acquired-replay",
        ),
        default="inventory",
    )
    parser.add_argument("--primary-db", default=DEFAULT_PRIMARY_DB)
    parser.add_argument("--replay-db", default=DEFAULT_REPLAY_DB)
    args = parser.parse_args()
    output = args.output.resolve()
    resume_stage = args.stage in {
        "import-accepted-evidence", "localization-baseline", "r2r-baseline-audit", "queue-provider",
        "execute-provider", "audit-acquisition-package", "import-acquired-replay",
    }
    if resume_stage:
        validate_owned_output_root(
            output, primary_database=args.primary_db, replay_database=args.replay_db
        )
        repository = {
            **read_json(output / "run-identity.json"),
            "resume_head": git("rev-parse", "HEAD"),
            "resume_ownership_passed": True,
        }
    else:
        repository = validate_repository_and_inputs(output)
    immutable_before = immutable_input_fingerprints()
    runtime_parser_denominator = audit_runtime_parser_denominator()
    pages, works, candidates = build_candidate_manifests()
    provider_gate = provider_gate_preflight()
    immutable_after = immutable_input_fingerprints()
    immutable_proof = {
        "before": immutable_before,
        "after": immutable_after,
        "unchanged": immutable_before == immutable_after,
        "database_write_count": 0,
        "storage_write_count": 0,
    }
    if not immutable_proof["unchanged"]:
        raise SV1BPreflightError("immutable_input_drift_detected")

    if not resume_stage:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "run-identity.json", {"phase": PHASE, **repository})
    write_jsonl(output / "candidate-page-media-manifest-private.jsonl", pages)
    write_jsonl(output / "distinct-work-acquisition-manifest-private.jsonl", works)
    write_json(output / "candidate-manifest-summary.json", candidates)
    write_json(output / "provider-hardening-preflight.json", provider_gate)
    write_json(output / "immutable-input-proof.json", immutable_proof)
    write_json(output / "runtime-parser-denominator-proof.json", runtime_parser_denominator)
    environment_isolation = None
    if args.stage == "prepare-databases":
        environment_isolation = prepare_isolated_databases(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    elif resume_stage:
        environment_isolation = read_json(output / "database-ownership-and-baseline-proof.json")
    accepted_evidence = None
    if args.stage == "import-accepted-evidence":
        accepted_evidence = import_accepted_nonderived_evidence(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    elif args.stage in {"localization-baseline", "r2r-baseline-audit", "queue-provider", "execute-provider", "audit-acquisition-package", "import-acquired-replay"}:
        accepted_evidence = read_json(output / "accepted-nonderived-evidence-proof.json")
    localization_baseline = None
    if args.stage == "localization-baseline":
        localization_baseline = prepare_localization_baseline(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    elif args.stage in {"r2r-baseline-audit", "queue-provider", "execute-provider", "audit-acquisition-package", "import-acquired-replay"}:
        localization_baseline = read_json(output / "localization-baseline-proof.json")
    r2r_baseline_audit = None
    if args.stage == "r2r-baseline-audit":
        r2r_baseline_audit = prepare_and_audit_r2r_baseline(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    elif args.stage in {"queue-provider", "execute-provider", "audit-acquisition-package", "import-acquired-replay"}:
        r2r_baseline_audit = read_json(output / "r2r-exact-remap-audit.json")
    provider_queue = None
    if args.stage == "queue-provider":
        provider_queue = queue_provider_manifest(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    elif args.stage in {"execute-provider", "audit-acquisition-package", "import-acquired-replay"}:
        provider_queue = read_json(output / "provider-queue-manifest-proof.json")
    provider_execution = None
    if args.stage == "execute-provider":
        provider_execution = execute_provider_manifest(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    elif args.stage in {"audit-acquisition-package", "import-acquired-replay"}:
        provider_execution_path = output / "provider-execution-proof.json"
        if not provider_execution_path.is_file():
            raise SV1BPreflightError("provider_execution_proof_missing")
        provider_execution = read_json(provider_execution_path)
    acquisition_package = None
    if args.stage == "audit-acquisition-package":
        acquisition_package = audit_acquisition_and_package(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    elif args.stage == "import-acquired-replay":
        acquisition_package = read_json(output / "acquisition-closure-and-package-proof.json")
    replay_acquired_import = None
    if args.stage == "import-acquired-replay":
        replay_acquired_import = import_acquired_package_to_replay(
            output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
    active_blockers: list[str] = []
    if provider_gate["passed"] is not True:
        active_blockers.append("blocked_sv1b_provider_authentication")
    if localization_baseline and localization_baseline.get("localization_complete") is not True:
        active_blockers.append("blocked_sv1b_normalization_or_localization")
    if r2r_baseline_audit and r2r_baseline_audit.get("target_completion_ready") is not True:
        active_blockers.append("blocked_sv1b_r2r_replay")
    status = active_blockers[0] if active_blockers else "provider_hardening_preflight_passed_auth_canary_pending"
    result = {
        "phase": PHASE,
        "status": status,
        "candidate_manifest": candidates,
        "runtime_parser_denominator": runtime_parser_denominator,
        "provider_hardening": provider_gate,
        "immutable_inputs_unchanged": immutable_proof["unchanged"],
        "environment_isolation": environment_isolation,
        "accepted_nonderived_evidence": accepted_evidence,
        "localization_baseline": localization_baseline,
        "r2r_baseline_audit": r2r_baseline_audit,
        "provider_queue": provider_queue,
        "provider_execution": provider_execution,
        "acquisition_package": acquisition_package,
        "replay_acquired_import": replay_acquired_import,
        "active_blockers": active_blockers,
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "manual_acceptance_required": True,
        "manual_acceptance_status": "not_generated_provider_gate_blocked",
        "next_phase_started": False,
    }
    write_json(output / "preflight-result.json", result)
    print(json.dumps(public_console_summary(result), ensure_ascii=False, sort_keys=True))
    return 0 if provider_gate["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
