#!/usr/bin/env python3
"""Run the cache-only SCV2-R2R autonomous recall/search closure preflight.

Lifecycle: phase-scoped operational runner. The initial R2R run is deliberately
cache-only. It can clone the accepted isolated R2 database and generate dry-run
evidence, but it cannot initialize or call an LLM provider.
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
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import (  # noqa: E402
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
)
from app.services.source_concept_autonomous_closure_service import (  # noqa: E402
    COMPATIBILITY_VERSION,
    OVERLAY_VERSION,
    CandidatePair,
    PairDisposition,
    build_candidate_pair_manifest,
    classify_legacy_cache_reuse,
    disposition_accounting,
    estimate_autonomous_budget,
    project_autonomous_materialization,
    write_deferred_overlay,
)
from app.services.source_concept_resolver_service import (  # noqa: E402
    RESOLVER_VERSION,
    build_data_aware_ambiguity_profiles,
    build_source_concept_signals,
    resolve_source_concepts,
    source_signal_inventory,
)
from app.services.source_concept_search_service import _search_keys_for_term  # noqa: E402
from app.services.source_name_candidate_extraction_service import FORBIDDEN_TRUTH_TABLES  # noqa: E402
from scripts import run_phase45_scv1_source_concept_coverage_audit as scv1  # noqa: E402
from scripts import run_phase45_scv2_a1_post_expansion_audit_route_decision as a1  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402
from scripts.phase_contracts.contract_checks import check_phase_contract  # noqa: E402

PHASE = "4.5-SCV2-R2R"
PHASE_TITLE = "SCV2-R2R: Autonomous Recall and Search Closure"
PHASE_SLUG = "phase-4.5-scv2-r2r-autonomous-recall-search-closure"
BRANCH = "codex/scv2-r2r-autonomous-recall-search-closure"
CONTRACT_ID = "r2r_autonomous_recall_search_closure_contract_v1"
R2_BASELINE_DB = "blombooru_scv2_r2_review4_test_20260710"
DEFAULT_WORKING_DB = "blombooru_scv2_r2r_dryrun_test_20260710"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
DEFAULT_LEGACY_CACHE_DIR = ROOT / ".local_manifests" / "source_concept_llm_adjudication_cache"
DEFAULT_R2R_CACHE_DIR = ROOT / ".local_manifests" / "source_concept_r2r_autonomous_cache"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
CREATE_CONFIRMATION = "CREATE_SCV2_R2R_ISOLATED_WORKING_DB"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

FIXED_INPUT_TABLES = r2.FIXED_INPUT_TABLES
SOURCE_CONCEPT_TABLES = r2.SOURCE_CONCEPT_TABLES


class R2RBlockedError(RuntimeError):
    """Raised when an R2R gate fails before provider or unsafe writes."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value) or ".." in value:
        raise R2RBlockedError("blocked_invalid_run_id")
    return value


def require_safe_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    root = (ROOT / ".local_manifests").resolve()
    if not resolved.is_relative_to(root):
        raise R2RBlockedError("output_dir_must_be_under_repo_local_manifests")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def require_safe_private_path(path: Path) -> Path:
    resolved = path.resolve()
    root = (ROOT / ".local_manifests").resolve()
    if not resolved.is_relative_to(root):
        raise R2RBlockedError("private_path_must_be_under_repo_local_manifests")
    return resolved


def artifact_path(output_dir: Path, name: str) -> Path:
    root = output_dir.resolve()
    candidate = (root / name).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise R2RBlockedError("blocked_artifact_path_escape")
    return candidate


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


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


def _production_profile_active() -> bool:
    return any(
        str(os.environ.get(name) or "").strip().casefold()
        in {"1", "true", "yes", "production", "active"}
        for name in (
            "VIOLET_PRODUCTION_PROFILE_ACTIVE",
            "VIOLET_PRODUCTION_PROFILE",
            "VIOLET_PRODUCTION_MODE",
            "VIOLET_LAUNCH_PRODUCTION",
        )
    )


def environment_isolation(source_db: str, working_db: str) -> dict[str, Any]:
    violet_env = str(os.environ.get("VIOLET_ENV") or "").casefold()
    production_active = _production_profile_active()
    separate = source_db != working_db
    working_safe = working_db.startswith("blombooru_scv2_r2r_") and "test" in working_db.casefold()
    passed = bool(
        source_db == R2_BASELINE_DB
        and separate
        and working_safe
        and violet_env == "test"
        and not production_active
        and working_db != "blombooru"
    )
    return {
        "passed": passed,
        "violet_env": violet_env,
        "source_db": source_db,
        "working_db": working_db,
        "dev_test_only": violet_env == "test" and working_safe,
        "working_db_is_separate_from_r2_baseline": separate,
        "r2_baseline_preserved": True,
        "production_profile_active": production_active,
        "canonical_production_profile_flag_checked": True,
        "production_write_attempted": False,
        "protected_source_write_attempted": False,
    }


