#!/usr/bin/env python3
"""Run Phase 4.5-SCV2-A1 post-expansion audit and route decision.

Lifecycle: phase-scoped operational runner.

This runner is read-only. It audits the current post-R1 development database,
writes private local artifacts, produces public-safe report files, and creates
a redacted ChatGPT review pack for independent route-decision review. It does
not execute providers, imports, AI/classification/localization, LLMs,
SourceConcept resolver writes, Entity bridge work, truth-path writes, or media
storage mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_phase45_scv1_source_concept_coverage_audit as scv1  # noqa: E402

PHASE = "4.5-SCV2-A1"
PHASE_TITLE = "Post-expansion Audit, Route Decision, and Durable ChatGPT Review Pack Policy"
PHASE_SLUG = "phase-4.5-scv2-a1-post-expansion-audit-route-decision"
BRANCH = "codex/phase45-scv2-a1-post-expansion-audit-route-decision"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
POLICY_DOC = ROOT / "docs" / "chatgpt-review-pack-policy.md"
PX1_SLUG = "phase-4.5-px1-pixiv-metadata-dedup-dry-run"

FINAL_ROUTE_DECISION_STATUS = "provisional_pending_chatgpt_pack_audit"
VISIBLE_STATUSES = ("active", "needs_review")
ACTIVE_STATUSES = ("active",)
VISIBLE_OR_HIDDEN_STATUSES = ("active", "needs_review", "superseded", "rejected", "ambiguous", "weak", "hidden")

SCV1_SUMMARY_JSON = ROOT / "docs" / "reports" / "phase-4.5-scv1-source-concept-coverage-audit-summary.json"
P0_SUMMARY_JSON = ROOT / "docs" / "reports" / "phase-4.5-scv2-p0-controlled-medium-expansion-policy-summary.json"
E1_SUMMARY_JSON = ROOT / "docs" / "reports" / "phase-4.5-scv2-e1-medium-import-ai-tag-completion-summary.json"
PX1_SUMMARY_JSON = ROOT / "docs" / "reports" / "phase-4.5-px1-pixiv-metadata-dedup-dry-run-summary.json"
R1_SUMMARY_JSON = ROOT / "docs" / "reports" / "phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json"

R1_TRUSTED_TRANSITION = {
    "source": "docs/current-handoff.md and docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md",
    "trusted_transition": {
        "total_source_concepts_before": 4214,
        "total_source_concepts_after": 6094,
        "active_source_concepts_before": 355,
        "active_source_concepts_after": 1078,
        "needs_review_source_concepts_before": 760,
        "needs_review_source_concepts_after": 1809,
        "px1_influenced_concepts_after": 1692,
    },
    "final_current_head_execute_rerun": {
        "total_source_concepts_before": 6094,
        "total_source_concepts_after": 6094,
        "active_source_concepts_before": 1078,
        "active_source_concepts_after": 1078,
        "needs_review_source_concepts_before": 1809,
        "needs_review_source_concepts_after": 1809,
        "delta": 0,
    },
}

REQUIRED_PRIVATE_ARTIFACTS = (
    "db-identity.json",
    "transaction-readonly-proof.json",
    "current-media-source-baseline.json",
    "source-metadata-coverage.json",
    "source-concept-current-state.json",
    "source-concept-gap-audit.json",
    "source-concept-gap-vs-scv1.json",
    "r1-transition-interpretation.json",
    "search-seed-symmetry-audit.json",
    "search-seed-asymmetry-examples-private.jsonl",
    "needs-review-triage-audit.json",
    "px1-evidence-impact-audit.json",
    "route-decision-matrix.json",
    "blocker-thresholds.json",
    "mutation-proof.json",
    "public-redaction-check.txt",
)

REVIEW_PACK_SAMPLE_FILES = (
    "gap-bucket-samples.jsonl",
    "search-seed-asymmetry-samples.jsonl",
    "needs-review-samples.jsonl",
    "px1-influenced-concept-samples.jsonl",
    "source-tag-unlinked-samples.jsonl",
    "source-assertion-unlinked-samples.jsonl",
    "same-alias-split-samples.jsonl",
    "same-display-context-split-samples.jsonl",
    "route-decision-evidence-samples.jsonl",
)

SUMMARY_REQUIRED_FIELDS = {
    "phase",
    "title",
    "branch",
    "generated_at",
    "report_provenance",
    "db_identity",
    "transaction_readonly_proof",
    "durable_review_pack_policy",
    "current_baseline",
    "source_metadata_coverage",
    "source_concept_current_state",
    "r1_transition_interpretation",
    "gap_audit",
    "gap_vs_scv1",
    "search_seed_symmetry",
    "needs_review_triage",
    "px1_evidence_impact",
    "route_decision_matrix",
    "runner_report_recommendation",
    "final_route_decision_status",
    "recommended_next_phase",
    "entity_bridge_blockers",
    "px1_b_decision",
    "provider2_decision",
    "scale_up_decision",
    "dedup1_decision",
    "chatgpt_review_pack",
    "mutation_proof",
    "public_redaction",
    "validation",
    "safety",
    "artifact_lifecycle",
    "private_artifacts",
}

FORBIDDEN_TABLES = tuple(
    dict.fromkeys(
        list(scv1.FORBIDDEN_TABLES)
        + [
            "blombooru_scan_jobs",
            "blombooru_scan_job_media",
            "blombooru_ai_tag_jobs",
            "blombooru_classification_jobs",
            "blombooru_tag_translation_jobs",
            "blombooru_entity_external_identities",
            "blombooru_entity_translations",
            "blombooru_negative_lookup_cache",
            "blombooru_source_concept_resolution_runs",
        ]
    )
)

SCV1_SEED_GROUPS = {
    "nahida_prompt_and_doc1": [
        "Nahida",
        "\u7eb3\u897f\u59b2",
        "\u8349\u795e",
        "nahida_(genshin_impact)",
        "\u7efe\u5ba0\u30bf\u6fe1\u778f",
        "\u947d\u592c\ue5a3",
    ],
    "kamisato_ayaka": ["Kamisato Ayaka", "kamisato_ayaka", "\u795e\u91cc\u7dbe\u83ef", "\u7ec1\u70ba\u5677\u7f0d\u6371\u5f72"],
    "nilou": ["Nilou", "nilou_(genshin_impact)", "\u59ae\u9732", "\u6fde\ue7c1\u6e36", "\u9289\u30cb\u3002\u5045\u9289\ue5dc\u504a"],
    "barbara": ["Barbara", "barbara_(genshin_impact)", "\u30d0\u30fc\u30d0\u30e9", "\u9289\u611c\u3002\u5171\u9289\u611c\u3002\u5125"],
    "mona": ["Mona", "mona_(genshin_impact)", "\u30e2\u30ca", "\u9289\ue760\u3001\u5115"],
    "2b": ["2B", "2b_(nier_automata)", "\u30e8\u30eb\u30cf\u4e8c\u53f7B\u578b", "\u9289\u30e8\u3002\u5137\u9289\u5fd2\u4e8c\u53f7B\u5a9a"],
}
PUBLIC_SEED_LABEL_ALLOWLIST = frozenset(
    scv1.normalize_source_text(seed)
    for seeds in SCV1_SEED_GROUPS.values()
    for seed in seeds
    if scv1.normalize_source_text(seed)
)

SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+\-/]{8,}|"
    r"authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+\-/]{8,}|"
    r"(?:access|refresh)[_-]?token\s*[=:]\s*\S+|"
    r"(?:authorization|cookie|api[_-]?key|password|secret)\s*[=:]\s*\S+|"
    r"sk-[A-Za-z0-9_-]{12,})"
)
LOCAL_PATH_RE = re.compile(
    r"(?i)((?<![A-Za-z])[A-Z]:[\\/]|file://|\\\\|/(?:Users|home|mnt|Volumes|workspace|tmp|storage|media|original|thumbnails|thumbs)(?:/|$)|\\Users\\)"
)
MEDIA_FILENAME_RE = re.compile(r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,120}\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)\b")
PRIVATE_JSON_KEY_RE = re.compile(
    r'"(?:media_id|concept_id|source_metadata_record_id|source_tag_observation_id|source_name_observation_id|assertion_id|raw_name|raw_tag|raw_input|filename|path|source_url)"\s*:'
)
OPAQUE_LABEL_REF_RE = re.compile(r"^label_ref_\d{6}$")
FIXED_SALT_REF_RE = re.compile(r"\b(?:label|concept|media|source_metadata)_[0-9a-f]{16}\b", re.IGNORECASE)


class A1BlockedError(RuntimeError):
    """Raised when A1 cannot continue safely."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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


