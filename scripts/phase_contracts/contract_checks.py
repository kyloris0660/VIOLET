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
POSIX_GENERIC_LOCAL_PATH_RE = re.compile(
    r"(?<![:/A-Za-z0-9_.-])/(?!/)(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+(?:\.[A-Za-z0-9]{1,8})?"
)
TOKEN_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{4,}|ghp_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|xoxb-[A-Za-z0-9-]{4,}|Authorization\s*:|Bearer\s+[A-Za-z0-9._-]{4,})"
)
SECRET_KEY_NAME_RE = re.compile(r"(?i)(api[_-]?key|token|password|secret|cookie|authorization|bearer)")
PRIVATE_PROVENANCE_KEY_RE = re.compile(
    r"(?i)(raw_filename|filename|file_name|source_url|original_url|thumbnail_url|source_path|local_path|source_root|original_path|provider_url|private_url|raw_label|private_label|provider_credential)"
)
FILENAME_VALUE_RE = re.compile(r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,120}\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)\b")

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
        "pipeline_contract.status",
        "pipeline_contract.contract_status",
        "contract_status",
        "full_chain_status",
        "final_route_decision_status",
        "route_decision.status",
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
    status = _contract_status(summary)
    if status and status.casefold() in {"route_approved", "approved", "approved_to_proceed"}:
        return True
    return _claim(summary, "route_approved") or _claim(summary, "approved")


def _declared_contract_id(summary: Mapping[str, Any]) -> Any:
    for path in ("pipeline_contract.contract_id", "contract_id", "contract.contract_id"):
        value = _get(summary, path, MISSING)
        if value is not MISSING and value is not None:
            return value
    return MISSING