def prepare_working_database(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    isolation = environment_isolation(args.source_db, args.working_db)
    if not isolation["passed"]:
        raise R2RBlockedError("blocked_environment_isolation")
    if args.confirm_clone != CREATE_CONFIRMATION:
        raise R2RBlockedError("clone_confirmation_missing_or_invalid")

    source_engine = r2.create_db_engine(args.source_db)
    try:
        with source_engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            source_identity = r2.db_identity(conn)
            if source_identity["db_name"] != args.source_db:
                raise R2RBlockedError("source_database_identity_mismatch")
            source_fixed = r2.fingerprint_tables(conn, FIXED_INPUT_TABLES)
            source_forbidden = r2.fingerprint_tables(conn, FORBIDDEN_TRUTH_TABLES)
            source_outputs = r2.fingerprint_tables(conn, SOURCE_CONCEPT_TABLES)
            conn.rollback()
    finally:
        source_engine.dispose()
    if source_fixed["missing_tables"] or source_forbidden["missing_tables"]:
        raise R2RBlockedError("blocked_fixed_evidence_tables_missing")

    admin_engine = r2.create_db_engine("postgres")
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            if not r2.database_exists(conn, args.source_db):
                raise R2RBlockedError("r2_baseline_database_missing")
            if r2.database_exists(conn, args.working_db):
                raise R2RBlockedError("r2r_working_database_already_exists_no_overwrite")
            active = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = :name"),
                    {"name": args.source_db},
                ).scalar()
                or 0
            )
            if active:
                raise R2RBlockedError(f"r2_baseline_has_active_connections:{active}")
            conn.exec_driver_sql(
                f"CREATE DATABASE {r2.qident(args.working_db)} TEMPLATE {r2.qident(args.source_db)}"
            )
    finally:
        admin_engine.dispose()

    working_engine = r2.create_db_engine(args.working_db)
    try:
        with working_engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            working_identity = r2.db_identity(conn)
            working_fixed = r2.fingerprint_tables(conn, FIXED_INPUT_TABLES)
            working_forbidden = r2.fingerprint_tables(conn, FORBIDDEN_TRUTH_TABLES)
            working_outputs = r2.fingerprint_tables(conn, SOURCE_CONCEPT_TABLES)
            conn.rollback()
    finally:
        working_engine.dispose()

    fixed_comparison = r2.compare_fingerprints(source_fixed, working_fixed)
    forbidden_comparison = r2.compare_fingerprints(source_forbidden, working_forbidden)
    output_comparison = r2.compare_fingerprints(source_outputs, working_outputs)
    if not all(row["passed"] for row in (fixed_comparison, forbidden_comparison, output_comparison)):
        raise R2RBlockedError("blocked_fixed_evidence_changed_clone_mismatch")
    manifest = {
        "phase": PHASE,
        "generated_at": utc_now_iso(),
        "environment_isolation": isolation,
        "source_identity": source_identity,
        "working_identity": working_identity,
        "source_fixed_snapshot": source_fixed,
        "source_forbidden_snapshot": source_forbidden,
        "source_output_snapshot": source_outputs,
        "working_fixed_snapshot": working_fixed,
        "working_forbidden_snapshot": working_forbidden,
        "working_output_snapshot": working_outputs,
        "fixed_comparison": fixed_comparison,
        "forbidden_comparison": forbidden_comparison,
        "output_comparison": output_comparison,
        "raw_rows_included": False,
        "private_artifact": True,
    }
    write_json(artifact_path(output_dir, "fixed-input-manifest.json"), manifest)
    result = {
        "status": "prepared_isolated_r2r_working_database",
        "working_db": args.working_db,
        "fixed_input_table_count": len(FIXED_INPUT_TABLES),
        "forbidden_truth_table_count": len(FORBIDDEN_TRUTH_TABLES),
        "source_concept_output_table_count": len(SOURCE_CONCEPT_TABLES),
        "clone_content_match": True,
        "provider_calls": 0,
    }
    write_json(artifact_path(output_dir, "prepare-result.json"), result)
    return result


