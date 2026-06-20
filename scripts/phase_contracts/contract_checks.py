"""Executable checks for V.I.O.L.E.T. phase contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contract_registry import (
    SOURCE_CONCEPT_ALLOWED_STATUSES,
    SOURCE_CONCEPT_FULL_CHAIN_STAGES,
    get_contract,
)
from .contract_types import ContractCheckResult, PhaseContract

MISSING = object()

WINDOWS_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>|]+")
UNC_PATH_RE = re.compile(r"\\\\[^\\\s\"'<>|]+\\[^\\\s\"'<>|]+")
FILE_URI_RE = re.compile(r"(?i)\bfile://[^\s\"'<>]+")
POSIX_PRIVATE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(home|Users|mnt|Volumes|tmp|workspace|opt|var)(/[^\s\"'<>]*)?",
)
TOKEN_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{4,}|ghp_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|xoxb-[A-Za-z0-9-]{4,}|Authorization\s*:|Bearer\s+[A-Za-z0-9._-]{4,})"
)
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
    "dynamic_library_sync": _check_dynamic_library_sync,
    "phase47_s2_baseline": _check_phase47_s2_baseline,
}
