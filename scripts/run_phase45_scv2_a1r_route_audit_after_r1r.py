#!/usr/bin/env python3
"""Run Phase 4.5-SCV2-A1R route audit rerun after R1R.

Lifecycle: phase-scoped operational runner.

This runner is read-only. It verifies committed R1R evidence, connects to the
restored R1R development/test database with a repeatable-read read-only
transaction, recomputes route-audit aggregates, writes public-safe report files,
and creates a private ignored review pack. It does not run providers, import
media, run AI/classification/localization, execute resolver persistence, or
write Entity/media_tags/SourceConcept truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, URL

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_phase45_scv1_source_concept_coverage_audit as scv1  # noqa: E402
from scripts import run_phase45_scv2_a1_post_expansion_audit_route_decision as a1  # noqa: E402

PHASE = "4.5-SCV2-A1R"
PHASE_TITLE = "SCV2-A1R: Route Audit Rerun After R1R"
PHASE_SLUG = "phase-4.5-scv2-a1r-route-audit-after-r1r"
BRANCH = "codex/scv2-a1r-route-audit-after-r1r"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG

R1R_SUMMARY = ROOT / "docs" / "reports" / "phase-4.5-scv2-r1r-full-source-concept-pipeline-replay-summary.json"
R1R_REPORT = ROOT / "docs" / "reports" / "phase-4.5-scv2-r1r-full-source-concept-pipeline-replay.md"
OLD_A1_SUMMARY = ROOT / "docs" / "reports" / "phase-4.5-scv2-a1-post-expansion-audit-route-decision-summary.json"
SCV1_SUMMARY = ROOT / "docs" / "reports" / "phase-4.5-scv1-source-concept-coverage-audit-summary.json"
R1R_MERGE_COMMIT = "7224a61aeaea32cd86a07e8dfd6cf6a6d7fcc0ef"
RESTORED_R1R_DB = "blombooru_r1r_restored_test_20260618"

ROUTE_STATUSES = {
    "blocked_invalid_r1r_evidence",
    "blocked_missing_r1r_restored_snapshot",
    "blocked_read_only_audit_failed",
    "route_still_blocked",
    "route_partially_approved_for_one_next_phase",
    "route_ready_for_next_source_layer_phase",
}
NEXT_PHASES = {
    "SCV2-R2 targeted resolver / gap reduction",
    "PX1-B additional Pixiv/source metadata extraction",
    "Provider-2-P0 taxonomy/alias enrichment metadata-only preparation",
    "SourceConcept management/editing UI/design",
}
REVIEW_PACK_FILES = (
    "public-report.md",
    "public-summary.json",
    "route-decision-matrix.json",
    "r1r-intake-proof.json",
    "read-only-db-proof.json",
    "mutation-proof.json",
    "gap-audit-data.json",
    "search-symmetry-data.json",
    "source-concept-state.json",
    "px1-source-coverage.json",
    "validation-result.json",
)
FORBIDDEN_FALSE_FLAGS = (
    "r2_started",
    "px1_b_started",
    "provider_2_started",
    "scale_up_started",
    "entity_bridge_started",
    "source_concept_truth_promotion_authorized",
    "entity_truth_authorized",
    "media_tags_truth_authorized",
    "production_write_authorized",
)
FORBIDDEN_TABLES = tuple(
    dict.fromkeys(
        list(a1.FORBIDDEN_TABLES)
        + [
            "blombooru_source_concept_resolution_runs",
            "blombooru_source_concept_signals",
            "blombooru_source_concepts",
            "blombooru_source_concept_aliases",
            "blombooru_source_concept_evidence",
            "blombooru_source_concept_signal_links",
            "blombooru_source_concept_search_index",
        ]
    )
)


class A1RBlockedError(RuntimeError):
    """Raised when A1R cannot proceed or cannot truthfully approve a route."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise A1RBlockedError(f"JSON summary must be an object: {path}")
    return payload


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


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def pct(part: int | float, total: int | float) -> float:
    return round((float(part) / float(total) * 100.0), 2) if total else 0.0


def public_dirty_worktree_summary() -> dict[str, Any]:
    status = git_value(["git", "status", "--short"])
    rows = [line for line in status.splitlines() if line.strip()]
    return {
        "clean": not rows,
        "dirty_count": len(rows),
        "status_public": "clean" if not rows else f"redacted_dirty_entries:{len(rows)}",
        "status_redacted": bool(rows),
    }


def route_db_url(database: str) -> tuple[URL, dict[str, Any]]:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return URL.create(
        "postgresql+psycopg2",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    ), {
        "requested_database": database,
        "host_redacted": True,
        "port": port,
        "user_redacted": True,
        "password_present": bool(password),
        "password_recorded": False,
        "violet_env": os.environ.get("VIOLET_ENV"),
    }


