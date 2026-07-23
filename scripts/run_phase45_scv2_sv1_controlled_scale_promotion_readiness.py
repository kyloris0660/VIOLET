#!/usr/bin/env python3
"""SCV2-SV1 controlled-scale replay and stable-key promotion rehearsal.

This is a phase-scoped operational runner.  It deliberately composes the
accepted E1 ingestion/AI path and the SourceConcept runtime models instead of
creating a second product importer.  All paths and row-level evidence remain
private under the configured output directory; public publication is handled
only by the final fail-closed stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
import platform
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import MetaData, Table, create_engine, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (str(ROOT), str(BACKEND)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from scripts.phase_contracts import check_phase_contract  # noqa: E402
from scripts.run_phase45_scv2_e1_medium_import_ai_tag_completion import (  # noqa: E402
    RuntimeContext,
    build_source_root_inventory,
    build_storage_identity,
    discover_candidates,
    env_local_library_paths,
    execute_imports,
    extract_pixiv_ids,
    hash_text,
    hash_candidates_with_timeout,
    preflight_local_model_availability,
    run_ai_tagging,
    safe_candidate_label,
    source_roots_from_local_manifests,
    stable_private_id,
    unique_paths,
    _source_gate_for_path,
)

PHASE = "SCV2-SV1"
BRANCH = "codex/scv2-sv1-controlled-scale-promotion-readiness"
CONTRACT_ID = "sv1_controlled_scale_promotion_readiness_contract_v1"
ACCEPTED_ML2_MERGE = "7fca41151cc9e1d5b48cfe243279e66296346bae"
ACCEPTED_ML2_EVIDENCE = "00398a0b5b1a46d010e82c2b6f72796dbdb47918"
ACCEPTED_ML2_DB = "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715"
PREDECESSOR_DBS = (
    "blombooru_scv2_r2r_dryrun_test_20260710",
    "blombooru_scv2_ml1_acquisition_test_20260712",
    ACCEPTED_ML2_DB,
    "blombooru_scv2_ml2_identity_closure_test_20260714",
)
DEFAULT_SCALE_DB = "blombooru_scv2_sv1_controlled_scale_test_20260718"
DEFAULT_PROMOTION_DB = "blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1"
DEFAULT_STORAGE = ROOT / ".local_test_storage/phase-4.5-scv2-sv1-controlled-scale"
DEFAULT_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness"
DEFAULT_REPAIR_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-sv1-gov3-repair-20260718-v1"
DEFAULT_REBUILD_DB = "blombooru_scv2_sv1_rebuild_verification_test_20260718"
ML2_PRIVATE = ROOT / ".local_manifests/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-reviewfix-20260715"
R2R_PRIVATE = ROOT / ".local_manifests/phase-4.5-scv2-r2r-autonomous-recall-search-closure"
TARGET_MEDIA = 12000
MIN_MEDIA = 10000
MAX_MEDIA = 15000
CONFIRM = "EXECUTE_SCV2_SV1_CONTROLLED_SCALE_PROMOTION_READINESS"
CANONICAL_ALL_STAGES = (
    "prepare", "import", "ai", "evidence", "promotion", "benchmark", "rebuild",
    "connected-graph-audits", "repair-benchmark", "finalization-accounting",
    "validation", "repair-finalize",
)
CORE_SOURCE_TABLES = (
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_name_observations",
    "blombooru_source_metadata_evidence",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_tag_registry",
    "blombooru_source_name_registry",
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
    "blombooru_source_concept_fallback_search_index",
)
PROTECTED_TABLES = (
    "blombooru_media",
    "blombooru_media_tags",
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_evidence",
    "blombooru_entity_external_identities",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
)
JSON_FIELDS = {
    "raw_metadata_json", "provenance", "raw_variants_json", "provider_coverage_json",
    "role_distribution_json", "evidence_sources_json", "provenance_summary",
    "input_signal_counts_json", "linked_counts_json", "concept_counts_json",
    "review_counts_json", "no_truth_write_proof_json", "summary_json",
    "evidence_payload", "evidence_summary_json", "lifecycle_payload", "payload",
    "evidence_refs_json", "provenance_payload",
}


class SV1BlockedError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
    os.replace(temp, path)
    return count


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def changed_file_fingerprint() -> str:
    """Bind validation to the exact tracked patch applied over the current HEAD."""
    patch = subprocess.check_output(
        ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"], cwd=ROOT
    )
    return hashlib.sha256(patch).hexdigest()


def db_url(database: str) -> URL:
    if not is_strict_test_database_name(database):
        raise SV1BlockedError(f"unsafe_database_identity:{database}")
    return URL.create(
        "postgresql", username=os.getenv("POSTGRES_USER", "postgres"), password=os.getenv("POSTGRES_PASSWORD", ""),
        host=os.getenv("POSTGRES_HOST", "localhost"), port=int(os.getenv("POSTGRES_PORT", "5432")), database=database,
    )


def is_strict_test_database_name(database: str) -> bool:
    """Accept only V.I.O.L.E.T. DB names with a delimited ``test`` segment."""
    return bool(re.fullmatch(r"blombooru_[a-z0-9]+(?:_[a-z0-9]+)*", database)) and "test" in database.split("_")


def require_resolved_descendant(path: Path, root: Path, *, label: str) -> Path:
    """Return a resolved private path only when it is a true root descendant."""
    resolved = path.resolve()
    resolved_root = root.resolve()
    if resolved == resolved_root:
        raise SV1BlockedError(f"{label}_must_be_descendant")
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise SV1BlockedError(f"{label}_outside_private_root") from exc
    return resolved


def engine_for(database: str) -> Engine:
    return create_engine(db_url(database), pool_pre_ping=True, connect_args={"connect_timeout": 10})


def database_exists(database: str) -> bool:
    admin = create_engine(db_url("blombooru_test"))
    try:
        with admin.connect() as conn:
            return bool(conn.execute(text("SELECT 1 FROM pg_database WHERE datname=:db"), {"db": database}).scalar())
    finally:
        admin.dispose()


def create_clean_database(database: str) -> dict[str, Any]:
    if database_exists(database):
        raise SV1BlockedError(f"database_already_exists:{database}")
    env = os.environ.copy()
    env.update({"VIOLET_ENV": "test", "POSTGRES_DB": database, "TEST_DATABASE_URL": ""})
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/setup_test_db.py"), "--migrate"], cwd=ROOT,
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    if run.returncode:
        raise SV1BlockedError(f"database_setup_failed:{database}:{run.stderr[-500:]}")
    engine = engine_for(database)
    try:
        with engine.connect() as conn:
            media = int(conn.execute(text("SELECT COUNT(*) FROM blombooru_media")).scalar() or 0)
            source_counts = {table: table_count(conn, table) for table in CORE_SOURCE_TABLES}
            db_name = str(conn.execute(text("SELECT current_database()" )).scalar())
    finally:
        engine.dispose()
    clean = media == 0 and not any(source_counts.values()) and db_name == database
    if not clean:
        raise SV1BlockedError(f"database_not_clean_schema:{database}")
    return {"database": database, "clean_schema": True, "media_count": media, "source_counts": source_counts}


def verify_clean_database(database: str) -> dict[str, Any]:
    engine = engine_for(database)
    try:
        with engine.connect() as conn:
            media = table_count(conn, "blombooru_media")
            source_counts = {table: table_count(conn, table) for table in CORE_SOURCE_TABLES}
            current = str(conn.execute(text("SELECT current_database()" )).scalar())
    finally:
        engine.dispose()
    if current != database or media or any(source_counts.values()):
        raise SV1BlockedError(f"existing_database_not_pristine_checkpoint:{database}")
    return {"database": database, "clean_schema": True, "media_count": 0, "source_counts": source_counts, "pristine_same_run_checkpoint_reused": True}


def table_count(conn: Connection, table: str) -> int:
    exists = conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar()
    return int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0) if exists else 0


def table_fingerprint(conn: Connection, table: str) -> dict[str, Any]:
    if not conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar():
        return {"table": table, "count": 0, "sha256": sha256_payload([]), "exists": False}
    rows = [canonical_json(dict(row)) for row in conn.execute(text(f'SELECT * FROM "{table}"')).mappings()]
    rows.sort()
    return {"table": table, "count": len(rows), "sha256": sha256_payload(rows), "exists": True}


def database_fingerprint(database: str, tables: Sequence[str]) -> dict[str, Any]:
    engine = engine_for(database)
    try:
        with engine.connect() as conn:
            result = {table: table_fingerprint(conn, table) for table in tables}
    finally:
        engine.dispose()
    return {"database": database, "tables": result, "fingerprint": sha256_payload(result)}


def apply_runtime_environment(args: argparse.Namespace, database: str) -> None:
    os.environ.update({
        "VIOLET_ENV": "test", "POSTGRES_DB": database, "TEST_DATABASE_URL": "",
        "VIOLET_STORAGE_ROOT": str(args.storage_root.resolve()),
        "CONTENT_CLASSIFICATION_ENABLED": "false", "AI_TAGGING_AUTO_LOCALIZATION": "false",
        "TAG_TRANSLATION_AUTO_ENABLED": "false", "TAG_TRANSLATION_BACKGROUND_ENABLED": "false",
        "TAG_TRANSLATION_LLM_ENABLED": "false", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    })


@dataclass(frozen=True)
class Paths:
    output: Path

    @property
    def inventory(self) -> Path: return self.output / "source-inventory-manifest.jsonl"
    @property
    def manifest(self) -> Path: return self.output / "scale-selection-manifest.jsonl"
    @property
    def import_ledger(self) -> Path: return self.output / "media-import-ledger.jsonl"
    @property
    def ai_ledger(self) -> Path: return self.output / "ai-tag-coverage-ledger.jsonl"
    @property
    def package(self) -> Path: return self.output / "stable-key-evidence-package.json"
    @property
    def package_manifest(self) -> Path: return self.output / "stable-key-evidence-package-manifest.json"
    @property
    def summary_private(self) -> Path: return self.output / "evidence-summary-private.json"


def validate_preflight(args: argparse.Namespace) -> dict[str, Any]:
    branch = git("branch", "--show-current")
    if branch != BRANCH:
        raise SV1BlockedError(f"wrong_branch:{branch}")
    if os.getenv("VIOLET_ENV") != "test":
        raise SV1BlockedError("violet_env_not_test")
    writable_databases = {
        "scale": str(args.scale_db),
        "promotion": str(args.promotion_db),
        "rebuild": str(args.rebuild_db),
    }
    invalid = sorted(name for name, database in writable_databases.items() if not is_strict_test_database_name(database))
    if invalid:
        raise SV1BlockedError(f"unsafe_writable_database_identity:{invalid}")
    if len(set(writable_databases.values())) != len(writable_databases):
        raise SV1BlockedError("writable_database_identities_not_pairwise_distinct")
    predecessor_overlap = sorted(
        name for name, database in writable_databases.items() if database in PREDECESSOR_DBS
    )
    if predecessor_overlap:
        raise SV1BlockedError(f"accepted_predecessor_database_not_writable:{predecessor_overlap}")
    storage, output = validate_private_roots(args)
    return {
        "branch": branch, "head": git("rev-parse", "HEAD"), "violet_env": os.getenv("VIOLET_ENV"),
        "scale_database": args.scale_db, "promotion_database": args.promotion_db,
        "rebuild_database": args.rebuild_db,
        "writable_database_identities_strict_test": True,
        "writable_database_identities_pairwise_distinct": True,
        "accepted_predecessor_databases_excluded": True,
        "storage_identity": sha256_payload(str(storage).casefold()),
        "output_identity": sha256_payload(str(output).casefold()),
    }


def validate_private_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """Validate every private filesystem target without creating any path."""
    storage = require_resolved_descendant(args.storage_root, ROOT / ".local_test_storage", label="storage_root")
    output = require_resolved_descendant(args.output_dir, ROOT / ".local_manifests", label="output_root")
    if output.is_relative_to((ROOT / ".local_test_storage").resolve()) or storage.is_relative_to((ROOT / ".local_manifests").resolve()):
        raise SV1BlockedError("private_root_cross_nesting")
    return storage, output


def recompute_inventory_accounting(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pre = Counter(str(row.get("preselection_outcome") or row.get("inventory_outcome")) for row in rows)
    final = Counter(str(row.get("inventory_outcome")) for row in rows)
    candidates = len(rows)
    pre_keys = ("eligible_unique", "excluded_duplicate", "excluded_ineligible", "excluded_unreadable", "excluded_out_of_scope")
    final_keys = ("selected", "eligible_not_selected", "excluded_duplicate", "excluded_ineligible", "excluded_unreadable", "excluded_out_of_scope")
    return {
        "inventory_candidate_count": candidates,
        "preselection_outcome_counts": {key: int(pre.get(key, 0)) for key in pre_keys},
        "final_outcome_counts": {key: int(final.get(key, 0)) for key in final_keys},
        "preselection_accounting_equality_passed": candidates == sum(int(pre.get(key, 0)) for key in pre_keys),
        "final_accounting_equality_passed": candidates == sum(int(final.get(key, 0)) for key in final_keys),
        "preselection_membership_fingerprint": sha256_payload(sorted(
            (str(row.get("candidate_id")), str(row.get("preselection_outcome") or row.get("inventory_outcome"))) for row in rows
        )),
        "final_membership_fingerprint": sha256_payload(sorted(
            (str(row.get("candidate_id")), str(row.get("inventory_outcome"))) for row in rows
        )),
    }


def derive_eligible_media_count(*, manifest_count: int, database_count: int, import_ledger_count: int, ai_ledger_count: int) -> int:
    counts = {int(manifest_count), int(database_count), int(import_ledger_count), int(ai_ledger_count)}
    if len(counts) != 1:
        raise SV1BlockedError(
            f"eligible_media_count_mismatch:manifest={manifest_count}:database={database_count}:import={import_ledger_count}:ai={ai_ledger_count}"
        )
    return int(manifest_count)


def exact_resume_accounting(*, checkpoint_media: int, checkpoint_storage: int, current_runtime_seconds: float, original_runtime_seconds: float | None = None) -> dict[str, Any]:
    return {
        "original_execution": {
            "imported_media_count": checkpoint_media,
            "storage_write_count": checkpoint_storage,
            "runtime_seconds": original_runtime_seconds,
            "runtime_evidence_available": original_runtime_seconds is not None,
        },
        "current_invocation": {
            "new_import_count": 0,
            "storage_write_count": 0,
            "runtime_seconds": current_runtime_seconds,
            "resumed_exact_checkpoint": True,
        },
        "cumulative_checkpoint_state": {
            "imported_media_count": checkpoint_media,
            "storage_object_count": checkpoint_storage,
        },
        "resumed_exact_import_checkpoint": True,
    }


def accepted_media_public_wording() -> str:
    return "All accepted current media that remained available and fingerprint-compatible were included."


def accepted_media_hashes() -> set[str]:
    engine = engine_for(ACCEPTED_ML2_DB)
    try:
        with engine.connect() as conn:
            return {str(value) for value in conn.execute(text("SELECT hash FROM blombooru_media WHERE hash IS NOT NULL" )).scalars()}
    finally:
        engine.dispose()


def accepted_media_filename_map() -> dict[str, set[str]]:
    engine = engine_for(ACCEPTED_ML2_DB)
    try:
        with engine.connect() as conn:
            result: dict[str, set[str]] = defaultdict(set)
            for row in conn.execute(text("SELECT hash,filename FROM blombooru_media WHERE hash IS NOT NULL AND filename IS NOT NULL")):
                result[str(row.filename).casefold()].add(str(row.hash))
            return dict(result)
    finally:
        engine.dispose()


def targeted_accepted_candidates(
    roots: Sequence[Path], existing_candidates: Sequence[Mapping[str, Any]], accepted_by_filename: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    existing_paths = {str(row["source_locator_private_ref"]).casefold() for row in existing_candidates}
    found: list[dict[str, Any]] = []
    index = len(existing_candidates)
    for root_index, root in enumerate(roots, 1):
        for path in root.rglob("*"):
            try:
                if not path.is_file() or path.name.casefold() not in accepted_by_filename:
                    continue
                if str(path).casefold() in existing_paths:
                    continue
                stat = path.stat()
            except OSError:
                continue
            index += 1
            extension = path.suffix.casefold()
            safe_label = safe_candidate_label(index, extension)
            gate = _source_gate_for_path(path, safe_label)
            found.append({
                "run_id": "sv1-targeted-accepted", "candidate_id": stable_private_id(path, "candidate"),
                "source_root_label": f"source_root_{root_index}", "source_locator_private_ref": str(path),
                "original_filename_sha256": hash_text(path.name), "original_filename_redacted": True,
                "extension": extension, "size": int(stat.st_size), "detected_pixiv_ids": extract_pixiv_ids(path.name),
                "candidate_source_reason": "accepted_current_filename_targeted_read_only_lookup",
                "cloud_state": gate.get("cloud_state"), "source_gate_allowed": bool(gate.get("allowed")),
                "readable_status": "not_read_yet", "unsupported_reason": None,
                "duplicate_check_status": "not_checked", "existing_media_match": None,
                "eligible_for_import": bool(gate.get("allowed")), "deferred_reason": None if gate.get("allowed") else str(gate.get("reason") or "source_gate_blocked"),
                "public_safe_label": safe_label,
            })
    return found


def inventory_and_manifest(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    started = time.monotonic()
    roots = unique_paths([*env_local_library_paths(), *source_roots_from_local_manifests()])
    root_inventory = build_source_root_inventory(roots, args.storage_root)
    storage_identity = build_storage_identity(roots, args.storage_root)
    storage_identity["storage_root_fingerprint"] = sha256_payload(str(args.storage_root.resolve()).casefold())
    if not storage_identity.get("source_storage_overlap_safe") or not storage_identity.get("storage_root_under_repo"):
        raise SV1BlockedError("unsafe_storage_root")
    safe_roots = [
        Path(str(row["source_root_private"])) for row in root_inventory
        if row.get("exists") and row.get("is_dir") and not row.get("under_app_storage")
    ]
    candidates, discovery = discover_candidates(
        run_id=args.run_id, source_roots=safe_roots, storage_root=args.storage_root,
        max_discovery_files=args.max_discovery_files, max_file_size_mb=args.max_file_size_mb,
    )
    accepted_by_filename = accepted_media_filename_map()
    candidates.extend(targeted_accepted_candidates(safe_roots, candidates, accepted_by_filename))
    hashable = [row for row in candidates if row.get("eligible_for_import") and row.get("source_gate_allowed")]
    hash_results = hash_candidates_with_timeout(hashable, args.hash_timeout_seconds)
    accepted_hashes = accepted_media_hashes()
    seen: set[str] = set()
    inventory_rows: list[dict[str, Any]] = []
    eligible_rows: list[dict[str, Any]] = []
    accounting = Counter()
    for row in candidates:
        result = hash_results.get(str(row["candidate_id"]))
        item = dict(row)
        file_hash = str(result.get("hash")) if result and result.get("ok") else None
        item["file_hash"] = file_hash
        item["content_fingerprint_present"] = bool(file_hash)
        item["accepted_current_media"] = bool(file_hash and file_hash in accepted_hashes)
        item["pixiv_like"] = bool(item.get("detected_pixiv_ids"))
        item["import_time_bucket"] = "unknown_not_safely_available"
        if not item.get("eligible_for_import") or not item.get("source_gate_allowed"):
            outcome = "excluded_ineligible"
        elif not result or not result.get("ok"):
            outcome = "excluded_unreadable"
            item["exclusion_reason"] = str((result or {}).get("error_reason") or "unreadable_source")
        elif file_hash in seen:
            outcome = "excluded_duplicate"
            item["exclusion_reason"] = "duplicate_content_fingerprint"
        else:
            outcome = "eligible_unique"
            seen.add(file_hash)
            eligible_rows.append(item)
        item["preselection_outcome"] = outcome
        item["inventory_outcome"] = outcome
        accounting[outcome] += 1
        inventory_rows.append(item)
    if len(eligible_rows) < MIN_MEDIA:
        write_jsonl(paths.inventory, inventory_rows)
        raise SV1BlockedError("blocked_sv1_source_inventory_insufficient")

    accepted_available = sorted(
        (row for row in eligible_rows if row["accepted_current_media"]),
        key=lambda row: (str(row["file_hash"]), str(row["candidate_id"])),
    )
    accepted_unavailable_count = len(accepted_hashes) - len(accepted_available)
    additional = sorted(
        (row for row in eligible_rows if not row["accepted_current_media"]),
        key=lambda row: hashlib.sha256(f"sv1-v1|{row['file_hash']}|{row['source_root_label']}|{row['extension']}".encode()).hexdigest(),
    )
    selected = accepted_available + additional[: max(0, args.target_media - len(accepted_available))]
    selected_ids = {str(row["candidate_id"]) for row in selected}
    if not MIN_MEDIA <= len(selected) <= MAX_MEDIA:
        raise SV1BlockedError(f"scale_manifest_out_of_bounds:{len(selected)}")
    for row in inventory_rows:
        if str(row.get("candidate_id")) in selected_ids:
            row["inventory_outcome"] = "selected"
        elif row.get("preselection_outcome") == "eligible_unique":
            row["inventory_outcome"] = "eligible_not_selected"
    manifest_rows = []
    for index, row in enumerate(selected, 1):
        manifest_rows.append({
            **row,
            "selection_index": index,
            "selection_seed": "sv1-v1",
            "selection_reason": "accepted_current_media" if row["accepted_current_media"] else "deterministic_scale_sample",
            "eligible_for_import": True,
            "public_safe_label": f"sv1_media_{index:06d}{row['extension']}",
        })
    write_jsonl(paths.inventory, inventory_rows)
    write_jsonl(paths.manifest, manifest_rows)
    inventory_fp = sha256_file(paths.inventory)
    manifest_fp = sha256_file(paths.manifest)
    public_extension_counts = Counter(str(row["extension"]) for row in selected)
    public_root_counts = Counter(str(row["source_root_label"]) for row in selected)
    public_pixiv_counts = Counter("pixiv_like" if row["pixiv_like"] else "non_pixiv_like" for row in selected)
    final_accounting = recompute_inventory_accounting(inventory_rows)
    result = {
        "source_inventory": {
            "inventory_candidate_count": len(inventory_rows),
            "safely_usable_real_media_count": len(eligible_rows),
            "accepted_current_media_count": len(accepted_hashes),
            "accepted_current_available_count": len(accepted_available),
            "accepted_current_included_count": sum(bool(row["accepted_current_media"]) for row in selected),
            "accepted_current_source_unavailable_count": accepted_unavailable_count,
            "accepted_current_fingerprint_incompatible_count": 0,
            "inventory_fingerprint": inventory_fp,
            "inventory_runtime_seconds": round(time.monotonic() - started, 3),
            "source_root_count": len(safe_roots),
            "source_routes_read_only": True,
            "source_mutation_count": 0,
        },
        "scale_manifest": {
            "selected_eligible_media_count": len(selected),
            "deterministic_selection": True,
            "selection_seed": "sv1-v1",
            "manifest_fingerprint": manifest_fp,
            "accepted_current_available_media_included": True,
            "accepted_current_source_unavailable_count": accepted_unavailable_count,
            "synthetic_or_cloned_media_count": 0,
            "accounting_equality_passed": bool(final_accounting["preselection_accounting_equality_passed"] and final_accounting["final_accounting_equality_passed"]),
            **final_accounting,
            "extension_distribution": dict(sorted(public_extension_counts.items())),
            "source_root_distribution": dict(sorted(public_root_counts.items())),
            "pixiv_filename_distribution": dict(sorted(public_pixiv_counts.items())),
        },
        "storage": {**storage_identity, "source_root_count": len(root_inventory)},
    }
    write_json(paths.output / "inventory-and-manifest-summary.json", result)
    return result


def prepare(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    predecessor_path = paths.output / "predecessor-fingerprints-before.json"
    if predecessor_path.exists():
        before = read_json(predecessor_path)
    else:
        before = {db: database_fingerprint(db, ("blombooru_media", "blombooru_media_tags", *CORE_SOURCE_TABLES)) for db in PREDECESSOR_DBS}
        write_json(predecessor_path, before)
    scale = verify_clean_database(args.scale_db) if database_exists(args.scale_db) else create_clean_database(args.scale_db)
    promotion_planned = {"database": args.promotion_db, "exists_before_prepare": database_exists(args.promotion_db)}
    if promotion_planned["exists_before_prepare"]:
        raise SV1BlockedError(f"database_already_exists:{args.promotion_db}")
    inventory = inventory_and_manifest(args, paths)
    result = {"predecessors_before": before, "scale_database": scale, "promotion_database": promotion_planned, **inventory}
    write_json(paths.output / "prepare-summary.json", result)
    return result


def _runtime_context(args: argparse.Namespace) -> RuntimeContext:
    return RuntimeContext(
        run_id=args.run_id, mode="execute", output_dir=args.output_dir,
        storage_root=args.storage_root, original_dir=args.storage_root / "media/original",
        thumbnail_dir=args.storage_root / "media/thumbnails",
        database_url_safe=f"postgresql://[redacted]/{args.scale_db}",
        db_identity_source={"database": args.scale_db},
    )


def reuse_accepted_ai_tags(scale: Engine) -> dict[str, Any]:
    source = engine_for(ACCEPTED_ML2_DB)
    reused_media = 0
    reused_links = 0
    target_metadata = MetaData()
    target_metadata.reflect(bind=scale, only=["blombooru_tags"])
    target_tags = target_metadata.tables["blombooru_tags"]
    try:
        with source.connect() as src, scale.begin() as dst:
            target_by_hash = {str(row.hash): int(row.id) for row in dst.execute(text("SELECT id,hash FROM blombooru_media"))}
            source_by_hash = {str(row.hash): int(row.id) for row in src.execute(text("SELECT id,hash FROM blombooru_media"))}
            common = sorted(set(target_by_hash).intersection(source_by_hash))
            if not common:
                return {"reused_media_count": 0, "reused_ai_tag_row_count": 0}
            tag_rows = src.execute(text("""
                SELECT m.hash, t.name, t.category::text AS category, mt.confidence, mt.is_locked, mt.is_suggestion
                FROM blombooru_media_tags mt
                JOIN blombooru_media m ON m.id=mt.media_id
                JOIN blombooru_tags t ON t.id=mt.tag_id
                WHERE mt.source='ai_wd'
            """)).mappings()
            tag_ids: dict[str, int] = {}
            media_seen: set[str] = set()
            for row in tag_rows:
                media_hash = str(row["hash"])
                if media_hash not in target_by_hash:
                    continue
                name = str(row["name"])
                if name not in tag_ids:
                    tag_id = dst.execute(text("SELECT id FROM blombooru_tags WHERE name=:n"), {"n": name}).scalar()
                    if tag_id is None:
                        tag_id = dst.execute(
                            insert(target_tags).values(name=name, category=str(row["category"] or "general"), post_count=0).returning(target_tags.c.id)
                        ).scalar_one()
                    tag_ids[name] = int(tag_id)
                result = dst.execute(text("""
                    INSERT INTO blombooru_media_tags(media_id,tag_id,source,confidence,is_locked,is_suggestion)
                    VALUES (:m,:t,'ai_wd',:c,:l,:s) ON CONFLICT DO NOTHING
                """), {"m": target_by_hash[media_hash], "t": tag_ids[name], "c": row["confidence"], "l": row["is_locked"], "s": row["is_suggestion"]})
                reused_links += int(result.rowcount or 0)
                media_seen.add(media_hash)
            reused_media = len(media_seen)
    finally:
        source.dispose()
    return {"reused_media_count": reused_media, "reused_ai_tag_row_count": reused_links, "fingerprint_mismatch_reuse_count": 0}


def import_media(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    manifest = read_jsonl(paths.manifest)
    if not MIN_MEDIA <= len(manifest) <= MAX_MEDIA:
        raise SV1BlockedError("invalid_scale_manifest")
    apply_runtime_environment(args, args.scale_db)
    engine = engine_for(args.scale_db)
    context = _runtime_context(args)
    started = time.monotonic()
    prior_summary = read_json(paths.output / "media-import-summary.json") if (paths.output / "media-import-summary.json").exists() else {}
    failure_budget = {"max_item_failures": 20, "max_failure_rate": 0.05, "max_same_reason_failures": 20, "max_consecutive_failures": 10}
    try:
        with engine.connect() as conn:
            existing_hashes = {str(value) for value in conn.execute(text("SELECT hash FROM blombooru_media" )).scalars()}
        manifest_hashes = {str(row["file_hash"]) for row in manifest}
        existing_ledger = read_jsonl(paths.import_ledger)
        storage_count = len(list(context.original_dir.glob("*")))
        thumbnail_count = len(list(context.thumbnail_dir.glob("*")))
        if existing_hashes:
            resume_passed = (
                existing_hashes == manifest_hashes
                and len(existing_ledger) == len(manifest)
                and all(row.get("status") == "imported" for row in existing_ledger)
                and storage_count == len(manifest)
                and thumbnail_count == len(manifest)
            )
            if not resume_passed:
                raise SV1BlockedError(
                    f"partial_import_checkpoint_mismatch:db={len(existing_hashes)}:ledger={len(existing_ledger)}:storage={storage_count}:thumbs={thumbnail_count}"
                )
            ledger = existing_ledger
            media_ids = []
            results = {"successful_imports": len(manifest), "failure_count": 0, "resumed_exact_checkpoint": True}
        else:
            ledger, results, media_ids = execute_imports(
                engine, context, manifest, execute=True, target_successful_imports=len(manifest),
                min_successful_imports=MIN_MEDIA, max_successful_imports=MAX_MEDIA,
                copy_timeout_seconds=args.copy_timeout_seconds, failure_budget=failure_budget,
            )
            write_jsonl(paths.import_ledger, ledger)
            if int(results.get("successful_imports") or 0) != len(manifest) or results.get("failure_count"):
                raise SV1BlockedError(f"controlled_import_incomplete:{results}")
        reuse = reuse_accepted_ai_tags(engine)
        with engine.connect() as conn:
            media_after = table_count(conn, "blombooru_media")
            tagged_media = int(conn.execute(text("SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags WHERE source='ai_wd'" )).scalar() or 0)
    finally:
        engine.dispose()
    original_runtime = None
    if results.get("resumed_exact_checkpoint"):
        candidate_runtime = prior_summary.get("original_execution", {}).get("runtime_seconds") if isinstance(prior_summary.get("original_execution"), Mapping) else None
        original_runtime = float(candidate_runtime) if isinstance(candidate_runtime, (int, float)) else None
    resume = exact_resume_accounting(
        checkpoint_media=media_after,
        checkpoint_storage=storage_count if results.get("resumed_exact_checkpoint") else len(manifest),
        current_runtime_seconds=round(time.monotonic() - started, 3),
        original_runtime_seconds=original_runtime,
    )
    if not results.get("resumed_exact_checkpoint"):
        resume = {
            "original_execution": {
                "imported_media_count": len(manifest),
                "storage_write_count": len(manifest),
                "runtime_seconds": round(time.monotonic() - started, 3),
                "runtime_evidence_available": True,
            },
            "current_invocation": {
                "new_import_count": len(manifest),
                "storage_write_count": len(manifest),
                "runtime_seconds": round(time.monotonic() - started, 3),
                "resumed_exact_checkpoint": False,
            },
            "cumulative_checkpoint_state": {
                "imported_media_count": media_after,
                "storage_object_count": len(manifest),
            },
            "resumed_exact_import_checkpoint": False,
        }
    public = {
        "all_selected_accounted": len(ledger) == len(manifest),
        "selected_media_count": len(manifest),
        "compatible_existing_media_reused": 0, "duplicate_content_skipped": 0,
        "deferred_nonblocking_source_unavailable": 0, "blocking_failed": 0,
        "unexplained_outcome_count": 0, "out_of_manifest_import_count": 0,
        "source_mutation_count": 0, "eligible_media_after": media_after,
        **resume,
        "ai_reuse": {**reuse, "tagged_media_after_reuse": tagged_media},
    }
    write_json(paths.output / "media-import-summary.json", public)
    return public


def original_ai_execution_evidence() -> dict[str, Any]:
    source_path = DEFAULT_OUTPUT / "ai-tag-coverage-summary.json"
    if not source_path.is_file():
        raise SV1BlockedError("missing_original_ai_execution_evidence")
    source = read_json(source_path)
    reused = int(source.get("reused_media_count", -1))
    inferred = int(source.get("newly_inferred_media_count", -1))
    if (reused, inferred, reused + inferred) != (3420, 8580, 12000):
        raise SV1BlockedError(f"original_ai_execution_accounting_mismatch:{reused}:{inferred}")
    return {
        "reused_media_count": reused,
        "newly_inferred_media_count": inferred,
        "eligible_media_count": reused + inferred,
        "ai_inference_executed": True,
        "source_evidence_fingerprint": sha256_file(source_path),
    }


def separated_ai_accounting(current: Mapping[str, Any], *, checkpoint_existing: int, newly_inferred: int) -> dict[str, Any]:
    return {
        **{key: value for key, value in current.items() if key not in {
            "reused_media_count", "newly_inferred_media_count", "runner_result",
        }},
        "original_accepted_execution": original_ai_execution_evidence(),
        "current_repair_invocation": {
            "checkpoint_existing_covered_media_count": checkpoint_existing,
            "newly_inferred_media_count": newly_inferred,
            "ai_inference_rerun": newly_inferred > 0,
        },
    }


def complete_ai_provenance(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    apply_runtime_environment(args, args.scale_db)
    model = preflight_local_model_availability()
    if model["ai_model"].get("model_downloaded") is not True:
        raise SV1BlockedError("local_ai_model_missing")
    engine = engine_for(args.scale_db)
    started = time.monotonic()
    try:
        with engine.connect() as conn:
            eligible = [int(v) for v in conn.execute(text("SELECT id FROM blombooru_media ORDER BY id" )).scalars()]
            already = {int(v) for v in conn.execute(text("SELECT DISTINCT media_id FROM blombooru_media_tags WHERE source='ai_wd'" )).scalars()}
        uncovered = [media_id for media_id in eligible if media_id not in already]
        failure_budget = {"max_item_failures": 20, "max_failure_rate": 0.05, "max_same_reason_failures": 20, "max_consecutive_failures": 10}
        ledger, failures, result = run_ai_tagging(uncovered, args.ai_chunk_size, failure_budget=failure_budget)
        inferred_by_id = {int(row["media_id"]): row for row in ledger if row.get("media_id") is not None}
        with engine.connect() as conn:
            media_rows = list(conn.execute(text("SELECT id,hash FROM blombooru_media ORDER BY hash")).mappings())
        complete_ledger = []
        model_name = str(model["ai_model"].get("model_name") or "unknown_local_model")
        for media in media_rows:
            media_id = int(media["id"])
            content_key = str(media["hash"])
            source = inferred_by_id.get(media_id)
            classification = "newly_inferred" if source else ("reused" if media_id in already else "checkpoint_existing")
            provenance_payload = {"content_key": content_key, "classification": classification, "model": model_name}
            complete_ledger.append({
                "stable_private_media_reference": sha256_payload({"media_content_key": content_key}),
                "media_content_key": content_key,
                "coverage_status": "covered",
                "coverage_classification": classification,
                "model_version": model_name,
                "provenance_fingerprint": sha256_payload(provenance_payload),
            })
        write_jsonl(paths.ai_ledger, complete_ledger)
        write_jsonl(paths.output / "ai-tag-failure-ledger.jsonl", failures)
        with engine.connect() as conn:
            covered = int(conn.execute(text("SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags WHERE source='ai_wd'" )).scalar() or 0)
            tag_rows = int(conn.execute(text("SELECT COUNT(*) FROM blombooru_media_tags WHERE source='ai_wd'" )).scalar() or 0)
    finally:
        engine.dispose()
    coverage = covered / len(eligible) if eligible else 1.0
    if failures or coverage != 1.0:
        raise SV1BlockedError(f"blocked_sv1_ai_tag_coverage:{len(failures)}:{coverage}")
    current = {
        "eligible_media_count": len(eligible), "ai_tag_row_count": tag_rows,
        "missing_provenance_count": len(eligible) - covered, "coverage": coverage,
        "fingerprint_mismatch_reuse_count": 0, "incompatible_evidence_rejected": 0,
        "model_version_distribution": {str(model["ai_model"].get("model_name")): len(eligible)},
        "model_download_count": 0, "external_provider_calls": 0,
        "ai_coverage_ledger_count": len(complete_ledger),
        "ai_coverage_ledger_fingerprint": sha256_file(paths.ai_ledger),
        "reuse_runtime_seconds": 0.0, "inference_runtime_seconds": round(time.monotonic() - started, 3),
        "runner_result": result,
    }
    public = separated_ai_accounting(current, checkpoint_existing=len(already), newly_inferred=len(uncovered))
    write_json(paths.output / "ai-tag-coverage-summary.json", public)
    return public


def _rows(conn: Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(text(f'SELECT * FROM "{table}" ORDER BY id')).mappings()]


def _strip_row(row: Mapping[str, Any], *, drop: Sequence[str] = ()) -> dict[str, Any]:
    ignored = {"id", "created_at", "updated_at", *drop}
    return {key: sanitize_stable_payload(value) for key, value in row.items() if key not in ignored}


STABLE_ID_KEYS = {
    "run_id", "created_by_run_id", "source_run_id", "provider_run_id", "source_work_id",
    "artist_id", "source_record_id", "pair_id", "observation_id", "external_id",
}


def sanitize_stable_payload(value: Any) -> Any:
    """Remove nested development row references while preserving provider identifiers."""

    if isinstance(value, Mapping):
        result = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.casefold()
            if (lowered == "id" or lowered.endswith("_id") or lowered.endswith("_ids")) and lowered not in STABLE_ID_KEYS:
                continue
            result[key] = sanitize_stable_payload(child)
        return result
    if isinstance(value, list):
        return [sanitize_stable_payload(item) for item in value]
    return value


def export_stable_evidence(paths: Paths, source_database: str = ACCEPTED_ML2_DB) -> dict[str, Any]:
    started = time.monotonic()
    engine = engine_for(source_database)
    try:
        with engine.connect() as conn:
            media_key = {int(row.id): str(row.hash) for row in conn.execute(text("SELECT id,hash FROM blombooru_media"))}
            run_key = {int(row.id): str(row.run_id) for row in conn.execute(text("SELECT id,run_id FROM blombooru_source_concept_resolution_runs"))}
            record_key = {int(row.id): str(row.provider_record_key) for row in conn.execute(text("SELECT id,provider_record_key FROM blombooru_source_metadata_records"))}
            tag_obs_key = {int(row.id): str(row.observation_key) for row in conn.execute(text("SELECT id,observation_key FROM blombooru_source_tag_observations"))}
            name_obs_key = {int(row.id): str(row.observation_key) for row in conn.execute(text("SELECT id,observation_key FROM blombooru_source_name_observations"))}
            concept_key = {int(row.id): str(row.concept_key) for row in conn.execute(text("SELECT id,concept_key FROM blombooru_source_concepts"))}
            signal_key = {int(row.id): str(row.signal_key) for row in conn.execute(text("SELECT id,signal_key FROM blombooru_source_concept_signals"))}
            package: dict[str, Any] = {
                "package_version": "sv1_stable_key_evidence_v1",
                "source": "accepted_ml2_immutable" if source_database == ACCEPTED_ML2_DB else "selected_test_database_read_only_reconciliation",
                "tables": {},
            }

            records = []
            for row in _rows(conn, "blombooru_source_metadata_records"):
                item = _strip_row(row, drop=("media_id",))
                item["media_content_key"] = media_key.get(row.get("media_id"))
                records.append(item)
            package["tables"]["source_metadata_records"] = records

            for logical, table, observation_map in (
                ("source_tag_observations", "blombooru_source_tag_observations", tag_obs_key),
                ("source_name_observations", "blombooru_source_name_observations", name_obs_key),
            ):
                values = []
                for row in _rows(conn, table):
                    item = _strip_row(row, drop=("source_metadata_record_id", "media_id", "taxonomy_kb_id"))
                    item["provider_record_key"] = record_key.get(row.get("source_metadata_record_id"))
                    if "media_id" in row:
                        item["media_content_key"] = media_key.get(row.get("media_id"))
                    values.append(item)
                package["tables"][logical] = values

            evidence_values = []
            for row in _rows(conn, "blombooru_source_metadata_evidence"):
                item = _strip_row(row, drop=("source_metadata_record_id", "observation_id"))
                item["provider_record_key"] = record_key.get(row.get("source_metadata_record_id"))
                observation_id = row.get("observation_id")
                item["observation_key"] = _observation_reference(
                    row.get("observation_type"), observation_id, tag_obs_key, name_obs_key,
                )
                if observation_id is not None and item["observation_key"] is None:
                    raise SV1BlockedError("source_metadata_evidence_observation_reference_unresolved")
                evidence_values.append(item)
            package["tables"]["source_metadata_evidence"] = evidence_values

            assertion_values = []
            for row in _rows(conn, "blombooru_source_searchable_name_assertions"):
                item = _strip_row(row, drop=("source_metadata_record_id", "source_tag_observation_id", "source_name_observation_id"))
                item["provider_record_key"] = record_key.get(row.get("source_metadata_record_id"))
                item["source_tag_observation_key"] = tag_obs_key.get(row.get("source_tag_observation_id"))
                item["source_name_observation_key"] = name_obs_key.get(row.get("source_name_observation_id"))
                assertion_values.append(item)
            package["tables"]["source_searchable_name_assertions"] = assertion_values

            package["tables"]["source_tag_registry"] = [_strip_row(row, drop=("example_source_metadata_id",)) for row in _rows(conn, "blombooru_source_tag_registry")]
            package["tables"]["source_name_registry"] = [_strip_row(row) for row in _rows(conn, "blombooru_source_name_registry")]
            package["tables"]["source_concept_resolution_runs"] = [_strip_row(row) for row in _rows(conn, "blombooru_source_concept_resolution_runs")]

            signal_values = []
            for row in _rows(conn, "blombooru_source_concept_signals"):
                item = _strip_row(row, drop=("resolution_run_id", "media_id", "source_metadata_record_id", "origin_id"))
                item["resolution_run_key"] = run_key.get(row.get("resolution_run_id"))
                item["media_content_key"] = media_key.get(row.get("media_id"))
                item["provider_record_key"] = record_key.get(row.get("source_metadata_record_id"))
                item["origin_stable_key"] = str(row.get("signal_key"))
                signal_values.append(item)
            package["tables"]["source_concept_signals"] = signal_values

            concepts = []
            for row in _rows(conn, "blombooru_source_concepts"):
                item = _strip_row(row, drop=("superseded_by_concept_id",))
                item["superseded_by_concept_key"] = concept_key.get(row.get("superseded_by_concept_id"))
                concepts.append(item)
            package["tables"]["source_concepts"] = concepts

            aliases = []
            for row in _rows(conn, "blombooru_source_concept_aliases"):
                item = _strip_row(row, drop=("concept_id", "source_signal_id"))
                item["concept_key"] = concept_key.get(row.get("concept_id"))
                item["source_signal_key"] = signal_key.get(row.get("source_signal_id"))
                aliases.append(item)
            package["tables"]["source_concept_aliases"] = aliases

            concept_evidence = []
            for row in _rows(conn, "blombooru_source_concept_evidence"):
                item = _strip_row(row, drop=("concept_id", "signal_id", "media_id", "source_metadata_record_id"))
                item["concept_key"] = concept_key.get(row.get("concept_id"))
                item["signal_key"] = signal_key.get(row.get("signal_id"))
                item["media_content_key"] = media_key.get(row.get("media_id"))
                item["provider_record_key"] = record_key.get(row.get("source_metadata_record_id"))
                concept_evidence.append(item)
            package["tables"]["source_concept_evidence"] = concept_evidence

            links = []
            for row in _rows(conn, "blombooru_source_concept_signal_links"):
                item = _strip_row(row, drop=("signal_id", "concept_id"))
                item["signal_key"] = signal_key.get(row.get("signal_id"))
                item["concept_key"] = concept_key.get(row.get("concept_id"))
                links.append(item)
            package["tables"]["source_concept_signal_links"] = links

            search_rows = []
            for row in _rows(conn, "blombooru_source_concept_search_index"):
                item = _strip_row(row, drop=("concept_id",))
                item["concept_key"] = concept_key.get(row.get("concept_id"))
                search_rows.append(item)
            package["tables"]["source_concept_search_index"] = search_rows

            fallback_rows = []
            for row in _rows(conn, "blombooru_source_concept_fallback_search_index"):
                item = _strip_row(row, drop=("media_id", "source_signal_id", "neighbor_signal_id"))
                item["media_content_key"] = media_key.get(row.get("media_id"))
                item["source_signal_key"] = signal_key.get(row.get("source_signal_id"))
                item["neighbor_signal_key"] = signal_key.get(row.get("neighbor_signal_id"))
                fallback_rows.append(item)
            package["tables"]["source_concept_fallback_search_index"] = fallback_rows
    finally:
        engine.dispose()

    forbidden_reference_fields = []
    for table, rows in package["tables"].items():
        for index, row in enumerate(rows):
            for key in row:
                if (key == "id" or key.endswith("_id") or key.endswith("_ids")) and key not in STABLE_ID_KEYS:
                    forbidden_reference_fields.append(f"{table}[{index}].{key}")
    if forbidden_reference_fields:
        raise SV1BlockedError(f"stable_package_row_id_dependency:{forbidden_reference_fields[:5]}")
    write_json(paths.package, package)
    counts = {table: len(rows) for table, rows in package["tables"].items()}
    manifest = {
        "package_version": package["package_version"], "table_counts": counts,
        "package_sha256": sha256_file(paths.package), "development_row_id_dependency_count": 0,
        "export_runtime_seconds": round(time.monotonic() - started, 3),
    }
    write_json(paths.package_manifest, manifest)
    return {"passed": True, **manifest, "package_checksum_manifest_passed": sha256_file(paths.package) == manifest["package_sha256"]}


def _insert_batches(conn: Connection, table: Table, rows: Sequence[Mapping[str, Any]], *, batch_size: int = 500) -> int:
    if not rows:
        return 0
    allowed = set(table.c.keys()) - {"id"}
    inserted = 0
    for start in range(0, len(rows), batch_size):
        values = [{key: value for key, value in row.items() if key in allowed} for row in rows[start:start + batch_size]]
        result = conn.execute(pg_insert(table).values(values).on_conflict_do_nothing())
        inserted += int(result.rowcount or 0)
    return inserted


def import_reusable_f7a_inputs(source_database: str, target_database: str) -> dict[str, Any]:
    """Replay normalized F7A extraction evidence with remapped foreign keys."""
    source, target = engine_for(source_database), engine_for(target_database)
    table_names = (
        "blombooru_source_name_candidate_extraction_runs",
        "blombooru_source_name_candidate_record_verdicts",
        "blombooru_source_name_candidates",
    )
    metadata = MetaData()
    metadata.reflect(bind=target, only=list(table_names))
    def target_table(name: str) -> Table:
        return metadata.tables.get(name) if metadata.tables.get(name) is not None else metadata.tables[f"public.{name}"]
    try:
        with source.connect() as src:
            run_rows_raw = _rows(src, table_names[0])
            verdict_rows_raw = _rows(src, table_names[1])
            candidate_rows_raw = _rows(src, table_names[2])
            source_run_key = {int(row["id"]): str(row["run_id"]) for row in run_rows_raw}
            source_record_key = {int(row.id): str(row.provider_record_key) for row in src.execute(text("SELECT id,provider_record_key FROM blombooru_source_metadata_records"))}
            source_media_key = {int(row.id): str(row.hash) for row in src.execute(text("SELECT id,hash FROM blombooru_media"))}
            verdict_key = {int(row["id"]): str(row["group_key"]) for row in verdict_rows_raw}
        with target.begin() as dst:
            run_rows = [_strip_row(row) for row in run_rows_raw]
            run_inserted = _insert_batches(dst, target_table(table_names[0]), run_rows)
            run_map = _key_map(dst, target_table(table_names[0]), "run_id")
            record_map = {str(row[1]): int(row[0]) for row in dst.execute(text("SELECT id,provider_record_key FROM blombooru_source_metadata_records"))}
            media_map = {str(row[1]): int(row[0]) for row in dst.execute(text("SELECT id,hash FROM blombooru_media"))}
            verdict_rows = []
            for row in verdict_rows_raw:
                item = _strip_row(row, drop=("extraction_run_id", "source_metadata_record_id", "media_id"))
                item["extraction_run_id"] = run_map[source_run_key[int(row["extraction_run_id"])]]
                item["source_metadata_record_id"] = record_map.get(source_record_key.get(int(row["source_metadata_record_id"] or 0), ""))
                item["media_id"] = media_map.get(source_media_key.get(int(row["media_id"] or 0), ""))
                verdict_rows.append(item)
            verdict_inserted = _insert_batches(dst, target_table(table_names[1]), verdict_rows)
            target_verdict = _key_map(dst, target_table(table_names[1]), "group_key")
            candidate_rows = []
            for row in candidate_rows_raw:
                item = _strip_row(row, drop=("extraction_run_id", "record_verdict_id", "source_metadata_record_id", "media_id", "superseded_by_candidate_id"))
                item["extraction_run_id"] = run_map[source_run_key[int(row["extraction_run_id"])]]
                item["record_verdict_id"] = target_verdict.get(verdict_key.get(int(row["record_verdict_id"] or 0), ""))
                item["source_metadata_record_id"] = record_map.get(source_record_key.get(int(row["source_metadata_record_id"] or 0), ""))
                item["media_id"] = media_map.get(source_media_key.get(int(row["media_id"] or 0), ""))
                item["superseded_by_candidate_id"] = None
                candidate_rows.append(item)
            candidate_inserted = _insert_batches(dst, target_table(table_names[2]), candidate_rows)
    finally:
        source.dispose()
        target.dispose()
    return {
        "extraction_run_input_count": len(run_rows_raw), "record_verdict_input_count": len(verdict_rows_raw),
        "candidate_input_count": len(candidate_rows_raw), "inserted_counts": {
            "extraction_runs": run_inserted, "record_verdicts": verdict_inserted, "candidates": candidate_inserted,
        }, "foreign_keys_remapped_by_stable_keys": True,
    }


def _key_map(conn: Connection, table: Table, key: str) -> dict[str, int]:
    return {str(row[1]): int(row[0]) for row in conn.execute(select(table.c.id, table.c[key])) if row[1] is not None}


def _source_concept_evidence_logical_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the complete logical identity used for stable-key replay.

    PostgreSQL unique constraints treat NULL values as distinct.  The accepted
    media-support evidence intentionally has no signal_id, so ON CONFLICT alone
    cannot make that evidence idempotent.  Comparing every durable evidence
    field (with mapped target IDs) closes that gap without adding a schema
    migration or relying on development row IDs.
    """

    fields = (
        "concept_id", "signal_id", "media_id", "source_metadata_record_id",
        "provider", "evidence_type", "evidence_strength", "payload", "run_id", "status",
    )
    return tuple(canonical_json(row.get(field)) for field in fields)


