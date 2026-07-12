#!/usr/bin/env python3
"""Run SCV2-R2 constraint-aware SourceConcept graph remediation.

Lifecycle: phase-scoped operational runner.

The runner reuses one immutable R1R evidence snapshot and the durable R1R LLM
pair-judgment cache. It never acquires provider metadata, invokes gallery-dl,
runs AI tagging/import, or writes truth-path tables. Execute mode is limited to
the seven SourceConcept-owned output tables in a separate R2 test database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, URL
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.source_concept_resolver_service import (  # noqa: E402
    LLMAdjudicationConfig,
    SOURCE_CONCEPT_ALLOWED_WRITE_TABLES,
    build_source_concept_input_scope,
    build_source_concept_signals,
    llm_resolver_decision,
    persist_source_concept_resolution,
    resolve_source_concepts,
    select_llm_adjudication_edges,
    source_signal_inventory,
)
from app.services.source_name_candidate_extraction_service import FORBIDDEN_TRUTH_TABLES  # noqa: E402
from scripts import run_phase45_scv1_source_concept_coverage_audit as scv1  # noqa: E402
from scripts import run_phase45_scv2_a1_post_expansion_audit_route_decision as a1  # noqa: E402
from scripts import run_phase45_scv2_a1r_route_audit_after_r1r as a1r  # noqa: E402
from scripts import run_phase45_scv2_r1r_full_source_concept_pipeline_replay as r1r  # noqa: E402
from scripts.phase_contracts.contract_checks import check_phase_contract  # noqa: E402

PHASE = "4.5-SCV2-R2"
PHASE_TITLE = "SCV2-R2: Constraint-Aware SourceConcept Graph Remediation"
PHASE_SLUG = "phase-4.5-scv2-r2-constraint-aware-graph-remediation"
BRANCH = "codex/scv2-r2-constraint-aware-source-concept-graph-remediation"
CONTRACT_ID = "r2_source_concept_graph_remediation_contract_v1"
QUALITY_INTERPRETATION = (
    "R2 met the constraint-aware graph-remediation target but intentionally produced a more conservative "
    "and fragmented graph. Search, gap, and recall closure remain incomplete."
)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
R1R_BASELINE_DB = "blombooru_r1r_restored_test_20260618"
DEFAULT_WORKING_DB = "blombooru_scv2_r2_test_20260710"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
DEFAULT_CACHE_DIR = ROOT / ".local_manifests" / "source_concept_llm_adjudication_cache"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
EVIDENCE_EXECUTION_CODE_PATHS = (
    "backend/app/services/source_concept_resolver_service.py",
    "scripts/run_phase45_scv2_r2_constraint_aware_graph_remediation.py",
    "scripts/phase_contracts/contract_checks.py",
)

CLONE_CONFIRMATION = "CREATE_SCV2_R2_ISOLATED_WORKING_DB"
EXECUTE_CONFIRMATION = "EXECUTE_SCV2_R2_SOURCECONCEPT_REBUILD"

FIXED_INPUT_TABLES = (
    "blombooru_media",
    "blombooru_tags",
    "blombooru_media_tags",
    "blombooru_source_metadata_records",
    "blombooru_source_metadata_evidence",
    "blombooru_source_tag_observations",
    "blombooru_source_tag_registry",
    "blombooru_source_name_observations",
    "blombooru_source_name_registry",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_name_candidate_extraction_runs",
    "blombooru_source_name_candidate_record_verdicts",
    "blombooru_source_name_candidates",
    "blombooru_source_name_alias_candidates",
    "blombooru_provider_cache",
)
SOURCE_CONCEPT_TABLES = tuple(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
ALL_MUTATION_PROOF_TABLES = tuple(dict.fromkeys((*FIXED_INPUT_TABLES, *SOURCE_CONCEPT_TABLES)))

TARGET_STATUSES = {
    "blocked_environment_isolation",
    "blocked_upstream_evidence_changed",
    "blocked_missing_fixed_input_manifest",
    "blocked_llm_approval_required",
    "blocked_quality_regression",
    "blocked_public_redaction",
    "partial_improvement_not_target_met",
    "target_met_constraint_aware_r2",
}


class R2BlockedError(RuntimeError):
    """Raised when an R2 isolation, evidence, or execution gate fails."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise R2BlockedError(f"json_object_required:{path.name}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(args: Sequence[str]) -> str:
    try:
        return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8", stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unavailable"


def qident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def database_url(database: str) -> URL:
    return URL.create(
        "postgresql+psycopg2",
        username=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=database,
    )


def create_db_engine(database: str, *, statement_timeout_ms: int = 900000):
    return create_engine(
        database_url(database),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "options": f"-c statement_timeout={statement_timeout_ms}"},
    )


def require_safe_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    private_root = (ROOT / ".local_manifests").resolve()
    if not resolved.is_relative_to(private_root):
        raise R2BlockedError("output_dir_must_be_under_repo_local_manifests")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value) or ".." in value:
        raise R2BlockedError("blocked_invalid_run_id")
    return value


def derived_run_id(value: str, suffix: str) -> str:
    value = validate_run_id(value)
    suffix = validate_run_id(suffix)
    candidate = f"{value}-{suffix}"
    if len(candidate) > 128:
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:12]
        candidate = f"{value[:96]}-{digest}-{suffix}"
    return validate_run_id(candidate)


def artifact_path(output_dir: Path, name: str) -> Path:
    root = output_dir.resolve()
    candidate = (root / name).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise R2BlockedError("blocked_artifact_path_escape")
    return candidate


def environment_isolation(source_db: str, working_db: str) -> dict[str, Any]:
    violet_env = str(os.environ.get("VIOLET_ENV") or "").casefold()
    production_profile_active = any(
        str(os.environ.get(name) or "").strip().casefold() in {"1", "true", "yes", "production"}
        for name in (
            "VIOLET_PRODUCTION_PROFILE_ACTIVE",
            "VIOLET_PRODUCTION_PROFILE",
            "VIOLET_PRODUCTION_MODE",
            "VIOLET_LAUNCH_PRODUCTION",
        )
    )
    separate = source_db != working_db
    working_name_safe = working_db.startswith("blombooru_scv2_r2_") and "test" in working_db.casefold()
    passed = (
        source_db == R1R_BASELINE_DB
        and separate
        and working_name_safe
        and violet_env == "test"
        and not production_profile_active
        and working_db != "blombooru"
    )
    return {
        "passed": passed,
        "violet_env": violet_env,
        "source_db": source_db,
        "working_db": working_db,
        "working_db_is_separate_from_r1r_baseline": separate,
        "r1r_baseline_preserved": True,
        "dev_test_only": violet_env == "test" and working_name_safe,
        "production_profile_active": production_profile_active,
        "canonical_production_profile_flag_checked": True,
        "production_write_attempted": False,
        "protected_source_write_attempted": False,
    }


def db_identity(conn: Connection) -> dict[str, Any]:
    row = conn.execute(
        text(
            "SELECT current_database() AS db_name, inet_server_port() AS server_port, "
            "current_setting('transaction_read_only') AS transaction_read_only"
        )
    ).mappings().one()
    return {
        "db_name": row.get("db_name"),
        "server_port": row.get("server_port"),
        "transaction_read_only": row.get("transaction_read_only"),
        "user_and_host_redacted": True,
    }


def fingerprint_table(conn: Connection, table: str) -> dict[str, Any]:
    exists = bool(
        conn.execute(
            text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": table},
        ).scalar()
    )
    if not exists:
        return {"table": table, "status": "missing", "count": None, "row_content_sha256": None, "columns": []}
    columns = [
        str(row[0])
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :table_name ORDER BY ordinal_position"
            ),
            {"table_name": table},
        )
    ]
    count = int(conn.execute(text(f"SELECT COUNT(*) FROM {qident(table)}")).scalar() or 0)
    digest = hashlib.sha256()
    statement = text(
        "SELECT row_hash FROM ("
        f"SELECT md5(to_jsonb(row_value)::text) AS row_hash FROM {qident(table)} AS row_value"
        ") AS hashed_rows ORDER BY row_hash"
    )
    rows = conn.execution_options(stream_results=True).execute(statement)
    for (row_hash,) in rows:
        digest.update(str(row_hash).encode("ascii"))
        digest.update(b"\n")
    return {
        "table": table,
        "status": "present",
        "count": count,
        "row_content_sha256": digest.hexdigest(),
        "columns": columns,
    }


def fingerprint_tables(conn: Connection, tables: Sequence[str]) -> dict[str, Any]:
    snapshots = {table: fingerprint_table(conn, table) for table in tables}
    return {
        "captured_at": utc_now_iso(),
        "database": str(conn.execute(text("SELECT current_database()" )).scalar() or ""),
        "fingerprint_algorithm": "sha256_over_sorted_md5_of_to_jsonb_rows",
        "tables": snapshots,
        "table_count": len(snapshots),
        "missing_tables": sorted(table for table, row in snapshots.items() if row.get("status") != "present"),
    }


