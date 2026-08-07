"""Create and validate the immutable SCV2-SV1B owner closeout evidence.

This phase-scoped runner is deliberately offline and has no application or database
imports.  It combines a previously exported owner result, the v4-to-v5-r3 per-case
delta audit, and the owner's explicit final decisions.  Known limitations remain
machine-distinct from PASS.  A second proof binds the accepted implementation HEAD to
a later governance-only closeout HEAD after a fail-closed Git diff audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Direct script execution and package-style test imports use different roots.
    from scripts.run_phase45_scv2_sv1b_static_manual_acceptance import (
        EXPECTED_CASE_IDS,
        file_sha256,
        payload_fingerprint,
        validate_static_packet,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by direct CLI execution
    from run_phase45_scv2_sv1b_static_manual_acceptance import (
        EXPECTED_CASE_IDS,
        file_sha256,
        payload_fingerprint,
        validate_static_packet,
    )


ACCEPTED_IMPLEMENTATION_HEAD = "e7ada8e83593cbb639f0c1fd4442f76e47537e8d"
BINDING_FINGERPRINT = "4992ed754539ef1f14500825d0fd78fc448e26846780cd4c64bacc5c2c6c3f81"
MANIFEST_SHA256 = "b37eb60dc90418959a6b3a7be188dedc29eb29ebf8c85c5303dd8665bdfdad5c"
DELTA_AUDIT_SHA256 = "fe3455b9b9fd2cfcb13d242f01208a378ef69342896905044c789523aaaadbb1"
OLD_RESULT_SHA256 = "6ad0d4d78815de0984a4e563490be91e985e9f109facb462c8528896867ae2b9"
OWNER_DECISION_IDENTITY = "owner_final_sv1b_acceptance_decision_v1_20260807"
OWNER_WAIVER_IDENTITY = (
    "owner_accepted_sv1b_placeholder_creator_identity_limitations_v1_20260807"
)

EXPLICIT_PASS_CASE_IDS = tuple(
    [f"A{i:02d}" for i in range(1, 13)]
    + ["B03", "D05", "D06", "E05", "E06"]
)
INHERITED_PASS_CASE_IDS = (
    "B02", "B05", "B06", "B07",
    "C01", "C02", "C03", "C04", "C05", "C06",
    "D01", "D02", "D03", "D04", "D07", "D08",
    "E01", "E02", "E03", "E04",
)
WAIVED_CASE_IDS = ("B01", "B04", "B08")

COMPOSITE_NAME = "sv1b-final-composite-owner-acceptance-v1.json"
CARRY_FORWARD_NAME = "sv1b-behavior-neutral-closeout-carry-forward-v1.json"

ALLOWED_CLOSEOUT_PATHS = (
    "docs/",
    "scripts/check_documentation_state.py",
    "scripts/phase_contracts/",
    "scripts/run_phase45_scv2_sv1b_acceptance_closeout.py",
    "tests/test_current_handoff_freshness.py",
    "tests/test_phase45_scv2_sv1b_acceptance_closeout.py",
    "tests/test_phase_contracts.py",
)


class CloseoutError(RuntimeError):
    """Fail-closed owner acceptance closeout error."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CloseoutError(f"invalid_json:{path.name}") from exc


