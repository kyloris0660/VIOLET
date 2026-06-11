"""Run Phase 4.5-SCV2-R1 post-PX1 SourceConcept triage.

Lifecycle: phase-scoped operational runner.

This runner consumes existing PX1 source-layer metadata through the
SourceConcept resolver and produces before/after audit artifacts. It does not
call providers, import media, run AI/classification/localization, or write
truth-path tables. Execute mode is explicitly confirm-gated and may write only
the SourceConcept tables listed in ALLOWED_WRITE_TABLES.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts import run_phase45_scv1_source_concept_coverage_audit as scv1  # noqa: E402
from app.services.source_concept_resolver_service import (  # noqa: E402
    RESOLVER_VERSION,
    SOURCE_CONCEPT_ALLOWED_WRITE_TABLES,
    build_source_concept_input_scope,
    build_source_concept_signals,
    run_source_concept_resolution,
    source_signal_inventory,
)

PHASE = "4.5-SCV2-R1"
PHASE_TITLE = "Post-PX1 SourceConcept Resolver and Needs-Review Triage"
PHASE_SLUG = "phase-4.5-scv2-r1-post-px1-source-concept-triage"
BRANCH = "codex/phase45-scv2-r1-post-px1-source-concept-triage"
PX1_SLUG = "phase-4.5-px1-pixiv-metadata-dedup-dry-run"
CONFIRM_PHRASE = "EXECUTE_PHASE45_SCV2_R1_SOURCE_CONCEPT_TRIAGE"

DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
DRY_RUN_MARKER_NAME = "dry-run-completed.marker"

ALLOWED_WRITE_TABLES = tuple(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
SOURCE_METADATA_READONLY_TABLES = (
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_name_observations",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_metadata_evidence",
)
PROMPT_FORBIDDEN_WRITE_TABLES = (
    "blombooru_media",
    "blombooru_media_tags",
    "blombooru_tags",
    "blombooru_tag_translations",
    "blombooru_tag_translation_jobs",
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_evidence",
    "blombooru_entity_external_identities",
    "blombooru_entity_translations",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    *SOURCE_METADATA_READONLY_TABLES,
    "blombooru_provider_cache",
    "blombooru_scan_jobs",
    "blombooru_scan_job_media",
    "blombooru_ai_tag_jobs",
    "blombooru_classification_jobs",
)

REQUIRED_PRIVATE_ARTIFACTS = (
    "db-identity-before.json",
    "db-identity-after.json",
    "source-layer-baseline-before.json",
    "source-layer-baseline-after.json",
    "px1-source-metadata-presence-check.json",
    "resolver-input-inventory.json",
    "resolver-run-ledger.json",
    "source-concept-before.json",
    "source-concept-after.json",
    "source-concept-delta.json",
    "alias-gap-before.json",
    "alias-gap-after.json",
    "alias-gap-delta.json",
    "needs-review-triage-before.json",
    "needs-review-triage-after.json",
    "needs-review-triage-delta.json",
    "search-seed-symmetry-check.json",
    "mutation-proof-before.json",
    "mutation-proof-after.json",
    "mutation-proof-delta.json",
    "public-redaction-check.txt",
)

SUMMARY_REQUIRED_FIELDS = {
    "phase",
    "title",
    "branch",
    "generated_at",
    "db_identity_before",
    "db_identity_after",
    "post_px1_baseline",
    "px1_source_metadata_check",
    "resolver_input_inventory",
    "source_concept_before",
    "source_concept_after",
    "source_concept_delta",
    "alias_gap_before",
    "alias_gap_after",
    "alias_gap_delta",
    "needs_review_before",
    "needs_review_after",
    "needs_review_delta",
    "search_seed_symmetry",
    "mutation_proof",
    "public_redaction",
    "decision_matrix",
    "validation",
    "safety",
    "artifact_lifecycle",
    "private_artifacts",
    "recommended_next_phase",
}


class R1BlockedError(RuntimeError):
    """Raised when R1 cannot continue safely."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_directory(output_dir: Path) -> Path:
    zip_path = output_dir.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != zip_path:
                archive.write(path, path.relative_to(output_dir))
    return zip_path