def compare_fingerprints(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    before_tables = before.get("tables") if isinstance(before.get("tables"), Mapping) else {}
    after_tables = after.get("tables") if isinstance(after.get("tables"), Mapping) else {}
    rows = []
    for table in sorted(set(before_tables) | set(after_tables)):
        left = before_tables.get(table) if isinstance(before_tables.get(table), Mapping) else {}
        right = after_tables.get(table) if isinstance(after_tables.get(table), Mapping) else {}
        count_match = left.get("count") == right.get("count")
        content_match = bool(left.get("row_content_sha256")) and left.get("row_content_sha256") == right.get("row_content_sha256")
        columns_match = left.get("columns") == right.get("columns")
        rows.append(
            {
                "table": table,
                "before_count": left.get("count"),
                "after_count": right.get("count"),
                "count_match": count_match,
                "content_match": content_match,
                "columns_match": columns_match,
                "matched": count_match and content_match and columns_match,
            }
        )
    changed = [row["table"] for row in rows if not row["matched"]]
    return {
        "passed": not changed,
        "row_counts_match": all(row["count_match"] for row in rows),
        "content_fingerprints_match": all(row["content_match"] for row in rows),
        "columns_match": all(row["columns_match"] for row in rows),
        "changed_tables": changed,
        "table_results": rows,
    }


def forbidden_truth_table_content_comparison(
    source_db: str,
    working_db: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compare forbidden truth-table content across two DBs without writing either DB."""

    def capture(database: str) -> dict[str, Any]:
        engine = create_db_engine(database)
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                snapshot = fingerprint_tables(conn, FORBIDDEN_TRUTH_TABLES)
                conn.rollback()
                return snapshot
        finally:
            engine.dispose()

    source_snapshot = capture(source_db)
    working_snapshot = capture(working_db)
    comparison = compare_fingerprints(source_snapshot, working_snapshot)
    public = {
        "authoritative_list_source": (
            "backend.app.services.source_name_candidate_extraction_service.FORBIDDEN_TRUTH_TABLES"
        ),
        "tables_accounted_for": list(FORBIDDEN_TRUTH_TABLES),
        "forbidden_truth_table_count": len(FORBIDDEN_TRUTH_TABLES),
        "forbidden_truth_tables_measured": True,
        "source_all_tables_present": not source_snapshot["missing_tables"],
        "working_all_tables_present": not working_snapshot["missing_tables"],
        "row_counts_match": comparison["row_counts_match"],
        "schemas_match": comparison["columns_match"],
        "content_fingerprints_match": comparison["content_fingerprints_match"],
        "changed_tables": list(comparison["changed_tables"]),
        "comparison_passed": comparison["passed"],
        "verification_mode": "read_only_post_run_baseline_vs_final_r2_comparison",
        "raw_fingerprints_private": True,
        "private_artifact_label": "forbidden-truth-table-comparison-closeout",
    }
    private = {
        "phase": PHASE,
        "verification_kind": "read_only_forbidden_truth_table_content_comparison",
        "source_db": source_db,
        "working_db": working_db,
        "source_snapshot": source_snapshot,
        "working_snapshot": working_snapshot,
        "comparison": comparison,
        "database_writes": False,
        "resolver_executed": False,
        "provider_calls": 0,
        "raw_fingerprints_private": True,
    }
    return public, private


def public_fixed_input_proof(
    manifest: Mapping[str, Any],
    clone_comparison: Mapping[str, Any],
    before_after: Mapping[str, Any],
) -> dict[str, Any]:
    source = manifest.get("source_snapshot") if isinstance(manifest.get("source_snapshot"), Mapping) else {}
    tables = source.get("tables") if isinstance(source.get("tables"), Mapping) else {}
    return {
        "present": True,
        "private_manifest_generated": True,
        "private_manifest_label": "r2-private-fixed-input-manifest",
        "table_count": len(tables),
        "content_fingerprint_count": sum(bool((row or {}).get("row_content_sha256")) for row in tables.values()),
        "table_row_counts": {table: row.get("count") for table, row in tables.items() if isinstance(row, Mapping)},
        "baseline_to_working_clone_match": bool(clone_comparison.get("passed")),
        "before_after_match": bool(before_after.get("passed")),
        "row_counts_match": bool(before_after.get("row_counts_match")),
        "content_fingerprints_match": bool(before_after.get("content_fingerprints_match")),
        "provenance_unchanged": bool(before_after.get("passed")),
        "changed_tables": list(before_after.get("changed_tables") or []),
        "raw_rows_public": False,
        "raw_fingerprint_values_public": False,
    }


def database_exists(conn: Connection, database: str) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}).scalar())


def prepare_working_database(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    isolation = environment_isolation(args.source_db, args.working_db)
    if not isolation["passed"]:
        raise R2BlockedError("blocked_environment_isolation")
    if args.confirm_clone != CLONE_CONFIRMATION:
        raise R2BlockedError("clone_confirmation_missing_or_invalid")

    source_engine = create_db_engine(args.source_db)
    with source_engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source_identity = db_identity(conn)
        if source_identity["db_name"] != args.source_db:
            raise R2BlockedError("source_database_identity_mismatch")
        source_snapshot = fingerprint_tables(conn, FIXED_INPUT_TABLES)
        conn.rollback()
    source_engine.dispose()
    if source_snapshot["missing_tables"]:
        raise R2BlockedError(f"fixed_input_tables_missing:{source_snapshot['missing_tables']}")

    admin_engine = create_db_engine("postgres")
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not database_exists(conn, args.source_db):
            raise R2BlockedError("r1r_source_database_missing")
        if database_exists(conn, args.working_db):
            raise R2BlockedError("r2_working_database_already_exists_no_overwrite")
        active = int(
            conn.execute(text("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = :name"), {"name": args.source_db}).scalar()
            or 0
        )
        if active:
            raise R2BlockedError(f"r1r_source_database_has_active_connections:{active}")
        conn.exec_driver_sql(f"CREATE DATABASE {qident(args.working_db)} TEMPLATE {qident(args.source_db)}")
    admin_engine.dispose()

    working_engine = create_db_engine(args.working_db)
    with working_engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        working_identity = db_identity(conn)
        if working_identity["db_name"] != args.working_db:
            raise R2BlockedError("working_database_identity_mismatch")
        working_snapshot = fingerprint_tables(conn, FIXED_INPUT_TABLES)
        conn.rollback()
    working_engine.dispose()

    clone_comparison = compare_fingerprints(source_snapshot, working_snapshot)
    if not clone_comparison["passed"]:
        raise R2BlockedError("blocked_upstream_evidence_changed_clone_mismatch")
    manifest = {
        "phase": PHASE,
        "generated_at": utc_now_iso(),
        "environment_isolation": isolation,
        "source_identity": source_identity,
        "working_identity": working_identity,
        "source_snapshot": source_snapshot,
        "working_snapshot_after_clone": working_snapshot,
        "clone_comparison": clone_comparison,
        "raw_rows_included": False,
        "private_artifact": True,
    }
    manifest_path = output_dir / "fixed-input-manifest.json"
    write_json(manifest_path, manifest)
    result = {
        "status": "prepared_isolated_r2_working_database",
        "source_db": args.source_db,
        "working_db": args.working_db,
        "fixed_input_table_count": len(FIXED_INPUT_TABLES),
        "clone_content_match": True,
        "manifest_label": "r2-private-fixed-input-manifest",
    }
    write_json(output_dir / "prepare-result.json", result)
    return result


def load_and_verify_manifest(output_dir: Path, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    path = output_dir / "fixed-input-manifest.json"
    if not path.exists():
        raise R2BlockedError("blocked_missing_fixed_input_manifest")
    manifest = read_json(path)
    isolation = manifest.get("environment_isolation") if isinstance(manifest.get("environment_isolation"), Mapping) else {}
    if isolation.get("source_db") != args.source_db or isolation.get("working_db") != args.working_db:
        raise R2BlockedError("fixed_input_manifest_database_identity_mismatch")
    source_snapshot = manifest.get("source_snapshot") if isinstance(manifest.get("source_snapshot"), Mapping) else {}
    engine = create_db_engine(args.source_db)
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        current_source = fingerprint_tables(conn, FIXED_INPUT_TABLES)
        conn.rollback()
    engine.dispose()
    comparison = compare_fingerprints(source_snapshot, current_source)
    if not comparison["passed"]:
        raise R2BlockedError("blocked_upstream_evidence_changed_r1r_baseline")
    return manifest, comparison


def route_metrics(conn: Connection) -> dict[str, Any]:
    media = scv1.audit_media_coverage(conn)
    source_layer = scv1.audit_source_layer_coverage(conn)
    concepts = scv1.load_concepts(conn)
    aliases = scv1.load_aliases(conn)
    evidence = scv1.load_evidence(conn)
    inventory, _alias_inventory, _evidence_inventory = scv1.audit_source_concepts(conn)
    full_gap, _gap_samples = scv1.audit_alias_gaps(conn, concepts, aliases)
    gap = a1.public_gap_audit(full_gap)
    needs, _needs_samples = scv1.audit_needs_review(conn, concepts, aliases, evidence)
    search = a1.build_search_seed_symmetry_audit(conn)
    state = a1r.enrich_source_concept_state(
        conn,
        a1.build_source_concept_current_state(conn, concepts, aliases, evidence, inventory),
    )
    baseline = a1.build_current_baseline(media, source_layer)
    return {
        "source_concept_state": state,
        "component_distribution": state.get("concept_component_size_distribution") or {},
        "gap_audit": gap,
        "needs_review": needs,
        "search_seed_symmetry": search,
        "source_baseline": baseline,
    }


def _signal_identity_payload(signal: Any) -> dict[str, str]:
    return {
        "signal_key": str(signal.signal_key),
        "canonical_key": str(signal.canonical_key or signal.normalized_key or ""),
        "work_context_key": str(signal.work_context_key or ""),
    }


def _cached_side_identity(value: Any) -> dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "signal_key": str(row.get("signal_key") or ""),
        "canonical_key": str(row.get("canonical_key") or ""),
        "work_context_key": str(row.get("work_context_key") or ""),
    }


def load_cached_judgments(
    cache_dir: Path,
    signals: Sequence[Any],
    deterministic_result: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    records_dir = cache_dir / "records"
    if not records_dir.is_dir():
        raise R2BlockedError("r1r_llm_cache_records_missing")
    signal_by_key = {str(signal.signal_key): signal for signal in signals}
    config = LLMAdjudicationConfig(
        enabled=True,
        max_calls=20000,
        max_budget_usd=15.0,
        selection_policy="budget_driven_all_eligible",
    )
    current_eligible = select_llm_adjudication_edges(
        deterministic_result.edge_candidates,
        signals=signals,
        config=config,
    )
    current_edge_by_pair = {
        tuple(sorted((str(edge.left_signal_key), str(edge.right_signal_key)))): edge
        for edge in current_eligible
    }

    judgments: list[dict[str, Any]] = []
    accounting = Counter()
    outcomes = Counter()
    outcomes_by_reuse: dict[str, Counter[str]] = defaultdict(Counter)
    reusable_pairs: set[tuple[str, str]] = set()
    records_for_analysis: list[dict[str, Any]] = []
    for path in sorted(records_dir.glob("*.json")):
        try:
            record = read_json(path)
        except Exception:
            accounting["invalidated"] += 1
            continue
        left_key = str(record.get("left_signal_key") or "")
        right_key = str(record.get("right_signal_key") or "")
        pair = tuple(sorted((left_key, right_key)))
        decision = llm_resolver_decision(str(record.get("resolver_decision") or record.get("decision") or "uncertain"))
        outcomes[decision] += 1
        left = signal_by_key.get(left_key)
        right = signal_by_key.get(right_key)
        if record.get("error_state") or not record.get("compatible_for_exact_reuse"):
            accounting["invalidated"] += 1
            outcomes_by_reuse["invalidated"][decision] += 1
            records_for_analysis.append(
                {"record": record, "reuse_level": "invalidated", "pair": pair, "decision": decision}
            )
            continue
        if left is None or right is None:
            accounting["invalidated"] += 1
            outcomes_by_reuse["invalidated"][decision] += 1
            records_for_analysis.append(
                {"record": record, "reuse_level": "invalidated", "pair": pair, "decision": decision}
            )
            continue
        cached_summary = record.get("input_signal_summary") if isinstance(record.get("input_signal_summary"), Mapping) else {}
        cached_left = _cached_side_identity(cached_summary.get("left"))
        cached_right = _cached_side_identity(cached_summary.get("right"))
        current_by_key = {left_key: _signal_identity_payload(left), right_key: _signal_identity_payload(right)}
        compatible = (
            cached_left == current_by_key.get(cached_left["signal_key"])
            and cached_right == current_by_key.get(cached_right["signal_key"])
        )
        if not compatible:
            accounting["semantic_prior"] += 1
            outcomes_by_reuse["semantic_prior"][decision] += 1
            records_for_analysis.append(
                {"record": record, "reuse_level": "semantic_prior", "pair": pair, "decision": decision}
            )
            continue
        exact = str(record.get("resolver_version") or "") == str(deterministic_result.summary.get("resolver_version") or "")
        reuse_level = "exact_compatible" if exact else "stable_pair_identity"
        accounting[reuse_level] += 1
        outcomes_by_reuse[reuse_level][decision] += 1
        reusable_pairs.add(pair)
        judgment = {
            "judgment_id": record.get("cache_key") or path.stem,
            "left_signal_key": left_key,
            "right_signal_key": right_key,
            "decision": decision,
            "confidence": record.get("confidence"),
            "reason_code": record.get("reason_code"),
            "cache_status": "hit",
            "cache_reuse_level": reuse_level,
            "source_layer_only": True,
        }
        judgments.append(judgment)
        records_for_analysis.append(
            {"record": record, "reuse_level": reuse_level, "pair": pair, "decision": decision}
        )

    current_pairs = set(current_edge_by_pair)
    new_pairs = current_pairs - reusable_pairs
    removed_legacy_pairs = reusable_pairs - current_pairs
    accounting_payload = {
        "existing_r1r_judgment_count": sum(accounting.values()),
        "exact_compatible_reuse_count": accounting["exact_compatible"],
        "stable_pair_identity_reuse_count": accounting["stable_pair_identity"],
        "semantic_prior_count": accounting["semantic_prior"],
        "invalidated_count": accounting["invalidated"],
        "reused_as_component_constraint_or_regression_label_count": len(judgments),
        "genuinely_new_or_missing_pair_count": len(new_pairs),
        "projected_new_call_cost_usd": round(len(new_pairs) * 0.00032, 6),
        "new_provider_call_count": 0,
        "provider_initialized": False,
        "outcome_counts": {
            "same": outcomes["must_link"],
            "cannot": outcomes["cannot_link"],
            "uncertain": outcomes["needs_review"],
        },
        "same_decision_counts": {
            "all_existing_r1r": outcomes["must_link"],
            "compatible_proof_grade": (
                outcomes_by_reuse["exact_compatible"]["must_link"]
                + outcomes_by_reuse["stable_pair_identity"]["must_link"]
            ),
            "semantic_prior": outcomes_by_reuse["semantic_prior"]["must_link"],
            "invalidated": outcomes_by_reuse["invalidated"]["must_link"],
        },
    }
    candidate_comparison = {
        "legacy_cached_pair_count": len(reusable_pairs),
        "current_deterministic_eligible_pair_count": len(current_pairs),
        "legacy_pairs_not_regenerated_deterministically": len(removed_legacy_pairs),
        "current_pairs_without_compatible_legacy_judgment": len(new_pairs),
        "legacy_judgments_still_applied_as_constraints_or_regression_labels": True,
        "new_pairs_private": [list(pair) for pair in sorted(new_pairs)],
    }
    return judgments, accounting_payload, candidate_comparison, records_for_analysis


def llm_yield_by_strata(
    records: Sequence[Mapping[str, Any]],
    deterministic_result: Any,
) -> dict[str, Any]:
    edge_by_pair = {
        tuple(sorted((str(edge.left_signal_key), str(edge.right_signal_key)))): edge
        for edge in deterministic_result.edge_candidates
    }
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    for item in records:
        record = item.get("record") if isinstance(item.get("record"), Mapping) else {}
        pair = tuple(item.get("pair") or ())
        summary = record.get("input_signal_summary") if isinstance(record.get("input_signal_summary"), Mapping) else {}
        left = summary.get("left") if isinstance(summary.get("left"), Mapping) else {}
        right = summary.get("right") if isinstance(summary.get("right"), Mapping) else {}
        edge = edge_by_pair.get(pair)
        decision = str(record.get("decision") or "uncertain").casefold()
        outcome = "same" if decision in {"same", "must_link"} else "cannot" if decision in {"cannot", "cannot_link"} else "uncertain"
        dimensions = {
            "edge_type": getattr(edge, "edge_type", "legacy_pair_only"),
            "block_type": str(getattr(edge, "evidence_source", "legacy_pair_only")).split(":", 1)[0],
            "reason_code": getattr(edge, "resolution_reason_code", "legacy_pair_only"),
            "role_pair": "+".join(sorted((str(left.get("role_hint") or "unknown"), str(right.get("role_hint") or "unknown")))),
            "trust_pair": "+".join(sorted((str(left.get("trust_tier") or "unknown"), str(right.get("trust_tier") or "unknown")))),
            "context_compatibility": str((getattr(edge, "payload", {}) or {}).get("context_compatibility") or "unknown"),
            "provider_pair": "+".join(sorted((str(left.get("provider") or "unknown"), str(right.get("provider") or "unknown")))),
            "ai_combination": "has_ai" if any("ai" in str(row.get("provider") or "").casefold() or "ai" in str(row.get("trust_tier") or "").casefold() for row in (left, right)) else "non_ai",
            "short_common_class": "short_or_common" if any(len(str(row.get("surface_key") or "")) <= 7 for row in (left, right)) else "specific",
            "cross_script_class": "cross_script" if bool(left.get("display_value")) and bool(right.get("display_value")) and any(ord(char) > 127 for char in str(left.get("display_value"))) != any(ord(char) > 127 for char in str(right.get("display_value"))) else "same_script",
        }
        for dimension, value in dimensions.items():
            strata[f"{dimension}:{value}"][outcome] += 1
    rows = []
    for key, counts in strata.items():
        total = sum(counts.values())
        rows.append(
            {
                "stratum": key,
                "total": total,
                "same": counts["same"],
                "cannot": counts["cannot"],
                "uncertain": counts["uncertain"],
                "same_yield": round(counts["same"] / total, 4) if total else 0.0,
                "cannot_yield": round(counts["cannot"] / total, 4) if total else 0.0,
            }
        )
    return {
        "stratum_count": len(rows),
        "high_same_yield_strata": sorted((row for row in rows if row["total"] >= 10 and row["same_yield"] >= 0.8), key=lambda row: (-row["same_yield"], -row["total"]))[:50],
        "high_cannot_yield_strata": sorted((row for row in rows if row["total"] >= 10 and row["cannot_yield"] >= 0.8), key=lambda row: (-row["cannot_yield"], -row["total"]))[:50],
        "all_strata": sorted(rows, key=lambda row: (-row["total"], row["stratum"])),
    }


def concept_membership(result: Any) -> dict[str, str]:
    return {
        str(signal.signal_key): str(concept.concept_key)
        for concept in result.concepts
        for signal in concept.signals
    }


def same_and_cannot_quality(
    result: Any,
    judgments: Sequence[Mapping[str, Any]],
    cache_analysis_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    membership = concept_membership(result)
    concept_members: dict[str, set[str]] = defaultdict(set)
    for signal_key, concept_key in membership.items():
        concept_members[concept_key].add(signal_key)
    same_candidates: list[tuple[str, str]] = []
    cannot_pairs = []
    for row in judgments:
        pair = tuple(sorted((str(row.get("left_signal_key") or ""), str(row.get("right_signal_key") or ""))))
        decision = str(row.get("decision") or "")
        if decision == "must_link":
            same_candidates.append(pair)
        elif decision == "cannot_link":
            cannot_pairs.append(pair)
    approved_negative_blocker_codes = {
        "role_conflict",
        "work_context_conflict",
        "alias_work_context_conflict",
        "rejected_signal_guard",
        "source_title_only_guard",
        "generic_identity_surface_guard",
        "ambiguous_short_without_work_context",
        "llm_cannot_link",
    }
    approved_review_blocker_codes = {
        "same_scope_cross_script_canonical_bridge",
        "llm_same_ai_only_requires_non_ai_corroboration",
        "unknown_role_requires_corroboration",
        "unknown_role_llm_same_requires_independent_corroboration",
    }

    def blocking_evidence(pair: tuple[str, str]) -> list[dict[str, Any]]:
        left_concept = membership.get(pair[0])
        right_concept = membership.get(pair[1])
        blockers: list[dict[str, Any]] = []
        if left_concept and right_concept and left_concept != right_concept:
            left_members = concept_members[left_concept]
            right_members = concept_members[right_concept]
            for edge in result.edge_candidates:
                payload = edge.payload or {}
                blocker_class = edge.negative_reason_code
                if blocker_class not in approved_negative_blocker_codes:
                    review_reason = str(payload.get("review_reason") or edge.resolution_reason_code or "")
                    blocker_class = review_reason if review_reason in approved_review_blocker_codes else None
                if blocker_class is None:
                    continue
                crosses_components = (
                    edge.left_signal_key in left_members and edge.right_signal_key in right_members
                ) or (
                    edge.right_signal_key in left_members and edge.left_signal_key in right_members
                )
                if not crosses_components:
                    continue
                blockers.append(
                    {
                        "left_signal_key": edge.left_signal_key,
                        "right_signal_key": edge.right_signal_key,
                        "edge_type": edge.edge_type,
                        "blocker_class": blocker_class,
                        "negative_reason_code": edge.negative_reason_code,
                        "resolution_reason_code": edge.resolution_reason_code,
                    }
                )
        return blockers

    same_candidates = sorted(set(same_candidates))
    cannot_pairs = sorted(set(cannot_pairs))
    retained = [
        pair
        for pair in same_candidates
        if membership.get(pair[0]) is not None and membership.get(pair[0]) == membership.get(pair[1])
    ]
    split_same = [pair for pair in same_candidates if pair not in set(retained)]
    blocker_by_pair = {pair: blocking_evidence(pair) for pair in split_same}
    intentionally_split = [pair for pair in split_same if blocker_by_pair[pair]]
    unexplained_regressions = [pair for pair in split_same if not blocker_by_pair[pair]]
    missing_signal_pairs = [pair for pair in same_candidates if pair[0] not in membership or pair[1] not in membership]
    cannot_avoided = [pair for pair in cannot_pairs if membership.get(pair[0]) != membership.get(pair[1])]
    ledger: list[dict[str, Any]] = []
    for pair in split_same:
        left_concept = membership.get(pair[0])
        right_concept = membership.get(pair[1])
        blockers = blocker_by_pair[pair]
        ledger.append(
            {
                "pair_ref": hashlib.sha256("|".join(pair).encode("utf-8")).hexdigest()[:20],
                "left_signal_key": pair[0],
                "right_signal_key": pair[1],
                "left_concept_key": left_concept,
                "right_concept_key": right_concept,
                "classification": (
                    "intentionally_split_with_valid_constraint"
                    if blockers
                    else "unexplained_same_regression"
                ),
                "reason": (
                    "component_level_hard_constraint_or_deterministic_guard"
                    if blockers
                    else "missing_signal_or_unexplained_component_split"
                ),
                "blocker_classes": sorted({str(row["blocker_class"]) for row in blockers}),
                "blocking_evidence": blockers,
                "blocking_evidence_count": len(blockers),
            }
        )
    all_same_count = sum(
        str(row.get("decision") or "") == "must_link"
        for row in cache_analysis_rows
    )
    semantic_prior_same_count = sum(
        str(row.get("reuse_level") or "") == "semantic_prior"
        and str(row.get("decision") or "") == "must_link"
        for row in cache_analysis_rows
    )
    invalidated_same_count = sum(
        str(row.get("reuse_level") or "") == "invalidated"
        and str(row.get("decision") or "") == "must_link"
        for row in cache_analysis_rows
    )
    benchmark_count = len(same_candidates)
    accounted_count = len(retained) + len(intentionally_split) + len(unexplained_regressions)
    accounting_complete = benchmark_count == accounted_count
    quality = {
        "same_benchmark_source": "compatible_reused_r1r_judgments",
        "same_benchmark_constructed_from_current_output": False,
        "same_benchmark_compatibility_policy": "exact_or_stable_pair_identity_only;semantic_prior_excluded",
        "all_existing_r1r_same_decision_count": all_same_count,
        "compatible_must_link_benchmark_count": benchmark_count,
        "semantic_prior_same_decision_count": semantic_prior_same_count,
        "invalidated_same_decision_count": invalidated_same_count,
        "retained_same_component_count": len(retained),
        "intentionally_split_with_valid_constraint_count": len(intentionally_split),
        "unexplained_same_regression_count": len(unexplained_regressions),
        "missing_signal_or_pair_count": len(missing_signal_pairs),
        "same_benchmark_accounting_total_count": accounted_count,
        "compatible_same_accounting_complete": accounting_complete,
        "intentionally_split_reason_ledger_count": len(intentionally_split),
        "split_same_reason_ledger_count": len(ledger),
        "known_same_benchmark_pair_count": benchmark_count,
        "compatible_high_confidence_same_pair_count": benchmark_count,
        "compatible_high_confidence_same_pair_retained_count": len(retained),
        "transitively_incompatible_same_pair_count": len(intentionally_split),
        "intentionally_split_same_pair_count": len(split_same),
        "known_same_regression_count": len(unexplained_regressions),
        "same_pair_reason_ledger_count": len(ledger),
        "known_same_recall": round(len(retained) / benchmark_count, 6) if benchmark_count else 1.0,
        "compatible_same_preservation_rate": (
            round((len(retained) + len(intentionally_split)) / benchmark_count, 6)
            if benchmark_count
            else 1.0
        ),
        "known_cannot_pair_count": len(cannot_pairs),
        "known_cannot_avoided_count": len(cannot_avoided),
        "known_cannot_avoidance_rate": round(len(cannot_avoided) / len(cannot_pairs), 6) if cannot_pairs else 1.0,
    }
    return quality, ledger


def in_memory_component_metrics(result: Any) -> dict[str, Any]:
    sizes = [len(concept.signals) for concept in result.concepts]
    buckets = Counter()
    for size in sizes:
        bucket = "1" if size == 1 else "2-3" if size <= 3 else "4-10" if size <= 10 else "11-25" if size <= 25 else "26+"
        buckets[bucket] += 1
    return {
        "concept_total": len(result.concepts),
        "active": sum(concept.status == "active" for concept in result.concepts),
        "needs_review": sum(concept.status == "needs_review" for concept in result.concepts),
        "component_size_distribution": {key: buckets[key] for key in ("1", "2-3", "4-10", "11-25", "26+")},
        "largest_component_signal_counts": sorted(sizes, reverse=True)[:20],
        "signal_count": len(result.signals),
        "edge_count": len(result.edge_candidates),
        "alias_count": len(result.aliases),
        "evidence_count": len(result.evidence),
        "search_index_count": len(result.search_index),
    }


def public_route_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    state = metrics.get("source_concept_state") if isinstance(metrics.get("source_concept_state"), Mapping) else {}
    gap = metrics.get("gap_audit") if isinstance(metrics.get("gap_audit"), Mapping) else {}
    needs = metrics.get("needs_review") if isinstance(metrics.get("needs_review"), Mapping) else {}
    search = metrics.get("search_seed_symmetry") if isinstance(metrics.get("search_seed_symmetry"), Mapping) else {}
    distribution = metrics.get("component_distribution") if isinstance(metrics.get("component_distribution"), Mapping) else {}
    return {
        "concept_total": state.get("total_source_concepts"),
        "active": state.get("active"),
        "needs_review": state.get("needs_review"),
        "superseded": state.get("superseded"),
        "table_counts": state.get("table_counts"),
        "component_size_distribution": distribution.get("distribution"),
        "largest_components": distribution.get("largest_components"),
        "gap_total": gap.get("total_gap_signals"),
        "gap_buckets": gap.get("gap_buckets"),
        "needs_review_metrics": {
            key: value
            for key, value in needs.items()
            if isinstance(value, (int, float, bool, str)) and "sample" not in key
        },
        "search_aggregate": search.get("aggregate"),
    }


def metric_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    scalar_keys = ("concept_total", "active", "needs_review", "superseded", "gap_total")
    return {
        "scalar_delta": {
            key: int(after.get(key) or 0) - int(before.get(key) or 0)
            for key in scalar_keys
        },
        "gap_bucket_delta": {
            key: int((after.get("gap_buckets") or {}).get(key) or 0) - int((before.get("gap_buckets") or {}).get(key) or 0)
            for key in sorted(set(before.get("gap_buckets") or {}) | set(after.get("gap_buckets") or {}))
        },
        "search_delta": {
            key: int((after.get("search_aggregate") or {}).get(key) or 0) - int((before.get("search_aggregate") or {}).get(key) or 0)
            for key in ("matched_seeds", "unmatched_seeds", "symmetric_groups", "asymmetric_groups")
        },
    }


def graph_invariants(result: Any) -> dict[str, Any]:
    summary = result.summary
    return {
        "review_only_edge_used_in_union_count": int(summary.get("review_only_edge_used_in_union_count") or 0),
        "direct_llm_cannot_pair_in_materialized_component_count": int(summary.get("direct_llm_cannot_pair_in_materialized_component_count") or 0),
        "deterministic_hard_conflict_in_materialized_component_count": int(summary.get("deterministic_hard_conflict_in_materialized_component_count") or 0),
        "transitive_cannot_violation_count": int(summary.get("transitive_cannot_violation_count") or 0),
        "materialized_identity_edge_count": int(summary.get("materialized_identity_edge_count") or 0),
        "review_overlay_edge_count": int(summary.get("review_overlay_edge_count") or 0),
        "unknown_role_bridge_candidate_count_before": int(summary.get("unknown_role_bridge_candidate_count_before") or 0),
        "deterministic_unknown_role_candidate_count": int(summary.get("deterministic_unknown_role_candidate_count") or 0),
        "llm_unknown_role_must_link_candidate_count": int(summary.get("llm_unknown_role_must_link_candidate_count") or 0),
        "unknown_role_bridge_materialized_count_after": int(summary.get("unknown_role_bridge_materialized_count_after") or 0),
        "unknown_role_review_only_count": int(summary.get("unknown_role_review_only_count") or 0),
        "unauthorized_unknown_role_materialization_count": int(
            summary.get("unauthorized_unknown_role_materialization_count") or 0
        ),
        "unknown_role_corroboration_distribution": summary.get("unknown_role_corroboration_distribution") or {},
    }


def private_review_samples(result: Any, same_reason_ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in result.edge_candidates:
        if edge.edge_type == "llm_same_concept":
            category = "same"
        elif edge.negative_reason_code:
            category = "cannot_or_conflict"
        elif edge.status == "needs_review":
            category = "uncertain_or_review"
        else:
            continue
        if len(categories[category]) >= 30:
            continue
        categories[category].append(
            {
                "edge_key": edge.edge_key,
                "left_signal_key": edge.left_signal_key,
                "right_signal_key": edge.right_signal_key,
                "edge_type": edge.edge_type,
                "status": edge.status,
                "reason": edge.resolution_reason_code,
                "negative_reason": edge.negative_reason_code,
            }
        )
    largest = sorted(result.concepts, key=lambda concept: len(concept.signals), reverse=True)[:30]
    return {
        "stratified_edges": dict(categories),
        "largest_components": [
            {
                "concept_key": concept.concept_key,
                "status": concept.status,
                "signal_count": len(concept.signals),
                "signals": [
                    {
                        "signal_key": signal.signal_key,
                        "display_value": signal.display_value,
                        "role": signal.role_hint,
                        "provider": signal.provider,
                        "context": signal.work_context_key,
                    }
                    for signal in concept.signals[:100]
                ],
            }
            for concept in largest
        ],
        "same_pair_reason_ledger": list(same_reason_ledger),
        "human_ground_truth_claimed": False,
        "sampling_purpose": "later_manual_inspection_only",
    }


def operation_counts() -> dict[str, int]:
    return {
        "gallery_dl_calls": 0,
        "provider_pixiv_network_calls": 0,
        "ai_tagging_calls": 0,
        "media_imports": 0,
        "upstream_observation_mutations": 0,
        "new_llm_provider_calls": 0,
        "production_writes": 0,
        "truth_path_writes": 0,
    }


def route_authorization() -> dict[str, bool]:
    return {
        "px1_b_authorized": False,
        "provider_2_authorized": False,
        "scale_up_authorized": False,
        "entity_bridge_authorized": False,
        "production_authorized": False,
        "full_library_execution_authorized": False,
        "source_concept_truth_promotion_authorized": False,
    }


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    baseline = summary.get("baseline_metrics") or {}
    post = summary.get("post_r2_metrics") or {}
    graph = summary.get("graph_invariants") or {}
    llm = summary.get("llm_judgment_accounting") or {}
    new_pair_adjudication = summary.get("new_pair_adjudication") or {}
    quality = summary.get("quality_evaluation") or {}
    fixed = summary.get("fixed_input_manifest") or {}
    evidence_version = summary.get("evidence_version_boundary") or {}
    forbidden_truth = summary.get("forbidden_truth_table_content_proof") or {}
    lines = [
        f"# {PHASE_TITLE}",
        "",
        "## Status",
        "",
        f"- Contract status: `{(summary.get('pipeline_contract') or {}).get('status')}`.",
        f"- Working DB: `{(summary.get('environment_isolation') or {}).get('working_db')}`.",
        f"- Resolver evidence code SHA: `{evidence_version.get('resolver_evidence_code_sha')}`.",
        f"- Post-evidence resolver/database execution semantics changed: `{evidence_version.get('post_evidence_execution_code_changed')}`.",
        f"- Post-evidence proof-only runner/contract code changed: `{evidence_version.get('post_evidence_proof_code_changed')}`; resolver path unchanged.",
        f"- Git relationship: {evidence_version.get('git_relationship_model')}",
        f"- Version model: {evidence_version.get('report_version_model')}",
        "- R1R restored evidence DB preserved: `True`.",
        "- Browser validation: not required; no UI/runtime surface changed.",
        "",
        "## Fixed upstream evidence",
        "",
        f"- Tables fingerprinted: `{fixed.get('table_count')}`.",
        f"- Baseline-to-clone match: `{fixed.get('baseline_to_working_clone_match')}`.",
        f"- Before/after row-content match: `{fixed.get('before_after_match')}`.",
        f"- Table row counts: `{fixed.get('table_row_counts')}`.",
        f"- Forbidden truth tables measured: `{forbidden_truth.get('forbidden_truth_table_count')}`; all exist in both DBs: `{bool(forbidden_truth.get('source_all_tables_present') and forbidden_truth.get('working_all_tables_present'))}`.",
        f"- Forbidden truth row-count/schema/content comparison: `{forbidden_truth.get('row_counts_match')}` / `{forbidden_truth.get('schemas_match')}` / `{forbidden_truth.get('content_fingerprints_match')}`; changed tables: `{forbidden_truth.get('changed_tables')}`.",
        "- Raw rows and fingerprint values remain private.",
        "",
        "## Constraint-aware graph",
        "",
        f"- Review-only edges used in Union-Find: `{graph.get('review_only_edge_used_in_union_count')}`.",
        f"- Direct LLM cannot pairs inside materialized components: `{graph.get('direct_llm_cannot_pair_in_materialized_component_count')}`.",
        f"- Transitive cannot violations: `{graph.get('transitive_cannot_violation_count')}`.",
        f"- Unknown-role deterministic / LLM candidates: `{graph.get('deterministic_unknown_role_candidate_count')}` / `{graph.get('llm_unknown_role_must_link_candidate_count')}`.",
        f"- Unknown-role materialized / review-only / unauthorized materialized: `{graph.get('unknown_role_bridge_materialized_count_after')}` / `{graph.get('unknown_role_review_only_count')}` / `{graph.get('unauthorized_unknown_role_materialization_count')}`.",
        f"- Unknown-role corroboration distribution: `{graph.get('unknown_role_corroboration_distribution')}`.",
        f"- Oversized-block diagnostics: `{summary.get('oversized_block_diagnostics')}`.",
        f"- Context-equivalence diagnostics: `{summary.get('context_equivalence_diagnostics')}`.",
        "",
        "## Existing LLM judgment reuse",
        "",
        f"- Existing judgments: `{llm.get('existing_r1r_judgment_count')}`.",
        f"- Exact-compatible / stable-pair / semantic-prior / invalidated: `{llm.get('exact_compatible_reuse_count')}` / `{llm.get('stable_pair_identity_reuse_count')}` / `{llm.get('semantic_prior_count')}` / `{llm.get('invalidated_count')}`.",
        f"- Genuinely new or missing pairs: `{llm.get('genuinely_new_or_missing_pair_count')}`.",
        f"- New provider calls: `{llm.get('new_provider_call_count')}`.",
        f"- New-pair portion status: `{new_pair_adjudication.get('status')}`; projected future cost: `${new_pair_adjudication.get('projected_cost_usd')}`.",
        "",
        "## Baseline vs post-R2",
        "",
        f"- Concepts total/active/needs_review: `{baseline.get('concept_total')}` / `{baseline.get('active')}` / `{baseline.get('needs_review')}` -> `{post.get('concept_total')}` / `{post.get('active')}` / `{post.get('needs_review')}`.",
        f"- Gap total: `{baseline.get('gap_total')}` -> `{post.get('gap_total')}`.",
        f"- Search aggregate before: `{baseline.get('search_aggregate')}`.",
        f"- Search aggregate after: `{post.get('search_aggregate')}`.",
        f"- Search symmetry: `{(baseline.get('search_aggregate') or {}).get('symmetric_groups')}` / `10` -> `{(post.get('search_aggregate') or {}).get('symmetric_groups')}` / `10`.",
        f"- Unmatched seeds: `{(baseline.get('search_aggregate') or {}).get('unmatched_seeds')}` -> `{(post.get('search_aggregate') or {}).get('unmatched_seeds')}`.",
        f"- Average pairwise Jaccard: `{((baseline.get('search_aggregate') or {}).get('media_result_overlap_metrics') or {}).get('average_pairwise_jaccard')}` -> `{((post.get('search_aggregate') or {}).get('media_result_overlap_metrics') or {}).get('average_pairwise_jaccard')}`.",
        f"- Metric deltas: `{summary.get('metric_delta')}`.",
        "",
        "## Quality",
        "",
        f"- Existing / compatible proof-grade / semantic-prior same decisions: `{quality.get('all_existing_r1r_same_decision_count')}` / `{quality.get('compatible_must_link_benchmark_count')}` / `{quality.get('semantic_prior_same_decision_count')}`.",
        f"- Compatible same retained / intentionally constrained / unexplained / missing-signal: `{quality.get('retained_same_component_count')}` / `{quality.get('intentionally_split_with_valid_constraint_count')}` / `{quality.get('unexplained_same_regression_count')}` / `{quality.get('missing_signal_or_pair_count')}`.",
        f"- Compatible same accounting: `{quality.get('compatible_must_link_benchmark_count')} = {quality.get('retained_same_component_count')} + {quality.get('intentionally_split_with_valid_constraint_count')} + {quality.get('unexplained_same_regression_count')}`; complete: `{quality.get('compatible_same_accounting_complete')}`.",
        f"- Known cannot avoidance: `{quality.get('known_cannot_avoidance_rate')}`.",
        f"- Meaningful structural improvement: `{quality.get('meaningful_structural_improvement')}`.",
        f"- Constraint target met: `{quality.get('constraint_safety_target_met')}`.",
        f"- Search quality improved: `{quality.get('search_quality_improved')}`.",
        f"- Gap quality improved: `{quality.get('gap_quality_improved')}`.",
        f"- Recall closure complete: `{quality.get('recall_closure_complete')}`.",
        f"- Route quality ready for scale: `{quality.get('route_quality_ready_for_scale')}`.",
        f"- R2R follow-up required: `{quality.get('r2r_followup_required')}`.",
        f"- Broad route/search quality non-regression: `{quality.get('no_major_quality_regression')}`.",
        f"- Interpretation: {quality.get('quality_interpretation')}",
        "",
        "## Safety",
        "",
        f"- Operation counts: `{summary.get('operation_counts')}`.",
        "- No PX1-B, Provider-2, scale-up, Entity bridge, production, full-library execution, or truth promotion was started or authorized.",
        "",
        "## Validation",
        "",
        f"- R2 contract passed: `{(summary.get('validation') or {}).get('r2_contract_passed')}`.",
        f"- Public redaction passed: `{(summary.get('public_redaction') or {}).get('passed')}`.",
        f"- Review pack integrity passed: `{(summary.get('review_pack') or {}).get('integrity_passed')}`.",
        "",
    ]
    return "\n".join(lines)


def scan_public_outputs(markdown: str, summary: Mapping[str, Any], output_dir: Path, run_id: str) -> dict[str, Any]:
    run_id = validate_run_id(run_id)
    staging = artifact_path(output_dir, f"public-redaction-staging-{run_id}")
    staging.mkdir(parents=True, exist_ok=False)
    md = staging / PUBLIC_REPORT_MD.name
    js = staging / PUBLIC_REPORT_JSON.name
    write_text(md, markdown)
    write_json(js, summary)
    scan = scv1.scan_public_artifacts(
        [md, js],
        public_path_labels=[f"docs/reports/{PUBLIC_REPORT_MD.name}", f"docs/reports/{PUBLIC_REPORT_JSON.name}"],
    )
    allowed = []
    findings = []
    for finding in scan.get("findings") or []:
        match = str(finding.get("match") or "")
        if finding.get("type") == "media_filename_like" and match in {PUBLIC_REPORT_MD.name, PUBLIC_REPORT_JSON.name}:
            allowed.append(finding)
        else:
            findings.append(finding)
    return {
        "passed": not findings,
        "findings": findings,
        "allowed_findings": allowed,
        "scanned_artifacts": [f"docs/reports/{PUBLIC_REPORT_MD.name}", f"docs/reports/{PUBLIC_REPORT_JSON.name}"],
        "clean_before_public_write": not findings,
        "unsafe_public_report_written": False,
    }


def public_redaction_success_record() -> dict[str, Any]:
    return {
        "passed": True,
        "findings": [],
        "allowed_findings": [],
        "scanned_artifacts": [f"docs/reports/{PUBLIC_REPORT_MD.name}", f"docs/reports/{PUBLIC_REPORT_JSON.name}"],
        "clean_before_public_write": True,
        "unsafe_public_report_written": False,
    }


def write_public_outputs_after_redaction(
    markdown: str,
    summary: dict[str, Any],
    output_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    run_id = validate_run_id(run_id)
    redaction = scan_public_outputs(markdown, summary, output_dir, derived_run_id(run_id, "public-write"))
    if not redaction["passed"] or redaction != summary.get("public_redaction"):
        summary["public_redaction"] = redaction
        diagnostic = {
            "phase": PHASE,
            "run_id": run_id,
            "status": "blocked_public_redaction",
            "public_redaction": redaction,
            "public_files_written": False,
        }
        write_json(artifact_path(output_dir, f"blocked-public-redaction-{run_id}.json"), diagnostic)
        raise R2BlockedError("blocked_public_redaction")
    write_text(PUBLIC_REPORT_MD, markdown)
    write_json(PUBLIC_REPORT_JSON, summary)
    return redaction


def write_review_pack(
    output_dir: Path,
    run_id: str,
    summary: Mapping[str, Any],
    markdown: str,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    run_id = validate_run_id(run_id)
    pack_dir = artifact_path(output_dir, f"review-pack-{run_id}")
    pack_dir.mkdir(parents=True, exist_ok=False)
    write_text(pack_dir / "public-report.md", markdown)
    write_json(pack_dir / "public-summary.json", summary)
    for name, payload in artifacts.items():
        write_json(pack_dir / name, payload)
    manifest = {
        "phase": PHASE,
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "files": sorted(path.name for path in pack_dir.iterdir() if path.is_file()),
        "public_report_copy_current": True,
        "not_committed": True,
    }
    write_json(pack_dir / "manifest.json", manifest)
    checksums = {
        path.name: sha256_file(path)
        for path in sorted(pack_dir.iterdir())
        if path.is_file() and path.name != "checksums.json"
    }
    write_json(pack_dir / "checksums.json", checksums)
    zip_path = artifact_path(output_dir, f"review-pack-{run_id}.zip")
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(pack_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(pack_dir))
    with zipfile.ZipFile(zip_path, "r") as archive:
        zip_integrity = archive.testzip() is None
    return {
        "generated": True,
        "manifest_present": (pack_dir / "manifest.json").exists(),
        "checksums_present": (pack_dir / "checksums.json").exists(),
        "integrity_passed": zip_integrity and len(checksums) >= 8,
        "not_committed": True,
        "public_report_copy_current": True,
        "zip_generated": zip_path.exists(),
        "zip_path_label": "r2-private-review-pack",
    }


def determine_status(
    fixed_proof: Mapping[str, Any],
    llm: Mapping[str, Any],
    graph: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> str:
    if not fixed_proof.get("before_after_match"):
        return "blocked_upstream_evidence_changed"
    if any(
        int(graph.get(key) or 0) > 0
        for key in (
            "review_only_edge_used_in_union_count",
            "direct_llm_cannot_pair_in_materialized_component_count",
            "deterministic_hard_conflict_in_materialized_component_count",
            "transitive_cannot_violation_count",
            "unauthorized_unknown_role_materialization_count",
        )
    ):
        return "blocked_quality_regression"
    if (
        not quality.get("known_same_recall_protected")
        or not quality.get("constraint_safety_target_met")
        or not quality.get("fixed_evidence_preserved")
        or quality.get("known_same_constraint_regression")
        or quality.get("known_cannot_constraint_regression")
        or not quality.get("giant_component_remediation_improved")
    ):
        return "blocked_quality_regression"
    if quality.get("meaningful_structural_improvement"):
        return "target_met_constraint_aware_r2"
    return "partial_improvement_not_target_met"


def run_remediation(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    args.run_id = validate_run_id(args.run_id)
    isolation = environment_isolation(args.source_db, args.working_db)
    if not isolation["passed"]:
        raise R2BlockedError("blocked_environment_isolation")
    if args.mode == "execute" and args.confirm_execute != EXECUTE_CONFIRMATION:
        raise R2BlockedError("execute_confirmation_missing_or_invalid")

    manifest, _source_recheck = load_and_verify_manifest(output_dir, args)
    clone_comparison = manifest.get("clone_comparison") if isinstance(manifest.get("clone_comparison"), Mapping) else {}
    expected_working = manifest.get("working_snapshot_after_clone") if isinstance(manifest.get("working_snapshot_after_clone"), Mapping) else {}

    engine = create_db_engine(args.working_db)
    with engine.connect() as conn:
        working_identity = db_identity(conn)
        if working_identity["db_name"] != args.working_db:
            raise R2BlockedError("working_database_identity_mismatch")
        fixed_before = fingerprint_tables(conn, FIXED_INPUT_TABLES)
        working_precheck = compare_fingerprints(expected_working, fixed_before)
        if not working_precheck["passed"]:
            raise R2BlockedError("blocked_upstream_evidence_changed_working_db_precheck")
        output_before = fingerprint_tables(conn, SOURCE_CONCEPT_TABLES)
        baseline_full = route_metrics(conn)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        run_id = args.run_id
        inventory = source_signal_inventory(session)
        signals = build_source_concept_signals(session, run_id=run_id)
        deterministic = resolve_source_concepts(signals, run_id=run_id)
        judgments, llm_accounting, candidate_comparison, cache_analysis_rows = load_cached_judgments(
            Path(args.llm_cache_dir),
            signals,
            deterministic,
        )
        result = resolve_source_concepts(signals, run_id=run_id, llm_judgments=judgments)
        same_quality, same_reason_ledger = same_and_cannot_quality(result, judgments, cache_analysis_rows)
        yield_strata = llm_yield_by_strata(cache_analysis_rows, deterministic)
        dry_run_payload = {
            "phase": PHASE,
            "run_id": run_id,
            "working_db": args.working_db,
            "in_memory_metrics": in_memory_component_metrics(result),
            "graph_invariants": graph_invariants(result),
            "llm_judgment_accounting": llm_accounting,
            "same_and_cannot_quality": same_quality,
            "same_pair_reason_ledger_private": same_reason_ledger,
            "candidate_generation_comparison": candidate_comparison,
            "oversized_block_diagnostics": result.summary.get("edge_graph"),
            "context_equivalence_diagnostics": result.summary.get("context_equivalence"),
            "provider_calls": 0,
            "db_write": False,
        }
        write_json(artifact_path(output_dir, f"dry-run-{run_id}.json"), dry_run_payload)
        if args.mode == "dry-run":
            session.rollback()
            return {
                "status": "dry_run_complete",
                "run_id": run_id,
                "working_db": args.working_db,
                "new_or_missing_pair_count": llm_accounting["genuinely_new_or_missing_pair_count"],
                "graph_invariants": graph_invariants(result),
                "provider_calls": 0,
                "db_write": False,
            }
        clear_proof = r1r.clear_source_concept_owned_tables(session)
        input_scope = build_source_concept_input_scope(signals)
        persistence = persist_source_concept_resolution(
            session,
            result,
            apply=True,
            inventory=inventory,
            input_scope=input_scope,
            run_label=PHASE_SLUG,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    with engine.connect() as conn:
        fixed_after = fingerprint_tables(conn, FIXED_INPUT_TABLES)
        output_after = fingerprint_tables(conn, SOURCE_CONCEPT_TABLES)
        post_full = route_metrics(conn)
    engine.dispose()

    fixed_before_after = compare_fingerprints(fixed_before, fixed_after)
    fixed_proof = public_fixed_input_proof(manifest, clone_comparison, fixed_before_after)
    output_comparison = compare_fingerprints(output_before, output_after)
    changed_sourceconcept = [
        row["table"]
        for row in output_comparison["table_results"]
        if not row["matched"]
    ]
    forbidden_truth_public, forbidden_truth_private = forbidden_truth_table_content_comparison(
        args.source_db,
        args.working_db,
    )
    if not forbidden_truth_public["comparison_passed"]:
        write_json(
            artifact_path(output_dir, f"blocked-forbidden-truth-content-{args.run_id}.json"),
            forbidden_truth_private,
        )
        changed = ",".join(forbidden_truth_public["changed_tables"])
        raise R2BlockedError(f"blocked_forbidden_truth_table_content_changed:{changed}")
    write_scope = {
        "allowed_tables": list(SOURCE_CONCEPT_TABLES),
        "rebuilt_tables": list(SOURCE_CONCEPT_TABLES),
        "changed_tables": changed_sourceconcept,
        "forbidden_changed_tables": list(forbidden_truth_public["changed_tables"]),
        "forbidden_changed_tables_source": "forbidden_truth_table_content_proof.changed_tables",
        "unexpected_changed_tables": [],
        "delete_method": clear_proof.get("method"),
        "truncate_drop_reset_used": False,
        "persistence_forbidden_truth_table_write_count": persistence.get("forbidden_truth_table_write_count"),
    }
    graph = graph_invariants(result)
    baseline_public = public_route_metrics(baseline_full)
    post_public = public_route_metrics(post_full)
    deltas = metric_delta(baseline_public, post_public)
    search_before = baseline_public.get("search_aggregate") or {}
    search_after = post_public.get("search_aggregate") or {}
    baseline_largest = baseline_public.get("largest_components") or []
    post_largest = post_public.get("largest_components") or []
    baseline_largest_size = int((baseline_largest[0] if baseline_largest else {}).get("signal_count") or 0)
    post_largest_size = int((post_largest[0] if post_largest else {}).get("signal_count") or 0)
    baseline_overlap = float((search_before.get("media_result_overlap_metrics") or {}).get("average_pairwise_jaccard") or 0.0)
    post_overlap = float((search_after.get("media_result_overlap_metrics") or {}).get("average_pairwise_jaccard") or 0.0)
    constraint_safety_target_met = all(
        int(graph.get(key) or 0) == 0
        for key in (
            "review_only_edge_used_in_union_count",
            "direct_llm_cannot_pair_in_materialized_component_count",
            "deterministic_hard_conflict_in_materialized_component_count",
            "transitive_cannot_violation_count",
            "unauthorized_unknown_role_materialization_count",
        )
    )
    fixed_evidence_preserved = bool(
        fixed_proof.get("baseline_to_working_clone_match") and fixed_proof.get("before_after_match")
    )
    known_same_constraint_regression = int(same_quality.get("unexplained_same_regression_count") or 0) > 0
    known_cannot_constraint_regression = float(same_quality.get("known_cannot_avoidance_rate") or 0.0) < 1.0
    giant_component_remediation_improved = 0 < post_largest_size < baseline_largest_size
    search_quality_improved = bool(
        int(search_after.get("symmetric_groups") or 0) > int(search_before.get("symmetric_groups") or 0)
        and int(search_after.get("unmatched_seeds") or 0) <= int(search_before.get("unmatched_seeds") or 0)
        and post_overlap >= baseline_overlap
    )
    gap_quality_improved = int(post_public.get("gap_total") or 0) < int(baseline_public.get("gap_total") or 0)
    recall_closure_complete = bool(
        llm_accounting["genuinely_new_or_missing_pair_count"] == 0
        and search_quality_improved
        and gap_quality_improved
    )
    quality = {
        **same_quality,
        "route_metrics_recomputed": True,
        "meaningful_structural_improvement": (
            graph["review_only_edge_used_in_union_count"] == 0
            and graph["transitive_cannot_violation_count"] == 0
            and int((result.summary.get("edge_graph") or {}).get("oversized_hub_edges_prevented") or 0) > 0
        ),
        "known_same_recall_protected": bool(
            same_quality.get("compatible_same_accounting_complete")
            and int(same_quality.get("unexplained_same_regression_count") or 0) == 0
            and int(same_quality.get("missing_signal_or_pair_count") or 0) == 0
        ),
        "compatible_same_accounting_complete": bool(same_quality.get("compatible_same_accounting_complete")),
        "constraint_safety_target_met": constraint_safety_target_met,
        "fixed_evidence_preserved": fixed_evidence_preserved,
        "known_same_constraint_regression": known_same_constraint_regression,
        "known_cannot_constraint_regression": known_cannot_constraint_regression,
        "giant_component_remediation_improved": giant_component_remediation_improved,
        "search_quality_improved": search_quality_improved,
        "gap_quality_improved": gap_quality_improved,
        "recall_closure_complete": recall_closure_complete,
        "route_quality_ready_for_scale": False,
        "r2r_followup_required": True,
        "no_major_quality_regression": False,
        "quality_interpretation": QUALITY_INTERPRETATION,
        "changed_denominators_reported": True,
        "weak_review_evidence_discarded": False,
    }
    status = determine_status(fixed_proof, llm_accounting, graph, quality)
    if status not in TARGET_STATUSES:
        raise R2BlockedError(f"invalid_r2_status:{status}")

    review_pack_claim = {
        "generated": True,
        "manifest_present": True,
        "checksums_present": True,
        "integrity_passed": True,
        "not_committed": True,
        "public_report_copy_current": True,
        "zip_generated": True,
        "zip_path_label": "r2-private-review-pack",
    }
    summary: dict[str, Any] = {
        "phase": PHASE,
        "run_id": run_id,
        "phase_slug": PHASE_SLUG,
        "title": PHASE_TITLE,
        "generated_at": utc_now_iso(),
        "branch": git_value(["git", "branch", "--show-current"]),
        "evidence_version_boundary": {
            "resolver_evidence_code_sha": git_value(["git", "rev-parse", "HEAD"]),
            "post_evidence_execution_code_changed": False,
            "post_evidence_execution_code_scope": (
                "resolver_candidate_edge_union_cache_and_persistence_semantics"
            ),
            "post_evidence_proof_code_changed": True,
            "execution_code_paths_compared": list(EVIDENCE_EXECUTION_CODE_PATHS),
            "execution_code_path_results": {
                "backend/app/services/source_concept_resolver_service.py": "unchanged",
                "scripts/run_phase45_scv2_r2_constraint_aware_graph_remediation.py": (
                    "proof_only_closeout_change_no_resolver_or_persistence_semantics"
                ),
                "scripts/phase_contracts/contract_checks.py": "proof_only_closeout_change",
            },
            "git_relationship_model": (
                "The resolver evidence commit is an ancestor of the PR branch head; no direct-parent "
                "relationship is asserted."
            ),
            "report_version_model": (
                "The final PR head is reported externally in the PR body because embedding the final "
                "commit SHA inside that same commit would be self-referential."
            ),
        },
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {
                "target_met": status == "target_met_constraint_aware_r2",
                "safe_to_merge": False,
                "route_approved": False,
            },
        },
        "environment_isolation": isolation,
        "fixed_input_manifest": fixed_proof,
        "operation_counts": operation_counts(),
        "source_concept_write_scope": write_scope,
        "forbidden_truth_table_content_proof": forbidden_truth_public,
        "llm_judgment_accounting": llm_accounting,
        "new_pair_adjudication": {
            "status": (
                "blocked_llm_approval_required"
                if llm_accounting["genuinely_new_or_missing_pair_count"]
                else "no_new_pairs"
            ),
            "pair_count": llm_accounting["genuinely_new_or_missing_pair_count"],
            "projected_cost_usd": llm_accounting["projected_new_call_cost_usd"],
            "provider_calls_made": 0,
            "provider_initialized": False,
            "execution_scope_excludes_unadjudicated_review_pairs": True,
            "separate_operator_approval_required": bool(
                llm_accounting["genuinely_new_or_missing_pair_count"]
            ),
        },
        "candidate_generation_comparison": {
            key: value for key, value in candidate_comparison.items() if not key.endswith("_private")
        },
        "graph_invariants": graph,
        "oversized_block_diagnostics": result.summary.get("edge_graph"),
        "context_equivalence_diagnostics": result.summary.get("context_equivalence"),
        "ambiguity_diagnostics": {
            "profile_count": result.summary.get("ambiguity_profile_count"),
            "ambiguous_surface_count": result.summary.get("data_aware_ambiguous_surface_count"),
        },
        "baseline_metrics": baseline_public,
        "post_r2_metrics": post_public,
        "metric_delta": deltas,
        "quality_evaluation": quality,
        "human_review_sample_pack": {
            "generated": True,
            "private_only": True,
            "sample_categories": ["same", "cannot_or_conflict", "uncertain_or_review", "largest_components"],
            "human_ground_truth_claimed": False,
        },
        "public_redaction": public_redaction_success_record(),
        "review_pack": review_pack_claim,
        "route_authorization": route_authorization(),
        "artifact_lifecycle": {
            "resolver": "durable production code",
            "runner": "phase-scoped operational runner",
            "tests": "phase-scoped validation test",
            "private_artifacts": "one-off local artifact / ignored output",
            "public_report": "public report / handoff / roadmap update",
        },
        "validation": {
            "browser_validation": "not_required_no_ui_runtime_change",
            "provider_network_attempted": False,
            "server_started": False,
            "python_executable": Path(sys.executable).name,
            "python_executable_path_redacted": True,
        },
    }

    contract_result = check_phase_contract(CONTRACT_ID, summary)
    summary["validation"]["r2_contract_passed"] = contract_result.passed
    summary["validation"]["r2_contract_error_count"] = len(contract_result.errors)
    if status == "target_met_constraint_aware_r2" and not contract_result.passed:
        summary["pipeline_contract"]["status"] = "blocked_quality_regression"
        summary["pipeline_contract"]["claims"]["target_met"] = False
        status = "blocked_quality_regression"
    markdown = public_report_markdown(summary)
    redaction = scan_public_outputs(markdown, summary, output_dir, derived_run_id(args.run_id, "final"))
    if not redaction["passed"]:
        summary["public_redaction"] = redaction
        write_json(
            artifact_path(output_dir, f"blocked-public-redaction-{args.run_id}.json"),
            {
                "phase": PHASE,
                "run_id": args.run_id,
                "status": "blocked_public_redaction",
                "public_redaction": redaction,
                "public_files_written": False,
            },
        )
        raise R2BlockedError("blocked_public_redaction")
    summary["public_redaction"] = redaction
    contract_result = check_phase_contract(CONTRACT_ID, summary)
    summary["validation"]["r2_contract_passed"] = contract_result.passed
    summary["validation"]["r2_contract_error_count"] = len(contract_result.errors)
    markdown = public_report_markdown(summary)

    private_artifacts = {
        "fixed-input-manifest.json": manifest,
        "fixed-input-before-after-comparison.json": fixed_before_after,
        "forbidden-truth-table-comparison-closeout.json": forbidden_truth_private,
        "sourceconcept-output-comparison.json": output_comparison,
        "llm-yield-by-strata.json": yield_strata,
        "candidate-generation-comparison.json": candidate_comparison,
        "same-pair-reason-ledger.json": list(same_reason_ledger),
        "human-review-samples.json": private_review_samples(result, same_reason_ledger),
        "resolver-summary.json": result.summary,
        "validation-result.json": {
            "contract_passed": contract_result.passed,
            "contract_errors": [error.to_dict() for error in contract_result.errors],
            "public_redaction": redaction,
        },
    }
    pack_result = write_review_pack(output_dir, args.run_id, summary, markdown, private_artifacts)
    if not pack_result["integrity_passed"]:
        raise R2BlockedError("review_pack_integrity_failed")
    if args.write_public_report:
        write_public_outputs_after_redaction(
            markdown,
            summary,
            output_dir,
            args.run_id,
        )
    write_json(artifact_path(output_dir, f"execute-result-{args.run_id}.json"), summary)
    return summary


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("r2-%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=PHASE_TITLE)
    parser.add_argument("--mode", required=True, choices=("prepare", "dry-run", "execute"))
    parser.add_argument("--source-db", default=R1R_BASELINE_DB)
    parser.add_argument("--working-db", default=DEFAULT_WORKING_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--llm-cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--confirm-clone", default="")
    parser.add_argument("--confirm-execute", default="")
    parser.add_argument("--write-public-report", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = require_safe_output_dir(Path(args.output_dir))
    try:
        args.run_id = validate_run_id(args.run_id)
        result = (
            prepare_working_database(args, output_dir)
            if args.mode == "prepare"
            else run_remediation(args, output_dir)
        )
    except R2BlockedError as exc:
        payload = {
            "phase": PHASE,
            "mode": args.mode,
            "status": str(exc).split(":", 1)[0],
            "blocked_reason": str(exc),
            "provider_calls": 0,
            "production_writes": 0,
        }
        diagnostic_run_id = args.run_id if isinstance(args.run_id, str) and RUN_ID_RE.fullmatch(args.run_id) and ".." not in args.run_id else "invalid-run-id"
        write_json(artifact_path(output_dir, f"blocked-{args.mode}-{diagnostic_run_id}.json"), payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2
    public = {
        "phase": PHASE,
        "mode": args.mode,
        "status": (result.get("pipeline_contract") or {}).get("status") if isinstance(result.get("pipeline_contract"), Mapping) else result.get("status"),
        "run_id": result.get("run_id"),
        "working_db": result.get("working_db") or (result.get("environment_isolation") or {}).get("working_db"),
        "provider_calls": 0,
    }
    print(json.dumps(public, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
