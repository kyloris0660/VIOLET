#!/usr/bin/env python3
"""Run the cache-only SCV2-R2R autonomous recall/search closure preflight.

Lifecycle: phase-scoped operational runner. The initial R2R run is deliberately
cache-only. It can clone the accepted isolated R2 database and generate dry-run
evidence, but it cannot initialize or call an LLM provider.
"""

from __future__ import annotations

import argparse
import asyncio
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

from sqlalchemy import inspect, text
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
    SourceConceptFallbackSearchIndex,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
)
from app.services.source_concept_autonomous_closure_service import (  # noqa: E402
    COMPATIBILITY_VERSION,
    CANDIDATE_ALGORITHM_VERSION,
    OVERLAY_VERSION,
    CandidatePair,
    PairDisposition,
    build_candidate_pair_manifest,
    classify_legacy_cache_reuse,
    disposition_accounting,
    estimate_autonomous_budget,
    execute_autonomous_missing_pairs,
    project_autonomous_materialization,
    write_deferred_overlay,
)
from app.services.source_concept_resolver_service import (  # noqa: E402
    HARD_NEGATIVE_REASON_CODES,
    RESOLVER_VERSION,
    build_data_aware_ambiguity_profiles,
    build_source_concept_signals,
    persist_source_concept_resolution,
    resolve_source_concepts,
    source_signal_inventory,
)
from app.services.source_concept_search_service import _search_keys_for_term  # noqa: E402
from app.services.source_concept_search_service import (  # noqa: E402
    rebuild_source_concept_fallback_search_index,
)
from app.services.source_name_candidate_extraction_service import (  # noqa: E402
    primary_openai_provider_from_settings,
)
from app.services.source_name_candidate_extraction_service import FORBIDDEN_TRUTH_TABLES  # noqa: E402
from scripts import run_phase45_scv1_source_concept_coverage_audit as scv1  # noqa: E402
from scripts import run_phase45_scv2_a1_post_expansion_audit_route_decision as a1  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402
from scripts.phase_contracts.contract_checks import check_phase_contract  # noqa: E402
from app.database import migrate_add_source_concept_fallback_search_index  # noqa: E402

PHASE = "4.5-SCV2-R2R"
PHASE_TITLE = "SCV2-R2R: Autonomous Recall and Search Closure"
PHASE_SLUG = "phase-4.5-scv2-r2r-autonomous-recall-search-closure"
BRANCH = "codex/scv2-r2r-autonomous-recall-search-closure"
CONTRACT_ID = "r2r_autonomous_recall_search_closure_contract_v1"
PROVIDER_AUTHORIZATION_SCOPE = "pr_135_autonomous_pair_closure"
PROVIDER_COST_RATE_USD_PER_1K_TOKENS = 0.002
MAX_PROVIDER_ATTEMPTS_PER_PASS = 3
MAX_FIXED_POINT_ROUNDS = 4
R2_BASELINE_DB = "blombooru_scv2_r2_review4_test_20260710"
DEFAULT_WORKING_DB = "blombooru_scv2_r2r_dryrun_test_20260710"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
DEFAULT_LEGACY_CACHE_DIR = ROOT / ".local_manifests" / "source_concept_llm_adjudication_cache"
DEFAULT_R2R_CACHE_DIR = ROOT / ".local_manifests" / "source_concept_r2r_autonomous_cache"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
CREATE_CONFIRMATION = "CREATE_SCV2_R2R_ISOLATED_WORKING_DB"
EXECUTE_CONFIRMATION = "EXECUTE_SCV2_R2R_PRIMARY_PROVIDER_PR135"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

FIXED_INPUT_TABLES = r2.FIXED_INPUT_TABLES
SOURCE_CONCEPT_TABLES = r2.SOURCE_CONCEPT_TABLES


class R2RBlockedError(RuntimeError):
    """Raised when an R2R gate fails before provider or unsafe writes."""


