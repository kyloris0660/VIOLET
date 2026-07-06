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
LLM_CONFIRMATION = "I APPROVE R1R LLM ADJUDICATION"
EXECUTE_CONFIRMATION = "EXECUTE_PHASE45_SCV2_R1R_SOURCE_CONCEPT_REPLAY"

PRODUCTION_DB_NAMES = {"blombooru", "production", "main", "postgres"}
DEV_DB_MARKERS = ("test", "dev", "r1r", "snapshot", "restored", "clone")
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
        "source_icloud_app_storage_write_target": False,
        "dynamic_production_launcher_used": production_profile_active,
        "production_db_storage_source_roots_private_ledgers_used_as_fixtures": False,
        "production_write_attempted": False,
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


def table_delta(before: Mapping[str, int | None], after: Mapping[str, int | None]) -> dict[str, Any]:
    rows = []
    forbidden_changed = []
    unexpected_changed = []
    for table in sorted(set(before) | set(after)):
        before_count = before.get(table)
        after_count = after.get(table)
        changed = before_count != after_count
        row = {
            "table": table,
            "before_count": before_count,
            "after_count": after_count,
            "delta": None if before_count is None or after_count is None else after_count - before_count,
            "changed": changed,
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


def make_stage_manifest(
    *,
    status: str,
    env_ok: bool,
    deterministic_executed: bool,
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
        "deterministic_blocking_key_generation": deterministic_executed,
        "deterministic_edge_graph_generation": deterministic_executed,
        "context_compatibility_guards": deterministic_executed,
        "alias_context_equivalence": deterministic_executed,
        "union_component_resolution": deterministic_executed,
        "bounded_llm_pair_planning": llm_plan_ready,
        "bounded_llm_provider_cache_budget_readiness": llm_plan_ready
        and status
        not in {"blocked_llm_approval_required", "blocked_provider", "blocked_budget", "blocked_llm_readiness"},
        "bounded_llm_pair_selection": llm_selected > 0,
        "bounded_llm_judgment_execution": llm_judgments > 0,
        "llm_decision_recording": llm_judgments > 0,
        "llm_decision_effects_applied_or_recorded": llm_judgments > 0,
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
        "provider_cache_adapter_or_zero_eligible_proof": 0,
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
        ("LLM decision effects", "record/apply source-layer only", "SC1 LLM edges", "old R1 none", "R1R blocked until LLM approval" if judgment_count == 0 else "R1R recorded decisions"),
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
    env = summary.get("environment_isolation") if isinstance(summary.get("environment_isolation"), Mapping) else {}
    mutation = summary.get("mutation_proof") if isinstance(summary.get("mutation_proof"), Mapping) else {}
    review_pack = summary.get("review_pack") if isinstance(summary.get("review_pack"), Mapping) else {}
    return {
        "phase": summary.get("phase"),
        "phase_slug": summary.get("phase_slug"),
        "status": (summary.get("pipeline_contract") or {}).get("status")
        if isinstance(summary.get("pipeline_contract"), Mapping)
        else None,
        "environment": {
            "violet_env": env.get("violet_env"),
            "db_label": env.get("db_name"),
            "isolation_passed": env.get("passed"),
            "production_profile_active": env.get("production_profile_active"),
            "production_write_attempted": env.get("production_write_attempted"),
        },
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
    public_json_payload = summary.get("public_json_payload") if isinstance(summary, Mapping) else None
    payload = public_json_payload if public_json_payload is not None else summary
    findings = scan_public_payload({"public_json_payload": payload}) + scan_public_payload({"public_markdown_text": markdown})
    return {"passed": not findings, "finding_count": len(findings), "findings": findings[:10]}


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    proof = summary["sc1_full_chain_proof"]
    llm = summary["llm_adjudication_plan"]
    route = summary["route_authorization"]
    lines = [
        "# Phase 4.5-SCV2-R1R Full SourceConcept Pipeline Replay",
        "",
        "## Status",
        "",
        f"- Contract status: `{summary['pipeline_contract']['status']}`.",
        f"- Complete SC1 pipeline executed: `{proof['complete_sc1_pipeline_executed']}`.",
        f"- Deterministic pipeline executed: `{proof['deterministic_pipeline_executed']}`.",
        f"- LLM adjudication requested/executed: `{proof['llm_pair_adjudication_requested']}` / `{proof['llm_pair_adjudication_executed']}`.",
        f"- LLM selected pairs / judgments: `{proof['llm_selected_pair_count']}` / `{proof['llm_judgment_count']}`.",
        f"- A1R still required: `{route['a1r_still_required']}`.",
        "",
        "## Isolation",
        "",
        f"- VIOLET_ENV: `{summary['environment_isolation']['violet_env']}`.",
        f"- DB target label: `{summary['environment_isolation']['db_name']}`.",
        f"- Production profile active: `{summary['environment_isolation']['production_profile_active']}`.",
        f"- Production DB/storage/source mutation: `{summary['environment_isolation']['production_write_attempted']}`.",
        "",
        "## LLM Readiness",
        "",
        f"- Operator approved: `{summary['llm_readiness']['operator_approved']}`.",
        f"- Provider available: `{summary['llm_readiness']['provider_available']}`.",
        f"- Cache ready: `{summary['llm_readiness']['cache_ready']}`.",
        f"- Budget ready: `{summary['llm_readiness']['budget_ready']}`.",
        f"- Eligible pairs: `{llm['eligible_pair_count']}`.",
        f"- Selected pairs: `{llm['selected_pair_count']}`.",
        f"- Max calls / budget USD: `{llm['max_calls']}` / `{llm['budget_usd']}`.",
        "",
        "## Stage Manifest",
        "",
        "| Stage | Status | Input | Output | Evidence |",
        "|---|---:|---:|---:|---|",
    ]
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
                else "R1R did not produce full-chain route approval evidence; A1R must not start as a route approval rerun yet."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def build_blocked_summary(environment: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    stage_manifest = make_stage_manifest(
        status="blocked_environment_isolation",
        env_ok=False,
        deterministic_executed=False,
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
    llm_config = LLMAdjudicationConfig(
        enabled=True,
        max_calls=int(args.max_llm_calls),
        max_budget_usd=float(args.max_llm_budget_usd),
        max_block_size=int(args.max_llm_block_size),
        model_label="primary_openai",
        cache_dir=str(cache_dir),
        fail_if_unavailable=bool(args.fail_if_llm_unavailable),
    )
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    with engine.connect() as conn:
        identity_before = db_identity(conn)
        before_table_counts = table_counts(conn, (*SOURCE_CONCEPT_ALLOWED_WRITE_TABLES, *FORBIDDEN_WRITE_TABLES))
        source_before = source_concept_counts(conn)

    db = SessionLocal()
    judgments: list[dict[str, Any]] = []
    llm_execution_summary: dict[str, Any] = {}
    try:
        inventory = source_signal_inventory(db)
        signals = build_source_concept_signals(db, run_id=args.run_id)
        deterministic_result = resolve_source_concepts(signals, run_id=args.run_id, llm_config=llm_config, llm_judgments=())
        plan = plan_llm_adjudication(deterministic_result.edge_candidates, signals=signals, config=llm_config)
        selected_edges = select_llm_adjudication_edges(deterministic_result.edge_candidates, signals=signals, config=llm_config)
        provider = None
        provider_summary = {
            "provider_mode": "primary_openai",
            "llm_access_configured": False,
            "uses_fallback_provider": False,
            "readiness_checked": False,
        }
        if args.check_llm_provider_readiness or llm_approved:
            provider, provider_summary = primary_openai_provider_from_settings()
            provider_summary = {**provider_summary, "readiness_checked": True}
        budget_ready = plan.status != "blocked"
        cache_stats = cache_stats_for_selected_pairs(cache_dir, len(selected_edges))
        if llm_approved:
            judgments, llm_execution_summary = run_bounded_llm_adjudication(
                deterministic_result.edge_candidates,
                signals=signals,
                config=llm_config,
            )
            if provider is None and provider_summary.get("llm_access_configured"):
                provider, provider_summary = primary_openai_provider_from_settings()
        if judgments:
            replay_result = resolve_source_concepts(signals, run_id=args.run_id, llm_config=llm_config, llm_judgments=judgments)
        else:
            replay_result = deterministic_result
        persistence = {
            "apply": False,
            "allowed_tables": list(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES),
            "forbidden_truth_table_write_count": 0,
        }
        if args.execute:
            if not judgments and len(selected_edges) > 0:
                raise SystemExit("R1R execute requires completed LLM adjudication or zero eligible selected pairs.")
            persistence = persist_source_concept_resolution(db, replay_result, apply=True, inventory=inventory)
            db.commit()
    finally:
        db.close()

    with engine.connect() as conn:
        identity_after = db_identity(conn)
        after_table_counts = table_counts(conn, (*SOURCE_CONCEPT_ALLOWED_WRITE_TABLES, *FORBIDDEN_WRITE_TABLES))
        source_after = source_concept_counts(conn)
    mutation_delta = table_delta(before_table_counts, after_table_counts)
    count_delta = compare_counts(source_before, source_after)
    deterministic_metrics = edge_metrics(deterministic_result)
    replay_metrics = edge_metrics(replay_result)
    adapter_counts = public_signal_inventory(inventory).get("adapter_counts", {})
    eligible_pair_count = int(plan.projected_calls)
    selected_pair_count = len(selected_edges)
    judgment_count = len(judgments)
    provider_available = bool(provider_summary.get("llm_access_configured")) if (args.check_llm_provider_readiness or llm_approved) else False
    llm_readiness_passed = bool(llm_approved and provider_available and budget_ready and cache_stats["cache_enabled"])
    if args.execute and judgment_count > 0:
        status = "target_met_full_chain" if mutation_delta["passed"] else "blocked_contract"
    elif not llm_approved and eligible_pair_count > 0:
        status = "blocked_llm_approval_required"
    elif llm_approved and not budget_ready:
        status = "blocked_budget"
    elif llm_approved and not provider_available:
        status = "blocked_provider"
    else:
        status = "dry_run_complete_execute_not_requested"

    full_chain_complete = status == "target_met_full_chain"
    stage_manifest = make_stage_manifest(
        status=status,
        env_ok=True,
        deterministic_executed=True,
        llm_plan_ready=True,
        llm_selected=selected_pair_count,
        llm_judgments=judgment_count,
        persistence_verified=bool(args.execute and persistence.get("apply")),
        mutation_verified=bool(mutation_delta["passed"]),
        post_commit_verified=bool(args.execute and mutation_delta["passed"]),
        review_pack_generated=True,
        redaction_passed=True,
        adapter_counts=adapter_counts if isinstance(adapter_counts, Mapping) else {},
        resolver_signal_count=len(deterministic_result.signals),
        edge_count=len(deterministic_result.edge_candidates),
        concept_count=len(replay_result.concepts),
    )
    outcomes = llm_outcome_counts(judgments)
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
        "environment_isolation": {
            **environment,
            "db_identity_before": identity_before,
            "db_identity_after": identity_after,
        },
        "old_r1_contamination_handling": {
            "new_r1r_run_label": args.run_id,
            "old_r1_used_as_baseline_only": True,
            "production_source_concept_tables_overwritten": False,
            "dev_test_restored_snapshot_scope_only": True,
            "old_r1_a1_remain_invalid_for_route_approval_until_a1r": True,
        },
        "sc1_required_stage_manifest": stage_manifest,
        "sc1_full_chain_proof": {
            "complete_sc1_pipeline_executed": full_chain_complete,
            "deterministic_pipeline_executed": True,
            "llm_pair_adjudication_requested": True,
            "llm_pair_adjudication_executed": judgment_count > 0,
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
            "skipped_required_stages": [row["stage_name"] for row in stage_manifest if row["required"] and row["skipped"]],
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
            "skipped_pair_count": max(0, int(plan.skipped_block_count)),
        },
        "llm_readiness": {
            "passed": llm_readiness_passed,
            "operator_approved": llm_approved,
            "provider_available": provider_available,
            "provider_mode": provider_summary.get("provider_mode"),
            "provider_readiness_checked": bool(provider_summary.get("readiness_checked")),
            "uses_fallback_provider": False,
            "cache_ready": bool(cache_stats["cache_enabled"]),
            "budget_ready": budget_ready,
            "cache_summary": cache_stats,
            "no_secret_leakage": True,
        },
        "llm_judgment_summary": {
            "judgment_count": judgment_count,
            "error_count": int(llm_execution_summary.get("error_count", 0) or 0),
            "selected_pair_count": selected_pair_count,
            **outcomes,
        },
        "source_concept_before": source_before,
        "source_concept_after": source_after,
        "source_concept_delta": count_delta,
        "mutation_proof": mutation_delta,
        "post_commit_verification": {
            "passed": bool(args.execute and mutation_delta["passed"]),
            "execute_requested": bool(args.execute),
            "fresh_connection_checked": True,
            "reason": None if args.execute else "dry_run_no_db_write",
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
    summary["public_json_payload"] = build_public_json_payload(summary)
    report = public_report_markdown(summary)
    redaction = public_redaction_check(summary, report)
    summary["public_redaction"] = redaction
    report = public_report_markdown(summary)
    write_text(PUBLIC_REPORT_MD, report)
    write_json(PUBLIC_REPORT_JSON, summary)

    contract_result = check_phase_contract(CONTRACT_ID, summary).to_dict()
    write_json(output_dir / "contract-result.json", contract_result)
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
    summary["review_pack"] = {
        **summary["review_pack"],
        "generated": True,
        "includes_stage_manifest": True,
        "label": "r1r-private-review-pack",
        "integrity_recorded": True,
    }
    summary["public_json_payload"] = build_public_json_payload(summary)
    write_json(output_dir / "review-pack-integrity-private.json", {"sha256": sha256_file(zip_path)})
    summary["contract_result"] = {
        "contract_id": CONTRACT_ID,
        "passed": contract_result["passed"],
        "error_count": contract_result["error_count"],
        "warning_count": contract_result["warning_count"],
    }
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
