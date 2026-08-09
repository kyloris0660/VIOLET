"""Fail-closed current-phase state checker and generated handoff renderer."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "docs" / "state" / "current-phase.json"
HANDOFF_PATH = ROOT / "docs" / "current-handoff.md"
ACTIVE_ROUTE_PATHS = (
    ROOT / "docs" / "roadmap" / "current-mainline-roadmap.md",
    ROOT / "docs" / "project-roadmap.md",
    ROOT / "docs" / "phase-contracts.md",
)
SCHEMA_VERSION = "violet.current-phase.v2"
STATUS_FIELDS = (
    "target_met",
    "safe_to_merge",
    "route_approved",
    "manual_acceptance_status",
    "next_phase_started",
)
REQUIRED_FIELDS = {
    "schema_version",
    "phase_id",
    "phase_title",
    "repository",
    "branch",
    "pr_number",
    "draft",
    "accepted_mainline_base",
    "implementation_evidence_head",
    "current_status",
    "route_scope",
    "planning_approved",
    "approved_planning_head",
    *STATUS_FIELDS,
    "prior_phase_acceptance",
    "planning_boundary",
    "completed_checkpoints",
    "active_blocker",
    "owner_decisions",
    "authorized_operations",
    "forbidden_operations",
    "protected_evidence",
    "public_state_boundary",
    "next_required_checkpoint",
    "durable_links",
    "deferred_debt",
    "upstream_pr_state",
    "next_phase_authorization",
    "updated_at",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_FORBIDDEN = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"(?i)\b(?:authorization|cookie|set-cookie)\s*[:=]"),
    re.compile(r"(?i)\b(?:api[_-]?key|refresh[_-]?token|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\.local_manifests"),
)
FL1_STATUS = "fl1_p1_r1_implementation_ready_for_owner_audit"
FL1_BLOCKER = "pending_final_owner_audit"
FL1_MANUAL_STATUS = "pending_final_owner_audit"
FL1_APPROVED_PLANNING_HEAD = "db90457d51a39b5dc930afc2a92a6ef3139a2760"
FL1_ROUTE_SCOPE = "SCV2-FL1-P1-R1 late-review remediation only; FL1-I1 remains an unapproved future candidate"
FL1_COMPLETED_SCOPE = "SCV2-FL1-P1-R1 late-review safety remediation and regression tests only"
FL1_PLAN_MERGE_COMMIT = "9ce1128be643c0eaa998ccdff8890d76196ce7db"
FL1_ACCEPTED_MAIN = "36100bfa0317387e064cd87b2e753eca3a201b5e"
FL1_PR141_HEAD = "495a6506b25bb27747ebc27e341a06de4860aaa4"
FL1_I1_BRANCH = "codex/scv2-fl1-i1-read-only-inventory-v2"
FL1_I1_ACCEPTED_MAIN = "a2f48bdba979f579b7cd1cdd9ef541137b2479c5"
FL1_I1_PR143_HEAD = "228983f510c975399b53b39dcd7dd170e59b3245"
FL1_I1_P1_R1_EVIDENCE = "a631160f58e8d5d61998863b5b4d60a549e88151"
FL1_I1_ROUTE_SCOPE = (
    "SCV2-FL1-I1 reusable read-only inventory safety tooling using only "
    "synthetic and newly created temporary fixtures"
)
SV1B_MERGE_COMMIT = "33af4111e1595dac3ece0ac50002556d466f0138"
SV1B_WAIVER = "owner_accepted_sv1b_placeholder_creator_identity_limitations_v1_20260807"
NON_ACTION_ATTESTATION_SCHEMA = (
    "violet.scv2-fl1-p1-r1-phase-non-action-attestation.v1"
)
ATTESTED_NON_ACTIONS = (
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


class DocumentationStateError(ValueError):
    """Raised when current public documentation state is inconsistent."""


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentationStateError(f"current_phase_unreadable:{exc}") from exc
    if not isinstance(payload, dict):
        raise DocumentationStateError("current_phase_root_must_be_object")
    return payload


def _require_list(
    state: dict[str, Any], key: str, *, allow_empty: bool = False
) -> list[Any]:
    value = state.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        suffix = "list" if allow_empty else "nonempty_list"
        raise DocumentationStateError(f"{key}_must_be_{suffix}")
    return value


def _validate_fl1_state(state: dict[str, Any]) -> None:
    """Validate the active I1 implementation or frozen owner-audit projection."""

    audit_ready = (
        state["current_status"]
        == "fl1_i1_synthetic_implementation_ready_for_owner_audit"
    )
    expected_status = (
        "fl1_i1_synthetic_implementation_ready_for_owner_audit"
        if audit_ready
        else "fl1_i1_read_only_inventory_implementation_in_progress"
    )
    expected_manual = (
        "pending_i1_synthetic_implementation_owner_audit"
        if audit_ready
        else "pending_i1_implementation_owner_audit"
    )
    expected_blocker = (
        "pending_i1_synthetic_implementation_owner_audit_and_real_source_scope"
        if audit_ready
        else "fl1_i1_implementation_in_progress"
    )
    if (
        state["phase_id"] != "SCV2-FL1-I1"
        or state["branch"] != FL1_I1_BRANCH
        or state["draft"] is not True
        or state["current_status"] != expected_status
        or state["target_met"] is not False
        or state["safe_to_merge"] is not False
        or state["route_approved"] is not False
        or state["route_scope"] != FL1_I1_ROUTE_SCOPE
        or state["planning_approved"] is not True
        or state["approved_planning_head"] != FL1_APPROVED_PLANNING_HEAD
        or state["manual_acceptance_status"] != expected_manual
        or state["next_phase_started"] is not True
        or state["accepted_mainline_base"] != FL1_I1_ACCEPTED_MAIN
    ):
        raise DocumentationStateError("fl1_i1_status_fields_conflict")
    if state["active_blocker"].get("code") != expected_blocker:
        raise DocumentationStateError("fl1_i1_blocker_conflict")

    boundary = state["planning_boundary"]
    expected_boundary = {
        "planning_only": False,
        "implementation_authorized": True,
        "implementation_completed": audit_ready,
        "owner_audit_pending": True,
        "owner_acceptance_valid": False,
        "merge_authorized": False,
        "fl1_i1_route_authorized": True,
        "fl1_i1_implementation_started": True,
        "synthetic_ephemeral_test_fixture_authorized": True,
        "authorized_read_only_source_code_path_implementation_authorized": True,
        "real_inventory_started": False,
        "real_source_inventory_authorized": False,
        "source_root_access_authorized": False,
        "data_execution_authorized": False,
        "database_access_authorized": False,
        "database_data_execution_authorized": False,
        "app_storage_write_authorized": False,
        "import_authorized": False,
        "classification_or_tagging_execution_authorized": False,
        "provider_or_llm_authorized": False,
        "provider_authorized": False,
        "llm_authorized": False,
        "media_or_thumbnail_download_authorized": False,
        "media_authorized": False,
        "stable_replay_authorized": False,
        "production_authorized": False,
        "projected_external_cost_usd": 0,
    }
    if not isinstance(boundary, dict) or any(
        boundary.get(key) != value for key, value in expected_boundary.items()
    ):
        raise DocumentationStateError("fl1_i1_boundary_invalid")

    upstream = state["upstream_pr_state"]
    expected_upstream = {
        "pr_number": 143,
        "state": "merged",
        "draft": False,
        "merge_commit": FL1_I1_ACCEPTED_MAIN,
        "final_head": FL1_I1_PR143_HEAD,
        "implementation_evidence_head": FL1_I1_P1_R1_EVIDENCE,
        "final_review_id": 4890771735,
        "final_review_submitted_at": "2026-08-09T07:24:25Z",
        "owner_closeout_active_unresolved_thread_count": 10,
        "owner_closeout_outdated_unresolved_thread_count": 7,
        "owner_accepted": True,
        "merge_topology": "merge_commit",
    }
    if not isinstance(upstream, dict) or any(
        upstream.get(key) != value for key, value in expected_upstream.items()
    ):
        raise DocumentationStateError("fl1_i1_upstream_state_invalid")

    next_phase = state["next_phase_authorization"]
    if not isinstance(next_phase, dict) or any(
        (
            next_phase.get("phase_id") != "SCV2-FL1-I1",
            next_phase.get("implementation_route_authorized") is not True,
            next_phase.get("next_phase_started") is not True,
            next_phase.get("implementation_started") is not True,
            next_phase.get("synthetic_fixture_execution_authorized") is not True,
            next_phase.get("temporary_fixture_execution_authorized") is not True,
            next_phase.get("real_inventory_started") is not False,
            next_phase.get("real_source_inventory_authorized") is not False,
        )
    ):
        raise DocumentationStateError("fl1_i1_authorization_state_invalid")

    prior = state["prior_phase_acceptance"]
    if not isinstance(prior, dict) or any(
        (
            prior.get("phase_id") != "SCV2-FL1-P1-R1",
            prior.get("status") != "owner_accepted_and_merge_commit_merged",
            prior.get("merge_commit") != FL1_I1_ACCEPTED_MAIN,
            prior.get("final_pr_head") != FL1_I1_PR143_HEAD,
            prior.get("implementation_evidence_head") != FL1_I1_P1_R1_EVIDENCE,
            prior.get("final_review_id") != 4890771735,
            prior.get("owner_adjudication_count") != 5,
            prior.get("automated_positive_authority") is not False,
        )
    ):
        raise DocumentationStateError("fl1_i1_prior_phase_acceptance_invalid")

    protected = state["protected_evidence"]
    zero_fields = (
        "real_source_inventory_operation_count",
        "existing_database_read_operation_count",
        "existing_database_write_operation_count",
        "app_storage_write_operation_count",
        "import_operation_count",
        "provider_operation_count",
        "llm_operation_count",
        "media_or_thumbnail_operation_count",
        "network_operation_count",
        "stable_replay_operation_count",
        "production_operation_count",
    )
    if not isinstance(protected, dict) or any(
        (
            protected.get("accepted_mainline_merge_commit") != FL1_I1_ACCEPTED_MAIN,
            protected.get("fl1_p1_r1_final_head") != FL1_I1_PR143_HEAD,
            protected.get("fl1_p1_r1_implementation_evidence_head")
            != FL1_I1_P1_R1_EVIDENCE,
            protected.get("fl1_p1_r1_final_review_id") != 4890771735,
            protected.get("fl1_i1_implementation_evidence_frozen") is not audit_ready,
            protected.get("preflight_remote_sync")
            != "self_healed_by_fast_forward",
            protected.get("preflight_remote_sync_is_contract_proof") is not False,
            any(protected.get(key) != 0 for key in zero_fields),
        )
    ):
        raise DocumentationStateError("fl1_i1_protected_evidence_invalid")
    if audit_ready and state["implementation_evidence_head"] == FL1_I1_ACCEPTED_MAIN:
        raise DocumentationStateError("fl1_i1_implementation_evidence_not_frozen")
    if not audit_ready and state["implementation_evidence_head"] != FL1_I1_ACCEPTED_MAIN:
        raise DocumentationStateError("fl1_i1_in_progress_evidence_must_equal_base")


def _validate_historical_fl1_p1_r1_state(state: dict[str, Any]) -> None:
    if (
        state["phase_id"] != "SCV2-FL1-P1-R1"
        or state["branch"]
        != "codex/scv2-fl1-p1-r1-late-review-remediation"
        or state["draft"] is not True
        or state["current_status"] != FL1_STATUS
        or state["target_met"] is not False
        or state["safe_to_merge"] is not False
        or state["route_approved"] is not False
        or state["route_scope"] != FL1_ROUTE_SCOPE
        or state["planning_approved"] is not True
        or state["approved_planning_head"] != FL1_APPROVED_PLANNING_HEAD
        or state["manual_acceptance_status"] != FL1_MANUAL_STATUS
        or state["next_phase_started"] is not False
    ):
        raise DocumentationStateError("fl1_p1_r1_status_fields_conflict")
    blocker = state["active_blocker"]
    if blocker.get("code") != FL1_BLOCKER:
        raise DocumentationStateError("fl1_p1_r1_blocker_conflict")

    boundary = state["planning_boundary"]
    expected_boundary = {
        "planning_only": False,
        "implementation_authorized": True,
        "implementation_scope": FL1_COMPLETED_SCOPE,
        "completed_implementation_scope": FL1_COMPLETED_SCOPE,
        "implementation_completed": True,
        "owner_audit_pending": True,
        "owner_acceptance_valid": False,
        "merge_authorized": False,
        "fl1_i1_route_authorized": False,
        "fl1_i1_implementation_started": False,
        "real_inventory_started": False,
        "data_execution_authorized": False,
        "production_authorized": False,
        "database_access_authorized": False,
        "database_data_execution_authorized": False,
        "source_root_access_authorized": False,
        "real_source_inventory_authorized": False,
        "provider_or_llm_authorized": False,
        "provider_authorized": False,
        "llm_authorized": False,
        "media_or_thumbnail_download_authorized": False,
        "media_authorized": False,
        "classification_or_tagging_execution_authorized": False,
        "stable_replay_authorized": False,
        "synthetic_ephemeral_test_fixture_authorized": True,
        "projected_external_cost_usd": 0,
    }
    if not isinstance(boundary, dict) or any(
        boundary.get(key) != value for key, value in expected_boundary.items()
    ):
        raise DocumentationStateError("fl1_p1_r1_boundary_invalid")

    upstream = state["upstream_pr_state"]
    expected_upstream = {
        "pr_number": 141,
        "physically_merged": True,
        "merge_commit": FL1_ACCEPTED_MAIN,
        "final_head": FL1_PR141_HEAD,
        "late_review_remediation_required": True,
        "late_review_thread_count": 8,
        "late_review_threads_resolved": False,
    }
    if not isinstance(upstream, dict) or any(
        upstream.get(key) != value for key, value in expected_upstream.items()
    ):
        raise DocumentationStateError("fl1_p1_r1_upstream_state_invalid")

    next_phase = state["next_phase_authorization"]
    expected_next_phase = {
        "phase_id": "SCV2-FL1-I1",
        "route_approved": False,
        "next_phase_started": False,
        "implementation_started": False,
        "real_inventory_started": False,
        "required_preconditions": [
            "P1-R1 owner audit",
            "separate owner FL1-I1 scope decision",
        ],
    }
    if not isinstance(next_phase, dict) or any(
        next_phase.get(key) != value for key, value in expected_next_phase.items()
    ):
        raise DocumentationStateError("fl1_i1_authorization_state_invalid")

    prior = state["prior_phase_acceptance"]
    if not isinstance(prior, dict) or any(
        (
            prior.get("phase_id") != "SCV2-SV1B",
            prior.get("merge_commit") != SV1B_MERGE_COMMIT,
            prior.get("pass_count") != 37,
            prior.get("owner_waived_nonblocking_known_limitation_count") != 3,
            prior.get("pending_count") != 0,
            prior.get("unwaived_fail_count") != 0,
            sorted(prior.get("owner_waived_case_ids") or ())
            != ["B01", "B04", "B08"],
            prior.get("owner_waiver_identity") != SV1B_WAIVER,
            prior.get("waiver_inherited_by_fl1") is not False,
        )
    ):
        raise DocumentationStateError("fl1_prior_phase_acceptance_invalid")

    protected = state["protected_evidence"]
    attestation = (
        protected.get("phase_non_action_attestation")
        if isinstance(protected, dict)
        else None
    )
    if not (
        isinstance(protected, dict)
        and isinstance(attestation, dict)
        and attestation.get("schema_version") == NON_ACTION_ATTESTATION_SCHEMA
        and attestation.get("attestation_kind")
        == "phase_operator_non_action_attestation"
        and attestation.get("phase_id") == "SCV2-FL1-P1-R1"
        and attestation.get("pr_number") == 143
        and attestation.get("implementation_evidence_head")
        == state["implementation_evidence_head"]
        and attestation.get("asserted_non_actions")
        == list(ATTESTED_NON_ACTIONS)
        and attestation.get("runtime_operation_evidence_source")
        == "instrumented_run_ledger_only"
        and attestation.get("executable_runtime_evidence") is False
        and attestation.get("grants_owner_acceptance") is False
        and attestation.get("grants_safe_to_merge") is False
        and attestation.get("grants_route_authorization") is False
    ):
        raise DocumentationStateError("fl1_phase_non_action_attestation_invalid")
    legacy_event_fields = {
        "activity_event_evidence",
        "production_consumed_or_modified_during_fl1_p1",
        "production_operation_count",
        "existing_database_read_operation_count",
        "existing_database_write_operation_count",
        "real_source_inventory_operation_count",
        "provider_operation_count",
        "llm_operation_count",
        "media_or_thumbnail_operation_count",
        "stable_replay_operation_count",
        "user_data_cleanup_delete_operation_count",
    }
    if any(field in protected for field in legacy_event_fields):
        raise DocumentationStateError("editable_phase_event_ledger_forbidden")
    if protected.get("approved_planning_head") != FL1_APPROVED_PLANNING_HEAD:
        raise DocumentationStateError("fl1_approved_planning_head_evidence_invalid")
    if (
        state["accepted_mainline_base"] != FL1_ACCEPTED_MAIN
        or protected.get("fl1_plan_merge_commit") != FL1_PLAN_MERGE_COMMIT
        or protected.get("accepted_mainline_base") != FL1_ACCEPTED_MAIN
    ):
        raise DocumentationStateError("fl1_accepted_mainline_invalid")
    if (
        protected.get("fl1_p1_r1_implementation_evidence_head")
        != state["implementation_evidence_head"]
        or state["implementation_evidence_head"] == FL1_ACCEPTED_MAIN
    ):
        raise DocumentationStateError("fl1_p1_r1_implementation_head_invalid")
    if any(
        key in protected
        for key in (
            "fl1_p1_owner_acceptance_identity",
            "fl1_i1_owner_acceptance_identity",
            "merge_authorization_identity",
            "next_phase_route_authorization_identity",
        )
    ):
        raise DocumentationStateError("unbound_acceptance_or_authorization_present")


def validate_state(state: dict[str, Any], *, root: Path = ROOT) -> None:
    missing = sorted(REQUIRED_FIELDS - state.keys())
    if missing:
        raise DocumentationStateError(f"missing_fields:{','.join(missing)}")
    if state["schema_version"] != SCHEMA_VERSION:
        raise DocumentationStateError("unsupported_schema_version")
    if state["repository"] != "kyloris0660/VIOLET":
        raise DocumentationStateError("repository_mismatch")
    if not HEX40.fullmatch(str(state["accepted_mainline_base"])):
        raise DocumentationStateError("accepted_mainline_base_invalid")
    if not HEX40.fullmatch(str(state["implementation_evidence_head"])):
        raise DocumentationStateError("implementation_evidence_head_invalid")
    if state["pr_number"] is not None and (
        isinstance(state["pr_number"], bool)
        or not isinstance(state["pr_number"], int)
        or state["pr_number"] <= 0
    ):
        raise DocumentationStateError("pr_number_invalid")
    if not isinstance(state["draft"], bool):
        raise DocumentationStateError("draft_must_be_boolean")
    for key in ("target_met", "safe_to_merge", "route_approved", "next_phase_started"):
        if not isinstance(state[key], bool):
            raise DocumentationStateError(f"{key}_must_be_boolean")
    if not isinstance(state["manual_acceptance_status"], str) or not state[
        "manual_acceptance_status"
    ]:
        raise DocumentationStateError("manual_acceptance_status_invalid")
    blocker = state["active_blocker"]
    if not isinstance(blocker, dict) or not all(
        blocker.get(key) for key in ("code", "scope", "resolution")
    ):
        raise DocumentationStateError("active_blocker_invalid")
    _require_list(state, "completed_checkpoints")
    _require_list(state, "owner_decisions")
    authorized = _require_list(state, "authorized_operations")
    forbidden = _require_list(state, "forbidden_operations")
    _require_list(state, "deferred_debt", allow_empty=True)
    if not state["next_required_checkpoint"]:
        raise DocumentationStateError("next_required_checkpoint_missing")
    if state.get("public_state_boundary") != (
        "public_safe_governance_only_no_private_proof_payloads_or_paths"
    ):
        raise DocumentationStateError("public_state_boundary_invalid")

    if state["phase_id"] == "SCV2-FL1-I1":
        _validate_fl1_state(state)
    else:
        raise DocumentationStateError("unsupported_active_phase")

    joined_authorized = "\n".join(map(str, authorized)).casefold()
    joined_forbidden = "\n".join(map(str, forbidden)).casefold()
    authorization_deny_terms = (
        "database creation",
        "database write",
        "production access",
        "source root access",
        "inventory execution",
        "import execution",
        "provider execution",
        "llm execution",
        "media download",
        "classification execution",
        "ai tagging execution",
    )
    if any(term in joined_authorized for term in authorization_deny_terms):
        raise DocumentationStateError("fl1_i1_authorizes_data_execution")
    for required in (
        "database",
        "production",
        "source",
        "provider",
        "llm",
        "media",
        "entity",
        "truth",
        "direct main push",
        "force-push",
    ):
        if required not in joined_forbidden:
            raise DocumentationStateError(f"forbidden_operation_missing:{required}")

    for checkpoint in state["completed_checkpoints"]:
        if not isinstance(checkpoint, dict) or not checkpoint.get("id") or not checkpoint.get("result"):
            raise DocumentationStateError("completed_checkpoint_invalid")
    for decision in state["owner_decisions"]:
        if not isinstance(decision, dict) or not decision.get("id") or not decision.get("decision"):
            raise DocumentationStateError("owner_decision_invalid")
    for debt in state["deferred_debt"]:
        if not isinstance(debt, dict) or not all(
            debt.get(key) for key in ("id", "owner", "reason", "due_before")
        ):
            raise DocumentationStateError("deferred_debt_invalid")
        requirements = debt.get("requirements")
        if not isinstance(requirements, list) or not requirements or not all(
            isinstance(requirement, str) and requirement for requirement in requirements
        ):
            raise DocumentationStateError("deferred_debt_requirements_invalid")
    for link in _require_list(state, "durable_links"):
        if not isinstance(link, dict) or not link.get("label") or not link.get("path"):
            raise DocumentationStateError("durable_link_invalid")
        target = (root / link["path"]).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            raise DocumentationStateError(f"durable_link_missing:{link.get('path')}")

    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    serialized_folded = serialized.casefold()
    forbidden_authority_claims = (
        '"target_met": true',
        '"safe_to_merge": true',
        '"route_approved": true',
        '"real_source_inventory_authorized": true',
        '"database_access_authorized": true',
        '"app_storage_write_authorized": true',
        '"production_authorized": true',
        '"manual_acceptance_status": "owner_accepted',
    )
    if any(claim in serialized_folded for claim in forbidden_authority_claims):
        raise DocumentationStateError("unauthorized_fl1_authority_claim_present")
    for pattern in PUBLIC_FORBIDDEN:
        if pattern.search(serialized):
            raise DocumentationStateError(f"public_state_redaction_failure:{pattern.pattern}")


def validate_git_ancestry(
    state: dict[str, Any],
    *,
    root: Path = ROOT,
    implementation_evidence: dict[str, Any] | None = None,
) -> None:
    base = str(state["accepted_mainline_base"])
    implementation = str(state["implementation_evidence_head"])
    base_object = subprocess.run(
        ["git", "cat-file", "-e", f"{base}^{{commit}}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if base_object.returncode != 0:
        raise DocumentationStateError("accepted_mainline_base_object_missing")
    implementation_object = subprocess.run(
        ["git", "cat-file", "-e", f"{implementation}^{{commit}}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if implementation_object.returncode != 0:
        raise DocumentationStateError("implementation_evidence_object_missing")
    base_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if base_check.returncode != 0:
        raise DocumentationStateError("accepted_mainline_base_not_ancestor_of_head")

    implementation_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", implementation, "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if implementation_check.returncode == 0:
        return
    if implementation_evidence is None:
        raise DocumentationStateError(
            "squash_trusted_reviewed_tree_context_required"
        )
    try:
        from scripts.fl1_p1_foundation import (
            ImplementationEvidence,
            ImplementationEvidenceMode,
            verify_implementation_evidence_repository,
        )

        evidence = ImplementationEvidence.from_dict(implementation_evidence)
        if (
            evidence.mode is not ImplementationEvidenceMode.SQUASH_CARRY_FORWARD
            or evidence.approved_base_commit != base
            or evidence.implementation_commit != implementation
        ):
            raise DocumentationStateError("squash_trusted_context_binding_invalid")
        verify_implementation_evidence_repository(
            repo_root=root,
            evidence=evidence,
        )
    except DocumentationStateError:
        raise
    except Exception as exc:
        raise DocumentationStateError(
            f"squash_trusted_repository_evidence_invalid:{type(exc).__name__}"
        ) from exc


def validate_roadmaps(state: dict[str, Any], *, root: Path = ROOT) -> None:
    marker = f"<!-- CURRENT_PHASE: {state['phase_id']} -->"
    for relative in (
        Path("docs/roadmap/current-mainline-roadmap.md"),
        Path("docs/project-roadmap.md"),
        Path("docs/phase-contracts.md"),
    ):
        text = (root / relative).read_text(encoding="utf-8")
        markers = re.findall(r"<!-- CURRENT_PHASE: ([A-Z0-9-]+) -->", text)
        if markers != [state["phase_id"]] or text.count(marker) != 1:
            raise DocumentationStateError(f"current_phase_conflict:{relative.as_posix()}")
    contract = (root / "docs" / "phase-contracts.md").read_text(encoding="utf-8")
    if state["active_blocker"]["code"] not in contract:
        raise DocumentationStateError("active_blocker_missing_from_contract")
    active_text = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            Path("docs/project-roadmap.md"),
            Path("docs/roadmap/current-mainline-roadmap.md"),
            Path("docs/phase-contracts.md"),
        )
    ).casefold()
    stale_current_claims = (
        "r1r is required as the current next phase",
        "r1r remains the current next technical phase",
        "start `r1r` only after",
        "full­lib-e1 as the next implementation",
        "status=fl1_p1_owner_accepted_for_merge",
        "status=fl1_p1_r1_implementation_ready_for_owner_audit",
        "manual_acceptance_status=pending_final_owner_audit",
        "pr #141 may become ready",
        "start a separate fl1-i1",
        "owner_authorized_fl1_i1_planning_and_synthetic_implementation_20260808",
    )
    if any(claim in active_text for claim in stale_current_claims):
        raise DocumentationStateError("stale_active_route_claim")


def _link_for_handoff(link: dict[str, str]) -> str:
    path = link["path"]
    if not path.startswith("docs/"):
        raise DocumentationStateError(f"handoff_link_outside_docs:{path}")
    return f"[{link['label']}]({path.removeprefix('docs/')})"


def render_handoff(state: dict[str, Any]) -> str:
    blocker = state["active_blocker"]
    boundary = state["planning_boundary"]
    if state["pr_number"] is None:
        pr_label = "Draft PR pending creation" if state["draft"] else "PR pending creation"
    elif state["draft"]:
        pr_label = f"Draft PR #{state['pr_number']}"
    else:
        pr_label = f"PR #{state['pr_number']}"
    lines = [
        "# Current Handoff - V.I.O.L.E.T.",
        "",
        "> Generated from `docs/state/current-phase.json`; this file is not the fact source.",
        "",
        "## Current Facts",
        "",
        f"- Phase: `{state['phase_id']}` — {state['phase_title']}.",
        f"- Repository / PR: `{state['repository']}` / {pr_label}.",
        f"- Branch: `{state['branch']}`.",
        f"- Accepted mainline base: `{state['accepted_mainline_base']}`.",
        f"- Implementation evidence HEAD: `{state['implementation_evidence_head']}` (frozen: `{str(state['protected_evidence']['fl1_i1_implementation_evidence_frozen']).lower()}`).",
        f"- Status: `{state['current_status']}`.",
        f"- `target_met={str(state['target_met']).lower()}`; `safe_to_merge={str(state['safe_to_merge']).lower()}`; `route_approved={str(state['route_approved']).lower()}`.",
        f"- `manual_acceptance_status={state['manual_acceptance_status']}`; `next_phase_started={str(state['next_phase_started']).lower()}` (I1 synthetic implementation is authorized; real source inventory is not authorized or started).",
        f"- Approved planning HEAD: `{state['approved_planning_head']}`; route scope: `{state['route_scope']}`.",
        "",
        "## Completed Checkpoints",
        "",
    ]
    for checkpoint in state["completed_checkpoints"]:
        suffix = f" — `{checkpoint['fingerprint']}`" if checkpoint.get("fingerprint") else ""
        lines.append(f"- `{checkpoint['id']}`: `{checkpoint['result']}`{suffix}.")
    lines.extend(
        [
            "",
            "## Current Gate And Boundary",
            "",
            f"- Gate: `{blocker['code']}` ({blocker['scope']}).",
            f"- Resolution: {blocker['resolution']}",
            f"- Planning only: `{str(boundary['planning_only']).lower()}`; implementation/data/production authorization: `{str(boundary['implementation_authorized']).lower()}/{str(boundary['data_execution_authorized']).lower()}/{str(boundary['production_authorized']).lower()}`.",
            f"- Existing database/real inventory/provider-or-LLM/media authorization: `{str(boundary['database_access_authorized']).lower()}/{str(boundary['real_source_inventory_authorized']).lower()}/{str(boundary['provider_or_llm_authorized']).lower()}/{str(boundary['media_authorized']).lower()}`; projected external cost: `${boundary['projected_external_cost_usd']}`.",
            f"- Public state boundary: `{state['public_state_boundary']}`. Preflight sync and phase non-actions are operator classifications only; executable I1 claims must be rebuilt from trusted private artifacts and grant no owner, merge, route, or real-source authority.",
            "",
            "## Allowed / Forbidden",
            "",
            "- Allowed: " + "; ".join(map(str, state["authorized_operations"])) + ".",
            "- Forbidden: " + "; ".join(map(str, state["forbidden_operations"])) + ".",
            "",
            "## Next Action",
            "",
            f"- Required checkpoint: `{state['next_required_checkpoint']}`.",
            "",
            "## Durable Links",
            "",
        ]
    )
    lines.extend(f"- {_link_for_handoff(link)}" for link in state["durable_links"])
    lines.extend(["", "## Deferred Debt", ""])
    if state["deferred_debt"]:
        for debt in state["deferred_debt"]:
            requirements = "; ".join(debt["requirements"])
            lines.append(
                f"- `{debt['id']}` — owner: {debt['owner']}; due before: `{debt['due_before']}`; {debt['reason']} Requirements: {requirements}."
            )
    else:
        lines.append("- None.")
    lines.extend([f"Updated: `{state['updated_at']}`.", ""])
    return "\n".join(lines)


def check_handoff(state: dict[str, Any], *, path: Path = HANDOFF_PATH) -> None:
    expected = render_handoff(state)
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        raise DocumentationStateError("generated_handoff_drift")
    line_count = len(actual.splitlines())
    if not 40 <= line_count <= 60:
        raise DocumentationStateError(f"handoff_line_count_out_of_range:{line_count}")


def write_handoff(state: dict[str, Any], *, path: Path = HANDOFF_PATH) -> None:
    """Atomically render the non-authoritative handoff from current state."""

    rendered = render_handoff(state)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    temporary.replace(path)


def check_documentation_state(
    *,
    root: Path = ROOT,
    implementation_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = load_state(root / "docs" / "state" / "current-phase.json")
    validate_state(state, root=root)
    if root.resolve() == ROOT.resolve():
        validate_git_ancestry(
            state,
            root=root,
            implementation_evidence=implementation_evidence,
        )
    validate_roadmaps(state, root=root)
    check_handoff(state, path=root / "docs" / "current-handoff.md")
    return {
        "passed": True,
        "schema_version": state["schema_version"],
        "phase_id": state["phase_id"],
        "current_status": state["current_status"],
        "manual_acceptance_status": state["manual_acceptance_status"],
        "handoff_line_count": len(render_handoff(state).splitlines()),
        "durable_link_count": len(state["durable_links"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--render", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument(
        "--implementation-evidence",
        help="Trusted ImplementationEvidence JSON required for squash carry-forward checks.",
    )
    args = parser.parse_args(argv)
    try:
        implementation_evidence = None
        if args.implementation_evidence:
            try:
                implementation_evidence = json.loads(
                    Path(args.implementation_evidence).read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise DocumentationStateError(
                    f"implementation_evidence_context_unreadable:{type(exc).__name__}"
                ) from exc
            if not isinstance(implementation_evidence, dict):
                raise DocumentationStateError(
                    "implementation_evidence_context_must_be_object"
                )
        state = load_state()
        if args.render:
            validate_state(state)
            sys.stdout.write(render_handoff(state))
            return 0
        if args.write:
            validate_state(state)
            validate_roadmaps(state)
            write_handoff(state)
        result = check_documentation_state(
            implementation_evidence=implementation_evidence
        )
    except DocumentationStateError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
