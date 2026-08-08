"""Executable checks for V.I.O.L.E.T. phase contracts."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contract_registry import (
    ML1_MULTILINGUAL_ALIAS_SOURCE_METADATA_CLOSURE_STATUSES,
    ML2_MULTILINGUAL_IDENTITY_CANDIDATE_CLOSURE_STATUSES,
    R1R_FULL_SOURCE_CONCEPT_PIPELINE_STATUSES,
    R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES,
    R2R_AUTONOMOUS_RECALL_SEARCH_CLOSURE_STATUSES,
    R2_SOURCE_CONCEPT_GRAPH_REMEDIATION_STATUSES,
    SCV2_FL1_I1_INVENTORY_STATUSES,
    SCV2_FL1_P1_FOUNDATION_STATUSES,
    SOURCE_CONCEPT_ALLOWED_STATUSES,
    SOURCE_CONCEPT_FULL_CHAIN_STAGES,
    SV1_CONTROLLED_SCALE_PROMOTION_READINESS_STATUSES,
    SV1B_CONTROLLED_PIXIV_METADATA_LOCALIZATION_SOURCE_GRAPH_CLOSURE_STATUSES,
    SV1B_OWNER_ACCEPTANCE_CLOSEOUT_STATUSES,
    get_contract,
)
from .contract_types import ContractCheckResult, PhaseContract

MISSING = object()
CONTRACT_ROOT = Path(__file__).resolve().parents[2]

WINDOWS_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>|]+")
UNC_PATH_RE = re.compile(r"\\\\[^\\\s\"'<>|]+\\[^\\\s\"'<>|]+")
FILE_URI_RE = re.compile(r"(?i)\bfile://[^\s\"'<>]+")
POSIX_PRIVATE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(home|Users|mnt|Volumes|tmp|workspace|opt|var)(/[^\s\"'<>]*)?",
)
TOKEN_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{4,}|ghp_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|xoxb-[A-Za-z0-9-]{4,}|Authorization\s*:|Bearer\s+[A-Za-z0-9._-]{4,})"
)
DB_URL_RE = re.compile(r"(?i)\b(postgresql|postgres|mysql|mariadb|mssql|mongodb|redis|sqlite)://[^\s\"'<>]+")
HTTP_URL_RE = re.compile(r"(?i)\bhttps?://[^\s\"'<>]+")
SECRET_KEY_NAME_RE = re.compile(r"(?i)(api[_-]?key|token|password|secret|cookie|authorization|bearer)")
SECRET_CONTEXT_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|password|secret|cookie|authorization|bearer|credential)")
PRIVATE_PROVENANCE_KEY_RE = re.compile(
    r"(?i)(raw_filename|filename|file_name|source_url|source_urls|original_url|thumbnail_url|source_path|local_path|source_root|selected_root_label|source_root_label|root_label|original_path|provider_url|private_url|raw_label|private_label|provider_credential)"
)
PRIVATE_CONTENT_HASH_KEY_RE = re.compile(
    r"(?i)(^|_)(content_hash|file_hash|sha256|sha_256|md5|phash|perceptual_hash)$"
)
FILENAME_VALUE_RE = re.compile(r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,120}\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)\b")

POSITIVE_STAGE_STATUSES = {"passed", "pass", "complete", "completed", "executed", "success", "succeeded"}
NEGATIVE_STAGE_STATUSES = {"blocked", "blocked_before_write", "inconclusive", "skipped", "missing", "failed", "fail", "not_run"}
R1R_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES = {
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
}
R2_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES = set(R1R_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
R2_FORBIDDEN_TRUTH_TABLES = {
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_evidence",
    "blombooru_entity_external_identities",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_media_tags",
    "blombooru_tag_translations",
    "blombooru_tag_translation_jobs",
    "blombooru_provider_cache",
    "blombooru_negative_lookup_cache",
}
R2_EVIDENCE_EXECUTION_CODE_PATHS = {
    "backend/app/services/source_concept_resolver_service.py",
    "scripts/run_phase45_scv2_r2_constraint_aware_graph_remediation.py",
    "scripts/phase_contracts/contract_checks.py",
}
R2_REQUIRED_ISOLATION_FLAGS: dict[str, bool] = {
    "passed": True,
    "working_db_is_separate_from_r1r_baseline": True,
    "r1r_baseline_preserved": True,
    "dev_test_only": True,
    "production_profile_active": False,
    "production_write_attempted": False,
    "protected_source_write_attempted": False,
}
R2_REQUIRED_ROUTE_AUTHORIZATION_FLAGS: tuple[str, ...] = (
    "px1_b_authorized",
    "provider_2_authorized",
    "scale_up_authorized",
    "entity_bridge_authorized",
    "production_authorized",
    "full_library_execution_authorized",
    "source_concept_truth_promotion_authorized",
)
R2_REQUIRED_QUALITY_FLAGS: dict[str, bool] = {
    "route_metrics_recomputed": True,
    "meaningful_structural_improvement": True,
    "known_same_recall_protected": True,
    "compatible_same_accounting_complete": True,
    "constraint_safety_target_met": True,
    "fixed_evidence_preserved": True,
    "known_same_constraint_regression": False,
    "known_cannot_constraint_regression": False,
    "giant_component_remediation_improved": True,
    "search_quality_improved": False,
    "gap_quality_improved": False,
    "recall_closure_complete": False,
    "route_quality_ready_for_scale": False,
    "r2r_followup_required": True,
    "no_major_quality_regression": False,
}
R1R_INPUT_SCOPE_MIN_RATIO = 0.8
R1R_BASELINE_ONLY_INPUT_SCOPE_METRICS = {
    "source_concept_total",
    "source_concept_active",
    "source_concept_needs_review",
    "source_concept_superseded",
}
R1R_REQUIRED_INPUT_SCOPE_METRICS = (
    "total_media",
    "eligible_media",
    "source_metadata_records_total",
    "px1_source_metadata_records",
    "source_tag_observations",
    "source_name_observations",
    "source_searchable_name_assertions",
    "source_metadata_evidence",
    "resolver_input_signals",
    "deterministic_edge_count",
    "source_concept_replay_total",
    "source_concept_replay_active",
    "source_concept_replay_needs_review",
    "llm_eligible_pair_count",
    "llm_selected_pair_count",
)
REQUIRED_NON_EMPTY_PROOF_FIELDS = {
    "request_ledger",
    "failure_ledger",
    "import_ledger",
    "validation_pack",
    "review_pack",
    "public_report_copy",
}
S2G1X_PROBE_EVIDENCE_CODE_PATHS = (
    "scripts/run_s2g1_ai_tagging_capability_probe.py",
    "scripts/s2g_s3a_job_control.py",
)

REDACTED_VALUES = {
    "",
    "[redacted]",
    "<redacted>",
    "[private]",
    "<private>",
    "[omitted]",
    "omitted",
    "redacted",
    "null",
    "none",
}


def load_summary_file(path: str | Path) -> dict[str, Any]:
    summary_path = Path(path)
    with summary_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Summary must be a JSON object: {summary_path}")
    return payload


def check_phase_contract(contract_id: str, summary: Mapping[str, Any]) -> ContractCheckResult:
    contract = get_contract(contract_id)
    result = ContractCheckResult(contract_id=contract.contract_id)
    result.status = _contract_status(summary)
    result.route_approved = _route_approved(summary)
    result.target_met_claimed = _claim(summary, "target_met")
    result.full_chain_complete_claimed = _claim(summary, "full_chain_complete") or _claim(summary, "full_chain_completed")
    result.safe_to_merge_claimed = _claim(summary, "safe_to_merge")

    _check_claimed_contract_id(contract, summary, result)
    _check_required_fields(contract, summary, result)
    _check_forbidden_stages(contract, summary, result)

    for check_name in contract.custom_checks:
        checker = CUSTOM_CHECKS.get(check_name)
        if checker is None:
            result.fail("unknown_custom_check", f"Contract references unknown custom check {check_name!r}.")
            continue
        checker(contract, summary, result)

    result.details.setdefault("contract", contract.explain())
    return result


def _get(payload: Any, path: str, default: Any = MISSING) -> Any:
    cursor = payload
    for part in path.split("."):
        if isinstance(cursor, Mapping) and part in cursor:
            cursor = cursor[part]
        else:
            return default
    return cursor


def _has(payload: Any, path: str) -> bool:
    return _get(payload, path, MISSING) is not MISSING


def _has_non_null(payload: Any, path: str) -> bool:
    value = _get(payload, path, MISSING)
    return value is not MISSING and value is not None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1", "passed", "approved", "complete", "completed"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _contract_status(summary: Mapping[str, Any]) -> str | None:
    for path in (
        "final_route_decision_status",
        "route_decision.status",
        "pipeline_contract.status",
        "pipeline_contract.contract_status",
        "contract_status",
        "full_chain_status",
        "conclusion",
    ):
        value = _get(summary, path)
        if value is not MISSING and value is not None:
            return str(value)
    return None


def _claim(summary: Mapping[str, Any], key: str) -> bool:
    paths = (
        key,
        f"pipeline_contract.{key}",
        f"pipeline_contract.claims.{key}",
        f"claims.{key}",
        f"validation.{key}",
        f"decision_matrix.{key}",
        f"route_decision.{key}",
    )
    return any(_as_bool(_get(summary, path, False)) for path in paths)


def _route_approved(summary: Mapping[str, Any]) -> bool:
    for path in ("final_route_decision_status", "route_decision.status"):
        status = _get(summary, path)
        if status is not MISSING and status is not None and str(status).casefold() in {"route_approved", "approved", "approved_to_proceed"}:
            return True
    return _claim(summary, "route_approved") or _claim(summary, "approved")


def _completion_or_approval_claimed(result: ContractCheckResult) -> bool:
    return (
        result.target_met_claimed
        or result.route_approved
        or result.full_chain_complete_claimed
        or result.safe_to_merge_claimed
    )


def _declared_contract_id(summary: Mapping[str, Any]) -> Any:
    for path in ("pipeline_contract.contract_id", "contract_id", "contract.contract_id"):
        value = _get(summary, path, MISSING)
        if value is not MISSING and value is not None:
            return value
    return MISSING


def _check_claimed_contract_id(contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    if not (result.target_met_claimed or result.route_approved or result.full_chain_complete_claimed or result.safe_to_merge_claimed):
        return
    if contract.contract_id == "public_redaction_contract_v1":
        return
    declared = _declared_contract_id(summary)
    if declared is MISSING:
        result.fail(
            "claimed_completion_missing_contract_id",
            "Summaries claiming completion, approval, or safe_to_merge must declare the executable contract id.",
            path="pipeline_contract.contract_id",
            expected=contract.contract_id,
        )
        return
    if str(declared) != contract.contract_id:
        result.fail(
            "claimed_completion_contract_id_mismatch",
            "Summary contract id does not match the requested executable contract.",
            path="pipeline_contract.contract_id",
            expected=contract.contract_id,
            actual=declared,
        )


def _check_required_fields(contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    missing = [path for path in contract.required_summary_fields if not _has_non_null(summary, path)]
    result.details["missing_required_summary_fields"] = missing
    for path in missing:
        result.fail(
            "missing_required_summary_field",
            f"Required summary field {path!r} is missing.",
            path=path,
        )
    for path in contract.required_summary_fields:
        if path in missing or not _requires_non_empty_proof(path):
            continue
        value = _get(summary, path)
        if _empty_proof_value(value):
            result.fail(
                "empty_required_artifact_or_proof",
                f"Required artifact/proof field {path!r} must not be empty.",
                path=path,
            )


def _requires_non_empty_proof(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1]
    return leaf in REQUIRED_NON_EMPTY_PROOF_FIELDS


def _empty_proof_value(value: Any) -> bool:
    if value is MISSING or value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, Mapping):
        return len(value) == 0
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    return False


def _executed_stage_names(summary: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for path in ("executed_stages", "pipeline_contract.executed_stages", "stages.executed"):
        value = _get(summary, path)
        if isinstance(value, list):
            names.update(str(item) for item in value)
        elif isinstance(value, tuple):
            names.update(str(item) for item in value)
    for path in ("stages", "pipeline_contract.stages"):
        value = _get(summary, path)
        if isinstance(value, Mapping):
            for stage, stage_value in value.items():
                if isinstance(stage_value, Mapping):
                    status = str(stage_value.get("status", "")).casefold()
                    if status in NEGATIVE_STAGE_STATUSES:
                        continue
                    if (
                        status in POSITIVE_STAGE_STATUSES
                        or _as_bool(stage_value.get("executed"))
                        or _as_bool(stage_value.get("passed"))
                        or _as_bool(stage_value.get("completed"))
                    ):
                        names.add(str(stage))
                elif _as_bool(stage_value):
                    names.add(str(stage))
    return names


def _normalize_stage_name(value: Any) -> str:
    return re.sub(r"[\s_-]+", "_", str(value).strip().casefold())


def _check_forbidden_stages(contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    executed = _executed_stage_names(summary)
    normalized_executed = {_normalize_stage_name(stage) for stage in executed}
    forbidden_present = sorted(
        stage
        for stage in contract.forbidden_stages
        if _forbidden_stage_present(summary, executed, normalized_executed, stage)
    )
    result.details["executed_stages"] = sorted(executed)
    result.details["executed_stages_normalized"] = sorted(normalized_executed)
    result.details["forbidden_stages_present"] = forbidden_present
    for stage in forbidden_present:
        result.fail("forbidden_stage_executed", f"Forbidden stage {stage!r} is present/executed.", path=stage)


def _forbidden_stage_present(
    summary: Mapping[str, Any], executed: set[str], normalized_executed: set[str], stage: str
) -> bool:
    normalized_stage = _normalize_stage_name(stage)
    if stage in executed or normalized_stage in normalized_executed or _as_bool(_get(summary, stage, False)):
        return True
    for path in ("stages", "pipeline_contract.stages"):
        value = _get(summary, path)
        if isinstance(value, Mapping):
            stage_key = None
            if stage in value:
                stage_key = stage
            else:
                stage_key = next(
                    (candidate for candidate in value if _normalize_stage_name(candidate) == normalized_stage),
                    None,
                )
            if stage_key is None:
                continue
            stage_value = value[stage_key]
            if isinstance(stage_value, Mapping):
                if _as_bool(stage_value.get("executed")):
                    return True
                status = str(stage_value.get("status", "")).casefold()
                if status in POSITIVE_STAGE_STATUSES:
                    return True
            elif _as_bool(stage_value):
                return True
    return False


def _missing_required_stages(contract: PhaseContract, summary: Mapping[str, Any]) -> list[str]:
    executed = _executed_stage_names(summary)
    explicit_missing = _get(summary, "missing_required_stages", [])
    if explicit_missing is MISSING:
        explicit_missing = _get(summary, "pipeline_contract.missing_required_stages", [])
    missing = set()
    if isinstance(explicit_missing, list):
        missing.update(str(stage) for stage in explicit_missing)
    missing.update(stage for stage in contract.required_stages if stage not in executed)
    missing.update(stage for stage in _non_completed_stage_names(summary) if stage in contract.required_stages)
    return sorted(missing)


def _non_completed_stage_names(summary: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for path in ("stages", "pipeline_contract.stages"):
        value = _get(summary, path)
        if not isinstance(value, Mapping):
            continue
        for stage, stage_value in value.items():
            if isinstance(stage_value, Mapping) and str(stage_value.get("status", "")).casefold() in NEGATIVE_STAGE_STATUSES:
                names.add(str(stage))
    return names


def _safe_redacted(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if text.casefold() in REDACTED_VALUES:
        return True
    return text.startswith("redacted_") or text.startswith("[redacted") or text.endswith("_redacted")


def _safe_public_provenance_marker(raw_path: str, value: Any) -> bool:
    key_name = raw_path.rsplit(".", 1)[-1]
    return key_name == "source_root_public_marker" and str(value).strip() == "audited-root"


def _safe_public_artifact_label(raw_path: str, value: Any) -> bool:
    key_name = raw_path.rsplit(".", 1)[-1]
    if key_name not in {
        "private_artifact_label",
        "evidence_artifact_label",
        "stage_manifest_artifact",
        "private_artifact_root_label",
        "durable_cache_root_label",
        "label",
        "artifact_label",
    }:
        return False
    text = str(value or "").strip()
    if not text:
        return True
    if WINDOWS_PATH_RE.search(text) or UNC_PATH_RE.search(text) or FILE_URI_RE.search(text) or POSIX_PRIVATE_PATH_RE.search(text):
        return False
    return text.startswith(("r1r-private-", "a1r-private-")) or text in {
        "[private]",
        "[blocked]",
        "source-concept-llm-adjudication-cache",
    }


def _safe_public_context_value(raw_path: str, value: Any) -> bool:
    key_name = raw_path.rsplit(".", 1)[-1]
    text = str(value or "").strip()
    if text and _redaction_findings_for_text(text, raw_path, kind="value"):
        return False
    if ".route_authorization." in raw_path:
        return True
    if isinstance(value, bool) and (
        key_name.endswith("_authorized")
        or key_name.endswith("_started")
        or key_name.endswith("_attempted")
        or key_name in {
            "a1r_still_required",
            "no_secret_leakage",
            "broad_downstream_work",
            "production_or_truth_work",
            "raw_db_url_recorded",
            "raw_local_paths_recorded_in_public",
            "required_operator_approval_for_next_phase",
            "production_db_storage_source_roots_private_ledgers_used_as_fixtures",
        }
    ):
        return True
    if isinstance(value, (int, float)) and key_name in {
        "projected_input_tokens",
        "projected_output_tokens",
    }:
        return True
    if key_name == "storage_root_label" and text in {
        "dedicated_test_storage",
        "development_storage",
        "restored_snapshot_storage",
        "test_storage",
    }:
        return True
    return False


def _format_json_path(parent: str, segment: str | int) -> str:
    if isinstance(segment, int):
        return f"{parent}[{segment}]"
    return f"{parent}.{segment}"


def _diagnostic_key_segment(key_text: str) -> str:
    if _key_needs_redaction_in_path(key_text):
        return "[redacted-key]"
    return key_text


def _key_needs_redaction_in_path(key_text: str) -> bool:
    if not key_text:
        return False
    if WINDOWS_PATH_RE.search(key_text) or UNC_PATH_RE.search(key_text) or FILE_URI_RE.search(key_text):
        return True
    if POSIX_PRIVATE_PATH_RE.search(key_text) or TOKEN_RE.search(key_text) or HTTP_URL_RE.search(key_text):
        return True
    return FILENAME_VALUE_RE.search(key_text) is not None


def _iter_json_values(payload: Any, raw_path: str = "$", display_path: str = "$") -> Iterable[tuple[str, str, str, Any]]:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            child_raw = _format_json_path(raw_path, key_text)
            child_display = _format_json_path(display_path, _diagnostic_key_segment(key_text))
            yield child_raw, child_display, "key", key_text
            if isinstance(value, (Mapping, list)) and not value and (
                _path_has_secret_context(child_raw) or _path_has_private_provenance_context(child_raw)
            ):
                yield child_raw, child_display, "empty_container", value
            yield from _iter_json_values(value, child_raw, child_display)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            child = _format_json_path(raw_path, index)
            child_display = _format_json_path(display_path, index)
            yield from _iter_json_values(item, child, child_display)
    elif isinstance(payload, (str, int, float, bool)) or payload is None:
        yield raw_path, display_path, "value", payload


def _path_has_private_provenance_context(path: str) -> bool:
    segments = [segment for segment in re.split(r"[.\[\]]+", path) if segment and segment != "$" and not segment.isdigit()]
    return any(PRIVATE_PROVENANCE_KEY_RE.search(segment) for segment in segments)


def _path_has_private_content_hash_context(path: str) -> bool:
    segments = [segment for segment in re.split(r"[.\[\]]+", path) if segment and segment != "$" and not segment.isdigit()]
    return any(PRIVATE_CONTENT_HASH_KEY_RE.search(segment) for segment in segments)


def _path_has_secret_context(path: str) -> bool:
    segments = [segment for segment in re.split(r"[.\[\]]+", path) if segment and segment != "$" and not segment.isdigit()]
    return any(SECRET_CONTEXT_KEY_RE.search(segment) for segment in segments)


def _redacted_match_payload(code: str, raw: str) -> dict[str, str | int]:
    return {
        "code": code,
        "match": "[redacted-match]",
        "match_category": code,
        "match_length": len(raw),
    }


def _redaction_findings_for_text(text: str, path: str, *, kind: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    checks = (
        ("windows_local_path", WINDOWS_PATH_RE),
        ("unc_local_path", UNC_PATH_RE),
        ("file_uri", FILE_URI_RE),
        ("posix_private_path", POSIX_PRIVATE_PATH_RE),
        ("db_url", DB_URL_RE),
        ("common_secret_or_token", TOKEN_RE),
    )
    for code, pattern in checks:
        match = pattern.search(text)
        if match:
            finding = {"path": path, "kind": kind}
            finding.update(_redacted_match_payload(code, match.group(0)))
            findings.append(finding)
    return findings


def scan_public_payload(payload: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for raw_path, display_path, kind, value in _iter_json_values(payload):
        text = value if isinstance(value, str) else None
        if text is not None:
            findings.extend(_redaction_findings_for_text(text, display_path, kind=kind))
        key_name = raw_path.rsplit(".", 1)[-1]
        if text is not None and not _safe_redacted(text):
            match = FILENAME_VALUE_RE.search(text)
            if match:
                finding = {"path": display_path, "kind": kind}
                finding.update(_redacted_match_payload("bare_filename", match.group(0)))
                findings.append(finding)
        secret_context = kind in {"value", "empty_container"} and _path_has_secret_context(raw_path)
        provenance_context = kind in {"value", "empty_container"} and _path_has_private_provenance_context(raw_path)
        content_hash_context = kind in {"value", "empty_container"} and _path_has_private_content_hash_context(raw_path)
        if secret_context and not _safe_redacted(value) and not _safe_public_context_value(raw_path, value):
            finding = {"path": display_path, "kind": kind}
            finding.update(_redacted_match_payload("secret_key_name_with_unredacted_value", key_name))
            findings.append(finding)
        if (
            provenance_context
            and not _safe_redacted(value)
            and not _safe_public_provenance_marker(raw_path, value)
            and not _safe_public_artifact_label(raw_path, value)
            and not _safe_public_context_value(raw_path, value)
        ):
            finding = {"path": display_path, "kind": kind}
            finding.update(_redacted_match_payload("private_provenance_value_unredacted", key_name))
            findings.append(finding)
        if content_hash_context and not _safe_redacted(value):
            finding = {"path": display_path, "kind": kind}
            finding.update(_redacted_match_payload("private_content_hash_value_unredacted", key_name))
            findings.append(finding)
        if text is not None and kind == "key" and (SECRET_KEY_NAME_RE.search(text) or PRIVATE_PROVENANCE_KEY_RE.search(text)):
            # Key names are not automatically failures; values decide whether a
            # public field is unsafe. Path-like key text is still caught above.
            continue
        if text is not None and kind == "key" and PRIVATE_CONTENT_HASH_KEY_RE.search(text):
            finding = {"path": display_path, "kind": kind}
            finding.update(_redacted_match_payload("private_content_hash_key_present", text))
            findings.append(finding)
    return findings


def _check_python_env(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    env = _get(summary, "python_env", {})
    if not isinstance(env, Mapping):
        result.fail("python_env_not_object", "python_env must be an object.", path="python_env")
        return
    if not _as_bool(env.get("expected_python_checked")):
        result.fail("python_expected_path_not_checked", "Expected Python executable path was not checked.", path="python_env.expected_python_checked")
    if not _as_bool(env.get("check_python_env_passed")):
        result.fail("python_env_check_failed", "scripts/check_python_env.py did not pass.", path="python_env.check_python_env_passed")
    if not _as_bool(env.get("executable_path_redacted")):
        result.fail("python_path_not_redacted", "Public summary must record only redacted executable identity.", path="python_env.executable_path_redacted")
    public_name = str(env.get("public_executable_name") or "")
    if "\\" in public_name or "/" in public_name:
        result.fail("python_public_name_contains_path", "Public Python executable name contains a path.", path="python_env.public_executable_name")


def _check_postgres_db(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    identity = _get(summary, "db_identity", {})
    if not isinstance(identity, Mapping):
        result.fail("db_identity_not_object", "db_identity must be an object.", path="db_identity")
        return
    resolution = identity.get("db_resolution") if isinstance(identity.get("db_resolution"), Mapping) else {}
    secret_findings = _db_secret_findings(identity, path="db_identity")
    if _as_bool(resolution.get("password_value_recorded")) or secret_findings:
        result.fail("db_password_recorded", "DB password values must never be recorded.", path="db_identity")
        for finding in secret_findings:
            result.fail("db_secret_field_recorded", "DB identity contains a nested password/secret-looking value.", path=finding["path"], actual=finding["key"])
    if not _as_bool(resolution.get("runner_matches_app_equivalent") or resolution.get("urls_match") or identity.get("app_compatible")):
        result.fail("db_url_equivalence_not_proven", "Expected app-compatible DB URL equivalence was not proven.", path="db_identity.db_resolution")
    destructive = _get(summary, "destructive_operation", {})
    if isinstance(destructive, Mapping) and _as_bool(destructive.get("executed")):
        result.fail("destructive_operation_without_contract", "Destructive operations require destructive_operation_contract_v1.", path="destructive_operation")


def _db_secret_findings(payload: Any, *, path: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            key_path = f"{path}.{key_text}"
            folded = key_text.casefold()
            secret_key = SECRET_KEY_NAME_RE.search(key_text) or "credential" in folded
            boolean_secret_marker = isinstance(value, bool) and (
                folded in {"password_present", "password_configured", "password_required", "password_value_recorded"}
                or folded.endswith("_present")
                or folded.endswith("_recorded")
            )
            if secret_key:
                if folded == "password_value_recorded" and _as_bool(value):
                    findings.append({"path": key_path, "key": key_text})
                elif boolean_secret_marker:
                    pass
                elif not _safe_redacted(value):
                    findings.append({"path": key_path, "key": key_text})
            if not isinstance(value, bool):
                findings.extend(_db_secret_findings(value, path=key_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(_db_secret_findings(item, path=f"{path}[{index}]"))
    elif isinstance(payload, str):
        if re.search(r"(?i)(password|pwd)\s*[:=]\s*[^;\s]+", payload) or re.search(r"://[^/\s:@]+:[^@\s/]+@", payload):
            findings.append({"path": path, "key": "string_contains_db_secret"})
    return findings


def _check_required_boolean_paths(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    paths: Iterable[str],
    *,
    code: str,
    message: str,
) -> None:
    for path in paths:
        if not _as_bool(_get(summary, path, False)):
            result.fail(code, message, path=path, expected=True, actual=_get(summary, path, None))


def _check_media_import(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    _check_required_boolean_paths(
        summary,
        result,
        ("source_root_safety_proof.passed", "staging_root_safety_proof.passed", "duplicate_path_leak_proof.passed", "mutation_proof.passed"),
        code="media_import_required_proof_failed",
        message="Media import contract requires source/staging safety, duplicate/path leak proof, and mutation proof.",
    )
    if _as_bool(_get(summary, "safety.source_icloud_mutation", False)):
        result.fail("media_import_source_mutation", "Source/iCloud mutation is forbidden unless separately approved.", path="safety.source_icloud_mutation")


def _check_classification(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    if _as_int(_get(summary, "eligible_denominator", 0)) <= 0:
        result.fail("classification_missing_denominator", "Classification requires a positive eligible denominator.", path="eligible_denominator")
    if _as_bool(_get(summary, "safety.source_or_storage_mutation", False)):
        result.fail("classification_source_storage_mutation", "Classification must not mutate source/storage.", path="safety.source_or_storage_mutation")
    if not _as_bool(_get(summary, "mutation_proof.passed", False)):
        result.fail("classification_mutation_proof_missing", "Classification requires mutation proof.", path="mutation_proof.passed")


def _check_ai_tagging(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    if _as_int(_get(summary, "eligible_media_denominator", 0)) <= 0:
        result.fail("ai_tagging_missing_denominator", "AI tagging requires a positive eligible media denominator.", path="eligible_media_denominator")
    if not _as_bool(_get(summary, "manual_truth_overwrite_proof.passed", False)):
        result.fail("ai_tagging_truth_overwrite_proof_missing", "AI tagging must prove manual/truth tags were not overwritten.", path="manual_truth_overwrite_proof.passed")
    if not _as_bool(_get(summary, "mutation_proof.passed", False)):
        result.fail("ai_tagging_mutation_proof_missing", "AI tagging requires mutation proof.", path="mutation_proof.passed")


def _check_localization(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    if _has(summary, "provider_policy.llm_enabled") and _as_bool(_get(summary, "provider_policy.llm_enabled")):
        _check_required_boolean_paths(
            summary,
            result,
            ("provider_policy.explicitly_approved", "rate_limit_cache_retry_accounting.passed"),
            code="localization_llm_policy_missing",
            message="LLM/provider localization requires approval plus rate/cache/retry accounting.",
        )
    if _as_bool(_get(summary, "mutation_proof.unrelated_tag_or_media_mutation", False)):
        result.fail("localization_unrelated_mutation", "Localization must not mutate unrelated tag/media state.", path="mutation_proof.unrelated_tag_or_media_mutation")


def _check_source_metadata(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    _check_required_boolean_paths(
        summary,
        result,
        (
            "provider_policy.explicitly_approved",
            "provider_identity.no_secret_logging",
            "cache_retry_rate_limit_accounting.passed",
            "source_metadata_write_allowlist.passed",
            "entity_truth_proof.no_entity_truth",
            "media_tags_mutation_proof.no_media_tags_mutation",
        ),
        code="source_metadata_required_gate_missing",
        message="Source metadata phases require provider, ledger, write allowlist, and no-truth/no-media_tags gates.",
    )
    if _as_bool(_get(summary, "image_upload_policy.uploaded_images", False)) and not _as_bool(_get(summary, "image_upload_policy.separately_approved", False)):
        result.fail("source_metadata_unapproved_image_upload", "Image upload requires separate explicit approval.", path="image_upload_policy")


def _check_source_concept_full_chain(contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    status = result.status or "unknown"
    result.details["source_concept_allowed_statuses"] = list(SOURCE_CONCEPT_ALLOWED_STATUSES)
    if status not in SOURCE_CONCEPT_ALLOWED_STATUSES:
        result.fail("source_concept_unknown_status", "SourceConcept full-chain status is not one of the allowed executable statuses.", path="pipeline_contract.status", expected=list(SOURCE_CONCEPT_ALLOWED_STATUSES), actual=status)

    contract_id = _get(summary, "pipeline_contract.contract_id")
    if contract_id is not MISSING and contract_id != contract.contract_id:
        result.fail("pipeline_contract_id_mismatch", "Summary pipeline_contract.contract_id does not match requested contract.", path="pipeline_contract.contract_id", expected=contract.contract_id, actual=contract_id)

    missing = _missing_required_stages(contract, summary)
    full_chain_claimed = status == "full_chain_completed" or result.full_chain_complete_claimed or _as_bool(_get(summary, "full_chain_completed", False))
    if full_chain_claimed:
        for stage in missing:
            result.fail("source_concept_required_stage_missing", f"Full-chain completion is missing required stage {stage!r}.", path="executed_stages", expected=stage)
    elif missing:
        result.warn("source_concept_stage_missing_non_complete_status", "Required stages are missing, but the summary is not claiming full-chain completion.", path="missing_required_stages", actual=missing)

    explicit_missing = _get(summary, "missing_required_stages", [])
    if isinstance(explicit_missing, list) and explicit_missing and full_chain_claimed:
        result.fail("source_concept_explicit_missing_stages", "Full-chain completion cannot have missing_required_stages.", path="missing_required_stages", expected=[], actual=explicit_missing)

    plan = _get(summary, "llm_adjudication_plan", {})
    plan_mapping = plan if isinstance(plan, Mapping) else {}
    llm_required = _as_bool(plan_mapping.get("required", True))
    llm_used = _as_bool(_get(summary, "llm_adjudication_used", False))
    llm_judgment_count_recorded = _has(summary, "llm_judgment_count")
    judgment_count = _as_int(_get(summary, "llm_judgment_count", 0))
    zero_eligible = _zero_eligible_proof_passed(plan_mapping)
    zero_eligible_reason_present = _zero_eligible_reason_present(plan_mapping)
    eligible_pair_count_recorded = "eligible_pair_count" in plan_mapping
    selected_pair_count_recorded = "selected_pair_count" in plan_mapping
    eligible = _as_int(plan_mapping.get("eligible_pair_count", plan_mapping.get("selected_block_count", 0)))
    max_calls = _as_int(plan_mapping.get("max_calls", _get(summary, "llm_max_calls", 0)))
    selected = _as_int(plan_mapping.get("selected_pair_count", plan_mapping.get("selected_block_count", 0)))
    budget = _as_float(plan_mapping.get("budget_usd", _get(summary, "llm_budget_usd", 0.0)))
    projected = _as_float(plan_mapping.get("projected_budget_usd", plan_mapping.get("projected_cost_usd", 0.0)))
    approved_overage = _as_bool(plan_mapping.get("explicit_over_budget_or_call_cap_approval"))
    blocked_status = status.startswith("full_chain_blocked")
    eligible_pairs_exist = eligible > 0
    valid_zero_eligible_proof = (
        zero_eligible
        and eligible_pair_count_recorded
        and selected_pair_count_recorded
        and llm_judgment_count_recorded
        and eligible == 0
        and selected == 0
        and judgment_count == 0
        and zero_eligible_reason_present
    )
    llm_evidence_required = full_chain_claimed and not valid_zero_eligible_proof
    if full_chain_claimed:
        required_counters = (
            ("eligible_pair_count", eligible_pair_count_recorded, "llm_adjudication_plan.eligible_pair_count"),
            ("selected_pair_count", selected_pair_count_recorded, "llm_adjudication_plan.selected_pair_count"),
            ("llm_judgment_count", llm_judgment_count_recorded, "llm_judgment_count"),
        )
        for counter_name, recorded, counter_path in required_counters:
            if not recorded:
                result.fail(
                    "source_concept_missing_llm_counter",
                    "Full-chain completion requires explicit LLM pair and judgment counters.",
                    path=counter_path,
                    expected=counter_name,
                )
    if full_chain_claimed and zero_eligible and not valid_zero_eligible_proof:
        result.fail(
            "source_concept_zero_eligible_proof_incomplete",
            "Zero-eligible LLM proof requires zero_eligible_proof=true, eligible_pair_count=0, selected_pair_count=0, llm_judgment_count=0, and a reason.",
            path="llm_adjudication_plan.zero_eligible_proof",
        )
    if full_chain_claimed and not llm_required and not valid_zero_eligible_proof:
        code = "source_concept_llm_required_opt_out_with_eligible_pairs" if eligible_pairs_exist else "source_concept_llm_required_opt_out_without_zero_eligible_proof"
        result.fail(
            code,
            "Full-chain completion cannot mark LLM adjudication not required without explicit zero-eligible proof.",
            path="llm_adjudication_plan.required",
            expected=True,
            actual=llm_required,
        )
    if llm_evidence_required:
        if not llm_used and not blocked_status:
            result.fail("source_concept_llm_required_missing", "Full-chain SourceConcept replay cannot silently skip LLM adjudication.", path="llm_adjudication_used", expected=True, actual=llm_used)
        if judgment_count <= 0:
            result.fail("source_concept_zero_llm_judgments_full_chain", "Full-chain completion requires LLM judgments or explicit zero-eligible proof.", path="llm_judgment_count", expected="> 0", actual=judgment_count)
    if full_chain_claimed and not _as_bool(_get(summary, "full_chain_fidelity_passed", False)):
        result.fail("source_concept_fidelity_not_passed", "Full-chain completion requires full_chain_fidelity_passed=true.", path="full_chain_fidelity_passed")

    if status == "deterministic_only" and _completion_or_approval_claimed(result):
        result.fail("deterministic_only_claimed_completion", "deterministic_only summaries must not claim target_met, route_approved, full_chain_complete, or safe_to_merge.", path="pipeline_contract.status")

    if status.startswith("full_chain_blocked") or status == "full_chain_inconclusive_missing_artifacts":
        if _completion_or_approval_claimed(result):
            result.fail("blocked_status_claimed_completion", "Blocked/inconclusive full-chain summaries must not claim completion, approval, or safe_to_merge.", path="pipeline_contract.status")

    if plan_mapping:
        plan_status = str(plan_mapping.get("status", "")).casefold()
        if full_chain_claimed and plan_status in {"unavailable", "blocked", "blocked_llm_unavailable", "over_budget", "blocked_budget"}:
            result.fail("source_concept_completed_with_blocked_llm_plan", "LLM unavailable/over-budget plans must use blocked status, not full_chain_completed.", path="llm_adjudication_plan.status", actual=plan_mapping.get("status"))
        if full_chain_claimed and eligible > max_calls and not approved_overage:
            result.fail("source_concept_llm_call_cap_exceeded", "Eligible LLM pairs exceed max_calls; full-chain completion cannot proceed without explicit approval.", path="llm_adjudication_plan")
        if full_chain_claimed and selected > max_calls and not approved_overage:
            result.fail("source_concept_llm_selected_call_cap_exceeded", "Selected LLM pairs exceed max_calls; full-chain completion cannot proceed without explicit approval.", path="llm_adjudication_plan")
        if full_chain_claimed and judgment_count > max_calls and not approved_overage:
            result.fail("source_concept_llm_judgment_call_cap_exceeded", "LLM judgment count exceeds max_calls; full-chain completion cannot proceed without explicit approval.", path="llm_judgment_count", expected=f"<= {max_calls}", actual=judgment_count)
        if full_chain_claimed and selected > 0 and not _llm_selected_pairs_resolved(summary, selected, judgment_count):
            result.fail(
                "source_concept_llm_selected_pairs_not_resolved",
                "Full-chain completion requires proof that every selected LLM pair was judged, cached, or explicitly skipped.",
                path="llm_adjudication_plan.selected_pair_count",
                expected=f"resolved >= {selected}",
            )
        if full_chain_claimed and budget >= 0 and projected > budget and not approved_overage:
            result.fail("source_concept_llm_budget_exceeded", "Projected LLM budget exceeds approved budget; phase must block/request approval.", path="llm_adjudication_plan")

    if full_chain_claimed:
        _check_required_boolean_paths(
            summary,
            result,
            ("mutation_proof.passed", "post_commit_verification.passed", "validation_pack.generated", "review_pack.generated"),
            code="source_concept_required_proof_missing",
            message="Full-chain SourceConcept completion requires mutation, post-commit, validation-pack, and review-pack proof.",
        )


def _check_r1r_full_source_concept_pipeline(
    contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult
) -> None:
    status = result.status or "unknown"
    result.details["r1r_allowed_statuses"] = list(R1R_FULL_SOURCE_CONCEPT_PIPELINE_STATUSES)
    if status not in R1R_FULL_SOURCE_CONCEPT_PIPELINE_STATUSES:
        result.fail(
            "r1r_unknown_status",
            "R1R full SourceConcept replay status is not one of the allowed executable statuses.",
            path="pipeline_contract.status",
            expected=list(R1R_FULL_SOURCE_CONCEPT_PIPELINE_STATUSES),
            actual=status,
        )

    target_met_full_chain = (
        status == "target_met_full_chain"
        or result.target_met_claimed
        or result.full_chain_complete_claimed
        or _as_bool(_get(summary, "sc1_full_chain_proof.complete_sc1_pipeline_executed", False))
    )
    if status != "target_met_full_chain" and _completion_or_approval_claimed(result):
        result.fail(
            "r1r_non_target_status_claimed_completion",
            "Only target_met_full_chain may claim target_met, full_chain_complete, route approval, or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met_full_chain",
            actual=status,
        )

    _check_r1r_environment_isolation(summary, result, target_met_full_chain=target_met_full_chain)
    _check_r1r_input_scope_fidelity(summary, result, status=status, target_met_full_chain=target_met_full_chain)
    _check_r1r_stage_manifest(contract, summary, result, target_met_full_chain=target_met_full_chain)
    _check_r1r_llm_truthfulness(summary, result, status=status, target_met_full_chain=target_met_full_chain)
    _check_r1r_write_scope(summary, result, target_met_full_chain=target_met_full_chain)
    _check_r1r_review_redaction(summary, result, target_met_full_chain=target_met_full_chain)
    _check_r1r_route_gate(summary, result, target_met_full_chain=target_met_full_chain)


def _check_r1r_environment_isolation(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    target_met_full_chain: bool,
) -> None:
    env = _get(summary, "environment_isolation", {})
    if not isinstance(env, Mapping):
        result.fail("r1r_environment_isolation_not_object", "R1R requires environment_isolation proof.", path="environment_isolation")
        return
    blockers = env.get("blockers") if isinstance(env.get("blockers"), list) else []
    if target_met_full_chain:
        if not _as_bool(env.get("passed")):
            result.fail(
                "r1r_environment_isolation_aggregate_failed_for_target",
                "target_met_full_chain requires environment_isolation.passed=true.",
                path="environment_isolation.passed",
                expected=True,
                actual=env.get("passed"),
            )
        if blockers:
            result.fail(
                "r1r_environment_isolation_blockers_present_for_target",
                "target_met_full_chain requires environment_isolation.blockers to be empty.",
                path="environment_isolation.blockers",
                expected=[],
                actual=blockers,
            )
    required_false = (
        "production_profile_active",
        "violet_env_is_production",
        "db_target_is_production",
        "storage_root_is_production",
        "source_icloud_app_storage_write_target",
        "dynamic_production_launcher_used",
        "production_db_storage_source_roots_private_ledgers_used_as_fixtures",
        "production_write_attempted",
    )
    for key in required_false:
        if _as_bool(env.get(key)):
            result.fail(
                "r1r_environment_isolation_failed",
                f"R1R environment isolation requires {key}=false.",
                path=f"environment_isolation.{key}",
                expected=False,
                actual=env.get(key),
            )
    if not _as_bool(env.get("dev_test_restored_snapshot_db_used")):
        result.fail(
            "r1r_dev_test_restored_snapshot_db_required",
            "R1R execution must use a dev/test/restored-snapshot DB.",
            path="environment_isolation.dev_test_restored_snapshot_db_used",
            expected=True,
            actual=env.get("dev_test_restored_snapshot_db_used"),
        )
    db_name = str(env.get("db_name") or "").strip().casefold()
    if db_name in {"blombooru", "production", "main", "postgres"}:
        result.fail(
            "r1r_production_db_name_rejected",
            "R1R must not target production-like database names.",
            path="environment_isolation.db_name",
            expected="dev/test/restored-snapshot DB name",
            actual=env.get("db_name"),
        )
    actual = env.get("exact_db_identity_from_actual_connection")
    if isinstance(actual, Mapping):
        if target_met_full_chain and not _as_bool(actual.get("passed")):
            result.fail(
                "r1r_actual_db_identity_gate_failed_for_target",
                "target_met_full_chain requires exact_db_identity_from_actual_connection.passed=true.",
                path="environment_isolation.exact_db_identity_from_actual_connection.passed",
                expected=True,
                actual=actual.get("passed"),
            )
        if not _as_bool(actual.get("checked_from_actual_connection")):
            result.fail(
                "r1r_actual_db_identity_not_checked",
                "R1R must validate the exact DB connection used for inventory and writes.",
                path="environment_isolation.exact_db_identity_from_actual_connection.checked_from_actual_connection",
            )
        actual_db = str(actual.get("db_name") or "").strip().casefold()
        if actual_db in {"blombooru", "production", "main", "postgres"} or "production" in actual_db:
            result.fail(
                "r1r_actual_connection_production_db_rejected",
                "The exact DB connection used by R1R points at a production-like DB.",
                path="environment_isolation.exact_db_identity_from_actual_connection.db_name",
                expected="dev/test/restored-snapshot DB name",
                actual=actual.get("db_name"),
            )
        if not _as_bool(actual.get("dev_test_restored_snapshot_db_used")):
            result.fail(
                "r1r_actual_connection_dev_test_snapshot_required",
                "The exact DB connection used by R1R must be dev/test/restored-snapshot.",
                path="environment_isolation.exact_db_identity_from_actual_connection.dev_test_restored_snapshot_db_used",
                expected=True,
                actual=actual.get("dev_test_restored_snapshot_db_used"),
            )
    elif _as_bool(env.get("passed")):
        result.fail(
            "r1r_actual_db_identity_missing",
            "R1R must record exact DB identity from the connection used for reads/writes.",
            path="environment_isolation.exact_db_identity_from_actual_connection",
        )
    storage_gate = env.get("storage_root_pre_settings_import")
    if not isinstance(storage_gate, Mapping) or not _as_bool(storage_gate.get("checked_before_settings_import")):
        result.fail(
            "r1r_storage_pre_settings_gate_missing",
            "R1R must classify VIOLET_STORAGE_ROOT before importing app settings.",
            path="environment_isolation.storage_root_pre_settings_import",
        )
    elif not _as_bool(storage_gate.get("passed")):
        result.fail(
            "r1r_storage_pre_settings_gate_failed",
            "R1R storage root must not overlap production, source/iCloud, app-managed, or protected roots.",
            path="environment_isolation.storage_root_pre_settings_import.passed",
            expected=True,
            actual=storage_gate.get("passed"),
        )
    output_gate = env.get("output_dir_safety")
    if target_met_full_chain:
        if not isinstance(storage_gate, Mapping) or not _as_bool(storage_gate.get("passed")):
            result.fail(
                "r1r_storage_pre_settings_gate_required_for_target",
                "target_met_full_chain requires storage_root_pre_settings_import.passed=true.",
                path="environment_isolation.storage_root_pre_settings_import.passed",
                expected=True,
                actual=storage_gate.get("passed") if isinstance(storage_gate, Mapping) else None,
            )
        if not isinstance(output_gate, Mapping) or not _as_bool(output_gate.get("passed")):
            result.fail(
                "r1r_output_dir_safety_gate_required_for_target",
                "target_met_full_chain requires output_dir_safety.passed=true.",
                path="environment_isolation.output_dir_safety.passed",
                expected=True,
                actual=output_gate.get("passed") if isinstance(output_gate, Mapping) else None,
            )


def _check_r1r_input_scope_fidelity(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    status: str,
    target_met_full_chain: bool,
) -> None:
    scope = _get(summary, "input_scope_fidelity", {})
    if not isinstance(scope, Mapping):
        result.fail("r1r_input_scope_fidelity_missing", "R1R requires input_scope_fidelity proof.", path="input_scope_fidelity")
        return
    table = scope.get("comparison_table")
    rows_by_metric: dict[str, Mapping[str, Any]] = {}
    if not isinstance(table, list) or not table:
        result.fail(
            "r1r_input_scope_comparison_missing",
            "R1R input-scope fidelity requires old R1 expected/current R1R actual/ratio/status rows.",
            path="input_scope_fidelity.comparison_table",
        )
    else:
        for index, row in enumerate(table):
            if not isinstance(row, Mapping):
                result.fail("r1r_input_scope_row_not_object", "Input-scope rows must be objects.", path=f"input_scope_fidelity.comparison_table[{index}]")
                continue
            metric = str(row.get("metric") or "")
            if metric:
                rows_by_metric[metric] = row
            missing = [
                key
                for key in ("metric", "old_r1_expected", "current_r1r_actual", "ratio", "status")
                if row.get(key) is None
            ]
            if missing:
                result.fail(
                    "r1r_input_scope_row_missing_field",
                    "Input-scope rows require metric, expected, actual, ratio, and status.",
                    path=f"input_scope_fidelity.comparison_table[{index}]",
                    actual=missing,
                )
        missing_required_rows = [metric for metric in R1R_REQUIRED_INPUT_SCOPE_METRICS if metric not in rows_by_metric]
        for metric in missing_required_rows:
            result.fail(
                "r1r_input_scope_required_metric_missing",
                "R1R input-scope fidelity rows must include every required old-R1 comparison metric.",
                path="input_scope_fidelity.comparison_table",
                expected=metric,
            )
    derived_failed_metrics: list[str] = []
    for metric in R1R_REQUIRED_INPUT_SCOPE_METRICS:
        row = rows_by_metric.get(metric)
        if not isinstance(row, Mapping):
            derived_failed_metrics.append(metric)
            continue
        expected = _as_float(row.get("old_r1_expected"), default=0.0)
        actual = _as_float(row.get("current_r1r_actual"), default=0.0)
        row_ratio = row.get("ratio")
        ratio = _as_float(row_ratio, default=(actual / expected if expected > 0 else 0.0))
        if expected <= 0 or ratio < R1R_INPUT_SCOPE_MIN_RATIO or actual < expected * R1R_INPUT_SCOPE_MIN_RATIO:
            derived_failed_metrics.append(metric)
    derived_pass = not derived_failed_metrics
    passed_claim = _as_bool(scope.get("passed"))
    route_allowed_claim = _as_bool(scope.get("route_evidence_allowed"))
    failed_metrics_claim = scope.get("failed_metrics") if isinstance(scope.get("failed_metrics"), list) else []
    result.details["r1r_input_scope_derived_failed_metrics"] = derived_failed_metrics
    if (passed_claim or route_allowed_claim) and not derived_pass:
        result.fail(
            "r1r_input_scope_claim_not_supported_by_rows",
            "R1R input-scope pass/route evidence claims must be recomputed from required comparison rows.",
            path="input_scope_fidelity.comparison_table",
            expected="all required metric ratios >= 0.8",
            actual=derived_failed_metrics,
        )
    if derived_pass and failed_metrics_claim:
        result.fail(
            "r1r_input_scope_failed_metrics_claim_mismatch",
            "R1R input-scope failed_metrics must match contract-derived comparison row status.",
            path="input_scope_fidelity.failed_metrics",
            expected=[],
            actual=failed_metrics_claim,
        )
    if target_met_full_chain and (not derived_pass or not passed_claim or not route_allowed_claim or failed_metrics_claim):
        result.fail(
            "r1r_target_met_with_insufficient_input_scope",
            "target_met_full_chain requires old-R1-equivalent source-layer input scope, not a tiny fixture.",
            path="input_scope_fidelity",
            expected={"passed": True, "route_evidence_allowed": True, "failed_metrics": []},
            actual={
                "passed": passed_claim,
                "route_evidence_allowed": route_allowed_claim,
                "failed_metrics": failed_metrics_claim,
                "derived_failed_metrics": derived_failed_metrics,
            },
        )
    if not derived_pass and status == "target_met_full_chain":
        result.fail(
            "r1r_input_scope_failure_not_blocked",
            "Insufficient input scope must use smoke_only_not_route_evidence or blocked_insufficient_input_scope, not target_met_full_chain.",
            path="pipeline_contract.status",
            expected="smoke_only_not_route_evidence",
            actual=status,
        )
    if not derived_pass and status not in {
        "smoke_only_not_route_evidence",
        "blocked_insufficient_input_scope",
        "blocked_environment_or_snapshot_unavailable",
        "blocked_snapshot_unavailable",
        "blocked_snapshot_restore_required",
        "blocked_operator_clone_approval_required",
        "blocked_environment_isolation",
        "blocked_contract",
    }:
        result.fail(
            "r1r_input_scope_failure_wrong_status",
            "Insufficient old-R1 scope must block or be classified as smoke-only.",
            path="pipeline_contract.status",
            expected="smoke_only_not_route_evidence",
            actual=status,
        )


def _check_r1r_stage_manifest(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    target_met_full_chain: bool,
) -> None:
    manifest = _get(summary, "sc1_required_stage_manifest", MISSING)
    if not isinstance(manifest, list) or not manifest:
        result.fail(
            "r1r_stage_manifest_missing",
            "R1R requires a non-empty SC1 required-stage manifest.",
            path="sc1_required_stage_manifest",
        )
        return
    rows: dict[str, Mapping[str, Any]] = {}
    required_stage_names = set(contract.required_stages)
    for index, row in enumerate(manifest):
        if not isinstance(row, Mapping):
            result.fail("r1r_stage_manifest_row_not_object", "Every stage manifest row must be an object.", path=f"sc1_required_stage_manifest[{index}]")
            continue
        stage_name = str(row.get("stage_name") or "")
        if not stage_name:
            result.fail("r1r_stage_manifest_row_missing_name", "Every stage manifest row needs stage_name.", path=f"sc1_required_stage_manifest[{index}].stage_name")
            continue
        rows[stage_name] = row
        status = str(row.get("status") or "").strip()
        executed = _as_bool(row.get("executed"))
        skipped = _as_bool(row.get("skipped"))
        required = stage_name in required_stage_names
        evidence_label = str(row.get("evidence_artifact_label") or "").strip()
        if stage_name in required_stage_names and row.get("required") is False:
            result.fail(
                "r1r_required_stage_row_cannot_opt_out",
                "Required R1R stages are defined by the contract, not by manifest row required=false.",
                path=f"sc1_required_stage_manifest[{index}].required",
                expected=True,
                actual=row.get("required"),
            )
        if required and skipped and not str(row.get("skip_reason") or "").strip():
            result.fail(
                "r1r_stage_skipped_without_reason",
                "Skipped required stages must include skip_reason.",
                path=f"sc1_required_stage_manifest[{index}].skip_reason",
            )
        if required and (executed or status in {"executed", "verified"}) and not evidence_label:
            result.fail(
                "r1r_stage_executed_without_evidence_label",
                "Executed/verified required stages need evidence_artifact_label.",
                path=f"sc1_required_stage_manifest[{index}].evidence_artifact_label",
            )
        if target_met_full_chain and required:
            allowed = status in {"executed", "verified"}
            if status == "skipped_not_applicable":
                allowed = _r1r_stage_skip_allowed(stage_name, row)
            if not allowed:
                result.fail(
                    "r1r_required_stage_not_verified_for_target",
                    "target_met_full_chain requires each required stage to execute/verify or carry explicit allowed not-applicable proof.",
                    path=f"sc1_required_stage_manifest[{index}].status",
                    expected="executed/verified",
                    actual=status,
                )
    missing = [stage for stage in contract.required_stages if stage not in rows]
    result.details["r1r_missing_stage_manifest_rows"] = missing
    for stage in missing:
        result.fail(
            "r1r_required_stage_manifest_row_missing",
            f"R1R stage manifest is missing required SC1 stage {stage!r}.",
            path="sc1_required_stage_manifest",
            expected=stage,
        )
    provider_cache = rows.get("provider_cache_adapter_or_zero_eligible_proof")
    if provider_cache and str(provider_cache.get("status") or "") == "skipped_not_applicable":
        if not _r1r_stage_skip_allowed("provider_cache_adapter_or_zero_eligible_proof", provider_cache):
            result.fail(
                "r1r_provider_cache_adapter_skip_without_zero_scope_proof",
                "ProviderCache adapter may be skipped only with zero-eligible or not-in-input-scope proof.",
                path="sc1_required_stage_manifest.provider_cache_adapter_or_zero_eligible_proof",
            )


def _r1r_stage_skip_allowed(stage_name: str, row: Mapping[str, Any]) -> bool:
    if stage_name != "provider_cache_adapter_or_zero_eligible_proof":
        return False
    explicit = _as_bool(row.get("zero_eligible_proof")) or _as_bool(row.get("not_in_input_scope_proof"))
    input_count = _as_int(row.get("input_count"), default=-1)
    return explicit and input_count == 0


def _check_r1r_llm_truthfulness(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    status: str,
    target_met_full_chain: bool,
) -> None:
    proof = _get(summary, "sc1_full_chain_proof", {})
    plan = _get(summary, "llm_adjudication_plan", {})
    readiness = _get(summary, "llm_readiness", {})
    provider_execution = _get(summary, "llm_provider_execution", {})
    judgment_summary = _get(summary, "llm_judgment_summary", {})
    cache_policy = _get(summary, "llm_cache_policy", MISSING)
    proof_mapping = proof if isinstance(proof, Mapping) else {}
    plan_mapping = plan if isinstance(plan, Mapping) else {}
    readiness_mapping = readiness if isinstance(readiness, Mapping) else {}
    provider_mapping = provider_execution if isinstance(provider_execution, Mapping) else {}
    judgment_mapping = judgment_summary if isinstance(judgment_summary, Mapping) else {}
    cache_mapping = cache_policy if isinstance(cache_policy, Mapping) else {}

    eligible = _as_int(
        proof_mapping.get("llm_eligible_pair_count", plan_mapping.get("eligible_pair_count", plan_mapping.get("projected_calls", 0)))
    )
    selected = _as_int(proof_mapping.get("llm_selected_pair_count", plan_mapping.get("selected_pair_count", plan_mapping.get("selected_block_count", 0))))
    judgments = _as_int(proof_mapping.get("llm_judgment_count", _get(summary, "llm_judgment_count", 0)))
    judgment_summary_count = _as_int(judgment_mapping.get("judgment_count", judgments))
    ledger_row_count = _as_int(judgment_mapping.get("ledger_row_count", judgment_summary_count))
    error_count = _as_int(judgment_mapping.get("error_count", _get(summary, "llm_error_count", 0)))
    accounting = (
        judgment_mapping.get("selected_pair_accounting")
        if isinstance(judgment_mapping.get("selected_pair_accounting"), Mapping)
        else {}
    )
    resolved_provider = _as_int(accounting.get("resolved_provider_judgment_count", judgments))
    cached = _as_int(accounting.get("valid_cached_judgment_count", 0))
    explicit_skipped = _as_int(accounting.get("explicit_skipped_pair_count", 0))
    provider_error_pairs = _as_int(accounting.get("provider_error_pair_count", error_count))
    successful_accounted = _as_int(
        accounting.get("successful_accounted_pair_count", resolved_provider + cached + explicit_skipped)
    )
    cache_hit_count = _as_int(cache_mapping.get("compatible_cache_hit_count", judgment_mapping.get("compatible_cache_hit_count", cached)))
    cache_new_provider_success = _as_int(cache_mapping.get("new_provider_success_count", judgment_mapping.get("new_provider_success_count", resolved_provider)))
    cache_new_provider_calls = _as_int(cache_mapping.get("new_provider_call_count", judgment_mapping.get("new_provider_call_count", 0)))
    cache_failed_provider_calls = _as_int(cache_mapping.get("failed_provider_call_count", judgment_mapping.get("failed_provider_call_count", error_count)))
    cache_remaining_missing = _as_int(cache_mapping.get("remaining_missing_pair_count", judgment_mapping.get("remaining_missing_pair_count", 0)))
    exact_cache_hit_count = _as_int(cache_mapping.get("exact_compatible_cache_hit_count", judgment_mapping.get("exact_compatible_cache_hit_count", 0)))
    skipped_pairs = _as_int(plan_mapping.get("skipped_pair_count", explicit_skipped))
    unselected_pairs = _as_int(plan_mapping.get("unselected_pair_count", 0))
    eligible_accounting_total = _as_int(
        plan_mapping.get("eligible_pair_accounting_total", selected + skipped_pairs + unselected_pairs)
    )
    selection_policy = str(plan_mapping.get("selection_policy") or "").strip().casefold()
    all_eligible_pairs_selected = _as_bool(plan_mapping.get("all_eligible_pairs_selected"))
    all_eligible_pairs_adjudicated = _as_bool(
        proof_mapping.get("all_eligible_llm_pairs_adjudicated", plan_mapping.get("all_eligible_pairs_adjudicated"))
    )
    fixed_call_cap_primary_limiter = _as_bool(plan_mapping.get("fixed_call_cap_primary_limiter"))
    llm_requested = _as_bool(proof_mapping.get("llm_pair_adjudication_requested", plan_mapping.get("required")))
    llm_executed = _as_bool(proof_mapping.get("llm_pair_adjudication_executed", _get(summary, "llm_adjudication_used", False)))
    operator_approved = _as_bool(readiness_mapping.get("operator_approved"))
    provider_available = _as_bool(readiness_mapping.get("provider_available"))
    cache_ready = _as_bool(readiness_mapping.get("cache_ready"))
    budget_ready = _as_bool(readiness_mapping.get("budget_ready"))
    readiness_passed = _as_bool(readiness_mapping.get("passed"))
    fallback_used = (
        _as_bool(readiness_mapping.get("uses_fallback_provider"))
        or _as_bool(provider_mapping.get("uses_fallback_provider"))
        or _as_bool(provider_mapping.get("fallback_provider_used"))
    )
    provider_mode = str(provider_mapping.get("provider_mode") or readiness_mapping.get("provider_mode") or "")
    input_scope_passed = _as_bool(_get(summary, "input_scope_fidelity.passed", True))
    cache_only_exact_covered = bool(
        selected > 0
        and exact_cache_hit_count >= selected
        and cache_hit_count >= selected
        and cache_new_provider_calls == 0
        and cache_failed_provider_calls == 0
        and cache_remaining_missing == 0
        and provider_error_pairs == 0
    )

    if input_scope_passed and eligible > 0 and not operator_approved and status not in {
        "blocked_llm_approval_required",
        "blocked_environment_isolation",
        "ready_for_old_r1_scope_rerun",
    }:
        result.fail(
            "r1r_llm_approval_required_status_missing",
            "Eligible LLM pairs without operator approval must use blocked_llm_approval_required.",
            path="pipeline_contract.status",
            expected="blocked_llm_approval_required",
            actual=status,
        )
    if input_scope_passed and operator_approved and not provider_available and not cache_only_exact_covered and status not in {
        "blocked_provider",
        "blocked_llm_readiness",
        "blocked_environment_isolation",
        "ready_for_old_r1_scope_rerun",
    }:
        result.fail(
            "r1r_provider_unavailable_not_blocked",
            "Approved R1R LLM adjudication with unavailable provider must block as blocked_provider or blocked_llm_readiness.",
            path="llm_readiness.provider_available",
            expected=True,
            actual=readiness_mapping.get("provider_available"),
        )
    if input_scope_passed and operator_approved and not budget_ready and status not in {
        "blocked_budget",
        "blocked_llm_readiness",
        "blocked_environment_isolation",
        "ready_for_old_r1_scope_rerun",
    }:
        result.fail(
            "r1r_budget_unready_not_blocked",
            "Approved R1R LLM adjudication with missing/exceeded budget must block as blocked_budget or blocked_llm_readiness.",
            path="llm_readiness.budget_ready",
            expected=True,
            actual=readiness_mapping.get("budget_ready"),
        )
    if fallback_used:
        result.fail(
            "r1r_fallback_provider_used",
            "R1R bounded LLM adjudication must use the primary OpenAI-compatible provider only.",
            path="llm_provider_execution.uses_fallback_provider",
            expected=False,
            actual=True,
        )
    if target_met_full_chain:
        zero_eligible_pair_proof = eligible == 0 and selected == 0 and _as_bool(
            proof_mapping.get("zero_eligible_pair_proof", False)
        )
        required_true = {
            "complete_sc1_pipeline_executed": proof_mapping.get("complete_sc1_pipeline_executed"),
            "deterministic_pipeline_executed": proof_mapping.get("deterministic_pipeline_executed"),
            "llm_pair_adjudication_requested": llm_requested,
            "llm_pair_adjudication_executed": llm_executed or zero_eligible_pair_proof,
            "all_required_stage_statuses_verified": proof_mapping.get("all_required_stage_statuses_verified"),
            "review_pack_includes_stage_manifest": proof_mapping.get("review_pack_includes_stage_manifest"),
        }
        for key, value in required_true.items():
            if not _as_bool(value):
                result.fail(
                    "r1r_full_chain_proof_field_missing",
                    f"target_met_full_chain requires sc1_full_chain_proof.{key}=true.",
                    path=f"sc1_full_chain_proof.{key}",
                    expected=True,
                    actual=value,
                )
        target_readiness_ok = bool(
            (readiness_passed or cache_only_exact_covered)
            and cache_ready
            and budget_ready
            and (provider_available or cache_only_exact_covered)
        )
        if not zero_eligible_pair_proof and not target_readiness_ok:
            result.fail(
                "r1r_llm_readiness_missing_for_target",
                "target_met_full_chain requires cache/budget readiness and provider readiness unless every selected pair is exact-compatible cached.",
                path="llm_readiness",
                expected={
                    "passed": True,
                    "provider_available": True,
                    "cache_ready": True,
                    "budget_ready": True,
                    "or_exact_compatible_cache_covers_all_selected_pairs": True,
                },
                actual=readiness_mapping,
            )
        if not isinstance(cache_policy, Mapping):
            result.fail(
                "r1r_llm_cache_policy_missing",
                "target_met_full_chain requires durable LLM adjudication cache policy proof.",
                path="llm_cache_policy",
            )
        else:
            required_cache_fields = {
                "policy_version": cache_mapping.get("policy_version"),
                "durable_cache_root_label": cache_mapping.get("durable_cache_root_label"),
                "cost_spent_this_run_usd": cache_mapping.get("cost_spent_this_run_usd"),
                "cost_avoided_by_cache_reuse_usd": cache_mapping.get("cost_avoided_by_cache_reuse_usd"),
                "projected_full_eligible_cost_usd": cache_mapping.get("projected_full_eligible_cost_usd"),
                "budget_cap_usd": cache_mapping.get("budget_cap_usd"),
            }
            missing_cache_fields = [key for key, value in required_cache_fields.items() if value in (None, "")]
            if missing_cache_fields:
                result.fail(
                    "r1r_llm_cache_policy_field_missing",
                    "Durable cache policy proof must include cache version, root label, and cost fields.",
                    path="llm_cache_policy",
                    actual=missing_cache_fields,
                )
            if not _as_bool(cache_mapping.get("cache_writes_atomic")):
                result.fail(
                    "r1r_llm_cache_atomic_write_proof_missing",
                    "Successful LLM judgments must be written atomically to durable cache.",
                    path="llm_cache_policy.cache_writes_atomic",
                    expected=True,
                    actual=cache_mapping.get("cache_writes_atomic"),
                )
            if not _as_bool(cache_mapping.get("raw_private_paths_redacted")):
                result.fail(
                    "r1r_llm_cache_public_redaction_missing",
                    "Public cache proof must use labels/aggregates and redact private paths/payloads.",
                    path="llm_cache_policy.raw_private_paths_redacted",
                    expected=True,
                    actual=cache_mapping.get("raw_private_paths_redacted"),
                )
            root_label = str(cache_mapping.get("durable_cache_root_label") or "")
            if ":\\" in root_label or "/" in root_label or "\\" in root_label:
                result.fail(
                    "r1r_llm_cache_root_label_leaks_path",
                    "Public cache root must be a label, not a raw local path.",
                    path="llm_cache_policy.durable_cache_root_label",
                    actual=root_label,
                )
            if cache_hit_count != cached:
                result.fail(
                    "r1r_llm_cache_hit_count_mismatch",
                    "Compatible cache hit count must match valid_cached_judgment_count.",
                    path="llm_cache_policy.compatible_cache_hit_count",
                    expected=cached,
                    actual=cache_hit_count,
                )
            if cache_new_provider_success != resolved_provider:
                result.fail(
                    "r1r_llm_cache_new_success_count_mismatch",
                    "New provider success count must match resolved_provider_judgment_count.",
                    path="llm_cache_policy.new_provider_success_count",
                    expected=resolved_provider,
                    actual=cache_new_provider_success,
                )
            if cache_failed_provider_calls != provider_error_pairs:
                result.fail(
                    "r1r_llm_cache_failure_count_mismatch",
                    "Provider failures recorded in cache policy must match provider error pair accounting.",
                    path="llm_cache_policy.failed_provider_call_count",
                    expected=provider_error_pairs,
                    actual=cache_failed_provider_calls,
                )
            if cache_remaining_missing != 0:
                result.fail(
                    "r1r_llm_cache_remaining_pairs_for_target",
                    "target_met_full_chain requires no remaining missing selected/eligible LLM pairs.",
                    path="llm_cache_policy.remaining_missing_pair_count",
                    expected=0,
                    actual=cache_remaining_missing,
                )
            if cache_hit_count + cache_new_provider_success + explicit_skipped != selected:
                result.fail(
                    "r1r_llm_cache_accounting_does_not_cover_selected",
                    "Compatible cache hits plus new provider successes plus explicit skips must cover selected pairs.",
                    path="llm_cache_policy",
                    expected=selected,
                    actual=cache_hit_count + cache_new_provider_success + explicit_skipped,
                )
            if cache_new_provider_calls < cache_new_provider_success + cache_failed_provider_calls:
                result.fail(
                    "r1r_llm_cache_provider_call_count_inconsistent",
                    "New provider call count must cover provider successes and provider failures.",
                    path="llm_cache_policy.new_provider_call_count",
                    expected=f">= {cache_new_provider_success + cache_failed_provider_calls}",
                    actual=cache_new_provider_calls,
                )
        if not zero_eligible_pair_proof and provider_mode != "primary_openai":
            result.fail(
                "r1r_primary_provider_identity_missing",
                "target_met_full_chain requires primary OpenAI-compatible provider identity.",
                path="llm_provider_execution.provider_mode",
                expected="primary_openai",
                actual=provider_mode,
            )
        if not zero_eligible_pair_proof and not provider_mapping.get("model_name"):
            result.fail(
                "r1r_llm_model_identity_missing",
                "target_met_full_chain requires the actual configured model name used for R1R adjudication.",
                path="llm_provider_execution.model_name",
                expected="configured model name",
                actual=provider_mapping.get("model_name"),
            )
        if error_count > 0:
            result.fail(
                "r1r_llm_error_count_nonzero_for_target",
                "target_met_full_chain cannot include provider or judgment errors.",
                path="llm_judgment_summary.error_count",
                expected=0,
                actual=error_count,
            )
        if provider_error_pairs > 0:
            result.fail(
                "r1r_provider_error_pairs_not_successful_judgments",
                "Provider error rows cannot count as successful R1R LLM judgments.",
                path="llm_judgment_summary.selected_pair_accounting.provider_error_pair_count",
                expected=0,
                actual=provider_error_pairs,
            )
        if judgments != judgment_summary_count or judgment_summary_count != ledger_row_count:
            result.fail(
                "r1r_llm_judgment_count_mismatch",
                "R1R LLM judgment proof count must match llm_judgment_summary.judgment_count and ledger_row_count.",
                path="llm_judgment_summary",
                expected={"proof_judgment_count": judgments},
                actual={
                    "proof_judgment_count": judgments,
                    "summary_judgment_count": judgment_summary_count,
                    "ledger_row_count": ledger_row_count,
                },
            )
        if eligible_accounting_total != eligible:
            result.fail(
                "r1r_eligible_pair_accounting_mismatch",
                "Eligible LLM pairs must be accounted for as selected, skipped, or explicitly unselected.",
                path="llm_adjudication_plan.eligible_pair_accounting_total",
                expected=eligible,
                actual=eligible_accounting_total,
            )
        if not zero_eligible_pair_proof and selected != eligible:
            result.fail(
                "r1r_target_requires_all_eligible_llm_pairs_selected",
                "target_met_full_chain requires all eligible R1R LLM pairs to be selected unless zero-eligible proof is present.",
                path="llm_adjudication_plan.selected_pair_count",
                expected=eligible,
                actual=selected,
            )
        if not zero_eligible_pair_proof and judgments != eligible:
            result.fail(
                "r1r_target_requires_all_eligible_llm_pairs_judged",
                "target_met_full_chain requires all eligible R1R LLM pairs to be judged/accounted within budget.",
                path="sc1_full_chain_proof.llm_judgment_count",
                expected=eligible,
                actual=judgments,
            )
        if not zero_eligible_pair_proof and not all_eligible_pairs_selected:
            result.fail(
                "r1r_all_eligible_llm_pairs_selected_missing",
                "target_met_full_chain requires an explicit all_eligible_pairs_selected proof.",
                path="llm_adjudication_plan.all_eligible_pairs_selected",
                expected=True,
                actual=plan_mapping.get("all_eligible_pairs_selected"),
            )
        if not zero_eligible_pair_proof and not all_eligible_pairs_adjudicated:
            result.fail(
                "r1r_all_eligible_llm_pairs_adjudicated_missing",
                "target_met_full_chain requires an explicit all_eligible_llm_pairs_adjudicated proof.",
                path="sc1_full_chain_proof.all_eligible_llm_pairs_adjudicated",
                expected=True,
                actual=proof_mapping.get("all_eligible_llm_pairs_adjudicated"),
            )
        if not zero_eligible_pair_proof and selection_policy not in {
            "budget_driven_all_eligible",
            "all_eligible",
            "zero_eligible",
        }:
            result.fail(
                "r1r_target_requires_budget_driven_llm_selection_policy",
                "target_met_full_chain requires budget-driven all-eligible LLM selection or explicit zero-eligible proof.",
                path="llm_adjudication_plan.selection_policy",
                expected="budget_driven_all_eligible",
                actual=selection_policy,
            )
        if not zero_eligible_pair_proof and fixed_call_cap_primary_limiter:
            result.fail(
                "r1r_fixed_call_cap_cannot_be_primary_target_limiter",
                "A fixed call cap cannot be the primary R1R route-evidence LLM limiter for target_met_full_chain.",
                path="llm_adjudication_plan.fixed_call_cap_primary_limiter",
                expected=False,
                actual=True,
            )
        if eligible > 0 and selected <= 0:
            result.fail(
                "r1r_llm_selection_missing_for_eligible_pairs",
                "target_met_full_chain requires selected LLM pairs when eligible pairs exist.",
                path="sc1_full_chain_proof.llm_selected_pair_count",
                expected="> 0",
                actual=selected,
            )
        if eligible > 0 and judgments <= 0:
            result.fail(
                "r1r_llm_judgment_count_zero_for_eligible_pairs",
                "target_met_full_chain requires LLM judgments when eligible pairs exist.",
                path="sc1_full_chain_proof.llm_judgment_count",
                expected="> 0",
                actual=judgments,
            )
        if selected > 0 and judgments <= 0:
            result.fail(
                "r1r_selected_pairs_without_judgments",
                "Selected R1R LLM pairs must be judged, cached, or explicitly skipped before full-chain target_met.",
                path="sc1_full_chain_proof.llm_judgment_count",
            )
        if selected > 0 and successful_accounted != selected:
            result.fail(
                "r1r_selected_llm_pairs_not_fully_accounted",
                "Every selected LLM pair must resolve through a provider judgment, valid cache hit, or explicit skip.",
                path="llm_judgment_summary.selected_pair_accounting.successful_accounted_pair_count",
                expected=selected,
                actual=successful_accounted,
            )
        outcome_keys = ("llm_same_count", "llm_cannot_count", "llm_uncertain_count")
        missing_outcomes = [key for key in outcome_keys if key not in proof_mapping]
        if missing_outcomes:
            result.fail(
                "r1r_llm_outcomes_not_recorded",
                "target_met_full_chain requires same/cannot/uncertain LLM outcome counters.",
                path="sc1_full_chain_proof",
                expected=list(outcome_keys),
                actual=missing_outcomes,
            )
        if not llm_executed and eligible > 0:
            result.fail(
                "r1r_llm_used_false_with_target_met_full_chain",
                "R1R cannot claim target_met_full_chain with llm_used=false while eligible pairs exist.",
                path="sc1_full_chain_proof.llm_pair_adjudication_executed",
                expected=True,
                actual=llm_executed,
            )
        if _as_bool(proof_mapping.get("deterministic_only_output_used_as_full_chain_route_approval_evidence")):
            result.fail(
                "r1r_deterministic_only_output_used_as_full_chain_evidence",
                "Deterministic-only output cannot be used as full-chain route approval evidence.",
                path="sc1_full_chain_proof.deterministic_only_output_used_as_full_chain_route_approval_evidence",
            )


def _check_r1r_write_scope(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    target_met_full_chain: bool,
) -> None:
    mutation = _get(summary, "mutation_proof", {})
    mutation_changed_tables: list[str] = []
    if not isinstance(mutation, Mapping):
        result.fail("r1r_mutation_proof_not_object", "R1R requires mutation_proof object.", path="mutation_proof")
    else:
        delta = mutation.get("delta") if isinstance(mutation.get("delta"), Mapping) else mutation
        mutation_changed_tables = _table_names(delta.get("changed_tables", []))
        outside_contract_allowlist = sorted(
            table for table in mutation_changed_tables if table not in R1R_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES
        )
        if outside_contract_allowlist:
            result.fail(
                "r1r_mutation_changed_table_outside_fixed_allowlist",
                "R1R contract compares mutation_proof.changed_tables against its fixed SourceConcept-owned allowlist.",
                path="mutation_proof.changed_tables",
                actual=outside_contract_allowlist,
            )
        forbidden_names, unexpected_names = _mutation_table_violations(mutation)
        if forbidden_names:
            result.fail(
                "r1r_forbidden_table_changed",
                "R1R detected forbidden table changes.",
                path="mutation_proof.forbidden_changed_tables",
                actual=forbidden_names,
            )
        if unexpected_names:
            result.fail(
                "r1r_unexpected_table_changed",
                "R1R detected unexpected table changes outside SourceConcept-owned scope.",
                path="mutation_proof.unexpected_changed_tables",
                actual=unexpected_names,
            )
        if target_met_full_chain and not _as_bool(mutation.get("passed")):
            result.fail(
                "r1r_mutation_proof_failed_for_target",
                "target_met_full_chain requires mutation_proof.passed=true.",
                path="mutation_proof.passed",
                expected=True,
                actual=mutation.get("passed"),
            )

    forbidden = _get(summary, "forbidden_writes", {})
    if isinstance(forbidden, Mapping):
        for key, value in forbidden.items():
            if _as_bool(value):
                result.fail(
                    "r1r_forbidden_write_claimed",
                    f"R1R forbids {key} writes.",
                    path=f"forbidden_writes.{key}",
                    expected=False,
                    actual=value,
                )

    scope = _get(summary, "source_concept_write_scope", MISSING)
    source_scope_required = target_met_full_chain or bool(mutation_changed_tables) or _as_bool(
        _get(summary, "post_commit_verification.execute_requested", False)
    )
    if source_scope_required and not isinstance(scope, Mapping):
        result.fail(
            "r1r_source_concept_write_scope_missing",
            "R1R requires source_concept_write_scope proof when SourceConcept persistence is claimed or changes are observed.",
            path="source_concept_write_scope",
        )
    if isinstance(scope, Mapping):
        changed_tables = _table_names(scope.get("changed_tables", []))
        outside_allowed = sorted(table for table in changed_tables if table not in R1R_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
        if outside_allowed:
            result.fail(
                "r1r_source_concept_write_outside_allowlist",
                "R1R writes must stay inside the contract-owned SourceConcept table allowlist.",
                path="source_concept_write_scope.changed_tables",
                actual=outside_allowed,
            )

    contamination = _get(summary, "old_r1_contamination_handling", {})
    if target_met_full_chain:
        if not isinstance(contamination, Mapping):
            result.fail(
                "r1r_old_r1_contamination_handling_missing",
                "target_met_full_chain requires proof that old deterministic R1 SourceConcept output was isolated.",
                path="old_r1_contamination_handling",
            )
        else:
            if not _as_bool(contamination.get("baseline_snapshot_recorded")):
                result.fail(
                    "r1r_old_r1_baseline_snapshot_missing",
                    "target_met_full_chain requires an old R1 SourceConcept baseline snapshot before isolation.",
                    path="old_r1_contamination_handling.baseline_snapshot_recorded",
                    expected=True,
                    actual=contamination.get("baseline_snapshot_recorded"),
                )
            if not _as_bool(contamination.get("old_r1_isolated_before_r1r_persistence")):
                result.fail(
                    "r1r_old_r1_output_not_isolated",
                    "target_met_full_chain cannot reuse old deterministic R1 SourceConcept rows as fresh R1R evidence.",
                    path="old_r1_contamination_handling.old_r1_isolated_before_r1r_persistence",
                    expected=True,
                    actual=contamination.get("old_r1_isolated_before_r1r_persistence"),
                )
            if not _as_bool(contamination.get("source_concept_owned_tables_cleared_or_rebuilt_in_dev_test")):
                result.fail(
                    "r1r_old_r1_sourceconcept_rebuild_missing",
                    "target_met_full_chain requires SourceConcept-owned output isolation in dev/test/restored-snapshot DB.",
                    path="old_r1_contamination_handling.source_concept_owned_tables_cleared_or_rebuilt_in_dev_test",
                    expected=True,
                    actual=contamination.get("source_concept_owned_tables_cleared_or_rebuilt_in_dev_test"),
                )

    post_commit = _get(summary, "post_commit_verification", {})
    if target_met_full_chain and not _as_bool(_get(summary, "post_commit_verification.passed", False)):
        result.fail(
            "r1r_post_commit_verification_missing_for_target",
            "target_met_full_chain requires post_commit_verification.passed=true.",
            path="post_commit_verification.passed",
            actual=post_commit.get("passed") if isinstance(post_commit, Mapping) else post_commit,
        )


def _check_r1r_review_redaction(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    target_met_full_chain: bool,
) -> None:
    review_pack = _get(summary, "review_pack", {})
    if isinstance(review_pack, Mapping):
        if _as_bool(review_pack.get("generated")) and not _as_bool(review_pack.get("includes_stage_manifest")):
            result.fail(
                "r1r_review_pack_omits_stage_manifest",
                "R1R review pack must include the SC1 required-stage manifest.",
                path="review_pack.includes_stage_manifest",
                expected=True,
                actual=review_pack.get("includes_stage_manifest"),
            )
        if target_met_full_chain and not _as_bool(review_pack.get("generated")):
            result.fail(
                "r1r_review_pack_missing_for_target",
                "target_met_full_chain requires a generated review pack.",
                path="review_pack.generated",
                expected=True,
                actual=review_pack.get("generated"),
            )
    elif target_met_full_chain:
        result.fail("r1r_review_pack_not_object", "target_met_full_chain requires review_pack object.", path="review_pack")

    redaction = _get(summary, "public_redaction", {})
    if isinstance(redaction, Mapping):
        if not _as_bool(redaction.get("passed")):
            result.fail(
                "r1r_public_redaction_missing_or_failed",
                "R1R public report/summary must pass public redaction.",
                path="public_redaction.passed",
                expected=True,
                actual=redaction.get("passed"),
            )
        scanned = redaction.get("scanned_artifacts") if isinstance(redaction.get("scanned_artifacts"), Mapping) else {}
        if target_met_full_chain or _as_bool(redaction.get("passed")):
            for key in ("final_json_summary", "final_markdown_report"):
                if not _as_bool(scanned.get(key)):
                    result.fail(
                        "r1r_public_redaction_final_artifact_scan_missing",
                        "R1R public redaction must scan the exact final committed JSON summary and Markdown report.",
                        path=f"public_redaction.scanned_artifacts.{key}",
                        expected=True,
                        actual=scanned.get(key),
                    )
            if not _as_bool(redaction.get("clean_before_public_write")):
                result.fail(
                    "r1r_public_redaction_not_clean_before_write",
                    "R1R must prove redaction passed before writing final target claims.",
                    path="public_redaction.clean_before_public_write",
                    expected=True,
                    actual=redaction.get("clean_before_public_write"),
                )
            if _as_bool(redaction.get("unsafe_public_report_written")):
                result.fail(
                    "r1r_unsafe_public_report_written",
                    "R1R must not publish target claims after a redaction failure.",
                    path="public_redaction.unsafe_public_report_written",
                    expected=False,
                    actual=redaction.get("unsafe_public_report_written"),
                )
    else:
        result.fail("r1r_public_redaction_not_object", "R1R requires public_redaction object.", path="public_redaction")


def _check_r1r_route_gate(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    target_met_full_chain: bool,
) -> None:
    route = _get(summary, "route_authorization", {})
    if not isinstance(route, Mapping):
        result.fail("r1r_route_authorization_not_object", "R1R requires route_authorization object.", path="route_authorization")
        return
    forbidden_true_paths = (
        "r2_authorized",
        "px1_b_authorized",
        "provider_2_authorized",
        "scale_up_authorized",
        "entity_bridge_authorized",
        "source_concept_truth_promotion_authorized",
        "route_approval_authorized",
    )
    for key in forbidden_true_paths:
        if _as_bool(route.get(key)):
            result.fail(
                "r1r_forbidden_route_authorization",
                f"R1R cannot authorize {key}; A1R remains required.",
                path=f"route_authorization.{key}",
                expected=False,
                actual=route.get(key),
            )
    forbidden_claim_paths = (
        "pipeline_contract.claims.route_approved",
        "pipeline_contract.claims.safe_to_merge",
        "pipeline_contract.claims.r2_authorized",
        "pipeline_contract.claims.px1_b_authorized",
        "pipeline_contract.claims.provider_2_authorized",
        "pipeline_contract.claims.scale_up_authorized",
        "pipeline_contract.claims.entity_bridge_authorized",
        "pipeline_contract.route_approved",
        "pipeline_contract.safe_to_merge",
        "pipeline_contract.r2_authorized",
        "pipeline_contract.px1_b_authorized",
        "pipeline_contract.provider_2_authorized",
        "pipeline_contract.scale_up_authorized",
        "pipeline_contract.entity_bridge_authorized",
        "claims.route_approved",
        "claims.safe_to_merge",
        "claims.r2_authorized",
        "claims.px1_b_authorized",
        "claims.provider_2_authorized",
        "claims.scale_up_authorized",
        "claims.entity_bridge_authorized",
        "route_approved",
        "safe_to_merge",
        "r2_authorized",
        "px1_b_authorized",
        "provider_2_authorized",
        "scale_up_authorized",
        "entity_bridge_authorized",
    )
    for path in forbidden_claim_paths:
        value = _get(summary, path, False)
        if _as_bool(value):
            result.fail(
                "r1r_forbidden_route_claim",
                "R1R may prove full-chain SourceConcept replay, but it must not claim route approval or downstream authorization.",
                path=path,
                expected=False,
                actual=value,
            )
    if not _as_bool(route.get("a1r_still_required", False)):
        result.fail(
            "r1r_a1r_still_required_missing",
            "R1R must explicitly state A1R remains required before route approval.",
            path="route_authorization.a1r_still_required",
            expected=True,
            actual=route.get("a1r_still_required"),
        )


def _check_r2_source_concept_graph_remediation(
    _contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    status = str(_get(summary, "pipeline_contract.status", "") or "")
    target = status == "target_met_constraint_aware_r2"
    if status not in R2_SOURCE_CONCEPT_GRAPH_REMEDIATION_STATUSES:
        result.fail(
            "r2_status_invalid",
            "R2 status must use the focused contract vocabulary.",
            path="pipeline_contract.status",
            expected=list(R2_SOURCE_CONCEPT_GRAPH_REMEDIATION_STATUSES),
            actual=status,
        )
        return
    if not target and _completion_or_approval_claimed(result):
        result.fail(
            "r2_non_target_status_claims_completion",
            "Only target_met_constraint_aware_r2 may claim completion or downstream approval.",
            path="pipeline_contract.status",
            actual=status,
        )
    if target:
        claims = _get(summary, "pipeline_contract.claims", MISSING)
        if not isinstance(claims, Mapping):
            result.fail(
                "r2_pipeline_claims_not_object",
                "R2 target requires explicit pipeline_contract.claims proof.",
                path="pipeline_contract.claims",
            )
        else:
            for key in ("route_approved", "safe_to_merge"):
                actual = claims.get(key, MISSING)
                if type(actual) is not bool or actual is not False:
                    result.fail(
                        "r2_pipeline_non_authorization_claim_missing_or_invalid",
                        "R2 target requires explicit false route_approved and safe_to_merge claims.",
                        path=f"pipeline_contract.claims.{key}",
                        expected=False,
                        actual="<missing>" if actual is MISSING else actual,
                    )

    isolation = _get(summary, "environment_isolation", MISSING)
    if not isinstance(isolation, Mapping):
        result.fail("r2_environment_isolation_not_object", "R2 requires structured isolation proof.", path="environment_isolation")
        isolation = {}
    if target:
        for key, expected in R2_REQUIRED_ISOLATION_FLAGS.items():
            actual = isolation.get(key, MISSING)
            if type(actual) is not bool or actual is not expected:
                result.fail(
                    "r2_environment_isolation_proof_missing_or_invalid",
                    "R2 target requires every isolation/safety flag to exist as an exact boolean with the required value.",
                    path=f"environment_isolation.{key}",
                    expected=expected,
                    actual="<missing>" if actual is MISSING else actual,
                )
    manifest = _get(summary, "fixed_input_manifest", {})
    if not isinstance(manifest, Mapping):
        result.fail("r2_fixed_input_manifest_not_object", "R2 requires fixed-input manifest proof.", path="fixed_input_manifest")
        manifest = {}
    if target:
        for key in (
            "present",
            "private_manifest_generated",
            "baseline_to_working_clone_match",
            "before_after_match",
            "row_counts_match",
            "content_fingerprints_match",
            "provenance_unchanged",
        ):
            if not _as_bool(manifest.get(key)):
                result.fail(
                    "r2_fixed_input_gate_failed",
                    "R2 target requires unchanged fixed upstream evidence with row-content proof.",
                    path=f"fixed_input_manifest.{key}",
                    expected=True,
                    actual=manifest.get(key),
                )
        table_count = _as_int(manifest.get("table_count"))
        fingerprint_count = _as_int(manifest.get("content_fingerprint_count"))
        if table_count < 14 or fingerprint_count != table_count:
            result.fail(
                "r2_fixed_input_table_coverage_incomplete",
                "R2 fixed-input proof must cover every required upstream table with a content fingerprint.",
                path="fixed_input_manifest",
                expected="at least 14 tables and one fingerprint per table",
                actual={"table_count": table_count, "content_fingerprint_count": fingerprint_count},
            )
        if manifest.get("changed_tables"):
            result.fail("r2_upstream_evidence_changed", "Fixed upstream evidence changed.", path="fixed_input_manifest.changed_tables", actual=manifest.get("changed_tables"))

    operations = _get(summary, "operation_counts", {})
    if not isinstance(operations, Mapping):
        result.fail("r2_operation_counts_not_object", "R2 requires forbidden-operation accounting.", path="operation_counts")
        operations = {}
    if target:
        for key in (
            "gallery_dl_calls",
            "provider_pixiv_network_calls",
            "ai_tagging_calls",
            "media_imports",
            "upstream_observation_mutations",
            "new_llm_provider_calls",
            "production_writes",
            "truth_path_writes",
        ):
            if _as_int(operations.get(key), default=-1) != 0:
                result.fail(
                    "r2_forbidden_operation_nonzero",
                    "R2 target requires zero acquisition/provider/import/truth/production operations.",
                    path=f"operation_counts.{key}",
                    expected=0,
                    actual=operations.get(key),
                )

    write_scope = _get(summary, "source_concept_write_scope", {})
    if not isinstance(write_scope, Mapping):
        result.fail("r2_write_scope_not_object", "R2 requires SourceConcept write-scope proof.", path="source_concept_write_scope")
        write_scope = {}
    if target:
        allowed_rows = write_scope.get("allowed_tables", MISSING)
        rebuilt_rows = write_scope.get("rebuilt_tables", MISSING)
        changed_rows = write_scope.get("changed_tables", MISSING)
        allowed = {str(value) for value in allowed_rows} if isinstance(allowed_rows, list) else set()
        rebuilt = {str(value) for value in rebuilt_rows} if isinstance(rebuilt_rows, list) else set()
        changed = set(_table_names(changed_rows)) if isinstance(changed_rows, list) else set()
        for key, actual in (
            ("allowed_tables", allowed_rows),
            ("rebuilt_tables", rebuilt_rows),
            ("changed_tables", changed_rows),
        ):
            if not isinstance(actual, list):
                result.fail(
                    "r2_write_scope_table_list_missing_or_invalid",
                    "R2 write-scope table fields must exist as lists.",
                    path=f"source_concept_write_scope.{key}",
                    expected="list",
                    actual="<missing>" if actual is MISSING else actual,
                )
        outside = sorted((allowed | rebuilt | changed) - R2_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
        if outside:
            result.fail("r2_write_outside_sourceconcept_allowlist", "R2 wrote outside the SourceConcept allowlist.", path="source_concept_write_scope", actual=outside)
        if rebuilt != R2_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES:
            result.fail(
                "r2_sourceconcept_rebuild_incomplete",
                "R2 target requires the complete SourceConcept-owned output set to be rebuilt.",
                path="source_concept_write_scope.rebuilt_tables",
                expected=sorted(R2_SOURCE_CONCEPT_ALLOWED_WRITE_TABLES),
                actual=sorted(rebuilt),
            )
        for key in ("forbidden_changed_tables", "unexpected_changed_tables"):
            actual = write_scope.get(key, MISSING)
            if not isinstance(actual, list) or actual:
                result.fail(
                    "r2_forbidden_or_unexpected_write",
                    "R2 mutation proof requires explicit empty forbidden/unexpected changed-table lists.",
                    path=f"source_concept_write_scope.{key}",
                    expected=[],
                    actual="<missing>" if actual is MISSING else actual,
                )
        truth_write_count = write_scope.get("persistence_forbidden_truth_table_write_count", MISSING)
        if type(truth_write_count) is not int or truth_write_count != 0:
            result.fail(
                "r2_persistence_forbidden_truth_write_count_invalid",
                "R2 target requires an explicit integer zero forbidden truth-table persistence delta.",
                path="source_concept_write_scope.persistence_forbidden_truth_table_write_count",
                expected=0,
                actual="<missing>" if truth_write_count is MISSING else truth_write_count,
            )
        forbidden_proof = _get(summary, "forbidden_truth_table_content_proof", MISSING)
        if not isinstance(forbidden_proof, Mapping):
            result.fail(
                "r2_forbidden_truth_content_proof_missing",
                "R2 target requires a measured baseline-vs-final content comparison for every forbidden truth table.",
                path="forbidden_truth_table_content_proof",
            )
            forbidden_proof = {}
        measured_tables = forbidden_proof.get("tables_accounted_for", MISSING)
        measured_table_set = (
            {str(value) for value in measured_tables}
            if isinstance(measured_tables, list)
            else set()
        )
        if (
            measured_table_set != R2_FORBIDDEN_TRUTH_TABLES
            or _as_int(forbidden_proof.get("forbidden_truth_table_count"), default=-1)
            != len(R2_FORBIDDEN_TRUTH_TABLES)
        ):
            result.fail(
                "r2_forbidden_truth_table_coverage_incomplete",
                "R2 must account for the complete authoritative forbidden truth-table set.",
                path="forbidden_truth_table_content_proof",
                expected=sorted(R2_FORBIDDEN_TRUTH_TABLES),
                actual=sorted(measured_table_set),
            )
        for key in (
            "forbidden_truth_tables_measured",
            "source_all_tables_present",
            "working_all_tables_present",
            "row_counts_match",
            "schemas_match",
            "content_fingerprints_match",
            "comparison_passed",
            "raw_fingerprints_private",
        ):
            if type(forbidden_proof.get(key, MISSING)) is not bool or forbidden_proof.get(key) is not True:
                result.fail(
                    "r2_forbidden_truth_content_proof_failed",
                    "R2 target requires an explicit passing read-only content comparison for forbidden truth tables.",
                    path=f"forbidden_truth_table_content_proof.{key}",
                    expected=True,
                    actual=forbidden_proof.get(key, "<missing>"),
                )
        measured_changed = forbidden_proof.get("changed_tables", MISSING)
        if not isinstance(measured_changed, list) or measured_changed:
            result.fail(
                "r2_forbidden_truth_content_changed",
                "R2 target requires zero measured forbidden truth-table content changes.",
                path="forbidden_truth_table_content_proof.changed_tables",
                expected=[],
                actual=measured_changed,
            )
        if write_scope.get("forbidden_changed_tables") != measured_changed or str(
            write_scope.get("forbidden_changed_tables_source") or ""
        ) != "forbidden_truth_table_content_proof.changed_tables":
            result.fail(
                "r2_forbidden_changed_tables_not_derived_from_measurement",
                "The public forbidden_changed_tables claim must be derived from the measured comparison.",
                path="source_concept_write_scope",
            )
        truncate_drop_reset = write_scope.get("truncate_drop_reset_used", MISSING)
        if type(truncate_drop_reset) is not bool or truncate_drop_reset is not False:
            result.fail(
                "r2_truncate_drop_reset_proof_missing_or_invalid",
                "R2 target requires explicit truncate_drop_reset_used=false.",
                path="source_concept_write_scope.truncate_drop_reset_used",
                expected=False,
                actual="<missing>" if truncate_drop_reset is MISSING else truncate_drop_reset,
            )

    graph = _get(summary, "graph_invariants", {})
    if not isinstance(graph, Mapping):
        result.fail("r2_graph_invariants_not_object", "R2 requires graph invariant diagnostics.", path="graph_invariants")
        graph = {}
    if target:
        for key in (
            "review_only_edge_used_in_union_count",
            "direct_llm_cannot_pair_in_materialized_component_count",
            "deterministic_hard_conflict_in_materialized_component_count",
            "transitive_cannot_violation_count",
            "unauthorized_unknown_role_materialization_count",
        ):
            if _as_int(graph.get(key), default=-1) != 0:
                result.fail(
                    "r2_graph_invariant_failed",
                    "R2 target requires review-only exclusion and zero component-level cannot violations.",
                    path=f"graph_invariants.{key}",
                    expected=0,
                    actual=graph.get(key),
                )

    llm = _get(summary, "llm_judgment_accounting", {})
    if not isinstance(llm, Mapping):
        result.fail("r2_llm_accounting_not_object", "R2 requires existing-judgment accounting.", path="llm_judgment_accounting")
        llm = {}
    if target:
        existing_total = _as_int(llm.get("existing_r1r_judgment_count"))
        accounted = sum(
            _as_int(llm.get(key))
            for key in (
                "exact_compatible_reuse_count",
                "stable_pair_identity_reuse_count",
                "semantic_prior_count",
                "invalidated_count",
            )
        )
        if existing_total != 6429 or accounted != existing_total:
            result.fail(
                "r2_llm_existing_judgment_accounting_mismatch",
                "R2 must account for all 6429 existing R1R judgments exactly once.",
                path="llm_judgment_accounting",
                expected=6429,
                actual={"existing_total": existing_total, "accounted": accounted},
            )
        if _as_int(llm.get("new_provider_call_count"), default=-1) != 0:
            result.fail("r2_new_llm_provider_calls_nonzero", "Initial R2 must make zero new LLM provider calls.", path="llm_judgment_accounting.new_provider_call_count", expected=0, actual=llm.get("new_provider_call_count"))
        new_pair_count = _as_int(llm.get("genuinely_new_or_missing_pair_count"), default=-1)
        adjudication = _get(summary, "new_pair_adjudication", {})
        if not isinstance(adjudication, Mapping):
            result.fail(
                "r2_new_pair_adjudication_not_object",
                "R2 requires a structured approval boundary for genuinely new pairs.",
                path="new_pair_adjudication",
            )
        else:
            expected_status = "blocked_llm_approval_required" if new_pair_count > 0 else "no_new_pairs"
            if str(adjudication.get("status") or "") != expected_status:
                result.fail(
                    "r2_new_pair_adjudication_status_invalid",
                    "New or incompatible R2 pairs must remain blocked from provider adjudication pending separate approval.",
                    path="new_pair_adjudication.status",
                    expected=expected_status,
                    actual=adjudication.get("status"),
                )
            if _as_int(adjudication.get("pair_count"), default=-1) != new_pair_count:
                result.fail(
                    "r2_new_pair_adjudication_count_mismatch",
                    "The new-pair approval boundary must match LLM accounting.",
                    path="new_pair_adjudication.pair_count",
                    expected=new_pair_count,
                    actual=adjudication.get("pair_count"),
                )
            if _as_int(adjudication.get("provider_calls_made"), default=-1) != 0 or _as_bool(adjudication.get("provider_initialized")):
                result.fail(
                    "r2_new_pair_provider_boundary_violated",
                    "Initial R2 must not initialize or call a provider for new pairs.",
                    path="new_pair_adjudication",
                )
            if new_pair_count > 0 and not all(
                _as_bool(adjudication.get(key))
                for key in (
                    "execution_scope_excludes_unadjudicated_review_pairs",
                    "separate_operator_approval_required",
                )
            ):
                result.fail(
                    "r2_new_pair_approval_boundary_incomplete",
                    "Unadjudicated new pairs must be excluded from materialization and require separate operator approval.",
                    path="new_pair_adjudication",
                )

    quality = _get(summary, "quality_evaluation", {})
    if not isinstance(quality, Mapping):
        result.fail("r2_quality_evaluation_not_object", "R2 requires quality evaluation.", path="quality_evaluation")
        quality = {}
    if target:
        for key, expected in R2_REQUIRED_QUALITY_FLAGS.items():
            actual = quality.get(key, MISSING)
            if type(actual) is not bool or actual is not expected:
                result.fail(
                    "r2_quality_dimension_missing_or_invalid",
                    "R2 target requires explicit constraint, search, gap, recall, and route-quality dimensions.",
                    path=f"quality_evaluation.{key}",
                    expected=expected,
                    actual="<missing>" if actual is MISSING else actual,
                )
        interpretation = quality.get("quality_interpretation", MISSING)
        if not isinstance(interpretation, str) or not interpretation.strip() or not all(
            phrase in interpretation
            for phrase in (
                "constraint-aware graph-remediation target",
                "Search, gap, and recall closure remain incomplete",
            )
        ):
            result.fail(
                "r2_quality_interpretation_missing_or_invalid",
                "R2 must explicitly distinguish its narrow constraint target from incomplete search/gap/recall quality.",
                path="quality_evaluation.quality_interpretation",
                actual="<missing>" if interpretation is MISSING else interpretation,
            )
        integer_fields = (
            "all_existing_r1r_same_decision_count",
            "compatible_must_link_benchmark_count",
            "semantic_prior_same_decision_count",
            "invalidated_same_decision_count",
            "retained_same_component_count",
            "intentionally_split_with_valid_constraint_count",
            "unexplained_same_regression_count",
            "missing_signal_or_pair_count",
            "same_benchmark_accounting_total_count",
            "intentionally_split_reason_ledger_count",
            "split_same_reason_ledger_count",
        )
        invalid_integer_fields = [
            key for key in integer_fields if type(quality.get(key, MISSING)) is not int or quality.get(key, -1) < 0
        ]
        if invalid_integer_fields:
            result.fail(
                "r2_same_benchmark_field_missing_or_invalid",
                "R2 same-benchmark counts must be explicit non-negative integers.",
                path="quality_evaluation",
                actual=invalid_integer_fields,
            )
        benchmark = _as_int(quality.get("compatible_must_link_benchmark_count"), default=-1)
        retained = _as_int(quality.get("retained_same_component_count"), default=-1)
        intentional = _as_int(quality.get("intentionally_split_with_valid_constraint_count"), default=-1)
        unexplained = _as_int(quality.get("unexplained_same_regression_count"), default=-1)
        accounted = _as_int(quality.get("same_benchmark_accounting_total_count"), default=-1)
        split_ledger = _as_int(quality.get("split_same_reason_ledger_count"), default=-1)
        intentional_ledger = _as_int(quality.get("intentionally_split_reason_ledger_count"), default=-1)
        if benchmark != retained + intentional + unexplained or accounted != benchmark:
            result.fail(
                "r2_same_benchmark_accounting_mismatch",
                "Compatible must-link judgments must balance exactly across retained, intentionally constrained, and unexplained outcomes.",
                path="quality_evaluation",
                expected="benchmark = retained + intentional + unexplained",
                actual={
                    "benchmark": benchmark,
                    "retained": retained,
                    "intentional": intentional,
                    "unexplained": unexplained,
                    "accounted": accounted,
                },
            )
        if unexplained != 0:
            result.fail(
                "r2_unexplained_same_regression",
                "R2 target cannot contain an unexplained compatible must-link split.",
                path="quality_evaluation.unexplained_same_regression_count",
                expected=0,
                actual=unexplained,
            )
        if intentional_ledger != intentional or split_ledger != intentional + unexplained:
            result.fail(
                "r2_same_split_reason_ledger_incomplete",
                "Every split compatible must-link judgment requires a private reason-ledger entry, including its blocker class.",
                path="quality_evaluation",
                actual={
                    "intentional": intentional,
                    "unexplained": unexplained,
                    "intentional_ledger": intentional_ledger,
                    "split_ledger": split_ledger,
                },
            )
        if str(quality.get("same_benchmark_source") or "") != "compatible_reused_r1r_judgments" or quality.get(
            "same_benchmark_constructed_from_current_output", MISSING
        ) is not False:
            result.fail(
                "r2_same_benchmark_source_invalid",
                "R2 must construct its proof-grade same benchmark from compatible reused R1R judgments, never current active output.",
                path="quality_evaluation",
            )
        if str(quality.get("same_benchmark_compatibility_policy") or "") != (
            "exact_or_stable_pair_identity_only;semantic_prior_excluded"
        ):
            result.fail(
                "r2_same_benchmark_compatibility_policy_invalid",
                "Semantic-prior-only same judgments must remain outside the proof-grade benchmark.",
                path="quality_evaluation.same_benchmark_compatibility_policy",
            )
        all_same = _as_int(quality.get("all_existing_r1r_same_decision_count"), default=-1)
        semantic_same = _as_int(quality.get("semantic_prior_same_decision_count"), default=-1)
        invalidated_same = _as_int(quality.get("invalidated_same_decision_count"), default=-1)
        if all_same != benchmark + semantic_same + invalidated_same:
            result.fail(
                "r2_all_same_decision_accounting_mismatch",
                "All existing R1R same decisions must be classified as compatible proof-grade, semantic prior, or invalidated.",
                path="quality_evaluation",
                actual={
                    "all": all_same,
                    "compatible": benchmark,
                    "semantic_prior": semantic_same,
                    "invalidated": invalidated_same,
                },
            )
        llm_same_counts = llm.get("same_decision_counts") if isinstance(llm.get("same_decision_counts"), Mapping) else {}
        expected_same_counts = {
            "all_existing_r1r": all_same,
            "compatible_proof_grade": benchmark,
            "semantic_prior": semantic_same,
            "invalidated": invalidated_same,
        }
        if any(
            type(llm_same_counts.get(key, MISSING)) is not int or llm_same_counts.get(key) != expected
            for key, expected in expected_same_counts.items()
        ):
            result.fail(
                "r2_same_benchmark_judgment_source_mismatch",
                "The quality benchmark must match same-decision counts computed while loading R1R judgments.",
                path="llm_judgment_accounting.same_decision_counts",
                expected=expected_same_counts,
                actual=dict(llm_same_counts),
            )

        evidence_boundary = _get(summary, "evidence_version_boundary", MISSING)
        if not isinstance(evidence_boundary, Mapping):
            result.fail(
                "r2_evidence_version_boundary_missing",
                "R2 target requires non-ambiguous resolver and report commit evidence.",
                path="evidence_version_boundary",
            )
        else:
            resolver_sha = str(evidence_boundary.get("resolver_evidence_code_sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40}", resolver_sha):
                result.fail(
                    "r2_evidence_version_sha_invalid",
                    "R2 must report the exact resolver code revision executed for evidence.",
                    path="evidence_version_boundary.resolver_evidence_code_sha",
                    actual=resolver_sha,
                )
            execution_changed = evidence_boundary.get("post_evidence_execution_code_changed", MISSING)
            if type(execution_changed) is not bool or execution_changed is not False:
                result.fail(
                    "r2_evidence_version_flag_missing_or_invalid",
                    "R2 target requires explicit proof that resolver/database execution semantics did not change after evidence.",
                    path="evidence_version_boundary.post_evidence_execution_code_changed",
                    expected=False,
                    actual=evidence_boundary.get("post_evidence_execution_code_changed", "<missing>"),
                )
            scope = str(evidence_boundary.get("post_evidence_execution_code_scope") or "")
            if scope != "resolver_candidate_edge_union_cache_and_persistence_semantics":
                result.fail(
                    "r2_evidence_execution_scope_missing_or_invalid",
                    "R2 must define the exact resolver/database execution semantics covered by the unchanged claim.",
                    path="evidence_version_boundary.post_evidence_execution_code_scope",
                    actual=scope,
                )
            proof_changed = evidence_boundary.get("post_evidence_proof_code_changed", MISSING)
            if type(proof_changed) is not bool or proof_changed is not True:
                result.fail(
                    "r2_evidence_proof_code_change_not_disclosed",
                    "The final proof-only runner/contract closeout changes must be explicitly disclosed.",
                    path="evidence_version_boundary.post_evidence_proof_code_changed",
                    expected=True,
                    actual=evidence_boundary.get("post_evidence_proof_code_changed", "<missing>"),
                )
            compared_paths = evidence_boundary.get("execution_code_paths_compared", MISSING)
            compared_path_set = (
                {str(value) for value in compared_paths}
                if isinstance(compared_paths, list)
                else set()
            )
            if compared_path_set != R2_EVIDENCE_EXECUTION_CODE_PATHS:
                result.fail(
                    "r2_evidence_execution_path_coverage_invalid",
                    "R2 must identify every resolver, runner, and contract path checked after evidence.",
                    path="evidence_version_boundary.execution_code_paths_compared",
                    expected=sorted(R2_EVIDENCE_EXECUTION_CODE_PATHS),
                    actual=sorted(compared_path_set),
                )
            path_results = evidence_boundary.get("execution_code_path_results", MISSING)
            if not isinstance(path_results, Mapping) or set(path_results) != R2_EVIDENCE_EXECUTION_CODE_PATHS:
                result.fail(
                    "r2_evidence_execution_path_results_invalid",
                    "R2 must disclose the diff classification for each compared resolver, runner, and contract path.",
                    path="evidence_version_boundary.execution_code_path_results",
                )
            elif (
                path_results.get("backend/app/services/source_concept_resolver_service.py") != "unchanged"
                or not str(path_results.get("scripts/run_phase45_scv2_r2_constraint_aware_graph_remediation.py") or "").startswith("proof_only")
                or not str(path_results.get("scripts/phase_contracts/contract_checks.py") or "").startswith("proof_only")
            ):
                result.fail(
                    "r2_evidence_execution_path_classification_invalid",
                    "R2 must distinguish the unchanged resolver from proof-only runner and contract closeout changes.",
                    path="evidence_version_boundary.execution_code_path_results",
                    actual=dict(path_results),
                )
            git_relationship = str(evidence_boundary.get("git_relationship_model") or "")
            if not all(phrase in git_relationship for phrase in ("ancestor", "no direct-parent relationship")):
                result.fail(
                    "r2_git_relationship_model_missing_or_invalid",
                    "R2 must describe an ancestor relationship without inventing a direct parent.",
                    path="evidence_version_boundary.git_relationship_model",
                    actual=git_relationship,
                )
            version_model = str(evidence_boundary.get("report_version_model") or "")
            if not all(phrase in version_model for phrase in ("final PR head", "PR body", "self-referential")):
                result.fail(
                    "r2_report_version_model_missing_or_invalid",
                    "The report must explain why the final PR head is recorded externally instead of self-referentially.",
                    path="evidence_version_boundary.report_version_model",
                    actual=version_model,
                )
            for forbidden_key in (
                "report_commit_parent_sha",
                "report_only_commit",
                "post_evidence_resolver_code_changed",
            ):
                if forbidden_key in evidence_boundary:
                    result.fail(
                        "r2_ambiguous_evidence_topology_field_present",
                        "R2 must not encode review-environment-dependent commit-topology claims in the public summary.",
                        path=f"evidence_version_boundary.{forbidden_key}",
                    )
        if _has(summary, "head_sha"):
            result.fail(
                "r2_ambiguous_top_level_head_sha_present",
                "R2 must not retain an ambiguous generic head_sha after resolver evidence regeneration.",
                path="head_sha",
                actual=_get(summary, "head_sha", None),
            )

    if target:
        if not _as_bool(_get(summary, "public_redaction.passed", False)):
            result.fail("r2_public_redaction_failed", "R2 public redaction must pass.", path="public_redaction.passed")
        pack = _get(summary, "review_pack", {})
        if not isinstance(pack, Mapping) or not all(
            _as_bool(pack.get(key))
            for key in ("generated", "manifest_present", "checksums_present", "integrity_passed", "not_committed")
        ):
            result.fail("r2_review_pack_incomplete", "R2 target requires a complete private review pack.", path="review_pack")

    route = _get(summary, "route_authorization", MISSING)
    if not isinstance(route, Mapping):
        result.fail("r2_route_authorization_not_object", "R2 requires downstream non-authorization proof.", path="route_authorization")
    else:
        if target:
            for key in R2_REQUIRED_ROUTE_AUTHORIZATION_FLAGS:
                actual = route.get(key, MISSING)
                if type(actual) is not bool or actual is not False:
                    result.fail(
                        "r2_route_authorization_flag_missing_or_invalid",
                        "R2 target requires every downstream authorization flag to exist as exact false.",
                        path=f"route_authorization.{key}",
                        expected=False,
                        actual="<missing>" if actual is MISSING else actual,
                    )
        forbidden_true = sorted(key for key, value in route.items() if value is True)
        if forbidden_true:
            result.fail("r2_forbidden_route_authorization", "R2 cannot authorize downstream/provider/production/truth work.", path="route_authorization", actual=forbidden_true)


def _check_r2r_autonomous_recall_search_closure(
    _contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    """Fail-closed SCV2-R2R gate for autonomous closure without human queues."""

    status = str(_get(summary, "pipeline_contract.status", "") or "")
    target = status == "target_met_autonomous_recall_search_closure"
    if status not in R2R_AUTONOMOUS_RECALL_SEARCH_CLOSURE_STATUSES:
        result.fail(
            "r2r_status_invalid",
            "R2R status must use the autonomous closure vocabulary.",
            path="pipeline_contract.status",
            expected=list(R2R_AUTONOMOUS_RECALL_SEARCH_CLOSURE_STATUSES),
            actual=status,
        )
        return
    if not target and _completion_or_approval_claimed(result):
        result.fail(
            "r2r_non_target_status_claims_completion",
            "Only target_met_autonomous_recall_search_closure may claim completion.",
            path="pipeline_contract.claims",
        )

    isolation = _get(summary, "environment_isolation", {})
    if not isinstance(isolation, Mapping):
        result.fail("r2r_isolation_not_object", "R2R requires structured isolation proof.", path="environment_isolation")
        isolation = {}
    isolation_expected = {
        "passed": True,
        "dev_test_only": True,
        "working_db_is_separate_from_r2_baseline": True,
        "r2_baseline_preserved": True,
        "production_profile_active": False,
        "canonical_production_profile_flag_checked": True,
        "production_write_attempted": False,
        "protected_source_write_attempted": False,
    }
    if target or status != "blocked_environment_isolation":
        for key, expected in isolation_expected.items():
            actual = isolation.get(key, MISSING)
            if type(actual) is not bool or actual is not expected:
                result.fail(
                    "r2r_isolation_proof_missing_or_invalid",
                    "R2R isolation flags must be exact booleans.",
                    path=f"environment_isolation.{key}",
                    expected=expected,
                    actual="<missing>" if actual is MISSING else actual,
                )
    if status == "partial_autonomous_closure" and _get(
        summary, "zero_provider_closeout.completed", False
    ) is True:
        closeout = _get(summary, "zero_provider_closeout", {})
        for key, expected in {
            "completed": True,
            "provider_surface_initialized": False,
            "provider_calls": 0,
            "graph_rebuilt": False,
            "materialization_rebuilt": False,
            "existing_working_database_reused": True,
        }.items():
            if closeout.get(key, MISSING) != expected:
                result.fail(
                    "r2r_zero_provider_closeout_invalid",
                    "Partial closeout must prove zero provider use and reuse accepted graph state.",
                    path=f"zero_provider_closeout.{key}",
                    expected=expected,
                    actual=closeout.get(key, "<missing>"),
                )

    fixed = _get(summary, "fixed_input_proof", {})
    if not isinstance(fixed, Mapping):
        result.fail("r2r_fixed_input_not_object", "R2R requires fixed-input proof.", path="fixed_input_proof")
        fixed = {}
    if target:
        for key in (
            "present",
            "baseline_to_working_clone_match",
            "before_after_match",
            "row_counts_match",
            "schemas_match",
            "content_fingerprints_match",
            "forbidden_truth_content_unchanged",
        ):
            if fixed.get(key) is not True:
                result.fail(
                    "r2r_fixed_evidence_changed_or_unproven",
                    "Target status requires unchanged fixed and forbidden evidence.",
                    path=f"fixed_input_proof.{key}",
                    expected=True,
                    actual=fixed.get(key),
                )
        if fixed.get("changed_tables") != [] or fixed.get("forbidden_truth_changed_tables") != []:
            result.fail(
                "r2r_fixed_evidence_changed",
                "R2R cannot complete with changed fixed or forbidden tables.",
                path="fixed_input_proof",
            )

    operations = _get(summary, "operation_counts", {})
    if not isinstance(operations, Mapping):
        result.fail("r2r_operation_counts_not_object", "R2R requires forbidden-operation accounting.", path="operation_counts")
        operations = {}
    forbidden_zero_keys = (
        "gallery_dl_calls",
        "provider_metadata_acquisition_calls",
        "pixiv_provider_calls",
        "ai_tagging_calls",
        "media_imports",
        "classification_calls",
        "localization_calls",
        "upstream_observation_mutations",
        "production_writes",
        "truth_path_writes",
        "fallback_provider_calls",
    )
    if status != "blocked_environment_isolation":
        for key in forbidden_zero_keys:
            if type(operations.get(key, MISSING)) is not int or operations.get(key) != 0:
                result.fail(
                    "r2r_forbidden_operation_nonzero_or_missing",
                    "R2R requires explicit zero acquisition/import/truth/production/fallback operations.",
                    path=f"operation_counts.{key}",
                    expected=0,
                    actual=operations.get(key, "<missing>"),
                )
    if status == "blocked_llm_approval_required" and _as_int(operations.get("primary_provider_calls"), default=-1) != 0:
        result.fail(
            "r2r_provider_called_before_approval",
            "The initial cache-only run must make zero primary-provider calls.",
            path="operation_counts.primary_provider_calls",
            expected=0,
            actual=operations.get("primary_provider_calls"),
        )

    authorization = _get(summary, "provider_authorization", {})
    if not isinstance(authorization, Mapping):
        result.fail(
            "r2r_provider_authorization_not_object",
            "R2R requires structured provider authorization proof.",
            path="provider_authorization",
        )
        authorization = {}
    provider_execution_authorized = authorization.get("status") == "approved"
    if provider_execution_authorized:
        required_authorization = {
            "approved_scope": "pr_135_autonomous_pair_closure",
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
        for key, expected in required_authorization.items():
            actual = authorization.get(key, MISSING)
            if actual is MISSING or type(actual) is not type(expected) or actual != expected:
                result.fail(
                    "r2r_provider_authorization_invalid",
                    "Provider execution requires the exact scope-bounded PR #135 authorization proof.",
                    path=f"provider_authorization.{key}",
                    expected=expected,
                    actual="<missing>" if actual is MISSING else actual,
                )
    primary_calls = _as_int(operations.get("primary_provider_calls"), default=0)
    if primary_calls > 0 and not provider_execution_authorized:
        result.fail(
            "r2r_provider_called_without_authorization",
            "Primary-provider calls require explicit scope-bounded authorization proof.",
            path="provider_authorization.status",
            expected="approved",
            actual=authorization.get("status"),
        )

    population = _get(summary, "candidate_population", {})
    dispositions = _get(summary, "candidate_dispositions", {})
    if not isinstance(population, Mapping) or not isinstance(dispositions, Mapping):
        result.fail("r2r_candidate_accounting_not_object", "R2R requires candidate population and dispositions.", path="candidate_population")
        population = {}
        dispositions = {}
    total = _as_int(population.get("total_candidate_pairs"), default=-1)
    manifest_pairs = _as_int(population.get("candidate_manifest_pair_count"), default=total)
    unique_eligible = _as_int(population.get("unique_budget_eligible_pair_count"), default=total)
    if manifest_pairs != unique_eligible or total != manifest_pairs:
        result.fail(
            "r2r_candidate_manifest_unique_population_mismatch",
            "Candidate deduplication must happen before manifest, budget, selection, and call ceilings.",
            path="candidate_population",
            expected="total_candidate_pairs = candidate_manifest_pair_count = unique_budget_eligible_pair_count",
            actual={"total": total, "manifest": manifest_pairs, "unique_eligible": unique_eligible},
        )
    must_link = _as_int(dispositions.get("must_link_count"), default=-1)
    cannot_link = _as_int(dispositions.get("cannot_link_count"), default=-1)
    deferred = _as_int(dispositions.get("deferred_nonblocking_count"), default=-1)
    unaccounted = _as_int(dispositions.get("unaccounted_pair_count"), default=-1)
    coverage = dispositions.get("candidate_disposition_coverage")
    equality = total == must_link + cannot_link + deferred
    if target and (
        min(total, must_link, cannot_link, deferred, unaccounted) < 0
        or not equality
        or unaccounted != 0
        or coverage != 1.0
        or dispositions.get("accounting_equality_passed") is not True
        or dispositions.get("duplicate_disposition_count") != 0
        or dispositions.get("silently_dropped_pair_count") != 0
    ):
        result.fail(
            "r2r_candidate_disposition_accounting_incomplete",
            "Every candidate pair must have exactly one machine disposition.",
            path="candidate_dispositions",
            expected="total = must_link + cannot_link + deferred_nonblocking; coverage=1.0",
            actual={
                "total": total,
                "must_link": must_link,
                "cannot_link": cannot_link,
                "deferred_nonblocking": deferred,
                "unaccounted": unaccounted,
                "coverage": coverage,
            },
        )

    automation = _get(summary, "automation_invariants", {})
    if not isinstance(automation, Mapping):
        result.fail("r2r_automation_invariants_not_object", "R2R requires automation invariants.", path="automation_invariants")
        automation = {}
    for key, expected in {
        "manual_review_required_count": 0,
        "operator_blocking_review_count": 0,
        "manual_review_queue_generated": False,
    }.items():
        actual = automation.get(key, MISSING)
        valid = type(actual) is int and actual == expected if type(expected) is int else type(actual) is bool and actual is expected
        if not valid:
            result.fail(
                "r2r_human_review_dependency_present",
                "R2R must not create or depend on human review.",
                path=f"automation_invariants.{key}",
                expected=expected,
                actual="<missing>" if actual is MISSING else actual,
            )

    materialization = _get(summary, "materialization_projection", {})
    if not isinstance(materialization, Mapping):
        result.fail("r2r_materialization_not_object", "R2R requires materialization projection proof.", path="materialization_projection")
        materialization = {}
    if target:
        for key, expected in {
            "materialized_needs_review_count": 0,
            "unresolved_evidence_retained": True,
            "idempotent_fingerprint_match": True,
            "deferred_overlay_versioned": True,
            "deferred_overlay_atomic": True,
        }.items():
            actual = materialization.get(key, MISSING)
            valid = type(actual) is int and actual == expected if type(expected) is int else type(actual) is bool and actual is expected
            if not valid:
                result.fail(
                    "r2r_materialization_projection_failed",
                    "R2R target requires zero materialized needs_review rows and retained unresolved evidence.",
                    path=f"materialization_projection.{key}",
                    expected=expected,
                    actual="<missing>" if actual is MISSING else actual,
                )
    if status == "partial_autonomous_closure" and _get(
        summary, "zero_provider_closeout.completed", False
    ) is True:
        for key, expected in {
            "materialized_needs_review_count": 0,
            "unresolved_evidence_retained": True,
            "deferred_overlay_versioned": True,
            "deferred_overlay_atomic": True,
            "fallback_index_generated": True,
            "fallback_index_idempotent": True,
            "fallback_index_identity_union_allowed": False,
            "manual_review_queue_generated": False,
        }.items():
            if materialization.get(key, MISSING) != expected:
                result.fail(
                    "r2r_partial_materialization_proof_incomplete",
                    "Partial closeout requires measured overlay/materialization lifecycle proof.",
                    path=f"materialization_projection.{key}",
                    expected=expected,
                    actual=materialization.get(key, "<missing>"),
                )

    graph = _get(summary, "graph_invariants", {})
    if not isinstance(graph, Mapping):
        result.fail("r2r_graph_invariants_not_object", "R2R requires graph invariants.", path="graph_invariants")
        graph = {}
    if target:
        for key in (
            "review_or_deferred_edge_used_in_union_count",
            "direct_cannot_violation_count",
            "transitive_cannot_violation_count",
            "deterministic_hard_conflict_count",
            "unauthorized_unknown_role_materialization_count",
            "unexplained_proof_grade_same_regression_count",
        ):
            if _as_int(graph.get(key), default=-1) != 0:
                result.fail(
                    "r2r_constraint_regression",
                    "R2R target requires zero union/cannot/unknown-role/same-regression violations.",
                    path=f"graph_invariants.{key}",
                    expected=0,
                    actual=graph.get(key),
                )

    llm = _get(summary, "llm_execution", {})
    checkpoint = _get(summary, "checkpoint_proof", {})
    if not isinstance(llm, Mapping) or not isinstance(checkpoint, Mapping):
        result.fail("r2r_llm_or_checkpoint_not_object", "R2R requires LLM and checkpoint proof.", path="llm_execution")
        llm = {}
        checkpoint = {}
    if target:
        if (
            _as_int(llm.get("remaining_unaccounted_missing_pairs"), default=-1) != 0
            or _as_int(llm.get("provider_failure_count"), default=-1) != 0
            or llm.get("all_approved_missing_pairs_accounted") is not True
            or llm.get("failed_judgments_counted_as_success") is not False
            or llm.get("primary_provider_only") is not True
            or llm.get("fallback_provider_used") is not False
            or llm.get("usage_accounting_complete") is not True
            or checkpoint.get("durable_checkpoint_passed") is not True
            or checkpoint.get("atomic_per_success_persistence") is not True
            or checkpoint.get("final_regeneration_cache_only") is not True
            or _as_int(checkpoint.get("final_regeneration_provider_calls"), default=-1) != 0
        ):
            result.fail(
                "r2r_llm_checkpoint_incomplete",
                "Target status requires complete approved-pair accounting and cache-only regeneration.",
                path="llm_execution",
            )

    search = _get(summary, "search_benchmark", {})
    if not isinstance(search, Mapping):
        result.fail("r2r_search_benchmark_not_object", "R2R requires automated search benchmark proof.", path="search_benchmark")
        search = {}
    if target:
        for key, expected in {
            "generated": True,
            "reproducible": True,
            "identity_and_fallback_reported_separately": True,
            "symmetry_improved_vs_r2": True,
            "unmatched_seeds_decreased_vs_r2": True,
            "average_overlap_improved_vs_r2": True,
            "giant_component_recurrence": False,
        }.items():
            actual = search.get(key, MISSING)
            valid = type(actual) is int and actual == expected if type(expected) is int else type(actual) is bool and actual is expected
            if not valid:
                result.fail(
                    "r2r_search_target_failed",
                    "R2R target requires reproducible dual-path search evidence without giant-component recurrence.",
                    path=f"search_benchmark.{key}",
                    expected=expected,
                    actual="<missing>" if actual is MISSING else actual,
                )
        erratum = _get(summary, "search_semantics_interpretation_erratum", {})
        if not isinstance(erratum, Mapping) or any(
            erratum.get(key, MISSING) is not expected
            for key, expected in {
                "old_interpretation_superseded": True,
                "historical_numeric_fields_preserved": True,
                "identity_union_is_search_result_union": False,
                "shared_bare_name_results_are_legitimate_when_supported": True,
                "and_search_is_media_level_intersection": True,
                "cannot_link_blocks_direct_supported_matches": False,
            }.items()
        ):
            result.fail(
                "r2r_search_semantics_erratum_missing",
                "R2R search evidence must carry the corrected identity-union versus search-union interpretation.",
                path="search_semantics_interpretation_erratum",
            )
        fallback_index = search.get("indexed_fallback")
        if not isinstance(fallback_index, Mapping) or not all(
            fallback_index.get(key) is expected
            for key, expected in {
                "generated": True,
                "deterministic": True,
                "idempotent": True,
                "full_signal_python_scan_per_query": False,
                "source_layer_only": True,
                "identity_union_allowed": False,
            }.items()
        ):
            result.fail(
                "r2r_indexed_fallback_proof_failed",
                "Target status requires a deterministic indexed source-layer fallback lookup.",
                path="search_benchmark.indexed_fallback",
            )
    if status == "partial_autonomous_closure" and _get(
        summary, "zero_provider_closeout.completed", False
    ) is True:
        for key, expected in {
            "benchmark_uses_persisted_runtime_index": True,
            "runtime_benchmark_equality_passed": True,
            "experimental_fallback_enabled_by_default": False,
        }.items():
            if search.get(key, MISSING) != expected:
                result.fail(
                    "r2r_partial_search_runtime_proof_incomplete",
                    "Partial closeout benchmark must use the persisted opt-in runtime path.",
                    path=f"search_benchmark.{key}",
                    expected=expected,
                    actual=search.get(key, "<missing>"),
                )
        output_proof = _get(summary, "r2r_output_mutation_proof", {})
        if not isinstance(output_proof, Mapping) or (
            output_proof.get("fallback_index_table_included") is not True
            or output_proof.get("unexpected_changed_tables") != []
            or output_proof.get("fallback_index_second_fingerprint_match") is not True
        ):
            result.fail(
                "r2r_partial_output_mutation_proof_incomplete",
                "Partial closeout must include the fallback index in deterministic mutation proof.",
                path="r2r_output_mutation_proof",
            )

    if target:
        if not _as_bool(_get(summary, "public_redaction.passed", False)):
            result.fail("r2r_public_redaction_failed", "R2R public redaction must pass.", path="public_redaction.passed")
        pack = _get(summary, "review_pack", {})
        if not isinstance(pack, Mapping) or not all(
            _as_bool(pack.get(key))
            for key in ("generated", "manifest_present", "checksums_present", "integrity_passed", "not_committed")
        ):
            result.fail("r2r_review_pack_incomplete", "R2R requires an integrity-checked private review pack.", path="review_pack")

    route = _get(summary, "route_authorization", {})
    if not isinstance(route, Mapping):
        result.fail("r2r_route_authorization_not_object", "R2R requires explicit downstream non-authorization.", path="route_authorization")
    else:
        forbidden_true = sorted(key for key, value in route.items() if value is True)
        if forbidden_true:
            result.fail(
                "r2r_forbidden_route_authorization",
                "R2R cannot authorize downstream, production, or truth work.",
                path="route_authorization",
                actual=forbidden_true,
            )


def _check_ml1_multilingual_alias_source_metadata_closure(
    _contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    """Fail-closed SCV2-ML1 gate for read-only multilingual/source closure."""

    status = str(_get(summary, "pipeline_contract.status", "") or "")
    target = status == "target_met_multilingual_alias_source_metadata_closure"
    partial_foundation = status == "partial_ml1_pixiv_metadata_foundation_complete"
    if status not in ML1_MULTILINGUAL_ALIAS_SOURCE_METADATA_CLOSURE_STATUSES:
        result.fail(
            "ml1_status_invalid",
            "ML1 status must use the registered closure vocabulary.",
            path="pipeline_contract.status",
            expected=list(ML1_MULTILINGUAL_ALIAS_SOURCE_METADATA_CLOSURE_STATUSES),
            actual=status,
        )
        return
    if not target and not partial_foundation and _completion_or_approval_claimed(result):
        result.fail(
            "ml1_non_target_status_claims_completion",
            "Only target_met_multilingual_alias_source_metadata_closure may claim completion.",
            path="pipeline_contract.claims",
        )
    claims = _get(summary, "pipeline_contract.claims", {})
    if partial_foundation:
        expected_partial_claims = {
            "target_met": False,
            "safe_to_merge": True,
            "route_approved": True,
        }
        if not isinstance(claims, Mapping) or any(
            claims.get(key, MISSING) is not expected
            for key, expected in expected_partial_claims.items()
        ):
            result.fail(
                "ml1_partial_foundation_claims_invalid",
                "Project-lead-approved partial ML1 foundation requires exact non-target merge/ML2-route claims.",
                path="pipeline_contract.claims",
                expected=expected_partial_claims,
                actual=claims,
            )

    documents = _get(summary, "document_semantics", {})
    if not isinstance(documents, Mapping):
        result.fail("ml1_document_semantics_not_object", "ML1 requires structured durable-policy proof.", path="document_semantics")
        documents = {}
    if status != "blocked_document_semantics_not_corrected":
        expected_document_flags = {
            "passed": True,
            "durable_policy_created": True,
            "r2r_interpretation_erratum_present": True,
            "old_one_name_one_family_interpretation_superseded": True,
            "identity_union_is_search_result_union": False,
            "shared_bare_name_results_are_legitimate_when_supported": True,
            "cannot_link_globally_suppresses_direct_matches": False,
            "and_search_is_media_level_intersection": True,
            "current_phase_is_ml1": True,
        }
        for key, expected in expected_document_flags.items():
            if documents.get(key, MISSING) is not expected:
                result.fail(
                    "ml1_document_semantics_incomplete",
                    "ML1 cannot continue while durable search semantics remain stale or contradictory.",
                    path=f"document_semantics.{key}",
                    expected=expected,
                    actual=documents.get(key, "<missing>"),
                )
        if _as_int(documents.get("contradictory_statement_count"), default=-1) != 0:
            result.fail(
                "ml1_document_semantics_contradictory",
                "Current guidance must contain zero contradictory search-semantics statements.",
                path="document_semantics.contradictory_statement_count",
                expected=0,
                actual=documents.get("contradictory_statement_count"),
            )

    isolation = _get(summary, "environment_isolation", {})
    if not isinstance(isolation, Mapping):
        result.fail("ml1_isolation_not_object", "ML1 requires structured isolation proof.", path="environment_isolation")
        isolation = {}
    if status != "blocked_environment_isolation":
        expected_network_disabled = _as_int(
            _get(summary, "acquisition_execution.provider_request_attempt_count", 0), default=0
        ) == 0
        for key, expected in {
            "passed": True,
            "violet_env_test": True,
            "accepted_r2r_database_immutable": True,
            "source_database_immutable": True,
            "production_profile_active": False,
            "production_write_attempted": False,
            "network_disabled": expected_network_disabled,
        }.items():
            if isolation.get(key, MISSING) is not expected:
                result.fail(
                    "ml1_isolation_proof_missing_or_invalid",
                    "ML1 initial execution requires immutable dev/test evidence and zero network.",
                    path=f"environment_isolation.{key}",
                    expected=expected,
                    actual=isolation.get(key, "<missing>"),
                )

    operations = _get(summary, "operation_counts", {})
    if not isinstance(operations, Mapping):
        result.fail("ml1_operation_counts_not_object", "ML1 requires forbidden-operation accounting.", path="operation_counts")
        operations = {}
    authorized_external_keys = {
        "gallery_dl_calls",
        "pixiv_provider_calls",
        "provider_metadata_acquisition_calls",
    }
    for key in (
        "gallery_dl_calls",
        "pixiv_provider_calls",
        "provider_metadata_acquisition_calls",
        "media_downloads",
        "llm_provider_calls",
        "fallback_provider_calls",
        "accepted_r2r_pair_readjudications",
        "fixed_evidence_mutations",
        "truth_path_writes",
        "production_writes",
        "media_imports",
        "ai_tagging_calls",
        "classification_calls",
        "localization_calls",
        "entity_writes",
    ):
        actual_operation = operations.get(key, MISSING)
        valid = type(actual_operation) is int and actual_operation >= 0
        if key not in authorized_external_keys:
            valid = valid and actual_operation == 0
        if not valid:
            result.fail(
                "ml1_forbidden_operation_nonzero_or_missing",
                "ML1 permits only explicitly bounded Pixiv metadata calls; every other operation must remain zero.",
                path=f"operation_counts.{key}",
                expected="nonnegative bounded external count or zero forbidden count",
                actual=operations.get(key, "<missing>"),
            )
    if status == "blocked_credential_rotation_confirmation_required":
        for key in authorized_external_keys:
            if _as_int(operations.get(key), default=-1) != 0:
                result.fail(
                    "ml1_credential_block_external_call_occurred",
                    "Credential-rotation blocking status requires zero external Pixiv/gallery-dl calls.",
                    path=f"operation_counts.{key}",
                    expected=0,
                    actual=operations.get(key),
                )

    credential_safety = _get(summary, "credential_safety", {})
    if not isinstance(credential_safety, Mapping):
        result.fail("ml1_credential_safety_not_object", "ML1 requires structured credential-safety evidence.", path="credential_safety")
        credential_safety = {}
    if credential_safety.get("policy") == "operator_accepted_local_credential_risk_v1":
        for key, expected in {
            "project_owner_authorized": True,
            "credential_rotation_required": False,
            "fingerprint_scan_required": False,
            "existing_profile_use_authorized": True,
            "scope": "isolated_ml1_pixiv_metadata_only_execution",
            "production_allowed": False,
            "raw_secret_exposure_allowed": False,
            "rotation_confirmation_present": False,
            "known_old_secret_fingerprint_scan_performed": False,
            "raw_secret_value_exposed": False,
        }.items():
            if credential_safety.get(key, MISSING) != expected:
                result.fail("ml1_local_credential_risk_waiver_invalid", "The owner waiver must remain explicit, isolated, and non-production.", path=f"credential_safety.{key}", expected=expected, actual=credential_safety.get(key, "<missing>"))
        environment = _get(summary, "environment_isolation", {})
        if not isinstance(environment, Mapping) or (
            environment.get("violet_env_test") is not True
            or environment.get("database_identity") != "blombooru_scv2_ml1_acquisition_test_20260712"
            or environment.get("accepted_r2r_database_immutable") is not True
            or environment.get("production_profile_active") is not False
            or environment.get("production_write_attempted") is not False
        ):
            result.fail("ml1_local_credential_risk_waiver_environment_invalid", "The credential waiver is valid only for the exact isolated ML1 test database.", path="environment_isolation")

    acquisition = _get(summary, "acquisition_execution", {})
    if not isinstance(acquisition, Mapping):
        result.fail("ml1_acquisition_accounting_not_object", "ML1 requires bounded manifest execution accounting.", path="acquisition_execution")
        acquisition = {}
    integer_fields = (
        "acquisition_manifest_distinct_work_count", "max_attempts_per_work",
        "unique_work_ids_attempted_count", "normal_manifest_work_ids_attempted_count",
        "conflict_manifest_work_ids_attempted_count", "provider_request_attempt_count",
        "gallery_dl_call_count", "successful_work_count", "terminal_work_count",
        "retryable_work_count", "normalization_failed_work_count",
        "provider_identity_mismatch_work_count", "skipped_complete_work_count", "resumed_work_count",
        "duplicate_unexpected_work_attempt_count", "out_of_manifest_work_attempt_count",
        "complete_work_reacquisition_count", "max_observed_attempts_for_one_work",
    )
    for key in integer_fields:
        value = acquisition.get(key, MISSING)
        if type(value) is not int or value < 0:
            result.fail("ml1_acquisition_accounting_invalid", "Acquisition execution counters must be explicit nonnegative integers.", path=f"acquisition_execution.{key}", actual=value)
    manifest_count = _as_int(acquisition.get("acquisition_manifest_distinct_work_count"), default=-1)
    conflict_manifest_count = _as_int(acquisition.get("conflict_resolution_manifest_count"), default=0)
    total_governed_manifest_count = manifest_count + max(conflict_manifest_count, 0)
    max_attempts = _as_int(acquisition.get("max_attempts_per_work"), default=-1)
    unique_attempted = _as_int(acquisition.get("unique_work_ids_attempted_count"), default=-1)
    request_attempts = _as_int(acquisition.get("provider_request_attempt_count"), default=-1)
    fingerprint = acquisition.get("acquisition_manifest_fingerprint")
    conflict_fingerprint = acquisition.get("conflict_resolution_manifest_fingerprint")
    for key, value in (
        ("acquisition_manifest_fingerprint", fingerprint),
        ("conflict_resolution_manifest_fingerprint", conflict_fingerprint),
    ):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            result.fail("ml1_acquisition_manifest_fingerprint_invalid", "Executable manifests require reproducible SHA-256 fingerprints, including empty fixed-point manifests.", path=f"acquisition_execution.{key}")
    if max_attempts < 1 or max_attempts > 3:
        result.fail("ml1_acquisition_retry_budget_invalid", "ML1 allows one to three attempts per manifest work.", path="acquisition_execution.max_attempts_per_work", actual=max_attempts)
    if unique_attempted > total_governed_manifest_count:
        result.fail("ml1_acquisition_unique_work_bound_exceeded", "Attempted work IDs cannot exceed the acquisition plus separately governed conflict manifest.", path="acquisition_execution.unique_work_ids_attempted_count", expected=f"<= {total_governed_manifest_count}", actual=unique_attempted)
    if request_attempts > total_governed_manifest_count * max(max_attempts, 0):
        result.fail("ml1_acquisition_request_bound_exceeded", "Provider requests exceeded governed manifest size times retry allowance.", path="acquisition_execution.provider_request_attempt_count", expected=f"<= {total_governed_manifest_count * max(max_attempts, 0)}", actual=request_attempts)
    if request_attempts < unique_attempted:
        result.fail("ml1_acquisition_attempt_attribution_invalid", "Every attempted unique work requires at least one attributable request.", path="acquisition_execution.provider_request_attempt_count")
    final_outcomes = acquisition.get("final_outcome_counts")
    allowed_final_outcomes = {
        "metadata_complete",
        "terminal_remote_unavailable",
        "retryable_exhausted_or_systemically_stopped",
        "normalization_failed",
        "provider_identity_mismatch",
        "conflict_resolved_metadata_complete",
        "conflict_resolved_terminal_unavailable",
        "conflict_unresolved_after_exact_provider_evidence",
        "conflict_normalization_failed",
        "conflict_retryable_exhausted",
    }
    if not isinstance(final_outcomes, Mapping):
        result.fail("ml1_acquisition_final_outcomes_missing", "Every attempted work requires one exhaustive final outcome.", path="acquisition_execution.final_outcome_counts")
        final_outcomes = {}
    unknown_outcomes = sorted(set(final_outcomes) - allowed_final_outcomes)
    invalid_outcome_counts = {
        key: value for key, value in final_outcomes.items()
        if type(value) is not int or value < 0
    }
    outcome_sum = sum(value for value in final_outcomes.values() if type(value) is int and value >= 0)
    if unknown_outcomes or invalid_outcome_counts or outcome_sum != unique_attempted:
        result.fail(
            "ml1_acquisition_final_outcome_accounting_invalid",
            "Final outcome buckets must be valid, mutually exclusive, and exhaustive for attempted works.",
            path="acquisition_execution.final_outcome_counts",
            expected=unique_attempted,
            actual={"sum": outcome_sum, "unknown": unknown_outcomes, "invalid": invalid_outcome_counts},
        )
    normal_outcome_sum = sum(
        _as_int(final_outcomes.get(key), default=0)
        for key in (
            "metadata_complete",
            "terminal_remote_unavailable",
            "retryable_exhausted_or_systemically_stopped",
            "normalization_failed",
            "provider_identity_mismatch",
        )
    )
    conflict_outcome_sum = sum(
        _as_int(final_outcomes.get(key), default=0)
        for key in (
            "conflict_resolved_metadata_complete",
            "conflict_resolved_terminal_unavailable",
            "conflict_unresolved_after_exact_provider_evidence",
            "conflict_normalization_failed",
            "conflict_retryable_exhausted",
        )
    )
    normal_attempted = _as_int(acquisition.get("normal_manifest_work_ids_attempted_count"), default=-1)
    conflict_attempted = _as_int(acquisition.get("conflict_manifest_work_ids_attempted_count"), default=-1)
    if (
        normal_outcome_sum != normal_attempted
        or conflict_outcome_sum != conflict_attempted
        or normal_attempted > manifest_count
        or conflict_attempted > conflict_manifest_count
        or normal_attempted + conflict_attempted != unique_attempted
    ):
        result.fail("ml1_acquisition_manifest_outcome_attribution_invalid", "Main/conflict outcomes must belong to their executable manifests and cover every attempted work exactly once.", path="acquisition_execution", actual={"normal_outcomes": normal_outcome_sum, "normal_attempted": normal_attempted, "conflict_outcomes": conflict_outcome_sum, "conflict_attempted": conflict_attempted})
    ledger_fingerprint = acquisition.get("final_outcome_ledger_fingerprint")
    if unique_attempted > 0 and re.fullmatch(r"[0-9a-f]{64}", str(ledger_fingerprint or "")) is None:
        result.fail("ml1_acquisition_outcome_ledger_fingerprint_invalid", "Attempted-work outcome ledger requires a reproducible private fingerprint.", path="acquisition_execution.final_outcome_ledger_fingerprint")
    for key in ("gallery_dl_call_count",):
        if _as_int(acquisition.get(key), default=-1) != request_attempts:
            result.fail("ml1_acquisition_external_count_mismatch", "gallery-dl calls must equal provider request attempts.", path=f"acquisition_execution.{key}", expected=request_attempts, actual=acquisition.get(key))
    for key in ("gallery_dl_calls", "pixiv_provider_calls", "provider_metadata_acquisition_calls"):
        if _as_int(operations.get(key), default=-1) != request_attempts:
            result.fail("ml1_acquisition_external_count_mismatch", "All external Pixiv counters must equal provider request attempts.", path=f"operation_counts.{key}", expected=request_attempts, actual=operations.get(key))
    for key in ("duplicate_unexpected_work_attempt_count", "out_of_manifest_work_attempt_count", "complete_work_reacquisition_count"):
        if _as_int(acquisition.get(key), default=-1) != 0:
            result.fail("ml1_acquisition_scope_violation", "Out-of-manifest, unexpected duplicate, and complete-work attempts are forbidden.", path=f"acquisition_execution.{key}", expected=0, actual=acquisition.get(key))
    if _as_int(acquisition.get("max_observed_attempts_for_one_work"), default=-1) > max_attempts:
        result.fail("ml1_acquisition_per_work_retry_bound_exceeded", "No manifest work may exceed max_attempts_per_work.", path="acquisition_execution.max_observed_attempts_for_one_work", expected=f"<= {max_attempts}", actual=acquisition.get("max_observed_attempts_for_one_work"))
    average_interval = acquisition.get("average_request_interval_seconds")
    if request_attempts > 1 and _as_float(average_interval, default=-1.0) < 2.0:
        result.fail("ml1_acquisition_request_spacing_invalid", "Observed average provider request interval must be at least two seconds.", path="acquisition_execution.average_request_interval_seconds", expected=">= 2.0", actual=average_interval)
    if (
        acquisition.get("systemic_stop") is True
        and acquisition.get("systemic_stop_stage") in {"canary", "main_manifest"}
        and acquisition.get("conflict_manifest_started") is True
    ):
        result.fail("ml1_systemic_stop_conflict_call_suppression_failed", "A canary/main systemic stop must suppress all conflict-manifest execution.", path="acquisition_execution.conflict_manifest_started", expected=False, actual=True)
    for key in ("retry_attempts_attributable_to_manifest_work", "resume_only_remaining_open_works"):
        if acquisition.get(key) is not True:
            result.fail("ml1_acquisition_resume_or_retry_proof_missing", "Retries and resume attempts must be attributable to remaining manifest work.", path=f"acquisition_execution.{key}", expected=True, actual=acquisition.get(key))
    for manifest_key, checkpoint_key in (
        ("acquisition_manifest_fingerprint", "checkpoint_main_manifest_fingerprint"),
        ("conflict_resolution_manifest_fingerprint", "checkpoint_conflict_manifest_fingerprint"),
    ):
        manifest_fingerprint = acquisition.get(manifest_key)
        checkpoint_fingerprint = acquisition.get(checkpoint_key)
        if manifest_fingerprint != checkpoint_fingerprint:
            result.fail("ml1_manifest_checkpoint_fingerprint_mismatch", "Executable manifest and checkpoint fingerprints must match exactly.", path=f"acquisition_execution.{checkpoint_key}", expected=manifest_fingerprint, actual=checkpoint_fingerprint)
    route_active = acquisition.get("acquisition_route_active") is True
    blocker_values = _get(summary, "pipeline_contract.active_blockers", [])
    blocked_zero_call = bool(
        {"blocked_credential_rotation_confirmation_required"}
        & set(blocker_values if isinstance(blocker_values, list) else [])
    )
    if (not route_active or blocked_zero_call) and request_attempts != 0:
        result.fail("ml1_acquisition_inactive_or_blocked_call_nonzero", "Inactive or credential/sample-blocked acquisition requires zero external calls.", path="acquisition_execution.provider_request_attempt_count", expected=0, actual=request_attempts)

    fixed = _get(summary, "fixed_evidence_proof", {})
    if not isinstance(fixed, Mapping):
        result.fail("ml1_fixed_evidence_not_object", "ML1 requires fixed-evidence proof.", path="fixed_evidence_proof")
        fixed = {}
    if status != "blocked_fixed_evidence_changed":
        for key, expected in {
            "present": True,
            "before_after_match": True,
            "accepted_r2r_dispositions_reused": True,
            "accepted_r2r_disposition_count": 3319,
            "forbidden_truth_content_unchanged": True,
            "changed_fixed_tables": [],
            "changed_forbidden_truth_tables": [],
        }.items():
            if fixed.get(key, MISSING) != expected:
                result.fail(
                    "ml1_fixed_evidence_changed_or_unproven",
                    "ML1 requires unchanged accepted R2R and forbidden truth evidence.",
                    path=f"fixed_evidence_proof.{key}",
                    expected=expected,
                    actual=fixed.get(key, "<missing>"),
                )
    else:
        changed_fixed = fixed.get("changed_fixed_tables")
        changed_forbidden = fixed.get("changed_forbidden_truth_tables")
        positive_change_proof = (
            fixed.get("before_after_match") is False
            or (isinstance(changed_fixed, list) and bool(changed_fixed))
            or (isinstance(changed_forbidden, list) and bool(changed_forbidden))
            or bool(fixed.get("missing_required_fixed_table_fingerprints"))
            or bool(fixed.get("missing_required_forbidden_table_fingerprints"))
            or bool(fixed.get("mismatched_required_fixed_table_fingerprints"))
            or bool(fixed.get("mismatched_required_forbidden_table_fingerprints"))
        )
        if not positive_change_proof:
            result.fail("ml1_fixed_evidence_block_unproven", "blocked_fixed_evidence_changed requires positive fixed/forbidden fingerprint change evidence.", path="fixed_evidence_proof")

    pixiv = _get(summary, "pixiv_accounting", {})
    if not isinstance(pixiv, Mapping):
        result.fail("ml1_pixiv_accounting_not_object", "ML1 requires media- and work-level Pixiv accounting.", path="pixiv_accounting")
        pixiv = {}
    if pixiv.get("canonical_complete_statuses") != ["accepted", "active", "metadata_complete", "observed"]:
        result.fail("ml1_canonical_complete_status_policy_mismatch", "Audit, ingestion, runner, closure, tests, and contract must share the canonical complete-status policy.", path="pixiv_accounting.canonical_complete_statuses")
    owner_sample = _get(summary, "owner_sample_validation", {})
    if not isinstance(owner_sample, Mapping):
        result.fail("ml1_owner_sample_not_object", "ML1 requires structured one-time owner sample proof.", path="owner_sample_validation")
        owner_sample = {}
    current_eligible_manifest_count = _as_int(
        owner_sample.get("current_eligible_manifest_count"), default=-1
    )
    required_sample_size = min(60, max(0, current_eligible_manifest_count))
    for key, expected in {
        "sample_generated": True,
        "sample_size": required_sample_size,
        "required_sample_size": required_sample_size,
        "normal_pipeline_human_dependency": False,
        "confirmation_env": None,
        "optional_stage_evidence": True,
        "runtime_gate_required": False,
    }.items():
        if owner_sample.get(key, MISSING) != expected:
            result.fail("ml1_owner_sample_proof_invalid", "Owner sample must be exact, deterministic, and non-runtime.", path=f"owner_sample_validation.{key}", expected=expected, actual=owner_sample.get(key, "<missing>"))
    if _as_int(owner_sample.get("conflict_cases_exported"), default=-1) < 0:
        result.fail("ml1_owner_sample_proof_invalid", "Conflict export count must be explicit.", path="owner_sample_validation.conflict_cases_exported")
    if current_eligible_manifest_count < 0:
        result.fail("ml1_owner_sample_proof_invalid", "Current eligible owner-review manifest size must be explicit.", path="owner_sample_validation.current_eligible_manifest_count")
    if re.fullmatch(r"[0-9a-f]{64}", str(owner_sample.get("owner_review_manifest_fingerprint") or "")) is None:
        result.fail("ml1_owner_sample_proof_invalid", "Owner sample requires a reproducible manifest fingerprint.", path="owner_sample_validation.sample_manifest_fingerprint")
    if status not in {
        "blocked_document_semantics_not_corrected",
        "blocked_environment_isolation",
        "blocked_pixiv_metadata_audit_incomplete",
    }:
        candidate_media = _as_int(pixiv.get("candidate_media_count"), default=-1)
        accounted_media = _as_int(pixiv.get("accounted_media_count"), default=-2)
        status_sum = sum(
            _as_int(pixiv.get(key), default=-10**9)
            for key in (
                "metadata_present_complete_media_count",
                "terminal_remote_unavailable_media_count",
                "deferred_nonblocking_source_page_mismatch_media_count",
                "metadata_pending_media_count",
                "retryable_failure_media_count",
                "provider_identity_mismatch_media_count",
                "parse_or_identity_failure_media_count",
                "unexplained_missing_media_count",
            )
        )
        status_sum += _as_int(
            pixiv.get("no_durable_attempt_or_result_evidence_media_count", pixiv.get("not_attempted_media_count")),
            default=-10**9,
        )
        if candidate_media < 0 or accounted_media != candidate_media or status_sum != candidate_media:
            result.fail(
                "ml1_pixiv_media_accounting_incomplete",
                "Every canonical Pixiv filename candidate media/page must have exactly one status.",
                path="pixiv_accounting",
                expected=candidate_media,
                actual={"accounted": accounted_media, "status_sum": status_sum},
            )
        candidate_work = _as_int(pixiv.get("candidate_distinct_work_count"), default=-1)
        work_status_sum = sum(
            _as_int(pixiv.get(key), default=-10**9)
            for key in (
                "metadata_present_complete_work_count",
                "terminal_remote_unavailable_work_count",
                "deferred_nonblocking_source_page_mismatch_work_count",
                "pending_work_count",
                "retryable_work_count",
                "normalization_failed_work_count",
                "provider_identity_mismatch_work_count",
                "missing_work_count",
                "conflict_unresolved_work_count",
            )
        )
        if (
            candidate_work < 0
            or _as_int(pixiv.get("accounted_distinct_work_count"), default=-2) != candidate_work
            or work_status_sum != candidate_work
        ):
            result.fail(
                "ml1_pixiv_work_accounting_incomplete",
                "Every distinct canonical Pixiv work ID must be accounted.",
                path="pixiv_accounting.accounted_distinct_work_count",
                expected=candidate_work,
                actual={"accounted": pixiv.get("accounted_distinct_work_count"), "status_sum": work_status_sum},
            )
        for key in ("candidate_media_accounting_coverage", "candidate_work_accounting_coverage"):
            if _as_float(pixiv.get(key), default=-1.0) != 1.0:
                result.fail("ml1_pixiv_accounting_coverage_failed", "Pixiv accounting coverage must equal 1.0.", path=f"pixiv_accounting.{key}")
        if "all_eligible_media_metadata_coverage" in pixiv:
            result.fail("ml1_pixiv_metric_semantics_regressed", "Queue/decision coverage must not be labeled provider metadata coverage.", path="pixiv_accounting.all_eligible_media_metadata_coverage")
        metric_pairs = (
            ("pixiv_candidate_complete_media_count", "pixiv_candidate_media_count", "pixiv_candidate_complete_media_coverage"),
            ("pixiv_candidate_complete_work_count", "pixiv_candidate_work_count", "pixiv_candidate_complete_work_coverage"),
            ("pixiv_ingestion_decision_media_count", "all_eligible_media_count", "pixiv_ingestion_decision_coverage"),
        )
        for numerator_key, denominator_key, coverage_key in metric_pairs:
            numerator = _as_int(pixiv.get(numerator_key), default=-1)
            denominator = _as_int(pixiv.get(denominator_key), default=-1)
            expected_coverage = round(numerator / denominator, 6) if denominator > 0 and numerator >= 0 else 1.0 if denominator == 0 and numerator == 0 else -1.0
            if numerator < 0 or denominator < 0 or _as_float(pixiv.get(coverage_key), default=-2.0) != expected_coverage:
                result.fail("ml1_pixiv_metric_semantics_regressed", "Pixiv decision and provider-completeness metrics require truthful numerators, denominators, and coverage.", path=f"pixiv_accounting.{coverage_key}", expected=expected_coverage, actual=pixiv.get(coverage_key))

    creator_metrics = _get(summary, "creator_metadata", {})
    if isinstance(creator_metrics, Mapping):
        if "successful_pixiv_metadata_record_count" in creator_metrics:
            result.fail("ml1_pixiv_metric_semantics_regressed", "Registry/queue records must not be labeled successful provider metadata.", path="creator_metadata.successful_pixiv_metadata_record_count")
        for key in (
            "pixiv_registry_record_count",
            "pixiv_queue_decision_record_count",
            "provider_metadata_record_count",
            "successful_acquisition_work_count",
            "successful_acquisition_media_or_page_count",
            "queue_records_carrying_acquired_provider_payload_count",
            "terminal_evidence_record_count",
            "deferred_page_mismatch_record_count",
        ):
            if type(creator_metrics.get(key)) is not int or creator_metrics.get(key) < 0:
                result.fail("ml1_pixiv_metric_semantics_regressed", "Pixiv registry/provider outcome counters must be explicit nonnegative integers.", path=f"creator_metadata.{key}")
        if _as_int(
            creator_metrics.get(
                "untrusted_parent_query_visible_creator_observation_count"
            ),
            default=-1,
        ) != 0:
            result.fail(
                "ml1_untrusted_parent_creator_observation_nonzero",
                "Query-visible PR #136 creator observations require a trusted complete Pixiv parent.",
                path="creator_metadata.untrusted_parent_query_visible_creator_observation_count",
                expected=0,
                actual=creator_metrics.get(
                    "untrusted_parent_query_visible_creator_observation_count"
                ),
            )

    governance_transition = _get(summary, "governance_transition", {})
    if isinstance(governance_transition, Mapping):
        governance_selection = governance_transition.get("selection") or {}
        if _as_int(
            governance_selection.get("deferred_returned_page_row_count_after"),
            default=-1,
        ) != 0:
            result.fail(
                "ml1_deferred_returned_page_row_nonzero",
                "Only provider-absent page rows may remain governed deferred.",
                path="governance_transition.selection.deferred_returned_page_row_count_after",
                expected=0,
                actual=governance_selection.get("deferred_returned_page_row_count_after"),
            )

    if status == "blocked_pixiv_incremental_acquisition_approval_required":
        if pixiv.get("incremental_acquisition_required") is not True or (
            _as_int(pixiv.get("retryable_failure_media_count"), default=0)
            + _as_int(pixiv.get("not_attempted_media_count"), default=0)
            + _as_int(pixiv.get("unexplained_missing_media_count"), default=0)
            <= 0
        ):
            result.fail(
                "ml1_pixiv_acquisition_block_unproven",
                "Pixiv acquisition block requires an exact non-empty retryable/missing scope.",
                path="pixiv_accounting",
            )
        for key in ("projected_gallery_dl_request_count", "authentication_requirements_present", "rate_limit_plan_present", "checkpoint_resume_plan_present"):
            value = pixiv.get(key, MISSING)
            valid = type(value) is int and value > 0 if key.endswith("count") else value is True
            if not valid:
                result.fail("ml1_pixiv_acquisition_manifest_incomplete", "Blocked acquisition requires request/auth/rate/checkpoint projection.", path=f"pixiv_accounting.{key}")

    creator = _get(summary, "creator_metadata", {})
    multilingual = _get(summary, "multilingual_benchmark", {})
    candidate = _get(summary, "candidate_generation", {})
    search = _get(summary, "search_semantics", {})
    for name, value in (("creator_metadata", creator), ("multilingual_benchmark", multilingual), ("candidate_generation", candidate), ("search_semantics", search)):
        if not isinstance(value, Mapping):
            result.fail(f"ml1_{name}_not_object", f"ML1 requires structured {name} proof.", path=name)

    active_blockers = _get(summary, "pipeline_contract.active_blockers", [])
    if not isinstance(active_blockers, list) or any(not isinstance(item, str) for item in active_blockers):
        result.fail("ml1_active_blockers_invalid", "ML1 summaries must expose every active blocker as a string list.", path="pipeline_contract.active_blockers")
        active_blockers = []

    status_proven = True
    if status == "blocked_document_semantics_not_corrected":
        status_proven = documents.get("passed") is not True or _as_int(documents.get("contradictory_statement_count"), default=0) > 0
    elif status == "blocked_environment_isolation":
        status_proven = any(
            isolation.get(key, MISSING) is not expected
            for key, expected in {
                "passed": True,
                "violet_env_test": True,
                "accepted_r2r_database_immutable": True,
                "source_database_immutable": True,
                "production_profile_active": False,
                "production_write_attempted": False,
            }.items()
        )
    elif status == "blocked_credential_rotation_confirmation_required":
        credential = _get(summary, "credential_safety", {})
        status_proven = (
            isinstance(credential, Mapping)
            and credential.get("rotation_confirmation_present") is False
            and credential.get("policy") != "operator_accepted_local_credential_risk_v1"
            and credential.get("external_call_attempted") is False
            and bool(pixiv.get("incremental_acquisition_required"))
        )
    elif status == "blocked_fixed_evidence_changed":
        status_proven = (
            fixed.get("before_after_match") is False
            or bool(fixed.get("changed_fixed_tables"))
            or bool(fixed.get("changed_forbidden_truth_tables"))
            or bool(fixed.get("missing_required_fixed_table_fingerprints"))
            or bool(fixed.get("missing_required_forbidden_table_fingerprints"))
            or bool(fixed.get("mismatched_required_fixed_table_fingerprints"))
            or bool(fixed.get("mismatched_required_forbidden_table_fingerprints"))
        )
    elif status == "blocked_pixiv_metadata_audit_incomplete":
        status_proven = (
            pixiv.get("work_accounting_equality_holds") is False
            or _as_float(pixiv.get("candidate_work_accounting_coverage"), default=0.0) != 1.0
            or _as_float(pixiv.get("candidate_media_accounting_coverage"), default=0.0) != 1.0
        )
    elif status == "blocked_pixiv_incremental_acquisition_approval_required":
        route = _get(summary, "route_authorization", {})
        status_proven = (
            bool(pixiv.get("incremental_acquisition_required"))
            and _as_int(pixiv.get("projected_gallery_dl_request_count"), default=0) > 0
            and isinstance(route, Mapping)
            and route.get("pixiv_acquisition_authorized") is False
        )
    elif status == "blocked_pixiv_acquisition_execution_incomplete":
        credential = _get(summary, "credential_safety", {})
        credential_gate_satisfied = isinstance(credential, Mapping) and (
            credential.get("rotation_confirmation_present") is True
            or (
                credential.get("policy") == "operator_accepted_local_credential_risk_v1"
                and credential.get("project_owner_authorized") is True
            )
        )
        status_proven = (
            credential_gate_satisfied
            and (
                _as_int(pixiv.get("retryable_work_count"), default=0)
                + _as_int(pixiv.get("missing_work_count"), default=0)
                + _as_int(pixiv.get("conflict_unresolved_work_count"), default=0)
                + _as_int(pixiv.get("normalization_failed_work_count"), default=0)
                + _as_int(pixiv.get("provider_identity_mismatch_work_count"), default=0)
            ) > 0
        )
    elif status == "blocked_creator_metadata_loss":
        status_proven = _as_int(creator.get("silently_dropped_creator_field_count"), default=0) > 0
    elif status == "blocked_multilingual_benchmark_incomplete":
        status_proven = multilingual.get("actual_runtime_search_used") is not True or multilingual.get("synthetic_alias_media_propagation_used") is not False
    elif status == "blocked_candidate_generation_gap":
        status_proven = _as_int(multilingual.get("candidate_not_generated_count"), default=0) > 0 or _as_int(candidate.get("unresolved_candidate_generation_count"), default=0) > 0
    elif status == "blocked_llm_approval_required":
        budget = _get(summary, "llm_budget_policy", {})
        status_proven = (
            _as_int(candidate.get("new_pair_manifest_count"), default=0) > 0
            and candidate.get("llm_approval_required") is True
            and (not isinstance(budget, Mapping) or budget.get("preauthorized") is not True)
        )
    elif status == "blocked_and_search_semantics":
        status_proven = (
            search.get("shared_name_union_passed") is not True
            or _as_int(search.get("and_constraint_leakage_count"), default=0) > 0
            or _as_int(search.get("unsupported_result_media_count"), default=0) > 0
            or _as_int(search.get("rejected_evidence_result_count"), default=0) > 0
            or _as_int(search.get("superseded_evidence_result_count"), default=0) > 0
        )
    elif status in {"partial_ml1_pixiv_metadata_closure_complete", "partial_ml1_pixiv_metadata_foundation_complete"}:
        foundation = _get(summary, "pixiv_metadata_foundation", {})
        status_proven = (
            isinstance(foundation, Mapping)
            and foundation.get("current_stock_closed") is True
            and foundation.get("continuous_ingestion_gate_implemented") is True
            and _as_float(
                foundation.get("complete_terminal_or_deferred_coverage"), default=0.0
            ) == 1.0
            and _as_int(
                foundation.get("deferred_nonblocking_source_page_mismatch_work_count"),
                default=-1,
            ) >= 0
        )
    if status.startswith("blocked_") and status not in active_blockers:
        result.fail("ml1_primary_blocker_missing_from_active_blockers", "The primary blocked status must appear in active_blockers.", path="pipeline_contract.active_blockers", actual=active_blockers)
    if not status_proven:
        result.fail("ml1_status_evidence_missing", "The selected ML1 status lacks its required executable evidence.", path="pipeline_contract.status", actual=status)

    known_blockers: set[str] = set()
    if documents.get("passed") is not True or _as_int(documents.get("contradictory_statement_count"), default=0) > 0:
        known_blockers.add("blocked_document_semantics_not_corrected")
    if pixiv.get("work_accounting_equality_holds") is False or _as_float(pixiv.get("candidate_work_accounting_coverage"), default=1.0) != 1.0:
        known_blockers.add("blocked_pixiv_metadata_audit_incomplete")
    if pixiv.get("incremental_acquisition_required") is True:
        route_state = _get(summary, "route_authorization", {})
        credential_state = _get(summary, "credential_safety", {})
        if isinstance(route_state, Mapping) and route_state.get("pixiv_acquisition_authorized") is False:
            known_blockers.add("blocked_pixiv_incremental_acquisition_approval_required")
        else:
            waiver_active = isinstance(credential_state, Mapping) and (
                credential_state.get("policy") == "operator_accepted_local_credential_risk_v1"
                and credential_state.get("project_owner_authorized") is True
            )
            rotation_active = isinstance(credential_state, Mapping) and credential_state.get("rotation_confirmation_present") is True
            if not (waiver_active or rotation_active):
                known_blockers.add("blocked_credential_rotation_confirmation_required")
            if waiver_active or rotation_active:
                known_blockers.add("blocked_pixiv_acquisition_execution_incomplete")
    if (
        _as_int(pixiv.get("normalization_failed_work_count"), default=0)
        + _as_int(pixiv.get("provider_identity_mismatch_work_count"), default=0)
        + _as_int(pixiv.get("conflict_unresolved_work_count"), default=0)
        > 0
    ):
        known_blockers.add("blocked_pixiv_acquisition_execution_incomplete")
    if _as_int(creator.get("silently_dropped_creator_field_count"), default=0) > 0:
        known_blockers.add("blocked_creator_metadata_loss")
    if multilingual.get("actual_runtime_search_used") is not True or multilingual.get("synthetic_alias_media_propagation_used") is not False:
        known_blockers.add("blocked_multilingual_benchmark_incomplete")
    if (
        not partial_foundation
        and (
            _as_int(multilingual.get("candidate_not_generated_count"), default=0) > 0
            or _as_int(candidate.get("unresolved_candidate_generation_count"), default=0) > 0
        )
    ):
        known_blockers.add("blocked_candidate_generation_gap")
    if (
        search.get("shared_name_union_passed") is not True
        or _as_int(search.get("and_constraint_leakage_count"), default=0) > 0
        or _as_int(search.get("unsupported_result_media_count"), default=0) > 0
        or _as_int(search.get("rejected_evidence_result_count"), default=0) > 0
        or _as_int(search.get("superseded_evidence_result_count"), default=0) > 0
    ):
        known_blockers.add("blocked_and_search_semantics")
    missing_active_blockers = sorted(known_blockers - set(active_blockers))
    if missing_active_blockers:
        result.fail(
            "ml1_active_blockers_incomplete",
            "ML1 active_blockers must expose every blocker derivable from current evidence.",
            path="pipeline_contract.active_blockers",
            expected=sorted(known_blockers),
            actual=active_blockers,
        )

    if target:
        for key, expected in {
            "available_creator_fields_accounting_coverage": 1.0,
            "stable_creator_id_preservation_coverage": 1.0,
            "observed_creator_search_support_coverage": 1.0,
            "silently_dropped_creator_field_count": 0,
            "creator_role_misclassification_count": 0,
            "creator_search_passed": True,
            "creator_and_character_work_intersection_passed": True,
        }.items():
            actual = creator.get(key, MISSING) if isinstance(creator, Mapping) else MISSING
            if actual != expected:
                result.fail("ml1_creator_target_failed", "ML1 target requires complete creator retention and search support.", path=f"creator_metadata.{key}", expected=expected, actual=actual)

        for key, expected in {
            "observed_alias_accounting_coverage": 1.0,
            "signal_generation_coverage": 1.0,
            "candidate_family_connectivity_coverage": 1.0,
            "adjudication_coverage": 1.0,
            "search_equivalence_coverage": 1.0,
            "and_work_equivalence_coverage": 1.0,
            "unexplained_multilingual_split_count": 0,
            "candidate_not_generated_count": 0,
            "role_or_context_loss_count": 0,
            "human_review_queue_generated": False,
        }.items():
            actual = multilingual.get(key, MISSING) if isinstance(multilingual, Mapping) else MISSING
            if actual != expected:
                result.fail("ml1_multilingual_target_failed", "ML1 target requires complete observed multilingual-family accounting.", path=f"multilingual_benchmark.{key}", expected=expected, actual=actual)

        for key, expected in {
            "all_misses_classified": True,
            "unresolved_candidate_generation_count": 0,
            "representative_edge_semantic_ranking_passed": True,
            "fresh_old_schema_migration_passed": True,
        }.items():
            actual = candidate.get(key, MISSING) if isinstance(candidate, Mapping) else MISSING
            if actual != expected:
                result.fail("ml1_candidate_generation_target_failed", "ML1 target requires closed candidate-generation accounting.", path=f"candidate_generation.{key}", expected=expected, actual=actual)

        for key, expected in {
            "runtime_application_path_used": True,
            "shared_name_union_passed": True,
            "unsupported_result_media_count": 0,
            "rejected_evidence_result_count": 0,
            "identity_union_from_search_count": 0,
            "and_constraint_leakage_count": 0,
            "direct_or_accepted_alias_support_coverage": 1.0,
            "multilingual_and_work_equivalence_coverage": 1.0,
            "creator_and_character_work_accuracy": 1.0,
        }.items():
            actual = search.get(key, MISSING) if isinstance(search, Mapping) else MISSING
            if actual != expected:
                result.fail("ml1_search_semantics_target_failed", "ML1 target requires supported shared-name union and leak-free AND search.", path=f"search_semantics.{key}", expected=expected, actual=actual)

        for key in (
            "normal_retrievable_missing_media_count",
            "not_attempted_media_count",
            "unexplained_missing_media_count",
            "work_id_mismatch_media_count",
        ):
            if _as_int(pixiv.get(key), default=-1) != 0:
                result.fail("ml1_pixiv_target_gap", "ML1 target requires zero retrievable/unattempted/unexplained/mismatched Pixiv gaps.", path=f"pixiv_accounting.{key}", expected=0, actual=pixiv.get(key))

    if status == "blocked_llm_approval_required":
        if _as_int(candidate.get("new_pair_manifest_count"), default=0) <= 0 or candidate.get("llm_approval_required") is not True:
            result.fail("ml1_llm_block_unproven", "LLM block requires an exact non-empty new-pair manifest.", path="candidate_generation")

    graph = _get(summary, "graph_invariants", {})
    if not isinstance(graph, Mapping):
        result.fail("ml1_graph_invariants_not_object", "ML1 requires graph-invariant proof.", path="graph_invariants")
        graph = {}
    for key in (
        "review_or_deferred_identity_union_count",
        "direct_cannot_violation_count",
        "transitive_cannot_violation_count",
        "unauthorized_unknown_role_materialization_count",
        "identity_changes_caused_by_search_count",
    ):
        if _as_int(graph.get(key), default=-1) != 0:
            result.fail("ml1_graph_invariant_failed", "ML1 must preserve identity/cannot/unknown-role invariants.", path=f"graph_invariants.{key}", expected=0, actual=graph.get(key))

    if _get(summary, "public_redaction.passed", False) is not True:
        result.fail("ml1_public_redaction_failed", "ML1 public artifacts must pass redaction.", path="public_redaction.passed")
    pack = _get(summary, "review_pack", {})
    if not isinstance(pack, Mapping) or not all(pack.get(key) is True for key in ("generated", "manifest_present", "checksums_present", "integrity_passed", "not_committed")):
        result.fail("ml1_review_pack_incomplete", "ML1 requires an integrity-checked private review pack.", path="review_pack")

    route = _get(summary, "route_authorization", {})
    if not isinstance(route, Mapping):
        result.fail("ml1_route_authorization_not_object", "ML1 requires explicit downstream non-authorization.", path="route_authorization")
    else:
        forbidden_true = sorted(
            key for key, value in route.items()
            if value is True and key != "pixiv_acquisition_authorized"
        )
        if forbidden_true:
            result.fail("ml1_forbidden_route_authorization", "ML1 may authorize only the exact bounded Pixiv metadata route, never downstream/production/truth work.", path="route_authorization", actual=forbidden_true)
        if partial_foundation and (
            route.get("route_approved_scope") != "SCV2-ML2_next_phase_only"
            or route.get("next_phase") != "SCV2-ML2: Multilingual Identity Candidate Closure"
            or route.get("production_authorized") is not False
            or route.get("scale_up_authorized") is not False
            or route.get("entity_bridge_authorized") is not False
            or route.get("truth_promotion_authorized") is not False
            or route.get("provider_2_authorized") is not False
            or route.get("full_library_execution_authorized") is not False
        ):
            result.fail(
                "ml1_partial_foundation_route_scope_invalid",
                "route_approved is limited to the separately governed SCV2-ML2 phase, never production, scale, Entity/truth, Provider-2, or full-library execution.",
                path="route_authorization",
            )

    if partial_foundation:
        governance = _get(summary, "governance_transition", {})
        selection = governance.get("selection", {}) if isinstance(governance, Mapping) else {}
        transition = governance.get("transition", {}) if isinstance(governance, Mapping) else {}
        operation_delta = governance.get("operation_delta", {}) if isinstance(governance, Mapping) else {}
        if not (
            isinstance(governance, Mapping)
            and governance.get("state") == "deferred_nonblocking_source_page_mismatch"
            and governance.get("policy_version") == "source_page_mismatch_deferred_nonblocking_v1"
            and isinstance(selection, Mapping)
            and _as_int(selection.get("distinct_work_count"), default=-1) == 14
            and _as_int(selection.get("main_manifest_work_count"), default=-1) == 11
            and _as_int(selection.get("conflict_manifest_work_count"), default=-1) == 3
            and _as_int(selection.get("distinct_work_count"), default=-1)
            == _as_int(
                pixiv.get("deferred_nonblocking_source_page_mismatch_work_count"),
                default=-2,
            )
            and selection.get("exact_predicate_passed") is True
            and selection.get("broader_normalization_or_conflict_population_converted") is False
            and isinstance(transition, Mapping)
            and transition.get("idempotent") is True
            and transition.get("raw_and_historical_queue_evidence_preserved") is True
            and transition.get("unsupported_page_link_created") is False
            and transition.get("conflict_winner_selected") is False
            and isinstance(operation_delta, Mapping)
            and operation_delta
            and all(type(value) is int and value == 0 for value in operation_delta.values())
        ):
            result.fail(
                "ml1_deferred_page_mismatch_governance_unproven",
                "Partial ML1 closure requires exact, idempotent, zero-network 11+3 page-mismatch governance evidence.",
                path="governance_transition",
            )
        for key in (
            "pending_work_count",
            "retryable_work_count",
            "missing_work_count",
            "normalization_failed_work_count",
            "provider_identity_mismatch_work_count",
            "conflict_unresolved_work_count",
        ):
            if _as_int(pixiv.get(key), default=-1) != 0:
                result.fail(
                    "ml1_partial_foundation_open_or_blocking_state_nonzero",
                    "safe_to_merge requires zero pending, retryable, missing, normalization, identity-mismatch, and blocking-conflict works.",
                    path=f"pixiv_accounting.{key}",
                    expected=0,
                    actual=pixiv.get(key),
                )
        if _as_float(
            pixiv.get("complete_terminal_or_deferred_work_coverage"), default=0.0
        ) != 1.0:
            result.fail(
                "ml1_partial_foundation_governed_coverage_incomplete",
                "Complete/terminal/governed-deferred work coverage must equal 1.0.",
                path="pixiv_accounting.complete_terminal_or_deferred_work_coverage",
                expected=1.0,
                actual=pixiv.get("complete_terminal_or_deferred_work_coverage"),
            )


def _zero_eligible_proof_passed(plan: Mapping[str, Any]) -> bool:
    proof = plan.get("zero_eligible_proof")
    if isinstance(proof, Mapping):
        return _as_bool(proof.get("passed") or proof.get("valid") or proof.get("explicit"))
    return _as_bool(proof)


def _zero_eligible_reason_present(plan: Mapping[str, Any]) -> bool:
    proof = plan.get("zero_eligible_proof")
    values: list[Any] = [
        plan.get("zero_eligible_reason"),
        plan.get("zero_eligible_proof_reason"),
        plan.get("zero_eligible_explanation"),
    ]
    if isinstance(proof, Mapping):
        values.extend([proof.get("reason"), proof.get("explanation"), proof.get("evidence")])
    for value in values:
        if isinstance(value, str) and value.strip() and not _safe_redacted(value):
            return True
        if isinstance(value, Mapping) and value:
            return True
    return False


def _llm_selected_pairs_resolved(summary: Mapping[str, Any], selected: int, judgment_count: int) -> bool:
    resolved = _get(summary, "llm_resolved_pair_count", MISSING)
    if resolved is not MISSING and _as_int(resolved, default=-1) >= selected:
        return True
    cached = _get(summary, "llm_cached_decision_count", MISSING)
    if cached is MISSING:
        cached = _get(summary, "llm_cache_summary.cached_decision_count", MISSING)
    skipped = _get(summary, "llm_skipped_with_explicit_reason_count", MISSING)
    has_additional_accounting = cached is not MISSING or skipped is not MISSING
    cached_count = _as_int(cached, default=0) if cached is not MISSING else 0
    skipped_count = _as_int(skipped, default=0) if skipped is not MISSING else 0
    if has_additional_accounting:
        return judgment_count + cached_count + skipped_count >= selected
    return judgment_count >= selected


def _check_review_pack(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    pack = _get(summary, "review_pack", MISSING)
    if pack is MISSING:
        pack = _get(summary, "chatgpt_review_pack", MISSING)
    if not isinstance(pack, Mapping):
        result.fail("review_pack_missing", "review_pack/chatgpt_review_pack object is required.", path="review_pack")
        return
    _check_review_pack_proof(pack, result, path_prefix="review_pack", require_zip=True)
    if _contains_private_review_pack_label(summary):
        result.fail("review_pack_private_label_leak", "Review pack contains reversible fixed-salt hashes or raw/private labels.", path="review_pack")


def _check_review_pack_proof(pack: Mapping[str, Any], result: ContractCheckResult, *, path_prefix: str, require_zip: bool) -> None:
    required_true = {
        "manifest_present": "manifest.json must exist.",
        "checksums_present": "checksums.json must exist.",
        "redaction_passed": "Review pack redaction scan must pass.",
        "redaction_scan_covers_final_file_set": "Final redaction scan must cover the final file set.",
        "not_committed": "Review pack zip/directory must not be committed.",
    }
    if require_zip:
        required_true["zip_generated"] = "Review pack zip must be generated."
    if not (_as_bool(pack.get("generated")) or _as_bool(pack.get("manifest_present"))):
        result.fail(
            "review_pack_generated_or_manifest_missing",
            "Review pack proof requires generated=true or manifest_present=true.",
            path=path_prefix,
            expected=True,
        )
    for key, message in required_true.items():
        if not _as_bool(pack.get(key)):
            result.fail("review_pack_required_flag_missing", message, path=f"{path_prefix}.{key}", expected=True, actual=pack.get(key))
    checksum_count = _as_int(pack.get("checksum_count", 0), default=-1)
    manifest = pack.get("manifest") if isinstance(pack.get("manifest"), Mapping) else {}
    manifest_checksum_count = _as_int(manifest.get("checksum_count", pack.get("manifest_checksum_count", checksum_count)), default=checksum_count)
    if checksum_count < 1:
        result.fail("review_pack_checksum_count_missing", "Review pack checksum count must be positive.", path=f"{path_prefix}.checksum_count")
    if manifest_checksum_count != checksum_count:
        result.fail("review_pack_checksum_count_mismatch", "Manifest checksum_count must match checksums.json count.", path=f"{path_prefix}.checksum_count", expected=manifest_checksum_count, actual=checksum_count)
    public_copy_ok = any(
        _as_bool(pack.get(key))
        for key in (
            "public_report_copy_fresh",
            "public_report_copy_rendered_from_current_summary",
            "public_report_copy_current",
            "public_report_copy_generated_from_current_summary",
        )
    )
    if not public_copy_ok:
        result.fail("review_pack_public_report_copy_missing", "Review pack requires current public-report-copy proof.", path=f"{path_prefix}.public_report_copy")


def _contains_private_review_pack_label(payload: Any) -> bool:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key).casefold()
            if "fixed_salt" in key_text:
                return True
            if key_text in {"raw_label", "raw_labels", "private_label", "private_labels"} and not _safe_redacted(value):
                return True
            if _contains_private_review_pack_label(value):
                return True
    elif isinstance(payload, list):
        return any(_contains_private_review_pack_label(item) for item in payload)
    elif isinstance(payload, str):
        folded = payload.casefold()
        if "fixed_salt" in folded or "fixed-salt" in folded:
            return True
    return False


def _check_route_audit(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    proof = _get(summary, "transaction_readonly_proof", {})
    if not isinstance(proof, Mapping):
        proof = {}
    read_only = str(proof.get("transaction_read_only", "")).casefold() == "on" or _as_bool(proof.get("read_only"))
    isolation = str(proof.get("transaction_isolation", "")).casefold()
    stable_snapshot = _as_bool(proof.get("stable_snapshot") or proof.get("snapshot_id_present"))
    if not read_only:
        result.fail("route_audit_not_read_only", "Route audit must use a read-only transaction.", path="transaction_readonly_proof.transaction_read_only", expected="on", actual=proof.get("transaction_read_only"))
    if isolation not in {"repeatable read", "serializable"}:
        result.fail("route_audit_weak_isolation", "Route audit must use a stable snapshot isolation level.", path="transaction_readonly_proof.transaction_isolation", expected="repeatable read or serializable", actual=proof.get("transaction_isolation"))
    if not stable_snapshot:
        result.fail("route_audit_missing_stable_snapshot", "Route audit must record stable snapshot proof.", path="transaction_readonly_proof.snapshot_id_present")

    upstream = _get(summary, "upstream_pipeline_contract", {})
    if not isinstance(upstream, Mapping):
        upstream = {}
    upstream_ok = _as_bool(upstream.get("passed"))
    status = result.status or "unknown"
    result.details["route_decision_status"] = status
    result.details["route_approved"] = result.route_approved
    status_folded = status.casefold()
    if result.route_approved and not upstream_ok:
        result.fail("route_approval_upstream_incomplete", "Route approval cannot proceed while upstream pipeline contract is failed or incomplete.", path="upstream_pipeline_contract")
    if result.route_approved:
        _check_route_approved_source_concept_upstream(upstream, result)
    if result.route_approved and ("blocked" in status_folded or "provisional" in status_folded or "inconclusive" in status_folded):
        result.fail("route_approval_blocked_or_provisional_status", "Route approval cannot be claimed while final_route_decision_status is blocked/provisional/inconclusive.", path="final_route_decision_status", actual=status)
    if "blocked" in status_folded or "provisional" in status_folded or "inconclusive" in status_folded:
        result.details["route_blocked_not_approved"] = True
    mutation = _get(summary, "mutation_proof", {})
    if not isinstance(mutation, Mapping):
        result.fail("route_audit_mutation_proof_not_object", "Route audit mutation_proof must be an object.", path="mutation_proof")
    else:
        mutation_passed = _as_bool(mutation.get("passed"))
        if not mutation_passed:
            result.fail("route_audit_mutation_proof_failed", "Route audits require mutation_proof.passed=true.", path="mutation_proof.passed", expected=True, actual=mutation.get("passed"))
        forbidden_names, unexpected_names = _mutation_table_violations(mutation)
        if forbidden_names:
            result.fail("route_audit_mutation_forbidden_table_changed", "Route audit detected forbidden table changes.", path="mutation_proof.forbidden_changed_tables", actual=forbidden_names)
        if unexpected_names:
            result.fail("route_audit_mutation_unexpected_table_changed", "Route audit detected unexpected table changes.", path="mutation_proof.unexpected_changed_tables", actual=unexpected_names)
    review_pack = _get(summary, "chatgpt_review_pack", _get(summary, "review_pack", {}))
    waiver = _get(summary, "route_audit_review_pack_waiver", _get(summary, "review_pack_waiver", {}))
    waiver_ok = isinstance(waiver, Mapping) and _as_bool(waiver.get("contract_approved")) and _as_bool(waiver.get("explicit"))
    review_pack_present = isinstance(review_pack, Mapping) and bool(review_pack)
    if result.route_approved and not review_pack_present and not waiver_ok:
        result.fail("route_audit_route_approval_missing_review_pack", "Route-approved summaries require review pack proof unless a contract-approved waiver is present.", path="chatgpt_review_pack")
    elif result.route_approved and review_pack_present:
        before = len(result.errors)
        _check_review_pack_proof(review_pack, result, path_prefix="chatgpt_review_pack", require_zip=False)
        if len(result.errors) > before:
            result.fail("route_audit_route_approval_incomplete_review_pack", "Route approval requires complete review-pack proof, not generated=true alone.", path="chatgpt_review_pack")
    if _is_a1r_route_audit(summary):
        _check_a1r_route_audit(summary, result)


def _is_a1r_route_audit(summary: Mapping[str, Any]) -> bool:
    return str(_get(summary, "phase_slug", "") or "") == "phase-4.5-scv2-a1r-route-audit-after-r1r" or str(
        _get(summary, "phase", "") or ""
    ) == "4.5-SCV2-A1R"


def _check_a1r_route_audit(summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "blocked_invalid_r1r_evidence",
        "blocked_missing_r1r_restored_snapshot",
        "blocked_read_only_audit_failed",
        "route_still_blocked",
        "route_partially_approved_for_one_next_phase",
        "route_ready_for_next_source_layer_phase",
    }
    status = str(result.status or "")
    if status not in allowed_statuses:
        result.fail(
            "a1r_route_status_unknown",
            "A1R route status must use the explicit A1R vocabulary.",
            path="final_route_decision_status",
            expected=sorted(allowed_statuses),
            actual=status or None,
        )

    intake = _get(summary, "r1r_evidence_intake", {})
    if not isinstance(intake, Mapping):
        result.fail("a1r_r1r_intake_not_object", "A1R requires structured R1R evidence intake.", path="r1r_evidence_intake")
    elif status != "blocked_invalid_r1r_evidence" and not _as_bool(intake.get("passed")):
        result.fail(
            "a1r_r1r_evidence_not_passed",
            "A1R cannot recommend a route unless R1R evidence intake passed.",
            path="r1r_evidence_intake.passed",
            expected=True,
            actual=intake.get("passed"),
        )

    route = _get(summary, "route_decision_matrix", {})
    if not isinstance(route, Mapping):
        result.fail("a1r_route_matrix_not_object", "A1R requires a route decision matrix.", path="route_decision_matrix")
        route = {}
    options = route.get("options") if isinstance(route, Mapping) else []
    if not isinstance(options, list):
        result.fail("a1r_route_options_not_list", "A1R route_decision_matrix.options must be a list.", path="route_decision_matrix.options")
        options = []
    recommended_options = [item for item in options if isinstance(item, Mapping) and _as_bool(item.get("recommended"))]
    if len(recommended_options) > 1:
        result.fail(
            "a1r_multiple_recommended_next_phases",
            "A1R may recommend no more than one next phase.",
            path="route_decision_matrix.options",
            expected="0 or 1 recommended option",
            actual=len(recommended_options),
        )

    recommended_next = _get(summary, "recommended_next_phase", None)
    status_recommends = status in {"route_partially_approved_for_one_next_phase", "route_ready_for_next_source_layer_phase"}
    if status_recommends:
        if len(recommended_options) != 1:
            result.fail(
                "a1r_recommended_status_without_exactly_one_option",
                "A1R recommended statuses require exactly one recommended option.",
                path="route_decision_matrix.options",
                expected=1,
                actual=len(recommended_options),
            )
        if not recommended_next:
            result.fail(
                "a1r_recommended_status_missing_next_phase",
                "A1R recommended statuses require recommended_next_phase.",
                path="recommended_next_phase",
            )
        elif recommended_options and str(recommended_options[0].get("candidate")) != str(recommended_next):
            result.fail(
                "a1r_recommended_next_phase_mismatch",
                "recommended_next_phase must match the sole recommended route matrix option.",
                path="recommended_next_phase",
                expected=recommended_options[0].get("candidate"),
                actual=recommended_next,
            )
        if not _get(summary, "required_contract_for_next_phase", None):
            result.fail(
                "a1r_recommended_next_contract_missing",
                "A1R recommendations must name the required next contract.",
                path="required_contract_for_next_phase",
            )
    elif recommended_next:
        result.fail(
            "a1r_blocked_status_has_next_phase",
            "Blocked A1R statuses must not recommend a next phase.",
            path="recommended_next_phase",
            actual=recommended_next,
        )

    authorization = _get(summary, "route_authorization", {})
    if not isinstance(authorization, Mapping):
        result.fail("a1r_route_authorization_not_object", "A1R requires explicit route_authorization flags.", path="route_authorization")
        authorization = {}
    for key in (
        "r2_started",
        "px1_b_started",
        "provider_2_started",
        "scale_up_started",
        "entity_bridge_started",
        "source_concept_truth_promotion_authorized",
        "entity_truth_authorized",
        "media_tags_truth_authorized",
        "production_write_authorized",
    ):
        if key not in authorization:
            result.fail(
                "a1r_route_authorization_flag_missing",
                "A1R must explicitly keep downstream start/truth/production flags false.",
                path=f"route_authorization.{key}",
                expected=False,
            )
        elif _as_bool(authorization.get(key)):
            result.fail(
                "a1r_forbidden_downstream_authorization",
                "A1R must not start or authorize broad downstream/truth/production work.",
                path=f"route_authorization.{key}",
                expected=False,
                actual=authorization.get(key),
            )

    still_blocked = authorization.get("still_blocked_routes", [])
    if recommended_options and isinstance(still_blocked, list):
        non_recommended = {
            str(item.get("candidate"))
            for item in options
            if isinstance(item, Mapping) and not _as_bool(item.get("recommended")) and item.get("candidate")
        }
        missing_blocked = sorted(non_recommended - {str(item) for item in still_blocked})
        if missing_blocked:
            result.fail(
                "a1r_non_recommended_routes_not_blocked",
                "A1R must list non-recommended routes as still blocked.",
                path="route_authorization.still_blocked_routes",
                expected=sorted(non_recommended),
                actual=still_blocked,
            )

    safety = _get(summary, "safety", {})
    if isinstance(safety, Mapping):
        for key in (
            "db_write_attempted",
            "provider_calls_attempted",
            "llm_provider_calls_attempted",
            "media_import_attempted",
            "classification_ai_localization_attempted",
            "source_concept_resolver_persistence_attempted",
            "entity_or_media_tags_truth_mutation_attempted",
            "source_icloud_app_storage_mutation_attempted",
            "cleanup_delete_reset_drop_truncate_attempted",
            "r2_started",
            "px1_b_started",
            "provider_2_started",
            "scale_up_started",
            "entity_bridge_started",
            "source_concept_truth_promotion_attempted",
        ):
            if _as_bool(safety.get(key)):
                result.fail(
                    "a1r_forbidden_work_attempted",
                    "A1R is read-only route audit work and must not attempt writes/providers/import/truth/downstream starts.",
                    path=f"safety.{key}",
                    expected=False,
                    actual=safety.get(key),
                )

    if not _as_bool(_get(summary, "public_redaction.passed", False)):
        result.fail("a1r_public_redaction_not_passed", "A1R public redaction must pass.", path="public_redaction.passed")
    review_pack = _get(summary, "chatgpt_review_pack", _get(summary, "review_pack", {}))
    if not isinstance(review_pack, Mapping) or not _as_bool(review_pack.get("generated")) or not _as_bool(review_pack.get("integrity_passed")):
        result.fail(
            "a1r_review_pack_missing_or_failed",
            "A1R must generate a review pack with integrity proof.",
            path="chatgpt_review_pack",
        )


def _check_route_approved_source_concept_upstream(upstream: Mapping[str, Any], result: ContractCheckResult) -> None:
    contract_id = upstream.get("contract_id")
    status = str(upstream.get("status") or upstream.get("pipeline_status") or upstream.get("contract_status") or "")
    status_folded = status.casefold()
    missing = upstream.get("missing_required_stages")
    if contract_id != "source_concept_full_chain_contract_v1":
        result.fail(
            "route_approval_missing_source_concept_full_chain_contract",
            "Route approval requires upstream SourceConcept full-chain contract evidence.",
            path="upstream_pipeline_contract.contract_id",
            expected="source_concept_full_chain_contract_v1",
            actual=contract_id,
        )
    if status != "full_chain_completed":
        result.fail(
            "route_approval_upstream_not_full_chain_completed",
            "Route approval requires upstream status full_chain_completed.",
            path="upstream_pipeline_contract.status",
            expected="full_chain_completed",
            actual=status or None,
        )
    if status_folded in {"deterministic_only", "full_chain_blocked_llm_unavailable", "full_chain_blocked_budget", "full_chain_inconclusive_missing_artifacts"} or "blocked" in status_folded or "inconclusive" in status_folded:
        result.fail("route_approval_upstream_blocked_or_deterministic", "Route approval cannot use deterministic-only, blocked, or inconclusive upstream evidence.", path="upstream_pipeline_contract.status", actual=status)
    if not _as_bool(upstream.get("passed")):
        result.fail("route_approval_upstream_contract_not_passed", "Route approval requires upstream pipeline contract passed=true.", path="upstream_pipeline_contract.passed", expected=True, actual=upstream.get("passed"))
    if not _as_bool(upstream.get("full_chain_fidelity_passed")):
        result.fail("route_approval_upstream_fidelity_not_passed", "Route approval requires upstream full_chain_fidelity_passed=true.", path="upstream_pipeline_contract.full_chain_fidelity_passed")
    if not isinstance(missing, list):
        result.fail("route_approval_upstream_missing_required_stages_absent", "Route approval requires upstream missing_required_stages to be explicitly recorded as [].", path="upstream_pipeline_contract.missing_required_stages", expected=[])
    elif missing:
        result.fail("route_approval_upstream_missing_required_stages", "Route approval requires no upstream missing_required_stages.", path="upstream_pipeline_contract.missing_required_stages", expected=[], actual=missing)


def _check_public_redaction(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    payloads: list[Any] = []
    if str(_get(summary, "phase_slug", "") or "") == "phase-4.5-scv2-r1r-full-source-concept-pipeline-replay":
        payloads.append(summary)
    elif _has(summary, "public_json_payload"):
        payloads.append(_get(summary, "public_json_payload"))
    else:
        payloads.append(summary)
    if _has(summary, "public_markdown_text"):
        payloads.append({"public_markdown_text": _get(summary, "public_markdown_text")})
    findings: list[dict[str, str]] = []
    for payload in payloads:
        findings.extend(scan_public_payload(payload))
    result.details["public_redaction_findings"] = findings
    for finding in findings:
        result.fail(f"public_redaction_{finding['code']}", "Public artifact redaction check failed.", path=finding["path"], actual=finding["match"])


def _check_mutation_safety(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    proof = _get(summary, "mutation_proof", {})
    if not isinstance(proof, Mapping):
        result.fail("mutation_proof_not_object", "mutation_proof must be an object.", path="mutation_proof")
        return
    if not _as_bool(proof.get("passed")):
        result.fail("mutation_proof_failed", "mutation_safety_contract_v1 requires mutation_proof.passed=true.", path="mutation_proof.passed", expected=True, actual=proof.get("passed"))
    forbidden_names, unexpected_names = _mutation_table_violations(proof)
    if forbidden_names:
        result.fail("mutation_forbidden_table_changed", "Forbidden table changes were detected.", path="mutation_proof.forbidden_changed_tables", actual=forbidden_names)
    if unexpected_names:
        result.fail("mutation_unexpected_table_changed", "Unexpected table changes were detected.", path="mutation_proof.unexpected_changed_tables", actual=unexpected_names)
    if _as_bool(_get(summary, "destructive_operation.executed", False)) and not _as_bool(_get(summary, "destructive_operation.contract_passed", False)):
        result.fail("mutation_destructive_without_contract", "Destructive operation executed without destructive contract proof.", path="destructive_operation")


def _mutation_table_violations(proof: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    delta = proof.get("delta") if isinstance(proof.get("delta"), Mapping) else proof
    forbidden = delta.get("forbidden_changed_tables") or []
    unexpected = delta.get("unexpected_changed_tables") or []
    changed = delta.get("changed_tables") or []
    forbidden_names = _table_names(forbidden)
    unexpected_names = _table_names(unexpected)
    for row in changed if isinstance(changed, list) else []:
        if isinstance(row, Mapping) and (row.get("prompt_forbidden") or row.get("allowed") is False):
            name = str(row.get("table") or row.get("name") or row)
            if row.get("prompt_forbidden") and name not in forbidden_names:
                forbidden_names.append(name)
            if row.get("allowed") is False and name not in unexpected_names:
                unexpected_names.append(name)
    return forbidden_names, unexpected_names


def _table_names(rows: Any) -> list[str]:
    names: list[str] = []
    if isinstance(rows, str):
        if rows.strip():
            names.append(rows)
    elif isinstance(rows, Mapping):
        if rows:
            names.append(str(rows.get("table") or rows.get("name") or rows))
    elif isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                names.append(str(row.get("table") or row.get("name") or row))
            else:
                names.append(str(row))
    return names


def _check_artifact_lifecycle(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    lifecycle = _get(summary, "artifact_lifecycle", MISSING)
    if lifecycle is MISSING:
        result.fail("artifact_lifecycle_missing", "artifact_lifecycle is required.", path="artifact_lifecycle")
        return
    artifacts: list[Mapping[str, Any]] = []
    if isinstance(lifecycle, list):
        artifacts = [item for item in lifecycle if isinstance(item, Mapping)]
    elif isinstance(lifecycle, Mapping):
        raw = lifecycle.get("artifacts")
        if isinstance(raw, list):
            artifacts = [item for item in raw if isinstance(item, Mapping)]
        else:
            for path, classification in lifecycle.items():
                if isinstance(classification, str):
                    artifacts.append({"path": path, "classification": classification})
    if not artifacts:
        result.fail("artifact_lifecycle_no_artifacts", "artifact_lifecycle must classify at least one artifact.", path="artifact_lifecycle")
        return
    for artifact in artifacts:
        classification = str(artifact.get("classification") or artifact.get("lifecycle") or "").casefold()
        normalized_classification = classification.replace("_", " ").replace("-", " ")
        path = str(artifact.get("path") or artifact.get("name") or "artifact")
        committed = _as_bool(artifact.get("committed"))
        if ("private" in normalized_classification or "one off local" in normalized_classification) and committed:
            result.fail("private_artifact_committed", "Private/local artifacts must not be committed.", path=path)
        if "review pack" in normalized_classification and committed:
            result.fail("review_pack_committed", "Review packs must not be committed.", path=path)
        if "public report" in normalized_classification or "public handoff" in normalized_classification:
            if "redacted" not in artifact:
                result.fail("public_artifact_redaction_evidence_missing", "Public report/handoff artifacts require explicit redaction evidence.", path=path)
            elif not _as_bool(artifact.get("redacted")):
                result.fail("public_artifact_not_redacted", "Public report/handoff artifacts must be redacted.", path=path)


def _check_destructive_operation(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    operation = _get(summary, "destructive_operation", {})
    if not isinstance(operation, Mapping):
        result.fail("destructive_operation_not_object", "destructive_operation must be an object.", path="destructive_operation")
        return
    required = (
        ("explicit_user_approval", "Explicit user approval is required."),
        ("dry_run_first", "Dry-run must run first."),
        ("backup_recovery_plan", "Backup/recovery plan is required."),
        ("exact_target_set", "Exact target set is required."),
        ("no_broad_wildcard_deletion", "Broad wildcard deletion is forbidden."),
        ("post_run_verification", "Post-run verification is required."),
    )
    for key, message in required:
        if not _as_bool(operation.get(key)):
            result.fail("destructive_operation_gate_missing", message, path=f"destructive_operation.{key}", expected=True, actual=operation.get(key))


def _check_entity_truth_bridge(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    bridge = _get(summary, "entity_truth_bridge", {})
    if not isinstance(bridge, Mapping):
        result.fail("entity_truth_bridge_not_object", "entity_truth_bridge must be an object.", path="entity_truth_bridge")
        return
    required = (
        ("route_approval", "Entity bridge requires upstream route approval."),
        ("preview_first", "Entity bridge requires preview first."),
        ("manual_confirmation_required", "Entity bridge requires manual confirmation."),
        ("audit_trail", "Entity bridge requires an audit trail."),
        ("rollback_or_supersede_behavior", "Entity bridge requires rollback/supersede behavior."),
        ("write_guards", "Entity bridge requires write guards."),
    )
    for key, message in required:
        if not _as_bool(bridge.get(key)):
            result.fail("entity_truth_bridge_gate_missing", message, path=f"entity_truth_bridge.{key}", expected=True, actual=bridge.get(key))


def _normalized_token_set(value: Any) -> set[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = []
    return {item.strip().casefold().replace("-", "_").replace(" ", "_") for item in raw_items if item.strip()}


def _check_required_false_paths(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    paths: Iterable[str],
    *,
    code: str,
    message: str,
) -> None:
    for path in paths:
        if _as_bool(_get(summary, path, False)):
            result.fail(code, message, path=path, expected=False, actual=True)


def _check_explicit_false_paths(
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    paths: Iterable[str],
    *,
    code: str,
    message: str,
) -> None:
    for path in paths:
        value = _get(summary, path, MISSING)
        if value is MISSING:
            result.fail(code, message, path=path, expected=False, actual="missing")
        elif value is not False:
            result.fail(code, message, path=path, expected=False, actual=value)


def _production_write_requested(summary: Mapping[str, Any]) -> list[str]:
    write_paths = (
        "write_requests.production_import",
        "write_requests.production_classification",
        "write_requests.production_ai_tagging",
        "write_requests.production_localization",
        "write_requests.source_root_registration",
        "write_requests.source_root_replacement",
        "write_requests.schema_setup",
        "write_requests.schema_migration",
    )
    return [path for path in write_paths if _as_bool(_get(summary, path, False))]


def _check_production_development_separation(
    _contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult
) -> None:
    _check_required_boolean_paths(
        summary,
        result,
        (
            "post_122_launcher_merged",
            "production_launcher_entry_documented",
            "production_profile_runtime_config_documented",
            "development_dotenv_not_production_source",
            "production_execution_requires_profile_or_runtime_config",
            "s2g_consolidated_route",
            "r1r_required_before_r2",
            "a1r_required_before_route_approval",
            "provider_entity_truth_blocked",
            "no_production_writes",
            "no_db_mutation",
            "no_source_icloud_mutation",
            "no_provider_calls",
            "safety.no_llm_calls",
            "safety.no_media_tags_mutation",
            "no_sourceconcept_mutation",
            "no_entity_truth_write",
            "governance_lanes.production.explicit",
            "governance_lanes.development.explicit",
            "production_promotion.required_for_production_writes",
            "production_write_gates.import_classification_ai_localization_requires_promotion",
            "production_source_root_write_gates.clean_identity_required",
            "production_source_root_write_gates.backup_proof_required",
            "schema_setup_gates.identity_gates_required",
            "schema_setup_gates.no_schema_setup_when_identity_blocked",
            "artifact_boundary.public_reports_aggregate_only",
            "artifact_boundary.public_reports_path_redacted",
            "artifact_boundary.public_redaction_contract_passed",
            "artifact_boundary.private_ledgers_local_ignored",
            "phase_boundaries.future_mentions_are_non_authorizing",
            "validation.focused_tests_passed",
        ),
        code="production_development_required_proof_failed",
        message="Post-S2 production/development separation requires post-#122 launcher state, consolidated S2G routing, explicit lanes, no-mutation proof, redacted artifacts, and focused tests.",
    )
    _check_required_false_paths(
        summary,
        result,
        (
            "development_lane.production_db_as_fixture",
            "development_lane.production_storage_as_fixture",
            "development_lane.production_source_roots_as_fixture",
            "development_lane.production_private_ledgers_as_fixture",
            "artifact_boundary.private_ledgers_committed",
        ),
        code="production_development_forbidden_fixture_or_artifact",
        message="Develop branches must not use production state as casual fixtures, and private ledgers must not be committed.",
    )

    allowed_sources = _normalized_token_set(_get(summary, "development_lane.allowed_data_sources", []))
    required_sources = {"dev_or_test_db", "dev_or_test_storage", "fixtures_or_restored_snapshots"}
    missing_sources = sorted(required_sources - allowed_sources)
    result.details["development_allowed_data_sources"] = sorted(allowed_sources)
    if missing_sources:
        result.fail(
            "production_development_allowed_sources_incomplete",
            "Develop lane must explicitly allow dev/test DB, dev/test storage, and fixtures or restored snapshots.",
            path="development_lane.allowed_data_sources",
            expected=sorted(required_sources),
            actual=sorted(allowed_sources),
        )

    top_level_phase = _get(summary, "phase", None)
    if top_level_phase != "PD1-A-R1":
        result.fail(
            "production_development_phase_mismatch",
            "This governance summary must identify PD1-A-R1 as the top-level phase.",
            path="phase",
            expected="PD1-A-R1",
            actual=top_level_phase,
        )
    current_phase = _get(summary, "phase_boundaries.current_phase", None)
    if current_phase != "PD1-A-R1":
        result.fail(
            "production_development_current_phase_mismatch",
            "This governance summary must identify PD1-A-R1 as the current phase.",
            path="phase_boundaries.current_phase",
            expected="PD1-A-R1",
            actual=current_phase,
        )
    next_phase_raw = str(_get(summary, "phase_boundaries.next_recommended_phase", "")).strip()
    next_phase = re.sub(r"\s+", " ", next_phase_raw).casefold()
    expected_next_phase = "s2g: gpu / ai tagging execution foundation"
    if next_phase != expected_next_phase:
        result.fail(
            "production_development_next_phase_not_consolidated_s2g",
            "The immediate recommended next phase after PD1-A-R1 must be exactly the consolidated S2G phase.",
            path="phase_boundaries.next_recommended_phase",
            expected="S2G: GPU / AI Tagging Execution Foundation",
            actual=_get(summary, "phase_boundaries.next_recommended_phase", None),
        )

    forbidden_authorizations = (
        "phase_boundaries.authorizes_s3",
        "phase_boundaries.authorizes_provider_calls",
        "phase_boundaries.authorizes_pixiv_gallery_dl_saucenao_google",
        "phase_boundaries.authorizes_sourceconcept_r1r_r2",
        "phase_boundaries.authorizes_entity_bridge",
        "phase_boundaries.authorizes_confirmed_assignments",
        "phase_boundaries.authorizes_automatic_production_sync",
        "phase_boundaries.authorizes_s2g_execution",
        "phase_boundaries.authorizes_gpu_benchmark",
        "phase_boundaries.authorizes_desired_media_backfill",
    )
    _check_required_false_paths(
        summary,
        result,
        forbidden_authorizations,
        code="production_development_forbidden_current_phase_authorization",
        message="PD1-A may mention future work but must not authorize future execution phases or production automation.",
    )
    for path in ("safety.no_llm_calls", "safety_no_mutation_proof.no_llm_calls"):
        if _has(summary, path) and not _as_bool(_get(summary, path)):
            result.fail(
                "production_development_llm_calls_not_forbidden",
                "PD1-A-R1 must explicitly forbid LLM calls.",
                path=path,
                expected=True,
                actual=_get(summary, path),
            )
    for path in ("safety.no_media_tags_mutation", "safety_no_mutation_proof.no_media_tags_mutation"):
        if _has(summary, path) and not _as_bool(_get(summary, path)):
            result.fail(
                "production_development_media_tags_mutation_not_forbidden",
                "PD1-A-R1 must explicitly forbid media_tags and tag-truth mutation.",
                path=path,
                expected=True,
                actual=_get(summary, path),
            )

    requested_writes = _production_write_requested(summary)
    result.details["production_write_requests"] = requested_writes
    if requested_writes:
        for path in requested_writes:
            result.fail(
                "production_development_write_request_forbidden",
                "PD1-A-R1 is a docs/contract reconciliation phase; production write requests are forbidden outright.",
                path=path,
                expected=False,
                actual=True,
            )
        return

    source_root_write_requested = _as_bool(_get(summary, "write_requests.source_root_registration", False)) or _as_bool(
        _get(summary, "write_requests.source_root_replacement", False)
    )
    if source_root_write_requested:
        _check_required_boolean_paths(
            summary,
            result,
            (
                "production_identity.db_clean",
                "production_identity.storage_clean",
                "production_identity.source_roots_clean",
                "backup_proof.valid",
            ),
            code="production_source_root_write_gate_missing",
            message="Production source-root registration/replacement requires clean identity gates and valid backup proof.",
        )

    schema_setup_requested = _as_bool(_get(summary, "write_requests.schema_setup", False)) or _as_bool(
        _get(summary, "write_requests.schema_migration", False)
    )
    schema_setup_requested = schema_setup_requested or _as_bool(_get(summary, "schema_setup_gates.schema_setup_requested", False))
    schema_setup_requested = schema_setup_requested or _as_bool(_get(summary, "schema_setup_gates.schema_setup_ran", False))
    if schema_setup_requested:
        if _as_bool(_get(summary, "identity_gates.blocked", False)):
            result.fail(
                "production_schema_setup_identity_blocked",
                "Schema setup/migration paths must not run while env/storage/DB identity gates are blocked.",
                path="identity_gates.blocked",
                expected=False,
                actual=True,
            )
        _check_required_boolean_paths(
            summary,
            result,
            ("identity_gates.env_clean", "identity_gates.db_clean", "identity_gates.storage_clean"),
            code="production_schema_setup_identity_gate_missing",
            message="Schema setup/migration paths require clean env, DB, and storage identity gates.",
        )


def _check_prod_launcher_mvp(
    _contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult
) -> None:
    required_true = (
        "mainline_sync.latest_main_after_pr120_included",
        "mainline_sync.merge_base_origin_main_is_ancestor",
        "mainline_sync.pr119_contract_preserved",
        "mainline_sync.pr120_contract_preserved",
        "mainline_sync.prod_launcher_contract_preserved",
        "launcher_code.control_exists",
        "launcher_code.cli_control_exists",
        "launcher_code.visual_launcher_exists",
        "launcher_code.cmd_entry_exists",
        "start_command.production_mode",
        "start_command.no_debug",
        "preflight_gates.env",
        "preflight_gates.debug_disabled",
        "preflight_gates.storage_root",
        "preflight_gates.db",
        "preflight_gates.port",
        "preflight_gates.venv",
        "preflight_gates.worktree_dev_refusal",
        "preflight_gates.destructive_e2e_disabled",
        "preflight_gates.dangerous_dev_test_flags_disabled",
        "preflight_gates.malformed_app_port_failure",
        "preflight_gates.malformed_db_port_failure",
        "startup_write_policy.normal_startup_maintenance_documented",
        "startup_write_policy.launcher_safe_startup_mode_enabled",
        "startup_write_policy.schema_migration_blocked_by_launcher_safe_mode",
        "startup_write_policy.operator_intent_required_for_startup_maintenance",
        "stop_safety.refuses_unknown_process",
        "stop_safety.managed_identity_required",
        "stop_safety.refuses_unverified_stale_pid",
        "stop_safety.verifies_process_create_time",
        "stop_safety.platform_aware_create_time",
        "stop_safety.verifies_python_executable",
        "stop_safety.verifies_port_owner_when_available",
        "stop_safety.force_kill_same_verified_only",
        "start_safety.serialized",
        "start_safety.start_already_in_progress_status",
        "start_safety.atomic_state_writes",
        "start_safety.stale_lock_reclaim",
        "state_file.local_ignored",
        "public_json_safety.log_tail_redacted",
        "health_status.auth_exempt_for_launcher",
        "health_status.public_safe",
        "health_status.no_paths",
        "health_status.safe_fields_only",
        "health_status.schema_compatible_check",
        "health_status.read_only_schema_check",
        "reports.redacted",
        "tests.preflight_failure",
        "tests.port_occupied",
        "tests.stale_pid",
        "tests.managed_stop",
        "tests.unknown_process_refusal",
        "tests.health_auth_exempt",
        "tests.startup_write_policy",
        "tests.destructive_e2e_denial",
        "tests.unverified_pid_refusal",
        "tests.start_serialization",
        "tests.malformed_app_port",
        "tests.malformed_db_port",
        "tests.log_tail_public_json",
        "tests.stale_lock_reclaim",
        "tests.posix_process_verification",
        "tests.health_schema_compatibility",
        "validation.focused_tests_passed",
        "validation.contract_passed",
        "safety.no_import_tagging_localization_sync_jobs",
        "safety.no_provider_calls",
        "safety.no_sourceconcept_or_entity",
        "safety.no_db_migrations",
        "safety.no_destructive_operations",
        "safety.no_source_icloud_mutation",
    )
    _check_required_boolean_paths(
        summary,
        result,
        required_true,
        code="prod_launcher_required_proof_failed",
        message="Production launcher MVP requires code, visible controls, hard preflight, safe stop, focused tests, and no forbidden operations.",
    )

    command = _get(summary, "start_command.command", [])
    command_text = " ".join(str(item) for item in command) if isinstance(command, list) else str(command)
    command_lower = command_text.casefold()
    result.details["prod_launcher_command"] = command_text
    if "run.py" not in command_lower:
        result.fail(
            "prod_launcher_start_command_missing_run_py",
            "Production launcher must start the current runtime entry point run.py.",
            path="start_command.command",
            expected="python run.py",
            actual=command,
        )
    if "--debug" in command_lower or _as_bool(_get(summary, "start_command.debug", False)):
        result.fail(
            "prod_launcher_start_command_debug_enabled",
            "Production launcher start command must not pass --debug or enable debug mode.",
            path="start_command.command",
            expected="no --debug",
            actual=command,
        )

    state_path = str(_get(summary, "state_file.path", "")).replace("\\", "/").casefold()
    if not state_path.startswith(".local_manifests/production_launcher/"):
        result.fail(
            "prod_launcher_state_file_not_local_ignored",
            "Launcher state must live under the ignored .local_manifests/production_launcher path.",
            path="state_file.path",
            expected=".local_manifests/production_launcher/<state>.json",
            actual=_get(summary, "state_file.path", None),
        )

    forbidden_false = (
        "forbidden_operations.import_jobs",
        "forbidden_operations.tagging_jobs",
        "forbidden_operations.localization_jobs",
        "forbidden_operations.sync_jobs",
        "forbidden_operations.provider_calls",
        "forbidden_operations.sourceconcept",
        "forbidden_operations.entity_bridge",
        "forbidden_operations.db_migrations",
        "forbidden_operations.destructive_operations",
        "forbidden_operations.source_icloud_mutation",
        "startup_write_policy.schema_migration_allowed",
        "startup_write_policy.destructive_cleanup_allowed",
        "startup_write_policy.import_tagging_sync_jobs_allowed",
        "safety.destructive_e2e_allowed",
        "public_json_safety.log_tail_in_public_json",
    )
    _check_explicit_false_paths(
        summary,
        result,
        forbidden_false,
        code="prod_launcher_forbidden_operation_enabled",
        message="Launcher MVP summary must explicitly report forbidden operations as false.",
    )

    for payload_path in ("health_status.status_example", "diagnostics.status_json_example", "startup_write_policy", "public_json_payload"):
        payload = _get(summary, payload_path, MISSING)
        if payload is MISSING:
            continue
        if _payload_has_any_key(payload, {"recent_log_tail", "log_tail"}):
            result.fail(
                "prod_launcher_log_tail_public_json",
                "Production launcher public JSON must not include raw log tail fields.",
                path=payload_path,
                expected="no recent_log_tail or log_tail in public JSON",
            )
        findings = scan_public_payload(payload)
        if findings:
            result.fail(
                "prod_launcher_public_status_not_safe",
                "Production launcher health/status examples must be public-safe.",
                path=payload_path,
                expected="no secrets, local paths, filenames, or private provenance",
                actual=findings[:5],
            )
    health_example = _get(summary, "health_status.status_example", {})
    if isinstance(health_example, Mapping) and _as_bool(health_example.get("ok")):
        if not _as_bool(health_example.get("schema_compatible")):
            result.fail(
                "prod_launcher_health_ok_without_schema_compatible",
                "Health examples that report ok=true must also prove schema_compatible=true.",
                path="health_status.status_example.schema_compatible",
                expected=True,
                actual=health_example.get("schema_compatible"),
            )
    diagnostics_example = _get(summary, "diagnostics.status_json_example", {})
    if isinstance(diagnostics_example, Mapping) and _as_bool(diagnostics_example.get("health_ok")):
        if not _as_bool(diagnostics_example.get("schema_compatible")):
            result.fail(
                "prod_launcher_diagnostics_health_ok_without_schema_compatible",
                "Launcher diagnostics that report health_ok=true must also include schema_compatible=true.",
                path="diagnostics.status_json_example.schema_compatible",
                expected=True,
                actual=diagnostics_example.get("schema_compatible"),
            )


def _check_prod_launcher_ux1_production_profile(
    _contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult
) -> None:
    required_true = (
        "mainline_sync.pr119_merge_commit_included",
        "mainline_sync.pr120_merge_commit_included",
        "mainline_sync.pr121_merge_commit_included",
        "production_profile.local_ignored_path",
        "production_profile.separate_from_development_dotenv",
        "production_profile.child_env_from_profile",
        "production_profile.child_env_skips_dotenv",
        "production_profile.clean_allowlisted_process_environment",
        "production_profile.profile_mismatch_fails_closed",
        "production_profile.repair_resets_invariants",
        "production_profile.local_bootstrap_supported",
        "production_profile.auth_policy_explicit",
        "production_profile.child_env_sets_require_auth",
        "production_profile.create_repair_persists_inferred_values",
        "production_profile.partial_update_bootstraps_inferred_values",
        "production_profile.profile_overrides_development_dotenv_for_child",
        "production_profile.storage_root_not_invented",
        "production_profile.incomplete_profile_state_explicit",
        "electron_launcher.exists",
        "electron_launcher.primary_documented_entrypoint",
        "electron_launcher.windows_executable_packaging",
        "electron_launcher.zh_cn_primary_visible_ui",
        "electron_launcher.inferred_values_not_marked_saved_without_profile",
        "electron_launcher.copy_diagnostics_preserves_state",
        "electron_launcher.open_browser_preserves_state",
        "electron_launcher.db_access_value_clear_explicit",
        "electron_launcher.calls_python_control_plane",
        "electron_launcher.raw_json_hidden_from_main_screen",
        "electron_launcher.advanced_diagnostics_collapsed_by_default",
        "npm_proxy_setup.local_ignored_npmrc",
        "npm_proxy_setup.reset_supported",
        "preflight_mapping.violet_env_production",
        "preflight_mapping.storage_root_explicit",
        "preflight_mapping.production_storage_root_shape",
        "preflight_mapping.db_readonly_reachable",
        "preflight_mapping.no_startup_mutation_automation",
        "health_status.auth_exempt_for_launcher",
        "health_status.public_safe",
        "health_status.schema_required_columns_check",
        "start_safety.launched_pid_verified",
        "start_safety.health_identity_verified",
        "start_safety.managed_unhealthy_is_unhealthy",
        "start_safety.existing_managed_unhealthy_blocks_start",
        "start_safety.failed_start_verification_cleanup",
        "start_safety.failed_start_state_cleared",
        "start_safety.failed_start_pid_reverified_before_signal",
        "start_safety.failed_start_child_reaped",
        "start_safety.profile_identity_updates_blocked_while_running",
        "stop_safety.refuses_unknown_process",
        "stop_safety.posix_unknown_port_owner_fails_closed",
        "shutdown_safety.safe_startup_skips_background_tasks",
        "shutdown_safety.tracked_background_tasks_cancelled",
        "public_json_safety.profile_paths_redacted",
        "public_json_safety.production_profile_suffix_redacted",
        "public_json_safety.forward_slash_windows_paths_redacted",
        "public_json_safety.unc_paths_redacted",
        "reviewer_ledger.completed",
        "state_machine_audit.completed",
        "same_class_sweep.completed",
        "manual_acceptance_required_before_merge",
        "safety.no_import_tagging_localization_sync_jobs",
        "safety.no_provider_calls",
        "safety.no_sourceconcept_or_entity",
        "safety.no_db_migrations",
        "safety.no_destructive_operations",
        "safety.no_source_icloud_mutation",
    )
    _check_required_boolean_paths(
        summary,
        result,
        required_true,
        code="prod_launcher_ux1_required_proof_failed",
        message="UX1/PF1 requires separate production profile, Electron UI, mapped blockers, safety preservation, and pending manual acceptance.",
    )

    if _as_bool(_get(summary, "production_profile.development_dotenv_modified", True)):
        result.fail(
            "prod_launcher_ux1_development_dotenv_modified",
            "Production launcher repair must not modify the development .env.",
            path="production_profile.development_dotenv_modified",
            expected=False,
            actual=_get(summary, "production_profile.development_dotenv_modified", None),
        )
    if _as_bool(_get(summary, "manual_acceptance_completed", False)):
        result.fail(
            "prod_launcher_ux1_manual_acceptance_must_be_real",
            "Manual acceptance must remain false unless the user has completed real Electron validation from the canonical production checkout.",
            path="manual_acceptance_completed",
            expected=False,
            actual=True,
        )
    if _as_bool(_get(summary, "merge_allowed", False)):
        result.fail(
            "prod_launcher_ux1_merge_allowed_before_manual_acceptance",
            "Merge must remain blocked until real manual acceptance is complete.",
            path="merge_allowed",
            expected=False,
            actual=True,
        )
    if _as_bool(_get(summary, "pipeline_contract.claims.safe_to_merge", False)) or _as_bool(_get(summary, "pipeline_contract.safe_to_merge", False)):
        result.fail(
            "prod_launcher_ux1_safe_to_merge_claimed_before_manual_acceptance",
            "UX1/PF1 must not claim safe_to_merge before manual acceptance.",
            path="pipeline_contract.claims.safe_to_merge",
            expected=False,
            actual=True,
        )
    if _as_bool(_get(summary, "pipeline_contract.claims.target_met", False)):
        result.fail(
            "prod_launcher_ux1_target_met_claimed_before_manual_acceptance",
            "UX1/PF1 can be implementation-complete, but target_met is reserved for real user acceptance.",
            path="pipeline_contract.claims.target_met",
            expected=False,
            actual=True,
        )
    if str(_get(summary, "validation.python_tests_status", "")).strip().casefold() != "passed":
        result.fail(
            "prod_launcher_ux1_python_validation_not_passed",
            "UX1/PF1 executable contract requires Python validation to be recorded as passed.",
            path="validation.python_tests_status",
            expected="passed",
            actual=_get(summary, "validation.python_tests_status", None),
        )
    if str(_get(summary, "validation.electron_tests_status", "")).strip().casefold() != "passed":
        result.fail(
            "prod_launcher_ux1_electron_validation_not_passed",
            "UX1/PF1 executable contract requires Electron validation to be recorded as passed.",
            path="validation.electron_tests_status",
            expected="passed",
            actual=_get(summary, "validation.electron_tests_status", None),
        )

    checklist_groups = set(str(item) for item in (_get(summary, "electron_launcher.checklist_groups", []) or []))
    required_groups = {
        "Production Profile",
        "Environment",
        "Storage",
        "Database",
        "Schema",
        "Port",
        "Safety Flags",
        "Startup Policy",
    }
    missing_groups = sorted(required_groups - checklist_groups)
    if missing_groups:
        result.fail(
            "prod_launcher_ux1_missing_checklist_groups",
            "Electron launcher checklist must expose the required production preflight groups.",
            path="electron_launcher.checklist_groups",
            expected=sorted(required_groups),
            actual=sorted(checklist_groups),
        )

    forbidden_false = (
        "public_json_safety.log_tail_in_public_json",
        "forbidden_operations.import_jobs",
        "forbidden_operations.tagging_jobs",
        "forbidden_operations.localization_jobs",
        "forbidden_operations.sync_jobs",
        "forbidden_operations.provider_calls",
        "forbidden_operations.sourceconcept",
        "forbidden_operations.entity_bridge",
        "forbidden_operations.db_migrations",
        "forbidden_operations.destructive_operations",
        "forbidden_operations.source_icloud_mutation",
    )
    _check_explicit_false_paths(
        summary,
        result,
        forbidden_false,
        code="prod_launcher_ux1_forbidden_operation_enabled",
        message="UX1/PF1 summary must explicitly report public log tail and forbidden operations as false.",
    )

    for payload_path in ("health_status.status_example", "diagnostics.status_json_example", "public_json_payload"):
        payload = _get(summary, payload_path, MISSING)
        if payload is MISSING:
            continue
        if _payload_has_any_key(payload, {"recent_log_tail", "log_tail"}):
            result.fail(
                "prod_launcher_ux1_log_tail_public_json",
                "Production launcher public JSON must not include raw log tail fields.",
                path=payload_path,
                expected="no recent_log_tail or log_tail in public JSON",
            )
        findings = scan_public_payload(payload)
        if findings:
            result.fail(
                "prod_launcher_ux1_public_payload_not_safe",
                "UX1/PF1 public examples must not leak local paths, filenames, secrets, or private provenance.",
                path=payload_path,
                actual=findings[:5],
            )


def _payload_has_any_key(payload: Any, keys: set[str]) -> bool:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in keys:
                return True
            if _payload_has_any_key(value, keys):
                return True
    if isinstance(payload, list):
        return any(_payload_has_any_key(item, keys) for item in payload)
    return False


def _check_dynamic_library_sync(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    tables = _get(summary, "dynamic_sync.schema.tables", [])
    required_tables = {
        "blombooru_dynamic_source_roots",
        "blombooru_dynamic_source_items",
        "blombooru_dynamic_sync_runs",
        "blombooru_dynamic_sync_run_items",
    }
    if not isinstance(tables, list) or not required_tables.issubset({str(item) for item in tables}):
        result.fail(
            "dynamic_sync_missing_schema_tables",
            "Dynamic sync foundation requires all durable source/sync state tables.",
            path="dynamic_sync.schema.tables",
            expected=sorted(required_tables),
            actual=tables,
        )

    identity = str(_get(summary, "dynamic_sync.identity.source_item_identity", "")).casefold()
    missing_identity_components = [
        component
        for component in ("source_root_id", "relative_path_hash")
        if component not in identity
    ]
    if missing_identity_components:
        result.fail(
            "dynamic_sync_missing_source_identity_components",
            "Incremental sync identity must explicitly declare source_root_id and relative_path_hash.",
            path="dynamic_sync.identity.source_item_identity",
            expected="source_root_id + relative_path_hash",
            actual=_get(summary, "dynamic_sync.identity.source_item_identity", None),
        )

    if _as_bool(_get(summary, "dynamic_sync.default_off_policy.auto_sync_enabled", False)):
        result.fail(
            "dynamic_sync_auto_writes_enabled",
            "Unattended automatic production writes must remain disabled by default in S1.",
            path="dynamic_sync.default_off_policy.auto_sync_enabled",
            expected=False,
            actual=True,
        )
    if _as_bool(_get(summary, "dynamic_sync.default_off_policy.manual_sync_enabled", False)):
        result.fail(
            "dynamic_sync_manual_execution_enabled_without_s2",
            "Manual pending sync execution must remain disabled by default in S1.",
            path="dynamic_sync.default_off_policy.manual_sync_enabled",
            expected=False,
            actual=True,
        )
    if _as_int(_get(summary, "dynamic_sync.threshold.default", 0)) != 100:
        result.fail(
            "dynamic_sync_threshold_not_default_100",
            "Dynamic sync threshold default must be 100.",
            path="dynamic_sync.threshold.default",
            expected=100,
            actual=_get(summary, "dynamic_sync.threshold.default", None),
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "dynamic_sync.pending_counts.visible",
            "dynamic_sync.dry_run_no_import",
            "dynamic_sync.source_root_safety.passed",
            "ai_localization.chain_verified",
            "ai_localization.ai_tagging_auto_localization_default_enabled",
            "proper_noun_safeguards.preserved",
            "proper_noun_safeguards.worker_excludes_proper_nouns",
            "proper_noun_safeguards.unreviewed_llm_aliases_excluded_from_search",
            "validation.focused_tests_passed",
        ),
        code="dynamic_sync_required_proof_failed",
        message="Dynamic sync S1 requires visible pending counts, no-import dry run, source safety, AI/localization readiness, proper-noun safeguards, and focused tests.",
    )

    browser_status = str(_get(summary, "validation.browser_validation.status", "")).casefold()
    if (
        result.target_met_claimed
        or result.route_approved
        or result.full_chain_complete_claimed
        or result.safe_to_merge_claimed
    ) and browser_status != "passed":
        result.fail(
            "dynamic_sync_browser_validation_not_passed",
            "Dynamic sync S1 completion claims require validation.browser_validation.status to be passed.",
            path="validation.browser_validation.status",
            expected="passed",
            actual=_get(summary, "validation.browser_validation.status", None),
        )

    forbidden_true_paths = (
        "safety.full_production_import",
        "safety.production_db_import",
        "safety.full_ai_tagging_run",
        "safety.full_llm_localization_batch",
        "safety.provider_calls",
        "safety.sourceconcept_or_entity",
        "safety.source_icloud_mutation",
        "safety.destructive_cleanup",
    )
    for path in forbidden_true_paths:
        if _as_bool(_get(summary, path, False)):
            result.fail("dynamic_sync_forbidden_execution", "S1 summary reports a forbidden execution path.", path=path, expected=False, actual=True)


def _current_git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=CONTRACT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _changed_paths_between(old_ref: str, new_ref: str, paths: Sequence[str]) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", f"{old_ref}..{new_ref}", "--", *paths],
        cwd=CONTRACT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ["[invalid-head-evidence]"]
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _check_s2g1x_head_evidence(summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    evidence_head = str(_get(summary, "head_evidence.report_generation_head_sha", "") or "").strip()
    if not evidence_head:
        result.fail(
            "s2g1x_head_evidence_missing",
            "S2G-1X summaries must record the probe/report-generation head evidence.",
            path="head_evidence.report_generation_head_sha",
            expected="non-empty git ref",
            actual=evidence_head,
        )
        return
    current_head = _current_git_head()
    if not current_head or evidence_head == current_head:
        return
    changed_paths = _changed_paths_between(evidence_head, current_head, S2G1X_PROBE_EVIDENCE_CODE_PATHS)
    result.details["s2g1x_probe_code_paths_changed_after_evidence"] = changed_paths
    if changed_paths:
        result.fail(
            "s2g1x_probe_evidence_stale_for_current_code",
            "S2G-1X probe/report evidence was generated before later probe, shared scaffold, or contract code changes.",
            path="head_evidence.report_generation_head_sha",
            expected=f"no changes under {list(S2G1X_PROBE_EVIDENCE_CODE_PATHS)} after evidence head",
            actual={"evidence_head": evidence_head, "current_head": current_head, "changed_paths": changed_paths},
        )


def _read_s2g1x_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    raw_path = _get(summary, "public_reports.markdown_report_path", MISSING)
    if raw_path is MISSING or not str(raw_path).strip():
        result.fail(
            "s2g1x_markdown_report_path_missing",
            "S2G-1X summaries must name the public Markdown report path for contract redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="repo-relative docs/reports/*.md path",
            actual=None,
        )
        return ""
    path_text = str(raw_path).replace("\\", "/")
    candidate = Path(path_text)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts or not path_text.startswith("docs/reports/") or candidate.suffix != ".md":
        result.fail(
            "s2g1x_markdown_report_path_unsafe",
            "S2G-1X Markdown report path must be a safe repo-relative docs/reports/*.md path.",
            path="public_reports.markdown_report_path",
            expected="repo-relative docs/reports/*.md path",
            actual="[redacted-path]" if "\\" in path_text or "/" in path_text else path_text,
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s2g1x_markdown_report_path_escape",
            "S2G-1X Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s2g1x_markdown_report_missing",
            "S2G-1X Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s2g1x_markdown_report_unreadable",
            "S2G-1X Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _check_s2g1x_probe(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {"target_met", "evidence_collected", "blocked_model_unavailable", "blocked_probe_unavailable"}
    status = str(result.status or "").casefold()
    completion_claimed = status == "target_met" or _completion_or_approval_claimed(result)
    _check_s2g1x_head_evidence(summary, result)
    if status not in allowed_statuses:
        result.fail(
            "s2g1x_unknown_status",
            "S2G-1X status must be an explicit probe status.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )

    if status != "target_met" and _completion_or_approval_claimed(result):
        result.fail(
            "s2g1x_non_completion_status_claimed_completion",
            "S2G-1X blocked or evidence-only statuses must not claim target_met, route approval, full-chain completion, or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met for completion claims",
            actual=result.status,
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "capability_probe.completed",
            "capability_probe.safe_probe.no_db_connection",
            "capability_probe.safe_probe.no_production_db_writes",
            "capability_probe.safe_probe.no_media_tags_writes",
            "capability_probe.safe_probe.no_full_library_ai_tagging",
            "capability_probe.safe_probe.no_model_download",
            "capability_probe.safe_probe.local_files_only",
            "s3a_dev_dry_run_plan.dry_run_only",
            "s2g_s3a_decision.should_share_job_progress_throttle_ledger_architecture",
            "s2g_s3a_decision.gpu_load_control_before_s3a_production_execution",
            "public_redaction.passed",
        ),
        code="s2g1x_required_probe_proof_missing",
        message="S2G-1X requires a completed safe local probe, CPU fallback evidence, shared-architecture decision, and public redaction proof.",
    )

    _check_required_false_paths(
        summary,
        result,
        (
            "capability_probe.model_identity.model_download_performed",
            "s3a_dev_dry_run_plan.production_execution_enabled",
            "s3a_dev_dry_run_plan.unattended_enabled",
            "s2g_s3a_decision.should_combine_current_production_execution",
            "s2g_s3a_decision.production_s3a_execution_enabled",
            "s2g_s3a_decision.unattended_s3b_enabled",
        ),
        code="s2g1x_forbidden_execution_or_mutation",
        message="S2G-1X must not enable production execution, unattended sync, providers, SourceConcept/Entity, destructive actions, or model downloads.",
    )

    _check_explicit_false_paths(
        summary,
        result,
        (
            "safety.production_db_writes",
            "safety.production_import",
            "safety.production_classification",
            "safety.production_ai_tagging",
            "safety.production_localization",
            "safety.production_s3a_execution_enabled",
            "safety.unattended_auto_sync_enabled",
            "safety.provider_pixiv_gallery_dl_saucenao_google_calls",
            "safety.sourceconcept_or_entity",
            "safety.confirmed_entity_assignments",
            "safety.source_icloud_mutation",
            "safety.cleanup_delete_reset_drop_truncate",
            "safety.model_download",
        ),
        code="s2g1x_required_safety_false_missing_or_true",
        message="S2G-1X safety proofs must explicitly set every forbidden operation flag to false.",
    )

    markdown_text = _read_s2g1x_markdown_report(summary, result)
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s2g1x_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s2g1x_public_payload_redaction_failed",
            "S2G-1X contract independently found forbidden public JSON or Markdown content; public_redaction.passed cannot be trusted alone.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )

    if completion_claimed:
        _check_required_boolean_paths(
            summary,
            result,
            (
                "capability_probe.model_identity.model_file_cached",
                "capability_probe.model_identity.label_file_cached",
                "capability_probe.provider_matrix.cpu.available",
                "capability_probe.provider_matrix.cpu.loaded",
                "capability_probe.provider_matrix.cpu.practical",
            ),
            code="s2g1x_completion_model_load_evidence_missing",
            message="S2G-1X completion claims require cached model and labels plus actual CPU model-load evidence.",
        )
        if _as_bool(_get(summary, "capability_probe.model_identity.network_download_required", False)):
            result.fail(
                "s2g1x_completion_requires_network_model_download",
                "S2G-1X completion claims require a locally cached model and must not require a network download.",
                path="capability_probe.model_identity.network_download_required",
                expected=False,
                actual=True,
            )
        benchmark_status = str(_get(summary, "capability_probe.provider_matrix.cpu.benchmark_status", "")).casefold()
        if benchmark_status != "completed":
            result.fail(
                "s2g1x_completion_cpu_benchmark_not_completed",
                "S2G-1X completion claims require the CPU provider benchmark to complete.",
                path="capability_probe.provider_matrix.cpu.benchmark_status",
                expected="completed",
                actual=_get(summary, "capability_probe.provider_matrix.cpu.benchmark_status", None),
            )
        throughput = _get(summary, "capability_probe.provider_matrix.cpu.throughput_items_per_second", MISSING)
        if throughput is MISSING or _as_float(throughput, 0.0) <= 0:
            result.fail(
                "s2g1x_completion_cpu_throughput_missing",
                "S2G-1X completion claims require positive CPU throughput evidence.",
                path="capability_probe.provider_matrix.cpu.throughput_items_per_second",
                expected="> 0",
                actual=None if throughput is MISSING else throughput,
            )
        for provider_key_name in ("cuda", "directml", "cpu"):
            row = _get(summary, f"capability_probe.provider_matrix.{provider_key_name}", MISSING)
            status_value = _get(summary, f"capability_probe.provider_matrix.{provider_key_name}.benchmark_status", MISSING)
            if not isinstance(row, Mapping) or status_value is MISSING:
                result.fail(
                    "s2g1x_completion_provider_check_missing",
                    "S2G-1X completion claims require explicit CUDA, DirectML, and CPU provider checks.",
                    path=f"capability_probe.provider_matrix.{provider_key_name}",
                    expected="provider row with benchmark_status",
                    actual=None if row is MISSING else row,
                )
                continue
            if str(status_value).casefold() == "not_requested":
                result.fail(
                    "s2g1x_completion_provider_not_checked",
                    "S2G-1X completion claims require CUDA, DirectML, and CPU providers to be explicitly checked, even when unavailable.",
                    path=f"capability_probe.provider_matrix.{provider_key_name}.benchmark_status",
                    expected="checked provider status",
                    actual=status_value,
                )

    sample_count = _as_int(_get(summary, "capability_probe.safe_probe.sample_count", 0))
    if sample_count < 1 or sample_count > 16:
        result.fail(
            "s2g1x_unbounded_sample_count",
            "The AI tagging probe must use a tiny bounded sample.",
            path="capability_probe.safe_probe.sample_count",
            expected="1..16",
            actual=sample_count,
        )

    model_name = str(_get(summary, "capability_probe.model_identity.model_name", ""))
    if not model_name.startswith("wd-"):
        result.fail(
            "s2g1x_model_identity_not_wd",
            "The probe must record a WD tagger model identity.",
            path="capability_probe.model_identity.model_name",
            expected="wd-*",
            actual=model_name,
        )

    forced_provider = str(_get(summary, "capability_probe.current_app_backend.forced_provider", ""))
    if forced_provider != "CPUExecutionProvider":
        result.warn(
            "s2g1x_current_app_provider_not_cpu",
            "Current app provider is not the expected hardcoded CPU provider; verify whether runtime code changed.",
            path="capability_probe.current_app_backend.forced_provider",
            expected="CPUExecutionProvider",
            actual=forced_provider,
        )

    batch_size = _as_int(_get(summary, "load_control.recommended_config.batch_size", 0))
    worker_count = _as_int(_get(summary, "load_control.recommended_config.worker_count", 0))
    concurrent_jobs = _as_int(_get(summary, "load_control.recommended_config.max_concurrent_jobs", 0))
    if batch_size < 1 or batch_size > 16:
        result.fail(
            "s2g1x_load_control_batch_unbounded",
            "S2G-1X load-control batch size must be bounded.",
            path="load_control.recommended_config.batch_size",
            expected="1..16",
            actual=batch_size,
        )
    if worker_count != 1 or concurrent_jobs != 1:
        result.fail(
            "s2g1x_parallel_execution_enabled_too_early",
            "S2G-1X must keep worker count and concurrent jobs at one until load control is implemented.",
            path="load_control.recommended_config",
            expected={"worker_count": 1, "max_concurrent_jobs": 1},
            actual={"worker_count": worker_count, "max_concurrent_jobs": concurrent_jobs},
        )

    plan_stages = _get(summary, "s3a_dev_dry_run_plan.stages", [])
    if not isinstance(plan_stages, list) or not plan_stages:
        result.fail(
            "s2g1x_s3a_plan_stages_missing",
            "S2G-1X must include a dry-run-only S3A stage skeleton.",
            path="s3a_dev_dry_run_plan.stages",
            expected="non-empty list",
            actual=plan_stages,
        )
    elif any(_as_bool(stage.get("writes_enabled", False)) for stage in plan_stages if isinstance(stage, Mapping)):
        result.fail(
            "s2g1x_s3a_plan_write_stage_enabled",
            "The current S3A scaffold must not enable write stages.",
            path="s3a_dev_dry_run_plan.stages",
            expected="all writes_enabled=false",
        )


def _read_s2g_s3a_f1_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    path_text = _get(summary, "public_reports.markdown_report_path", MISSING)
    if path_text is MISSING or not str(path_text).strip():
        result.fail(
            "s2g_s3a_f1_markdown_report_path_missing",
            "S2G/S3A-F1 summaries must name the public Markdown report path.",
            path="public_reports.markdown_report_path",
        )
        return ""
    candidate = Path(str(path_text))
    if candidate.is_absolute():
        result.fail(
            "s2g_s3a_f1_markdown_report_path_unsafe",
            "S2G/S3A-F1 Markdown report path must be repo-relative.",
            path="public_reports.markdown_report_path",
            expected="repo-relative path",
            actual="[redacted-path]",
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s2g_s3a_f1_markdown_report_path_escape",
            "S2G/S3A-F1 Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s2g_s3a_f1_markdown_report_missing",
            "S2G/S3A-F1 Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s2g_s3a_f1_markdown_report_unreadable",
            "S2G/S3A-F1 Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _read_s2g_real1_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    path_text = _get(summary, "public_reports.markdown_report_path", MISSING)
    if path_text is MISSING or not str(path_text).strip():
        result.fail(
            "s2g_real1_markdown_report_path_missing",
            "S2G-REAL1 summaries must name the public Markdown report path.",
            path="public_reports.markdown_report_path",
        )
        return ""
    candidate = Path(str(path_text))
    if candidate.is_absolute():
        result.fail(
            "s2g_real1_markdown_report_path_unsafe",
            "S2G-REAL1 Markdown report path must be repo-relative.",
            path="public_reports.markdown_report_path",
            expected="repo-relative path",
            actual="[redacted-path]",
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s2g_real1_markdown_report_path_escape",
            "S2G-REAL1 Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s2g_real1_markdown_report_missing",
            "S2G-REAL1 Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s2g_real1_markdown_report_unreadable",
            "S2G-REAL1 Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _read_s2g_m1_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    path_text = _get(summary, "public_reports.markdown_report_path", MISSING)
    if path_text is MISSING or not str(path_text).strip():
        result.fail(
            "s2g_m1_markdown_report_path_missing",
            "S2G-M1 summaries must name the public Markdown report path.",
            path="public_reports.markdown_report_path",
        )
        return ""
    candidate = Path(str(path_text))
    if candidate.is_absolute():
        result.fail(
            "s2g_m1_markdown_report_path_unsafe",
            "S2G-M1 Markdown report path must be repo-relative.",
            path="public_reports.markdown_report_path",
            expected="repo-relative path",
            actual="[redacted-path]",
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s2g_m1_markdown_report_path_escape",
            "S2G-M1 Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s2g_m1_markdown_report_missing",
            "S2G-M1 Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s2g_m1_markdown_report_unreadable",
            "S2G-M1 Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _read_s3a_m1_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    path_text = _get(summary, "public_reports.markdown_report_path", MISSING)
    if path_text is MISSING or not str(path_text).strip():
        result.fail(
            "s3a_m1_markdown_report_path_missing",
            "S3A-M1 summaries must name the public Markdown report path.",
            path="public_reports.markdown_report_path",
        )
        return ""
    candidate = Path(str(path_text))
    if candidate.is_absolute():
        result.fail(
            "s3a_m1_markdown_report_path_unsafe",
            "S3A-M1 Markdown report path must be repo-relative.",
            path="public_reports.markdown_report_path",
            expected="repo-relative path",
            actual="[redacted-path]",
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s3a_m1_markdown_report_path_escape",
            "S3A-M1 Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s3a_m1_markdown_report_missing",
            "S3A-M1 Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s3a_m1_markdown_report_unreadable",
            "S3A-M1 Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _read_s3a_pilot1_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    path_text = _get(summary, "public_reports.markdown_report_path", MISSING)
    if path_text is MISSING or not str(path_text).strip():
        result.fail(
            "s3a_pilot1_markdown_report_path_missing",
            "S3A-PILOT1 summaries must name the public Markdown report path.",
            path="public_reports.markdown_report_path",
        )
        return ""
    candidate = Path(str(path_text))
    if candidate.is_absolute():
        result.fail(
            "s3a_pilot1_markdown_report_path_unsafe",
            "S3A-PILOT1 Markdown report path must be repo-relative.",
            path="public_reports.markdown_report_path",
            expected="repo-relative path",
            actual="[redacted-path]",
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s3a_pilot1_markdown_report_path_escape",
            "S3A-PILOT1 Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s3a_pilot1_markdown_report_missing",
            "S3A-PILOT1 Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s3a_pilot1_markdown_report_unreadable",
            "S3A-PILOT1 Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _read_s3a_prod1_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    path_text = _get(summary, "public_reports.markdown_report_path", MISSING)
    if path_text is MISSING or not str(path_text).strip():
        result.fail(
            "s3a_prod1_markdown_report_path_missing",
            "S3A-PROD1 summaries must name the public Markdown report path.",
            path="public_reports.markdown_report_path",
        )
        return ""
    candidate = Path(str(path_text))
    if candidate.is_absolute():
        result.fail(
            "s3a_prod1_markdown_report_path_unsafe",
            "S3A-PROD1 Markdown report path must be repo-relative.",
            path="public_reports.markdown_report_path",
            expected="repo-relative path",
            actual="[redacted-path]",
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s3a_prod1_markdown_report_path_escape",
            "S3A-PROD1 Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s3a_prod1_markdown_report_missing",
            "S3A-PROD1 Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s3a_prod1_markdown_report_unreadable",
            "S3A-PROD1 Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _read_s3a_prod2_markdown_report(summary: Mapping[str, Any], result: ContractCheckResult) -> str:
    path_text = _get(summary, "public_reports.markdown_report_path", MISSING)
    if path_text is MISSING or not str(path_text).strip():
        result.fail(
            "s3a_prod2_markdown_report_path_missing",
            "S3A-PROD2/S3B-D1 summaries must name the public Markdown report path.",
            path="public_reports.markdown_report_path",
        )
        return ""
    candidate = Path(str(path_text))
    if candidate.is_absolute():
        result.fail(
            "s3a_prod2_markdown_report_path_unsafe",
            "S3A-PROD2/S3B-D1 Markdown report path must be repo-relative.",
            path="public_reports.markdown_report_path",
            expected="repo-relative path",
            actual="[redacted-path]",
        )
        return ""
    root = CONTRACT_ROOT.resolve()
    resolved = (CONTRACT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        result.fail(
            "s3a_prod2_markdown_report_path_escape",
            "S3A-PROD2/S3B-D1 Markdown report path must stay inside the repository.",
            path="public_reports.markdown_report_path",
            expected="inside repository",
            actual="[redacted-path]",
        )
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.fail(
            "s3a_prod2_markdown_report_missing",
            "S3A-PROD2/S3B-D1 Markdown report path does not exist.",
            path="public_reports.markdown_report_path",
            expected="existing public report",
            actual=path_text,
        )
    except OSError as exc:
        result.fail(
            "s3a_prod2_markdown_report_unreadable",
            "S3A-PROD2/S3B-D1 Markdown report could not be read for redaction scanning.",
            path="public_reports.markdown_report_path",
            expected="readable public report",
            actual=exc.__class__.__name__,
        )
    return ""


def _check_s2g_s3a_f1_foundation(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {"target_met", "foundation_ready", "blocked_model_unavailable", "blocked_provider_unavailable"}
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s2g_s3a_f1_unknown_status",
            "S2G/S3A-F1 status must be an explicit foundation status.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status != "target_met" and _completion_or_approval_claimed(result):
        result.fail(
            "s2g_s3a_f1_non_completion_status_claimed_completion",
            "Blocked or foundation-only statuses must not claim target_met or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met for completion claims",
            actual=result.status,
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "wd_tagger.provider_abstraction.implemented",
            "wd_tagger.provider_abstraction.hardcoded_cpu_provider_removed",
            "public_redaction.passed",
        ),
        code="s2g_s3a_f1_required_foundation_proof_missing",
        message="S2G/S3A-F1 requires provider abstraction, hardcoded CPU removal, and public redaction proof.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "safety.production_db_writes",
            "safety.production_import",
            "safety.production_classification",
            "safety.production_ai_tagging",
            "safety.production_localization",
            "safety.production_s3a_execution_enabled",
            "safety.unattended_auto_sync_enabled",
            "safety.provider_pixiv_gallery_dl_saucenao_google_calls",
            "safety.sourceconcept_or_entity",
            "safety.confirmed_entity_assignments",
            "safety.source_icloud_mutation",
            "safety.cleanup_delete_reset_drop_truncate",
            "safety.model_download",
            "safety.db_schema_change",
        ),
        code="s2g_s3a_f1_required_safety_false_missing_or_true",
        message="S2G/S3A-F1 safety proofs must explicitly set forbidden operation flags to false.",
    )
    _check_required_false_paths(
        summary,
        result,
        (
            "s3a_dry_run_plan.production_execution_enabled",
            "s3a_dry_run_plan.unattended_enabled",
        ),
        code="s2g_s3a_f1_forbidden_execution_enabled",
        message="S2G/S3A-F1 must keep S3A production execution and unattended S3B disabled.",
    )

    requested = _get(summary, "wd_tagger.provider_abstraction.requested_provider_preference", [])
    actual_provider = _get(summary, "wd_tagger.provider_abstraction.actual_provider", None)
    available = _get(summary, "wd_tagger.provider_abstraction.available_onnx_providers", [])
    if not isinstance(requested, list) or not requested:
        result.fail(
            "s2g_s3a_f1_provider_preference_missing",
            "WDTagger provider preference must be recorded as a non-empty list.",
            path="wd_tagger.provider_abstraction.requested_provider_preference",
            expected="non-empty list",
            actual=requested,
        )
    if not isinstance(actual_provider, str) or not actual_provider.strip():
        result.fail(
            "s2g_s3a_f1_actual_provider_missing",
            "WDTagger actual loaded ONNX provider must be reported.",
            path="wd_tagger.provider_abstraction.actual_provider",
            expected="non-empty provider name",
            actual=actual_provider,
        )
    elif isinstance(available, list) and available and actual_provider not in available:
        result.fail(
            "s2g_s3a_f1_actual_provider_not_available",
            "WDTagger actual provider must be one of the reported ONNX Runtime available providers.",
            path="wd_tagger.provider_abstraction.actual_provider",
            expected=available,
            actual=actual_provider,
        )

    fallback_occurred = _as_bool(_get(summary, "wd_tagger.provider_abstraction.fallback_occurred", False))
    fallback_reason = _get(summary, "wd_tagger.provider_abstraction.fallback_reason", None)
    if fallback_occurred and not str(fallback_reason or "").strip():
        result.fail(
            "s2g_s3a_f1_fallback_reason_missing",
            "Provider fallback must include a truthful fallback reason.",
            path="wd_tagger.provider_abstraction.fallback_reason",
            expected="non-empty fallback reason when fallback_occurred=true",
            actual=fallback_reason,
        )
    if not fallback_occurred and str(fallback_reason or "").strip():
        result.fail(
            "s2g_s3a_f1_fallback_reason_present_without_fallback",
            "Provider fallback reason must not be reported when the selected provider was the first usable requested provider.",
            path="wd_tagger.provider_abstraction.fallback_reason",
            expected="empty when fallback_occurred=false",
            actual=fallback_reason,
        )

    caps = {
        "batch_size": _as_int(_get(summary, "wd_tagger.load_control.batch_size", 0)),
        "configured_batch_size": _as_int(_get(summary, "wd_tagger.load_control.configured_batch_size", 0)),
        "effective_batch_size": _as_int(_get(summary, "wd_tagger.load_control.effective_batch_size", 0)),
        "cpu_intra_op_threads": _as_int(_get(summary, "wd_tagger.load_control.cpu_intra_op_threads", 0)),
        "cpu_inter_op_threads": _as_int(_get(summary, "wd_tagger.load_control.cpu_inter_op_threads", 0)),
        "preprocess_workers": _as_int(_get(summary, "wd_tagger.load_control.preprocess_workers", 0)),
    }
    batch_cap_source = str(_get(summary, "wd_tagger.load_control.batch.batch_cap_source", "") or "").strip()
    if caps["configured_batch_size"] < 1:
        result.fail(
            "s2g_s3a_f1_configured_batch_size_missing",
            "Configured batch size must be reported separately from effective runtime batch size.",
            path="wd_tagger.load_control.configured_batch_size",
            expected="positive integer",
            actual=caps["configured_batch_size"],
        )
    if caps["effective_batch_size"] != caps["batch_size"]:
        result.fail(
            "s2g_s3a_f1_effective_batch_size_mismatch",
            "Reported batch_size must represent the effective runtime batch size.",
            path="wd_tagger.load_control.effective_batch_size",
            expected=caps["batch_size"],
            actual=caps["effective_batch_size"],
        )
    if not (1 <= caps["effective_batch_size"] <= 16):
        result.fail(
            "s2g_s3a_f1_effective_batch_size_unbounded",
            "Effective AI tagging batch size must be bounded after config/env parsing.",
            path="wd_tagger.load_control.effective_batch_size",
            expected="1..16",
            actual=caps["effective_batch_size"],
        )
    if not batch_cap_source:
        result.fail(
            "s2g_s3a_f1_batch_cap_source_missing",
            "Batch provenance must report which cap determined the effective batch size.",
            path="wd_tagger.load_control.batch.batch_cap_source",
            expected="non-empty cap source",
            actual=batch_cap_source,
        )
    if not (1 <= caps["batch_size"] <= 16):
        result.fail("s2g_s3a_f1_batch_size_unbounded", "AI tagging batch size must be bounded.", path="wd_tagger.load_control.batch_size", expected="1..16", actual=caps["batch_size"])
    if not (1 <= caps["cpu_intra_op_threads"] <= 4):
        result.fail("s2g_s3a_f1_cpu_intra_threads_unbounded", "CPU intra-op threads must be capped for this phase.", path="wd_tagger.load_control.cpu_intra_op_threads", expected="1..4", actual=caps["cpu_intra_op_threads"])
    if caps["cpu_inter_op_threads"] != 1:
        result.fail("s2g_s3a_f1_cpu_inter_threads_unbounded", "CPU inter-op threads must remain one for this phase.", path="wd_tagger.load_control.cpu_inter_op_threads", expected=1, actual=caps["cpu_inter_op_threads"])
    if not (1 <= caps["preprocess_workers"] <= 2):
        result.fail("s2g_s3a_f1_preprocess_workers_unbounded", "Preprocess workers must be capped for this phase.", path="wd_tagger.load_control.preprocess_workers", expected="1..2", actual=caps["preprocess_workers"])
    if str(_get(summary, "wd_tagger.load_control.execution_mode", "")).upper() != "ORT_SEQUENTIAL":
        result.fail(
            "s2g_s3a_f1_parallel_execution_enabled",
            "ORT execution mode must default to ORT_SEQUENTIAL in this foundation phase.",
            path="wd_tagger.load_control.execution_mode",
            expected="ORT_SEQUENTIAL",
            actual=_get(summary, "wd_tagger.load_control.execution_mode", None),
        )

    provenance_fields = set(_get(summary, "wd_tagger.provenance.fields_available", []) or [])
    required_provenance_fields = {
        "model_name",
        "model_repo_id",
        "thresholds",
        "requested_provider_preference",
        "actual_provider",
        "fallback_reason",
        "batch_size",
        "effective_batch_size",
        "configured_batch_size",
        "batch_cap_source",
        "cpu_thread_settings",
        "preprocess_workers",
        "execution_mode",
        "tagger_version_source",
    }
    missing_provenance = sorted(required_provenance_fields - provenance_fields)
    if missing_provenance:
        result.fail(
            "s2g_s3a_f1_provenance_fields_missing",
            "AI tagging provenance is missing required fields.",
            path="wd_tagger.provenance.fields_available",
            expected=sorted(required_provenance_fields),
            actual=sorted(provenance_fields),
        )

    if _as_bool(_get(summary, "wd_tagger.model.model_download_allowed", False)):
        result.fail(
            "s2g_s3a_f1_model_download_allowed",
            "The F1/G1 smoke summary must be cache-only by default and not allow model downloads.",
            path="wd_tagger.model.model_download_allowed",
            expected=False,
            actual=True,
        )
    if _as_bool(_get(summary, "wd_tagger.model.model_download_performed", False)):
        result.fail(
            "s2g_s3a_f1_model_download_performed",
            "The F1/G1 smoke summary must not perform a model download.",
            path="wd_tagger.model.model_download_performed",
            expected=False,
            actual=True,
        )

    gpu_attempted = _as_bool(_get(summary, "gpu_directml_enablement.attempted", False))
    gpu_success = _as_bool(_get(summary, "gpu_directml_enablement.success", False))
    gpu_blocker = str(_get(summary, "gpu_directml_enablement.blocker", "") or "").strip()
    providers_after_attempt = _get(summary, "gpu_directml_enablement.available_onnx_providers_after_attempt", [])
    actual_gpu_provider = _get(summary, "gpu_directml_enablement.actual_gpu_provider_loaded", None)
    if not gpu_attempted:
        result.fail(
            "s2g_s3a_f1_gpu_enablement_not_attempted",
            "F1+G1 must include a DirectML/CUDA enablement attempt or explicit provider blocker.",
            path="gpu_directml_enablement.attempted",
            expected=True,
            actual=False,
        )
    if not isinstance(providers_after_attempt, list) or not providers_after_attempt:
        result.fail(
            "s2g_s3a_f1_gpu_attempt_provider_list_missing",
            "GPU enablement summary must report actual ONNX Runtime providers after the attempt.",
            path="gpu_directml_enablement.available_onnx_providers_after_attempt",
            expected="non-empty list",
            actual=providers_after_attempt,
        )
    if gpu_success:
        if actual_gpu_provider not in {"DmlExecutionProvider", "CUDAExecutionProvider"}:
            result.fail(
                "s2g_s3a_f1_gpu_success_without_gpu_provider",
                "GPU success cannot be claimed unless DirectML or CUDA was actually loaded.",
                path="gpu_directml_enablement.actual_gpu_provider_loaded",
                expected=["DmlExecutionProvider", "CUDAExecutionProvider"],
                actual=actual_gpu_provider,
            )
        if isinstance(providers_after_attempt, list) and actual_gpu_provider not in providers_after_attempt:
            result.fail(
                "s2g_s3a_f1_gpu_success_provider_not_available",
                "Claimed GPU provider must be present in ONNX Runtime available providers after the attempt.",
                path="gpu_directml_enablement.available_onnx_providers_after_attempt",
                expected=actual_gpu_provider,
                actual=providers_after_attempt,
            )
    elif not gpu_blocker:
        result.fail(
            "s2g_s3a_f1_gpu_unavailable_blocker_missing",
            "If GPU/DirectML is unavailable, the summary must report an explicit blocker.",
            path="gpu_directml_enablement.blocker",
            expected="non-empty blocker when success=false",
            actual=gpu_blocker,
        )

    cpu_benchmark_status = str(_get(summary, "benchmarks.cpu.status", "") or "").strip()
    if status == "target_met" and cpu_benchmark_status != "completed":
        result.fail(
            "s2g_s3a_f1_cpu_benchmark_not_completed",
            "Target-met F1+G1 summaries must include a completed bounded CPU benchmark.",
            path="benchmarks.cpu.status",
            expected="completed",
            actual=cpu_benchmark_status,
        )

    concepts = set(_get(summary, "shared_foundation.concepts", []) or [])
    required_concepts = {"LoadControlConfig", "ProviderCapability", "JobRun", "StageRun", "ProgressSnapshot", "ProviderProvenance"}
    missing_concepts = sorted(required_concepts - concepts)
    if missing_concepts:
        result.fail(
            "s2g_s3a_f1_shared_concepts_missing",
            "Shared foundation must expose the required job/progress/provider vocabulary.",
            path="shared_foundation.concepts",
            expected=sorted(required_concepts),
            actual=sorted(concepts),
        )

    stage_rows = _get(summary, "s3a_dry_run_plan.stages", [])
    stage_names = {
        str(stage.get("name"))
        for stage in stage_rows
        if isinstance(stage, Mapping)
    } if isinstance(stage_rows, list) else set()
    expected_stages = {"update_check", "hydration_read", "import_reuse", "classification", "ai_tagging", "localization", "summary"}
    missing_stages = sorted(expected_stages - stage_names)
    if missing_stages:
        result.fail(
            "s2g_s3a_f1_s3a_stages_missing",
            "S3A dry-run planning summary must include every future stage name.",
            path="s3a_dry_run_plan.stages",
            expected=sorted(expected_stages),
            actual=sorted(stage_names),
        )
    elif any(_as_bool(stage.get("writes_enabled", False)) for stage in stage_rows if isinstance(stage, Mapping)):
        result.fail(
            "s2g_s3a_f1_s3a_write_stage_enabled",
            "S3A dry-run planning stages must keep writes disabled.",
            path="s3a_dry_run_plan.stages",
            expected="all writes_enabled=false",
        )

    markdown_text = _read_s2g_s3a_f1_markdown_report(summary, result)
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s2g_s3a_f1_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s2g_s3a_f1_public_payload_redaction_failed",
            "S2G/S3A-F1 contract independently found forbidden public JSON or Markdown content.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )


def _check_s2g_real1_bounded_ai_tagging_validation(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "target_met_dry_run_only",
        "target_met_with_bounded_write",
        "blocked_no_media",
        "blocked_scope_invalid",
        "blocked_model_cache_missing",
        "blocked_model_download_allowed",
        "blocked_dry_run_not_completed",
        "blocked_dry_run_item_failures",
        "blocked_cpu_fallback_not_validated",
        "blocked_write_item_failures",
        "blocked_write_prerequisites_failed",
        "blocked_write_requested_without_exact_confirmation",
        "blocked_write_requested_not_completed",
    }
    status = str(result.status or "").casefold()
    target_statuses = {"target_met_dry_run_only", "target_met_with_bounded_write"}
    if status not in allowed_statuses:
        result.fail(
            "s2g_real1_unknown_status",
            "S2G-REAL1 status must be explicit about dry-run/write validation outcome.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status not in target_statuses and _completion_or_approval_claimed(result):
        result.fail(
            "s2g_real1_non_completion_status_claimed_completion",
            "Blocked S2G-REAL1 summaries must not claim target_met or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met status for completion claims",
            actual=result.status,
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "run_configuration.local_files_only",
            "selected_media.no_full_library_fallback",
            "model_cache.local_files_only",
            "dry_run.executed",
            "dry_run.no_media_tags_writes",
            "cpu_fallback_validation.executed",
            "load_control_observations.appeared_bounded",
            "public_redaction.passed",
            "safety.max_items_lte_5",
            "safety.no_full_library_run",
            "safety.dry_run_before_write",
        ),
        code="s2g_real1_required_proof_missing",
        message="S2G-REAL1 requires local-only model loading, bounded dry-run proof, CPU fallback proof, and public redaction proof.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "run_configuration.model_download_allowed",
            "selected_media.private_locator_values_recorded",
            "s3a_boundary.production_execution_enabled",
            "s3a_boundary.unattended_enabled",
            "safety.ai_tagging_write_without_confirmation",
            "safety.dry_run_media_tags_write",
            "safety.production_s3a_execution_enabled",
            "safety.unattended_s3b_enabled",
            "safety.provider_pixiv_gallery_dl_saucenao_google_calls",
            "safety.provider_pixiv_r1r_entity_operations",
            "safety.sourceconcept_r1r_r2",
            "safety.entity_bridge",
            "safety.confirmed_entity_assignments",
            "safety.source_icloud_mutation",
            "safety.cleanup_delete_reset_drop_truncate",
            "safety.db_import",
            "safety.production_import",
            "safety.production_classification",
            "safety.production_localization",
            "safety.model_download",
            "safety.private_locator_values_recorded",
        ),
        code="s2g_real1_forbidden_safety_flag",
        message="S2G-REAL1 summaries must explicitly keep forbidden operations disabled.",
    )

    max_items = _as_int(_get(summary, "run_configuration.max_items", 0))
    selected_count = _as_int(_get(summary, "selected_media.count", 0))
    dry_selected = _as_int(_get(summary, "dry_run.selected_media_count", 0))
    dry_processed = _as_int(_get(summary, "dry_run.processed", 0))
    dry_status = str(_get(summary, "dry_run.status", "") or "").casefold()
    dry_failed = _as_int(_get(summary, "dry_run.failed", 0))
    dry_error_state = _as_bool(_get(summary, "dry_run.error_state", False)) or _as_bool(_get(summary, "dry_run.rollback_error", False))
    cpu_status = str(_get(summary, "cpu_fallback_validation.status", "") or "").casefold()
    cpu_failed = _as_int(_get(summary, "cpu_fallback_validation.failed", 0))
    cpu_error_state = _as_bool(_get(summary, "cpu_fallback_validation.error_state", False)) or _as_bool(_get(summary, "cpu_fallback_validation.rollback_error", False))
    write_requested = _as_bool(_get(summary, "run_configuration.write_requested", False))
    write_executed = _as_bool(_get(summary, "write_run.executed", False))
    confirmation = _as_bool(_get(summary, "run_configuration.operator_confirmation_exact", False))
    if not (1 <= max_items <= 5):
        result.fail(
            "s2g_real1_max_items_unbounded",
            "S2G-REAL1 max_items must stay between 1 and 5.",
            path="run_configuration.max_items",
            expected="1..5",
            actual=max_items,
        )
    if not (1 <= selected_count <= max_items <= 5):
        result.fail(
            "s2g_real1_selected_media_not_small",
            "S2G-REAL1 selected media count must be non-zero and within max_items <= 5.",
            path="selected_media.count",
            expected=f"1..{max_items}",
            actual=selected_count,
        )
    if dry_selected != selected_count:
        result.fail(
            "s2g_real1_dry_run_scope_mismatch",
            "Dry-run selected media count must match the selected validation scope.",
            path="dry_run.selected_media_count",
            expected=selected_count,
            actual=dry_selected,
        )

    dry_success = (
        _as_bool(_get(summary, "dry_run.executed", False))
        and dry_status == "completed"
        and dry_failed == 0
        and dry_processed == selected_count
        and not dry_error_state
    )
    if status in target_statuses and not dry_success:
        result.fail(
            "s2g_real1_target_without_successful_dry_run",
            "S2G-REAL1 target_met requires a completed primary dry-run with zero item failures.",
            path="dry_run",
            expected={"status": "completed", "failed": 0, "processed": selected_count},
            actual={"status": _get(summary, "dry_run.status", None), "failed": dry_failed, "processed": dry_processed},
        )

    dry_delta = _as_int(_get(summary, "dry_run.media_tags_count_delta", 0))
    if dry_delta != 0:
        result.fail(
            "s2g_real1_dry_run_media_tags_delta",
            "Dry-run must not write media_tags.",
            path="dry_run.media_tags_count_delta",
            expected=0,
            actual=dry_delta,
        )

    requested = _get(summary, "primary_provider_validation.provider_preference_requested", [])
    provider = _get(summary, "primary_provider_validation.provider", {})
    actual_provider = _get(summary, "primary_provider_validation.provider.actual_provider", None)
    if not isinstance(requested, list) or not requested:
        result.fail(
            "s2g_real1_provider_preference_missing",
            "Primary validation must record requested provider preference.",
            path="primary_provider_validation.provider_preference_requested",
            expected="non-empty list",
            actual=requested,
        )
    if not isinstance(actual_provider, str) or not actual_provider.strip():
        result.fail(
            "s2g_real1_actual_provider_missing",
            "Primary validation must report the actual ONNX provider loaded.",
            path="primary_provider_validation.provider.actual_provider",
            expected="non-empty provider",
            actual=actual_provider,
        )
    elif isinstance(requested, list) and requested and actual_provider not in requested:
        result.fail(
            "s2g_real1_actual_provider_not_requested",
            "Primary actual provider must come from the bounded requested provider preference.",
            path="primary_provider_validation.provider.actual_provider",
            expected=requested,
            actual=actual_provider,
        )

    if isinstance(requested, list) and "DmlExecutionProvider" in requested and actual_provider != "DmlExecutionProvider":
        fallback_occurred = _as_bool(_get(summary, "primary_provider_validation.provider.fallback_occurred", False))
        fallback_reason = str(_get(summary, "primary_provider_validation.provider.fallback_reason", "") or "").strip()
        load_errors = _get(summary, "primary_provider_validation.provider.provider_load_errors", [])
        has_blocker = fallback_occurred and (fallback_reason or load_errors)
        if not has_blocker:
            result.fail(
                "s2g_real1_directml_missing_without_blocker",
                "If DirectML is requested but not loaded, the summary must report explicit fallback or blocker evidence.",
                path="primary_provider_validation.provider",
                expected="DmlExecutionProvider or fallback/blocker",
                actual=provider,
            )

    cpu_actual = _get(summary, "cpu_fallback_validation.provider.actual_provider", None)
    if cpu_actual != "CPUExecutionProvider":
        result.fail(
            "s2g_real1_cpu_fallback_actual_provider_invalid",
            "CPU fallback validation must force and load CPUExecutionProvider.",
            path="cpu_fallback_validation.provider.actual_provider",
            expected="CPUExecutionProvider",
            actual=cpu_actual,
        )
    if _as_int(_get(summary, "cpu_fallback_validation.media_tags_count_delta", 0)) != 0:
        result.fail(
            "s2g_real1_cpu_fallback_media_tags_delta",
            "CPU fallback validation must not write media_tags.",
            path="cpu_fallback_validation.media_tags_count_delta",
            expected=0,
            actual=_get(summary, "cpu_fallback_validation.media_tags_count_delta", None),
        )

    cpu_success = (
        _as_bool(_get(summary, "cpu_fallback_validation.executed", False))
        and cpu_status == "completed"
        and cpu_failed == 0
        and not cpu_error_state
        and cpu_actual == "CPUExecutionProvider"
        and _as_int(_get(summary, "cpu_fallback_validation.media_tags_count_delta", 0)) == 0
    )
    if status in target_statuses and not cpu_success:
        result.fail(
            "s2g_real1_target_without_successful_cpu_fallback",
            "S2G-REAL1 target_met requires a completed CPU fallback validation with zero failures.",
            path="cpu_fallback_validation",
            expected={"status": "completed", "failed": 0, "actual_provider": "CPUExecutionProvider"},
            actual={"status": _get(summary, "cpu_fallback_validation.status", None), "failed": cpu_failed, "actual_provider": cpu_actual},
        )

    effective_batch = _as_int(_get(summary, "load_control_observations.effective_batch_size", 0))
    intra = _as_int(_get(summary, "load_control_observations.cpu_intra_op_threads", 0))
    inter = _as_int(_get(summary, "load_control_observations.cpu_inter_op_threads", 0))
    preprocess = _as_int(_get(summary, "load_control_observations.preprocess_workers", 0))
    max_jobs = _as_int(_get(summary, "load_control_observations.max_concurrent_ai_jobs", 0))
    if not (1 <= effective_batch <= 5):
        result.fail("s2g_real1_effective_batch_unbounded", "Effective batch size must stay within the tiny validation cap.", path="load_control_observations.effective_batch_size", expected="1..5", actual=effective_batch)
    if not (1 <= intra <= 4):
        result.fail("s2g_real1_cpu_intra_threads_unbounded", "CPU intra-op threads must stay capped.", path="load_control_observations.cpu_intra_op_threads", expected="1..4", actual=intra)
    if inter != 1:
        result.fail("s2g_real1_cpu_inter_threads_unbounded", "CPU inter-op threads must remain one.", path="load_control_observations.cpu_inter_op_threads", expected=1, actual=inter)
    if not (1 <= preprocess <= 2):
        result.fail("s2g_real1_preprocess_workers_unbounded", "Preprocess workers must stay capped.", path="load_control_observations.preprocess_workers", expected="1..2", actual=preprocess)
    if max_jobs != 1:
        result.fail("s2g_real1_max_concurrent_jobs_unbounded", "S2G-REAL1 must keep max concurrent AI jobs at one.", path="load_control_observations.max_concurrent_ai_jobs", expected=1, actual=max_jobs)

    model_download_allowed = _as_bool(_get(summary, "run_configuration.model_download_allowed", False)) or _as_bool(_get(summary, "model_cache.model_download_allowed", False))
    if model_download_allowed and status in target_statuses:
        result.fail(
            "s2g_real1_model_download_allowed_claimed_target",
            "S2G-REAL1 public validation must remain local-cache-only and cannot claim target_met when model download is allowed.",
            path="run_configuration.model_download_allowed",
            expected=False,
            actual=_get(summary, "run_configuration.model_download_allowed", None),
        )
    if model_download_allowed and status != "blocked_model_download_allowed":
        result.fail(
            "s2g_real1_model_download_allowed_not_blocked",
            "Model download allowance must produce blocked_model_download_allowed for S2G-REAL1 public validation.",
            path="pipeline_contract.status",
            expected="blocked_model_download_allowed",
            actual=result.status,
        )

    if write_requested and not confirmation and status != "blocked_write_requested_without_exact_confirmation":
        result.fail(
            "s2g_real1_write_requested_without_exact_confirmation_not_blocked",
            "An --execute request without the exact operator confirmation must block, not fall through to dry-run target_met.",
            path="pipeline_contract.status",
            expected="blocked_write_requested_without_exact_confirmation",
            actual=result.status,
        )
    if write_executed and not confirmation:
        result.fail(
            "s2g_real1_write_without_exact_confirmation",
            "Bounded write validation requires the exact operator confirmation string.",
            path="run_configuration.operator_confirmation_exact",
            expected=True,
            actual=False,
        )

    if status == "target_met_dry_run_only":
        if write_requested:
            result.fail(
                "s2g_real1_dry_run_target_with_write_requested",
                "target_met_dry_run_only is only valid when no write run was requested.",
                path="run_configuration.write_requested",
                expected=False,
                actual=True,
            )
        if write_executed:
            result.fail(
                "s2g_real1_dry_run_target_with_write_executed",
                "target_met_dry_run_only must not include a bounded write execution.",
                path="write_run.executed",
                expected=False,
                actual=True,
            )

    write_status = str(_get(summary, "write_run.status", "") or "").casefold()
    write_failed = _as_int(_get(summary, "write_run.failed", 0))
    write_processed = _as_int(_get(summary, "write_run.processed", 0))
    write_delta_present = _has_non_null(summary, "write_run.media_tags_count_delta")
    write_error_state = _as_bool(_get(summary, "write_run.error_state", False)) or _as_bool(_get(summary, "write_run.rollback_error", False))
    write_prerequisites_all_passed = _as_bool(_get(summary, "write_prerequisites.all_passed", False))
    write_after_prerequisites = _as_bool(_get(summary, "write_prerequisites.write_executed_after_prerequisites_passed", False))
    if write_executed and not write_after_prerequisites:
        result.fail(
            "s2g_real1_write_before_prerequisites",
            "Bounded write must not execute until dry-run, CPU fallback, model cache, scope, redaction, and confirmation prerequisites pass.",
            path="write_prerequisites.write_executed_after_prerequisites_passed",
            expected=True,
            actual=False,
        )
    write_has_item_failures = write_executed and (
        write_status != "completed"
        or write_failed != 0
        or write_processed != selected_count
        or not write_delta_present
        or write_error_state
    )
    if write_has_item_failures and status != "blocked_write_item_failures":
        result.fail(
            "s2g_real1_write_run_failed_not_blocked",
            "A bounded write with item failures, rollback/error state, missing delta, or scope mismatch must block.",
            path="pipeline_contract.status",
            expected="blocked_write_item_failures",
            actual=result.status,
        )
    if status == "target_met_with_bounded_write" and not write_executed:
        result.fail(
            "s2g_real1_confirmed_write_missing",
            "A target_met_with_bounded_write summary must include an executed write result.",
            path="write_run.executed",
            expected=True,
            actual=False,
        )
    if status == "target_met_with_bounded_write":
        if not write_requested:
            result.fail(
                "s2g_real1_write_target_without_write_requested",
                "target_met_with_bounded_write requires an explicit write request.",
                path="run_configuration.write_requested",
                expected=True,
                actual=False,
            )
        if not confirmation:
            result.fail(
                "s2g_real1_write_target_without_exact_confirmation",
                "target_met_with_bounded_write requires exact operator confirmation.",
                path="run_configuration.operator_confirmation_exact",
                expected=True,
                actual=False,
            )
        if not write_prerequisites_all_passed:
            result.fail(
                "s2g_real1_write_target_without_prerequisites",
                "target_met_with_bounded_write requires all write prerequisites to pass before execution.",
                path="write_prerequisites.all_passed",
                expected=True,
                actual=_get(summary, "write_prerequisites.all_passed", None),
            )
        required_prerequisite_paths = (
            "write_prerequisites.primary_dry_run_success",
            "write_prerequisites.cpu_fallback_success",
            "write_prerequisites.exact_write_confirmation_present",
            "write_prerequisites.write_executed_after_prerequisites_passed",
        )
        for path in required_prerequisite_paths:
            if not _as_bool(_get(summary, path, False)):
                result.fail(
                    "s2g_real1_write_target_missing_prerequisite",
                    "target_met_with_bounded_write requires successful dry-run, CPU fallback, confirmation, and post-prerequisite execution proof.",
                    path=path,
                    expected=True,
                    actual=_get(summary, path, None),
                )
        if write_has_item_failures:
            result.fail(
                "s2g_real1_write_run_failed_target",
                "target_met_with_bounded_write requires a completed bounded write with failed=0, processed scope match, present delta, and no rollback/error state.",
                path="write_run",
                expected={"status": "completed", "failed": 0, "processed": selected_count, "media_tags_count_delta": "present"},
                actual={
                    "status": _get(summary, "write_run.status", None),
                    "failed": write_failed,
                    "processed": write_processed,
                    "media_tags_count_delta_present": write_delta_present,
                    "error_state": write_error_state,
                },
            )
    if write_executed and _as_int(_get(summary, "write_run.selected_media_count", selected_count)) > 5:
        result.fail(
            "s2g_real1_write_scope_unbounded",
            "Bounded write validation must process at most five media IDs.",
            path="write_run.selected_media_count",
            expected="<=5",
            actual=_get(summary, "write_run.selected_media_count", None),
        )

    markdown_text = _read_s2g_real1_markdown_report(summary, result)
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s2g_real1_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s2g_real1_public_payload_redaction_failed",
            "S2G-REAL1 contract independently found forbidden public JSON or Markdown content.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )


def _check_s2g_manual_sync_foundation(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "target_met",
        "foundation_ready_pending_validation",
        "blocked_probe_unavailable",
        "blocked_manual_sync_foundation",
        "blocked_public_redaction_failed",
        "blocked_stale_head_evidence",
    }
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s2g_m1_unknown_status",
            "S2G-M1 status must explicitly describe the foundation/probe outcome.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status != "target_met" and _completion_or_approval_claimed(result):
        result.fail(
            "s2g_m1_non_completion_status_claimed_completion",
            "Non-target S2G-M1 statuses must not claim target_met, route approval, full-chain completion, or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met for completion claims",
            actual=result.status,
        )
    if status == "target_met" and not _as_bool(_get(summary, "validation.focused_tests_passed", False)):
        result.fail(
            "s2g_m1_target_without_focused_tests",
            "S2G-M1 may claim target_met only after focused tests have passed.",
            path="validation.focused_tests_passed",
            expected=True,
            actual=_get(summary, "validation.focused_tests_passed", None),
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "pipeline_contract.post_123_route_respected",
            "head_evidence.post_validation_changes_report_only",
            "capability_probe.attempted",
            "capability_probe.bounded",
            "capability_probe.synthetic_input_only",
            "capability_probe.local_files_only",
            "capability_probe.provider_fallback_decision_recorded",
            "provider_fallback.decision_recorded",
            "load_control_policy.present",
            "load_control_policy.single_active_ai_execution_guard",
            "load_control_policy.failure_isolation_per_image",
            "load_control_policy.no_unbounded_production_loop",
            "provenance_policy.present",
            "provenance_policy.manual_locked_tags_not_overwritten",
            "provenance_policy.suggestions_vs_confirmed_recorded",
            "manual_sync.dry_run_planner.implemented",
            "manual_sync.dry_run_planner.public_safe",
            "manual_sync.job_ledger_foundation.implemented",
            "manual_sync.job_ledger_foundation.job_id_present",
            "manual_sync.job_ledger_foundation.per_file_state_records_present",
            "manual_sync.controlled_pipeline.implemented",
            "manual_sync.controlled_pipeline.dry_run_only_this_phase",
            "validation.runner_completed",
            "public_redaction.passed",
        ),
        code="s2g_m1_required_foundation_proof_missing",
        message="S2G-M1 requires current-main proof, bounded probe proof, load/provenance proof, planner/ledger/pipeline proof, and public redaction proof.",
    )
    if status == "target_met":
        _check_required_boolean_paths(
            summary,
            result,
            (
                "head_evidence.pr123_merge_is_ancestor_of_origin_main",
                "head_evidence.latest_main_after_pr123",
                "head_evidence.validated_implementation_is_ancestor_of_head",
                "head_evidence.validated_implementation_is_not_base_main",
                "head_evidence.head_evidence_valid",
            ),
            code="s2g_m1_target_head_evidence_invalid",
            message="S2G-M1 target_met requires validated implementation evidence tied to the current PR head.",
        )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "ai_execution_profile.production_writes_enabled",
            "ai_execution_profile.provider_network_calls_enabled",
            "ai_execution_profile.llm_calls_enabled",
            "provenance_policy.production_writes_enabled",
            "manual_sync.dry_run_planner.db_write_performed",
            "manual_sync.dry_run_planner.source_mutation_performed",
            "manual_sync.dry_run_planner.app_storage_mutation_performed",
            "manual_sync.controlled_pipeline.production_execute_enabled",
            "api_surface.production_write_endpoint_enabled",
            "api_surface.automatic_execution_endpoint_added",
            "safety.production_db_mutation",
            "safety.production_import",
            "safety.production_classification",
            "safety.production_ai_tagging_writes",
            "safety.production_localization_writes",
            "safety.source_icloud_mutation",
            "safety.app_managed_production_storage_mutation",
            "safety.external_provider_calls",
            "safety.gallery_dl_pixiv_saucenao_google_calls",
            "safety.sourceconcept_mutation",
            "safety.entity_truth_writes",
            "safety.confirmed_assignment_writes",
            "safety.production_media_tags_mutation",
            "safety.llm_calls",
            "safety.automatic_sync_enabled",
            "safety.scheduled_sync_enabled",
            "safety.system_service_enabled",
            "safety.startup_task_enabled",
            "safety.long_running_daemon_enabled",
            "safety.final_production_acceptance_completed",
        ),
        code="s2g_m1_forbidden_execution_or_mutation",
        message="S2G-M1 must explicitly keep production writes, provider/LLM/entity work, and automatic/scheduled sync disabled.",
    )

    if str(_get(summary, "pipeline_contract.phase_identity", "")) != "S2G-M1":
        result.fail(
            "s2g_m1_phase_identity_mismatch",
            "S2G-M1 summaries must declare the exact phase identity.",
            path="pipeline_contract.phase_identity",
            expected="S2G-M1",
            actual=_get(summary, "pipeline_contract.phase_identity", None),
        )

    validated_sha = str(_get(summary, "head_evidence.validated_implementation_sha", "") or "").strip()
    report_head_sha = str(_get(summary, "head_evidence.report_generation_head_sha", "") or "").strip()
    base_main_sha = str(_get(summary, "head_evidence.pr123_merge_commit", "") or "").strip()
    origin_main_sha = str(_get(summary, "head_evidence.origin_main_sha", "") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", validated_sha):
        result.fail(
            "s2g_m1_validated_implementation_sha_missing",
            "S2G-M1 summary must identify the implementation commit validated by the runner.",
            path="head_evidence.validated_implementation_sha",
            expected="40-char git sha",
            actual=validated_sha or None,
        )
    elif validated_sha in {base_main_sha, origin_main_sha} and status == "target_met":
        result.fail(
            "s2g_m1_stale_base_main_head_evidence",
            "S2G-M1 implementation evidence must not point only at the pre-PR base/main commit.",
            path="head_evidence.validated_implementation_sha",
            expected="PR implementation commit, not base main",
            actual=validated_sha,
        )
    elif subprocess.run(
        ["git", "merge-base", "--is-ancestor", validated_sha, "HEAD"],
        cwd=CONTRACT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        result.fail(
            "s2g_m1_validated_implementation_not_ancestor",
            "S2G-M1 validated implementation commit must be an ancestor of the current report head.",
            path="head_evidence.validated_implementation_sha",
            expected="ancestor of current HEAD",
            actual=validated_sha,
        )
    if report_head_sha and not re.fullmatch(r"[0-9a-f]{40}", report_head_sha):
        result.fail(
            "s2g_m1_report_head_sha_invalid",
            "S2G-M1 report generation head must be a git SHA when recorded.",
            path="head_evidence.report_generation_head_sha",
            expected="40-char git sha",
            actual=report_head_sha,
        )

    profile = _get(summary, "ai_execution_profile", {})
    if not isinstance(profile, Mapping):
        result.fail("s2g_m1_profile_not_object", "ai_execution_profile must be an object.", path="ai_execution_profile")
        profile = {}
    if str(profile.get("provider_backend") or "") != "onnxruntime":
        result.fail(
            "s2g_m1_provider_backend_invalid",
            "AI tagging execution profile must remain local ONNX Runtime only.",
            path="ai_execution_profile.provider_backend",
            expected="onnxruntime",
            actual=profile.get("provider_backend"),
        )
    provider_preference = profile.get("provider_preference")
    if not isinstance(provider_preference, list) or "CPUExecutionProvider" not in provider_preference:
        result.fail(
            "s2g_m1_cpu_fallback_missing_from_profile",
            "AI tagging execution profile must keep CPU fallback in the provider preference.",
            path="ai_execution_profile.provider_preference",
            expected="list containing CPUExecutionProvider",
            actual=provider_preference,
        )
    batch_size = _as_int(profile.get("batch_size", 0))
    concurrency = _as_int(profile.get("concurrency", 0))
    per_image_timeout = _as_int(profile.get("per_image_timeout_seconds", 0))
    job_timeout = _as_int(profile.get("job_timeout_seconds", 0))
    if not (1 <= batch_size <= 16):
        result.fail("s2g_m1_batch_size_unbounded", "AI tagging batch size must stay within the phase cap.", path="ai_execution_profile.batch_size", expected="1..16", actual=batch_size)
    if concurrency != 1:
        result.fail("s2g_m1_concurrency_unbounded", "AI tagging concurrency must remain one for this foundation phase.", path="ai_execution_profile.concurrency", expected=1, actual=concurrency)
    if per_image_timeout < 1:
        result.fail("s2g_m1_per_image_timeout_missing", "Per-image timeout must be a positive integer.", path="ai_execution_profile.per_image_timeout_seconds", expected="positive integer", actual=per_image_timeout)
    if job_timeout < per_image_timeout:
        result.fail("s2g_m1_job_timeout_too_small", "Job timeout must be at least the per-image timeout.", path="ai_execution_profile.job_timeout_seconds", expected=f">= {per_image_timeout}", actual=job_timeout)
    if not _as_bool(profile.get("local_files_only")):
        result.fail("s2g_m1_model_cache_not_local_only", "S2G-M1 must use local-files-only model resolution.", path="ai_execution_profile.local_files_only", expected=True, actual=profile.get("local_files_only"))

    probe = _get(summary, "capability_probe", {})
    if not isinstance(probe, Mapping):
        result.fail("s2g_m1_probe_not_object", "capability_probe must be an object.", path="capability_probe")
        probe = {}
    sample_count = _as_int(probe.get("sample_count", 0))
    if not (1 <= sample_count <= 16):
        result.fail("s2g_m1_probe_sample_count_unbounded", "Capability probe sample count must be bounded.", path="capability_probe.sample_count", expected="1..16", actual=sample_count)
    provider_matrix = probe.get("provider_matrix")
    if not isinstance(provider_matrix, Mapping) or "cpu" not in provider_matrix:
        result.fail(
            "s2g_m1_cpu_provider_row_missing",
            "Capability probe must report CPU fallback status.",
            path="capability_probe.provider_matrix.cpu",
            expected="CPU provider row",
            actual=provider_matrix,
        )
    selection = probe.get("provider_selection")
    if not isinstance(selection, Mapping) or not selection.get("requested_provider_preference"):
        result.fail(
            "s2g_m1_provider_selection_missing",
            "Capability probe must record requested provider preference and selected/fallback result.",
            path="capability_probe.provider_selection",
            expected="provider selection object",
            actual=selection,
        )
    completed_provider_rows = [
        row
        for row in (provider_matrix or {}).values()
        if isinstance(row, Mapping) and str(row.get("status")) == "completed"
    ] if isinstance(provider_matrix, Mapping) else []
    if not completed_provider_rows and not str(probe.get("blocker") or "").strip():
        result.fail(
            "s2g_m1_probe_blocker_missing",
            "If no provider benchmark completed, the probe must report an explicit blocker.",
            path="capability_probe.blocker",
            expected="non-empty blocker when no provider completed",
            actual=probe.get("blocker"),
        )
    recommended_batch = _as_int(probe.get("recommended_batch_size", 0))
    recommended_concurrency = _as_int(probe.get("recommended_concurrency", 0))
    if not (1 <= recommended_batch <= 16):
        result.fail("s2g_m1_recommended_batch_unbounded", "Recommended AI batch size must stay bounded.", path="capability_probe.recommended_batch_size", expected="1..16", actual=recommended_batch)
    if recommended_concurrency != 1:
        result.fail("s2g_m1_recommended_concurrency_unbounded", "Recommended concurrency must remain one.", path="capability_probe.recommended_concurrency", expected=1, actual=recommended_concurrency)

    state_counts = _get(summary, "manual_sync.dry_run_planner.state_counts", {})
    if not isinstance(state_counts, Mapping):
        result.fail("s2g_m1_state_counts_missing", "Manual sync dry-run planner must report per-state counts.", path="manual_sync.dry_run_planner.state_counts")
        state_counts = {}
    expected_states = {
        "import_planned",
        "skipped_unsupported",
        "skipped_zero_byte",
        "skipped_duplicate",
        "skipped_existing_media",
        "failed",
    }
    missing_states = sorted(expected_states - set(str(key) for key in state_counts.keys()))
    if missing_states:
        result.fail(
            "s2g_m1_planner_state_coverage_missing",
            "Manual sync dry-run planner proof must cover candidate, duplicate/existing, unsupported, zero-byte, and failure states.",
            path="manual_sync.dry_run_planner.state_counts",
            expected=sorted(expected_states),
            actual=sorted(state_counts.keys()),
        )
    if _as_int(_get(summary, "manual_sync.dry_run_planner.estimated_import_count", 0)) < 1:
        result.fail(
            "s2g_m1_no_import_candidate_planned",
            "The S2G-M1 dry-run fixture proof must include at least one import-planned candidate.",
            path="manual_sync.dry_run_planner.estimated_import_count",
            expected=">=1",
            actual=_get(summary, "manual_sync.dry_run_planner.estimated_import_count", None),
        )

    stages = _get(summary, "manual_sync.controlled_pipeline.stages", [])
    stage_names = {
        str(stage.get("name"))
        for stage in stages
        if isinstance(stages, list) and isinstance(stage, Mapping)
    } if isinstance(stages, list) else set()
    expected_stages = {"candidate_discovery", "import", "classification", "ai_tagging", "localization", "summary"}
    missing_stages = sorted(expected_stages - stage_names)
    if missing_stages:
        result.fail(
            "s2g_m1_pipeline_stages_missing",
            "Controlled pipeline foundation must expose each future manual sync stage.",
            path="manual_sync.controlled_pipeline.stages",
            expected=sorted(expected_stages),
            actual=sorted(stage_names),
        )
    elif any(
        _as_bool(stage.get("writes_enabled", False)) or _as_bool(stage.get("production_execution_enabled", False))
        for stage in stages
        if isinstance(stage, Mapping)
    ):
        result.fail(
            "s2g_m1_pipeline_stage_write_enabled",
            "S2G-M1 controlled pipeline stages must stay dry-run-only with writes disabled.",
            path="manual_sync.controlled_pipeline.stages",
            expected="all writes_enabled=false and production_execution_enabled=false",
        )

    placement = str(_get(summary, "final_button_recommendation.placement", "") or "")
    if placement not in {"launcher", "web_admin", "both_launcher_and_web_admin"}:
        result.fail(
            "s2g_m1_button_placement_invalid",
            "Final button recommendation must choose launcher, Web Admin, or both.",
            path="final_button_recommendation.placement",
            expected=["launcher", "web_admin", "both_launcher_and_web_admin"],
            actual=placement,
        )
    max_files = _as_int(_get(summary, "final_button_recommendation.safe_default_max_files", 0))
    max_duration = _as_int(_get(summary, "final_button_recommendation.safe_default_max_duration_seconds", 0))
    if not (1 <= max_files <= 1000):
        result.fail("s2g_m1_final_button_max_files_unbounded", "Final button safe default max files must be bounded.", path="final_button_recommendation.safe_default_max_files", expected="1..1000", actual=max_files)
    if not (1 <= max_duration <= 3600):
        result.fail("s2g_m1_final_button_duration_unbounded", "Final button safe default max duration must be bounded.", path="final_button_recommendation.safe_default_max_duration_seconds", expected="1..3600", actual=max_duration)
    if _as_int(_get(summary, "final_button_recommendation.safe_default_ai_batch_size", 0)) != batch_size:
        result.fail(
            "s2g_m1_button_batch_mismatch",
            "Final button recommendation should reuse the S2G-M1 AI execution profile batch size.",
            path="final_button_recommendation.safe_default_ai_batch_size",
            expected=batch_size,
            actual=_get(summary, "final_button_recommendation.safe_default_ai_batch_size", None),
        )
    if _as_int(_get(summary, "final_button_recommendation.safe_default_concurrency", 0)) != 1:
        result.fail(
            "s2g_m1_button_concurrency_unbounded",
            "Final button recommendation must keep AI concurrency at one by default.",
            path="final_button_recommendation.safe_default_concurrency",
            expected=1,
            actual=_get(summary, "final_button_recommendation.safe_default_concurrency", None),
        )

    if _as_bool(_get(summary, "validation.browser_validation_required", True)):
        result.fail(
            "s2g_m1_browser_validation_unexpectedly_required",
            "S2G-M1 should not require browser validation unless visible UI behavior changed.",
            path="validation.browser_validation_required",
            expected=False,
            actual=_get(summary, "validation.browser_validation_required", None),
        )

    redaction_passed = _as_bool(_get(summary, "public_redaction.passed", False))
    if status == "target_met" and not redaction_passed:
        result.fail(
            "s2g_m1_target_with_failed_public_redaction",
            "S2G-M1 target_met requires public redaction to pass.",
            path="public_redaction.passed",
            expected=True,
            actual=_get(summary, "public_redaction.passed", None),
        )
    markdown_text = _read_s2g_m1_markdown_report(summary, result)
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s2g_m1_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s2g_m1_public_payload_redaction_failed",
            "S2G-M1 contract independently found forbidden public JSON or Markdown content.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )


def _check_s3a_m1_manual_sync_execute(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "target_met_dev_test_ready",
        "blocked_validation_failed",
        "blocked_execute_gate_failed",
        "blocked_public_redaction_failed",
    }
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s3a_m1_unknown_status",
            "S3A-M1 status must explicitly describe dev/test readiness or the blocking condition.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status != "target_met_dev_test_ready" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_m1_non_completion_status_claimed_completion",
            "Non-target S3A-M1 statuses must not claim target_met, route approval, full-chain completion, or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met_dev_test_ready for completion claims",
            actual=result.status,
        )
    if str(_get(summary, "pipeline_contract.phase_identity", "") or "") != "S3A-M1":
        result.fail(
            "s3a_m1_phase_identity_mismatch",
            "S3A-M1 summaries must declare the exact phase identity.",
            path="pipeline_contract.phase_identity",
            expected="S3A-M1",
            actual=_get(summary, "pipeline_contract.phase_identity", None),
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "manual_sync.plan_endpoint",
            "manual_sync.execute_endpoint",
            "manual_sync.status_endpoint",
            "manual_sync.job_status_endpoint",
            "manual_sync.cancel_endpoint",
            "manual_sync.plan_hash_required",
            "manual_sync.exact_confirmation_required",
            "manual_sync.plan_freshness_required",
            "manual_sync.registered_root_required_for_execute",
            "manual_sync.hydrated_only_required",
            "manual_sync.default_execute_disabled",
            "manual_sync.stale_replan_rejected",
            "manual_sync.limits.execute_max_files_exceeded_rejected",
            "manual_sync.limits.dry_run_execute_default_max_files_aligned",
            "manual_sync.limits.normal_update_check_not_forced_to_execute_cap",
            "manual_sync.limits.separate_update_check_and_execute_limits",
            "manual_sync.active_job_gates.ai_job_active_blocked",
            "manual_sync.active_job_gates.classification_job_active_blocked",
            "manual_sync.active_job_gates.queued_ai_job_blocked",
            "manual_sync.active_job_gates.queued_classification_job_blocked",
            "manual_sync.active_job_gates.queued_manual_sync_execute_blocks_ai_job",
            "manual_sync.active_job_gates.queued_manual_sync_execute_blocks_classification_job",
            "manual_sync.active_job_gates.manual_sync_execute_active_blocks_ai_job",
            "manual_sync.active_job_gates.manual_sync_execute_active_blocks_classification_job",
            "manual_sync.runner_outputs.default_report_json_gitignored",
            "manual_sync.runner_outputs.default_report_md_gitignored",
            "manual_sync.runner_outputs.docs_reports_require_explicit_flags",
            "manual_sync.runner_outputs.execute_report_uses_approved_plan",
            "manual_sync.runner_outputs.standalone_db_session_initialized",
            "manual_sync.translation_side_effect_gates.background_llm_blocked",
            "manual_sync.translation_side_effect_gates.auto_llm_blocked",
            "manual_sync.translation_side_effect_gates.llm_enabled_blocked",
            "manual_sync.translation_side_effect_gates.live_worker_state_blocked",
            "manual_sync.translation_side_effect_gates.schedule_localization_false_not_sufficient",
            "manual_sync.classification.local_only",
            "manual_sync.classification.clip_cache_only_required",
            "manual_sync.classification.uncached_clip_skips",
            "manual_sync.classification.method_and_order_reported",
            "manual_sync.classification.heuristic_ai_tags_before_classification",
            "manual_sync.classification.heuristic_deferred_when_ai_tags_unavailable",
            "manual_sync.classification.heuristic_ai_failure_does_not_write_unknown",
            "manual_sync.classification.clip_cached_path_preserved",
            "manual_sync.ai_tagging.item_exception_containment",
            "manual_sync.ai_tagging.returned_error_sanitized",
            "manual_sync.ai_tagging.proper_nouns_suggestion_only",
            "manual_sync.ai_tagging.no_sourceconcept_or_entity_truth_from_ai_proper_nouns",
            "manual_sync.ai_tagging.single_item_failure_does_not_fail_whole_run",
            "manual_sync.active_run_recovery.stale_pending_running_finalized",
            "manual_sync.active_run_recovery.stale_cancelling_finalized",
            "manual_sync.plan_replay_protection.plan_hash_binds_created_at",
            "manual_sync.plan_replay_protection.directory_walk_order_deterministic",
            "manual_sync.plan_replay_protection.unchanged_tree_not_rejected_by_directory_order",
            "manual_sync.plan_replay_protection.forged_fresh_timestamp_rejected",
            "manual_sync.plan_replay_protection.source_change_still_rejected",
            "manual_sync.per_item_failures.source_missing_recorded",
            "manual_sync.per_item_failures.read_error_recorded",
            "manual_sync.per_item_failures.read_timeout_recorded",
            "manual_sync.per_item_failures.continue_within_failure_budget",
            "manual_sync.failure_budget.stopped_by_failure_budget_recorded",
            "manual_sync.failure_budget.stopped_by_duration_budget_recorded",
            "manual_sync.failure_budget.unprocessed_count_reported",
            "manual_sync.failure_budget.pending_import_preserved_on_early_stop",
            "manual_sync.localization.blocked_current_phase",
            "manual_sync.public_serialization.generic_sync_run_redacts_private_plan",
            "manual_sync.public_serialization.dashboard_state_redacts_private_plan",
            "manual_sync.public_serialization.pending_summary_redacts_private_plan",
            "manual_sync.public_serialization.job_serializers_redact_private_plan",
            "manual_sync.ledger.per_file_records_present",
            "manual_sync.ledger.dynamic_sync_run_used",
            "manual_sync.ledger.import_preledger_committed_before_media_write",
            "manual_sync.ledger.import_preledger_success_failure_updated",
            "manual_sync.ledger.run_item_deduplicated_per_source_item",
            "manual_sync.ledger.deferred_unprocessed_rows_materialized",
            "manual_sync.ledger.deferred_unprocessed_without_source_read_or_hash",
            "manual_sync.ledger.public_safe",
            "manual_sync.pipeline.dev_test_execute_supported",
            "manual_sync.pipeline.production_acceptance_pending",
            "api_surface.manual_execute_endpoint_added",
            "ui.web_admin_manual_execute_panel",
            "ui.web_admin_plan_confirmation_flow",
            "ui.web_admin_default_max_files_visible",
            "ui.web_admin_separate_update_check_limit",
            "ui.launcher_manual_sync_entry",
            "ui.launcher_manual_sync_forces_content_tab",
            "validation.focused_tests_passed",
            "validation.launcher_tests_passed",
            "validation.browser_validation_performed",
            "validation.contract_check_passed",
            "public_redaction.passed",
        ),
        code="s3a_m1_required_proof_missing",
        message="S3A-M1 requires guarded execute, UI/launcher, validation, and public-redaction proof.",
    )
    if status == "target_met_dev_test_ready":
        _check_required_boolean_paths(
            summary,
            result,
            (
                "target_met",
                "manual_sync.dev_test_execute_validation.completed",
                "manual_sync.dev_test_execute_validation.source_mutation_absent",
                "manual_sync.dev_test_execute_validation.llm_calls_absent",
            ),
            code="s3a_m1_target_validation_missing",
            message="S3A-M1 target status requires focused dev/test execute validation proof.",
        )

    _check_explicit_false_paths(
        summary,
        result,
        (
            "ai_execution_profile.llm_calls_enabled",
            "api_surface.automatic_execution_endpoint_added",
            "safety.production_execute_performed",
            "safety.production_import",
            "safety.production_classification",
            "safety.production_ai_tagging_writes",
            "safety.production_localization_writes",
            "safety.source_icloud_mutation",
            "safety.app_managed_production_storage_mutation",
            "safety.external_provider_calls",
            "safety.model_downloads",
            "safety.llm_calls",
            "safety.automatic_sync_enabled",
            "safety.scheduled_sync_enabled",
            "safety.system_service_enabled",
            "safety.startup_task_enabled",
            "safety.production_acceptance_completed",
        ),
        code="s3a_m1_forbidden_execution_or_mutation",
        message="S3A-M1 must keep production writes, source/iCloud mutation, LLM/provider calls, and unattended sync disabled.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "manual_sync.classification.model_downloads_allowed",
            "manual_sync.ai_tagging.raw_error_details_public",
            "manual_sync.localization.scheduled_in_execute",
        ),
        code="s3a_m1_forbidden_model_or_localization_side_effect",
        message="S3A-M1 must not allow classification model downloads or execute-time localization scheduling.",
    )

    profile = _get(summary, "ai_execution_profile", {})
    if not isinstance(profile, Mapping):
        result.fail("s3a_m1_profile_not_object", "ai_execution_profile must be an object.", path="ai_execution_profile")
        profile = {}
    if str(profile.get("provider_backend") or "") != "onnxruntime":
        result.fail("s3a_m1_provider_backend_invalid", "S3A-M1 AI execution must stay on local ONNX Runtime.", path="ai_execution_profile.provider_backend", expected="onnxruntime", actual=profile.get("provider_backend"))
    provider_preference = profile.get("provider_preference")
    if not isinstance(provider_preference, list) or "CPUExecutionProvider" not in provider_preference:
        result.fail("s3a_m1_cpu_fallback_missing", "S3A-M1 AI profile must keep CPU fallback.", path="ai_execution_profile.provider_preference", expected="list containing CPUExecutionProvider", actual=provider_preference)
    if not _as_bool(profile.get("local_files_only")):
        result.fail("s3a_m1_local_files_only_missing", "S3A-M1 AI execution must enforce local-files-only model loading.", path="ai_execution_profile.local_files_only", expected=True, actual=profile.get("local_files_only"))
    batch_size = _as_int(profile.get("batch_size", 0))
    concurrency = _as_int(profile.get("concurrency", 0))
    if not (1 <= batch_size <= 16):
        result.fail("s3a_m1_batch_unbounded", "S3A-M1 AI batch size must stay bounded.", path="ai_execution_profile.batch_size", expected="1..16", actual=batch_size)
    if concurrency != 1:
        result.fail("s3a_m1_concurrency_unbounded", "S3A-M1 AI concurrency must remain one.", path="ai_execution_profile.concurrency", expected=1, actual=concurrency)

    stages = _get(summary, "manual_sync.pipeline.stages", [])
    stage_names = {
        str(stage.get("name"))
        for stage in stages
        if isinstance(stages, list) and isinstance(stage, Mapping)
    } if isinstance(stages, list) else set()
    expected_stages = {"plan", "import", "classification", "ai_tagging", "localization", "summary"}
    missing_stages = sorted(expected_stages - stage_names)
    if missing_stages:
        result.fail(
            "s3a_m1_pipeline_stages_missing",
            "Manual execute pipeline must expose plan, import, classification, AI tagging, localization, and summary stages.",
            path="manual_sync.pipeline.stages",
            expected=sorted(expected_stages),
            actual=sorted(stage_names),
        )

    max_files = _as_int(_get(summary, "manual_sync.limits.safe_default_max_files", 0))
    execute_max_files = _as_int(_get(summary, "manual_sync.limits.execute_max_files", 0))
    default_execute_max_files = _as_int(_get(summary, "manual_sync.limits.manual_execute_default_max_files", 0))
    max_duration = _as_int(_get(summary, "manual_sync.limits.max_duration_seconds", 0))
    if not (1 <= max_files <= 1000):
        result.fail("s3a_m1_max_files_unbounded", "S3A-M1 safe default max files must stay bounded.", path="manual_sync.limits.safe_default_max_files", expected="1..1000", actual=max_files)
    if not (1 <= execute_max_files <= 5):
        result.fail("s3a_m1_execute_max_files_unbounded", "S3A-M1 execute max_files cap must stay at or below 5.", path="manual_sync.limits.execute_max_files", expected="1..5", actual=execute_max_files)
    if default_execute_max_files != execute_max_files:
        result.fail("s3a_m1_default_execute_max_files_mismatch", "S3A-M1 dry-run/manual execute default max_files must match the execute cap.", path="manual_sync.limits.manual_execute_default_max_files", expected=execute_max_files, actual=default_execute_max_files)
    if not (1 <= max_duration <= 3600):
        result.fail("s3a_m1_max_duration_unbounded", "S3A-M1 max duration must stay bounded.", path="manual_sync.limits.max_duration_seconds", expected="1..3600", actual=max_duration)

    recovery_timeout = _as_int(_get(summary, "manual_sync.active_run_recovery.timeout_seconds", 0))
    if not (60 <= recovery_timeout <= 86400):
        result.fail("s3a_m1_active_recovery_timeout_invalid", "S3A-M1 stale active run recovery timeout must be bounded.", path="manual_sync.active_run_recovery.timeout_seconds", expected="60..86400", actual=recovery_timeout)
    max_item_failures = _as_int(_get(summary, "manual_sync.failure_budget.max_item_failures", 0))
    max_consecutive_failures = _as_int(_get(summary, "manual_sync.failure_budget.max_consecutive_failures", 0))
    max_failure_rate = _as_float(_get(summary, "manual_sync.failure_budget.max_failure_rate", 0.0))
    failure_duration = _as_int(_get(summary, "manual_sync.failure_budget.max_duration_seconds", 0))
    if not (1 <= max_item_failures <= 1000):
        result.fail("s3a_m1_failure_budget_count_invalid", "S3A-M1 failure budget count must be present and bounded.", path="manual_sync.failure_budget.max_item_failures", expected="1..1000", actual=max_item_failures)
    if not (1 <= max_consecutive_failures <= max_item_failures):
        result.fail("s3a_m1_consecutive_failure_budget_invalid", "S3A-M1 consecutive failure cap must be present and no higher than the item failure budget.", path="manual_sync.failure_budget.max_consecutive_failures", expected="1..max_item_failures", actual=max_consecutive_failures)
    if not (0.0 < max_failure_rate <= 1.0):
        result.fail("s3a_m1_failure_rate_invalid", "S3A-M1 failure rate cap must be a positive fraction.", path="manual_sync.failure_budget.max_failure_rate", expected="0..1", actual=max_failure_rate)
    if not (1 <= failure_duration <= 3600):
        result.fail("s3a_m1_failure_duration_invalid", "S3A-M1 duration stop budget must stay bounded.", path="manual_sync.failure_budget.max_duration_seconds", expected="1..3600", actual=failure_duration)
    localization_reason = str(_get(summary, "manual_sync.localization.blocked_reason", "") or "").strip()
    if not localization_reason:
        result.fail("s3a_m1_localization_block_reason_missing", "S3A-M1 localization state must report why scheduling is blocked.", path="manual_sync.localization.blocked_reason", expected="non-empty reason", actual=localization_reason)
    model_uncached_reason = str(_get(summary, "manual_sync.ai_tagging.model_uncached_reason", "") or "")
    file_missing_reason = str(_get(summary, "manual_sync.ai_tagging.file_missing_reason", "") or "")
    inference_reason = str(_get(summary, "manual_sync.ai_tagging.inference_failure_reason", "") or "")
    if model_uncached_reason != "ai_tagger_model_uncached":
        result.fail("s3a_m1_ai_model_uncached_reason_invalid", "S3A-M1 must report the stable AI model cache failure reason.", path="manual_sync.ai_tagging.model_uncached_reason", expected="ai_tagger_model_uncached", actual=model_uncached_reason)
    if file_missing_reason != "ai_tagger_file_missing":
        result.fail("s3a_m1_ai_file_missing_reason_invalid", "S3A-M1 must report the stable AI file-missing failure reason.", path="manual_sync.ai_tagging.file_missing_reason", expected="ai_tagger_file_missing", actual=file_missing_reason)
    if inference_reason != "ai_tagger_inference_failed":
        result.fail("s3a_m1_ai_inference_reason_invalid", "S3A-M1 must report the stable AI inference failure reason.", path="manual_sync.ai_tagging.inference_failure_reason", expected="ai_tagger_inference_failed", actual=inference_reason)
    if _as_bool(_get(summary, "manual_sync.ai_tagging.raw_error_details_public", False)):
        result.fail("s3a_m1_ai_raw_error_public", "S3A-M1 public status/reporting must not expose raw AI tagging error details.", path="manual_sync.ai_tagging.raw_error_details_public", expected=False, actual=True)
    ai_tags_unavailable_reason = str(_get(summary, "manual_sync.classification.ai_tags_unavailable_reason", "") or "")
    ai_tagging_failed_reason = str(_get(summary, "manual_sync.classification.ai_tagging_failed_reason", "") or "")
    if ai_tags_unavailable_reason != "classification_deferred_ai_tags_unavailable":
        result.fail(
            "s3a_m1_heuristic_ai_tags_unavailable_reason_invalid",
            "S3A-M1 heuristic classification must report a stable defer reason when fresh AI tags are unavailable.",
            path="manual_sync.classification.ai_tags_unavailable_reason",
            expected="classification_deferred_ai_tags_unavailable",
            actual=ai_tags_unavailable_reason,
        )
    if ai_tagging_failed_reason != "classification_skipped_ai_tagging_failed":
        result.fail(
            "s3a_m1_heuristic_ai_tagging_failed_reason_invalid",
            "S3A-M1 heuristic classification must report a stable skip reason when AI tagging failed.",
            path="manual_sync.classification.ai_tagging_failed_reason",
            expected="classification_skipped_ai_tagging_failed",
            actual=ai_tagging_failed_reason,
        )

    phrase = str(_get(summary, "manual_sync.confirmation_phrase_prefix", "") or "")
    production_phrase = str(_get(summary, "manual_sync.production_confirmation_phrase_prefix", "") or "")
    if phrase != "I APPROVE S3A-M1 MANUAL SYNC EXECUTE":
        result.fail("s3a_m1_confirmation_prefix_invalid", "Manual confirmation prefix changed unexpectedly.", path="manual_sync.confirmation_phrase_prefix", expected="I APPROVE S3A-M1 MANUAL SYNC EXECUTE", actual=phrase)
    if production_phrase != "I APPROVE S3A-M1 PRODUCTION MANUAL SYNC EXECUTE":
        result.fail("s3a_m1_production_confirmation_prefix_invalid", "Production confirmation prefix changed unexpectedly.", path="manual_sync.production_confirmation_phrase_prefix", expected="I APPROVE S3A-M1 PRODUCTION MANUAL SYNC EXECUTE", actual=production_phrase)

    markdown_text = _read_s3a_m1_markdown_report(summary, result)
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s3a_m1_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s3a_m1_public_payload_redaction_failed",
            "S3A-M1 contract independently found forbidden public JSON or Markdown content.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )


def _check_s3a_pilot1_new_data_directml_chain(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "target_met_dry_run_only",
        "target_met_with_bounded_write",
        "write_executed_but_first_time_insertion_unproven",
        "blocked_input_over_cap",
        "blocked_scope_invalid",
        "blocked_no_media",
        "blocked_model_cache_missing",
        "blocked_model_download_allowed",
        "blocked_import_write_prerequisites",
        "blocked_import_requested_without_exact_confirmation",
        "blocked_ai_tagging_requested_without_exact_confirmation",
        "blocked_import_item_failures",
        "blocked_classification_failures",
        "blocked_ai_tagging_item_failures",
        "blocked_ai_tagging_write_not_executed",
        "blocked_directml_provider_not_validated",
        "blocked_cpu_fallback_not_validated",
        "blocked_public_redaction_failed",
    }
    target_statuses = {"target_met_dry_run_only", "target_met_with_bounded_write"}
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s3a_pilot1_unknown_status",
            "S3A-PILOT1 status must explicitly describe completion or the blocking gate.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status not in target_statuses and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_pilot1_non_completion_status_claimed_completion",
            "Blocked S3A-PILOT1 summaries must not claim target_met, full_chain_complete, or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met status for completion claims",
            actual=result.status,
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "run_configuration.local_files_only",
            "scope.no_full_library_fallback",
            "model_cache.local_files_only",
            "import_reuse.reported",
            "classification.reported",
            "directml_ai_tagging.reported",
            "cpu_fallback_validation.reported",
            "localization.reported",
            "s3a_boundary.operator_triggered_pilot_only",
            "safety.max_items_lte_5",
            "safety.selected_input_explicit_bounded",
            "safety.no_full_library_run",
        ),
        code="s3a_pilot1_required_proof_missing",
        message="S3A-PILOT1 requires explicit bounded input, local-only model loading, staged results, S3A boundary proof, and public redaction proof.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "run_configuration.model_download_allowed",
            "run_configuration.s3a_production_execution_enabled",
            "run_configuration.unattended_s3b_enabled",
            "scope.private_locator_values_recorded",
            "s3a_boundary.production_execution_enabled",
            "s3a_boundary.unattended_enabled",
            "s3a_boundary.scheduled_automation_enabled",
            "s3a_boundary.broad_production_sync_enabled",
            "safety.import_write_without_confirmation",
            "safety.ai_tagging_write_without_confirmation",
            "safety.production_s3a_execution_enabled",
            "safety.unattended_s3b_enabled",
            "safety.scheduled_automation_enabled",
            "safety.broad_production_sync_enabled",
            "safety.provider_pixiv_gallery_dl_saucenao_google_calls",
            "safety.sourceconcept_r1_r2_r1r",
            "safety.entity_bridge",
            "safety.confirmed_entity_assignments",
            "safety.desired_media_backfill",
            "safety.cleanup_delete_reset_drop_truncate",
            "safety.source_icloud_mutation",
            "safety.model_download",
            "safety.private_locator_values_recorded",
            "safety.external_llm_provider_used",
            "localization.llm_external_provider_used",
        ),
        code="s3a_pilot1_forbidden_safety_flag",
        message="S3A-PILOT1 summaries must explicitly keep forbidden automation, provider, entity, destructive, model-download, and privacy paths disabled.",
    )

    max_items = _as_int(_get(summary, "run_configuration.max_items", 0))
    selected_count = _as_int(_get(summary, "scope.selected_count", 0))
    over_cap = _as_int(_get(summary, "scope.over_cap_count", 0))
    input_mode = str(_get(summary, "run_configuration.input_mode", "") or "")
    if input_mode not in {"input_path", "media_ids"}:
        result.fail(
            "s3a_pilot1_input_mode_invalid",
            "S3A-PILOT1 must use an explicit input path or explicit media IDs.",
            path="run_configuration.input_mode",
            expected=["input_path", "media_ids"],
            actual=input_mode,
        )
    if not (1 <= max_items <= 5):
        result.fail(
            "s3a_pilot1_max_items_unbounded",
            "S3A-PILOT1 max_items must stay between 1 and 5.",
            path="run_configuration.max_items",
            expected="1..5",
            actual=max_items,
        )
    if not over_cap and not (1 <= selected_count <= max_items <= 5):
        result.fail(
            "s3a_pilot1_selected_sample_not_small",
            "S3A-PILOT1 selected sample count must be non-zero and within max_items <= 5.",
            path="scope.selected_count",
            expected=f"1..{max_items}",
            actual=selected_count,
        )
    if over_cap:
        result.fail(
            "s3a_pilot1_input_over_cap",
            "S3A-PILOT1 input must be visibly bounded and should not hide extra supported files behind max_items truncation.",
            path="scope.over_cap_count",
            expected=0,
            actual=over_cap,
        )
        if status in target_statuses:
            result.fail(
                "s3a_pilot1_target_claimed_with_over_cap_input",
                "S3A-PILOT1 must block over-cap input before target claims.",
                path="pipeline_contract.status",
                expected="blocked_input_over_cap",
                actual=result.status,
            )

    import_write_requested = _as_bool(_get(summary, "run_configuration.import_write_requested", False))
    import_confirmed = _as_bool(_get(summary, "run_configuration.import_confirmation_exact", False))
    import_executed = _as_bool(_get(summary, "import_reuse.executed", False))
    import_preconditions_passed = _as_bool(_get(summary, "import_write_preconditions.passed", False))
    ai_write_requested = _as_bool(_get(summary, "run_configuration.ai_tagging_write_requested", False))
    ai_confirmed = _as_bool(_get(summary, "run_configuration.ai_tagging_confirmation_exact", False))
    ai_executed = _as_bool(_get(summary, "directml_ai_tagging.executed", False))
    ai_dry_run = _as_bool(_get(summary, "directml_ai_tagging.dry_run", True))
    if import_write_requested and not import_confirmed and status != "blocked_import_requested_without_exact_confirmation":
        result.fail(
            "s3a_pilot1_import_requested_without_exact_confirmation_not_blocked",
            "An import write request without the exact operator confirmation must block.",
            path="pipeline_contract.status",
            expected="blocked_import_requested_without_exact_confirmation",
            actual=result.status,
        )
    if import_write_requested and import_confirmed and not import_preconditions_passed and status not in {
        "blocked_import_write_prerequisites",
        "blocked_model_cache_missing",
        "blocked_model_download_allowed",
        "blocked_scope_invalid",
        "blocked_input_over_cap",
    }:
        result.fail(
            "s3a_pilot1_import_prerequisites_not_blocked",
            "Import writes must block unless model cache, local-files-only, scope, over-cap, and confirmation gates all pass before execution.",
            path="pipeline_contract.status",
            expected="blocked import precondition status",
            actual={
                "status": result.status,
                "preconditions_passed": import_preconditions_passed,
                "blockers": _get(summary, "import_write_preconditions.blockers", None),
            },
        )
    if import_executed and not import_preconditions_passed:
        result.fail(
            "s3a_pilot1_import_write_executed_before_prerequisites",
            "Import write execution must not happen before all pre-write prerequisites pass.",
            path="import_write_preconditions.passed",
            expected=True,
            actual=False,
        )
    if import_executed and not import_confirmed:
        result.fail(
            "s3a_pilot1_import_write_without_exact_confirmation",
            "Tiny import writes require the exact S3A-PILOT1 import confirmation string.",
            path="run_configuration.import_confirmation_exact",
            expected=True,
            actual=False,
        )
    if ai_write_requested and not ai_confirmed and status != "blocked_ai_tagging_requested_without_exact_confirmation":
        result.fail(
            "s3a_pilot1_ai_requested_without_exact_confirmation_not_blocked",
            "An AI tagging write request without the exact operator confirmation must block.",
            path="pipeline_contract.status",
            expected="blocked_ai_tagging_requested_without_exact_confirmation",
            actual=result.status,
        )
    if ai_executed and not ai_dry_run and not ai_confirmed:
        result.fail(
            "s3a_pilot1_ai_write_without_exact_confirmation",
            "DirectML AI tagging writes require the exact S3A-PILOT1 AI tagging confirmation string.",
            path="run_configuration.ai_tagging_confirmation_exact",
            expected=True,
            actual=False,
        )

    imported = _as_int(_get(summary, "import_reuse.imported_count", 0))
    reused = _as_int(_get(summary, "import_reuse.reused_count", 0))
    would_import = _as_int(_get(summary, "import_reuse.would_import_count", 0))
    skipped = _as_int(_get(summary, "import_reuse.skipped_count", 0))
    import_failed = _as_int(_get(summary, "import_reuse.failed_count", 0))
    downstream = _as_int(_get(summary, "import_reuse.downstream_media_count", 0))
    if imported + reused + would_import + skipped + import_failed < selected_count:
        result.fail(
            "s3a_pilot1_import_reuse_counts_under_reported",
            "Import/reuse counts must account for the selected tiny sample.",
            path="import_reuse",
            expected=f">={selected_count} accounted items",
            actual={
                "imported": imported,
                "reused": reused,
                "would_import": would_import,
                "skipped": skipped,
                "failed": import_failed,
            },
        )
    if import_failed and status != "blocked_import_item_failures":
        result.fail(
            "s3a_pilot1_import_failures_not_blocked",
            "Import/reuse item failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_import_item_failures",
            actual=result.status,
        )
    if downstream <= 0 and status in target_statuses:
        result.fail(
            "s3a_pilot1_target_without_downstream_media",
            "S3A-PILOT1 target_met requires at least one imported or reused downstream media item.",
            path="import_reuse.downstream_media_count",
            expected=">=1",
            actual=downstream,
        )

    classification_failed = _as_int(_get(summary, "classification.failed_count", 0))
    classification_executed = _as_bool(_get(summary, "classification.executed", False))
    if classification_failed and status != "blocked_classification_failures":
        result.fail(
            "s3a_pilot1_classification_failures_not_blocked",
            "Classification failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_classification_failures",
            actual=result.status,
        )
    if status in target_statuses and not classification_executed:
        result.fail(
            "s3a_pilot1_target_without_classification",
            "S3A-PILOT1 target_met requires a reported classification validation for downstream media.",
            path="classification.executed",
            expected=True,
            actual=classification_executed,
        )

    requested = _get(summary, "directml_ai_tagging.provider_preference_requested", [])
    provider = _get(summary, "directml_ai_tagging.provider", {})
    actual_provider = _get(summary, "directml_ai_tagging.provider.actual_provider", None)
    ai_failed = _as_int(_get(summary, "directml_ai_tagging.failed", 0))
    ai_processed = _as_int(_get(summary, "directml_ai_tagging.processed", 0))
    if ai_failed and status != "blocked_ai_tagging_item_failures":
        result.fail(
            "s3a_pilot1_ai_failures_not_blocked",
            "AI tagging item failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_ai_tagging_item_failures",
            actual=result.status,
        )
    if status in target_statuses:
        if not ai_executed or ai_processed != downstream:
            result.fail(
                "s3a_pilot1_target_without_ai_scope_match",
                "S3A-PILOT1 target_met requires DirectML AI tagging validation over the downstream media scope.",
                path="directml_ai_tagging.processed",
                expected=downstream,
                actual=ai_processed,
            )
        if not isinstance(requested, list) or not requested:
            result.fail(
                "s3a_pilot1_provider_preference_missing",
                "DirectML AI tagging validation must record requested provider preference.",
                path="directml_ai_tagging.provider_preference_requested",
                expected="non-empty list",
                actual=requested,
            )
        if not isinstance(actual_provider, str) or not actual_provider.strip():
            result.fail(
                "s3a_pilot1_actual_provider_missing",
                "DirectML AI tagging validation must report the actual ONNX provider loaded.",
                path="directml_ai_tagging.provider.actual_provider",
                expected="non-empty provider",
                actual=actual_provider,
            )
        elif isinstance(requested, list) and requested and actual_provider not in requested:
            result.fail(
                "s3a_pilot1_actual_provider_not_requested",
                "Primary actual provider must come from the requested bounded provider preference.",
                path="directml_ai_tagging.provider.actual_provider",
                expected=requested,
                actual=actual_provider,
            )
        if isinstance(requested, list) and "DmlExecutionProvider" in requested and actual_provider != "DmlExecutionProvider":
            fallback_occurred = _as_bool(_get(summary, "directml_ai_tagging.provider.fallback_occurred", False))
            fallback_reason = str(_get(summary, "directml_ai_tagging.provider.fallback_reason", "") or "").strip()
            load_errors = _get(summary, "directml_ai_tagging.provider.provider_load_errors", [])
            if not (fallback_occurred and (fallback_reason or load_errors)):
                result.fail(
                    "s3a_pilot1_directml_missing_without_blocker",
                    "If DirectML is requested but not loaded, the summary must report explicit fallback/blocker evidence.",
                    path="directml_ai_tagging.provider",
                    expected="DmlExecutionProvider or fallback/blocker",
                    actual=provider,
                )

    ai_delta = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_delta", 0))
    ai_first_time = _as_bool(_get(summary, "directml_ai_tagging.first_time_media_tag_insertion_proven", False))
    if status == "target_met_dry_run_only" and ai_delta != 0:
        result.fail(
            "s3a_pilot1_dry_run_media_tags_delta",
            "Dry-run AI tagging validation must not write media_tags.",
            path="directml_ai_tagging.media_tags_count_delta",
            expected=0,
            actual=ai_delta,
        )
    if status == "target_met_dry_run_only" and ai_first_time:
        result.fail(
            "s3a_pilot1_dry_run_claims_first_time_insertion",
            "Dry-run target summaries must not claim first-time media_tags insertion proof.",
            path="directml_ai_tagging.first_time_media_tag_insertion_proven",
            expected=False,
            actual=True,
        )
    if status == "target_met_with_bounded_write":
        accepted_fallback = _as_bool(_get(summary, "directml_ai_tagging.provider.explicit_accepted_fallback", False))
        if not ai_write_requested:
            result.fail(
                "s3a_pilot1_write_target_without_ai_write_request",
                "target_met_with_bounded_write requires an explicit AI tagging write request.",
                path="run_configuration.ai_tagging_write_requested",
                expected=True,
                actual=False,
            )
        if not ai_confirmed:
            result.fail(
                "s3a_pilot1_write_target_without_ai_confirmation",
                "target_met_with_bounded_write requires the exact DirectML AI tagging confirmation.",
                path="run_configuration.ai_tagging_confirmation_exact",
                expected=True,
                actual=False,
            )
        if ai_dry_run:
            result.fail(
                "s3a_pilot1_write_target_ai_still_dry_run",
                "target_met_with_bounded_write requires directml_ai_tagging.dry_run=false.",
                path="directml_ai_tagging.dry_run",
                expected=False,
                actual=True,
            )
        if ai_failed != 0:
            result.fail(
                "s3a_pilot1_write_target_ai_failures",
                "target_met_with_bounded_write requires directml_ai_tagging.failed=0.",
                path="directml_ai_tagging.failed",
                expected=0,
                actual=ai_failed,
            )
        if actual_provider != "DmlExecutionProvider" and not accepted_fallback:
            result.fail(
                "s3a_pilot1_write_target_without_directml_or_accepted_fallback",
                "target_met_with_bounded_write requires DmlExecutionProvider or an explicit accepted fallback.",
                path="directml_ai_tagging.provider.actual_provider",
                expected="DmlExecutionProvider or explicit accepted fallback",
                actual=actual_provider,
            )
        if ai_delta <= 0 and not ai_first_time:
            result.fail(
                "s3a_pilot1_write_target_without_first_time_media_tags",
                "target_met_with_bounded_write requires positive media_tags delta or explicit first-time insertion proof.",
                path="directml_ai_tagging.media_tags_count_delta",
                expected=">0 or first_time_media_tag_insertion_proven=true",
                actual={"media_tags_count_delta": ai_delta, "first_time_media_tag_insertion_proven": ai_first_time},
            )
        if input_mode == "input_path" and import_write_requested and imported <= 0:
            result.fail(
                "s3a_pilot1_write_target_without_imported_input",
                "Input-path write targets should prove at least one newly imported staged item.",
                path="import_reuse.imported_count",
                expected=">=1",
                actual=imported,
            )

    cpu_executed = _as_bool(_get(summary, "cpu_fallback_validation.executed", False))
    cpu_status = str(_get(summary, "cpu_fallback_validation.status", "") or "").casefold()
    cpu_failed = _as_int(_get(summary, "cpu_fallback_validation.failed", 0))
    cpu_dry_run = _as_bool(_get(summary, "cpu_fallback_validation.dry_run", False))
    cpu_actual = _get(summary, "cpu_fallback_validation.provider.actual_provider", None)
    cpu_delta = _as_int(_get(summary, "cpu_fallback_validation.media_tags_count_delta", 0))
    if status in target_statuses:
        if not cpu_executed:
            result.fail(
                "s3a_pilot1_target_without_cpu_fallback",
                "S3A-PILOT1 target_met requires CPU fallback validation or smoke proof.",
                path="cpu_fallback_validation.executed",
                expected=True,
                actual=cpu_executed,
            )
        if cpu_status != "completed":
            result.fail(
                "s3a_pilot1_cpu_fallback_status_invalid",
                "CPU fallback validation must complete successfully before any target status.",
                path="cpu_fallback_validation.status",
                expected="completed",
                actual=_get(summary, "cpu_fallback_validation.status", None),
            )
        if cpu_failed != 0:
            result.fail(
                "s3a_pilot1_cpu_fallback_failed",
                "CPU fallback validation must report failed=0 before any target status.",
                path="cpu_fallback_validation.failed",
                expected=0,
                actual=cpu_failed,
            )
        if not cpu_dry_run:
            result.fail(
                "s3a_pilot1_cpu_fallback_not_dry_run",
                "CPU fallback validation must remain dry-run.",
                path="cpu_fallback_validation.dry_run",
                expected=True,
                actual=cpu_dry_run,
            )
        if cpu_actual != "CPUExecutionProvider":
            result.fail(
                "s3a_pilot1_cpu_fallback_actual_provider_invalid",
                "CPU fallback validation must force and load CPUExecutionProvider.",
                path="cpu_fallback_validation.provider.actual_provider",
                expected="CPUExecutionProvider",
                actual=cpu_actual,
            )
        if cpu_delta != 0:
            result.fail(
                "s3a_pilot1_cpu_fallback_media_tags_delta",
                "CPU fallback validation must not write media_tags.",
                path="cpu_fallback_validation.media_tags_count_delta",
                expected=0,
                actual=cpu_delta,
            )
    if status == "blocked_cpu_fallback_not_validated" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_pilot1_cpu_fallback_blocker_claimed_completion",
            "CPU fallback failure must clear target/safe_to_merge claims.",
            path="pipeline_contract.claims",
            expected="all false",
            actual=_get(summary, "pipeline_contract.claims", None),
        )

    localization_failed = _as_int(_get(summary, "localization.failed", 0))
    if localization_failed:
        result.fail(
            "s3a_pilot1_localization_failed",
            "Localization validation must report zero failures or defer without external provider use.",
            path="localization.failed",
            expected=0,
            actual=localization_failed,
        )

    markdown_text = _read_s3a_pilot1_markdown_report(summary, result)
    redaction_passed = _as_bool(_get(summary, "public_redaction.passed", False))
    if not redaction_passed and status in target_statuses:
        result.fail(
            "s3a_pilot1_target_with_failed_public_redaction",
            "Public redaction failure must clear target/safe_to_merge claims and block the run.",
            path="public_redaction.passed",
            expected=True,
            actual=_get(summary, "public_redaction.passed", None),
        )
    if status == "blocked_public_redaction_failed" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_pilot1_redaction_blocker_claimed_completion",
            "blocked_public_redaction_failed must not claim target_met, full_chain_complete, or safe_to_merge.",
            path="pipeline_contract.claims",
            expected="all false",
            actual=_get(summary, "pipeline_contract.claims", None),
        )
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s3a_pilot1_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s3a_pilot1_public_payload_redaction_failed",
            "S3A-PILOT1 contract independently found forbidden public JSON or Markdown content.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )


def _check_s3a_prod1_operator_incremental_sync(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "preflight_completed_write_confirmation_required",
        "target_met_with_bounded_write",
        "blocked_input_over_cap",
        "blocked_scope_invalid",
        "blocked_no_media",
        "blocked_model_cache_missing",
        "blocked_model_download_allowed",
        "blocked_provider_preference_invalid",
        "blocked_protected_input_path",
        "blocked_source_safety_gate",
        "blocked_directml_unavailable",
        "blocked_cpu_fallback_unavailable",
        "blocked_import_write_prerequisites",
        "blocked_production_write_requested_without_exact_confirmation",
        "blocked_import_item_failures",
        "blocked_classification_failures",
        "blocked_ai_tagging_item_failures",
        "blocked_ai_tagging_write_not_executed",
        "blocked_directml_provider_not_validated",
        "blocked_cpu_fallback_not_validated",
        "blocked_localization_failures",
        "blocked_public_redaction_failed",
        "blocked_db_unavailable",
        "write_executed_but_first_time_insertion_unproven",
    }
    target_statuses = {"target_met_with_bounded_write"}
    structural_blocker_statuses = {
        "blocked_input_over_cap",
        "blocked_scope_invalid",
        "blocked_model_cache_missing",
        "blocked_model_download_allowed",
        "blocked_provider_preference_invalid",
        "blocked_protected_input_path",
        "blocked_source_safety_gate",
        "blocked_directml_unavailable",
        "blocked_cpu_fallback_unavailable",
        "blocked_db_unavailable",
        "blocked_import_write_prerequisites",
    }
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s3a_prod1_unknown_status",
            "S3A-PROD1 status must explicitly describe completion, preflight, or the blocking gate.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status not in target_statuses and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_prod1_non_completion_status_claimed_completion",
            "Blocked or preflight-only S3A-PROD1 summaries must not claim target_met, full_chain_complete, or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met status for completion claims",
            actual=result.status,
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "run_configuration.local_files_only",
            "run_configuration.single_operator_triggered_run_only",
            "run_configuration.no_full_library_fallback",
            "scope.explicit_input_path_supplied",
            "scope.no_full_library_fallback",
            "scope.protected_input_gate.reported",
            "scope.source_safety_gate.reported",
            "model_cache.local_files_only",
            "preflight.provider_availability.reported",
            "preflight.protected_input_gate.reported",
            "preflight.source_safety_gate.reported",
            "preflight.no_full_library_fallback",
            "preflight.public_private_path_redaction.absolute_paths_redacted",
            "preflight.public_private_path_redaction.file_labels_redacted",
            "import_reuse.reported",
            "classification.reported",
            "directml_ai_tagging.reported",
            "cpu_fallback_validation.reported",
            "localization.reported",
            "s3a_boundary.single_operator_triggered_run_only",
            "safety.max_items_lte_5",
            "safety.explicit_input_required",
            "safety.no_full_library_run",
            "safety.single_operator_triggered_run_only",
        ),
        code="s3a_prod1_required_proof_missing",
        message="S3A-PROD1 requires explicit bounded input, local-only model loading, staged results, operator-run boundary proof, and public redaction proof.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "run_configuration.model_download_allowed",
            "run_configuration.production_automation_enabled",
            "run_configuration.scheduled_automation_enabled",
            "run_configuration.unattended_s3b_enabled",
            "scope.private_locator_values_recorded",
            "scope.source_safety_gate.read_probe_used",
            "scope.source_safety_gate.hydration_policy_enabled",
            "s3a_boundary.production_automation_enabled",
            "s3a_boundary.scheduled_automation_enabled",
            "s3a_boundary.unattended_s3b_enabled",
            "s3a_boundary.broad_production_sync_enabled",
            "s3a_boundary.full_library_fallback_enabled",
            "safety.production_write_without_confirmation",
            "safety.import_write_without_confirmation",
            "safety.ai_tagging_write_without_confirmation",
            "safety.production_automation_enabled",
            "safety.unattended_s3b_enabled",
            "safety.scheduled_automation_enabled",
            "safety.broad_production_sync_enabled",
            "safety.provider_pixiv_gallery_dl_saucenao_google_calls",
            "safety.sourceconcept_r1_r2_r1r",
            "safety.entity_bridge",
            "safety.confirmed_entity_assignments",
            "safety.desired_media_backfill",
            "safety.cleanup_delete_reset_drop_truncate",
            "safety.source_icloud_mutation",
            "safety.cloud_hydration_or_recall_triggered",
            "safety.model_download",
            "safety.private_locator_values_recorded",
            "safety.external_llm_provider_used",
            "localization.llm_external_provider_used",
            "localization.external_provider_used",
        ),
        code="s3a_prod1_forbidden_safety_flag",
        message="S3A-PROD1 summaries must explicitly keep scheduled automation, unattended S3B, provider, entity, destructive, model-download, and privacy paths disabled.",
    )

    input_mode = str(_get(summary, "run_configuration.input_mode", "") or "")
    if input_mode != "input_path":
        result.fail(
            "s3a_prod1_input_mode_invalid",
            "S3A-PROD1 must use explicit --input-path only.",
            path="run_configuration.input_mode",
            expected="input_path",
            actual=input_mode,
        )
    if _get(summary, "preflight.input_mode", None) != "input_path":
        result.fail(
            "s3a_prod1_preflight_input_mode_invalid",
            "S3A-PROD1 preflight must report input_path mode.",
            path="preflight.input_mode",
            expected="input_path",
            actual=_get(summary, "preflight.input_mode", None),
        )

    production_write_requested = _as_bool(_get(summary, "run_configuration.production_write_requested", False))
    exact_confirmation = _as_bool(_get(summary, "run_configuration.exact_production_sync_confirmation", False))
    import_executed = _as_bool(_get(summary, "import_reuse.executed", False))
    ai_executed = _as_bool(_get(summary, "directml_ai_tagging.executed", False))
    ai_dry_run = _as_bool(_get(summary, "directml_ai_tagging.dry_run", True))

    max_items = _as_int(_get(summary, "run_configuration.max_items", 0))
    selected_count = _as_int(_get(summary, "scope.selected_count", 0))
    over_cap = _as_int(_get(summary, "scope.over_cap_count", 0))
    protected_blocked = _as_int(_get(summary, "scope.protected_input_gate.blocked_count", 0))
    source_blocked = _as_int(_get(summary, "scope.source_safety_gate.blocked_count", 0))
    protected_passed = _as_bool(_get(summary, "scope.protected_input_gate.passed", False))
    source_safety_passed = _as_bool(_get(summary, "scope.source_safety_gate.passed", False))
    if not (1 <= max_items <= 5):
        result.fail(
            "s3a_prod1_max_items_unbounded",
            "S3A-PROD1 max_items must stay between 1 and 5.",
            path="run_configuration.max_items",
            expected="1..5",
            actual=max_items,
        )
    if not over_cap and selected_count <= 0:
        if status not in {"blocked_scope_invalid", "blocked_protected_input_path", "blocked_source_safety_gate"}:
            result.fail(
                "s3a_prod1_selected_sample_not_small",
                "S3A-PROD1 selected input count must be non-zero unless the report is fail-closed on invalid scope.",
                path="scope.selected_count",
                expected=f"1..{max_items} or input/safety blocked status",
                actual=selected_count,
            )
    elif not over_cap and not (selected_count <= max_items <= 5):
        result.fail(
            "s3a_prod1_selected_sample_not_small",
            "S3A-PROD1 selected input count must be non-zero and within max_items <= 5.",
            path="scope.selected_count",
            expected=f"1..{max_items}",
            actual=selected_count,
        )
    if over_cap:
        if status != "blocked_input_over_cap":
            result.fail(
                "s3a_prod1_input_over_cap",
                "S3A-PROD1 input must block over-cap supported files before selection.",
                path="scope.over_cap_count",
                expected="0 or blocked_input_over_cap",
                actual=over_cap,
            )
        if status in target_statuses:
            result.fail(
                "s3a_prod1_target_claimed_with_over_cap_input",
                "S3A-PROD1 must block over-cap input before target claims.",
                path="pipeline_contract.status",
                expected="blocked_input_over_cap",
                actual=result.status,
            )
    if protected_blocked:
        if status != "blocked_protected_input_path":
            result.fail(
                "s3a_prod1_protected_input_not_blocked",
                "Inputs inside app-managed/protected roots must fail closed before import.",
                path="pipeline_contract.status",
                expected="blocked_protected_input_path",
                actual=result.status,
            )
        if import_executed or (ai_executed and not ai_dry_run):
            result.fail(
                "s3a_prod1_protected_input_claims_writes",
                "Protected input blocked reports must not claim import or media_tags writes.",
                path="scope.protected_input_gate.blocked_count",
                expected={"blocked_count": protected_blocked, "writes": False},
                actual={"import_executed": import_executed, "ai_write_executed": ai_executed and not ai_dry_run},
            )
    if source_blocked:
        if status != "blocked_source_safety_gate" and not (protected_blocked and status == "blocked_protected_input_path"):
            result.fail(
                "s3a_prod1_source_safety_not_blocked",
                "Cloud/placeholder/unreadable/zero-byte source safety failures must block before import.",
                path="pipeline_contract.status",
                expected="blocked_source_safety_gate",
                actual=result.status,
            )
        if import_executed or (ai_executed and not ai_dry_run):
            result.fail(
                "s3a_prod1_source_safety_blocked_claims_writes",
                "Source-safety blocked reports must not claim import or media_tags writes.",
                path="scope.source_safety_gate.blocked_count",
                expected={"blocked_count": source_blocked, "writes": False},
                actual={"import_executed": import_executed, "ai_write_executed": ai_executed and not ai_dry_run},
            )
    if status in target_statuses and not _as_bool(_get(summary, "safety.selected_input_explicit_bounded", False)):
        result.fail(
            "s3a_prod1_target_without_selected_input_proof",
            "S3A-PROD1 target_met requires selected explicit bounded input proof.",
            path="safety.selected_input_explicit_bounded",
            expected=True,
            actual=_get(summary, "safety.selected_input_explicit_bounded", None),
        )
    if status in target_statuses and (not protected_passed or not source_safety_passed):
        result.fail(
            "s3a_prod1_target_without_source_safety_gates",
            "S3A-PROD1 target_met requires protected input and source safety gates to pass.",
            path="scope.source_safety_gate.passed",
            expected={"protected_input_gate.passed": True, "source_safety_gate.passed": True},
            actual={"protected_input_gate.passed": protected_passed, "source_safety_gate.passed": source_safety_passed},
        )

    directml_available = _as_bool(_get(summary, "preflight.directml_available", False))
    cpu_available = _as_bool(_get(summary, "preflight.cpu_fallback_available", False))
    model_status = str(_get(summary, "model_cache.status", "") or "")
    model_local_only = _as_bool(_get(summary, "model_cache.local_files_only", False))
    model_download_allowed = _as_bool(_get(summary, "model_cache.model_download_allowed", False))
    model_download_performed = _as_bool(_get(summary, "model_cache.model_download_performed", False))
    provider_preference_requested = _get(summary, "run_configuration.provider_preference_requested", [])
    provider_preference_valid = provider_preference_requested == ["DmlExecutionProvider", "CPUExecutionProvider"]
    if production_write_requested and exact_confirmation and not provider_preference_valid and status != "blocked_provider_preference_invalid":
        result.fail(
            "s3a_prod1_provider_preference_invalid_not_blocked",
            "S3A-PROD1 production writes require DmlExecutionProvider,CPUExecutionProvider before import or AI writes.",
            path="pipeline_contract.status",
            expected="blocked_provider_preference_invalid",
            actual=result.status,
        )
    if status in target_statuses:
        if not provider_preference_valid:
            result.fail(
                "s3a_prod1_target_provider_preference_not_bounded",
                "S3A-PROD1 target_met requires the bounded DirectML+CPU provider preference.",
                path="run_configuration.provider_preference_requested",
                expected=["DmlExecutionProvider", "CPUExecutionProvider"],
                actual=provider_preference_requested,
            )
        if model_status != "cached" or not model_local_only or model_download_allowed or model_download_performed:
            result.fail(
                "s3a_prod1_target_without_cached_model_proof",
                "S3A-PROD1 target_met requires cached local-only model proof and no download.",
                path="model_cache",
                expected={
                    "status": "cached",
                    "local_files_only": True,
                    "model_download_allowed": False,
                    "model_download_performed": False,
                },
                actual={
                    "status": model_status,
                    "local_files_only": model_local_only,
                    "model_download_allowed": model_download_allowed,
                    "model_download_performed": model_download_performed,
                },
            )
    if not directml_available and status in target_statuses:
        result.fail(
            "s3a_prod1_target_without_directml_available",
            "S3A-PROD1 target_met requires DirectML availability in preflight.",
            path="preflight.directml_available",
            expected=True,
            actual=directml_available,
        )
    if not cpu_available and status in target_statuses:
        result.fail(
            "s3a_prod1_target_without_cpu_fallback_available",
            "S3A-PROD1 target_met requires CPU fallback availability in preflight.",
            path="preflight.cpu_fallback_available",
            expected=True,
            actual=cpu_available,
        )

    db_available = _as_bool(_get(summary, "db_session.available", True))
    if not db_available and status not in structural_blocker_statuses:
        result.fail(
            "s3a_prod1_db_unavailable_not_blocked",
            "A DB-unavailable S3A-PROD1 report must fail closed with a structural blocked status.",
            path="pipeline_contract.status",
            expected=sorted(structural_blocker_statuses),
            actual=result.status,
        )

    if not db_available and (import_executed or (ai_executed and not ai_dry_run)):
        result.fail(
            "s3a_prod1_db_unavailable_report_claims_writes",
            "blocked_db_unavailable reports must not claim import or media_tags writes.",
            path="db_session.available",
            expected={"available": False, "writes": False},
            actual={"import_executed": import_executed, "ai_write_executed": ai_executed and not ai_dry_run},
        )
    if production_write_requested and not exact_confirmation and status != "blocked_production_write_requested_without_exact_confirmation":
        result.fail(
            "s3a_prod1_write_requested_without_exact_confirmation_not_blocked",
            "A production sync write request without the exact operator confirmation must block.",
            path="pipeline_contract.status",
            expected="blocked_production_write_requested_without_exact_confirmation",
            actual=result.status,
        )
    if import_executed and not exact_confirmation:
        result.fail(
            "s3a_prod1_import_write_without_exact_confirmation",
            "S3A-PROD1 import writes require the exact production sync confirmation string.",
            path="run_configuration.exact_production_sync_confirmation",
            expected=True,
            actual=False,
        )
    if ai_executed and not ai_dry_run and not exact_confirmation:
        result.fail(
            "s3a_prod1_ai_write_without_exact_confirmation",
            "S3A-PROD1 DirectML AI tagging writes require the exact production sync confirmation string.",
            path="run_configuration.exact_production_sync_confirmation",
            expected=True,
            actual=False,
        )
    if status in target_statuses:
        if not production_write_requested or not exact_confirmation:
            result.fail(
                "s3a_prod1_target_without_exact_confirmation",
                "S3A-PROD1 target_met requires an explicit write request with the exact production sync confirmation.",
                path="run_configuration.exact_production_sync_confirmation",
                expected=True,
                actual=exact_confirmation,
            )
        if not _as_bool(_get(summary, "s3a_boundary.operator_triggered_production_sync_enabled", False)):
            result.fail(
                "s3a_prod1_target_without_operator_sync_boundary",
                "S3A-PROD1 target_met requires the operator-triggered production sync boundary to be explicit.",
                path="s3a_boundary.operator_triggered_production_sync_enabled",
                expected=True,
                actual=_get(summary, "s3a_boundary.operator_triggered_production_sync_enabled", None),
            )

    import_preconditions_passed = _as_bool(_get(summary, "import_write_preconditions.passed", False))
    if import_executed and not import_preconditions_passed:
        result.fail(
            "s3a_prod1_import_write_executed_before_prerequisites",
            "S3A-PROD1 import write execution must not happen before all pre-write prerequisites pass.",
            path="import_write_preconditions.passed",
            expected=True,
            actual=False,
        )
    if status in target_statuses and not import_preconditions_passed:
        result.fail(
            "s3a_prod1_target_without_import_preconditions",
            "S3A-PROD1 target_met requires passing import/write preconditions.",
            path="import_write_preconditions.passed",
            expected=True,
            actual=False,
        )

    imported = _as_int(_get(summary, "import_reuse.imported_count", 0))
    reused = _as_int(_get(summary, "import_reuse.reused_count", 0))
    would_import = _as_int(_get(summary, "import_reuse.would_import_count", 0))
    skipped = _as_int(_get(summary, "import_reuse.skipped_count", 0))
    import_failed = _as_int(_get(summary, "import_reuse.failed_count", 0))
    import_status = str(_get(summary, "import_reuse.status", "") or "").casefold()
    downstream = _as_int(_get(summary, "import_reuse.downstream_media_count", 0))
    if imported + reused + would_import + skipped + import_failed < selected_count:
        result.fail(
            "s3a_prod1_import_reuse_counts_under_reported",
            "Import/reuse counts must account for the selected bounded input scope.",
            path="import_reuse",
            expected=f">={selected_count} accounted items",
            actual={
                "imported": imported,
                "reused": reused,
                "would_import": would_import,
                "skipped": skipped,
                "failed": import_failed,
            },
        )
    if (import_failed or "item_failures" in import_status) and status != "blocked_import_item_failures":
        result.fail(
            "s3a_prod1_import_failures_not_blocked",
            "Import/reuse item failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_import_item_failures",
            actual=result.status,
        )
    if status in target_statuses and downstream <= 0:
        result.fail(
            "s3a_prod1_target_without_downstream_media",
            "S3A-PROD1 target_met requires at least one imported or reused downstream media item.",
            path="import_reuse.downstream_media_count",
            expected=">=1",
            actual=downstream,
        )

    classification_failed = _as_int(_get(summary, "classification.failed_count", 0))
    classification_status = str(_get(summary, "classification.status", "") or "").casefold()
    classification_executed = _as_bool(_get(summary, "classification.executed", False))
    if (classification_failed or "item_failures" in classification_status) and status != "blocked_classification_failures":
        result.fail(
            "s3a_prod1_classification_failures_not_blocked",
            "Classification failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_classification_failures",
            actual=result.status,
        )
    if status in target_statuses and not classification_executed:
        result.fail(
            "s3a_prod1_target_without_classification",
            "S3A-PROD1 target_met requires reported classification validation for downstream media.",
            path="classification.executed",
            expected=True,
            actual=classification_executed,
        )

    requested = _get(summary, "directml_ai_tagging.provider_preference_requested", [])
    actual_provider = _get(summary, "directml_ai_tagging.provider.actual_provider", None)
    ai_failed = _as_int(_get(summary, "directml_ai_tagging.failed", 0))
    ai_processed = _as_int(_get(summary, "directml_ai_tagging.processed", 0))
    ai_status = str(_get(summary, "directml_ai_tagging.status", "") or "").casefold()
    gate_passed = _as_bool(_get(summary, "provider_write_gate.passed", False))
    gate_write_allowed = _as_bool(_get(summary, "provider_write_gate.write_allowed", False))
    gate_blockers = _get(summary, "provider_write_gate.blockers", [])
    gate_prefers_directml = _as_bool(_get(summary, "provider_write_gate.provider_preference_includes_directml", False))
    gate_dml_then_cpu = _as_bool(_get(summary, "provider_write_gate.provider_preference_dml_then_cpu", False))
    probe_actual = _get(summary, "provider_write_gate.probe_actual_provider", None)
    probe_executed = _as_bool(_get(summary, "provider_write_gate.probe_executed", False))
    probe_status = str(_get(summary, "provider_write_gate.probe_status", "") or "")
    probe_failed = _as_int(_get(summary, "provider_write_gate.probe_failed", 0))
    probe_rollback_error = _as_bool(_get(summary, "provider_write_gate.probe_rollback_error", False))
    probe_error_state = _as_bool(_get(summary, "provider_write_gate.probe_error_state", False))
    probe_clean = bool(
        probe_executed
        and probe_status == "completed"
        and probe_failed == 0
        and not probe_rollback_error
        and not probe_error_state
        and probe_actual == "DmlExecutionProvider"
    )
    if (ai_failed or "item_failures" in ai_status) and status != "blocked_ai_tagging_item_failures":
        result.fail(
            "s3a_prod1_ai_failures_not_blocked",
            "AI tagging item failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_ai_tagging_item_failures",
            actual=result.status,
        )
    if ai_executed and not ai_dry_run and not gate_passed:
        result.fail(
            "s3a_prod1_ai_write_without_directml_gate",
            "S3A-PROD1 media_tags writes must be preceded by a passing DirectML provider write gate.",
            path="provider_write_gate.passed",
            expected=True,
            actual=_get(summary, "provider_write_gate.passed", None),
        )
    if ai_executed and not ai_dry_run and not probe_clean:
        result.fail(
            "s3a_prod1_ai_write_without_clean_directml_probe",
            "S3A-PROD1 media_tags writes require a clean DirectML prewrite probe before dry_run=false.",
            path="directml_provider_probe",
            expected={
                "executed": True,
                "status": "completed",
                "failed": 0,
                "rollback_error": False,
                "error_state": False,
                "provider.actual_provider": "DmlExecutionProvider",
            },
            actual={
                "executed": probe_executed,
                "status": probe_status,
                "failed": probe_failed,
                "rollback_error": probe_rollback_error,
                "error_state": probe_error_state,
                "provider.actual_provider": probe_actual,
            },
        )
    if status == "blocked_directml_provider_not_validated" and production_write_requested and exact_confirmation and not gate_blockers:
        result.fail(
            "s3a_prod1_directml_provider_blocker_missing",
            "DirectML provider blocked reports must include the provider write-gate blocker.",
            path="provider_write_gate.blockers",
            expected="non-empty blocker list",
            actual=gate_blockers,
        )
    if status in target_statuses:
        if not ai_executed or ai_dry_run or ai_processed != downstream:
            result.fail(
                "s3a_prod1_target_without_ai_write_scope_match",
                "S3A-PROD1 target_met requires DirectML AI tagging write execution over the downstream media scope.",
                path="directml_ai_tagging.processed",
                expected=downstream,
                actual=ai_processed,
            )
        if requested != ["DmlExecutionProvider", "CPUExecutionProvider"]:
            result.fail(
                "s3a_prod1_provider_preference_invalid",
                "S3A-PROD1 must request DirectML first with CPU fallback.",
                path="directml_ai_tagging.provider_preference_requested",
                expected=["DmlExecutionProvider", "CPUExecutionProvider"],
                actual=requested,
            )
        if not gate_passed or not gate_write_allowed or not gate_prefers_directml or not gate_dml_then_cpu or not probe_clean:
            result.fail(
                "s3a_prod1_target_without_directml_write_gate",
                "S3A-PROD1 target_met requires a passing pre-write DirectML provider gate before any media_tags write.",
                path="provider_write_gate",
                expected={
                    "passed": True,
                    "write_allowed": True,
                    "provider_preference_includes_directml": True,
                    "provider_preference_dml_then_cpu": True,
                    "probe_executed": True,
                    "probe_status": "completed",
                    "probe_failed": 0,
                    "probe_rollback_error": False,
                    "probe_error_state": False,
                    "probe_actual_provider": "DmlExecutionProvider",
                },
                actual={
                    "passed": gate_passed,
                    "write_allowed": gate_write_allowed,
                    "provider_preference_includes_directml": gate_prefers_directml,
                    "provider_preference_dml_then_cpu": gate_dml_then_cpu,
                    "probe_executed": probe_executed,
                    "probe_status": probe_status,
                    "probe_failed": probe_failed,
                    "probe_rollback_error": probe_rollback_error,
                    "probe_error_state": probe_error_state,
                    "probe_actual_provider": probe_actual,
                    "blockers": gate_blockers,
                },
            )
        if actual_provider != "DmlExecutionProvider":
            result.fail(
                "s3a_prod1_actual_provider_not_directml",
                "S3A-PROD1 production write target requires DmlExecutionProvider.",
                path="directml_ai_tagging.provider.actual_provider",
                expected="DmlExecutionProvider",
                actual=actual_provider,
            )

    ai_before = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_before", 0))
    ai_after = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_after", 0))
    ai_delta = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_delta", 0))
    ai_first_time = _as_bool(_get(summary, "directml_ai_tagging.first_time_media_tag_insertion_proven", False))
    if ai_after - ai_before != ai_delta:
        result.fail(
            "s3a_prod1_media_tags_delta_inconsistent",
            "S3A-PROD1 media_tags delta must equal after - before.",
            path="directml_ai_tagging.media_tags_count_delta",
            expected=ai_after - ai_before,
            actual=ai_delta,
        )
    if status in target_statuses and not (ai_delta > 0 or ai_first_time):
        result.fail(
            "s3a_prod1_write_target_without_media_tags_delta",
            "target_met_with_bounded_write requires a positive media_tags delta or explicit first-time insertion proof.",
            path="directml_ai_tagging.media_tags_count_delta",
            expected=">0 or first_time_media_tag_insertion_proven=true",
            actual={"media_tags_count_delta": ai_delta, "first_time_media_tag_insertion_proven": ai_first_time},
        )

    validation_write_completed = _as_bool(_get(summary, "validation.production_write_completed", False))
    expected_write_completed = bool(
        status in target_statuses
        and import_failed == 0
        and ai_failed == 0
        and actual_provider == "DmlExecutionProvider"
        and (ai_delta > 0 or ai_first_time)
    )
    if validation_write_completed and not expected_write_completed:
        result.fail(
            "s3a_prod1_production_write_completed_overstated",
            "validation.production_write_completed may only be true for a successful target write with DirectML and real media_tags proof.",
            path="validation.production_write_completed",
            expected=False,
            actual=True,
        )
    if status in target_statuses and not validation_write_completed:
        result.fail(
            "s3a_prod1_target_without_production_write_completed",
            "S3A-PROD1 target_met should mark production_write_completed only after all target write proof is present.",
            path="validation.production_write_completed",
            expected=True,
            actual=validation_write_completed,
        )

    cpu_executed = _as_bool(_get(summary, "cpu_fallback_validation.executed", False))
    cpu_status = str(_get(summary, "cpu_fallback_validation.status", "") or "").casefold()
    cpu_failed = _as_int(_get(summary, "cpu_fallback_validation.failed", 0))
    cpu_dry_run = _as_bool(_get(summary, "cpu_fallback_validation.dry_run", False))
    cpu_actual = _get(summary, "cpu_fallback_validation.provider.actual_provider", None)
    cpu_delta = _as_int(_get(summary, "cpu_fallback_validation.media_tags_count_delta", 0))
    if status in target_statuses:
        if not cpu_executed:
            result.fail(
                "s3a_prod1_target_without_cpu_fallback",
                "S3A-PROD1 target_met requires CPU fallback validation.",
                path="cpu_fallback_validation.executed",
                expected=True,
                actual=cpu_executed,
            )
        if cpu_status != "completed":
            result.fail(
                "s3a_prod1_cpu_fallback_status_invalid",
                "CPU fallback validation must complete successfully before target status.",
                path="cpu_fallback_validation.status",
                expected="completed",
                actual=_get(summary, "cpu_fallback_validation.status", None),
            )
        if cpu_failed != 0:
            result.fail(
                "s3a_prod1_cpu_fallback_failed",
                "CPU fallback validation must report failed=0 before target status.",
                path="cpu_fallback_validation.failed",
                expected=0,
                actual=cpu_failed,
            )
        if not cpu_dry_run:
            result.fail(
                "s3a_prod1_cpu_fallback_not_dry_run",
                "CPU fallback validation must remain dry-run.",
                path="cpu_fallback_validation.dry_run",
                expected=True,
                actual=cpu_dry_run,
            )
        if cpu_actual != "CPUExecutionProvider":
            result.fail(
                "s3a_prod1_cpu_fallback_actual_provider_invalid",
                "CPU fallback validation must force and load CPUExecutionProvider.",
                path="cpu_fallback_validation.provider.actual_provider",
                expected="CPUExecutionProvider",
                actual=cpu_actual,
            )
        if cpu_delta != 0:
            result.fail(
                "s3a_prod1_cpu_fallback_media_tags_delta",
                "CPU fallback validation must not write media_tags.",
                path="cpu_fallback_validation.media_tags_count_delta",
                expected=0,
                actual=cpu_delta,
            )

    localization_failed = _as_int(_get(summary, "localization.failed", 0))
    if localization_failed and status != "blocked_localization_failures":
        result.fail(
            "s3a_prod1_localization_failed",
            "Localization validation must report zero failures or defer without external provider use.",
            path="localization.failed",
            expected=0,
            actual=localization_failed,
        )

    markdown_text = _read_s3a_prod1_markdown_report(summary, result)
    redaction_passed = _as_bool(_get(summary, "public_redaction.passed", False))
    if not redaction_passed and status in target_statuses:
        result.fail(
            "s3a_prod1_target_with_failed_public_redaction",
            "Public redaction failure must clear target/safe_to_merge claims and block the run.",
            path="public_redaction.passed",
            expected=True,
            actual=_get(summary, "public_redaction.passed", None),
        )
    if status == "blocked_public_redaction_failed" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_prod1_redaction_blocker_claimed_completion",
            "blocked_public_redaction_failed must not claim target_met, full_chain_complete, or safe_to_merge.",
            path="pipeline_contract.claims",
            expected="all false",
            actual=_get(summary, "pipeline_contract.claims", None),
        )
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s3a_prod1_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s3a_prod1_public_payload_redaction_failed",
            "S3A-PROD1 contract independently found forbidden public JSON or Markdown content.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )


def _check_s3a_prod2_s3b_d1_operator_scaleup_disabled_sync(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "target_met_dry_run_only",
        "target_met_with_bounded_write",
        "write_executed_but_batch_scale_unproven",
        "write_executed_but_first_time_insertion_unproven",
        "blocked_input_over_cap",
        "blocked_scope_invalid",
        "blocked_full_library_fallback",
        "blocked_max_items_over_phase_cap",
        "blocked_no_media",
        "blocked_model_cache_missing",
        "blocked_model_download_allowed",
        "blocked_provider_preference_invalid",
        "blocked_directml_provider_not_available",
        "blocked_cpu_fallback_provider_not_available",
        "blocked_concurrent_job_active",
        "blocked_concurrent_operator_sync",
        "blocked_protected_input_root",
        "blocked_source_file_preflight_failures",
        "blocked_write_requested_without_exact_confirmation",
        "blocked_write_preconditions",
        "blocked_write_window_concurrency",
        "blocked_import_item_failures",
        "blocked_classification_failures",
        "blocked_ai_tagging_item_failures",
        "blocked_write_not_executed",
        "blocked_directml_provider_not_validated",
        "blocked_cpu_fallback_not_validated",
        "blocked_s3b_scaffold_not_disabled",
        "blocked_public_redaction_failed",
    }
    target_statuses = {"target_met_dry_run_only", "target_met_with_bounded_write"}
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s3a_prod2_unknown_status",
            "S3A-PROD2/S3B-D1 status must explicitly describe completion or the blocking gate.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status not in target_statuses and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_prod2_non_completion_status_claimed_completion",
            "Blocked S3A-PROD2/S3B-D1 summaries must not claim target_met, full_chain_complete, or safe_to_merge.",
            path="pipeline_contract.status",
            expected="target_met status for completion claims",
            actual=result.status,
        )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "run_configuration.local_files_only",
            "scope.no_full_library_fallback",
            "scope.protected_input_gate.reported",
            "model_cache.local_files_only",
            "source_file_preflight.reported",
            "provider_availability.reported",
            "job_concurrency.reported",
            "write_provider_policy.reported",
            "write_window_protection.reported",
            "directml_provider_probe.reported",
            "provider_write_gate.reported",
            "import_reuse.reported",
            "classification.reported",
            "directml_ai_tagging.reported",
            "cpu_fallback_validation.reported",
            "localization.reported",
            "s3a_boundary.operator_triggered_only",
            "safety.max_items_lte_20",
            "safety.selected_input_explicit_bounded",
            "safety.no_full_library_run",
            "s3b_disabled_scaffold.roots_redacted",
            "s3b_disabled_scaffold.cloud_file_policy.paths_redacted",
        ),
        code="s3a_prod2_required_proof_missing",
        message="S3A-PROD2/S3B-D1 requires bounded explicit input, local-only model loading, staged results, S3B disabled scaffold proof, and public redaction proof.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "run_configuration.model_download_allowed",
            "run_configuration.s3a_production_automation_enabled",
            "run_configuration.unattended_s3b_enabled",
            "run_configuration.scheduled_s3b_enabled",
            "run_configuration.cpu_fallback_write_allowed",
            "scope.private_locator_values_recorded",
            "write_provider_policy.cpu_fallback_write_allowed",
            "s3a_boundary.production_execution_enabled",
            "s3a_boundary.unattended_enabled",
            "s3a_boundary.scheduled_automation_enabled",
            "s3a_boundary.broad_production_sync_enabled",
            "safety.write_without_confirmation",
            "safety.source_icloud_mutation",
            "safety.source_mutation",
            "safety.production_s3a_automation_enabled",
            "safety.unattended_s3b_enabled",
            "safety.scheduled_s3b_enabled",
            "safety.scheduler_started",
            "safety.background_job_started",
            "safety.automatic_writes_started",
            "safety.provider_pixiv_gallery_dl_saucenao_google_calls",
            "safety.sourceconcept_r1_r2_r1r",
            "safety.entity_bridge",
            "safety.confirmed_entity_assignments",
            "safety.desired_media_backfill",
            "safety.cleanup_delete_reset_drop_truncate",
            "safety.drop_truncate_reset",
            "safety.model_download",
            "safety.private_locator_values_recorded",
            "safety.external_llm_provider_used",
            "public_redaction.unsafe_public_report_written",
            "localization.llm_external_provider_used",
            "s3b_disabled_scaffold.policy.unattended_enabled",
            "s3b_disabled_scaffold.policy.scheduled_enabled",
            "s3b_disabled_scaffold.trigger.unattended",
            "s3b_disabled_scaffold.trigger.scheduled",
            "s3b_disabled_scaffold.trigger.scheduler_started",
            "s3b_disabled_scaffold.trigger.background_job_started",
            "s3b_disabled_scaffold.scheduler_started",
            "s3b_disabled_scaffold.background_job_started",
            "s3b_disabled_scaffold.automatic_writes_started",
            "s3b_disabled_scaffold.source_mutation",
            "s3b_disabled_scaffold.cleanup_delete_reset_drop_truncate",
        ),
        code="s3a_prod2_forbidden_safety_flag",
        message="S3A-PROD2/S3B-D1 summaries must explicitly keep unattended, scheduled, provider, entity, destructive, model-download, and privacy paths disabled.",
    )

    max_items = _as_int(_get(summary, "run_configuration.max_items", 0))
    selected_count = _as_int(_get(summary, "scope.selected_count", 0))
    over_cap = _as_int(_get(summary, "scope.over_cap_count", 0))
    input_mode = str(_get(summary, "run_configuration.input_mode", "") or "")
    if input_mode != "input_path":
        result.fail(
            "s3a_prod2_input_mode_invalid",
            "S3A-PROD2/S3B-D1 must use explicit input paths only.",
            path="run_configuration.input_mode",
            expected="input_path",
            actual=input_mode,
        )
    if not (1 <= max_items <= 20):
        result.fail(
            "s3a_prod2_max_items_unbounded",
            "S3A-PROD2/S3B-D1 max_items must stay between 1 and 20.",
            path="run_configuration.max_items",
            expected="1..20",
            actual=max_items,
        )
    if over_cap:
        result.fail(
            "s3a_prod2_input_over_cap",
            "S3A-PROD2/S3B-D1 input must block over-cap batches instead of truncating silently.",
            path="scope.over_cap_count",
            expected=0,
            actual=over_cap,
        )
        if status in target_statuses:
            result.fail(
                "s3a_prod2_target_claimed_with_over_cap_input",
                "S3A-PROD2/S3B-D1 must block over-cap input before target claims.",
                path="pipeline_contract.status",
                expected="blocked_input_over_cap",
                actual=result.status,
            )
    if not over_cap and status in target_statuses and not (1 <= selected_count <= max_items <= 20):
        result.fail(
            "s3a_prod2_selected_batch_invalid",
            "S3A-PROD2/S3B-D1 selected sample count must be non-zero and within max_items <= 20.",
            path="scope.selected_count",
            expected=f"1..{max_items}",
            actual=selected_count,
        )

    source_failed = _as_int(_get(summary, "source_file_preflight.failed_count", 0))
    protected_gate_reported = _as_bool(_get(summary, "scope.protected_input_gate.reported", False))
    protected_blocked = _as_int(_get(summary, "scope.protected_input_gate.blocked_count", 0))
    protected_passed = _as_bool(_get(summary, "scope.protected_input_gate.passed", False))
    if not protected_gate_reported:
        result.fail(
            "s3a_prod2_protected_input_gate_missing",
            "S3A-PROD2/S3B-D1 must report the protected app-managed input root gate.",
            path="scope.protected_input_gate.reported",
            expected=True,
            actual=_get(summary, "scope.protected_input_gate.reported", None),
        )
    if protected_blocked:
        if status != "blocked_protected_input_root":
            result.fail(
                "s3a_prod2_protected_input_not_blocked",
                "Input paths under app-managed or protected roots must block before source preflight or writes.",
                path="pipeline_contract.status",
                expected="blocked_protected_input_root",
                actual={"status": result.status, "blocked_count": protected_blocked},
            )
        if status in target_statuses:
            result.fail(
                "s3a_prod2_target_claimed_with_protected_input",
                "Protected app-managed input roots cannot be used for target claims.",
                path="scope.protected_input_gate.blocked_count",
                expected=0,
                actual=protected_blocked,
            )
    if status in target_statuses and not protected_passed:
        result.fail(
            "s3a_prod2_target_without_protected_input_gate",
            "Target claims require protected input root gate passed=true.",
            path="scope.protected_input_gate.passed",
            expected=True,
            actual=protected_passed,
        )
    if source_failed and not (
        status == "blocked_source_file_preflight_failures"
        or (protected_blocked and status == "blocked_protected_input_root")
    ):
        result.fail(
            "s3a_prod2_target_with_source_preflight_failures",
            "Source-file preflight failures, including missing explicit inputs and protected child paths, must block the run.",
            path="source_file_preflight.failed_count",
            expected="0 or blocked source/protected-input status",
            actual={"failed_count": source_failed, "status": result.status},
        )
    write_requested = _as_bool(_get(summary, "run_configuration.write_requested", False))
    write_confirmed = _as_bool(_get(summary, "run_configuration.operator_confirmation_exact", False))
    write_preconditions_passed = _as_bool(_get(summary, "write_preconditions.passed", False))
    import_executed = _as_bool(_get(summary, "import_reuse.executed", False))
    ai_executed = _as_bool(_get(summary, "directml_ai_tagging.executed", False))
    ai_dry_run = _as_bool(_get(summary, "directml_ai_tagging.dry_run", True))
    provider_preference_requested = _get(summary, "run_configuration.provider_preference_requested", [])
    provider_preference_valid = provider_preference_requested == ["DmlExecutionProvider", "CPUExecutionProvider"]
    directml_available = _as_bool(_get(summary, "provider_availability.directml_available", False))
    cpu_available = _as_bool(_get(summary, "provider_availability.cpu_fallback_available", False))
    s3b_state_passed = _as_bool(_get(summary, "write_preconditions.s3b_disabled_state_passed", False))
    if protected_blocked and (import_executed or (ai_executed and not ai_dry_run)):
        result.fail(
            "s3a_prod2_protected_input_claims_writes",
            "Protected app-managed input roots must block before import or media_tags writes.",
            path="scope.protected_input_gate.blocked_count",
            expected={"blocked_count": 0, "writes": False},
            actual={
                "blocked_count": protected_blocked,
                "import_executed": import_executed,
                "ai_write_executed": ai_executed and not ai_dry_run,
            },
        )
    if source_failed and (import_executed or (ai_executed and not ai_dry_run)):
        result.fail(
            "s3a_prod2_source_preflight_failure_claims_writes",
            "Missing, unreadable, or disappeared explicit inputs must not be silently dropped before writes.",
            path="source_file_preflight.failed_count",
            expected={"failed_count": 0, "writes": False},
            actual={"failed_count": source_failed, "import_executed": import_executed, "ai_write_executed": ai_executed and not ai_dry_run},
        )
    if write_requested and not write_confirmed and status != "blocked_write_requested_without_exact_confirmation":
        result.fail(
            "s3a_prod2_write_requested_without_exact_confirmation_not_blocked",
            "A write request without the exact S3A-PROD2 operator confirmation must block.",
            path="pipeline_contract.status",
            expected="blocked_write_requested_without_exact_confirmation",
            actual=result.status,
        )
    if write_requested and write_confirmed and not provider_preference_valid and status != "blocked_provider_preference_invalid":
        result.fail(
            "s3a_prod2_provider_preference_invalid_not_blocked",
            "S3A-PROD2 writes require provider preference DmlExecutionProvider,CPUExecutionProvider before import or AI writes.",
            path="pipeline_contract.status",
            expected="blocked_provider_preference_invalid",
            actual={"status": result.status, "provider_preference": provider_preference_requested},
        )
    if write_requested and write_confirmed and not cpu_available and status != "blocked_cpu_fallback_provider_not_available":
        result.fail(
            "s3a_prod2_cpu_fallback_unavailable_not_blocked",
            "S3A-PROD2 writes require CPUExecutionProvider availability before import or AI writes.",
            path="pipeline_contract.status",
            expected="blocked_cpu_fallback_provider_not_available",
            actual={"status": result.status, "cpu_fallback_available": cpu_available},
        )
    if write_requested and write_confirmed and not s3b_state_passed and status != "blocked_s3b_scaffold_not_disabled":
        result.fail(
            "s3a_prod2_s3b_enabled_not_blocked_before_writes",
            "S3B unattended/scheduled/background state must be checked and disabled before import or AI writes.",
            path="write_preconditions.s3b_disabled_state_passed",
            expected=True,
            actual={"status": result.status, "s3b_disabled_state_passed": s3b_state_passed},
        )
    if write_requested and write_confirmed and not write_preconditions_passed and status not in {
        "blocked_write_preconditions",
        "blocked_model_cache_missing",
        "blocked_model_download_allowed",
        "blocked_provider_preference_invalid",
        "blocked_directml_provider_not_available",
        "blocked_cpu_fallback_provider_not_available",
        "blocked_concurrent_job_active",
        "blocked_concurrent_operator_sync",
        "blocked_protected_input_root",
        "blocked_source_file_preflight_failures",
        "blocked_write_window_concurrency",
        "blocked_s3b_scaffold_not_disabled",
        "blocked_scope_invalid",
        "blocked_input_over_cap",
    }:
        result.fail(
            "s3a_prod2_write_prerequisites_not_blocked",
            "Writes must block unless model cache, local-files-only, scope, provider, concurrency, and confirmation gates all pass before execution.",
            path="pipeline_contract.status",
            expected="blocked write precondition status",
            actual={
                "status": result.status,
                "preconditions_passed": write_preconditions_passed,
                "blockers": _get(summary, "write_preconditions.blockers", None),
            },
        )
    if not write_preconditions_passed and ai_executed and not ai_dry_run:
        result.fail(
            "s3a_prod2_ai_write_without_write_preconditions",
            "DirectML AI tagging must remain dry-run unless the same write preconditions used for import writes passed.",
            path="write_preconditions.passed",
            expected={"write_preconditions.passed": True, "directml_ai_tagging.dry_run": True},
            actual={"write_preconditions.passed": write_preconditions_passed, "directml_ai_tagging.dry_run": ai_dry_run},
        )
    if import_executed and not write_preconditions_passed:
        result.fail(
            "s3a_prod2_import_write_without_write_preconditions",
            "Import writes must not execute unless write preconditions passed.",
            path="write_preconditions.passed",
            expected=True,
            actual=False,
        )
    if (import_executed or (ai_executed and not ai_dry_run)) and not write_confirmed:
        result.fail(
            "s3a_prod2_write_without_exact_confirmation",
            "Import and DirectML media_tags writes require the exact S3A-PROD2 operator confirmation string.",
            path="run_configuration.operator_confirmation_exact",
            expected=True,
            actual=False,
        )
    if import_executed or (ai_executed and not ai_dry_run):
        durable_lock_held_for_write = _as_bool(_get(summary, "write_window_protection.durable_lock_held", False))
        operator_lock_acquired_for_write = _as_bool(
            _get(summary, "write_window_protection.operator_sync_lock.acquired", False)
        )
        job_lock_held_for_write = _as_bool(_get(summary, "job_concurrency.durable_lock_held", False))
        if not durable_lock_held_for_write or not operator_lock_acquired_for_write or not job_lock_held_for_write:
            result.fail(
                "s3a_prod2_write_without_durable_operator_lock",
                "Import and media_tags writes must be covered by the S3A-PROD2 durable operator sync guard.",
                path="write_window_protection",
                expected={
                    "write_window_protection.durable_lock_held": True,
                    "write_window_protection.operator_sync_lock.acquired": True,
                    "job_concurrency.durable_lock_held": True,
                },
                actual={
                    "write_window_protection.durable_lock_held": durable_lock_held_for_write,
                    "write_window_protection.operator_sync_lock.acquired": operator_lock_acquired_for_write,
                    "job_concurrency.durable_lock_held": job_lock_held_for_write,
                },
            )
        import_recheck_passed_for_write = _as_bool(
            _get(summary, "write_window_protection.import_recheck.passed", False)
        )
        ai_recheck_passed_for_write = _as_bool(
            _get(summary, "write_window_protection.ai_write_recheck.passed", False)
        )
        if not import_recheck_passed_for_write or ((ai_executed and not ai_dry_run) and not ai_recheck_passed_for_write):
            result.fail(
                "s3a_prod2_write_without_window_recheck",
                "Import and media_tags writes must not execute unless the write window concurrency rechecks passed.",
                path="write_window_protection",
                expected={"import_recheck.passed": True, "ai_write_recheck.passed": True},
                actual={
                    "import_recheck.passed": import_recheck_passed_for_write,
                    "ai_write_recheck.passed": ai_recheck_passed_for_write,
                },
            )

    imported = _as_int(_get(summary, "import_reuse.imported_count", 0))
    reused = _as_int(_get(summary, "import_reuse.reused_count", 0))
    would_import = _as_int(_get(summary, "import_reuse.would_import_count", 0))
    skipped = _as_int(_get(summary, "import_reuse.skipped_count", 0))
    import_failed = _as_int(_get(summary, "import_reuse.failed_count", 0))
    downstream = _as_int(_get(summary, "import_reuse.downstream_media_count", 0))
    if imported + reused + would_import + skipped + import_failed < selected_count:
        result.fail(
            "s3a_prod2_import_reuse_counts_under_reported",
            "Import/reuse counts must account for the selected bounded batch.",
            path="import_reuse",
            expected=f">={selected_count} accounted items",
            actual={"imported": imported, "reused": reused, "would_import": would_import, "skipped": skipped, "failed": import_failed},
        )
    if import_failed and status != "blocked_import_item_failures":
        result.fail(
            "s3a_prod2_import_failures_not_blocked",
            "Import/reuse item failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_import_item_failures",
            actual=result.status,
        )
    if downstream <= 0 and status in target_statuses:
        result.fail(
            "s3a_prod2_target_without_downstream_media",
            "S3A-PROD2/S3B-D1 target_met requires at least one imported or reused downstream media item.",
            path="import_reuse.downstream_media_count",
            expected=">=1",
            actual=downstream,
        )

    classification_failed = _as_int(_get(summary, "classification.failed_count", 0))
    if classification_failed and status != "blocked_classification_failures":
        result.fail(
            "s3a_prod2_classification_failures_not_blocked",
            "Classification failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_classification_failures",
            actual=result.status,
        )

    requested = _get(summary, "directml_ai_tagging.provider_preference_requested", [])
    actual_provider = _get(summary, "directml_ai_tagging.provider.actual_provider", None)
    actual_write_preference = _get(summary, "write_provider_policy.actual_write_provider_preference", [])
    fallback_write_allowed = _as_bool(_get(summary, "write_provider_policy.cpu_fallback_write_allowed", False))
    fallback_disabled_for_write = _as_bool(
        _get(summary, "write_provider_policy.provider_fallback_disabled_for_actual_write", False)
    )
    ai_failed = _as_int(_get(summary, "directml_ai_tagging.failed", 0))
    ai_processed = _as_int(_get(summary, "directml_ai_tagging.processed", 0))
    if ai_executed and not ai_dry_run:
        if requested != ["DmlExecutionProvider"]:
            result.fail(
                "s3a_prod2_ai_write_provider_preference_not_dml_only",
                "Non-dry-run S3A-PROD2 AI tagging writes must request DmlExecutionProvider only so CPU fallback cannot write.",
                path="directml_ai_tagging.provider_preference_requested",
                expected=["DmlExecutionProvider"],
                actual=requested,
            )
        if actual_provider != "DmlExecutionProvider":
            result.fail(
                "s3a_prod2_fallback_write_provider_not_allowed",
                "Non-dry-run S3A-PROD2 AI tagging writes must not run or write through CPU fallback.",
                path="directml_ai_tagging.provider.actual_provider",
                expected="DmlExecutionProvider",
                actual=actual_provider,
            )
    if status == "target_met_with_bounded_write":
        if actual_write_preference != ["DmlExecutionProvider"] or fallback_write_allowed or not fallback_disabled_for_write:
            result.fail(
                "s3a_prod2_cpu_fallback_write_path_allowed",
                "Bounded write target must force DmlExecutionProvider-only actual writes and disable provider fallback for that write path.",
                path="write_provider_policy",
                expected={
                    "actual_write_provider_preference": ["DmlExecutionProvider"],
                    "cpu_fallback_write_allowed": False,
                    "provider_fallback_disabled_for_actual_write": True,
                },
                actual={
                    "actual_write_provider_preference": actual_write_preference,
                    "cpu_fallback_write_allowed": fallback_write_allowed,
                    "provider_fallback_disabled_for_actual_write": fallback_disabled_for_write,
                },
            )
    if ai_failed and status != "blocked_ai_tagging_item_failures":
        result.fail(
            "s3a_prod2_ai_failures_not_blocked",
            "AI tagging item failures must produce a blocked status.",
            path="pipeline_contract.status",
            expected="blocked_ai_tagging_item_failures",
            actual=result.status,
        )
    if status in target_statuses:
        if not provider_preference_valid:
            result.fail(
                "s3a_prod2_target_provider_preference_not_bounded",
                "S3A-PROD2 target_met requires the exact DirectML+CPU provider preference.",
                path="run_configuration.provider_preference_requested",
                expected=["DmlExecutionProvider", "CPUExecutionProvider"],
                actual=provider_preference_requested,
            )
        if not directml_available:
            result.fail(
                "s3a_prod2_target_without_directml_available",
                "S3A-PROD2 target_met requires DirectML availability before writes.",
                path="provider_availability.directml_available",
                expected=True,
                actual=directml_available,
            )
        if not cpu_available:
            result.fail(
                "s3a_prod2_target_without_cpu_fallback_available",
                "S3A-PROD2 target_met requires CPU fallback availability before writes.",
                path="provider_availability.cpu_fallback_available",
                expected=True,
                actual=cpu_available,
            )
        if not s3b_state_passed:
            result.fail(
                "s3a_prod2_target_without_s3b_disabled_precondition",
                "S3A-PROD2 target_met requires S3B disabled state in write preconditions.",
                path="write_preconditions.s3b_disabled_state_passed",
                expected=True,
                actual=s3b_state_passed,
            )
        if not ai_executed or ai_processed != downstream:
            result.fail(
                "s3a_prod2_target_without_ai_scope_match",
                "S3A-PROD2/S3B-D1 target_met requires DirectML AI tagging validation over the downstream media scope.",
                path="directml_ai_tagging.processed",
                expected=downstream,
                actual=ai_processed,
            )
        if not isinstance(requested, list) or "DmlExecutionProvider" not in requested:
            result.fail(
                "s3a_prod2_provider_preference_missing_directml",
                "DirectML AI tagging validation must record a bounded provider preference including DmlExecutionProvider.",
                path="directml_ai_tagging.provider_preference_requested",
                expected="list containing DmlExecutionProvider",
                actual=requested,
            )
        if not isinstance(actual_provider, str) or not actual_provider.strip():
            result.fail(
                "s3a_prod2_actual_provider_missing",
                "DirectML AI tagging validation must report the actual ONNX provider loaded.",
                path="directml_ai_tagging.provider.actual_provider",
                expected="non-empty provider",
                actual=actual_provider,
            )
        if actual_provider != "DmlExecutionProvider":
            result.fail(
                "s3a_prod2_target_without_primary_directml",
                "S3A-PROD2 target claims, including dry-run target claims, require DmlExecutionProvider on the primary DirectML path.",
                path="directml_ai_tagging.provider.actual_provider",
                expected="DmlExecutionProvider",
                actual=actual_provider,
            )

    gate_passed = _as_bool(_get(summary, "provider_write_gate.passed", False))
    gate_write_allowed = _as_bool(_get(summary, "provider_write_gate.write_allowed", False))
    gate_dml_then_cpu = _as_bool(_get(summary, "provider_write_gate.provider_preference_dml_then_cpu", False))
    gate_probe_actual = _get(summary, "provider_write_gate.probe_actual_provider", None)
    gate_probe_executed = _as_bool(_get(summary, "provider_write_gate.probe_executed", False))
    gate_probe_status = str(_get(summary, "provider_write_gate.probe_status", "") or "")
    gate_probe_failed = _as_int(_get(summary, "provider_write_gate.probe_failed", 0))
    gate_probe_rollback_error = _as_bool(_get(summary, "provider_write_gate.probe_rollback_error", False))
    gate_probe_error_state = _as_bool(_get(summary, "provider_write_gate.probe_error_state", False))
    probe_dry_run = _as_bool(_get(summary, "directml_provider_probe.dry_run", False))
    probe_delta = _as_int(_get(summary, "directml_provider_probe.media_tags_count_delta", 0))
    if status == "target_met_with_bounded_write":
        if not gate_passed or not gate_write_allowed or not gate_dml_then_cpu:
            result.fail(
                "s3a_prod2_write_target_without_provider_write_gate",
                "Bounded write target requires a passing provider write gate before DirectML media_tags writes.",
                path="provider_write_gate",
                expected={"passed": True, "write_allowed": True, "provider_preference_dml_then_cpu": True},
                actual={
                    "passed": gate_passed,
                    "write_allowed": gate_write_allowed,
                    "provider_preference_dml_then_cpu": gate_dml_then_cpu,
                    "blockers": _get(summary, "provider_write_gate.blockers", None),
                },
            )
        if not gate_probe_executed or gate_probe_status != "completed" or gate_probe_failed or gate_probe_rollback_error or gate_probe_error_state or gate_probe_actual != "DmlExecutionProvider":
            result.fail(
                "s3a_prod2_write_target_without_clean_directml_probe",
                "Bounded write target requires a clean DirectML dry-run probe before AI writes.",
                path="provider_write_gate",
                expected={
                    "probe_executed": True,
                    "probe_status": "completed",
                    "probe_failed": 0,
                    "probe_rollback_error": False,
                    "probe_error_state": False,
                    "probe_actual_provider": "DmlExecutionProvider",
                },
                actual={
                    "probe_executed": gate_probe_executed,
                    "probe_status": gate_probe_status,
                    "probe_failed": gate_probe_failed,
                    "probe_rollback_error": gate_probe_rollback_error,
                    "probe_error_state": gate_probe_error_state,
                    "probe_actual_provider": gate_probe_actual,
                },
            )
        if not probe_dry_run or probe_delta != 0:
            result.fail(
                "s3a_prod2_directml_probe_not_dry_run",
                "DirectML prewrite probe must be dry-run and must not write media_tags.",
                path="directml_provider_probe",
                expected={"dry_run": True, "media_tags_count_delta": 0},
                actual={"dry_run": probe_dry_run, "media_tags_count_delta": probe_delta},
            )

    if all(
        _has_non_null(summary, path)
        for path in (
            "directml_ai_tagging.media_tags_count_before",
            "directml_ai_tagging.media_tags_count_after",
            "directml_ai_tagging.media_tags_count_delta",
        )
    ):
        ai_before = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_before", 0))
        ai_after = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_after", 0))
        ai_delta_reported = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_delta", 0))
        if ai_delta_reported != ai_after - ai_before:
            result.fail(
                "s3a_prod2_media_tags_delta_inconsistent",
                "directml_ai_tagging.media_tags_count_delta must equal after - before.",
                path="directml_ai_tagging.media_tags_count_delta",
                expected=ai_after - ai_before,
                actual={
                    "before": ai_before,
                    "after": ai_after,
                    "delta": ai_delta_reported,
                },
            )
    if all(
        _has_non_null(summary, path)
        for path in (
            "directml_ai_tagging.media_with_ai_tags_before",
            "directml_ai_tagging.media_with_ai_tags_after",
            "directml_ai_tagging.media_with_ai_tags_delta",
        )
    ):
        media_before = _as_int(_get(summary, "directml_ai_tagging.media_with_ai_tags_before", 0))
        media_after = _as_int(_get(summary, "directml_ai_tagging.media_with_ai_tags_after", 0))
        media_delta_reported = _as_int(_get(summary, "directml_ai_tagging.media_with_ai_tags_delta", 0))
        if media_delta_reported != media_after - media_before:
            result.fail(
                "s3a_prod2_media_with_ai_tags_delta_inconsistent",
                "directml_ai_tagging.media_with_ai_tags_delta must equal after - before.",
                path="directml_ai_tagging.media_with_ai_tags_delta",
                expected=media_after - media_before,
                actual={
                    "before": media_before,
                    "after": media_after,
                    "delta": media_delta_reported,
                },
            )

    ai_delta = _as_int(_get(summary, "directml_ai_tagging.media_tags_count_delta", 0))
    first_time_count = _as_int(_get(summary, "directml_ai_tagging.first_time_media_tag_insertion_count", 0))
    if status == "target_met_dry_run_only" and ai_delta != 0:
        result.fail(
            "s3a_prod2_dry_run_media_tags_delta",
            "Dry-run AI tagging validation must not write media_tags.",
            path="directml_ai_tagging.media_tags_count_delta",
            expected=0,
            actual=ai_delta,
        )
    if status == "target_met_with_bounded_write":
        if not write_requested:
            result.fail(
                "s3a_prod2_write_target_without_write_request",
                "target_met_with_bounded_write requires an explicit write request.",
                path="run_configuration.write_requested",
                expected=True,
                actual=False,
            )
        if not write_confirmed:
            result.fail(
                "s3a_prod2_write_target_without_confirmation",
                "target_met_with_bounded_write requires the exact operator confirmation.",
                path="run_configuration.operator_confirmation_exact",
                expected=True,
                actual=False,
            )
        if ai_dry_run:
            result.fail(
                "s3a_prod2_write_target_ai_still_dry_run",
                "target_met_with_bounded_write requires directml_ai_tagging.dry_run=false.",
                path="directml_ai_tagging.dry_run",
                expected=False,
                actual=True,
            )
        if selected_count < 2:
            result.fail(
                "s3a_prod2_write_target_batch_scale_unproven",
                "S3A-PROD2 is a scale-up phase and target write summaries must prove more than one selected item.",
                path="scope.selected_count",
                expected=">=2",
                actual=selected_count,
            )
        if ai_delta <= 0 and first_time_count <= 0:
            result.fail(
                "s3a_prod2_write_target_without_first_time_media_tags",
                "target_met_with_bounded_write requires positive media_tags delta or first-time insertion count.",
                path="directml_ai_tagging.first_time_media_tag_insertion_count",
                expected=">0",
                actual={"media_tags_count_delta": ai_delta, "first_time_media_tag_insertion_count": first_time_count},
            )
        window_mode = str(_get(summary, "write_window_protection.mode", "") or "")
        window_rechecked = _as_bool(_get(summary, "write_window_protection.write_window_rechecked", False))
        window_no_concurrent = _as_bool(_get(summary, "write_window_protection.no_concurrent_import_or_tagging_jobs", False))
        import_recheck_passed = _as_bool(_get(summary, "write_window_protection.import_recheck.passed", False))
        ai_recheck_passed = _as_bool(_get(summary, "write_window_protection.ai_write_recheck.passed", False))
        window_durable_lock_held = _as_bool(_get(summary, "write_window_protection.durable_lock_held", False))
        window_operator_lock_acquired = _as_bool(
            _get(summary, "write_window_protection.operator_sync_lock.acquired", False)
        )
        job_durable_lock_held = _as_bool(_get(summary, "job_concurrency.durable_lock_held", False))
        job_operator_lock_acquired = _as_bool(_get(summary, "job_concurrency.operator_sync_lock_acquired", False))
        if (
            window_mode != "lock_file_atomic_create_plus_immediate_recheck"
            or not window_rechecked
            or not window_no_concurrent
            or not import_recheck_passed
            or not ai_recheck_passed
            or not window_durable_lock_held
            or not window_operator_lock_acquired
            or not job_durable_lock_held
            or not job_operator_lock_acquired
        ):
            result.fail(
                "s3a_prod2_write_window_not_protected",
                "Bounded write target must prove a durable runner lock plus immediate rechecks covered import and AI writes.",
                path="write_window_protection",
                expected={
                    "mode": "lock_file_atomic_create_plus_immediate_recheck",
                    "durable_lock_held": True,
                    "operator_sync_lock.acquired": True,
                    "write_window_rechecked": True,
                    "no_concurrent_import_or_tagging_jobs": True,
                    "import_recheck.passed": True,
                    "ai_write_recheck.passed": True,
                },
                actual={
                    "mode": window_mode,
                    "write_window_rechecked": window_rechecked,
                    "no_concurrent_import_or_tagging_jobs": window_no_concurrent,
                    "import_recheck.passed": import_recheck_passed,
                    "ai_write_recheck.passed": ai_recheck_passed,
                    "durable_lock_held": window_durable_lock_held,
                    "operator_sync_lock.acquired": window_operator_lock_acquired,
                    "job_concurrency.durable_lock_held": job_durable_lock_held,
                    "job_concurrency.operator_sync_lock_acquired": job_operator_lock_acquired,
                },
            )

    cpu_executed = _as_bool(_get(summary, "cpu_fallback_validation.executed", False))
    cpu_status = str(_get(summary, "cpu_fallback_validation.status", "") or "").casefold()
    cpu_failed = _as_int(_get(summary, "cpu_fallback_validation.failed", 0))
    cpu_dry_run = _as_bool(_get(summary, "cpu_fallback_validation.dry_run", False))
    cpu_actual = _get(summary, "cpu_fallback_validation.provider.actual_provider", None)
    cpu_delta = _as_int(_get(summary, "cpu_fallback_validation.media_tags_count_delta", 0))
    if status in target_statuses:
        if not cpu_executed:
            result.fail(
                "s3a_prod2_target_without_cpu_fallback",
                "S3A-PROD2/S3B-D1 target_met requires CPU fallback validation or smoke proof.",
                path="cpu_fallback_validation.executed",
                expected=True,
                actual=cpu_executed,
            )
        if cpu_status != "completed":
            result.fail(
                "s3a_prod2_cpu_fallback_status_invalid",
                "CPU fallback validation must complete successfully before any target status.",
                path="cpu_fallback_validation.status",
                expected="completed",
                actual=_get(summary, "cpu_fallback_validation.status", None),
            )
        if cpu_failed != 0:
            result.fail(
                "s3a_prod2_cpu_fallback_failed",
                "CPU fallback validation must report failed=0 before any target status.",
                path="cpu_fallback_validation.failed",
                expected=0,
                actual=cpu_failed,
            )
        if not cpu_dry_run:
            result.fail(
                "s3a_prod2_cpu_fallback_not_dry_run",
                "CPU fallback validation must remain dry-run.",
                path="cpu_fallback_validation.dry_run",
                expected=True,
                actual=cpu_dry_run,
            )
        if cpu_actual != "CPUExecutionProvider":
            result.fail(
                "s3a_prod2_cpu_fallback_actual_provider_invalid",
                "CPU fallback validation must force and load CPUExecutionProvider.",
                path="cpu_fallback_validation.provider.actual_provider",
                expected="CPUExecutionProvider",
                actual=cpu_actual,
            )
        if cpu_delta != 0:
            result.fail(
                "s3a_prod2_cpu_fallback_media_tags_delta",
                "CPU fallback validation must not write media_tags.",
                path="cpu_fallback_validation.media_tags_count_delta",
                expected=0,
                actual=cpu_delta,
            )

    localization_failed = _as_int(_get(summary, "localization.failed", 0))
    if localization_failed:
        result.fail(
            "s3a_prod2_localization_failed",
            "Localization validation must report zero failures or defer without external provider use.",
            path="localization.failed",
            expected=0,
            actual=localization_failed,
        )

    if _get(summary, "s3b_disabled_scaffold.status", None) != "disabled_scaffold_ready":
        result.fail(
            "s3a_prod2_s3b_scaffold_status_invalid",
            "S3B scaffold must be disabled and ready; enabled or missing scheduler policy blocks target claims.",
            path="s3b_disabled_scaffold.status",
            expected="disabled_scaffold_ready",
            actual=_get(summary, "s3b_disabled_scaffold.status", None),
        )
    if not _as_bool(_get(summary, "s3b_disabled_scaffold.policy.dry_run_only", False)):
        result.fail(
            "s3a_prod2_s3b_not_dry_run_only",
            "S3B scaffold must remain dry-run-only by default.",
            path="s3b_disabled_scaffold.policy.dry_run_only",
            expected=True,
            actual=_get(summary, "s3b_disabled_scaffold.policy.dry_run_only", None),
        )
    if not _as_bool(_get(summary, "s3b_disabled_scaffold.policy.require_operator_confirmation", False)):
        result.fail(
            "s3a_prod2_s3b_confirmation_not_required",
            "S3B scaffold must require operator confirmation.",
            path="s3b_disabled_scaffold.policy.require_operator_confirmation",
            expected=True,
            actual=_get(summary, "s3b_disabled_scaffold.policy.require_operator_confirmation", None),
        )

    failure_budget_passed = _as_bool(_get(summary, "failure_budget.passed", False))
    if status in target_statuses and not failure_budget_passed:
        result.fail(
            "s3a_prod2_target_with_failed_failure_budget",
            "Target claims require import/classification/AI/CPU/redaction failure budget to pass.",
            path="failure_budget.passed",
            expected=True,
            actual=_get(summary, "failure_budget.passed", None),
        )

    markdown_text = _read_s3a_prod2_markdown_report(summary, result)
    redaction_passed = _as_bool(_get(summary, "public_redaction.passed", False))
    redaction_finding_count = _as_int(_get(summary, "public_redaction.finding_count", 0))
    clean_before_public_write = _as_bool(_get(summary, "public_redaction.clean_before_public_write", False))
    unsafe_public_report_written = _as_bool(_get(summary, "public_redaction.unsafe_public_report_written", False))
    if redaction_finding_count:
        result.fail(
            "s3a_prod2_redaction_findings_cannot_publish_report",
            "S3A-PROD2/S3B-D1 public reports must only be produced after a clean redaction scan.",
            path="public_redaction.finding_count",
            expected=0,
            actual=redaction_finding_count,
        )
    if status in target_statuses and (not clean_before_public_write or unsafe_public_report_written):
        result.fail(
            "s3a_prod2_target_without_clean_redaction_before_write",
            "Target claims require a clean redaction scan before public report write and must not record unsafe report publication.",
            path="public_redaction",
            expected={"clean_before_public_write": True, "unsafe_public_report_written": False},
            actual={
                "clean_before_public_write": clean_before_public_write,
                "unsafe_public_report_written": unsafe_public_report_written,
            },
        )
    if not redaction_passed and status in target_statuses:
        result.fail(
            "s3a_prod2_target_with_failed_public_redaction",
            "Public redaction failure must clear target/safe_to_merge claims and block the run.",
            path="public_redaction.passed",
            expected=True,
            actual=_get(summary, "public_redaction.passed", None),
        )
    if status == "blocked_public_redaction_failed" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_prod2_redaction_blocker_claimed_completion",
            "blocked_public_redaction_failed must not claim target_met, full_chain_complete, or safe_to_merge.",
            path="pipeline_contract.claims",
            expected="all false",
            actual=_get(summary, "pipeline_contract.claims", None),
        )
    redaction_findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown_text})
    result.details["s3a_prod2_public_redaction_finding_count"] = len(redaction_findings)
    if redaction_findings:
        result.fail(
            "s3a_prod2_public_payload_redaction_failed",
            "S3A-PROD2/S3B-D1 contract independently found forbidden public JSON or Markdown content.",
            path="public_payload",
            expected="no findings",
            actual={"finding_count": len(redaction_findings), "findings_redacted": True},
        )


def _check_phase47_s2_baseline(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    readiness_passed = _as_bool(_get(summary, "readiness.passed", False))
    schema_ensure_ran = _as_bool(_get(summary, "gate0.schema.ensure.ran", False))
    schema_missing_after = _get(summary, "gate0.schema.after.tables_missing", [])
    backup_exists = _as_bool(_get(summary, "gate0.backup_recovery.proof_exists", False))
    backup_valid = backup_exists and _as_bool(_get(summary, "gate0.backup_recovery.valid", False))
    dry_run_executed = _as_bool(_get(summary, "dynamic_sync_dry_run.executed", False))
    dry_run_status = str(_get(summary, "dynamic_sync_dry_run.status", "")).casefold()
    import_executed = _as_bool(_get(summary, "import_results.executed", False))
    classification_executed = _as_bool(_get(summary, "classification_results.executed", False))
    ai_tagging_executed = _as_bool(_get(summary, "ai_tagging_results.executed", False))
    localization_executed = _as_bool(_get(summary, "localization_results.executed", False))
    llm_called = _as_bool(_get(summary, "localization_results.llm_called", False))
    llm_approved = _as_bool(_get(summary, "readiness.llm_localization.operator_approved", False))
    execute_confirmation_present = _as_bool(_get(summary, "pipeline_contract.execute_confirmation_present", False))
    blockers = _get(summary, "readiness.blockers", [])
    if blockers is MISSING or blockers is None:
        blockers = []
    if _has(summary, "head_sha"):
        result.fail(
            "phase47_s2_ambiguous_top_level_head_sha_present",
            "S2 public reports must split validated run, report generation, and PR handoff head evidence instead of a stale top-level head_sha.",
            path="head_sha",
            expected="omitted",
            actual=_get(summary, "head_sha", None),
        )
    required_head_paths = (
        "head_evidence.validated_run_head_sha",
        "head_evidence.report_generation_head_sha",
        "head_evidence.current_pr_head_sha",
    )
    for path in required_head_paths:
        if not _has_non_null(summary, path) or not str(_get(summary, path, "")).strip():
            result.fail(
                "phase47_s2_head_evidence_missing",
                "S2 public reports must include split head evidence.",
                path=path,
                expected="non-empty",
                actual=_get(summary, path, None),
            )
    if not _as_bool(_get(summary, "head_evidence.top_level_head_sha_omitted", False)):
        result.fail(
            "phase47_s2_head_evidence_does_not_omit_top_level_sha",
            "S2 head evidence must explicitly state that ambiguous top-level head_sha is omitted.",
            path="head_evidence.top_level_head_sha_omitted",
            expected=True,
            actual=_get(summary, "head_evidence.top_level_head_sha_omitted", None),
        )
    if _get(summary, "public_redaction.passed", MISSING) is MISSING or not _as_bool(_get(summary, "public_redaction.passed", False)):
        result.fail(
            "phase47_s2_public_redaction_absent_or_failed",
            "S2 public artifacts require an explicit passing redaction proof.",
            path="public_redaction.passed",
            expected=True,
            actual=_get(summary, "public_redaction.passed", None),
        )
    if _get(summary, "private_artifacts.private_artifacts_committed", MISSING) is MISSING or _as_bool(
        _get(summary, "private_artifacts.private_artifacts_committed", False)
    ):
        result.fail(
            "phase47_s2_private_artifacts_committed_or_missing",
            "S2 private artifacts must be explicitly reported as not committed.",
            path="private_artifacts.private_artifacts_committed",
            expected=False,
            actual=_get(summary, "private_artifacts.private_artifacts_committed", None),
        )
    required_true_safety_paths = (
        "safety.no_source_icloud_mutation",
        "safety.no_cleanup_delete_reset_drop_truncate",
    )
    for path in required_true_safety_paths:
        if not _as_bool(_get(summary, path, False)):
            result.fail(
                "phase47_s2_required_safety_flag_missing_or_false",
                "S2 safety proof field must be true.",
                path=path,
                expected=True,
                actual=_get(summary, path, None),
            )
    conditional_no_execution_flags = (
        (not import_executed, "safety.no_db_import"),
        (not classification_executed, "safety.no_classification"),
        (not ai_tagging_executed, "safety.no_ai_tagging"),
        (not localization_executed and not llm_called, "safety.no_llm_call"),
    )
    for required, path in conditional_no_execution_flags:
        if required and not _as_bool(_get(summary, path, False)):
            result.fail(
                "phase47_s2_missing_no_execution_safety_flag",
                "S2 summaries that do not claim a stage executed must explicitly prove the matching no-execution safety flag.",
                path=path,
                expected=True,
                actual=_get(summary, path, None),
            )
    if readiness_passed and isinstance(blockers, list) and blockers:
        result.fail(
            "phase47_s2_readiness_passed_with_blockers",
            "Readiness cannot pass while blockers are present.",
            path="readiness.blockers",
            expected=[],
            actual=blockers,
        )
    if readiness_passed:
        readiness_required_true_paths = (
            "readiness.python_env.check_python_env_passed",
            "readiness.app_settings_db_identity_matches_execution_db",
            "readiness.production_storage.explicitly_set",
            "readiness.backup_recovery.valid",
            "readiness.ai_model.model_downloaded",
            "readiness.automatic_production_sync.remains_opt_in",
            "readiness.proper_noun_safeguards.unreviewed_llm_aliases_excluded_from_search",
        )
        for path in readiness_required_true_paths:
            if not _as_bool(_get(summary, path, False)):
                result.fail(
                    "phase47_s2_readiness_claim_not_independently_proven",
                    "Gate 1 readiness.passed=true must be backed by independent readiness proof fields.",
                    path=path,
                    expected=True,
                    actual=_get(summary, path, None),
                )
        if _get(summary, "readiness.dynamic_schema.tables_missing_count", 0) not in (0, "0"):
            result.fail(
                "phase47_s2_readiness_claim_schema_missing",
                "Gate 1 readiness.passed=true cannot coexist with missing dynamic sync tables.",
                path="readiness.dynamic_schema.tables_missing_count",
                expected=0,
                actual=_get(summary, "readiness.dynamic_schema.tables_missing_count", None),
            )
        if int(_get(summary, "readiness.input_root_counts.valid_count", 0) or 0) <= 0:
            result.fail(
                "phase47_s2_readiness_claim_no_valid_source_root",
                "Gate 1 readiness.passed=true requires at least one valid active source root.",
                path="readiness.input_root_counts.valid_count",
                expected=">0",
                actual=_get(summary, "readiness.input_root_counts.valid_count", None),
            )
    if not readiness_passed and _completion_or_approval_claimed(result):
        result.fail(
            "phase47_s2_completion_claimed_with_gate1_blocker",
            "S2 cannot claim completion, route approval, or safe_to_merge when Gate 1 readiness failed.",
            path="readiness.passed",
            expected=True,
            actual=False,
        )
    if schema_ensure_ran and not backup_valid:
        result.fail(
            "phase47_s2_schema_ensure_without_valid_backup_proof",
            "S2 schema setup cannot run or be claimed without valid backup proof.",
            path="gate0.backup_recovery.valid",
            expected=True,
            actual=_get(summary, "gate0.backup_recovery.valid", None),
        )
    if schema_ensure_ran and schema_missing_after:
        result.fail(
            "phase47_s2_schema_tables_missing_after_ensure",
            "S2 schema ensure cannot pass while required dynamic sync tables remain missing.",
            path="gate0.schema.after.tables_missing",
            expected=[],
            actual=schema_missing_after,
        )
    if import_executed and not backup_valid:
        result.fail(
            "phase47_s2_import_claimed_without_valid_backup_proof",
            "S2 import cannot be claimed without valid backup proof.",
            path="gate0.backup_recovery.valid",
            expected=True,
            actual=_get(summary, "gate0.backup_recovery.valid", None),
        )
    if (import_executed or classification_executed or ai_tagging_executed or localization_executed or llm_called) and not execute_confirmation_present:
        result.fail(
            "phase47_s2_execution_claimed_without_exact_confirmation",
            "S2 execution stages require the exact execute confirmation in pipeline_contract.execute_confirmation_present.",
            path="pipeline_contract.execute_confirmation_present",
            expected=True,
            actual=_get(summary, "pipeline_contract.execute_confirmation_present", None),
        )
    if import_executed and not (dry_run_executed and dry_run_status in {"completed", "passed"}):
        result.fail(
            "phase47_s2_import_claimed_without_fresh_dry_run",
            "S2 import cannot be claimed without a completed fresh dynamic sync dry-run.",
            path="dynamic_sync_dry_run.status",
            expected=["completed", "passed"],
            actual=_get(summary, "dynamic_sync_dry_run.status", None),
        )
    if import_executed and not _as_bool(_get(summary, "import_results.per_item_ledgers_written", False)):
        result.fail(
            "phase47_s2_import_missing_per_item_ledgers",
            "S2 import claims require per-item ledgers/failure accounting.",
            path="import_results.per_item_ledgers_written",
            expected=True,
            actual=_get(summary, "import_results.per_item_ledgers_written", None),
        )
    if import_executed and _as_bool(_get(summary, "import_results.hydration_failure_budget.threshold_exceeded", False)):
        result.fail(
            "phase47_s2_hydration_failure_budget_exceeded",
            "S2 cannot claim a passing import when the hydration/read failure budget is exceeded.",
            path="import_results.hydration_failure_budget.threshold_exceeded",
            expected=False,
            actual=True,
        )
    if import_executed and _as_bool(_get(summary, "import_results.import_failure_budget.threshold_exceeded", False)):
        result.fail(
            "phase47_s2_import_failure_budget_exceeded",
            "S2 cannot claim a passing import when the import failure budget is exceeded.",
            path="import_results.import_failure_budget.threshold_exceeded",
            expected=False,
            actual=True,
        )
    if classification_executed:
        if _get(summary, "classification_results.failure_budget.threshold_exceeded", MISSING) is MISSING:
            result.fail(
                "phase47_s2_classification_failure_budget_missing",
                "S2 classification execution requires explicit failure-budget proof.",
                path="classification_results.failure_budget.threshold_exceeded",
                expected=False,
                actual=None,
            )
        elif _as_bool(_get(summary, "classification_results.failure_budget.threshold_exceeded", False)):
            result.fail(
                "phase47_s2_classification_failure_budget_exceeded",
                "S2 cannot claim passing classification when its failure budget is exceeded.",
                path="classification_results.failure_budget.threshold_exceeded",
                expected=False,
                actual=True,
            )
    if ai_tagging_executed and _as_bool(_get(summary, "ai_tagging_results.failure_budget.threshold_exceeded", False)):
        result.fail(
            "phase47_s2_ai_failure_budget_exceeded",
            "S2 cannot claim passing AI tagging when the AI failure budget is exceeded.",
            path="ai_tagging_results.failure_budget.threshold_exceeded",
            expected=False,
            actual=True,
        )
    if localization_executed and _as_bool(_get(summary, "localization_results.failure_budget.threshold_exceeded", False)):
        result.fail(
            "phase47_s2_localization_failure_budget_exceeded",
            "S2 cannot claim passing localization when the localization failure budget is exceeded.",
            path="localization_results.failure_budget.threshold_exceeded",
            expected=False,
            actual=True,
        )
    if localization_executed and _get(summary, "localization_results.stopped_by_rule", None) == "localization_max_tags_reached":
        if _get(summary, "localization_results.status", None) != "partial_localization_max_tags_reached":
            result.fail(
                "phase47_s2_capped_localization_status_not_partial",
                "Capped S2 localization runs must report a partial status.",
                path="localization_results.status",
                expected="partial_localization_max_tags_reached",
                actual=_get(summary, "localization_results.status", None),
            )
        if _completion_or_approval_claimed(result) or _as_bool(_get(summary, "localization_results.target_met", False)):
            result.fail(
                "phase47_s2_capped_localization_claimed_complete",
                "Capped S2 localization runs must not claim target_met, safe_to_merge, or full-chain completion.",
                path="localization_results.stopped_by_rule",
                expected="no completion claim when localization_max_tags_reached",
                actual="localization_max_tags_reached",
            )
    if llm_called and not llm_approved:
        result.fail(
            "phase47_s2_llm_called_without_operator_approval",
            "S2 LLM localization claims require explicit operator approval.",
            path="readiness.llm_localization.operator_approved",
            expected=True,
            actual=False,
        )
    if llm_called:
        if not _has(summary, "llm_localization_audit.provider_call_count_lower_bound"):
            result.fail(
                "phase47_s2_llm_audit_missing",
                "S2 LLM localization claims require an explicit provider-call audit, including background auto-translation if observed.",
                path="llm_localization_audit.provider_call_count_lower_bound",
                expected="present",
                actual=None,
            )
        if _as_bool(_get(summary, "llm_localization_audit.provider_calls_undercounted", False)):
            result.fail(
                "phase47_s2_llm_provider_calls_undercounted",
                "S2 public summaries must not knowingly undercount LLM provider calls.",
                path="llm_localization_audit.provider_calls_undercounted",
                expected=False,
                actual=True,
            )
        ai_stage_suppresses_auto = _as_bool(
            _get(summary, "ai_tagging_results.auto_translation_suppressed_during_ai_stage", False)
        ) or _as_bool(
            _get(summary, "llm_localization_audit.current_runner_suppresses_auto_translation_during_ai_stage", False)
        )
        background_calls_ledgered = _as_bool(_get(summary, "llm_localization_audit.background_provider_calls_ledgered", False))
        if ai_tagging_executed and not (ai_stage_suppresses_auto or background_calls_ledgered):
            result.fail(
                "phase47_s2_unledgered_background_auto_translation_not_prevented",
                "S2 AI tagging must suppress auto-translation side effects or prove background provider calls are ledgered.",
                path="llm_localization_audit.current_runner_suppresses_auto_translation_during_ai_stage",
                expected=True,
                actual=False,
            )
    if int(_get(summary, "gate0.input_root_registration.registered_count", 0) or 0) > 0:
        if not backup_valid or not _as_bool(_get(summary, "gate0.db_identity.matches_expected_database", True)) or not _as_bool(
            _get(summary, "gate0.storage_identity.matches_expected", True)
        ):
            result.fail(
                "phase47_s2_source_root_write_without_clean_identity_or_backup",
                "S2 source-root registration writes require clean DB/storage identity gates and valid backup proof.",
                path="gate0.input_root_registration.registered_count",
                expected="0 unless identity gates and backup are clean",
                actual=_get(summary, "gate0.input_root_registration.registered_count", None),
            )
    if _completion_or_approval_claimed(result):
        if not _as_bool(_get(summary, "dynamic_sync_dry_run.source_scope_check.passed", False)):
            result.fail(
                "phase47_s2_full_completion_claimed_without_full_scope_dry_run",
                "Full S2 completion requires a full-scope dry-run; partial/source-scope mismatch dry-runs cannot claim execution complete.",
                path="dynamic_sync_dry_run.source_scope_check.passed",
                expected=True,
                actual=_get(summary, "dynamic_sync_dry_run.source_scope_check.passed", None),
            )
        expected_statuses = {
            "dynamic_sync_dry_run.status": {"completed", "passed"},
            "import_results.status": {"completed", "completed_with_item_failures_within_budget"},
            "classification_results.status": {"completed", "completed_with_item_failures_within_budget"},
            "ai_tagging_results.status": {"completed", "completed_with_item_failures_within_budget"},
            "localization_results.status": {"completed", "completed_with_gap_visible"},
            "browser_validation.status": {"passed"},
        }
        for path, allowed in expected_statuses.items():
            value = str(_get(summary, path, "")).casefold()
            if value not in allowed:
                result.fail(
                    "phase47_s2_required_stage_not_complete",
                    "S2 completion claims require every baseline stage to complete or pass.",
                    path=path,
                    expected=sorted(allowed),
                    actual=_get(summary, path, None),
                )
        required_full_execution_flags = (
            ("dynamic_sync_dry_run.executed", True),
            ("import_results.executed", True),
            ("import_results.per_item_ledgers_written", True),
            ("classification_results.executed", True),
            ("ai_tagging_results.executed", True),
            ("browser_validation.status", "passed"),
        )
        for path, expected in required_full_execution_flags:
            actual = _get(summary, path, None)
            if expected == "passed":
                ok = str(actual).casefold() == "passed"
            else:
                ok = _as_bool(actual) is expected
            if not ok:
                result.fail(
                    "phase47_s2_full_completion_missing_executed_proof",
                    "Full S2 completion claims require executed proof for every baseline stage.",
                    path=path,
                    expected=expected,
                    actual=actual,
                )
        localization_gap_reported = _as_bool(_get(summary, "localization_results.gap_report_generated", False)) or str(
            _get(summary, "localization_results.status", "")
        ).casefold() == "completed_with_gap_visible"
        if not (localization_executed or localization_gap_reported):
            result.fail(
                "phase47_s2_full_completion_missing_localization_or_gap_report",
                "Full S2 completion requires localization execution or an explicit localization gap report.",
                path="localization_results",
                expected="executed or gap_report_generated",
                actual=_get(summary, "localization_results", None),
            )
    if _as_bool(_get(summary, "localization_results.proper_noun_unreviewed_aliases_trusted", False)):
        result.fail(
            "phase47_s2_unreviewed_proper_noun_alias_trusted",
            "Unreviewed proper-noun LLM aliases must not be trusted into Chinese search.",
            path="localization_results.proper_noun_unreviewed_aliases_trusted",
            expected=False,
            actual=True,
        )
    forbidden_safety_paths = (
        "safety.source_icloud_mutation",
        "safety.cleanup_delete_reset_drop_truncate",
        "safety.sourceconcept_entity_resolver_similarity",
    )
    for path in forbidden_safety_paths:
        if _as_bool(_get(summary, path, False)):
            result.fail("phase47_s2_forbidden_safety_flag", "S2 summary reports a forbidden safety flag.", path=path, expected=False, actual=True)


def _check_s3a_m2_production_delta_e2e(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "dry_run_complete_pending_approval",
        "target_met",
        "completed_partial_gpu_validation",
        "completed_with_followup_required",
        "blocked_readiness",
        "blocked_delta_cap_exceeded",
        "blocked_execute_not_completed",
        "blocked_localization_incomplete",
        "blocked_public_redaction_failed",
    }
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s3a_m2_unknown_status",
            "S3A-M2 status must explicitly report dry-run pending approval, target_met, partial completion, or the blocking condition.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status != "target_met" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_m2_non_target_status_claimed_completion",
            "Only S3A-M2 target_met may claim target_met, safe_to_merge, route approval, or full-chain completion.",
            path="pipeline_contract.status",
            expected="target_met for completion claims",
            actual=result.status,
        )
    if str(_get(summary, "pipeline_contract.phase_identity", "") or "") != "S3A-M2":
        result.fail(
            "s3a_m2_phase_identity_mismatch",
            "S3A-M2 summaries must declare the exact phase identity.",
            path="pipeline_contract.phase_identity",
            expected="S3A-M2",
            actual=_get(summary, "pipeline_contract.phase_identity", None),
        )

    postmortem_required = status != "dry_run_complete_pending_approval"
    if postmortem_required:
        required_postmortem_sections = {
            "failure_timeline": list,
            "deferred_failed_inventory": Mapping,
            "gui_hang_root_cause": Mapping,
            "api_vs_gui_divergence": Mapping,
            "branch_profile_provenance": Mapping,
            "scanner_incremental_model": Mapping,
            "priority_backlog_root_cause": Mapping,
            "local_copy_repeated_incremental_e2e": Mapping,
            "localization_diagnosis": Mapping,
            "unsupported_inventory": Mapping,
            "manual_sync_safety_judgement": Mapping,
            "remaining_blockers": list,
        }
        for section_path, expected_type in required_postmortem_sections.items():
            value = _get(summary, section_path, MISSING)
            if value is MISSING or not isinstance(value, expected_type):
                result.fail(
                    "s3a_m2_postmortem_section_missing",
                    "Post-execute S3A-M2 summaries must include structured incident/postmortem sections.",
                    path=section_path,
                    expected=expected_type.__name__,
                    actual=None if value is MISSING else type(value).__name__,
                )

        timeline = _get(summary, "failure_timeline", [])
        if isinstance(timeline, list) and not timeline:
            result.fail(
                "s3a_m2_failure_timeline_empty",
                "S3A-M2 postmortem must include a chronological failure/correction timeline.",
                path="failure_timeline",
                expected="non-empty list",
                actual=[],
            )
        elif isinstance(timeline, list):
            required_event_fields = {"event", "what_happened", "detected_by", "why_earlier_evidence_missed", "production_impact", "repair_or_prevention"}
            for index, item in enumerate(timeline):
                if not isinstance(item, Mapping):
                    result.fail(
                        "s3a_m2_failure_timeline_event_invalid",
                        "Every S3A-M2 timeline event must be a structured public-safe object.",
                        path=f"failure_timeline[{index}]",
                        expected="mapping",
                        actual=type(item).__name__,
                    )
                    continue
                missing_event_fields = sorted(field for field in required_event_fields if not item.get(field))
                if missing_event_fields:
                    result.fail(
                        "s3a_m2_failure_timeline_event_incomplete",
                        "Every S3A-M2 timeline event must explain detection, missed evidence, impact, and repair/prevention.",
                        path=f"failure_timeline[{index}]",
                        expected=sorted(required_event_fields),
                        actual=missing_event_fields,
                    )

        deferred_inventory = _get(summary, "deferred_failed_inventory", {})
        if isinstance(deferred_inventory, Mapping):
            required_deferred_paths = (
                "source_field",
                "query_scope",
                "total",
                "reason_counts",
                "pipeline_status_counts",
                "current_actionable_importable_pending",
                "current_placeholder_reason_count",
                "ui_recommendation",
            )
            for key in required_deferred_paths:
                if key not in deferred_inventory:
                    result.fail(
                        "s3a_m2_deferred_failed_inventory_incomplete",
                        "S3A-M2 must explain the Web Admin deferred/failed inventory instead of leaving the number uninterpreted.",
                        path=f"deferred_failed_inventory.{key}",
                        expected="present",
                        actual=None,
                    )
            if status == "target_met":
                if _as_int(deferred_inventory.get("current_actionable_importable_pending", 0)) != 0:
                    result.fail(
                        "s3a_m2_target_with_actionable_deferred_inventory",
                        "S3A-M2 target_met cannot hide currently importable work inside the deferred/failed inventory.",
                        path="deferred_failed_inventory.current_actionable_importable_pending",
                        expected=0,
                        actual=deferred_inventory.get("current_actionable_importable_pending"),
                    )
                if _as_int(deferred_inventory.get("current_placeholder_reason_count", 0)) != 0:
                    result.fail(
                        "s3a_m2_target_with_current_placeholder_inventory",
                        "S3A-M2 target_met cannot leave current placeholder work inside the deferred/failed inventory.",
                        path="deferred_failed_inventory.current_placeholder_reason_count",
                        expected=0,
                        actual=deferred_inventory.get("current_placeholder_reason_count"),
                    )

        gui_root = _get(summary, "gui_hang_root_cause", {})
        if isinstance(gui_root, Mapping):
            for key in ("endpoint_called", "root_cause", "backend_request_sent", "backend_kept_scanning", "cleanup_performed", "watchdog_timeout_added"):
                if key not in gui_root:
                    result.fail(
                        "s3a_m2_gui_hang_root_cause_incomplete",
                        "S3A-M2 GUI hang analysis must include endpoint, backend activity, cleanup, and watchdog evidence.",
                        path=f"gui_hang_root_cause.{key}",
                        expected="present",
                        actual=None,
                    )

        api_gui = _get(summary, "api_vs_gui_divergence", {})
        if isinstance(api_gui, Mapping):
            for key in ("runner_gui_planner_diverged", "api_runner_proved_backend_only", "prevention_added"):
                if key not in api_gui:
                    result.fail(
                        "s3a_m2_api_gui_divergence_incomplete",
                        "S3A-M2 must explain why API/runner evidence did not prove the GUI workflow.",
                        path=f"api_vs_gui_divergence.{key}",
                        expected="present",
                        actual=None,
                    )

        provenance = _get(summary, "branch_profile_provenance", {})
        if isinstance(provenance, Mapping):
            summary_head = str(_get(summary, "head_sha", "") or "")
            provenance_head = str(provenance.get("head_sha") or "")
            if summary_head and provenance_head and provenance_head != summary_head:
                result.fail(
                    "s3a_m2_branch_profile_head_mismatch",
                    "Branch/profile provenance must match the report head SHA.",
                    path="branch_profile_provenance.head_sha",
                    expected=summary_head,
                    actual=provenance_head,
                )
            for key in ("branch", "head_sha", "profile_id", "db_name", "violet_env", "stale_process_cleanup_status"):
                if not provenance.get(key):
                    result.fail(
                        "s3a_m2_branch_profile_provenance_incomplete",
                        "S3A-M2 must record branch/head/profile/server provenance for GUI validation.",
                        path=f"branch_profile_provenance.{key}",
                        expected="non-empty",
                        actual=provenance.get(key),
                    )

        scanner_model = _get(summary, "scanner_incremental_model", {})
        if isinstance(scanner_model, Mapping):
            required_scanner_paths = (
                "model",
                "durable_state_tables",
                "durable_global_filesystem_cursor",
                "starts_from_root_each_run",
                "stable_known_files_fast_skipped_without_hash",
                "hash_only_when",
                "cap_semantics",
                "next_batch_continuation",
                "invalidation_policy",
            )
            for key in required_scanner_paths:
                if key not in scanner_model:
                    result.fail(
                        "s3a_m2_scanner_incremental_model_incomplete",
                        "S3A-M2 must explain the durable source-ledger/checkpoint model that prevents repeated root-wide duplicate hashing.",
                        path=f"scanner_incremental_model.{key}",
                        expected="present",
                        actual=None,
                    )
            if status == "target_met":
                if not _as_bool(scanner_model.get("stable_known_files_fast_skipped_without_hash")):
                    result.fail(
                        "s3a_m2_scanner_does_not_fast_skip_stable_known_files",
                        "S3A-M2 target_met requires stable known files to be skipped by source ledger metadata without content hashing.",
                        path="scanner_incremental_model.stable_known_files_fast_skipped_without_hash",
                        expected=True,
                        actual=scanner_model.get("stable_known_files_fast_skipped_without_hash"),
                    )
                cap_semantics = str(scanner_model.get("cap_semantics") or "")
                if "actionable" not in cap_semantics or "unchanged" not in cap_semantics:
                    result.fail(
                        "s3a_m2_scanner_cap_semantics_not_actionable",
                        "S3A-M2 cap semantics must be based on actionable candidates and must not be consumed by unchanged existing media.",
                        path="scanner_incremental_model.cap_semantics",
                        expected="actionable candidates; unchanged existing media excluded",
                        actual=cap_semantics,
                    )

        priority_backlog = _get(summary, "priority_backlog_root_cause", {})
        if isinstance(priority_backlog, Mapping):
            required_priority_paths = (
                "table",
                "root_public_ref",
                "total_priority_workset_rows",
                "legacy_pending_changed_rows",
                "legacy_pending_changed_outside_safety_window",
                "rows_matching_existing_media",
                "rows_imported_but_still_pending_or_changed",
                "rows_that_should_be_actionable_now",
                "rows_that_need_repair_or_migration",
                "root_cause",
                "repair_migration_plan",
                "production_db_repair_executed",
            )
            for key in required_priority_paths:
                if key not in priority_backlog:
                    result.fail(
                        "s3a_m2_priority_backlog_root_cause_incomplete",
                        "S3A-M2 must explain the priority workset backlog root cause and repair/migration posture.",
                        path=f"priority_backlog_root_cause.{key}",
                        expected="present",
                        actual=None,
                    )
            repair_plan = priority_backlog.get("repair_migration_plan")
            if isinstance(repair_plan, Mapping):
                for key in ("candidate_count", "candidate_condition", "requires_owner_approval", "would_modify_db", "validation_after_repair"):
                    if key not in repair_plan:
                        result.fail(
                            "s3a_m2_priority_backlog_repair_plan_incomplete",
                            "S3A-M2 priority backlog repair must include dry-run conditions, approval requirement, and validation plan.",
                            path=f"priority_backlog_root_cause.repair_migration_plan.{key}",
                            expected="present",
                            actual=None,
                        )

        local_copy_e2e = _get(summary, "local_copy_repeated_incremental_e2e", {})
        if isinstance(local_copy_e2e, Mapping):
            required_local_copy_paths = (
                "status",
                "bulk_run_alone_sufficient",
                "completed",
                "scenario_count",
                "pass_criteria_failures",
                "plan_expensive_ops_zero_all_cycles",
                "browser_normal_flow_passed",
                "source_originals_mutated",
                "production_db_used",
                "user_retry_recommended",
            )
            for key in required_local_copy_paths:
                if key not in local_copy_e2e:
                    result.fail(
                        "s3a_m2_local_copy_incremental_e2e_incomplete",
                        "S3A-M2 must report the repeated local-copy incremental E2E status before recommending production GUI retry.",
                        path=f"local_copy_repeated_incremental_e2e.{key}",
                        expected="present",
                        actual=None,
                    )
            retry_recommended = _as_bool(local_copy_e2e.get("user_retry_recommended", False))
            if retry_recommended:
                if not _as_bool(local_copy_e2e.get("completed", False)):
                    result.fail(
                        "s3a_m2_retry_recommended_without_local_copy_e2e",
                        "User production GUI retry must not be recommended before the repeated local-copy incremental E2E completes.",
                        path="local_copy_repeated_incremental_e2e.completed",
                        expected=True,
                        actual=local_copy_e2e.get("completed"),
                    )
                failures = local_copy_e2e.get("pass_criteria_failures")
                if failures:
                    result.fail(
                        "s3a_m2_retry_recommended_with_local_copy_failures",
                        "User production GUI retry must not be recommended while local-copy incremental E2E pass criteria failed.",
                        path="local_copy_repeated_incremental_e2e.pass_criteria_failures",
                        expected=[],
                        actual=failures,
                    )
                if _as_int(local_copy_e2e.get("scenario_count", 0)) < 10:
                    result.fail(
                        "s3a_m2_retry_recommended_without_required_incremental_cycles",
                        "User production GUI retry requires the repeated incremental E2E cycle set, not a one-time bulk import.",
                        path="local_copy_repeated_incremental_e2e.scenario_count",
                        expected=">=10",
                        actual=local_copy_e2e.get("scenario_count"),
                    )
                if not _as_bool(local_copy_e2e.get("plan_expensive_ops_zero_all_cycles", False)):
                    result.fail(
                        "s3a_m2_retry_recommended_with_expensive_plan_ops",
                        "Normal manual sync Plan must remain metadata-only before recommending production GUI retry.",
                        path="local_copy_repeated_incremental_e2e.plan_expensive_ops_zero_all_cycles",
                        expected=True,
                        actual=local_copy_e2e.get("plan_expensive_ops_zero_all_cycles"),
                    )
                if not _as_bool(local_copy_e2e.get("browser_normal_flow_passed", False)):
                    result.fail(
                        "s3a_m2_retry_recommended_without_browser_normal_flow",
                        "User production GUI retry requires real browser evidence for the normal Start manual sync flow.",
                        path="local_copy_repeated_incremental_e2e.browser_normal_flow_passed",
                        expected=True,
                        actual=local_copy_e2e.get("browser_normal_flow_passed"),
                    )
                if _as_bool(local_copy_e2e.get("bulk_run_alone_sufficient", True)):
                    result.fail(
                        "s3a_m2_retry_recommended_from_bulk_only_evidence",
                        "A one-time bulk local-copy run is not sufficient evidence for production GUI retry.",
                        path="local_copy_repeated_incremental_e2e.bulk_run_alone_sufficient",
                        expected=False,
                        actual=local_copy_e2e.get("bulk_run_alone_sufficient"),
                    )
                if _as_bool(local_copy_e2e.get("source_originals_mutated", False)) or _as_bool(local_copy_e2e.get("production_db_used", False)):
                    result.fail(
                        "s3a_m2_local_copy_e2e_safety_violation",
                        "Local-copy E2E must not mutate original source/iCloud files or use the production DB.",
                        path="local_copy_repeated_incremental_e2e",
                        expected="source_originals_mutated=false and production_db_used=false",
                        actual={
                            "source_originals_mutated": local_copy_e2e.get("source_originals_mutated"),
                            "production_db_used": local_copy_e2e.get("production_db_used"),
                        },
                    )

        safety_judgement = _get(summary, "manual_sync_safety_judgement", {})
        if isinstance(safety_judgement, Mapping):
            safety_status = str(safety_judgement.get("status") or "")
            allowed_safety_statuses = {
                "manual_sync_safe_for_normal_use",
                "manual_sync_safe_with_operator_checks",
                "manual_sync_not_yet_safe_gui_execute_unvalidated",
                "manual_sync_not_safe_blockers_remaining",
            }
            if safety_status not in allowed_safety_statuses:
                result.fail(
                    "s3a_m2_manual_sync_safety_judgement_missing",
                    "S3A-M2 must include one of the explicit manual sync safety judgement statuses.",
                    path="manual_sync_safety_judgement.status",
                    expected=sorted(allowed_safety_statuses),
                    actual=safety_status,
                )
            if status == "target_met" and safety_status not in {"manual_sync_safe_for_normal_use", "manual_sync_safe_with_operator_checks"}:
                result.fail(
                    "s3a_m2_target_without_safe_manual_sync_judgement",
                    "S3A-M2 target_met requires an evidence-based judgement that manual sync is safe for use.",
                    path="manual_sync_safety_judgement.status",
                    expected=["manual_sync_safe_for_normal_use", "manual_sync_safe_with_operator_checks"],
                    actual=safety_status,
                )
            if safety_status in {"manual_sync_safe_for_normal_use", "manual_sync_safe_with_operator_checks"}:
                if not _as_bool(safety_judgement.get("evidence_based", False)):
                    result.fail(
                        "s3a_m2_manual_sync_safe_without_evidence",
                        "Manual sync cannot be marked safe without evidence-based engineering judgement.",
                        path="manual_sync_safety_judgement.evidence_based",
                        expected=True,
                        actual=safety_judgement.get("evidence_based"),
                    )
                if not _as_bool(_get(summary, "launcher_web_admin_acceptance.gui_execute_completed", False)):
                    result.fail(
                        "s3a_m2_manual_sync_safe_without_gui_execute",
                        "Manual sync cannot be marked safe for normal use until a GUI Execute run is validated.",
                        path="launcher_web_admin_acceptance.gui_execute_completed",
                        expected=True,
                        actual=_get(summary, "launcher_web_admin_acceptance.gui_execute_completed", None),
                    )

        if status != "target_met":
            blockers = _get(summary, "remaining_blockers", [])
            if isinstance(blockers, list) and not blockers:
                result.fail(
                    "s3a_m2_non_target_without_remaining_blockers",
                    "A non-target S3A-M2 postmortem summary must explicitly list the remaining blockers.",
                    path="remaining_blockers",
                    expected="non-empty list",
                    actual=[],
                )

        incident = _get(summary, "ai_tag_assignment_incident", {})
        if _as_int(_get(summary, "final_totals.ai_tagged", _get(summary, "ai_tagging.count", 0))) > 0:
            if not isinstance(incident, Mapping):
                result.fail(
                    "s3a_m2_ai_tag_semantic_validation_missing",
                    "S3A-M2 cannot use AI-tagged counts as proof without assignment-level semantic validation.",
                    path="ai_tag_assignment_incident",
                    expected="mapping",
                    actual=type(incident).__name__,
                )
            else:
                after = incident.get("after") if isinstance(incident.get("after"), Mapping) else {}
                if not after:
                    result.fail(
                        "s3a_m2_ai_tag_semantic_validation_missing",
                        "S3A-M2 cannot use AI-tagged counts as proof without before/after assignment semantics.",
                        path="ai_tag_assignment_incident.after",
                        expected="mapping",
                        actual=after,
                    )

    _check_required_boolean_paths(
        summary,
        result,
        (
            "pipeline_contract.fresh_dry_run_completed",
            "source.paths_redacted",
            "registered_roots_public.paths_redacted",
            "controlled_delta.hydrated_only",
            "api_runner_acceptance.dry_run_plan_generated",
            "classification.reported",
            "ai_tagging.reported",
            "ai_tagging.mature_media_tag_policy",
            "ai_tagging.no_sourceconcept_or_entity_truth_from_ai_only_tags",
            "public_redaction.passed",
        ),
        code="s3a_m2_required_proof_missing",
        message="S3A-M2 requires fresh dry-run, hydrated-only source handling, stage accounting, mature AI media-tag policy proof, AI-only Entity/SourceConcept safeguards, and public redaction proof.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "controlled_delta.silently_truncated",
            "private_artifacts.private_artifacts_committed",
            "safety.automatic_sync_enabled",
            "safety.scheduled_sync_enabled",
            "safety.startup_sync_enabled",
            "safety.system_service_enabled",
            "safety.source_icloud_mutation",
            "safety.source_mutation_attempted",
            "safety.provider_pixiv_gallery_dl_saucenao_google_calls",
            "safety.sourceconcept_entity_bridge",
            "safety.cleanup_delete_reset_drop_truncate",
            "safety.full_library_reimport",
            "safety.private_paths_or_hashes_in_public_report",
        ),
        code="s3a_m2_forbidden_scope_or_mutation",
        message="S3A-M2 must keep unattended sync, source/provider expansion, source/iCloud mutation, full-library reimport, destructive cleanup, and private public-report leaks disabled.",
    )

    cap = _as_int(_get(summary, "controlled_delta.cap", 0))
    total_seen = _as_int(_get(summary, "dry_run.total_seen", 0))
    cap_exceeded = _as_bool(_get(summary, "controlled_delta.cap_exceeded", False))
    if not (6 <= cap <= 1000):
        result.fail(
            "s3a_m2_delta_cap_out_of_bounds",
            "S3A-M2 controlled delta cap must be explicitly above the M1 micro-batch cap and no higher than 1000.",
            path="controlled_delta.cap",
            expected="6..1000",
            actual=cap,
        )
    if total_seen > cap and not cap_exceeded:
        result.fail(
            "s3a_m2_cap_exceeded_not_reported",
            "Dry-run counts above the configured cap must set controlled_delta.cap_exceeded.",
            path="controlled_delta.cap_exceeded",
            expected=True,
            actual=False,
        )
    if cap_exceeded and status != "blocked_delta_cap_exceeded":
        result.fail(
            "s3a_m2_cap_exceeded_wrong_status",
            "Cap-exceeded dry-runs must stop with blocked_delta_cap_exceeded.",
            path="pipeline_contract.status",
            expected="blocked_delta_cap_exceeded",
            actual=result.status,
        )
    if cap_exceeded and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_m2_cap_exceeded_claimed_completion",
            "S3A-M2 cannot claim completion when the production delta exceeded the explicit cap.",
            path="controlled_delta.cap_exceeded",
            expected=False,
            actual=True,
        )

    execute_requested = _as_bool(_get(summary, "pipeline_contract.execute_after_approval", False))
    production_performed = _as_bool(_get(summary, "production_acceptance.performed", False))
    if execute_requested and not _as_bool(_get(summary, "pipeline_contract.exact_operator_approval_present", False)):
        result.fail(
            "s3a_m2_execute_without_exact_operator_approval",
            "S3A-M2 execute requires the exact phase-specific operator approval phrase.",
            path="pipeline_contract.exact_operator_approval_present",
            expected=True,
            actual=False,
        )
    if production_performed and not _as_bool(_get(summary, "api_runner_acceptance.execute_ran", False)):
        result.fail(
            "s3a_m2_production_acceptance_without_execute",
            "Production acceptance cannot be marked performed unless the API/runner execute path ran.",
            path="api_runner_acceptance.execute_ran",
            expected=True,
            actual=False,
        )
    execute_status_for_localization = str(_get(summary, "execute.status", "") or "").casefold()
    if execute_status_for_localization != "completed" and (
        _as_bool(_get(summary, "localization.executed", False)) or _as_bool(_get(summary, "localization.llm_called", False))
    ):
        result.fail(
            "s3a_m2_localization_ran_without_completed_execute",
            "S3A-M2 localization must not run unless manual execute completed successfully.",
            path="localization.executed",
            expected=False,
            actual=_get(summary, "localization.executed", None),
        )
    if production_performed:
        env_name = str(_get(summary, "readiness.production_settings.violet_env", "") or "").casefold()
        db_name = str(_get(summary, "readiness.production_settings.db_name", "") or "")
        db_name_normalized = db_name.casefold()
        if env_name and env_name != "production":
            result.fail(
                "s3a_m2_production_acceptance_not_production_env",
                "Production acceptance must not be claimed from a non-production VIOLET_ENV.",
                path="readiness.production_settings.violet_env",
                expected="production",
                actual=env_name,
            )
        if not db_name:
            result.fail(
                "s3a_m2_production_db_identity_missing",
                "Production acceptance must report the resolved DB identity so test DB execution cannot pass as production.",
                path="readiness.production_settings.db_name",
                expected="non-empty production DB name",
                actual=db_name,
            )
        elif db_name_normalized == "blombooru_test" or db_name_normalized.endswith("_test") or db_name_normalized.startswith("test_"):
            result.fail(
                "s3a_m2_production_acceptance_on_test_db",
                "Production acceptance must fail closed when the resolved DB identity is a test database.",
                path="readiness.production_settings.db_name",
                expected="production DB name",
                actual=db_name,
            )
        if str(_get(summary, "execute.status", "")).casefold() != "completed":
            result.fail(
                "s3a_m2_execute_not_completed",
                "Performed production acceptance requires a completed manual sync execute run.",
                path="execute.status",
                expected="completed",
                actual=_get(summary, "execute.status", None),
            )
        if not _as_bool(_get(summary, "ledger_consistency.passed", False)):
            result.fail(
                "s3a_m2_ledger_consistency_failed",
                "Performed production acceptance requires ledger consistency proof.",
                path="ledger_consistency.passed",
                expected=True,
                actual=_get(summary, "ledger_consistency.passed", None),
            )
        localization_status = str(_get(summary, "localization.status", "")).casefold()
        if localization_status not in {"completed", "completed_noop_no_candidates"}:
            result.fail(
                "s3a_m2_localization_not_complete",
                "Performed production acceptance requires completed localization or an explicit no-candidate completion.",
                path="localization.status",
                expected=["completed", "completed_noop_no_candidates"],
                actual=_get(summary, "localization.status", None),
            )
        if _as_int(_get(summary, "localization.failed", 0)) != 0:
            result.fail(
                "s3a_m2_localization_failures_present",
                "Target production acceptance cannot include failed localization rows.",
                path="localization.failed",
                expected=0,
                actual=_get(summary, "localization.failed", None),
            )
        loc_diag = str(_get(summary, "localization_diagnosis.diagnosis", "") or "")
        if loc_diag and loc_diag != "benign_all_localizable_tags_already_localized_or_newly_localized":
            result.fail(
                "s3a_m2_localization_diagnosis_not_benign",
                "Localization diagnosis must explain that remaining localizable tag gaps are resolved or benign.",
                path="localization_diagnosis.diagnosis",
                expected="benign_all_localizable_tags_already_localized_or_newly_localized",
                actual=loc_diag,
            )
        if _as_int(_get(summary, "localization_diagnosis.tags_requiring_localization_after_runner", 0)) != 0:
            result.fail(
                "s3a_m2_localization_gap_remaining",
                "S3A-M2 cannot leave localizable tag gaps unreported or unresolved.",
                path="localization_diagnosis.tags_requiring_localization_after_runner",
                expected=0,
                actual=_get(summary, "localization_diagnosis.tags_requiring_localization_after_runner", None),
            )
        if str(_get(summary, "localization.status", "") or "") == "partial_localization_max_tags_reached" and not _as_bool(
            _get(summary, "localization.candidate_overflow", False)
        ):
            result.fail(
                "s3a_m2_localization_partial_without_overflow",
                "Localization may report partial max-tags only when the candidate query proved overflow beyond the exact limit.",
                path="localization.candidate_overflow",
                expected=True,
                actual=_get(summary, "localization.candidate_overflow", None),
            )
        if _as_bool(_get(summary, "localization.candidate_overflow", False)):
            if localization_status in {"completed", "completed_noop_no_candidates"}:
                result.fail(
                    "s3a_m2_localization_overflow_claimed_complete",
                    "Localization overflow must leave remaining localization work deferred or incomplete, not completed.",
                    path="localization.status",
                    expected="deferred or partial overflow status",
                    actual=_get(summary, "localization.status", None),
                )
            if str(_get(summary, "localization.dynamic_source_items_target_status", "") or "") != "deferred":
                result.fail(
                    "s3a_m2_localization_overflow_source_items_not_deferred",
                    "Localization overflow must not mark all imported source items localized.",
                    path="localization.dynamic_source_items_target_status",
                    expected="deferred",
                    actual=_get(summary, "localization.dynamic_source_items_target_status", None),
                )
            if not str(_get(summary, "localization.dynamic_source_items_deferred_reason", "") or ""):
                result.fail(
                    "s3a_m2_localization_overflow_missing_deferred_reason",
                    "Localization overflow must include a stable deferred reason for remaining source items.",
                    path="localization.dynamic_source_items_deferred_reason",
                    expected="non-empty stable reason",
                    actual=_get(summary, "localization.dynamic_source_items_deferred_reason", None),
                )

    gpu_status = str(_get(summary, "gpu_telemetry.validation_status", "") or "").casefold()
    actual_provider = str(_get(summary, "gpu_telemetry.actual_provider", "") or "")
    gpu_providers = {"DmlExecutionProvider", "CUDAExecutionProvider"}
    if gpu_status == "passed" and actual_provider not in gpu_providers:
        result.fail(
            "s3a_m2_gpu_pass_without_gpu_provider",
            "GPU validation cannot pass unless the actual ONNX Runtime provider is DirectML or CUDA.",
            path="gpu_telemetry.actual_provider",
            expected=sorted(gpu_providers),
            actual=actual_provider,
        )
    if actual_provider == "CPUExecutionProvider" and status == "target_met":
        result.fail(
            "s3a_m2_cpu_fallback_claimed_target",
            "CPU fallback must be reported as partial and cannot satisfy S3A-M2 GPU validation.",
            path="gpu_telemetry.actual_provider",
            expected=sorted(gpu_providers),
            actual=actual_provider,
        )

    launcher_status_any = str(_get(summary, "launcher_web_admin_acceptance.status", "") or "").casefold()
    launcher_execute_clicked = _as_bool(_get(summary, "launcher_web_admin_acceptance.execute_clicked", False))
    if launcher_status_any == "passed_gui_execute_completed" and not launcher_execute_clicked:
        result.fail(
            "s3a_m2_gui_execute_claim_without_click",
            "Launcher/Web Admin validation must not claim GUI execute completion unless the GUI clicked Execute.",
            path="launcher_web_admin_acceptance.execute_clicked",
            expected=True,
            actual=launcher_execute_clicked,
        )
    if launcher_status_any == "passed_gui_execute_completed":
        gui_completed = _as_bool(_get(summary, "launcher_web_admin_acceptance.gui_execute_completed", False))
        gui_run_id = _as_int(
            _get(
                summary,
                "launcher_web_admin_acceptance.gui_execute_run_id",
                _get(summary, "launcher_web_admin_acceptance.production_execute_run_id_seen", 0),
            )
        )
        previous_runner_run_id = max(
            _as_int(_get(summary, "initial_run.run_id", 0)),
            _as_int(_get(summary, "remaining_run.run_id", 0)),
            _as_int(_get(summary, "launcher_web_admin_acceptance.previous_execute_run_id", 0)),
        )
        if not gui_completed:
            result.fail(
                "s3a_m2_gui_execute_claim_without_completed_run",
                "Launcher/Web Admin validation must not claim GUI execute completion unless the GUI-created run completed.",
                path="launcher_web_admin_acceptance.gui_execute_completed",
                expected=True,
                actual=_get(summary, "launcher_web_admin_acceptance.gui_execute_completed", None),
            )
        if previous_runner_run_id and gui_run_id <= previous_runner_run_id:
            result.fail(
                "s3a_m2_gui_execute_claim_without_newer_run",
                "GUI Execute acceptance must validate a GUI-created run newer than the prior runner/API execute runs.",
                path="launcher_web_admin_acceptance.gui_execute_run_id",
                expected=f"> {previous_runner_run_id}",
                actual=gui_run_id,
            )
        gui_provenance_valid = _as_bool(_get(summary, "launcher_web_admin_acceptance.gui_provenance_valid", False))
        request_source = str(_get(summary, "launcher_web_admin_acceptance.request_source", "") or "")
        gui_session_present = _as_bool(
            _get(summary, "launcher_web_admin_acceptance.gui_validation_session_id_present", False)
        )
        gui_session_signature_valid = _as_bool(
            _get(summary, "launcher_web_admin_acceptance.gui_validation_session_signature_valid", False)
        )
        if (
            not gui_provenance_valid
            or request_source != "web_admin_gui"
            or not gui_session_present
            or not gui_session_signature_valid
        ):
            result.fail(
                "s3a_m2_gui_execute_claim_without_gui_provenance",
                "GUI Execute acceptance must be backed by a Web Admin GUI-created run with a signed durable GUI validation session marker.",
                path="launcher_web_admin_acceptance",
                expected={
                    "gui_provenance_valid": True,
                    "request_source": "web_admin_gui",
                    "gui_validation_session_id_present": True,
                    "gui_validation_session_signature_valid": True,
                },
                actual={
                    "gui_provenance_valid": gui_provenance_valid,
                    "request_source": request_source,
                    "gui_validation_session_id_present": gui_session_present,
                    "gui_validation_session_signature_valid": gui_session_signature_valid,
                },
            )
        gui_plan_hash_bound = _as_bool(_get(summary, "launcher_web_admin_acceptance.gui_plan_hash_bound", False))
        gui_plan_flow_verified = _as_bool(_get(summary, "launcher_web_admin_acceptance.gui_plan_flow_verified", False))
        gui_plan_request_id_present = _as_bool(
            _get(summary, "launcher_web_admin_acceptance.gui_plan_request_id_present", False)
        )
        if not (gui_plan_hash_bound and gui_plan_flow_verified and gui_plan_request_id_present):
            result.fail(
                "s3a_m2_gui_execute_claim_without_bound_plan_flow",
                "GUI Execute acceptance must bind the Web Admin session to the browser-generated plan request id and plan hash.",
                path="launcher_web_admin_acceptance",
                expected={
                    "gui_plan_hash_bound": True,
                    "gui_plan_flow_verified": True,
                    "gui_plan_request_id_present": True,
                },
                actual={
                    "gui_plan_hash_bound": gui_plan_hash_bound,
                    "gui_plan_flow_verified": gui_plan_flow_verified,
                    "gui_plan_request_id_present": gui_plan_request_id_present,
                },
            )
        if not _as_bool(_get(summary, "launcher_web_admin_acceptance.runtime_head_matches_current", False)):
            result.fail(
                "s3a_m2_gui_execute_claim_without_current_head_runtime",
                "GUI Execute acceptance must validate that the GUI-created run was produced by the current report head.",
                path="launcher_web_admin_acceptance.runtime_head_matches_current",
                expected=True,
                actual=_get(summary, "launcher_web_admin_acceptance.runtime_head_matches_current", None),
            )
    if launcher_status_any == "passed_gui_execute_not_safe_runner_execute_used":
        if launcher_execute_clicked:
            result.fail(
                "s3a_m2_runner_fallback_claim_with_gui_execute_click",
                "Runner fallback status must not also claim the GUI clicked Execute.",
                path="launcher_web_admin_acceptance.execute_clicked",
                expected=False,
                actual=True,
            )
        if not str(_get(summary, "launcher_web_admin_acceptance.fallback_reason", "") or ""):
            result.fail(
                "s3a_m2_runner_fallback_missing_reason",
                "Runner fallback status must include a stable public-safe fallback reason.",
                path="launcher_web_admin_acceptance.fallback_reason",
                expected="non-empty public-safe reason",
                actual=_get(summary, "launcher_web_admin_acceptance.fallback_reason", None),
            )

    if status == "dry_run_complete_pending_approval":
        if production_performed or _as_bool(_get(summary, "api_runner_acceptance.execute_ran", False)):
            result.fail(
                "s3a_m2_dry_run_status_after_execute",
                "Dry-run pending approval status must not report production execute.",
                path="production_acceptance.performed",
                expected=False,
                actual=production_performed,
            )

    if status == "target_met":
        incident = _get(summary, "ai_tag_assignment_incident", {})
        cohort = _get(summary, "cohort_self_audit", {})
        if not isinstance(incident, Mapping):
            result.fail(
                "s3a_m2_ai_tag_assignment_incident_missing",
                "S3A-M2 target_met requires an assignment-level AI tag incident/self-audit section.",
                path="ai_tag_assignment_incident",
            )
            incident = {}
        if not isinstance(cohort, Mapping):
            result.fail(
                "s3a_m2_cohort_self_audit_missing",
                "S3A-M2 target_met requires cohort-level comparison against the mature pipeline.",
                path="cohort_self_audit",
            )
            cohort = {}
        incident_status = str(incident.get("status") or "")
        if incident_status not in {"repaired", "passed_no_incident"}:
            result.fail(
                "s3a_m2_ai_tag_assignment_incident_not_resolved",
                "S3A-M2 target_met requires the AI tag assignment incident to be repaired or explicitly absent after audit.",
                path="ai_tag_assignment_incident.status",
                expected=["repaired", "passed_no_incident"],
                actual=incident_status,
            )
        incident_after = incident.get("after") if isinstance(incident.get("after"), Mapping) else {}
        expected_nonproper_normal = _as_int(incident_after.get("high_conf_nonproper_expected_normal_count", 0))
        incorrect_nonproper_suggestions = _as_int(incident_after.get("high_conf_nonproper_incorrect_suggestion_count", 0))
        nonproper_normal_count = _as_int(incident_after.get("high_conf_nonproper_normal_count", 0))
        expected_proper_normal = _as_int(incident_after.get("high_conf_proper_expected_normal_count", 0))
        incorrect_proper_suggestions = _as_int(incident_after.get("high_conf_proper_incorrect_suggestion_count", 0))
        proper_normal_count = _as_int(incident_after.get("high_conf_proper_normal_count", 0))
        if (expected_nonproper_normal > 0 or expected_proper_normal > 0) and _as_bool(
            incident_after.get("all_ai_assignments_are_suggestions", False)
        ):
            result.fail(
                "s3a_m2_all_ai_tags_suggestions_with_mature_policy_expected",
                "S3A-M2 cannot pass when all AI tags are suggestions while mature-policy high-confidence tags should be normal media tags.",
                path="ai_tag_assignment_incident.after.all_ai_assignments_are_suggestions",
                expected=False,
                actual=True,
            )
        if incorrect_nonproper_suggestions != 0:
            result.fail(
                "s3a_m2_high_conf_nonproper_ai_tags_still_suggestions",
                "High-confidence non-proper AI tags must be normal media tags after repair.",
                path="ai_tag_assignment_incident.after.high_conf_nonproper_incorrect_suggestion_count",
                expected=0,
                actual=incorrect_nonproper_suggestions,
            )
        if expected_nonproper_normal > 0 and nonproper_normal_count <= 0:
            result.fail(
                "s3a_m2_high_conf_nonproper_ai_tags_not_normalized",
                "S3A-M2 target_met requires normal non-suggestion AI tag assignments for high-confidence non-proper tags.",
                path="ai_tag_assignment_incident.after.high_conf_nonproper_normal_count",
                expected="> 0",
                actual=nonproper_normal_count,
            )
        if incorrect_proper_suggestions != 0:
            result.fail(
                "s3a_m2_high_conf_proper_ai_tags_still_suggestions",
                "High-confidence mature-policy character/copyright/artist AI media tags must not be forced into suggestions.",
                path="ai_tag_assignment_incident.after.high_conf_proper_incorrect_suggestion_count",
                expected=0,
                actual=incorrect_proper_suggestions,
            )
        if expected_proper_normal > 0 and proper_normal_count < expected_proper_normal:
            result.fail(
                "s3a_m2_high_conf_proper_ai_tags_not_normalized",
                "S3A-M2 target_met requires mature-policy character/copyright/artist AI media tags to be normal media tags when above threshold.",
                path="ai_tag_assignment_incident.after.high_conf_proper_normal_count",
                expected=f">= {expected_proper_normal}",
                actual=proper_normal_count,
            )
        if _as_int(incident.get("entity_truth_violations_found", 0)) != 0:
            result.fail(
                "s3a_m2_ai_only_entity_truth_violation",
                "AI-only proper nouns must not create SourceConcept truth, Entity truth, or confirmed entity assignments.",
                path="ai_tag_assignment_incident.entity_truth_violations_found",
                expected=0,
                actual=incident.get("entity_truth_violations_found"),
            )
        if _as_int(incident.get("localization_remaining_gap", 0)) != 0:
            result.fail(
                "s3a_m2_incident_localization_gap_remaining",
                "AI tag assignment repair must not leave unexplained localization gaps.",
                path="ai_tag_assignment_incident.localization_remaining_gap",
                expected=0,
                actual=incident.get("localization_remaining_gap"),
            )
        ui_verification = incident.get("ui_verification")
        if not isinstance(ui_verification, Mapping):
            ui_verification = _get(summary, "post_repair_ui_validation", {})
        if not isinstance(ui_verification, Mapping) or str(ui_verification.get("status") or "") != "passed":
            result.fail(
                "s3a_m2_post_repair_ui_validation_not_passed",
                "S3A-M2 target_met requires post-repair UI validation showing normal tags outside suggestion grouping.",
                path="ai_tag_assignment_incident.ui_verification.status",
                expected="passed",
                actual=ui_verification.get("status") if isinstance(ui_verification, Mapping) else None,
            )
        elif _as_int(ui_verification.get("normal_visible_pass_count", 0)) < _as_int(ui_verification.get("sample_count", 0)):
            result.fail(
                "s3a_m2_post_repair_ui_normal_tags_not_visible",
                "Every post-repair UI sample with normal AI tags must show normal GENERAL/META groups.",
                path="ai_tag_assignment_incident.ui_verification.normal_visible_pass_count",
                expected=ui_verification.get("sample_count"),
                actual=ui_verification.get("normal_visible_pass_count"),
            )
        cohort_status = str(cohort.get("status") or "")
        if cohort_status not in {"passed", "passed_after_repair"}:
            result.fail(
                "s3a_m2_cohort_self_audit_not_passed",
                "Cohort-level S3A-M2 regression audit must pass before target_met.",
                path="cohort_self_audit.status",
                expected=["passed", "passed_after_repair"],
                actual=cohort_status,
            )
        if _as_int(cohort.get("blocker_anomaly_count", 0)) != 0:
            result.fail(
                "s3a_m2_cohort_blocker_anomalies_remaining",
                "S3A-M2 target_met cannot leave cohort-level blocker anomalies unresolved.",
                path="cohort_self_audit.blocker_anomaly_count",
                expected=0,
                actual=cohort.get("blocker_anomaly_count"),
            )
        if not _as_bool(cohort.get("normal_ai_tag_semantics_consistent_with_policy", False)):
            result.fail(
                "s3a_m2_cohort_ai_tag_semantics_abnormal",
                "Cohort audit must prove normal-vs-suggestion AI tag assignment semantics match policy.",
                path="cohort_self_audit.normal_ai_tag_semantics_consistent_with_policy",
                expected=True,
                actual=cohort.get("normal_ai_tag_semantics_consistent_with_policy"),
            )
        if _as_int(cohort.get("affected_media_count", 0)) <= 0 or _as_int(cohort.get("baseline_media_count", 0)) <= 0:
            result.fail(
                "s3a_m2_cohort_sample_missing",
                "Cohort audit must include both affected S3A-M2 media and an older mature-pipeline baseline cohort.",
                path="cohort_self_audit",
                expected="affected_media_count > 0 and baseline_media_count > 0",
                actual={
                    "affected_media_count": cohort.get("affected_media_count"),
                    "baseline_media_count": cohort.get("baseline_media_count"),
                },
            )
        if not _as_bool(incident.get("public_safe", False)) or not _as_bool(cohort.get("public_safe", False)):
            result.fail(
                "s3a_m2_incident_or_cohort_report_not_public_safe",
                "Incident and cohort audit summaries must be explicitly public safe.",
                path="ai_tag_assignment_incident.public_safe",
                expected=True,
                actual={"incident_public_safe": incident.get("public_safe"), "cohort_public_safe": cohort.get("public_safe")},
            )
        initial_validation = _get(summary, "initial_run_validation", {})
        if not isinstance(initial_validation, Mapping) or not _as_bool(initial_validation.get("passed", False)):
            result.fail(
                "s3a_m2_initial_run_validation_not_passed",
                "S3A-M2 aggregate target_met requires the initial production run to be validated before claiming aggregate completion.",
                path="initial_run_validation.passed",
                expected=True,
                actual=initial_validation.get("passed") if isinstance(initial_validation, Mapping) else None,
            )
        required_target_true = (
            "production_acceptance.performed",
            "pipeline_contract.exact_operator_approval_present",
            "api_runner_acceptance.execute_ran",
            "ledger_consistency.passed",
            "launcher_web_admin_acceptance.validated",
            "standard_pipeline_flow.public_safe",
        )
        _check_required_boolean_paths(
            summary,
            result,
            required_target_true,
            code="s3a_m2_target_proof_missing",
            message="S3A-M2 target_met requires approved production execute, ledger proof, and launcher/Web Admin validation.",
        )
        if str(_get(summary, "standard_pipeline_flow.status", "") or "").casefold() != "completed":
            result.fail(
                "s3a_m2_standard_pipeline_flow_incomplete",
                "S3A-M2 target_met requires the standardized scan/hydrate/rescan/import/classify/AI/localize/ledger/telemetry/redaction/GUI/report flow to be complete.",
                path="standard_pipeline_flow.status",
                expected="completed",
                actual=_get(summary, "standard_pipeline_flow.status", None),
            )
        for step_name in (
            "scan_current_source_delta",
            "detect_cloud_placeholders",
            "hydrate_placeholders_non_destructively",
            "rescan_after_hydration",
            "import_all_current_importable_items",
            "classify_imported_media",
            "run_ai_tagging",
            "run_localization_or_stable_reasons",
            "record_ledger_for_every_planned_item",
            "capture_resource_gpu_telemetry",
            "validate_public_redaction",
            "validate_launcher_web_admin_workflow",
            "produce_public_report_and_contract",
        ):
            step_path = f"standard_pipeline_flow.steps.{step_name}.completed"
            if not _as_bool(_get(summary, step_path, False)):
                result.fail(
                    "s3a_m2_standard_pipeline_step_incomplete",
                    "Every standard S3A-M2 pipeline step must be completed before target_met.",
                    path=step_path,
                    expected=True,
                    actual=_get(summary, step_path, None),
                )
        launcher_status = str(_get(summary, "launcher_web_admin_acceptance.status", "")).casefold()
        allowed_launcher_statuses = {
            "passed_gui_execute_completed",
        }
        if launcher_status not in allowed_launcher_statuses:
            result.fail(
                "s3a_m2_launcher_validation_not_passed",
                "S3A-M2 target_met requires a real launcher/Web Admin GUI Execute validation to pass.",
                path="launcher_web_admin_acceptance.status",
                expected=sorted(allowed_launcher_statuses),
                actual=_get(summary, "launcher_web_admin_acceptance.status", None),
            )
        expected_execute_run_id = _as_int(_get(summary, "execute.run_id", 0))
        launcher_execute_run_id = _as_int(_get(summary, "launcher_web_admin_acceptance.production_execute_run_id_seen", 0))
        if expected_execute_run_id and launcher_execute_run_id != expected_execute_run_id:
            result.fail(
                "s3a_m2_launcher_validation_run_id_mismatch",
                "Launcher/Web Admin validation artifact must identify the production execute run it validates.",
                path="launcher_web_admin_acceptance.production_execute_run_id_seen",
                expected=expected_execute_run_id,
                actual=launcher_execute_run_id,
            )
        expected_head_sha = str(_get(summary, "head_sha", "") or "")
        launcher_head_sha = str(_get(summary, "launcher_web_admin_acceptance.validated_head_sha", "") or "")
        if expected_head_sha and launcher_head_sha != expected_head_sha:
            result.fail(
                "s3a_m2_launcher_validation_head_sha_mismatch",
                "Launcher/Web Admin validation artifact must match the report head SHA.",
                path="launcher_web_admin_acceptance.validated_head_sha",
                expected=expected_head_sha,
                actual=launcher_head_sha,
            )
        expected_source_identity = str(_get(summary, "source.public_source_identity", "") or "")
        launcher_source_identity = str(_get(summary, "launcher_web_admin_acceptance.public_source_identity", "") or "")
        if expected_source_identity and launcher_source_identity != expected_source_identity:
            result.fail(
                "s3a_m2_launcher_validation_source_mismatch",
                "Launcher/Web Admin validation artifact must match the public-safe source identity for this execute.",
                path="launcher_web_admin_acceptance.public_source_identity",
                expected=expected_source_identity,
                actual=launcher_source_identity,
            )
        if gpu_status != "passed" or actual_provider not in gpu_providers:
            result.fail(
                "s3a_m2_gpu_validation_not_passed",
                "S3A-M2 target_met requires GPU validation through DirectML or CUDA.",
                path="gpu_telemetry.validation_status",
                expected="passed",
                actual=_get(summary, "gpu_telemetry.validation_status", None),
            )
        placeholder_status = str(_get(summary, "placeholder_hydration.status", "") or "").casefold()
        if placeholder_status not in {"completed", "completed_with_stable_failures", "not_required"}:
            result.fail(
                "s3a_m2_placeholder_hydration_missing",
                "S3A-M2 target_met requires placeholder hydration evidence or an explicit not-required state.",
                path="placeholder_hydration.status",
                expected=["completed", "completed_with_stable_failures", "not_required"],
                actual=_get(summary, "placeholder_hydration.status", None),
            )
        if _as_int(_get(summary, "placeholder_hydration.remaining_placeholders_after_hydration", 0)) != 0:
            result.fail(
                "s3a_m2_placeholders_remaining_after_hydration",
                "S3A-M2 target_met cannot treat remaining iCloud placeholders as completed work.",
                path="placeholder_hydration.remaining_placeholders_after_hydration",
                expected=0,
                actual=_get(summary, "placeholder_hydration.remaining_placeholders_after_hydration", None),
            )
        if _as_int(_get(summary, "final_inventory.current_importable_hydrated_supported_items", 0)) != 0:
            result.fail(
                "s3a_m2_importable_items_remaining",
                "All currently importable hydrated supported delta items must be imported before target_met.",
                path="final_inventory.current_importable_hydrated_supported_items",
                expected=0,
                actual=_get(summary, "final_inventory.current_importable_hydrated_supported_items", None),
            )
        if _as_int(_get(summary, "final_inventory.placeholders_remaining", 0)) != 0:
            result.fail(
                "s3a_m2_final_placeholders_remaining",
                "Final inventory must show zero remaining placeholders or stable accepted failure details outside target_met.",
                path="final_inventory.placeholders_remaining",
                expected=0,
                actual=_get(summary, "final_inventory.placeholders_remaining", None),
            )
        if _as_bool(_get(summary, "final_inventory.scan_cap_stopped_scan", False)):
            result.fail(
                "s3a_m2_final_inventory_cap_stopped_scan",
                "Final remaining-delta inventory must not be cap-truncated when target_met is claimed.",
                path="final_inventory.scan_cap_stopped_scan",
                expected=False,
                actual=True,
            )
        if _as_int(_get(summary, "final_totals.imported", _get(summary, "execute.imported", 0))) <= 0:
            result.fail(
                "s3a_m2_target_without_imported_delta",
                "S3A-M2 target_met requires a real imported production delta.",
                path="final_totals.imported",
                expected="> 0",
                actual=_get(summary, "final_totals.imported", _get(summary, "execute.imported", None)),
            )
        if _as_int(_get(summary, "final_totals.classified", _get(summary, "classification.count", 0))) <= 0:
            result.fail(
                "s3a_m2_target_without_classification",
                "S3A-M2 target_met requires classification work to complete for the delta.",
                path="final_totals.classified",
                expected="> 0",
                actual=_get(summary, "final_totals.classified", _get(summary, "classification.count", None)),
            )
        if _as_int(_get(summary, "final_totals.ai_tagged", _get(summary, "ai_tagging.count", 0))) <= 0:
            result.fail(
                "s3a_m2_target_without_ai_tagging",
                "S3A-M2 target_met requires AI tagging work to complete for the delta.",
                path="final_totals.ai_tagged",
                expected="> 0",
                actual=_get(summary, "final_totals.ai_tagged", _get(summary, "ai_tagging.count", None)),
            )
        telemetry_root = str(_get(summary, "private_artifacts.telemetry_root", "") or "")
        if telemetry_root and not telemetry_root.replace("\\", "/").startswith(".local_manifests/s3a_m2_delta_e2e/telemetry"):
            result.fail(
                "s3a_m2_telemetry_artifact_outside_approved_tree",
                "S3A-M2 raw telemetry must stay under .local_manifests/s3a_m2_delta_e2e/telemetry.",
                path="private_artifacts.telemetry_root",
                expected=".local_manifests/s3a_m2_delta_e2e/telemetry",
                actual=telemetry_root,
            )

    public_payloads: list[Any] = [summary]
    for report_key in ("markdown_report_path", "ai_tag_incident_report_path", "gui_validation_postmortem_path"):
        report_path = _get(summary, f"public_reports.{report_key}", None)
        if not isinstance(report_path, str) or not report_path:
            continue
        path = (CONTRACT_ROOT / report_path).resolve()
        try:
            path.relative_to(CONTRACT_ROOT)
            if path.exists():
                public_payloads.append({f"public_{report_key}_text": path.read_text(encoding="utf-8")})
        except Exception:
            result.fail(
                "s3a_m2_public_report_path_invalid",
                "S3A-M2 public markdown report path must stay under the repository root.",
                path=f"public_reports.{report_key}",
                expected="repo-relative path",
                actual=report_path,
            )
    findings: list[dict[str, Any]] = []
    for payload in public_payloads:
        findings.extend(scan_public_payload(payload))
    if findings:
        result.fail(
            "s3a_m2_public_payload_not_safe",
            "S3A-M2 public artifacts must not leak local paths, filenames, secrets, source roots, source URLs, or private provenance.",
            path="public_redaction",
            actual=findings[:5],
        )


def _check_s3a_m2_r_lifecycle_workitem(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "pr_r1_core_complete",
        "blocked_validation",
        "blocked_public_redaction_failed",
        "blocked_contract_failed",
    }
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s3a_m2_r_lifecycle_unknown_status",
            "S3A-M2-R PR-R1 status must explicitly report core completion or the blocking condition.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if status != "pr_r1_core_complete" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_m2_r_lifecycle_non_complete_status_claimed_completion",
            "Only pr_r1_core_complete may claim PR-R1 completion.",
            path="pipeline_contract.status",
            expected="pr_r1_core_complete for completion claims",
            actual=result.status,
        )
    if str(_get(summary, "pipeline_contract.phase_identity", "") or "") != "S3A-M2-R PR-R1":
        result.fail(
            "s3a_m2_r_lifecycle_phase_identity_mismatch",
            "The lifecycle WorkItem summary must declare the exact PR-R1 phase identity.",
            path="pipeline_contract.phase_identity",
            expected="S3A-M2-R PR-R1",
            actual=_get(summary, "pipeline_contract.phase_identity", None),
        )
    if _as_bool(_get(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", False)) or _as_bool(
        _get(summary, "scope.full_s3a_m2_r_completion_claimed", False)
    ):
        result.fail(
            "s3a_m2_r_lifecycle_overclaimed_full_completion",
            "PR-R1 must not claim full S3A-M2-R completion.",
            path="pipeline_contract.claims.full_s3a_m2_r_complete",
            expected=False,
            actual=True,
        )

    expected_lifecycle = {
        "APP_MEDIA_FOLLOWUP",
        "IMPORT_CANDIDATE",
        "RETRYABLE_SOURCE_FAILURE",
        "PLACEHOLDER_DEFERRED",
        "STABLE_NOOP",
        "HISTORICAL_DIAGNOSTIC",
        "CONTINUATION",
        "BROKEN_STATE",
        "FATAL_BLOCKER",
    }
    lifecycle_kinds = set(str(value) for value in (_get(summary, "lifecycle_classifier.lifecycle_kinds", []) or []))
    missing_lifecycle = sorted(expected_lifecycle - lifecycle_kinds)
    if missing_lifecycle:
        result.fail(
            "s3a_m2_r_lifecycle_kinds_missing",
            "The public summary must list every canonical LifecycleKind.",
            path="lifecycle_classifier.lifecycle_kinds",
            expected=sorted(expected_lifecycle),
            actual=sorted(lifecycle_kinds),
        )
    expected_work = {"FOLLOWUP", "IMPORT", "RETRY_SOURCE", "PLACEHOLDER", "NOOP_DIAGNOSTIC", "BROKEN_STATE"}
    work_kinds = set(str(value) for value in (_get(summary, "lifecycle_classifier.work_item_kinds", []) or []))
    missing_work = sorted(expected_work - work_kinds)
    if missing_work:
        result.fail(
            "s3a_m2_r_work_item_kinds_missing",
            "The public summary must list every canonical WorkItemKind.",
            path="lifecycle_classifier.work_item_kinds",
            expected=sorted(expected_work),
            actual=sorted(work_kinds),
        )

    boundary_expectations = {
        "FOLLOWUP": (False, True, True),
        "IMPORT": (True, True, True),
        "RETRY_SOURCE": (True, True, True),
        "PLACEHOLDER": (False, False, False),
        "NOOP_DIAGNOSTIC": (False, False, False),
        "BROKEN_STATE": (False, False, False),
    }
    for work_kind, (source_reads, can_execute, consumes_cap) in boundary_expectations.items():
        boundary = _get(summary, f"work_item_source_read_boundaries.{work_kind}", {})
        if not isinstance(boundary, Mapping):
            result.fail(
                "s3a_m2_r_source_boundary_missing",
                "Every WorkItem source-read boundary must be a structured mapping.",
                path=f"work_item_source_read_boundaries.{work_kind}",
                expected="mapping",
                actual=type(boundary).__name__,
            )
            continue
        if _as_bool(boundary.get("allowed_source_reads")) is not source_reads:
            result.fail(
                "s3a_m2_r_source_boundary_invalid",
                "WorkItem allowed_source_reads does not match the canonical boundary.",
                path=f"work_item_source_read_boundaries.{work_kind}.allowed_source_reads",
                expected=source_reads,
                actual=boundary.get("allowed_source_reads"),
            )
        if _as_bool(boundary.get("can_execute")) is not can_execute:
            result.fail(
                "s3a_m2_r_execute_boundary_invalid",
                "WorkItem can_execute does not match the canonical boundary.",
                path=f"work_item_source_read_boundaries.{work_kind}.can_execute",
                expected=can_execute,
                actual=boundary.get("can_execute"),
            )
        if _as_bool(boundary.get("consumes_actionable_cap")) is not consumes_cap:
            result.fail(
                "s3a_m2_r_cap_boundary_invalid",
                "WorkItem cap consumption does not match the canonical boundary.",
                path=f"work_item_source_read_boundaries.{work_kind}.consumes_actionable_cap",
                expected=consumes_cap,
                actual=boundary.get("consumes_actionable_cap"),
            )

    if _as_int(_get(summary, "validation.table_driven_lifecycle_scenarios.count", 0)) < 20:
        result.fail(
            "s3a_m2_r_lifecycle_scenario_count_too_low",
            "PR-R1 must table-test at least the requested lifecycle scenarios.",
            path="validation.table_driven_lifecycle_scenarios.count",
            expected=">=20",
            actual=_get(summary, "validation.table_driven_lifecycle_scenarios.count", None),
        )
    _check_required_boolean_paths(
        summary,
        result,
        (
            "lifecycle_classifier.implemented",
            "operator_status_mapping.implemented",
            "operator_status_mapping.legacy_completed_with_failures_mapped",
            "validation.table_driven_lifecycle_scenarios.passed",
            "validation.source_read_boundary_tests.passed",
            "validation.app_media_missing_broken_state_covered",
            "validation.attempted_vs_completed_separation_covered",
            "validation.root_scoped_validator_report_coverage",
            "validation.phase_contract_tests_passed",
            "public_redaction.passed",
            "safety.no_production_execute",
            "safety.no_source_icloud_mutation",
            "safety.no_app_storage_repair_or_mutation",
            "safety.no_db_import",
            "safety.no_production_classification_ai_localization",
            "safety.no_provider_or_source_metadata_calls",
            "safety.no_sourceconcept_entity_media_tags_truth_writes",
        ),
        code="s3a_m2_r_lifecycle_required_proof_missing",
        message="PR-R1 requires classifier, status mapping, lifecycle tests, root-scoped report coverage, redaction, and safety proofs.",
    )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "pipeline_contract.claims.full_s3a_m2_r_complete",
            "scope.full_s3a_m2_r_completion_claimed",
            "scope.ui_progress_browser_validation_in_scope",
            "safety.production_execute_ran",
            "safety.source_icloud_mutation",
            "safety.app_storage_repair_or_mutation",
            "safety.db_import",
            "safety.production_classification_ai_localization",
            "safety.provider_or_source_metadata_calls",
            "safety.sourceconcept_entity_media_tags_truth_writes",
        ),
        code="s3a_m2_r_lifecycle_forbidden_scope_or_mutation",
        message="PR-R1 must not run production Execute, mutate source/iCloud/app storage, write production truth, run providers, or claim PR-R2 UI/browser scope.",
    )

    if _as_int(_get(summary, "debt_model.older_app_media_source_missing_downstream_incomplete.count", 0)) != 20:
        result.fail(
            "s3a_m2_r_older_app_media_debt_count_missing",
            "The public summary must preserve the R0 20-row app-media/source-missing debt interpretation.",
            path="debt_model.older_app_media_source_missing_downstream_incomplete.count",
            expected=20,
            actual=_get(summary, "debt_model.older_app_media_source_missing_downstream_incomplete.count", None),
        )
    if _as_int(_get(summary, "debt_model.run18_deferred_continuation.count", 0)) != 75:
        result.fail(
            "s3a_m2_r_run18_continuation_count_missing",
            "The public summary must preserve the R0 75-row continuation interpretation.",
            path="debt_model.run18_deferred_continuation.count",
            expected=75,
            actual=_get(summary, "debt_model.run18_deferred_continuation.count", None),
        )
    if _as_int(_get(summary, "debt_model.run18_retryable_source_failures.count", 0)) != 11:
        result.fail(
            "s3a_m2_r_run18_retryable_count_missing",
            "The public summary must preserve the R0 11-row retryable source-failure interpretation.",
            path="debt_model.run18_retryable_source_failures.count",
            expected=11,
            actual=_get(summary, "debt_model.run18_retryable_source_failures.count", None),
        )

    public_payloads: list[Any] = [summary]
    report_path = _get(summary, "public_reports.markdown_report_path", None)
    if isinstance(report_path, str) and report_path:
        path = (CONTRACT_ROOT / report_path).resolve()
        try:
            path.relative_to(CONTRACT_ROOT)
            if path.exists():
                public_payloads.append({"public_markdown_text": path.read_text(encoding="utf-8")})
        except Exception:
            result.fail(
                "s3a_m2_r_public_report_path_invalid",
                "PR-R1 public markdown report path must stay under the repository root.",
                path="public_reports.markdown_report_path",
                expected="repo-relative path",
                actual=report_path,
            )
    findings: list[dict[str, Any]] = []
    for payload in public_payloads:
        findings.extend(scan_public_payload(payload))
    if findings:
        result.fail(
            "s3a_m2_r_public_payload_not_safe",
            "PR-R1 public artifacts must not leak local paths, filenames, source roots, content hashes, secrets, or private provenance.",
            path="public_redaction",
            actual=findings[:5],
        )


def _s3a_m2_r_label_is_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if "\ufffd" in text:
        return True
    if text.count("?") >= 4:
        return True
    return text.replace("?", "").strip() == ""


def _s3a_m2_r_non_clean_full_chain_evidence(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    non_clean_markers = (
        "failed",
        "deferred",
        "blocked",
        "missing",
        "source_missing",
        "read_timeout",
        "cloud_hydration_failed",
        "localization_failed",
        "localization_deferred",
    )

    def add_count(path: str, key: str, count: Any) -> None:
        amount = _as_int(count)
        if amount <= 0:
            return
        key_folded = str(key or "").casefold()
        if any(marker in key_folded for marker in non_clean_markers):
            findings.append({"path": f"{path}.{key}", "count": amount})

    db_truth = _get(summary, "work_item_kind_first.local_final_db_truth", {})
    if isinstance(db_truth, Mapping):
        for status_key in (
            "import_status",
            "classification_status",
            "ai_tagging_status",
            "localization_status",
            "source_status",
            "sync_state",
            "failure_reason",
            "deferred_reason",
        ):
            counts = db_truth.get(status_key)
            if isinstance(counts, Mapping):
                for key, count in counts.items():
                    add_count(f"work_item_kind_first.local_final_db_truth.{status_key}", str(key), count)

    rounds = _get(summary, "local_gui_acceptance.isolated_incremental_gui_e2e.rounds", [])

    def scan_execute_payload(path: str, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            return
        status = str(payload.get("status") or "").casefold()
        if "failed" in status or "deferred" in status:
            findings.append({"path": f"{path}.status", "status": status})
        outcome_counts = payload.get("outcome_counts")
        if isinstance(outcome_counts, Mapping):
            for key, count in outcome_counts.items():
                add_count(f"{path}.outcome_counts", str(key), count)

    if isinstance(rounds, Sequence) and not isinstance(rounds, (str, bytes, bytearray)):
        for index, round_payload in enumerate(rounds):
            if not isinstance(round_payload, Mapping):
                continue
            round_path = f"local_gui_acceptance.isolated_incremental_gui_e2e.rounds[{index}]"
            scan_execute_payload(f"{round_path}.execute", round_payload.get("execute"))
            scan_execute_payload(f"{round_path}.retry_execute", round_payload.get("retry_execute"))
            batches = round_payload.get("batches")
            if isinstance(batches, Sequence) and not isinstance(batches, (str, bytes, bytearray)):
                for batch_index, batch in enumerate(batches):
                    if isinstance(batch, Mapping):
                        scan_execute_payload(
                            f"{round_path}.batches[{batch_index}].execute",
                            batch.get("execute"),
                        )
    return findings


def _check_s3a_m2_r_operator_validation(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    allowed_statuses = {
        "operator_ready",
        "blocked_browser_validation",
        "blocked_local_gui_acceptance",
        "blocked_production_plan_only",
        "blocked_public_redaction_failed",
        "blocked_contract_failed",
    }
    status = str(result.status or "").casefold()
    if status not in allowed_statuses:
        result.fail(
            "s3a_m2_r_operator_unknown_status",
            "S3A-M2-R PR-R2 status must explicitly report operator readiness or the blocking gate.",
            path="pipeline_contract.status",
            expected=sorted(allowed_statuses),
            actual=result.status,
        )
    if str(_get(summary, "pipeline_contract.phase_identity", "") or "") != "S3A-M2-R PR-R2":
        result.fail(
            "s3a_m2_r_operator_phase_identity_mismatch",
            "The PR-R2 summary must declare the exact phase identity.",
            path="pipeline_contract.phase_identity",
            expected="S3A-M2-R PR-R2",
            actual=_get(summary, "pipeline_contract.phase_identity", None),
        )
    if status != "operator_ready" and _completion_or_approval_claimed(result):
        result.fail(
            "s3a_m2_r_operator_non_ready_claimed_completion",
            "Only operator_ready may claim target_met, safe_to_merge, or full-chain completion.",
            path="pipeline_contract.status",
            expected="operator_ready for completion claims",
            actual=result.status,
        )

    required_true_paths = (
        "ui_progress.plan_progress_visible",
        "ui_progress.execute_transition_visible_before_run_id",
        "ui_progress.execute_duplicate_submit_disabled",
        "ui_progress.stage_heartbeat_visible",
        "ui_progress.error_state_visible",
        "work_item_kind_first.import_counts_from_work_item_kind",
        "work_item_kind_first.retry_counts_from_work_item_kind",
        "work_item_kind_first.legacy_state_does_not_override_work_item_kind",
        "work_item_kind_first.noop_and_placeholder_non_executable",
        "work_item_kind_first.broken_diagnostics_visible_non_actionable",
        "work_item_kind_first.successful_retry_creates_visible_pending_import",
        "work_item_kind_first.source_missing_retry_debt_visible",
        "work_item_kind_first.missing_media_or_app_file_visible",
        "browser_validation.execute_transition_checked",
        "local_gui_acceptance.gui_plan_passed",
        "local_gui_acceptance.gui_execute_passed",
        "production_plan_only.gui_path_used",
        "production_plan_only.execute_not_run",
        "production_plan_only.no_unsafe_execute_implied",
        "advanced_full_rescan_policy.retry_source_not_executable_until_validated",
        "public_redaction.passed",
        "safety.no_production_execute_without_owner_approval",
        "safety.no_source_icloud_mutation",
        "safety.no_app_storage_repair_or_mutation",
        "safety.no_destructive_cleanup",
        "safety.no_provider_pixiv_sourceconcept_entity_work",
    )
    _check_required_boolean_paths(
        summary,
        result,
        required_true_paths,
        code="s3a_m2_r_operator_required_proof_missing",
        message="PR-R2 requires UI progress, WorkItemKind-first, browser/local GUI, production Plan-only, redaction, and safety proofs.",
    )

    expected_operator_statuses = {
        "completed",
        "completed_with_retryable_failures",
        "completed_with_followup_required",
        "completed_with_continuation",
        "completed_with_retryable_failures_plus_continuation",
        "failed_systemic",
        "blocked_preflight",
        "cancelled",
    }
    expected_work_item_kinds = {"IMPORT", "FOLLOWUP", "RETRY_SOURCE", "BROKEN_STATE", "PLACEHOLDER", "NOOP_DIAGNOSTIC"}
    expected_lifecycle_kinds = {
        "APP_MEDIA_FOLLOWUP",
        "IMPORT_CANDIDATE",
        "RETRYABLE_SOURCE_FAILURE",
        "PLACEHOLDER_DEFERRED",
        "STABLE_NOOP",
        "HISTORICAL_DIAGNOSTIC",
        "CONTINUATION",
        "BROKEN_STATE",
        "FATAL_BLOCKER",
    }
    catalog_expectations = (
        ("operator_labels.operator_statuses", expected_operator_statuses, "s3a_m2_r_operator_status_labels_missing"),
        ("operator_labels.work_item_kinds", expected_work_item_kinds, "s3a_m2_r_work_item_labels_missing"),
        ("operator_labels.lifecycle_kinds", expected_lifecycle_kinds, "s3a_m2_r_lifecycle_labels_missing"),
    )
    for path, expected, code in catalog_expectations:
        labels = _get(summary, path, {})
        keys = set(labels.keys()) if isinstance(labels, Mapping) else set(str(value) for value in (labels or []))
        missing = sorted(expected - keys)
        if missing:
            result.fail(
                code,
                "PR-R2 public summary must include Chinese operator labels for every required status/kind.",
                path=path,
                expected=sorted(expected),
                actual=sorted(keys),
            )
        if isinstance(labels, Mapping):
            placeholder_labels = sorted(
                key for key in expected if key in labels and _s3a_m2_r_label_is_placeholder(labels.get(key))
            )
            if placeholder_labels:
                result.fail(
                    f"{code}_placeholder_or_empty",
                    "PR-R2 required Chinese operator labels must be readable text, not empty or question-mark placeholders.",
                    path=path,
                    expected="readable Chinese operator labels",
                    actual={key: labels.get(key) for key in placeholder_labels},
                )

    if str(_get(summary, "browser_validation.status", "")).casefold() != "passed":
        result.fail(
            "s3a_m2_r_browser_validation_not_passed",
            "PR-R2 cannot claim operator readiness without passed real browser validation.",
            path="browser_validation.status",
            expected="passed",
            actual=_get(summary, "browser_validation.status", None),
        )
    if str(_get(summary, "local_gui_acceptance.status", "")).casefold() != "passed":
        result.fail(
            "s3a_m2_r_local_gui_acceptance_not_passed",
            "PR-R2 cannot claim operator readiness without local-image GUI Plan and Execute acceptance.",
            path="local_gui_acceptance.status",
            expected="passed",
            actual=_get(summary, "local_gui_acceptance.status", None),
        )
    if str(_get(summary, "production_plan_only.status", "")).casefold() != "passed":
        result.fail(
            "s3a_m2_r_production_plan_only_not_passed",
            "PR-R2 cannot claim operator readiness without production GUI Plan-only acceptance.",
            path="production_plan_only.status",
            expected="passed",
            actual=_get(summary, "production_plan_only.status", None),
        )
    production_selected_plan_items = _as_int(
        _get(summary, "production_plan_only.selected_plan_items", _get(summary, "production_plan_only.plan_items", 0))
    )
    production_work_item_counts = _get(summary, "production_plan_only.work_item_counts", {})
    production_lifecycle_counts = _get(summary, "production_plan_only.lifecycle_counts", {})
    production_state_counts = _get(summary, "production_plan_only.state_counts", {})
    if production_selected_plan_items:
        if isinstance(production_work_item_counts, Mapping):
            work_item_total = sum(_as_int(value) for value in production_work_item_counts.values())
            if work_item_total != production_selected_plan_items:
                result.fail(
                    "s3a_m2_r_production_plan_only_work_item_count_mismatch",
                    "Production Plan-only selected_plan_items must equal the selected WorkItem counts total.",
                    path="production_plan_only.work_item_counts",
                    expected=production_selected_plan_items,
                    actual=work_item_total,
                )
        if isinstance(production_lifecycle_counts, Mapping):
            lifecycle_total = sum(_as_int(value) for value in production_lifecycle_counts.values())
            if lifecycle_total != production_selected_plan_items:
                result.fail(
                    "s3a_m2_r_production_plan_only_lifecycle_count_mismatch",
                    "Production Plan-only selected_plan_items must equal the selected lifecycle counts total.",
                    path="production_plan_only.lifecycle_counts",
                    expected=production_selected_plan_items,
                    actual=lifecycle_total,
                )
        if isinstance(production_state_counts, Mapping):
            state_total = sum(_as_int(value) for value in production_state_counts.values())
            if state_total != production_selected_plan_items:
                state_scope = str(_get(summary, "production_plan_only.state_counts_scope", "") or "").strip()
                declared_state_total = _as_int(_get(summary, "production_plan_only.state_counts_total", -1), -1)
                if not state_scope or state_scope == "selected_plan_items" or declared_state_total != state_total:
                    result.fail(
                        "s3a_m2_r_production_plan_only_state_count_scope_missing",
                        "Production Plan-only state_counts may differ from selected_plan_items only when an explicit broader state_counts_scope and matching state_counts_total are recorded.",
                        path="production_plan_only.state_counts",
                        expected={
                            "selected_plan_items": production_selected_plan_items,
                            "state_counts_scope": "explicit broader-than-selected scope",
                            "state_counts_total": state_total,
                        },
                        actual={
                            "state_counts_total": state_total,
                            "declared_state_counts_total": declared_state_total,
                            "state_counts_scope": state_scope,
                        },
                    )
    if _as_bool(_get(summary, "s3b_disabled.enabled", True)):
        result.fail(
            "s3a_m2_r_s3b_enabled",
            "PR-R2 must keep S3B unattended/scheduled/startup sync disabled.",
            path="s3b_disabled.enabled",
            expected=False,
            actual=_get(summary, "s3b_disabled.enabled", None),
        )

    production_execute_ran = _as_bool(_get(summary, "production_execute.ran", False))
    safety_production_execute_ran = _as_bool(_get(summary, "safety.production_execute_ran", False))
    if production_execute_ran != safety_production_execute_ran:
        result.fail(
            "s3a_m2_r_production_execute_flags_disagree",
            "production_execute.ran and safety.production_execute_ran must agree so the PR-R2 summary cannot false-pass.",
            path="production_execute.ran",
            expected=safety_production_execute_ran,
            actual=production_execute_ran,
        )
    owner_approved = _as_bool(_get(summary, "production_execute.owner_approved", False))
    production_execute_indicated = production_execute_ran or safety_production_execute_ran
    if production_execute_indicated and not owner_approved:
        result.fail(
            "s3a_m2_r_production_execute_without_owner_approval",
            "Production Execute is forbidden unless explicit owner approval is recorded.",
            path="production_execute.owner_approved",
            expected=True,
            actual=_get(summary, "production_execute.owner_approved", None),
        )
    if production_execute_indicated:
        approval_reference = str(_get(summary, "production_execute.approval_reference", "") or "").strip()
        approval_justification = str(_get(summary, "production_execute.approval_justification", "") or "").strip()
        if not approval_reference and not approval_justification:
            result.fail(
                "s3a_m2_r_production_execute_approval_reference_missing",
                "Production Execute approval must include an approval_reference or explicit approval_justification.",
                path="production_execute.approval_reference",
                expected="non-empty approval_reference or approval_justification",
                actual=_get(summary, "production_execute.approval_reference", None),
            )
    _check_explicit_false_paths(
        summary,
        result,
        (
            "safety.source_icloud_mutation",
            "safety.app_storage_repair_or_mutation",
            "safety.destructive_cleanup",
            "safety.provider_pixiv_sourceconcept_entity_work",
            "scope.s3b_started",
            "scope.pixiv_provider_sourceconcept_entity_started",
        ),
        code="s3a_m2_r_operator_forbidden_scope_or_mutation",
        message="PR-R2 must not enable S3B, start provider/SourceConcept/Entity work, mutate source/iCloud/app storage, or run destructive cleanup.",
    )

    final_acceptance_complete = all(
        _as_bool(_get(summary, path, False))
        for path in (
            "browser_validation.execute_transition_checked",
            "local_gui_acceptance.gui_plan_passed",
            "local_gui_acceptance.gui_execute_passed",
            "production_plan_only.gui_path_used",
            "production_plan_only.execute_not_run",
            "public_redaction.passed",
        )
    )
    non_clean_full_chain_evidence = _s3a_m2_r_non_clean_full_chain_evidence(summary)
    result.details["s3a_m2_r_non_clean_full_chain_evidence"] = non_clean_full_chain_evidence[:20]
    full_s3a_m2_r_claimed = _as_bool(_get(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", False)) or _as_bool(
        _get(summary, "scope.full_s3a_m2_r_completion_claimed", False)
    )
    if non_clean_full_chain_evidence and result.full_chain_complete_claimed:
        result.fail(
            "s3a_m2_r_operator_full_chain_overclaimed_with_non_clean_evidence",
            "PR-R2 must not claim full-chain completion while local GUI evidence still includes failed/deferred downstream work.",
            path="pipeline_contract.claims.full_chain_complete",
            expected=False,
            actual=_get(summary, "pipeline_contract.claims.full_chain_complete", None),
        )
    if non_clean_full_chain_evidence and full_s3a_m2_r_claimed:
        result.fail(
            "s3a_m2_r_operator_full_s3a_m2_r_overclaimed_with_non_clean_evidence",
            "PR-R2 operator readiness must not be overloaded as full S3A-M2-R completion when failed/deferred downstream work remains visible.",
            path="pipeline_contract.claims.full_s3a_m2_r_complete",
            expected=False,
            actual=_get(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", None),
        )
    if non_clean_full_chain_evidence and (result.target_met_claimed or result.safe_to_merge_claimed):
        claim_scope = str(
            _get(
                summary,
                "pipeline_contract.claims.safe_to_merge_scope",
                _get(summary, "pipeline_contract.claims.target_met_scope", ""),
            )
            or ""
        ).strip()
        if claim_scope != "operator_ready_visible_non_clean_debt":
            result.fail(
                "s3a_m2_r_operator_non_clean_safe_to_merge_scope_missing",
                "target_met/safe_to_merge with non-clean E2E evidence must explicitly mean operator-ready with visible non-clean debt, not clean full-chain completion.",
                path="pipeline_contract.claims.safe_to_merge_scope",
                expected="operator_ready_visible_non_clean_debt",
                actual=claim_scope or None,
            )
    if _as_bool(_get(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", False)) and not (
        status == "operator_ready" and final_acceptance_complete
    ):
        result.fail(
            "s3a_m2_r_operator_full_completion_overclaimed",
            "Full S3A-M2-R completion cannot be claimed until GUI/local/production Plan-only/redaction acceptance is complete.",
            path="pipeline_contract.claims.full_s3a_m2_r_complete",
            expected="operator_ready with all final acceptance gates",
            actual=_get(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", None),
        )

    public_payloads: list[Any] = [summary]
    report_path = _get(summary, "public_reports.markdown_report_path", None)
    if isinstance(report_path, str) and report_path:
        path = (CONTRACT_ROOT / report_path).resolve()
        try:
            path.relative_to(CONTRACT_ROOT)
            if path.exists():
                public_payloads.append({"public_markdown_text": path.read_text(encoding="utf-8")})
        except Exception:
            result.fail(
                "s3a_m2_r_operator_public_report_path_invalid",
                "PR-R2 public markdown report path must stay under the repository root.",
                path="public_reports.markdown_report_path",
                expected="repo-relative path",
                actual=report_path,
            )
    findings: list[dict[str, Any]] = []
    for payload in public_payloads:
        findings.extend(scan_public_payload(payload))
    if findings:
        result.fail(
            "s3a_m2_r_operator_public_payload_not_safe",
            "PR-R2 public artifacts must not leak local paths, filenames, source roots, content hashes, URLs, secrets, or private provenance.",
            path="public_redaction",
            actual=findings[:5],
        )


def _check_ml2_multilingual_identity_candidate_closure(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    """Independently validate current-state ML2 repair evidence."""

    pipeline = _get(summary, "pipeline_contract", {})
    status = pipeline.get("status") if isinstance(pipeline, Mapping) else None
    if status not in ML2_MULTILINGUAL_IDENTITY_CANDIDATE_CLOSURE_STATUSES:
        result.fail(
            "ml2_status_invalid",
            "ML2 status must use the registered closure vocabulary.",
            path="pipeline_contract.status",
            actual=status,
        )
    blockers: list[str] = []

    sync = _get(summary, "repository_sync_preflight", {})
    sync_ok = bool(
        isinstance(sync, Mapping)
        and sync.get("status") == "passed_synchronization_preflight"
        and sync.get("evidence_source") == "actual_git_subprocess"
        and sync.get("repository_root_verified") is True
        and sync.get("current_branch") == "codex/scv2-ml2-multilingual-identity-candidate-closure"
        and sync.get("base_is_ancestor") is True
        and sync.get("actual_merge_base") == "f6cae3483f4cf75974746a4cc82222f28e399b96"
        and _as_int(sync.get("tracked_change_count"), default=-1) == 0
        and _as_int(sync.get("staged_change_count"), default=-1) == 0
        and _as_int(sync.get("behind"), default=-1) == 0
        and sync.get("preexisting_user_owned_paths_preserved") is True
        and _as_int(sync.get("preexisting_user_owned_path_missing_count"), default=-1) == 0
        and all(
            isinstance(sync.get(key), str) and len(sync.get(key)) == length
            for key, length in (
                ("current_head", 40),
                ("remote_head", 40),
                ("actual_merge_base", 40),
                ("preexisting_untracked_path_list_sha256", 64),
                ("preexisting_ignored_path_list_sha256", 64),
            )
        )
    )
    if not sync_ok:
        blockers.append("blocked_ml2_environment_isolation")
    isolation = _get(summary, "environment_isolation", {})
    if not (
        isinstance(isolation, Mapping)
        and isolation.get("passed") is True
        and isolation.get("production_profile_active") is False
        and isolation.get("working_database_is_fresh_separate_clone") is True
        and all(
            isinstance(isolation.get(key), str) and len(isolation.get(key)) == 64
            for key in (
                "source_database_fingerprint",
                "superseded_ml2_database_fingerprint",
            )
        )
    ):
        blockers.append("blocked_ml2_environment_isolation")

    manifests = _get(summary, "manifest_fingerprints", {})
    required_manifests = (
        "creator-identity-family-manifest.jsonl",
        "creator-identity-alias-observation-manifest.jsonl",
        "candidate-generation-gap-manifest.jsonl",
        "creator-context-search-case-manifest.jsonl",
        "search-only-family-regression-manifest.jsonl",
        "candidate-pair-ledger.jsonl",
        "family-closure-ledger.jsonl",
        "creator-context-closure-ledger.jsonl",
    )
    if not isinstance(manifests, Mapping) or any(
        not isinstance(manifests.get(name), Mapping)
        or type(manifests[name].get("count")) is not int
        or manifests[name]["count"] < 0
        or not isinstance(manifests[name].get("sha256"), str)
        or len(manifests[name]["sha256"]) != 64
        for name in required_manifests
    ):
        blockers.append("blocked_ml2_input_manifest_invalid")

    baseline = _get(summary, "baseline", {})
    if not (
        isinstance(baseline, Mapping)
        and baseline.get("identity_family_count_before") == baseline.get("identity_family_count_after")
        and baseline.get("search_only_family_count_before") == baseline.get("search_only_family_count_after")
        and _as_int(baseline.get("accepted_r2r_disposition_count"), default=-1) == 3319
    ):
        blockers.append("blocked_ml2_baseline_drift")

    r2r = _get(summary, "r2r_reuse", {})
    if not (
        isinstance(r2r, Mapping)
        and _as_int(r2r.get("accepted_pair_count"), default=-1) == 3319
        and _as_int(r2r.get("accepted_must_link_count"), default=-1) == 1522
        and _as_int(r2r.get("accepted_cannot_link_count"), default=-1) == 1791
        and _as_int(r2r.get("accepted_deferred_nonblocking_count"), default=-1) == 6
        and _as_float(r2r.get("candidate_disposition_coverage"), default=0.0) == 1.0
        and isinstance(r2r.get("snapshot_fingerprint"), str)
        and len(r2r.get("snapshot_fingerprint")) == 64
        and r2r.get("database_snapshot_crosscheck_passed") is True
        and r2r.get("private_pair_manifest_crosscheck_passed") is True
        and r2r.get("cache_only_rebuild_passed") is True
        and _as_int(r2r.get("provider_attempt_count"), default=-1) == 0
        and _as_int(r2r.get("disposition_conflict_count"), default=-1) == 0
        and r2r.get("accepted_dispositions_mutated") is False
        and r2r.get("preserved_r2r_artifacts_mutated") is False
        and isinstance(r2r.get("preserved_r2r_artifact_fingerprint"), str)
        and len(r2r.get("preserved_r2r_artifact_fingerprint")) == 64
        and type(r2r.get("reused_accepted_pair_count")) is int
    ):
        blockers.append("blocked_ml2_r2r_reuse_evidence")

    pair = _get(summary, "pair_accounting", {})
    if not (
        isinstance(pair, Mapping)
        and pair.get("accounting_equality_passed") is True
        and not any(
            _as_int(pair.get(key), default=-1)
            for key in (
                "duplicate_pair_count",
                "missing_pair_count",
                "outside_manifest_pair_count",
                "invalid_disposition_count",
            )
        )
    ):
        blockers.append("blocked_ml2_pair_accounting")
    family = _get(summary, "family_accounting", {})
    if not (
        isinstance(family, Mapping)
        and family.get("accounting_equality_passed") is True
        and not any(
            _as_int(family.get(key), default=-1)
            for key in (
                "duplicate_family_count",
                "missing_family_count",
                "outside_manifest_family_count",
                "invalid_outcome_count",
            )
        )
    ):
        blockers.append("blocked_ml2_pair_accounting")
    growth = _get(summary, "candidate_growth", {})
    if not (
        isinstance(growth, Mapping)
        and growth.get("linear_bound_passed") is True
        and growth.get("all_pairs_alias_expansion_used") is False
    ):
        blockers.append("blocked_ml2_pair_accounting")
    gaps = _get(summary, "candidate_gap_closure", {})
    if not isinstance(gaps, Mapping) or any(
        _as_int(gaps.get(key), default=-1)
        for key in ("remaining_gap_count", "unexplained_gap_count")
    ):
        blockers.append("blocked_ml2_candidate_generation_gap")

    active = _get(summary, "active_concept_audit", {})
    if not isinstance(active, Mapping) or _as_int(active.get("inactive_concept_reuse_count"), default=-1) != 0:
        blockers.append("blocked_ml2_existing_component_fragmentation")
    graph = _get(summary, "graph_safety", {})
    graph_zero_keys = (
        "multi_stable_id_creator_component_count",
        "unauthorized_cross_role_component_count",
        "unknown_role_materialization_count",
        "character_work_copyright_contamination_count",
        "trusted_parent_lineage_failure_count",
        "direct_disposition_conflict_count",
        "cannot_endpoints_same_component_count",
        "direct_cannot_violation_count",
        "transitive_cannot_violation_count",
        "postclosure_duplicate_active_identity_concept_count",
    )
    if not (
        isinstance(graph, Mapping)
        and graph.get("full_touched_component_audit_passed") is True
        and graph.get("existing_12_full_component_audit_passed") is True
        and graph.get("graph_audit_cannot_pair_count_equality_passed") is True
        and _as_int(graph.get("graph_audit_cannot_pair_count"), default=-1)
        == _as_int(pair.get("cannot_link_count"), default=-2)
        and not any(_as_int(graph.get(key), default=-1) for key in graph_zero_keys)
    ):
        blockers.append("blocked_ml2_graph_safety")

    support = _get(summary, "concept_media_support", {})
    if not (
        isinstance(support, Mapping)
        and support.get("passed") is True
        and support.get("per_media_evidence_linear_bound_passed") is True
        and support.get("concept_media_support_row_count")
        == support.get("expected_concept_media_support_row_count")
        and not any(
            _as_int(support.get(key), default=-1)
            for key in (
                "duplicate_concept_media_support_count",
                "missing_sourceconcept_media_count",
                "unsupported_sourceconcept_media_count",
                "media_count_mismatch_count",
                "support_provenance_failure_count",
            )
        )
    ):
        blockers.append("blocked_ml2_runtime_media_binding")
    runtime = _get(summary, "sourceconcept_only_runtime", {})
    materialized_family_count = (
        _as_int(family.get("already_materialized_family_count"), default=0)
        + _as_int(family.get("newly_materialized_family_count"), default=0)
        + _as_int(family.get("cannot_link_closed_family_count"), default=0)
    )
    if not (
        isinstance(runtime, Mapping)
        and runtime.get("passed") is True
        and runtime.get("sourceconcept_alias_family_count") == materialized_family_count
        and _as_float(runtime.get("sourceconcept_alias_expected_media_coverage"), default=0.0) == 1.0
        and runtime.get("media_detail_sourceconcept_visibility_passed") is True
        and runtime.get("direct_source_name_or_tag_fallback_used") is False
        and not any(
            _as_int(runtime.get(key), default=-1)
            for key in (
                "search_inert_materialized_concept_count",
                "missing_sourceconcept_media_count",
                "unsupported_sourceconcept_media_count",
                "media_detail_sample_failure_count",
            )
        )
    ):
        blockers.append("blocked_ml2_runtime_media_binding")

    creator_context = _get(summary, "creator_context", {})
    if not (
        isinstance(creator_context, Mapping)
        and creator_context.get("case_count") == creator_context.get("classification_count")
        and _as_float(creator_context.get("supported_evidence_runtime_success_coverage"), default=0.0) == 1.0
        and _as_int(creator_context.get("implementation_failure_with_sufficient_evidence_count"), default=-1) == 0
        and _as_int(creator_context.get("unexplained_failure_count"), default=-1) == 0
    ):
        blockers.append("blocked_ml2_creator_context_recall")
    search = _get(summary, "search_validation", {})
    if not isinstance(search, Mapping) or any(
        _as_int(search.get(key), default=-1)
        for key in (
            "search_only_regression_count",
            "unsupported_result_count",
            "rejected_only_result_count",
            "superseded_only_result_count",
            "invalid_or_deleted_only_result_count",
            "and_leakage_count",
            "search_caused_identity_mutation_count",
        )
    ):
        blockers.append("blocked_ml2_search_safety")
    mutation = _get(summary, "mutation_proof", {})
    if not (
        isinstance(mutation, Mapping)
        and isolation.get("source_database_immutable") is True
        and isolation.get("superseded_ml2_database_immutable") is True
        and mutation.get("fixed_tables_unchanged") is True
        and mutation.get("forbidden_truth_tables_unchanged") is True
        and not mutation.get("changed_fixed_tables")
        and not mutation.get("changed_forbidden_truth_tables")
        and not any(
            _as_int(mutation.get(key), default=-1)
            for key in (
                "production_write_count",
                "entity_truth_write_count",
                "media_tags_truth_write_count",
                "source_or_icloud_write_count",
            )
        )
    ):
        blockers.append("blocked_ml2_fixed_evidence_changed")
    idempotency = _get(summary, "idempotency", {})
    if not (
        isinstance(idempotency, Mapping)
        and idempotency.get("passed") is True
        and idempotency.get("fingerprints_equal") is True
        and _as_int(idempotency.get("second_run_duplicate_media_support"), default=-1) == 0
    ):
        blockers.append("blocked_ml2_graph_safety")
    operations = _get(summary, "operation_counts", {})
    if not isinstance(operations, Mapping) or any(_as_int(value, default=-1) for value in operations.values()):
        blockers.append("blocked_ml2_fixed_evidence_changed")
    validation = _get(summary, "validation", {})
    if not isinstance(validation, Mapping) or validation.get("public_redaction_passed") is not True:
        blockers.append("blocked_ml2_public_redaction")

    route = _get(summary, "route_decision", {})
    if not isinstance(route, Mapping) or route.get("route_approved") is not False or route.get("next_phase_started") is not False:
        result.fail(
            "ml2_route_boundary_failed",
            "ML2 cannot authorize or start Controlled Scale Validation.",
            path="route_decision",
        )
    derived = sorted(set(blockers))
    declared = sorted(set(pipeline.get("active_blockers") or ())) if isinstance(pipeline, Mapping) else []
    if derived != declared:
        result.fail(
            "ml2_active_blockers_incomplete",
            "ML2 active_blockers must exactly expose independently derived blockers.",
            path="pipeline_contract.active_blockers",
            expected=derived,
            actual=declared,
        )
    target = bool(pipeline.get("target_met")) if isinstance(pipeline, Mapping) else False
    safe = bool(pipeline.get("safe_to_merge")) if isinstance(pipeline, Mapping) else False
    if derived:
        if target or safe or status == "target_met_multilingual_identity_candidate_closure":
            result.fail("ml2_target_overclaimed", "ML2 cannot claim target/safe with blockers.", path="pipeline_contract")
    elif not (
        target
        and safe
        and status == "target_met_multilingual_identity_candidate_closure"
        and pipeline.get("route_approved") is False
        and pipeline.get("semantic_completeness_claimed") is False
        and pipeline.get("production_readiness_claimed") is False
        and pipeline.get("scale_readiness_claimed") is False
    ):
        result.fail("ml2_target_claim_incomplete", "Blocker-free evidence requires the exact bounded ML2 claim.", path="pipeline_contract")


def _check_sv1_controlled_scale_promotion_readiness(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    """Independently derive every SV1 blocker from aggregate evidence."""

    pipeline = _get(summary, "pipeline_contract", {})
    status = pipeline.get("status") if isinstance(pipeline, Mapping) else None
    if status not in SV1_CONTROLLED_SCALE_PROMOTION_READINESS_STATUSES:
        result.fail("sv1_status_invalid", "SV1 status must use the registered vocabulary.", path="pipeline_contract.status", actual=status)

    blockers: list[str] = []
    missing_stages = _missing_required_stages(contract, summary)
    if missing_stages:
        result.fail(
            "sv1_required_stage_missing",
            "SV1 target claims require every declared stage to complete.",
            path="pipeline_contract.executed_stages",
            expected=list(contract.required_stages),
            actual=sorted(_executed_stage_names(summary)),
        )
        blockers.append("blocked_sv1_fixed_or_forbidden_mutation")
    sync = _get(summary, "repository_sync_preflight", {})
    if not (
        isinstance(sync, Mapping)
        and sync.get("passed") is True
        and sync.get("local_main_equals_origin_main_before_branch") is True
        and sync.get("accepted_ml2_merge_is_ancestor") is True
        and sync.get("accepted_ml2_merge_sha") == "7fca41151cc9e1d5b48cfe243279e66296346bae"
        and sync.get("task_branch_start_sha") == "7fca41151cc9e1d5b48cfe243279e66296346bae"
        and _as_int(sync.get("tracked_change_count_before_sync"), -1) == 0
        and _as_int(sync.get("staged_change_count_before_sync"), -1) == 0
        and sync.get("user_owned_artifacts_preserved") is True
    ):
        blockers.append("blocked_sv1_repository_sync")

    tests = _get(summary, "global_test_baseline", {})
    if not (
        isinstance(tests, Mapping)
        and _as_int(tests.get("final_unexpected_failure_count"), -1) == 0
        and _as_int(tests.get("unexplained_skip_count"), -1) == 0
        and tests.get("environment_specific_profiles_passed") is True
        and tests.get("sv1_regression_count") == 0
    ):
        blockers.append("blocked_sv1_global_test_baseline")

    isolation = _get(summary, "environment_isolation", {})
    writable_database_identities = [
        str(_get(isolation, "scale_database_identity", "")),
        str(_get(isolation, "promotion_database_identity", "")),
        str(_get(isolation, "rebuild_database_identity", "")),
    ] if isinstance(isolation, Mapping) else []
    predecessor_databases = {
        "blombooru_scv2_r2r_dryrun_test_20260710",
        "blombooru_scv2_ml1_acquisition_test_20260712",
        "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715",
        "blombooru_scv2_ml2_identity_closure_test_20260714",
    }
    strict_test_identities = all(
        bool(re.fullmatch(r"blombooru_[a-z0-9]+(?:_[a-z0-9]+)*", database))
        and "test" in database.split("_")
        for database in writable_database_identities
    )
    if not (
        isinstance(isolation, Mapping)
        and isolation.get("passed") is True
        and isolation.get("violet_env") == "test"
        and isolation.get("production_profile_active") is False
        and isolation.get("scale_database_clean_schema") is True
        and isolation.get("promotion_database_independent") is True
        and isolation.get("source_routes_read_only") is True
        and isolation.get("predecessor_databases_immutable") is True
        and isolation.get("production_database_selected") is False
        and isolation.get("production_storage_selected") is False
        and len(writable_database_identities) == 3
        and strict_test_identities
        and len(set(writable_database_identities)) == 3
        and not predecessor_databases.intersection(writable_database_identities)
    ):
        blockers.append("blocked_sv1_environment_isolation")

    inventory = _get(summary, "source_inventory", {})
    manifest = _get(summary, "scale_manifest", {})
    eligible = _as_int(manifest.get("selected_eligible_media_count"), -1) if isinstance(manifest, Mapping) else -1
    if not isinstance(inventory, Mapping) or _as_int(inventory.get("safely_usable_real_media_count"), -1) < 10000:
        blockers.append("blocked_sv1_source_inventory_insufficient")
    if not (
        isinstance(manifest, Mapping)
        and 10000 <= eligible <= 15000
        and manifest.get("deterministic_selection") is True
        and manifest.get("accepted_current_available_media_included") is True
        and manifest.get("accounting_equality_passed") is True
        and _as_int(manifest.get("synthetic_or_cloned_media_count"), -1) == 0
        and "inventory_outcome_counts" not in manifest
        and isinstance(manifest.get("preselection_outcome_counts"), Mapping)
        and isinstance(manifest.get("final_outcome_counts"), Mapping)
        and bool(manifest.get("preselection_membership_fingerprint"))
        and bool(manifest.get("final_membership_fingerprint"))
    ):
        blockers.append("blocked_sv1_scale_manifest")

    media_import = _get(summary, "media_import", {})
    if not (
        isinstance(media_import, Mapping)
        and media_import.get("all_selected_accounted") is True
        and _as_int(media_import.get("blocking_failed"), -1) == 0
        and _as_int(media_import.get("unexplained_outcome_count"), -1) == 0
        and _as_int(media_import.get("out_of_manifest_import_count"), -1) == 0
        and _as_int(media_import.get("source_mutation_count"), -1) == 0
        and 10000 <= _as_int(media_import.get("eligible_media_after"), -1) <= 15000
        and "app_managed_storage_write_count" not in media_import
        and "copy_import_runtime_seconds" not in media_import
        and _as_int(_get(media_import, "current_invocation.new_import_count", -1)) == 0
        and _as_int(_get(media_import, "current_invocation.storage_write_count", -1)) == 0
        and _get(media_import, "current_invocation.resumed_exact_checkpoint", False) is True
        and _as_int(_get(media_import, "cumulative_checkpoint_state.imported_media_count", -1)) == eligible
        and _as_int(_get(media_import, "cumulative_checkpoint_state.storage_object_count", -1)) == eligible
        and _as_int(_get(media_import, "original_execution.imported_media_count", -1)) == eligible
        and _as_int(_get(media_import, "original_execution.storage_write_count", -1)) == eligible
        and _get(media_import, "original_execution.runtime_evidence_available", True) is False
        and _get(media_import, "original_execution.runtime_seconds", "not-null") is None
    ):
        blockers.append("blocked_sv1_import_accounting")

    ai = _get(summary, "ai_tag_provenance", {})
    if not (
        isinstance(ai, Mapping)
        and _as_float(ai.get("coverage"), -1.0) == 1.0
        and _as_int(ai.get("missing_provenance_count"), -1) == 0
        and _as_int(ai.get("fingerprint_mismatch_reuse_count"), -1) == 0
        and ai.get("external_provider_calls") == 0
        and ai.get("model_download_count") == 0
        and _as_int(ai.get("ai_coverage_ledger_count"), -1) == eligible
        and bool(ai.get("ai_coverage_ledger_fingerprint"))
        and _as_int(_get(ai, "original_accepted_execution.reused_media_count", -1)) == 3420
        and _as_int(_get(ai, "original_accepted_execution.newly_inferred_media_count", -1)) == 8580
        and _get(ai, "original_accepted_execution.ai_inference_executed", False) is True
        and _as_int(_get(ai, "current_repair_invocation.checkpoint_existing_covered_media_count", -1)) == eligible
        and _as_int(_get(ai, "current_repair_invocation.newly_inferred_media_count", -1)) == 0
        and _get(ai, "current_repair_invocation.ai_inference_rerun", True) is False
    ):
        blockers.append("blocked_sv1_ai_tag_coverage")

    export = _get(summary, "evidence_export", {})
    if not (
        isinstance(export, Mapping)
        and export.get("passed") is True
        and _as_int(export.get("development_row_id_dependency_count"), -1) == 0
        and export.get("package_checksum_manifest_passed") is True
    ):
        blockers.append("blocked_sv1_evidence_export")
    evidence_import = _get(summary, "evidence_import", {})
    evidence_tables = _get(evidence_import, "per_table_accounting", {})
    evidence_export_counts = _get(export, "table_counts", {})
    required_media_bound_tables = {
        "source_metadata_records", "source_tag_observations", "source_name_observations",
        "source_concept_evidence", "source_concept_fallback_search_index",
    }
    evidence_equations_passed = (
        isinstance(evidence_tables, Mapping)
        and isinstance(evidence_export_counts, Mapping)
        and set(evidence_tables) == set(evidence_export_counts)
        and all(
        isinstance(row, Mapping)
        and _as_int(row.get("exported"), -1) == _as_int(evidence_export_counts.get(table), -2)
        and _as_int(row.get("exported"), -1) == sum(
            _as_int(row.get(key), -1)
            for key in (
                "inserted", "compatible_existing", "deferred_target_missing",
                "rejected_incompatible", "blocking_failed",
            )
        )
        and row.get("equation_balanced") is True
        and _as_int(row.get("rejected_incompatible"), -1) == 0
        and _as_int(row.get("blocking_failed"), -1) == 0
        for table, row in evidence_tables.items()
        )
    )
    fallback_accounting = _get(evidence_tables, "source_concept_fallback_search_index", {})
    if not (
        isinstance(evidence_import, Mapping)
        and _as_int(evidence_import.get("blocking_failed"), -1) == 0
        and _as_int(evidence_import.get("unexplained_item_count"), -1) == 0
        and _as_int(evidence_import.get("accepted_evidence_silently_dropped"), -1) == 0
        and _as_int(evidence_import.get("development_row_id_dependency_count"), -1) == 0
        and evidence_import.get("exact_stable_key_membership_passed") is True
        and evidence_import.get("all_table_equations_balanced") is True
        and evidence_import.get("atomic_import_contract_enforced") is True
        and evidence_import.get("success_ledger_written_only_after_commit") is True
        and _as_int(evidence_import.get("current_reaudit_write_count"), -1) == 0
        and _as_int(evidence_import.get("extra_materialized_count"), -1) == 0
        and evidence_equations_passed
        and required_media_bound_tables.issubset(set(evidence_tables))
        and all(
            _as_int(_get(evidence_tables, f"{table}.target_missing_reference_count", -1)) >= 0
            for table in required_media_bound_tables
        )
        and _as_int(evidence_import.get("fallback_search_target_missing_count"), -1)
        == _as_int(_get(fallback_accounting, "deferred_target_missing", -2))
    ):
        blockers.append("blocked_sv1_evidence_import")

    denominator = _get(summary, "denominator_audit", {})
    if not (
        isinstance(denominator, Mapping)
        and denominator.get("accounting_equality_passed") is True
        and denominator.get("mandatory_and_supplemental_distinguished") is True
        and _as_int(denominator.get("unclassified_count"), -1) == 0
        and _as_int(denominator.get("unexplained_count"), -1) == 0
        and denominator.get("canonical_runtime_denominator_changed") is False
        and denominator.get("independent_stored_path_parser_executed") is True
        and denominator.get("stored_path_population_derived_independently") is True
        and _as_float(denominator.get("selected_media_classification_coverage"), -1.0) == 1.0
        and bool(denominator.get("denominator_classification_fingerprint"))
        and denominator.get("database_identity") == _get(summary, "environment_isolation.scale_database_identity", None)
        and denominator.get("exact_membership_equality") is True
        and denominator.get("safe_to_publish_denominator") is True
        and _as_int(denominator.get("manifest_content_key_count"), -1) == eligible
        and _as_int(denominator.get("database_content_key_count"), -1) == eligible
        and _as_int(denominator.get("duplicate_manifest_content_key_count"), -1) == 0
        and _as_int(denominator.get("missing_in_database_count"), -1) == 0
        and _as_int(denominator.get("extra_in_database_count"), -1) == 0
        and bool(denominator.get("manifest_membership_fingerprint"))
        and bool(denominator.get("database_membership_fingerprint"))
        and bool(denominator.get("missing_membership_fingerprint"))
        and bool(denominator.get("extra_membership_fingerprint"))
    ):
        blockers.append("blocked_sv1_denominator_audit")

    r2r = _get(summary, "r2r_reuse", {})
    if not (
        isinstance(r2r, Mapping)
        and r2r.get("exact_pair_membership_passed") is True
        and r2r.get("fingerprint_compatible") is True
        and _as_int(r2r.get("accepted_pair_count"), -1) == 3319
        and _as_int(r2r.get("must_link_count"), -1) == 1522
        and _as_int(r2r.get("cannot_link_count"), -1) == 1791
        and _as_int(r2r.get("deferred_nonblocking_count"), -1) == 6
        and _as_float(r2r.get("coverage"), -1.0) == 1.0
    ):
        blockers.append("blocked_sv1_graph_safety")

    identity = _get(summary, "identity_traceability", {})
    pair = _get(summary, "pair_accounting", {})
    graph = _get(summary, "graph_safety", {})
    graph_zero = (
        "multi_stable_id_creator_component_count",
        "direct_cannot_link_violation_count",
        "transitive_cannot_link_violation_count",
        "unauthorized_cross_role_component_count",
        "unknown_role_materialization_count",
        "deferred_identity_union_count",
        "duplicate_active_stable_identity_count",
    )
    if not (
        isinstance(identity, Mapping)
        and identity.get("accepted_606_family_traceability_passed") is True
        and _as_int(identity.get("accepted_family_count"), -1) == 606
        and _as_int(identity.get("human_review_queue_count"), -1) == 0
        and _as_int(identity.get("needs_review_normal_pipeline_count"), -1) == 0
        and isinstance(pair, Mapping)
        and pair.get("candidate_equation_passed") is True
        and pair.get("all_pairs_creator_alias_expansion_used") is False
        and isinstance(graph, Mapping)
        and graph.get("graph_audit_algorithm_version") == "active_bipartite_connected_components_v2"
        and bool(graph.get("component_membership_fingerprint"))
        and bool(graph.get("pair_membership_fingerprint"))
        and graph.get("giant_component_recurrence") is False
        and not any(_as_int(graph.get(key), -1) for key in graph_zero)
    ):
        blockers.append("blocked_sv1_graph_safety")

    independent_graphs = _get(summary, "independent_graph_metrics", {})
    expected_graph_databases = {
        "scale": _get(isolation, "scale_database_identity", None),
        "promotion": _get(isolation, "promotion_database_identity", None),
        "rebuild": _get(isolation, "rebuild_database_identity", None),
    }
    independent_graphs_passed = (
        isinstance(independent_graphs, Mapping)
        and set(independent_graphs) == set(expected_graph_databases)
        and all(
            isinstance(independent_graphs.get(name), Mapping)
            and independent_graphs[name].get("database_identity") == database
            and independent_graphs[name].get("graph_audit_algorithm_version") == "active_bipartite_connected_components_v2"
            and bool(independent_graphs[name].get("component_membership_fingerprint"))
            and bool(independent_graphs[name].get("pair_membership_fingerprint"))
            and independent_graphs[name].get("giant_component_recurrence") is False
            and not any(_as_int(independent_graphs[name].get(key), -1) for key in graph_zero)
            for name, database in expected_graph_databases.items()
        )
    )
    if not independent_graphs_passed:
        blockers.append("blocked_sv1_graph_safety")

    rebuild = _get(summary, "actual_rebuild_verification", {})
    media_equality = _get(summary, "media_count_equality", {})
    new_media = _get(summary, "true_new_media_search_benchmark", {})
    python = _get(summary, "python_identity", {})
    if not (
        isinstance(rebuild, Mapping)
        and _as_int(rebuild.get("derived_row_import_count"), -1) == 0
        and _as_float(rebuild.get("accepted_r2r_disposition_compatibility"), -1.0) == 1.0
        and _as_float(rebuild.get("accepted_creator_family_traceability"), -1.0) == 1.0
        and _as_int(rebuild.get("blocking_creator_gap_count"), -1) == 0
        and rebuild.get("actual_r2r_ml2_derivation_replayed") is True
        and _as_int((rebuild.get("logical_subset_comparison") or {}).get("graph_logical_mismatch_count"), -1) == 0
        and _as_int((rebuild.get("logical_subset_comparison") or {}).get("search_logical_mismatch_count"), -1) == 0
        and (rebuild.get("logical_subset_comparison") or {}).get("numeric_row_id_equality_claimed") is False
        and bool(rebuild.get("ledger_fingerprint"))
        and bool(rebuild.get("ledger_algorithm_version"))
        and bool(rebuild.get("derivation_algorithm_identity"))
    ):
        blockers.append("blocked_sv1_evidence_import")
    if not (
        isinstance(media_equality, Mapping)
        and media_equality.get("passed") is True
        and len(set(_as_int(media_equality.get(key), -1) for key in ("manifest_count", "database_count", "import_ledger_count", "ai_ledger_count"))) == 1
    ):
        blockers.append("blocked_sv1_import_accounting")
    if not (
        isinstance(new_media, Mapping)
        and _as_int(new_media.get("case_count"), -1) == 40
        and _as_int(new_media.get("scale_unsupported_result_count"), -1) == 0
        and _as_int(new_media.get("promotion_unsupported_result_count"), -1) == 0
        and _as_int(new_media.get("rebuild_unsupported_result_count"), -1) == 0
        and _as_int(new_media.get("leakage_count"), -1) == 0
        and bool(new_media.get("deterministic_selection_fingerprint"))
    ):
        blockers.append("blocked_sv1_search_correctness")
    if not (
        isinstance(python, Mapping)
        and "sys_executable" not in python
        and "code_root" not in python
        and str(python.get("python_version") or "").startswith("3.12.0")
        and bool(python.get("architecture"))
        and python.get("interpreter_class") == "repo_local_venv"
        and bool(python.get("code_root_fingerprint"))
    ):
        blockers.append("blocked_sv1_environment_isolation")

    search = _get(summary, "search_benchmark", {})
    search_zero = (
        "unsupported_result_count",
        "rejected_only_result_count",
        "superseded_only_result_count",
        "invalid_or_deleted_only_result_count",
        "and_leakage_count",
        "search_caused_identity_mutation_count",
    )
    if not isinstance(search, Mapping) or any(_as_int(search.get(key), -1) for key in search_zero):
        blockers.append("blocked_sv1_search_correctness")
    if not (
        isinstance(search, Mapping)
        and search.get("performance_gate_passed") is True
        and _as_float(search.get("scale_p95_ms"), 1e12) <= _as_float(search.get("allowed_scale_p95_ms"), -1.0)
        and _as_float(search.get("scale_max_ms"), 1e12) <= 3000.0
        and _as_float(search.get("promotion_max_ms"), 1e12) <= 3000.0
    ):
        blockers.append("blocked_sv1_search_performance")

    promotion = _get(summary, "promotion_rehearsal", {})
    if not isinstance(promotion, Mapping) or promotion.get("rollback_fingerprint_restoration") is not True:
        blockers.append("blocked_sv1_promotion_rollback")
    if not (
        isinstance(promotion, Mapping)
        and _as_int(promotion.get("second_import_mutation_count"), -1) == 0
        and _as_int(promotion.get("logical_cross_database_mismatch_count"), -1) == 0
    ):
        blockers.append("blocked_sv1_idempotency")

    mutation = _get(summary, "mutation_proof", {})
    immutable = _get(summary, "immutable_artifact_proof", {})
    validation = _get(summary, "validation", {})
    root_proof = _get(summary, "prewrite_root_containment", {})
    orchestration = _get(summary, "canonical_orchestration", {})
    operations = _get(summary, "operation_counts", {})
    forbidden_operation_keys = (
        "provider_calls",
        "pixiv_calls",
        "gallery_dl_calls",
        "external_llm_calls",
        "production_operations",
        "entity_operations",
        "confirmed_assignment_operations",
        "truth_promotion_operations",
        "source_mutations",
        "localization_operations",
    )
    if not (
        isinstance(mutation, Mapping)
        and mutation.get("predecessor_databases_unchanged") is True
        and mutation.get("media_media_tags_unchanged_during_promotion") is True
        and mutation.get("protected_forbidden_tables_unchanged") is True
        and isinstance(operations, Mapping)
        and not any(_as_int(operations.get(key), -1) for key in forbidden_operation_keys)
    ):
        blockers.append("blocked_sv1_fixed_or_forbidden_mutation")

    immutable_required = (
        "accepted_manifest_import_ai_package_unchanged",
        "storage_object_membership_unchanged",
        "scale_protected_tables_unchanged",
        "promotion_protected_tables_unchanged",
        "accepted_predecessor_databases_unchanged",
    )
    if not (
        isinstance(immutable, Mapping)
        and immutable.get("passed") is True
        and all(immutable.get(key) is True for key in immutable_required)
        and bool(immutable.get("proof_fingerprint"))
        and isinstance(mutation, Mapping)
        and mutation.get("immutable_heavy_artifact_proof_passed") is True
    ):
        blockers.append("blocked_sv1_fixed_or_forbidden_mutation")

    if not (
        isinstance(validation, Mapping)
        and validation.get("current_candidate_validation_passed") is True
        and validation.get("head_sha_matches_current") is True
        and validation.get("changed_file_fingerprint_matches") is True
        and validation.get("python_identity_fingerprint_matches") is True
        and validation.get("validation_ledger_fingerprint_verified") is True
        and validation.get("py_compile_passed") is True
        and validation.get("focused_tests_passed") is True
        and validation.get("documentation_contract_tests_passed") is True
        and validation.get("full_non_e2e_passed") is True
    ):
        blockers.append("blocked_sv1_global_test_baseline")

    if not (
        isinstance(root_proof, Mapping)
        and root_proof.get("passed") is True
        and root_proof.get("validation_order") == "resolved_and_validated_before_mkdir_or_artifact_write"
    ):
        blockers.append("blocked_sv1_fixed_or_forbidden_mutation")

    canonical_stages = {
        "prepare", "import", "ai", "evidence", "promotion", "benchmark", "rebuild",
        "connected-graph-audits", "repair-benchmark", "finalization-accounting",
        "validation", "repair-finalize",
    }
    if not (
        isinstance(orchestration, Mapping)
        and orchestration.get("stage") == "all"
        and orchestration.get("complete") is True
        and set(orchestration.get("stages") or ()) == canonical_stages
    ):
        blockers.append("blocked_sv1_fixed_or_forbidden_mutation")

    redaction = _get(summary, "public_redaction", {})
    pack = _get(summary, "review_pack", {})
    if not (
        isinstance(redaction, Mapping)
        and redaction.get("passed") is True
        and redaction.get("negative_control_passed") is True
        and redaction.get("exact_final_bytes_scanned") is True
        and _as_int(redaction.get("absolute_path_finding_count"), -1) == 0
        and isinstance(pack, Mapping)
        and pack.get("integrity_passed") is True
        and pack.get("member_checksum_equality_passed") is True
        and pack.get("canonical_final_pack") is True
        and pack.get("pack_fingerprint_recorded_privately") is True
        and pack.get("pack_id") == "sv1-finalization-safety-canonical-pack-v2"
    ):
        blockers.append("blocked_sv1_fixed_or_forbidden_mutation")

    route = _get(summary, "route_decision", {})
    if not (
        isinstance(route, Mapping)
        and route.get("route_approved") is False
        and route.get("recommended_next_phase") == "SCV2-SV1B"
        and route.get("next_phase_started") is False
    ):
        result.fail("sv1_route_boundary_failed", "SV1-A must recommend SCV2-SV1B with route_approved=false.", path="route_decision")

    derived = sorted(set(blockers))
    declared = sorted(set(pipeline.get("active_blockers") or ())) if isinstance(pipeline, Mapping) else []
    if derived != declared:
        result.fail("sv1_active_blockers_incomplete", "SV1 active_blockers must exactly match independently derived blockers.", path="pipeline_contract.active_blockers", expected=derived, actual=declared)
    target = bool(pipeline.get("target_met")) if isinstance(pipeline, Mapping) else False
    safe = bool(pipeline.get("safe_to_merge")) if isinstance(pipeline, Mapping) else False
    if derived:
        if target or safe or status == "partial_sv1_media_ai_scale_and_stable_key_promotion_complete":
            result.fail("sv1_target_overclaimed", "SV1 cannot claim target/safe with blockers.", path="pipeline_contract")
    elif not (
        status == "partial_sv1_media_ai_scale_and_stable_key_promotion_complete"
        and target is False
        and safe is True
        and pipeline.get("route_approved") is False
        and pipeline.get("semantic_completeness_claimed") is False
        and pipeline.get("full_library_readiness_claimed") is False
        and pipeline.get("production_readiness_claimed") is False
        and pipeline.get("provider_readiness_claimed") is False
        and pipeline.get("entity_readiness_claimed") is False
        and pipeline.get("full_pipeline_completion_claimed") is False
    ):
        result.fail("sv1_target_claim_incomplete", "Blocker-free SV1-A evidence requires the exact partial bounded claim.", path="pipeline_contract")


def _check_sv1b_controlled_pixiv_metadata_localization_source_graph_closure(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    """Validate SV1B automated closure while preserving the user-acceptance gate."""

    pipeline = _get(summary, "pipeline_contract", {})
    status = pipeline.get("status") if isinstance(pipeline, Mapping) else None
    if status not in SV1B_CONTROLLED_PIXIV_METADATA_LOCALIZATION_SOURCE_GRAPH_CLOSURE_STATUSES:
        result.fail("sv1b_status_invalid", "SV1B status must use the registered vocabulary.", path="pipeline_contract.status", actual=status)

    blockers: list[str] = []
    if _missing_required_stages(contract, summary):
        blockers.append("blocked_sv1b_validation")

    sync = _get(summary, "repository_sync_preflight", {})
    if not (
        isinstance(sync, Mapping)
        and sync.get("passed") is True
        and sync.get("accepted_merge_sha") == "46861489fa0b3b05ae917a99a3932897efd70365"
        and sync.get("accepted_evidence_head") == "af073ca0ad2a9df9418cf072dc381d7b2c10216a"
        and sync.get("branch_start_sha") == "46861489fa0b3b05ae917a99a3932897efd70365"
        and sync.get("local_main_equals_origin_main_before_branch") is True
        and _as_int(sync.get("tracked_change_count_before_sync"), -1) == 0
        and _as_int(sync.get("staged_change_count_before_sync"), -1) == 0
        and sync.get("user_owned_artifacts_preserved") is True
    ):
        blockers.append("blocked_sv1b_repository_sync")

    isolation = _get(summary, "environment_isolation", {})
    primary_db = str(_get(isolation, "primary_database_identity", ""))
    replay_db = str(_get(isolation, "replay_database_identity", ""))
    accepted_dbs = {
        "blombooru_scv2_r2r_dryrun_test_20260710",
        "blombooru_scv2_ml1_acquisition_test_20260712",
        "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715",
        "blombooru_scv2_sv1_controlled_scale_test_20260718",
        "blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1",
        "blombooru_scv2_sv1_rebuild_verification_test_20260718",
    }
    strict_db = lambda value: bool(re.fullmatch(r"blombooru_[a-z0-9]+(?:_[a-z0-9]+)*", value)) and "test" in value.split("_") and "prod" not in value
    if not (
        isinstance(isolation, Mapping)
        and isolation.get("passed") is True
        and isolation.get("violet_env") == "test"
        and primary_db != replay_db
        and strict_db(primary_db)
        and strict_db(replay_db)
        and not accepted_dbs.intersection({primary_db, replay_db})
        and isolation.get("accepted_storage_read_only") is True
        and isolation.get("production_selected") is False
    ):
        blockers.append("blocked_sv1b_environment_isolation")

    immutable = _get(summary, "immutable_input_proof", {})
    if not (
        isinstance(immutable, Mapping)
        and immutable.get("passed") is True
        and immutable.get("manifest_fingerprint") == "5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f"
        and immutable.get("all_before_after_fingerprints_equal") is True
        and _as_int(immutable.get("accepted_database_mutation_count"), -1) == 0
        and _as_int(immutable.get("accepted_storage_mutation_count"), -1) == 0
    ):
        blockers.append("blocked_sv1b_environment_isolation")

    checkpoint_a = _get(summary, "accepted_baseline_checkpoint", {})
    checkpoint_databases = (
        _get(checkpoint_a, "primary", {}), _get(checkpoint_a, "replay", {})
    )
    if not (
        isinstance(checkpoint_a, Mapping)
        and checkpoint_a.get("passed") is True
        and checkpoint_a.get("checkpoint") == "A_ACCEPTED_BASELINE"
        and checkpoint_a.get("manifest_fingerprint")
        == "5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f"
        and checkpoint_a.get("accepted_r2r_snapshot_fingerprint")
        == "25090761abff2c2ae9f7ef8d9ea04904c47a9f3a43ce03ab660a39502ae792fc"
        and checkpoint_a.get("provider_tooling_executed_before_checkpoint") is False
        and bool(checkpoint_a.get("checkpoint_fingerprint"))
        and all(
            isinstance(database, Mapping)
            and _as_int(_get(database, "accepted_stable_key_reconciliation.missing_accepted_stable_keys", -1), -1) == 0
            and _as_int(_get(database, "accepted_stable_key_reconciliation.extra_nonderived_stable_keys", -1), -1) == 0
            and _as_int(_get(database, "accepted_stable_key_reconciliation.accepted_payload_drift", -1), -1) == 0
            and _as_int(database.get("derived_graph_row_count"), -1) == 0
            and _as_int(database.get("phase_owned_delta_row_count"), -1) == 0
            and _as_int(database.get("phase_owned_provider_execution_row_count"), -1) == 0
            for database in checkpoint_databases
        )
    ):
        blockers.append("blocked_sv1b_accepted_baseline_checkpoint")

    retry1_forensics = _get(summary, "retry1_forensics", {})
    if not (
        isinstance(retry1_forensics, Mapping)
        and retry1_forensics.get("passed") is True
        and retry1_forensics.get("read_only") is True
        and retry1_forensics.get("retry1_provider_execution_authorized") is False
        and _as_int(retry1_forensics.get("payload_drift_row_count"), -1) == 489
        and _as_int(retry1_forensics.get("accepted_provider_fact_mutation_count"), -1) == 0
        and _as_int(retry1_forensics.get("stable_identity_change_count"), -1) == 0
    ):
        blockers.append("blocked_sv1b_accepted_provider_fact_mutation")

    phase_delta = _get(summary, "primary_phase_delta_checkpoint", {})
    if not (
        isinstance(phase_delta, Mapping)
        and phase_delta.get("passed") is True
        and phase_delta.get("checkpoint") == "B_PRIMARY_PHASE_DELTA"
        and _as_int(phase_delta.get("accepted_rows_missing"), -1) == 0
        and _as_int(phase_delta.get("accepted_stable_identities_changed"), -1) == 0
        and _as_int(phase_delta.get("accepted_provider_facts_changed"), -1) == 0
        and _as_int(phase_delta.get("phase_delta_envelope_failure_count"), -1) == 0
        and phase_delta.get("accepted_baseline_plus_phase_delta_equation_passed") is True
        and phase_delta.get("retry1_deterministic_transformation_reproduced") is True
        and bool(phase_delta.get("phase_delta_fingerprint"))
    ):
        blockers.append("blocked_sv1b_primary_phase_delta_checkpoint")

    hardening = _get(summary, "provider_hardening", {})
    hardening_flags = (
        "persistent_cross_process_spacing_passed",
        "spacing_survives_restart_and_resume",
        "manifest_scoped_outcome_keys_passed",
        "conflict_mismatch_persistence_passed",
        "terminal_classifier_precedence_passed",
        "finite_manifest_passed",
        "no_concurrent_duplicate_execution",
        "metadata_only_command_passed",
        "subprocess_arguments_redacted",
        "subprocess_environment_redacted",
    )
    if not (
        isinstance(hardening, Mapping)
        and all(hardening.get(key) is True for key in hardening_flags)
        and _as_float(hardening.get("minimum_spacing_seconds"), -1.0) >= 2.0
        and _as_int(hardening.get("maximum_attempts_per_work"), -1) <= 3
        and hardening.get("fallback_provider_used") is False
        and hardening.get("media_download_enabled") is False
    ):
        blockers.append("blocked_sv1b_provider_hardening")

    credential = _get(summary, "credential_preflight", {})
    default_credential_route = bool(
        credential.get("delimiter_aware_fingerprint_scan_passed") is True
    ) if isinstance(credential, Mapping) else False
    sv1b_waiver_route = bool(
        isinstance(credential, Mapping)
        and credential.get("credential_risk_waiver_accepted") is True
        and credential.get("credential_risk_waiver_policy")
        == "operator_accepted_existing_local_pixiv_credential_risk_sv1b_v1"
        and credential.get("credential_rotation_performed") is False
        and credential.get("known_compromised_secret_fingerprint_scan_performed") is False
        and credential.get("generic_delimiter_aware_secret_scan_passed") is True
        and _as_int(credential.get("raw_credential_exposure_count"), -1) == 0
        and _as_int(credential.get("raw_config_exposure_count"), -1) == 0
        and _as_int(credential.get("credential_like_value_finding_count"), -1) == 0
    )
    if not (
        isinstance(credential, Mapping)
        and credential.get("approved_local_route_available") is True
        and credential.get("operator_confirmation_policy_passed") is True
        and (default_credential_route or sv1b_waiver_route)
        and credential.get("redacted_authentication_preflight_passed") is True
        and credential.get("secret_value_exposed") is False
        and credential.get("raw_configuration_output_exposed") is False
    ):
        blockers.append("blocked_sv1b_provider_authentication")

    candidate = _get(summary, "candidate_accounting", {})
    candidate_total = _as_int(candidate.get("canonical_candidate_media_count"), -1) if isinstance(candidate, Mapping) else -1
    non_candidate = _as_int(candidate.get("explicit_non_candidate_media_count"), -1) if isinstance(candidate, Mapping) else -1
    candidate_page_rows = _as_int(candidate.get("page_media_manifest_row_count"), -1) if isinstance(candidate, Mapping) else -1
    candidate_work_rows = _as_int(candidate.get("distinct_work_manifest_row_count"), -1) if isinstance(candidate, Mapping) else -1
    if not (
        isinstance(candidate, Mapping)
        and _as_int(candidate.get("manifest_media_count"), -1) == 12000
        and candidate_total + non_candidate == 12000
        and candidate.get("accounting_equality_passed") is True
        and candidate.get("independently_reproduced") is True
        and candidate.get("change_from_sv1a_fully_accounted") is True
        and _as_int(candidate.get("unclassified_count"), -1) == 0
        and _as_int(candidate.get("unexplained_count"), -1) == 0
        and bool(candidate.get("page_media_manifest_fingerprint"))
        and bool(candidate.get("distinct_work_manifest_fingerprint"))
        and candidate_page_rows >= candidate_total
        and candidate_work_rows > 0
    ):
        blockers.append("blocked_sv1b_candidate_manifest")

    acquisition = _get(summary, "acquisition_accounting", {})
    page_outcomes = _get(acquisition, "page_outcome_counts", {})
    work_outcomes = _get(acquisition, "work_outcome_counts", {})
    page_closed = sum(_as_int(_get(page_outcomes, key, -1), -1) for key in (
        "metadata_complete", "terminal_remote_unavailable", "deferred_nonblocking_source_page_mismatch"
    ))
    work_closed = sum(_as_int(_get(work_outcomes, key, -1), -1) for key in (
        "metadata_complete",
        "terminal_remote_unavailable",
        "deferred_nonblocking_source_page_mismatch",
    ))
    work_closed += _as_int(_get(work_outcomes, "mixed_closed", 0), 0)
    open_outcome_keys = (
        "unattempted", "pending", "retryable", "authentication_failure",
        "rate_limit_failure", "network_failure", "generic_provider_failure",
        "parser_failure", "normalization_failure", "unresolved_identity_conflict",
        "unexplained_outcome", "blocking_failure",
    )
    if not (
        isinstance(acquisition, Mapping)
        and isinstance(page_outcomes, Mapping)
        and isinstance(work_outcomes, Mapping)
        and page_closed == _as_int(acquisition.get("requested_page_count"), -2) == candidate_page_rows
        and work_closed == _as_int(acquisition.get("distinct_work_count"), -2) == candidate_work_rows
        and not any(_as_int(_get(page_outcomes, key, -1), -1) for key in open_outcome_keys)
        and not any(_as_int(_get(work_outcomes, key, -1), -1) for key in open_outcome_keys)
        and acquisition.get("page_equation_passed") is True
        and acquisition.get("work_equation_passed") is True
        and acquisition.get("checkpoint_after_every_attempt") is True
        and _as_int(acquisition.get("out_of_manifest_attempt_count"), -1) == 0
        and _as_int(acquisition.get("concurrent_duplicate_attempt_count"), -1) == 0
    ):
        blockers.append("blocked_sv1b_acquisition_incomplete")

    retention = _get(summary, "metadata_retention", {})
    localization = _get(summary, "localization_closure", {})
    localization_equations = _get(localization, "localization_equations", {})
    localization_transport = _get(localization, "transport_logging", {})
    final_localization_reason_keys = (
        "localization_ambiguity_count",
        "final_untranslated_echo_count",
        "final_missing_result_count",
        "final_invalid_display_count",
        "final_invalid_aliases_count",
        "final_unexpected_result_count",
        "final_duplicate_result_count",
    )
    manual_pending_count = _as_int(
        localization.get("manual_localization_review_pending_count"), -1
    ) if isinstance(localization, Mapping) else -1
    manual_pending_reason_count = sum(
        _as_int(localization.get(key), -1)
        for key in final_localization_reason_keys
    ) if isinstance(localization, Mapping) else -1
    if not (
        isinstance(retention, Mapping)
        and retention.get("raw_and_normalized_package_retained") is True
        and retention.get("creator_identity_fields_retained") is True
        and retention.get("work_title_and_provider_tags_retained") is True
        and retention.get("trusted_parent_policy_passed") is True
        and retention.get("entity_truth_write_count") == 0
        and retention.get("media_tags_truth_write_count") == 0
        and isinstance(localization, Mapping)
        and localization.get("eligible_ai_tag_missing_count") == 0
        and localization.get("silently_missing_eligible_count") == 0
        and localization.get("missing_disposition_count") == 0
        and localization.get("duplicate_disposition_count") == 0
        and localization.get("localization_accounting_closed") is True
        and manual_pending_count >= 0
        and manual_pending_reason_count == manual_pending_count
        and localization.get("localization_translation_complete")
        is (manual_pending_count == 0)
        and localization.get("item_validation_policy_version")
        == "sv1b_localization_item_validation_v1"
        and localization.get("display_preserve_policy_version")
        == "sv1b_localization_display_preserve_v1"
        and localization.get("targeted_adjudication_prompt_version")
        == "sv1b_localization_targeted_item_prompt_v1"
        and localization.get("manual_review_policy_version")
        == "sv1b_manual_localization_review_pending_v1"
        and _as_int(localization.get("manual_review_pending_threshold"), -1)
        == 8
        and _as_int(localization.get("initial_eligible_count"), -1) == 1788
        and _as_int(localization.get("explicit_proper_noun_exclusion_count"), -1)
        == 454
        and _as_int(localization.get("initial_eligible_count"), -1)
        == _as_int(localization.get("accepted_new_translation_count"), -2)
        + _as_int(localization.get("explicit_display_preserved_count"), -2)
        + manual_pending_count
        + _as_int(localization.get("manual_localization_override_count"), -2)
        and _as_int(localization.get("external_llm_call_count"), -1)
        == _as_int(localization.get("standard_batch_call_count"), -2)
        + _as_int(localization.get("item_adjudication_call_count"), -2)
        and localization.get("primary_replay_translation_fingerprint_equal") is True
        and isinstance(localization_equations, Mapping)
        and localization_equations
        and all(value is True for value in localization_equations.values())
        and isinstance(localization_transport, Mapping)
        and localization_transport.get("minimum_log_level") == "WARNING"
        and localization_transport.get(
            "process_log_record_factory_redaction_enabled"
        ) is False
        and _as_int(localization_transport.get("root_handler_filters_added"), -1)
        == 0
        and localization_transport.get("unrelated_loggers_modified") is False
        and localization_transport.get("non_sensitive_url_context_preserved")
        is True
        and localization_transport.get("exception_context_preserved") is True
        and localization_transport.get("request_response_body_logging_enabled") is False
        and localization.get("provider_tags_written_to_media_tags_count") == 0
        and localization.get("original_provider_text_preserved") is True
        and _as_float(localization.get("projected_and_actual_llm_cost_usd"), 1e9) <= 10.0
        and localization.get("fallback_provider_used") is False
        and localization.get("image_upload_count") == 0
    ):
        blockers.append("blocked_sv1b_normalization_or_localization")
    elif manual_pending_count > 8:
        if localization.get("downstream_progression_allowed") is False:
            blockers.append("blocked_sv1b_systemic_localization_quality")
        else:
            blockers.append("blocked_sv1b_normalization_or_localization")
    elif localization.get("downstream_progression_allowed") is not True:
        blockers.append("blocked_sv1b_normalization_or_localization")

    r2r = _get(summary, "r2r_replay_accounting", {})
    if not (
        isinstance(r2r, Mapping)
        and r2r.get("accepted_snapshot_fingerprint") == "25090761abff2c2ae9f7ef8d9ea04904c47a9f3a43ce03ab660a39502ae792fc"
        and r2r.get("exact_endpoint_and_disposition_membership_passed") is True
        and _as_int(r2r.get("accepted_pair_count"), -1) == sum(_as_int(r2r.get(key), -1) for key in (
            "comparable_count", "genuine_target_missing_count", "ambiguous_remap_count", "conflicting_remap_count"
        ))
        and _as_int(r2r.get("ambiguous_remap_count"), -1) == 0
        and _as_int(r2r.get("conflicting_remap_count"), -1) == 0
        and r2r.get("compatibility_derived_from_verified_pairs") is True
    ):
        blockers.append("blocked_sv1b_r2r_replay")

    baseline = _get(summary, "baseline_preservation", {})
    if not (
        isinstance(baseline, Mapping)
        and _as_int(baseline.get("accepted_family_count"), -1) == 606
        and _as_int(baseline.get("accepted_family_traceable_count"), -1) == 606
        and _as_int(baseline.get("accepted_stable_identity_disappeared_count"), -1) == 0
        and _as_int(baseline.get("cannot_link_became_identity_union_count"), -1) == 0
        and _as_int(baseline.get("search_only_became_identity_count"), -1) == 0
        and baseline.get("every_changed_family_has_governed_reason") is True
    ):
        blockers.append("blocked_sv1b_graph_safety")

    graph_zero_keys = (
        "multi_stable_id_creator_component_count", "direct_cannot_link_violation_count",
        "transitive_cannot_link_violation_count", "deferred_identity_union_count",
        "unauthorized_cross_role_component_count", "unknown_role_materialization_count",
        "duplicate_active_stable_identity_count",
    )
    for name in ("primary_graph_safety", "replay_graph_safety"):
        graph = _get(summary, name, {})
        if not (
            isinstance(graph, Mapping)
            and not any(_as_int(graph.get(key), -1) for key in graph_zero_keys)
            and graph.get("giant_component_recurrence") is False
            and bool(graph.get("concept_signal_link_membership_fingerprint"))
            and bool(graph.get("pair_membership_fingerprint"))
        ):
            blockers.append("blocked_sv1b_graph_safety")

    comparison = _get(summary, "primary_replay_comparison", {})
    if not (
        isinstance(comparison, Mapping)
        and comparison.get("checkpoint_membership_gate_passed") is True
        and _as_int(comparison.get("unexplained_logical_mismatch_count"), -1) == 0
        and comparison.get("numeric_row_id_equality_claimed") is False
    ):
        blockers.append("blocked_sv1b_replay_mismatch")

    search = _get(summary, "search_validation", {})
    search_zero_keys = (
        "unsupported_result_count", "rejected_only_result_count",
        "superseded_only_result_count", "invalid_deleted_only_result_count",
        "and_leakage_count", "search_caused_identity_mutation_count",
        "lifecycle_status_violation_count", "supported_query_missing_result_count",
    )
    if not (
        isinstance(search, Mapping)
        and search.get("counters_derived_from_returned_rows") is True
        and search.get("independent_expected_membership_used") is True
        and search.get("blombooru_tags_protected") is True
        and not any(_as_int(search.get(key), -1) for key in search_zero_keys)
        and _as_float(search.get("p95_latency_ms"), -1.0) >= 0.0
    ):
        blockers.append("blocked_sv1b_search_safety")

    validation = _get(summary, "validation", {})
    if not (
        isinstance(validation, Mapping)
        and _as_int(validation.get("failed_test_count"), -1) == 0
        and _as_int(validation.get("unexplained_skip_count"), -1) == 0
        and validation.get("exact_approved_skip_membership_passed") is True
        and validation.get("full_default_non_e2e_passed") is True
        and validation.get("environment_specific_profiles_passed") is True
        and validation.get("json_parse_passed") is True
        and validation.get("public_redaction_passed") is True
        and validation.get("git_diff_check_passed") is True
        and validation.get("real_browser_validation_passed") is True
    ):
        blockers.append("blocked_sv1b_validation")

    manual = _get(summary, "manual_acceptance", {})
    categories = _get(manual, "category_case_counts", {})
    if not (
        isinstance(manual, Mapping)
        and manual.get("required") is True
        and manual.get("status") == "pending_user"
        and _as_int(manual.get("case_count"), -1) == 40
        and isinstance(categories, Mapping)
        and dict(categories) == {"pixiv_metadata": 12, "creator_clustering": 8, "shared_name_cannot_link": 6, "ai_tag_localization": 8, "search_and_negative": 6}
        and manual.get("actual_backend_services_used") is True
        and manual.get("result_private_and_uncommitted") is True
        and manual.get("absolute_paths_exposed") is False
        and bool(manual.get("acceptance_case_manifest_fingerprint"))
        and str(manual.get("localhost_url") or "").startswith("http://127.0.0.1:")
    ):
        blockers.append("blocked_sv1b_manual_acceptance_harness")

    operations = _get(summary, "operation_counts", {})
    forbidden_operations = (
        "media_downloads", "media_imports", "ai_tagging_runs", "classification_runs",
        "production_operations", "full_library_operations", "entity_operations",
        "confirmed_assignment_operations", "media_tags_truth_writes",
        "source_icloud_mutations", "fallback_provider_calls", "hidden_daemon_starts",
        "fl1_operations",
    )
    if not isinstance(operations, Mapping) or any(_as_int(operations.get(key), -1) for key in forbidden_operations):
        blockers.append("blocked_sv1b_validation")

    route = _get(summary, "route_decision", {})
    if not (
        isinstance(route, Mapping)
        and route.get("route_approved") is False
        and route.get("recommended_next_phase") == "SCV2-FL1"
        and route.get("next_phase_started") is False
    ):
        blockers.append("blocked_sv1b_validation")

    derived = sorted(set(blockers))
    declared = sorted(set(pipeline.get("active_blockers") or ())) if isinstance(pipeline, Mapping) else []
    if derived != declared:
        result.fail("sv1b_active_blockers_incomplete", "SV1B active_blockers must exactly match independent checks.", path="pipeline_contract.active_blockers", expected=derived, actual=declared)

    exact_pending_claim = (
        status == "automated_sv1b_candidate_ready_manual_acceptance_pending"
        and pipeline.get("target_met") is False
        and pipeline.get("safe_to_merge") is False
        and pipeline.get("route_approved") is False
        and pipeline.get("manual_acceptance_required") is True
        and pipeline.get("manual_acceptance_status") == "pending_user"
    )
    if derived:
        if bool(pipeline.get("target_met")) or bool(pipeline.get("safe_to_merge")) or status == "automated_sv1b_candidate_ready_manual_acceptance_pending":
            result.fail("sv1b_completion_overclaimed", "Blocked SV1B evidence cannot claim an automated acceptance candidate.", path="pipeline_contract")
    elif not exact_pending_claim:
        result.fail("sv1b_pending_claim_incomplete", "Blocker-free SV1B automation must stop at the exact pending-user claim.", path="pipeline_contract")


def _check_sv1b_owner_acceptance_closeout(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    del contract
    pipeline = _get(summary, "pipeline_contract", {})
    status = str(_get(summary, "pipeline_contract.status", ""))
    if status not in SV1B_OWNER_ACCEPTANCE_CLOSEOUT_STATUSES:
        result.fail(
            "sv1b_closeout_status_invalid",
            "SV1B owner closeout status is not recognized.",
            path="pipeline_contract.status",
            expected=SV1B_OWNER_ACCEPTANCE_CLOSEOUT_STATUSES,
            actual=status,
        )

    composite = _get(summary, "composite_acceptance", {})
    exact_composite = (
        isinstance(composite, Mapping)
        and composite.get("passed") is True
        and composite.get("manual_acceptance_status")
        == "accepted_with_known_nonblocking_limitations"
        and _as_int(composite.get("case_count"), -1) == 40
        and _as_int(composite.get("pass_count"), -1) == 37
        and _as_int(
            composite.get("owner_waived_nonblocking_known_limitation_count"), -1
        )
        == 3
        and _as_int(composite.get("pending_count"), -1) == 0
        and _as_int(composite.get("unwaived_fail_count"), -1) == 0
        and sorted(composite.get("owner_waived_case_ids") or ())
        == ["B01", "B04", "B08"]
        and composite.get("owner_waiver_identity")
        == "owner_accepted_sv1b_placeholder_creator_identity_limitations_v1_20260807"
        and composite.get("underlying_mismatch_preserved") is True
        and composite.get("waiver_scope") == "SCV2-SV1B_only"
        and bool(composite.get("file_sha256"))
        and bool(composite.get("composite_fingerprint"))
        and composite.get("binding_fingerprint")
        == "4992ed754539ef1f14500825d0fd78fc448e26846780cd4c64bacc5c2c6c3f81"
        and composite.get("case_manifest_sha256")
        == "b37eb60dc90418959a6b3a7be188dedc29eb29ebf8c85c5303dd8665bdfdad5c"
        and composite.get("delta_audit_sha256")
        == "fe3455b9b9fd2cfcb13d242f01208a378ef69342896905044c789523aaaadbb1"
        and composite.get("old_result_sha256")
        == "6ad0d4d78815de0984a4e563490be91e985e9f109facb462c8528896867ae2b9"
    )
    if not exact_composite:
        result.fail(
            "sv1b_closeout_composite_invalid",
            "Composite acceptance must preserve exact 37 PASS / 3 owner-waived / 0 pending / 0 unwaived-fail accounting and immutable evidence bindings.",
            path="composite_acceptance",
        )

    carry = _get(summary, "behavior_neutral_carry_forward", {})
    exact_carry = (
        isinstance(carry, Mapping)
        and carry.get("passed") is True
        and carry.get("accepted_implementation_head")
        == "e7ada8e83593cbb639f0c1fd4442f76e47537e8d"
        and isinstance(carry.get("closeout_head"), str)
        and len(str(carry.get("closeout_head"))) == 40
        and carry.get("closeout_head") != carry.get("accepted_implementation_head")
        and bool(carry.get("file_sha256"))
        and bool(carry.get("proof_fingerprint"))
        and carry.get("runtime_data_search_graph_localization_semantics_changed")
        is False
        and isinstance(carry.get("changed_files"), list)
        and bool(carry.get("changed_files"))
    )
    if not exact_carry:
        result.fail(
            "sv1b_closeout_carry_forward_invalid",
            "Closeout must bind the accepted implementation to a later behavior-neutral governance-only HEAD.",
            path="behavior_neutral_carry_forward",
        )

    operations = _get(summary, "operation_counts", {})
    forbidden_operation_keys = (
        "database_access",
        "database_write",
        "provider_request",
        "llm_request",
        "media_download",
        "production_access",
        "entity_truth_write",
        "provider_derived_media_tags_write",
    )
    if not isinstance(operations, Mapping) or any(
        _as_int(operations.get(key), -1) != 0 for key in forbidden_operation_keys
    ):
        result.fail(
            "sv1b_closeout_forbidden_activity",
            "Owner closeout may not enter database, provider, LLM, media, production, Entity/truth, or provider-derived media_tags routes.",
            path="operation_counts",
        )

    route = _get(summary, "route_decision", {})
    exact_route = (
        isinstance(route, Mapping)
        and route.get("route_approved") is True
        and route.get("route_scope") == "SCV2-FL1_planning_only_no_execution"
        and route.get("fl1_data_execution_authorized") is False
        and route.get("production_authorized") is False
        and route.get("next_phase_started") is False
    )
    if not exact_route:
        result.fail(
            "sv1b_closeout_route_scope_invalid",
            "Route authorization is limited to FL1 planning with no data execution or production.",
            path="route_decision",
        )

    exact_claim = (
        isinstance(pipeline, Mapping)
        and pipeline.get("contract_id")
        == "sv1b_owner_acceptance_closeout_contract_v1"
        and status == "sv1b_accepted_with_known_nonblocking_limitations"
        and pipeline.get("target_met") is False
        and pipeline.get("safe_to_merge") is True
        and pipeline.get("route_approved") is True
        and pipeline.get("manual_acceptance_required") is True
        and pipeline.get("manual_acceptance_status")
        == "accepted_with_known_nonblocking_limitations"
        and list(pipeline.get("active_blockers") or ()) == []
    )
    if not exact_claim:
        result.fail(
            "sv1b_closeout_claim_invalid",
            "SV1B closeout claim must be derived as safe_to_merge with target_met false and a planning-only route.",
            path="pipeline_contract",
        )


def _check_scv2_fl1_p1_foundation(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    pipeline = _get(summary, "pipeline_contract", {})
    status = str(_get(summary, "pipeline_contract.status", ""))
    if status not in SCV2_FL1_P1_FOUNDATION_STATUSES:
        result.fail(
            "fl1_p1_status_invalid",
            "FL1-P1 foundation status is not registered.",
            path="pipeline_contract.status",
            expected=SCV2_FL1_P1_FOUNDATION_STATUSES,
            actual=status,
        )

    blockers = list(_get(summary, "pipeline_contract.active_blockers", []) or [])
    owner_accepted = status == "owner_accepted_for_merge"
    common_claim_valid = (
        isinstance(pipeline, Mapping)
        and pipeline.get("contract_id") == contract.contract_id
    )
    if owner_accepted:
        claim_valid = (
            common_claim_valid
            and pipeline.get("target_met") is True
            and pipeline.get("safe_to_merge") is True
            and pipeline.get("route_approved") is True
            and blockers == []
            and isinstance(pipeline.get("owner_acceptance_identity"), str)
            and bool(str(pipeline.get("owner_acceptance_identity")).strip())
        )
    else:
        claim_valid = (
            common_claim_valid
            and pipeline.get("target_met") is False
            and pipeline.get("safe_to_merge") is False
            and pipeline.get("route_approved") is False
            and bool(blockers)
            and pipeline.get("owner_acceptance_identity") is None
        )
    if not claim_valid:
        result.fail(
            "fl1_p1_claim_invalid",
            "FL1-P1 must either remain fail-closed at an explicit checkpoint or carry an exact owner-accepted merge claim with no blockers.",
            path="pipeline_contract",
        )
    if status == "implementation_ready_for_owner_audit" and blockers != [
        "pending_owner_audit"
    ]:
        result.fail(
            "fl1_p1_owner_audit_blocker_invalid",
            "Audit-ready FL1-P1 evidence must stop only at pending_owner_audit.",
            path="pipeline_contract.active_blockers",
            expected=["pending_owner_audit"],
            actual=blockers,
        )

    authorization = _get(summary, "authorization", {})
    forbidden_authorizations = (
        "production_authorized",
        "real_inventory_authorized",
        "existing_database_access_authorized",
        "data_execution_authorized",
        "provider_authorized",
        "llm_authorized",
        "media_authorized",
        "stable_replay_authorized",
    )
    if not isinstance(authorization, Mapping) or any(
        authorization.get(key) is not False for key in forbidden_authorizations
    ):
        result.fail(
            "fl1_p1_authorization_boundary_invalid",
            "FL1-P1 may not authorize production, real inventory, existing databases, data execution, provider, LLM, media, or Stable Replay.",
            path="authorization",
        )

    isolation = _get(summary, "environment_isolation", {})
    isolation_true_fields = (
        "passed",
        "git_head_match",
        "python_identity_match",
        "database_identity_explicit",
        "database_path_new_and_contained",
        "source_root_explicit_and_contained",
        "storage_root_explicit_and_contained",
        "source_storage_non_overlapping",
        "unknown_identity_rejected",
        "production_identity_rejected",
        "synthetic_only",
    )
    isolation_valid = (
        isinstance(isolation, Mapping)
        and isolation.get("environment") in {"test", "development"}
        and isinstance(isolation.get("database_identity"), str)
        and str(isolation.get("database_identity")).startswith("violet_fl1_")
        and all(isolation.get(key) is True for key in isolation_true_fields)
        and _as_int(isolation.get("forbidden_root_overlap_count"), -1) == 0
        and isolation.get("production_fallback_used") is False
        and isolation.get("existing_database_accessed") is False
    )
    if not isolation_valid:
        result.fail(
            "fl1_p1_environment_isolation_invalid",
            "FL1-P1 requires explicit, contained, synthetic Dev/Test identities with no fallback or existing database access.",
            path="environment_isolation",
        )

    mutation = _get(summary, "mutation_policy", {})
    if not isinstance(mutation, Mapping) or not (
        mutation.get("default_deny") is True
        and mutation.get("allowlist_explicit") is True
        and mutation.get("ledger_read_contained") is True
        and mutation.get("production_mutation_allowed") is False
        and mutation.get("source_mutation_allowed") is False
        and mutation.get("unexpected_mutation_allowed") is False
    ):
        result.fail(
            "fl1_p1_mutation_policy_invalid",
            "FL1-P1 mutation policy must default deny and prohibit production, source, and unexpected mutations.",
            path="mutation_policy",
        )

    ledger = _get(summary, "ledger", {})
    ledger_valid = (
        isinstance(ledger, Mapping)
        and ledger.get("schema_version") == "violet.scv2-fl1-p1-ledger.v1"
        and ledger.get("stable_item_identity") == "violet.scv2-fl1-item.v1"
        and ledger.get("logical_target_identity")
        == "violet.scv2-fl1-logical-target.v1"
        and _as_int(ledger.get("manifest_entry_count"), -1) >= 1
        and _as_int(ledger.get("source_item_count"), -1) >= 1
        and _as_int(ledger.get("unique_item_count"), -1) >= 1
        and _as_int(ledger.get("duplicate_entry_count"), -1) >= 0
        and _as_int(ledger.get("manifest_entry_count"), -1)
        == _as_int(ledger.get("unique_item_count"), -2)
        + _as_int(ledger.get("duplicate_entry_count"), -2)
        and _as_int(ledger.get("source_item_count"), -1)
        == _as_int(ledger.get("unique_item_count"), -2)
        + _as_int(ledger.get("content_duplicate_item_count"), -2)
        and _as_int(ledger.get("manifest_entry_count"), -1)
        == _as_int(ledger.get("source_item_count"), -2)
        + _as_int(ledger.get("repeated_manifest_entry_count"), -2)
        and _as_int(ledger.get("duplicate_second_mutation_count"), -1) == 0
        and ledger.get("attempt_budget_persisted") is True
        and ledger.get("checkpoint_persisted") is True
        and ledger.get("manual_stop_persisted") is True
        and ledger.get("interrupted_mutation_reconciliation_required") is True
        and ledger.get("generation_conflict_rejected") is True
    )
    if not ledger_valid:
        result.fail(
            "fl1_p1_ledger_invalid",
            "FL1-P1 ledger evidence must prove stable identity, denominator accounting, checkpoints, budgets, stops, and zero duplicate mutation.",
            path="ledger",
        )

    validation = _get(summary, "validation", {})
    validation_fields = (
        "focused_tests_passed",
        "full_non_e2e_passed",
        "production_identity_rejection_passed",
        "unknown_identity_rejection_passed",
        "containment_rejection_passed",
        "mutation_default_deny_passed",
        "duplicate_idempotency_passed",
        "content_fingerprint_deduplication_passed",
        "restart_recovery_passed",
        "interrupted_mutation_reconciliation_passed",
        "failure_budget_stop_passed",
        "per_item_and_global_budget_separation_passed",
        "concurrent_generation_guard_passed",
        "manual_stop_passed",
        "synthetic_isolation_passed",
    )
    if not isinstance(validation, Mapping) or any(
        validation.get(key) is not True for key in validation_fields
    ):
        result.fail(
            "fl1_p1_validation_incomplete",
            "FL1-P1 requires focused and full non-E2E validation of every named safety behavior.",
            path="validation",
        )

    operations = _get(summary, "operation_counts", {})
    required_zero_operations = (
        "production_activity",
        "real_source_inventory_activity",
        "existing_database_read_activity",
        "existing_database_write_activity",
        "provider_activity",
        "llm_activity",
        "media_activity",
        "stable_replay_activity",
        "user_data_cleanup_delete_activity",
    )
    if not isinstance(operations, Mapping) or any(
        _as_int(operations.get(key), -1) != 0 for key in required_zero_operations
    ):
        result.fail(
            "fl1_p1_forbidden_activity",
            "All production, real inventory, existing database, external, media, replay, and cleanup counts must remain zero.",
            path="operation_counts",
        )

    redaction = _get(summary, "public_redaction", {})
    if not isinstance(redaction, Mapping) or not (
        redaction.get("passed") is True
        and redaction.get("private_paths_emitted") is False
    ):
        result.fail(
            "fl1_p1_public_redaction_invalid",
            "FL1-P1 public evidence must pass redaction and emit no private path values.",
            path="public_redaction",
        )


def _check_scv2_fl1_i1_inventory(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
) -> None:
    pipeline = _get(summary, "pipeline_contract", {})
    status = str(_get(summary, "pipeline_contract.status", ""))
    blockers = list(_get(summary, "pipeline_contract.active_blockers", []) or [])
    if status not in SCV2_FL1_I1_INVENTORY_STATUSES:
        result.fail(
            "fl1_i1_status_invalid",
            "FL1-I1 inventory status is not registered.",
            path="pipeline_contract.status",
            expected=SCV2_FL1_I1_INVENTORY_STATUSES,
            actual=status,
        )
    pipeline_valid = (
        isinstance(pipeline, Mapping)
        and pipeline.get("contract_id") == contract.contract_id
        and pipeline.get("target_met") is False
        and pipeline.get("safe_to_merge") is False
        and pipeline.get("route_approved") is False
        and bool(blockers)
    )
    if status == "synthetic_implementation_ready_for_owner_audit":
        pipeline_valid = pipeline_valid and blockers == [
            "pending_owner_audit",
            "real_source_scope_not_authorized",
        ]
    if not pipeline_valid:
        result.fail(
            "fl1_i1_claim_invalid",
            "FL1-I1 synthetic evidence must remain fail-closed at owner audit and exact real-source authorization.",
            path="pipeline_contract",
        )

    authorization = _get(summary, "authorization", {})
    authorization_valid = (
        isinstance(authorization, Mapping)
        and authorization.get("synthetic_fixture_inventory_authorized") is True
        and all(
            authorization.get(key) is False
            for key in (
                "real_source_inventory_authorized",
                "database_access_authorized",
                "app_storage_write_authorized",
                "data_execution_authorized",
                "provider_authorized",
                "llm_authorized",
                "media_authorized",
                "stable_replay_authorized",
                "network_authorized",
            )
        )
    )
    if not authorization_valid:
        result.fail(
            "fl1_i1_authorization_boundary_invalid",
            "FL1-I1 foundation may authorize only synthetic fixture inventory, never real source, DB, storage, data, external, or network activity.",
            path="authorization",
        )

    preflight = _get(summary, "preflight", {})
    preflight_valid = (
        isinstance(preflight, Mapping)
        and preflight.get("source_kind") == "synthetic_fixture"
        and isinstance(preflight.get("source_scope_id"), str)
        and bool(
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
                str(preflight.get("source_scope_id")),
            )
        )
        and all(
            preflight.get(key) is True
            for key in (
                "git_head_match",
                "python_identity_match",
                "source_root_explicit_and_contained",
                "synthetic_only",
                "bounded_item_count",
                "bounded_source_bytes",
            )
        )
        and preflight.get("symlink_following_allowed") is False
        and _as_int(preflight.get("forbidden_root_overlap_count"), -1) == 0
    )
    if not preflight_valid:
        result.fail(
            "fl1_i1_preflight_invalid",
            "FL1-I1 requires an exact, contained, bounded synthetic source with symlink following disabled.",
            path="preflight",
        )

    proof = _get(summary, "read_only_proof", {})
    before = str(_get(summary, "read_only_proof.source_snapshot_before", ""))
    after = str(_get(summary, "read_only_proof.source_snapshot_after", ""))
    proof_valid = (
        isinstance(proof, Mapping)
        and bool(re.fullmatch(r"[0-9a-f]{64}", before))
        and before == after
        and proof.get("source_tree_unchanged") is True
        and proof.get("expected_snapshot_matched") is True
        and all(
            _as_int(proof.get(key), -1) == 0
            for key in (
                "source_mutation_count",
                "database_connection_count",
                "database_write_count",
                "app_storage_write_count",
                "external_request_count",
            )
        )
    )
    if not proof_valid:
        result.fail(
            "fl1_i1_read_only_proof_invalid",
            "FL1-I1 must prove an unchanged source snapshot and zero source, database, app-storage, or external mutation.",
            path="read_only_proof",
        )

    inventory = _get(summary, "inventory", {})
    count_keys = (
        "discovered",
        "supported",
        "unsupported",
        "duplicate",
        "cloud_recall_deferred",
        "unreadable_or_missing",
        "eligible_candidate",
        "imported",
        "import_deferred",
        "import_failed",
        "unresolved",
    )
    counts = {key: _as_int(inventory.get(key), -1) for key in count_keys} if isinstance(inventory, Mapping) else {}
    inventory_valid = (
        isinstance(inventory, Mapping)
        and inventory.get("schema_version")
        == "violet.scv2-fl1-i1-inventory-manifest.v1"
        and inventory.get("membership_identity_version")
        == "violet.scv2-fl1-i1-membership.v1"
        and bool(
            re.fullmatch(
                r"[0-9a-f]{64}", str(inventory.get("manifest_fingerprint", ""))
            )
        )
        and counts.get("discovered", -1) >= 1
        and all(value >= 0 for value in counts.values())
        and counts["discovered"] == counts["supported"] + counts["unsupported"]
        and counts["supported"]
        == counts["duplicate"]
        + counts["cloud_recall_deferred"]
        + counts["unreadable_or_missing"]
        + counts["eligible_candidate"]
        and counts["eligible_candidate"]
        == counts["imported"] + counts["import_deferred"] + counts["import_failed"]
        and counts["imported"] == 0
        and counts["import_failed"] == 0
        and counts["unresolved"] == 0
        and all(
            inventory.get(key) is True
            for key in (
                "discovered_equation_balanced",
                "supported_equation_balanced",
                "eligible_equation_balanced",
                "one_terminal_disposition_per_item",
                "content_fingerprint_deduplication",
            )
        )
        and inventory.get("filename_or_row_order_identity_used") is False
    )
    if not inventory_valid:
        result.fail(
            "fl1_i1_inventory_denominator_invalid",
            "FL1-I1 inventory must balance every denominator equation with zero unresolved or import execution.",
            path="inventory",
        )

    operations = _get(summary, "operation_counts", {})
    operation_valid = (
        isinstance(operations, Mapping)
        and _as_int(operations.get("synthetic_source_file_read_attempts"), -1) >= 0
        and _as_int(operations.get("synthetic_source_file_read_successes"), -1) >= 0
        and _as_int(operations.get("synthetic_source_file_read_successes"), -1)
        <= _as_int(operations.get("synthetic_source_file_read_attempts"), -2)
        and _as_int(operations.get("synthetic_source_bytes_read"), -1) >= 0
        and all(
            _as_int(operations.get(key), -1) == 0
            for key in (
                "production_activity",
                "real_source_inventory_activity",
                "existing_database_read_activity",
                "existing_database_write_activity",
                "app_storage_write_activity",
                "provider_activity",
                "llm_activity",
                "media_activity",
                "stable_replay_activity",
                "user_data_cleanup_delete_activity",
            )
        )
    )
    if not operation_valid:
        result.fail(
            "fl1_i1_forbidden_activity",
            "FL1-I1 permits bounded synthetic reads only; every real, DB, storage, external, replay, or cleanup count must remain zero.",
            path="operation_counts",
        )

    validation = _get(summary, "validation", {})
    validation_fields = (
        "focused_tests_passed",
        "full_non_e2e_passed",
        "synthetic_source_containment_passed",
        "real_source_rejection_passed",
        "read_only_snapshot_passed",
        "symlink_rejection_passed",
        "finite_budget_passed",
        "denominator_equations_passed",
        "duplicate_accounting_passed",
        "cloud_and_unreadable_terminal_state_passed",
        "import_deferred_boundary_passed",
        "public_redaction_passed",
    )
    if not isinstance(validation, Mapping) or any(
        validation.get(key) is not True for key in validation_fields
    ):
        result.fail(
            "fl1_i1_validation_incomplete",
            "FL1-I1 requires focused and full validation of every synthetic read-only inventory safety behavior.",
            path="validation",
        )

    redaction = _get(summary, "public_redaction", {})
    if not isinstance(redaction, Mapping) or not (
        redaction.get("passed") is True
        and redaction.get("private_paths_emitted") is False
        and redaction.get("content_fingerprints_emitted") is False
        and redaction.get("per_item_private_records_emitted") is False
    ):
        result.fail(
            "fl1_i1_public_redaction_invalid",
            "FL1-I1 public evidence must contain aggregates only, with no paths, per-item records, or content fingerprints.",
            path="public_redaction",
        )


CUSTOM_CHECKS = {
    "python_env": _check_python_env,
    "postgres_db": _check_postgres_db,
    "media_import": _check_media_import,
    "classification": _check_classification,
    "ai_tagging": _check_ai_tagging,
    "localization": _check_localization,
    "source_metadata": _check_source_metadata,
    "source_concept_full_chain": _check_source_concept_full_chain,
    "r1r_full_source_concept_pipeline": _check_r1r_full_source_concept_pipeline,
    "r2_source_concept_graph_remediation": _check_r2_source_concept_graph_remediation,
    "r2r_autonomous_recall_search_closure": _check_r2r_autonomous_recall_search_closure,
    "ml1_multilingual_alias_source_metadata_closure": _check_ml1_multilingual_alias_source_metadata_closure,
    "ml2_multilingual_identity_candidate_closure": _check_ml2_multilingual_identity_candidate_closure,
    "sv1_controlled_scale_promotion_readiness": _check_sv1_controlled_scale_promotion_readiness,
    "sv1b_controlled_pixiv_metadata_localization_source_graph_closure": _check_sv1b_controlled_pixiv_metadata_localization_source_graph_closure,
    "sv1b_owner_acceptance_closeout": _check_sv1b_owner_acceptance_closeout,
    "scv2_fl1_p1_foundation": _check_scv2_fl1_p1_foundation,
    "scv2_fl1_i1_inventory": _check_scv2_fl1_i1_inventory,
    "review_pack": _check_review_pack,
    "route_audit": _check_route_audit,
    "public_redaction": _check_public_redaction,
    "mutation_safety": _check_mutation_safety,
    "artifact_lifecycle": _check_artifact_lifecycle,
    "destructive_operation": _check_destructive_operation,
    "entity_truth_bridge": _check_entity_truth_bridge,
    "production_development_separation": _check_production_development_separation,
    "prod_launcher_mvp": _check_prod_launcher_mvp,
    "prod_launcher_ux1_production_profile": _check_prod_launcher_ux1_production_profile,
    "dynamic_library_sync": _check_dynamic_library_sync,
    "s2g1x_probe": _check_s2g1x_probe,
    "s2g_s3a_f1_foundation": _check_s2g_s3a_f1_foundation,
    "s2g_real1_bounded_ai_tagging_validation": _check_s2g_real1_bounded_ai_tagging_validation,
    "s2g_manual_sync_foundation": _check_s2g_manual_sync_foundation,
    "s3a_m1_manual_sync_execute": _check_s3a_m1_manual_sync_execute,
    "s3a_m2_production_delta_e2e": _check_s3a_m2_production_delta_e2e,
    "s3a_m2_r_lifecycle_workitem": _check_s3a_m2_r_lifecycle_workitem,
    "s3a_m2_r_operator_validation": _check_s3a_m2_r_operator_validation,
    "s3a_pilot1_new_data_directml_chain": _check_s3a_pilot1_new_data_directml_chain,
    "s3a_prod1_operator_incremental_sync": _check_s3a_prod1_operator_incremental_sync,
    "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync": _check_s3a_prod2_s3b_d1_operator_scaleup_disabled_sync,
    "phase47_s2_baseline": _check_phase47_s2_baseline,
}
