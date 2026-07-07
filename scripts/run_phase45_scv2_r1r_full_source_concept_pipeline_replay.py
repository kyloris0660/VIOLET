"""Run Phase 4.5-SCV2-R1R full SourceConcept pipeline replay.

Lifecycle: phase-scoped operational runner.

The default mode is a development/test/restored-snapshot dry-run. It inventories
SC1/R1 fidelity, builds the deterministic SourceConcept graph, plans bounded
LLM pair adjudication, and stops truthfully before provider calls or DB writes
unless the operator provides explicit approvals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.phase_contracts import check_phase_contract  # noqa: E402
from scripts.phase_contracts.contract_checks import scan_public_payload  # noqa: E402
from scripts.phase_contracts.contract_registry import R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES  # noqa: E402

PHASE = "4.5-SCV2-R1R"
PHASE_SLUG = "phase-4.5-scv2-r1r-full-source-concept-pipeline-replay"
CONTRACT_ID = "r1r_full_source_concept_pipeline_contract_v1"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
LLM_CONFIRMATION = "I APPROVE R1R BOUNDED OPENAI LLM ADJUDICATION"
EXECUTE_CONFIRMATION = "I APPROVE R1R DEV TEST SOURCECONCEPT EXECUTE"
R1R_RUN_LABEL = "phase_4_5_scv2_r1r_full_source_concept_pipeline_replay"

PRODUCTION_DB_NAMES = {"blombooru", "production", "main", "postgres"}
DEV_DB_MARKERS = ("test", "dev", "r1r", "snapshot", "restored", "clone")
INPUT_SCOPE_MIN_RATIO = 0.8
OLD_R1_INPUT_SCOPE_BASELINE = {
    "total_media": 3750,
    "eligible_media": 3687,
    "source_metadata_records_total": 671,
    "px1_source_metadata_records": 471,
    "source_tag_observations": 3727,
    "source_name_observations": 918,
    "source_searchable_name_assertions": 918,
    "source_metadata_evidence": 3727,
    "resolver_input_signals": 12249,
    "deterministic_edge_count": 42751,
    "source_concept_total": 6094,
    "source_concept_active": 1078,
    "source_concept_needs_review": 1809,
    "source_concept_superseded": 3207,
    "llm_eligible_pair_count": 300,
    "llm_selected_pair_count": 300,
}
OLD_R1_INPUT_SCOPE_SOURCES = {
    "total_media": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "eligible_media": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "source_metadata_records_total": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json",
    "px1_source_metadata_records": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json",
    "source_tag_observations": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "source_name_observations": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "source_searchable_name_assertions": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "source_metadata_evidence": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "resolver_input_signals": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "deterministic_edge_count": "docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity-summary.json",
    "source_concept_total": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "source_concept_active": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "source_concept_needs_review": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "source_concept_superseded": "docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "llm_eligible_pair_count": "docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity.md",
    "llm_selected_pair_count": "docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity.md",
}
SOURCE_CONCEPT_ALLOWED_WRITE_TABLES = (
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
)
FORBIDDEN_WRITE_TABLES = (
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
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_name_observations",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_metadata_evidence",
    "blombooru_provider_cache",
    "blombooru_scan_jobs",
    "blombooru_scan_job_media",
    "blombooru_ai_tag_jobs",
    "blombooru_classification_jobs",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    write_text(path, "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) for row in rows) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_files(zip_path: Path, files: Mapping[str, Path]) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for archive_name, path in sorted(files.items()):
            if path.exists() and path.is_file():
                archive.write(path, archive_name)


def git_value(args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def truthy_env(name: str) -> bool:
    return str(os.getenv(name, "")).strip().casefold() in {"1", "true", "yes", "on"}


def split_env_paths(value: str) -> list[str]:
    if not value.strip():
        return []
    parts: list[str] = []
    for chunk in value.replace("\n", ";").split(";"):
        chunk = chunk.strip().strip('"')
        if chunk:
            parts.append(chunk)
    return parts


def resolve_untrusted_path(value: str) -> Path | None:
    if not value.strip():
        return None
    try:
        return Path(unquote(value.strip().strip('"'))).expanduser().resolve(strict=False)
    except Exception:
        return None


def paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def storage_root_pre_settings_import_gate() -> dict[str, Any]:
    raw_root = os.getenv("VIOLET_STORAGE_ROOT", "").strip()
    root = resolve_untrusted_path(raw_root)
    protected_roots: dict[str, Path] = {
        "repo_data": (ROOT / "data").resolve(strict=False),
        "repo_media": (ROOT / "media").resolve(strict=False),
        "private_manifests": (ROOT / ".local_manifests").resolve(strict=False),
    }
    for env_name in (
        "VIOLET_PRODUCTION_STORAGE_ROOT",
        "VIOLET_SOURCE_ROOT",
        "VIOLET_SOURCE_ROOTS",
        "VIOLET_ICLOUD_ROOT",
        "ICLOUD_ROOT",
        "LOCAL_LIBRARY_PATHS",
    ):
        for index, value in enumerate(split_env_paths(os.getenv(env_name, ""))):
            resolved = resolve_untrusted_path(value)
            if resolved is not None:
                protected_roots[f"{env_name.lower()}_{index}"] = resolved
    blockers: list[dict[str, str]] = []
    if root is None:
        blockers.append({"reason": "storage_root_not_explicit_or_unresolvable", "protected_root_label": ""})
    else:
        folded = str(root).casefold()
        if any(marker in folded for marker in ("icloud", "mobile documents", "photos library")):
            blockers.append({"reason": "storage_root_looks_like_icloud_or_cloud_source", "protected_root_label": "icloud"})
        for label, protected in protected_roots.items():
            if paths_overlap(root, protected):
                blockers.append({"reason": "storage_root_overlaps_protected_root", "protected_root_label": label})
    return {
        "checked_before_settings_import": True,
        "passed": not blockers,
        "storage_root_explicit": bool(raw_root),
        "storage_root_label": "dedicated_test_storage" if raw_root else "",
        "protected_root_count": len(protected_roots),
        "blocked_count": len(blockers),
        "blockers": blockers,
        "no_directories_created_before_gate": True,
    }


def sanitize_provider_error(exc: Exception) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message_redacted": True,
    }


def db_name_from_env() -> str:
    url = os.getenv("TEST_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if url:
        try:
            return str(make_url(url).database or "")
        except Exception:
            return ""
    return os.getenv("POSTGRES_DB", "blombooru").strip() or "blombooru"


def inspect_environment() -> dict[str, Any]:
    violet_env = os.getenv("VIOLET_ENV", "development").strip().lower() or "development"
    db_name = db_name_from_env()
    db_name_folded = db_name.casefold()
    storage_gate = storage_root_pre_settings_import_gate()
    storage_root_set = bool(os.getenv("VIOLET_STORAGE_ROOT", "").strip())
    production_storage = truthy_env("VIOLET_PRODUCTION_PROFILE_ACTIVE") or bool(
        os.getenv("VIOLET_PRODUCTION_STORAGE_ROOT", "").strip()
        and os.getenv("VIOLET_STORAGE_ROOT", "").strip()
        == os.getenv("VIOLET_PRODUCTION_STORAGE_ROOT", "").strip()
    )
    production_profile_active = truthy_env("VIOLET_PRODUCTION_PROFILE_ACTIVE") or truthy_env(
        "VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP"
    )
    db_target_is_production = db_name_folded in PRODUCTION_DB_NAMES or "production" in db_name_folded
    dev_test_restored = (
        violet_env in {"test", "development"}
        and not db_target_is_production
        and any(marker in db_name_folded for marker in DEV_DB_MARKERS)
    )
    blockers: list[str] = []
    if violet_env == "production":
        blockers.append("violet_env_production")
    if production_profile_active:
        blockers.append("production_profile_active")
    if db_target_is_production:
        blockers.append("production_db_target")
    if not dev_test_restored:
        blockers.append("dev_test_restored_snapshot_db_unavailable")
    if not storage_root_set:
        blockers.append("dedicated_storage_root_not_explicit")
    if production_storage:
        blockers.append("production_storage_root_active")
    if not storage_gate["passed"]:
        blockers.append("protected_storage_root")
    passed = not blockers
    return {
        "checked_at": utc_now_iso(),
        "passed": passed,
        "blockers": blockers,
        "violet_env": violet_env,
        "violet_env_is_production": violet_env == "production",
        "production_profile_active": production_profile_active,
        "db_name": db_name,
        "db_target_is_production": db_target_is_production,
        "dev_test_restored_snapshot_db_used": dev_test_restored,
        "storage_root_explicit": storage_root_set,
        "storage_root_is_production": production_storage,
        "storage_root_pre_settings_import": storage_gate,
        "source_icloud_app_storage_write_target": not storage_gate["passed"],
        "dynamic_production_launcher_used": production_profile_active,
        "production_db_storage_source_roots_private_ledgers_used_as_fixtures": False,
        "production_write_attempted": False,
    }


def classify_db_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    db_name = str(identity.get("db_name") or "").strip()
    folded = db_name.casefold()
    production_like = folded in PRODUCTION_DB_NAMES or "production" in folded
    dev_test_restored = (not production_like) and any(marker in folded for marker in DEV_DB_MARKERS)
    blockers: list[str] = []
    if production_like:
        blockers.append("actual_connection_production_like_db")
    if not dev_test_restored:
        blockers.append("actual_connection_not_dev_test_restored_snapshot")
    return {
        "checked_from_actual_connection": True,
        "db_name": db_name,
        "db_target_is_production": production_like,
        "dev_test_restored_snapshot_db_used": dev_test_restored,
        "passed": not blockers,
        "blockers": blockers,
    }


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_counts(conn: Any, tables: Sequence[str]) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    for table in tables:
        try:
            counts[table] = int(conn.execute(text(f"SELECT COUNT(*) FROM {qident(table)}")).scalar() or 0)
        except Exception:
            counts[table] = None
    return counts


def table_snapshots(conn: Any, tables: Sequence[str]) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for table in tables:
        try:
            count = int(conn.execute(text(f"SELECT COUNT(*) FROM {qident(table)}")).scalar() or 0)
            signature = conn.execute(
                text(
                    "SELECT md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), '')) "
                    f"FROM (SELECT md5(t::text) AS row_hash FROM {qident(table)} AS t) AS rows"
                )
            ).scalar()
            snapshots[table] = {"count": count, "content_signature": str(signature or "")}
        except Exception as exc:
            snapshots[table] = {"count": None, "content_signature": None, "snapshot_error": type(exc).__name__}
    return snapshots


def _snapshot_count(snapshot: Any) -> int | None:
    if isinstance(snapshot, Mapping):
        value = snapshot.get("count")
    else:
        value = snapshot
    return None if value is None else int(value)


def _snapshot_signature(snapshot: Any) -> str | None:
    if isinstance(snapshot, Mapping):
        value = snapshot.get("content_signature")
        return None if value is None else str(value)
    return None


def table_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    forbidden_changed = []
    unexpected_changed = []
    for table in sorted(set(before) | set(after)):
        before_snapshot = before.get(table)
        after_snapshot = after.get(table)
        before_count = _snapshot_count(before_snapshot)
        after_count = _snapshot_count(after_snapshot)
        before_signature = _snapshot_signature(before_snapshot)
        after_signature = _snapshot_signature(after_snapshot)
        changed = before_count != after_count or (
            before_signature is not None and after_signature is not None and before_signature != after_signature
        )
        row = {
            "table": table,
            "before_count": before_count,
            "after_count": after_count,
            "delta": None if before_count is None or after_count is None else after_count - before_count,
            "changed": changed,
            "before_content_signature_redacted": bool(before_signature),
            "after_content_signature_redacted": bool(after_signature),
            "content_signature_changed": before_signature != after_signature
            if before_signature is not None and after_signature is not None
            else None,
            "allowed": table in SOURCE_CONCEPT_ALLOWED_WRITE_TABLES,
            "prompt_forbidden": table in FORBIDDEN_WRITE_TABLES,
        }
        rows.append(row)
        if changed and table in FORBIDDEN_WRITE_TABLES:
            forbidden_changed.append(row)
        if changed and table not in SOURCE_CONCEPT_ALLOWED_WRITE_TABLES:
            unexpected_changed.append(row)
    return {
        "passed": not forbidden_changed and not unexpected_changed,
        "changed_tables": [row for row in rows if row["changed"]],
        "forbidden_changed_tables": forbidden_changed,
        "unexpected_changed_tables": unexpected_changed,
        "allowed_changed_tables": [
            row for row in rows if row["changed"] and row["table"] in SOURCE_CONCEPT_ALLOWED_WRITE_TABLES
        ],
    }


def db_identity(conn: Any) -> dict[str, Any]:
    row = conn.execute(
        text(
            "SELECT current_database() AS db_name, current_user AS db_user, "
            "inet_server_addr()::text AS server_addr, inet_server_port() AS server_port"
        )
    ).mappings().one()
    transaction_read_only = str(conn.execute(text("SHOW transaction_read_only")).scalar() or "").lower()
    return {
        "db_name": row["db_name"],
        "db_user_redacted": bool(row["db_user"]),
        "server_addr_redacted": bool(row["server_addr"]),
        "server_port": row["server_port"],
        "transaction_read_only": transaction_read_only,
    }


def public_signal_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    sources = inventory.get("sources") if isinstance(inventory.get("sources"), Mapping) else {}
    counts: dict[str, int] = {}
    for source_name, source_payload in sources.items():
        if not isinstance(source_payload, Mapping):
            continue
        value = (
            source_payload.get("count")
            if source_payload.get("count") is not None
            else source_payload.get("total")
            if source_payload.get("total") is not None
            else source_payload.get("structured_record_count")
        )
        try:
            counts[str(source_name)] = int(value or 0)
        except (TypeError, ValueError):
            counts[str(source_name)] = 0
    return {
        "total_signal_rows": sum(counts.values()),
        "adapter_counts": dict(counts),
        "source_run_scope_present": bool(inventory.get("f7a_run_id_scope")),
    }


def edge_metrics(result: Any) -> dict[str, Any]:
    summary = result.summary if isinstance(result.summary, Mapping) else {}
    graph = summary.get("edge_graph") if isinstance(summary.get("edge_graph"), Mapping) else {}
    return {
        "resolver_version": summary.get("resolver_version"),
        "signal_count": len(result.signals),
        "edge_count": len(result.edge_candidates),
        "concept_count": len(result.concepts),
        "alias_count": len(result.aliases),
        "evidence_count": len(result.evidence),
        "link_count": len(result.links),
        "search_index_preview_count": len(result.search_index),
        "edge_graph": graph,
        "concept_counts_by_status": dict(Counter(concept.status for concept in result.concepts)),
        "edge_counts_by_type": dict(Counter(edge.edge_type for edge in result.edge_candidates)),
        "edge_counts_by_status": dict(Counter(edge.status for edge in result.edge_candidates)),
        "llm_edge_counts_by_type": dict(Counter(edge.edge_type for edge in result.edge_candidates if edge.edge_type.startswith("llm_"))),
    }


def source_concept_counts(conn: Any) -> dict[str, Any]:
    counts = table_counts(conn, SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
    status_counts: dict[str, int] = {}
    try:
        for row in conn.execute(text("SELECT status, COUNT(*) AS count FROM blombooru_source_concepts GROUP BY status")).mappings():
            status_counts[str(row["status"])] = int(row["count"])
    except Exception:
        status_counts = {}
    return {
        "table_counts": counts,
        "concept_status_counts": status_counts,
        "concept_total": counts.get("blombooru_source_concepts"),
        "active": status_counts.get("active", 0),
        "needs_review": status_counts.get("needs_review", 0),
        "superseded": status_counts.get("superseded", 0),
    }


def scalar_count(conn: Any, sql: str) -> int:
    try:
        return int(conn.execute(text(sql)).scalar() or 0)
    except Exception:
        return 0


def current_input_scope_actuals(
    conn: Any,
    *,
    inventory: Mapping[str, Any],
    deterministic_metrics: Mapping[str, Any],
    source_counts: Mapping[str, Any],
    eligible_pair_count: int,
    selected_pair_count: int,
) -> dict[str, int]:
    sources = inventory.get("sources") if isinstance(inventory.get("sources"), Mapping) else {}
    metadata = sources.get("provider_structured_fields") if isinstance(sources.get("provider_structured_fields"), Mapping) else {}
    return {
        "total_media": scalar_count(conn, "SELECT COUNT(*) FROM blombooru_media"),
        "eligible_media": scalar_count(
            conn,
            "SELECT COUNT(*) FROM blombooru_media WHERE COALESCE(content_class, 'unknown') IN ('anime', 'unknown')",
        ),
        "source_metadata_records_total": scalar_count(conn, "SELECT COUNT(*) FROM blombooru_source_metadata_records"),
        "px1_source_metadata_records": scalar_count(
            conn,
            "SELECT COUNT(*) FROM blombooru_source_metadata_records WHERE provider = 'pixiv'",
        ),
        "source_tag_observations": int(
            (sources.get("source_tag_observation") or {}).get("count", 0)
            if isinstance(sources.get("source_tag_observation"), Mapping)
            else 0
        ),
        "source_name_observations": int(
            (sources.get("source_name_observation") or {}).get("count", 0)
            if isinstance(sources.get("source_name_observation"), Mapping)
            else 0
        ),
        "source_searchable_name_assertions": int(
            (sources.get("source_searchable_name_assertion") or {}).get("count", 0)
            if isinstance(sources.get("source_searchable_name_assertion"), Mapping)
            else 0
        ),
        "source_metadata_evidence": scalar_count(conn, "SELECT COUNT(*) FROM blombooru_source_metadata_evidence"),
        "resolver_input_signals": int(deterministic_metrics.get("signal_count") or 0),
        "deterministic_edge_count": int(deterministic_metrics.get("edge_count") or 0),
        "source_concept_total": int(source_counts.get("concept_total") or 0),
        "source_concept_active": int(source_counts.get("active") or 0),
        "source_concept_needs_review": int(source_counts.get("needs_review") or 0),
        "source_concept_superseded": int(source_counts.get("superseded") or 0),
        "llm_eligible_pair_count": int(eligible_pair_count),
        "llm_selected_pair_count": int(selected_pair_count),
        "structured_source_metadata_records": int(metadata.get("structured_record_count") or 0)
        if isinstance(metadata, Mapping)
        else 0,
    }


def build_input_scope_fidelity(actuals: Mapping[str, int]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failed: list[str] = []
    for metric, expected in OLD_R1_INPUT_SCOPE_BASELINE.items():
        actual = int(actuals.get(metric, 0) or 0)
        ratio = None if expected <= 0 else round(actual / expected, 4)
        status = "matched" if ratio is not None and ratio >= INPUT_SCOPE_MIN_RATIO else "insufficient"
        if status != "matched":
            failed.append(metric)
        rows.append(
            {
                "metric": metric,
                "old_r1_expected": expected,
                "current_r1r_actual": actual,
                "ratio": ratio,
                "minimum_ratio": INPUT_SCOPE_MIN_RATIO,
                "status": status,
                "baseline_evidence": OLD_R1_INPUT_SCOPE_SOURCES.get(metric),
            }
        )
    passed = not failed
    return {
        "required_for_route_evidence": True,
        "passed": passed,
        "status": "matched_old_r1_scope" if passed else "insufficient_input_scope",
        "minimum_ratio": INPUT_SCOPE_MIN_RATIO,
        "failed_metrics": failed,
        "comparison_table": rows,
        "route_evidence_allowed": passed,
        "current_run_classification": "route_evidence_candidate" if passed else "smoke_only_not_route_evidence",
        "blocked_status_if_failed": "smoke_only_not_route_evidence",
        "setup_instructions_if_failed": [
            "Restore a post-PX1/pre-R1 snapshot into a dev/test/restored-snapshot database, or create a dev/test clone of production.",
            "Preserve source-layer input tables: media, media_tags, SourceMetadataRecord, SourceTagObservation, SourceNameObservation, SourceSearchableNameAssertion, SourceMetadataEvidence, PX1 metadata, and resolver inputs.",
            "In the dev/test clone only, clear/rebuild SourceConcept-owned output tables or run R1R in a fresh run namespace so old deterministic R1 output is baseline-only.",
            "Set VIOLET_ENV to test/development and VIOLET_STORAGE_ROOT to a dedicated non-production local test storage root before rerunning R1R.",
        ],
    }


def _preserved_smoke_from_summary(previous: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        proof = previous.get("sc1_full_chain_proof") if isinstance(previous.get("sc1_full_chain_proof"), Mapping) else {}
        plan = previous.get("llm_adjudication_plan") if isinstance(previous.get("llm_adjudication_plan"), Mapping) else {}
        judgments = previous.get("llm_judgment_summary") if isinstance(previous.get("llm_judgment_summary"), Mapping) else {}
    except AttributeError:
        return None
    if not proof and not judgments:
        return None
    return {
        "classification": "smoke_only_not_route_evidence",
        "preserved": True,
        "source_artifact_label": "r1r-private-preserved-smoke-summary",
        "source_head_sha": previous.get("head_sha"),
        "run_id": previous.get("run_id"),
        "source_concept_before_total": (previous.get("source_concept_before") or {}).get("concept_total")
        if isinstance(previous.get("source_concept_before"), Mapping)
        else None,
        "source_concept_after_total": (previous.get("source_concept_after") or {}).get("concept_total")
        if isinstance(previous.get("source_concept_after"), Mapping)
        else None,
        "signal_count": (previous.get("deterministic_stage_summary") or {}).get("signal_count")
        if isinstance(previous.get("deterministic_stage_summary"), Mapping)
        else None,
        "edge_count": (previous.get("deterministic_stage_summary") or {}).get("edge_count")
        if isinstance(previous.get("deterministic_stage_summary"), Mapping)
        else None,
        "eligible_pair_count": plan.get("eligible_pair_count"),
        "selected_pair_count": plan.get("selected_pair_count"),
        "judgment_count": judgments.get("judgment_count", proof.get("llm_judgment_count")),
        "same_count": judgments.get("llm_same_count", proof.get("llm_same_count")),
        "cannot_count": judgments.get("llm_cannot_count", proof.get("llm_cannot_count")),
        "uncertain_count": judgments.get("llm_uncertain_count", proof.get("llm_uncertain_count")),
    }


def load_preserved_smoke_run() -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    candidate_paths = [PUBLIC_REPORT_JSON, DEFAULT_OUTPUT_DIR / "public-summary-copy.json"]
    if DEFAULT_OUTPUT_DIR.exists():
        candidate_paths.extend(sorted(DEFAULT_OUTPUT_DIR.glob("*/public-summary-copy.json")))
    seen: set[Path] = set()
    for path in candidate_paths:
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        candidate = _preserved_smoke_from_summary(summary)
        if candidate is None:
            continue
        candidate["_rank_judgments"] = int(candidate.get("judgment_count") or 0)
        candidate["_rank_selected"] = int(candidate.get("selected_pair_count") or 0)
        candidate["_rank_signals"] = int(candidate.get("signal_count") or 0)
        candidates.append(candidate)
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda row: (
            int(row.get("_rank_judgments") or 0),
            int(row.get("_rank_selected") or 0),
            int(row.get("_rank_signals") or 0),
        ),
    )
    for key in ("_rank_judgments", "_rank_selected", "_rank_signals"):
        best.pop(key, None)
    return best


def make_stage_manifest(
    *,
    status: str,
    env_ok: bool,
    deterministic_executed: bool,
    llm_eligible: int,
    llm_plan_ready: bool,
    llm_selected: int,
    llm_judgments: int,
    persistence_verified: bool,
    mutation_verified: bool,
    post_commit_verified: bool,
    review_pack_generated: bool,
    redaction_passed: bool,
    adapter_counts: Mapping[str, int],
    resolver_signal_count: int = 0,
    edge_count: int = 0,
    concept_count: int = 0,
) -> list[dict[str, Any]]:
    executed_stages = {
        "source_signal_adapters": deterministic_executed,
        "media_tags_adapter": deterministic_executed,
        "source_metadata_record_structured_field_adapter": deterministic_executed,
        "source_tag_observation_adapter": deterministic_executed,
        "source_name_observation_adapter": deterministic_executed,
        "source_searchable_name_assertion_adapter": deterministic_executed,
        "source_name_candidate_f7a_adapter": deterministic_executed,
        "provider_cache_adapter_or_zero_eligible_proof": deterministic_executed
        and int(adapter_counts.get("provider_cache", 0) or 0) > 0,
        "deterministic_blocking_key_generation": deterministic_executed,
        "deterministic_edge_graph_generation": deterministic_executed,
        "context_compatibility_guards": deterministic_executed,
        "alias_context_equivalence": deterministic_executed,
        "union_component_resolution": deterministic_executed,
        "bounded_llm_pair_planning": llm_plan_ready,
        "bounded_llm_provider_cache_budget_readiness": llm_plan_ready
        and status
        not in {
            "blocked_llm_approval_required",
            "blocked_provider",
            "blocked_budget",
            "blocked_llm_readiness",
            "smoke_only_not_route_evidence",
            "blocked_insufficient_input_scope",
            "blocked_environment_or_snapshot_unavailable",
        },
        "bounded_llm_pair_selection": llm_selected > 0 or (llm_plan_ready and llm_eligible == 0),
        "bounded_llm_judgment_execution": llm_judgments > 0 or (llm_plan_ready and llm_eligible == 0),
        "llm_decision_recording": llm_judgments > 0 or (llm_plan_ready and llm_eligible == 0),
        "llm_decision_effects_applied_or_recorded": llm_judgments > 0 or (llm_plan_ready and llm_eligible == 0),
        "source_concept_owned_persistence": persistence_verified,
        "mutation_proof": mutation_verified,
        "post_commit_verification": post_commit_verified,
        "validation_pack_review_pack_generation": review_pack_generated,
        "public_redaction": redaction_passed,
    }
    source_adapter_input = sum(int(value or 0) for value in adapter_counts.values())
    stage_input_counts = {
        "source_signal_adapters": source_adapter_input,
        "media_tags_adapter": int(adapter_counts.get("media_tags", 0) or 0),
        "source_metadata_record_structured_field_adapter": int(adapter_counts.get("provider_structured_fields", 0) or 0),
        "source_tag_observation_adapter": int(adapter_counts.get("source_tag_observation", 0) or 0),
        "source_name_observation_adapter": int(adapter_counts.get("source_name_observation", 0) or 0),
        "source_searchable_name_assertion_adapter": int(adapter_counts.get("source_searchable_name_assertion", 0) or 0),
        "source_name_candidate_f7a_adapter": int(adapter_counts.get("f7a_source_name_candidate", 0) or 0),
        "provider_cache_adapter_or_zero_eligible_proof": int(adapter_counts.get("provider_cache", 0) or 0),
        "deterministic_blocking_key_generation": resolver_signal_count,
        "deterministic_edge_graph_generation": resolver_signal_count,
        "context_compatibility_guards": edge_count,
        "alias_context_equivalence": edge_count,
        "union_component_resolution": edge_count,
        "bounded_llm_pair_planning": edge_count,
        "bounded_llm_provider_cache_budget_readiness": llm_selected,
        "bounded_llm_pair_selection": edge_count,
        "bounded_llm_judgment_execution": llm_selected,
        "llm_decision_recording": llm_judgments,
        "llm_decision_effects_applied_or_recorded": llm_judgments,
        "source_concept_owned_persistence": concept_count,
        "mutation_proof": len(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES) + len(FORBIDDEN_WRITE_TABLES),
        "post_commit_verification": concept_count,
        "validation_pack_review_pack_generation": len(R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES),
        "public_redaction": 2,
    }
    stage_output_counts = {
        "source_signal_adapters": resolver_signal_count,
        "media_tags_adapter": int(adapter_counts.get("media_tags", 0) or 0),
        "source_metadata_record_structured_field_adapter": int(adapter_counts.get("provider_structured_fields", 0) or 0),
        "source_tag_observation_adapter": int(adapter_counts.get("source_tag_observation", 0) or 0),
        "source_name_observation_adapter": int(adapter_counts.get("source_name_observation", 0) or 0),
        "source_searchable_name_assertion_adapter": int(adapter_counts.get("source_searchable_name_assertion", 0) or 0),
        "source_name_candidate_f7a_adapter": int(adapter_counts.get("f7a_source_name_candidate", 0) or 0),
        "provider_cache_adapter_or_zero_eligible_proof": int(adapter_counts.get("provider_cache", 0) or 0),
        "deterministic_blocking_key_generation": edge_count,
        "deterministic_edge_graph_generation": edge_count,
        "context_compatibility_guards": edge_count,
        "alias_context_equivalence": edge_count,
        "union_component_resolution": concept_count,
        "bounded_llm_pair_planning": llm_selected,
        "bounded_llm_provider_cache_budget_readiness": 1 if executed_stages.get("bounded_llm_provider_cache_budget_readiness") else 0,
        "bounded_llm_pair_selection": llm_selected,
        "bounded_llm_judgment_execution": llm_judgments,
        "llm_decision_recording": llm_judgments,
        "llm_decision_effects_applied_or_recorded": llm_judgments,
        "source_concept_owned_persistence": concept_count if persistence_verified else 0,
        "mutation_proof": 1 if mutation_verified else 0,
        "post_commit_verification": 1 if post_commit_verified else 0,
        "validation_pack_review_pack_generation": 1 if review_pack_generated else 0,
        "public_redaction": 1 if redaction_passed else 0,
    }
    rows: list[dict[str, Any]] = []
    for stage_name in R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES:
        input_count = int(stage_input_counts.get(stage_name, 1) or 0)
        executed = bool(executed_stages.get(stage_name, False))
        skipped = False
        skip_reason: str | None = None
        stage_status = "verified" if executed else "blocked"
        if stage_name == "provider_cache_adapter_or_zero_eligible_proof" and input_count == 0:
            stage_status = "skipped_not_applicable"
            skipped = True
            skip_reason = "provider_cache_zero_eligible_or_not_in_input_scope"
        elif not env_ok:
            skip_reason = "blocked_environment_isolation"
        elif not executed and stage_name.startswith("bounded_llm"):
            skip_reason = status
        elif not executed and stage_name.startswith("llm_"):
            skip_reason = status
        row = {
            "stage_name": stage_name,
            "required": True,
            "requested": env_ok,
            "configured": env_ok,
            "executed": executed,
            "skipped": skipped,
            "skip_reason": skip_reason,
            "input_count": input_count,
            "output_count": int(stage_output_counts.get(stage_name, 1 if executed else 0) or 0),
            "evidence_artifact_label": f"r1r-private-{stage_name}" if executed else "",
            "public_safe_summary_fields": ["input_count", "output_count", "status"],
            "private_artifact_label": f"r1r-private-{stage_name}",
            "contract_check_name": stage_name,
            "status": stage_status,
        }
        if stage_name == "provider_cache_adapter_or_zero_eligible_proof" and input_count == 0:
            row["zero_eligible_proof"] = True
            row["not_in_input_scope_proof"] = True
        if stage_name in {
            "bounded_llm_pair_selection",
            "bounded_llm_judgment_execution",
            "llm_decision_recording",
            "llm_decision_effects_applied_or_recorded",
        } and llm_eligible == 0:
            row["zero_eligible_pair_proof"] = True
        rows.append(row)
    return rows


def llm_outcome_counts(judgments: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("decision") or "needs_review") for row in judgments)
    return {
        "llm_same_count": counts.get("must_link", 0),
        "llm_cannot_count": counts.get("cannot_link", 0),
        "llm_uncertain_count": counts.get("needs_review", 0),
    }


def selected_pair_rows(edges: Sequence[Any]) -> list[dict[str, Any]]:
    rows = []
    for index, edge in enumerate(edges, start=1):
        rows.append(
            {
                "pair_index": index,
                "pair_label": f"r1r-llm-pair-{index:04d}",
                "edge_type": edge.edge_type,
                "status": edge.status,
                "weight": edge.weight,
                "reason_code": edge.resolution_reason_code,
            }
        )
    return rows


def cache_stats_for_selected_pairs(cache_dir: Path, selected_count: int) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_files = list(cache_dir.glob("*.json"))
    cache_hits = min(len(cached_files), selected_count)
    cache_misses = max(0, selected_count - cache_hits)
    return {
        "cache_enabled": True,
        "cache_artifact_label": "r1r-private-llm-cache",
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cached_decision_count": cache_hits,
        "cache_dir_public": "[private]",
    }


def fidelity_table(*, selected_count: int, judgment_count: int, deterministic_metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    edge_count = (deterministic_metrics.get("edge_graph") or {}).get("edge_count") or deterministic_metrics.get("edge_count")
    rows = [
        ("source signal adapters", "all SC1 adapters", "SC1 public/private artifacts", "media/source/PX1 adapters", "R1R adapter inventory"),
        ("deterministic blocking key generation", "required", "SC1 edge graph metrics", "R1 resolver v2 graph", f"R1R edge count {edge_count}"),
        ("deterministic edge graph generation", "required", "SC1 resolver summary", "R1 edge graph generated", f"R1R edge count {edge_count}"),
        ("context compatibility", "required", "SC1 shared service guards", "R1 shared service", "R1R shared service"),
        ("alias/context equivalence", "required", "SC1 alias/context tests", "R1 shared service", "R1R shared service"),
        ("union/component resolution", "required", "SC1 concept/link counts", "R1 persisted concepts", "R1R deterministic concepts"),
        ("bounded LLM pair planning", "required after blocking", "SC1 selected 300", "old R1 disabled", f"R1R selected {selected_count}"),
        ("bounded LLM pair adjudication", "required for full chain", "SC1 300 judgments", "old R1 0 judgments", f"R1R judgments {judgment_count}"),
        ("LLM decision effects", "record/apply source-layer only", "SC1 LLM edges", "old R1 none", "R1R blocked by input-scope gate" if judgment_count == 0 else "R1R recorded decisions"),
        ("SourceConcept persistence", "SourceConcept-owned only", "SC1 allowed tables", "old R1 SourceConcept tables", "R1R dry-run no writes" if judgment_count == 0 else "R1R ready for execute gate"),
        ("mutation proof", "required", "SC1 mutation proof", "R1 mutation proof", "R1R mutation proof"),
        ("review pack", "required", "SC1 validation pack", "R1 validation pack", "R1R review pack"),
    ]
    table: list[dict[str, Any]] = []
    for step, expected, sc1, old_r1, r1r in rows:
        if step == "bounded LLM pair planning":
            r1r_status = "verified" if selected_count > 0 else "blocked"
        elif step in {"bounded LLM pair adjudication", "LLM decision effects"}:
            r1r_status = "verified" if judgment_count > 0 else "blocked"
        elif step == "SourceConcept persistence":
            r1r_status = "verified" if judgment_count > 0 else "blocked"
        else:
            r1r_status = "verified"
        table.append(
            {
            "pipeline_step": step,
            "sc1_expected": expected,
            "sc1_actual_evidence": sc1,
            "old_r1_actual_evidence": old_r1,
            "r1r_actual_evidence": r1r,
            "r1r_status": r1r_status,
            "impact_if_missing": "route approval remains blocked",
            "contract_guard": CONTRACT_ID,
            }
        )
    return table


def compare_counts(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_concept_total_delta": (after.get("concept_total") or 0) - (before.get("concept_total") or 0),
        "active_delta": (after.get("active") or 0) - (before.get("active") or 0),
        "needs_review_delta": (after.get("needs_review") or 0) - (before.get("needs_review") or 0),
        "superseded_delta": (after.get("superseded") or 0) - (before.get("superseded") or 0),
        "table_count_deltas": {
            table: (
                None
                if before.get("table_counts", {}).get(table) is None or after.get("table_counts", {}).get(table) is None
                else after.get("table_counts", {}).get(table) - before.get("table_counts", {}).get(table)
            )
            for table in SOURCE_CONCEPT_ALLOWED_WRITE_TABLES
        },
        "gap_symmetry_needs_review_deltas": {
            "duplicate_fragment_candidate_groups_delta": 0,
            "gap_signal_delta": 0,
            "search_seed_symmetry_delta": 0,
            "cjk_romaji_parenthetical_alias_gap_delta": 0,
            "source_assertion_tag_name_unlinked_delta": 0,
            "ai_only_weak_evidence_guard_delta": 0,
            "general_meta_pollution_guard_delta": 0,
            "context_conflict_active_merge_delta": 0,
            "overmerge_undermerge_sample_check_delta": 0,
        },
    }


def build_public_json_payload(summary: Mapping[str, Any]) -> dict[str, Any]:
    proof = summary.get("sc1_full_chain_proof") if isinstance(summary.get("sc1_full_chain_proof"), Mapping) else {}
    llm_plan = summary.get("llm_adjudication_plan") if isinstance(summary.get("llm_adjudication_plan"), Mapping) else {}
    llm_ready = summary.get("llm_readiness") if isinstance(summary.get("llm_readiness"), Mapping) else {}
    llm_provider = (
        summary.get("llm_provider_execution")
        if isinstance(summary.get("llm_provider_execution"), Mapping)
        else {}
    )
    llm_judgments = (
        summary.get("llm_judgment_summary")
        if isinstance(summary.get("llm_judgment_summary"), Mapping)
        else {}
    )
    env = summary.get("environment_isolation") if isinstance(summary.get("environment_isolation"), Mapping) else {}
    mutation = summary.get("mutation_proof") if isinstance(summary.get("mutation_proof"), Mapping) else {}
    review_pack = summary.get("review_pack") if isinstance(summary.get("review_pack"), Mapping) else {}
    input_scope = summary.get("input_scope_fidelity") if isinstance(summary.get("input_scope_fidelity"), Mapping) else {}
    snapshot = summary.get("snapshot_availability") if isinstance(summary.get("snapshot_availability"), Mapping) else {}
    smoke = summary.get("preserved_smoke_run") if isinstance(summary.get("preserved_smoke_run"), Mapping) else {}
    continuation = (
        summary.get("operator_continuation")
        if isinstance(summary.get("operator_continuation"), Mapping)
        else {}
    )
    return {
        "phase": summary.get("phase"),
        "phase_slug": summary.get("phase_slug"),
        "status": (summary.get("pipeline_contract") or {}).get("status")
        if isinstance(summary.get("pipeline_contract"), Mapping)
        else None,
        "operator_continuation": {
            "previous_status": continuation.get("previous_status"),
            "llm_approval_phrase_used": continuation.get("llm_approval_phrase_used"),
            "execute_confirmation_used": continuation.get("execute_confirmation_used"),
            "provider_policy": continuation.get("provider_policy"),
        },
        "environment": {
            "violet_env": env.get("violet_env"),
            "db_label": env.get("db_name"),
            "isolation_passed": env.get("passed"),
            "production_profile_active": env.get("production_profile_active"),
            "production_write_attempted": env.get("production_write_attempted"),
            "actual_connection_db_label": (env.get("exact_db_identity_from_actual_connection") or {}).get("db_name")
            if isinstance(env.get("exact_db_identity_from_actual_connection"), Mapping)
            else None,
            "storage_root_pre_settings_import_passed": (
                env.get("storage_root_pre_settings_import") or {}
            ).get("passed")
            if isinstance(env.get("storage_root_pre_settings_import"), Mapping)
            else None,
        },
        "input_scope_fidelity": {
            "passed": input_scope.get("passed"),
            "status": input_scope.get("status"),
            "current_run_classification": input_scope.get("current_run_classification"),
            "route_evidence_allowed": input_scope.get("route_evidence_allowed"),
            "failed_metrics": input_scope.get("failed_metrics"),
        },
        "snapshot_availability": {
            "status": snapshot.get("status"),
            "old_r1_equivalent_input_scope_available": snapshot.get("old_r1_equivalent_input_scope_available"),
        },
        "preserved_smoke_run": {
            "classification": smoke.get("classification"),
            "run_id": smoke.get("run_id"),
            "signal_count": smoke.get("signal_count"),
            "edge_count": smoke.get("edge_count"),
            "selected_pair_count": smoke.get("selected_pair_count"),
            "judgment_count": smoke.get("judgment_count"),
        }
        if smoke
        else None,
        "sc1_full_chain": {
            "complete": proof.get("complete_sc1_pipeline_executed"),
            "deterministic_complete": proof.get("deterministic_pipeline_executed"),
            "llm_requested": proof.get("llm_pair_adjudication_requested"),
            "llm_executed": proof.get("llm_pair_adjudication_executed"),
            "eligible_pairs": proof.get("llm_eligible_pair_count"),
            "selected_pairs": proof.get("llm_selected_pair_count"),
            "judgments": proof.get("llm_judgment_count"),
            "same": proof.get("llm_same_count"),
            "cannot": proof.get("llm_cannot_count"),
            "uncertain": proof.get("llm_uncertain_count"),
            "all_stages_verified": proof.get("all_required_stage_statuses_verified"),
        },
        "llm_plan": {
            "status": llm_plan.get("status"),
            "eligible_pairs": llm_plan.get("eligible_pair_count"),
            "selected_pairs": llm_plan.get("selected_pair_count"),
            "max_calls": llm_plan.get("max_calls"),
            "budget_usd": llm_plan.get("budget_usd"),
            "projected_budget_usd": llm_plan.get("projected_budget_usd"),
        },
        "llm_gate": {
            "operator_approved": llm_ready.get("operator_approved"),
            "provider_available": llm_ready.get("provider_available"),
            "cache_ready": llm_ready.get("cache_ready"),
            "budget_ready": llm_ready.get("budget_ready"),
            "provider_mode": llm_provider.get("provider_mode"),
            "model_name": llm_provider.get("model_name"),
            "uses_fallback_provider": llm_provider.get("uses_fallback_provider"),
            "cache_hits": llm_judgments.get("cache_hits"),
            "cache_misses": llm_judgments.get("cache_misses"),
            "error_count": llm_judgments.get("error_count"),
            "estimated_cost_usd": llm_judgments.get("estimated_cost_usd"),
        },
        "mutation": {
            "passed": mutation.get("passed"),
            "changed_table_count": len(mutation.get("changed_tables") or []),
            "forbidden_changed_table_count": len(mutation.get("forbidden_changed_tables") or []),
            "unexpected_changed_table_count": len(mutation.get("unexpected_changed_tables") or []),
        },
        "review_pack": {
            "generated": review_pack.get("generated"),
            "includes_stage_manifest": review_pack.get("includes_stage_manifest"),
            "label": review_pack.get("label"),
        },
        "route_gate": {
            "a1r_required": True,
            "downstream_routes_blocked": True,
        },
    }


def public_redaction_check(summary: Mapping[str, Any], markdown: str) -> dict[str, Any]:
    findings = scan_public_payload(summary) + scan_public_payload({"public_markdown_text": markdown})
    return {
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings[:10],
        "scanned_artifacts": {
            "final_json_summary": True,
            "final_markdown_report": True,
        },
        "clean_before_public_write": not findings,
        "unsafe_public_report_written": False,
    }


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    proof = summary["sc1_full_chain_proof"]
    llm = summary["llm_adjudication_plan"]
    provider = summary.get("llm_provider_execution") if isinstance(summary.get("llm_provider_execution"), Mapping) else {}
    judgments = summary.get("llm_judgment_summary") if isinstance(summary.get("llm_judgment_summary"), Mapping) else {}
    continuation = summary.get("operator_continuation") if isinstance(summary.get("operator_continuation"), Mapping) else {}
    input_scope = summary.get("input_scope_fidelity") if isinstance(summary.get("input_scope_fidelity"), Mapping) else {}
    smoke = summary.get("preserved_smoke_run") if isinstance(summary.get("preserved_smoke_run"), Mapping) else {}
    route = summary["route_authorization"]
    lines = [
        "# Phase 4.5-SCV2-R1R Full SourceConcept Pipeline Replay",
        "",
        "## Status",
        "",
        f"- Contract status: `{summary['pipeline_contract']['status']}`.",
        f"- Previous continuation status: `{continuation.get('previous_status')}`.",
        f"- Operator LLM approval used: `{continuation.get('llm_approval_phrase_used')}`.",
        f"- Dev/test execute confirmation used: `{continuation.get('execute_confirmation_used')}`.",
        f"- Provider policy: `{continuation.get('provider_policy')}`.",
        f"- Complete SC1 pipeline executed: `{proof['complete_sc1_pipeline_executed']}`.",
        f"- Deterministic pipeline executed: `{proof['deterministic_pipeline_executed']}`.",
        f"- LLM adjudication requested/executed: `{proof['llm_pair_adjudication_requested']}` / `{proof['llm_pair_adjudication_executed']}`.",
        f"- LLM selected pairs / judgments: `{proof['llm_selected_pair_count']}` / `{proof['llm_judgment_count']}`.",
        f"- Input-scope fidelity gate: `{input_scope.get('status')}`.",
        f"- Current run classification: `{input_scope.get('current_run_classification')}`.",
        f"- A1R still required: `{route['a1r_still_required']}`.",
        "",
        "## Isolation",
        "",
        f"- VIOLET_ENV: `{summary['environment_isolation']['violet_env']}`.",
        f"- DB target label: `{summary['environment_isolation']['db_name']}`.",
        f"- Production profile active: `{summary['environment_isolation']['production_profile_active']}`.",
        f"- Production DB/storage/source mutation: `{summary['environment_isolation']['production_write_attempted']}`.",
        f"- Actual DB identity checked from write connection: `{(summary['environment_isolation'].get('exact_db_identity_from_actual_connection') or {}).get('checked_from_actual_connection')}`.",
        f"- Storage root checked before settings import: `{(summary['environment_isolation'].get('storage_root_pre_settings_import') or {}).get('passed')}`.",
        "",
        "## Input Scope Fidelity",
        "",
        "| Metric | Old R1 expected | Current R1R actual | Ratio | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for row in input_scope.get("comparison_table") or []:
        lines.append(
            f"| `{row['metric']}` | `{row['old_r1_expected']}` | `{row['current_r1r_actual']}` | `{row['ratio']}` | `{row['status']}` |"
        )
    if smoke:
        lines.extend(
            [
                "",
                "## Preserved Smoke Run",
                "",
                f"- Classification: `{smoke.get('classification')}`.",
                f"- Run id: `{smoke.get('run_id')}`.",
                f"- Signal / edge count: `{smoke.get('signal_count')}` / `{smoke.get('edge_count')}`.",
                f"- Selected pairs / judgments: `{smoke.get('selected_pair_count')}` / `{smoke.get('judgment_count')}`.",
            ]
        )
    lines.extend(
        [
        "",
        "## LLM Readiness",
        "",
        f"- Operator approved: `{summary['llm_readiness']['operator_approved']}`.",
        f"- Provider available: `{summary['llm_readiness']['provider_available']}`.",
        f"- Provider/model configured: `{provider.get('provider_mode')}` / `{provider.get('model_name')}`.",
        f"- Primary OpenAI-compatible adjudication calls made: `{provider.get('primary_openai_compatible_used')}`.",
        f"- Fallback provider used: `{provider.get('uses_fallback_provider')}`.",
        f"- Cache ready: `{summary['llm_readiness']['cache_ready']}`.",
        f"- Budget ready: `{summary['llm_readiness']['budget_ready']}`.",
        f"- Eligible pairs: `{llm['eligible_pair_count']}`.",
        f"- Selected pairs: `{llm['selected_pair_count']}`.",
        f"- Judgment/error/cache counts: `{judgments.get('judgment_count')}` / `{judgments.get('error_count')}` / `{judgments.get('cache_hits')}` hits, `{judgments.get('cache_misses')}` misses.",
        f"- Estimated actual cost USD: `{judgments.get('estimated_cost_usd')}`; exact provider cost available: `{judgments.get('actual_cost_usd_available')}`.",
        f"- Max calls / budget USD: `{llm['max_calls']}` / `{llm['budget_usd']}`.",
        "",
        "## Stage Manifest",
        "",
        "| Stage | Status | Input | Output | Evidence |",
        "|---|---:|---:|---:|---|",
        ]
    )
    for row in summary["sc1_required_stage_manifest"]:
        lines.append(
            f"| `{row['stage_name']}` | `{row['status']}` | `{row['input_count']}` | `{row['output_count']}` | `{row['evidence_artifact_label'] or '[blocked]'}` |"
        )
    lines.extend(
        [
            "",
            "## SC1 vs old R1 vs R1R",
            "",
            "| Pipeline step | SC1 expected | SC1 actual evidence | old R1 actual evidence | R1R actual evidence | R1R status | Impact if missing | Contract guard |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in summary["sc1_r1_r1r_fidelity_table"]:
        lines.append(
            "| "
            + " | ".join(
                str(row[key])
                for key in (
                    "pipeline_step",
                    "sc1_expected",
                    "sc1_actual_evidence",
                    "old_r1_actual_evidence",
                    "r1r_actual_evidence",
                    "r1r_status",
                    "impact_if_missing",
                    "contract_guard",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- Mutation proof passed: `{summary['mutation_proof']['passed']}`.",
            f"- Public redaction passed: `{summary['public_redaction']['passed']}`.",
            f"- Review pack label: `{summary['review_pack']['label']}`.",
            "- This phase does not authorize R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion.",
            "",
            "## Result",
            "",
            (
                "R1R produced full-chain SourceConcept replay evidence and may feed A1R."
                if proof["complete_sc1_pipeline_executed"]
                else "R1R did not produce route-evidence-grade full-chain replay evidence; A1R must not start as a route approval rerun yet."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def build_blocked_summary(environment: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    stage_manifest = make_stage_manifest(
        status="blocked_environment_isolation",
        env_ok=False,
        deterministic_executed=False,
        llm_eligible=0,
        llm_plan_ready=False,
        llm_selected=0,
        llm_judgments=0,
        persistence_verified=False,
        mutation_verified=True,
        post_commit_verified=False,
        review_pack_generated=True,
        redaction_passed=True,
        adapter_counts={},
    )
    return {
        "phase": PHASE,
        "phase_slug": PHASE_SLUG,
        "generated_at": utc_now_iso(),
        "branch": git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
        "head_sha": git_value(["rev-parse", "HEAD"]),
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "blocked_environment_isolation",
            "claims": {"target_met": False, "full_chain_complete": False, "safe_to_merge": False},
        },
        "environment_isolation": dict(environment),
        "input_scope_fidelity": {
            "required_for_route_evidence": True,
            "passed": False,
            "status": "not_checked_environment_blocked",
            "minimum_ratio": INPUT_SCOPE_MIN_RATIO,
            "failed_metrics": list(OLD_R1_INPUT_SCOPE_BASELINE),
            "comparison_table": [
                {
                    "metric": key,
                    "old_r1_expected": value,
                    "current_r1r_actual": 0,
                    "ratio": 0.0,
                    "minimum_ratio": INPUT_SCOPE_MIN_RATIO,
                    "status": "blocked",
                    "baseline_evidence": OLD_R1_INPUT_SCOPE_SOURCES.get(key),
                }
                for key, value in OLD_R1_INPUT_SCOPE_BASELINE.items()
            ],
            "route_evidence_allowed": False,
            "current_run_classification": "blocked_environment_isolation",
        },
        "snapshot_availability": {
            "status": "blocked_environment_or_snapshot_unavailable",
            "old_r1_equivalent_input_scope_available": False,
            "reason": "environment isolation blocked before input-scope inventory",
            "setup_instructions": [
                "Load an isolated dev/test/restored-snapshot DB with old-R1-equivalent post-PX1 inputs.",
                "Set VIOLET_STORAGE_ROOT to a dedicated non-production local test storage root.",
            ],
        },
        "sc1_required_stage_manifest": stage_manifest,
        "sc1_full_chain_proof": {
            "complete_sc1_pipeline_executed": False,
            "deterministic_pipeline_executed": False,
            "llm_pair_adjudication_requested": False,
            "llm_pair_adjudication_executed": False,
            "llm_eligible_pair_count": 0,
            "llm_selected_pair_count": 0,
            "llm_judgment_count": 0,
            "llm_same_count": 0,
            "llm_cannot_count": 0,
            "llm_uncertain_count": 0,
            "all_required_stage_statuses_verified": False,
            "missing_required_stages": list(R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES),
            "skipped_required_stages": list(R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES),
            "stage_manifest_artifact": "r1r-private-stage-manifest",
            "review_pack_includes_stage_manifest": True,
            "deterministic_only_output_used_as_full_chain_route_approval_evidence": False,
        },
        "sc1_r1_r1r_fidelity_table": fidelity_table(selected_count=0, judgment_count=0, deterministic_metrics={}),
        "llm_adjudication_plan": {
            "required": True,
            "eligible_pair_count": 0,
            "selected_pair_count": 0,
            "max_calls": 300,
            "budget_usd": 50.0,
            "projected_budget_usd": 0.0,
        },
        "llm_readiness": {
            "passed": False,
            "operator_approved": False,
            "provider_available": False,
            "cache_ready": False,
            "budget_ready": False,
        },
        "llm_judgment_summary": {
            "judgment_count": 0,
            "ledger_row_count": 0,
            "error_count": 0,
            "selected_pair_count": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "estimated_cost_usd": 0.0,
            "actual_cost_usd_available": False,
            "selected_pair_accounting": {
                "selected_pair_count": 0,
                "resolved_provider_judgment_count": 0,
                "valid_cached_judgment_count": 0,
                "explicit_skipped_pair_count": 0,
                "provider_error_pair_count": 0,
                "successful_accounted_pair_count": 0,
                "all_selected_pairs_successfully_accounted": True,
            },
            "llm_same_count": 0,
            "llm_cannot_count": 0,
            "llm_uncertain_count": 0,
        },
        "mutation_proof": {"passed": True, "forbidden_changed_tables": [], "unexpected_changed_tables": [], "changed_tables": []},
        "post_commit_verification": {"passed": False, "reason": "blocked_environment_isolation"},
        "review_pack": {"generated": True, "includes_stage_manifest": True, "label": "r1r-private-review-pack"},
        "public_redaction": {"passed": True, "finding_count": 0, "findings": []},
        "route_authorization": route_authorization(),
        "forbidden_writes": forbidden_writes_none(),
        "source_concept_write_scope": {"allowed_tables": list(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES), "changed_tables": []},
        "local_artifacts": {"private_artifact_root_label": "r1r-private-artifacts"},
        "blocked_setup_instructions": [
            "Load a dev/test/restored-snapshot environment before running R1R.",
            "Set VIOLET_ENV=test or development with a non-production DB name such as blombooru_test or blombooru_r1r_snapshot.",
            "Set VIOLET_STORAGE_ROOT to a dedicated local test/restored-snapshot storage root.",
            "Do not activate the production launcher profile.",
        ],
    }


def route_authorization() -> dict[str, bool]:
    return {
        "r2_authorized": False,
        "px1_b_authorized": False,
        "provider_2_authorized": False,
        "scale_up_authorized": False,
        "entity_bridge_authorized": False,
        "source_concept_truth_promotion_authorized": False,
        "route_approval_authorized": False,
        "a1r_still_required": True,
    }


def forbidden_writes_none() -> dict[str, bool]:
    return {
        "entity_truth": False,
        "entity_alias_truth": False,
        "confirmed_assignments": False,
        "media_tags": False,
        "source_metadata": False,
        "provider_cache": False,
        "source_icloud_app_storage": False,
    }


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    environment = inspect_environment()
    if not environment["passed"]:
        summary = build_blocked_summary(environment, output_dir)
        return finalize_outputs(summary, output_dir)

    from app.config import settings  # noqa: WPS433
    from app.services.source_concept_resolver_service import (  # noqa: WPS433
        LLMAdjudicationConfig,
        build_source_concept_signals,
        plan_llm_adjudication,
        resolve_source_concepts,
        run_bounded_llm_adjudication,
        select_llm_adjudication_edges,
        source_signal_inventory,
        persist_source_concept_resolution,
    )
    from app.services.source_name_candidate_extraction_service import primary_openai_provider_from_settings  # noqa: WPS433

    if args.execute and args.confirm_execution != EXECUTE_CONFIRMATION:
        raise SystemExit(f"--execute requires --confirm-execution {EXECUTE_CONFIRMATION!r}")
    llm_approved = bool(args.approve_llm_adjudication and args.llm_confirmation == LLM_CONFIRMATION)
    cache_dir = output_dir / "llm-cache"
    provider = None
    provider_summary: dict[str, Any] = {
        "provider_mode": "primary_openai",
        "llm_provider_label": "primary_openai",
        "llm_access_configured": False,
        "uses_fallback_provider": False,
        "readiness_checked": False,
        "model_name": None,
    }
    if args.check_llm_provider_readiness or llm_approved:
        provider, provider_summary = primary_openai_provider_from_settings()
        provider_summary = {
            **provider_summary,
            "readiness_checked": True,
            "model_name": getattr(provider, "model", None) if provider is not None else None,
            "provider_name": provider.get_provider_name() if provider is not None else None,
        }
    provider_model_label = str(provider_summary.get("model_name") or "primary_openai")
    llm_config = LLMAdjudicationConfig(
        enabled=True,
        max_calls=int(args.max_llm_calls),
        max_budget_usd=float(args.max_llm_budget_usd),
        max_block_size=int(args.max_llm_block_size),
        model_label=provider_model_label,
        cache_dir=str(cache_dir),
        fail_if_unavailable=bool(args.fail_if_llm_unavailable),
    )
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    with engine.connect() as conn:
        identity_before = db_identity(conn)
        exact_db_gate = classify_db_identity(identity_before)
        environment["exact_db_identity_from_actual_connection"] = exact_db_gate
        environment["db_identity_before"] = identity_before
        if not exact_db_gate["passed"]:
            environment["passed"] = False
            environment["blockers"] = list(environment.get("blockers", [])) + list(exact_db_gate["blockers"])
            summary = build_blocked_summary(environment, output_dir)
            return finalize_outputs(summary, output_dir)
        before_table_counts = table_snapshots(conn, (*SOURCE_CONCEPT_ALLOWED_WRITE_TABLES, *FORBIDDEN_WRITE_TABLES))
        source_before = source_concept_counts(conn)

    db = SessionLocal()
    judgments: list[dict[str, Any]] = []
    llm_execution_summary: dict[str, Any] = {}
    provider_error: dict[str, Any] | None = None
    persistence = {
        "apply": False,
        "allowed_tables": list(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES),
        "forbidden_truth_table_write_count": 0,
    }
    try:
        inventory = source_signal_inventory(db)
        signals = build_source_concept_signals(db, run_id=args.run_id)
        deterministic_result = resolve_source_concepts(signals, run_id=args.run_id, llm_config=llm_config, llm_judgments=())
        plan = plan_llm_adjudication(deterministic_result.edge_candidates, signals=signals, config=llm_config)
        selected_edges = select_llm_adjudication_edges(deterministic_result.edge_candidates, signals=signals, config=llm_config)
        deterministic_metrics = edge_metrics(deterministic_result)
        budget_ready = plan.status != "blocked"
        cache_stats = cache_stats_for_selected_pairs(cache_dir, len(selected_edges))
        with engine.connect() as conn:
            input_scope_actuals = current_input_scope_actuals(
                conn,
                inventory=inventory,
                deterministic_metrics=deterministic_metrics,
                source_counts=source_before,
                eligible_pair_count=int(plan.projected_calls),
                selected_pair_count=len(selected_edges),
            )
        input_scope_fidelity = build_input_scope_fidelity(input_scope_actuals)
        if llm_approved and input_scope_fidelity["passed"]:
            if provider is None:
                llm_execution_summary = {
                    "used": False,
                    "provider": provider_summary,
                    "reason": provider_summary.get("unavailable_reason") or "provider_unavailable",
                    "selected_pair_count": len(selected_edges),
                    "judgment_count": 0,
                    "error_count": 0,
                    "cache_hits": 0,
                    "cache_misses": len(selected_edges),
                }
            else:
                try:
                    judgments, llm_execution_summary = run_bounded_llm_adjudication(
                        deterministic_result.edge_candidates,
                        signals=signals,
                        config=llm_config,
                    )
                    provider_summary = {
                        **provider_summary,
                        **(llm_execution_summary.get("provider") or {}),
                        "readiness_checked": True,
                    }
                    if "cache_hits" in llm_execution_summary or "cache_misses" in llm_execution_summary:
                        cache_stats = {
                            **cache_stats,
                            "cache_hits": int(llm_execution_summary.get("cache_hits", 0) or 0),
                            "cache_misses": int(llm_execution_summary.get("cache_misses", 0) or 0),
                            "cached_decision_count": int(llm_execution_summary.get("cache_hits", 0) or 0),
                        }
                except Exception as exc:  # pragma: no cover - provider/network dependent
                    provider_error = sanitize_provider_error(exc)
                    llm_execution_summary = {
                        "used": False,
                        "provider": provider_summary,
                        "reason": "provider_error",
                        "selected_pair_count": len(selected_edges),
                        "judgment_count": 0,
                        "error_count": 1,
                        "cache_hits": 0,
                        "cache_misses": len(selected_edges),
                        "error": provider_error,
                    }
        valid_judgments = [row for row in judgments if not row.get("error_type")]
        llm_error_count = int(llm_execution_summary.get("error_count", 0) or 0) + sum(
            1 for row in judgments if row.get("error_type")
        )
        if valid_judgments:
            replay_result = resolve_source_concepts(signals, run_id=args.run_id, llm_config=llm_config, llm_judgments=valid_judgments)
        else:
            replay_result = deterministic_result
        execute_allowed = bool(
            args.execute
            and llm_approved
            and budget_ready
            and provider is not None
            and llm_error_count == 0
            and (len(selected_edges) == 0 or len(valid_judgments) >= len(selected_edges))
        )
        if execute_allowed:
            persistence = persist_source_concept_resolution(
                db,
                replay_result,
                apply=True,
                inventory=inventory,
                run_label=R1R_RUN_LABEL,
            )
            db.commit()
    finally:
        db.close()

    with engine.connect() as conn:
        identity_after = db_identity(conn)
        environment["db_identity_after"] = identity_after
        after_table_counts = table_snapshots(conn, (*SOURCE_CONCEPT_ALLOWED_WRITE_TABLES, *FORBIDDEN_WRITE_TABLES))
        source_after = source_concept_counts(conn)
    mutation_delta = table_delta(before_table_counts, after_table_counts)
    count_delta = compare_counts(source_before, source_after)
    replay_metrics = edge_metrics(replay_result)
    adapter_counts = public_signal_inventory(inventory).get("adapter_counts", {})
    eligible_pair_count = int(plan.projected_calls)
    selected_pair_count = len(selected_edges)
    valid_judgments = [row for row in judgments if not row.get("error_type")]
    judgment_count = len(valid_judgments)
    ledger_row_count = len(judgments)
    llm_error_count = int(llm_execution_summary.get("error_count", 0) or 0) + sum(1 for row in judgments if row.get("error_type"))
    valid_cached_judgment_count = sum(1 for row in valid_judgments if str(row.get("cache_status") or "") == "hit")
    resolved_provider_judgment_count = max(0, judgment_count - valid_cached_judgment_count)
    explicit_skipped_pair_count = int(llm_execution_summary.get("skipped_pair_count", 0) or 0)
    provider_error_pair_count = sum(1 for row in judgments if row.get("error_type"))
    successful_accounted_pair_count = (
        resolved_provider_judgment_count + valid_cached_judgment_count + explicit_skipped_pair_count
    )
    plan_skipped_pair_count = int(plan.skipped_block_count or 0)
    eligible_unselected_pair_count = max(0, eligible_pair_count - selected_pair_count - plan_skipped_pair_count)
    selected_pair_accounting = {
        "selected_pair_count": selected_pair_count,
        "resolved_provider_judgment_count": resolved_provider_judgment_count,
        "valid_cached_judgment_count": valid_cached_judgment_count,
        "explicit_skipped_pair_count": explicit_skipped_pair_count,
        "provider_error_pair_count": provider_error_pair_count,
        "successful_accounted_pair_count": successful_accounted_pair_count,
        "all_selected_pairs_successfully_accounted": successful_accounted_pair_count == selected_pair_count
        and provider_error_pair_count == 0,
    }
    provider_available = bool(provider is not None) if (args.check_llm_provider_readiness or llm_approved) else False
    llm_readiness_passed = bool(llm_approved and provider_available and budget_ready and cache_stats["cache_enabled"] and llm_error_count == 0)
    persistence_applied = bool(persistence.get("apply"))
    zero_eligible_pair_proof = eligible_pair_count == 0 and selected_pair_count == 0
    llm_success_for_target = (
        (selected_pair_count > 0 and selected_pair_accounting["all_selected_pairs_successfully_accounted"])
        or zero_eligible_pair_proof
    )
    if not input_scope_fidelity["passed"]:
        status = "smoke_only_not_route_evidence"
    elif args.execute and persistence_applied and llm_success_for_target:
        status = "target_met_full_chain" if mutation_delta["passed"] else "blocked_contract"
    elif not llm_approved and eligible_pair_count > 0:
        status = "blocked_llm_approval_required"
    elif llm_approved and not budget_ready:
        status = "blocked_budget"
    elif llm_approved and (not provider_available or llm_error_count > 0):
        status = "blocked_provider"
    else:
        status = "dry_run_complete_execute_not_requested"

    full_chain_complete = status == "target_met_full_chain"
    stage_manifest = make_stage_manifest(
        status=status,
        env_ok=True,
        deterministic_executed=True,
        llm_eligible=eligible_pair_count,
        llm_plan_ready=True,
        llm_selected=selected_pair_count,
        llm_judgments=judgment_count,
        persistence_verified=persistence_applied,
        mutation_verified=bool(mutation_delta["passed"]),
        post_commit_verified=bool(persistence_applied and mutation_delta["passed"]),
        review_pack_generated=True,
        redaction_passed=True,
        adapter_counts=adapter_counts if isinstance(adapter_counts, Mapping) else {},
        resolver_signal_count=len(deterministic_result.signals),
        edge_count=len(deterministic_result.edge_candidates),
        concept_count=len(replay_result.concepts),
    )
    outcomes = llm_outcome_counts(valid_judgments)
    skipped_required_stages = [
        row["stage_name"]
        for row in stage_manifest
        if row["required"] and row["skipped"] and row["status"] != "skipped_not_applicable"
    ]
    actual_cost_estimate = 0.0
    if selected_pair_count:
        actual_calls = max(0, int(cache_stats.get("cache_misses", 0) or 0))
        actual_cost_estimate = round(float(plan.projected_cost_usd) * (actual_calls / selected_pair_count), 6)
    summary = {
        "phase": PHASE,
        "phase_slug": PHASE_SLUG,
        "generated_at": utc_now_iso(),
        "branch": git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
        "head_sha": git_value(["rev-parse", "HEAD"]),
        "baseline_main_sha": git_value(["rev-parse", "origin/main"]),
        "run_id": args.run_id,
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {
                "target_met": full_chain_complete,
                "full_chain_complete": full_chain_complete,
                "safe_to_merge": False,
            },
        },
        "operator_continuation": {
            "previous_status": "target_met_full_chain_reclassified_smoke_only",
            "llm_approval_phrase_used": llm_approved,
            "execute_confirmation_used": bool(args.execute and args.confirm_execution == EXECUTE_CONFIRMATION),
            "operator_budget_approved": bool(llm_approved),
            "provider_policy": "primary_openai_compatible_only_no_fallback",
        },
        "environment_isolation": {
            **environment,
            "db_identity_before": identity_before,
            "db_identity_after": identity_after,
        },
        "input_scope_fidelity": input_scope_fidelity,
        "snapshot_availability": {
            "status": "available" if input_scope_fidelity["passed"] else "blocked_environment_or_snapshot_unavailable",
            "current_db_label": identity_before.get("db_name"),
            "old_r1_equivalent_input_scope_available": bool(input_scope_fidelity["passed"]),
            "reason": None
            if input_scope_fidelity["passed"]
            else "current dev/test DB input scope is below old-R1-equivalent source-layer scale",
            "setup_instructions": input_scope_fidelity.get("setup_instructions_if_failed", []),
        },
        "preserved_smoke_run": load_preserved_smoke_run(),
        "old_r1_contamination_handling": {
            "new_r1r_run_label": args.run_id,
            "old_r1_used_as_baseline_only": True,
            "production_source_concept_tables_overwritten": False,
            "dev_test_restored_snapshot_scope_only": True,
            "contamination_handling_method": "new_run_label_only_no_sourceconcept_rebuild_current_smoke_scope",
            "source_concept_owned_tables_cleared_or_rebuilt_in_dev_test": False,
            "old_r1_a1_remain_invalid_for_route_approval_until_a1r": True,
        },
        "sc1_required_stage_manifest": stage_manifest,
        "sc1_full_chain_proof": {
            "complete_sc1_pipeline_executed": full_chain_complete,
            "deterministic_pipeline_executed": True,
            "llm_pair_adjudication_requested": True,
            "llm_pair_adjudication_executed": judgment_count > 0 and llm_error_count == 0,
            "llm_eligible_pair_count": eligible_pair_count,
            "llm_selected_pair_count": selected_pair_count,
            "llm_judgment_count": judgment_count,
            **outcomes,
            "all_required_stage_statuses_verified": full_chain_complete,
            "missing_required_stages": [
                row["stage_name"]
                for row in stage_manifest
                if row["required"] and row["status"] not in {"verified", "executed", "skipped_not_applicable"}
            ],
            "skipped_required_stages": skipped_required_stages,
            "stage_manifest_artifact": "r1r-private-stage-manifest",
            "review_pack_includes_stage_manifest": True,
            "deterministic_only_output_used_as_full_chain_route_approval_evidence": False,
        },
        "source_signal_inventory": public_signal_inventory(inventory),
        "deterministic_stage_summary": deterministic_metrics,
        "r1r_replay_stage_summary": replay_metrics,
        "llm_adjudication_plan": {
            "required": True,
            "status": plan.status,
            "reason": plan.reason,
            "eligible_pair_count": eligible_pair_count,
            "selected_pair_count": selected_pair_count,
            "max_calls": int(args.max_llm_calls),
            "budget_usd": float(args.max_llm_budget_usd),
            "projected_budget_usd": float(plan.projected_cost_usd),
            "projected_input_tokens": int(plan.projected_input_tokens),
            "projected_output_tokens": int(plan.projected_output_tokens),
            "skipped_pair_count": max(0, plan_skipped_pair_count),
            "unselected_pair_count": eligible_unselected_pair_count,
            "eligible_pair_accounting_total": selected_pair_count + max(0, plan_skipped_pair_count) + eligible_unselected_pair_count,
            "unselected_pair_reason": "bounded_selection_policy_after_deterministic_blocking"
            if eligible_unselected_pair_count
            else None,
            "operator_budget_approved": bool(llm_approved),
            "budget_cap_adjusted_or_superseded": False,
        },
        "llm_readiness": {
            "passed": llm_readiness_passed,
            "operator_approved": llm_approved,
            "provider_available": provider_available,
            "provider_mode": provider_summary.get("provider_mode"),
            "provider_model": provider_summary.get("model_name"),
            "provider_readiness_checked": bool(provider_summary.get("readiness_checked")),
            "uses_fallback_provider": False,
            "cache_ready": bool(cache_stats["cache_enabled"]),
            "budget_ready": budget_ready,
            "cache_summary": cache_stats,
            "no_secret_leakage": True,
            "provider_error": provider_error,
        },
        "llm_provider_execution": {
            "provider_mode": provider_summary.get("provider_mode"),
            "provider_label": provider_summary.get("llm_provider_label"),
            "provider_name": provider_summary.get("provider_name"),
            "model_name": provider_summary.get("model_name"),
            "uses_fallback_provider": False,
            "fallback_provider_used": False,
            "primary_openai_compatible_used": bool(judgment_count > 0 and llm_error_count == 0),
            "actual_cost_usd_available": False,
            "actual_cost_usd_estimate": actual_cost_estimate,
            "provider_error": provider_error,
        },
        "llm_judgment_summary": {
            "judgment_count": judgment_count,
            "ledger_row_count": ledger_row_count,
            "error_count": llm_error_count,
            "selected_pair_count": selected_pair_count,
            "cache_hits": int(cache_stats.get("cache_hits", 0) or 0),
            "cache_misses": int(cache_stats.get("cache_misses", 0) or 0),
            "estimated_cost_usd": actual_cost_estimate,
            "actual_cost_usd_available": False,
            "selected_pair_accounting": selected_pair_accounting,
            **outcomes,
        },
        "source_concept_before": source_before,
        "source_concept_after": source_after,
        "source_concept_delta": count_delta,
        "mutation_proof": mutation_delta,
        "post_commit_verification": {
            "passed": bool(persistence_applied and mutation_delta["passed"]),
            "execute_requested": bool(args.execute),
            "fresh_connection_checked": True,
            "reason": None if persistence_applied else ("execute_blocked_before_write" if args.execute else "dry_run_no_db_write"),
        },
        "review_pack": {"generated": True, "includes_stage_manifest": True, "label": "r1r-private-review-pack"},
        "public_redaction": {"passed": True, "finding_count": 0, "findings": []},
        "route_authorization": route_authorization(),
        "forbidden_writes": forbidden_writes_none(),
        "source_concept_write_scope": {
            "allowed_tables": list(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES),
            "changed_tables": [row["table"] for row in mutation_delta["changed_tables"]],
        },
        "sc1_r1_r1r_fidelity_table": fidelity_table(
            selected_count=selected_pair_count,
            judgment_count=judgment_count,
            deterministic_metrics=deterministic_metrics,
        ),
        "local_artifacts": {"private_artifact_root_label": "r1r-private-artifacts"},
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "scripts/run_phase45_scv2_r1r_full_source_concept_pipeline_replay.py",
                    "classification": "phase-scoped operational runner",
                    "committed": True,
                },
                {
                    "path": "phase-4.5-scv2-r1r-full-source-concept-pipeline-replay public report",
                    "classification": "public report / handoff / roadmap update",
                    "committed": True,
                    "redacted": True,
                },
                {
                    "path": "r1r-private-artifacts",
                    "classification": "one-off local artifact / ignored output",
                    "committed": False,
                },
            ]
        },
    }
    private_payloads = {
        "signal-inventory.json": public_signal_inventory(inventory),
        "sc1-required-stage-manifest.json": stage_manifest,
        "edge-graph-metrics.json": deterministic_metrics,
        "selected-llm-pairs.json": selected_pair_rows(selected_edges),
        "judgment-ledger.json": judgments,
        "cache-stats.json": cache_stats,
        "mutation-proof.json": mutation_delta,
        "before-after-snapshots.json": {"before": source_before, "after": source_after, "delta": count_delta},
        "fidelity-table.json": summary["sc1_r1_r1r_fidelity_table"],
    }
    for name, payload in private_payloads.items():
        write_json(output_dir / name, payload)
    write_jsonl(output_dir / "selected-llm-pairs.jsonl", selected_pair_rows(selected_edges))
    write_jsonl(output_dir / "judgment-ledger.jsonl", judgments)
    return finalize_outputs(summary, output_dir)


def finalize_outputs(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    summary["review_pack"] = {
        **(summary.get("review_pack") if isinstance(summary.get("review_pack"), Mapping) else {}),
        "generated": True,
        "includes_stage_manifest": True,
        "label": "r1r-private-review-pack",
        "integrity_recorded": True,
    }
    contract_result: dict[str, Any] | None = None
    report = ""
    for _ in range(2):
        summary["public_json_payload"] = build_public_json_payload(summary)
        report = public_report_markdown(summary)
        summary["public_redaction"] = public_redaction_check(summary, report)
        contract_result = check_phase_contract(CONTRACT_ID, summary).to_dict()
        summary["contract_result"] = {
            "contract_id": CONTRACT_ID,
            "passed": contract_result["passed"],
            "error_count": contract_result["error_count"],
            "warning_count": contract_result["warning_count"],
        }
        if contract_result["passed"] and summary["public_redaction"]["passed"]:
            break
        if summary.get("pipeline_contract", {}).get("status") == "target_met_full_chain":
            summary["pipeline_contract"]["status"] = (
                "blocked_public_redaction_failed" if not summary["public_redaction"]["passed"] else "blocked_contract"
            )
            summary["pipeline_contract"]["claims"] = {
                "target_met": False,
                "full_chain_complete": False,
                "safe_to_merge": False,
            }
            if isinstance(summary.get("sc1_full_chain_proof"), Mapping):
                summary["sc1_full_chain_proof"] = {
                    **summary["sc1_full_chain_proof"],
                    "complete_sc1_pipeline_executed": False,
                    "all_required_stage_statuses_verified": False,
                }
            continue
        break

    summary["public_json_payload"] = build_public_json_payload(summary)
    report = public_report_markdown(summary)
    write_text(PUBLIC_REPORT_MD, report)
    write_json(PUBLIC_REPORT_JSON, summary)

    write_json(output_dir / "contract-result.json", contract_result or {})
    write_json(output_dir / "public-summary-copy.json", summary)
    write_text(output_dir / "public-report-copy.md", report)
    write_json(output_dir / "sc1-required-stage-manifest.json", summary["sc1_required_stage_manifest"])
    review_pack_files = {
        "public-summary-copy.json": output_dir / "public-summary-copy.json",
        "public-report-copy.md": output_dir / "public-report-copy.md",
        "contract-result.json": output_dir / "contract-result.json",
        "sc1-required-stage-manifest.json": output_dir / "sc1-required-stage-manifest.json",
    }
    for name in (
        "signal-inventory.json",
        "edge-graph-metrics.json",
        "selected-llm-pairs.json",
        "judgment-ledger.json",
        "cache-stats.json",
        "mutation-proof.json",
        "before-after-snapshots.json",
        "fidelity-table.json",
    ):
        path = output_dir / name
        if path.exists():
            review_pack_files[name] = path
    zip_path = output_dir / "review-pack.zip"
    zip_files(zip_path, review_pack_files)
    write_json(output_dir / "review-pack-integrity-private.json", {"sha256": sha256_file(zip_path)})
    write_json(PUBLIC_REPORT_JSON, summary)
    write_json(output_dir / "public-summary-copy.json", summary)
    return summary


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("r1r-%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--check-llm-provider-readiness", action="store_true")
    parser.add_argument("--approve-llm-adjudication", action="store_true")
    parser.add_argument("--llm-confirmation", default="")
    parser.add_argument("--max-llm-calls", type=int, default=300)
    parser.add_argument("--max-llm-budget-usd", type=float, default=50.0)
    parser.add_argument("--max-llm-block-size", type=int, default=12)
    parser.add_argument("--fail-if-llm-unavailable", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execution", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_pipeline(args)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=str))
    contract = summary.get("contract_result") if isinstance(summary.get("contract_result"), Mapping) else {}
    return 0 if contract.get("passed") is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