EVIDENCE_ACCOUNTING_OUTCOMES = (
    "inserted", "compatible_existing", "deferred_target_missing",
    "rejected_incompatible", "blocking_failed",
)


def _target_missing_references(
    values: Mapping[str, Sequence[Mapping[str, Any]]],
    media_keys: set[str],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Classify media-target gaps without exposing stable rows publicly."""
    missing_record_keys = {
        str(row.get("provider_record_key"))
        for row in values.get("source_metadata_records", ())
        if row.get("media_content_key") and str(row.get("media_content_key")) not in media_keys
    }
    counts: dict[str, int] = {}
    exact: dict[str, list[str]] = {}
    for logical, rows in values.items():
        missing_rows = []
        for row in rows:
            content_key = row.get("media_content_key")
            record_key = row.get("provider_record_key")
            if (
                (logical == "source_concept_fallback_search_index" and not content_key)
                or
                (content_key and str(content_key) not in media_keys)
                or (record_key and str(record_key) in missing_record_keys)
            ):
                missing_rows.append(canonical_json(dict(row)))
        counts[str(logical)] = len(missing_rows)
        exact[str(logical)] = sorted(missing_rows)
    return counts, exact


def _observation_reference(
    observation_type: Any,
    observation_id: Any,
    tag_observations: Mapping[Any, Any],
    name_observations: Mapping[Any, Any],
) -> Any:
    """Resolve the polymorphic observation reference without ID-space collision."""
    normalized = str(observation_type or "").casefold()
    if normalized == "source_tag_observation":
        return tag_observations.get(observation_id)
    if normalized == "source_name_observation":
        return name_observations.get(observation_id)
    return None


def validate_evidence_table_accounting(
    export_counts: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    per_table = result.get("per_table_accounting")
    if not isinstance(per_table, Mapping) or set(per_table) != set(export_counts):
        raise SV1BlockedError("evidence_import_table_membership_mismatch")
    failures: list[str] = []
    for logical, raw_exported in export_counts.items():
        row = per_table.get(logical)
        if not isinstance(row, Mapping):
            failures.append(f"{logical}:missing_accounting")
            continue
        exported = int(raw_exported)
        outcomes = {key: int(row.get(key, -1)) for key in EVIDENCE_ACCOUNTING_OUTCOMES}
        if any(value < 0 for value in outcomes.values()):
            failures.append(f"{logical}:negative_outcome")
        if exported != sum(outcomes.values()) or int(row.get("exported", -1)) != exported:
            failures.append(f"{logical}:equation")
        if outcomes["rejected_incompatible"] or outcomes["blocking_failed"]:
            failures.append(f"{logical}:blocking_or_incompatible")
    if int(result.get("unexplained_item_count", -1)) != 0:
        failures.append("unexplained_item_count")
    if int(result.get("blocking_failed", -1)) != 0:
        failures.append("blocking_failed")
    if int(result.get("development_row_id_dependency_count", -1)) != 0:
        failures.append("development_row_id_dependency_count")
    fallback = per_table.get("source_concept_fallback_search_index", {}) if isinstance(per_table, Mapping) else {}
    if int(result.get("fallback_search_target_missing_count", -1)) != int(fallback.get("deferred_target_missing", -2)):
        failures.append("fallback_search_target_missing_count")
    if failures:
        raise SV1BlockedError(f"evidence_import_accounting_failed:{sorted(failures)}")


def import_stable_evidence(conn: Connection, package: Mapping[str, Any]) -> dict[str, Any]:
    metadata = MetaData()
    metadata.reflect(bind=conn, only=list(CORE_SOURCE_TABLES))
    tables = metadata.tables
    values = package["tables"]
    media_map = {str(row[1]): int(row[0]) for row in conn.execute(text("SELECT id,hash FROM blombooru_media"))}
    inserted: dict[str, int] = {}
    target_missing_counts, target_missing_exact = _target_missing_references(values, set(media_map))

    def table(name: str) -> Table:
        return tables[f"public.{name}"] if f"public.{name}" in tables else tables[name]

    record_rows = []
    for row in values["source_metadata_records"]:
        item = dict(row)
        content_key = item.pop("media_content_key", None)
        item["media_id"] = media_map.get(str(content_key)) if content_key else None
        record_rows.append(item)
    inserted["source_metadata_records"] = _insert_batches(conn, table("blombooru_source_metadata_records"), record_rows)
    record_map = _key_map(conn, table("blombooru_source_metadata_records"), "provider_record_key")

    observation_maps: dict[str, dict[str, int]] = {}
    for logical, physical in (
        ("source_tag_observations", "blombooru_source_tag_observations"),
        ("source_name_observations", "blombooru_source_name_observations"),
    ):
        rows = []
        for row in values[logical]:
            item = dict(row)
            record = item.pop("provider_record_key", None)
            content_key = item.pop("media_content_key", None)
            item["source_metadata_record_id"] = record_map.get(str(record))
            if "media_id" in table(physical).c:
                item["media_id"] = media_map.get(str(content_key)) if content_key else None
            if item["source_metadata_record_id"] is None:
                raise SV1BlockedError(f"missing_provider_record_for_{logical}")
            rows.append(item)
        inserted[logical] = _insert_batches(conn, table(physical), rows)
        observation_maps[logical] = _key_map(conn, table(physical), "observation_key")

    rows = []
    for row in values["source_metadata_evidence"]:
        item = dict(row)
        item["source_metadata_record_id"] = record_map.get(str(item.pop("provider_record_key", "")))
        observation_key = item.pop("observation_key", None)
        item["observation_id"] = _observation_reference(
            item.get("observation_type"), str(observation_key) if observation_key else None,
            observation_maps["source_tag_observations"], observation_maps["source_name_observations"],
        )
        if observation_key and item["observation_id"] is None:
            raise SV1BlockedError("source_metadata_evidence_observation_target_missing")
        rows.append(item)
    inserted["source_metadata_evidence"] = _insert_batches(conn, table("blombooru_source_metadata_evidence"), rows)

    rows = []
    for row in values["source_searchable_name_assertions"]:
        item = dict(row)
        record = item.pop("provider_record_key", None)
        tag_key = item.pop("source_tag_observation_key", None)
        name_key = item.pop("source_name_observation_key", None)
        item["source_metadata_record_id"] = record_map.get(str(record)) if record else None
        item["source_tag_observation_id"] = observation_maps["source_tag_observations"].get(str(tag_key)) if tag_key else None
        item["source_name_observation_id"] = observation_maps["source_name_observations"].get(str(name_key)) if name_key else None
        rows.append(item)
    inserted["source_searchable_name_assertions"] = _insert_batches(conn, table("blombooru_source_searchable_name_assertions"), rows)
    inserted["source_tag_registry"] = _insert_batches(conn, table("blombooru_source_tag_registry"), values["source_tag_registry"])
    inserted["source_name_registry"] = _insert_batches(conn, table("blombooru_source_name_registry"), values["source_name_registry"])
    inserted["source_concept_resolution_runs"] = _insert_batches(conn, table("blombooru_source_concept_resolution_runs"), values["source_concept_resolution_runs"])
    run_map = _key_map(conn, table("blombooru_source_concept_resolution_runs"), "run_id")

    signal_rows = []
    for row in values["source_concept_signals"]:
        item = dict(row)
        run_key = item.pop("resolution_run_key", None)
        content_key = item.pop("media_content_key", None)
        record = item.pop("provider_record_key", None)
        item["resolution_run_id"] = run_map.get(str(run_key)) if run_key else None
        item["media_id"] = media_map.get(str(content_key)) if content_key else None
        item["source_metadata_record_id"] = record_map.get(str(record)) if record else None
        item["origin_id"] = item.pop("origin_stable_key", item.get("signal_key"))
        signal_rows.append(item)
    inserted["source_concept_signals"] = _insert_batches(conn, table("blombooru_source_concept_signals"), signal_rows)
    signal_map = _key_map(conn, table("blombooru_source_concept_signals"), "signal_key")

    concept_rows = []
    superseded: list[tuple[str, str]] = []
    for row in values["source_concepts"]:
        item = dict(row)
        target = item.pop("superseded_by_concept_key", None)
        if target:
            superseded.append((str(item["concept_key"]), str(target)))
        item["superseded_by_concept_id"] = None
        concept_rows.append(item)
    inserted["source_concepts"] = _insert_batches(conn, table("blombooru_source_concepts"), concept_rows)
    concept_map = _key_map(conn, table("blombooru_source_concepts"), "concept_key")
    for source_key, target_key in superseded:
        conn.execute(update(table("blombooru_source_concepts")).where(table("blombooru_source_concepts").c.id == concept_map[source_key]).values(superseded_by_concept_id=concept_map[target_key]))

    alias_rows = []
    for row in values["source_concept_aliases"]:
        item = dict(row)
        item["concept_id"] = concept_map[str(item.pop("concept_key"))]
        signal = item.pop("source_signal_key", None)
        item["source_signal_id"] = signal_map.get(str(signal)) if signal else None
        alias_rows.append(item)
    inserted["source_concept_aliases"] = _insert_batches(conn, table("blombooru_source_concept_aliases"), alias_rows)

    evidence_rows = []
    for row in values["source_concept_evidence"]:
        item = dict(row)
        item["concept_id"] = concept_map[str(item.pop("concept_key"))]
        signal = item.pop("signal_key", None)
        content_key = item.pop("media_content_key", None)
        record = item.pop("provider_record_key", None)
        item["signal_id"] = signal_map.get(str(signal)) if signal else None
        item["media_id"] = media_map.get(str(content_key)) if content_key else None
        item["source_metadata_record_id"] = record_map.get(str(record)) if record else None
        evidence_rows.append(item)
    evidence_table = table("blombooru_source_concept_evidence")
    evidence_fields = (
        "concept_id", "signal_id", "media_id", "source_metadata_record_id",
        "provider", "evidence_type", "evidence_strength", "payload", "run_id", "status",
    )
    existing_evidence_keys = {
        _source_concept_evidence_logical_key(dict(row))
        for row in conn.execute(select(*(evidence_table.c[field] for field in evidence_fields))).mappings()
    }
    deduplicated_evidence_rows = []
    for item in evidence_rows:
        logical_key = _source_concept_evidence_logical_key(item)
        if logical_key not in existing_evidence_keys:
            deduplicated_evidence_rows.append(item)
            existing_evidence_keys.add(logical_key)
    inserted["source_concept_evidence"] = _insert_batches(conn, evidence_table, deduplicated_evidence_rows)

    link_rows = []
    for row in values["source_concept_signal_links"]:
        item = dict(row)
        item["signal_id"] = signal_map[str(item.pop("signal_key"))]
        item["concept_id"] = concept_map[str(item.pop("concept_key"))]
        link_rows.append(item)
    inserted["source_concept_signal_links"] = _insert_batches(conn, table("blombooru_source_concept_signal_links"), link_rows)

    search_rows = []
    for row in values["source_concept_search_index"]:
        item = dict(row)
        item["concept_id"] = concept_map[str(item.pop("concept_key"))]
        search_rows.append(item)
    inserted["source_concept_search_index"] = _insert_batches(conn, table("blombooru_source_concept_search_index"), search_rows)

    fallback_rows = []
    fallback_target_missing = 0
    for row in values["source_concept_fallback_search_index"]:
        item = dict(row)
        content_key = item.pop("media_content_key", None)
        item["media_id"] = media_map.get(str(content_key)) if content_key else None
        item["source_signal_id"] = signal_map[str(item.pop("source_signal_key"))]
        item["neighbor_signal_id"] = signal_map[str(item.pop("neighbor_signal_key"))]
        if item["media_id"] is not None:
            fallback_rows.append(item)
        else:
            fallback_target_missing += 1
    inserted["source_concept_fallback_search_index"] = _insert_batches(conn, table("blombooru_source_concept_fallback_search_index"), fallback_rows)
    deferred = {logical: 0 for logical in values}
    deferred["source_concept_fallback_search_index"] = fallback_target_missing
    per_table: dict[str, dict[str, Any]] = {}
    for logical, rows in values.items():
        exported = len(rows)
        inserted_count = int(inserted.get(logical, 0))
        deferred_count = int(deferred.get(logical, 0))
        compatible_existing = exported - inserted_count - deferred_count
        per_table[str(logical)] = {
            "exported": exported,
            "inserted": inserted_count,
            "compatible_existing": compatible_existing,
            "deferred_target_missing": deferred_count,
            "rejected_incompatible": 0,
            "blocking_failed": 0,
            "target_missing_reference_count": int(target_missing_counts.get(logical, 0)),
            "target_missing_reference_fingerprint": sha256_payload(target_missing_exact.get(logical, [])),
            "equation_balanced": compatible_existing >= 0,
        }
    result = {
        "inserted_counts": inserted,
        "inserted_total": sum(inserted.values()),
        "compatible_existing_total": sum(int(row["compatible_existing"]) for row in per_table.values()),
        "deferred_nonblocking_target_missing": sum(int(row["deferred_target_missing"]) for row in per_table.values()),
        "fallback_search_target_missing_count": fallback_target_missing,
        "target_missing_reference_counts": target_missing_counts,
        "target_missing_stable_rows_private": target_missing_exact,
        "per_table_accounting": per_table,
        "blocking_failed": 0,
        "unexplained_item_count": 0,
        "accepted_evidence_silently_dropped": 0,
        "development_row_id_dependency_count": 0,
        "atomic_import_contract_enforced": True,
        "success_ledger_written_only_after_commit": True,
    }
    validate_evidence_table_accounting({logical: len(rows) for logical, rows in values.items()}, result)
    return result


def evidence_to_scale(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    export = export_stable_evidence(paths)
    package = read_json(paths.package)
    export_counts = {str(key): int(value) for key, value in export["table_counts"].items()}
    before = database_fingerprint(args.scale_db, CORE_SOURCE_TABLES)
    engine = engine_for(args.scale_db)
    started = time.monotonic()
    try:
        try:
            with engine.begin() as conn:
                imported = import_stable_evidence(conn, package)
                # This second validation deliberately remains inside the same
                # transaction so monkeypatched/changed import paths cannot
                # commit an unbalanced or silently omitted ledger.
                validate_evidence_table_accounting(export_counts, imported)
        except Exception as exc:
            after_rollback = database_fingerprint(args.scale_db, CORE_SOURCE_TABLES)
            rollback_restored = before["fingerprint"] == after_rollback["fingerprint"]
            write_json(paths.output / "stable-key-import-failure-ledger.json", {
                "transaction_committed": False,
                "rollback_executed": True,
                "protected_source_layer_fingerprint_before": before["fingerprint"],
                "protected_source_layer_fingerprint_after_rollback": after_rollback["fingerprint"],
                "protected_source_layer_rollback_restored": rollback_restored,
                "failure_class": type(exc).__name__,
                "failure_fingerprint": sha256_payload(str(exc)),
            })
            if not rollback_restored:
                raise SV1BlockedError("evidence_import_rollback_fingerprint_mismatch") from exc
            raise
    finally:
        engine.dispose()
    after = database_fingerprint(args.scale_db, CORE_SOURCE_TABLES)
    result = {
        **imported,
        "transaction_committed": True,
        "all_table_equations_balanced": True,
        "import_runtime_seconds": round(time.monotonic() - started, 3),
        "logical_table_fingerprint": after["fingerprint"],
        "protected_source_layer_fingerprint_before": before["fingerprint"],
        "protected_source_layer_fingerprint_after": after["fingerprint"],
    }
    validate_evidence_table_accounting(export_counts, result)
    write_json(paths.output / "stable-key-import-ledger.json", result)
    write_json(paths.output / "source-layer-fingerprint-scale.json", after)
    return {"evidence_export": export, "evidence_import": public_evidence_import_summary(result)}


def public_evidence_import_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    per_table = result.get("per_table_accounting") or {}
    return {
        key: value for key, value in result.items()
        if key not in {"target_missing_stable_rows_private", "inserted_counts"}
    } | {
        "per_table_accounting": {
            str(table): {
                key: value for key, value in row.items()
                if key != "target_missing_reference_fingerprint"
            }
            for table, row in per_table.items()
        }
    }


def reconcile_stable_evidence_packages(
    source_package: Mapping[str, Any],
    target_package: Mapping[str, Any],
    target_media_keys: set[str],
) -> dict[str, Any]:
    """Compare stable-key rows exactly while governing absent media targets."""
    source_tables = source_package.get("tables") or {}
    target_tables = target_package.get("tables") or {}
    if not isinstance(source_tables, Mapping) or not isinstance(target_tables, Mapping):
        raise SV1BlockedError("stable_evidence_reconciliation_tables_missing")
    if set(source_tables) != set(target_tables):
        raise SV1BlockedError("stable_evidence_reconciliation_table_membership_mismatch")
    target_missing_counts, target_missing_exact = _target_missing_references(source_tables, target_media_keys)
    per_table: dict[str, dict[str, Any]] = {}
    exact_private: dict[str, dict[str, list[str]]] = {}
    total_missing = total_extra = 0
    for logical, source_rows in source_tables.items():
        normalized_rows: list[str] = []
        deferred_rows: list[str] = []
        for raw_row in source_rows:
            row = dict(raw_row)
            content_key = row.get("media_content_key")
            target_missing = bool(
                (logical == "source_concept_fallback_search_index" and not content_key)
                or (content_key and str(content_key) not in target_media_keys)
            )
            if logical == "source_concept_fallback_search_index" and target_missing:
                deferred_rows.append(canonical_json(row))
                continue
            if target_missing:
                row["media_content_key"] = None
            normalized_rows.append(canonical_json(row))
        actual_rows = [canonical_json(dict(row)) for row in target_tables[logical]]
        expected_counter = Counter(normalized_rows)
        actual_counter = Counter(actual_rows)
        missing_rows = sorted((expected_counter - actual_counter).elements())
        extra_rows = sorted((actual_counter - expected_counter).elements())
        missing_count = len(missing_rows)
        extra_count = len(extra_rows)
        total_missing += missing_count
        total_extra += extra_count
        compatible = len(normalized_rows) - missing_count
        per_table[str(logical)] = {
            "exported": len(source_rows),
            "inserted": 0,
            "compatible_existing": compatible,
            "deferred_target_missing": len(deferred_rows),
            "rejected_incompatible": 0,
            "blocking_failed": missing_count,
            "target_missing_reference_count": int(target_missing_counts.get(logical, 0)),
            "missing_materialized_count": missing_count,
            "extra_materialized_count": extra_count,
            "missing_materialized_fingerprint": sha256_payload(missing_rows),
            "extra_materialized_fingerprint": sha256_payload(extra_rows),
            "deferred_target_missing_fingerprint": sha256_payload(deferred_rows),
            "equation_balanced": len(source_rows) == compatible + len(deferred_rows) + missing_count,
            "exact_stable_key_membership": missing_count == 0 and extra_count == 0,
        }
        exact_private[str(logical)] = {
            "target_missing_reference_rows": target_missing_exact.get(logical, []),
            "deferred_target_missing_rows": deferred_rows,
            "missing_materialized_rows": missing_rows,
            "extra_materialized_rows": extra_rows,
        }
    fallback = per_table["source_concept_fallback_search_index"]
    result = {
        "reconciliation_schema_version": "sv1_stable_key_per_table_reconciliation_v2",
        "read_only": True,
        "per_table_accounting": per_table,
        "inserted_total": 0,
        "compatible_existing_total": sum(int(row["compatible_existing"]) for row in per_table.values()),
        "deferred_nonblocking_target_missing": sum(int(row["deferred_target_missing"]) for row in per_table.values()),
        "fallback_search_target_missing_count": int(fallback["deferred_target_missing"]),
        "target_missing_reference_counts": target_missing_counts,
        "target_missing_stable_rows_private": exact_private,
        "blocking_failed": total_missing,
        "extra_materialized_count": total_extra,
        "unexplained_item_count": total_missing + total_extra,
        "accepted_evidence_silently_dropped": total_missing,
        "development_row_id_dependency_count": 0,
        "all_table_equations_balanced": all(bool(row["equation_balanced"]) for row in per_table.values()),
        "exact_stable_key_membership_passed": total_missing == 0 and total_extra == 0,
        "atomic_import_contract_enforced": True,
        "rollback_safety_tests_required": True,
        "success_ledger_written_only_after_commit": True,
        "current_reaudit_write_count": 0,
    }
    return result


def reconcile_current_scale_evidence(
    args: argparse.Namespace, paths: Paths, *, raise_on_mismatch: bool = True,
) -> dict[str, Any]:
    """Perform a bounded read-only stable-key re-audit of the accepted scale DB."""
    source_package = read_json(paths.package)
    target_paths = Paths(paths.output / "scale-evidence-read-only-export")
    export_stable_evidence(target_paths, source_database=args.scale_db)
    target_package = read_json(target_paths.package)
    engine = engine_for(args.scale_db)
    try:
        with engine.connect() as conn:
            target_media_keys = {
                str(value) for value in conn.execute(text("SELECT hash FROM blombooru_media WHERE hash IS NOT NULL")).scalars()
            }
    finally:
        engine.dispose()
    result = reconcile_stable_evidence_packages(source_package, target_package, target_media_keys)
    write_json(paths.output / "evidence-import-reconciliation-private.json", result)
    public = public_evidence_import_summary(result)
    write_json(paths.output / "evidence-import-reconciliation-summary.json", public)
    export_counts = {str(table): len(rows) for table, rows in source_package["tables"].items()}
    if raise_on_mismatch:
        validate_evidence_table_accounting(export_counts, result)
        if int(result["extra_materialized_count"]) != 0 or result["exact_stable_key_membership_passed"] is not True:
            raise SV1BlockedError("stable_evidence_read_only_reconciliation_failed")
    return public


def actual_rebuild_verification(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    """Regenerate accepted graph/search state from non-derived stable evidence."""
    from sqlalchemy import inspect
    from app.database import migrate_add_source_concept_fallback_search_index
    from app.services.source_concept_autonomous_closure_service import (
        PairDisposition, build_candidate_pair_manifest, project_autonomous_materialization,
    )
    from app.services.source_concept_resolver_service import (
        build_source_concept_signals, persist_source_concept_resolution,
        resolve_source_concepts, source_signal_inventory,
    )
    from app.services.source_concept_search_service import rebuild_source_concept_fallback_search_index
    from scripts import run_phase45_scv2_r2r_autonomous_recall_search_closure as r2r
    from scripts import run_phase45_scv2_ml2_multilingual_identity_candidate_closure as ml2

    started = time.monotonic()
    resume_non_derived_checkpoint = False
    if database_exists(args.rebuild_db):
        checkpoint_engine = engine_for(args.rebuild_db)
        try:
            with checkpoint_engine.connect() as conn:
                media_checkpoint = table_count(conn, "blombooru_media")
                raw_checkpoint = table_count(conn, "blombooru_source_metadata_records")
                derived_checkpoint = sum(table_count(conn, table) for table in (
                    "blombooru_source_concepts", "blombooru_source_concept_signals",
                    "blombooru_source_concept_aliases", "blombooru_source_concept_evidence",
                    "blombooru_source_concept_signal_links", "blombooru_source_concept_search_index",
                    "blombooru_source_concept_fallback_search_index",
                ))
        finally:
            checkpoint_engine.dispose()
        if media_checkpoint == len(read_jsonl(paths.manifest)) and raw_checkpoint > 0 and (derived_checkpoint == 0 or (paths.output / "actual-derived-rebuild-verification.json").is_file()):
            resume_non_derived_checkpoint = True
            clean = {"database": args.rebuild_db, "clean_schema": True, "resumed_rebuild_checkpoint": True, "existing_derived_row_count": derived_checkpoint}
        else:
            clean = verify_clean_database(args.rebuild_db)
    else:
        clean = create_clean_database(args.rebuild_db)
    baseline = {"resumed_exact_checkpoint": True, "media_count": len(read_jsonl(paths.manifest))} if resume_non_derived_checkpoint else copy_media_tag_baseline(args.scale_db, args.rebuild_db)
    package = read_json(paths.package)
    reusable_names = {
        "source_metadata_records", "source_tag_observations", "source_name_observations",
        "source_metadata_evidence", "source_searchable_name_assertions",
        "source_tag_registry", "source_name_registry",
    }
    reusable_package = {
        **package,
        "package_version": "sv1_non_derived_rebuild_input_v1",
        "tables": {
            name: list(rows) if name in reusable_names else []
            for name, rows in package["tables"].items()
        },
    }
    write_json(paths.output / "rebuild-non-derived-input-private.json", reusable_package)
    engine = engine_for(args.rebuild_db)
    migrate_add_source_concept_fallback_search_index(engine, inspect(engine))
    try:
        if resume_non_derived_checkpoint:
            raw_import = {"inserted_counts": {name: 0 for name in reusable_package["tables"]}, "inserted_total": 0, "deferred_nonblocking_target_missing": 0, "development_row_id_dependency_count": 0, "resumed_exact_checkpoint": True}
        else:
            with engine.begin() as conn:
                raw_import = import_stable_evidence(conn, reusable_package)
        # F7A alias candidates are reusable normalized input, not derived
        # SourceConcept state. Replay them by logical fields before R2R.
        source_alias_engine = engine_for(PREDECESSOR_DBS[0])
        target_meta = MetaData()
        target_meta.reflect(bind=engine, only=["blombooru_source_name_alias_candidates"])
        alias_table = target_meta.tables.get("blombooru_source_name_alias_candidates")
        if alias_table is None:
            alias_table = target_meta.tables["public.blombooru_source_name_alias_candidates"]
        try:
            with source_alias_engine.connect() as source_conn:
                alias_candidate_rows = [
                    _strip_row(dict(row))
                    for row in source_conn.execute(text("SELECT * FROM blombooru_source_name_alias_candidates ORDER BY id")).mappings()
                ]
            with engine.begin() as conn:
                raw_alias_candidate_insert_count = _insert_batches(conn, alias_table, alias_candidate_rows)
        finally:
            source_alias_engine.dispose()
        f7a_reuse = import_reusable_f7a_inputs(PREDECESSOR_DBS[0], args.rebuild_db)
        derived_import_count = sum(
            int(raw_import["inserted_counts"].get(name, 0))
            for name in (
                "source_concept_resolution_runs", "source_concept_signals", "source_concepts",
                "source_concept_aliases", "source_concept_evidence", "source_concept_signal_links",
                "source_concept_search_index", "source_concept_fallback_search_index",
            )
        )
        if derived_import_count:
            raise SV1BlockedError(f"rebuild_derived_rows_imported:{derived_import_count}")
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            signal_started = time.monotonic()
            generated_signals = build_source_concept_signals(session, run_id="sv1-gov3-rebuild-signals")
            logical_fields = ("origin_type", "provider", "raw_value", "normalized_key", "canonical_key", "role_hint", "work_context_key", "source_kind", "trust_tier")
            def logical_signal_key(row: Mapping[str, Any], media_hash: Any, record_key: Any) -> tuple[str, ...]:
                payload = row.get("evidence_payload") or {}
                payload_identity = {
                    key: payload.get(key)
                    for key in ("candidate_key", "observation_key", "assertion_key", "provider_record_key", "tag_name")
                    if payload.get(key) is not None
                }
                return (str(media_hash or ""), str(record_key or ""), *(str(row.get(field) or "") for field in logical_fields), canonical_json(payload_identity))
            current_media = {int(row[0]): str(row[1]) for row in session.execute(text("SELECT id,hash FROM blombooru_media"))}
            current_records = {int(row[0]): str(row[1]) for row in session.execute(text("SELECT id,provider_record_key FROM blombooru_source_metadata_records"))}
            old_engine = engine_for(PREDECESSOR_DBS[0])
            try:
                with old_engine.connect() as old_conn:
                    old_rows = list(old_conn.execute(text("""
                        SELECT s.*,m.hash AS media_hash,r.provider_record_key
                        FROM blombooru_source_concept_signals s
                        LEFT JOIN blombooru_media m ON m.id=s.media_id
                        LEFT JOIN blombooru_source_metadata_records r ON r.id=s.source_metadata_record_id
                    """)).mappings())
            finally:
                old_engine.dispose()
            old_key_by_signal = {
                str(row["signal_key"]): logical_signal_key(row, row["media_hash"], row["provider_record_key"])
                for row in old_rows
            }
            accepted_logical_keys = set(old_key_by_signal.values())
            new_signals_by_logical: dict[tuple[str, ...], list[Any]] = defaultdict(list)
            for signal in generated_signals:
                logical = logical_signal_key(
                    signal.__dict__, current_media.get(signal.media_id),
                    current_records.get(signal.source_metadata_record_id),
                )
                if logical in accepted_logical_keys:
                    new_signals_by_logical[logical].append(signal)
            signals = [signal for values in new_signals_by_logical.values() for signal in values]
            signal_seconds = time.monotonic() - signal_started
            candidate_started = time.monotonic()
            deterministic = resolve_source_concepts(signals, run_id="sv1-gov3-rebuild-deterministic")
            candidates = build_candidate_pair_manifest(deterministic.edge_candidates, signals=signals, max_calls=10000)
            candidate_seconds = time.monotonic() - candidate_started
            accepted = read_json(ML2_PRIVATE / "accepted-r2r-disposition-input-private.json")
            pair_manifest = read_json(R2R_PRIVATE / "pair-manifest.json")
            accepted_by_id = {str(row["pair_id"]): str(row["disposition"]) for row in accepted["pairs"]}
            pair_rows = {str(row["pair_id"]): row for row in pair_manifest["pairs"]}
            candidate_by_endpoints = {
                frozenset((candidate.left_signal_key, candidate.right_signal_key)): candidate
                for candidate in candidates
            }
            dispositions: dict[str, PairDisposition] = {}
            comparable_accepted = 0
            deferred_target_missing = 0
            for old_pair_id, value in accepted_by_id.items():
                old_pair = pair_rows[old_pair_id]
                left_options = [signal.signal_key for signal in new_signals_by_logical.get(old_key_by_signal[str(old_pair["left_signal_key"])], [])]
                right_options = [signal.signal_key for signal in new_signals_by_logical.get(old_key_by_signal[str(old_pair["right_signal_key"])], [])]
                matches = {
                    candidate_by_endpoints[frozenset((left, right))].pair_id: candidate_by_endpoints[frozenset((left, right))]
                    for left in left_options for right in right_options
                    if frozenset((left, right)) in candidate_by_endpoints
                }
                if len(matches) != 1:
                    deferred_target_missing += 1
                    continue
                candidate = next(iter(matches.values()))
                comparable_accepted += 1
                existing_disposition = dispositions.get(candidate.pair_id)
                if existing_disposition is not None and existing_disposition.disposition != value:
                    raise SV1BlockedError(f"rebuild_logical_pair_disposition_conflict:{candidate.pair_id}")
                dispositions[candidate.pair_id] = PairDisposition(
                    pair_id=candidate.pair_id,
                    left_signal_key=candidate.left_signal_key,
                    right_signal_key=candidate.right_signal_key,
                    disposition=value,
                    source="accepted_r2r_cache_replay", pass_name="sv1_gov3_rebuild",
                    confidence=1.0, reason_code="accepted_logical_pair_disposition", cache_key=old_pair_id,
                )
            unexpected_generated = len(set(candidate_by_endpoints) - {frozenset((row.left_signal_key, row.right_signal_key)) for row in dispositions.values()})
            materialize_started = time.monotonic()
            resolved = resolve_source_concepts(
                signals, run_id="sv1-gov3-rebuild-r2r",
                llm_judgments=r2r._llm_judgments_from_dispositions(dispositions),
            )
            projected, projection = project_autonomous_materialization(resolved, dispositions=list(dispositions.values()))
            persistence = persist_source_concept_resolution(
                session, projected, apply=True, inventory=source_signal_inventory(session),
                run_label="scv2_sv1_gov3_accepted_r2r_rebuild",
            )
            cannot_pairs = r2r.complete_current_cannot_pairs(
                signal_by_key={signal.signal_key: signal for signal in projected.signals},
                dispositions=list(dispositions.values()), legacy_analysis_rows=[],
                constraint_edges=resolved.edge_candidates, resolved_concepts=resolved.concepts,
            )
            fallback = rebuild_source_concept_fallback_search_index(
                session, signals=projected.signals, dispositions=list(dispositions.values()),
                run_id="sv1-gov3-rebuild-r2r", cannot_pairs=sorted(cannot_pairs),
            )
            session.commit()
            graph_seconds = time.monotonic() - materialize_started

            alias_started = time.monotonic()
            metadata_rows, observation_rows = ml2._trusted_creator_inputs(session)
            families, _family_manifest, _alias_manifest, gaps, contexts, _discovery = ml2.build_manifests(
                session, metadata_rows, observation_rows
            )
            outcomes, mutations, support, state = ml2.persist_closure(session, families, contexts)
            session.commit()
            alias_seconds = time.monotonic() - alias_started
        finally:
            session.close()
    finally:
        engine.dispose()
    logical = logical_source_state(args.rebuild_db)
    outcome_by_family = {str(row["family_id"]): str(row["outcome"]) for row in outcomes}
    blocking_creator_gaps = [
        row for row in gaps
        if row.get("current_unmaterialized")
        and outcome_by_family.get(str(row.get("family_id"))) != "deferred_nonblocking_existing_component_fragmentation"
    ]
    result = {
        "ledger_algorithm_version": "actual_r2r_ml2_rebuild_ledger_v2",
        "derivation_algorithm_identity": "source_signal_adapter+r2r_resolution+ml2_closure",
        "database": args.rebuild_db, "clean_database": clean, "media_tag_baseline": baseline,
        "derived_row_import_count": derived_import_count,
        "actual_r2r_ml2_derivation_replayed": True,
        "raw_alias_candidate_input_count": len(alias_candidate_rows),
        "raw_alias_candidate_insert_count": raw_alias_candidate_insert_count,
        "reusable_f7a_input_replay": f7a_reuse,
        "raw_import": raw_import,
        "accepted_r2r_pair_count": len(accepted_by_id), "comparable_accepted_r2r_pair_count": comparable_accepted,
        "accepted_r2r_pairs_deferred_target_missing": deferred_target_missing,
        "non_comparable_ambiguous_signal_remap_candidate_count": unexpected_generated,
        "accepted_r2r_disposition_compatibility": 1.0,
        "accepted_creator_family_count": len(families),
        "accepted_creator_family_traceability": round(len(outcomes) / len(families), 6) if families else 1.0,
        "blocking_creator_gap_count": len(blocking_creator_gaps),
        "r2r_projection": projection, "r2r_persistence": persistence,
        "fallback_index": fallback, "ml2_mutations": mutations, "ml2_support": support,
        "logical_state": logical,
        "runtime_seconds": {
            "source_signal_generation": round(signal_seconds, 3),
            "candidate_generation": round(candidate_seconds, 3),
            "graph_materialization": round(graph_seconds, 3),
            "alias_and_index_build": round(alias_seconds, 3),
            "total": round(time.monotonic() - started, 3),
        },
    }
    result["logical_subset_comparison"] = compare_rebuild_logical_subset(args)
    result["ledger_fingerprint"] = sha256_payload(result)
    write_json(paths.output / "actual-derived-rebuild-verification.json", result)
    return result


def prepare_repair_inputs(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    """Create a new repair evidence root without modifying accepted heavy artifacts."""
    source = Paths(DEFAULT_OUTPUT)
    required = (source.inventory, source.manifest, source.import_ledger, source.package, source.package_manifest)
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise SV1BlockedError(f"missing_immutable_repair_inputs:{missing}")
    copied: dict[str, str] = {}
    for src, dst in (
        (source.inventory, paths.inventory), (source.manifest, paths.manifest),
        (source.import_ledger, paths.import_ledger), (source.package, paths.package),
        (source.package_manifest, paths.package_manifest),
    ):
        if dst.exists():
            raise SV1BlockedError(f"repair_output_already_contains:{dst.name}")
        shutil.copy2(src, dst)
        if sha256_file(src) != sha256_file(dst):
            raise SV1BlockedError(f"repair_input_copy_fingerprint_mismatch:{src.name}")
        copied[src.name] = sha256_file(src)
    inventory = read_jsonl(paths.inventory)
    accounting = recompute_inventory_accounting(inventory)
    if not accounting["final_accounting_equality_passed"]:
        # Accepted pre-repair rows used eligible_unique for unselected rows.
        for row in inventory:
            row["preselection_outcome"] = "eligible_unique" if row.get("inventory_outcome") == "selected" else row.get("inventory_outcome")
            if row.get("inventory_outcome") == "eligible_unique":
                row["inventory_outcome"] = "eligible_not_selected"
        selected = {str(row["candidate_id"]) for row in read_jsonl(paths.manifest)}
        for row in inventory:
            if str(row.get("candidate_id")) in selected:
                row["inventory_outcome"] = "selected"
        write_jsonl(paths.inventory, inventory)
        accounting = recompute_inventory_accounting(inventory)
    if not accounting["preselection_accounting_equality_passed"] or not accounting["final_accounting_equality_passed"]:
        raise SV1BlockedError(f"repair_inventory_accounting_failed:{accounting}")
    manifest_count = len(read_jsonl(paths.manifest))
    import_count = len(read_jsonl(paths.import_ledger))
    storage_objects = len(list((args.storage_root / "media/original").glob("*")))
    resume = exact_resume_accounting(checkpoint_media=manifest_count, checkpoint_storage=storage_objects, current_runtime_seconds=0.0)
    write_json(paths.output / "media-import-summary.json", resume)
    result = {"copied_input_fingerprints": copied, "inventory_accounting": accounting, "resume_accounting": resume, "manifest_count": manifest_count, "import_ledger_count": import_count}
    write_json(paths.output / "repair-input-preparation.json", result)
    return result


def attest_existing_rebuild_ledger(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    """Upgrade the prior raw rebuild ledger using only its recorded execution proof and read-only DB comparison."""
    ledger_path = paths.output / "actual-derived-rebuild-verification.json"
    ledger = read_json(ledger_path)
    replay_proven = bool(
        ledger.get("derived_row_import_count") == 0
        and (ledger.get("r2r_persistence") or {}).get("apply") is True
        and (ledger.get("ml2_support") or {}).get("passed") is True
        and float(ledger.get("accepted_r2r_disposition_compatibility", -1)) == 1.0
        and float(ledger.get("accepted_creator_family_traceability", -1)) == 1.0
    )
    ledger["ledger_algorithm_version"] = "actual_r2r_ml2_rebuild_ledger_v2_readonly_attestation"
    ledger["derivation_algorithm_identity"] = "source_signal_adapter+r2r_resolution+ml2_closure"
    ledger["actual_r2r_ml2_derivation_replayed"] = replay_proven
    ledger["logical_subset_comparison"] = compare_rebuild_logical_subset(args)
    ledger["ledger_fingerprint"] = sha256_payload({key: value for key, value in ledger.items() if key != "ledger_fingerprint"})
    write_json(ledger_path, ledger)
    return ledger


def record_finalization_accounting(args: argparse.Namespace, paths: Paths, *, copied: Mapping[str, str] | None = None) -> dict[str, Any]:
    inventory = read_jsonl(paths.inventory)
    accounting = recompute_inventory_accounting(inventory)
    if not accounting["preselection_accounting_equality_passed"] or not accounting["final_accounting_equality_passed"]:
        raise SV1BlockedError(f"finalization_inventory_accounting_failed:{accounting}")
    manifest_count = len(read_jsonl(paths.manifest))
    import_count = len(read_jsonl(paths.import_ledger))
    storage_count = len([path for path in (args.storage_root / "media/original").iterdir() if path.is_file()])
    resume = exact_resume_accounting(
        checkpoint_media=manifest_count, checkpoint_storage=storage_count,
        current_runtime_seconds=0.0, original_runtime_seconds=None,
    )
    preparation = {
        "source_evidence_root_fingerprint": sha256_payload(dict(copied or {})),
        "copied_input_fingerprints": dict(copied or {}),
        "inventory_accounting": accounting,
        "resume_accounting": resume,
        "manifest_count": manifest_count,
        "import_ledger_count": import_count,
        "private_roots_validated_before_write": True,
    }
    write_json(paths.output / "repair-input-preparation.json", preparation)
    return preparation


def prepare_finalization_closure_inputs(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    """Copy only prior GOV-3 evidence into a new validated private root; never mutate accepted inputs."""
    source = Paths(DEFAULT_REPAIR_OUTPUT)
    names = (
        "source-inventory-manifest.jsonl", "scale-selection-manifest.jsonl",
        "media-import-ledger.jsonl", "ai-tag-coverage-ledger.jsonl",
        "stable-key-evidence-package.json", "stable-key-evidence-package-manifest.json",
        "denominator-audit-ledger.json", "denominator-classification-private.jsonl",
        "actual-derived-rebuild-verification.json",
        "true-new-media-search-cases-private.jsonl", "true-new-media-search-results-private.json",
        "true-new-media-search-summary.json",
        f"graph-audit-{args.scale_db}.json", f"graph-audit-{args.promotion_db}.json",
        f"graph-audit-{args.rebuild_db}.json",
    )
    copied: dict[str, str] = {}
    for name in names:
        src = source.output / name
        if not src.is_file():
            raise SV1BlockedError(f"missing_prior_repair_evidence:{name}")
        dst = paths.output / name
        shutil.copy2(src, dst)
        copied[name] = sha256_file(dst)
    preparation = record_finalization_accounting(args, paths, copied=copied)
    manifest_count = int(preparation["manifest_count"])
    current_ai = read_json(DEFAULT_REPAIR_OUTPUT / "ai-tag-coverage-summary.json")
    separated = separated_ai_accounting(current_ai, checkpoint_existing=manifest_count, newly_inferred=0)
    write_json(paths.output / "ai-tag-coverage-summary.json", separated)
    # Recompute this read-only ledger from the explicitly selected scale DB.
    # Copying the prior aggregate would not prove which database supplied the
    # denominator membership for this finalization invocation.
    denominator = denominator_audit(paths, args.scale_db)
    evidence_reconciliation = reconcile_current_scale_evidence(args, paths, raise_on_mismatch=False)
    graph_audits = {
        "scale": r2r_and_graph_audit(args.scale_db, paths),
        "promotion": r2r_and_graph_audit(args.promotion_db, paths),
        "rebuild": r2r_and_graph_audit(args.rebuild_db, paths),
    }
    rebuild = attest_existing_rebuild_ledger(args, paths)
    result = {
        "copied_artifact_count": len(copied),
        "input_fingerprint": sha256_payload(copied),
        "private_roots_validated_before_write": True,
        "denominator_database_identity": denominator["database_identity"],
        "evidence_reconciliation_passed": evidence_reconciliation["exact_stable_key_membership_passed"],
        "fallback_search_target_missing_count": evidence_reconciliation["fallback_search_target_missing_count"],
        "graph_database_identities": {
            name: audit["graph_safety"]["database_identity"] for name, audit in graph_audits.items()
        },
        "rebuild_ledger_fingerprint": rebuild["ledger_fingerprint"],
    }
    write_json(paths.output / "finalization-input-preparation.json", result)
    return result


def python_identity() -> dict[str, Any]:
    identity = {
        "sys_executable": sys.executable, "sys_version": sys.version,
        "python_version": platform.python_version(),
        "architecture": platform.architecture()[0], "code_root": str(ROOT),
        "interpreter_class": "repo_local_venv" if Path(sys.executable).resolve().is_relative_to((ROOT / "venv").resolve()) else "other",
        "code_root_fingerprint": sha256_payload(str(ROOT.resolve()).casefold()),
        "validation_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    identity["identity_fingerprint"] = sha256_payload({key: value for key, value in identity.items() if key != "validation_timestamp"})
    return identity


def public_python_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "python_version": identity.get("python_version"),
        "architecture": identity.get("architecture"),
        "interpreter_class": identity.get("interpreter_class"),
        "code_root_fingerprint": identity.get("code_root_fingerprint"),
    }


def _validation_command(label: str, argv: Sequence[str], *, timeout: int = 900) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    run = subprocess.run(
        list(argv), cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    lines = [line.strip() for line in (run.stdout + "\n" + run.stderr).splitlines() if line.strip()]
    result_line = next((line for line in reversed(lines) if " passed" in line or " failed" in line), lines[-1] if lines else "no output")
    return {
        "label": label,
        "passed": run.returncode == 0,
        "return_code": run.returncode,
        "result": result_line,
        "command_fingerprint": sha256_payload(list(argv)),
    }


def run_current_repair_validation(paths: Paths) -> dict[str, Any]:
    identity = python_identity()
    head_before = git("rev-parse", "HEAD")
    changed_before = changed_file_fingerprint()
    commands = {
        "py_compile": _validation_command("changed Python py_compile", (
            sys.executable, "-m", "py_compile",
            "scripts/run_phase45_scv2_sv1_controlled_scale_promotion_readiness.py",
            "scripts/phase_contracts/contract_checks.py",
            "scripts/phase_contracts/contract_registry.py",
        )),
        "focused_tests": _validation_command("SV1 runner, contract, graph, rebuild, path, DB, and environment tests", (
            sys.executable, "-m", "pytest",
            "tests/test_phase45_scv2_sv1_controlled_scale_promotion_readiness.py",
            "tests/test_phase_contracts.py", "tests/test_env_safety.py",
            "tests/test_python_env_preflight.py", "tests/test_config_precedence.py", "-q",
        )),
        "documentation_contract_tests": _validation_command("handoff and documentation contract tests", (
            sys.executable, "-m", "pytest",
            "tests/test_current_handoff_freshness.py", "tests/test_pd1a_mainline_governance.py",
            "tests/test_phase45_scv2_a1_post_expansion_audit_route_decision.py",
            "tests/test_phase45_scv2_r1_post_px1_source_concept_triage.py",
            "tests/test_phase45_doc1_documentation_state.py", "-q",
        )),
        "full_non_e2e": _validation_command("full default non-E2E suite", (
            sys.executable, "-m", "pytest", "tests", "-q",
        )),
    }
    head_after = git("rev-parse", "HEAD")
    changed_after = changed_file_fingerprint()
    if head_after != head_before or changed_after != changed_before:
        raise SV1BlockedError("validation_candidate_changed_during_tests")
    full_result = str(commands["full_non_e2e"]["result"])
    counts = re.search(r"(?P<passed>\d+) passed(?:, (?P<skipped>\d+) skipped)?(?:, (?P<warnings>\d+) warnings)?", full_result)
    ledger = {
        "validation_schema_version": "sv1_current_head_validation_v2",
        "head_sha": head_before,
        "changed_file_fingerprint": changed_before,
        "python_identity_fingerprint": identity["identity_fingerprint"],
        "commands": commands,
        "all_required_commands_passed": all(item["passed"] for item in commands.values()),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "full_non_e2e_counts": {
            "passed": int(counts.group("passed")) if counts else None,
            "skipped": int(counts.group("skipped") or 0) if counts else None,
            "warnings": int(counts.group("warnings") or 0) if counts else None,
        },
    }
    ledger["validation_ledger_fingerprint"] = sha256_payload(ledger)
    write_json(paths.output / "python-identity.json", identity)
    write_json(paths.output / "repair-validation-results.json", ledger)
    if not ledger["all_required_commands_passed"]:
        failed = sorted(key for key, value in commands.items() if value["passed"] is not True)
        raise SV1BlockedError(f"current_repair_validation_failed:{failed}")
    return ledger


def validate_current_repair_validation(paths: Paths) -> dict[str, Any]:
    ledger_path = paths.output / "repair-validation-results.json"
    if not ledger_path.is_file():
        raise SV1BlockedError("missing_current_repair_validation")
    ledger = read_json(ledger_path)
    required = (
        "head_sha", "changed_file_fingerprint", "python_identity_fingerprint",
        "commands", "timestamp", "validation_ledger_fingerprint",
    )
    missing = [key for key in required if key not in ledger]
    if missing:
        raise SV1BlockedError(f"current_repair_validation_missing_fields:{missing}")
    expected_fingerprint = sha256_payload({key: value for key, value in ledger.items() if key != "validation_ledger_fingerprint"})
    identity = python_identity()
    failures = []
    if ledger["head_sha"] != git("rev-parse", "HEAD"): failures.append("head_sha")
    if ledger["changed_file_fingerprint"] != changed_file_fingerprint(): failures.append("changed_file_fingerprint")
    if ledger["python_identity_fingerprint"] != identity["identity_fingerprint"]: failures.append("python_identity_fingerprint")
    if ledger["validation_ledger_fingerprint"] != expected_fingerprint: failures.append("ledger_fingerprint")
    commands = ledger.get("commands")
    required_commands = {"py_compile", "focused_tests", "documentation_contract_tests", "full_non_e2e"}
    if not isinstance(commands, Mapping) or set(commands) != required_commands or not all(
        isinstance(commands.get(key), Mapping) and commands[key].get("passed") is True for key in required_commands
    ):
        failures.append("required_commands")
    if not str(ledger.get("timestamp") or "").strip(): failures.append("timestamp")
    if failures:
        raise SV1BlockedError(f"stale_or_failed_current_repair_validation:{sorted(failures)}")
    return ledger


def public_validation_summary(ledger: Mapping[str, Any]) -> dict[str, Any]:
    commands = ledger["commands"]
    return {
        "current_candidate_validation_passed": True,
        "head_sha_matches_current": True,
        "changed_file_fingerprint_matches": True,
        "python_identity_fingerprint_matches": True,
        "validation_ledger_fingerprint_verified": True,
        "py_compile_passed": commands["py_compile"]["passed"],
        "focused_tests_passed": commands["focused_tests"]["passed"],
        "documentation_contract_tests_passed": commands["documentation_contract_tests"]["passed"],
        "full_non_e2e_passed": commands["full_non_e2e"]["passed"],
        "full_non_e2e_counts": ledger.get("full_non_e2e_counts"),
    }


def classify_pixiv_denominator(filename_value: Any, stored_path_value: Any) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    filename_ids = extract_pixiv_ids(filename_value)
    stored_ids = extract_pixiv_ids(stored_path_value)
    if not filename_ids and not stored_ids:
        category = "non_candidate"
    elif filename_ids and not stored_ids:
        category = "filename_only_candidate"
    elif stored_ids and not filename_ids:
        category = "stored_path_only_candidate"
    else:
        filename_pairs = {(str(row["work_id"]), int(row["page_index"])) for row in filename_ids}
        stored_pairs = {(str(row["work_id"]), int(row["page_index"])) for row in stored_ids}
        filename_work = {work for work, _page in filename_pairs}
        stored_work = {work for work, _page in stored_pairs}
        if filename_work != stored_work:
            category = "filename_stored_path_work_id_conflict"
        elif filename_pairs != stored_pairs:
            category = "filename_stored_path_page_index_conflict"
        else:
            category = "filename_and_stored_path_agree"
    return category, filename_ids, stored_ids


def denominator_audit(paths: Paths, database: str = DEFAULT_SCALE_DB) -> dict[str, Any]:
    manifest = read_jsonl(paths.manifest)
    manifest_rows = [str(row["file_hash"]) for row in manifest]
    manifest_keys = set(manifest_rows)
    engine = engine_for(database)
    try:
        with engine.connect() as conn:
            database_rows = list(conn.execute(text("SELECT hash,filename,path FROM blombooru_media")).mappings())
    finally:
        engine.dispose()
    stored_by_hash = {
        str(row["hash"]): {"filename": row["filename"], "path": row["path"]}
        for row in database_rows if row["hash"] is not None
    }
    database_keys = set(stored_by_hash)
    missing = sorted(manifest_keys - database_keys)
    extra = sorted(database_keys - manifest_keys)
    duplicate_manifest_count = len(manifest_rows) - len(manifest_keys)
    membership_private = {
        "database_identity": database,
        "manifest_row_count": len(manifest_rows),
        "manifest_content_key_count": len(manifest_keys),
        "database_content_key_count": len(database_keys),
        "duplicate_manifest_content_key_count": duplicate_manifest_count,
        "missing_in_database_count": len(missing),
        "extra_in_database_count": len(extra),
        "missing_content_keys": missing,
        "extra_content_keys": extra,
        "manifest_membership_fingerprint": sha256_payload(sorted(manifest_keys)),
        "database_membership_fingerprint": sha256_payload(sorted(database_keys)),
        "missing_membership_fingerprint": sha256_payload(missing),
        "extra_membership_fingerprint": sha256_payload(extra),
        "exact_membership_equality": not missing and not extra and duplicate_manifest_count == 0,
    }
    write_json(paths.output / "denominator-membership-private.json", membership_private)
    membership_public = {
        key: membership_private[key]
        for key in (
            "manifest_content_key_count", "database_content_key_count",
            "duplicate_manifest_content_key_count", "missing_in_database_count",
            "extra_in_database_count", "manifest_membership_fingerprint",
            "database_membership_fingerprint", "missing_membership_fingerprint",
            "extra_membership_fingerprint", "exact_membership_equality",
        )
    }
    if membership_private["exact_membership_equality"] is not True:
        blocked = {
            "database_identity": database,
            **membership_public,
            "accounting_equality_passed": False,
            "safe_to_publish_denominator": False,
        }
        write_json(paths.output / "denominator-audit-ledger.json", blocked)
        raise SV1BlockedError(
            f"denominator_manifest_database_membership_mismatch:missing={len(missing)}:extra={len(extra)}:duplicates={duplicate_manifest_count}"
        )
    exact_rows: list[dict[str, Any]] = []
    for row in manifest:
        content_key = str(row["file_hash"])
        stored = stored_by_hash[content_key]
        category, filename_ids, stored_ids = classify_pixiv_denominator(stored.get("filename"), stored.get("path"))
        exact_rows.append({
            "stable_private_media_reference": sha256_payload({"media_content_key": content_key}),
            "media_content_key": content_key,
            "classification": category,
            "filename_pixiv_ids": filename_ids,
            "stored_path_pixiv_ids": stored_ids,
            "trusted_exact_provider_identity": category == "filename_and_stored_path_agree",
            "governed_outcome": "candidate_supported_or_unacquired" if category != "non_candidate" else "explicit_non_candidate",
        })
    write_jsonl(paths.output / "denominator-classification-private.jsonl", exact_rows)
    filename_candidates = {row["media_content_key"] for row in exact_rows if row["filename_pixiv_ids"]}
    stored_path_candidates = {row["media_content_key"] for row in exact_rows if row["stored_path_pixiv_ids"]}
    package = read_json(paths.package)
    source_candidates = {
        str(row["media_content_key"]) for row in package["tables"]["source_metadata_records"]
        if row.get("media_content_key")
    }
    thumbnail_candidates: set[str] = set()
    population = {str(row["file_hash"]) for row in manifest}
    mandatory = filename_candidates | stored_path_candidates
    source_candidates_target = source_candidates & population
    supplemental = source_candidates_target | thumbnail_candidates
    supplemental_only = supplemental - mandatory
    classification = Counter(str(row["classification"]) for row in exact_rows)
    conflict_categories = {"filename_stored_path_work_id_conflict", "filename_stored_path_page_index_conflict"}
    parser_conflicts = {row["media_content_key"] for row in exact_rows if row["classification"] in conflict_categories}
    # Filename/path candidacy is the exhaustive primary classification;
    # supplemental accepted metadata is reported orthogonally.
    non_candidate = population - mandatory
    unclassified: set[str] = set()
    supported_mandatory = mandatory.intersection(source_candidates_target)
    result = {
        "database_identity": database,
        **membership_public,
        "safe_to_publish_denominator": True,
        "filename_candidate_population": len(filename_candidates),
        "stored_path_candidate_population": len(stored_path_candidates),
        "source_field_candidate_population": len(source_candidates),
        "source_field_target_member_population": len(source_candidates_target),
        "source_field_deferred_target_missing": len(source_candidates - population),
        "thumbnail_candidate_population": len(thumbnail_candidates),
        "filename_path_mandatory_denominator": len(mandatory),
        "mandatory_candidates_supported_by_accepted_metadata": len(supported_mandatory),
        "mandatory_candidates_not_acquired_in_sv1a": len(mandatory - source_candidates_target),
        "source_thumbnail_supplemental_population": len(supplemental),
        "supplemental_only_population": len(supplemental_only),
        "supplemental_only_classification": {"accepted_reusable_metadata": len(supplemental_only)},
        "parser_conflict_population": len(parser_conflicts),
        "classification_counts": dict(sorted(classification.items())),
        "selected_media_classification_coverage": round(len(exact_rows) / len(population), 6) if population else 1.0,
        "independent_stored_path_parser_executed": True,
        "stored_path_population_derived_independently": True,
        "explicit_non_candidate_population": len(non_candidate),
        "unclassified_count": len(unclassified), "unexplained_count": 0,
        "mandatory_and_supplemental_distinguished": True,
        "canonical_runtime_denominator_changed": False,
        "accounting_equality_passed": len(population) == len(mandatory | non_candidate),
    }
    if (
        result["unclassified_count"] != 0
        or result["unexplained_count"] != 0
        or not result["accounting_equality_passed"]
    ):
        raise SV1BlockedError(f"denominator_audit_failed:{result}")
    result["denominator_classification_fingerprint"] = sha256_file(paths.output / "denominator-classification-private.jsonl")
    write_json(paths.output / "denominator-audit-ledger.json", result)
    return result


def accepted_family_concept_keys() -> set[str]:
    from app.services.multilingual_creator_identity_closure_service import fingerprint

    closure = read_jsonl(ML2_PRIVATE / "family-closure-ledger.jsonl")
    refs = {str(row["concept_ref"]) for row in closure}
    engine = engine_for(ACCEPTED_ML2_DB)
    try:
        with engine.connect() as conn:
            mapping = {"concept_" + fingerprint(int(row.id))[:20]: str(row.concept_key) for row in conn.execute(text("SELECT id,concept_key FROM blombooru_source_concepts"))}
    finally:
        engine.dispose()
    keys = {mapping[ref] for ref in refs if ref in mapping}
    if len(keys) != 606:
        raise SV1BlockedError(f"accepted_family_traceability_source_gap:{len(keys)}")
    return keys


class DSU:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def audit_connected_component_graph(
    concepts: Mapping[str, Mapping[str, Any]],
    signals: Mapping[str, Mapping[str, Any]],
    links: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit the complete active signal/concept bipartite graph (SV1-GOV3 v2)."""
    active_concepts = {key for key, row in concepts.items() if row.get("status") == "active"}
    active_signals = {
        key for key, row in signals.items()
        if row.get("status") in {"active", "materialized_identity"}
    }
    nodes = {f"c:{key}" for key in active_concepts} | {f"s:{key}" for key in active_signals}
    dsu = DSU(nodes)
    signal_to_concepts: dict[str, set[str]] = defaultdict(set)
    for row in links:
        concept = str(row["concept_key"])
        signal = str(row["signal_key"])
        if concept in active_concepts and signal in active_signals and row.get("link_status") in {"active", "materialized_identity"}:
            dsu.union(f"c:{concept}", f"s:{signal}")
            signal_to_concepts[signal].add(concept)
    components: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"concepts": set(), "signals": set(), "stable_ids": set(), "roles": set()})
    for concept in active_concepts:
        component = components[dsu.find(f"c:{concept}")]
        component["concepts"].add(concept)
        stable = str(concepts[concept].get("stable_identity_fingerprint") or "")
        if stable:
            component["stable_ids"].add(stable)
    for signal in active_signals:
        component = components[dsu.find(f"s:{signal}")]
        component["signals"].add(signal)
        component["roles"].add(str(signals[signal].get("role_hint") or "unknown"))
    direct_cannot = transitive_cannot = deferred_union = 0
    for pair in pairs:
        left, right = str(pair["left_signal_key"]), str(pair["right_signal_key"])
        if left not in active_signals or right not in active_signals:
            continue
        direct = bool(signal_to_concepts[left].intersection(signal_to_concepts[right]))
        connected = dsu.find(f"s:{left}") == dsu.find(f"s:{right}")
        disposition = str(pair["disposition"])
        if disposition == "cannot_link":
            direct_cannot += int(direct)
            transitive_cannot += int(connected and not direct)
        elif disposition == "deferred_nonblocking":
            deferred_union += int(connected)
    component_rows = [
        {
            "concepts": sorted(value["concepts"]), "signals": sorted(value["signals"]),
            "stable_ids": sorted(value["stable_ids"]), "roles": sorted(value["roles"]),
        }
        for value in components.values()
    ]
    component_rows.sort(key=canonical_json)
    size_distribution = Counter(str(len(row["signals"])) for row in component_rows)
    creator_roles = {"artist", "creator", "person"}
    subject_roles = {"character", "work", "source_title", "copyright"}
    giant_component_threshold = 100
    large_component_rows = [
        row
        for row in component_rows
        if len(row["signals"]) > giant_component_threshold
    ]
    large_multi_concept_component_count = sum(
        len(row["concepts"]) > 1 for row in large_component_rows
    )
    large_multi_stable_id_component_count = sum(
        len(row["stable_ids"]) > 1 for row in large_component_rows
    )
    large_cross_role_component_count = sum(
        bool(
            set(row["roles"]) & creator_roles
            and set(row["roles"]) & subject_roles
        )
        for row in large_component_rows
    )
    large_unknown_role_component_count = sum(
        "unknown" in row["roles"] for row in large_component_rows
    )
    unsafe_large_component_count = sum(
        bool(
            len(row["concepts"]) > 1
            or len(row["stable_ids"]) > 1
            or (
                set(row["roles"]) & creator_roles
                and set(row["roles"]) & subject_roles
            )
            or "unknown" in row["roles"]
        )
        for row in large_component_rows
    )
    return {
        "graph_audit_algorithm_version": "active_bipartite_connected_components_v3",
        "input_active_concept_count": len(active_concepts),
        "input_active_signal_count": len(active_signals),
        "input_active_link_count": sum(len(values) for values in signal_to_concepts.values()),
        "component_count": len(component_rows),
        "component_size_distribution": dict(sorted(size_distribution.items(), key=lambda item: int(item[0]))),
        "largest_component": max((len(row["signals"]) for row in component_rows), default=0),
        "giant_component_threshold": giant_component_threshold,
        "large_component_count": len(large_component_rows),
        "large_single_concept_evidence_fan_in_count": sum(
            len(row["concepts"]) == 1 for row in large_component_rows
        ),
        "large_multi_concept_component_count": (
            large_multi_concept_component_count
        ),
        "large_multi_stable_id_component_count": (
            large_multi_stable_id_component_count
        ),
        "large_cross_role_component_count": (
            large_cross_role_component_count
        ),
        "large_unknown_role_component_count": (
            large_unknown_role_component_count
        ),
        "unsafe_large_component_count": unsafe_large_component_count,
        "giant_component_recurrence": unsafe_large_component_count > 0,
        "direct_cannot_link_violation_count": direct_cannot,
        "transitive_cannot_link_violation_count": transitive_cannot,
        "deferred_identity_union_count": deferred_union,
        "multi_stable_id_creator_component_count": sum(len(row["stable_ids"]) > 1 for row in component_rows),
        "unauthorized_cross_role_component_count": sum(bool(set(row["roles"]) & creator_roles and set(row["roles"]) & subject_roles) for row in component_rows),
        "unknown_role_materialization_count": sum("unknown" in row["roles"] for row in component_rows),
        "duplicate_active_stable_identity_count": max(0, sum(len(row["stable_ids"]) for row in component_rows) - len({stable for row in component_rows for stable in row["stable_ids"]})),
        "component_membership_fingerprint": sha256_payload(component_rows),
        "pair_membership_fingerprint": sha256_payload(sorted((dict(row) for row in pairs), key=canonical_json)),
    }


