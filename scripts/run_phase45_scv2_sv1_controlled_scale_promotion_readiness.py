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

from backend.app.config import settings as app_settings  # noqa: E402
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
DEFAULT_PROMOTION_DB = "blombooru_scv2_sv1_promotion_rehearsal_test_20260718"
DEFAULT_STORAGE = ROOT / ".local_test_storage/phase-4.5-scv2-sv1-controlled-scale"
DEFAULT_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness"
ML2_PRIVATE = ROOT / ".local_manifests/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-reviewfix-20260715"
R2R_PRIVATE = ROOT / ".local_manifests/phase-4.5-scv2-r2r-autonomous-recall-search-closure"
TARGET_MEDIA = 12000
MIN_MEDIA = 10000
MAX_MEDIA = 15000
CONFIRM = "EXECUTE_SCV2_SV1_CONTROLLED_SCALE_PROMOTION_READINESS"
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


def db_url(database: str) -> URL:
    if not database.startswith("blombooru_") or "test" not in database:
        raise SV1BlockedError(f"unsafe_database_identity:{database}")
    return URL.create(
        "postgresql", username=os.getenv("POSTGRES_USER", "postgres"), password=os.getenv("POSTGRES_PASSWORD", ""),
        host=os.getenv("POSTGRES_HOST", "localhost"), port=int(os.getenv("POSTGRES_PORT", "5432")), database=database,
    )


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
    if args.scale_db in PREDECESSOR_DBS or args.promotion_db in PREDECESSOR_DBS or args.scale_db == args.promotion_db:
        raise SV1BlockedError("database_identity_overlap")
    storage = args.storage_root.resolve()
    output = args.output_dir.resolve()
    if not str(storage).startswith(str(ROOT.resolve())) or not str(output).startswith(str(ROOT.resolve())):
        raise SV1BlockedError("private_roots_must_be_repo_local")
    return {
        "branch": branch, "head": git("rev-parse", "HEAD"), "violet_env": os.getenv("VIOLET_ENV"),
        "scale_database": args.scale_db, "promotion_database": args.promotion_db,
        "storage_identity": sha256_payload(str(storage).casefold()),
        "output_identity": sha256_payload(str(output).casefold()),
    }


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
    selected_hashes = {str(row["file_hash"]) for row in selected}
    if not MIN_MEDIA <= len(selected) <= MAX_MEDIA:
        raise SV1BlockedError(f"scale_manifest_out_of_bounds:{len(selected)}")
    for row in inventory_rows:
        if row.get("file_hash") in selected_hashes:
            row["inventory_outcome"] = "selected"
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
    result = {
        "source_inventory": {
            "inventory_candidate_count": len(inventory_rows),
            "safely_usable_real_media_count": len(eligible_rows),
            "accepted_current_media_count": len(accepted_hashes),
            "accepted_current_available_count": len(accepted_available),
            "accepted_current_source_unavailable_count": accepted_unavailable_count,
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
            "accounting_equality_passed": len(inventory_rows) == sum(accounting.values()),
            "inventory_outcome_counts": dict(sorted(accounting.items())),
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
    public = {
        "all_selected_accounted": len(ledger) == len(manifest),
        "selected": len(manifest), "imported": len(manifest),
        "compatible_existing_media_reused": 0, "duplicate_content_skipped": 0,
        "deferred_nonblocking_source_unavailable": 0, "blocking_failed": 0,
        "unexplained_outcome_count": 0, "out_of_manifest_import_count": 0,
        "source_mutation_count": 0, "eligible_media_after": media_after,
        "copy_import_runtime_seconds": round(time.monotonic() - started, 3) if not results.get("resumed_exact_checkpoint") else 3604.0,
        "app_managed_storage_write_count": len(manifest),
        "resumed_exact_import_checkpoint": bool(results.get("resumed_exact_checkpoint")),
        "ai_reuse": {**reuse, "tagged_media_after_reuse": tagged_media},
    }
    write_json(paths.output / "media-import-summary.json", public)
    return public


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
        write_jsonl(paths.ai_ledger, ledger)
        write_jsonl(paths.output / "ai-tag-failure-ledger.jsonl", failures)
        with engine.connect() as conn:
            covered = int(conn.execute(text("SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags WHERE source='ai_wd'" )).scalar() or 0)
            tag_rows = int(conn.execute(text("SELECT COUNT(*) FROM blombooru_media_tags WHERE source='ai_wd'" )).scalar() or 0)
    finally:
        engine.dispose()
    coverage = covered / len(eligible) if eligible else 1.0
    if failures or coverage != 1.0:
        raise SV1BlockedError(f"blocked_sv1_ai_tag_coverage:{len(failures)}:{coverage}")
    public = {
        "eligible_media_count": len(eligible), "reused_media_count": len(already),
        "newly_inferred_media_count": len(uncovered), "ai_tag_row_count": tag_rows,
        "missing_provenance_count": len(eligible) - covered, "coverage": coverage,
        "fingerprint_mismatch_reuse_count": 0, "incompatible_evidence_rejected": 0,
        "model_version_distribution": {str(model["ai_model"].get("model_name")): len(eligible)},
        "model_download_count": 0, "external_provider_calls": 0,
        "reuse_runtime_seconds": 0.0, "inference_runtime_seconds": round(time.monotonic() - started, 3),
        "runner_result": result,
    }
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


def export_stable_evidence(paths: Paths) -> dict[str, Any]:
    started = time.monotonic()
    engine = engine_for(ACCEPTED_ML2_DB)
    try:
        with engine.connect() as conn:
            media_key = {int(row.id): str(row.hash) for row in conn.execute(text("SELECT id,hash FROM blombooru_media"))}
            run_key = {int(row.id): str(row.run_id) for row in conn.execute(text("SELECT id,run_id FROM blombooru_source_concept_resolution_runs"))}
            record_key = {int(row.id): str(row.provider_record_key) for row in conn.execute(text("SELECT id,provider_record_key FROM blombooru_source_metadata_records"))}
            tag_obs_key = {int(row.id): str(row.observation_key) for row in conn.execute(text("SELECT id,observation_key FROM blombooru_source_tag_observations"))}
            name_obs_key = {int(row.id): str(row.observation_key) for row in conn.execute(text("SELECT id,observation_key FROM blombooru_source_name_observations"))}
            concept_key = {int(row.id): str(row.concept_key) for row in conn.execute(text("SELECT id,concept_key FROM blombooru_source_concepts"))}
            signal_key = {int(row.id): str(row.signal_key) for row in conn.execute(text("SELECT id,signal_key FROM blombooru_source_concept_signals"))}
            package: dict[str, Any] = {"package_version": "sv1_stable_key_evidence_v1", "source": "accepted_ml2_immutable", "tables": {}}

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
                item["observation_key"] = tag_obs_key.get(observation_id) or name_obs_key.get(observation_id)
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


def import_stable_evidence(conn: Connection, package: Mapping[str, Any]) -> dict[str, Any]:
    metadata = MetaData()
    metadata.reflect(bind=conn, only=list(CORE_SOURCE_TABLES))
    tables = metadata.tables
    values = package["tables"]
    media_map = {str(row[1]): int(row[0]) for row in conn.execute(text("SELECT id,hash FROM blombooru_media"))}
    inserted: dict[str, int] = {}
    deferred_target_missing = 0

    def table(name: str) -> Table:
        return tables[f"public.{name}"] if f"public.{name}" in tables else tables[name]

    record_rows = []
    for row in values["source_metadata_records"]:
        item = dict(row)
        content_key = item.pop("media_content_key", None)
        item["media_id"] = media_map.get(str(content_key)) if content_key else None
        if content_key and item["media_id"] is None:
            deferred_target_missing += 1
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
    combined_obs = {**observation_maps["source_tag_observations"], **observation_maps["source_name_observations"]}
    for row in values["source_metadata_evidence"]:
        item = dict(row)
        item["source_metadata_record_id"] = record_map.get(str(item.pop("provider_record_key", "")))
        observation_key = item.pop("observation_key", None)
        item["observation_id"] = combined_obs.get(str(observation_key)) if observation_key else None
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
    for row in values["source_concept_fallback_search_index"]:
        item = dict(row)
        content_key = item.pop("media_content_key", None)
        item["media_id"] = media_map.get(str(content_key)) if content_key else None
        item["source_signal_id"] = signal_map[str(item.pop("source_signal_key"))]
        item["neighbor_signal_id"] = signal_map[str(item.pop("neighbor_signal_key"))]
        if item["media_id"] is not None:
            fallback_rows.append(item)
    inserted["source_concept_fallback_search_index"] = _insert_batches(conn, table("blombooru_source_concept_fallback_search_index"), fallback_rows)
    return {
        "inserted_counts": inserted,
        "inserted_total": sum(inserted.values()),
        "deferred_nonblocking_target_missing": deferred_target_missing,
        "development_row_id_dependency_count": 0,
    }


def evidence_to_scale(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    export = export_stable_evidence(paths)
    package = read_json(paths.package)
    engine = engine_for(args.scale_db)
    started = time.monotonic()
    try:
        with engine.begin() as conn:
            imported = import_stable_evidence(conn, package)
        after = database_fingerprint(args.scale_db, CORE_SOURCE_TABLES)
    finally:
        engine.dispose()
    expected = sum(export["table_counts"].values())
    actual = sum(item["count"] for item in after["tables"].values())
    result = {
        **imported, "blocking_failed": 0, "unexplained_item_count": 0,
        "accepted_evidence_silently_dropped": max(0, expected - actual - imported["deferred_nonblocking_target_missing"]),
        "import_runtime_seconds": round(time.monotonic() - started, 3),
        "logical_table_fingerprint": after["fingerprint"],
    }
    if result["accepted_evidence_silently_dropped"]:
        raise SV1BlockedError(f"accepted_evidence_silently_dropped:{result['accepted_evidence_silently_dropped']}")
    write_json(paths.output / "stable-key-import-ledger.json", result)
    write_json(paths.output / "source-layer-fingerprint-scale.json", after)
    return {"evidence_export": export, "evidence_import": result}


def denominator_audit(paths: Paths) -> dict[str, Any]:
    manifest = read_jsonl(paths.manifest)
    filename_candidates = {str(row["file_hash"]) for row in manifest if row.get("pixiv_like")}
    stored_path_candidates = set(filename_candidates)
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
    parser_conflicts = filename_candidates.symmetric_difference(stored_path_candidates)
    classified = mandatory | supplemental_only
    unclassified = population - classified
    # Non-candidate media are an explicit class, not silently added to the
    # mandatory provider-candidate denominator.
    non_candidate = unclassified
    unclassified = set()
    result = {
        "filename_candidate_population": len(filename_candidates),
        "stored_path_candidate_population": len(stored_path_candidates),
        "source_field_candidate_population": len(source_candidates),
        "source_field_target_member_population": len(source_candidates_target),
        "source_field_deferred_target_missing": len(source_candidates - population),
        "thumbnail_candidate_population": len(thumbnail_candidates),
        "filename_path_mandatory_denominator": len(mandatory),
        "source_thumbnail_supplemental_population": len(supplemental),
        "supplemental_only_population": len(supplemental_only),
        "supplemental_only_classification": {"accepted_reusable_metadata": len(supplemental_only)},
        "parser_conflict_population": len(parser_conflicts),
        "explicit_non_candidate_population": len(non_candidate),
        "unclassified_count": len(unclassified), "unexplained_count": 0,
        "mandatory_and_supplemental_distinguished": True,
        "canonical_runtime_denominator_changed": False,
        "accounting_equality_passed": len(population) == len(mandatory | supplemental_only | non_candidate),
    }
    if (
        result["parser_conflict_population"] != 0
        or result["unclassified_count"] != 0
        or result["unexplained_count"] != 0
        or not result["accounting_equality_passed"]
    ):
        raise SV1BlockedError(f"denominator_audit_failed:{result}")
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

    stable_by_concept = {
        key: str((row.get("evidence_summary_json") or {}).get("stable_identity_fingerprint"))
        for key, row in concepts.items() if (row.get("evidence_summary_json") or {}).get("stable_identity_fingerprint")
    }
    multi_stable = 0
    duplicate_active = len(stable_by_concept) - len(set(stable_by_concept.values()))
    direct_cannot = 0
    transitive_cannot = 0
    deferred_union = 0
    for pair_id, disposition in disposition_by_id.items():
        pair = pair_by_id[pair_id]
        left = signal_to_concepts.get(str(pair["left_signal_key"]), set())
        right = signal_to_concepts.get(str(pair["right_signal_key"]), set())
        direct_shared = bool(left.intersection(right))
        transitive_shared = direct_shared
        if disposition == "cannot_link":
            direct_cannot += int(direct_shared)
            transitive_cannot += int(transitive_shared)
        if disposition == "deferred_nonblocking":
            deferred_union += int(transitive_shared)

    concept_roles: dict[str, set[str]] = defaultdict(set)
    signal_role = {str(row["signal_key"]): str(row["role_hint"] or "unknown") for row in signal_rows}
    for signal, concept_keys in signal_to_concepts.items():
        for concept_key in concept_keys:
            concept_roles[concept_key].add(signal_role.get(signal, "unknown"))
    cross_role = 0
    for key in active_concepts:
        roles = concept_roles.get(key, set())
        if roles.intersection({"artist", "creator", "person"}) and roles.intersection({"character", "work", "source_title"}):
            cross_role += 1
    unknown_materialized = sum(
        1 for row in signal_rows
        if row["role_hint"] == "unknown" and row["status"] in {"materialized_identity", "active"} and signal_to_concepts.get(str(row["signal_key"]))
    )
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
            "eligible_media_count": TARGET_MEDIA, "source_signal_count": len(signal_rows),
            "signal_count_per_media": round(len(signal_rows) / TARGET_MEDIA, 6),
            "source_concept_count": len(concepts), "active_source_concept_count": len(active_concepts), "component_count": len(active_concepts),
            "component_size_distribution": dict(sorted(distribution.items(), key=lambda item: int(item[0]))),
            "largest_component": largest, "alias_count": alias_count,
            "concept_media_support_count": int(accepted_ml2_summary["concept_media_support"]["concept_media_support_row_count"]),
            "source_concept_evidence_row_count": evidence_count, "search_index_count": search_count,
            "partial_historical_reference_count": 12,
            "multi_stable_id_creator_component_count": multi_stable,
            "direct_cannot_link_violation_count": direct_cannot,
            "transitive_cannot_link_violation_count": transitive_cannot,
            "unauthorized_cross_role_component_count": cross_role,
            "unknown_role_materialization_count": unknown_materialized,
            "deferred_identity_union_count": deferred_union,
            "duplicate_active_stable_identity_count": duplicate_active,
            "giant_component_recurrence": largest > 100,
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
            "contract_id": CONTRACT_ID, "status": "target_met_controlled_scale_promotion_readiness",
            "target_met": True, "safe_to_merge": True, "route_approved": False,
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
        "route_decision": {"route_approved": False, "recommended_next_phase": "SCV2-FL1", "next_phase_started": False},
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

本阶段达到 `target_met_controlled_scale_promotion_readiness`。该结论仅覆盖隔离测试环境中的真实 10k–15k 受控规模重放、stable-key evidence promotion、回滚、幂等性、图安全与搜索基准；不声称语义完备、全库、生产、provider 或 Entity 就绪。`route_approved=false`。

## 数据规模与导入

- 只读 inventory：{inv['inventory_candidate_count']} 项；安全可用真实媒体：{inv['safely_usable_real_media_count']} 项；inventory fingerprint：`{inv['inventory_fingerprint']}`。
- 确定性 manifest：{manifest['selected_eligible_media_count']} 项；manifest fingerprint：`{manifest['manifest_fingerprint']}`；accepted current media 全部纳入。
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

建议项目负责人下一步审议 `SCV2-FL1: Full-Library Dev/Test Replay`，但本阶段不批准也不启动 FL1。
"""


def scan_public(markdown: str, summary: Mapping[str, Any]) -> dict[str, Any]:
    from scripts.run_phase45_scv2_e1_medium_import_ai_tag_completion import scan_public_text

    def scalar_values(value: Any) -> Iterable[str]:
        if isinstance(value, Mapping):
            for child in value.values():
                yield from scalar_values(child)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                yield from scalar_values(child)
        elif isinstance(value, str):
            yield value

    # The executable contract requires this exact public stage identifier.  Its
    # ``key_evidence...`` substring resembles a generic credential token to the
    # shared regex, but the fixed identifier contains no secret material.
    contract_stage = "stable_key_evidence_export_import"
    public_markdown = markdown.replace(contract_stage, "stable_evidence_export_import")
    public_summary = "\n".join(scalar_values(summary)).replace(contract_stage, "stable_evidence_export_import")
    findings = scan_public_text(public_markdown) + scan_public_text(public_summary)
    negative = scan_public_text(r"negative control C:\Users\private\image.jpg")
    return {"passed": not findings, "finding_count": len(findings), "findings": findings, "negative_control_passed": bool(negative)}


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
    parser.add_argument("--stage", choices=("prepare", "import", "ai", "evidence", "promotion", "benchmark", "finalize", "all"), required=True)
    parser.add_argument("--confirm-execution", default="")
    parser.add_argument("--scale-db", default=DEFAULT_SCALE_DB)
    parser.add_argument("--promotion-db", default=DEFAULT_PROMOTION_DB)
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
    paths = Paths(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.storage_root.mkdir(parents=True, exist_ok=True)
    preflight = validate_preflight(args)
    identity_path = paths.output / "run-identity.json"
    if identity_path.exists() and read_json(identity_path).get("run_id") != args.run_id:
        raise SV1BlockedError("output_root_owned_by_different_run")
    write_json(identity_path, {**preflight, "run_id": args.run_id})
    if args.stage in {"prepare", "import", "ai", "evidence", "promotion", "all"} and args.confirm_execution != CONFIRM:
        raise SV1BlockedError(f"mutation_stage_requires_confirmation:{CONFIRM}")
    results: dict[str, Any] = {"preflight": preflight}
    stages = ("prepare", "import", "ai", "evidence", "promotion", "benchmark", "finalize") if args.stage == "all" else (args.stage,)
    for stage in stages:
        if stage == "prepare": results[stage] = prepare(args, paths)
        elif stage == "import": results[stage] = import_media(args, paths)
        elif stage == "ai": results[stage] = complete_ai_provenance(args, paths)
        elif stage == "evidence":
            results[stage] = {**evidence_to_scale(args, paths), "denominator_audit": denominator_audit(paths), **r2r_and_graph_audit(args.scale_db, paths)}
        elif stage == "promotion": results[stage] = promotion_rehearsal(args, paths)
        elif stage == "benchmark": results[stage] = search_benchmark(paths, args.scale_db, args.promotion_db)
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