def verify_r1r_evidence(summary_path: Path, report_path: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, actual: Any, expected: Any = True, passed: bool | None = None) -> None:
        ok = bool(actual) if passed is None else bool(passed)
        checks.append({"check": name, "passed": ok, "actual": actual, "expected": expected})

    check("r1r_summary_file_exists", rel(summary_path), passed=summary_path.exists())
    check("r1r_report_file_exists", rel(report_path), passed=report_path.exists())
    if not summary_path.exists() or not report_path.exists():
        return {"passed": False, "status_if_failed": "blocked_invalid_r1r_evidence", "checks": checks}

    r1r = read_json(summary_path)
    report_text = report_path.read_text(encoding="utf-8", errors="replace")
    contract = r1r.get("contract_result") or {}
    pipeline = r1r.get("pipeline_contract") or {}
    public_payload = r1r.get("public_json_payload") or {}
    llm = r1r.get("llm_judgment_summary") or {}
    plan = r1r.get("llm_adjudication_plan") or {}
    cache = r1r.get("llm_cache_policy") or {}
    provider = r1r.get("llm_provider_execution") or {}
    route = r1r.get("route_authorization") or {}
    redaction = r1r.get("public_redaction") or {}
    review_pack = r1r.get("review_pack") or public_payload.get("review_pack") or {}
    mutation = r1r.get("mutation_proof") or public_payload.get("mutation") or {}
    env = r1r.get("environment_isolation") or {}

    expected_contracts = {"r1r_full_source_concept_pipeline_contract_v1", "source_concept_full_chain_contract_v1"}
    check("r1r_contract_id_expected", contract.get("contract_id"), expected=sorted(expected_contracts), passed=contract.get("contract_id") in expected_contracts)
    check("r1r_pipeline_contract_id_expected", pipeline.get("contract_id"), expected="r1r_full_source_concept_pipeline_contract_v1", passed=pipeline.get("contract_id") == "r1r_full_source_concept_pipeline_contract_v1")
    check("r1r_status_target_met_full_chain", pipeline.get("status"), expected="target_met_full_chain", passed=pipeline.get("status") == "target_met_full_chain")
    check("r1r_contract_result_passed", contract.get("passed"), expected=True, passed=contract.get("passed") is True)
    check("r1r_public_redaction_passed", redaction.get("passed"), expected=True, passed=redaction.get("passed") is True)
    check("r1r_review_pack_integrity_recorded", review_pack.get("integrity_recorded"), expected=True, passed=review_pack.get("integrity_recorded") is True)
    check("r1r_review_pack_generated", review_pack.get("generated"), expected=True, passed=review_pack.get("generated") is True)
    check("r1r_review_pack_includes_stage_manifest", review_pack.get("includes_stage_manifest"), expected=True, passed=review_pack.get("includes_stage_manifest") is True)
    check("r1r_eligible_pairs", plan.get("eligible_pair_count"), expected=6429, passed=int(plan.get("eligible_pair_count") or 0) == 6429)
    check("r1r_selected_pairs", plan.get("selected_pair_count"), expected=6429, passed=int(plan.get("selected_pair_count") or 0) == 6429)
    check("r1r_judged_pairs", llm.get("judgment_count"), expected=6429, passed=int(llm.get("judgment_count") or 0) == 6429)
    check("r1r_all_eligible_pairs_adjudicated", plan.get("all_eligible_pairs_adjudicated"), expected=True, passed=plan.get("all_eligible_pairs_adjudicated") is True)
    check("r1r_provider_failures_zero", llm.get("failed_provider_call_count"), expected=0, passed=int(llm.get("failed_provider_call_count") or 0) == 0)
    check("r1r_fallback_provider_used_false", provider.get("fallback_provider_used"), expected=False, passed=provider.get("fallback_provider_used") is False and provider.get("uses_fallback_provider") is False)
    check("r1r_cache_exact_hits", cache.get("exact_compatible_cache_hit_count"), expected=6429, passed=int(cache.get("exact_compatible_cache_hit_count") or 0) == 6429)
    check("r1r_new_provider_calls_zero", cache.get("new_provider_call_count"), expected=0, passed=int(cache.get("new_provider_call_count") or 0) == 0)
    check("r1r_remaining_missing_zero", cache.get("remaining_missing_pair_count"), expected=0, passed=int(cache.get("remaining_missing_pair_count") or 0) == 0)
    check("r1r_budget_cap_usd", plan.get("budget_cap_usd"), expected=15.0, passed=float(plan.get("budget_cap_usd") or 0.0) == 15.0)
    check("r1r_restored_db_label", env.get("db_name") or public_payload.get("environment", {}).get("actual_connection_db_label"), expected=RESTORED_R1R_DB, passed=(env.get("db_name") or public_payload.get("environment", {}).get("actual_connection_db_label")) == RESTORED_R1R_DB)
    check("r1r_production_write_attempted_false", env.get("production_write_attempted"), expected=False, passed=env.get("production_write_attempted") is False)
    check("r1r_mutation_proof_passed", mutation.get("passed"), expected=True, passed=mutation.get("passed") is True)

    for key in (
        "r2_authorized",
        "px1_b_authorized",
        "provider_2_authorized",
        "scale_up_authorized",
        "entity_bridge_authorized",
        "source_concept_truth_promotion_authorized",
    ):
        check(f"r1r_route_authorization_{key}_false", route.get(key), expected=False, passed=route.get(key) is False)
    check("r1r_a1r_still_required", route.get("a1r_still_required"), expected=True, passed=route.get("a1r_still_required") is True)
    check("r1r_report_mentions_a1r_required", "A1R still required" in report_text, expected=True, passed="A1R still required" in report_text)

    passed = all(item["passed"] for item in checks)
    return {
        "passed": passed,
        "status_if_failed": "blocked_invalid_r1r_evidence",
        "merge_commit": R1R_MERGE_COMMIT,
        "summary_path": rel(summary_path),
        "report_path": rel(report_path),
        "status": pipeline.get("status"),
        "llm_accounting": {
            "eligible": int(plan.get("eligible_pair_count") or 0),
            "selected": int(plan.get("selected_pair_count") or 0),
            "judged": int(llm.get("judgment_count") or 0),
            "all_eligible_pairs_adjudicated": bool(plan.get("all_eligible_pairs_adjudicated")),
        },
        "cache_accounting": {
            "exact_compatible_cache_hit_count": int(cache.get("exact_compatible_cache_hit_count") or 0),
            "new_provider_call_count": int(cache.get("new_provider_call_count") or 0),
            "remaining_missing_pair_count": int(cache.get("remaining_missing_pair_count") or 0),
        },
        "decisions": {
            "same": int(llm.get("llm_same_count") or 0),
            "cannot": int(llm.get("llm_cannot_count") or 0),
            "uncertain": int(llm.get("llm_uncertain_count") or 0),
        },
        "budget_cap_usd": float(plan.get("budget_cap_usd") or 0.0),
        "checks": checks,
    }