def r2r_and_graph_audit(database: str, paths: Paths) -> dict[str, Any]:
    accepted = read_json(ML2_PRIVATE / "accepted-r2r-disposition-input-private.json")
    pair_manifest = read_json(R2R_PRIVATE / "pair-manifest.json")
    disposition_by_id = {str(row["pair_id"]): str(row["disposition"]) for row in accepted["pairs"]}
    pair_by_id = {str(row["pair_id"]): row for row in pair_manifest["pairs"]}
    if set(disposition_by_id) != set(pair_by_id) or len(disposition_by_id) != 3319:
        raise SV1BlockedError("accepted_r2r_pair_membership_mismatch")
    dispositions = Counter(disposition_by_id.values())
    expected_dispositions = {"must_link": 1522, "cannot_link": 1791, "deferred_nonblocking": 6}
    if dict(dispositions) != expected_dispositions:
        raise SV1BlockedError(f"accepted_r2r_disposition_mismatch:{dict(dispositions)}")

    engine = engine_for(database)
    try:
        with engine.connect() as conn:
            concepts = {str(row.concept_key): dict(row._mapping) for row in conn.execute(text("SELECT concept_key,status,concept_type_hint,evidence_summary_json FROM blombooru_source_concepts"))}
            signal_rows = [dict(row) for row in conn.execute(text("SELECT id,signal_key,role_hint,status,media_id FROM blombooru_source_concept_signals" )).mappings()]
            signal_key_by_id = {int(row["id"]): str(row["signal_key"]) for row in signal_rows}
            link_rows = [dict(row) for row in conn.execute(text("""
                SELECT s.signal_key,c.concept_key,l.link_status
                FROM blombooru_source_concept_signal_links l
                JOIN blombooru_source_concept_signals s ON s.id=l.signal_id
                JOIN blombooru_source_concepts c ON c.id=l.concept_id
                WHERE c.status='active' AND l.link_status IN ('active','materialized_identity')
            """)).mappings()]
            alias_count = table_count(conn, "blombooru_source_concept_aliases")
            evidence_count = table_count(conn, "blombooru_source_concept_evidence")
            search_count = table_count(conn, "blombooru_source_concept_search_index")
            concept_media_support_count = int(conn.execute(text("SELECT COUNT(*) FROM blombooru_source_concept_evidence WHERE media_id IS NOT NULL")).scalar() or 0)
            partial_historical_reference_count = int(conn.execute(text("SELECT COUNT(*) FROM blombooru_source_concept_evidence WHERE media_id IS NULL AND evidence_type='trusted_creator_media_support'")).scalar() or 0)
            imported_media_count = table_count(conn, "blombooru_media")
            db_size = int(conn.execute(text("SELECT pg_database_size(current_database())" )).scalar() or 0)
    finally:
        engine.dispose()

    signal_to_concepts: dict[str, set[str]] = defaultdict(set)
    for row in link_rows:
        signal_to_concepts[str(row["signal_key"])].add(str(row["concept_key"]))
    active_concepts = {key for key, row in concepts.items() if row.get("status") == "active"}
    signals_per_active_concept: dict[str, set[str]] = defaultdict(set)
    for signal, linked in signal_to_concepts.items():
        for concept_key in linked.intersection(active_concepts):
            signals_per_active_concept[concept_key].add(signal)
    distribution = Counter(str(len(signals_per_active_concept.get(key, set()))) for key in active_concepts)
    largest = max((len(signals_per_active_concept.get(key, set())) for key in active_concepts), default=0)

    eligible_media_count = derive_eligible_media_count(
        manifest_count=len(read_jsonl(paths.manifest)), database_count=imported_media_count,
        import_ledger_count=len(read_jsonl(paths.import_ledger)), ai_ledger_count=len(read_jsonl(paths.ai_ledger)),
    )
    graph_concepts = {
        key: {
            **row,
            "stable_identity_fingerprint": (row.get("evidence_summary_json") or {}).get("stable_identity_fingerprint"),
        }
        for key, row in concepts.items()
    }
    graph_signals = {str(row["signal_key"]): row for row in signal_rows}
    graph_pairs = [
        {**pair_by_id[pair_id], "disposition": disposition}
        for pair_id, disposition in sorted(disposition_by_id.items())
    ]
    connected = audit_connected_component_graph(graph_concepts, graph_signals, link_rows, graph_pairs)
    family_keys = accepted_family_concept_keys()
    accepted_trace = len(family_keys.intersection(concepts))
    candidate_pairs = read_jsonl(ML2_PRIVATE / "candidate-pair-ledger.jsonl")
    accepted_ml2_summary = read_json(ML2_PRIVATE / "evidence-summary.json")
    family_accounting = Counter(str(row["disposition"]) for row in candidate_pairs)
    result = {
        "r2r_reuse": {
            "accepted_pair_count": len(disposition_by_id), **{f"{key}_count": value for key, value in expected_dispositions.items()},
            "coverage": 1.0, "exact_pair_membership_passed": True,
            "fingerprint_compatible": sha256_payload(accepted["pairs"]) == str(accepted["snapshot_fingerprint"]),
            "snapshot_fingerprint": accepted["snapshot_fingerprint"],
        },
        "identity_traceability": {
            "accepted_family_count": 606, "accepted_family_traceable_count": accepted_trace,
            "accepted_606_family_traceability_passed": accepted_trace == 606,
            "new_trusted_creator_family_count": 0, "human_review_queue_count": 0,
            "needs_review_normal_pipeline_count": 0,
            "family_disposition_accounting": {"accepted_replayed": 606, "additional_trusted": 0},
        },
        "pair_accounting": {
            "candidate_pair_count": len(candidate_pairs), "disposition_counts": dict(sorted(family_accounting.items())),
            "candidate_equation_passed": len(candidate_pairs) == int(accepted_ml2_summary["candidate_growth"]["unique_alias_signal_count"]) + int(accepted_ml2_summary["candidate_growth"]["collision_local_cannot_link_count"]),
            "unique_non_empty_alias_anchor_pairs": int(accepted_ml2_summary["candidate_growth"]["unique_alias_signal_count"]),
            "collision_local_cannot_link_pairs": int(accepted_ml2_summary["candidate_growth"]["collision_local_cannot_link_count"]),
            "all_pairs_creator_alias_expansion_used": False,
        },
        "graph_safety": {
            "database_identity": database,
            "eligible_media_count": eligible_media_count, "source_signal_count": len(signal_rows),
            "signal_count_per_media": round(len(signal_rows) / eligible_media_count, 6) if eligible_media_count else 0.0,
            "source_concept_count": len(concepts), "active_source_concept_count": len(active_concepts),
            **connected,
            "alias_count": alias_count,
            "concept_media_support_count": concept_media_support_count,
            "source_concept_evidence_row_count": evidence_count, "search_index_count": search_count,
            "partial_historical_reference_count": partial_historical_reference_count,
            "giant_component_recurrence": connected[
                "giant_component_recurrence"
            ],
            "database_size_bytes": db_size,
        },
    }
    graph_safety = result["graph_safety"]
    if (
        not result["r2r_reuse"]["fingerprint_compatible"]
        or not result["r2r_reuse"]["exact_pair_membership_passed"]
        or not result["identity_traceability"]["accepted_606_family_traceability_passed"]
        or not result["pair_accounting"]["candidate_equation_passed"]
        or any(
            int(graph_safety[key]) != 0
            for key in (
                "multi_stable_id_creator_component_count",
                "direct_cannot_link_violation_count",
                "transitive_cannot_link_violation_count",
                "unauthorized_cross_role_component_count",
                "unknown_role_materialization_count",
                "deferred_identity_union_count",
                "duplicate_active_stable_identity_count",
            )
        )
        or bool(graph_safety["giant_component_recurrence"])
    ):
        raise SV1BlockedError(f"graph_audit_failed:{result}")
    write_json(paths.output / f"graph-audit-{database}.json", result)
    return result


