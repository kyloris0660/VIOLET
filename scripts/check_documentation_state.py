"""Fail-closed current-phase state checker and generated handoff renderer."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable


WindowsGitLocationProvider = Callable[
    [], tuple[tuple[Path, ...], tuple[Path, ...]]
]


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trusted_git import (
    TrustedGitError,
    decode_git_z_paths,
    resolve_trusted_git_executable as _shared_resolve_trusted_git_executable,
    run_trusted_git_bytes,
    run_trusted_git_text,
    trusted_git_candidates as _shared_trusted_git_candidates,
    trusted_git_environment as _shared_trusted_git_environment,
    validate_git_path,
    windows_system_git_roots as _shared_windows_system_git_roots,
    windows_trusted_git_candidates as _shared_windows_trusted_git_candidates,
)

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
    "planning_authorized",
    "planning_completed",
    "planning_approved",
    "approved_planning_head",
    "approved_planning_tree",
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
    "terminal_review_findings",
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
FL1_I1_HISTORICAL_EVIDENCE = "5194a484d0d8fb8dd5e0697cd61054f596aee5ec"
FL1_I1_HISTORICAL_EVIDENCE_TREE = "9b30ba024beb6fcd58709e707d7879887ad7c081"
FL1_I1_FIRST_REVIEWED_HEAD = "b65c7b84adfe45b92f85dfb72d60920bd1fb0ad3"
FL1_I1_FIRST_REVIEW_ID = 4891695875
FL1_I1_ROUTE_SCOPE = (
    "SCV2-FL1-I1 reusable read-only inventory safety tooling using only "
    "synthetic and newly created temporary fixtures"
)
FL1_I2_BRANCH = "codex/scv2-fl1-i2-synthetic-pre-real-hardening"
FL1_I1_MERGE_COMMIT = "8955b95e91630d4c5e18e1e2ca252b19754c81d5"
FL1_I2_PREVIOUS_FINAL_HEAD = "2f8d5f8ce6cde9759c530de71d4ddd1893481656"
FL1_I2_PREVIOUS_FINAL_TREE = "8930a21bdbac037702f92bcb75bd9b8a3632a073"
FL1_I2_PREVIOUS_EVIDENCE = "6992e7f1e5a45857111d15da1ad0274e49008a99"
FL1_I2_PREVIOUS_EVIDENCE_TREE = "6ff185defb150c3751c7433ef635c00a200c44bf"
FL1_I2_TERMINAL_REVIEW_ID = 4897012517
FL1_I2_PR_NUMBER = 146
FL1_I2_PLANNING_PR_NUMBER = 145
FL1_I2_APPROVED_PLANNING_HEAD = "acb12c1db258fdef1d4f063b053d422e0d887abf"
FL1_I2_APPROVED_PLANNING_TREE = "fc573c7646ad5edf10c32c7712de7f27ab058a2a"
FL1_I2_PLANNING_PROJECTION_HEAD = "7275bceff9152ea5f823186691e6b91ee2ca1e11"
FL1_I2_PLANNING_PROJECTION_TREE = "5cd76cf148c99c456c7bed1ee87f74d3ecf323a7"
FL1_I2_PLANNING_MERGE_COMMIT = "1913bd27517efc1a6007a202fc9650de4f20fab4"
FL1_I2_PLANNING_MERGE_TREE = "5cd76cf148c99c456c7bed1ee87f74d3ecf323a7"
FL1_I2_PLANNING_MERGE_PARENTS = (
    "8955b95e91630d4c5e18e1e2ca252b19754c81d5",
    FL1_I2_PLANNING_PROJECTION_HEAD,
)
FL1_I2_OWNER_REVIEW_ID = 4907783329
FL1_I2_OWNER_THREAD_ID = "PRRT_kwDOSTBMB86YRuq7"
FL1_I2_OWNER_COMMENT_ID = 3759240785
FL1_I2_OWNER_DECISION_ID = (
    "owner_accepted_scv2_fl1_i2_pr145_exact_planning_evidence_20260813"
)
FL1_I2_OWNER_DECISION = (
    "SCV2_FL1_I2_PR145_OWNER_ACCEPTED_EXACT_PLANNING_EVIDENCE_AND_"
    "AUTHORIZED_GOVERNANCE_PROJECTION_EXPECTED_HEAD_MERGE"
)
FL1_I2_STATUS = "fl1_i2_pr146_bounded_correction_ready_for_owner_reaudit"
FL1_I2_IMPLEMENTATION_DECISION_ID = (
    "owner_authorized_scv2_fl1_i2_synthetic_pre_real_hardening_20260817"
)
FL1_I2_POST_MERGE_REVIEW_ID = 4927462216
FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD = "e1a978c4c12bcb8ae4a8312c148fca3fcbfac049"
FL1_I2_IMPLEMENTATION_EVIDENCE_TREE = "99573bda4c45f9b51f8a1acd5989de0c807efbd1"
FL1_I2_SUPERSEDED_EVIDENCE_HEAD = "78ccbdc69ee1bf0f51c297435b56e2be868b54e9"
FL1_I2_SUPERSEDED_EVIDENCE_TREE = "311b34f7c7fb5e5947b696598ded15dfd325e3f4"
FL1_I2_BOUNDED_CORRECTION_REVIEW_ID = 4952182962
FL1_I2_BOUNDED_CORRECTION_DECISION_ID = (
    "owner_authorized_scv2_fl1_i2_pr146_bounded_correction_20260817"
)
FL1_I2_BOUNDED_CORRECTION_THREAD_IDS = (
    "PRRT_kwDOSTBMB86Z0JuG",
    "PRRT_kwDOSTBMB86Z0JuI",
    "PRRT_kwDOSTBMB86Z0JuM",
    "PRRT_kwDOSTBMB86Z0JuP",
    "PRRT_kwDOSTBMB86Z0JuR",
    "PRRT_kwDOSTBMB86Z0JuT",
    "PRRT_kwDOSTBMB86Z0JuV",
    "PRRT_kwDOSTBMB86Z0JuY",
    "PRRT_kwDOSTBMB86Z0Juc",
    "PRRT_kwDOSTBMB86Z0Juf",
)
FL1_I2_CONTRACT_ID = "scv2_fl1_i2_pre_real_hardening_contract_v1"
FL1_I2_PUBLIC_SCHEMA = "violet.scv2-fl1-i2-public-summary.v1"
FL1_I2_G0_THREAD_IDS = (
    "PRRT_kwDOSTBMB86Y8Xjl",
    "PRRT_kwDOSTBMB86Y8Xjr",
    "PRRT_kwDOSTBMB86Y8Xju",
    "PRRT_kwDOSTBMB86Y8Xjx",
    "PRRT_kwDOSTBMB86Y8Xj1",
)
FL1_I2_BLOCKER = "pending_fl1_i2_bounded_followup_review_and_owner_reaudit"
FL1_I2_MANUAL_STATUS = "pending_fl1_i2_bounded_correction_owner_reaudit"
FL1_I2_ROUTE_SCOPE = (
    "SCV2-FL1-I2 synthetic pre-real hardening implementation using only "
    "adversarial newly created temporary fixtures; no real-source execution"
)
FL1_I2_PROJECTION_ALLOWLIST = frozenset(
    {
        "README.md",
        "docs/current-handoff.md",
        "docs/phase-contracts.md",
        "docs/project-roadmap.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/state/current-phase.json",
        "docs/test-workflow.md",
        "scripts/check_documentation_state.py",
        "tests/test_current_handoff_freshness.py",
        "tests/test_pd1a_mainline_governance.py",
        "tests/test_phase45_doc1_documentation_state.py",
        "tests/test_phase45_scv2_a1_post_expansion_audit_route_decision.py",
        "tests/test_phase45_scv2_r1_post_px1_source_concept_triage.py",
        "tests/test_scv2_fl1_i2_validation_receipt.py",
    }
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

    status = state["current_status"]
    status_policy = {
        "fl1_i1_read_only_inventory_implementation_in_progress": (
            "pending_i1_implementation_owner_audit",
            "fl1_i1_implementation_in_progress",
            False,
            False,
        ),
        "fl1_i1_synthetic_implementation_ready_for_owner_audit": (
            "pending_i1_synthetic_implementation_owner_audit",
            "pending_i1_synthetic_implementation_owner_audit_and_real_source_scope",
            True,
            False,
        ),
        "fl1_i1_first_review_bounded_remediation_in_progress": (
            "pending_i1_bounded_remediation_owner_audit",
            "pr144_first_review_current_i1_trust_recovery_counterexamples",
            False,
            True,
        ),
        "fl1_i1_bounded_remediation_ready_for_owner_audit": (
            "pending_i1_bounded_remediation_owner_audit",
            "pending_i1_bounded_remediation_owner_audit",
            True,
            True,
        ),
    }
    try:
        expected_manual, expected_blocker, audit_ready, remediation = status_policy[
            status
        ]
    except KeyError as exc:
        raise DocumentationStateError("fl1_i1_status_fields_conflict") from exc
    if (
        state["phase_id"] != "SCV2-FL1-I1"
        or state["branch"] != FL1_I1_BRANCH
        or state["draft"] is not True
        or state["current_status"] != status
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
    if remediation and any(
        (
            state.get("bounded_remediation_round") != "1_of_1",
            state.get("implementation_evidence_status")
            not in {
                "historical_superseded_pending_bounded_remediation_replacement",
                "bounded_remediation_replacement_frozen",
            },
        )
    ):
        raise DocumentationStateError("fl1_i1_bounded_remediation_state_invalid")

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
    if remediation and any(
        (
            boundary.get("bounded_remediation_authorized") is not True,
            boundary.get("bounded_remediation_round") != "1_of_1",
            boundary.get("reviewed_head") != FL1_I1_FIRST_REVIEWED_HEAD,
        )
    ):
        raise DocumentationStateError("fl1_i1_bounded_remediation_boundary_invalid")

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
    if (
        not audit_ready
        and not remediation
        and state["implementation_evidence_head"] != FL1_I1_ACCEPTED_MAIN
    ):
        raise DocumentationStateError("fl1_i1_in_progress_evidence_must_equal_base")
    if remediation:
        remediation_expected = {
            "fl1_i1_historical_superseded_implementation_evidence_head": FL1_I1_HISTORICAL_EVIDENCE,
            "fl1_i1_historical_superseded_implementation_evidence_tree": FL1_I1_HISTORICAL_EVIDENCE_TREE,
            "fl1_i1_historical_superseded_evidence_status": "superseded_by_pr144_review_4891695875_pending_replacement",
            "pr144_first_review_id": FL1_I1_FIRST_REVIEW_ID,
            "pr144_first_reviewed_head": FL1_I1_FIRST_REVIEWED_HEAD,
            "pr144_first_review_finding_count": 18,
            "pr144_first_review_p1_count": 15,
            "pr144_first_review_p2_count": 3,
            "bounded_remediation_round": "1_of_1",
        }
        if any(
            protected.get(key) != value
            for key, value in remediation_expected.items()
        ):
            raise DocumentationStateError(
                "fl1_i1_bounded_remediation_protected_evidence_invalid"
            )
        if not audit_ready and any(
            (
                state["implementation_evidence_head"]
                != FL1_I1_HISTORICAL_EVIDENCE,
                protected.get("fl1_i1_current_implementation_evidence_pending")
                is not True,
                protected.get("fl1_i1_implementation_evidence_frozen") is not False,
            )
        ):
            raise DocumentationStateError(
                "fl1_i1_bounded_remediation_evidence_transition_invalid"
            )
        if audit_ready and any(
            (
                state["implementation_evidence_head"]
                in {FL1_I1_ACCEPTED_MAIN, FL1_I1_HISTORICAL_EVIDENCE},
                protected.get("fl1_i1_current_implementation_evidence_pending")
                is not False,
                protected.get("fl1_i1_implementation_evidence_frozen") is not True,
                protected.get("fl1_i1_implementation_evidence_head")
                != state["implementation_evidence_head"],
            )
        ):
            raise DocumentationStateError(
                "fl1_i1_bounded_remediation_evidence_transition_invalid"
            )


def _validate_fl1_i2_state(state: dict[str, Any]) -> None:
    """Validate the post-PR-144 governance closeout and I2 planning boundary."""

    expected_top_level = {
        "phase_id": "SCV2-FL1-I2",
        "phase_title": "Real-source Read-only Inventory Hardening and Canary Readiness",
        "branch": FL1_I2_BRANCH,
        "draft": False,
        "accepted_mainline_base": FL1_I2_PLANNING_MERGE_COMMIT,
        "implementation_evidence_head": FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,
        "implementation_evidence_status": "current_i2_bounded_correction_implementation_evidence_frozen",
        "current_status": FL1_I2_STATUS,
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "route_scope": FL1_I2_ROUTE_SCOPE,
        "planning_authorized": True,
        "planning_completed": True,
        "planning_approved": True,
        "approved_planning_head": FL1_I2_APPROVED_PLANNING_HEAD,
        "approved_planning_tree": FL1_I2_APPROVED_PLANNING_TREE,
        "manual_acceptance_status": FL1_I2_MANUAL_STATUS,
        "next_phase_started": True,
        "previous_phase": "SCV2-FL1-I1",
        "previous_phase_status": "owner_accepted_and_merge_commit_merged",
        "previous_phase_merge_commit": FL1_I1_MERGE_COMMIT,
        "previous_phase_final_head": FL1_I2_PREVIOUS_FINAL_HEAD,
        "previous_phase_final_tree": FL1_I2_PREVIOUS_FINAL_TREE,
        "previous_phase_implementation_completed": True,
        "previous_phase_accepted_scope": "synthetic_and_new_temporary_fixture_foundation_only",
        "previous_phase_real_inventory_target_met": False,
        "previous_phase_owner_accepted": True,
        "previous_phase_safe_to_merge": True,
        "previous_phase_terminal_review_id": FL1_I2_TERMINAL_REVIEW_ID,
        "previous_phase_terminal_review_findings": 17,
        "previous_phase_terminal_review_p1": 13,
        "previous_phase_terminal_review_p2": 4,
        "previous_phase_github_checks": 0,
        "previous_phase_machine_verifiable_ci": False,
    }
    if any(state.get(key) != value for key, value in expected_top_level.items()):
        raise DocumentationStateError("fl1_i2_status_fields_conflict")
    if state["pr_number"] != FL1_I2_PR_NUMBER:
        raise DocumentationStateError("fl1_i2_pr_number_invalid")
    if state["active_blocker"].get("code") != FL1_I2_BLOCKER:
        raise DocumentationStateError("fl1_i2_blocker_conflict")

    boundary = state["planning_boundary"]
    expected_boundary = {
        "planning_only": False,
        "planning_authorized": True,
        "planning_completed": True,
        "planning_approved": True,
        "implementation_authorized": True,
        "implementation_started": True,
        "implementation_completed": True,
        "owner_audit_pending": True,
        "owner_acceptance_valid": True,
        "merge_authorized": False,
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
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
        "synthetic_ephemeral_test_fixture_authorized": True,
        "documentation_validation_authorized": True,
        "projected_external_cost_usd": 0,
    }
    if not isinstance(boundary, dict) or any(
        boundary.get(key) != value for key, value in expected_boundary.items()
    ):
        raise DocumentationStateError("fl1_i2_boundary_invalid")

    upstream = state["upstream_pr_state"]
    expected_upstream = {
        "pr_number": 144,
        "state": "merged",
        "merged": True,
        "draft": False,
        "merge_commit": FL1_I1_MERGE_COMMIT,
        "merge_topology": "merge_commit",
        "final_head": FL1_I2_PREVIOUS_FINAL_HEAD,
        "final_tree": FL1_I2_PREVIOUS_FINAL_TREE,
        "implementation_evidence_head": FL1_I2_PREVIOUS_EVIDENCE,
        "implementation_evidence_tree": FL1_I2_PREVIOUS_EVIDENCE_TREE,
        "terminal_review_id": FL1_I2_TERMINAL_REVIEW_ID,
        "terminal_reviewed_head": FL1_I2_PREVIOUS_FINAL_HEAD,
        "terminal_review_finding_count": 17,
        "terminal_review_p1_count": 13,
        "terminal_review_p2_count": 4,
        "terminal_review_resolved_count": 0,
        "terminal_review_outdated_count": 0,
        "github_checks": 0,
        "historical_review_threads_preserved": True,
        "owner_accepted": True,
        "owner_decision": "SCV2_FL1_I1_PR144_TERMINAL_OWNER_AUDIT_ACCEPTED_AS_SYNTHETIC_FOUNDATION_WITH_USE_BEFORE_GATES",
    }
    if not isinstance(upstream, dict) or any(
        upstream.get(key) != value for key, value in expected_upstream.items()
    ):
        raise DocumentationStateError("fl1_i2_upstream_state_invalid")

    next_phase = state["next_phase_authorization"]
    expected_next_phase = {
        "phase_id": "SCV2-FL1-I2",
        "planning_authorized": True,
        "planning_completed": True,
        "planning_approved": True,
        "implementation_authorized": True,
        "implementation_started": True,
        "implementation_completed": True,
        "synthetic_fixture_execution_authorized": True,
        "real_inventory_started": False,
        "real_source_inventory_authorized": False,
        "i3_canary_started": False,
        "required_preconditions": [
            "PR #145 is merged at the owner-authorized expected governance projection HEAD before any I2 implementation",
            "I2 implementation is separately authorized by the owner for this synthetic-only branch",
            "I2 implementation is restricted to synthetic or adversarial newly created temporary fixtures while real source, iCloud, database, app storage, import, provider, model, media, and production authority remain false",
            "all fourteen I2 delivery gates close before implementation_completed, target_met, safe_to_merge, merge, or I3",
            "I2 passes owner audit and merges before any real source operation",
            "a separate FL1_I3_REAL_SOURCE_SCOPE_GATE binds exact private source scope, protected roots, budgets, no-hydration policy, and stop conditions",
        ],
    }
    if not isinstance(next_phase, dict) or any(
        next_phase.get(key) != value for key, value in expected_next_phase.items()
    ):
        raise DocumentationStateError("fl1_i2_authorization_state_invalid")

    prior = state["prior_phase_acceptance"]
    expected_prior = {
        "phase_id": "SCV2-FL1-I1",
        "status": "owner_accepted_and_merge_commit_merged",
        "accepted_scope": "synthetic_and_new_temporary_fixture_foundation_only",
        "real_inventory_target_met": False,
        "implementation_completed": True,
        "owner_accepted": True,
        "safe_to_merge": True,
        "merge_commit": FL1_I1_MERGE_COMMIT,
        "final_head": FL1_I2_PREVIOUS_FINAL_HEAD,
        "final_tree": FL1_I2_PREVIOUS_FINAL_TREE,
        "implementation_evidence_head": FL1_I2_PREVIOUS_EVIDENCE,
        "implementation_evidence_tree": FL1_I2_PREVIOUS_EVIDENCE_TREE,
        "terminal_review_id": FL1_I2_TERMINAL_REVIEW_ID,
        "terminal_review_finding_count": 17,
        "terminal_review_p1_count": 13,
        "terminal_review_p2_count": 4,
        "github_checks": 0,
        "machine_verifiable_ci": False,
        "automated_positive_authority": False,
    }
    if not isinstance(prior, dict) or any(
        prior.get(key) != value for key, value in expected_prior.items()
    ):
        raise DocumentationStateError("fl1_i2_prior_phase_acceptance_invalid")

    protected = state["protected_evidence"]
    expected_protected = {
        "accepted_mainline_merge_commit": FL1_I2_PLANNING_MERGE_COMMIT,
        "accepted_mainline_merge_tree": FL1_I2_PLANNING_MERGE_TREE,
        "accepted_mainline_merge_parents": list(FL1_I2_PLANNING_MERGE_PARENTS),
        "planning_projection_head": FL1_I2_PLANNING_PROJECTION_HEAD,
        "planning_projection_tree": FL1_I2_PLANNING_PROJECTION_TREE,
        "previous_phase_final_head": FL1_I2_PREVIOUS_FINAL_HEAD,
        "previous_phase_final_tree": FL1_I2_PREVIOUS_FINAL_TREE,
        "previous_phase_implementation_evidence_head": FL1_I2_PREVIOUS_EVIDENCE,
        "previous_phase_implementation_evidence_tree": FL1_I2_PREVIOUS_EVIDENCE_TREE,
        "fl1_i1_implementation_evidence_frozen": True,
        "previous_phase_terminal_review_id": FL1_I2_TERMINAL_REVIEW_ID,
        "previous_phase_terminal_reviewed_head": FL1_I2_PREVIOUS_FINAL_HEAD,
        "previous_phase_terminal_review_finding_count": 17,
        "previous_phase_terminal_review_p1_count": 13,
        "previous_phase_terminal_review_p2_count": 4,
        "previous_phase_terminal_review_resolved_count": 0,
        "previous_phase_terminal_review_outdated_count": 0,
        "validation_receipt_trust_level": "local_operator_receipt",
        "machine_verifiable_ci": False,
        "github_checks_observed": 0,
        "ci_authority": False,
        "preflight_remote_sync": "self_healed_by_fast_forward",
        "preflight_remote_sync_is_contract_proof": False,
        "authorized_git_github_governance_control_plane_operations_occurred": True,
        "approved_planning_head": FL1_I2_APPROVED_PLANNING_HEAD,
        "approved_planning_tree": FL1_I2_APPROVED_PLANNING_TREE,
        "approved_planning_pr_number": FL1_I2_PLANNING_PR_NUMBER,
        "approved_planning_review_id": FL1_I2_OWNER_REVIEW_ID,
        "approved_planning_thread_id": FL1_I2_OWNER_THREAD_ID,
        "approved_planning_comment_id": FL1_I2_OWNER_COMMENT_ID,
        "owner_acceptance_decision_id": FL1_I2_OWNER_DECISION_ID,
        "planning_merge_authority_consumed": True,
        "planning_merge_authorized": False,
        "post_merge_review_id": FL1_I2_POST_MERGE_REVIEW_ID,
        "g0_post_merge_governance_entry_gate_closed": True,
        "g0_thread_ids": list(FL1_I2_G0_THREAD_IDS),
        "windows_same_handle_feasibility_passed": True,
        "windows_same_handle_feasibility_scope": "os_created_temporary_directory_only",
        "windows_same_handle_no_path_fallback": True,
        "file_open_no_recall_claim_scope": (
            "open_itself_only_not_later_read_guarantee"
        ),
        "fl1_i2_implementation_evidence_head": FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,
        "fl1_i2_implementation_evidence_tree": FL1_I2_IMPLEMENTATION_EVIDENCE_TREE,
        "fl1_i2_implementation_evidence_frozen": True,
        "fl1_i2_superseded_evidence_head": FL1_I2_SUPERSEDED_EVIDENCE_HEAD,
        "fl1_i2_superseded_evidence_tree": FL1_I2_SUPERSEDED_EVIDENCE_TREE,
        "fl1_i2_superseded_evidence_reason": (
            "owner_adjudicated_pr146_review_findings_require_bounded_correction"
        ),
        "fl1_i2_bounded_correction_review_id": (
            FL1_I2_BOUNDED_CORRECTION_REVIEW_ID
        ),
        "fl1_i2_bounded_correction_thread_ids": list(
            FL1_I2_BOUNDED_CORRECTION_THREAD_IDS
        ),
        "fl1_i2_bounded_correction_authorized": True,
        "fl1_i2_one_followup_codex_review_authorized": True,
        "fl1_i2_contract_id": FL1_I2_CONTRACT_ID,
        "fl1_i2_public_schema": FL1_I2_PUBLIC_SCHEMA,
        "fl1_i2_delivery_gate_count": 14,
        "fl1_i2_delivery_gates_closed": True,
    }
    zero_fields = (
        "real_source_inventory_operation_count",
        "existing_database_read_operation_count",
        "existing_database_write_operation_count",
        "app_storage_write_operation_count",
        "import_operation_count",
        "classification_or_tagging_operation_count",
        "provider_operation_count",
        "llm_operation_count",
        "media_or_thumbnail_operation_count",
        "external_data_plane_network_operation_count",
        "stable_replay_operation_count",
        "ui_or_server_operation_count",
        "production_operation_count",
    )
    if not isinstance(protected, dict) or "network_operation_count" in protected or any(
        protected.get(key) != value for key, value in expected_protected.items()
    ) or any(protected.get(key) != 0 for key in zero_fields):
        raise DocumentationStateError("fl1_i2_protected_evidence_invalid")

    matching_decisions = [
        decision
        for decision in state["owner_decisions"]
        if isinstance(decision, dict)
        and decision.get("id") == FL1_I2_OWNER_DECISION_ID
    ]
    expected_owner_decision = {
        "id": FL1_I2_OWNER_DECISION_ID,
        "decision": FL1_I2_OWNER_DECISION,
        "pr_number": FL1_I2_PLANNING_PR_NUMBER,
        "accepted_planning_head": FL1_I2_APPROVED_PLANNING_HEAD,
        "accepted_planning_tree": FL1_I2_APPROVED_PLANNING_TREE,
        "review_id": FL1_I2_OWNER_REVIEW_ID,
        "thread_id": FL1_I2_OWNER_THREAD_ID,
        "comment_id": FL1_I2_OWNER_COMMENT_ID,
        "finding_severity": "P1",
        "finding_disposition": (
            "closed_in_owner_acceptance_projection_exact_binding_contract"
        ),
        "implementation_authorized": False,
        "real_source_inventory_authorized": False,
    }
    if matching_decisions != [expected_owner_decision]:
        raise DocumentationStateError("fl1_i2_owner_acceptance_binding_invalid")

    implementation_decisions = [
        decision
        for decision in state["owner_decisions"]
        if isinstance(decision, dict)
        and decision.get("id") == FL1_I2_IMPLEMENTATION_DECISION_ID
    ]
    if implementation_decisions != [
        {
            "id": FL1_I2_IMPLEMENTATION_DECISION_ID,
            "decision": (
                "Authorize one SCV2-FL1-I2 synthetic pre-real hardening "
                "implementation branch and Draft PR using only adversarial "
                "newly created temporary fixtures."
            ),
            "accepted_mainline_base": FL1_I2_PLANNING_MERGE_COMMIT,
            "post_merge_review_id": FL1_I2_POST_MERGE_REVIEW_ID,
            "g0_thread_ids": list(FL1_I2_G0_THREAD_IDS),
            "implementation_authorized": True,
            "synthetic_fixture_execution_authorized": True,
            "real_source_inventory_authorized": False,
            "merge_authorized": False,
        }
    ]:
        raise DocumentationStateError("fl1_i2_implementation_authority_binding_invalid")

    correction_decisions = [
        decision
        for decision in state["owner_decisions"]
        if isinstance(decision, dict)
        and decision.get("id") == FL1_I2_BOUNDED_CORRECTION_DECISION_ID
    ]
    if correction_decisions != [
        {
            "id": FL1_I2_BOUNDED_CORRECTION_DECISION_ID,
            "decision": (
                "Authorize one bounded additive correction round in PR #146 "
                "for all ten accepted exact-head findings, followed by one "
                "explicit Codex review request and an owner re-audit stop."
            ),
            "pr_number": FL1_I2_PR_NUMBER,
            "superseded_head": FL1_I2_SUPERSEDED_EVIDENCE_HEAD,
            "superseded_tree": FL1_I2_SUPERSEDED_EVIDENCE_TREE,
            "review_id": FL1_I2_BOUNDED_CORRECTION_REVIEW_ID,
            "thread_ids": list(FL1_I2_BOUNDED_CORRECTION_THREAD_IDS),
            "finding_disposition": "accept_and_require_fix",
            "correction_authorized": True,
            "same_branch_normal_push_authorized": True,
            "one_followup_codex_review_authorized": True,
            "merge_authorized": False,
            "real_source_inventory_authorized": False,
        }
    ]:
        raise DocumentationStateError("fl1_i2_bounded_correction_binding_invalid")

    matching_checkpoints = [
        checkpoint
        for checkpoint in state["completed_checkpoints"]
        if isinstance(checkpoint, dict)
        and checkpoint.get("id") == "fl1_i2_exact_plan_owner_acceptance"
    ]
    if matching_checkpoints != [
        {
            "id": "fl1_i2_exact_plan_owner_acceptance",
            "result": "owner_accepted_exact_planning_head_tree_pending_expected_head_merge",
            "fingerprint": FL1_I2_APPROVED_PLANNING_HEAD,
        }
    ]:
        raise DocumentationStateError("fl1_i2_owner_acceptance_checkpoint_invalid")

    g0_checkpoints = [
        checkpoint
        for checkpoint in state["completed_checkpoints"]
        if isinstance(checkpoint, dict)
        and checkpoint.get("id") == "fl1_i2_g0_post_merge_governance_entry_gate"
    ]
    if g0_checkpoints != [
        {
            "id": "fl1_i2_g0_post_merge_governance_entry_gate",
            "result": "five_post_merge_p1_findings_closed_in_shared_git_state_and_history_guards",
            "fingerprint": FL1_I2_PLANNING_MERGE_COMMIT,
        }
    ]:
        raise DocumentationStateError("fl1_i2_g0_checkpoint_invalid")

    feasibility_checkpoints = [
        checkpoint
        for checkpoint in state["completed_checkpoints"]
        if isinstance(checkpoint, dict)
        and checkpoint.get("id") == "fl1_i2_windows_same_handle_feasibility"
    ]
    if feasibility_checkpoints != [
        {
            "id": "fl1_i2_windows_same_handle_feasibility",
            "result": (
                "pass_on_windows_live_new_temporary_directory_with_no_path_traversal_fallback"
            ),
            "fingerprint": "windows_live_temp_file_id_extd_ntcreatefile_v1",
        }
    ]:
        raise DocumentationStateError("fl1_i2_windows_feasibility_checkpoint_invalid")

    implementation_checkpoints = [
        checkpoint
        for checkpoint in state["completed_checkpoints"]
        if isinstance(checkpoint, dict)
        and checkpoint.get("id") == "fl1_i2_synthetic_implementation_evidence"
    ]
    if implementation_checkpoints != [
        {
            "id": "fl1_i2_synthetic_implementation_evidence",
            "result": "fourteen_delivery_gates_closed_and_executable_contract_registered",
            "fingerprint": FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,
        }
    ]:
        raise DocumentationStateError("fl1_i2_implementation_checkpoint_invalid")

    expected_findings = [
        (1, "PRRT_kwDOSTBMB86X4OUS", "P1", "Scrub Git control variables before trusted invocations", "scripts/fl1_i1_runtime_context.py", "git_control_environment_sanitization", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (2, "PRRT_kwDOSTBMB86X4OUW", "P1", "Validate the parent-observed child identity", "scripts/phase_contracts/fl1_i1_contract.py", "parent_observed_child_identity_claim_boundary", "claim_boundary_local_evidence_not_tamper_resistant_attestation"),
        (3, "PRRT_kwDOSTBMB86X4OUa", "P1", "Recheck recall attributes before final resolution", "scripts/fl1_i1_operation_gateway.py", "cloud_attribute_and_final_open_object_consistency", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (4, "PRRT_kwDOSTBMB86X4OUe", "P1", "Allow interrupted attempts before corrupt-media closure", "scripts/phase_contracts/fl1_i1_contract.py", "interrupted_attempt_corrupt_media_accounting", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (5, "PRRT_kwDOSTBMB86X4OUk", "P2", "Enforce the deadline around blocking file operations", "scripts/fl1_i1_operation_gateway.py", "interruptible_blocking_file_operations", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (6, "PRRT_kwDOSTBMB86X4OUq", "P1", "Bind the receipt to one unchanged HEAD", "scripts/fl1_i1_validation_receipt.py", "validation_receipt_same_head_before_after", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (7, "PRRT_kwDOSTBMB86X4OUy", "P1", "Re-derive the adapter policy during contract validation", "scripts/phase_contracts/fl1_i1_contract.py", "adapter_policy_rederived_from_trusted_config", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (8, "PRRT_kwDOSTBMB86X4OU1", "P2", "Stop at the configured failure maximum", "scripts/fl1_i1_inventory.py", "failure_maximum_stop_boundary", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (9, "PRRT_kwDOSTBMB86X4OU5", "P1", "Pin the frozen remediation commit and tree", "scripts/check_documentation_state.py", "frozen_i1_evidence_commit_tree_binding", "closed_in_current_governance_pr"),
        (10, "PRRT_kwDOSTBMB86X4OVA", "P1", "Reject CI authority in documentation state", "scripts/check_documentation_state.py", "documentation_ci_authority_fail_closed", "closed_in_current_governance_pr"),
        (11, "PRRT_kwDOSTBMB86X4OVI", "P1", "Include a change identity in file signatures", "scripts/fl1_i1_inventory.py", "windows_file_identity_and_change_identity", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (12, "PRRT_kwDOSTBMB86X4OVM", "P1", "Reject hard-linked files that alias protected data", "scripts/fl1_i1_inventory.py", "hard_link_reparse_and_alias_policy", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (13, "PRRT_kwDOSTBMB86X4OVT", "P1", "Confine private artifact reads as well as writes", "scripts/fl1_i1_operation_gateway.py", "task_owned_nofollow_private_artifact_reads", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (14, "PRRT_kwDOSTBMB86X4OVW", "P1", "Enumerate directories through a verified no-follow handle", "scripts/fl1_i1_operation_gateway.py", "handle_based_directory_enumeration", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (15, "PRRT_kwDOSTBMB86X4OVa", "P1", "Reconcile intents from ended failed invocations", "scripts/fl1_i1_inventory.py", "ended_failed_invocation_intent_recovery", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (16, "PRRT_kwDOSTBMB86X4OVe", "P2", "Validate media structure beyond boundary markers", "scripts/fl1_i1_inventory.py", "bounded_media_structure_validation", "must_close_during_i2_before_i2_completion_merge_or_i3"),
        (17, "PRRT_kwDOSTBMB86X4OVk", "P2", "Handle runtime-context failures in the scanner CLI", "scripts/fl1_i1_inventory.py", "privacy_safe_cli_runtime_context_error_envelope", "must_close_during_i2_before_i2_completion_merge_or_i3"),
    ]
    actual_findings = state["terminal_review_findings"]
    expected_payload = [
        {
            "number": number,
            "thread_id": thread_id,
            "severity": severity,
            "title": title,
            "path": path,
            "code": code,
            "classification": (
                "closed_in_fl1_i2_synthetic_implementation_evidence"
                if number in {1, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 16, 17}
                else classification
            ),
        }
        for number, thread_id, severity, title, path, code, classification in expected_findings
    ]
    if actual_findings != expected_payload:
        raise DocumentationStateError("fl1_i2_terminal_review_findings_invalid")
    if state.get("artifact_lifecycle") != [
        "synthetic_pre_real_hardening_implementation",
        "task_owned_private_evidence",
        "public_safe_contract_projection",
    ]:
        raise DocumentationStateError("fl1_i2_artifact_lifecycle_invalid")


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
    if not HEX40.fullmatch(str(state["approved_planning_head"])):
        raise DocumentationStateError("approved_planning_head_invalid")
    if not HEX40.fullmatch(str(state["approved_planning_tree"])):
        raise DocumentationStateError("approved_planning_tree_invalid")
    if state["pr_number"] is not None and (
        isinstance(state["pr_number"], bool)
        or not isinstance(state["pr_number"], int)
        or state["pr_number"] <= 0
    ):
        raise DocumentationStateError("pr_number_invalid")
    if not isinstance(state["draft"], bool):
        raise DocumentationStateError("draft_must_be_boolean")
    for key in (
        "target_met",
        "safe_to_merge",
        "route_approved",
        "next_phase_started",
        "planning_authorized",
        "planning_completed",
        "planning_approved",
    ):
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
        "public_safe_synthetic_implementation_no_private_proof_payloads_or_paths"
    ):
        raise DocumentationStateError("public_state_boundary_invalid")

    if state["phase_id"] == "SCV2-FL1-I2":
        _validate_fl1_i2_state(state)
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
        raise DocumentationStateError("fl1_i2_authorizes_execution")
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
    forbidden_positive_authority = any(
        state[field] for field in ("target_met", "route_approved")
    ) or any(
        state["planning_boundary"][field]
        for field in (
            "real_inventory_started",
            "real_source_inventory_authorized",
            "source_root_access_authorized",
            "data_execution_authorized",
            "database_access_authorized",
            "app_storage_write_authorized",
            "import_authorized",
            "classification_or_tagging_execution_authorized",
            "provider_or_llm_authorized",
            "media_authorized",
            "stable_replay_authorized",
            "production_authorized",
        )
    ) or state["protected_evidence"]["machine_verifiable_ci"] is not False
    if forbidden_positive_authority:
        raise DocumentationStateError("unauthorized_fl1_i2_authority_claim_present")
    for pattern in PUBLIC_FORBIDDEN:
        if pattern.search(serialized):
            raise DocumentationStateError(f"public_state_redaction_failure:{pattern.pattern}")


def _trusted_git_environment(
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    """Compatibility wrapper for the shared Git control scrubber."""

    return _shared_trusted_git_environment(inherited)


def _casefold_deduplicated_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Return paths in first-seen order with Windows-style case deduplication."""

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def _read_windows_registry_path(
    registry: Any,
    *,
    key_path: str,
    value_name: str,
    view: int,
) -> Path | None:
    """Read one absolute Windows path from an explicit HKLM registry view."""

    try:
        handle = registry.OpenKey(
            registry.HKEY_LOCAL_MACHINE,
            key_path,
            0,
            registry.KEY_READ | view,
        )
    except (OSError, TypeError, ValueError):
        return None
    try:
        value, _value_type = registry.QueryValueEx(handle, value_name)
    except (OSError, TypeError, ValueError):
        return None
    finally:
        try:
            registry.CloseKey(handle)
        except OSError:
            pass
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or not PureWindowsPath(normalized).is_absolute():
        return None
    return Path(normalized)