def root_relative_or_name(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def stable_private_ref(kind: str, value: Any) -> str:
    digest = hashlib.sha256(f"{kind}:{value}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{kind}_private_{digest}"


class ReviewPackRefBuilder:
    """Per-pack opaque refs; no raw mapping or reversible salt is written."""

    def __init__(self) -> None:
        self._refs: dict[str, dict[str, str]] = defaultdict(dict)
        self._counts: Counter[str] = Counter()

    def ref(self, kind: str, value: Any) -> str:
        text_value = scv1.normalize_source_text(value) if kind == "label" else str(value or "").strip()
        if not text_value:
            return ""
        if text_value not in self._refs[kind]:
            self._counts[kind] += 1
            self._refs[kind][text_value] = f"{kind}_ref_{self._counts[kind]:06d}"
        return self._refs[kind][text_value]

    def label(self, value: Any, *, allow_public_seed: bool = False) -> str:
        text_value = scv1.normalize_source_text(value)
        if not text_value:
            return ""
        if allow_public_seed and text_value in PUBLIC_SEED_LABEL_ALLOWLIST and not scan_text_for_review_pack_leaks(text_value, include_private_keys=False):
            return text_value
        return self.ref("label", text_value)


def safe_label(value: Any, *, fallback: str = "[redacted]", allow_public_seed: bool = False) -> str:
    text_value = scv1.normalize_source_text(value)
    if not text_value:
        return ""
    if allow_public_seed and text_value in PUBLIC_SEED_LABEL_ALLOWLIST and not scan_text_for_review_pack_leaks(text_value, include_private_keys=False):
        return text_value
    return fallback


def is_redacted_sample_label(value: Any, *, allow_public_seed: bool = False) -> bool:
    text_value = scv1.normalize_source_text(value)
    if not text_value:
        return True
    if allow_public_seed and text_value in PUBLIC_SEED_LABEL_ALLOWLIST:
        return True
    return text_value.startswith("[redacted") or bool(OPAQUE_LABEL_REF_RE.fullmatch(text_value))


def sample_sequence_ref(bucket: str, index: int) -> str:
    return f"{bucket}_{index:03d}"


def forbid_review_pack_raw_label_field(key: str, value: Any) -> str | None:
    if key == "display_label" and not is_redacted_sample_label(value):
        return "display_label_raw_private_label"
    if key == "search_seed_label" and not is_redacted_sample_label(value, allow_public_seed=True):
        return "search_seed_label_raw_private_label"
    return None


def scan_json_payload_for_review_pack_leaks(payload: Any, *, path: str = "$") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            if key_text == "canonical_key_hash":
                findings.append({"type": "unsalted_or_dictionary_attackable_key_hash", "match": path})
            label_finding = forbid_review_pack_raw_label_field(key_text, value)
            if label_finding:
                findings.append({"type": label_finding, "match": f"{path}.{key_text}"})
            findings.extend(scan_json_payload_for_review_pack_leaks(value, path=f"{path}.{key_text}"))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(scan_json_payload_for_review_pack_leaks(item, path=f"{path}[{index}]"))
    elif isinstance(payload, str):
        match = FIXED_SALT_REF_RE.search(payload)
        if match:
            findings.append({"type": "fixed_salt_or_hash_ref", "match": f"{path}:{match.group(0)}"})
    return findings


def scan_json_file_for_review_pack_leaks(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    findings: list[dict[str, str]] = []
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [{"type": "invalid_json_in_review_pack", "match": f"{path.name}:{exc.lineno}"}]
        return scan_json_payload_for_review_pack_leaks(payload)
    if suffix == ".jsonl":
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                findings.append({"type": "invalid_jsonl_in_review_pack", "match": f"{path.name}:{line_number}:{exc.colno}"})
                continue
            for finding in scan_json_payload_for_review_pack_leaks(payload):
                findings.append({**finding, "match": f"line {line_number}:{finding['match']}"})
    return findings


def rows_dict_expanding(conn: Connection, sql: str, params: Mapping[str, Any], expanding: Sequence[str]) -> list[dict[str, Any]]:
    stmt = text(sql)
    for name in expanding:
        stmt = stmt.bindparams(bindparam(name, expanding=True))
    return [dict(row) for row in conn.execute(stmt, params).mappings().all()]


def public_db_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "violet_env": identity.get("violet_env"),
        "database": identity.get("database"),
        "connected_database": identity.get("connected_database"),
        "connected_user": identity.get("connected_user"),
        "host": identity.get("host"),
        "port": identity.get("port"),
        "server_port": identity.get("server_port"),
        "transaction_read_only": identity.get("transaction_read_only"),
        "transaction_read_only_ok": identity.get("transaction_read_only_ok"),
        "git_branch": identity.get("git_branch"),
        "git_sha": identity.get("git_sha"),
        "python_executable": Path(str(identity.get("python_executable") or sys.executable)).name,
        "python_executable_path_redacted": True,
        "db_resolution": {
            "app_compatible": identity.get("db_resolution", {}).get("app_compatible"),
            "settings_json_exists": identity.get("db_resolution", {}).get("settings_json_exists"),
            "database_file_settings_used": identity.get("db_resolution", {}).get("database_file_settings_used"),
            "field_sources": identity.get("db_resolution", {}).get("field_sources"),
            "password_present": identity.get("db_resolution", {}).get("password_present"),
            "password_value_recorded": False,
            "urls_match": identity.get("db_resolution", {}).get("urls_match"),
            "runner_matches_app_equivalent": identity.get("db_resolution", {}).get("runner_matches_app_equivalent"),
        },
        "recorded_at": identity.get("recorded_at"),
    }


def build_table_counts(conn: Connection) -> dict[str, Any]:
    return {
        "tables": {table: scv1.count_table(conn, table) for table in FORBIDDEN_TABLES},
        "recorded_at": utc_now_iso(),
    }


def compare_table_counts(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    changed = []
    missing = []
    for table in FORBIDDEN_TABLES:
        left = before.get("tables", {}).get(table, {})
        right = after.get("tables", {}).get(table, {})
        if left.get("status") == "missing_table" or right.get("status") == "missing_table":
            missing.append(table)
            continue
        if left.get("count") != right.get("count"):
            changed.append({"table": table, "before": left.get("count"), "after": right.get("count")})
    return {
        "passed": not changed,
        "changed_tables": changed,
        "missing_tables": missing,
        "checked_tables": list(FORBIDDEN_TABLES),
        "recorded_at": utc_now_iso(),
    }


def transaction_readonly_proof(db_identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "passed": bool(db_identity.get("transaction_read_only_ok")),
        "transaction_read_only": db_identity.get("transaction_read_only"),
        "required": "on",
        "enforced_by": "BEGIN TRANSACTION READ ONLY",
        "no_execute_or_write_flags": True,
    }


def validate_summary_schema(summary: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(SUMMARY_REQUIRED_FIELDS.difference(summary))
    return {"passed": not missing, "missing_fields": missing, "required_fields": sorted(SUMMARY_REQUIRED_FIELDS)}


def build_current_baseline(media: Mapping[str, Any], source_layer: Mapping[str, Any]) -> dict[str, Any]:
    source_records = source_layer.get("source_records", {})
    return {
        "total_media": media.get("total_media"),
        "eligible_media": media.get("eligible_media_count"),
        "eligible_policy": media.get("eligible_policy"),
        "eligible_ai_tag_coverage": {
            "covered": media.get("eligible_media_with_ai_tag_provenance"),
            "total": media.get("eligible_media_count"),
            "percent": media.get("eligible_ai_tag_provenance_pct"),
        },
        "source_metadata_records": source_records.get("total_rows"),
        "source_metadata_linked_to_media": source_records.get("linked_to_media"),
        "distinct_media_with_source_metadata": source_records.get("distinct_media"),
        "distinct_eligible_media_with_source_metadata": source_records.get("distinct_eligible_media"),
        "source_metadata_coverage_percent": source_records.get("distinct_eligible_media_pct"),
        "pixiv_source_metadata_rows": (source_records.get("by_provider") or {}).get("pixiv", 0),
        "source_records_linked_to_media": source_records.get("linked_to_media"),
        "source_layer_media_with_signals": media.get("media_with_source_layer_signals"),
        "source_concept_media_with_evidence_or_links": media.get("media_with_source_concept_evidence_or_links"),
    }


def build_source_metadata_coverage(conn: Connection, source_layer: Mapping[str, Any]) -> dict[str, Any]:
    source_records = source_layer.get("source_records", {})
    tag_provider = scv1.group_count(conn, "blombooru_source_tag_observations", "provider")
    tag_category_status = rows_by_keys(
        conn,
        "blombooru_source_tag_observations",
        ("provider", "source_category_raw", "status"),
        order_limit=100,
    )
    name_provider = scv1.group_count(conn, "blombooru_source_name_observations", "provider")
    name_role_status = rows_by_keys(
        conn,
        "blombooru_source_name_observations",
        ("provider", "name_role", "status"),
        order_limit=100,
    )
    assertion_provider = scv1.group_count(conn, "blombooru_source_searchable_name_assertions", "provider")
    assertion_role_status = rows_by_keys(
        conn,
        "blombooru_source_searchable_name_assertions",
        ("provider", "asserted_role", "status"),
        order_limit=100,
    )
    return {
        "source_metadata_records_total": source_records.get("total_rows"),
        "source_metadata_records_linked_to_media": source_records.get("linked_to_media"),
        "source_metadata_distinct_media_count": source_records.get("distinct_media"),
        "source_metadata_distinct_eligible_media_count": source_records.get("distinct_eligible_media"),
        "source_metadata_distinct_media_pct": source_records.get("distinct_media_pct"),
        "source_metadata_distinct_eligible_media_pct": source_records.get("distinct_eligible_media_pct"),
        "source_metadata_by_provider": source_records.get("by_provider"),
        "source_metadata_by_status": source_records.get("by_status"),
        "pixiv_source_metadata_rows": (source_records.get("by_provider") or {}).get("pixiv", 0),
        "pixiv_source_tag_observations": tag_provider.get("pixiv", 0),
        "pixiv_source_name_observations": name_provider.get("pixiv", 0),
        "pixiv_source_assertions": assertion_provider.get("pixiv", 0),
        "source_tag_observations_by_provider": tag_provider,
        "source_tag_observations_by_provider_category_status": tag_category_status,
        "source_name_observations_by_provider": name_provider,
        "source_name_observations_by_provider_role_status": name_role_status,
        "source_assertions_by_provider": assertion_provider,
        "source_assertions_by_provider_role_status": assertion_role_status,
        "source_assertions_by_status": scv1.group_count(conn, "blombooru_source_searchable_name_assertions", "status"),
        "coverage_denominator_policy": "distinct eligible media, not raw metadata row count",
    }


def rows_by_keys(conn: Connection, table_name: str, keys: Sequence[str], *, order_limit: int = 50) -> list[dict[str, Any]]:
    if not scv1.table_exists(conn, table_name):
        return []
    for key in keys:
        if not scv1.column_exists(conn, table_name, key):
            return []
    select_parts = [f"COALESCE(CAST({scv1.qident(key)} AS TEXT), '<null>') AS {scv1.qident(key)}" for key in keys]
    group_parts = [scv1.qident(key) for key in keys]
    rows = scv1.rows_dict(
        conn,
        f"""
        SELECT {", ".join(select_parts)}, COUNT(*) AS count
        FROM {scv1.qident(table_name)}
        GROUP BY {", ".join(group_parts)}
        ORDER BY count DESC
        LIMIT {int(order_limit)}
        """,
    )
    return [{**{key: row.get(key) for key in keys}, "count": int(row.get("count") or 0)} for row in rows]


def build_source_concept_current_state(
    conn: Connection,
    concepts: Sequence[Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    scv1_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    by_status = Counter(str(row.get("status") or "<null>") for row in concepts)
    hidden = {status: by_status.get(status, 0) for status in ("rejected", "ambiguous", "superseded", "weak", "hidden")}
    px1_strict_ids = px1_strict_influenced_concept_ids(conn)
    pixiv_all_ids = pixiv_all_influenced_concept_ids(conn)
    visible_with_media = {
        int(row["id"])
        for row in concepts
        if row.get("status") in VISIBLE_STATUSES and len(scv1.concept_media_set_for_ids(conn, [int(row["id"])])) > 0
    }
    all_ids = {int(row["id"]) for row in concepts}
    visible_ids = {int(row["id"]) for row in concepts if row.get("status") in VISIBLE_STATUSES}
    alias_groups = alias_group_counts(aliases)
    active_needs_alias = active_needs_review_alias_overlap(aliases, concepts)
    undermerge_groups = alias_groups["same_alias_key_group_count"] + alias_groups["same_display_name_group_count"]
    overmerge_groups = overmerge_risk_groups(concepts, aliases, evidence)
    weak_ai_general = weak_ai_or_general_concepts(concepts, evidence)
    return {
        "total_source_concepts": len(concepts),
        "by_status": dict(by_status),
        "active": by_status.get("active", 0),
        "needs_review": by_status.get("needs_review", 0),
        "superseded": by_status.get("superseded", 0),
        "rejected": by_status.get("rejected", 0),
        "ambiguous": by_status.get("ambiguous", 0),
        "weak": by_status.get("weak", 0),
        "hidden": by_status.get("hidden", 0),
        "hidden_status_counts": hidden,
        "source_concept_signal_count_by_origin_provider_status": rows_by_keys(
            conn,
            "blombooru_source_concept_signals",
            ("origin_type", "provider", "status"),
            order_limit=150,
        ),
        "source_concept_signal_count_by_origin_provider_status_total": scv1.count_table(conn, "blombooru_source_concept_signals").get("count"),
        "source_concept_alias_count_by_role_status_provider": alias_count_by_role_status_provider(conn),
        "source_concept_alias_total": len(aliases),
        "source_concept_evidence_count_by_type_status_provider": rows_by_keys(
            conn,
            "blombooru_source_concept_evidence",
            ("evidence_type", "status", "provider"),
            order_limit=150,
        ),
        "source_concept_evidence_total": len(evidence),
        "source_concept_search_index_count_by_status": scv1.group_count(conn, "blombooru_source_concept_search_index", "status"),
        "source_concept_search_index_total": scv1.count_table(conn, "blombooru_source_concept_search_index").get("count"),
        "concepts_influenced_by_px1_evidence": len(px1_strict_ids),
        "concepts_influenced_by_px1_evidence_scope": f"strict PX1 SourceMetadataRecord run_label={PX1_SLUG}",
        "concepts_influenced_by_strict_px1_evidence": len(px1_strict_ids),
        "concepts_influenced_by_all_pixiv_evidence": len(pixiv_all_ids),
        "concepts_influenced_by_non_px1_pixiv_evidence": len(pixiv_all_ids - px1_strict_ids),
        "concepts_with_media": len(visible_with_media),
        "concepts_with_media_status_scope": "visible_statuses_active_or_needs_review",
        "concepts_without_media": max(len(visible_ids) - len(visible_with_media), 0),
        "concepts_without_media_status_scope": "visible_statuses_active_or_needs_review",
        "all_status_source_concept_count": len(all_ids),
        "visible_status_source_concept_count": len(visible_ids),
        "concepts_with_only_weak_ai_general_evidence": weak_ai_general,
        "active_concepts_sharing_aliases_with_needs_review": active_needs_alias["active_concepts_sharing_aliases_with_needs_review"],
        "needs_review_concepts_sharing_aliases_with_active": active_needs_alias["needs_review_concepts_sharing_aliases_with_active"],
        "duplicate_fragment_candidate_groups": undermerge_groups,
        "overmerge_risk_groups": len(overmerge_groups),
        "undermerge_risk_groups": undermerge_groups,
        "context_conflict_groups": alias_groups["same_display_name_group_count"],
        "same_alias_key_group_count": alias_groups["same_alias_key_group_count"],
        "same_display_name_group_count": alias_groups["same_display_name_group_count"],
        "source_scv1_inventory_public_fields": {
            "ai_only_concept_count": scv1_inventory.get("ai_only_concept_count"),
            "source_title_only_concept_count": scv1_inventory.get("source_title_only_concept_count"),
            "weak_only_concept_count": scv1_inventory.get("weak_only_concept_count"),
        },
    }


def alias_count_by_role_status_provider(conn: Connection) -> list[dict[str, Any]]:
    if not scv1.table_exists(conn, "blombooru_source_concept_aliases"):
        return []
    if not scv1.table_exists(conn, "blombooru_source_concept_signals"):
        return rows_by_keys(conn, "blombooru_source_concept_aliases", ("alias_role", "status"), order_limit=100)
    return scv1.rows_dict(
        conn,
        """
        SELECT COALESCE(a.alias_role, '<null>') AS alias_role,
               COALESCE(a.status, '<null>') AS status,
               COALESCE(s.provider, '<null>') AS provider,
               COUNT(*) AS count
        FROM blombooru_source_concept_aliases a
        LEFT JOIN blombooru_source_concept_signals s ON s.id = a.source_signal_id
        GROUP BY 1, 2, 3
        ORDER BY count DESC
        LIMIT 150
        """,
    )


def pixiv_all_influenced_concept_ids(conn: Connection) -> set[int]:
    if not scv1.table_exists(conn, "blombooru_source_concept_evidence") or not scv1.table_exists(conn, "blombooru_source_metadata_records"):
        return set()
    rows = scv1.rows_dict(
        conn,
        """
        SELECT DISTINCT e.concept_id
        FROM blombooru_source_concept_evidence e
        JOIN blombooru_source_metadata_records r ON r.id = e.source_metadata_record_id
        WHERE e.concept_id IS NOT NULL
          AND LOWER(COALESCE(r.provider, '')) = 'pixiv'
        """,
    )
    return {int(row["concept_id"]) for row in rows if row.get("concept_id") is not None}


def strict_px1_source_metadata_record_ids(conn: Connection, px1_slug: str = PX1_SLUG) -> set[int]:
    if not scv1.table_exists(conn, "blombooru_source_metadata_records"):
        return set()
    if not scv1.column_exists(conn, "blombooru_source_metadata_records", "run_label"):
        return set()
    rows = scv1.rows_dict(
        conn,
        """
        SELECT id
        FROM blombooru_source_metadata_records
        WHERE LOWER(COALESCE(provider, '')) = 'pixiv'
          AND run_label = :px1_slug
        """,
        {"px1_slug": px1_slug},
    )
    return {int(row["id"]) for row in rows if row.get("id") is not None}


def concepts_with_source_metadata_record_ids(conn: Connection, source_metadata_record_ids: Iterable[int]) -> set[int]:
    ids = sorted({int(value) for value in source_metadata_record_ids})
    if not ids or not scv1.table_exists(conn, "blombooru_source_concept_evidence"):
        return set()
    rows = rows_dict_expanding(
        conn,
        """
        SELECT DISTINCT concept_id
        FROM blombooru_source_concept_evidence
        WHERE concept_id IS NOT NULL
          AND source_metadata_record_id IN :source_metadata_record_ids
        """,
        {"source_metadata_record_ids": ids},
        expanding=("source_metadata_record_ids",),
    )
    return {int(row["concept_id"]) for row in rows if row.get("concept_id") is not None}


def px1_strict_influenced_concept_ids(conn: Connection) -> set[int]:
    return concepts_with_source_metadata_record_ids(conn, strict_px1_source_metadata_record_ids(conn))


def alias_group_counts(aliases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    by_key: dict[str, set[int]] = defaultdict(set)
    by_display: dict[str, set[int]] = defaultdict(set)
    for row in aliases:
        if row.get("status") not in VISIBLE_STATUSES:
            continue
        concept_id = int(row["concept_id"])
        key = str(row.get("alias_key") or "")
        display = scv1.canonical_source_key(row.get("display_name") or row.get("alias_value") or key)
        if key:
            by_key[key].add(concept_id)
        if display:
            by_display[display].add(concept_id)
    return {
        "same_alias_key_group_count": sum(1 for ids in by_key.values() if len(ids) > 1),
        "same_display_name_group_count": sum(1 for ids in by_display.values() if len(ids) > 1),
    }


def active_needs_review_alias_overlap(aliases: Sequence[Mapping[str, Any]], concepts: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    status_by_id = {int(row["id"]): str(row.get("status") or "") for row in concepts}
    key_status_ids: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for row in aliases:
        if row.get("status") not in VISIBLE_STATUSES:
            continue
        key = str(row.get("alias_key") or "")
        if not key:
            continue
        concept_id = int(row["concept_id"])
        key_status_ids[key][status_by_id.get(concept_id, "")].add(concept_id)
    active_ids: set[int] = set()
    needs_ids: set[int] = set()
    for status_ids in key_status_ids.values():
        if status_ids.get("active") and status_ids.get("needs_review"):
            active_ids.update(status_ids["active"])
            needs_ids.update(status_ids["needs_review"])
    return {
        "active_concepts_sharing_aliases_with_needs_review": len(active_ids),
        "needs_review_concepts_sharing_aliases_with_active": len(needs_ids),
    }


def weak_ai_or_general_concepts(concepts: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]) -> int:
    by_concept: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in evidence:
        if row.get("concept_id") is not None:
            by_concept[int(row["concept_id"])].append(row)
    count = 0
    for concept in concepts:
        rows = by_concept.get(int(concept["id"]), [])
        if not rows:
            continue
        text_blob = " ".join(
            f"{row.get('provider') or ''} {row.get('evidence_type') or ''} {row.get('evidence_strength') or ''}"
            for row in rows
        ).casefold()
        strengths = {str(row.get("evidence_strength") or "").casefold() for row in rows}
        if ("ai" in text_blob or "wd" in text_blob or "general" in text_blob) and not {"strong", "high"}.intersection(strengths):
            count += 1
    return count


def overmerge_risk_groups(
    concepts: Sequence[Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    alias_counts = Counter(int(row["concept_id"]) for row in aliases if row.get("concept_id") is not None)
    evidence_counts = Counter(int(row["concept_id"]) for row in evidence if row.get("concept_id") is not None)
    rows = []
    for concept in concepts:
        concept_id = int(concept["id"])
        media_count = int(concept.get("media_count") or 0)
        if concept.get("status") in VISIBLE_STATUSES and media_count >= 25 and alias_counts[concept_id] >= 8 and evidence_counts[concept_id] >= 20:
            rows.append(
                {
                    "stable_private_concept_ref": stable_private_ref("concept", concept_id),
                    "status": concept.get("status"),
                    "media_count": media_count,
                    "alias_count": alias_counts[concept_id],
                    "evidence_count": evidence_counts[concept_id],
                }
            )
    return rows[:50]


def build_gap_vs_scv1(current_gap: Mapping[str, Any], scv1_summary: Mapping[str, Any]) -> dict[str, Any]:
    scv1_gap = scv1_summary.get("alias_gap_analysis") or {}
    before_buckets = scv1_gap.get("gap_buckets") or {}
    current_buckets = current_gap.get("gap_buckets") or {}
    all_keys = sorted(set(before_buckets) | set(current_buckets))
    bucket_rows = []
    improved = []
    regressed = []
    not_comparable = []
    for key in all_keys:
        before = before_buckets.get(key)
        after = current_buckets.get(key)
        denominator_before = (scv1_gap.get("gap_bucket_details") or {}).get(key, {}).get("total_distinct_keys")
        denominator_after = (current_gap.get("gap_bucket_details") or {}).get(key, {}).get("total_distinct_keys")
        comparable = before is not None and after is not None and denominator_before == denominator_after
        delta = int(after or 0) - int(before or 0)
        row = {
            "bucket": key,
            "scv1_count": before,
            "current_count": after,
            "delta": delta,
            "scv1_denominator": denominator_before,
            "current_denominator": denominator_after,
            "comparable": comparable,
            "comparison_note": "same denominator" if comparable else "denominator changed or field absent; compare direction cautiously",
        }
        bucket_rows.append(row)
        if not comparable:
            not_comparable.append(key)
        elif delta < 0:
            improved.append(key)
        elif delta > 0:
            regressed.append(key)
    return {
        "scv1_total_gap_signals": scv1_gap.get("total_gap_signals"),
        "current_total_gap_signals": current_gap.get("total_gap_signals"),
        "total_gap_delta": int(current_gap.get("total_gap_signals") or 0) - int(scv1_gap.get("total_gap_signals") or 0),
        "bucket_comparisons": bucket_rows,
        "improved_buckets": improved,
        "regressed_buckets": regressed,
        "not_comparable_buckets": not_comparable,
        "denominator_policy": "Only buckets with the same denominator are treated as directly comparable.",
    }


def build_dynamic_px1_seed_groups(conn: Connection) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    if scv1.table_exists(conn, "blombooru_source_name_observations"):
        name_rows = scv1.rows_dict(
            conn,
            """
            SELECT MIN(raw_name) AS label, canonical_name_key, name_role, COUNT(*) AS count
            FROM blombooru_source_name_observations
            WHERE provider = 'pixiv'
              AND canonical_name_key IS NOT NULL
            GROUP BY canonical_name_key, name_role
            ORDER BY count DESC
            LIMIT 12
            """,
        )
        groups["px1_high_frequency_source_names_private"] = [str(row.get("label") or row.get("canonical_name_key")) for row in name_rows[:8]]
        groups["px1_ambiguous_short_names_private"] = [
            str(row.get("label") or row.get("canonical_name_key"))
            for row in name_rows
            if len(str(row.get("canonical_name_key") or "")) <= 3
        ][:8]
    if scv1.table_exists(conn, "blombooru_source_tag_observations"):
        tag_rows = scv1.rows_dict(
            conn,
            """
            SELECT MIN(raw_tag) AS label, canonical_tag_key, COUNT(*) AS count
            FROM blombooru_source_tag_observations
            WHERE provider = 'pixiv'
              AND canonical_tag_key IS NOT NULL
            GROUP BY canonical_tag_key
            ORDER BY count DESC
            LIMIT 10
            """,
        )
        groups["px1_high_frequency_source_tags_private"] = [str(row.get("label") or row.get("canonical_tag_key")) for row in tag_rows[:8]]
    if scv1.table_exists(conn, "blombooru_source_searchable_name_assertions"):
        assertion_rows = scv1.rows_dict(
            conn,
            """
            SELECT MIN(COALESCE(asserted_name, raw_input)) AS label, canonical_name_key, asserted_role, COUNT(*) AS count
            FROM blombooru_source_searchable_name_assertions
            WHERE provider = 'pixiv'
              AND asserted_role IN ('work_title', 'source_title', 'copyright', 'character', 'artist')
              AND canonical_name_key IS NOT NULL
            GROUP BY canonical_name_key, asserted_role
            ORDER BY count DESC
            LIMIT 10
            """,
        )
        groups["px1_title_or_work_assertions_private"] = [str(row.get("label") or row.get("canonical_name_key")) for row in assertion_rows[:8]]
    return {key: [value for value in values if value] for key, values in groups.items() if values}


def evaluate_seed_groups(conn: Connection, seed_groups: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    private_examples: list[dict[str, Any]] = []
    reason_buckets: Counter[str] = Counter()
    total_seeds = 0
    matched_seeds = 0
    unmatched_seeds = 0
    symmetric_groups = 0
    asymmetric_groups = 0
    jaccard_values: list[float] = []
    for group_name, seeds in seed_groups.items():
        seed_rows = []
        media_sets = []
        active_media_sets = []
        group_reasons: Counter[str] = Counter()
        for seed in seeds:
            total_seeds += 1
            visible_ids, hidden_rows = scv1.concept_ids_for_term(conn, seed, statuses=VISIBLE_STATUSES)
            active_ids, _active_hidden = scv1.concept_ids_for_term(conn, seed, statuses=ACTIVE_STATUSES)
            visible_media = scv1.concept_media_set_for_ids(conn, visible_ids, statuses=VISIBLE_STATUSES)
            active_media = scv1.concept_media_set_for_ids(conn, active_ids, statuses=ACTIVE_STATUSES)
            if visible_ids:
                matched_seeds += 1
            else:
                unmatched_seeds += 1
                group_reasons["unmatched_alias"] += 1
            if visible_ids and not active_ids:
                group_reasons["needs_review_not_included_in_active_search"] += 1
            if len(visible_ids) > 1:
                group_reasons["concept_split"] += 1
            if hidden_rows:
                group_reasons["hidden_or_superseded_raw_match"] += 1
            seed_rows.append(
                {
                    "seed_label_private": seed,
                    "seed_label_public": safe_label(seed, fallback="[redacted seed]", allow_public_seed=True),
                    "matched": bool(visible_ids),
                    "active_matched": bool(active_ids),
                    "matched_concept_count": len(visible_ids),
                    "active_concept_count": len(active_ids),
                    "visible_media_count": len(visible_media),
                    "active_media_count": len(active_media),
                    "hidden_raw_match_count": len(hidden_rows),
                    "stable_private_concept_refs": [stable_private_ref("concept", value) for value in visible_ids[:12]],
                }
            )
            media_sets.append(visible_media)
            active_media_sets.append(active_media)
        nonempty_sets = [item for item in media_sets if item]
        symmetric_media = bool(media_sets) and len({tuple(sorted(item)) for item in media_sets}) <= 1
        all_matched = all(row["matched"] for row in seed_rows)
        active_contrast = any(row["matched"] and not row["active_matched"] for row in seed_rows)
        if len(nonempty_sets) >= 2:
            base = nonempty_sets[0]
            for other in nonempty_sets[1:]:
                union = base | other
                jaccard_values.append(round(len(base & other) / len(union), 4) if union else 1.0)
        if not symmetric_media and all_matched:
            group_reasons["media_result_mismatch"] += 1
        if not all_matched:
            group_reasons["missing_alias_or_unmatched_seed"] += 1
        if active_contrast:
            group_reasons["active_only_vs_needs_review_contrast"] += 1
        is_symmetric = symmetric_media and all_matched and not active_contrast
        if is_symmetric:
            symmetric_groups += 1
        else:
            asymmetric_groups += 1
            if not group_reasons:
                group_reasons["search_service_behavior"] += 1
        reason_buckets.update(group_reasons)
        groups[group_name] = {
            "seed_count": len(seeds),
            "matched_seed_count": sum(1 for row in seed_rows if row["matched"]),
            "unmatched_seed_count": sum(1 for row in seed_rows if not row["matched"]),
            "symmetric": is_symmetric,
            "symmetric_media_result": symmetric_media,
            "active_only_vs_include_needs_review_contrast": active_contrast,
            "asymmetry_reasons": dict(group_reasons),
            "seed_rows_public": [
                {
                    "seed_label": row["seed_label_public"],
                    "matched": row["matched"],
                    "active_matched": row["active_matched"],
                    "matched_concept_count": row["matched_concept_count"],
                    "visible_media_count": row["visible_media_count"],
                    "active_media_count": row["active_media_count"],
                }
                for row in seed_rows
            ],
        }
        if not is_symmetric:
            for row in seed_rows:
                private_examples.append(
                    {
                        "group": group_name,
                        "seed_label": row["seed_label_private"],
                        "matched": row["matched"],
                        "active_matched": row["active_matched"],
                        "matched_concept_count": row["matched_concept_count"],
                        "visible_media_count": row["visible_media_count"],
                        "active_media_count": row["active_media_count"],
                        "reason_bucket": ",".join(sorted(group_reasons)) or "unknown_asymmetry",
                        "stable_private_concept_refs": row["stable_private_concept_refs"],
                    }
                )
    aggregate = {
        "groups_tested": len(seed_groups),
        "seeds_tested": total_seeds,
        "matched_seeds": matched_seeds,
        "unmatched_seeds": unmatched_seeds,
        "symmetric_groups": symmetric_groups,
        "asymmetric_groups": asymmetric_groups,
        "asymmetry_reason_buckets": dict(reason_buckets),
        "unmatched_aliases_count_as_asymmetry": True,
        "media_result_overlap_metrics": {
            "pairwise_jaccard_count": len(jaccard_values),
            "average_pairwise_jaccard": round(sum(jaccard_values) / len(jaccard_values), 4) if jaccard_values else None,
            "min_pairwise_jaccard": min(jaccard_values) if jaccard_values else None,
        },
    }
    return {"aggregate": aggregate, "groups": groups, "private_examples": private_examples}


def build_search_seed_symmetry_audit(conn: Connection) -> dict[str, Any]:
    seed_groups = {**SCV1_SEED_GROUPS, **build_dynamic_px1_seed_groups(conn)}
    evaluated = evaluate_seed_groups(conn, seed_groups)
    return {
        "seed_group_source": {
            "scv1_seed_group_count": len(SCV1_SEED_GROUPS),
            "dynamic_px1_seed_group_count": len(seed_groups) - len(SCV1_SEED_GROUPS),
        },
        **evaluated,
    }


def build_px1_evidence_impact(conn: Connection, r1_summary: Mapping[str, Any]) -> dict[str, Any]:
    strict_record_ids = strict_px1_source_metadata_record_ids(conn)
    px1_strict_ids = concepts_with_source_metadata_record_ids(conn, strict_record_ids)
    pixiv_all_ids = pixiv_all_influenced_concept_ids(conn)
    r1_delta = r1_summary.get("source_concept_delta") or {}
    px1_check = r1_summary.get("px1_source_metadata_check") or {}
    return {
        "px1_slug": PX1_SLUG,
        "px1_filter_scope": "strict SourceMetadataRecord filter: provider='pixiv' and run_label equals px1_slug",
        "px1_source_metadata_record_count_strict": len(strict_record_ids),
        "px1_strict_influenced_concepts": len(px1_strict_ids),
        "pixiv_all_influenced_concepts": len(pixiv_all_ids),
        "non_px1_pixiv_influenced_concepts": len(pixiv_all_ids - px1_strict_ids),
        "route_decision_px1_impact_metric": "px1_strict_influenced_concepts",
        "r1_summary_concepts_influenced_by_px1_evidence_after": r1_delta.get("concepts_influenced_by_px1_evidence_after"),
        "r1_summary_concepts_newly_influenced_by_px1_evidence_current_head_rerun": r1_delta.get("concepts_newly_influenced_by_px1_evidence"),
        "px1_records": px1_check.get("px1_source_metadata_records"),
        "px1_tags": px1_check.get("px1_source_tag_observations"),
        "px1_names": px1_check.get("px1_source_name_observations"),
        "px1_assertions": px1_check.get("px1_searchable_name_assertions"),
        "px1_assertions_review_scoped": {
            "needs_review_requires_review": px1_check.get("px1_assertions_needs_review_requires_review"),
            "searchable_active": px1_check.get("px1_assertions_searchable_active"),
            "interpretation": "PX1 assertions are evidence/backlog input, not active truth.",
        },
        "impact_interpretation": "PX1 materially expanded review-scoped evidence and SourceConcept influence, but did not make final route approval safe without sample-level review.",
    }


def build_r1_transition_interpretation(r1_summary: Mapping[str, Any]) -> dict[str, Any]:
    source_delta = r1_summary.get("source_concept_delta") or {}
    return {
        **R1_TRUSTED_TRANSITION,
        "current_r1_summary_mode": r1_summary.get("mode"),
        "current_r1_summary_source_concept_delta": {
            "total_source_concepts_delta": source_delta.get("total_source_concepts_delta"),
            "active_source_concepts_delta": source_delta.get("active_source_concepts_delta"),
            "needs_review_source_concepts_delta": source_delta.get("needs_review_source_concepts_delta"),
            "concepts_newly_influenced_by_px1_evidence": source_delta.get("concepts_newly_influenced_by_px1_evidence"),
        },
        "interpretation": (
            "R1 had a trusted transition from the pre-R1 state to the post-R1 state. "
            "The latest current-head execute rerun was idempotent over the already committed R1 state; "
            "that rerun must not be interpreted as R1 having no effect."
        ),
        "r1_had_effect": True,
        "final_rerun_idempotent": True,
    }


def build_blocker_thresholds(
    gap_audit: Mapping[str, Any],
    search_seed: Mapping[str, Any],
    needs_review: Mapping[str, Any],
    source_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    total_gap = int(gap_audit.get("total_gap_signals") or 0)
    asym = int((search_seed.get("aggregate") or {}).get("asymmetric_groups") or 0)
    unmatched = int((search_seed.get("aggregate") or {}).get("unmatched_seeds") or 0)
    needs = int(needs_review.get("total_needs_review_concepts") or 0)
    metadata_pct = float(source_metadata.get("source_metadata_distinct_eligible_media_pct") or 0)
    return {
        "entity_bridge": {
            "required_before_consideration": {
                "search_asymmetric_groups_max": 0,
                "unmatched_search_seeds_max": 0,
                "total_gap_signals_max": 100,
                "needs_review_concepts_max": 250,
                "source_assertion_unlinked_max": 0,
                "truth_path_preview_required": True,
            },
            "current_values": {
                "search_asymmetric_groups": asym,
                "unmatched_search_seeds": unmatched,
                "total_gap_signals": total_gap,
                "needs_review_concepts": needs,
            },
            "blocked": asym > 0 or unmatched > 0 or total_gap > 100 or needs > 250,
        },
        "scale_up_5k_10k": {
            "required_before_consideration": {
                "search_asymmetric_groups_max": 2,
                "total_gap_signals_max": 1000,
                "needs_review_concepts_max": 1000,
                "source_metadata_distinct_eligible_media_pct_min": 20.0,
                "ingestion_run_ledger_required": True,
            },
            "current_values": {
                "search_asymmetric_groups": asym,
                "total_gap_signals": total_gap,
                "needs_review_concepts": needs,
                "source_metadata_distinct_eligible_media_pct": metadata_pct,
            },
            "blocked": asym > 2 or total_gap > 1000 or needs > 1000 or metadata_pct < 20.0,
        },
        "provider2": {
            "required_before_consideration": {
                "taxonomy_or_alias_classification_bottleneck_evidenced": True,
                "provider_policy_required": True,
                "no_image_upload_default": True,
                "resolver_gap_dominance_checked_first": True,
            },
            "current_values": {
                "source_tag_gap": (gap_audit.get("gap_buckets") or {}).get("source_tag_present_no_source_concept_alias"),
                "source_assertion_gap": (gap_audit.get("gap_buckets") or {}).get("source_assertion_present_not_connected"),
                "resolver_gap_dominance": total_gap > 0,
            },
            "blocked": total_gap > 0,
        },
        "px1_b": {
            "required_before_consideration": {
                "metadata_coverage_is_dominant_bottleneck": True,
                "resolver_gaps_not_dominant": True,
            },
            "current_values": {
                "source_metadata_distinct_eligible_media_pct": metadata_pct,
                "total_gap_signals": total_gap,
            },
            "deferred": total_gap > 0,
        },
    }


def route_option(
    key: str,
    *,
    recommended: bool,
    priority: str,
    why: str,
    blockers: Sequence[str],
    prerequisites: Sequence[str],
    expected_value: str,
    risk: str,
    writes_db: bool,
    touches_truth_path: bool,
    browser_validation_required: bool,
    user_manual_approval_required: bool,
) -> dict[str, Any]:
    return {
        "key": key,
        "recommended": bool(recommended),
        "priority": priority,
        "why": why,
        "blockers": list(blockers),
        "prerequisites": list(prerequisites),
        "expected_value": expected_value,
        "risk": risk,
        "writes_db": bool(writes_db),
        "touches_truth_path": bool(touches_truth_path),
        "browser_validation_required": bool(browser_validation_required),
        "user_manual_approval_required": bool(user_manual_approval_required),
    }


def build_route_decision_matrix(
    gap_audit: Mapping[str, Any],
    search_seed: Mapping[str, Any],
    needs_review: Mapping[str, Any],
    source_metadata: Mapping[str, Any],
    blocker_thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    total_gap = int(gap_audit.get("total_gap_signals") or 0)
    source_tag_gap = int((gap_audit.get("gap_buckets") or {}).get("source_tag_present_no_source_concept_alias") or 0)
    alias_split_gap = int((gap_audit.get("gap_buckets") or {}).get("same_normalized_alias_key_split_across_multiple_concepts") or 0)
    asym = int((search_seed.get("aggregate") or {}).get("asymmetric_groups") or 0)
    needs = int(needs_review.get("total_needs_review_concepts") or 0)
    metadata_pct = float(source_metadata.get("source_metadata_distinct_eligible_media_pct") or 0)
    resolver_dominant = total_gap > 0 or asym > 0 or needs > 500
    metadata_dominant = metadata_pct < 20.0 and not resolver_dominant
    options = [
        route_option(
            "SCV2-R2 targeted resolver/gap reduction",
            recommended=resolver_dominant,
            priority="P1",
            why=f"Current audit shows gap signals={total_gap}, asymmetric search groups={asym}, needs_review={needs}.",
            blockers=["must stay source-layer only", "must not create Entity/media_tags truth"],
            prerequisites=["focused resolver/gap tests", "read-only/dry-run first unless explicitly approved"],
            expected_value="Reduce alias fragmentation and make search behavior more symmetric before larger data growth.",
            risk="Low to medium if bounded to SourceConcept resolver/search evidence contracts.",
            writes_db=True,
            touches_truth_path=False,
            browser_validation_required=False,
            user_manual_approval_required=True,
        ),
        route_option(
            "PX1-B additional Pixiv metadata extraction",
            recommended=metadata_dominant,
            priority="P2",
            why=f"Metadata coverage is {metadata_pct}% distinct eligible media, but resolver/search gaps are still dominant={resolver_dominant}.",
            blockers=["provider policy and budget required", "resolver gaps should not be the dominant bottleneck"],
            prerequisites=["separate provider run approval", "cache/audit/rate-limit gates"],
            expected_value="More source metadata if coverage is the bottleneck after resolver gaps are reduced.",
            risk="Medium provider/privacy/accounting risk.",
            writes_db=True,
            touches_truth_path=False,
            browser_validation_required=False,
            user_manual_approval_required=True,
        ),
        route_option(
            "Provider-2-P0 taxonomy/alias enrichment metadata-only",
            recommended=False,
            priority="P2",
            why=f"Source tag gap={source_tag_gap} and alias split gap={alias_split_gap} suggest resolver/taxonomy questions, but Provider-2 needs a separate P0 policy after A1/R2.",
            blockers=["provider policy not yet approved", "resolver gap dominance not closed"],
            prerequisites=["metadata-only provider design", "no image upload default", "privacy and budget policy"],
            expected_value="Improve taxonomy/category/alias classification if Pixiv-only metadata cannot resolve gaps.",
            risk="Medium provider and taxonomy drift risk.",
            writes_db=False,
            touches_truth_path=False,
            browser_validation_required=False,
            user_manual_approval_required=True,
        ),
        route_option(
            "SCV2-E2 controlled scale-up import to about 6000-6500 media",
            recommended=False,
            priority="P3",
            why="Scale-up would multiply current retrieval noise before resolver/search stability is proven.",
            blockers=["run ledger prerequisite", "search/gap/needs_review thresholds not met"],
            prerequisites=["Ingestion Run Ledger / Source Item State Ledger", "stable retrieval thresholds"],
            expected_value="More library coverage after quality gates are met.",
            risk="High if done before retrieval quality stabilizes.",
            writes_db=True,
            touches_truth_path=False,
            browser_validation_required=True,
            user_manual_approval_required=True,
        ),
        route_option(
            "SourceConcept management/editing UI/design",
            recommended=False,
            priority="P2",
            why="Manual correction may help later, but current dominant issue is automated resolver/gap reduction rather than UI processing.",
            blockers=["must not assume exhaustive manual review", "needs audit/rollback design"],
            prerequisites=["correction-oriented workflow design", "source-layer audit trail"],
            expected_value="Targeted correction of high-impact clusters after resolver gaps narrow.",
            risk="Medium product/workflow risk.",
            writes_db=True,
            touches_truth_path=False,
            browser_validation_required=True,
            user_manual_approval_required=True,
        ),
        route_option(
            "Entity bridge preview",
            recommended=False,
            priority="P3",
            why="Entity bridge remains blocked by search asymmetry, gap signals, and high needs_review volume.",
            blockers=["current thresholds not met", "truth-path preview/manual confirmation design absent"],
            prerequisites=["0 asymmetric required seed groups", "low needs_review/noise", "manual confirmation/audit/rollback guards"],
            expected_value="Eventually map source evidence into confirmed identity workflows.",
            risk="High truth pollution risk if premature.",
            writes_db=True,
            touches_truth_path=True,
            browser_validation_required=True,
            user_manual_approval_required=True,
        ),
        route_option(
            "DEDUP1 exact duplicate cleanup execution",
            recommended=False,
            priority="P3",
            why="PX1 exact duplicate dry-run groups remained zero.",
            blockers=["no useful exact duplicate target from PX1 dry-run"],
            prerequisites=["fresh duplicate audit with nonzero groups", "destructive approval and backup-first plan"],
            expected_value="None in current data.",
            risk="High destructive-file risk if run without targets.",
            writes_db=True,
            touches_truth_path=False,
            browser_validation_required=False,
            user_manual_approval_required=True,
        ),
        route_option(
            "Full-library / 10k expansion",
            recommended=False,
            priority="P3",
            why="Full-library expansion is blocked by current quality gates and ledger prerequisites.",
            blockers=["retrieval quality unstable", "ledger prerequisite missing"],
            prerequisites=["production ingestion ledger", "provider/source run ledger discipline", "stable retrieval thresholds"],
            expected_value="Large-scale library coverage only after quality and recovery controls exist.",
            risk="High operational and noise amplification risk.",
            writes_db=True,
            touches_truth_path=False,
            browser_validation_required=True,
            user_manual_approval_required=True,
        ),
    ]
    recommended = "SCV2-R2 targeted resolver/gap reduction" if resolver_dominant else "PX1-B additional Pixiv metadata extraction"
    return {
        "runner_report_recommendation": recommended,
        "requires_external_pack_review": True,
        "final_route_decision_status": FINAL_ROUTE_DECISION_STATUS,
        "options": options,
        "blocker_thresholds": blocker_thresholds,
        "decision_bias": {
            "entity_bridge_blocked": True,
            "dedup1_not_useful": True,
            "scale_up_waits_for_quality": True,
            "px1_b_waits_if_resolver_gaps_dominate": resolver_dominant,
        },
    }


def entity_bridge_blockers(route: Mapping[str, Any], thresholds: Mapping[str, Any]) -> dict[str, Any]:
    bridge = thresholds.get("entity_bridge", {})
    return {
        "blocked": bool(bridge.get("blocked", True)),
        "why": [
            "SourceConcept evidence is still unconfirmed source-layer evidence.",
            "Search asymmetry and unmatched seed failures remain.",
            "needs_review volume and gap signals exceed bridge thresholds.",
            "Preview/manual confirmation/audit/rollback write guards are not implemented in this phase.",
        ],
        "thresholds": bridge,
        "route_option": next((item for item in route.get("options", []) if item["key"] == "Entity bridge preview"), {}),
    }


def decision_for_option(route: Mapping[str, Any], option_key: str, *, status_key: str) -> dict[str, Any]:
    option = next((item for item in route.get("options", []) if item["key"].startswith(option_key)), {})
    return {
        "status": status_key,
        "recommended": bool(option.get("recommended")),
        "priority": option.get("priority"),
        "why": option.get("why"),
        "blockers": option.get("blockers", []),
    }


def sample_gap_rows(gap_samples: Sequence[Mapping[str, Any]], refs: ReviewPackRefBuilder) -> list[dict[str, Any]]:
    rows = []
    for row in gap_samples:
        concept_value = row.get("concept_id")
        rows.append(
            {
                "stable_private_concept_ref": refs.ref("concept", concept_value) if concept_value else "",
                "reason_bucket": row.get("bucket"),
                "display_label": refs.label(row.get("sample")),
                "evidence_count": row.get("count"),
                "status": row.get("status"),
                "recommended_action": "review resolver/alias linkage before truth promotion",
                "why_this_sample_matters": "Represents a concrete gap bucket behind the route recommendation.",
            }
        )
    return rows


def sample_needs_review_rows(needs_samples: Sequence[Mapping[str, Any]], refs: ReviewPackRefBuilder) -> list[dict[str, Any]]:
    rows = []
    for row in needs_samples:
        rows.append(
            {
                "stable_private_concept_ref": refs.ref("concept", row.get("concept_id")),
                "status": "needs_review",
                "display_label": refs.label(row.get("display")),
                "media_count": row.get("media_count"),
                "evidence_count": row.get("evidence_count"),
                "alias_count": row.get("alias_count"),
                "reason_bucket": "shares_active_alias" if row.get("shares_active_alias") else "review_candidate",
                "recommended_action": "triage before bridge or scale-up",
                "why_this_sample_matters": "Shows whether needs_review appears useful as recall evidence or noisy fragmentation.",
            }
        )
    return rows


def sample_search_rows(search_audit: Mapping[str, Any], refs: ReviewPackRefBuilder) -> list[dict[str, Any]]:
    rows = []
    for row in search_audit.get("private_examples", []):
        rows.append(
            {
                "search_seed_label": refs.label(row.get("seed_label"), allow_public_seed=True),
                "reason_bucket": row.get("reason_bucket"),
                "matched": row.get("matched"),
                "active_matched": row.get("active_matched"),
                "result_counts": {
                    "matched_concept_count": row.get("matched_concept_count"),
                    "visible_media_count": row.get("visible_media_count"),
                    "active_media_count": row.get("active_media_count"),
                },
                "stable_private_concept_refs": [refs.ref("concept", value) for value in row.get("stable_private_concept_refs", [])],
                "asymmetric_reason": row.get("reason_bucket"),
                "recommended_action": "fix alias/search symmetry before expansion or bridge",
                "why_this_sample_matters": "Unmatched seeds are counted as asymmetry instead of being silently ignored.",
            }
        )
    return rows


def sample_px1_influenced_concepts(conn: Connection, refs: ReviewPackRefBuilder, limit: int = 80) -> list[dict[str, Any]]:
    if not scv1.table_exists(conn, "blombooru_source_concept_evidence") or not scv1.table_exists(conn, "blombooru_source_metadata_records"):
        return []
    if not scv1.column_exists(conn, "blombooru_source_metadata_records", "run_label"):
        return []
    rows = scv1.rows_dict(
        conn,
        f"""
        SELECT e.concept_id,
               MIN(c.status) AS status,
               MIN(c.concept_type_hint) AS concept_type_hint,
               COUNT(DISTINCT e.id) AS evidence_count,
               COUNT(DISTINCT e.media_id) FILTER (WHERE e.media_id IS NOT NULL) AS media_count,
               COUNT(DISTINCT e.source_metadata_record_id) AS source_metadata_count
        FROM blombooru_source_concept_evidence e
        JOIN blombooru_source_metadata_records r ON r.id = e.source_metadata_record_id
        JOIN blombooru_source_concepts c ON c.id = e.concept_id
        WHERE LOWER(COALESCE(r.provider, '')) = 'pixiv'
          AND r.run_label = :px1_slug
        GROUP BY e.concept_id
        ORDER BY evidence_count DESC, e.concept_id ASC
        LIMIT {int(limit)}
        """,
        {"px1_slug": PX1_SLUG},
    )
    return [
        {
            "stable_private_concept_ref": refs.ref("concept", row.get("concept_id")),
            "provider": "pixiv",
            "status": row.get("status"),
            "role": row.get("concept_type_hint"),
            "evidence_count": int(row.get("evidence_count") or 0),
            "media_count": int(row.get("media_count") or 0),
            "stable_private_source_metadata_ref_count": int(row.get("source_metadata_count") or 0),
            "reason_bucket": "px1_influenced_source_concept",
            "recommended_action": "review as evidence, not truth",
            "why_this_sample_matters": "Shows how bounded PX1 evidence influenced SourceConcept backlog.",
        }
        for row in rows
    ]


def sample_unlinked_source_rows(
    conn: Connection,
    table_name: str,
    key_column: str,
    label_column: str,
    bucket: str,
    refs: ReviewPackRefBuilder,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not scv1.table_exists(conn, table_name) or not scv1.column_exists(conn, table_name, key_column):
        return []
    visible_alias_keys = {
        str(row.get("alias_key") or "")
        for row in scv1.load_aliases(conn)
        if row.get("status") in VISIBLE_STATUSES and row.get("alias_key")
    }
    rows = scv1.rows_dict(
        conn,
        f"""
        SELECT {scv1.qident(key_column)} AS key_value,
               MIN({scv1.qident(label_column)}) AS label,
               MIN(provider) AS provider,
               COUNT(*) AS count
        FROM {scv1.qident(table_name)}
        WHERE {scv1.qident(key_column)} IS NOT NULL
        GROUP BY {scv1.qident(key_column)}
        ORDER BY count DESC
        LIMIT {int(limit * 3)}
        """,
    )
    samples = []
    for row in rows:
        key_value = str(row.get("key_value") or "")
        if key_value in visible_alias_keys:
            continue
        samples.append(
            {
                "provider": row.get("provider"),
                "sample_ref": sample_sequence_ref(bucket, len(samples) + 1),
                "display_label": refs.label(row.get("label") or key_value),
                "evidence_count": int(row.get("count") or 0),
                "reason_bucket": bucket,
                "recommended_action": "link or intentionally exclude from SourceConcept alias path",
                "why_this_sample_matters": "Source evidence exists but is not connected to a visible SourceConcept alias.",
            }
        )
        if len(samples) >= limit:
            break
    return samples


def sample_same_alias_split_rows(conn: Connection, refs: ReviewPackRefBuilder, limit: int = 100) -> list[dict[str, Any]]:
    if not scv1.table_exists(conn, "blombooru_source_concept_aliases"):
        return []
    rows = scv1.rows_dict(
        conn,
        f"""
        SELECT alias_key, COUNT(DISTINCT concept_id) AS concept_count, COUNT(*) AS alias_count
        FROM blombooru_source_concept_aliases
        WHERE status IN ('active', 'needs_review')
          AND alias_key IS NOT NULL
        GROUP BY alias_key
        HAVING COUNT(DISTINCT concept_id) > 1
        ORDER BY concept_count DESC, alias_count DESC
        LIMIT {int(limit)}
        """,
    )
    return [
        {
            "sample_ref": sample_sequence_ref("same_alias_split", index),
            "display_label": refs.label(row.get("alias_key")),
            "reason_bucket": "same_normalized_alias_key_split_across_multiple_concepts",
            "result_counts": {"concept_count": int(row.get("concept_count") or 0), "alias_count": int(row.get("alias_count") or 0)},
            "recommended_action": "resolver merge/supersede review",
            "why_this_sample_matters": "A single normalized alias key maps to multiple visible concepts.",
        }
        for index, row in enumerate(rows, start=1)
    ]


def sample_same_display_context_split_rows(conn: Connection, refs: ReviewPackRefBuilder, limit: int = 100) -> list[dict[str, Any]]:
    aliases = scv1.load_aliases(conn)
    groups: dict[str, set[int]] = defaultdict(set)
    labels: dict[str, str] = {}
    for row in aliases:
        if row.get("status") not in VISIBLE_STATUSES:
            continue
        display = str(row.get("display_name") or row.get("alias_value") or row.get("alias_key") or "")
        key = scv1.canonical_source_key(display)
        if not key:
            continue
        groups[key].add(int(row["concept_id"]))
        labels.setdefault(key, display)
    rows = []
    for key, ids in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(ids) <= 1:
            continue
        rows.append(
            {
                "sample_ref": sample_sequence_ref("same_display_context_split", len(rows) + 1),
                "display_label": refs.label(labels.get(key)),
                "reason_bucket": "same_display_name_split_across_contexts",
                "result_counts": {"concept_count": len(ids)},
                "recommended_action": "context-aware merge or split review",
                "why_this_sample_matters": "Same display-normalized label is split across multiple contexts/concepts.",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def route_decision_evidence_samples(route: Mapping[str, Any], gap: Mapping[str, Any], search: Mapping[str, Any], needs: Mapping[str, Any]) -> list[dict[str, Any]]:
    base = [
        ("gap_total", gap.get("total_gap_signals"), "Resolver/gap reduction remains the dominant route signal."),
        ("search_asymmetric_groups", (search.get("aggregate") or {}).get("asymmetric_groups"), "Search symmetry is not ready for bridge or scale-up."),
        ("search_unmatched_seeds", (search.get("aggregate") or {}).get("unmatched_seeds"), "Unmatched aliases are explicit failures."),
        ("needs_review_total", needs.get("total_needs_review_concepts"), "needs_review backlog remains large enough to require triage."),
    ]
    for bucket, value in sorted((gap.get("gap_buckets") or {}).items(), key=lambda item: (-int(item[1] or 0), item[0])):
        base.append((bucket, value, "Gap bucket contributes to the route decision."))
    rows = []
    for index, (key, value, why) in enumerate(base[:30]):
        rows.append(
            {
                "stable_private_concept_ref": "",
                "reason_bucket": key,
                "result_counts": {"count": value},
                "recommended_action": route.get("runner_report_recommendation"),
                "why_this_sample_matters": why,
                "sample_exhausted": len(base) < 20,
                "ordinal": index + 1,
            }
        )
    while len(rows) < 20:
        rows.append(
            {
                "reason_bucket": "route_decision_context",
                "result_counts": {"count": len(rows)},
                "recommended_action": route.get("runner_report_recommendation"),
                "why_this_sample_matters": "Padding row records that route-decision evidence had fewer than 20 distinct public-safe buckets.",
                "sample_exhausted": True,
                "ordinal": len(rows) + 1,
            }
        )
    return rows


def review_samples(
    conn: Connection,
    gap_samples: Sequence[Mapping[str, Any]],
    search_audit: Mapping[str, Any],
    needs_samples: Sequence[Mapping[str, Any]],
    route: Mapping[str, Any],
    gap: Mapping[str, Any],
    needs: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    refs = ReviewPackRefBuilder()
    return {
        "gap-bucket-samples.jsonl": sample_gap_rows(gap_samples, refs)[:250],
        "search-seed-asymmetry-samples.jsonl": sample_search_rows(search_audit, refs)[:250],
        "needs-review-samples.jsonl": sample_needs_review_rows(needs_samples, refs)[:250],
        "px1-influenced-concept-samples.jsonl": sample_px1_influenced_concepts(conn, refs),
        "source-tag-unlinked-samples.jsonl": sample_unlinked_source_rows(
            conn,
            "blombooru_source_tag_observations",
            "canonical_tag_key",
            "raw_tag",
            "source_tag_present_no_source_concept_alias",
            refs,
        ),
        "source-assertion-unlinked-samples.jsonl": sample_unlinked_source_rows(
            conn,
            "blombooru_source_searchable_name_assertions",
            "canonical_name_key",
            "raw_input",
            "source_assertion_present_not_connected",
            refs,
        ),
        "same-alias-split-samples.jsonl": sample_same_alias_split_rows(conn, refs),
        "same-display-context-split-samples.jsonl": sample_same_display_context_split_rows(conn, refs),
        "route-decision-evidence-samples.jsonl": route_decision_evidence_samples(route, gap, search_audit, needs),
    }


def scan_text_for_review_pack_leaks(text_value: str, *, include_private_keys: bool = True) -> list[dict[str, str]]:
    checks = [
        ("local_path_or_private_root", LOCAL_PATH_RE),
        ("media_filename_like", MEDIA_FILENAME_RE),
        ("secret_or_auth_like", SECRET_RE),
    ]
    if include_private_keys:
        checks.append(("private_json_key_or_raw_field", PRIVATE_JSON_KEY_RE))
    findings = []
    for name, pattern in checks:
        match = pattern.search(text_value)
        if match:
            findings.append({"type": name, "match": match.group(0)[:120]})
    for finding in scv1.scan_public_text(text_value):
        findings.append({"type": finding["type"], "match": finding["match"]})
    return findings


def scan_review_pack_directory(pack_dir: Path, *, checked_at: str | None = None) -> dict[str, Any]:
    findings = []
    scanned_files = []
    allowed_matches = {"chatgpt-review-pack.zip"}
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(pack_dir)).replace("\\", "/")
        scanned_files.append(rel)
        text_value = path.read_text(encoding="utf-8", errors="replace")
        for finding in scan_text_for_review_pack_leaks(text_value, include_private_keys=True):
            if finding.get("type") == "media_filename_like" and finding.get("match") in allowed_matches:
                continue
            findings.append({"path": rel, **finding})
        for finding in scan_json_file_for_review_pack_leaks(path):
            findings.append({"path": rel, **finding})
    return {
        "checked_at": checked_at or utc_now_iso(),
        "passed": not findings,
        "scanned_file_count": len(scanned_files),
        "scanned_files": scanned_files,
        "findings": findings,
        "final_file_set_scanned": True,
        "policy": "scan every final review-pack file before zip creation, including manifest, checksums, redaction report, public report copy, audit data, and samples",
    }


def row_count_for_file(path: Path) -> int | None:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            return 1
    return None


def build_pack_checksums(pack_dir: Path) -> dict[str, str]:
    checksums = {}
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file() or path.name == "checksums.json":
            continue
        checksums[str(path.relative_to(pack_dir)).replace("\\", "/")] = sha256_file(path)
    return checksums


def list_pack_files(pack_dir: Path) -> list[str]:
    return [str(path.relative_to(pack_dir)).replace("\\", "/") for path in sorted(pack_dir.rglob("*")) if path.is_file()]


def pack_row_counts(pack_dir: Path, included_files: Sequence[str]) -> dict[str, int | None]:
    return {
        rel: row_count_for_file(pack_dir / rel)
        for rel in included_files
        if (pack_dir / rel).suffix.lower() in {".json", ".jsonl"}
    }


def write_review_pack_public_report_copy(pack_dir: Path, summary: Mapping[str, Any]) -> None:
    write_text(pack_dir / "public-report-copy" / PUBLIC_REPORT_MD.name, public_report_markdown(dict(summary)))
    write_json(pack_dir / "public-report-copy" / PUBLIC_REPORT_JSON.name, dict(summary))
    assert_fresh_report_copy_matches_summary(pack_dir, summary)


def assert_fresh_report_copy_matches_summary(pack_dir: Path, summary: Mapping[str, Any]) -> None:
    json_path = pack_dir / "public-report-copy" / PUBLIC_REPORT_JSON.name
    md_path = pack_dir / "public-report-copy" / PUBLIC_REPORT_MD.name
    if not json_path.exists() or not md_path.exists():
        raise A1BlockedError("ChatGPT review pack public-report-copy is missing current report files.")
    try:
        copied_summary = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise A1BlockedError(f"ChatGPT review pack public summary copy is invalid JSON: {exc}") from exc
    if copied_summary.get("generated_at") != summary.get("generated_at"):
        raise A1BlockedError("ChatGPT review pack public summary copy is stale.")
    if copied_summary.get("phase") != summary.get("phase"):
        raise A1BlockedError("ChatGPT review pack public summary copy is for the wrong phase.")
    expected_pack = summary.get("chatgpt_review_pack") or {}
    copied_pack = copied_summary.get("chatgpt_review_pack") or {}
    for key in ("generated", "file_count", "checksum_count", "redaction_scan_covers_final_file_set"):
        if expected_pack.get(key) != copied_pack.get(key):
            raise A1BlockedError(f"ChatGPT review pack public summary copy has stale chatgpt_review_pack.{key}.")


def build_review_pack_manifest(
    pack_dir: Path,
    summary: Mapping[str, Any],
    *,
    scan: Mapping[str, Any] | None,
) -> dict[str, Any]:
    included_files = list_pack_files(pack_dir)
    checksum_count = len([rel for rel in included_files if rel != "checksums.json"])
    scan_files = set(scan.get("scanned_files", [])) if scan else set()
    return {
        "phase": PHASE,
        "generated_at": utc_now_iso(),
        "git_branch": summary.get("branch"),
        "git_sha": summary.get("db_identity", {}).get("git_sha"),
        "report_provenance": summary.get("report_provenance"),
        "runtime_audit_git_sha": summary.get("runtime_audit_git_sha"),
        "runtime_audit_git_sha_scope": summary.get("runtime_audit_git_sha_scope"),
        "final_pr_head_sha_if_different": summary.get("final_pr_head_sha_if_different"),
        "public_report_generated_from_runtime_sha": summary.get("public_report_generated_from_runtime_sha"),
        "operational_result_reused_older_artifacts": summary.get("operational_result_reused_older_artifacts"),
        "dirty_worktree_status": summary.get("validation", {}).get("dirty_worktree_status"),
        "db_identity_summary_redacted": summary.get("db_identity"),
        "command_used_to_generate_pack": summary.get("validation", {}).get("operational_audit_command"),
        "public_report_copy_source": "rendered_from_current_summary",
        "public_report_copy_generated_at": summary.get("generated_at"),
        "included_files": included_files,
        "file_count": len(included_files),
        "checksum_file_path": "checksums.json",
        "checksum_count": checksum_count,
        "row_counts": pack_row_counts(pack_dir, included_files),
        "private_fields_are_present": False,
        "redaction_status": "passed" if scan and scan.get("passed") else "pending",
        "redaction_scanned_file_count": scan.get("scanned_file_count") if scan else None,
        "redaction_scan_covers_final_file_set": bool(scan and scan_files == set(included_files)),
        "known_limitations": [
            "checksums.json self-hash is omitted to avoid self-referential checksum churn",
            "sample display labels use per-pack sequential opaque refs unless they are allowlisted public search seed labels",
        ],
    }


def finalize_review_pack_metadata(pack_dir: Path, summary: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    checked_at = utc_now_iso()
    write_json(pack_dir / "manifest.json", {"phase": PHASE, "redaction_status": "pending"})
    write_json(pack_dir / "checksums.json", {})
    pending_scan = {
        "checked_at": checked_at,
        "passed": None,
        "scanned_file_count": 0,
        "scanned_files": [],
        "findings": [],
        "final_file_set_scanned": False,
        "policy": "placeholder before final review-pack redaction scan",
    }
    write_json(pack_dir / "redaction" / "redaction-report.json", pending_scan)
    write_text(pack_dir / "redaction" / "public-redaction-check.txt", json.dumps(pending_scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    scan: dict[str, Any] | None = None
    manifest: dict[str, Any] = {}
    checksums: dict[str, str] = {}
    for _ in range(3):
        manifest = build_review_pack_manifest(pack_dir, summary, scan=scan)
        write_json(pack_dir / "manifest.json", manifest)
        checksums = build_pack_checksums(pack_dir)
        write_json(pack_dir / "checksums.json", checksums)
        scan = scan_review_pack_directory(pack_dir, checked_at=checked_at)
        write_json(pack_dir / "redaction" / "redaction-report.json", scan)
        write_text(pack_dir / "redaction" / "public-redaction-check.txt", json.dumps(scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        if not scan["passed"]:
            raise A1BlockedError(f"ChatGPT review pack redaction failed: {scan['findings']!r}")

    assert scan is not None
    manifest = build_review_pack_manifest(pack_dir, summary, scan=scan)
    write_json(pack_dir / "manifest.json", manifest)
    checksums = build_pack_checksums(pack_dir)
    write_json(pack_dir / "checksums.json", checksums)
    final_scan = scan_review_pack_directory(pack_dir, checked_at=checked_at)
    if not final_scan["passed"]:
        write_json(pack_dir / "redaction" / "redaction-report.json", final_scan)
        write_text(pack_dir / "redaction" / "public-redaction-check.txt", json.dumps(final_scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise A1BlockedError(f"Final ChatGPT review pack redaction failed: {final_scan['findings']!r}")
    if set(final_scan["scanned_files"]) != set(list_pack_files(pack_dir)):
        final_scan = {**final_scan, "passed": False, "findings": final_scan["findings"] + [{"type": "final_file_set_not_scanned", "match": "review-pack"}]}
        write_json(pack_dir / "redaction" / "redaction-report.json", final_scan)
        write_text(pack_dir / "redaction" / "public-redaction-check.txt", json.dumps(final_scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise A1BlockedError("Final ChatGPT review pack redaction scan did not cover the final file set.")
    if manifest["checksum_count"] != len(checksums):
        raise A1BlockedError("Review-pack manifest checksum_count does not match checksums.json.")
    return manifest, checksums, final_scan


def zip_review_pack(pack_dir: Path) -> Path:
    zip_path = pack_dir.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(pack_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(pack_dir).as_posix())
    return zip_path


def review_pack_readme() -> str:
    return """# README_FOR_CHATGPT_REVIEW

This pack supports an independent ChatGPT audit of Phase 4.5-SCV2-A1.

Use the public report copy for the narrative conclusion, audit-data JSON files
for machine-readable counts, and review-samples JSONL files for concrete
sample-level checks. Samples are private-but-redacted: refs are per-pack
opaque sequence IDs, not database IDs, reversible hashes, or local paths.

Audit questions:

1. Does the evidence support the provisional route recommendation?
2. Are unmatched search seeds counted as failures instead of ignored?
3. Do gap buckets indicate resolver/gap reduction before PX1-B, Provider-2,
   Entity bridge, or scale-up?
4. Are sample files privacy-safe while still reviewable?
5. Are route blockers and thresholds conservative enough?

Do not infer confirmed Entity truth, confirmed assignments, or media_tags truth
from this pack. It contains source-layer evidence only. The pack excludes raw
local paths, originals, thumbnails, provider credentials, cookies, tokens, and
unredacted database IDs.
"""


def private_field_policy() -> dict[str, Any]:
    return {
        "allowed_sample_fields": [
            "stable_private_concept_ref",
            "stable_private_concept_refs",
            "stable_private_media_ref",
            "stable_private_source_metadata_ref",
            "sample_ref",
            "search_seed_label",
            "role",
            "category",
            "status",
            "provider",
            "display_label",
            "evidence_count",
            "media_count",
            "reason_bucket",
            "result_counts",
            "asymmetric_reason",
            "recommended_action",
            "why_this_sample_matters",
        ],
        "opaque_ref_policy": "Uploadable samples use per-pack sequential refs such as label_ref_000001 and concept_ref_000001; raw mappings, salts, and fixed-salt hashes are not included.",
        "forbidden_fields": [
            "local absolute paths",
            "source root paths",
            "original or thumbnail paths",
            "raw filenames",
            "cookies",
            "tokens",
            "API keys",
            "raw provider stdout/stderr",
            "raw private source labels",
            "raw unredacted source URLs",
            "raw media_id/concept_id/source_metadata_record_id fields",
        ],
    }


def generate_review_pack(
    output_dir: Path,
    summary: Mapping[str, Any],
    audit_data: Mapping[str, Any],
    samples: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    pack_dir = output_dir / "chatgpt-review-pack"
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    (pack_dir / "public-report-copy").mkdir(parents=True, exist_ok=True)
    (pack_dir / "audit-data").mkdir(parents=True, exist_ok=True)
    (pack_dir / "review-samples").mkdir(parents=True, exist_ok=True)
    (pack_dir / "redaction").mkdir(parents=True, exist_ok=True)

    write_review_pack_public_report_copy(pack_dir, summary)

    audit_name_map = {
        "current-baseline.json": "current_baseline",
        "source-metadata-coverage.json": "source_metadata_coverage",
        "source-concept-current-state.json": "source_concept_current_state",
        "gap-audit.json": "gap_audit",
        "gap-vs-scv1.json": "gap_vs_scv1",
        "search-seed-symmetry-audit.json": "search_seed_symmetry",
        "needs-review-triage-audit.json": "needs_review_triage",
        "px1-evidence-impact-audit.json": "px1_evidence_impact",
        "route-decision-matrix.json": "route_decision_matrix",
        "blocker-thresholds.json": "blocker_thresholds",
        "mutation-proof.json": "mutation_proof",
        "transaction-readonly-proof.json": "transaction_readonly_proof",
    }
    for filename, key in audit_name_map.items():
        write_json(pack_dir / "audit-data" / filename, audit_data[key])
    for filename in REVIEW_PACK_SAMPLE_FILES:
        write_jsonl(pack_dir / "review-samples" / filename, list(samples.get(filename, [])))
    write_json(pack_dir / "redaction" / "private-field-policy.json", private_field_policy())
    write_text(pack_dir / "README_FOR_CHATGPT_REVIEW.md", review_pack_readme())

    manifest, checksums, scan = finalize_review_pack_metadata(pack_dir, summary)
    zip_path = zip_review_pack(pack_dir)
    return {
        "generated": True,
        "pack_dir": pack_dir,
        "zip_path": zip_path,
        "zip_path_label": ".local_manifests/phase-4.5-scv2-a1-post-expansion-audit-route-decision/chatgpt-review-pack.zip",
        "exact_local_zip_path_private": str(zip_path),
        "committed": False,
        "file_count": manifest["file_count"],
        "checksum_count": len(checksums),
        "redaction_passed": True,
        "manifest_present": (pack_dir / "manifest.json").exists(),
        "sample_files_present": all((pack_dir / "review-samples" / filename).exists() for filename in REVIEW_PACK_SAMPLE_FILES),
        "known_limitations": manifest["known_limitations"],
        "upload_required_for_final_audit": True,
        "redaction_report": scan,
        "redaction_scanned_file_count": scan["scanned_file_count"],
        "redaction_scan_covers_final_file_set": scan["final_file_set_scanned"],
    }


def public_review_pack_summary(pack_info: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "generated": bool(pack_info.get("generated")),
        "zip_path_label": pack_info.get(
            "zip_path_label",
            ".local_manifests/phase-4.5-scv2-a1-post-expansion-audit-route-decision/chatgpt-review-pack.zip",
        ),
        "exact_local_zip_path_private": "[private local path omitted from committed public summary; provided in final delivery report]",
        "committed": False,
        "file_count": pack_info.get("file_count"),
        "checksum_count": pack_info.get("checksum_count"),
        "redaction_passed": pack_info.get("redaction_passed"),
        "redaction_scanned_file_count": pack_info.get("redaction_scanned_file_count"),
        "redaction_scan_covers_final_file_set": pack_info.get("redaction_scan_covers_final_file_set"),
        "manifest_present": pack_info.get("manifest_present"),
        "sample_files_present": pack_info.get("sample_files_present"),
        "known_limitations": list(pack_info.get("known_limitations", [])),
        "upload_required_for_final_audit": True,
    }


def build_durable_review_pack_policy_summary() -> dict[str, Any]:
    return {
        "policy_doc": "docs/chatgpt-review-pack-policy.md",
        "required_for": [
            "route-decision phases",
            "large-data audit phases where aggregate metrics may hide sample-level issues",
            "phases recommending or unlocking higher-risk next steps",
            "phases whose approval depends on concrete examples",
            "user-requested independent audit milestones",
        ],
        "recommended_for": [
            "bounded DB-writing source-layer phases with nontrivial sample-level outcomes",
            "provider metadata extraction phases",
            "UI/browser validation milestones with useful screenshots or logs",
            "import/scale phases with sampled ledgers",
        ],
        "not_normally_required_for": [
            "small bugfix PRs",
            "docs-only cleanup with no route decision",
            "tests-only changes",
            "narrow safety patches without data/report decisions",
            "local formatting or mechanical refactors",
        ],
        "decision_rule": FINAL_ROUTE_DECISION_STATUS,
    }


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    baseline = summary["current_baseline"]
    concepts = summary["source_concept_current_state"]
    gap = summary["gap_audit"]
    search = summary["search_seed_symmetry"].get("aggregate", {})
    needs = summary["needs_review_triage"]
    route = summary["route_decision_matrix"]
    pack = summary["chatgpt_review_pack"]
    provenance = summary["report_provenance"]
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        "",
        f"- Status: `{summary['final_route_decision_status']}`.",
        f"- Branch/runtime audit SHA: `{summary['branch']}` / `{provenance.get('runtime_audit_git_sha')}`.",
        f"- Recommendation: `{summary['recommended_next_phase']}`.",
        f"- Review pack required before final route approval: `{route.get('requires_external_pack_review')}`.",
        "",
        "## Provenance / SHA boundary",
        "",
        f"- Runtime audit git SHA: `{provenance.get('runtime_audit_git_sha')}`.",
        f"- Runtime audit git SHA scope: {provenance.get('runtime_audit_git_sha_scope')}",
        f"- Public report generated from runtime SHA: `{provenance.get('public_report_generated_from_runtime_sha')}`.",
        f"- Final PR head SHA if different: `{provenance.get('final_pr_head_sha_if_different')}`.",
        f"- Final PR head SHA scope: {provenance.get('final_pr_head_sha_if_different_scope')}",
        f"- Operational result reused older artifacts: `{provenance.get('operational_result_reused_older_artifacts')}`.",
        f"- Dirty worktree status at runtime: `{provenance.get('dirty_worktree_status')}`.",
        "- If the final reviewed PR head differs from the runtime audit SHA, it is expected to be the later report/test/review-pack regeneration commit after this read-only audit.",
        "",
        "## Scope and non-goals",
        "",
        "- Scope: read-only post-R1 SourceConcept/source metadata/search audit plus durable ChatGPT review pack policy.",
        "- Non-goals: no DB write, provider call, import, classification, AI tagging, localization, LLM, resolver execute, Entity bridge, truth-path write, media_tags mutation, source storage mutation, iCloud mutation, DEDUP execution, or browser validation.",
        "",
        "## Durable ChatGPT review pack policy update",
        "",
        "- Added `docs/chatgpt-review-pack-policy.md`.",
        f"- A1 recommendations remain `{FINAL_ROUTE_DECISION_STATUS}` until the user uploads the review pack to ChatGPT and receives independent audit.",
        "",
        "## Current DB/source baseline",
        "",
        f"- Total / eligible media: `{baseline.get('total_media')}` / `{baseline.get('eligible_media')}`.",
        f"- Eligible AI tag coverage: `{baseline.get('eligible_ai_tag_coverage', {}).get('covered')}` / `{baseline.get('eligible_ai_tag_coverage', {}).get('total')}` = `{baseline.get('eligible_ai_tag_coverage', {}).get('percent')}`%.",
        f"- Source metadata rows / distinct eligible media: `{baseline.get('source_metadata_records')}` / `{baseline.get('distinct_eligible_media_with_source_metadata')}`.",
        f"- Source metadata coverage percent: `{baseline.get('source_metadata_coverage_percent')}`%.",
        "",
        "## R1 transition interpretation",
        "",
        "- R1 trusted transition: SourceConcept total `4214 -> 6094`, active `355 -> 1078`, needs_review `760 -> 1809`, PX1-influenced concepts `1692`.",
        "- The latest current-head execute rerun was idempotent over the already committed R1 state. It must not be interpreted as R1 having no effect.",
        "",
        "## SourceConcept current state",
        "",
        f"- Total SourceConcept: `{concepts.get('total_source_concepts')}`.",
        f"- By status: `{json.dumps(concepts.get('by_status'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Strict PX1-influenced concepts: `{concepts.get('concepts_influenced_by_strict_px1_evidence')}` (`{concepts.get('concepts_influenced_by_px1_evidence_scope')}`).",
        f"- All Pixiv-influenced concepts: `{concepts.get('concepts_influenced_by_all_pixiv_evidence')}`; non-PX1 Pixiv-influenced concepts: `{concepts.get('concepts_influenced_by_non_px1_pixiv_evidence')}`.",
        f"- Duplicate/fragment candidate groups: `{concepts.get('duplicate_fragment_candidate_groups')}`.",
        "",
        "## Gap audit",
        "",
        f"- Total gap signals: `{gap.get('total_gap_signals')}`.",
        f"- Gap buckets: `{json.dumps(gap.get('gap_buckets'), ensure_ascii=False, sort_keys=True)}`.",
        "- Increased total gap signals are interpreted against changed denominators and newly exposed PX1/R1 evidence, not as a simple regression.",
        "",
        "## Search seed symmetry audit",
        "",
        f"- Groups / seeds / matched / unmatched: `{search.get('groups_tested')}` / `{search.get('seeds_tested')}` / `{search.get('matched_seeds')}` / `{search.get('unmatched_seeds')}`.",
        f"- Symmetric / asymmetric groups: `{search.get('symmetric_groups')}` / `{search.get('asymmetric_groups')}`.",
        f"- Asymmetry reason buckets: `{json.dumps(search.get('asymmetry_reason_buckets'), ensure_ascii=False, sort_keys=True)}`.",
        "- Unmatched aliases are counted as asymmetry or explicit unmatched failures.",
        "",
        "## needs_review triage audit",
        "",
        f"- Total needs_review concepts: `{needs.get('total_needs_review_concepts')}`.",
        f"- needs_review with media / high evidence / sharing active alias: `{needs.get('needs_review_with_media')}` / `{needs.get('needs_review_high_evidence_count')}` / `{needs.get('needs_review_sharing_alias_with_active')}`.",
        f"- Assessment: `{needs.get('assessment')}`.",
        "",
        "## PX1 evidence impact",
        "",
        f"- Strict PX1-influenced concepts: `{summary['px1_evidence_impact'].get('px1_strict_influenced_concepts')}` using `{summary['px1_evidence_impact'].get('px1_filter_scope')}`.",
        f"- All Pixiv-influenced concepts: `{summary['px1_evidence_impact'].get('pixiv_all_influenced_concepts')}`.",
        f"- Non-PX1 Pixiv-influenced concepts: `{summary['px1_evidence_impact'].get('non_px1_pixiv_influenced_concepts')}`.",
        f"- Route decision PX1 impact metric: `{summary['px1_evidence_impact'].get('route_decision_px1_impact_metric')}`.",
        "- PX1 remains review-scoped evidence/backlog input, not active Entity or media_tags truth.",
        "",
        "## Comparison with SCV1/P0/E1/PX1/R1",
        "",
        f"- SCV1 total gap signals -> current: `{summary['gap_vs_scv1'].get('scv1_total_gap_signals')}` -> `{summary['gap_vs_scv1'].get('current_total_gap_signals')}`.",
        f"- Not directly comparable buckets: `{json.dumps(summary['gap_vs_scv1'].get('not_comparable_buckets'), ensure_ascii=False)}`.",
        "",
        "## Route decision matrix",
        "",
    ]
    for option in route.get("options", []):
        lines.append(
            f"- `{option['key']}`: priority `{option['priority']}`, recommended `{option['recommended']}`; writes DB `{option['writes_db']}`; truth path `{option['touches_truth_path']}`; why: {option['why']}"
        )
    lines.extend(
        [
            "",
            "## Entity bridge blocker analysis",
            "",
            f"- Blocked: `{summary['entity_bridge_blockers'].get('blocked')}`.",
            "- Reason: search asymmetry, gap signals, needs_review volume, and missing truth-path preview/manual-confirmation guards remain unresolved.",
            "",
            "## PX1-B decision",
            "",
            f"- `{summary['px1_b_decision'].get('status')}`: {summary['px1_b_decision'].get('why')}",
            "",
            "## Provider-2 decision",
            "",
            f"- `{summary['provider2_decision'].get('status')}`: {summary['provider2_decision'].get('why')}",
            "",
            "## Scale-up decision",
            "",
            f"- `{summary['scale_up_decision'].get('status')}`: {summary['scale_up_decision'].get('why')}",
            "",
            "## DEDUP1 decision",
            "",
            f"- `{summary['dedup1_decision'].get('status')}`: {summary['dedup1_decision'].get('why')}",
            "",
            "## Recommended next phase",
            "",
            f"`{summary['recommended_next_phase']}` is the runner recommendation and remains `{FINAL_ROUTE_DECISION_STATUS}` pending ChatGPT pack audit.",
            "",
            "## ChatGPT independent review pack",
            "",
            f"- Generated: `{pack.get('generated')}`.",
            f"- Not committed: `{not pack.get('committed')}`.",
            f"- Zip path label: `{pack.get('zip_path_label')}`.",
            "- Exact private paths are not exposed in this public report.",
            "- The user should upload the local `chatgpt-review-pack.zip` to ChatGPT before final route approval.",
            "- Final route decision should be made only after reviewing both this PR/report and the review pack.",
            "",
            "## Validation",
            "",
            f"- Operational command: `{summary['validation'].get('operational_audit_command')}`.",
            f"- Operational result: `{summary['validation'].get('operational_audit_result')}`.",
            f"- Browser validation: `{summary['validation'].get('browser_validation')}`.",
            "",
            "## Mutation proof / read-only proof",
            "",
            f"- PostgreSQL transaction_read_only: `{summary['transaction_readonly_proof'].get('transaction_read_only')}`.",
            f"- Mutation proof passed: `{summary['mutation_proof'].get('passed')}`.",
            f"- Changed forbidden tables: `{summary['mutation_proof'].get('changed_tables')}`.",
            "",
            "## Public/private artifact boundary",
            "",
            "- Public report/summary contain aggregate counts and redacted labels only.",
            "- Private `.local_manifests` artifacts are ignored and not committed.",
            f"- Public redaction passed: `{summary['public_redaction'].get('passed')}`.",
            "",
            "## Engineering judgment / operator notes",
            "",
            "- Artifact lifecycle: A1 runner and focused tests are phase-scoped; policy doc is durable project policy; public report/summary are public report/handoff artifacts; `.local_manifests` outputs and review pack are one-off ignored local artifacts.",
            "- Phase boundary is appropriate: A1 answers the route question without executing another resolver/provider/import/truth phase.",
            "- Remaining risks: the route recommendation is provisional until independent ChatGPT review pack audit completes.",
            "- Recommended next step: review the PR and upload the generated review pack to ChatGPT before approving a final route.",
            "",
        ]
    )
    return "\n".join(lines)


def write_public_outputs(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    paths = [PUBLIC_REPORT_MD, PUBLIC_REPORT_JSON]
    labels = [root_relative_or_name(path) for path in paths]
    checked_at = utc_now_iso()
    temp_dir = output_dir / "_public_report_staging"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_md = temp_dir / PUBLIC_REPORT_MD.name
    temp_json = temp_dir / PUBLIC_REPORT_JSON.name
    pending = {
        "checked_at": checked_at,
        "passed": None,
        "public_paths": labels,
        "findings": [],
        "final_public_scan_after_public_fields_finalized": False,
        "exact_private_paths_public": False,
    }
    summary["public_redaction"] = pending
    write_text(temp_md, public_report_markdown(summary))
    write_json(temp_json, summary)
    scan = scan_public_artifacts([temp_md, temp_json], checked_at=checked_at, public_path_labels=labels)
    if not scan["passed"]:
        failed = {**pending, "passed": False, "findings": scan["findings"]}
        write_text(output_dir / "public-redaction-check.txt", json.dumps(failed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise A1BlockedError(f"Public redaction scan failed: {scan['findings']!r}")
    final = {
        "checked_at": checked_at,
        "passed": True,
        "public_paths": labels,
        "findings": [],
        "final_public_scan_after_public_fields_finalized": True,
        "exact_private_paths_public": False,
        "policy": "public Markdown/JSON are rendered to ignored staging files, scanned, then replace tracked paths",
    }
    summary["public_redaction"] = final
    write_text(temp_md, public_report_markdown(summary))
    write_json(temp_json, summary)
    final_scan = scan_public_artifacts([temp_md, temp_json], checked_at=checked_at, public_path_labels=labels)
    if not final_scan["passed"]:
        failed = {**final, "passed": False, "findings": final_scan["findings"]}
        write_text(output_dir / "public-redaction-check.txt", json.dumps(failed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise A1BlockedError(f"Final public redaction scan failed: {final_scan['findings']!r}")
    PUBLIC_REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    temp_md.replace(PUBLIC_REPORT_MD)
    temp_json.replace(PUBLIC_REPORT_JSON)
    try:
        temp_dir.rmdir()
    except OSError:
        pass
    write_text(output_dir / "public-redaction-check.txt", json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return final


def scan_public_artifacts(
    paths: Sequence[Path],
    *,
    checked_at: str | None = None,
    public_path_labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    """A1 public scan with a narrow allowlist for its fixed review-pack zip label."""
    scan = scv1.scan_public_artifacts(paths, checked_at=checked_at, public_path_labels=public_path_labels)
    allowed_matches = {"chatgpt-review-pack.zip"}
    findings = []
    for finding in scan["findings"]:
        match = str(finding.get("match") or "")
        if finding.get("type") == "media_filename_like" and match in allowed_matches:
            continue
        findings.append(finding)
    return {**scan, "passed": not findings, "findings": findings}


def private_artifacts_summary(output_dir: Path, *, review_pack_generated: bool) -> dict[str, Any]:
    existing = [name for name in REQUIRED_PRIVATE_ARTIFACTS if (output_dir / name).exists()]
    return {
        "private_artifact_root_label": f".local_manifests/{PHASE_SLUG}",
        "required_private_artifacts": list(REQUIRED_PRIVATE_ARTIFACTS),
        "existing_required_private_artifacts": existing,
        "missing_required_private_artifacts": [name for name in REQUIRED_PRIVATE_ARTIFACTS if name not in existing],
        "chatgpt_review_pack_generated": bool(review_pack_generated),
        "committed": False,
        "exact_private_paths_public": False,
    }


def write_private_artifacts(
    output_dir: Path,
    *,
    db_identity: Mapping[str, Any],
    transaction_proof: Mapping[str, Any],
    baseline: Mapping[str, Any],
    source_metadata: Mapping[str, Any],
    concepts: Mapping[str, Any],
    gap: Mapping[str, Any],
    gap_vs_scv1: Mapping[str, Any],
    r1_transition: Mapping[str, Any],
    search: Mapping[str, Any],
    needs: Mapping[str, Any],
    px1: Mapping[str, Any],
    route: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    mutation: Mapping[str, Any],
) -> None:
    payloads = {
        "db-identity.json": db_identity,
        "transaction-readonly-proof.json": transaction_proof,
        "current-media-source-baseline.json": baseline,
        "source-metadata-coverage.json": source_metadata,
        "source-concept-current-state.json": concepts,
        "source-concept-gap-audit.json": gap,
        "source-concept-gap-vs-scv1.json": gap_vs_scv1,
        "r1-transition-interpretation.json": r1_transition,
        "search-seed-symmetry-audit.json": {key: value for key, value in search.items() if key != "private_examples"},
        "needs-review-triage-audit.json": needs,
        "px1-evidence-impact-audit.json": px1,
        "route-decision-matrix.json": route,
        "blocker-thresholds.json": thresholds,
        "mutation-proof.json": mutation,
    }
    for name, payload in payloads.items():
        write_json(output_dir / name, payload)
    write_jsonl(output_dir / "search-seed-asymmetry-examples-private.jsonl", list(search.get("private_examples", [])))


def build_summary(
    *,
    db_identity: Mapping[str, Any],
    transaction_proof: Mapping[str, Any],
    baseline: Mapping[str, Any],
    source_metadata: Mapping[str, Any],
    concepts: Mapping[str, Any],
    r1_transition: Mapping[str, Any],
    gap: Mapping[str, Any],
    gap_vs_scv1: Mapping[str, Any],
    search: Mapping[str, Any],
    needs: Mapping[str, Any],
    px1: Mapping[str, Any],
    route: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    mutation: Mapping[str, Any],
    output_dir: Path,
    validation: Mapping[str, Any],
    review_pack_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    review_pack_public = public_review_pack_summary(review_pack_info or {"generated": False, "upload_required_for_final_audit": True})
    provenance = build_report_provenance(db_identity, validation)
    summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "branch": BRANCH,
        "generated_at": utc_now_iso(),
        "report_provenance": provenance,
        "runtime_audit_git_sha": provenance["runtime_audit_git_sha"],
        "runtime_audit_git_sha_scope": provenance["runtime_audit_git_sha_scope"],
        "final_pr_head_sha_if_different": provenance["final_pr_head_sha_if_different"],
        "public_report_generated_from_runtime_sha": provenance["public_report_generated_from_runtime_sha"],
        "operational_result_reused_older_artifacts": provenance["operational_result_reused_older_artifacts"],
        "dirty_worktree_status": provenance["dirty_worktree_status"],
        "db_identity": public_db_identity(db_identity),
        "transaction_readonly_proof": transaction_proof,
        "durable_review_pack_policy": build_durable_review_pack_policy_summary(),
        "current_baseline": baseline,
        "source_metadata_coverage": source_metadata,
        "source_concept_current_state": concepts,
        "r1_transition_interpretation": r1_transition,
        "gap_audit": gap,
        "gap_vs_scv1": gap_vs_scv1,
        "search_seed_symmetry": {key: value for key, value in search.items() if key != "private_examples"},
        "needs_review_triage": needs,
        "px1_evidence_impact": px1,
        "route_decision_matrix": route,
        "runner_report_recommendation": route.get("runner_report_recommendation"),
        "final_route_decision_status": route.get("final_route_decision_status"),
        "recommended_next_phase": route.get("runner_report_recommendation"),
        "entity_bridge_blockers": entity_bridge_blockers(route, thresholds),
        "px1_b_decision": decision_for_option(route, "PX1-B", status_key="deferred_pending_r2_or_pack_audit"),
        "provider2_decision": decision_for_option(route, "Provider-2-P0", status_key="deferred_pending_resolver_gap_reduction"),
        "scale_up_decision": decision_for_option(route, "SCV2-E2", status_key="blocked_quality_and_ledger_thresholds"),
        "dedup1_decision": decision_for_option(route, "DEDUP1", status_key="not_useful_zero_exact_duplicate_groups"),
        "chatgpt_review_pack": review_pack_public,
        "mutation_proof": mutation,
        "public_redaction": {"passed": None, "findings": []},
        "validation": dict(validation),
        "safety": {
            "db_write": False,
            "transaction_read_only_required": True,
            "provider_calls": False,
            "media_import": False,
            "classification": False,
            "ai_tagging": False,
            "localization_or_llm": False,
            "source_concept_resolver_execute": False,
            "entity_truth_or_media_tags": False,
            "source_icloud_storage_mutation": False,
            "cleanup_delete_reset_drop_truncate": False,
            "server_or_browser_validation_required": False,
        },
        "artifact_lifecycle": {
            "scripts/run_phase45_scv2_a1_post_expansion_audit_route_decision.py": "phase-scoped operational runner",
            "tests/test_phase45_scv2_a1_post_expansion_audit_route_decision.py": "phase-scoped validation test",
            "docs/chatgpt-review-pack-policy.md": "durable project policy",
            "docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision.md": "public report / handoff / roadmap update",
            "docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision-summary.json": "public report / handoff / roadmap update",
            ".local_manifests/phase-4.5-scv2-a1-post-expansion-audit-route-decision": "one-off local artifact / ignored output",
        },
        "private_artifacts": private_artifacts_summary(output_dir, review_pack_generated=bool(review_pack_info and review_pack_info.get("generated"))),
    }
    schema = validate_summary_schema(summary)
    summary["validation"]["summary_schema"] = schema
    if not schema["passed"]:
        raise A1BlockedError(f"Summary schema missing fields: {schema['missing_fields']!r}")
    return summary


def public_gap_audit(gap: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "gap_buckets": gap.get("gap_buckets"),
        "gap_bucket_details": gap.get("gap_bucket_details"),
        "sample_limit_policy": gap.get("sample_limit_policy"),
        "total_gap_signals": gap.get("total_gap_signals"),
        "normal_tag_gap_policy": gap.get("normal_tag_gap_policy"),
        "recommended_next_fix_category": gap.get("recommended_next_fix_category"),
    }


def validation_context(command_label: str) -> dict[str, Any]:
    return {
        "operational_audit_command": command_label,
        "operational_audit_result": "passed",
        "browser_validation": "not_run_no_ui_runtime_change",
        "server_started": False,
        "provider_network_attempted": False,
        "dirty_worktree_status": git_value(["git", "status", "--short"]),
        "python_executable": Path(sys.executable).name,
        "python_executable_path_redacted": True,
    }


def build_report_provenance(db_identity: Mapping[str, Any], validation: Mapping[str, Any]) -> dict[str, Any]:
    runtime_sha = str(db_identity.get("git_sha") or "unavailable")
    return {
        "runtime_audit_git_sha": runtime_sha,
        "runtime_audit_git_sha_scope": "git rev-parse HEAD at A1 read-only runner execution; if dirty_worktree_status is non-empty, the runtime also included the listed working-tree changes.",
        "final_pr_head_sha_if_different": "reported by PR metadata/final delivery after the report regeneration commit; a commit cannot truthfully contain its own final SHA.",
        "final_pr_head_sha_if_different_scope": "If the final PR head differs from runtime_audit_git_sha, the difference is expected to be the later A1 report/test/review-pack regeneration commit, not a separate operational audit.",
        "public_report_generated_from_runtime_sha": runtime_sha,
        "operational_result_reused_older_artifacts": False,
        "dirty_worktree_status": validation.get("dirty_worktree_status"),
        "public_report_contains_runtime_result": True,
        "committed_report_sha_boundary_note": "The committed report records the runtime audit SHA and may itself be committed by a later PR head.",
    }


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    if not args.read_only:
        raise A1BlockedError("A1 runner requires --read-only.")
    if not args.write_chatgpt_review_pack:
        raise A1BlockedError("A1 requires --write-chatgpt-review-pack so final route approval remains review-pack-gated.")
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    command_label = (
        f"python scripts/run_phase45_scv2_a1_post_expansion_audit_route_decision.py"
        f" --output-dir .local_manifests/{PHASE_SLUG} --write-public-report --read-only --write-chatgpt-review-pack"
    )
    url, env_identity = scv1.build_database_url()
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "options": "-c statement_timeout=600000"},
    )
    conn: Connection | None = None
    try:
        conn = engine.connect()
        if conn.dialect.name != "postgresql":
            raise A1BlockedError(f"A1 requires PostgreSQL read-only transaction support, got {conn.dialect.name!r}.")
        conn.exec_driver_sql("BEGIN TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '600s'")
        db_identity = scv1.read_only_identity(conn, env_identity)
        transaction_proof = transaction_readonly_proof(db_identity)
        if not transaction_proof["passed"]:
            raise A1BlockedError("PostgreSQL transaction_read_only proof failed.")
        before_counts = build_table_counts(conn)

        media = scv1.audit_media_coverage(conn)
        source_layer = scv1.audit_source_layer_coverage(conn)
        concepts_rows = scv1.load_concepts(conn)
        aliases_rows = scv1.load_aliases(conn)
        evidence_rows = scv1.load_evidence(conn)
        scv1_concepts_inventory, _alias_inventory, _evidence_inventory = scv1.audit_source_concepts(conn)
        full_gap, gap_samples = scv1.audit_alias_gaps(conn, concepts_rows, aliases_rows)
        public_gap = public_gap_audit(full_gap)
        needs_review, needs_samples = scv1.audit_needs_review(conn, concepts_rows, aliases_rows, evidence_rows)
        search_audit = build_search_seed_symmetry_audit(conn)
        baseline = build_current_baseline(media, source_layer)
        source_metadata = build_source_metadata_coverage(conn, source_layer)
        source_concepts = build_source_concept_current_state(
            conn,
            concepts_rows,
            aliases_rows,
            evidence_rows,
            scv1_concepts_inventory,
        )
        scv1_summary = read_json(SCV1_SUMMARY_JSON)
        r1_summary = read_json(R1_SUMMARY_JSON)
        gap_vs_scv1 = build_gap_vs_scv1(public_gap, scv1_summary)
        r1_transition = build_r1_transition_interpretation(r1_summary)
        px1 = build_px1_evidence_impact(conn, r1_summary)
        thresholds = build_blocker_thresholds(public_gap, search_audit, needs_review, source_metadata)
        route = build_route_decision_matrix(public_gap, search_audit, needs_review, source_metadata, thresholds)

        after_counts = build_table_counts(conn)
        mutation = compare_table_counts(before_counts, after_counts)
        if not mutation["passed"]:
            raise A1BlockedError(f"Forbidden table counts changed during read-only audit: {mutation['changed_tables']!r}")

        validation = validation_context(command_label)
        write_private_artifacts(
            output_dir,
            db_identity=db_identity,
            transaction_proof=transaction_proof,
            baseline=baseline,
            source_metadata=source_metadata,
            concepts=source_concepts,
            gap=full_gap,
            gap_vs_scv1=gap_vs_scv1,
            r1_transition=r1_transition,
            search=search_audit,
            needs=needs_review,
            px1=px1,
            route=route,
            thresholds=thresholds,
            mutation=mutation,
        )

        summary = build_summary(
            db_identity=db_identity,
            transaction_proof=transaction_proof,
            baseline=baseline,
            source_metadata=source_metadata,
            concepts=source_concepts,
            r1_transition=r1_transition,
            gap=public_gap,
            gap_vs_scv1=gap_vs_scv1,
            search=search_audit,
            needs=needs_review,
            px1=px1,
            route=route,
            thresholds=thresholds,
            mutation=mutation,
            output_dir=output_dir,
            validation=validation,
            review_pack_info={"generated": False},
        )
        if args.write_public_report:
            write_public_outputs(summary, output_dir)

        sample_payloads = review_samples(conn, gap_samples, search_audit, needs_samples, route, public_gap, needs_review)
        audit_data = {
            "current_baseline": baseline,
            "source_metadata_coverage": source_metadata,
            "source_concept_current_state": source_concepts,
            "gap_audit": public_gap,
            "gap_vs_scv1": gap_vs_scv1,
            "search_seed_symmetry": {key: value for key, value in search_audit.items() if key != "private_examples"},
            "needs_review_triage": needs_review,
            "px1_evidence_impact": px1,
            "route_decision_matrix": route,
            "blocker_thresholds": thresholds,
            "mutation_proof": mutation,
            "transaction_readonly_proof": transaction_proof,
        }
        pack_info = generate_review_pack(output_dir, summary, audit_data, sample_payloads)
        summary = build_summary(
            db_identity=db_identity,
            transaction_proof=transaction_proof,
            baseline=baseline,
            source_metadata=source_metadata,
            concepts=source_concepts,
            r1_transition=r1_transition,
            gap=public_gap,
            gap_vs_scv1=gap_vs_scv1,
            search=search_audit,
            needs=needs_review,
            px1=px1,
            route=route,
            thresholds=thresholds,
            mutation=mutation,
            output_dir=output_dir,
            validation=validation,
            review_pack_info=pack_info,
        )
        if args.write_public_report:
            write_public_outputs(summary, output_dir)
        pack_info = generate_review_pack(output_dir, summary, audit_data, sample_payloads)
        summary["chatgpt_review_pack"] = public_review_pack_summary(pack_info)
        pack_info = generate_review_pack(output_dir, summary, audit_data, sample_payloads)
        summary["chatgpt_review_pack"] = public_review_pack_summary(pack_info)
        assert_fresh_report_copy_matches_summary(pack_info["pack_dir"], summary)
        if args.write_public_report:
            write_public_outputs(summary, output_dir)
        write_json(output_dir / "summary-private-copy.json", {**summary, "chatgpt_review_pack_private": {**pack_info, "pack_dir": str(pack_info["pack_dir"]), "zip_path": str(pack_info["zip_path"])}})
        write_json(output_dir / "checksums.json", build_pack_checksums(output_dir))
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
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--write-chatgpt-review-pack", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_audit(args)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