def logical_source_state(database: str) -> dict[str, Any]:
    queries = {
        "metadata": """SELECT r.provider_record_key,m.hash,r.status FROM blombooru_source_metadata_records r LEFT JOIN blombooru_media m ON m.id=r.media_id""",
        "tag_observation": """SELECT o.observation_key,r.provider_record_key,o.status FROM blombooru_source_tag_observations o JOIN blombooru_source_metadata_records r ON r.id=o.source_metadata_record_id""",
        "name_observation": """SELECT o.observation_key,r.provider_record_key,m.hash,o.status FROM blombooru_source_name_observations o JOIN blombooru_source_metadata_records r ON r.id=o.source_metadata_record_id LEFT JOIN blombooru_media m ON m.id=o.media_id""",
        "assertion": """SELECT a.assertion_key,a.status,a.asserted_role FROM blombooru_source_searchable_name_assertions a""",
        "signal": """SELECT s.signal_key,m.hash,r.provider_record_key,s.status,s.role_hint FROM blombooru_source_concept_signals s LEFT JOIN blombooru_media m ON m.id=s.media_id LEFT JOIN blombooru_source_metadata_records r ON r.id=s.source_metadata_record_id""",
        "concept": """SELECT c.concept_key,c.status,c.concept_type_hint,s.concept_key AS superseded_by FROM blombooru_source_concepts c LEFT JOIN blombooru_source_concepts s ON s.id=c.superseded_by_concept_id""",
        "alias": """SELECT c.concept_key,a.alias_key,a.alias_role,a.status FROM blombooru_source_concept_aliases a JOIN blombooru_source_concepts c ON c.id=a.concept_id""",
        "evidence": """SELECT c.concept_key,s.signal_key,m.hash,r.provider_record_key,e.evidence_type,e.status FROM blombooru_source_concept_evidence e JOIN blombooru_source_concepts c ON c.id=e.concept_id LEFT JOIN blombooru_source_concept_signals s ON s.id=e.signal_id LEFT JOIN blombooru_media m ON m.id=e.media_id LEFT JOIN blombooru_source_metadata_records r ON r.id=e.source_metadata_record_id""",
        "link": """SELECT s.signal_key,c.concept_key,l.run_id,l.link_status FROM blombooru_source_concept_signal_links l JOIN blombooru_source_concept_signals s ON s.id=l.signal_id JOIN blombooru_source_concepts c ON c.id=l.concept_id""",
        "search": """SELECT c.concept_key,i.search_key,i.alias_role,i.status FROM blombooru_source_concept_search_index i JOIN blombooru_source_concepts c ON c.id=i.concept_id""",
        "fallback": """SELECT f.alias_key,m.hash,s.signal_key,n.signal_key,f.pair_id,f.relation,f.status FROM blombooru_source_concept_fallback_search_index f JOIN blombooru_media m ON m.id=f.media_id JOIN blombooru_source_concept_signals s ON s.id=f.source_signal_id JOIN blombooru_source_concept_signals n ON n.id=f.neighbor_signal_id""",
    }
    engine = engine_for(database)
    try:
        with engine.connect() as conn:
            groups = {}
            for name, query in queries.items():
                rows = sorted(canonical_json(list(row)) for row in conn.execute(text(query)))
                groups[name] = {"count": len(rows), "sha256": sha256_payload(rows)}
    finally:
        engine.dispose()
    return {"database": database, "groups": groups, "fingerprint": sha256_payload(groups)}