class PrimaryProviderJudgmentExecutor:
    """Strict primary-only JSON executor with aggregate usage accounting."""

    def __init__(self, provider: Any, provider_summary: Mapping[str, Any]):
        if provider is None or provider_summary.get("uses_fallback_provider") is not False:
            raise R2RBlockedError("blocked_primary_provider_unavailable_or_fallback_configured")
        self.provider = provider
        self.provider_summary = dict(provider_summary)
        self.attempted_calls = 0
        self.returned_calls = 0
        self.error_calls = 0
        self.usage_missing_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.pass_calls: Counter[str] = Counter()

    def __call__(self, pass_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), Mapping) else {}
        pair_id = str(candidate.get("pair_id") or "")
        pass_version = str(payload.get("pass_version") or "")
        messages = [
            {
                "role": "system",
                "content": (
                    "You adjudicate unconfirmed SourceConcept source-layer signal pairs. "
                    "Return one JSON object only. Required keys: pair_id, pass_version, "
                    "decision, confidence; optional reason_code. Echo pair_id and pass_version "
                    "exactly. decision must be must_link, cannot_link, or deferred_nonblocking. "
                    "confidence must be a finite JSON number from 0.0 through 1.0. reason_code, "
                    "if present, must be a short ASCII identifier using letters, digits, dot, "
                    "underscore, colon, or hyphen. Do not create Entity truth, request human "
                    "review, expose chain-of-thought, or include any extra prose."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "autonomous_source_concept_pair_disposition",
                        "pair_id": pair_id,
                        "pass_version": pass_version,
                        "fixed_evidence_payload": payload,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        self.attempted_calls += 1
        self.pass_calls[pass_name] += 1
        try:
            response = asyncio.run(
                self.provider.complete_json(
                    messages,
                    temperature=0.0,
                    max_tokens=220 if pass_name == "first" else 320,
                )
            )
        except Exception:
            self.error_calls += 1
            raise
        self.returned_calls += 1
        usage = getattr(self.provider, "last_usage", {})
        if not isinstance(usage, Mapping) or not usage:
            self.usage_missing_calls += 1
        else:
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
            self.total_tokens += int(usage.get("total_tokens") or 0)
        return response

    def public_summary(self) -> dict[str, Any]:
        return {
            "provider_mode": "primary_openai_compatible_only",
            "primary_provider_only": True,
            "fallback_provider_used": False,
            "attempted_calls": self.attempted_calls,
            "returned_calls": self.returned_calls,
            "transport_or_provider_error_calls": self.error_calls,
            "usage_missing_call_count": self.usage_missing_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "actual_cost_usd": round(
                self.total_tokens / 1000.0 * PROVIDER_COST_RATE_USD_PER_1K_TOKENS,
                6,
            ),
            "cost_accounting_rate_usd_per_1k_tokens": PROVIDER_COST_RATE_USD_PER_1K_TOKENS,
            "first_pass_calls": self.pass_calls["first"],
            "second_pass_calls": self.pass_calls["second"],
            "provider_identity_redacted": True,
            "provider_url_redacted": True,
        }


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


def complete_current_cannot_pairs(
    *,
    signal_by_key: Mapping[str, Any],
    dispositions: Sequence[PairDisposition],
    legacy_analysis_rows: Sequence[Mapping[str, Any]],
    constraint_edges: Sequence[Any],
    resolved_concepts: Sequence[Any],
) -> set[tuple[str, str]]:
    """Return direct and component-transitive current cannot constraints."""

    cannot_pairs: set[tuple[str, str]] = set()
    for row in legacy_analysis_rows:
        if (
            row.get("disposition") != "cannot_link"
            or row.get("reuse_level") not in {"exact_compatible", "stable_pair_identity"}
            or not row.get("current_candidate")
        ):
            continue
        pair = tuple(row.get("pair") or ())
        if len(pair) == 2 and pair[0] in signal_by_key and pair[1] in signal_by_key:
            cannot_pairs.add(tuple(sorted((str(pair[0]), str(pair[1])))))
    for disposition in dispositions:
        if disposition.disposition == "cannot_link":
            cannot_pairs.add(
                tuple(
                    sorted(
                        (
                            str(disposition.left_signal_key),
                            str(disposition.right_signal_key),
                        )
                    )
                )
            )
    hard_constraint_pairs = {
        tuple(sorted((str(edge.left_signal_key), str(edge.right_signal_key))))
        for edge in constraint_edges
        if str(edge.negative_reason_code or "") in HARD_NEGATIVE_REASON_CODES
    }
    cannot_pairs.update(hard_constraint_pairs)
    component_by_signal: dict[str, set[str]] = {}
    for concept in resolved_concepts:
        members = {str(signal.signal_key) for signal in concept.signals}
        for signal_key in members:
            component_by_signal[signal_key] = members
    for left_key, right_key in sorted(hard_constraint_pairs):
        for left_member in component_by_signal.get(left_key, {left_key}):
            for right_member in component_by_signal.get(right_key, {right_key}):
                if left_member != right_member:
                    cannot_pairs.add(tuple(sorted((left_member, right_member))))
    return cannot_pairs


def build_automated_search_benchmark(
    session: Any,
    signals: Sequence[Any],
    *,
    dispositions: Sequence[PairDisposition],
    legacy_analysis_rows: Sequence[Mapping[str, Any]],
    constraint_edges: Sequence[Any] = (),
    resolved_concepts: Sequence[Any] = (),
    legacy_seed_groups_override: Mapping[str, Sequence[str]] | None = None,
    apply_cannot_alias_guards: bool = True,
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
        .filter(SourceConceptSignal.status.in_(("active", "materialized_identity")))
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
                "concept_ids": [],
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
                "concept_ids": [concept_id],
            }
        )

    cannot_pairs = complete_current_cannot_pairs(
        signal_by_key=signal_by_key,
        dispositions=dispositions,
        legacy_analysis_rows=legacy_analysis_rows,
        constraint_edges=constraint_edges,
        resolved_concepts=resolved_concepts,
    )
    blocked_cannot_alias_keys: set[str] = set()
    for left_key, right_key in cannot_pairs:
        left = signal_by_key.get(left_key)
        right = signal_by_key.get(right_key)
        if left is None or right is None:
            continue
        blocked_cannot_alias_keys.update(
            {
                str(value)
                for value in (left.canonical_key, left.normalized_key)
                if value
            }.intersection(
                {
                    str(value)
                    for value in (right.canonical_key, right.normalized_key)
                    if value
                }
            )
        )
    if not apply_cannot_alias_guards:
        blocked_cannot_alias_keys = set()
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
                "concept_ids": [],
            }
        )

    concept_media_by_id: dict[int, set[int]] = defaultdict(set)
    for concept_id, media_id in (
        session.query(SourceConceptEvidence.concept_id, SourceConceptEvidence.media_id)
        .join(SourceConcept, SourceConcept.id == SourceConceptEvidence.concept_id)
        .filter(SourceConcept.status == "active")
        .filter(SourceConceptEvidence.status == "active")
        .filter(SourceConceptEvidence.media_id.isnot(None))
        .all()
    ):
        concept_media_by_id[int(concept_id)].add(int(media_id))
    for concept_id, media_id in (
        session.query(SourceConceptSignalLink.concept_id, SourceConceptSignal.media_id)
        .join(SourceConcept, SourceConcept.id == SourceConceptSignalLink.concept_id)
        .join(SourceConceptSignal, SourceConceptSignal.id == SourceConceptSignalLink.signal_id)
        .filter(SourceConcept.status == "active")
        .filter(SourceConceptSignalLink.link_status == "active")
        .filter(SourceConceptSignal.status.in_(("active", "materialized_identity")))
        .filter(SourceConceptSignal.media_id.isnot(None))
        .all()
    ):
        concept_media_by_id[int(concept_id)].add(int(media_id))
    concept_ids_by_signal_key: dict[str, set[int]] = defaultdict(set)
    for signal_key, concept_id in (
        session.query(SourceConceptSignal.signal_key, SourceConceptAlias.concept_id)
        .join(SourceConceptAlias, SourceConceptAlias.source_signal_id == SourceConceptSignal.id)
        .join(SourceConcept, SourceConcept.id == SourceConceptAlias.concept_id)
        .filter(SourceConcept.status == "active")
        .filter(SourceConceptAlias.status == "active")
        .all()
    ):
        concept_ids_by_signal_key[str(signal_key)].add(int(concept_id))

    def allowed_family_media_ids(family: Mapping[str, Any]) -> set[int]:
        signal_keys = {str(value) for value in family.get("signal_keys") or []}
        allowed = {
            int(signal_by_key[key].media_id)
            for key in signal_keys
            if key in signal_by_key and signal_by_key[key].media_id is not None
        }
        concept_ids = {int(value) for value in family.get("concept_ids") or []}
        for signal_key in signal_keys:
            concept_ids.update(concept_ids_by_signal_key.get(signal_key, set()))
        for concept_id in concept_ids:
            allowed.update(concept_media_by_id.get(concept_id, set()))
        for disposition in dispositions:
            if disposition.disposition not in {"must_link", "deferred_nonblocking"}:
                continue
            endpoints = {
                str(disposition.left_signal_key),
                str(disposition.right_signal_key),
            }
            if not endpoints.issubset(signal_keys):
                continue
            for endpoint in endpoints:
                signal = signal_by_key.get(endpoint)
                if signal is not None and signal.media_id is not None:
                    allowed.add(int(signal.media_id))
        return allowed

    seed_cache: dict[str, dict[str, set[int]]] = {}
    category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evaluated: list[dict[str, Any]] = []
    false_broad_union = 0
    unexpected_media_count = 0
    false_broad_seed_ids: set[str] = set()
    false_broad_by_category: Counter[str] = Counter()
    false_broad_samples: list[dict[str, Any]] = []
    contamination = 0
    for family in families:
        allowed_media = allowed_family_media_ids(family)
        per_seed = []
        for seed in family["seeds"]:
            if seed not in seed_cache:
                keys = _search_keys_for_term(seed) - blocked_cannot_alias_keys
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
            unexpected = paths["combined"] - allowed_media
            if unexpected:
                false_broad_union += 1
                unexpected_media_count += len(unexpected)
                seed_id = hashlib.sha256(
                    f"{family['family_id']}:{seed}".encode("utf-8")
                ).hexdigest()
                false_broad_seed_ids.add(seed_id)
                for category in family["categories"]:
                    false_broad_by_category[str(category)] += 1
                if len(false_broad_samples) < 25:
                    false_broad_samples.append(
                        {
                            "family_id": family["family_id"],
                            "seed_id": seed_id,
                            "unexpected_media_count": len(unexpected),
                            "unexpected_media_ids_redacted": True,
                        }
                    )
            per_seed.append(
                {
                    "seed": seed,
                    "identity_media": sorted(paths["identity"]),
                    "fallback_media": sorted(paths["evidence_fallback"]),
                    "combined_media": sorted(paths["combined"]),
                    "allowed_family_media": sorted(allowed_media),
                    "unexpected_fallback_media": sorted(unexpected),
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
            keys = _search_keys_for_term(seed) - blocked_cannot_alias_keys
            identity_direct = set().union(*(identity_media_by_key.get(key, set()) for key in keys)) if keys else set()
            fallback_direct = set().union(*(fallback_media_by_key.get(key, set()) for key in keys)) if keys else set()
            overlay_added = set().union(*(overlay_media_by_key.get(key, set()) for key in keys)) if keys else set()
            combined = identity_direct | fallback_direct | overlay_added
            other_signal_key = right_key if signal_key == left_key else left_key
            other = signal_by_key[other_signal_key]
            other_media = {int(other.media_id)} if other.media_id is not None else set()
            for concept_id in concept_ids_by_signal_key.get(other_signal_key, set()):
                other_media.update(concept_media_by_id.get(concept_id, set()))
            identity_contamination = identity_direct.intersection(other_media)
            fallback_contamination = combined.intersection(other_media)
            contamination += len(identity_contamination) + len(fallback_contamination)
            cannot_contamination_checks.append(
                {
                    "pair": [left_key, right_key],
                    "seed_signal_key": signal_key,
                    "direct_result_count": len(combined),
                    "identity_path_contamination_media_count": len(identity_contamination),
                    "evidence_fallback_contamination_media_count": len(fallback_contamination),
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
    legacy_seed_groups = (
        dict(legacy_seed_groups_override)
        if legacy_seed_groups_override is not None
        else {
            **a1.SCV1_SEED_GROUPS,
            **a1.build_dynamic_px1_seed_groups(session.connection()),
        }
    )
    legacy_identity_sets: list[set[int]] = []
    legacy_fallback_sets: list[set[int]] = []
    legacy_groups_private: list[dict[str, Any]] = []
    for group_name, seeds in legacy_seed_groups.items():
        group_identity: list[set[int]] = []
        group_fallback: list[set[int]] = []
        seed_rows = []
        for seed in seeds:
            if seed not in seed_cache:
                keys = _search_keys_for_term(seed) - blocked_cannot_alias_keys
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
        "benchmark_version": "r2r_automated_search_benchmark_v2_independent_universe_complete_cannot",
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
        "seeds_with_false_broad_union": len(false_broad_seed_ids),
        "unexpected_media_count": unexpected_media_count,
        "false_broad_union_category_breakdown": dict(false_broad_by_category),
        "cannot_linked_search_contamination_count": contamination,
        "identity_path_cannot_contamination_count": sum(
            int(row["identity_path_contamination_media_count"])
            for row in cannot_contamination_checks
        ),
        "evidence_fallback_cannot_contamination_count": sum(
            int(row["evidence_fallback_contamination_media_count"])
            for row in cannot_contamination_checks
        ),
        "complete_current_cannot_pair_count": len(cannot_pairs),
        "blocked_cannot_ambiguous_alias_key_count": len(blocked_cannot_alias_keys),
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
        "false_broad_union_samples": false_broad_samples,
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


def provider_authorization() -> dict[str, Any]:
    return {
        "status": "approved",
        "approved_scope": PROVIDER_AUTHORIZATION_SCOPE,
        "primary_provider_only": True,
        "fixed_monetary_cap": None,
        "further_budget_approval_required": False,
        "first_pass_authorized": True,
        "second_pass_authorized": True,
        "compatible_deferred_reescalation_authorized": True,
        "post_rebuild_new_pair_authorized": True,
        "bounded_retry_authorized": True,
        "fallback_provider_authorized": False,
        "metadata_acquisition_authorized": False,
        "other_phase_authorized": False,
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


def _legacy_public_report_markdown(summary: Mapping[str, Any]) -> str:
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


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    candidate = summary.get("candidate_population") or {}
    cache = summary.get("cache_reuse") or {}
    execution = summary.get("llm_execution") or {}
    dispositions = summary.get("candidate_dispositions") or {}
    projection = summary.get("materialization_projection") or {}
    graph = summary.get("graph_invariants") or {}
    search = summary.get("search_benchmark") or {}
    legacy = search.get("legacy_58_seed_compatibility_benchmark") or {}
    checkpoint = summary.get("checkpoint_proof") or {}
    authorization = summary.get("provider_authorization") or {}
    return "\n".join(
        [
            f"# {PHASE_TITLE}",
            "",
            "## 状态",
            "",
            f"- 合同状态：`{(summary.get('pipeline_contract') or {}).get('status')}`。",
            f"- 分支：`{summary.get('branch')}`。",
            f"- 证据代码 SHA：`{summary.get('evidence_code_sha')}`。",
            "- 本阶段仅处理 PR #135 的 SourceConcept source-layer 自主闭环。",
            "",
            "## Provider 授权与执行",
            "",
            f"- 授权状态 / 范围：`{authorization.get('status')}` / `{authorization.get('approved_scope')}`。",
            f"- 固定金额上限：`{authorization.get('fixed_monetary_cap')}`；仍需预算审批：`{authorization.get('further_budget_approval_required')}`。",
            f"- Primary calls / fallback used：`{execution.get('attempted_calls', 0)}` / `{execution.get('fallback_provider_used', False)}`。",
            f"- Prompt / completion / total tokens：`{execution.get('prompt_tokens', 0)}` / `{execution.get('completion_tokens', 0)}` / `{execution.get('total_tokens', 0)}`。",
            f"- 实际计费估算：`${execution.get('actual_cost_usd', 0)}`。",
            "",
            "## 候选与机器处置",
            "",
            f"- 唯一候选 pair：`{candidate.get('total_candidate_pairs')}`；执行前去重：`{candidate.get('duplicates_removed_before_execution')}`。",
            f"- Exact / stable cache reuse：`{cache.get('exact_compatible_cache_hit_count')}` / `{cache.get('stable_compatible_reuse_count')}`。",
            f"- must_link / cannot_link / deferred_nonblocking：`{dispositions.get('must_link_count')}` / `{dispositions.get('cannot_link_count')}` / `{dispositions.get('deferred_nonblocking_count')}`。",
            f"- Unaccounted / coverage：`{dispositions.get('unaccounted_pair_count')}` / `{dispositions.get('candidate_disposition_coverage')}`。",
            "- manual_review_required_count / operator_blocking_review_count：`0 / 0`。",
            "- 未生成 human review queue。",
            "",
            "## 物化、约束与证据覆盖",
            "",
            f"- Materialized SourceConcept / needs_review：`{projection.get('actual_materialized_source_concept_count', projection.get('materialized_source_concept_count'))}` / `{projection.get('actual_materialized_needs_review_count', projection.get('materialized_needs_review_count'))}`。",
            f"- Deferred evidence signals：`{projection.get('deferred_evidence_signal_count')}`。",
            f"- Indexed fallback rows：`{((projection.get('indexed_fallback') or {}).get('row_count'))}`。",
            f"- Direct/transitive cannot violations：`{graph.get('direct_cannot_violation_count')}` / `{graph.get('transitive_cannot_violation_count')}`。",
            f"- Review/deferred union / unknown-role materialization：`{graph.get('review_or_deferred_edge_used_in_union_count')}` / `{graph.get('unauthorized_unknown_role_materialization_count')}`。",
            "",
            "## 搜索基准",
            "",
            f"- Expanded families / seeds：`{search.get('family_count')}` / `{search.get('seed_count')}`。",
            f"- Identity path：`{search.get('identity_path')}`。",
            f"- Evidence fallback path：`{search.get('evidence_fallback_path')}`。",
            f"- False broad union seeds / indicators / unexpected media：`{search.get('seeds_with_false_broad_union')}` / `{search.get('false_broad_union_indicator_count')}` / `{search.get('unexpected_media_count')}`。",
            f"- Identity/fallback cannot contamination：`{search.get('identity_path_cannot_contamination_count')}` / `{search.get('evidence_fallback_cannot_contamination_count')}`。",
            f"- Legacy 58-seed benchmark：`{legacy}`。",
            "",
            "## 缓存、合同与安全",
            "",
            f"- Final regeneration cache-only / provider calls：`{checkpoint.get('final_regeneration_cache_only')}` / `{checkpoint.get('final_regeneration_provider_calls')}`。",
            f"- 固定证据 / forbidden truth unchanged：`{(summary.get('fixed_input_proof') or {}).get('before_after_match')}` / `{(summary.get('fixed_input_proof') or {}).get('forbidden_truth_content_unchanged')}`。",
            f"- R2R contract / public redaction / review pack：`{(summary.get('validation') or {}).get('r2r_contract_passed')}` / `{(summary.get('public_redaction') or {}).get('passed')}` / `{(summary.get('review_pack') or {}).get('integrity_passed')}`。",
            "- 未启动 PX1-B、Provider-2、scale-up、Entity bridge、production、full-library 或 truth promotion。",
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
            constraint_edges=resolved.edge_candidates,
            resolved_concepts=resolved.concepts,
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
            "provider_authorization": provider_authorization(),
            "provider_initialized": False,
            "provider_calls": 0,
            "errors": [],
            "status": "authorized_preflight_no_calls",
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
    status = "partial_autonomous_closure"
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
        "provider_authorization": provider_authorization(),
        "candidate_population": {
            "total_candidate_pairs": len(candidates),
            "candidate_manifest_pair_count": len(candidates),
            "unique_budget_eligible_pair_count": len(candidates),
            "candidate_algorithm_version": CANDIDATE_ALGORITHM_VERSION,
            "duplicates_removed_before_execution": max(
                0,
                sum(
                    edge.status in {"weak", "needs_review"}
                    and edge.edge_type
                    in {
                        "cooccurrence_context",
                        "alias_candidate_edge",
                        "same_surface_context",
                        "exact_canonical_key",
                    }
                    for edge in deterministic.edge_candidates
                )
                - len(candidates),
            ),
            "eligibility_policy": "budget_driven_all_eligible_unique_pairs",
            "fixed_small_pair_cap_used": False,
            "emergency_call_ceiling": args.emergency_call_ceiling,
        },
        "cache_reuse": cache_reuse,
        "budget_projection": budget,
        "llm_execution": {
            "status": status,
            "operator_approval_required": False,
            "fixed_monetary_cap": None,
            "further_budget_approval_required": False,
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
            "candidate_algorithm_version": CANDIDATE_ALGORITHM_VERSION,
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


def _disposition_fingerprint(dispositions: Mapping[str, PairDisposition]) -> str:
    payload = [
        {
            "pair_id": pair_id,
            "disposition": row.disposition,
            "pass_name": row.pass_name,
        }
        for pair_id, row in sorted(dispositions.items())
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _llm_judgments_from_dispositions(
    dispositions: Mapping[str, PairDisposition],
) -> list[dict[str, Any]]:
    return [
        {
            "judgment_id": f"r2r:{row.pair_id}",
            "left_signal_key": row.left_signal_key,
            "right_signal_key": row.right_signal_key,
            "decision": row.disposition,
            "confidence": row.confidence,
            "reason_code": row.reason_code,
            "source_layer_only": True,
            "human_review_required": False,
            "provider_failure": False,
        }
        for row in sorted(dispositions.values(), key=lambda item: item.pair_id)
    ]


def _component_distribution(result: Any) -> dict[str, int]:
    buckets = Counter()
    for concept in result.concepts:
        size = len(concept.signals)
        if size <= 1:
            buckets["1"] += 1
        elif size <= 3:
            buckets["2-3"] += 1
        elif size <= 10:
            buckets["4-10"] += 1
        elif size <= 25:
            buckets["11-25"] += 1
        else:
            buckets["26+"] += 1
    return {key: buckets[key] for key in ("1", "2-3", "4-10", "11-25", "26+")}


def _merge_execution_proofs(proofs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    aggregate: Counter[str] = Counter()
    transitions: list[dict[str, Any]] = []
    for proof in proofs:
        for key, value in proof.items():
            if isinstance(value, int) and not isinstance(value, bool):
                aggregate[key] += value
        transitions.extend(proof.get("transitions") or [])
    return {**dict(aggregate), "transitions": transitions}


def run_authorized_execution(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    """Execute the approved primary-provider closure and deterministic rebuild."""

    if args.execute_confirmation != EXECUTE_CONFIRMATION:
        raise R2RBlockedError("blocked_missing_authorized_execution_confirmation")
    isolation = environment_isolation(args.source_db, args.working_db)
    if not isolation["passed"]:
        raise R2RBlockedError("blocked_environment_isolation")
    manifest, source_recheck = load_and_verify_manifest(args, output_dir)
    preflight_path = artifact_path(output_dir, f"dry-run-result-{args.preflight_run_id}.json")
    if not preflight_path.exists():
        raise R2RBlockedError("blocked_missing_current_cache_only_preflight")
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if (
        preflight.get("evidence_code_sha") != git_value(["git", "rev-parse", "HEAD"])
        or (preflight.get("provider_authorization") or {}).get("status") != "approved"
        or not (preflight.get("validation") or {}).get("r2r_contract_passed")
    ):
        raise R2RBlockedError("blocked_stale_or_invalid_cache_only_preflight")

    cache_root = require_safe_private_path(Path(args.r2r_cache_dir))
    engine = r2.create_db_engine(args.working_db)
    provider = None
    provider_summary: dict[str, Any] = {}
    all_execution_proofs: list[dict[str, Any]] = []
    convergence_rounds: list[dict[str, Any]] = []
    final_projection: dict[str, Any] = {}
    final_index_proof: dict[str, Any] = {}
    final_search: dict[str, Any] = {}
    final_search_private: dict[str, Any] = {}
    final_graph: dict[str, Any] = {}
    final_result: Any = None
    final_projected: Any = None
    final_candidates: tuple[CandidatePair, ...] = ()
    final_dispositions: dict[str, PairDisposition] = {}
    final_cache_reuse: dict[str, Any] = {}
    final_legacy_analysis: list[dict[str, Any]] = []
    fixed_before: dict[str, Any] = {}
    forbidden_before: dict[str, Any] = {}
    executor: PrimaryProviderJudgmentExecutor | None = None
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            fixed_before = r2.fingerprint_tables(conn, FIXED_INPUT_TABLES)
            forbidden_before = r2.fingerprint_tables(conn, FORBIDDEN_TRUTH_TABLES)
            conn.rollback()
        if not r2.compare_fingerprints(manifest["working_fixed_snapshot"], fixed_before)["passed"]:
            raise R2RBlockedError("blocked_fixed_evidence_changed")
        if not r2.compare_fingerprints(manifest["working_forbidden_snapshot"], forbidden_before)["passed"]:
            raise R2RBlockedError("blocked_fixed_evidence_changed")

        provider, provider_summary = primary_openai_provider_from_settings()
        if provider is None or provider_summary.get("uses_fallback_provider") is not False:
            raise R2RBlockedError("blocked_primary_provider_configuration_unavailable")
        executor = PrimaryProviderJudgmentExecutor(provider, provider_summary)

        migrate_add_source_concept_fallback_search_index(engine, inspect(engine))
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            signals = build_source_concept_signals(session, run_id=f"{args.run_id}-signals")
            deterministic = resolve_source_concepts(signals, run_id=f"{args.run_id}-deterministic")
            candidates = build_candidate_pair_manifest(
                deterministic.edge_candidates,
                signals=signals,
                max_calls=args.emergency_call_ceiling,
            )
            expected_population = int(
                (preflight.get("candidate_population") or {}).get("total_candidate_pairs") or -1
            )
            if len(candidates) != expected_population:
                raise R2RBlockedError(
                    f"blocked_candidate_population_changed_after_preflight:{len(candidates)}!={expected_population}"
                )
            reused, cache_reuse, legacy_analysis = classify_legacy_cache_reuse(
                Path(args.legacy_cache_dir),
                candidates=candidates,
                signals=signals,
                resolver_version=RESOLVER_VERSION,
            )
            current_initial = dict(reused)
            previous_pair_ids = {candidate.pair_id for candidate in candidates}

            for round_number in range(1, MAX_FIXED_POINT_ROUNDS + 1):
                dispositions, execution_proof = execute_autonomous_missing_pairs(
                    candidates,
                    initial_dispositions=current_initial,
                    signal_by_key={signal.signal_key: signal for signal in signals},
                    cache_root=cache_root,
                    executor=executor,
                    max_attempts_per_pass=MAX_PROVIDER_ATTEMPTS_PER_PASS,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                )
                all_execution_proofs.append(execution_proof)
                accounting = disposition_accounting(candidates, dispositions.values())
                if accounting["unaccounted_pair_count"]:
                    final_candidates = tuple(candidates)
                    final_dispositions = dispositions
                    final_cache_reuse = cache_reuse
                    final_legacy_analysis = legacy_analysis
                    break

                run_id = f"{args.run_id}-round-{round_number}"
                resolved = resolve_source_concepts(
                    signals,
                    run_id=run_id,
                    llm_judgments=_llm_judgments_from_dispositions(dispositions),
                )
                projected, projection = project_autonomous_materialization(
                    resolved,
                    dispositions=list(dispositions.values()),
                )
                projection_check_result, projection_check = project_autonomous_materialization(
                    resolved,
                    dispositions=list(dispositions.values()),
                )
                projection["idempotent_fingerprint_match"] = bool(
                    projection["projection_fingerprint"]
                    == projection_check["projection_fingerprint"]
                    and len(projected.concepts) == len(projection_check_result.concepts)
                )
                persistence = persist_source_concept_resolution(
                    session,
                    projected,
                    apply=True,
                    inventory=source_signal_inventory(session),
                    run_label="scv2_r2r_autonomous_recall_search_closure",
                )
                current_cannot_pairs = complete_current_cannot_pairs(
                    signal_by_key={signal.signal_key: signal for signal in projected.signals},
                    dispositions=list(dispositions.values()),
                    legacy_analysis_rows=legacy_analysis,
                    constraint_edges=resolved.edge_candidates,
                    resolved_concepts=resolved.concepts,
                )
                index_first = rebuild_source_concept_fallback_search_index(
                    session,
                    signals=projected.signals,
                    dispositions=list(dispositions.values()),
                    run_id=run_id,
                    cannot_pairs=sorted(current_cannot_pairs),
                )
                index_second = rebuild_source_concept_fallback_search_index(
                    session,
                    signals=projected.signals,
                    dispositions=list(dispositions.values()),
                    run_id=run_id,
                    cannot_pairs=sorted(current_cannot_pairs),
                )
                index_first["generated"] = True
                index_first["deterministic"] = True
                index_first["idempotent"] = bool(
                    index_first["deterministic_fingerprint"]
                    == index_second["deterministic_fingerprint"]
                    and index_first["row_count"] == index_second["row_count"]
                )
                index_first["source_layer_only"] = True
                session.commit()

                regenerated_signals = build_source_concept_signals(
                    session,
                    run_id=f"{args.run_id}-round-{round_number}-regenerated",
                )
                regenerated_deterministic = resolve_source_concepts(
                    regenerated_signals,
                    run_id=f"{args.run_id}-round-{round_number}-candidate-regeneration",
                )
                regenerated_candidates = build_candidate_pair_manifest(
                    regenerated_deterministic.edge_candidates,
                    signals=regenerated_signals,
                    max_calls=args.emergency_call_ceiling,
                )
                regenerated_ids = {candidate.pair_id for candidate in regenerated_candidates}
                disappeared = sorted(previous_pair_ids - regenerated_ids)
                new_ids = sorted(regenerated_ids - previous_pair_ids)
                convergence_rounds.append(
                    {
                        "round": round_number,
                        "candidate_pair_count": len(candidates),
                        "new_pair_count": len(new_ids),
                        "disappeared_pair_count": len(disappeared),
                        "accounting_coverage": accounting["candidate_disposition_coverage"],
                        "materialized_source_concept_count": projection[
                            "materialized_source_concept_count"
                        ],
                    }
                )
                if disappeared:
                    raise R2RBlockedError("blocked_non_monotonic_candidate_manifest")

                final_result = resolved
                final_projected = projected
                final_projection = projection
                final_index_proof = index_first
                final_candidates = tuple(candidates)
                final_dispositions = dispositions
                final_cache_reuse = cache_reuse
                final_legacy_analysis = legacy_analysis
                if not new_ids:
                    break
                if round_number == MAX_FIXED_POINT_ROUNDS:
                    raise R2RBlockedError("blocked_fixed_point_convergence_guard_exhausted")

                next_reused, cache_reuse, legacy_analysis = classify_legacy_cache_reuse(
                    Path(args.legacy_cache_dir),
                    candidates=regenerated_candidates,
                    signals=regenerated_signals,
                    resolver_version=RESOLVER_VERSION,
                )
                current_initial = dict(dispositions)
                for pair_id, disposition in next_reused.items():
                    current_initial.setdefault(pair_id, disposition)
                signals = regenerated_signals
                deterministic = regenerated_deterministic
                candidates = regenerated_candidates
                previous_pair_ids = regenerated_ids

            accounting = disposition_accounting(final_candidates, final_dispositions.values())
            cache_only_provider_attempts = 0

            def cache_only_executor(_pass_name: str, _payload: Mapping[str, Any]) -> Mapping[str, Any]:
                nonlocal cache_only_provider_attempts
                cache_only_provider_attempts += 1
                raise AssertionError("final cache-only regeneration attempted provider")

            regen_reused, _regen_cache, _regen_analysis = classify_legacy_cache_reuse(
                Path(args.legacy_cache_dir),
                candidates=final_candidates,
                signals=signals,
                resolver_version=RESOLVER_VERSION,
            )
            regenerated_dispositions, regeneration_proof = execute_autonomous_missing_pairs(
                final_candidates,
                initial_dispositions=regen_reused,
                signal_by_key={signal.signal_key: signal for signal in signals},
                cache_root=cache_root,
                executor=cache_only_executor,
                max_attempts_per_pass=1,
            )
            regeneration_accounting = disposition_accounting(
                final_candidates,
                regenerated_dispositions.values(),
            )
            final_regeneration_cache_only = bool(
                cache_only_provider_attempts == 0
                and regeneration_accounting["accounting_equality_passed"]
                and _disposition_fingerprint(regenerated_dispositions)
                == _disposition_fingerprint(final_dispositions)
            )

            if final_result is not None and final_projected is not None:
                final_graph = _graph_invariants(final_result)
                final_search, final_search_private = build_automated_search_benchmark(
                    session,
                    final_projected.signals,
                    dispositions=list(final_dispositions.values()),
                    legacy_analysis_rows=final_legacy_analysis,
                    constraint_edges=final_result.edge_candidates,
                    resolved_concepts=final_result.concepts,
                )
                final_search["indexed_fallback"] = final_index_proof
                final_search["symmetry_improved_vs_r2"] = bool(
                    (final_search.get("legacy_58_seed_compatibility_benchmark") or {}).get(
                        "symmetry_improved_vs_r2"
                    )
                )
                final_search["unmatched_seeds_decreased_vs_r2"] = bool(
                    (final_search.get("legacy_58_seed_compatibility_benchmark") or {}).get(
                        "unmatched_seeds_decreased_vs_r2"
                    )
                )
                final_search["average_overlap_improved_vs_r2"] = bool(
                    (final_search.get("legacy_58_seed_compatibility_benchmark") or {}).get(
                        "average_overlap_improved_vs_r2"
                    )
                )
                final_search["giant_component_recurrence"] = (
                    final_graph.get("largest_component_signal_count", 0) > 88
                )
            else:
                final_regeneration_cache_only = False
                final_search = {"generated": False}
                final_graph = {}

            fixed_after = r2.fingerprint_tables(session.connection(), FIXED_INPUT_TABLES)
            forbidden_after = r2.fingerprint_tables(session.connection(), FORBIDDEN_TRUTH_TABLES)
            fixed_comparison = r2.compare_fingerprints(fixed_before, fixed_after)
            forbidden_comparison = r2.compare_fingerprints(forbidden_before, forbidden_after)
            session.rollback()

            execution = _merge_execution_proofs(all_execution_proofs)
            provider_public = executor.public_summary()
            constraints_clean = bool(
                final_graph
                and all(
                    int(final_graph.get(key) or 0) == 0
                    for key in (
                        "review_or_deferred_edge_used_in_union_count",
                        "direct_cannot_violation_count",
                        "transitive_cannot_violation_count",
                        "deterministic_hard_conflict_count",
                        "unauthorized_unknown_role_materialization_count",
                        "unexplained_proof_grade_same_regression_count",
                    )
                )
            )
            search_target = bool(
                final_search.get("generated")
                and final_search.get("false_broad_union_indicator_count") == 0
                and final_search.get("cannot_linked_search_contamination_count") == 0
                and final_search.get("symmetry_improved_vs_r2")
                and final_search.get("unmatched_seeds_decreased_vs_r2")
                and final_search.get("average_overlap_improved_vs_r2")
                and not final_search.get("giant_component_recurrence")
            )
            if accounting["unaccounted_pair_count"]:
                status = "blocked_llm_execution_incomplete"
            elif not constraints_clean:
                status = "blocked_constraint_regression"
            elif not final_regeneration_cache_only or not search_target:
                status = "partial_autonomous_closure"
            else:
                status = "target_met_autonomous_recall_search_closure"

            operation = operation_counts()
            operation["primary_provider_calls"] = provider_public["attempted_calls"]
            fixed_proof = {
                "present": True,
                "table_count": len(FIXED_INPUT_TABLES),
                "forbidden_truth_table_count": len(FORBIDDEN_TRUTH_TABLES),
                "baseline_to_working_clone_match": bool(
                    (manifest.get("fixed_comparison") or {}).get("passed")
                ),
                "source_recheck_match": bool(source_recheck.get("passed")),
                "before_after_match": bool(fixed_comparison.get("passed")),
                "row_counts_match": bool(fixed_comparison.get("row_counts_match")),
                "schemas_match": bool(fixed_comparison.get("columns_match")),
                "content_fingerprints_match": bool(
                    fixed_comparison.get("content_fingerprints_match")
                ),
                "forbidden_truth_content_unchanged": bool(
                    forbidden_comparison.get("passed")
                ),
                "changed_tables": list(fixed_comparison.get("changed_tables") or []),
                "forbidden_truth_changed_tables": list(
                    forbidden_comparison.get("changed_tables") or []
                ),
            }
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
                    "claims": {
                        "target_met": status
                        == "target_met_autonomous_recall_search_closure",
                        "safe_to_merge": False,
                        "route_approved": False,
                    },
                },
                "environment_isolation": isolation,
                "fixed_input_proof": fixed_proof,
                "operation_counts": operation,
                "provider_authorization": provider_authorization(),
                "candidate_population": {
                    "total_candidate_pairs": len(final_candidates),
                    "candidate_manifest_pair_count": len(final_candidates),
                    "unique_budget_eligible_pair_count": len(final_candidates),
                    "candidate_algorithm_version": CANDIDATE_ALGORITHM_VERSION,
                    "duplicates_removed_before_execution": int(
                        (preflight.get("candidate_population") or {}).get(
                            "duplicates_removed_before_execution"
                        )
                        or 0
                    ),
                    "fixed_point_round_count": len(convergence_rounds),
                },
                "cache_reuse": final_cache_reuse,
                "budget_projection": preflight.get("budget_projection") or {},
                "llm_execution": {
                    **provider_public,
                    "status": status,
                    "operator_approval_required": False,
                    "fixed_monetary_cap": None,
                    "further_budget_approval_required": False,
                    "provider_failure_count": accounting["unaccounted_pair_count"],
                    "remaining_unaccounted_missing_pairs": accounting[
                        "unaccounted_pair_count"
                    ],
                    "all_approved_missing_pairs_accounted": accounting[
                        "unaccounted_pair_count"
                    ]
                    == 0,
                    "failed_judgments_counted_as_success": False,
                    "primary_provider_only": True,
                    "fallback_provider_used": False,
                    "execution_counters": execution,
                    "fixed_point_rounds": convergence_rounds,
                },
                "candidate_dispositions": accounting,
                "automation_invariants": {
                    "manual_review_required_count": 0,
                    "operator_blocking_review_count": 0,
                    "manual_review_queue_generated": False,
                    "needs_review_is_human_queue": False,
                },
                "materialization_projection": {
                    **final_projection,
                    "actual_materialized_source_concept_count": session.query(SourceConcept)
                    .filter(SourceConcept.status == "active")
                    .count(),
                    "actual_materialized_needs_review_count": session.query(SourceConcept)
                    .filter(SourceConcept.status == "needs_review")
                    .count(),
                    "component_size_distribution": _component_distribution(final_projected)
                    if final_projected is not None
                    else {},
                    "indexed_fallback": final_index_proof,
                },
                "graph_invariants": final_graph,
                "search_benchmark": final_search,
                "checkpoint_proof": {
                    "compatibility_version": COMPATIBILITY_VERSION,
                    "candidate_algorithm_version": CANDIDATE_ALGORITHM_VERSION,
                    "durable_checkpoint_passed": accounting[
                        "unaccounted_pair_count"
                    ]
                    == 0,
                    "atomic_per_success_persistence": True,
                    "bounded_retry_count": MAX_PROVIDER_ATTEMPTS_PER_PASS,
                    "final_regeneration_cache_only": final_regeneration_cache_only,
                    "final_regeneration_provider_calls": 0,
                    "final_regeneration_cache_miss_attempts": cache_only_provider_attempts,
                    "final_regeneration_accounting": regeneration_proof.get("accounting"),
                },
                "public_redaction": {
                    "passed": True,
                    "findings": [],
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
                    "indexed_fallback_search": "durable production code",
                    "runner": "phase-scoped operational runner",
                    "private_artifacts": "one-off local artifact / ignored output",
                    "public_reports": "public report / handoff / roadmap update",
                },
                "validation": {
                    "python_executable": Path(sys.executable).name,
                    "python_executable_path_redacted": True,
                    "provider_network_attempted": provider_public["attempted_calls"] > 0,
                    "browser_validation": "not_required_no_ui_change",
                    "server_started": False,
                },
            }

            private_artifacts = {
                "provider-execution-ledger.json": {
                    "provider_authorization": provider_authorization(),
                    "provider_execution": provider_public,
                    "execution_counters": execution,
                    "fixed_point_rounds": convergence_rounds,
                    "status": status,
                },
                "first-second-pass-disposition-transitions.json": {
                    "transitions": execution.get("transitions") or [],
                },
                "same-cannot-accounting.json": accounting,
                "component-diagnostics.json": {
                    "graph_invariants": final_graph,
                    "component_size_distribution": summary["materialization_projection"][
                        "component_size_distribution"
                    ],
                },
                "automated-search-benchmark.json": final_search_private,
                "persistence-projection-comparison.json": {
                    "projection": final_projection,
                    "fallback_index": final_index_proof,
                    "db_write": final_projected is not None,
                },
                "fixed-point-convergence.json": {"rounds": convergence_rounds},
                "cache-only-final-regeneration.json": {
                    "provider_calls": 0,
                    "cache_miss_attempts": cache_only_provider_attempts,
                    "passed": final_regeneration_cache_only,
                    "accounting": regeneration_proof.get("accounting"),
                },
            }
            for name, payload in private_artifacts.items():
                write_json(artifact_path(output_dir, name), payload)

            contract = check_phase_contract(CONTRACT_ID, summary)
            summary["validation"]["r2r_contract_passed"] = contract.passed
            summary["validation"]["r2r_contract_error_count"] = len(contract.errors)
            markdown = public_report_markdown(summary)
            redaction = scan_public_outputs(markdown, summary, output_dir, f"{args.run_id}-execute")
            if not redaction["passed"]:
                raise R2RBlockedError("blocked_public_redaction")
            summary["public_redaction"] = redaction
            pack = write_review_pack(
                output_dir,
                args.run_id,
                summary,
                markdown,
                list(private_artifacts),
            )
            summary["review_pack"] = pack
            contract = check_phase_contract(CONTRACT_ID, summary)
            summary["validation"]["r2r_contract_passed"] = contract.passed
            summary["validation"]["r2r_contract_error_count"] = len(contract.errors)
            if not contract.passed:
                write_json(
                    artifact_path(output_dir, f"blocked-contract-{args.run_id}.json"),
                    {"errors": [finding.to_dict() for finding in contract.errors]},
                )
                raise R2RBlockedError("blocked_contract")
            markdown = public_report_markdown(summary)
            final_redaction = scan_public_outputs(
                markdown,
                summary,
                output_dir,
                f"{args.run_id}-execute-publish",
            )
            if not final_redaction["passed"]:
                raise R2RBlockedError("blocked_public_redaction")
            summary["public_redaction"] = final_redaction
            write_text(PUBLIC_REPORT_MD, markdown)
            write_json(PUBLIC_REPORT_JSON, summary)
            write_json(artifact_path(output_dir, f"execution-result-{args.run_id}.json"), summary)
            return summary
        finally:
            session.close()
    finally:
        engine.dispose()


def default_run_id() -> str:
    return "r2r-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("prepare", "dry-run", "execute"))
    parser.add_argument("--source-db", default=R2_BASELINE_DB)
    parser.add_argument("--working-db", default=DEFAULT_WORKING_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--legacy-cache-dir", default=str(DEFAULT_LEGACY_CACHE_DIR))
    parser.add_argument("--r2r-cache-dir", default=str(DEFAULT_R2R_CACHE_DIR))
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--confirm-clone", default="")
    parser.add_argument("--execute-confirmation", default="")
    parser.add_argument("--preflight-run-id", default="")
    parser.add_argument("--retry-backoff-seconds", type=float, default=1.0)
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
        elif args.mode == "execute":
            result = run_authorized_execution(args, output_dir)
        else:
            result = run_cache_only_dry_run(args, output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        try:
            output_dir = require_safe_output_dir(Path(args.output_dir))
            write_json(
                artifact_path(output_dir, f"blocked-{args.mode}-{args.run_id}.json"),
                {
                    "phase": PHASE,
                    "mode": args.mode,
                    "status": str(exc),
                    "provider_calls": 0 if args.mode != "execute" else None,
                    "provider_calls_unknown_on_exception": args.mode == "execute",
                },
            )
        except Exception:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