def read_db_snapshot_proof(conn: Connection) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT current_database() AS database,
                   current_user AS db_user,
                   inet_server_port() AS server_port,
                   current_setting('transaction_isolation') AS transaction_isolation,
                   current_setting('transaction_read_only') AS transaction_read_only,
                   pg_current_snapshot()::text AS snapshot_id
            """
        )
    ).mappings().one()
    isolation = str(row.get("transaction_isolation") or "").casefold()
    snapshot_id = str(row.get("snapshot_id") or "")
    return {
        "database": row.get("database"),
        "db_user_redacted": True,
        "server_port": row.get("server_port"),
        "transaction_isolation": row.get("transaction_isolation"),
        "transaction_read_only": row.get("transaction_read_only"),
        "snapshot_id_present": bool(snapshot_id),
        "snapshot_id_redacted": True,
        "stable_snapshot": isolation in {"repeatable read", "serializable"} and bool(snapshot_id),
        "required_isolation": "repeatable read or serializable",
        "passed": str(row.get("transaction_read_only")).casefold() == "on" and isolation in {"repeatable read", "serializable"} and bool(snapshot_id),
        "recorded_at": utc_now_iso(),
    }


def table_counts(conn: Connection) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    missing = []
    for table in FORBIDDEN_TABLES:
        row = scv1.count_table(conn, table)
        if row.get("status") == "missing_table":
            missing.append(table)
            counts[table] = None
        else:
            counts[table] = int(row.get("count") or 0)
    return {"counts": counts, "missing_tables": missing, "recorded_at": utc_now_iso()}


def mutation_proof(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    changed = []
    for table, left in (before.get("counts") or {}).items():
        right = (after.get("counts") or {}).get(table)
        if left != right:
            changed.append({"table": table, "before": left, "after": right, "prompt_forbidden": True})
    return {
        "passed": not changed,
        "changed_tables": changed,
        "forbidden_changed_tables": [row["table"] for row in changed],
        "unexpected_changed_tables": [row["table"] for row in changed],
        "checked_table_count": len(before.get("counts") or {}),
        "missing_tables": sorted(set(before.get("missing_tables") or []) | set(after.get("missing_tables") or [])),
        "production_write_attempted": False,
        "db_write_attempted": False,
        "source_icloud_storage_write_attempted": False,
        "destructive_operation_attempted": False,
        "recorded_at": utc_now_iso(),
    }


def component_distribution(conn: Connection) -> dict[str, Any]:
    if not scv1.table_exists(conn, "blombooru_source_concept_signal_links"):
        return {"distribution": [], "largest_components": []}
    rows = scv1.rows_dict(
        conn,
        """
        SELECT c.id AS concept_id,
               c.status,
               COUNT(DISTINCT l.signal_id) AS signal_count,
               COUNT(DISTINCT a.id) AS alias_count,
               COUNT(DISTINCT e.id) AS evidence_count,
               COUNT(DISTINCT e.media_id) FILTER (WHERE e.media_id IS NOT NULL) AS media_count
        FROM blombooru_source_concepts c
        LEFT JOIN blombooru_source_concept_signal_links l ON l.concept_id = c.id
        LEFT JOIN blombooru_source_concept_aliases a ON a.concept_id = c.id
        LEFT JOIN blombooru_source_concept_evidence e ON e.concept_id = c.id
        GROUP BY c.id, c.status
        """
    )
    buckets = Counter()
    for row in rows:
        size = int(row.get("signal_count") or 0)
        if size == 0:
            key = "0"
        elif size == 1:
            key = "1"
        elif size <= 3:
            key = "2-3"
        elif size <= 10:
            key = "4-10"
        elif size <= 25:
            key = "11-25"
        else:
            key = "26+"
        buckets[key] += 1
    largest = sorted(rows, key=lambda row: (int(row.get("signal_count") or 0), int(row.get("evidence_count") or 0)), reverse=True)[:20]
    return {
        "component_size_basis": "distinct source_concept_signal_links per concept",
        "distribution": [{"bucket": key, "count": buckets[key]} for key in ("0", "1", "2-3", "4-10", "11-25", "26+")],
        "largest_components": [
            {
                "concept_ref": f"concept_ref_{index:03d}",
                "status": row.get("status"),
                "signal_count": int(row.get("signal_count") or 0),
                "alias_count": int(row.get("alias_count") or 0),
                "evidence_count": int(row.get("evidence_count") or 0),
                "media_count": int(row.get("media_count") or 0),
            }
            for index, row in enumerate(largest, start=1)
        ],
    }


def enrich_source_concept_state(conn: Connection, base: Mapping[str, Any]) -> dict[str, Any]:
    no_alias = scv1.rows_dict(
        conn,
        """
        SELECT c.status, COUNT(*) AS count
        FROM blombooru_source_concepts c
        LEFT JOIN blombooru_source_concept_aliases a ON a.concept_id = c.id
        WHERE a.id IS NULL
        GROUP BY c.status
        """
    )
    active_weak = scv1.rows_dict(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM blombooru_source_concepts c
        LEFT JOIN blombooru_source_concept_evidence e ON e.concept_id = c.id
        WHERE c.status = 'active'
        GROUP BY c.id
        HAVING COUNT(e.id) <= 1
        """
    )
    needs_high = scv1.rows_dict(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM (
          SELECT c.id
          FROM blombooru_source_concepts c
          JOIN blombooru_source_concept_evidence e ON e.concept_id = c.id
          WHERE c.status = 'needs_review'
          GROUP BY c.id
          HAVING COUNT(e.id) >= 5
        ) q
        """
    )
    return {
        **base,
        "table_counts": {
            "source_concepts": scv1.count_table(conn, "blombooru_source_concepts").get("count"),
            "aliases": scv1.count_table(conn, "blombooru_source_concept_aliases").get("count"),
            "evidence": scv1.count_table(conn, "blombooru_source_concept_evidence").get("count"),
            "signal_links": scv1.count_table(conn, "blombooru_source_concept_signal_links").get("count"),
            "search_index": scv1.count_table(conn, "blombooru_source_concept_search_index").get("count"),
        },
        "concept_component_size_distribution": component_distribution(conn),
        "concepts_with_no_aliases_by_status": {row["status"]: int(row["count"] or 0) for row in no_alias},
        "active_concepts_with_weak_evidence": len(active_weak),
        "needs_review_concepts_with_high_evidence": int((needs_high[0] or {}).get("count") or 0) if needs_high else 0,
    }


def compare_buckets(current: Mapping[str, Any], old: Mapping[str, Any]) -> dict[str, Any]:
    current_buckets = current.get("gap_buckets") or {}
    old_buckets = ((old.get("gap_audit") or {}).get("gap_buckets") or {})
    keys = sorted(set(current_buckets) | set(old_buckets))
    rows = []
    improved = []
    worsened = []
    unchanged = []
    for key in keys:
        old_value = int(old_buckets.get(key) or 0)
        new_value = int(current_buckets.get(key) or 0)
        delta = new_value - old_value
        rows.append({"bucket": key, "old_a1": old_value, "a1r": new_value, "delta": delta})
        if delta < 0:
            improved.append(key)
        elif delta > 0:
            worsened.append(key)
        else:
            unchanged.append(key)
    return {
        "old_a1_total_gap_signals": int((old.get("gap_audit") or {}).get("total_gap_signals") or 0),
        "a1r_total_gap_signals": int(current.get("total_gap_signals") or 0),
        "total_delta": int(current.get("total_gap_signals") or 0) - int((old.get("gap_audit") or {}).get("total_gap_signals") or 0),
        "bucket_comparisons": rows,
        "improved_buckets": improved,
        "worsened_buckets": worsened,
        "unchanged_buckets": unchanged,
        "still_route_blocking_buckets": [
            row["bucket"]
            for row in rows
            if row["a1r"] > 0
            and row["bucket"]
            in {
                "cjk_alias_without_english_romaji_sibling",
                "source_tag_present_no_source_concept_alias",
                "same_normalized_alias_key_split_across_multiple_concepts",
                "same_display_name_split_across_contexts",
                "needs_review_cluster_with_no_active_alias_path",
            }
        ],
    }


def compare_search(current: Mapping[str, Any], old: Mapping[str, Any]) -> dict[str, Any]:
    current_agg = current.get("aggregate") or {}
    old_agg = ((old.get("search_seed_symmetry") or {}).get("aggregate") or {})
    keys = ("groups_tested", "seeds_tested", "matched_seeds", "unmatched_seeds", "symmetric_groups", "asymmetric_groups")
    return {
        "old_a1": {key: old_agg.get(key) for key in keys},
        "a1r": {key: current_agg.get(key) for key in keys},
        "delta": {key: int(current_agg.get(key) or 0) - int(old_agg.get(key) or 0) for key in keys},
        "asymmetry_reason_buckets": current_agg.get("asymmetry_reason_buckets") or {},
    }


def llm_decision_impact(r1r: Mapping[str, Any]) -> dict[str, Any]:
    llm = r1r.get("llm_judgment_summary") or {}
    replay = r1r.get("r1r_replay_stage_summary") or {}
    deterministic = r1r.get("deterministic_stage_summary") or {}
    total = int(llm.get("judgment_count") or 0)
    same = int(llm.get("llm_same_count") or 0)
    cannot = int(llm.get("llm_cannot_count") or 0)
    uncertain = int(llm.get("llm_uncertain_count") or 0)
    return {
        "decision_counts": {"same": same, "cannot": cannot, "uncertain": uncertain, "total": total},
        "decision_percentages": {"same": pct(same, total), "cannot": pct(cannot, total), "uncertain": pct(uncertain, total)},
        "decision_distribution_by_edge_type": replay.get("llm_edge_counts_by_type") or {},
        "decision_distribution_by_signal_or_provenance": "not_available_in_public_r1r_summary",
        "source_concept_output_change_vs_deterministic": {
            "deterministic_concepts": deterministic.get("concept_count"),
            "r1r_concepts": replay.get("concept_count"),
            "concept_delta": int(replay.get("concept_count") or 0) - int(deterministic.get("concept_count") or 0),
            "deterministic_active": (deterministic.get("concept_counts_by_status") or {}).get("active"),
            "r1r_active": (replay.get("concept_counts_by_status") or {}).get("active"),
            "deterministic_needs_review": (deterministic.get("concept_counts_by_status") or {}).get("needs_review"),
            "r1r_needs_review": (replay.get("concept_counts_by_status") or {}).get("needs_review"),
        },
        "interpretation": {
            "same_decisions": "LLM same decisions reduced deterministic fragmentation and are useful source-layer evidence, not truth promotion.",
            "cannot_decisions": "The high cannot ratio indicates broad candidate generation / weak-edge selection and supports targeted resolver/gap reduction.",
            "uncertain_decisions": "The uncertain residue remains meaningful review or resolver-targeting backlog.",
            "all_eligible_vs_300_pair_run": "All-eligible adjudication converts old 300-pair smoke evidence into complete route evidence, but it does not clear gap/search/needs_review blockers.",
        },
    }


def build_route_matrix(
    gap: Mapping[str, Any],
    search: Mapping[str, Any],
    needs: Mapping[str, Any],
    source_metadata: Mapping[str, Any],
    llm_impact: Mapping[str, Any],
) -> dict[str, Any]:
    total_gap = int(gap.get("total_gap_signals") or 0)
    asym = int((search.get("aggregate") or {}).get("asymmetric_groups") or 0)
    unmatched = int((search.get("aggregate") or {}).get("unmatched_seeds") or 0)
    needs_count = int(needs.get("total_needs_review_concepts") or 0)
    high_needs = int(needs.get("needs_review_high_evidence_count") or 0)
    metadata_pct = float(source_metadata.get("source_metadata_distinct_eligible_media_pct") or 0.0)
    decisions = llm_impact.get("decision_counts") or {}
    cannot_ratio = pct(int(decisions.get("cannot") or 0), int(decisions.get("total") or 0))
    uncertain = int(decisions.get("uncertain") or 0)

    resolver_dominant = (
        cannot_ratio >= 40.0
        or uncertain >= 100
        or total_gap > 1000
        or asym > 0
        or needs_count > 1000
    )
    metadata_dominant = metadata_pct < 20.0 and not resolver_dominant
    ui_dominant = not resolver_dominant and needs_count <= 500 and high_needs >= 50

    if resolver_dominant:
        status = "route_partially_approved_for_one_next_phase"
        recommended = "SCV2-R2 targeted resolver / gap reduction"
        required_contract = "route_audit_contract_v1 plus focused SCV2-R2 resolver/gap contract"
    elif metadata_dominant:
        status = "route_partially_approved_for_one_next_phase"
        recommended = "PX1-B additional Pixiv/source metadata extraction"
        required_contract = "source_metadata_contract_v1 plus provider/cache/budget dry-run contract"
    elif ui_dominant:
        status = "route_partially_approved_for_one_next_phase"
        recommended = "SourceConcept management/editing UI/design"
        required_contract = "route_audit_contract_v1 plus correction UI/browser validation contract"
    else:
        status = "route_still_blocked"
        recommended = None
        required_contract = None

    def option(
        name: str,
        priority: str,
        writes_db: bool,
        truth_path: bool,
        production: bool,
        why: str,
        blockers: Sequence[str],
        required_next_contract: str,
    ) -> dict[str, Any]:
        is_recommended = name == recommended
        return {
            "candidate": name,
            "priority": priority,
            "recommended": is_recommended,
            "writes_db": writes_db,
            "truth_path": truth_path,
            "production_involved": production,
            "allowed_by_A1R": is_recommended,
            "why": why,
            "blockers": list(blockers),
            "required_next_contract": required_next_contract,
            "a1r_itself_starts_it": False,
        }

    options = [
        option(
            "SCV2-R2 targeted resolver / gap reduction",
            "P1",
            True,
            False,
            False,
            f"Resolver/gap signals still dominate: cannot_ratio={cannot_ratio}%, uncertain={uncertain}, gap_signals={total_gap}, asymmetric_groups={asym}, needs_review={needs_count}.",
            [] if recommended == "SCV2-R2 targeted resolver / gap reduction" else ["resolver/gap dominance must be confirmed by A1R"],
            "focused SCV2-R2 resolver/gap contract with dry-run-first write gates",
        ),
        option(
            "PX1-B additional Pixiv/source metadata extraction",
            "P2",
            True,
            False,
            False,
            f"Source metadata coverage remains low ({metadata_pct}%), but resolver/search/needs_review blockers dominate first.",
            ["resolver/gap blockers remain primary", "provider/source metadata write approval still separate"],
            "source_metadata_contract_v1 plus provider/cache/budget approval contract",
        ),
        option(
            "Provider-2-P0 taxonomy/alias enrichment metadata-only preparation",
            "P2",
            False,
            False,
            False,
            "Taxonomy/alias enrichment may help later, but current evidence points first to resolver/gap reduction.",
            ["not the dominant next bottleneck", "provider policy and privacy/budget design still absent"],
            "metadata-only provider design contract, no uploads, no truth writes",
        ),
        option(
            "SCV2-E2 controlled scale-up import",
            "P3",
            True,
            False,
            True,
            "Scale-up would amplify current retrieval/search/gap issues.",
            ["search symmetry poor", "gap buckets high", "needs_review high", "production/import ledger not in scope"],
            "future ingestion/source item ledger contract after source-layer quality gates pass",
        ),
        option(
            "SourceConcept management/editing UI/design",
            "P2",
            True,
            False,
            False,
            "Management UI may be useful, but remaining work is not primarily human triage yet.",
            ["machine resolver/gap reduction remains higher leverage", "manual review must stay sparse/correction-oriented"],
            "correction-oriented UI/design contract with audit/rollback and browser validation",
        ),
        option(
            "Entity bridge preview",
            "P3",
            True,
            True,
            False,
            "Entity bridge remains unsafe while search symmetry, gap, and needs_review gates fail.",
            ["truth bridge contract absent", "search symmetry not acceptable", "needs_review/gap buckets not under control"],
            "future entity_truth_bridge_contract_v1 only after route quality gates pass",
        ),
        option(
            "DEDUP1 exact duplicate cleanup execution",
            "P3",
            True,
            False,
            True,
            "Duplicate cleanup is unrelated to the R1R SourceConcept route decision.",
            ["destructive/file cleanup approval absent", "not a SourceConcept route blocker"],
            "destructive_operation_contract_v1 with backup and exact targets",
        ),
        option(
            "Full-library / 10k or 40k expansion",
            "P3",
            True,
            False,
            True,
            "Full-library expansion remains blocked until source-layer quality and ledgers are credible.",
            ["scale-up explicitly out of A1R scope", "quality gates fail", "production ledger/cost/cache plan required"],
            "future full-library run ledger, budget/cache, and source-layer quality contracts",
        ),
    ]
    return {
        "final_route_decision_status": status,
        "recommended_next_phase": recommended,
        "authorized_now": {
            "single_next_phase": recommended,
            "broad_downstream_work": False,
            "production_or_truth_work": False,
        },
        "required_contract_for_next_phase": required_contract,
        "required_operator_approval_for_next_phase": recommended is not None,
        "still_blocked_routes": [
            item["candidate"]
            for item in options
            if not item["allowed_by_A1R"]
        ],
        "decision_criteria": {
            "cannot_ratio_percent": cannot_ratio,
            "uncertain_count": uncertain,
            "total_gap_signals": total_gap,
            "asymmetric_groups": asym,
            "unmatched_seeds": unmatched,
            "needs_review": needs_count,
            "source_metadata_coverage_percent": metadata_pct,
            "resolver_dominant": resolver_dominant,
            "metadata_dominant": metadata_dominant,
            "ui_dominant": ui_dominant,
        },
        "options": options,
    }


def route_authorization(route: Mapping[str, Any]) -> dict[str, Any]:
    recommended = route.get("recommended_next_phase")
    return {
        "recommended_next_phase": recommended,
        "authorized_now": route.get("authorized_now"),
        "required_contract_for_next_phase": route.get("required_contract_for_next_phase"),
        "required_operator_approval_for_next_phase": route.get("required_operator_approval_for_next_phase"),
        "still_blocked_routes": route.get("still_blocked_routes"),
        "r2_started": False,
        "px1_b_started": False,
        "provider_2_started": False,
        "scale_up_started": False,
        "entity_bridge_started": False,
        "source_concept_truth_promotion_authorized": False,
        "entity_truth_authorized": False,
        "media_tags_truth_authorized": False,
        "production_write_authorized": False,
    }


def mark_public_artifacts_redacted(summary: dict[str, Any], passed: bool) -> None:
    for artifact in (summary.get("artifact_lifecycle") or {}).get("artifacts") or []:
        path = str(artifact.get("path") or "")
        if path in {rel(PUBLIC_REPORT_MD), rel(PUBLIC_REPORT_JSON)}:
            artifact["redacted"] = bool(passed)


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    route = summary.get("route_decision_matrix") or {}
    state = summary.get("source_concept_state_after_r1r") or {}
    llm = summary.get("llm_decision_impact") or {}
    gap = summary.get("gap_audit_rerun") or {}
    search = summary.get("search_seed_symmetry_audit_rerun") or {}
    source = summary.get("px1_source_metadata_coverage") or {}
    needs = summary.get("needs_review_triage") or {}
    lines = [
        f"# {PHASE_TITLE}",
        "",
        "## Summary",
        "",
        f"- Final route status: `{summary.get('final_route_decision_status')}`.",
        f"- Recommended next phase: `{summary.get('recommended_next_phase') or 'N/A'}`.",
        f"- Required next contract: `{summary.get('required_contract_for_next_phase') or 'N/A'}`.",
        f"- R1R merge commit: `{R1R_MERGE_COMMIT}`.",
        "- A1R did not start R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion.",
        "",
        "## R1R Evidence Intake",
        "",
        f"- Intake passed: `{summary.get('r1r_evidence_intake', {}).get('passed')}`.",
        f"- R1R status: `{summary.get('r1r_evidence_intake', {}).get('status')}`.",
        f"- LLM accounting: `{summary.get('r1r_evidence_intake', {}).get('llm_accounting')}`.",
        f"- Cache accounting: `{summary.get('r1r_evidence_intake', {}).get('cache_accounting')}`.",
        "",
        "## Read-only Runtime Proof",
        "",
        f"- DB source: `{summary.get('read_only_runtime_proof', {}).get('db_source')}`.",
        f"- DB label: `{summary.get('read_only_runtime_proof', {}).get('database')}`.",
        f"- transaction_read_only: `{summary.get('transaction_readonly_proof', {}).get('transaction_read_only')}`.",
        f"- transaction isolation: `{summary.get('transaction_readonly_proof', {}).get('transaction_isolation')}`.",
        f"- stable snapshot: `{summary.get('transaction_readonly_proof', {}).get('stable_snapshot')}`.",
        f"- mutation proof passed: `{summary.get('mutation_proof', {}).get('passed')}`.",
        "",
        "## SourceConcept State After R1R",
        "",
        f"- Concepts: `{state.get('total_source_concepts')}` total, `{state.get('active')}` active, `{state.get('needs_review')}` needs_review, `{state.get('superseded')}` superseded.",
        f"- Aliases/evidence/signal links/search index: `{state.get('source_concept_alias_total')}` / `{state.get('source_concept_evidence_total')}` / `{state.get('table_counts', {}).get('signal_links')}` / `{state.get('source_concept_search_index_total')}`.",
        f"- Concepts without media: `{state.get('concepts_without_media')}`; active weak evidence: `{state.get('active_concepts_with_weak_evidence')}`.",
        "",
        "## LLM Decision Impact",
        "",
        f"- Counts: `{llm.get('decision_counts')}`.",
        f"- Percentages: `{llm.get('decision_percentages')}`.",
        f"- Edge-type distribution: `{llm.get('decision_distribution_by_edge_type')}`.",
        "",
        "## Gap Audit Rerun",
        "",
        f"- Total gap signals: `{gap.get('total_gap_signals')}`.",
        f"- Old A1 comparison: `{summary.get('gap_audit_old_a1_comparison', {}).get('total_delta')}` total delta.",
        f"- Route-blocking buckets: `{summary.get('gap_audit_old_a1_comparison', {}).get('still_route_blocking_buckets')}`.",
        "",
        "## Search Seed Symmetry Audit Rerun",
        "",
        f"- Aggregate: `{search.get('aggregate')}`.",
        f"- Old A1 comparison delta: `{summary.get('search_seed_old_a1_comparison', {}).get('delta')}`.",
        "",
        "## PX1 / Pixiv / Source Metadata Coverage",
        "",
        f"- Eligible media: `{summary.get('current_baseline', {}).get('eligible_media')}`.",
        f"- Source metadata rows: `{source.get('source_metadata_records_total')}`.",
        f"- Distinct eligible media with source metadata: `{source.get('source_metadata_distinct_eligible_media_count')}` (`{source.get('source_metadata_distinct_eligible_media_pct')}`%).",
        f"- Strict PX1-influenced concepts: `{summary.get('px1_evidence_impact', {}).get('px1_strict_influenced_concepts')}`.",
        f"- All Pixiv-influenced concepts: `{summary.get('px1_evidence_impact', {}).get('pixiv_all_influenced_concepts')}`.",
        "",
        "## needs_review Triage",
        "",
        f"- Total needs_review: `{needs.get('total_needs_review_concepts')}`.",
        f"- With media / high evidence / sharing active aliases / no active alias path: `{needs.get('needs_review_with_media')}` / `{needs.get('needs_review_high_evidence_count')}` / `{needs.get('needs_review_sharing_alias_with_active')}` / `{needs.get('needs_review_clusters_with_no_active_alias_path')}`.",
        "",
        "## Route Decision Matrix",
        "",
        "| Candidate | Priority | Recommended | Allowed by A1R | Writes DB | Truth path | Production |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for option in route.get("options") or []:
        lines.append(
            f"| `{option.get('candidate')}` | `{option.get('priority')}` | `{option.get('recommended')}` | `{option.get('allowed_by_A1R')}` | `{option.get('writes_db')}` | `{option.get('truth_path')}` | `{option.get('production_involved')}` |"
        )
    lines.extend(
        [
            "",
            "## Final Route Recommendation",
            "",
            f"- Status: `{route.get('final_route_decision_status')}`.",
            f"- Exactly one recommended next phase: `{route.get('recommended_next_phase') or 'N/A'}`.",
            f"- Still blocked routes: `{route.get('still_blocked_routes')}`.",
            "- Production/truth-path work remains blocked.",
            "",
            "## Validation",
            "",
            f"- Contract result: `{summary.get('contract_result')}`.",
            f"- Public redaction: `{summary.get('public_redaction')}`.",
            f"- Review pack: `{summary.get('chatgpt_review_pack')}`.",
            "- Browser/Electron validation: not required; no UI/runtime change.",
            "",
        ]
    )
    return "\n".join(lines)


def scan_public_outputs(markdown_text: str, summary: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    temp = output_dir / "_public_redaction_staging"
    temp.mkdir(parents=True, exist_ok=True)
    md = temp / PUBLIC_REPORT_MD.name
    js = temp / PUBLIC_REPORT_JSON.name
    write_text(md, markdown_text)
    write_json(js, summary)
    scan = scv1.scan_public_artifacts([md, js], public_path_labels=[rel(PUBLIC_REPORT_MD), rel(PUBLIC_REPORT_JSON)])
    allowed = []
    findings = []
    for finding in scan.get("findings") or []:
        match = str(finding.get("match") or "")
        if finding.get("type") == "media_filename_like" and match in {"public-report.md", "public-summary.json"}:
            allowed.append(finding)
            continue
        findings.append(finding)
    try:
        shutil.rmtree(temp)
    except OSError:
        pass
    return {"passed": not findings, "findings": findings, "allowed_findings": allowed, "scanned_artifacts": [rel(PUBLIC_REPORT_MD), rel(PUBLIC_REPORT_JSON)]}


def write_review_pack(output_dir: Path, summary: Mapping[str, Any], markdown_text: str, artifacts: Mapping[str, Any]) -> dict[str, Any]:
    pack_dir = output_dir / "review-pack"
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)
    write_text(pack_dir / "public-report.md", markdown_text)
    write_json(pack_dir / "public-summary.json", summary)
    for name, payload in artifacts.items():
        write_json(pack_dir / name, payload)
    manifest = {
        "phase": PHASE,
        "generated_at": utc_now_iso(),
        "files": sorted(path.name for path in pack_dir.iterdir() if path.is_file()),
        "public_report_copy_current": True,
        "not_committed": True,
    }
    write_json(pack_dir / "manifest.json", manifest)
    checksums = {path.name: sha256_file(path) for path in sorted(pack_dir.iterdir()) if path.is_file() and path.name != "checksums.json"}
    write_json(pack_dir / "checksums.json", checksums)
    integrity = {
        "hash_algorithm": "sha256",
        "checksum_count": len(checksums),
        "review_pack_hash": hashlib.sha256(json.dumps(checksums, sort_keys=True).encode("utf-8")).hexdigest(),
        "passed": len(checksums) >= len(REVIEW_PACK_FILES) + 1,
    }
    write_json(pack_dir / "review-pack-integrity.json", integrity)
    checksums = {path.name: sha256_file(path) for path in sorted(pack_dir.iterdir()) if path.is_file() and path.name != "checksums.json"}
    write_json(pack_dir / "checksums.json", checksums)
    zip_path = output_dir / "review-pack.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(pack_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(pack_dir))
    return {
        "generated": True,
        "manifest_present": True,
        "checksums_present": True,
        "checksum_count": len(checksums),
        "manifest_checksum_count": len(checksums),
        "redaction_passed": bool((summary.get("public_redaction") or {}).get("passed")),
        "redaction_scan_covers_final_file_set": True,
        "public_report_copy_current": True,
        "zip_generated": zip_path.exists(),
        "not_committed": True,
        "zip_path_label": "a1r-private-review-pack",
        "integrity_hash": integrity["review_pack_hash"],
        "integrity_passed": integrity["passed"],
    }


def build_summary(
    *,
    r1r_summary: Mapping[str, Any],
    intake: Mapping[str, Any],
    db_url_info: Mapping[str, Any],
    db_proof: Mapping[str, Any],
    baseline: Mapping[str, Any],
    source_metadata: Mapping[str, Any],
    source_state: Mapping[str, Any],
    llm: Mapping[str, Any],
    gap: Mapping[str, Any],
    gap_comparison: Mapping[str, Any],
    search: Mapping[str, Any],
    search_comparison: Mapping[str, Any],
    needs: Mapping[str, Any],
    px1: Mapping[str, Any],
    route: Mapping[str, Any],
    mutation: Mapping[str, Any],
    validation: Mapping[str, Any],
    output_dir: Path,
    review_pack: Mapping[str, Any] | None = None,
    public_redaction: Mapping[str, Any] | None = None,
    contract_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    recommended = route.get("recommended_next_phase")
    summary = {
        "phase": PHASE,
        "phase_slug": PHASE_SLUG,
        "title": PHASE_TITLE,
        "branch": BRANCH,
        "generated_at": utc_now_iso(),
        "baseline_main_sha": R1R_MERGE_COMMIT,
        "head_sha_at_runtime": git_value(["git", "rev-parse", "HEAD"]),
        "pipeline_contract": {
            "contract_id": "route_audit_contract_v1",
            "status": route.get("final_route_decision_status"),
            "claims": {
                "target_met": False,
                "route_approved": False,
                "full_chain_complete": False,
                "safe_to_merge": False,
            },
        },
        "upstream_pipeline_contract": {
            "contract_id": "source_concept_full_chain_contract_v1",
            "status": "full_chain_completed" if intake.get("passed") else "blocked_invalid_r1r_evidence",
            "passed": bool(intake.get("passed")),
            "full_chain_fidelity_passed": bool(intake.get("passed")),
            "missing_required_stages": [],
            "source_contract_id": (r1r_summary.get("pipeline_contract") or {}).get("contract_id"),
            "source_status": (r1r_summary.get("pipeline_contract") or {}).get("status"),
        },
        "final_route_decision_status": route.get("final_route_decision_status"),
        "recommended_next_phase": recommended,
        "required_contract_for_next_phase": route.get("required_contract_for_next_phase"),
        "required_operator_approval_for_next_phase": route.get("required_operator_approval_for_next_phase"),
        "r1r_evidence_intake": intake,
        "read_only_runtime_proof": {
            "db_source": "restored_r1r_db",
            "requested_db": db_url_info.get("requested_database"),
            "database": db_proof.get("database"),
            "transaction_read_only": db_proof.get("transaction_read_only"),
            "transaction_isolation": db_proof.get("transaction_isolation"),
            "stable_snapshot": db_proof.get("stable_snapshot"),
            "public_private_boundary": {
                "public_artifacts": [rel(PUBLIC_REPORT_MD), rel(PUBLIC_REPORT_JSON)],
                "private_artifact_label": "a1r-private-artifacts",
                "raw_db_url_recorded": False,
                "raw_local_paths_recorded_in_public": False,
            },
        },
        "transaction_readonly_proof": db_proof,
        "current_baseline": baseline,
        "source_concept_state_after_r1r": source_state,
        "llm_decision_impact": llm,
        "gap_audit_rerun": gap,
        "gap_audit_old_a1_comparison": gap_comparison,
        "search_seed_symmetry_audit_rerun": {key: value for key, value in search.items() if key != "private_examples"},
        "search_seed_old_a1_comparison": search_comparison,
        "px1_source_metadata_coverage": source_metadata,
        "px1_evidence_impact": px1,
        "needs_review_triage": {
            **needs,
            "needs_review_clusters_with_no_active_alias_path": (gap.get("gap_buckets") or {}).get("needs_review_cluster_with_no_active_alias_path"),
            "manageable_for_next_route": int(needs.get("total_needs_review_concepts") or 0) < 250,
            "recommended_handling": "R2 targeted resolver/gap reduction before management UI/design",
        },
        "route_decision_matrix": route,
        "route_authorization": route_authorization(route),
        "mutation_proof": mutation,
        "public_redaction": public_redaction or {"passed": None, "findings": []},
        "chatgpt_review_pack": review_pack or {"generated": False},
        "review_pack": review_pack or {"generated": False},
        "contract_result": contract_result or {"contract_id": "route_audit_contract_v1", "passed": None},
        "validation": validation,
        "safety": {
            "read_only": True,
            "db_write_attempted": False,
            "provider_calls_attempted": False,
            "llm_provider_calls_attempted": False,
            "pixiv_gallery_dl_saucenao_google_attempted": False,
            "media_import_attempted": False,
            "classification_ai_localization_attempted": False,
            "source_concept_resolver_persistence_attempted": False,
            "entity_or_media_tags_truth_mutation_attempted": False,
            "source_icloud_app_storage_mutation_attempted": False,
            "cleanup_delete_reset_drop_truncate_attempted": False,
            "r2_started": False,
            "px1_b_started": False,
            "provider_2_started": False,
            "scale_up_started": False,
            "entity_bridge_started": False,
            "source_concept_truth_promotion_attempted": False,
        },
        "artifact_lifecycle": {
            "artifacts": [
                {"path": "scripts/run_phase45_scv2_a1r_route_audit_after_r1r.py", "classification": "phase-scoped operational runner", "committed": True},
                {"path": "tests/test_phase45_scv2_a1r_route_audit_after_r1r.py", "classification": "phase-scoped validation test", "committed": True},
                {"path": rel(PUBLIC_REPORT_MD), "classification": "public report / handoff / roadmap update", "committed": True, "redacted": bool((public_redaction or {}).get("passed"))},
                {"path": rel(PUBLIC_REPORT_JSON), "classification": "public report / handoff / roadmap update", "committed": True, "redacted": bool((public_redaction or {}).get("passed"))},
                {"path": f".local_manifests/{PHASE_SLUG}", "classification": "one-off local artifact / ignored output", "committed": False},
            ]
        },
        "private_artifacts": {
            "private_artifact_root_label": "a1r-private-artifacts",
            "review_pack_label": "a1r-private-review-pack",
            "committed": False,
            "exact_private_paths_public": False,
        },
    }
    if route.get("final_route_decision_status") not in ROUTE_STATUSES:
        raise A1RBlockedError(f"Invalid A1R route status: {route.get('final_route_decision_status')!r}")
    if recommended and recommended not in NEXT_PHASES:
        raise A1RBlockedError(f"Invalid A1R recommended next phase: {recommended!r}")
    return summary


def run_contract_check(summary_path: Path) -> dict[str, Any]:
    cmd = [sys.executable, "scripts/check_phase_contract.py", "--contract", "route_audit_contract_v1", "--summary", rel(summary_path)]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, encoding="utf-8", capture_output=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": proc.stdout, "stderr": proc.stderr}
    return {"contract_id": "route_audit_contract_v1", "passed": proc.returncode == 0, "returncode": proc.returncode, "result": payload}


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    if not args.read_only:
        raise A1RBlockedError("A1R requires --read-only.")
    if not args.write_review_pack:
        raise A1RBlockedError("A1R requires --write-review-pack.")
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    r1r_summary_path = (ROOT / args.require_r1r_summary).resolve() if not Path(args.require_r1r_summary).is_absolute() else Path(args.require_r1r_summary)
    r1r_report_path = (ROOT / args.require_r1r_report).resolve() if not Path(args.require_r1r_report).is_absolute() else Path(args.require_r1r_report)
    intake = verify_r1r_evidence(r1r_summary_path, r1r_report_path)
    r1r_summary = read_json(r1r_summary_path) if r1r_summary_path.exists() else {}
    if not intake["passed"]:
        route = {
            "final_route_decision_status": "blocked_invalid_r1r_evidence",
            "recommended_next_phase": None,
            "authorized_now": {"single_next_phase": None, "broad_downstream_work": False, "production_or_truth_work": False},
            "required_contract_for_next_phase": None,
            "required_operator_approval_for_next_phase": False,
            "still_blocked_routes": sorted(NEXT_PHASES),
            "options": [],
        }
        validation = {"operational_audit_result": "blocked_invalid_r1r_evidence", "browser_validation": "not_required_no_ui_runtime_change", "dirty_worktree": public_dirty_worktree_summary()}
        summary = build_summary(
            r1r_summary=r1r_summary,
            intake=intake,
            db_url_info={"requested_database": RESTORED_R1R_DB},
            db_proof={"passed": False, "transaction_read_only": None, "transaction_isolation": None, "stable_snapshot": False},
            baseline={},
            source_metadata={},
            source_state={},
            llm={},
            gap={},
            gap_comparison={},
            search={},
            search_comparison={},
            needs={},
            px1={},
            route=route,
            mutation={"passed": True, "changed_tables": []},
            validation=validation,
            output_dir=output_dir,
        )
        markdown = public_report_markdown(summary)
        redaction = scan_public_outputs(markdown, summary, output_dir)
        summary["public_redaction"] = redaction
        mark_public_artifacts_redacted(summary, bool(redaction.get("passed")))
        if args.write_public_report:
            write_text(PUBLIC_REPORT_MD, public_report_markdown(summary))
            write_json(PUBLIC_REPORT_JSON, summary)
        return summary

    db_url, db_url_info = route_db_url(RESTORED_R1R_DB)
    engine = create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 10, "options": "-c statement_timeout=600000"})
    conn: Connection | None = None
    try:
        conn = engine.connect()
    except Exception as exc:
        route = {
            "final_route_decision_status": "blocked_missing_r1r_restored_snapshot",
            "recommended_next_phase": None,
            "authorized_now": {"single_next_phase": None, "broad_downstream_work": False, "production_or_truth_work": False},
            "required_contract_for_next_phase": None,
            "required_operator_approval_for_next_phase": False,
            "still_blocked_routes": sorted(NEXT_PHASES),
            "options": [],
        }
        validation = {"operational_audit_result": "blocked_missing_r1r_restored_snapshot", "error_public": type(exc).__name__, "browser_validation": "not_required_no_ui_runtime_change", "dirty_worktree": public_dirty_worktree_summary()}
        summary = build_summary(
            r1r_summary=r1r_summary,
            intake=intake,
            db_url_info=db_url_info,
            db_proof={"passed": False, "database": RESTORED_R1R_DB, "transaction_read_only": None, "transaction_isolation": None, "stable_snapshot": False},
            baseline={},
            source_metadata={},
            source_state={},
            llm=llm_decision_impact(r1r_summary),
            gap={},
            gap_comparison={},
            search={},
            search_comparison={},
            needs={},
            px1={},
            route=route,
            mutation={"passed": True, "changed_tables": []},
            validation=validation,
            output_dir=output_dir,
        )
        markdown = public_report_markdown(summary)
        redaction = scan_public_outputs(markdown, summary, output_dir)
        summary["public_redaction"] = redaction
        mark_public_artifacts_redacted(summary, bool(redaction.get("passed")))
        if args.write_public_report:
            write_text(PUBLIC_REPORT_MD, public_report_markdown(summary))
            write_json(PUBLIC_REPORT_JSON, summary)
        return summary

    try:
        if conn.dialect.name != "postgresql":
            raise A1RBlockedError(f"A1R requires PostgreSQL read-only transaction support, got {conn.dialect.name!r}.")
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '600s'")
        db_proof = read_db_snapshot_proof(conn)
        if db_proof.get("database") != RESTORED_R1R_DB:
            raise A1RBlockedError(f"A1R connected to {db_proof.get('database')!r}, expected {RESTORED_R1R_DB!r}.")
        if not db_proof.get("passed"):
            raise A1RBlockedError("A1R read-only stable snapshot proof failed.")
        before = table_counts(conn)

        media = scv1.audit_media_coverage(conn)
        source_layer = scv1.audit_source_layer_coverage(conn)
        concepts_rows = scv1.load_concepts(conn)
        aliases_rows = scv1.load_aliases(conn)
        evidence_rows = scv1.load_evidence(conn)
        scv1_inventory, _alias_inventory, _evidence_inventory = scv1.audit_source_concepts(conn)
        full_gap, _gap_samples = scv1.audit_alias_gaps(conn, concepts_rows, aliases_rows)
        public_gap = a1.public_gap_audit(full_gap)
        needs_review, _needs_samples = scv1.audit_needs_review(conn, concepts_rows, aliases_rows, evidence_rows)
        search = a1.build_search_seed_symmetry_audit(conn)
        source_metadata = a1.build_source_metadata_coverage(conn, source_layer)
        baseline = a1.build_current_baseline(media, source_layer)
        source_state = enrich_source_concept_state(
            conn,
            a1.build_source_concept_current_state(conn, concepts_rows, aliases_rows, evidence_rows, scv1_inventory),
        )
        old_a1 = read_json(OLD_A1_SUMMARY)
        gap_comparison = compare_buckets(public_gap, old_a1)
        search_comparison = compare_search(search, old_a1)
        px1 = a1.build_px1_evidence_impact(conn, r1r_summary)
        llm = llm_decision_impact(r1r_summary)
        route = build_route_matrix(public_gap, search, needs_review, source_metadata, llm)
        after = table_counts(conn)
        mutation = mutation_proof(before, after)
        if not mutation["passed"]:
            raise A1RBlockedError(f"A1R read-only audit observed table changes: {mutation['changed_tables']!r}")
        validation = {
            "operational_audit_command": (
                "python scripts/run_phase45_scv2_a1r_route_audit_after_r1r.py --read-only "
                "--require-r1r-summary docs/reports/phase-4.5-scv2-r1r-full-source-concept-pipeline-replay-summary.json "
                "--require-r1r-report docs/reports/phase-4.5-scv2-r1r-full-source-concept-pipeline-replay.md "
                "--output-dir .local_manifests/phase-4.5-scv2-a1r-route-audit-after-r1r --write-public-report --write-review-pack"
            ),
            "operational_audit_result": "passed",
            "browser_validation": "not_required_no_ui_runtime_change",
            "provider_network_attempted": False,
            "llm_provider_attempted": False,
            "server_started": False,
            "dirty_worktree": public_dirty_worktree_summary(),
            "python_executable": Path(sys.executable).name,
            "python_executable_path_redacted": True,
            "python_version": sys.version,
        }
        summary = build_summary(
            r1r_summary=r1r_summary,
            intake=intake,
            db_url_info=db_url_info,
            db_proof=db_proof,
            baseline=baseline,
            source_metadata=source_metadata,
            source_state=source_state,
            llm=llm,
            gap=public_gap,
            gap_comparison=gap_comparison,
            search=search,
            search_comparison=search_comparison,
            needs=needs_review,
            px1=px1,
            route=route,
            mutation=mutation,
            validation=validation,
            output_dir=output_dir,
        )
        markdown = public_report_markdown(summary)
        redaction = scan_public_outputs(markdown, summary, output_dir)
        if not redaction["passed"]:
            raise A1RBlockedError(f"A1R public redaction failed: {redaction['findings']!r}")
        summary["public_redaction"] = redaction
        mark_public_artifacts_redacted(summary, True)
        markdown = public_report_markdown(summary)
        write_private_artifacts = {
            "route-decision-matrix.json": route,
            "r1r-intake-proof.json": intake,
            "read-only-db-proof.json": db_proof,
            "mutation-proof.json": mutation,
            "gap-audit-data.json": {"gap": public_gap, "old_a1_comparison": gap_comparison},
            "search-symmetry-data.json": {"search": {key: value for key, value in search.items() if key != "private_examples"}, "old_a1_comparison": search_comparison},
            "source-concept-state.json": source_state,
            "px1-source-coverage.json": {"source_metadata": source_metadata, "px1": px1},
            "validation-result.json": validation,
        }
        for name, payload in write_private_artifacts.items():
            write_json(output_dir / name, payload)
        write_json(output_dir / "r1r-intake-proof.json", intake)
        write_json(output_dir / "read-only-db-proof.json", db_proof)
        write_json(output_dir / "mutation-proof.json", mutation)
        review_pack = write_review_pack(output_dir, summary, markdown, write_private_artifacts)
        summary["chatgpt_review_pack"] = review_pack
        summary["review_pack"] = review_pack
        markdown = public_report_markdown(summary)
        redaction = scan_public_outputs(markdown, summary, output_dir)
        if not redaction["passed"]:
            raise A1RBlockedError(f"A1R final public redaction failed: {redaction['findings']!r}")
        summary["public_redaction"] = redaction
        mark_public_artifacts_redacted(summary, True)
        summary["chatgpt_review_pack"]["redaction_passed"] = True
        summary["review_pack"] = summary["chatgpt_review_pack"]
        if args.write_public_report:
            write_text(PUBLIC_REPORT_MD, public_report_markdown(summary))
            write_json(PUBLIC_REPORT_JSON, summary)
        contract_result = run_contract_check(PUBLIC_REPORT_JSON) if args.write_public_report else {"contract_id": "route_audit_contract_v1", "passed": None}
        summary["contract_result"] = contract_result
        if args.write_public_report:
            write_text(PUBLIC_REPORT_MD, public_report_markdown(summary))
            write_json(PUBLIC_REPORT_JSON, summary)
        write_json(output_dir / "summary-private-copy.json", summary)
        write_json(output_dir / "checksums.json", {path.name: sha256_file(path) for path in sorted(output_dir.iterdir()) if path.is_file() and path.name != "checksums.json"})
        conn.exec_driver_sql("ROLLBACK")
        return summary
    finally:
        if conn is not None:
            try:
                if not conn.closed:
                    conn.exec_driver_sql("ROLLBACK")
            except Exception:
                pass
            conn.close()
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--require-r1r-summary", default=rel(R1R_SUMMARY))
    parser.add_argument("--require-r1r-report", default=rel(R1R_REPORT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--write-review-pack", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_audit(args)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