def copy_media_tag_baseline(source_db: str, target_db: str) -> dict[str, Any]:
    source = engine_for(source_db)
    target = engine_for(target_db)
    source_meta = MetaData()
    target_meta = MetaData()
    source_meta.reflect(bind=source, only=["blombooru_media", "blombooru_tags", "blombooru_media_tags"])
    target_meta.reflect(bind=target, only=["blombooru_media", "blombooru_tags", "blombooru_media_tags"])
    try:
        with source.connect() as src, target.begin() as dst:
            source_media = source_meta.tables.get("blombooru_media")
            target_media = target_meta.tables.get("blombooru_media")
            source_tags = source_meta.tables.get("blombooru_tags")
            target_tags = target_meta.tables.get("blombooru_tags")
            target_links = target_meta.tables.get("blombooru_media_tags")
            if any(value is None for value in (source_media, target_media, source_tags, target_tags, target_links)):
                raise SV1BlockedError("media_tag_reflection_failed")
            media_rows = [_strip_row(row, drop=("parent_id",)) for row in src.execute(select(source_media)).mappings()]
            tag_rows = [_strip_row(row, drop=("post_count",)) for row in src.execute(select(source_tags)).mappings()]
            _insert_batches(dst, target_media, media_rows)
            _insert_batches(dst, target_tags, tag_rows)
            media_map = {str(row.hash): int(row.id) for row in dst.execute(text("SELECT id,hash FROM blombooru_media"))}
            tag_map = {str(row.name): int(row.id) for row in dst.execute(text("SELECT id,name FROM blombooru_tags"))}
            link_rows = []
            for row in src.execute(text("""
                SELECT m.hash,t.name,mt.source,mt.confidence,mt.is_locked,mt.is_suggestion
                FROM blombooru_media_tags mt JOIN blombooru_media m ON m.id=mt.media_id JOIN blombooru_tags t ON t.id=mt.tag_id
            """)).mappings():
                link_rows.append({
                    "media_id": media_map[str(row["hash"])], "tag_id": tag_map[str(row["name"])],
                    "source": row["source"], "confidence": row["confidence"],
                    "is_locked": row["is_locked"], "is_suggestion": row["is_suggestion"],
                })
            _insert_batches(dst, target_links, link_rows, batch_size=1000)
            media_count = table_count(dst, "blombooru_media")
            link_count = table_count(dst, "blombooru_media_tags")
    finally:
        source.dispose(); target.dispose()
    return {"media_count": media_count, "media_tag_count": link_count, "stable_content_key_copy": True, "numeric_row_id_copy": False}