def _write_exclusive_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise CloseoutError(f"immutable_output_exists:{path.name}")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode(
        "utf-8"
    ) + b"\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise CloseoutError(f"immutable_output_exists:{path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _validate_sha(path: Path, expected: str, code: str) -> None:
    if not path.is_file() or file_sha256(path) != expected:
        raise CloseoutError(code)


def _validate_delta(delta: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    declared = str(delta.get("audit_payload_fingerprint") or "")
    body = dict(delta)
    body.pop("audit_payload_fingerprint", None)
    if declared != payload_fingerprint(body):
        raise CloseoutError("delta_audit_self_fingerprint_invalid")
    rows = delta.get("cases")
    if not isinstance(rows, list) or len(rows) != 40:
        raise CloseoutError("delta_audit_case_count_invalid")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise CloseoutError("delta_audit_case_shape_invalid")
        case_id = str(row.get("case_id") or "")
        if case_id in by_id:
            raise CloseoutError("delta_audit_duplicate_case")
        by_id[case_id] = row
    if tuple(sorted(by_id)) != tuple(sorted(EXPECTED_CASE_IDS)):
        raise CloseoutError("delta_audit_case_membership_invalid")
    return by_id


def _validate_old_result(result: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = result.get("per_case_result")
    if not isinstance(rows, list) or len(rows) != 40:
        raise CloseoutError("old_result_case_count_invalid")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise CloseoutError("old_result_case_shape_invalid")
        case_id = str(row.get("case_id") or "")
        if case_id in by_id:
            raise CloseoutError("old_result_duplicate_case")
        by_id[case_id] = row
    if tuple(sorted(by_id)) != tuple(sorted(EXPECTED_CASE_IDS)):
        raise CloseoutError("old_result_case_membership_invalid")
    return by_id


def compose(
    *, packet_root: Path, delta_path: Path, old_result_path: Path, output_root: Path
) -> dict[str, Any]:
    packet = validate_static_packet(
        packet_root, expected_git_head=ACCEPTED_IMPLEMENTATION_HEAD
    )
    if packet["binding_fingerprint"] != BINDING_FINGERPRINT:
        raise CloseoutError("binding_fingerprint_mismatch")
    _validate_sha(
        packet_root / "manual-acceptance/case-manifest-private.json",
        MANIFEST_SHA256,
        "case_manifest_sha_mismatch",
    )
    _validate_sha(delta_path, DELTA_AUDIT_SHA256, "delta_audit_sha_mismatch")
    _validate_sha(old_result_path, OLD_RESULT_SHA256, "old_result_sha_mismatch")
    delta = _read_json(delta_path)
    old_result = _read_json(old_result_path)
    if not isinstance(delta, Mapping) or not isinstance(old_result, Mapping):
        raise CloseoutError("source_payload_not_object")
    delta_by_id = _validate_delta(delta)
    old_by_id = _validate_old_result(old_result)

    if set(EXPLICIT_PASS_CASE_IDS) | set(INHERITED_PASS_CASE_IDS) | set(
        WAIVED_CASE_IDS
    ) != set(EXPECTED_CASE_IDS):
        raise CloseoutError("owner_decision_membership_invalid")
    if (
        set(EXPLICIT_PASS_CASE_IDS) & set(INHERITED_PASS_CASE_IDS)
        or set(EXPLICIT_PASS_CASE_IDS) & set(WAIVED_CASE_IDS)
        or set(INHERITED_PASS_CASE_IDS) & set(WAIVED_CASE_IDS)
    ):
        raise CloseoutError("owner_decision_membership_overlap")

    cases: list[dict[str, Any]] = []
    for case_id in EXPECTED_CASE_IDS:
        delta_row = delta_by_id[case_id]
        inherited = case_id in INHERITED_PASS_CASE_IDS
        if inherited:
            if str(old_by_id[case_id].get("decision") or "").casefold() != "pass":
                raise CloseoutError(f"inherited_case_not_old_pass:{case_id}")
            if delta_row.get("classification") != "carry_forward_eligible":
                raise CloseoutError(f"inherited_case_not_delta_eligible:{case_id}")
            final_disposition = "pass"
            source = "v4_owner_pass_strict_case_carry_forward"
            old_sha: str | None = OLD_RESULT_SHA256
            reason = "old_owner_pass_with_delta_audit_proving_case_evidence_semantics_and_media_eligible_for_carry_forward"
        elif case_id in WAIVED_CASE_IDS:
            final_disposition = "owner_waived_nonblocking_known_limitation"
            source = "owner_explicit_20260807_nonblocking_waiver"
            old_sha = OLD_RESULT_SHA256
            reason = "underlying_placeholder_or_default_creator_identity_case_remains_inconsistent_and_is_owner_accepted_only_for_scv2_sv1b"
        else:
            final_disposition = "pass"
            source = "owner_explicit_20260807_case_decision"
            old_sha = OLD_RESULT_SHA256
            reason = "owner_explicitly_reviewed_and_passed_the_v5_r3_case"
        cases.append(
            {
                "case_id": case_id,
                "final_disposition": final_disposition,
                "decision_source": source,
                "old_result_sha256": old_sha,
                "old_case_fingerprint": delta_row.get("old_case_fingerprint"),
                "new_case_fingerprint": delta_row.get("new_case_fingerprint"),
                "old_evidence_fingerprint": delta_row.get("old_evidence_fingerprint"),
                "new_evidence_fingerprint": delta_row.get("new_evidence_fingerprint"),
                "old_media_fingerprint": delta_row.get("old_media_fingerprint"),
                "new_media_fingerprint": delta_row.get("new_media_fingerprint"),
                "binding_fingerprint": BINDING_FINGERPRINT,
                "case_manifest_sha256": MANIFEST_SHA256,
                "owner_decision_identity": (
                    OWNER_WAIVER_IDENTITY
                    if case_id in WAIVED_CASE_IDS
                    else OWNER_DECISION_IDENTITY
                ),
                "classification_reason": reason,
                "underlying_case_mismatch_preserved": case_id in WAIVED_CASE_IDS,
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "sv1b_final_composite_owner_acceptance_v1",
        "accepted_implementation_head": ACCEPTED_IMPLEMENTATION_HEAD,
        "binding_fingerprint": BINDING_FINGERPRINT,
        "case_manifest_sha256": MANIFEST_SHA256,
        "delta_audit_sha256": DELTA_AUDIT_SHA256,
        "old_result_sha256": OLD_RESULT_SHA256,
        "owner_decision_identity": OWNER_DECISION_IDENTITY,
        "owner_waiver": {
            "identity": OWNER_WAIVER_IDENTITY,
            "case_ids": list(WAIVED_CASE_IDS),
            "scope": "SCV2-SV1B_only",
            "does_not_convert_underlying_mismatch_to_pass": True,
            "does_not_apply_to": [
                "real_creator_identity",
                "reliable_provider_account_id",
                "normal_search_result",
                "truth_path",
                "SCV2-FL1",
                "production",
                "Provider-2",
                "other_pull_request",
            ],
            "reopen_required_if_scope_boundary_crossed": True,
        },
        "summary": {
            "manual_acceptance_status": "accepted_with_known_nonblocking_limitations",
            "case_count": 40,
            "pass_count": 37,
            "owner_waived_nonblocking_known_limitation_count": 3,
            "pending_count": 0,
            "unwaived_fail_count": 0,
            "pass_case_ids": [
                row["case_id"] for row in cases if row["final_disposition"] == "pass"
            ],
            "owner_waived_case_ids": list(WAIVED_CASE_IDS),
        },
        "cases": cases,
        "operation_counts": {
            "database_access": 0,
            "database_write": 0,
            "provider_request": 0,
            "llm_request": 0,
            "media_download": 0,
            "production_access": 0,
        },
    }
    payload["composite_fingerprint"] = payload_fingerprint(payload)
    _write_exclusive_atomic(output_root / COMPOSITE_NAME, payload)
    return validate_composite(output_root / COMPOSITE_NAME)


def validate_composite(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise CloseoutError("composite_not_object")
    declared = str(payload.get("composite_fingerprint") or "")
    body = dict(payload)
    body.pop("composite_fingerprint", None)
    if declared != payload_fingerprint(body):
        raise CloseoutError("composite_self_fingerprint_invalid")
    summary = payload.get("summary")
    waiver = payload.get("owner_waiver")
    cases = payload.get("cases")
    if not isinstance(summary, Mapping) or not isinstance(waiver, Mapping) or not isinstance(cases, list):
        raise CloseoutError("composite_shape_invalid")
    dispositions = {str(row.get("case_id")): str(row.get("final_disposition")) for row in cases if isinstance(row, Mapping)}
    if tuple(sorted(dispositions)) != tuple(sorted(EXPECTED_CASE_IDS)):
        raise CloseoutError("composite_case_membership_invalid")
    if {case for case, value in dispositions.items() if value == "pass"} != set(
        EXPLICIT_PASS_CASE_IDS
    ) | set(INHERITED_PASS_CASE_IDS):
        raise CloseoutError("composite_pass_membership_invalid")
    if {case for case, value in dispositions.items() if value == "owner_waived_nonblocking_known_limitation"} != set(WAIVED_CASE_IDS):
        raise CloseoutError("composite_waiver_membership_invalid")
    if (
        summary.get("pass_count") != 37
        or summary.get("owner_waived_nonblocking_known_limitation_count") != 3
        or summary.get("pending_count") != 0
        or summary.get("unwaived_fail_count") != 0
        or waiver.get("identity") != OWNER_WAIVER_IDENTITY
        or waiver.get("does_not_convert_underlying_mismatch_to_pass") is not True
    ):
        raise CloseoutError("composite_summary_invalid")
    return {
        "passed": True,
        "path": str(path),
        "file_sha256": file_sha256(path),
        "composite_fingerprint": declared,
        "summary": dict(summary),
    }


def create_carry_forward(
    *, repo_root: Path, composite_path: Path, output_root: Path
) -> dict[str, Any]:
    composite = validate_composite(composite_path)
    head = _git(repo_root, "rev-parse", "HEAD").casefold()
    if head == ACCEPTED_IMPLEMENTATION_HEAD:
        raise CloseoutError("closeout_head_not_advanced")
    changed = tuple(
        row.replace("\\", "/")
        for row in _git(
            repo_root, "diff", "--name-only", f"{ACCEPTED_IMPLEMENTATION_HEAD}..{head}"
        ).splitlines()
        if row.strip()
    )
    if not changed:
        raise CloseoutError("closeout_diff_empty")
    disallowed = [
        path
        for path in changed
        if not any(
            path == prefix or path.startswith(prefix)
            for prefix in ALLOWED_CLOSEOUT_PATHS
        )
    ]
    if disallowed:
        raise CloseoutError(f"runtime_or_data_path_changed:{','.join(disallowed)}")
    diff = _git(
        repo_root,
        "diff",
        "--binary",
        "--no-ext-diff",
        f"{ACCEPTED_IMPLEMENTATION_HEAD}..{head}",
    )
    payload: dict[str, Any] = {
        "schema_version": "sv1b_behavior_neutral_acceptance_carry_forward_v1",
        "accepted_implementation_head": ACCEPTED_IMPLEMENTATION_HEAD,
        "closeout_head": head,
        "composite_file_sha256": composite["file_sha256"],
        "composite_fingerprint": composite["composite_fingerprint"],
        "changed_files": list(changed),
        "changed_files_fingerprint": payload_fingerprint(list(changed)),
        "git_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "runtime_data_search_graph_localization_semantics_changed": False,
        "database_or_external_route_entered": False,
        "passed": True,
    }
    payload["proof_fingerprint"] = payload_fingerprint(payload)
    _write_exclusive_atomic(output_root / CARRY_FORWARD_NAME, payload)
    return validate_carry_forward(
        output_root / CARRY_FORWARD_NAME,
        repo_root=repo_root,
        composite_path=composite_path,
    )


def validate_carry_forward(
    path: Path, *, repo_root: Path, composite_path: Path
) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise CloseoutError("carry_forward_not_object")
    declared = str(payload.get("proof_fingerprint") or "")
    body = dict(payload)
    body.pop("proof_fingerprint", None)
    if declared != payload_fingerprint(body):
        raise CloseoutError("carry_forward_self_fingerprint_invalid")
    head = _git(repo_root, "rev-parse", "HEAD").casefold()
    composite = validate_composite(composite_path)
    if (
        payload.get("passed") is not True
        or payload.get("accepted_implementation_head") != ACCEPTED_IMPLEMENTATION_HEAD
        or payload.get("closeout_head") != head
        or payload.get("composite_file_sha256") != composite["file_sha256"]
        or payload.get("composite_fingerprint") != composite["composite_fingerprint"]
        or payload.get("runtime_data_search_graph_localization_semantics_changed") is not False
        or payload.get("database_or_external_route_entered") is not False
    ):
        raise CloseoutError("carry_forward_binding_invalid")
    return {
        "passed": True,
        "path": str(path),
        "file_sha256": file_sha256(path),
        "proof_fingerprint": declared,
        "closeout_head": head,
        "changed_files": payload.get("changed_files"),
    }


def contract_summary(
    *, composite_path: Path, carry_forward_path: Path, repo_root: Path
) -> dict[str, Any]:
    composite = validate_composite(composite_path)
    carry = validate_carry_forward(
        carry_forward_path, repo_root=repo_root, composite_path=composite_path
    )
    return {
        "pipeline_contract": {
            "contract_id": "sv1b_owner_acceptance_closeout_contract_v1",
            "status": "sv1b_accepted_with_known_nonblocking_limitations",
            "target_met": False,
            "safe_to_merge": True,
            "route_approved": True,
            "manual_acceptance_required": True,
            "manual_acceptance_status": "accepted_with_known_nonblocking_limitations",
            "active_blockers": [],
        },
        "composite_acceptance": {
            **composite["summary"],
            "passed": True,
            "file_sha256": composite["file_sha256"],
            "composite_fingerprint": composite["composite_fingerprint"],
            "binding_fingerprint": BINDING_FINGERPRINT,
            "case_manifest_sha256": MANIFEST_SHA256,
            "delta_audit_sha256": DELTA_AUDIT_SHA256,
            "old_result_sha256": OLD_RESULT_SHA256,
            "owner_waiver_identity": OWNER_WAIVER_IDENTITY,
            "owner_waived_case_ids": list(WAIVED_CASE_IDS),
            "underlying_mismatch_preserved": True,
            "waiver_scope": "SCV2-SV1B_only",
        },
        "behavior_neutral_carry_forward": {
            "passed": True,
            "accepted_implementation_head": ACCEPTED_IMPLEMENTATION_HEAD,
            "closeout_head": carry["closeout_head"],
            "file_sha256": carry["file_sha256"],
            "proof_fingerprint": carry["proof_fingerprint"],
            "runtime_data_search_graph_localization_semantics_changed": False,
            "changed_files": carry["changed_files"],
        },
        "operation_counts": {
            "database_access": 0,
            "database_write": 0,
            "provider_request": 0,
            "llm_request": 0,
            "media_download": 0,
            "production_access": 0,
            "entity_truth_write": 0,
            "provider_derived_media_tags_write": 0,
        },
        "route_decision": {
            "route_approved": True,
            "route_scope": "SCV2-FL1_planning_only_no_execution",
            "fl1_data_execution_authorized": False,
            "production_authorized": False,
            "next_phase_started": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    compose_parser = sub.add_parser("compose")
    compose_parser.add_argument("--packet-root", type=Path, required=True)
    compose_parser.add_argument("--delta-audit", type=Path, required=True)
    compose_parser.add_argument("--old-result", type=Path, required=True)
    compose_parser.add_argument("--output", type=Path, required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--composite", type=Path, required=True)
    carry_parser = sub.add_parser("carry-forward")
    carry_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    carry_parser.add_argument("--composite", type=Path, required=True)
    carry_parser.add_argument("--output", type=Path, required=True)
    summary_parser = sub.add_parser("contract-summary")
    summary_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    summary_parser.add_argument("--composite", type=Path, required=True)
    summary_parser.add_argument("--carry-forward", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "compose":
        result = compose(
            packet_root=args.packet_root,
            delta_path=args.delta_audit,
            old_result_path=args.old_result,
            output_root=args.output,
        )
    elif args.command == "validate":
        result = validate_composite(args.composite)
    elif args.command == "carry-forward":
        result = create_carry_forward(
            repo_root=args.repo_root,
            composite_path=args.composite,
            output_root=args.output,
        )
    else:
        result = contract_summary(
            composite_path=args.composite,
            carry_forward_path=args.carry_forward,
            repo_root=args.repo_root,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