def build_artifact_checksums(output_dir: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            checksums[str(path.relative_to(output_dir)).replace("\\", "/")] = sha256_file(path)
    return checksums


def table_columns(conn: Connection, table_name: str) -> set[str]:
    if not scv1.table_exists(conn, table_name):
        return set()
    rows = scv1.rows_dict(
        conn,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
        """,
        {"table_name": table_name},
    )
    return {str(row["column_name"]) for row in rows}


def list_blombooru_tables(conn: Connection) -> list[str]:
    rows = scv1.rows_dict(
        conn,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name LIKE 'blombooru_%'
        ORDER BY table_name
        """,
    )
    return [str(row["table_name"]) for row in rows]


def table_fingerprint(conn: Connection, table_name: str) -> dict[str, Any]:
    if not scv1.table_exists(conn, table_name):
        return {"status": "missing_table", "count": None, "fingerprint": None}
    columns = table_columns(conn, table_name)
    selectors = ["COUNT(*)::text AS row_count"]
    if "id" in columns:
        selectors.extend(
            [
                "COALESCE(MIN(id)::text, '') AS min_id",
                "COALESCE(MAX(id)::text, '') AS max_id",
                "COALESCE(SUM(id)::text, '') AS sum_id",
            ]
        )
    else:
        selectors.extend(["'' AS min_id", "'' AS max_id", "'' AS sum_id"])
    if "updated_at" in columns:
        selectors.append("COALESCE(MAX(updated_at)::text, '') AS max_updated_at")
    elif "created_at" in columns:
        selectors.append("COALESCE(MAX(created_at)::text, '') AS max_updated_at")
    else:
        selectors.append("'' AS max_updated_at")
    row = conn.execute(text(f"SELECT {', '.join(selectors)} FROM {scv1.qident(table_name)}")).mappings().one()
    payload = {
        "count": int(row["row_count"] or 0),
        "min_id": row["min_id"],
        "max_id": row["max_id"],
        "sum_id": row["sum_id"],
        "max_updated_at": row["max_updated_at"],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {"status": "present", **payload, "fingerprint": digest}


def build_table_state(conn: Connection) -> dict[str, Any]:
    table_names = sorted(set(list_blombooru_tables(conn)) | set(ALLOWED_WRITE_TABLES) | set(PROMPT_FORBIDDEN_WRITE_TABLES))
    return {
        "recorded_at": utc_now_iso(),
        "tables": {table: table_fingerprint(conn, table) for table in table_names},
    }


def compare_table_state(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    allowed_tables: Sequence[str] = ALLOWED_WRITE_TABLES,
    prompt_forbidden_tables: Sequence[str] = PROMPT_FORBIDDEN_WRITE_TABLES,
) -> dict[str, Any]:
    allowed = set(allowed_tables)
    prompt_forbidden = set(prompt_forbidden_tables)
    changed: list[dict[str, Any]] = []
    before_tables = before.get("tables", {})
    after_tables = after.get("tables", {})
    for table in sorted(set(before_tables) | set(after_tables)):
        left = before_tables.get(table, {})
        right = after_tables.get(table, {})
        if left.get("status") == "missing_table" and right.get("status") == "missing_table":
            continue
        if left.get("count") != right.get("count") or left.get("fingerprint") != right.get("fingerprint"):
            changed.append(
                {
                    "table": table,
                    "before": left.get("count"),
                    "after": right.get("count"),
                    "delta": (right.get("count") or 0) - (left.get("count") or 0)
                    if left.get("count") is not None and right.get("count") is not None
                    else None,
                    "fingerprint_changed": left.get("fingerprint") != right.get("fingerprint"),
                    "allowed": table in allowed,
                    "prompt_forbidden": table in prompt_forbidden,
                }
            )
    allowed_changed = [row for row in changed if row["table"] in allowed]
    unexpected_changed = [row for row in changed if row["table"] not in allowed]
    forbidden_changed = [row for row in changed if row["table"] in prompt_forbidden]
    source_metadata_changed = [row for row in changed if row["table"] in SOURCE_METADATA_READONLY_TABLES]
    return {
        "passed": not unexpected_changed and not forbidden_changed,
        "changed_tables": changed,
        "allowed_changed_tables": allowed_changed,
        "unexpected_changed_tables": unexpected_changed,
        "forbidden_changed_tables": forbidden_changed,
        "source_metadata_readonly_changed_tables": source_metadata_changed,
        "allowed_tables": list(allowed_tables),
        "prompt_forbidden_tables": list(prompt_forbidden_tables),
        "source_metadata_readonly_tables": list(SOURCE_METADATA_READONLY_TABLES),
    }


def db_identity(conn: Connection, env_identity: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT current_database() AS database_name,
                   current_user AS database_user,
                   inet_server_addr()::text AS server_addr,
                   inet_server_port() AS server_port,
                   version() AS server_version
            """
        )
    ).mappings().one()
    transaction_read_only = str(conn.execute(text("SHOW transaction_read_only")).scalar() or "").lower()
    identity = {
        **dict(env_identity),
        "connected_database": row["database_name"],
        "connected_user": row["database_user"],
        "server_addr": row["server_addr"],
        "server_port": row["server_port"],
        "server_version": row["server_version"],
        "transaction_read_only": transaction_read_only,
        "mode": mode,
        "code_root": ROOT.name,
        "code_root_path_redacted": True,
        "git_branch": scv1.git_value(["git", "branch", "--show-current"]),
        "git_sha": scv1.git_value(["git", "rev-parse", "HEAD"]),
        "python_executable": Path(sys.executable).name,
        "python_executable_path_private": str(Path(sys.executable)),
        "python_executable_path_redacted_for_public": True,
        "recorded_at": utc_now_iso(),
    }
    if identity["connected_database"] != "blombooru":
        raise R1BlockedError(f"Connected DB identity is not blombooru: {identity['connected_database']!r}")
    scv1.assert_db_resolution_parity(identity)
    return identity


def public_db_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: identity.get(key)
        for key in [
            "violet_env",
            "database",
            "host",
            "port",
            "connected_database",
            "server_port",
            "transaction_read_only",
            "mode",
            "git_branch",
            "git_sha",
            "python_executable",
            "recorded_at",
            "db_resolution",
        ]
    }


def build_source_layer_baseline(conn: Connection) -> dict[str, Any]:
    media = scv1.audit_media_coverage(conn)
    source_layer = scv1.audit_source_layer_coverage(conn)
    return {
        "generated_at": utc_now_iso(),
        "media": media,
        "source_layer": source_layer,
        "post_px1_snapshot": {
            "total_media": media.get("total_media"),
            "eligible_media": media.get("eligible_media_count"),
            "eligible_ai_tag_provenance_pct": media.get("eligible_ai_tag_provenance_pct"),
            "source_metadata_records_total": source_layer.get("source_records", {}).get("total_rows"),
            "source_metadata_distinct_media_count": source_layer.get("source_metadata_distinct_media_count"),
            "source_assertions_by_status": source_layer.get("source_assertions_by_status"),
            "source_name_observations_by_provider": source_layer.get("source_name_observations_by_provider"),
            "source_tag_observations_by_provider": source_layer.get("source_tag_observations_by_provider"),
        },
    }


def build_px1_presence_check(conn: Connection) -> dict[str, Any]:
    record_filter = """
        FROM blombooru_source_metadata_records r
        WHERE r.provider = 'pixiv'
          AND r.run_label = :px1_slug
          AND r.data_type_label = 'real_live_or_local_provider_data'
    """
    records = scv1.scalar_count(conn, f"SELECT COUNT(*) {record_filter}", {"px1_slug": PX1_SLUG})
    distinct_media = scv1.scalar_count(
        conn,
        f"SELECT COUNT(DISTINCT r.media_id) {record_filter} AND r.media_id IS NOT NULL",
        {"px1_slug": PX1_SLUG},
    )
    tags = scv1.scalar_count(
        conn,
        """
        SELECT COUNT(*)
        FROM blombooru_source_tag_observations t
        JOIN blombooru_source_metadata_records r ON r.id = t.source_metadata_record_id
        WHERE r.provider = 'pixiv'
          AND r.run_label = :px1_slug
          AND t.provider = 'pixiv'
        """,
        {"px1_slug": PX1_SLUG},
    )
    names = scv1.scalar_count(
        conn,
        """
        SELECT COUNT(*)
        FROM blombooru_source_name_observations n
        JOIN blombooru_source_metadata_records r ON r.id = n.source_metadata_record_id
        WHERE r.provider = 'pixiv'
          AND r.run_label = :px1_slug
          AND n.provider = 'pixiv'
        """,
        {"px1_slug": PX1_SLUG},
    )
    evidence = scv1.scalar_count(
        conn,
        """
        SELECT COUNT(*)
        FROM blombooru_source_metadata_evidence e
        JOIN blombooru_source_metadata_records r ON r.id = e.source_metadata_record_id
        WHERE r.provider = 'pixiv'
          AND r.run_label = :px1_slug
        """,
        {"px1_slug": PX1_SLUG},
    )
    assertion_rows = scv1.rows_dict(
        conn,
        """
        SELECT a.status, a.requires_review, COUNT(*) AS count
        FROM blombooru_source_searchable_name_assertions a
        JOIN blombooru_source_metadata_records r ON r.id = a.source_metadata_record_id
        WHERE r.provider = 'pixiv'
          AND r.run_label = :px1_slug
          AND a.provider = 'pixiv'
        GROUP BY a.status, a.requires_review
        ORDER BY a.status, a.requires_review
        """,
        {"px1_slug": PX1_SLUG},
    )
    assertions_total = sum(int(row["count"] or 0) for row in assertion_rows)
    needs_review_requires_review = sum(
        int(row["count"] or 0)
        for row in assertion_rows
        if row.get("status") == "needs_review" and bool(row.get("requires_review"))
    )
    searchable_active = sum(
        int(row["count"] or 0)
        for row in assertion_rows
        if row.get("status") == "searchable_active"
    )
    expected_minimums = {
        "source_metadata_records": 470,
        "source_tag_observations": 3727,
        "source_name_observations": 918,
        "source_searchable_name_assertions": 918,
        "source_metadata_evidence": 3727,
    }
    checks = {
        "metadata_records_minimum_met": records >= expected_minimums["source_metadata_records"],
        "tag_observations_minimum_met": tags >= expected_minimums["source_tag_observations"],
        "name_observations_minimum_met": names >= expected_minimums["source_name_observations"],
        "assertions_minimum_met": assertions_total >= expected_minimums["source_searchable_name_assertions"],
        "metadata_evidence_minimum_met": evidence >= expected_minimums["source_metadata_evidence"],
        "assertions_are_review_scoped": assertions_total > 0 and assertions_total == needs_review_requires_review,
        "assertions_not_searchable_active": searchable_active == 0,
    }
    return {
        "checked_at": utc_now_iso(),
        "px1_slug": PX1_SLUG,
        "expected_minimums": expected_minimums,
        "counts": {
            "source_metadata_records": records,
            "distinct_media": distinct_media,
            "source_tag_observations": tags,
            "source_name_observations": names,
            "source_searchable_name_assertions": assertions_total,
            "source_metadata_evidence": evidence,
            "assertion_needs_review_requires_review": needs_review_requires_review,
            "assertion_searchable_active": searchable_active,
        },
        "assertions_by_status_requires_review": assertion_rows,
        "checks": checks,
        "passed": all(checks.values()),
    }


def px1_source_record_ids(conn: Connection) -> set[int]:
    rows = scv1.rows_dict(
        conn,
        """
        SELECT id
        FROM blombooru_source_metadata_records
        WHERE provider = 'pixiv'
          AND run_label = :px1_slug
          AND data_type_label = 'real_live_or_local_provider_data'
        """,
        {"px1_slug": PX1_SLUG},
    )
    return {int(row["id"]) for row in rows}


def summarize_signals(signals: Sequence[Any], px1_record_ids: set[int]) -> dict[str, Any]:
    by_origin_status = Counter()
    by_origin_provider_status = Counter()
    by_origin_trust_status = Counter()
    px1_by_origin_status = Counter()
    px1_by_origin_trust_status = Counter()
    px1_needs_review_assertion_count = 0
    px1_active_assertion_count = 0
    for signal in signals:
        by_origin_status[(signal.origin_type, signal.status)] += 1
        by_origin_provider_status[(signal.origin_type, signal.provider or "unknown", signal.status)] += 1
        by_origin_trust_status[(signal.origin_type, signal.trust_tier, signal.status)] += 1
        if signal.source_metadata_record_id in px1_record_ids:
            px1_by_origin_status[(signal.origin_type, signal.status)] += 1
            px1_by_origin_trust_status[(signal.origin_type, signal.trust_tier, signal.status)] += 1
            if signal.origin_type == "source_assertion" and signal.status == "needs_review":
                px1_needs_review_assertion_count += 1
            if signal.origin_type == "source_assertion" and signal.status == "active":
                px1_active_assertion_count += 1

    def counter_rows(counter: Counter[tuple[Any, ...]]) -> list[dict[str, Any]]:
        rows = []
        for key, count in sorted(counter.items(), key=lambda item: (str(item[0]), item[1])):
            rows.append({"key": [str(part) for part in key], "count": int(count)})
        return rows

    return {
        "total_signals": len(signals),
        "signal_counts_by_origin_status": counter_rows(by_origin_status),
        "signal_counts_by_origin_provider_status": counter_rows(by_origin_provider_status),
        "signal_counts_by_origin_trust_status": counter_rows(by_origin_trust_status),
        "px1_signal_counts_by_origin_status": counter_rows(px1_by_origin_status),
        "px1_signal_counts_by_origin_trust_status": counter_rows(px1_by_origin_trust_status),
        "px1_needs_review_source_assertion_signal_count": px1_needs_review_assertion_count,
        "px1_active_source_assertion_signal_count": px1_active_assertion_count,
        "px1_assertions_included_only_as_review_scoped_input": px1_needs_review_assertion_count > 0
        and px1_active_assertion_count == 0,
    }


def build_resolver_input_inventory(session: Session, conn: Connection, *, run_id: str) -> dict[str, Any]:
    px1_ids = px1_source_record_ids(conn)
    signals = build_source_concept_signals(session, run_id=run_id)
    inventory = source_signal_inventory(session)
    scope = build_source_concept_input_scope(signals)
    signal_summary = summarize_signals(signals, px1_ids)
    return {
        "generated_at": utc_now_iso(),
        "resolver_version": RESOLVER_VERSION,
        "input_scope": scope,
        "px1_source_metadata_record_count": len(px1_ids),
        "source_signal_inventory": inventory,
        "resolver_adapter_accounting": {
            "SourceMetadataRecord": "consumed via provider_structured_field signals from explicit title/artist/raw metadata fields",
            "SourceTagObservation": "consumed via source_tag_observation signals; general/meta tags stay rejected and do not enter concept buckets",
            "SourceNameObservation": "consumed via source_name_observation signals; requires_review rows stay needs_review",
            "SourceSearchableNameAssertion": "consumed via source_assertion signals; needs_review rows stay review-scoped",
            "SourceMetadataEvidence": "read through source_metadata_record_id linkage and covered by PX1 presence/mutation proof; not written by R1",
            "media_tags": "consumed as normal_media_tag or ai_model_tag signals only for concept-eligible identity/category/parenthetical tags",
            "source_name_candidates": "consumed as f7a_candidate signals when active",
        },
        "signal_summary": signal_summary,
        "sample_policy": {
            "private_artifact_may_include_raw_values": True,
            "public_report_uses_aggregate_counts_only": True,
        },
    }


def source_concept_snapshot(conn: Connection) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    concepts = scv1.load_concepts(conn)
    aliases = scv1.load_aliases(conn)
    evidence = scv1.load_evidence(conn)
    summary, alias_inventory, evidence_inventory = scv1.audit_source_concepts(conn)
    signal_counts = (
        scv1.rows_dict(
            conn,
            """
            SELECT origin_type, provider, status, trust_tier, COUNT(*) AS count
            FROM blombooru_source_concept_signals
            GROUP BY origin_type, provider, status, trust_tier
            ORDER BY origin_type, provider, status, trust_tier
            """,
        )
        if scv1.table_exists(conn, "blombooru_source_concept_signals")
        else []
    )
    alias_counts = (
        scv1.rows_dict(
            conn,
            """
            SELECT a.alias_role, a.status, COALESCE(s.provider, 'unknown') AS provider, COUNT(*) AS count
            FROM blombooru_source_concept_aliases a
            LEFT JOIN blombooru_source_concept_signals s ON s.id = a.source_signal_id
            GROUP BY a.alias_role, a.status, COALESCE(s.provider, 'unknown')
            ORDER BY a.alias_role, a.status, COALESCE(s.provider, 'unknown')
            """,
        )
        if scv1.table_exists(conn, "blombooru_source_concept_aliases")
        and scv1.table_exists(conn, "blombooru_source_concept_signals")
        else []
    )
    evidence_counts = (
        scv1.rows_dict(
            conn,
            """
            SELECT evidence_type, status, provider, COUNT(*) AS count
            FROM blombooru_source_concept_evidence
            GROUP BY evidence_type, status, provider
            ORDER BY evidence_type, status, provider
            """,
        )
        if scv1.table_exists(conn, "blombooru_source_concept_evidence")
        else []
    )
    search_index_counts = (
        scv1.rows_dict(
            conn,
            """
            SELECT status, COUNT(*) AS count
            FROM blombooru_source_concept_search_index
            GROUP BY status
            ORDER BY status
            """,
        )
        if scv1.table_exists(conn, "blombooru_source_concept_search_index")
        else []
    )
    px1_ids = px1_source_record_ids(conn)
    px1_concepts = concepts_with_px1_evidence(conn, px1_ids)
    summary.update(
        {
            "signal_count_by_origin_provider_status": signal_counts,
            "alias_count_by_role_status_provider": alias_counts,
            "evidence_count_by_type_status_provider": evidence_counts,
            "search_index_count_by_status": search_index_counts,
            "concepts_influenced_by_px1_evidence": len(px1_concepts),
            "px1_influenced_concept_ids_private": sorted(px1_concepts)[:500],
        }
    )
    return summary, concepts, aliases, evidence


def concepts_with_px1_evidence(conn: Connection, px1_record_ids: set[int]) -> set[int]:
    if not px1_record_ids:
        return set()
    rows = scv1.rows_dict_expanding(
        conn,
        """
        SELECT DISTINCT concept_id
        FROM blombooru_source_concept_evidence
        WHERE source_metadata_record_id IN :ids
        """,
        {"ids": sorted(px1_record_ids)},
        ("ids",),
    )
    return {int(row["concept_id"]) for row in rows if row.get("concept_id") is not None}


def numeric_delta(before: Any, after: Any) -> Any:
    if isinstance(before, bool) or isinstance(after, bool):
        return after
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return round(after - before, 6) if isinstance(before, float) or isinstance(after, float) else after - before
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        output: dict[str, Any] = {}
        for key in sorted(set(before) | set(after)):
            if key in before and key in after:
                value = numeric_delta(before[key], after[key])
                if value not in ({}, [], None):
                    output[key] = value
            elif key in after and isinstance(after[key], (int, float)):
                output[key] = after[key]
            elif key in before and isinstance(before[key], (int, float)):
                output[key] = -before[key]
        return output
    return None


def build_source_concept_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    before_concepts: Sequence[Mapping[str, Any]],
    after_concepts: Sequence[Mapping[str, Any]],
    px1_influenced_after: set[int],
) -> dict[str, Any]:
    before_by_key = {str(row.get("primary_display_name")) + "|" + str(row.get("concept_type_hint")): row for row in before_concepts}
    before_px1_count = int(before.get("concepts_influenced_by_px1_evidence") or 0)
    after_px1_count = int(after.get("concepts_influenced_by_px1_evidence") or 0)
    status_changed_px1 = 0
    for row in after_concepts:
        concept_id = int(row["id"])
        if concept_id not in px1_influenced_after:
            continue
        key = str(row.get("primary_display_name")) + "|" + str(row.get("concept_type_hint"))
        previous = before_by_key.get(key)
        if previous and previous.get("status") != row.get("status"):
            status_changed_px1 += 1
    return {
        "numeric_delta": numeric_delta(before, after),
        "total_source_concepts_delta": int(after.get("total_source_concepts") or 0) - int(before.get("total_source_concepts") or 0),
        "active_source_concepts_delta": int(after.get("active_concepts") or 0) - int(before.get("active_concepts") or 0),
        "needs_review_source_concepts_delta": int(after.get("needs_review_concepts") or 0) - int(before.get("needs_review_concepts") or 0),
        "concepts_influenced_by_px1_evidence_before": before_px1_count,
        "concepts_influenced_by_px1_evidence_after": after_px1_count,
        "concepts_influenced_by_px1_evidence_delta": after_px1_count - before_px1_count,
        "concepts_newly_influenced_by_px1_evidence": max(
            0,
            after_px1_count - before_px1_count,
        ),
        "px1_influenced_concepts_with_status_change_estimate": status_changed_px1,
    }


def build_alias_gap_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    before_buckets = before.get("gap_buckets") or {}
    after_buckets = after.get("gap_buckets") or {}
    bucket_delta = {
        key: int(after_buckets.get(key) or 0) - int(before_buckets.get(key) or 0)
        for key in sorted(set(before_buckets) | set(after_buckets))
    }
    return {
        "total_gap_signals_delta": int(after.get("total_gap_signals") or 0) - int(before.get("total_gap_signals") or 0),
        "gap_bucket_delta": bucket_delta,
        "improved_bucket_count": sum(1 for value in bucket_delta.values() if value < 0),
        "regressed_bucket_count": sum(1 for value in bucket_delta.values() if value > 0),
        "unchanged_bucket_count": sum(1 for value in bucket_delta.values() if value == 0),
    }


def build_needs_review_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    keys = sorted({key for key, value in before.items() if isinstance(value, int)} | {key for key, value in after.items() if isinstance(value, int)})
    return {
        "numeric_delta": {key: int(after.get(key) or 0) - int(before.get(key) or 0) for key in keys},
        "total_needs_review_concepts_delta": int(after.get("total_needs_review_concepts") or 0)
        - int(before.get("total_needs_review_concepts") or 0),
    }


def public_source_concept_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    aggregate_keys = (
        "total_source_concepts",
        "by_status",
        "by_concept_type_hint",
        "active_concepts",
        "needs_review_concepts",
        "hidden_status_counts",
        "aliases_total",
        "evidence_total",
        "signal_links_total",
        "search_index_total",
        "alias_count_per_concept",
        "evidence_count_per_concept",
        "link_count_per_concept",
        "search_index_count_per_concept",
        "media_link_count_per_concept",
        "media_linked_concepts",
        "concepts_with_no_media",
        "concepts_with_no_aliases",
        "concepts_with_no_evidence",
        "concepts_with_no_search_index",
        "concepts_with_multiple_aliases",
        "singleton_alias_concepts",
        "concepts_by_evidence_origin_provider_source_kind",
        "ai_only_concept_count",
        "source_title_only_concept_count",
        "weak_only_concept_count",
        "signal_count_by_origin_provider_status",
        "alias_count_by_role_status_provider",
        "evidence_count_by_type_status_provider",
        "search_index_count_by_status",
        "concepts_influenced_by_px1_evidence",
    )
    private_sample_keys = (
        "high_media_count_concepts",
        "high_evidence_count_concepts",
        "duplicate_primary_display_name_groups",
        "same_alias_key_across_multiple_concepts",
        "px1_influenced_concept_ids_private",
    )
    output = {key: snapshot.get(key) for key in aggregate_keys if key in snapshot}
    output["omitted_private_sample_sets"] = {
        key: len(snapshot.get(key) or [])
        for key in private_sample_keys
        if key in snapshot
    }
    output["public_sample_policy"] = "aggregate_counts_only; raw names, alias keys, concept IDs, and sample rows stay in private artifacts"
    return output


def public_private_artifacts(output_dir: Path, *, bundle_created: bool) -> dict[str, Any]:
    return {
        "private_artifact_root_label": f".local_manifests/{PHASE_SLUG}",
        "private_artifact_count": len(REQUIRED_PRIVATE_ARTIFACTS),
        "required_private_artifacts": list(REQUIRED_PRIVATE_ARTIFACTS),
        "private_artifact_bundle_created": bundle_created,
        "private_artifact_bundle_format": "zip" if bundle_created else None,
        "exact_private_paths_public": False,
    }


def seed_groups_from_db(conn: Connection) -> dict[str, list[str]]:
    groups = {key: list(values) for key, values in scv1.SEED_GROUPS.items()}
    px1_names = scv1.rows_dict(
        conn,
        """
        SELECT MIN(n.raw_name) AS label, n.canonical_name_key, n.name_role, COUNT(*) AS count
        FROM blombooru_source_name_observations n
        JOIN blombooru_source_metadata_records r ON r.id = n.source_metadata_record_id
        WHERE r.run_label = :px1_slug
          AND n.provider = 'pixiv'
        GROUP BY n.canonical_name_key, n.name_role
        ORDER BY count DESC
        LIMIT 12
        """,
        {"px1_slug": PX1_SLUG},
    )
    px1_tags = scv1.rows_dict(
        conn,
        """
        SELECT MIN(t.raw_tag) AS label, t.canonical_tag_key, COUNT(*) AS count
        FROM blombooru_source_tag_observations t
        JOIN blombooru_source_metadata_records r ON r.id = t.source_metadata_record_id
        WHERE r.run_label = :px1_slug
          AND t.provider = 'pixiv'
        GROUP BY t.canonical_tag_key
        ORDER BY count DESC
        LIMIT 12
        """,
        {"px1_slug": PX1_SLUG},
    )
    px1_titles = scv1.rows_dict(
        conn,
        """
        SELECT MIN(a.raw_input) AS label, a.asserted_role, COUNT(*) AS count
        FROM blombooru_source_searchable_name_assertions a
        JOIN blombooru_source_metadata_records r ON r.id = a.source_metadata_record_id
        WHERE r.run_label = :px1_slug
          AND a.provider = 'pixiv'
          AND a.asserted_role IN ('source_title', 'work_title')
        GROUP BY a.canonical_name_key, a.asserted_role
        ORDER BY count DESC
        LIMIT 8
        """,
        {"px1_slug": PX1_SLUG},
    )
    ambiguous = scv1.rows_dict(
        conn,
        """
        SELECT MIN(a.raw_input) AS label, a.asserted_role, COUNT(*) AS count
        FROM blombooru_source_searchable_name_assertions a
        JOIN blombooru_source_metadata_records r ON r.id = a.source_metadata_record_id
        WHERE r.run_label = :px1_slug
          AND a.provider = 'pixiv'
          AND length(a.canonical_name_key) <= 3
        GROUP BY a.canonical_name_key, a.asserted_role
        ORDER BY count DESC
        LIMIT 8
        """,
        {"px1_slug": PX1_SLUG},
    )
    groups["px1_high_frequency_source_names_private"] = [str(row["label"]) for row in px1_names if row.get("label")]
    groups["px1_high_frequency_source_tags_private"] = [str(row["label"]) for row in px1_tags if row.get("label")]
    groups["px1_title_or_work_assertions_private"] = [str(row["label"]) for row in px1_titles if row.get("label")]
    groups["px1_ambiguous_short_names_private"] = [str(row["label"]) for row in ambiguous if row.get("label")]
    return groups


def evaluate_seed_groups(conn: Connection, groups: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    group_results: dict[str, Any] = {}
    aggregate = Counter()
    for group_name, values in groups.items():
        value_results = []
        media_shapes = []
        matched_values = 0
        for value in values:
            concept_ids, hidden = scv1.concept_ids_for_term(conn, value, statuses=scv1.VISIBLE_STATUSES)
            media_ids = scv1.concept_media_set_for_ids(conn, concept_ids, statuses=scv1.VISIBLE_STATUSES) if concept_ids else set()
            if concept_ids:
                matched_values += 1
            media_shapes.append(tuple(sorted(media_ids)))
            value_results.append(
                {
                    "value_private": value,
                    "public_label": scv1.safe_public_value(value, fallback="[redacted seed]"),
                    "concept_ids": concept_ids,
                    "concept_count": len(concept_ids),
                    "media_count": len(media_ids),
                    "hidden_raw_match_count": len(hidden),
                }
            )
        distinct_shapes = len({shape for shape in media_shapes if shape})
        symmetric = distinct_shapes <= 1
        group_results[group_name] = {
            "seed_count": len(values),
            "matched_seed_count": matched_values,
            "distinct_matched_media_shapes": distinct_shapes,
            "symmetric_media_result": symmetric,
            "values": value_results,
        }
        aggregate["groups_checked"] += 1
        aggregate["seeds_checked"] += len(values)
        aggregate["matched_seeds"] += matched_values
        aggregate["symmetric_groups"] += 1 if symmetric else 0
        aggregate["asymmetric_groups"] += 0 if symmetric else 1
    return {"aggregate": dict(aggregate), "groups": group_results}


def build_search_seed_symmetry(conn: Connection, *, before: Mapping[str, Any] | None = None) -> dict[str, Any]:
    groups = seed_groups_from_db(conn)
    evaluated = evaluate_seed_groups(conn, groups)
    if before:
        before_groups = before.get("groups") or {}
        changed_groups = []
        for name, after_group in evaluated["groups"].items():
            before_group = before_groups.get(name) or {}
            if before_group.get("matched_seed_count") != after_group.get("matched_seed_count") or before_group.get(
                "distinct_matched_media_shapes"
            ) != after_group.get("distinct_matched_media_shapes"):
                changed_groups.append(name)
        evaluated["delta_from_before"] = {
            "changed_group_count": len(changed_groups),
            "changed_groups": changed_groups,
            "aggregate_delta": numeric_delta(before.get("aggregate") or {}, evaluated["aggregate"]),
        }
    evaluated["public_summary"] = {
        "groups_checked": evaluated["aggregate"].get("groups_checked", 0),
        "seeds_checked": evaluated["aggregate"].get("seeds_checked", 0),
        "matched_seeds": evaluated["aggregate"].get("matched_seeds", 0),
        "asymmetric_groups": evaluated["aggregate"].get("asymmetric_groups", 0),
        "px1_sample_groups": [
            key
            for key in evaluated["groups"]
            if key.startswith("px1_")
        ],
        "scv1_seed_groups_included": [
            key
            for key in evaluated["groups"]
            if not key.startswith("px1_")
        ],
    }
    return evaluated


def build_decision_matrix(
    px1_check: Mapping[str, Any],
    source_delta: Mapping[str, Any],
    alias_delta: Mapping[str, Any],
    needs_delta: Mapping[str, Any],
    search_seed: Mapping[str, Any],
    mutation_proof: Mapping[str, Any],
    redaction: Mapping[str, Any],
) -> dict[str, Any]:
    px1_consumed = int(source_delta.get("concepts_influenced_by_px1_evidence_after") or 0) > 0
    mutation_passed = bool((mutation_proof.get("delta") or {}).get("passed"))
    redaction_passed = bool(redaction.get("passed"))
    r1_target_met = bool(px1_check.get("passed") and px1_consumed and mutation_passed and redaction_passed)
    total_gap_delta = int(alias_delta.get("total_gap_signals_delta") or 0)
    needs_review_delta = int(needs_delta.get("total_needs_review_concepts_delta") or 0)
    source_assertion_gap_delta = int((alias_delta.get("gap_bucket_delta") or {}).get("source_assertion_present_not_connected", 0))
    source_tag_gap_delta = int((alias_delta.get("gap_bucket_delta") or {}).get("source_tag_present_no_source_concept_alias", 0))
    asymmetric_seed_groups = int((search_seed.get("public_summary") or {}).get("asymmetric_groups") or 0)
    a1_next = r1_target_met and mutation_passed
    return {
        "answers": {
            "r1_target_met": r1_target_met,
            "px1_evidence_consumed_by_source_concepts": px1_consumed,
            "scv2_a1_should_start_next": a1_next,
            "px1_b_recommended_before_a1": False,
            "px1_b_recommended_after_a1_or_deferred": True,
            "entity_bridge_remains_blocked": True,
            "dedup1_remains_not_useful": True,
            "what_must_not_happen_yet": [
                "no Entity bridge",
                "no confirmed assignments",
                "no media_tags mutation",
                "no PX1-B",
                "no DEDUP1",
                "no 5k/10k/full-library expansion",
            ],
        },
        "metrics": {
            "total_gap_signals_delta": total_gap_delta,
            "needs_review_concepts_delta": needs_review_delta,
            "source_assertion_gap_delta": source_assertion_gap_delta,
            "source_tag_gap_delta": source_tag_gap_delta,
            "search_seed_asymmetric_groups": asymmetric_seed_groups,
        },
        "options": [
            {
                "key": "SCV2-A1_post_expansion_audit",
                "recommended": a1_next,
                "reason": "R1 consumed PX1 evidence and produced mutation/redaction-safe before/after counts",
            },
            {
                "key": "PX1-B_more_provider_metadata",
                "recommended": False,
                "reason": "R1 already has a bounded PX1 batch to consume; additional provider extraction should wait for A1 route review",
            },
            {
                "key": "Entity_bridge",
                "recommended": False,
                "reason": "SourceConcept gaps and needs_review triage still require review; no truth promotion path is approved",
            },
            {
                "key": "DEDUP1",
                "recommended": False,
                "reason": "PX1 exact duplicate dry-run groups remain zero",
            },
        ],
        "recommended_next_phase": "SCV2-A1" if a1_next else "pause_for_R1_review",
    }


def validate_summary_schema(summary: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(SUMMARY_REQUIRED_FIELDS - set(summary.keys()))
    return {"passed": not missing, "missing_fields": missing, "required_fields": sorted(SUMMARY_REQUIRED_FIELDS)}


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    before = summary["source_concept_before"]
    after = summary["source_concept_after"]
    source_delta = summary["source_concept_delta"]
    alias_delta = summary["alias_gap_delta"]
    needs_delta = summary["needs_review_delta"]
    px1 = summary["px1_source_metadata_check"]
    decision = summary["decision_matrix"]
    answers = decision["answers"]
    mutation = summary["mutation_proof"]["delta"]
    search = summary["search_seed_symmetry"]["public_summary"]
    validation = summary["validation"]
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        "",
        f"- Status: `{'target_met' if answers['r1_target_met'] else 'needs_review'}`.",
        f"- Branch/head: `{summary['branch']}` / `{summary['db_identity_after'].get('git_sha')}`.",
        f"- SourceConcept total before/after/delta: `{before.get('total_source_concepts')}` / `{after.get('total_source_concepts')}` / `{source_delta.get('total_source_concepts_delta')}`.",
        f"- Active SourceConcept before/after/delta: `{before.get('active_concepts')}` / `{after.get('active_concepts')}` / `{source_delta.get('active_source_concepts_delta')}`.",
        f"- needs_review SourceConcept before/after/delta: `{before.get('needs_review_concepts')}` / `{after.get('needs_review_concepts')}` / `{source_delta.get('needs_review_source_concepts_delta')}`.",
        f"- Concepts newly influenced by PX1 evidence: `{source_delta.get('concepts_newly_influenced_by_px1_evidence')}`.",
        "",
        "## Scope and non-goals",
        "",
        "- Scope: consume the bounded PX1 source-layer metadata through the existing SourceConcept resolver and produce before/after triage artifacts.",
        "- Source-layer writes in execute mode are limited to SourceConcept resolver tables.",
        "- Non-goals: no provider calls, media import, classification, AI tagging, localization, LLM, Entity bridge, confirmed assignment, `media_tags` mutation, source or iCloud mutation, PX1-B, DEDUP1, or 5k/10k/full-library expansion.",
        "",
        "## Post-PX1 baseline",
        "",
        f"- Total media / eligible media: `{summary['post_px1_baseline']['media'].get('total_media')}` / `{summary['post_px1_baseline']['media'].get('eligible_media_count')}`.",
        f"- Eligible AI tag coverage: `{summary['post_px1_baseline']['media'].get('eligible_ai_tag_provenance_pct')}`%.",
        f"- Source metadata rows / distinct media: `{summary['post_px1_baseline']['source_layer'].get('source_records', {}).get('total_rows')}` / `{summary['post_px1_baseline']['source_layer'].get('source_metadata_distinct_media_count')}`.",
        "",
        "## PX1 source metadata availability",
        "",
        f"- PX1 presence check passed: `{px1.get('passed')}`.",
        f"- PX1 records / tags / names / assertions / evidence: `{px1['counts'].get('source_metadata_records')}` / `{px1['counts'].get('source_tag_observations')}` / `{px1['counts'].get('source_name_observations')}` / `{px1['counts'].get('source_searchable_name_assertions')}` / `{px1['counts'].get('source_metadata_evidence')}`.",
        f"- PX1 assertions needs_review+requires_review: `{px1['counts'].get('assertion_needs_review_requires_review')}`; searchable_active: `{px1['counts'].get('assertion_searchable_active')}`.",
        "",
        "## Resolver input inventory",
        "",
        f"- Resolver version: `{summary['resolver_input_inventory'].get('resolver_version')}`.",
        f"- Total resolver input signals: `{summary['resolver_input_inventory']['signal_summary'].get('total_signals')}`.",
        f"- PX1 source assertion signals included as review-scoped input: `{summary['resolver_input_inventory']['signal_summary'].get('px1_needs_review_source_assertion_signal_count')}`.",
        f"- PX1 active source assertion signals: `{summary['resolver_input_inventory']['signal_summary'].get('px1_active_source_assertion_signal_count')}`.",
        "",
        "## Resolver changes, if any",
        "",
        "- Resolver code change required: `False`.",
        "- Existing resolver adapters already consume `SourceMetadataRecord`, `SourceTagObservation`, `SourceNameObservation`, `SourceSearchableNameAssertion`, existing `media_tags`, and source name candidates.",
        "- R1 added a phase-scoped operational runner and safety/report tests rather than changing active source search semantics.",
        "",
        "## SourceConcept before/after",
        "",
        f"- By status before: `{json.dumps(before.get('by_status'), ensure_ascii=False, sort_keys=True)}`.",
        f"- By status after: `{json.dumps(after.get('by_status'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Superseded/rejected/ambiguous before: `{json.dumps(before.get('hidden_status_counts'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Superseded/rejected/ambiguous after: `{json.dumps(after.get('hidden_status_counts'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Search index by status after: `{json.dumps(after.get('search_index_count_by_status'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Alias gap before/after",
        "",
        f"- Total gap signals before/after/delta: `{summary['alias_gap_before'].get('total_gap_signals')}` / `{summary['alias_gap_after'].get('total_gap_signals')}` / `{alias_delta.get('total_gap_signals_delta')}`.",
        f"- Gap bucket delta: `{json.dumps(alias_delta.get('gap_bucket_delta'), ensure_ascii=False, sort_keys=True)}`.",
        "- SCV1 historical baseline: total gap signals `1571`; source_tag gap `81`; source_assertion gap `22`; same normalized alias split `176`; same display/context split `176`; needs_review no active alias path `523`; identity tag gap `14`.",
        "",
        "## needs_review triage before/after",
        "",
        f"- Total needs_review concepts before/after/delta: `{summary['needs_review_before'].get('total_needs_review_concepts')}` / `{summary['needs_review_after'].get('total_needs_review_concepts')}` / `{needs_delta.get('total_needs_review_concepts_delta')}`.",
        f"- Triage numeric deltas: `{json.dumps(needs_delta.get('numeric_delta'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Search seed symmetry checks",
        "",
        f"- Groups / seeds / matched seeds: `{search.get('groups_checked')}` / `{search.get('seeds_checked')}` / `{search.get('matched_seeds')}`.",
        f"- Asymmetric groups: `{search.get('asymmetric_groups')}`.",
        f"- Included SCV1 seed groups: `{json.dumps(search.get('scv1_seed_groups_included'), ensure_ascii=False)}`.",
        f"- Included PX1 sample groups: `{json.dumps(search.get('px1_sample_groups'), ensure_ascii=False)}`.",
        "",
        "## Mutation proof",
        "",
        f"- Mutation proof passed: `{mutation.get('passed')}`.",
        f"- Allowed changed tables: `{json.dumps([row['table'] for row in mutation.get('allowed_changed_tables', [])], ensure_ascii=False)}`.",
        f"- Forbidden changed tables: `{json.dumps([row['table'] for row in mutation.get('forbidden_changed_tables', [])], ensure_ascii=False)}`.",
        f"- Source metadata read-only changed tables: `{json.dumps([row['table'] for row in mutation.get('source_metadata_readonly_changed_tables', [])], ensure_ascii=False)}`.",
        "",
        "## Public/private artifact boundary",
        "",
        "- Public report and summary contain aggregate counts and redacted labels only.",
        f"- Private artifact root label: `{summary['private_artifacts'].get('private_artifact_root_label')}`.",
        f"- Public redaction passed: `{summary['public_redaction'].get('passed')}`.",
        "",
        "## Decision matrix",
        "",
        f"- R1 target met: `{answers['r1_target_met']}`.",
        f"- SCV2-A1 should start next: `{answers['scv2_a1_should_start_next']}`.",
        f"- PX1-B recommended before A1: `{answers['px1_b_recommended_before_a1']}`.",
        f"- Entity bridge remains blocked: `{answers['entity_bridge_remains_blocked']}`.",
        f"- DEDUP1 remains not useful: `{answers['dedup1_remains_not_useful']}`.",
        "",
        "## Whether R1 target was met",
        "",
        f"`{answers['r1_target_met']}`.",
        "",
        "## Whether SCV2-A1 should start next",
        "",
        f"`{answers['scv2_a1_should_start_next']}`. A1 should be an audit/route decision phase, not a provider or truth-promotion phase.",
        "",
        "## Whether PX1-B is recommended before or after A1",
        "",
        "PX1-B should wait until after A1 or remain deferred. R1 consumed the current bounded PX1 batch.",
        "",
        "## Whether Entity bridge remains blocked",
        "",
        "`True`. SourceConcept evidence remains unconfirmed source-layer evidence only.",
        "",
        "## Whether DEDUP1 remains not useful",
        "",
        "`True`. PX1 exact duplicate dry-run groups were `0`.",
        "",
        "## Validation",
        "",
        f"- Operational dry-run run: `{validation.get('dry_run_completed')}`.",
        f"- Operational execute run: `{validation.get('execute_completed')}`.",
        f"- Browser validation: `{validation.get('browser_validation')}`.",
        f"- Commands recorded: `{json.dumps(validation.get('commands'), ensure_ascii=False)}`.",
        "",
        "## Safety confirmation",
        "",
        "- No push main, no merge, no media import, no provider call, no classification, no AI tagging, no localization, no LLM, no Entity Resolver/similarity, no Entity truth, no confirmed assignments, no `media_tags` mutation, no source, iCloud, or storage mutation, no cleanup/delete/reset/drop/truncate.",
        "",
        "## Engineering judgment / operator notes",
        "",
        "- Artifact lifecycle: runner and tests are phase-scoped; public report/summary are public report and handoff artifacts; `.local_manifests` output is one-off ignored local artifact.",
        "- Phase boundary is appropriate: R1 consumes source-layer evidence and writes only SourceConcept resolver tables under explicit confirmation.",
        "- Remaining risks: SourceConcept gaps may move rather than vanish because PX1 adds much more review-scoped evidence; Entity bridge remains blocked until a later explicit preview/confirmation/audit design.",
        "- No reviewer findings were fixed in this run; reviewer status is pending after PR creation.",
        "- Recommended next step: review/merge R1 if accepted, then run SCV2-A1 as a post-expansion audit and route decision.",
    ]
    return "\n".join(lines) + "\n"


def write_public_outputs(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    paths = [PUBLIC_REPORT_MD, PUBLIC_REPORT_JSON]
    labels = [scv1.root_relative_or_name(path) for path in paths]
    checked_at = utc_now_iso()
    temp_dir = output_dir / "_public_report_staging"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_md = temp_dir / PUBLIC_REPORT_MD.name
    temp_json = temp_dir / PUBLIC_REPORT_JSON.name
    pending_redaction = {
        "checked_at": checked_at,
        "passed": None,
        "public_paths": labels,
        "findings": [],
        "final_public_scan_after_public_fields_finalized": False,
        "exact_private_paths_public": False,
        "private_artifact_paths_public": False,
    }
    summary["public_redaction"] = pending_redaction
    write_text(temp_md, public_report_markdown(summary))
    write_json(temp_json, summary)
    scan = scv1.scan_public_artifacts([temp_md, temp_json], checked_at=checked_at, public_path_labels=labels)
    if not scan["passed"]:
        failed = {**pending_redaction, "passed": False, "findings": scan["findings"]}
        write_json(output_dir / "public-redaction-check.json", failed)
        write_text(output_dir / "public-redaction-check.txt", json.dumps(failed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise R1BlockedError(f"Public redaction scan failed: {scan['findings']!r}")
    final_redaction = {
        "checked_at": checked_at,
        "passed": True,
        "public_paths": labels,
        "findings": [],
        "final_public_scan_after_public_fields_finalized": True,
        "exact_private_paths_public": False,
        "private_artifact_paths_public": False,
        "policy": "public Markdown/JSON are rendered to ignored staging files, scanned, then atomically replace tracked report paths",
    }
    summary["public_redaction"] = final_redaction
    write_text(temp_md, public_report_markdown(summary))
    write_json(temp_json, summary)
    final_scan = scv1.scan_public_artifacts([temp_md, temp_json], checked_at=checked_at, public_path_labels=labels)
    if not final_scan["passed"]:
        failed = {**final_redaction, "passed": False, "findings": final_scan["findings"]}
        write_json(output_dir / "public-redaction-check.json", failed)
        write_text(output_dir / "public-redaction-check.txt", json.dumps(failed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise R1BlockedError(f"Final public redaction scan failed: {final_scan['findings']!r}")
    PUBLIC_REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    temp_md.replace(PUBLIC_REPORT_MD)
    temp_json.replace(PUBLIC_REPORT_JSON)
    try:
        temp_dir.rmdir()
    except OSError:
        pass
    write_text(output_dir / "public-redaction-check.txt", json.dumps(final_redaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return final_redaction


def build_summary(
    *,
    mode: str,
    db_identity_before: Mapping[str, Any],
    db_identity_after: Mapping[str, Any],
    baseline_before: Mapping[str, Any],
    baseline_after: Mapping[str, Any],
    px1_check: Mapping[str, Any],
    resolver_input: Mapping[str, Any],
    resolver_ledger: Mapping[str, Any],
    source_before: Mapping[str, Any],
    source_after: Mapping[str, Any],
    source_delta: Mapping[str, Any],
    alias_before: Mapping[str, Any],
    alias_after: Mapping[str, Any],
    alias_delta: Mapping[str, Any],
    needs_before: Mapping[str, Any],
    needs_after: Mapping[str, Any],
    needs_delta: Mapping[str, Any],
    search_seed: Mapping[str, Any],
    mutation_before: Mapping[str, Any],
    mutation_after: Mapping[str, Any],
    mutation_delta: Mapping[str, Any],
    output_dir: Path,
    validation_commands: Sequence[str],
    dry_run_previously_completed: bool,
) -> dict[str, Any]:
    decision = build_decision_matrix(px1_check, source_delta, alias_delta, needs_delta, search_seed, {"delta": mutation_delta}, {"passed": True})
    validation = {
        "mode": mode,
        "dry_run_completed": mode == "dry_run" or dry_run_previously_completed,
        "execute_completed": mode == "execute",
        "commands": list(validation_commands),
        "browser_validation": "not_run_no_ui_runtime_change",
        "server_started": False,
        "provider_network_attempted": False,
        "no_provider_import_classification_ai_localization_entity": True,
    }
    safety = {
        "allowed_write_tables": list(ALLOWED_WRITE_TABLES),
        "forbidden_write_tables": list(PROMPT_FORBIDDEN_WRITE_TABLES),
        "source_metadata_readonly_tables": list(SOURCE_METADATA_READONLY_TABLES),
        "db_write": mode == "execute",
        "db_migration": False,
        "provider_calls": False,
        "media_import": False,
        "classification": False,
        "ai_tagging": False,
        "localization_or_llm": False,
        "entity_truth_or_media_tags": False,
        "source_icloud_storage_mutation": False,
        "cleanup_delete_reset_drop_truncate": False,
    }
    summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "branch": BRANCH,
        "generated_at": utc_now_iso(),
        "mode": mode,
        "db_identity_before": public_db_identity(db_identity_before),
        "db_identity_after": public_db_identity(db_identity_after),
        "post_px1_baseline": baseline_after,
        "baseline_delta": numeric_delta(baseline_before, baseline_after),
        "px1_source_metadata_check": px1_check,
        "resolver_input_inventory": {
            "generated_at": resolver_input.get("generated_at"),
            "resolver_version": resolver_input.get("resolver_version"),
            "px1_source_metadata_record_count": resolver_input.get("px1_source_metadata_record_count"),
            "resolver_adapter_accounting": resolver_input.get("resolver_adapter_accounting"),
            "signal_summary": resolver_input.get("signal_summary"),
        },
        "source_concept_before": public_source_concept_summary(source_before),
        "source_concept_after": public_source_concept_summary(source_after),
        "source_concept_delta": source_delta,
        "alias_gap_before": {
            "gap_buckets": alias_before.get("gap_buckets"),
            "gap_bucket_details": alias_before.get("gap_bucket_details"),
            "total_gap_signals": alias_before.get("total_gap_signals"),
            "normal_tag_gap_policy": alias_before.get("normal_tag_gap_policy"),
            "recommended_next_fix_category": alias_before.get("recommended_next_fix_category"),
        },
        "alias_gap_after": {
            "gap_buckets": alias_after.get("gap_buckets"),
            "gap_bucket_details": alias_after.get("gap_bucket_details"),
            "total_gap_signals": alias_after.get("total_gap_signals"),
            "normal_tag_gap_policy": alias_after.get("normal_tag_gap_policy"),
            "recommended_next_fix_category": alias_after.get("recommended_next_fix_category"),
        },
        "alias_gap_delta": alias_delta,
        "needs_review_before": needs_before,
        "needs_review_after": needs_after,
        "needs_review_delta": needs_delta,
        "search_seed_symmetry": {
            "public_summary": search_seed.get("public_summary"),
            "delta_from_before": search_seed.get("delta_from_before"),
        },
        "mutation_proof": {
            "before": {"recorded_at": mutation_before.get("recorded_at")},
            "after": {"recorded_at": mutation_after.get("recorded_at")},
            "delta": mutation_delta,
        },
        "public_redaction": {"passed": None, "findings": []},
        "decision_matrix": decision,
        "validation": validation,
        "safety": safety,
        "artifact_lifecycle": {
            "scripts/run_phase45_scv2_r1_post_px1_source_concept_triage.py": "phase-scoped operational runner",
            "tests/test_phase45_scv2_r1_post_px1_source_concept_triage.py": "phase-scoped validation test",
            "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md": "public report / handoff / roadmap update",
            "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json": "public report / handoff / roadmap update",
            ".local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage": "one-off local artifact / ignored output",
        },
        "private_artifacts": public_private_artifacts(output_dir, bundle_created=False),
        "recommended_next_phase": decision["recommended_next_phase"],
    }
    schema = validate_summary_schema(summary)
    summary["validation"]["summary_schema"] = schema
    if not schema["passed"]:
        raise R1BlockedError(f"Summary schema missing fields: {schema['missing_fields']!r}")
    return summary


def write_private_artifacts(
    output_dir: Path,
    *,
    db_identity_before: Mapping[str, Any],
    db_identity_after: Mapping[str, Any],
    baseline_before: Mapping[str, Any],
    baseline_after: Mapping[str, Any],
    px1_check: Mapping[str, Any],
    resolver_input: Mapping[str, Any],
    resolver_ledger: Mapping[str, Any],
    source_before: Mapping[str, Any],
    source_after: Mapping[str, Any],
    source_delta: Mapping[str, Any],
    alias_before: Mapping[str, Any],
    alias_after: Mapping[str, Any],
    alias_delta: Mapping[str, Any],
    needs_before: Mapping[str, Any],
    needs_after: Mapping[str, Any],
    needs_delta: Mapping[str, Any],
    search_seed: Mapping[str, Any],
    mutation_before: Mapping[str, Any],
    mutation_after: Mapping[str, Any],
    mutation_delta: Mapping[str, Any],
) -> None:
    payloads = {
        "db-identity-before.json": db_identity_before,
        "db-identity-after.json": db_identity_after,
        "source-layer-baseline-before.json": baseline_before,
        "source-layer-baseline-after.json": baseline_after,
        "px1-source-metadata-presence-check.json": px1_check,
        "resolver-input-inventory.json": resolver_input,
        "resolver-run-ledger.json": resolver_ledger,
        "source-concept-before.json": source_before,
        "source-concept-after.json": source_after,
        "source-concept-delta.json": source_delta,
        "alias-gap-before.json": alias_before,
        "alias-gap-after.json": alias_after,
        "alias-gap-delta.json": alias_delta,
        "needs-review-triage-before.json": needs_before,
        "needs-review-triage-after.json": needs_after,
        "needs-review-triage-delta.json": needs_delta,
        "search-seed-symmetry-check.json": search_seed,
        "mutation-proof-before.json": mutation_before,
        "mutation-proof-after.json": mutation_after,
        "mutation-proof-delta.json": mutation_delta,
    }
    for name, payload in payloads.items():
        write_json(output_dir / name, payload)


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    mode = "execute" if args.execute else "dry_run"
    if args.execute and args.confirm_execution != CONFIRM_PHRASE:
        raise R1BlockedError(f"Execute mode requires --confirm-execution {CONFIRM_PHRASE}")
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    dry_run_marker = output_dir / DRY_RUN_MARKER_NAME
    dry_run_previously_completed = dry_run_marker.exists()

    url, env_identity = scv1.build_database_url()
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "options": "-c statement_timeout=600000"},
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False)
    conn: Connection | None = None
    session: Session | None = None
    started = time.perf_counter()
    command_label = (
        f"python scripts/run_phase45_scv2_r1_post_px1_source_concept_triage.py --{mode.replace('_', '-')}"
        f" --output-dir .local_manifests/{PHASE_SLUG}"
        + (" --write-public-report" if args.write_public_report else "")
    )
    if args.execute:
        command_label += f" --confirm-execution {CONFIRM_PHRASE}"
    validation_commands = [command_label]
    if mode == "execute" and dry_run_previously_completed:
        validation_commands.insert(
            0,
            f"python scripts/run_phase45_scv2_r1_post_px1_source_concept_triage.py --dry-run"
            f" --output-dir .local_manifests/{PHASE_SLUG}"
            + (" --write-public-report" if args.write_public_report else ""),
        )
    try:
        conn = engine.connect()
        if conn.dialect.name != "postgresql":
            raise R1BlockedError(f"R1 requires PostgreSQL, got {conn.dialect.name!r}")
        if mode == "dry_run":
            conn.exec_driver_sql("BEGIN TRANSACTION READ ONLY")
        else:
            conn.exec_driver_sql("SET statement_timeout = '600s'")
        db_identity_before = db_identity(conn, env_identity, mode=mode)
        mutation_before = build_table_state(conn)
        baseline_before = build_source_layer_baseline(conn)
        px1_check = build_px1_presence_check(conn)
        if not px1_check["passed"]:
            raise R1BlockedError(f"PX1 source metadata presence check failed: {px1_check['checks']!r}")

        source_before, concepts_before, aliases_before_rows, evidence_before_rows = source_concept_snapshot(conn)
        alias_before, _alias_before_samples = scv1.audit_alias_gaps(conn, concepts_before, aliases_before_rows)
        needs_before, _needs_before_samples = scv1.audit_needs_review(conn, concepts_before, aliases_before_rows, evidence_before_rows)
        search_before = build_search_seed_symmetry(conn)

        session = SessionLocal(bind=conn)
        run_id = args.run_id or f"{PHASE_SLUG}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        resolver_input = build_resolver_input_inventory(session, conn, run_id=run_id)
        if not resolver_input["signal_summary"]["px1_assertions_included_only_as_review_scoped_input"]:
            raise R1BlockedError("PX1 needs_review assertions were not included only as review-scoped resolver input.")
        result, inventory, persistence = run_source_concept_resolution(session, run_id=run_id, apply=args.execute)
        resolver_ledger = {
            "run_id": run_id,
            "mode": mode,
            "resolver_version": RESOLVER_VERSION,
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "result_summary": result.summary,
            "inventory": inventory,
            "persistence": persistence,
            "truth_path_write_count": persistence.get("forbidden_truth_table_write_count", 0),
        }
        if args.execute and persistence.get("forbidden_truth_table_write_count") not in (0, None):
            raise R1BlockedError(f"Resolver reported forbidden truth table writes: {persistence!r}")
        if mode == "dry_run":
            # The read-only transaction remains open for after snapshots; no DB
            # writes have been made, so after should match before.
            pass
        baseline_after = build_source_layer_baseline(conn)
        source_after, concepts_after, aliases_after_rows, evidence_after_rows = source_concept_snapshot(conn)
        alias_after, _alias_after_samples = scv1.audit_alias_gaps(conn, concepts_after, aliases_after_rows)
        needs_after, _needs_after_samples = scv1.audit_needs_review(conn, concepts_after, aliases_after_rows, evidence_after_rows)
        px1_after_ids = px1_source_record_ids(conn)
        source_delta = build_source_concept_delta(
            source_before,
            source_after,
            before_concepts=concepts_before,
            after_concepts=concepts_after,
            px1_influenced_after=concepts_with_px1_evidence(conn, px1_after_ids),
        )
        alias_delta = build_alias_gap_delta(alias_before, alias_after)
        needs_delta = build_needs_review_delta(needs_before, needs_after)
        search_seed = build_search_seed_symmetry(conn, before=search_before)
        mutation_after = build_table_state(conn)
        mutation_delta = compare_table_state(mutation_before, mutation_after)
        if not mutation_delta["passed"]:
            raise R1BlockedError(f"Unexpected DB mutation detected: {mutation_delta['unexpected_changed_tables']!r}")
        db_identity_after = db_identity(conn, env_identity, mode=mode)

        write_private_artifacts(
            output_dir,
            db_identity_before=db_identity_before,
            db_identity_after=db_identity_after,
            baseline_before=baseline_before,
            baseline_after=baseline_after,
            px1_check=px1_check,
            resolver_input=resolver_input,
            resolver_ledger=resolver_ledger,
            source_before=source_before,
            source_after=source_after,
            source_delta=source_delta,
            alias_before=alias_before,
            alias_after=alias_after,
            alias_delta=alias_delta,
            needs_before=needs_before,
            needs_after=needs_after,
            needs_delta=needs_delta,
            search_seed=search_seed,
            mutation_before=mutation_before,
            mutation_after=mutation_after,
            mutation_delta=mutation_delta,
        )
        summary = build_summary(
            mode=mode,
            db_identity_before=db_identity_before,
            db_identity_after=db_identity_after,
            baseline_before=baseline_before,
            baseline_after=baseline_after,
            px1_check=px1_check,
            resolver_input=resolver_input,
            resolver_ledger=resolver_ledger,
            source_before=source_before,
            source_after=source_after,
            source_delta=source_delta,
            alias_before=alias_before,
            alias_after=alias_after,
            alias_delta=alias_delta,
            needs_before=needs_before,
            needs_after=needs_after,
            needs_delta=needs_delta,
            search_seed=search_seed,
            mutation_before=mutation_before,
            mutation_after=mutation_after,
            mutation_delta=mutation_delta,
            output_dir=output_dir,
            validation_commands=validation_commands,
            dry_run_previously_completed=dry_run_previously_completed,
        )
        if args.write_public_report:
            redaction = write_public_outputs(summary, output_dir)
            summary["public_redaction"] = redaction
            # Rebuild decision with actual redaction status.
            summary["decision_matrix"] = build_decision_matrix(
                px1_check,
                source_delta,
                alias_delta,
                needs_delta,
                search_seed,
                {"delta": mutation_delta},
                redaction,
            )
            summary["recommended_next_phase"] = summary["decision_matrix"]["recommended_next_phase"]
            write_public_outputs(summary, output_dir)
        zip_path = zip_directory(output_dir)
        summary["private_artifacts"] = public_private_artifacts(output_dir, bundle_created=zip_path.exists())
        if args.write_public_report:
            write_public_outputs(summary, output_dir)
        write_json(output_dir / "checksums.json", build_artifact_checksums(output_dir))
        zip_directory(output_dir)
        if mode == "dry_run":
            write_text(dry_run_marker, f"completed_at={utc_now_iso()}\n")
        if mode == "dry_run":
            conn.exec_driver_sql("ROLLBACK")
        return summary
    finally:
        if session is not None:
            session.close()
        if conn is not None:
            try:
                if not conn.closed and mode == "dry_run":
                    conn.exec_driver_sql("ROLLBACK")
            except Exception:
                pass
            conn.close()
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Plan and audit without DB writes; default.")
    mode.add_argument("--execute", action="store_true", help="Apply SourceConcept resolver writes after confirmation.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--confirm-execution", default="")
    parser.add_argument("--run-id", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        args.dry_run = True
    summary = run_pipeline(args)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
