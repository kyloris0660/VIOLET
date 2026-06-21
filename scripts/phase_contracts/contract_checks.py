"""Executable checks for V.I.O.L.E.T. phase contracts."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contract_registry import (
    SOURCE_CONCEPT_ALLOWED_STATUSES,
    SOURCE_CONCEPT_FULL_CHAIN_STAGES,
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
    r"(?i)(raw_filename|filename|file_name|source_url|source_urls|original_url|thumbnail_url|source_path|local_path|source_root|original_path|provider_url|private_url|raw_label|private_label|provider_credential)"
)
FILENAME_VALUE_RE = re.compile(r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,120}\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)\b")

POSITIVE_STAGE_STATUSES = {"passed", "pass", "complete", "completed", "executed", "success", "succeeded"}
NEGATIVE_STAGE_STATUSES = {"blocked", "blocked_before_write", "inconclusive", "skipped", "missing", "failed", "fail", "not_run"}
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


def _check_forbidden_stages(contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    executed = _executed_stage_names(summary)
    forbidden_present = sorted(stage for stage in contract.forbidden_stages if _forbidden_stage_present(summary, executed, stage))
    result.details["executed_stages"] = sorted(executed)
    result.details["forbidden_stages_present"] = forbidden_present
    for stage in forbidden_present:
        result.fail("forbidden_stage_executed", f"Forbidden stage {stage!r} is present/executed.", path=stage)


def _forbidden_stage_present(summary: Mapping[str, Any], executed: set[str], stage: str) -> bool:
    if stage in executed or _as_bool(_get(summary, stage, False)):
        return True
    for path in ("stages", "pipeline_contract.stages"):
        value = _get(summary, path)
        if isinstance(value, Mapping) and stage in value:
            stage_value = value[stage]
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
        if secret_context and not _safe_redacted(value):
            finding = {"path": display_path, "kind": kind}
            finding.update(_redacted_match_payload("secret_key_name_with_unredacted_value", key_name))
            findings.append(finding)
        if provenance_context and not _safe_redacted(value):
            finding = {"path": display_path, "kind": kind}
            finding.update(_redacted_match_payload("private_provenance_value_unredacted", key_name))
            findings.append(finding)
        if text is not None and kind == "key" and (SECRET_KEY_NAME_RE.search(text) or PRIVATE_PROVENANCE_KEY_RE.search(text)):
            # Key names are not automatically failures; values decide whether a
            # public field is unsafe. Path-like key text is still caught above.
            continue
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
    if _has(summary, "public_json_payload"):
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
        message="Post-S2 production/development separation requires explicit lanes, promotion gates, redacted artifacts, and focused tests.",
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

    current_phase = str(_get(summary, "phase_boundaries.current_phase", "")).casefold()
    if current_phase not in {"pd1-a", "pd1_a"}:
        result.fail(
            "production_development_current_phase_mismatch",
            "This governance summary must identify PD1-A as the current phase.",
            path="phase_boundaries.current_phase",
            expected="PD1-A",
            actual=_get(summary, "phase_boundaries.current_phase", None),
        )
    next_phase = str(_get(summary, "phase_boundaries.next_recommended_phase", "")).casefold()
    if "s2g-1" not in next_phase and "s2g_1" not in next_phase:
        result.fail(
            "production_development_next_phase_not_s2g1",
            "The immediate recommended next phase after PD1-A must remain S2G-1.",
            path="phase_boundaries.next_recommended_phase",
            expected="S2G-1",
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

    requested_writes = _production_write_requested(summary)
    result.details["production_write_requests"] = requested_writes
    if requested_writes:
        if not _as_bool(_get(summary, "production_promotion.enabled", False)):
            result.fail(
                "production_write_without_promotion_mode",
                "Production writes require explicit production/promotion mode.",
                path="production_promotion.enabled",
                expected=True,
                actual=_get(summary, "production_promotion.enabled", None),
            )
        if not _as_bool(_get(summary, "production_promotion.operator_confirmation_present", False)):
            result.fail(
                "production_write_without_operator_confirmation",
                "Production writes require explicit operator confirmation.",
                path="production_promotion.operator_confirmation_present",
                expected=True,
                actual=_get(summary, "production_promotion.operator_confirmation_present", None),
            )

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
            "model_cache.local_files_only",
            "preflight.provider_availability.reported",
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

    max_items = _as_int(_get(summary, "run_configuration.max_items", 0))
    selected_count = _as_int(_get(summary, "scope.selected_count", 0))
    over_cap = _as_int(_get(summary, "scope.over_cap_count", 0))
    if not (1 <= max_items <= 5):
        result.fail(
            "s3a_prod1_max_items_unbounded",
            "S3A-PROD1 max_items must stay between 1 and 5.",
            path="run_configuration.max_items",
            expected="1..5",
            actual=max_items,
        )
    if not over_cap and selected_count <= 0:
        if status != "blocked_scope_invalid":
            result.fail(
                "s3a_prod1_selected_sample_not_small",
                "S3A-PROD1 selected input count must be non-zero unless the report is fail-closed on invalid scope.",
                path="scope.selected_count",
                expected=f"1..{max_items} or blocked_scope_invalid",
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
    if status in target_statuses and not _as_bool(_get(summary, "safety.selected_input_explicit_bounded", False)):
        result.fail(
            "s3a_prod1_target_without_selected_input_proof",
            "S3A-PROD1 target_met requires selected explicit bounded input proof.",
            path="safety.selected_input_explicit_bounded",
            expected=True,
            actual=_get(summary, "safety.selected_input_explicit_bounded", None),
        )

    directml_available = _as_bool(_get(summary, "preflight.directml_available", False))
    cpu_available = _as_bool(_get(summary, "preflight.cpu_fallback_available", False))
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
    if not db_available and status != "blocked_db_unavailable":
        result.fail(
            "s3a_prod1_db_unavailable_not_blocked",
            "A DB-unavailable S3A-PROD1 report must fail closed with blocked_db_unavailable.",
            path="pipeline_contract.status",
            expected="blocked_db_unavailable",
            actual=result.status,
        )

    production_write_requested = _as_bool(_get(summary, "run_configuration.production_write_requested", False))
    exact_confirmation = _as_bool(_get(summary, "run_configuration.exact_production_sync_confirmation", False))
    import_executed = _as_bool(_get(summary, "import_reuse.executed", False))
    ai_executed = _as_bool(_get(summary, "directml_ai_tagging.executed", False))
    ai_dry_run = _as_bool(_get(summary, "directml_ai_tagging.dry_run", True))
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
    probe_actual = _get(summary, "provider_write_gate.probe_actual_provider", None)
    probe_executed = _as_bool(_get(summary, "provider_write_gate.probe_executed", False))
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
        if not gate_passed or not gate_write_allowed or not gate_prefers_directml or not probe_executed or probe_actual != "DmlExecutionProvider":
            result.fail(
                "s3a_prod1_target_without_directml_write_gate",
                "S3A-PROD1 target_met requires a passing pre-write DirectML provider gate before any media_tags write.",
                path="provider_write_gate",
                expected={
                    "passed": True,
                    "write_allowed": True,
                    "provider_preference_includes_directml": True,
                    "probe_executed": True,
                    "probe_actual_provider": "DmlExecutionProvider",
                },
                actual={
                    "passed": gate_passed,
                    "write_allowed": gate_write_allowed,
                    "provider_preference_includes_directml": gate_prefers_directml,
                    "probe_executed": probe_executed,
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


CUSTOM_CHECKS = {
    "python_env": _check_python_env,
    "postgres_db": _check_postgres_db,
    "media_import": _check_media_import,
    "classification": _check_classification,
    "ai_tagging": _check_ai_tagging,
    "localization": _check_localization,
    "source_metadata": _check_source_metadata,
    "source_concept_full_chain": _check_source_concept_full_chain,
    "review_pack": _check_review_pack,
    "route_audit": _check_route_audit,
    "public_redaction": _check_public_redaction,
    "mutation_safety": _check_mutation_safety,
    "artifact_lifecycle": _check_artifact_lifecycle,
    "destructive_operation": _check_destructive_operation,
    "entity_truth_bridge": _check_entity_truth_bridge,
    "production_development_separation": _check_production_development_separation,
    "dynamic_library_sync": _check_dynamic_library_sync,
    "s2g1x_probe": _check_s2g1x_probe,
    "s2g_s3a_f1_foundation": _check_s2g_s3a_f1_foundation,
    "s2g_real1_bounded_ai_tagging_validation": _check_s2g_real1_bounded_ai_tagging_validation,
    "s3a_pilot1_new_data_directml_chain": _check_s3a_pilot1_new_data_directml_chain,
    "s3a_prod1_operator_incremental_sync": _check_s3a_prod1_operator_incremental_sync,
    "phase47_s2_baseline": _check_phase47_s2_baseline,
}