def _windows_system_git_roots(
    *,
    registry: Any | None = None,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Compatibility wrapper for shared OS-backed HKLM discovery."""

    roots, program_files = _shared_windows_system_git_roots(registry=registry)
    return tuple(Path(value) for value in roots), tuple(
        Path(value) for value in program_files
    )


def _windows_trusted_git_candidates(
    *,
    git_install_roots: Iterable[Path],
    program_files_roots: Iterable[Path],
) -> tuple[Path, ...]:
    """Compatibility wrapper for shared bounded candidate generation."""

    return tuple(
        _shared_windows_trusted_git_candidates(
            git_install_roots=git_install_roots,
            program_files_roots=program_files_roots,
        )
    )


def _trusted_git_candidates(
    *,
    platform_name: str | None = None,
    windows_location_provider: WindowsGitLocationProvider | None = None,
) -> tuple[Path, ...]:
    """Return bounded system candidates without consulting caller environment."""

    provider = windows_location_provider or _windows_system_git_roots
    return tuple(
        _shared_trusted_git_candidates(
            platform_name=platform_name,
            windows_location_provider=provider,
        )
    )


def _trusted_git_executable(
    *,
    root: Path = ROOT,
    platform_name: str | None = None,
    windows_location_provider: WindowsGitLocationProvider | None = None,
) -> Path:
    """Resolve Git from trusted system locations, never from caller PATH."""

    try:
        return _shared_resolve_trusted_git_executable(
            repo_root=root,
            platform_name=platform_name,
            windows_location_provider=windows_location_provider,
        ).path
    except TrustedGitError as exc:
        raise DocumentationStateError(str(exc)) from exc


def _run_trusted_git(
    arguments: list[str],
    *,
    root: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    """Run a non-interactive repository-bound Git command without caller config."""

    try:
        return run_trusted_git_text(root, arguments)
    except TrustedGitError as exc:
        raise DocumentationStateError(str(exc)) from exc


def _validate_fl1_i2_projection_paths(paths: Iterable[str]) -> None:
    """Fail closed unless every post-plan path is governance projection only."""

    for raw_path in paths:
        try:
            path = validate_git_path(raw_path)
        except TrustedGitError as exc:
            raise DocumentationStateError(
                "fl1_i2_governance_projection_path_invalid"
            ) from exc
        if path not in FL1_I2_PROJECTION_ALLOWLIST:
            raise DocumentationStateError(
                f"fl1_i2_governance_projection_path_invalid:{path}"
            )


def _trusted_git_changed_paths(
    arguments: list[str],
    *,
    root: Path,
    error_code: str,
) -> tuple[str, ...]:
    try:
        result = run_trusted_git_bytes(root, arguments)
        if result.returncode != 0:
            raise DocumentationStateError(error_code)
        return decode_git_z_paths(result.stdout)
    except TrustedGitError as exc:
        raise DocumentationStateError(error_code) from exc


def _validate_fl1_i2_projection_history(*, root: Path) -> None:
    """Audit every parent edge in the immutable plan-to-projection history."""

    projection_tree = _run_trusted_git(
        ["rev-parse", f"{FL1_I2_PLANNING_PROJECTION_HEAD}^{{tree}}"], root=root
    )
    if (
        projection_tree.returncode != 0
        or projection_tree.stdout.strip() != FL1_I2_PLANNING_PROJECTION_TREE
    ):
        raise DocumentationStateError("fl1_i2_projection_tree_mismatch")
    ancestry = _run_trusted_git(
        [
            "merge-base",
            "--is-ancestor",
            FL1_I2_APPROVED_PLANNING_HEAD,
            FL1_I2_PLANNING_PROJECTION_HEAD,
        ],
        root=root,
    )
    if ancestry.returncode != 0:
        raise DocumentationStateError("fl1_i2_projection_ancestry_invalid")
    revision_list = _run_trusted_git(
        [
            "rev-list",
            "--topo-order",
            "--reverse",
            FL1_I2_PLANNING_PROJECTION_HEAD,
            f"^{FL1_I2_APPROVED_PLANNING_HEAD}",
        ],
        root=root,
    )
    if revision_list.returncode != 0:
        raise DocumentationStateError("fl1_i2_projection_history_unavailable")
    commits = tuple(line for line in revision_list.stdout.splitlines() if line)
    if not commits or commits[-1] != FL1_I2_PLANNING_PROJECTION_HEAD:
        raise DocumentationStateError("fl1_i2_projection_history_invalid")
    for commit in commits:
        if not HEX40.fullmatch(commit):
            raise DocumentationStateError("fl1_i2_projection_history_invalid")
        parent_result = _run_trusted_git(
            ["show", "-s", "--format=%P", commit], root=root
        )
        if parent_result.returncode != 0:
            raise DocumentationStateError("fl1_i2_projection_history_unavailable")
        parents = tuple(parent_result.stdout.strip().split())
        if not parents:
            raise DocumentationStateError("fl1_i2_projection_history_invalid")
        for parent in parents:
            paths = _trusted_git_changed_paths(
                [
                    "diff",
                    "--no-ext-diff",
                    "--name-only",
                    "-z",
                    "--diff-filter=ACDMRTUXB",
                    parent,
                    commit,
                    "--",
                ],
                root=root,
                error_code="fl1_i2_projection_history_diff_unavailable",
            )
            _validate_fl1_i2_projection_paths(paths)


def _validate_fl1_i2_implementation_projection_history(*, root: Path) -> None:
    """Allow only the final governance/test projection after frozen I2 code."""

    ancestry = _run_trusted_git(
        [
            "merge-base",
            "--is-ancestor",
            FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,
            "HEAD",
        ],
        root=root,
    )
    if ancestry.returncode != 0:
        raise DocumentationStateError("fl1_i2_implementation_evidence_not_ancestor")
    revision_list = _run_trusted_git(
        [
            "rev-list",
            "--topo-order",
            "--reverse",
            "HEAD",
            f"^{FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD}",
        ],
        root=root,
    )
    if revision_list.returncode != 0:
        raise DocumentationStateError("fl1_i2_implementation_projection_unavailable")
    commits = tuple(line for line in revision_list.stdout.splitlines() if line)
    if not commits:
        raise DocumentationStateError("fl1_i2_implementation_projection_missing")
    for commit in commits:
        parent_result = _run_trusted_git(
            ["show", "-s", "--format=%P", commit], root=root
        )
        if parent_result.returncode != 0:
            raise DocumentationStateError("fl1_i2_implementation_projection_unavailable")
        parents = tuple(parent_result.stdout.strip().split())
        if not parents:
            raise DocumentationStateError("fl1_i2_implementation_projection_invalid")
        for parent in parents:
            paths = _trusted_git_changed_paths(
                [
                    "diff",
                    "--no-ext-diff",
                    "--no-renames",
                    "--name-only",
                    "-z",
                    parent,
                    commit,
                    "--",
                ],
                root=root,
                error_code="fl1_i2_implementation_projection_diff_unavailable",
            )
            _validate_fl1_i2_projection_paths(paths)


def _validate_fl1_i2_merge_topology(*, root: Path) -> None:
    tree = _run_trusted_git(
        ["rev-parse", f"{FL1_I2_PLANNING_MERGE_COMMIT}^{{tree}}"], root=root
    )
    if tree.returncode != 0 or tree.stdout.strip() != FL1_I2_PLANNING_MERGE_TREE:
        raise DocumentationStateError("fl1_i2_planning_merge_tree_mismatch")
    parents = _run_trusted_git(
        ["show", "-s", "--format=%P", FL1_I2_PLANNING_MERGE_COMMIT], root=root
    )
    if (
        parents.returncode != 0
        or tuple(parents.stdout.strip().split()) != FL1_I2_PLANNING_MERGE_PARENTS
    ):
        raise DocumentationStateError("fl1_i2_planning_merge_topology_invalid")


def _validate_fl1_i2_owner_acceptance_git(
    state: dict[str, Any],
    *,
    root: Path,
) -> None:
    """Bind owner acceptance to the exact plan and governance-only descendants."""

    if (
        state.get("approved_planning_head") != FL1_I2_APPROVED_PLANNING_HEAD
        or state.get("approved_planning_tree") != FL1_I2_APPROVED_PLANNING_TREE
    ):
        raise DocumentationStateError("fl1_i2_approved_planning_binding_invalid")

    planning_object = _run_trusted_git(
        ["cat-file", "-e", f"{FL1_I2_APPROVED_PLANNING_HEAD}^{{commit}}"],
        root=root,
    )
    if planning_object.returncode != 0:
        raise DocumentationStateError("fl1_i2_approved_planning_object_missing")
    planning_tree = _run_trusted_git(
        ["rev-parse", f"{FL1_I2_APPROVED_PLANNING_HEAD}^{{tree}}"],
        root=root,
    )
    if planning_tree.returncode != 0:
        raise DocumentationStateError("fl1_i2_approved_planning_object_missing")
    if planning_tree.stdout.strip() != FL1_I2_APPROVED_PLANNING_TREE:
        raise DocumentationStateError("fl1_i2_approved_planning_tree_mismatch")

    ancestor = _run_trusted_git(
        [
            "merge-base",
            "--is-ancestor",
            FL1_I2_APPROVED_PLANNING_HEAD,
            FL1_I2_PLANNING_PROJECTION_HEAD,
        ],
        root=root,
    )
    if ancestor.returncode != 0:
        raise DocumentationStateError("fl1_i2_approved_planning_not_ancestor")

    _validate_fl1_i2_projection_history(root=root)
    _validate_fl1_i2_merge_topology(root=root)
    merge_ancestor = _run_trusted_git(
        [
            "merge-base",
            "--is-ancestor",
            FL1_I2_PLANNING_MERGE_COMMIT,
            "HEAD",
        ],
        root=root,
    )
    if merge_ancestor.returncode != 0:
        raise DocumentationStateError("fl1_i2_planning_merge_not_ancestor")


def validate_git_ancestry(
    state: dict[str, Any],
    *,
    root: Path = ROOT,
    implementation_evidence: dict[str, Any] | None = None,
) -> None:
    base = str(state["accepted_mainline_base"])
    implementation = str(state["implementation_evidence_head"])
    expected_commit_trees: dict[str, str] = {}
    if state.get("phase_id") == "SCV2-FL1-I2":
        expected_commit_trees = {
            str(state["previous_phase_final_head"]): str(
                state["previous_phase_final_tree"]
            ),
            str(
                state["protected_evidence"][
                    "previous_phase_implementation_evidence_head"
                ]
            ): str(
                state["protected_evidence"][
                    "previous_phase_implementation_evidence_tree"
                ]
            ),
            implementation: str(
                state["protected_evidence"]["fl1_i2_implementation_evidence_tree"]
            ),
            base: FL1_I2_PLANNING_MERGE_TREE,
        }
    for commit, expected_tree in expected_commit_trees.items():
        tree_result = _run_trusted_git(
            ["rev-parse", f"{commit}^{{tree}}"], root=root
        )
        if tree_result.returncode != 0:
            raise DocumentationStateError("frozen_i1_evidence_object_missing")
        if tree_result.stdout.strip() != expected_tree:
            raise DocumentationStateError("frozen_i1_evidence_tree_mismatch")
    base_object = _run_trusted_git(
        ["cat-file", "-e", f"{base}^{{commit}}"], root=root
    )
    if base_object.returncode != 0:
        raise DocumentationStateError("accepted_mainline_base_object_missing")
    implementation_object = _run_trusted_git(
        ["cat-file", "-e", f"{implementation}^{{commit}}"], root=root
    )
    if implementation_object.returncode != 0:
        raise DocumentationStateError("implementation_evidence_object_missing")
    base_check = _run_trusted_git(
        ["merge-base", "--is-ancestor", base, "HEAD"], root=root
    )
    if base_check.returncode != 0:
        raise DocumentationStateError("accepted_mainline_base_not_ancestor_of_head")

    if state.get("phase_id") == "SCV2-FL1-I2":
        _validate_fl1_i2_owner_acceptance_git(state, root=root)

    implementation_check = _run_trusted_git(
        ["merge-base", "--is-ancestor", implementation, "HEAD"], root=root
    )
    if implementation_check.returncode == 0:
        if state.get("phase_id") == "SCV2-FL1-I2":
            _validate_fl1_i2_implementation_projection_history(root=root)
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


def _render_handoff_legacy(state: dict[str, Any]) -> str:
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
        f"- Implementation evidence HEAD: `{state['implementation_evidence_head']}` (status: `{state.get('implementation_evidence_status', 'current')}`; frozen: `{str(state['protected_evidence']['fl1_i1_implementation_evidence_frozen']).lower()}`).",
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


def render_handoff(state: dict[str, Any]) -> str:
    """Render the complete public-safe I2 planning handoff."""

    blocker = state["active_blocker"]
    boundary = state["planning_boundary"]
    if state["pr_number"] is None:
        pr_label = "PR pending creation"
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
        f"- Phase: `{state['phase_id']}` - {state['phase_title']}.",
        f"- Repository / PR: `{state['repository']}` / {pr_label}.",
        f"- Branch: `{state['branch']}`.",
        f"- Accepted mainline base: `{state['accepted_mainline_base']}`.",
        f"- Previous phase: `{state['previous_phase']}` / PR #144; status: `{state['previous_phase_status']}`.",
        f"- Previous final HEAD/tree: `{state['previous_phase_final_head']}` / `{state['previous_phase_final_tree']}`; merge commit: `{state['previous_phase_merge_commit']}`.",
        f"- Previous I1 implementation evidence HEAD/tree: `{state['protected_evidence']['previous_phase_implementation_evidence_head']}` / `{state['protected_evidence']['previous_phase_implementation_evidence_tree']}` (frozen: `true`; accepted scope: `{state['previous_phase_accepted_scope']}`).",
        f"- Current I2 implementation evidence HEAD/tree: `{state['implementation_evidence_head']}` / `{state['protected_evidence']['fl1_i2_implementation_evidence_tree']}`; contract: `{state['protected_evidence']['fl1_i2_contract_id']}`; fourteen delivery gates closed in synthetic evidence.",
        f"- PR #146 bounded correction: rejected evidence `{state['protected_evidence']['fl1_i2_superseded_evidence_head']}` / `{state['protected_evidence']['fl1_i2_superseded_evidence_tree']}` is superseded by owner adjudication of review `{state['protected_evidence']['fl1_i2_bounded_correction_review_id']}` and `{len(state['protected_evidence']['fl1_i2_bounded_correction_thread_ids'])}` accepted findings; one follow-up Codex review is authorized.",
        f"- Terminal review: `{state['previous_phase_terminal_review_id']}` at `{state['previous_phase_final_head']}`; findings: `{state['previous_phase_terminal_review_findings']}` (`P1={state['previous_phase_terminal_review_p1']}`, `P2={state['previous_phase_terminal_review_p2']}`); GitHub checks: `{state['previous_phase_github_checks']}`.",
        f"- Status: `{state['current_status']}`.",
        f"- `target_met={str(state['target_met']).lower()}`; `safe_to_merge={str(state['safe_to_merge']).lower()}`; `route_approved={str(state['route_approved']).lower()}`.",
        f"- Planning: `authorized={str(state['planning_authorized']).lower()}`, `completed={str(state['planning_completed']).lower()}`, `approved={str(state['planning_approved']).lower()}`; `manual_acceptance_status={state['manual_acceptance_status']}`.",
        f"- Owner-approved planning HEAD/tree: `{state['approved_planning_head']}` / `{state['approved_planning_tree']}`.",
        f"- Owner evidence: PR `#{state['protected_evidence']['approved_planning_pr_number']}`, review `{state['protected_evidence']['approved_planning_review_id']}`, thread `{state['protected_evidence']['approved_planning_thread_id']}`, comment `{state['protected_evidence']['approved_planning_comment_id']}`; the P1 exact-revision finding closes in this governance projection binding.",
        f"- Planning owner acceptance / current implementation merge authorization: `{str(boundary['owner_acceptance_valid']).lower()}/{str(boundary['merge_authorized']).lower()}`.",
        f"- I2 implementation / real-source authorization: `{str(boundary['implementation_authorized']).lower()}/{str(boundary['real_source_inventory_authorized']).lower()}`; route scope: `{state['route_scope']}`.",
        "",
        "## Completed Checkpoints",
        "",
    ]
    for checkpoint in state["completed_checkpoints"]:
        suffix = f" - `{checkpoint['fingerprint']}`" if checkpoint.get("fingerprint") else ""
        lines.append(f"- `{checkpoint['id']}`: `{checkpoint['result']}`{suffix}.")
    lines.extend(
        [
            "",
            "## PR #144 Terminal Review Use-Before Classification",
            "",
            "All 17 findings remain historical audit records. No PR #144 thread was replied to, resolved, or reopened.",
            "",
        ]
    )
    for finding in state["terminal_review_findings"]:
        lines.append(
            f"- #{finding['number']} [{finding['severity']}] {finding['title']} - `{finding['code']}`; `{finding['classification']}`."
        )
    lines.extend(
        [
            "",
            "## Current Gate And Boundary",
            "",
            f"- Gate: `{blocker['code']}` ({blocker['scope']}).",
            f"- Resolution: {blocker['resolution']}",
            f"- Planning only: `{str(boundary['planning_only']).lower()}`; implementation/data/production authorization: `{str(boundary['implementation_authorized']).lower()}/{str(boundary['data_execution_authorized']).lower()}/{str(boundary['production_authorized']).lower()}`.",
            f"- Existing database/real inventory/provider-or-LLM/media authorization: `{str(boundary['database_access_authorized']).lower()}/{str(boundary['real_source_inventory_authorized']).lower()}/{str(boundary['provider_or_llm_authorized']).lower()}/{str(boundary['media_authorized']).lower()}`; projected external cost: `${boundary['projected_external_cost_usd']}`.",
            f"- Public evidence boundary: `trust_level={state['protected_evidence']['validation_receipt_trust_level']}`, `machine_verifiable_ci={str(state['protected_evidence']['machine_verifiable_ci']).lower()}`, `github_checks={state['protected_evidence']['github_checks_observed']}`.",
            f"- Network truth: external source/provider/model/media data-plane operations = `{state['protected_evidence']['external_data_plane_network_operation_count']}`; authorized Git/GitHub governance control-plane operations occurred = `{str(state['protected_evidence']['authorized_git_github_governance_control_plane_operations_occurred']).lower()}`.",
            "- Parent-observed child identity remains local provenance, not OS/kernel/TPM/remote/CI or tamper-resistant attestation.",
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
    for debt in state["deferred_debt"]:
        requirements = "; ".join(debt["requirements"])
        lines.append(
            f"- `{debt['id']}` - owner: {debt['owner']}; due before: `{debt['due_before']}`; {debt['reason']} Requirements: {requirements}."
        )
    lines.extend([f"Updated: `{state['updated_at']}`.", ""])
    return "\n".join(lines)


def check_handoff(state: dict[str, Any], *, path: Path = HANDOFF_PATH) -> None:
    expected = render_handoff(state)
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        raise DocumentationStateError("generated_handoff_drift")
    line_count = len(actual.splitlines())
    if not 55 <= line_count <= 115:
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