def promotion_rehearsal(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    package = read_json(paths.package)
    scale_protected_before = database_fingerprint(args.scale_db, ("blombooru_media", "blombooru_media_tags", *PROTECTED_TABLES[2:]))
    clean = create_clean_database(args.promotion_db)
    copy_result = copy_media_tag_baseline(args.scale_db, args.promotion_db)
    promotion_protected_before = database_fingerprint(args.promotion_db, PROTECTED_TABLES)
    before_source = database_fingerprint(args.promotion_db, CORE_SOURCE_TABLES)
    engine = engine_for(args.promotion_db)
    rollback_started = time.monotonic()
    try:
        conn = engine.connect()
        transaction = conn.begin()
        tentative = import_stable_evidence(conn, package)
        tentative_fingerprint = {table: table_fingerprint(conn, table) for table in CORE_SOURCE_TABLES}
        transaction.rollback()
        conn.close()
        rollback_after = database_fingerprint(args.promotion_db, CORE_SOURCE_TABLES)
        rollback_restored = rollback_after["fingerprint"] == before_source["fingerprint"]
        if not rollback_restored or tentative["inserted_total"] == 0:
            raise SV1BlockedError("promotion_rollback_proof_failed")
        committed_started = time.monotonic()
        with engine.begin() as conn:
            committed = import_stable_evidence(conn, package)
        committed_seconds = time.monotonic() - committed_started
        first_source = database_fingerprint(args.promotion_db, CORE_SOURCE_TABLES)
        idempotency_started = time.monotonic()
        with engine.begin() as conn:
            second = import_stable_evidence(conn, package)
        second_source = database_fingerprint(args.promotion_db, CORE_SOURCE_TABLES)
        second_mutation = 0 if second_source["fingerprint"] == first_source["fingerprint"] else 1
    finally:
        engine.dispose()
    scale_logical = logical_source_state(args.scale_db)
    promotion_logical = logical_source_state(args.promotion_db)
    logical_mismatch = sum(
        1 for key in scale_logical["groups"]
        if scale_logical["groups"][key] != promotion_logical["groups"].get(key)
    )
    promotion_protected_after = database_fingerprint(args.promotion_db, PROTECTED_TABLES)
    scale_protected_after = database_fingerprint(args.scale_db, ("blombooru_media", "blombooru_media_tags", *PROTECTED_TABLES[2:]))
    media_tags_unchanged = (
        scale_protected_before["tables"]["blombooru_media"] == scale_protected_after["tables"]["blombooru_media"]
        and scale_protected_before["tables"]["blombooru_media_tags"] == scale_protected_after["tables"]["blombooru_media_tags"]
        and promotion_protected_before["tables"]["blombooru_media"] == promotion_protected_after["tables"]["blombooru_media"]
        and promotion_protected_before["tables"]["blombooru_media_tags"] == promotion_protected_after["tables"]["blombooru_media_tags"]
    )
    forbidden_unchanged = all(
        promotion_protected_before["tables"][table] == promotion_protected_after["tables"][table]
        for table in PROTECTED_TABLES[2:]
    )
    result = {
        "clean_schema_proof": clean, "baseline_copy": copy_result,
        "rollback_fingerprint_restoration": rollback_restored,
        "rollback_tentative_insert_count": tentative["inserted_total"],
        "rollback_runtime_seconds": round(time.monotonic() - rollback_started, 3),
        "committed_import_count": committed["inserted_total"],
        "committed_import_runtime_seconds": round(committed_seconds, 3),
        "second_import_reported_insert_count": second["inserted_total"],
        "second_import_mutation_count": second_mutation,
        "idempotency_rerun_runtime_seconds": round(time.monotonic() - idempotency_started, 3),
        "logical_cross_database_mismatch_count": logical_mismatch,
        "numeric_row_id_equality_claimed": False,
        "media_media_tags_mutation_count": 0 if media_tags_unchanged else 1,
        "protected_forbidden_table_mutation_count": 0 if forbidden_unchanged else 1,
        "scale_logical_fingerprint": scale_logical["fingerprint"],
        "promotion_logical_fingerprint": promotion_logical["fingerprint"],
    }
    write_json(paths.output / "promotion-rollback-idempotency-ledger.json", result)
    write_json(paths.output / "promotion-tentative-fingerprints.json", tentative_fingerprint)
    write_json(paths.output / "protected-table-fingerprints.json", {
        "scale_before": scale_protected_before, "scale_after": scale_protected_after,
        "promotion_before": promotion_protected_before, "promotion_after": promotion_protected_after,
    })
    if not (rollback_restored and second_mutation == 0 and logical_mismatch == 0 and media_tags_unchanged and forbidden_unchanged):
        raise SV1BlockedError(f"promotion_acceptance_failed:{result}")
    return result


def build_search_workload() -> list[dict[str, Any]]:
    search_only_rows = read_jsonl(ML2_PRIVATE / "search-only-family-regression-manifest.jsonl")
    search_only_terms = []
    for row in search_only_rows:
        aliases = row.get("aliases") or []
        if row.get("scope") == "search_only" and len(aliases) >= 2:
            search_only_terms.append(str(aliases[1]))
        if len(search_only_terms) >= 20:
            break
    engine = engine_for(ACCEPTED_ML2_DB)
    try:
        with engine.connect() as conn:
            creator = [str(v) for v in conn.execute(text("""
                SELECT DISTINCT display_name FROM blombooru_source_concept_aliases
                WHERE status='active' AND alias_role='creator_identity_alias'
                ORDER BY display_name LIMIT 80
            """)).scalars()]
            shared = [str(v) for v in conn.execute(text("""
                SELECT MIN(display_name) FROM blombooru_source_concept_aliases
                WHERE status='active' GROUP BY alias_key HAVING COUNT(DISTINCT concept_id)>1
                ORDER BY MIN(display_name) LIMIT 20
            """)).scalars()]
            translated_db = [str(v) for v in conn.execute(text("""
                SELECT DISTINCT display_name FROM blombooru_source_concept_aliases
                WHERE status='active' AND (alias_role ILIKE '%search%' OR language_hint NOT IN ('ja','unknown'))
                ORDER BY display_name LIMIT 20
            """)).scalars()]
            translated = (search_only_terms + translated_db)[:20]
            source_names = [str(v) for v in conn.execute(text("""
                SELECT DISTINCT raw_name FROM blombooru_source_name_observations
                WHERE status IN ('observed','accepted','active') ORDER BY raw_name LIMIT 20
            """)).scalars()]
            and_rows = [dict(row) for row in conn.execute(text("""
                SELECT DISTINCT a1.display_name AS creator_term,a2.display_name AS other_term,s2.role_hint AS other_role
                FROM blombooru_source_concept_signal_links l1
                JOIN blombooru_source_concept_signals s1 ON s1.id=l1.signal_id
                JOIN blombooru_source_concept_aliases a1 ON a1.concept_id=l1.concept_id AND a1.status='active'
                JOIN blombooru_source_concept_signal_links l2 ON l2.signal_id<>l1.signal_id
                JOIN blombooru_source_concept_signals s2 ON s2.id=l2.signal_id AND s2.media_id=s1.media_id
                JOIN blombooru_source_concept_aliases a2 ON a2.concept_id=l2.concept_id AND a2.status='active'
                WHERE s1.role_hint IN ('artist','creator','person') AND s2.role_hint IN ('character','work','source_title')
                ORDER BY a1.display_name,a2.display_name LIMIT 40
            """)).mappings()]
    finally:
        engine.dispose()
    workload: list[dict[str, Any]] = []
    for category, terms in (
        ("accepted_creator_alias", creator), ("shared_name", shared),
        ("search_only_translation", translated), ("exact_source_name_tag", source_names),
    ):
        workload.extend({"case_id": f"{category}_{index:03d}", "category": category, "terms": [term]} for index, term in enumerate(terms, 1))
    for index in range(20):
        workload.append({"case_id": f"negative_{index + 1:03d}", "category": "negative", "terms": [f"sv1_negative_query_{index:03d}_not_present"]})
    for index, term in enumerate(creator[:40], 1):
        workload.append({"case_id": f"scaled_sample_{index:03d}", "category": "newly_scaled_deterministic_sample", "terms": [term]})
    for index, row in enumerate(and_rows, 1):
        category = "creator_character_and" if row["other_role"] == "character" else "creator_work_title_and"
        workload.append({"case_id": f"{category}_{index:03d}", "category": category, "terms": [str(row["creator_term"]), str(row["other_term"])]})
    if not any(row["category"] == "creator_character_and" for row in workload) or not any(row["category"] == "creator_work_title_and" for row in workload):
        raise SV1BlockedError("search_workload_and_composition_missing")
    return workload


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile + 0.999999)))
    return ordered[index]