def _check_claimed_contract_id(contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    if not (result.target_met_claimed or result.route_approved or result.full_chain_complete_claimed or result.safe_to_merge_claimed):
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
                if _as_bool(stage_value):
                    names.add(str(stage))
                elif isinstance(stage_value, Mapping) and (
                    _as_bool(stage_value.get("executed"))
                    or str(stage_value.get("status", "")).casefold() in {"passed", "complete", "completed", "blocked_before_write"}
                ):
                    names.add(str(stage))
    return names


def _check_forbidden_stages(contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    executed = _executed_stage_names(summary)
    forbidden_present = sorted(stage for stage in contract.forbidden_stages if stage in executed or _as_bool(_get(summary, stage, False)))
    result.details["executed_stages"] = sorted(executed)
    result.details["forbidden_stages_present"] = forbidden_present
    for stage in forbidden_present:
        result.fail("forbidden_stage_executed", f"Forbidden stage {stage!r} is present/executed.", path=stage)


def _missing_required_stages(contract: PhaseContract, summary: Mapping[str, Any]) -> list[str]:
    executed = _executed_stage_names(summary)
    explicit_missing = _get(summary, "missing_required_stages", [])
    if explicit_missing is MISSING:
        explicit_missing = _get(summary, "pipeline_contract.missing_required_stages", [])
    missing = set()
    if isinstance(explicit_missing, list):
        missing.update(str(stage) for stage in explicit_missing)
    missing.update(stage for stage in contract.required_stages if stage not in executed)
    return sorted(missing)


def _safe_redacted(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if text.casefold() in REDACTED_VALUES:
        return True
    return text.startswith("redacted_") or text.startswith("[redacted") or text.endswith("_redacted")


def _iter_json_strings(payload: Any, path: str = "$") -> Iterable[tuple[str, str, str]]:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            child = f"{path}.{key_text}"
            yield child, "key", key_text
            yield from _iter_json_strings(value, child)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from _iter_json_strings(item, f"{path}[{index}]")
    elif isinstance(payload, str):
        yield path, "value", payload


def _path_has_private_provenance_context(path: str) -> bool:
    segments = [segment for segment in re.split(r"[.\[\]]+", path) if segment and segment != "$" and not segment.isdigit()]
    return any(PRIVATE_PROVENANCE_KEY_RE.search(segment) for segment in segments)


def _redaction_findings_for_text(text: str, path: str, *, kind: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    checks = (
        ("windows_local_path", WINDOWS_PATH_RE),
        ("unc_local_path", UNC_PATH_RE),
        ("file_uri", FILE_URI_RE),
        ("posix_private_path", POSIX_PRIVATE_PATH_RE),
        ("posix_local_path", POSIX_GENERIC_LOCAL_PATH_RE),
        ("common_secret_or_token", TOKEN_RE),
    )
    for code, pattern in checks:
        match = pattern.search(text)
        if match:
            findings.append({"code": code, "path": path, "kind": kind, "match": match.group(0)[:160]})
    return findings


def scan_public_payload(payload: Any) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path, kind, text in _iter_json_strings(payload):
        findings.extend(_redaction_findings_for_text(text, path, kind=kind))
        key_name = path.rsplit(".", 1)[-1]
        if not _safe_redacted(text):
            match = FILENAME_VALUE_RE.search(text)
            if match:
                findings.append({"code": "bare_filename", "path": path, "kind": kind, "match": match.group(0)[:160]})
        if kind == "value" and SECRET_KEY_NAME_RE.search(key_name) and not _safe_redacted(text):
            findings.append({"code": "secret_key_name_with_unredacted_value", "path": path, "kind": kind, "match": key_name})
        if kind == "value" and _path_has_private_provenance_context(path):
            if not _safe_redacted(text):
                findings.append({"code": "private_provenance_value_unredacted", "path": path, "kind": kind, "match": path})
        if kind == "key" and (SECRET_KEY_NAME_RE.search(text) or PRIVATE_PROVENANCE_KEY_RE.search(text)):
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
    judgment_count = _as_int(_get(summary, "llm_judgment_count", 0))
    zero_eligible = _zero_eligible_proof_passed(plan_mapping)
    zero_eligible_reason_present = _zero_eligible_reason_present(plan_mapping)
    eligible_pair_count_recorded = "eligible_pair_count" in plan_mapping
    eligible = _as_int(plan_mapping.get("eligible_pair_count", plan_mapping.get("selected_block_count", 0)))
    max_calls = _as_int(plan_mapping.get("max_calls", _get(summary, "llm_max_calls", 0)))
    selected = _as_int(plan_mapping.get("selected_pair_count", plan_mapping.get("selected_block_count", 0)))
    budget = _as_float(plan_mapping.get("budget_usd", _get(summary, "llm_budget_usd", 0.0)))
    projected = _as_float(plan_mapping.get("projected_budget_usd", plan_mapping.get("projected_cost_usd", 0.0)))
    approved_overage = _as_bool(plan_mapping.get("explicit_over_budget_or_call_cap_approval"))
    blocked_status = status.startswith("full_chain_blocked")
    eligible_pairs_exist = eligible > 0
    valid_zero_eligible_proof = zero_eligible and eligible_pair_count_recorded and eligible == 0 and zero_eligible_reason_present
    llm_evidence_required = full_chain_claimed and not valid_zero_eligible_proof
    if full_chain_claimed and zero_eligible and not valid_zero_eligible_proof:
        result.fail(
            "source_concept_zero_eligible_proof_incomplete",
            "Zero-eligible LLM proof requires zero_eligible_proof=true, explicit eligible_pair_count=0, and a reason.",
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

    if status == "deterministic_only" and (result.target_met_claimed or result.route_approved or result.full_chain_complete_claimed):
        result.fail("deterministic_only_claimed_completion", "deterministic_only summaries must not claim target_met, route_approved, or full_chain_complete.", path="pipeline_contract.status")

    if status.startswith("full_chain_blocked") or status == "full_chain_inconclusive_missing_artifacts":
        if result.target_met_claimed or result.route_approved or result.full_chain_complete_claimed:
            result.fail("blocked_status_claimed_completion", "Blocked/inconclusive full-chain summaries must not claim completion or approval.", path="pipeline_contract.status")

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


def _check_review_pack(_contract: PhaseContract, summary: Mapping[str, Any], result: ContractCheckResult) -> None:
    pack = _get(summary, "review_pack", MISSING)
    if pack is MISSING:
        pack = _get(summary, "chatgpt_review_pack", MISSING)
    if not isinstance(pack, Mapping):
        result.fail("review_pack_missing", "review_pack/chatgpt_review_pack object is required.", path="review_pack")
        return
    required_true = {
        "manifest_present": "manifest.json must exist.",
        "checksums_present": "checksums.json must exist.",
        "redaction_passed": "Review pack redaction scan must pass.",
        "redaction_scan_covers_final_file_set": "Final redaction scan must cover the final file set.",
        "zip_generated": "Review pack zip must be generated.",
        "not_committed": "Review pack zip/directory must not be committed.",
    }
    for key, message in required_true.items():
        if not _as_bool(pack.get(key)):
            result.fail("review_pack_required_flag_missing", message, path=f"review_pack.{key}", expected=True, actual=pack.get(key))
    checksum_count = _as_int(pack.get("checksum_count", 0), default=-1)
    manifest = pack.get("manifest") if isinstance(pack.get("manifest"), Mapping) else {}
    manifest_checksum_count = _as_int(manifest.get("checksum_count", pack.get("manifest_checksum_count", checksum_count)), default=checksum_count)
    if checksum_count < 1:
        result.fail("review_pack_checksum_count_missing", "Review pack checksum count must be positive.", path="review_pack.checksum_count")
    if manifest_checksum_count != checksum_count:
        result.fail("review_pack_checksum_count_mismatch", "Manifest checksum_count must match checksums.json count.", path="review_pack.checksum_count", expected=manifest_checksum_count, actual=checksum_count)
    public_copy_ok = any(
        _as_bool(pack.get(key))
        for key in (
            "public_report_copy_present",
            "public_report_copy_fresh",
            "public_report_copy_rendered_from_current_summary",
            "public_report_copy_current",
            "public_report_copy_generated",
        )
    )
    if not public_copy_ok:
        result.fail("review_pack_public_report_copy_missing", "Review pack requires public-report-copy proof.", path="review_pack.public_report_copy")
    if _contains_private_review_pack_label(summary):
        result.fail("review_pack_private_label_leak", "Review pack contains reversible fixed-salt hashes or raw/private labels.", path="review_pack")


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
    upstream_ok = _as_bool(upstream.get("passed")) or _as_bool(upstream.get("full_chain_fidelity_passed"))
    status = result.status or "unknown"
    result.details["route_decision_status"] = status
    result.details["route_approved"] = result.route_approved
    status_folded = status.casefold()
    if result.route_approved and not upstream_ok:
        result.fail("route_approval_upstream_incomplete", "Route approval cannot proceed while upstream pipeline contract is failed or incomplete.", path="upstream_pipeline_contract")
    if result.route_approved:
        _check_route_approved_source_concept_upstream(upstream, result)
    if result.route_approved and ("blocked" in status_folded or "provisional" in status_folded):
        result.fail("route_approval_blocked_or_provisional_status", "Route approval cannot be claimed while final_route_decision_status is blocked/provisional.", path="final_route_decision_status", actual=status)
    if "blocked" in status_folded or "provisional" in status_folded:
        result.details["route_blocked_not_approved"] = True
    mutation = _get(summary, "mutation_proof", {})
    if not isinstance(mutation, Mapping):
        result.fail("route_audit_mutation_proof_not_object", "Route audit mutation_proof must be an object.", path="mutation_proof")
    else:
        mutation_passed = _as_bool(mutation.get("passed"))
        if _has(summary, "mutation_proof.passed") and not mutation_passed:
            result.fail("route_audit_mutation_proof_failed", "Route audit fails when mutation_proof.passed=false.", path="mutation_proof.passed", expected=True, actual=mutation.get("passed"))
        if result.route_approved and not mutation_passed:
            result.fail("route_audit_route_approval_without_mutation_proof", "Route approval requires mutation_proof.passed=true.", path="mutation_proof.passed", expected=True, actual=mutation.get("passed"))
        forbidden_names, unexpected_names = _mutation_table_violations(mutation)
        if forbidden_names:
            result.fail("route_audit_mutation_forbidden_table_changed", "Route audit detected forbidden table changes.", path="mutation_proof.forbidden_changed_tables", actual=forbidden_names)
        if unexpected_names:
            result.fail("route_audit_mutation_unexpected_table_changed", "Route audit detected unexpected table changes.", path="mutation_proof.unexpected_changed_tables", actual=unexpected_names)
    review_pack = _get(summary, "chatgpt_review_pack", _get(summary, "review_pack", {}))
    waiver = _get(summary, "route_audit_review_pack_waiver", _get(summary, "review_pack_waiver", {}))
    waiver_ok = isinstance(waiver, Mapping) and _as_bool(waiver.get("contract_approved")) and _as_bool(waiver.get("explicit"))
    review_pack_present = isinstance(review_pack, Mapping) and bool(review_pack) and (
        _as_bool(review_pack.get("generated")) or _as_bool(review_pack.get("manifest_present"))
    )
    if isinstance(review_pack, Mapping) and review_pack and not review_pack_present:
        result.fail("route_audit_review_pack_missing", "Route-decision phases require a review pack unless explicitly waived.", path="chatgpt_review_pack")
    if result.route_approved and not review_pack_present and not waiver_ok:
        result.fail("route_audit_route_approval_missing_review_pack", "Route-approved summaries require review pack proof unless a contract-approved waiver is present.", path="chatgpt_review_pack")


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
    if not _as_bool(upstream.get("full_chain_fidelity_passed")):
        result.fail("route_approval_upstream_fidelity_not_passed", "Route approval requires upstream full_chain_fidelity_passed=true.", path="upstream_pipeline_contract.full_chain_fidelity_passed")
    if isinstance(missing, list) and missing:
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
    if isinstance(rows, list):
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
        if ("private" in classification or "one-off local" in classification) and committed:
            result.fail("private_artifact_committed", "Private/local artifacts must not be committed.", path=path)
        if "review pack" in normalized_classification and committed:
            result.fail("review_pack_committed", "Review packs must not be committed.", path=path)
        if "public report" in classification or "handoff" in classification:
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
}