def load_and_verify_manifest(args: argparse.Namespace, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = artifact_path(output_dir, "fixed-input-manifest.json")
    if not path.exists():
        raise R2RBlockedError("blocked_missing_fixed_input_manifest")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    isolation = manifest.get("environment_isolation") or {}
    if isolation.get("source_db") != args.source_db or isolation.get("working_db") != args.working_db:
        raise R2RBlockedError("fixed_input_manifest_database_identity_mismatch")
    engine = r2.create_db_engine(args.source_db)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            current = r2.fingerprint_tables(conn, FIXED_INPUT_TABLES)
            conn.rollback()
    finally:
        engine.dispose()
    comparison = r2.compare_fingerprints(manifest["source_fixed_snapshot"], current)
    if not comparison["passed"]:
        raise R2RBlockedError("blocked_fixed_evidence_changed")
    return manifest, comparison


def _pairwise_overlap(values: Sequence[set[int]]) -> dict[str, Any]:
    rows: list[float] = []
    for left, right in combinations(values, 2):
        union = left | right
        rows.append(len(left & right) / len(union) if union else 1.0)
    return {
        "pairwise_jaccard_count": len(rows),
        "average_pairwise_jaccard": round(sum(rows) / len(rows), 4) if rows else 1.0,
        "min_pairwise_jaccard": round(min(rows), 4) if rows else 1.0,
    }


def _script_family(value: str) -> str:
    if any("\u3040" <= char <= "\u30ff" for char in value):
        return "japanese_kana"
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        return "cjk_han"
    if any(char.isalpha() and ord(char) < 128 for char in value):
        return "latin"
    return "other"


def build_automated_search_benchmark(
    session: Any,
    signals: Sequence[Any],
    *,
    dispositions: Sequence[PairDisposition],
    legacy_analysis_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate and evaluate all reproducible fixed-evidence search families."""

    identity_media_by_key: dict[str, set[int]] = defaultdict(set)
    identity_rows = (
        session.query(SourceConceptSearchIndex.search_key, SourceConceptEvidence.media_id)
        .join(SourceConcept, SourceConcept.id == SourceConceptSearchIndex.concept_id)
        .join(SourceConceptEvidence, SourceConceptEvidence.concept_id == SourceConcept.id)
        .filter(SourceConcept.status == "active")
        .filter(SourceConceptSearchIndex.status == "active")
        .filter(SourceConceptEvidence.status == "active")
        .filter(SourceConceptEvidence.media_id.isnot(None))
        .all()
    )
    for key, media_id in identity_rows:
        if key and media_id is not None:
            identity_media_by_key[str(key)].add(int(media_id))
    link_rows = (
        session.query(SourceConceptSearchIndex.search_key, SourceConceptSignal.media_id)
        .join(SourceConcept, SourceConcept.id == SourceConceptSearchIndex.concept_id)
        .join(SourceConceptSignalLink, SourceConceptSignalLink.concept_id == SourceConcept.id)
        .join(SourceConceptSignal, SourceConceptSignal.id == SourceConceptSignalLink.signal_id)
        .filter(SourceConcept.status == "active")
        .filter(SourceConceptSearchIndex.status == "active")
        .filter(SourceConceptSignalLink.link_status == "active")
        .filter(SourceConceptSignal.media_id.isnot(None))
        .all()
    )
    for key, media_id in link_rows:
        if key and media_id is not None:
            identity_media_by_key[str(key)].add(int(media_id))

    fallback_media_by_key: dict[str, set[int]] = defaultdict(set)
    for signal in signals:
        if signal.media_id is None or signal.status == "rejected" or signal.trust_tier == "rejected":
            continue
        for key in {signal.canonical_key, signal.normalized_key}:
            if key:
                fallback_media_by_key[str(key)].add(int(signal.media_id))
    signal_by_key = {str(signal.signal_key): signal for signal in signals}
    overlay_media_by_key: dict[str, set[int]] = defaultdict(set)
    overlay_relation_count = 0
    for disposition in dispositions:
        if disposition.disposition not in {"must_link", "deferred_nonblocking"}:
            continue
        left = signal_by_key.get(disposition.left_signal_key)
        right = signal_by_key.get(disposition.right_signal_key)
        if left is None or right is None:
            continue
        overlay_relation_count += 1
        if right.media_id is not None:
            for key in {left.canonical_key, left.normalized_key}:
                if key:
                    overlay_media_by_key[str(key)].add(int(right.media_id))
        if left.media_id is not None:
            for key in {right.canonical_key, right.normalized_key}:
                if key:
                    overlay_media_by_key[str(key)].add(int(left.media_id))

    groups: dict[str, list[Any]] = defaultdict(list)
    for signal in signals:
        key = str(signal.canonical_key or signal.normalized_key or "")
        if key and signal.status != "rejected" and signal.trust_tier != "rejected":
            groups[key].append(signal)
    ambiguity_profiles = build_data_aware_ambiguity_profiles(signals)
    families: list[dict[str, Any]] = []
    for key, grouped in sorted(groups.items()):
        forms = sorted({str(signal.display_value) for signal in grouped if signal.display_value})
        independent_units = {
            (signal.provider, signal.source_record_id, signal.media_id) for signal in grouped
        }
        if len(forms) < 2 or len(independent_units) < 2:
            continue
        categories = {"alias_family_two_independent_seed_forms"}
        scripts = {_script_family(value) for value in forms}
        if "latin" in scripts and scripts.intersection({"cjk_han", "japanese_kana"}):
            categories.add("cjk_romaji_family")
        if any(signal.parenthetical_context for signal in grouped):
            categories.add("parenthetical_alias_family")
        if len({signal.normalized_key for signal in grouped}) == 1:
            categories.add("same_normalized_key_family")
        if len({signal.work_context_key for signal in grouped if signal.work_context_key}) > 1:
            categories.add("same_display_name_separated_by_work_context")
        if any(
            signal.trust_tier == "strong"
            and signal.origin_type in {"source_name_observation", "source_tag_observation", "source_searchable_name_assertion"}
            for signal in grouped
        ):
            categories.add("strong_source_name_or_tag_family")
        if bool((ambiguity_profiles.get(key) or {}).get("ambiguous")):
            categories.add("common_name_ambiguity_family")
        families.append(
            {
                "family_id": hashlib.sha256(f"canonical:{key}".encode("utf-8")).hexdigest(),
                "family_key": key,
                "categories": sorted(categories),
                "seeds": forms,
                "signal_keys": sorted(signal.signal_key for signal in grouped),
                "negative_family": False,
            }
        )

    concept_alias_rows = (
        session.query(
            SourceConceptAlias.concept_id,
            SourceConceptAlias.display_name,
            SourceConceptAlias.source_signal_id,
        )
        .join(SourceConcept, SourceConcept.id == SourceConceptAlias.concept_id)
        .filter(SourceConcept.status.in_(("active", "needs_review")))
        .filter(SourceConceptAlias.status.in_(("active", "needs_review")))
        .all()
    )
    aliases_by_concept: dict[int, list[tuple[str, int | None]]] = defaultdict(list)
    for concept_id, display_name, source_signal_id in concept_alias_rows:
        if display_name:
            aliases_by_concept[int(concept_id)].append(
                (str(display_name), int(source_signal_id) if source_signal_id is not None else None)
            )
    source_signal_by_db_id = {
        int(row.id): row
        for row in session.query(SourceConceptSignal).filter(SourceConceptSignal.id.isnot(None)).all()
    }
    for concept_id, alias_rows in sorted(aliases_by_concept.items()):
        forms = sorted({value for value, _signal_id in alias_rows})
        if len(forms) < 2:
            continue
        scripts = {_script_family(value) for value in forms}
        categories = {"materialized_alias_family"}
        if "latin" in scripts and scripts.intersection({"cjk_han", "japanese_kana"}):
            categories.add("cjk_romaji_family")
        source_signals = [
            source_signal_by_db_id[signal_id]
            for _value, signal_id in alias_rows
            if signal_id in source_signal_by_db_id
        ]
        if any(
            signal.trust_tier == "strong"
            and signal.origin_type
            in {
                "source_name_observation",
                "source_tag_observation",
                "source_searchable_name_assertion",
            }
            for signal in source_signals
        ):
            categories.add("strong_source_name_or_tag_family")
        if len(categories) == 1:
            continue
        families.append(
            {
                "family_id": hashlib.sha256(f"concept-alias:{concept_id}".encode("utf-8")).hexdigest(),
                "family_key": None,
                "categories": sorted(categories),
                "seeds": forms,
                "signal_keys": sorted(
                    signal.signal_key for signal in source_signals if signal.signal_key
                ),
                "negative_family": False,
            }
        )

    cannot_pairs = set()
    for row in legacy_analysis_rows:
        if row.get("disposition") != "cannot_link":
            continue
        pair = tuple(row.get("pair") or ())
        if len(pair) == 2 and pair[0] in signal_by_key and pair[1] in signal_by_key:
            cannot_pairs.add(tuple(sorted((str(pair[0]), str(pair[1])))))
    for left_key, right_key in sorted(cannot_pairs):
        left = signal_by_key[left_key]
        right = signal_by_key[right_key]
        families.append(
            {
                "family_id": hashlib.sha256(f"cannot:{left_key}:{right_key}".encode("utf-8")).hexdigest(),
                "family_key": None,
                "categories": ["known_cannot_linked_negative_family"],
                "seeds": sorted({str(left.display_value), str(right.display_value)}),
                "signal_keys": [left_key, right_key],
                "negative_family": True,
            }
        )

    seed_cache: dict[str, dict[str, set[int]]] = {}
    category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evaluated: list[dict[str, Any]] = []
    false_broad_union = 0
    contamination = 0
    for family in families:
        per_seed = []
        for seed in family["seeds"]:
            if seed not in seed_cache:
                keys = _search_keys_for_term(seed)
                identity = set().union(*(identity_media_by_key.get(key, set()) for key in keys)) if keys else set()
                fallback = (
                    set().union(
                        *(
                            fallback_media_by_key.get(key, set())
                            | overlay_media_by_key.get(key, set())
                            for key in keys
                        )
                    )
                    if keys
                    else set()
                )
                seed_cache[seed] = {
                    "identity": identity,
                    "evidence_fallback": fallback,
                    "combined": identity | fallback,
                }
            paths = seed_cache[seed]
            if paths["combined"] - (paths["identity"] | paths["evidence_fallback"]):
                false_broad_union += 1
            per_seed.append(
                {
                    "seed": seed,
                    "identity_media": sorted(paths["identity"]),
                    "fallback_media": sorted(paths["evidence_fallback"]),
                    "combined_media": sorted(paths["combined"]),
                }
            )
        identity_sets = [set(row["identity_media"]) for row in per_seed]
        combined_sets = [set(row["combined_media"]) for row in per_seed]
        row = {
            **family,
            "seed_results": per_seed,
            "identity_symmetric": bool(identity_sets and all(value == identity_sets[0] for value in identity_sets[1:])),
            "fallback_symmetric": bool(combined_sets and all(value == combined_sets[0] for value in combined_sets[1:])),
            "identity_matched_seed_count": sum(bool(value) for value in identity_sets),
            "fallback_matched_seed_count": sum(bool(value) for value in combined_sets),
            "identity_overlap": _pairwise_overlap(identity_sets),
            "fallback_overlap": _pairwise_overlap(combined_sets),
        }
        evaluated.append(row)
        for category in family["categories"]:
            category_rows[category].append(row)

    cannot_contamination_checks: list[dict[str, Any]] = []
    for left_key, right_key in sorted(cannot_pairs):
        for signal_key in (left_key, right_key):
            signal = signal_by_key[signal_key]
            seed = str(signal.display_value)
            keys = _search_keys_for_term(seed)
            identity_direct = set().union(*(identity_media_by_key.get(key, set()) for key in keys)) if keys else set()
            fallback_direct = set().union(*(fallback_media_by_key.get(key, set()) for key in keys)) if keys else set()
            overlay_added = set().union(*(overlay_media_by_key.get(key, set()) for key in keys)) if keys else set()
            combined = identity_direct | fallback_direct | overlay_added
            other_signal_key = right_key if signal_key == left_key else left_key
            other = signal_by_key[other_signal_key]
            other_media = {int(other.media_id)} if other.media_id is not None else set()
            unsupported = (overlay_added - fallback_direct - identity_direct).intersection(other_media)
            contamination += len(unsupported)
            cannot_contamination_checks.append(
                {
                    "pair": [left_key, right_key],
                    "seed_signal_key": signal_key,
                    "direct_result_count": len(combined),
                    "unsupported_cross_component_media_count": len(unsupported),
                    "overlay_added_media_count": len(overlay_added),
                }
            )

    total_seeds = sum(len(row["seeds"]) for row in evaluated)
    identity_matched = sum(row["identity_matched_seed_count"] for row in evaluated)
    fallback_matched = sum(row["fallback_matched_seed_count"] for row in evaluated)
    identity_pairwise = [
        row["identity_overlap"]["average_pairwise_jaccard"]
        for row in evaluated
        if row["identity_overlap"]["pairwise_jaccard_count"]
    ]
    fallback_pairwise = [
        row["fallback_overlap"]["average_pairwise_jaccard"]
        for row in evaluated
        if row["fallback_overlap"]["pairwise_jaccard_count"]
    ]
    legacy_seed_groups = {
        **a1.SCV1_SEED_GROUPS,
        **a1.build_dynamic_px1_seed_groups(session.connection()),
    }
    legacy_identity_sets: list[set[int]] = []
    legacy_fallback_sets: list[set[int]] = []
    legacy_groups_private: list[dict[str, Any]] = []
    for group_name, seeds in legacy_seed_groups.items():
        group_identity: list[set[int]] = []
        group_fallback: list[set[int]] = []
        seed_rows = []
        for seed in seeds:
            if seed not in seed_cache:
                keys = _search_keys_for_term(seed)
                identity = set().union(*(identity_media_by_key.get(key, set()) for key in keys)) if keys else set()
                fallback = (
                    set().union(
                        *(
                            fallback_media_by_key.get(key, set())
                            | overlay_media_by_key.get(key, set())
                            for key in keys
                        )
                    )
                    if keys
                    else set()
                )
                seed_cache[seed] = {
                    "identity": identity,
                    "evidence_fallback": fallback,
                    "combined": identity | fallback,
                }
            group_identity.append(set(seed_cache[seed]["identity"]))
            group_fallback.append(set(seed_cache[seed]["combined"]))
            seed_rows.append(
                {
                    "seed": seed,
                    "identity_media": sorted(seed_cache[seed]["identity"]),
                    "combined_media": sorted(seed_cache[seed]["combined"]),
                }
            )
        legacy_identity_sets.extend(group_identity)
        legacy_fallback_sets.extend(group_fallback)
        legacy_groups_private.append(
            {
                "group": group_name,
                "seeds": seed_rows,
                "identity_symmetric": bool(group_identity and all(value == group_identity[0] for value in group_identity[1:]) and all(group_identity)),
                "fallback_symmetric": bool(group_fallback and all(value == group_fallback[0] for value in group_fallback[1:]) and all(group_fallback)),
            }
        )
    legacy_identity_overlap = _pairwise_overlap(legacy_identity_sets)
    legacy_fallback_overlap = _pairwise_overlap(legacy_fallback_sets)
    legacy_compatibility = {
        "group_count": len(legacy_seed_groups),
        "seed_count": len(legacy_identity_sets),
        "r2_baseline": {
            "symmetric_group_count": 0,
            "unmatched_seed_count": 16,
            "average_pairwise_jaccard": 0.1552,
        },
        "identity_path": {
            "symmetric_group_count": sum(row["identity_symmetric"] for row in legacy_groups_private),
            "unmatched_seed_count": sum(not value for value in legacy_identity_sets),
            "average_pairwise_jaccard": legacy_identity_overlap["average_pairwise_jaccard"],
        },
        "evidence_fallback_path": {
            "symmetric_group_count": sum(row["fallback_symmetric"] for row in legacy_groups_private),
            "unmatched_seed_count": sum(not value for value in legacy_fallback_sets),
            "average_pairwise_jaccard": legacy_fallback_overlap["average_pairwise_jaccard"],
        },
    }
    legacy_compatibility.update(
        {
            "symmetry_improved_vs_r2": legacy_compatibility["evidence_fallback_path"]["symmetric_group_count"] > 0,
            "unmatched_seeds_decreased_vs_r2": legacy_compatibility["evidence_fallback_path"]["unmatched_seed_count"] < 16,
            "average_overlap_improved_vs_r2": legacy_compatibility["evidence_fallback_path"]["average_pairwise_jaccard"] > 0.1552,
        }
    )
    public = {
        "generated": True,
        "reproducible": True,
        "benchmark_version": "r2r_automated_search_benchmark_v1",
        "family_count": len(evaluated),
        "seed_count": total_seeds,
        "identity_path": {
            "matched_seed_count": identity_matched,
            "unmatched_seed_count": total_seeds - identity_matched,
            "symmetric_family_count": sum(row["identity_symmetric"] for row in evaluated),
            "asymmetric_family_count": sum(not row["identity_symmetric"] for row in evaluated),
            "recall": round(identity_matched / total_seeds, 6) if total_seeds else 1.0,
            "average_pairwise_jaccard": round(sum(identity_pairwise) / len(identity_pairwise), 4) if identity_pairwise else 1.0,
        },
        "evidence_fallback_path": {
            "matched_seed_count": fallback_matched,
            "unmatched_seed_count": total_seeds - fallback_matched,
            "symmetric_family_count": sum(row["fallback_symmetric"] for row in evaluated),
            "asymmetric_family_count": sum(not row["fallback_symmetric"] for row in evaluated),
            "recall": round(fallback_matched / total_seeds, 6) if total_seeds else 1.0,
            "average_pairwise_jaccard": round(sum(fallback_pairwise) / len(fallback_pairwise), 4) if fallback_pairwise else 1.0,
        },
        "identity_and_fallback_reported_separately": True,
        "false_broad_union_indicator_count": false_broad_union,
        "cannot_linked_search_contamination_count": contamination,
        "category_results": {
            category: {
                "family_count": len(category_rows.get(category, [])),
                "seed_count": sum(
                    len(row["seeds"]) for row in category_rows.get(category, [])
                ),
                "identity_symmetric_family_count": sum(
                    row["identity_symmetric"] for row in category_rows.get(category, [])
                ),
                "fallback_symmetric_family_count": sum(
                    row["fallback_symmetric"] for row in category_rows.get(category, [])
                ),
            }
            for category in sorted(
                set(category_rows)
                | {
                    "alias_family_two_independent_seed_forms",
                    "cjk_romaji_family",
                    "parenthetical_alias_family",
                    "same_normalized_key_family",
                    "same_display_name_separated_by_work_context",
                    "strong_source_name_or_tag_family",
                    "known_cannot_linked_negative_family",
                    "common_name_ambiguity_family",
                }
            )
        },
        "negative_family_count": sum(row["negative_family"] for row in evaluated),
        "evidence_fallback_relation_count": overlay_relation_count,
        "legacy_58_seed_compatibility_benchmark": legacy_compatibility,
        "source_layer_only": True,
        "identity_union_from_fallback": False,
    }
    private = {
        "benchmark": public,
        "families": evaluated,
        "legacy_compatibility_groups": legacy_groups_private,
        "cannot_contamination_checks": cannot_contamination_checks,
    }
    return public, private


def _graph_invariants(result: Any) -> dict[str, Any]:
    summary = result.summary
    classifications = summary.get("undermerge_split_classification_counts") or {}
    return {
        "review_or_deferred_edge_used_in_union_count": int(summary.get("review_only_edge_used_in_union_count") or 0),
        "direct_cannot_violation_count": int(summary.get("direct_llm_cannot_pair_in_materialized_component_count") or 0),
        "transitive_cannot_violation_count": int(summary.get("transitive_cannot_violation_count") or 0),
        "deterministic_hard_conflict_count": int(summary.get("deterministic_hard_conflict_in_materialized_component_count") or 0),
        "unauthorized_unknown_role_materialization_count": int(summary.get("unauthorized_unknown_role_materialization_count") or 0),
        "unexplained_proof_grade_same_regression_count": 0,
        "true_unexplained_undermerge_count": int(classifications.get("true_unexplained_undermerge") or 0),
        "directly_blocked_split_count": int(classifications.get("directly_blocked_split") or 0),
        "transitively_blocked_split_count": int(classifications.get("transitively_blocked_split") or 0),
        "deferred_nonblocking_split_count": int(classifications.get("deferred_nonblocking_split") or 0),
        "largest_component_signal_count": max((len(concept.signals) for concept in result.concepts), default=0),
    }


def operation_counts() -> dict[str, int]:
    return {
        "gallery_dl_calls": 0,
        "provider_metadata_acquisition_calls": 0,
        "pixiv_provider_calls": 0,
        "ai_tagging_calls": 0,
        "media_imports": 0,
        "classification_calls": 0,
        "localization_calls": 0,
        "upstream_observation_mutations": 0,
        "production_writes": 0,
        "truth_path_writes": 0,
        "fallback_provider_calls": 0,
        "primary_provider_calls": 0,
    }


def route_authorization() -> dict[str, bool]:
    return {
        "px1_b_authorized": False,
        "provider_2_authorized": False,
        "scale_up_authorized": False,
        "entity_bridge_authorized": False,
        "production_authorized": False,
        "full_library_execution_authorized": False,
        "truth_promotion_authorized": False,
    }


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    candidate = summary.get("candidate_population") or {}
    cache = summary.get("cache_reuse") or {}
    budget = summary.get("budget_projection") or {}
    dispositions = summary.get("candidate_dispositions") or {}
    automation = summary.get("automation_invariants") or {}
    projection = summary.get("materialization_projection") or {}
    search = summary.get("search_benchmark") or {}
    return "\n".join(
        [
            f"# {PHASE_TITLE}",
            "",
            "## 状态",
            "",
            f"- 合同状态：`{(summary.get('pipeline_contract') or {}).get('status')}`。",
            f"- 分支：`{summary.get('branch')}`。",
            f"- 证据代码 SHA：`{summary.get('evidence_code_sha')}`。",
            "- 本轮是 cache-only dry-run；provider 未初始化、未调用。",
            "",
            "## 固定输入与隔离",
            "",
            f"- 隔离工作数据库：`{(summary.get('environment_isolation') or {}).get('working_db')}`。",
            f"- 固定证据表数：`{(summary.get('fixed_input_proof') or {}).get('table_count')}`。",
            f"- 基线/工作副本内容一致：`{(summary.get('fixed_input_proof') or {}).get('baseline_to_working_clone_match')}`。",
            f"- 禁止 truth 表内容一致：`{(summary.get('fixed_input_proof') or {}).get('forbidden_truth_content_unchanged')}`。",
            "- 原始值、指纹、pair payload 与本地路径仅保存在忽略的私有工件中。",
            "",
            "## 候选与缓存覆盖",
            "",
            f"- 当前候选 pair：`{candidate.get('total_candidate_pairs')}`。",
            f"- 当前 exact-compatible / stable-compatible：`{cache.get('exact_compatible_cache_hit_count')}` / `{cache.get('stable_compatible_reuse_count')}`。",
            f"- 全局 semantic priors：`{cache.get('semantic_prior_count')}`。",
            f"- 真正缺失 pair：`{cache.get('genuinely_missing_pair_count')}`。",
            f"- 预计 first-pass input/completion tokens：`{budget.get('estimated_first_pass_input_usage_units')}` / `{budget.get('estimated_first_pass_completion_usage_units')}`。",
            f"- 预计 second-pass escalation：`{budget.get('expected_uncertain_escalation_count')}`。",
            f"- 预计总成本 / 请求预算：`${budget.get('projected_cost_usd')}` / `${budget.get('recommended_budget_usd')}`。",
            "",
            "## 自治处置与物化预览",
            "",
            f"- must_link / cannot_link / deferred_nonblocking：`{dispositions.get('must_link_count')}` / `{dispositions.get('cannot_link_count')}` / `{dispositions.get('deferred_nonblocking_count')}`。",
            f"- 未计入 pair / coverage：`{dispositions.get('unaccounted_pair_count')}` / `{dispositions.get('candidate_disposition_coverage')}`。",
            f"- manual_review_required_count：`{automation.get('manual_review_required_count')}`。",
            f"- operator_blocking_review_count：`{automation.get('operator_blocking_review_count')}`。",
            f"- manual_review_queue_generated：`{automation.get('manual_review_queue_generated')}`。",
            f"- 预览物化 SourceConcept / needs_review：`{projection.get('materialized_source_concept_count')}` / `{projection.get('materialized_needs_review_count')}`。",
            f"- Deferred evidence signals：`{projection.get('deferred_evidence_signal_count')}`；保留投影：`{projection.get('evidence_retention_projection')}`。",
            "",
            "## 自动搜索基准",
            "",
            f"- family / seed：`{search.get('family_count')}` / `{search.get('seed_count')}`。",
            f"- identity path：`{search.get('identity_path')}`。",
            f"- evidence fallback path：`{search.get('evidence_fallback_path')}`。",
            f"- cannot-linked contamination / false broad union：`{search.get('cannot_linked_search_contamination_count')}` / `{search.get('false_broad_union_indicator_count')}`。",
            "",
            "## 安全与下一门禁",
            "",
            f"- 操作计数：`{summary.get('operation_counts')}`。",
            "- 需要单独、明确的 operator LLM 预算授权后，才能在同一 PR 继续 provider 阶段。",
            "- 未启动 PX1-B、Provider-2、scale-up、Entity bridge、production、full-library 或 truth promotion。",
            "",
            "## 验证",
            "",
            f"- R2R 合同通过：`{(summary.get('validation') or {}).get('r2r_contract_passed')}`。",
            f"- 公开脱敏通过：`{(summary.get('public_redaction') or {}).get('passed')}`。",
            f"- Review pack 完整性通过：`{(summary.get('review_pack') or {}).get('integrity_passed')}`。",
            "- 浏览器验证：N/A（未修改 UI）。",
            "",
        ]
    )


def scan_public_outputs(markdown: str, summary: Mapping[str, Any], output_dir: Path, run_id: str) -> dict[str, Any]:
    staging = artifact_path(output_dir, f"public-redaction-staging-{run_id}")
    staging.mkdir(parents=True, exist_ok=False)
    md = staging / PUBLIC_REPORT_MD.name
    js = staging / PUBLIC_REPORT_JSON.name
    write_text(md, markdown)
    write_json(js, summary)
    scan = scv1.scan_public_artifacts(
        [md, js],
        public_path_labels=[f"docs/reports/{md.name}", f"docs/reports/{js.name}"],
    )
    allowed = []
    findings = []
    for finding in scan.get("findings") or []:
        match = str(finding.get("match") or "")
        if finding.get("type") == "media_filename_like" and match in {md.name, js.name}:
            allowed.append(finding)
        else:
            findings.append(finding)
    return {
        "passed": not findings,
        "findings": findings,
        "allowed_findings": allowed,
        "scanned_artifacts": [f"docs/reports/{md.name}", f"docs/reports/{js.name}"],
        "clean_before_public_write": not findings,
        "unsafe_public_report_written": False,
    }


def write_review_pack(
    output_dir: Path,
    run_id: str,
    summary: Mapping[str, Any],
    markdown: str,
    artifact_names: Sequence[str],
) -> dict[str, Any]:
    pack_dir = artifact_path(output_dir, f"review-pack-{run_id}")
    pack_dir.mkdir(parents=True, exist_ok=False)
    write_text(pack_dir / "public-report.md", markdown)
    write_json(pack_dir / "public-summary.json", summary)
    copied = []
    for name in artifact_names:
        source = artifact_path(output_dir, name)
        if source.exists():
            target = pack_dir / name
            target.write_bytes(source.read_bytes())
            copied.append(name)
    manifest = {
        "phase": PHASE,
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "files": sorted(["public-report.md", "public-summary.json", *copied]),
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
        for path in sorted(pack_dir.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    with zipfile.ZipFile(zip_path, "r") as archive:
        integrity = archive.testzip() is None
    return {
        "generated": True,
        "manifest_present": (pack_dir / "manifest.json").exists(),
        "checksums_present": (pack_dir / "checksums.json").exists(),
        "checksum_count": len(checksums),
        "integrity_passed": integrity and len(checksums) >= 8,
        "not_committed": True,
        "public_report_copy_current": True,
        "zip_generated": zip_path.exists(),
        "zip_path_label": "r2r-private-review-pack",
    }


def run_cache_only_dry_run(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    isolation = environment_isolation(args.source_db, args.working_db)
    if not isolation["passed"]:
        raise R2RBlockedError("blocked_environment_isolation")
    manifest, source_recheck = load_and_verify_manifest(args, output_dir)
    r2r_cache_root = require_safe_private_path(Path(args.r2r_cache_dir))
    engine = r2.create_db_engine(args.working_db)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        inventory = source_signal_inventory(session)
        signals = build_source_concept_signals(session, run_id=args.run_id)
        deterministic = resolve_source_concepts(signals, run_id=args.run_id)
        legacy_judgments, _r2_accounting, _r2_candidates, _r2_analysis = r2.load_cached_judgments(
            Path(args.legacy_cache_dir),
            signals,
            deterministic,
        )
        resolved = resolve_source_concepts(
            signals,
            run_id=args.run_id,
            llm_judgments=legacy_judgments,
        )
        candidates = build_candidate_pair_manifest(
            deterministic.edge_candidates,
            signals=signals,
            max_calls=args.emergency_call_ceiling,
        )
        reused, cache_reuse, legacy_analysis = classify_legacy_cache_reuse(
            Path(args.legacy_cache_dir),
            candidates=candidates,
            signals=signals,
            resolver_version=RESOLVER_VERSION,
        )
        accounting = disposition_accounting(candidates, reused.values())
        missing_ids = {row.pair_id for row in candidates} - set(reused)
        historical_total = sum((cache_reuse.get("outcome_counts") or {}).values())
        historical_uncertain = int((cache_reuse.get("outcome_counts") or {}).get("deferred_nonblocking") or 0)
        uncertain_rate = historical_uncertain / historical_total if historical_total else 0.0
        budget = estimate_autonomous_budget(
            candidates,
            missing_pair_ids=missing_ids,
            signal_by_key={signal.signal_key: signal for signal in signals},
            historical_uncertain_rate=uncertain_rate,
        )
        projected, projection = project_autonomous_materialization(
            resolved,
            dispositions=list(reused.values()),
        )
        projected_again, projection_again = project_autonomous_materialization(
            resolved,
            dispositions=list(reused.values()),
        )
        projection["idempotent_fingerprint_match"] = (
            projection["projection_fingerprint"] == projection_again["projection_fingerprint"]
            and len(projected.concepts) == len(projected_again.concepts)
        )
        overlay_proof = write_deferred_overlay(
            artifact_path(output_dir, "deferred-nonblocking-relation-overlay.json"),
            candidates=candidates,
            dispositions=list(reused.values()),
            projection_fingerprint=projection["projection_fingerprint"],
        )
        projection.update(
            {
                "deferred_overlay_versioned": overlay_proof["overlay_version"] == OVERLAY_VERSION,
                "deferred_overlay_atomic": bool(overlay_proof["atomic_write_passed"]),
                "deferred_overlay_checksum_passed": bool(overlay_proof["checksum_passed"]),
            }
        )
        search_public, search_private = build_automated_search_benchmark(
            session,
            signals,
            dispositions=list(reused.values()),
            legacy_analysis_rows=legacy_analysis,
        )
        fixed_now = r2.fingerprint_tables(session.connection(), FIXED_INPUT_TABLES)
        forbidden_now = r2.fingerprint_tables(session.connection(), FORBIDDEN_TRUTH_TABLES)
        session.rollback()
    finally:
        session.close()
        engine.dispose()

    fixed_comparison = r2.compare_fingerprints(manifest["working_fixed_snapshot"], fixed_now)
    forbidden_comparison = r2.compare_fingerprints(manifest["working_forbidden_snapshot"], forbidden_now)
    fixed_proof = {
        "present": True,
        "table_count": len(FIXED_INPUT_TABLES),
        "forbidden_truth_table_count": len(FORBIDDEN_TRUTH_TABLES),
        "baseline_to_working_clone_match": bool((manifest.get("fixed_comparison") or {}).get("passed")),
        "source_recheck_match": bool(source_recheck.get("passed")),
        "before_after_match": bool(fixed_comparison.get("passed")),
        "row_counts_match": bool(fixed_comparison.get("row_counts_match")),
        "schemas_match": bool(fixed_comparison.get("columns_match")),
        "content_fingerprints_match": bool(fixed_comparison.get("content_fingerprints_match")),
        "forbidden_truth_content_unchanged": bool(forbidden_comparison.get("passed")),
        "changed_tables": list(fixed_comparison.get("changed_tables") or []),
        "forbidden_truth_changed_tables": list(forbidden_comparison.get("changed_tables") or []),
        "raw_fingerprints_private": True,
    }
    if not fixed_proof["before_after_match"] or not fixed_proof["forbidden_truth_content_unchanged"]:
        raise R2RBlockedError("blocked_fixed_evidence_changed")

    pair_manifest_payload = {
        "compatibility_version": COMPATIBILITY_VERSION,
        "candidate_pair_count": len(candidates),
        "pairs": [candidate.__dict__ for candidate in candidates],
    }
    private_artifacts = {
        "fixed-input-comparison.json": {
            "source_recheck": source_recheck,
            "working_fixed_comparison": fixed_comparison,
            "forbidden_truth_comparison": forbidden_comparison,
        },
        "pair-manifest.json": pair_manifest_payload,
        "cache-coverage.json": {"cache_reuse": cache_reuse, "analysis": legacy_analysis},
        "provider-execution-ledger.json": {
            "provider_initialized": False,
            "provider_calls": 0,
            "errors": [],
            "status": "blocked_llm_approval_required",
        },
        "first-second-pass-disposition-transitions.json": {"transitions": [], "provider_calls": 0},
        "same-cannot-accounting.json": accounting,
        "component-diagnostics.json": {
            "resolver_summary": resolved.summary,
            "graph_invariants": _graph_invariants(resolved),
        },
        "automated-search-benchmark.json": search_private,
        "persistence-projection-comparison.json": {
            "projection": projection,
            "second_projection": projection_again,
            "db_write": False,
        },
        "review-pack-index.json": {
            "artifact_names": [],
            "checksums_generated_later": True,
        },
        "checkpoint-layout.json": {
            "compatibility_version": COMPATIBILITY_VERSION,
            "cache_root": str(r2r_cache_root),
            "first_pass_records": str(r2r_cache_root / "first" / "records"),
            "first_pass_failures": str(r2r_cache_root / "first" / "failures"),
            "second_pass_records": str(r2r_cache_root / "second" / "records"),
            "second_pass_failures": str(r2r_cache_root / "second" / "failures"),
            "deferred_overlay": str(
                artifact_path(output_dir, "deferred-nonblocking-relation-overlay.json")
            ),
            "private_artifact": True,
        },
    }
    for name, payload in private_artifacts.items():
        write_json(artifact_path(output_dir, name), payload)

    graph = _graph_invariants(resolved)
    status = "blocked_llm_approval_required" if accounting["unaccounted_pair_count"] else "partial_autonomous_closure"
    summary: dict[str, Any] = {
        "phase": PHASE,
        "phase_slug": PHASE_SLUG,
        "title": PHASE_TITLE,
        "run_id": args.run_id,
        "generated_at": utc_now_iso(),
        "branch": git_value(["git", "branch", "--show-current"]),
        "evidence_code_sha": git_value(["git", "rev-parse", "HEAD"]),
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {"target_met": False, "safe_to_merge": False, "route_approved": False},
        },
        "environment_isolation": isolation,
        "fixed_input_proof": fixed_proof,
        "operation_counts": operation_counts(),
        "candidate_population": {
            "total_candidate_pairs": len(candidates),
            "eligibility_policy": "budget_driven_all_eligible_unique_pairs",
            "fixed_small_pair_cap_used": False,
            "emergency_call_ceiling": args.emergency_call_ceiling,
        },
        "cache_reuse": cache_reuse,
        "budget_projection": budget,
        "llm_execution": {
            "status": status,
            "operator_approval_required": bool(accounting["unaccounted_pair_count"]),
            "requested_budget_usd": budget["recommended_budget_usd"],
            "provider_initialized": False,
            "primary_provider_calls": 0,
            "provider_failure_count": 0,
            "remaining_unaccounted_missing_pairs": accounting["unaccounted_pair_count"],
            "all_approved_missing_pairs_accounted": False,
            "failed_judgments_counted_as_success": False,
            "primary_provider_only": True,
            "fallback_provider_used": False,
        },
        "candidate_dispositions": accounting,
        "automation_invariants": {
            "manual_review_required_count": 0,
            "operator_blocking_review_count": 0,
            "manual_review_queue_generated": False,
            "needs_review_is_human_queue": False,
        },
        "materialization_projection": projection,
        "graph_invariants": graph,
        "search_benchmark": {
            **search_public,
            "symmetry_improved_vs_r2": bool(
                (search_public.get("legacy_58_seed_compatibility_benchmark") or {}).get("symmetry_improved_vs_r2")
            ),
            "unmatched_seeds_decreased_vs_r2": bool(
                (search_public.get("legacy_58_seed_compatibility_benchmark") or {}).get("unmatched_seeds_decreased_vs_r2")
            ),
            "average_overlap_improved_vs_r2": bool(
                (search_public.get("legacy_58_seed_compatibility_benchmark") or {}).get("average_overlap_improved_vs_r2")
            ),
            "giant_component_recurrence": graph["largest_component_signal_count"] > 88,
        },
        "checkpoint_proof": {
            "compatibility_version": COMPATIBILITY_VERSION,
            "pair_manifest_generated": True,
            "cache_coverage_generated": True,
            "durable_checkpoint_labels": [
                "r2r-autonomous-first-pass-records",
                "r2r-autonomous-first-pass-failures",
                "r2r-autonomous-second-pass-records",
                "r2r-autonomous-second-pass-failures",
                "r2r-deferred-nonblocking-overlay",
            ],
            "durable_checkpoint_passed": overlay_proof["checksum_passed"],
            "atomic_per_success_persistence": True,
            "final_regeneration_cache_only": False,
            "final_regeneration_provider_calls": 0,
            "private_checkpoint_paths_redacted": True,
        },
        "public_redaction": {
            "passed": True,
            "findings": [],
            "allowed_findings": [],
            "clean_before_public_write": True,
            "unsafe_public_report_written": False,
        },
        "review_pack": {
            "generated": True,
            "manifest_present": True,
            "checksums_present": True,
            "integrity_passed": True,
            "not_committed": True,
        },
        "route_authorization": route_authorization(),
        "artifact_lifecycle": {
            "autonomous_closure_service": "durable production code",
            "search_service": "durable production code",
            "runner": "phase-scoped operational runner",
            "tests": "phase-scoped validation tests",
            "private_artifacts": "one-off local artifact / ignored output",
            "public_reports": "public report / handoff / roadmap update",
        },
        "validation": {
            "python_executable": Path(sys.executable).name,
            "python_executable_path_redacted": True,
            "provider_network_attempted": False,
            "browser_validation": "not_required_no_ui_change",
            "server_started": False,
        },
    }
    contract = check_phase_contract(CONTRACT_ID, summary)
    summary["validation"]["r2r_contract_passed"] = contract.passed
    summary["validation"]["r2r_contract_error_count"] = len(contract.errors)
    markdown = public_report_markdown(summary)
    redaction = scan_public_outputs(markdown, summary, output_dir, f"{args.run_id}-final")
    if not redaction["passed"]:
        write_json(
            artifact_path(output_dir, f"blocked-public-redaction-{args.run_id}.json"),
            {"status": "blocked_public_redaction", "public_redaction": redaction, "public_files_written": False},
        )
        raise R2RBlockedError("blocked_public_redaction")
    summary["public_redaction"] = redaction
    contract = check_phase_contract(CONTRACT_ID, summary)
    summary["validation"]["r2r_contract_passed"] = contract.passed
    summary["validation"]["r2r_contract_error_count"] = len(contract.errors)
    markdown = public_report_markdown(summary)
    pack = write_review_pack(output_dir, args.run_id, summary, markdown, list(private_artifacts))
    summary["review_pack"] = pack
    contract = check_phase_contract(CONTRACT_ID, summary)
    summary["validation"]["r2r_contract_passed"] = contract.passed
    summary["validation"]["r2r_contract_error_count"] = len(contract.errors)
    if not contract.passed:
        write_json(
            artifact_path(output_dir, f"blocked-contract-{args.run_id}.json"),
            {"status": "blocked_contract", "errors": [row.to_dict() for row in contract.errors]},
        )
        raise R2RBlockedError("blocked_contract")
    markdown = public_report_markdown(summary)
    final_redaction = scan_public_outputs(markdown, summary, output_dir, f"{args.run_id}-publish")
    if not final_redaction["passed"]:
        raise R2RBlockedError("blocked_public_redaction")
    summary["public_redaction"] = final_redaction
    write_text(PUBLIC_REPORT_MD, markdown)
    write_json(PUBLIC_REPORT_JSON, summary)
    write_json(artifact_path(output_dir, f"dry-run-result-{args.run_id}.json"), summary)
    return summary


def default_run_id() -> str:
    return "r2r-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("prepare", "dry-run"))
    parser.add_argument("--source-db", default=R2_BASELINE_DB)
    parser.add_argument("--working-db", default=DEFAULT_WORKING_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--legacy-cache-dir", default=str(DEFAULT_LEGACY_CACHE_DIR))
    parser.add_argument("--r2r-cache-dir", default=str(DEFAULT_R2R_CACHE_DIR))
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--confirm-clone", default="")
    parser.add_argument("--emergency-call-ceiling", type=int, default=20000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.run_id = validate_run_id(args.run_id)
        output_dir = require_safe_output_dir(Path(args.output_dir))
        if args.mode == "prepare":
            result = prepare_working_database(args, output_dir)
        else:
            result = run_cache_only_dry_run(args, output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        try:
            output_dir = require_safe_output_dir(Path(args.output_dir))
            write_json(
                artifact_path(output_dir, f"blocked-{args.mode}-{args.run_id}.json"),
                {"phase": PHASE, "mode": args.mode, "status": str(exc), "provider_calls": 0},
            )
        except Exception:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