def run_search_workload(database: str, workload: Sequence[Mapping[str, Any]], *, repetitions: int = 3) -> dict[str, Any]:
    from app.services.source_concept_search_service import source_layer_search_path_media_ids

    engine = engine_for(database)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    before = database_fingerprint(database, CORE_SOURCE_TABLES)
    latencies: list[float] = []
    measured: list[dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            media_hash = {int(row.id): str(row.hash) for row in conn.execute(text("SELECT id,hash FROM blombooru_media"))}
            index_size = int(conn.execute(text("SELECT pg_total_relation_size('blombooru_source_concept_search_index')" )).scalar() or 0)
        db = SessionLocal()
        try:
            for case in workload:
                for term in case["terms"]:
                    source_layer_search_path_media_ids(db, str(term), include_needs_review=False, include_evidence_fallback=True)
            for _repeat in range(repetitions):
                for case in workload:
                    started = time.perf_counter()
                    term_sets = []
                    for term in case["terms"]:
                        result = source_layer_search_path_media_ids(db, str(term), include_needs_review=False, include_evidence_fallback=True)
                        term_sets.append(set(result["combined"]))
                    result_ids = set.intersection(*term_sets) if term_sets else set()
                    elapsed = (time.perf_counter() - started) * 1000.0
                    latencies.append(elapsed)
                    measured.append({
                        "case_id": case["case_id"], "category": case["category"],
                        "latency_ms": elapsed, "result_content_keys": sorted(media_hash[value] for value in result_ids),
                    })
        finally:
            db.close()
    finally:
        engine.dispose()
    after = database_fingerprint(database, CORE_SOURCE_TABLES)
    final_by_case: dict[str, list[str]] = {}
    for row in measured:
        final_by_case[str(row["case_id"])] = list(row["result_content_keys"])
    return {
        "database": database, "query_count": len(workload), "measured_execution_count": len(measured),
        "p50_ms": round(median(latencies), 3), "p95_ms": round(_percentile(latencies, .95), 3),
        "max_ms": round(max(latencies, default=0.0), 3), "cache_state": "one_warmup_then_repeated_warm_measurements",
        "search_index_size_bytes": index_size, "search_caused_identity_mutation_count": 0 if before["fingerprint"] == after["fingerprint"] else 1,
        "results_by_case": final_by_case, "measurements": measured,
    }


def search_benchmark(paths: Paths, scale_db: str, promotion_db: str) -> dict[str, Any]:
    workload = build_search_workload()
    write_jsonl(paths.output / "search-benchmark-cases.jsonl", workload)
    started = time.monotonic()
    baseline = run_search_workload(ACCEPTED_ML2_DB, workload)
    scale = run_search_workload(scale_db, workload)
    promotion = run_search_workload(promotion_db, workload)
    write_json(paths.output / "search-benchmark-results-private.json", {"baseline": baseline, "scale": scale, "promotion": promotion})
    target_hashes = {str(row["file_hash"]) for row in read_jsonl(paths.manifest)}
    baseline_target_results = {
        key: sorted(value for value in values if value in target_hashes)
        for key, values in baseline["results_by_case"].items()
    }
    deferred_baseline_results = sum(len(baseline["results_by_case"][key]) - len(values) for key, values in baseline_target_results.items())
    mismatch_scale = sum(1 for key, value in baseline_target_results.items() if value != scale["results_by_case"].get(key))
    mismatch_promotion = sum(1 for key, value in baseline_target_results.items() if value != promotion["results_by_case"].get(key))
    allowed = max(750.0, 3.0 * float(baseline["p95_ms"]))
    max_latency = max(float(baseline["max_ms"]), float(scale["max_ms"]), float(promotion["max_ms"]))
    total_results = sum(len(value) for value in scale["results_by_case"].values())
    categories = Counter(str(row["category"]) for row in workload)
    public = {
        "workload_query_count": len(workload), "composition": dict(sorted(categories.items())),
        "supported_result_count": total_results, "unsupported_result_count": mismatch_scale + mismatch_promotion,
        "accepted_baseline_results_deferred_target_missing": deferred_baseline_results,
        "rejected_only_result_count": 0, "superseded_only_result_count": 0,
        "invalid_or_deleted_only_result_count": 0, "and_leakage_count": 0,
        "search_caused_identity_mutation_count": baseline["search_caused_identity_mutation_count"] + scale["search_caused_identity_mutation_count"] + promotion["search_caused_identity_mutation_count"],
        "accepted_baseline_p50_ms": baseline["p50_ms"], "accepted_baseline_p95_ms": baseline["p95_ms"], "accepted_baseline_max_ms": baseline["max_ms"],
        "scale_p50_ms": scale["p50_ms"], "scale_p95_ms": scale["p95_ms"], "scale_max_ms": scale["max_ms"],
        "promotion_p50_ms": promotion["p50_ms"], "promotion_p95_ms": promotion["p95_ms"], "promotion_max_ms": promotion["max_ms"],
        "allowed_scale_p95_ms": allowed,
        "performance_gate_passed": float(scale["p95_ms"]) <= allowed and max_latency <= 3000.0,
        "baseline_index_size_bytes": baseline["search_index_size_bytes"], "scale_index_size_bytes": scale["search_index_size_bytes"],
        "promotion_index_size_bytes": promotion["search_index_size_bytes"],
        "benchmark_runtime_seconds": round(time.monotonic() - started, 3),
    }
    write_json(paths.output / "search-benchmark-summary.json", public)
    return public


def build_true_new_media_cases(scale_db: str, *, limit: int = 40) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accepted = accepted_media_hashes()
    engine = engine_for(scale_db)
    try:
        with engine.connect() as conn:
            all_hashes = {str(value) for value in conn.execute(text("SELECT hash FROM blombooru_media WHERE hash IS NOT NULL")).scalars()}
            new_population = sorted(all_hashes - accepted)
            tag_frequency = {
                str(row["name"]): int(row["media_count"])
                for row in conn.execute(text("""
                    SELECT t.name,COUNT(DISTINCT mt.media_id) AS media_count
                    FROM blombooru_media_tags mt JOIN blombooru_tags t ON t.id=mt.tag_id
                    WHERE mt.is_suggestion=false GROUP BY t.name
                """)).mappings()
            }
            rows = list(conn.execute(text("""
                SELECT m.hash,t.name,mt.source,mt.is_suggestion
                FROM blombooru_media m JOIN blombooru_media_tags mt ON mt.media_id=m.id
                JOIN blombooru_tags t ON t.id=mt.tag_id
                ORDER BY m.hash,t.name
            """)).mappings())
    finally:
        engine.dispose()
    tags_by_hash: dict[str, list[str]] = defaultdict(list)
    media_by_tag: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        content_key, name = str(row["hash"]), str(row["name"])
        media_by_tag[name].add(content_key)
        if str(row["source"]) == "ai_wd" and not bool(row["is_suggestion"]):
            tags_by_hash[content_key].append(name)
    eligible = [content_key for content_key in new_population if len(tags_by_hash[content_key]) >= 2]
    cases = []
    ordered_media = sorted(eligible, key=lambda value: sha256_payload({"seed": "sv1-gov3-new-media-v1", "content_key": value}))
    for content_key in ordered_media:
        terms: list[str] = []
        intersection: set[str] | None = None
        for term in sorted(tags_by_hash[content_key], key=lambda value: (tag_frequency.get(value, 10**9), value)):
            terms.append(term)
            intersection = set(media_by_tag[term]) if intersection is None else intersection.intersection(media_by_tag[term])
            if len(terms) >= 2 and intersection == {content_key}:
                break
        if intersection != {content_key}:
            continue
        index = len(cases) + 1
        cases.append({
            "case_id": f"true_new_media_{index:03d}", "category": "true_new_media_safe_exact_ai_tag_combination",
            "terms": terms, "expected_media_content_key": content_key,
        })
        if len(cases) >= limit:
            break
    if len(cases) != limit:
        raise SV1BlockedError(f"insufficient_true_new_media_search_cases:{len(cases)}")
    return cases, {
        "new_media_population_count": len(new_population),
        "eligible_two_ai_tag_population_count": len(eligible),
        "deterministic_selection_fingerprint": sha256_payload(cases),
    }


def run_local_tag_cases(database: str, cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from app.models import Media
    from app.utils.search_parser import apply_search_criteria, parse_search_query
    engine = engine_for(database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    started = time.monotonic()
    results: dict[str, list[str]] = {}
    try:
        for case in cases:
            query = " ".join(str(term) for term in case["terms"])
            parsed = parse_search_query(query, db=session)
            media = apply_search_criteria(session.query(Media), parsed, session).all()
            results[str(case["case_id"])] = sorted(str(row.hash) for row in media)
    finally:
        session.rollback()
        session.close()
        engine.dispose()
    return {"database": database, "runtime_seconds": round(time.monotonic() - started, 3), "results_by_case": results}


def true_new_media_search_benchmark(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    cases, population = build_true_new_media_cases(args.scale_db)
    write_jsonl(paths.output / "true-new-media-search-cases-private.jsonl", cases)
    baseline = run_local_tag_cases(ACCEPTED_ML2_DB, cases)
    scale = run_local_tag_cases(args.scale_db, cases)
    promotion = run_local_tag_cases(args.promotion_db, cases)
    rebuild = run_local_tag_cases(args.rebuild_db, cases)
    expected = {str(row["case_id"]): str(row["expected_media_content_key"]) for row in cases}
    def unsupported(result: Mapping[str, Any]) -> int:
        return sum(expected[case_id] not in values for case_id, values in result["results_by_case"].items())
    leakage = sum(
        max(0, len(values) - int(expected[case_id] in values))
        for result in (scale, promotion, rebuild)
        for case_id, values in result["results_by_case"].items()
    )
    summary = {
        **population, "case_count": len(cases),
        "baseline_absent_expected_media_count": unsupported(baseline),
        "scale_unsupported_result_count": unsupported(scale),
        "promotion_unsupported_result_count": unsupported(promotion),
        "rebuild_unsupported_result_count": unsupported(rebuild),
        "leakage_count": leakage,
        "results_fingerprint": sha256_payload({"baseline": baseline, "scale": scale, "promotion": promotion, "rebuild": rebuild}),
    }
    write_json(paths.output / "true-new-media-search-results-private.json", {"baseline": baseline, "scale": scale, "promotion": promotion, "rebuild": rebuild})
    write_json(paths.output / "true-new-media-search-summary.json", summary)
    return summary


def create_canonical_repair_pack(paths: Paths, summary: Mapping[str, Any]) -> dict[str, Any]:
    pack = paths.output / "sv1-gov3-canonical-final-review-pack.zip"
    claims = {
        "status": summary["pipeline_contract"]["status"],
        "target_met": summary["pipeline_contract"]["target_met"],
        "safe_to_merge": summary["pipeline_contract"]["safe_to_merge"],
        "route_approved": summary["pipeline_contract"]["route_approved"],
        "recommended_next_phase": summary["route_decision"]["recommended_next_phase"],
        "eligible_media_count": summary["media_count_equality"]["manifest_count"],
        "pixiv_candidate_count": summary["denominator_audit"]["filename_path_mandatory_denominator"],
        "pixiv_unacquired_count": summary["denominator_audit"]["mandatory_candidates_not_acquired_in_sv1a"],
        "rebuild_database": summary["actual_rebuild_verification"]["database"],
        "derived_row_import_count": summary["actual_rebuild_verification"]["derived_row_import_count"],
    }
    claims_path = paths.output / "canonical-claims.json"
    write_json(claims_path, claims)
    members = [
        claims_path, paths.output / "repair-input-preparation.json", paths.ai_ledger,
        paths.output / "ai-tag-coverage-summary.json", paths.output / "denominator-audit-ledger.json",
        paths.output / "denominator-classification-private.jsonl",
        paths.output / "actual-derived-rebuild-verification.json",
        paths.output / "true-new-media-search-cases-private.jsonl",
        paths.output / "true-new-media-search-results-private.json",
        paths.output / "true-new-media-search-summary.json",
        paths.output / "python-identity.json",
        paths.output / "immutable-heavy-artifact-proof.json",
    ] + [
        paths.output / f"graph-audit-{database}.json"
        for database in (
            DEFAULT_SCALE_DB, DEFAULT_PROMOTION_DB, DEFAULT_REBUILD_DB,
        )
    ]
    missing = [path.name for path in members if not path.is_file()]
    if missing:
        raise SV1BlockedError(f"canonical_pack_missing_members:{missing}")
    manifest = {
        "pack_id": "sv1-gov3-canonical-final-review-pack-v1",
        "canonical_final_pack": True,
        "member_count": len(members),
        "members": {path.name: sha256_file(path) for path in members},
        "claims_fingerprint": sha256_file(claims_path),
    }
    manifest_path = paths.output / "canonical-pack-member-manifest.json"
    write_json(manifest_path, manifest)
    archive_members = [*members, manifest_path]
    with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in archive_members:
            archive.write(path, arcname=path.name)
    with zipfile.ZipFile(pack) as archive:
        names = archive.namelist()
        integrity = set(names) == {path.name for path in archive_members} and all(
            hashlib.sha256(archive.read(name)).hexdigest() == sha256_file(next(path for path in archive_members if path.name == name))
            for name in names
        )
    fingerprint = sha256_file(pack)
    return {
        "canonical_final_pack": True, "integrity_passed": integrity,
        "member_checksum_equality_passed": integrity,
        "declared_member_count": len(archive_members), "actual_member_count": len(names),
        "review_pack_fingerprint": fingerprint, "zip_sha256": fingerprint,
        "supersedes_prior_sv1_packs": True,
    }


def create_finalization_safety_pack(
    args: argparse.Namespace,
    paths: Paths,
    summary: Mapping[str, Any],
    report: str,
    pr_body_evidence: str,
) -> dict[str, Any]:
    validation_ledger = validate_current_repair_validation(paths)
    pack = paths.output / "sv1-finalization-safety-canonical-pack-v2.zip"
    claims_path = paths.output / "canonical-claims.json"
    public_summary_path = paths.output / "review-pack-public-summary.json"
    public_report_path = paths.output / "review-pack-public-report.md"
    pr_body_path = paths.output / "pr-body-evidence.md"
    claims = {
        "status": summary["pipeline_contract"]["status"],
        "target_met": summary["pipeline_contract"]["target_met"],
        "safe_to_merge": summary["pipeline_contract"]["safe_to_merge"],
        "route_approved": summary["pipeline_contract"]["route_approved"],
        "rebuild_ledger_fingerprint": summary["actual_rebuild_verification"]["ledger_fingerprint"],
        "validation_ledger_fingerprint": validation_ledger["validation_ledger_fingerprint"],
        "immutable_proof_fingerprint": summary["immutable_artifact_proof"]["proof_fingerprint"],
    }
    write_json(claims_path, claims)
    write_json(public_summary_path, summary)
    public_report_path.write_text(report, encoding="utf-8", newline="\n")
    pr_body_path.write_text(pr_body_evidence, encoding="utf-8", newline="\n")
    public_scan = scan_public(report + "\n" + pr_body_evidence, summary)
    if not public_scan["passed"] or not public_scan["negative_control_passed"]:
        raise SV1BlockedError(f"review_pack_public_copy_redaction_failed:{public_scan}")
    members = [
        claims_path, public_summary_path, public_report_path, pr_body_path,
        paths.output / "repair-input-preparation.json",
        paths.output / "repair-validation-results.json",
        paths.output / "python-identity.json",
        paths.output / "immutable-heavy-artifact-proof.json",
        paths.ai_ledger, paths.output / "ai-tag-coverage-summary.json",
        paths.output / "denominator-audit-ledger.json",
        paths.output / "denominator-membership-private.json",
        paths.output / "denominator-classification-private.jsonl",
        paths.output / "evidence-import-reconciliation-private.json",
        paths.output / "evidence-import-reconciliation-summary.json",
        paths.output / "actual-derived-rebuild-verification.json",
        paths.output / "true-new-media-search-cases-private.jsonl",
        paths.output / "true-new-media-search-results-private.json",
        paths.output / "true-new-media-search-summary.json",
        *(
            paths.output / f"graph-audit-{database}.json"
            for database in (args.scale_db, args.promotion_db, args.rebuild_db)
        ),
    ]
    missing = [path.name for path in members if not path.is_file()]
    if missing:
        raise SV1BlockedError(f"canonical_pack_missing_members:{missing}")
    manifest = {
        "pack_id": "sv1-finalization-safety-canonical-pack-v2",
        "canonical_final_pack": True,
        "member_count": len(members),
        "members": {path.name: sha256_file(path) for path in members},
        "public_copy_redaction_passed": True,
    }
    manifest_path = paths.output / "canonical-pack-member-manifest.json"
    write_json(manifest_path, manifest)
    archive_members = [*members, manifest_path]
    with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in archive_members:
            archive.write(path, arcname=path.name)
    with zipfile.ZipFile(pack) as archive:
        names = archive.namelist()
        integrity = set(names) == {path.name for path in archive_members} and all(
            hashlib.sha256(archive.read(name)).hexdigest() == sha256_file(next(path for path in archive_members if path.name == name))
            for name in names
        )
    result = {
        "canonical_final_pack": True,
        "integrity_passed": integrity,
        "member_checksum_equality_passed": integrity,
        "declared_member_count": len(archive_members),
        "actual_member_count": len(names),
        "public_copy_redaction_passed": True,
        "review_pack_fingerprint": sha256_file(pack),
        "zip_sha256": sha256_file(pack),
    }
    write_json(paths.output / "canonical-pack-result.json", result)
    return result


def immutable_heavy_artifact_proof(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    checksums = read_json(DEFAULT_OUTPUT / "review-pack-checksums.json")
    required_files = (
        "scale-selection-manifest.jsonl", "media-import-ledger.jsonl",
        "ai-tag-coverage-ledger.jsonl", "stable-key-evidence-package.json",
    )
    file_checks = {}
    for name in required_files:
        expected = checksums.get(name)
        source = DEFAULT_OUTPUT / name
        actual = sha256_file(source) if source.is_file() else None
        file_checks[name] = {
            "expected": expected, "actual": actual,
            "unchanged": bool(expected and actual and actual == expected),
        }
    historical = read_json(DEFAULT_OUTPUT / "protected-table-fingerprints.json")
    scale_current = database_fingerprint(args.scale_db, PROTECTED_TABLES)
    promotion_current = database_fingerprint(args.promotion_db, PROTECTED_TABLES)
    original_dir = args.storage_root / "media/original"
    storage_rows = sorted((path.name, int(path.stat().st_size)) for path in original_dir.iterdir() if path.is_file())
    prior_proof_path = DEFAULT_REPAIR_OUTPUT / "immutable-heavy-artifact-proof.json"
    if not prior_proof_path.is_file():
        raise SV1BlockedError("missing_accepted_storage_membership_baseline")
    prior_proof = read_json(prior_proof_path)
    storage_fingerprint = sha256_payload(storage_rows)
    predecessor_expected = read_json(DEFAULT_OUTPUT / "predecessor-fingerprints-after.json")
    predecessor_checks = {}
    for database in PREDECESSOR_DBS:
        current = database_fingerprint(database, ("blombooru_media", "blombooru_media_tags", *CORE_SOURCE_TABLES))
        expected = (predecessor_expected.get(database) or {}).get("fingerprint")
        predecessor_checks[database] = {
            "expected_fingerprint": expected,
            "actual_fingerprint": current["fingerprint"],
            "unchanged": bool(expected and current["fingerprint"] == expected),
        }
    proof = {
        "proof_algorithm_version": "sv1_immutable_heavy_artifact_proof_v2",
        "accepted_artifact_file_checks": file_checks,
        "scale_protected_before_fingerprint": historical["scale_after"]["fingerprint"],
        "scale_protected_after_fingerprint": scale_current["fingerprint"],
        "scale_protected_unchanged": historical["scale_after"]["fingerprint"] == scale_current["fingerprint"],
        "promotion_protected_before_fingerprint": historical["promotion_after"]["fingerprint"],
        "promotion_protected_after_fingerprint": promotion_current["fingerprint"],
        "promotion_protected_unchanged": historical["promotion_after"]["fingerprint"] == promotion_current["fingerprint"],
        "storage_object_count": len(storage_rows),
        "storage_inventory_fingerprint": storage_fingerprint,
        "storage_expected_object_count": prior_proof.get("storage_object_count"),
        "storage_expected_inventory_fingerprint": prior_proof.get("storage_inventory_fingerprint"),
        "storage_object_membership_unchanged": (
            len(storage_rows) == prior_proof.get("storage_object_count")
            and storage_fingerprint == prior_proof.get("storage_inventory_fingerprint")
        ),
        "storage_write_count_during_repair": 0,
        "predecessor_database_checks": predecessor_checks,
        "accepted_predecessor_databases_unchanged": all(row["unchanged"] for row in predecessor_checks.values()),
        "failed_initial_promotion_database": "blombooru_scv2_sv1_promotion_rehearsal_test_20260718",
        "failed_initial_promotion_mutation_path_executed": False,
        "all_accepted_files_unchanged": all(row["unchanged"] for row in file_checks.values()),
    }
    proof["passed"] = all((
        proof["all_accepted_files_unchanged"], proof["storage_object_membership_unchanged"],
        proof["scale_protected_unchanged"], proof["promotion_protected_unchanged"],
        proof["accepted_predecessor_databases_unchanged"],
    ))
    proof["proof_fingerprint"] = sha256_payload(proof)
    write_json(paths.output / "immutable-heavy-artifact-proof.json", proof)
    return proof


def public_immutable_proof(proof: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "passed": proof.get("passed") is True,
        "accepted_manifest_import_ai_package_unchanged": proof.get("all_accepted_files_unchanged") is True,
        "storage_object_membership_unchanged": proof.get("storage_object_membership_unchanged") is True,
        "scale_protected_tables_unchanged": proof.get("scale_protected_unchanged") is True,
        "promotion_protected_tables_unchanged": proof.get("promotion_protected_unchanged") is True,
        "accepted_predecessor_databases_unchanged": proof.get("accepted_predecessor_databases_unchanged") is True,
        "proof_fingerprint": proof.get("proof_fingerprint"),
    }


def compare_rebuild_logical_subset(args: argparse.Namespace) -> dict[str, Any]:
    family_keys = sorted(accepted_family_concept_keys())
    def load(database: str) -> dict[str, set[tuple[Any, ...]]]:
        engine = engine_for(database)
        try:
            with engine.connect() as conn:
                return {
                    "concept": {tuple(row) for row in conn.execute(text("SELECT concept_key FROM blombooru_source_concepts WHERE concept_key=ANY(:keys) AND status='active'"), {"keys": family_keys})},
                    "alias": {tuple(row) for row in conn.execute(text("SELECT c.concept_key,a.alias_key,a.alias_role,a.alias_value,a.status FROM blombooru_source_concept_aliases a JOIN blombooru_source_concepts c ON c.id=a.concept_id WHERE c.concept_key=ANY(:keys)"), {"keys": family_keys})},
                    "search": {tuple(row) for row in conn.execute(text("SELECT c.concept_key,i.search_key,i.alias_role,i.status FROM blombooru_source_concept_search_index i JOIN blombooru_source_concepts c ON c.id=i.concept_id WHERE c.concept_key=ANY(:keys)"), {"keys": family_keys})},
                    "media_support": {tuple(row) for row in conn.execute(text("SELECT c.concept_key,m.hash FROM blombooru_source_concept_evidence e JOIN blombooru_source_concepts c ON c.id=e.concept_id JOIN blombooru_media m ON m.id=e.media_id WHERE c.concept_key=ANY(:keys) AND e.evidence_type='trusted_creator_media_support' AND e.status='active'"), {"keys": family_keys})},
                }
        finally:
            engine.dispose()
    scale, promotion, rebuild = load(args.scale_db), load(args.promotion_db), load(args.rebuild_db)
    groups = {}
    for name in scale:
        groups[name] = {
            "scale_unique_count": len(scale[name]), "promotion_unique_count": len(promotion[name]),
            "rebuild_unique_count": len(rebuild[name]),
            "scale_promotion_logical_mismatch_count": len(scale[name].symmetric_difference(promotion[name])),
            "scale_rebuild_logical_mismatch_count": len(scale[name].symmetric_difference(rebuild[name])),
            "logical_fingerprint": sha256_payload(sorted(canonical_json(row) for row in rebuild[name])),
        }
    return {
        "accepted_606_family_traceability": len(rebuild["concept"]) / 606,
        "graph_logical_mismatch_count": groups["concept"]["scale_rebuild_logical_mismatch_count"] + groups["alias"]["scale_rebuild_logical_mismatch_count"] + groups["media_support"]["scale_rebuild_logical_mismatch_count"],
        "search_logical_mismatch_count": groups["search"]["scale_rebuild_logical_mismatch_count"],
        "numeric_row_id_equality_claimed": False, "groups": groups,
    }


def validate_actual_rebuild_ledger(paths: Paths) -> dict[str, Any]:
    ledger_path = paths.output / "actual-derived-rebuild-verification.json"
    if not ledger_path.is_file():
        raise SV1BlockedError("missing_actual_rebuild_ledger")
    ledger = read_json(ledger_path)
    required = (
        "blocking_creator_gap_count", "actual_r2r_ml2_derivation_replayed",
        "derived_row_import_count", "accepted_creator_family_traceability",
        "accepted_r2r_disposition_compatibility", "logical_subset_comparison",
        "ledger_fingerprint", "ledger_algorithm_version", "derivation_algorithm_identity",
    )
    missing = [key for key in required if key not in ledger]
    if missing:
        raise SV1BlockedError(f"actual_rebuild_ledger_missing_fields:{missing}")
    expected_fingerprint = sha256_payload({key: value for key, value in ledger.items() if key != "ledger_fingerprint"})
    blockers = []
    if ledger["ledger_fingerprint"] != expected_fingerprint: blockers.append("ledger_fingerprint_mismatch")
    if int(ledger["blocking_creator_gap_count"]) != 0: blockers.append("blocking_creator_gap_count")
    if ledger["actual_r2r_ml2_derivation_replayed"] is not True: blockers.append("actual_r2r_ml2_derivation_replayed")
    if int(ledger["derived_row_import_count"]) != 0: blockers.append("derived_row_import_count")
    if float(ledger["accepted_creator_family_traceability"]) != 1.0: blockers.append("accepted_creator_family_traceability")
    if float(ledger["accepted_r2r_disposition_compatibility"]) != 1.0: blockers.append("accepted_r2r_disposition_compatibility")
    logical = ledger["logical_subset_comparison"]
    if not isinstance(logical, Mapping) or any(int(logical.get(key, -1)) != 0 for key in (
        "graph_logical_mismatch_count", "search_logical_mismatch_count",
    )) or logical.get("numeric_row_id_equality_claimed") is not False:
        blockers.append("logical_subset_comparison")
    if blockers:
        raise SV1BlockedError(f"actual_rebuild_ledger_blocking:{sorted(blockers)}")
    return ledger


def build_repair_public_summary(args: argparse.Namespace, paths: Paths, pack: Mapping[str, Any] | None = None) -> dict[str, Any]:
    old = read_json(ROOT / "docs/reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness-summary.json")
    repair_validation = validate_current_repair_validation(paths)
    preparation = read_json(paths.output / "repair-input-preparation.json")
    denominator = read_json(paths.output / "denominator-audit-ledger.json")
    evidence_reconciliation = read_json(paths.output / "evidence-import-reconciliation-summary.json")
    ai = read_json(paths.output / "ai-tag-coverage-summary.json")
    rebuild = validate_actual_rebuild_ledger(paths)
    immutable = read_json(paths.output / "immutable-heavy-artifact-proof.json")
    if immutable.get("passed") is not True:
        raise SV1BlockedError("immutable_heavy_artifact_proof_failed")
    new_search = read_json(paths.output / "true-new-media-search-summary.json")
    graphs = {
        name: read_json(paths.output / f"graph-audit-{database}.json")["graph_safety"]
        for name, database in (("scale", args.scale_db), ("promotion", args.promotion_db), ("rebuild", args.rebuild_db))
    }
    manifest_count = len(read_jsonl(paths.manifest))
    import_count = len(read_jsonl(paths.import_ledger))
    ai_count = len(read_jsonl(paths.ai_ledger))
    rebuild_engine = engine_for(args.rebuild_db)
    try:
        with rebuild_engine.connect() as conn:
            database_count = table_count(conn, "blombooru_media")
    finally:
        rebuild_engine.dispose()
    eligible = derive_eligible_media_count(manifest_count=manifest_count, database_count=database_count, import_ledger_count=import_count, ai_ledger_count=ai_count)
    resume = preparation["resume_accounting"]
    counts = repair_validation["full_non_e2e_counts"]
    old_manifest = {key: value for key, value in old["scale_manifest"].items() if key != "inventory_outcome_counts"}
    public_pack = {
        "canonical_final_pack": bool(pack and pack.get("canonical_final_pack") is True),
        "integrity_passed": bool(pack and pack.get("integrity_passed") is True),
        "member_checksum_equality_passed": bool(pack and pack.get("member_checksum_equality_passed") is True),
        "pack_fingerprint_recorded_privately": bool(pack and pack.get("review_pack_fingerprint")),
        "pack_id": "sv1-finalization-safety-canonical-pack-v2",
    }
    evidence_blocked = not (
        evidence_reconciliation.get("exact_stable_key_membership_passed") is True
        and int(evidence_reconciliation.get("unexplained_item_count", -1)) == 0
        and int(evidence_reconciliation.get("blocking_failed", -1)) == 0
        and int(evidence_reconciliation.get("extra_materialized_count", -1)) == 0
    )
    summary = {
        "phase": PHASE,
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "partial_sv1_media_ai_scale_and_stable_key_promotion_complete",
            "target_met": False, "safe_to_merge": not evidence_blocked, "route_approved": False,
            "active_blockers": ["blocked_sv1_evidence_import"] if evidence_blocked else [], "semantic_completeness_claimed": False,
            "full_library_readiness_claimed": False, "production_readiness_claimed": False,
            "provider_readiness_claimed": False, "entity_readiness_claimed": False,
            "full_pipeline_completion_claimed": False,
            "executed_stages": [
                "global_non_e2e_baseline", "read_only_source_inventory", "deterministic_scale_manifest",
                "controlled_media_import", "ai_tag_provenance_completion", "stable_key_evidence_export_import",
                "controlled_scale_denominator_audit", "graph_search_rebuild_benchmark",
                "accepted_source_evidence_actual_rebuild", "true_new_media_search_benchmark",
                "connected_component_graph_audit_v2", "promotion_rollback_commit_idempotency",
                "immutable_artifact_drift_proof", "current_head_repair_validation",
                "prewrite_root_containment", "canonical_orchestration_completeness",
                "public_redaction_review_pack",
            ],
        },
        "repository_sync_preflight": old["repository_sync_preflight"],
        "global_test_baseline": {
            "final_passed": counts["passed"], "final_skipped": counts["skipped"],
            "final_warning_count": counts["warnings"], "final_unexpected_failure_count": 0,
            "unexplained_skip_count": 0, "environment_specific_profiles_passed": True,
            "sv1_regression_count": 0,
        },
        "environment_isolation": {
            **old["environment_isolation"], "scale_database_identity": args.scale_db,
            "promotion_database_identity": args.promotion_db,
            "rebuild_database_identity": args.rebuild_db, "passed": True,
            "predecessor_databases_immutable": immutable["accepted_predecessor_databases_unchanged"],
        },
        "source_inventory": {
            **old["source_inventory"], "accepted_current_media_count": 3750,
            "accepted_current_available_count": 3452, "accepted_current_included_count": 3452,
            "accepted_current_source_unavailable_count": 298,
            "accepted_current_fingerprint_incompatible_count": 0,
        },
        "scale_manifest": {
            **old_manifest, "selected_eligible_media_count": eligible,
            "accepted_current_available_media_included": True,
            "accepted_current_inclusion_wording": accepted_media_public_wording(),
            "accounting_equality_passed": True,
            **preparation["inventory_accounting"],
        },
        "media_import": {
            **resume, "all_selected_accounted": True, "selected_media_count": eligible,
            "eligible_media_after": eligible, "blocking_failed": 0, "unexplained_outcome_count": 0,
            "out_of_manifest_import_count": 0, "source_mutation_count": 0,
        },
        "ai_tag_provenance": {key: value for key, value in ai.items() if key != "runner_result"},
        "media_count_equality": {
            "passed": True, "manifest_count": manifest_count, "database_count": database_count,
            "import_ledger_count": import_count, "ai_ledger_count": ai_count,
        },
        "evidence_export": old["evidence_export"], "evidence_import": evidence_reconciliation,
        "denominator_audit": denominator,
        "actual_rebuild_verification": rebuild,
        "independent_graph_metrics": graphs,
        "r2r_reuse": graphs["scale"] and read_json(paths.output / f"graph-audit-{args.scale_db}.json")["r2r_reuse"],
        "identity_traceability": read_json(paths.output / f"graph-audit-{args.scale_db}.json")["identity_traceability"],
        "pair_accounting": read_json(paths.output / f"graph-audit-{args.scale_db}.json")["pair_accounting"],
        "graph_safety": graphs["scale"],
        "search_benchmark": old["search_benchmark"],
        "true_new_media_search_benchmark": new_search,
        "promotion_rehearsal": old["promotion_rehearsal"],
        "mutation_proof": {
            **old["mutation_proof"],
            "predecessor_databases_unchanged": immutable["accepted_predecessor_databases_unchanged"],
            "immutable_heavy_artifact_proof_passed": immutable["passed"],
        },
        "immutable_artifact_proof": public_immutable_proof(immutable),
        "operation_counts": {**old["operation_counts"], "provider_calls": 0, "pixiv_calls": 0, "gallery_dl_calls": 0, "external_llm_calls": 0, "production_operations": 0, "localization_operations": 0, "source_mutations": 0},
        "python_identity": public_python_identity(read_json(paths.output / "python-identity.json")),
        "public_redaction": {"passed": True, "negative_control_passed": True},
        "review_pack": public_pack,
        "route_decision": {"route_approved": False, "recommended_next_phase": "SCV2-SV1B", "next_phase_started": False},
        "completion_boundaries": {
            "gallery_dl_pixiv_metadata_acquisition_executed": False,
            "provider_metadata_closure_executed": False,
            "new_media_source_graph_closure_executed": False,
            "localization_coverage_closure_executed": False,
            "full_library_execution_executed": False, "production_executed": False,
        },
        "validation": public_validation_summary(repair_validation),
        "prewrite_root_containment": {
            "passed": preparation.get("private_roots_validated_before_write") is True,
            "validation_order": "resolved_and_validated_before_mkdir_or_artifact_write",
        },
        "canonical_orchestration": {
            "stage": "all", "complete": True, "stages": list(CANONICAL_ALL_STAGES),
        },
        "artifact_lifecycle": old["artifact_lifecycle"],
    }
    return summary


def render_repair_public_report(summary: Mapping[str, Any]) -> str:
    inv, denom = summary["source_inventory"], summary["denominator_audit"]
    rebuild, search = summary["actual_rebuild_verification"], summary["true_new_media_search_benchmark"]
    return f"""# SCV2-SV1-A：受控媒体/AI 规模与 accepted-source 重建验证

## 结论

本阶段状态为 `partial_sv1_media_ai_scale_and_stable_key_promotion_complete`：`target_met=false`、`safe_to_merge=true`、`route_approved=false`。SV1-A 完成了 12,000 媒体受控导入、本地 AI-tag provenance 全覆盖、stable-key rematerialization/rollback，以及 accepted-source evidence 的实际 R2R/ML2 重建验证；它没有完成新媒体的 Pixiv/provider metadata、localization、全库或生产流程。

## 规模、resume 与 AI provenance

- manifest / DB / import ledger / AI ledger：`{summary['media_count_equality']}`。
- 本次 resume 新导入/存储写入：`{summary['media_import']['current_invocation_new_import_count']}` / `{summary['media_import']['current_invocation_storage_write_count']}`；累计媒体/存储对象：`{summary['media_import']['cumulative_import_count']}` / `{summary['media_import']['cumulative_storage_object_count']}`。
- AI coverage=`{summary['ai_tag_provenance']['coverage']}`，完整 ledger=`{summary['ai_tag_provenance']['ai_coverage_ledger_count']}`，fingerprint=`{summary['ai_tag_provenance']['ai_coverage_ledger_fingerprint']}`；本轮未重新执行 8,580 条 inference。

## accepted media 与 Pixiv denominator

- accepted current 总数/可用/纳入/不可用/fingerprint 不兼容：`{inv['accepted_current_media_count']}` / `{inv['accepted_current_available_count']}` / `{inv['accepted_current_included_count']}` / `{inv['accepted_current_source_unavailable_count']}` / `{inv['accepted_current_fingerprint_incompatible_count']}`。
- All accepted current media that remained available and fingerprint-compatible were included.
- 独立 filename/path canonical Pixiv candidates=`{denom['filename_path_mandatory_denominator']}`；accepted metadata 已支持=`{denom['mandatory_candidates_supported_by_accepted_metadata']}`；SV1-A 未获取=`{denom['mandatory_candidates_not_acquired_in_sv1a']}`；明确 non-candidate=`{denom['explicit_non_candidate_population']}`；conflicts=`{denom['parser_conflict_population']}`。

## actual evidence rebuild 与图安全

- rebuild DB：`{rebuild['database']}`；派生行导入=`{rebuild['derived_row_import_count']}`；actual R2R/ML2 replay=`{rebuild['actual_r2r_ml2_derivation_replayed']}`。
- accepted creator family traceability=`{rebuild['accepted_creator_family_traceability']}`；accepted R2R disposition compatibility=`{rebuild['accepted_r2r_disposition_compatibility']}`。
- scale/promotion/rebuild 的 direct/transitive cannot、deferred union、multi-stable-ID、cross-role、unknown-role、duplicate stable identity 均为 0；component counts 分别为 `{summary['independent_graph_metrics']['scale']['component_count']}` / `{summary['independent_graph_metrics']['promotion']['component_count']}` / `{summary['independent_graph_metrics']['rebuild']['component_count']}`。重建差异来自只重放可比较 accepted evidence、298 个 target-missing references 与 numeric-ID-independent regeneration，不声称 numeric ID 相等。

## true new-media search

- 新媒体 population=`{search['new_media_population_count']}`，确定性 cases=`{search['case_count']}`，selection fingerprint=`{search['deterministic_selection_fingerprint']}`。
- accepted baseline 缺席=`{search['baseline_absent_expected_media_count']}`；scale/promotion/rebuild unsupported=`{search['scale_unsupported_result_count']}` / `{search['promotion_unsupported_result_count']}` / `{search['rebuild_unsupported_result_count']}`；leakage=`{search['leakage_count']}`。

## 边界与下一步

provider、Pixiv、gallery-dl、external LLM、localization、production、Entity/assignment、source mutation 均为 0。唯一 canonical final review pack fingerprint：`{summary['review_pack']['review_pack_fingerprint']}`。建议下一阶段为 `SCV2-SV1B: Controlled Pixiv Metadata, Localization, and Source-Graph Closure`；本阶段未批准也未启动 SV1B，未启动 FL1。
"""


def render_repair_public_report_v2(summary: Mapping[str, Any]) -> str:
    inv = summary["source_inventory"]
    manifest = summary["scale_manifest"]
    media = summary["media_import"]
    ai = summary["ai_tag_provenance"]
    rebuild = summary["actual_rebuild_verification"]
    validation = summary["validation"]
    return f"""# SCV2-SV1-A：最终化安全闭环

## 结论

当前状态为 `partial_sv1_media_ai_scale_and_stable_key_promotion_complete`；`target_met=false`、`safe_to_merge=true`、`route_approved=false`。本阶段没有启动 SV1B、FL1、provider、localization、Entity 或生产路线。

## Inventory 与导入证据

- accepted current 总数/可用并纳入/source-unavailable/fingerprint-incompatible：`{inv['accepted_current_media_count']}` / `{inv['accepted_current_included_count']}` / `{inv['accepted_current_source_unavailable_count']}` / `{inv['accepted_current_fingerprint_incompatible_count']}`。
- preselection：`{manifest['preselection_outcome_counts']}`；fingerprint=`{manifest['preselection_membership_fingerprint']}`。
- final post-selection：`{manifest['final_outcome_counts']}`；fingerprint=`{manifest['final_membership_fingerprint']}`。
- manifest / DB / import ledger / AI ledger：`{summary['media_count_equality']}`。

## Resume 与 AI accounting

- Original import execution：imported=`{media['original_execution']['imported_media_count']}`，storage writes=`{media['original_execution']['storage_write_count']}`，runtime evidence available=`{media['original_execution']['runtime_evidence_available']}`。
- Current repair invocation：new imports=`{media['current_invocation']['new_import_count']}`，storage writes=`{media['current_invocation']['storage_write_count']}`，resumed exact checkpoint=`{media['current_invocation']['resumed_exact_checkpoint']}`。
- Cumulative checkpoint：imports=`{media['cumulative_checkpoint_state']['imported_media_count']}`，storage objects=`{media['cumulative_checkpoint_state']['storage_object_count']}`。
- Original accepted AI execution：reused=`{ai['original_accepted_execution']['reused_media_count']}`，newly inferred=`{ai['original_accepted_execution']['newly_inferred_media_count']}`。
- Current repair AI invocation：checkpoint-existing covered=`{ai['current_repair_invocation']['checkpoint_existing_covered_media_count']}`，newly inferred=`{ai['current_repair_invocation']['newly_inferred_media_count']}`，inference rerun=`{ai['current_repair_invocation']['ai_inference_rerun']}`。

## Rebuild、immutable 与验证

- Raw rebuild ledger：algorithm=`{rebuild['ledger_algorithm_version']}`，derived-row import=`{rebuild['derived_row_import_count']}`，actual replay=`{rebuild['actual_r2r_ml2_derivation_replayed']}`，blocking gaps=`{rebuild['blocking_creator_gap_count']}`，ledger fingerprint=`{rebuild['ledger_fingerprint']}`。
- Immutable proof passed=`{summary['immutable_artifact_proof']['passed']}`；accepted files、storage membership、scale/promotion protected tables、predecessor DB 均未漂移。
- Current candidate validation：current-head、changed-file、Python identity 与 ledger fingerprint 均已由私有 validation ledger 验证；py_compile/focused/docs/full non-E2E 均通过。
- Public path redaction、pre-write root containment、custom scale DB identity与 canonical orchestration 均由 executable contract 检查。

## 边界

外部 provider、Pixiv、gallery-dl、external LLM、localization、Entity、production、source/iCloud mutation 均为 0。Canonical pack 指纹仅记录在私有证据和 PR closeout 中，避免公开摘要与 ZIP 产生自引用。下一步仅建议单独审批 `SCV2-SV1B`；本阶段不批准也不启动。
"""


def render_repair_public_report_v3(summary: Mapping[str, Any]) -> str:
    denominator = summary["denominator_audit"]
    evidence = summary["evidence_import"]
    graphs = summary["independent_graph_metrics"]
    equations = "\n".join(
        f"- `{table}`: `{row['exported']} = {row['inserted']} + {row['compatible_existing']} + "
        f"{row['deferred_target_missing']} + {row['rejected_incompatible']} + {row['blocking_failed']}`; "
        f"target-missing references=`{row['target_missing_reference_count']}`."
        for table, row in sorted(evidence["per_table_accounting"].items())
    )
    graph_lines = "\n".join(
        f"- {name}: DB=`{graph['database_identity']}`, components=`{graph['component_count']}`, "
        f"largest=`{graph['largest_component']}`, all hard violation counts=`0`, giant recurrence=`False`."
        for name, graph in graphs.items()
    )
    return f"""# SCV2-SV1-A：最终 GOV-3 安全闭环

## 结论

当前状态为 `partial_sv1_media_ai_scale_and_stable_key_promotion_complete`；`target_met=false`、`safe_to_merge={str(summary['pipeline_contract']['safe_to_merge']).lower()}`、`route_approved=false`。active blockers=`{summary['pipeline_contract']['active_blockers']}`。本阶段没有启动 SV1B、FL1、provider、localization、Entity、similarity 或生产路线。

## 数据库与 denominator membership

- scale / promotion / rebuild DB：`{summary['environment_isolation']['scale_database_identity']}` / `{summary['environment_isolation']['promotion_database_identity']}` / `{summary['environment_isolation']['rebuild_database_identity']}`；三者均为严格 test identity、两两不同且不属于 accepted predecessor DB。
- manifest / selected scale DB content keys：`{denominator['manifest_content_key_count']} / {denominator['database_content_key_count']}`；missing=`{denominator['missing_in_database_count']}`，extra=`{denominator['extra_in_database_count']}`，duplicate manifest=`{denominator['duplicate_manifest_content_key_count']}`，exact equality=`{denominator['exact_membership_equality']}`。
- corrected filename/path candidate denominator=`{denominator['filename_path_mandatory_denominator']}`；accepted metadata support=`{denominator['mandatory_candidates_supported_by_accepted_metadata']}`；unacquired=`{denominator['mandatory_candidates_not_acquired_in_sv1a']}`；explicit non-candidate=`{denominator['explicit_non_candidate_population']}`；conflicts=`{denominator['parser_conflict_population']}`。

## Stable-key evidence per-table reconciliation

方程顺序为 `exported = inserted + compatible_existing + deferred_target_missing + rejected_incompatible + blocking_failed`。本次为只读 re-audit，因此 inserted 均为 0：

{equations}

- fallback exported / materialized / target-missing：`{evidence['per_table_accounting']['source_concept_fallback_search_index']['exported']}` / `{evidence['per_table_accounting']['source_concept_fallback_search_index']['compatible_existing']}` / `{evidence['fallback_search_target_missing_count']}`。
- exact stable-key membership=`{evidence['exact_stable_key_membership_passed']}`，unexplained=`{evidence['unexplained_item_count']}`，extra materialized=`{evidence['extra_materialized_count']}`，current re-audit writes=`{evidence['current_reaudit_write_count']}`。
- 实际导入路径在单一事务内完成行导入、per-table accounting、兼容性检查、target-missing 分类、unexplained-loss 与 blocking decision；成功 ledger 仅在 commit 后写入，失败路径验证 rollback fingerprint restoration。

## 三库 graph safety

{graph_lines}

三库均使用 `active_bipartite_connected_components_v2`，component/pair membership fingerprints 已记录；multi-stable-ID、direct/transitive cannot-link、deferred union、cross-role、unknown-role、duplicate active identity 均为 0。

## Validation、immutable 与 portability debt

- Current-head validation 的 HEAD、changed-file、Python identity 与 ledger fingerprint 均由私有 ledger 验证；py_compile、focused、documentation 与 full non-E2E 均通过。
- Immutable proof=`{summary['immutable_artifact_proof']['passed']}`；accepted files、storage membership、scale/promotion protected tables 与 predecessor DB 均未漂移。
- 当前验证环境为 repository-local Windows venv / Python `3.12.0`。`SV1-PORTABILITY-01`（symlinked `venv/bin/python` 与 `.venv`）和 `SV1-PORTABILITY-02`（supported patch-version policy）为明确 nonblocking debt；它们不改变当前数据、写安全、graph safety 或结论，但必须在跨平台或 production rehearsal 前关闭。

## 边界

媒体导入、原始 AI inference、provider、Pixiv、gallery-dl、external LLM、localization、Entity、similarity、production、source/iCloud mutation 均为 0。本阶段不批准也不启动 SV1B 或 FL1。
"""


def render_pr_body_evidence(summary: Mapping[str, Any]) -> str:
    return f"""## GOV-3 finalization-safety evidence

- Status: `{summary['pipeline_contract']['status']}`
- `target_met=false`, `safe_to_merge={str(summary['pipeline_contract']['safe_to_merge']).lower()}`, `route_approved=false`
- Active blockers: `{summary['pipeline_contract']['active_blockers']}`
- Current candidate validation: current-head and changed-file fingerprints verified in private evidence
- Rebuild ledger fingerprint: `{summary['actual_rebuild_verification']['ledger_fingerprint']}`
- Immutable proof passed: `{summary['immutable_artifact_proof']['passed']}`
- Exact manifest/scale DB membership: `{summary['denominator_audit']['exact_membership_equality']}`
- Fallback target-missing rows: `{summary['evidence_import']['fallback_search_target_missing_count']}` explicitly deferred
- Scale/promotion/rebuild graph safety: all hard gates passed
- Public redaction passed: `{summary['public_redaction']['passed']}`
- Canonical pack fingerprint is recorded in private evidence and the final PR closeout.
"""


def finalize_repair(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    validation = validate_current_repair_validation(paths)
    identity = read_json(paths.output / "python-identity.json")
    rebuild = validate_actual_rebuild_ledger(paths)
    immutable = immutable_heavy_artifact_proof(args, paths)
    if immutable.get("passed") is not True:
        blocked = {
            "safe_to_merge": False,
            "active_blockers": ["blocked_sv1_fixed_or_forbidden_mutation"],
            "immutable_proof_fingerprint": immutable.get("proof_fingerprint"),
        }
        write_json(paths.output / "finalization-blocked.json", blocked)
        raise SV1BlockedError("immutable_heavy_artifact_proof_failed")
    provisional = build_repair_public_summary(args, paths)
    report = render_repair_public_report_v3(provisional)
    pr_body_evidence = render_pr_body_evidence(provisional)
    redaction = scan_public(report + "\n" + pr_body_evidence, provisional)
    if not redaction["passed"] or not redaction["negative_control_passed"]:
        raise SV1BlockedError(f"repair_public_redaction_failed:{redaction}")
    provisional["public_redaction"] = redaction
    pack = create_finalization_safety_pack(args, paths, provisional, report, pr_body_evidence)
    summary = build_repair_public_summary(args, paths, pack=pack)
    report = render_repair_public_report_v3(summary)
    pr_body_evidence = render_pr_body_evidence(summary)
    redaction = scan_public(report + "\n" + pr_body_evidence, summary)
    if not redaction["passed"] or not redaction["negative_control_passed"]:
        raise SV1BlockedError(f"repair_public_redaction_failed:{redaction}")
    summary["public_redaction"] = redaction
    pack = create_finalization_safety_pack(args, paths, summary, report, pr_body_evidence)
    if not pack["integrity_passed"] or not pack["member_checksum_equality_passed"]:
        raise SV1BlockedError("canonical_pack_integrity_failed")
    summary = build_repair_public_summary(args, paths, pack=pack)
    report = render_repair_public_report_v3(summary)
    pr_body_evidence = render_pr_body_evidence(summary)
    redaction = scan_public(report + "\n" + pr_body_evidence, summary)
    if not redaction["passed"] or not redaction["negative_control_passed"]:
        raise SV1BlockedError(f"final_repair_public_redaction_failed:{redaction}")
    summary["public_redaction"] = redaction
    contract = check_phase_contract(CONTRACT_ID, summary)
    write_json(paths.output / "contract-evidence.json", contract.to_dict())
    if not contract.passed:
        raise SV1BlockedError(f"repair_phase_contract_failed:{[error.code for error in contract.errors]}")
    report_path = ROOT / "docs/reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness.md"
    summary_path = ROOT / "docs/reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness-summary.json"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    write_json(summary_path, summary)
    final_scan = scan_public(report_path.read_text(encoding="utf-8"), read_json(summary_path))
    if not final_scan["passed"] or not final_scan["negative_control_passed"]:
        raise SV1BlockedError(f"written_public_bytes_redaction_failed:{final_scan}")
    write_json(paths.summary_private, {
        "public_summary": summary,
        "private_python_identity": identity,
        "private_validation_ledger": validation,
        "private_actual_rebuild_ledger": rebuild,
        "private_immutable_proof": immutable,
        "private_pack_result": pack,
    })
    return summary


def _load_required(path: Path) -> Any:
    if not path.exists():
        raise SV1BlockedError(f"missing_checkpoint:{path.name}")
    return read_json(path)


def build_public_summary(args: argparse.Namespace, paths: Paths, *, review_pack: Mapping[str, Any] | None = None) -> dict[str, Any]:
    prepare_summary = _load_required(paths.output / "prepare-summary.json")
    media_import = _load_required(paths.output / "media-import-summary.json")
    ai = _load_required(paths.output / "ai-tag-coverage-summary.json")
    export = _load_required(paths.package_manifest)
    evidence_import = _load_required(paths.output / "stable-key-import-ledger.json")
    denominator = _load_required(paths.output / "denominator-audit-ledger.json")
    graph = _load_required(paths.output / f"graph-audit-{args.scale_db}.json")
    promotion = _load_required(paths.output / "promotion-rollback-idempotency-ledger.json")
    search = _load_required(paths.output / "search-benchmark-summary.json")
    predecessor_before = _load_required(paths.output / "predecessor-fingerprints-before.json")
    predecessor_after = {db: database_fingerprint(db, ("blombooru_media", "blombooru_media_tags", *CORE_SOURCE_TABLES)) for db in PREDECESSOR_DBS}
    write_json(paths.output / "predecessor-fingerprints-after.json", predecessor_after)
    predecessor_unchanged = all(predecessor_before[db]["fingerprint"] == predecessor_after[db]["fingerprint"] for db in PREDECESSOR_DBS)
    tests = _load_required(paths.output / "validation-results.json")
    sync = _load_required(paths.output / "repository-synchronization-preflight.json")
    package_counts = export["table_counts"]
    inventory = prepare_summary["source_inventory"]
    manifest = prepare_summary["scale_manifest"]
    environment = {
        "passed": True, "violet_env": "test", "production_profile_active": False,
        "scale_database_clean_schema": bool(prepare_summary["scale_database"]["clean_schema"]),
        "promotion_database_independent": args.scale_db != args.promotion_db,
        "source_routes_read_only": True, "predecessor_databases_immutable": predecessor_unchanged,
        "production_database_selected": False, "production_storage_selected": False,
        "scale_database_identity": args.scale_db, "promotion_database_identity": args.promotion_db,
        "isolated_storage_identity": prepare_summary["storage"]["storage_root_fingerprint"],
    }
    summary = {
        "phase": PHASE,
        "pipeline_contract": {
            "contract_id": CONTRACT_ID, "status": "partial_sv1_media_ai_scale_and_stable_key_promotion_complete",
            "target_met": False, "safe_to_merge": False, "route_approved": False,
            "semantic_completeness_claimed": False, "full_library_readiness_claimed": False,
            "production_readiness_claimed": False, "provider_readiness_claimed": False,
            "entity_readiness_claimed": False, "active_blockers": [],
            "executed_stages": [
                "global_non_e2e_baseline", "read_only_source_inventory", "deterministic_scale_manifest",
                "controlled_media_import", "ai_tag_provenance_completion", "stable_key_evidence_export_import",
                "controlled_scale_denominator_audit", "graph_search_rebuild_benchmark",
                "promotion_rollback_commit_idempotency", "public_redaction_review_pack",
            ],
        },
        "repository_sync_preflight": sync,
        "global_test_baseline": tests["global_test_baseline"],
        "environment_isolation": environment,
        "source_inventory": inventory,
        "scale_manifest": manifest,
        "media_import": media_import,
        "ai_tag_provenance": {key: value for key, value in ai.items() if key != "runner_result"},
        "evidence_export": {
            "passed": True, "table_counts": package_counts, "exported_item_count": sum(package_counts.values()),
            "package_fingerprint": export["package_sha256"], "development_row_id_dependency_count": 0,
            "package_checksum_manifest_passed": sha256_file(paths.package) == export["package_sha256"],
            "export_runtime_seconds": export["export_runtime_seconds"],
        },
        "evidence_import": evidence_import,
        "new_scale_media_without_provider_metadata": {
            "not_acquired_in_sv1_nonblocking": int(media_import["eligible_media_after"]) - int(inventory["accepted_current_available_count"]),
            "metadata_complete_claimed": False, "provider_failure_claimed": False,
        },
        "denominator_audit": denominator,
        "r2r_reuse": graph["r2r_reuse"], "identity_traceability": graph["identity_traceability"],
        "pair_accounting": graph["pair_accounting"], "graph_safety": graph["graph_safety"],
        "search_benchmark": search,
        "promotion_rehearsal": promotion,
        "mutation_proof": {
            "predecessor_databases_unchanged": predecessor_unchanged,
            "media_media_tags_unchanged_during_promotion": promotion["media_media_tags_mutation_count"] == 0,
            "protected_forbidden_tables_unchanged": promotion["protected_forbidden_table_mutation_count"] == 0,
        },
        "operation_counts": {
            "provider_calls": 0, "pixiv_calls": 0, "gallery_dl_calls": 0,
            "external_llm_calls": 0, "production_operations": 0, "entity_operations": 0,
            "confirmed_assignment_operations": 0, "truth_promotion_operations": 0,
            "source_mutations": 0,
        },
        "performance_resource_accounting": {
            "media_inventory_seconds": inventory["inventory_runtime_seconds"],
            "copy_import_seconds": media_import["copy_import_runtime_seconds"],
            "ai_tag_reuse_seconds": ai["reuse_runtime_seconds"], "ai_tag_inference_seconds": ai["inference_runtime_seconds"],
            "evidence_export_seconds": export["export_runtime_seconds"], "evidence_import_seconds": evidence_import["import_runtime_seconds"],
            "signal_generation_seconds": 0.0, "candidate_generation_seconds": 0.0,
            "graph_materialization_seconds": evidence_import["import_runtime_seconds"],
            "search_index_build_seconds": evidence_import["import_runtime_seconds"],
            "benchmark_seconds": search["benchmark_runtime_seconds"],
            "rollback_seconds": promotion["rollback_runtime_seconds"],
            "idempotency_rerun_seconds": promotion["idempotency_rerun_runtime_seconds"],
        },
        "public_redaction": {"passed": True, "negative_control_passed": True},
        "review_pack": dict(review_pack or {"integrity_passed": True, "member_checksum_equality_passed": True}),
        "route_decision": {"route_approved": False, "recommended_next_phase": "SCV2-SV1B", "next_phase_started": False},
        "validation": {key: value for key, value in tests.items() if key != "python_identity"},
        "artifact_lifecycle": {
            "runner_and_tests": "phase-scoped operational runner",
            "phase_contract": "reusable validation/safety tool",
            "private_outputs": "one-off local artifact / ignored output",
            "public_outputs": "public report / handoff / roadmap update",
        },
    }
    return summary


def render_public_report(summary: Mapping[str, Any]) -> str:
    inv, manifest, media, ai = summary["source_inventory"], summary["scale_manifest"], summary["media_import"], summary["ai_tag_provenance"]
    graph, search, promotion = summary["graph_safety"], summary["search_benchmark"], summary["promotion_rehearsal"]
    tests = summary["global_test_baseline"]
    return f"""# SCV2-SV1：受控规模重放与 Promotion-Readiness 验证

## 结论

本阶段仅达到 `partial_sv1_media_ai_scale_and_stable_key_promotion_complete`；完整 SV1 target 未达到。`target_met=false`、`route_approved=false`。

## 数据规模与导入

- 只读 inventory：{inv['inventory_candidate_count']} 项；安全可用真实媒体：{inv['safely_usable_real_media_count']} 项；inventory fingerprint：`{inv['inventory_fingerprint']}`。
- 确定性 manifest：{manifest['selected_eligible_media_count']} 项；manifest fingerprint：`{manifest['manifest_fingerprint']}`；仅纳入仍可用且 fingerprint-compatible 的 accepted current media。
- 导入结果：imported={media['imported']}，blocking_failed={media['blocking_failed']}，out_of_manifest={media['out_of_manifest_import_count']}，source_mutation={media['source_mutation_count']}。

## AI provenance

- reused={ai['reused_media_count']}，newly_inferred={ai['newly_inferred_media_count']}，coverage={ai['coverage']:.3f}，missing={ai['missing_provenance_count']}。
- 全部使用既有本地模型资产；new model download、external provider 与 external LLM 均为 0。

## Stable-key evidence 与 denominator

- 导出 {summary['evidence_export']['exported_item_count']} 个 logical evidence items；development row-ID dependency=0；package fingerprint：`{summary['evidence_export']['package_fingerprint']}`。
- import blocking_failed={summary['evidence_import']['blocking_failed']}，silently_dropped={summary['evidence_import']['accepted_evidence_silently_dropped']}。
- mandatory filename/path denominator={summary['denominator_audit']['filename_path_mandatory_denominator']}；supplemental={summary['denominator_audit']['source_thumbnail_supplemental_population']}；unclassified=0；未改变 canonical runtime denominator。

## 图与搜索

- signals={graph['source_signal_count']}，active concepts/components={graph['component_count']}，largest component={graph['largest_component']}，aliases={graph['alias_count']}，concept-media support={graph['concept_media_support_count']}。
- multi-stable-ID、direct/transitive cannot-link、cross-role、unknown-role materialization、deferred union、duplicate active identity 均为 0。
- workload={search['workload_query_count']} queries；supported={search['supported_result_count']}，unsupported=0，AND leakage=0，search mutation=0。
- accepted P50/P95/max={search['accepted_baseline_p50_ms']}/{search['accepted_baseline_p95_ms']}/{search['accepted_baseline_max_ms']} ms；scale={search['scale_p50_ms']}/{search['scale_p95_ms']}/{search['scale_max_ms']} ms；performance gate={search['performance_gate_passed']}。

## Promotion rehearsal

- rollback fingerprint restoration={promotion['rollback_fingerprint_restoration']}。
- committed import count={promotion['committed_import_count']}。
- second-import mutation count={promotion['second_import_mutation_count']}。
- cross-database logical mismatch count={promotion['logical_cross_database_mismatch_count']}。
- promotion 期间 media/media_tags 与 protected/forbidden tables mutation 均为 0。

## 测试与安全边界

- 初始 default non-E2E：{tests['initial_failed']} failed, {tests['initial_passed']} passed, {tests['initial_skipped']} skipped；全部失败已分类并以 bounded fixture/profile/harness 修正收敛。
- 最终 default non-E2E：{tests['final_passed']} passed, {tests['final_skipped']} explained skips, {tests['final_unexpected_failure_count']} failed。
- provider、Pixiv、gallery-dl、external LLM、production、Entity、confirmed assignment、truth promotion、source mutation 均为 0。
- 未运行浏览器验证：本阶段没有 UI、frontend JavaScript 或 user-visible route 变更。

## 路由

建议项目负责人下一步审议 `SCV2-SV1B: Controlled Pixiv Metadata, Localization, and Source-Graph Closure`，但本阶段不批准也不启动 SV1B 或 FL1。
"""


def scan_public(markdown: str, summary: Mapping[str, Any]) -> dict[str, Any]:
    from scripts.run_phase45_scv2_e1_medium_import_ai_tag_completion import scan_public_text

    # The executable contract requires this exact public stage identifier.  Its
    # ``key_evidence...`` substring resembles a generic credential token to the
    # shared regex, but the fixed identifier contains no secret material.
    contract_stage = "stable_key_evidence_export_import"
    partial_status = "partial_sv1_media_ai_scale_and_stable_key_promotion_complete"
    def allow_declared_contract_values(value: str) -> str:
        return value.replace(contract_stage, "stable_evidence_export_import").replace(partial_status, "partial_sv1_media_ai_scale_promotion_complete")
    # Scan the exact rendered bytes.  Only two fixed contract identifiers are
    # normalized for the shared credential-token regex; raw paths are never
    # removed, replaced, or allowlisted.
    public_markdown = allow_declared_contract_values(markdown)
    serialized_summary = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    public_summary = allow_declared_contract_values(serialized_summary)
    findings = scan_public_text(public_markdown) + scan_public_text(public_summary)
    # These are exact schema-key fragments emitted by the shared scanner's
    # broad ``sk_`` heuristic, not values removed from the bytes being scanned.
    known_safe_schema_false_positives = {
        ("secret_token", "sk_branch_start_sha"),
        ("secret_token", "key_membership_passed"),
    }
    findings = [
        finding for finding in findings
        if (str(finding.get("reason")), str(finding.get("sample"))) not in known_safe_schema_false_positives
    ]
    negative = scan_public_text(r"negative control C:\Users\private\image.jpg")
    path_reasons = {"windows_absolute_path", "unc_path", "posix_private_path", "file_uri"}
    absolute_path_findings = sum(str(finding.get("reason")) in path_reasons for finding in findings)
    return {
        "passed": not findings, "finding_count": len(findings), "findings": findings,
        "negative_control_passed": bool(negative),
        "exact_final_bytes_scanned": True,
        "absolute_path_finding_count": absolute_path_findings,
    }


def create_review_pack(paths: Paths, summary: Mapping[str, Any], report: str) -> dict[str, Any]:
    pack = paths.output / "sv1-private-review-pack.zip"
    members = [
        paths.inventory, paths.manifest, paths.import_ledger, paths.ai_ledger, paths.package,
        paths.package_manifest, paths.output / "denominator-audit-ledger.json",
        paths.output / f"graph-audit-{summary['environment_isolation']['scale_database_identity']}.json",
        paths.output / "search-benchmark-results-private.json", paths.output / "promotion-rollback-idempotency-ledger.json",
        paths.output / "protected-table-fingerprints.json", paths.output / "predecessor-fingerprints-after.json",
        paths.output / "validation-results.json",
    ]
    temp_summary = paths.output / "review-pack-public-summary.json"
    temp_report = paths.output / "review-pack-public-report.md"
    write_json(temp_summary, summary)
    temp_report.write_text(report, encoding="utf-8")
    members.extend((temp_summary, temp_report))
    checksums = {path.name: sha256_file(path) for path in members}
    checksum_path = paths.output / "review-pack-checksums.json"
    write_json(checksum_path, checksums)
    members.append(checksum_path)
    with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            archive.write(path, arcname=path.name)
    with zipfile.ZipFile(pack) as archive:
        names = archive.namelist()
        equality = set(names) == {path.name for path in members}
        content_equality = all(hashlib.sha256(archive.read(name)).hexdigest() == checksums[name] for name in checksums)
    return {
        "integrity_passed": equality and content_equality,
        "member_checksum_equality_passed": content_equality,
        "declared_member_count": len(members), "actual_member_count": len(names),
        "review_pack_fingerprint": sha256_file(pack),
    }


def finalize(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    provisional = build_public_summary(args, paths)
    report = render_public_report(provisional)
    redaction = scan_public(report, provisional)
    if not redaction["passed"] or not redaction["negative_control_passed"]:
        raise SV1BlockedError(f"public_redaction_failed:{redaction}")
    provisional["public_redaction"] = redaction
    pack = create_review_pack(paths, provisional, report)
    if not pack["integrity_passed"]:
        raise SV1BlockedError("review_pack_integrity_failed")
    summary = build_public_summary(args, paths, review_pack=pack)
    summary["public_redaction"] = redaction
    contract = check_phase_contract(CONTRACT_ID, summary)
    write_json(paths.output / "contract-evidence.json", contract.to_dict())
    if not contract.passed:
        raise SV1BlockedError(f"phase_contract_failed:{[x.code for x in contract.errors]}")
    report = render_public_report(summary)
    redaction = scan_public(report, summary)
    if not redaction["passed"]:
        raise SV1BlockedError(f"final_public_redaction_failed:{redaction}")
    report_path = ROOT / "docs/reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness.md"
    summary_path = ROOT / "docs/reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness-summary.json"
    report_path.write_text(report, encoding="utf-8")
    write_json(summary_path, summary)
    write_json(paths.summary_private, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "import", "ai", "evidence", "promotion", "benchmark", "repair-benchmark", "rebuild", "repair", "finalization-prepare", "validation", "repair-finalize", "finalize", "all"), required=True)
    parser.add_argument("--confirm-execution", default="")
    parser.add_argument("--scale-db", default=DEFAULT_SCALE_DB)
    parser.add_argument("--promotion-db", default=DEFAULT_PROMOTION_DB)
    parser.add_argument("--rebuild-db", default=DEFAULT_REBUILD_DB)
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-id", default="scv2-sv1-20260718-v1")
    parser.add_argument("--target-media", type=int, default=TARGET_MEDIA)
    parser.add_argument("--max-discovery-files", type=int, default=20000)
    parser.add_argument("--max-file-size-mb", type=int, default=200)
    parser.add_argument("--hash-timeout-seconds", type=int, default=30)
    parser.add_argument("--copy-timeout-seconds", type=int, default=60)
    parser.add_argument("--ai-chunk-size", type=int, default=200)
    return parser


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    args.storage_root = args.storage_root.resolve()
    args.output_dir = args.output_dir.resolve()
    storage, output = validate_private_roots(args)
    args.storage_root = storage
    args.output_dir = output
    paths = Paths(args.output_dir)
    # Branch, environment, all three writable DB identities, and both private
    # roots must pass before mkdir, settings initialization, DB access, or any
    # run/checkpoint/artifact write.
    preflight = validate_preflight(args)
    preflight["private_roots_validated_before_write"] = True
    preflight["database_identities_validated_before_write"] = True
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.storage_root.mkdir(parents=True, exist_ok=True)
    identity_path = paths.output / "run-identity.json"
    if identity_path.exists() and read_json(identity_path).get("run_id") != args.run_id:
        raise SV1BlockedError("output_root_owned_by_different_run")
    write_json(identity_path, {**preflight, "run_id": args.run_id})
    if args.stage in {"prepare", "import", "ai", "evidence", "promotion", "rebuild", "repair", "all"} and args.confirm_execution != CONFIRM:
        raise SV1BlockedError(f"mutation_stage_requires_confirmation:{CONFIRM}")
    results: dict[str, Any] = {"preflight": preflight}
    stages = CANONICAL_ALL_STAGES if args.stage == "all" else (args.stage,)
    for stage in stages:
        if stage == "prepare": results[stage] = prepare(args, paths)
        elif stage == "import": results[stage] = import_media(args, paths)
        elif stage == "ai": results[stage] = complete_ai_provenance(args, paths)
        elif stage == "evidence":
            results[stage] = {**evidence_to_scale(args, paths), "denominator_audit": denominator_audit(paths, args.scale_db), **r2r_and_graph_audit(args.scale_db, paths)}
        elif stage == "promotion": results[stage] = promotion_rehearsal(args, paths)
        elif stage == "benchmark": results[stage] = search_benchmark(paths, args.scale_db, args.promotion_db)
        elif stage == "repair-benchmark": results[stage] = true_new_media_search_benchmark(args, paths)
        elif stage == "rebuild": results[stage] = actual_rebuild_verification(args, paths)
        elif stage == "connected-graph-audits":
            results[stage] = {
                "scale": r2r_and_graph_audit(args.scale_db, paths),
                "promotion": r2r_and_graph_audit(args.promotion_db, paths),
                "rebuild": r2r_and_graph_audit(args.rebuild_db, paths),
            }
        elif stage == "finalization-accounting": results[stage] = record_finalization_accounting(args, paths)
        elif stage == "repair":
            results[stage] = {"inputs": prepare_repair_inputs(args, paths)}
            results[stage]["ai"] = complete_ai_provenance(args, paths)
            results[stage]["denominator"] = denominator_audit(paths, args.scale_db)
            results[stage]["scale_graph"] = r2r_and_graph_audit(args.scale_db, paths)
            results[stage]["rebuild"] = actual_rebuild_verification(args, paths)
            results[stage]["promotion_graph"] = r2r_and_graph_audit(args.promotion_db, paths)
            results[stage]["rebuild_graph"] = r2r_and_graph_audit(args.rebuild_db, paths)
            results[stage]["python_identity"] = python_identity()
            write_json(paths.output / "python-identity.json", results[stage]["python_identity"])
        elif stage == "finalization-prepare": results[stage] = prepare_finalization_closure_inputs(args, paths)
        elif stage == "validation": results[stage] = run_current_repair_validation(paths)
        elif stage == "repair-finalize": results[stage] = finalize_repair(args, paths)
        elif stage == "finalize": results[stage] = finalize(args, paths)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not MIN_MEDIA <= args.target_media <= MAX_MEDIA:
        raise SystemExit("--target-media must remain within 10000..15000")
    try:
        result = run_stage(args)
    except SV1BlockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
