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
from scripts.scv2_px1_validation_receipt import (
    Px1ValidationReceiptError,
    validate_px1_evidence_carry_forward,
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
FL1_I2_STATUS = (
    "fl1_i2_pr146_final_owner_adjudicated_correction_ready_for_direct_owner_merge_audit"
)
FL1_I2_IMPLEMENTATION_DECISION_ID = (
    "owner_authorized_scv2_fl1_i2_synthetic_pre_real_hardening_20260817"
)
FL1_I2_POST_MERGE_REVIEW_ID = 4927462216
FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD = "9aab3e31f5223e0c689046b5c5c61f21268f840c"
FL1_I2_IMPLEMENTATION_EVIDENCE_TREE = "9119d489800c0b40c5586a9aa4ceb89d34f93e5c"
FL1_I2_PRIOR_POST_TERMINAL_EVIDENCE_HEAD = (
    "46bc25363531d9fb1fb3995d0eb361abab84a016"
)
FL1_I2_PRIOR_POST_TERMINAL_EVIDENCE_TREE = (
    "476bf43b0ed771e8be33a099997019ed2d8b61fc"
)
FL1_I2_PRE_RECEIPT_EVIDENCE_HEAD = "46d38cff259823588863e6ef36dbd0ed886edf35"
FL1_I2_PRE_RECEIPT_EVIDENCE_TREE = "6322959f96bb55ca5a5de133c07dd3e93172087f"
FL1_I2_INTERMEDIATE_POST_TERMINAL_PROJECTION_HEAD = "85407b8fd29652c5e2999c77552bf5d0ab2e1f14"
FL1_I2_INTERMEDIATE_POST_TERMINAL_PROJECTION_TREE = "1d2c1243b14cfcda893840ae40bebb0c543284cc"
FL1_I2_POST_TERMINAL_PROJECTION_HEAD = "7b258e97c3267e933c370b2fd1a526216aabb721"
FL1_I2_POST_TERMINAL_PROJECTION_TREE = "afd5eaf2e701aac174c482f82fb64fb3d319539d"
FL1_I2_PRE_TERMINAL_EVIDENCE_HEAD = "4fb6a6c9133c6c22d6e8d97cd800db25a8fed2a5"
FL1_I2_PRE_TERMINAL_EVIDENCE_TREE = "e3dc5d6d6047b195964123396bf3b814665010b7"
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
FL1_I2_FINAL_REJECTED_HEAD = "441d0c1bb1d8d0823b6f24c31accf44e068509f2"
FL1_I2_FINAL_REJECTED_TREE = "83f28d1f0dbb50f4ac0331b4c14cc046383eb6f7"
FL1_I2_FINAL_REVIEW_ID = 4952516658
FL1_I2_FINAL_CONVERGENCE_DECISION_ID = (
    "owner_authorized_scv2_fl1_i2_pr146_final_convergence_20260818"
)
FL1_I2_FINAL_THREAD_IDS = (
    "PRRT_kwDOSTBMB86Z0xo5",
    "PRRT_kwDOSTBMB86Z0xo6",
    "PRRT_kwDOSTBMB86Z0xo7",
    "PRRT_kwDOSTBMB86Z0xo-",
    "PRRT_kwDOSTBMB86Z0xpB",
    "PRRT_kwDOSTBMB86Z0xpD",
    "PRRT_kwDOSTBMB86Z0xpG",
)
FL1_I2_TERMINAL_REJECTED_HEAD = "ef828853a0f8b748aeb228b1e10ec317cafa9f5d"
FL1_I2_TERMINAL_REJECTED_TREE = "9cc1670dcddb1ff24f1afcfc4cded91a9fc9ae72"
FL1_I2_POST_TERMINAL_REVIEW_ID = 4961359578
FL1_I2_POST_TERMINAL_DECISION_ID = (
    "owner_authorized_scv2_fl1_i2_pr146_post_terminal_bounded_correction_20260818"
)
FL1_I2_POST_TERMINAL_FINDINGS = (
    ("PRRT_kwDOSTBMB86aHgQq", "P1"),
    ("PRRT_kwDOSTBMB86aHgQu", "P1"),
    ("PRRT_kwDOSTBMB86aHgQy", "P2"),
    ("PRRT_kwDOSTBMB86aHgQ0", "P1"),
    ("PRRT_kwDOSTBMB86aHgQ3", "P2"),
    ("PRRT_kwDOSTBMB86aHgQ8", "P2"),
    ("PRRT_kwDOSTBMB86aHgRC", "P1"),
    ("PRRT_kwDOSTBMB86aHgRG", "P1"),
    ("PRRT_kwDOSTBMB86aHgRK", "P1"),
)
FL1_I2_FINAL_OWNER_REJECTED_HEAD = "d4478660df1f11b1c8d3ceba1af70f8635542a9d"
FL1_I2_FINAL_OWNER_REJECTED_TREE = "113280a8697e6bef3cb9e4292a042c2d46b1f025"
FL1_I2_FINAL_OWNER_REVIEW_ID = 4963026941
FL1_I2_FINAL_OWNER_DECISION_ID = (
    "owner_authorized_scv2_fl1_i2_pr146_final_owner_adjudicated_"
    "no_rereview_correction_20260826"
)
FL1_I2_FINAL_OWNER_DECISION = (
    "SCV2_FL1_I2_PR146_FINAL_OWNER_ADJUDICATED_NO_REREVIEW_"
    "CORRECTION_AUTHORIZED"
)
FL1_I2_FINAL_OWNER_FINDINGS = (
    (
        "PRRT_kwDOSTBMB86aLCo0",
        "P1",
        "accept_and_require_fix",
        "closed_in_final_owner_adjudicated_implementation_evidence",
    ),
    (
        "PRRT_kwDOSTBMB86aLCo6",
        "P1",
        "accept_and_require_fix",
        "closed_in_final_owner_adjudicated_implementation_evidence",
    ),
    (
        "PRRT_kwDOSTBMB86aLCpS",
        "P1",
        "accept_and_require_fix",
        "closed_in_final_owner_adjudicated_implementation_evidence",
    ),
    (
        "PRRT_kwDOSTBMB86aLCpZ",
        "P2",
        "accept_and_require_fix",
        "closed_in_final_owner_adjudicated_implementation_evidence",
    ),
    (
        "PRRT_kwDOSTBMB86aLCpA",
        "P2",
        "accept_and_require_safe_downgrade",
        "closed_by_avif_explicit_unsupported_projection",
    ),
    (
        "PRRT_kwDOSTBMB86aLCpL",
        "P2",
        "accept_and_require_safe_downgrade",
        "closed_by_gif_explicit_unsupported_projection",
    ),
    (
        "PRRT_kwDOSTBMB86aLCpE",
        "P1",
        "defer_with_exact_due_gate",
        "deferred_nonblocking_pre_posix_or_untrusted_environment_execution",
    ),
    (
        "PRRT_kwDOSTBMB86aLCpI",
        "P1",
        "defer_with_exact_due_gate",
        "deferred_nonblocking_pre_ci_or_tamper_resistant_receipt",
    ),
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
FL1_I2_BLOCKER = "pending_fl1_i2_final_direct_owner_merge_audit"
FL1_I2_MANUAL_STATUS = "pending_fl1_i2_final_direct_owner_merge_audit"
FL1_I2_ROUTE_SCOPE = (
    "SCV2-FL1-I2 synthetic pre-real hardening implementation using only "
    "adversarial newly created temporary fixtures; no real-source execution"
)
FL1_I2_PROJECTION_ALLOWLIST = frozenset(
    {
        "README.md",
        "docs/current-handoff.md",
        "docs/phase-contracts.md",
        "docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md",
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

SCV2_PX1_BRANCH = "codex/scv2-px1-pixiv-metadata-consolidation"
SCV2_PX1_ACCEPTED_MAIN = "8a825bcdd12f76d1c2c396b7039bd9e326cd63dc"
SCV2_PX1_ACCEPTED_MAIN_TREE = "9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71"
SCV2_PX1_PR146_ACCEPTED_HEAD = "914d746c3548241a99333393daa88caefd8b2337"
SCV2_PX1_PR146_FINAL_REVIEW_ID = 5031131564
SCV2_PX1_CONTRACT_ID = "scv2_px1_pixiv_metadata_consolidation_contract_v1"
SCV2_PX1_PUBLIC_SCHEMA = "violet.scv2-px1-pixiv-metadata-summary.v1"
SCV2_PX1_BLOCKER = "pending_scv2_px1_final_owner_merge_audit"
SCV2_PX1_IN_PROGRESS_BLOCKER = "scv2_px1_bounded_correction_in_progress"
SCV2_PX1_READY_STATUS = (
    "SCV2_PX1_BOUNDED_CORRECTION_READY_FOR_FINAL_OWNER_MERGE_AUDIT"
)
SCV2_PX1_IN_PROGRESS_STATUS = "scv2_px1_implementation_in_progress"
SCV2_PX1_FINAL_REVIEW_DUE_GATES = {
    "PRRT_kwDOSTBMB86cezeV": "FL1_I2_LISTED_MEMBER_VALIDATION_GATE",
    "PRRT_kwDOSTBMB86cezeZ": "FL1_I2_EVENT_TIME_LOWER_BOUND_GATE",
    "PRRT_kwDOSTBMB86cezef": "FL1_I2_JPEG_CONTENT_AUTHORITY_GATE",
    "PRRT_kwDOSTBMB86cezel": "FL1_I2_VP8_CONTENT_AUTHORITY_GATE",
    "PRRT_kwDOSTBMB86cezeq": "FL1_I2_INITIAL_ENUMERATION_BUDGET_GATE",
    "PRRT_kwDOSTBMB86cezet": "FL1_I2_OPERATION_ADMISSION_CAP_GATE",
    "PRRT_kwDOSTBMB86cezey": "FL1_I2_EVIDENCE_PREPARSE_BUDGET_GATE",
    "PRRT_kwDOSTBMB86ceze1": "FL1_I2_MAX_DEPTH_REDERIVATION_GATE",
    "PRRT_kwDOSTBMB86ceze9": "FL1_I2_NONNEGATIVE_BYTE_ACCOUNTING_GATE",
    "PRRT_kwDOSTBMB86cezfD": "FL1_I2_FAILED_RECEIPT_COMPLETION_GATE",
}
SCV2_PX1_REQUIRED_DEFERRED_GATES = frozenset(
    {
        *SCV2_PX1_FINAL_REVIEW_DUE_GATES.values(),
        "FL1_I2_DYNAMIC_LOADER_ENVIRONMENT_POLICY",
        "FL1_I2_VENV_FULL_PYTHON_SUPPLY_CHAIN_BINDING",
        "FL1_I3_REAL_SOURCE_SCOPE_GATE",
        "PARENT_OBSERVED_CHILD_IDENTITY_CLAIM_BOUNDARY",
        "VALIDATION_RECEIPT_GATE",
        "OWNER_AUTHORITY_GATE",
        "POSIX_LEDGER_DURABILITY_GATE",
        "STABLE_REPLAY_GATE",
        "SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE",
    }
)
SCV2_PX1_EXPECTED_AUTHORITIES = {
    "px1_implementation_authorized": True,
    "repository_read_authorized": True,
    "synthetic_fixture_execution_authorized": True,
    "task_owned_temporary_database_authorized": True,
    "isolated_worktree_authorized": True,
    "branch_commit_push_authorized": True,
    "one_normal_pull_request_authorized": True,
    "documentation_state_transition_authorized": True,
    "merge_authorized": False,
    "real_source_inventory_authorized": False,
    "source_root_or_icloud_access_authorized": False,
    "existing_database_access_authorized": False,
    "app_storage_write_authorized": False,
    "real_pixiv_or_gallery_dl_network_execution_authorized": False,
    "provider_credentials_authorized": False,
    "media_or_thumbnail_download_authorized": False,
    "import_authorized": False,
    "classification_or_tagging_execution_on_user_data_authorized": False,
    "llm_or_external_model_authorized": False,
    "server_browser_or_e2e_authorized": False,
    "production_authorized": False,
    "full_library_import_authorized": False,
}

SCV2_PX2_BRANCH = "codex/scv2-px2-deterministic-pixiv-clustering"
SCV2_PX2_ACCEPTED_MAIN = "5a8efdaf954ab95bd82f95464af31a7fd0873e5e"
SCV2_PX2_ACCEPTED_MAIN_TREE = "480d6a548e6276afeccf49ec75a73d7389b995fe"
SCV2_PX2_PR147_ACCEPTED_HEAD = "15cbb0c71d4b4c6e5ea32c5eb99a1f56e561d65a"
SCV2_PX2_PR147_ACCEPTED_TREE = "480d6a548e6276afeccf49ec75a73d7389b995fe"
SCV2_PX2_PR147_MERGE_PARENTS = (
    "8a825bcdd12f76d1c2c396b7039bd9e326cd63dc",
    SCV2_PX2_PR147_ACCEPTED_HEAD,
)
SCV2_PX2_CONTRACT_ID = "scv2_px2_deterministic_pixiv_clustering_contract_v1"
SCV2_PX2_PUBLIC_SCHEMA = (
    "violet.scv2-px2-pixiv-source-concept-cluster-result.v1"
)
SCV2_PX2_IN_PROGRESS_STATUS = "scv2_px2_implementation_in_progress"
SCV2_PX2_READY_STATUS = (
    "SCV2_PX2_DETERMINISTIC_PIXIV_CLUSTERING_READY_FOR_OWNER_MERGE_AUDIT"
)
SCV2_PX2_IN_PROGRESS_BLOCKER = "scv2_px2_implementation_in_progress"
SCV2_PX2_READY_BLOCKER = "pending_scv2_px2_owner_merge_audit"
SCV2_PX2_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST = frozenset(
    {
        "docs/current-handoff.md",
        "docs/phase-contracts.md",
        "docs/project-roadmap.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/state/current-phase.json",
    }
)
SCV2_PX2_EXPECTED_AUTHORITIES = {
    "px2_start_authorized": True,
    "px2_synthetic_implementation_authorized": True,
    "repository_read_authorized": True,
    "synthetic_fixture_execution_authorized": True,
    "task_owned_temporary_database_authorized": True,
    "isolated_worktree_authorized": True,
    "branch_commit_push_authorized": True,
    "one_normal_pull_request_authorized": True,
    "documentation_state_transition_authorized": True,
    "px2_merge_authorized": False,
    "real_source_inventory_authorized": False,
    "source_root_or_icloud_access_authorized": False,
    "existing_database_access_authorized": False,
    "app_storage_write_authorized": False,
    "real_pixiv_or_gallery_dl_network_execution_authorized": False,
    "provider_credentials_authorized": False,
    "media_or_thumbnail_download_authorized": False,
    "migration_authorized": False,
    "import_authorized": False,
    "classification_or_tagging_execution_on_user_data_authorized": False,
    "llm_or_external_model_authorized": False,
    "server_browser_or_e2e_authorized": False,
    "production_authorized": False,
    "full_library_import_authorized": False,
}

SCV2_PX3_BRANCH = "codex/scv2-px3-pixiv-product-integration"
SCV2_PX3_ACCEPTED_MAIN = "421e2989d274e2dc4492d5bccc10720dcfbbaa4f"
SCV2_PX3_ACCEPTED_MAIN_TREE = "507a223a9156ff2f9944524303419e85891812fa"
SCV2_PX3_PR148_ACCEPTED_HEAD = "bf8055af61c3a5d32155701ed7110db692047dba"
SCV2_PX3_PR148_ACCEPTED_TREE = SCV2_PX3_ACCEPTED_MAIN_TREE
SCV2_PX3_PR148_MERGE_PARENTS = (
    "5a8efdaf954ab95bd82f95464af31a7fd0873e5e",
    SCV2_PX3_PR148_ACCEPTED_HEAD,
)
SCV2_PX3_CONTRACT_ID = "scv2_px3_pixiv_product_integration_contract_v1"
SCV2_PX3_PUBLIC_SCHEMA = "violet.scv2-px3-pixiv-product-integration-result.v1"
SCV2_PX3_IN_PROGRESS_STATUS = "scv2_px3_product_integration_in_progress"
SCV2_PX3_READY_STATUS = (
    "SCV2_PX3_PIXIV_PRODUCT_INTEGRATION_READY_FOR_OWNER_ACCEPTANCE_AND_CONTROLLED_CANARY"
)
SCV2_PX3_IN_PROGRESS_BLOCKER = "scv2_px3_implementation_in_progress"
SCV2_PX3_CLOSURE_READY_STATUS = 'SCV2_PX3_FINAL_PRODUCT_CLOSURE_ACCEPTED_PENDING_EXPECTED_HEAD_MERGE'
SCV2_PX3_MERGED_STATUS = 'SCV2_PX3_MERGED_READY_FOR_CONTROLLED_CANARY'
SCV2_PX3_READY_BLOCKER = "pending_scv2_px3_owner_acceptance_and_controlled_canary"
SCV2_PX3_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST = frozenset(
    {
        "docs/current-handoff.md",
        "docs/phase-contracts.md",
        "docs/project-roadmap.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/state/current-phase.json",
    }
)
SCV2_PX3_EXPECTED_AUTHORITIES = {
    "px3_audit_design_and_implementation_authorized": True,
    "repository_migration_code_authorized": True,
    "task_owned_temporary_database_authorized": True,
    "synthetic_local_server_browser_e2e_authorized": True,
    "isolated_worktree_authorized": True,
    "branch_commit_push_authorized": True,
    "one_normal_pull_request_authorized": True,
    "documentation_state_transition_authorized": True,
    "px3_merge_authorized": False,
    "real_pixiv_network_execution_authorized": False,
    "provider_credentials_authorized": False,
    "real_source_or_icloud_access_authorized": False,
    "existing_database_or_app_storage_mutation_authorized": False,
    "user_data_import_authorized": False,
    "production_authorized": False,
    "full_library_import_authorized": False,
}


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
        "implementation_evidence_status": "current_i2_final_owner_adjudicated_evidence_frozen_pending_direct_owner_merge_audit",
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
        "fl1_i2_prior_post_terminal_evidence_head": FL1_I2_PRIOR_POST_TERMINAL_EVIDENCE_HEAD,
        "fl1_i2_prior_post_terminal_evidence_tree": FL1_I2_PRIOR_POST_TERMINAL_EVIDENCE_TREE,
        "fl1_i2_prior_post_terminal_evidence_status": "superseded_and_rejected_by_final_owner_review",
        "fl1_i2_pre_receipt_evidence_head": FL1_I2_PRE_RECEIPT_EVIDENCE_HEAD,
        "fl1_i2_pre_receipt_evidence_tree": FL1_I2_PRE_RECEIPT_EVIDENCE_TREE,
        "fl1_i2_pre_receipt_evidence_status": "superseded_by_validation_temp_cleanup_correction",
        "fl1_i2_intermediate_post_terminal_projection_head": FL1_I2_INTERMEDIATE_POST_TERMINAL_PROJECTION_HEAD,
        "fl1_i2_intermediate_post_terminal_projection_tree": FL1_I2_INTERMEDIATE_POST_TERMINAL_PROJECTION_TREE,
        "fl1_i2_intermediate_post_terminal_projection_status": "superseded_by_validation_temp_cleanup_correction",
        "fl1_i2_post_terminal_governance_projection_head": FL1_I2_POST_TERMINAL_PROJECTION_HEAD,
        "fl1_i2_post_terminal_governance_projection_tree": FL1_I2_POST_TERMINAL_PROJECTION_TREE,
        "fl1_i2_post_terminal_governance_projection_frozen": True,
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
        "fl1_i2_final_convergence_rejected_head": FL1_I2_FINAL_REJECTED_HEAD,
        "fl1_i2_final_convergence_rejected_tree": FL1_I2_FINAL_REJECTED_TREE,
        "fl1_i2_final_convergence_review_id": FL1_I2_FINAL_REVIEW_ID,
        "fl1_i2_final_convergence_thread_ids": list(FL1_I2_FINAL_THREAD_IDS),
        "fl1_i2_final_convergence_correction_authorized": True,
        "fl1_i2_one_terminal_followup_review_authorized": True,
        "fl1_i2_pre_terminal_evidence_head": FL1_I2_PRE_TERMINAL_EVIDENCE_HEAD,
        "fl1_i2_pre_terminal_evidence_tree": FL1_I2_PRE_TERMINAL_EVIDENCE_TREE,
        "fl1_i2_pre_terminal_evidence_status": "superseded_and_rejected_by_terminal_review",
        "fl1_i2_terminal_rejected_head": FL1_I2_TERMINAL_REJECTED_HEAD,
        "fl1_i2_terminal_rejected_tree": FL1_I2_TERMINAL_REJECTED_TREE,
        "fl1_i2_post_terminal_review_id": FL1_I2_POST_TERMINAL_REVIEW_ID,
        "fl1_i2_post_terminal_findings": [
            {
                "thread_id": thread_id,
                "severity": severity,
                "disposition": "accept_and_require_fix",
            }
            for thread_id, severity in FL1_I2_POST_TERMINAL_FINDINGS
        ],
        "fl1_i2_post_terminal_bounded_correction_authorized": True,
        "fl1_i2_one_post_terminal_review_authorized": True,
        "fl1_i2_final_owner_rejected_head": FL1_I2_FINAL_OWNER_REJECTED_HEAD,
        "fl1_i2_final_owner_rejected_tree": FL1_I2_FINAL_OWNER_REJECTED_TREE,
        "fl1_i2_final_owner_review_id": FL1_I2_FINAL_OWNER_REVIEW_ID,
        "fl1_i2_final_owner_findings": [
            {
                "thread_id": thread_id,
                "severity": severity,
                "owner_disposition": disposition,
                "classification": classification,
            }
            for thread_id, severity, disposition, classification in FL1_I2_FINAL_OWNER_FINDINGS
        ],
        "fl1_i2_final_owner_required_fix_count": 4,
        "fl1_i2_final_owner_safe_downgrade_count": 2,
        "fl1_i2_final_owner_deferred_count": 2,
        "fl1_i2_final_owner_correction_authorized": True,
        "fl1_i2_additional_codex_review_authorized": False,
        "fl1_i2_final_governance_projection_mode": "current_head_tree_derived_by_trusted_git",
        "fl1_i2_final_governance_projection_parent_head": FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,
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

    final_decisions = [
        decision
        for decision in state["owner_decisions"]
        if isinstance(decision, dict)
        and decision.get("id") == FL1_I2_FINAL_CONVERGENCE_DECISION_ID
    ]
    if final_decisions != [
        {
            "id": FL1_I2_FINAL_CONVERGENCE_DECISION_ID,
            "decision": (
                "Authorize the final additive convergence correction in PR #146 "
                "for seven accepted exact-head findings plus bounded recursive "
                "same-handle traversal and run-wide budget closure, followed by "
                "one terminal Codex review and an owner audit stop."
            ),
            "pr_number": FL1_I2_PR_NUMBER,
            "rejected_head": FL1_I2_FINAL_REJECTED_HEAD,
            "rejected_tree": FL1_I2_FINAL_REJECTED_TREE,
            "review_id": FL1_I2_FINAL_REVIEW_ID,
            "thread_ids": list(FL1_I2_FINAL_THREAD_IDS),
            "finding_disposition": "accept_and_require_fix",
            "final_convergence_correction_authorized": True,
            "same_branch_normal_push_authorized": True,
            "one_terminal_followup_review_authorized": True,
            "merge_authorized": False,
            "i3_started": False,
            "real_source_inventory_authorized": False,
        }
    ]:
        raise DocumentationStateError("fl1_i2_final_convergence_binding_invalid")

    post_terminal_decisions = [
        decision
        for decision in state["owner_decisions"]
        if isinstance(decision, dict)
        and decision.get("id") == FL1_I2_POST_TERMINAL_DECISION_ID
    ]
    if post_terminal_decisions != [
        {
            "id": FL1_I2_POST_TERMINAL_DECISION_ID,
            "decision": (
                "SCV2_FL1_I2_PR146_POST_TERMINAL_REVIEW_BOUNDED_"
                "CORRECTION_AUTHORIZED"
            ),
            "pr_number": FL1_I2_PR_NUMBER,
            "rejected_head": FL1_I2_TERMINAL_REJECTED_HEAD,
            "rejected_tree": FL1_I2_TERMINAL_REJECTED_TREE,
            "review_id": FL1_I2_POST_TERMINAL_REVIEW_ID,
            "findings": [
                {
                    "thread_id": thread_id,
                    "severity": severity,
                    "disposition": "accept_and_require_fix",
                }
                for thread_id, severity in FL1_I2_POST_TERMINAL_FINDINGS
            ],
            "post_terminal_bounded_correction_authorized": True,
            "same_branch_normal_push_authorized": True,
            "one_followup_codex_review_authorized": True,
            "merge_authorized": False,
            "i3_started": False,
            "real_source_inventory_authorized": False,
        }
    ]:
        raise DocumentationStateError("fl1_i2_post_terminal_binding_invalid")

    final_owner_decisions = [
        decision
        for decision in state["owner_decisions"]
        if isinstance(decision, dict)
        and decision.get("id") == FL1_I2_FINAL_OWNER_DECISION_ID
    ]
    if final_owner_decisions != [
        {
            "id": FL1_I2_FINAL_OWNER_DECISION_ID,
            "decision": FL1_I2_FINAL_OWNER_DECISION,
            "pr_number": FL1_I2_PR_NUMBER,
            "rejected_head": FL1_I2_FINAL_OWNER_REJECTED_HEAD,
            "rejected_tree": FL1_I2_FINAL_OWNER_REJECTED_TREE,
            "review_id": FL1_I2_FINAL_OWNER_REVIEW_ID,
            "findings": [
                {
                    "thread_id": thread_id,
                    "severity": severity,
                    "owner_disposition": disposition,
                    "classification": classification,
                }
                for thread_id, severity, disposition, classification in FL1_I2_FINAL_OWNER_FINDINGS
            ],
            "required_fix_count": 4,
            "safe_downgrade_count": 2,
            "deferred_count": 2,
            "same_branch_normal_push_authorized": True,
            "additional_codex_review_authorized": False,
            "merge_authorized": False,
            "i3_started": False,
            "px1_started": False,
            "real_source_inventory_authorized": False,
        }
    ]:
        raise DocumentationStateError("fl1_i2_final_owner_binding_invalid")

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
            "result": (
                "final_owner_adjudicated_correction_closes_four_required_"
                "fixes_applies_two_safe_downgrades_and_records_two_exact_"
                "gate_deferrals"
            ),
            "fingerprint": FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,
        }
    ]:
        raise DocumentationStateError("fl1_i2_implementation_checkpoint_invalid")

    projection_checkpoints = [
        checkpoint
        for checkpoint in state["completed_checkpoints"]
        if isinstance(checkpoint, dict)
        and checkpoint.get("id") == "fl1_i2_intermediate_post_terminal_governance_projection"
    ]
    if projection_checkpoints != [
        {
            "id": "fl1_i2_intermediate_post_terminal_governance_projection",
            "result": (
                "post_terminal_review_rejection_and_bounded_correction_truth_"
                "projected_pending_exact_head_owner_reaudit"
            ),
            "fingerprint": FL1_I2_INTERMEDIATE_POST_TERMINAL_PROJECTION_HEAD,
        }
    ]:
        raise DocumentationStateError("fl1_i2_post_terminal_projection_checkpoint_invalid")

    current_projection_checkpoints = [
        checkpoint
        for checkpoint in state["completed_checkpoints"]
        if isinstance(checkpoint, dict)
        and checkpoint.get("id") == "fl1_i2_post_terminal_governance_projection"
    ]
    if current_projection_checkpoints != [
        {
            "id": "fl1_i2_post_terminal_governance_projection",
            "result": (
                "validated_post_terminal_correction_truth_projected_pending_"
                "exact_head_owner_reaudit"
            ),
            "fingerprint": FL1_I2_POST_TERMINAL_PROJECTION_HEAD,
        }
    ]:
        raise DocumentationStateError(
            "fl1_i2_current_post_terminal_projection_checkpoint_invalid"
        )

    final_owner_projection_checkpoints = [
        checkpoint
        for checkpoint in state["completed_checkpoints"]
        if isinstance(checkpoint, dict)
        and checkpoint.get("id")
        == "fl1_i2_final_owner_adjudicated_governance_projection"
    ]
    if final_owner_projection_checkpoints != [
        {
            "id": "fl1_i2_final_owner_adjudicated_governance_projection",
            "result": (
                "current_head_tree_derived_by_trusted_git_pending_direct_"
                "owner_merge_audit"
            ),
        }
    ]:
        raise DocumentationStateError(
            "fl1_i2_final_owner_projection_checkpoint_invalid"
        )

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


def _validate_scv2_px1_state(state: dict[str, Any], *, root: Path) -> None:
    required = {
        "schema_version",
        "phase_id",
        "phase_title",
        "repository",
        "branch",
        "pr_number",
        "draft",
        "accepted_mainline_base",
        "accepted_mainline_tree",
        "implementation_evidence_head",
        "implementation_evidence_tree",
        "implementation_evidence_status",
        "current_status",
        "target_met",
        "safe_to_merge",
        "route_approved",
        "route_scope",
        "planning_authorized",
        "planning_completed",
        "planning_approved",
        "manual_acceptance_status",
        "next_phase_started",
        "previous_phase",
        "previous_phase_status",
        "previous_phase_pr_number",
        "previous_phase_merge_commit",
        "previous_phase_merge_tree",
        "previous_phase_final_head",
        "previous_phase_final_tree",
        "repository_sync_preflight",
        "pipeline_contract",
        "authorities",
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
        "upcoming_route",
        "artifact_lifecycle",
        "updated_at",
    }
    missing = sorted(required - state.keys())
    if missing:
        raise DocumentationStateError(f"missing_fields:{','.join(missing)}")
    if (
        state.get("schema_version") != SCHEMA_VERSION
        or state.get("phase_id") != "SCV2-PX1"
        or state.get("phase_title")
        != "Pixiv Metadata Consolidation and Offline Vertical Slice"
        or state.get("repository") != "kyloris0660/VIOLET"
        or state.get("branch") != SCV2_PX1_BRANCH
        or state.get("accepted_mainline_base") != SCV2_PX1_ACCEPTED_MAIN
        or state.get("accepted_mainline_tree") != SCV2_PX1_ACCEPTED_MAIN_TREE
        or state.get("previous_phase") != "SCV2-FL1-I2"
        or state.get("previous_phase_status")
        != "owner_adjudicated_pr146_merged_with_deferred_due_gates_preserved"
        or state.get("previous_phase_pr_number") != 146
        or state.get("previous_phase_merge_commit") != SCV2_PX1_ACCEPTED_MAIN
        or state.get("previous_phase_merge_tree") != SCV2_PX1_ACCEPTED_MAIN_TREE
        or state.get("previous_phase_final_head")
        != SCV2_PX1_PR146_ACCEPTED_HEAD
        or state.get("previous_phase_final_tree") != SCV2_PX1_ACCEPTED_MAIN_TREE
    ):
        raise DocumentationStateError("scv2_px1_identity_or_baseline_invalid")
    if not HEX40.fullmatch(str(state.get("implementation_evidence_head"))) or not HEX40.fullmatch(
        str(state.get("implementation_evidence_tree"))
    ):
        raise DocumentationStateError("scv2_px1_implementation_identity_invalid")
    if state.get("pr_number") is not None and (
        isinstance(state.get("pr_number"), bool)
        or not isinstance(state.get("pr_number"), int)
        or state["pr_number"] <= 0
    ):
        raise DocumentationStateError("pr_number_invalid")
    if not isinstance(state.get("draft"), bool):
        raise DocumentationStateError("draft_must_be_boolean")

    status = state.get("current_status")
    if status not in {SCV2_PX1_IN_PROGRESS_STATUS, SCV2_PX1_READY_STATUS}:
        raise DocumentationStateError("scv2_px1_status_invalid")
    ready = status == SCV2_PX1_READY_STATUS
    if (
        state.get("target_met") is not ready
        or state.get("safe_to_merge") is not False
        or state.get("route_approved") is not False
        or state.get("manual_acceptance_status")
        != (
            "pending_scv2_px1_final_owner_merge_audit"
            if ready
            else "owner_adjudicated_bounded_correction_in_progress"
        )
        or state.get("next_phase_started") is not False
        or state.get("planning_authorized") is not True
        or state.get("planning_completed") is not True
        or state.get("planning_approved") is not True
    ):
        raise DocumentationStateError("scv2_px1_status_fields_conflict")
    if state.get("authorities") != SCV2_PX1_EXPECTED_AUTHORITIES:
        raise DocumentationStateError("scv2_px1_authority_map_invalid")
    contract = state.get("pipeline_contract")
    if not isinstance(contract, dict) or contract != {
        "contract_id": SCV2_PX1_CONTRACT_ID,
        "public_schema": SCV2_PX1_PUBLIC_SCHEMA,
        "synthetic_vertical_slice_verified": ready,
        "deterministic_replay_verified": ready,
        "px2_consumer_contract_frozen": ready,
        "machine_verifiable_ci": False,
        "owner_authority_machine_verifiable": False,
    }:
        raise DocumentationStateError("scv2_px1_contract_projection_invalid")
    blocker = state.get("active_blocker")
    expected_blocker = SCV2_PX1_BLOCKER if ready else SCV2_PX1_IN_PROGRESS_BLOCKER
    if not isinstance(blocker, dict) or blocker.get("code") != expected_blocker or not all(
        blocker.get(key) for key in ("scope", "resolution")
    ):
        raise DocumentationStateError("scv2_px1_blocker_invalid")
    protected = state.get("protected_evidence")
    if not isinstance(protected, dict) or any(
        (
            protected.get("pr146_accepted_head") != SCV2_PX1_PR146_ACCEPTED_HEAD,
            protected.get("pr146_accepted_tree") != SCV2_PX1_ACCEPTED_MAIN_TREE,
            protected.get("pr146_merge_commit") != SCV2_PX1_ACCEPTED_MAIN,
            protected.get("pr146_merge_tree") != SCV2_PX1_ACCEPTED_MAIN_TREE,
            protected.get("pr146_merged") is not True,
            protected.get("pr146_final_review_id")
            != SCV2_PX1_PR146_FINAL_REVIEW_ID,
            protected.get("pr146_final_reviewed_head")
            != SCV2_PX1_PR146_ACCEPTED_HEAD,
            protected.get("pr146_final_review_finding_count") != 10,
            protected.get("pr146_final_review_resolved_count") != 0,
            protected.get("pr146_final_review_outdated_count") != 0,
            protected.get("machine_verifiable_ci") is not False,
            protected.get("owner_accepted") is not False,
            protected.get("safe_to_merge") is not False,
            protected.get("merge_authorized") is not False,
            protected.get("external_data_plane_network_operation_count") != 0,
            protected.get("existing_database_access_operation_count") != 0,
            protected.get("real_source_operation_count") != 0,
            protected.get("media_download_operation_count") != 0,
            protected.get("production_operation_count") != 0,
        )
    ):
        raise DocumentationStateError("scv2_px1_protected_evidence_invalid")
    findings = protected.get("pr146_final_review_deferred_findings")
    if not isinstance(findings, list):
        raise DocumentationStateError("scv2_px1_final_review_findings_invalid")
    actual_due_map = {
        str(item.get("thread_id")): str(item.get("due_gate"))
        for item in findings
        if isinstance(item, dict)
        and item.get("status") == "deferred_exact_gate_not_closed_in_px1"
    }
    if actual_due_map != SCV2_PX1_FINAL_REVIEW_DUE_GATES:
        raise DocumentationStateError("scv2_px1_final_review_due_gate_map_invalid")

    for key in (
        "completed_checkpoints",
        "owner_decisions",
        "authorized_operations",
        "forbidden_operations",
        "durable_links",
        "deferred_debt",
    ):
        _require_list(state, key)
    for checkpoint in state["completed_checkpoints"]:
        if not isinstance(checkpoint, dict) or not checkpoint.get("id") or not checkpoint.get("result"):
            raise DocumentationStateError("completed_checkpoint_invalid")
    for decision in state["owner_decisions"]:
        if not isinstance(decision, dict) or not decision.get("id") or not decision.get("decision"):
            raise DocumentationStateError("owner_decision_invalid")
    debt_ids: set[str] = set()
    for debt in state["deferred_debt"]:
        if not isinstance(debt, dict) or not all(
            debt.get(key) for key in ("id", "owner", "reason", "due_before")
        ):
            raise DocumentationStateError("deferred_debt_invalid")
        requirements = debt.get("requirements")
        if not isinstance(requirements, list) or not requirements or not all(
            isinstance(value, str) and value for value in requirements
        ):
            raise DocumentationStateError("deferred_debt_requirements_invalid")
        debt_ids.add(str(debt["id"]))
    if debt_ids != SCV2_PX1_REQUIRED_DEFERRED_GATES:
        raise DocumentationStateError("scv2_px1_deferred_due_gate_set_invalid")
    if state.get("upcoming_route") != [
        {
            "phase_id": "SCV2-PX1",
            "scope": "Pixiv metadata consolidation and offline synthetic vertical slice",
            "started": True,
        },
        {
            "phase_id": "SCV2-PX2",
            "scope": "deterministic clustering, identity, candidate explanation, ambiguous queue, controlled sample evaluation, and persistable cluster result",
            "started": False,
        },
        {
            "phase_id": "SCV2-PX3",
            "scope": "real source/provider, necessary migration, persistence, API/UI, dry-run/apply, idempotency, backup/recovery, canary, rollback, and final full-library import checkpoint",
            "started": False,
        },
    ]:
        raise DocumentationStateError("scv2_px1_fixed_route_invalid")
    if state.get("artifact_lifecycle") != [
        "durable_backend_aggregate_and_signal_projection",
        "thin_repository_owned_offline_runner",
        "task_owned_temporary_private_evidence",
        "public_safe_governance_projection",
    ]:
        raise DocumentationStateError("scv2_px1_artifact_lifecycle_invalid")
    if state.get("public_state_boundary") != (
        "public_safe_synthetic_implementation_no_private_proof_payloads_or_paths"
    ):
        raise DocumentationStateError("public_state_boundary_invalid")
    for link in state["durable_links"]:
        if not isinstance(link, dict) or not link.get("label") or not link.get("path"):
            raise DocumentationStateError("durable_link_invalid")
        target = (root / str(link["path"])).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            raise DocumentationStateError(f"durable_link_missing:{link.get('path')}")
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if "\\u0000" in serialized:
        raise DocumentationStateError("public_state_redaction_failure:nul")
    for pattern in PUBLIC_FORBIDDEN:
        if pattern.search(serialized):
            raise DocumentationStateError(
                f"public_state_redaction_failure:{pattern.pattern}"
            )


def _validate_scv2_px2_state(state: dict[str, Any], *, root: Path) -> None:
    required = {
        "schema_version",
        "phase_id",
        "phase_title",
        "repository",
        "branch",
        "pr_number",
        "draft",
        "accepted_mainline_base",
        "accepted_mainline_tree",
        "implementation_evidence_head",
        "implementation_evidence_tree",
        "implementation_evidence_status",
        "current_status",
        "target_met",
        "safe_to_merge",
        "route_approved",
        "route_scope",
        "planning_authorized",
        "planning_completed",
        "planning_approved",
        "manual_acceptance_status",
        "next_phase_started",
        "previous_phase",
        "previous_phase_status",
        "previous_phase_pr_number",
        "previous_phase_merge_commit",
        "previous_phase_merge_tree",
        "previous_phase_final_head",
        "previous_phase_final_tree",
        "repository_sync_preflight",
        "pipeline_contract",
        "authorities",
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
        "upcoming_route",
        "artifact_lifecycle",
        "updated_at",
    }
    missing = sorted(required - state.keys())
    if missing:
        raise DocumentationStateError(f"missing_fields:{','.join(missing)}")
    if (
        state.get("schema_version") != SCHEMA_VERSION
        or state.get("phase_id") != "SCV2-PX2"
        or state.get("phase_title")
        != "Deterministic Pixiv SourceConcept Clustering"
        or state.get("repository") != "kyloris0660/VIOLET"
        or state.get("branch") != SCV2_PX2_BRANCH
        or state.get("accepted_mainline_base") != SCV2_PX2_ACCEPTED_MAIN
        or state.get("accepted_mainline_tree") != SCV2_PX2_ACCEPTED_MAIN_TREE
        or state.get("previous_phase") != "SCV2-PX1"
        or state.get("previous_phase_status")
        != "owner_accepted_pr147_merged_with_exact_tree_preserved"
        or state.get("previous_phase_pr_number") != 147
        or state.get("previous_phase_merge_commit") != SCV2_PX2_ACCEPTED_MAIN
        or state.get("previous_phase_merge_tree") != SCV2_PX2_ACCEPTED_MAIN_TREE
        or state.get("previous_phase_final_head")
        != SCV2_PX2_PR147_ACCEPTED_HEAD
        or state.get("previous_phase_final_tree")
        != SCV2_PX2_PR147_ACCEPTED_TREE
    ):
        raise DocumentationStateError("scv2_px2_identity_or_baseline_invalid")
    if not HEX40.fullmatch(
        str(state.get("implementation_evidence_head"))
    ) or not HEX40.fullmatch(str(state.get("implementation_evidence_tree"))):
        raise DocumentationStateError("scv2_px2_implementation_identity_invalid")
    if not isinstance(state.get("implementation_evidence_status"), str) or not state[
        "implementation_evidence_status"
    ]:
        raise DocumentationStateError("scv2_px2_implementation_status_invalid")
    if state.get("pr_number") is not None and (
        isinstance(state.get("pr_number"), bool)
        or not isinstance(state.get("pr_number"), int)
        or state["pr_number"] <= 0
    ):
        raise DocumentationStateError("pr_number_invalid")
    if not isinstance(state.get("draft"), bool):
        raise DocumentationStateError("draft_must_be_boolean")

    status = state.get("current_status")
    if status not in {SCV2_PX2_IN_PROGRESS_STATUS, SCV2_PX2_READY_STATUS}:
        raise DocumentationStateError("scv2_px2_status_invalid")
    ready = status == SCV2_PX2_READY_STATUS
    expected_manual = (
        "pending_scv2_px2_owner_merge_audit"
        if ready
        else "px2_synthetic_implementation_in_progress"
    )
    if (
        state.get("target_met") is not ready
        or state.get("safe_to_merge") is not False
        or state.get("route_approved") is not False
        or state.get("manual_acceptance_status") != expected_manual
        or state.get("next_phase_started") is not False
        or state.get("planning_authorized") is not True
        or state.get("planning_completed") is not True
        or state.get("planning_approved") is not True
        or (ready and (state.get("pr_number") is None or state.get("draft") is not False))
    ):
        raise DocumentationStateError("scv2_px2_status_fields_conflict")
    if state.get("authorities") != SCV2_PX2_EXPECTED_AUTHORITIES:
        raise DocumentationStateError("scv2_px2_authority_map_invalid")
    contract = state.get("pipeline_contract")
    expected_contract = {
        "contract_id": SCV2_PX2_CONTRACT_ID,
        "public_schema": SCV2_PX2_PUBLIC_SCHEMA,
        "px1_consumer_validation_verified": ready,
        "existing_resolver_reused": ready,
        "candidate_accounting_verified": ready,
        "ambiguous_ledger_verified": ready,
        "deterministic_replay_verified": ready,
        "temporary_persistence_idempotent": ready,
        "persistable_cluster_result_verified": ready,
        "machine_verifiable_ci": False,
        "owner_authority_machine_verifiable": False,
    }
    if not isinstance(contract, dict) or contract != expected_contract:
        raise DocumentationStateError("scv2_px2_contract_projection_invalid")
    blocker = state.get("active_blocker")
    expected_blocker = (
        SCV2_PX2_READY_BLOCKER if ready else SCV2_PX2_IN_PROGRESS_BLOCKER
    )
    if (
        not isinstance(blocker, dict)
        or blocker.get("code") != expected_blocker
        or not all(blocker.get(key) for key in ("scope", "resolution"))
    ):
        raise DocumentationStateError("scv2_px2_blocker_invalid")

    protected = state.get("protected_evidence")
    if not isinstance(protected, dict) or any(
        (
            protected.get("pr146_accepted_head")
            != SCV2_PX1_PR146_ACCEPTED_HEAD,
            protected.get("pr146_accepted_tree")
            != SCV2_PX1_ACCEPTED_MAIN_TREE,
            protected.get("pr146_merge_commit") != SCV2_PX1_ACCEPTED_MAIN,
            protected.get("pr146_merge_tree") != SCV2_PX1_ACCEPTED_MAIN_TREE,
            protected.get("pr146_merged") is not True,
            protected.get("pr146_final_owner_adjudication_review_id")
            != 4963026941,
            protected.get("pr146_final_review_id")
            != SCV2_PX1_PR146_FINAL_REVIEW_ID,
            protected.get("pr146_final_reviewed_head")
            != SCV2_PX1_PR146_ACCEPTED_HEAD,
            protected.get("pr146_final_review_finding_count") != 10,
            protected.get("pr146_final_review_resolved_count") != 0,
            protected.get("pr146_final_review_outdated_count") != 0,
            protected.get("pr147_accepted_head")
            != SCV2_PX2_PR147_ACCEPTED_HEAD,
            protected.get("pr147_accepted_tree")
            != SCV2_PX2_PR147_ACCEPTED_TREE,
            protected.get("pr147_merge_commit") != SCV2_PX2_ACCEPTED_MAIN,
            protected.get("pr147_merge_tree") != SCV2_PX2_ACCEPTED_MAIN_TREE,
            tuple(protected.get("pr147_merge_parents", ()))
            != SCV2_PX2_PR147_MERGE_PARENTS,
            protected.get("pr147_merged") is not True,
            protected.get("pr147_final_reviewed_head")
            != "557c6f26a85b708f9f386f975d0933be205c112c",
            protected.get("pr147_final_review_accepted_finding_count") != 4,
            protected.get("pr147_final_review_rejected_finding_count") != 1,
            protected.get("pr147_deferred_workspace_confinement_count") != 1,
            protected.get("pr147_deferred_workspace_confinement_gate")
            != "SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE",
            protected.get("px1_owner_accepted") is not True,
            protected.get("px1_merged") is not True,
            protected.get("px2_started") is not True,
            protected.get("px2_implementation_completed") is not ready,
            protected.get("deterministic_clustering_verified") is not ready,
            protected.get("persistable_cluster_result_verified") is not ready,
            protected.get("px2_owner_accepted") is not False,
            protected.get("px2_safe_to_merge") is not False,
            protected.get("px2_merge_authorized") is not False,
            protected.get("px3_started") is not False,
            protected.get("existing_db_or_app_storage_activity") != 0,
            protected.get("provider_network_activity") != 0,
            protected.get("real_source_activity") != 0,
            protected.get("llm_activity") != 0,
            protected.get("production_activity") != 0,
            protected.get("machine_verifiable_ci") is not False,
            protected.get("external_data_plane_network_operation_count") != 0,
            protected.get("existing_database_access_operation_count") != 0,
            protected.get("media_download_operation_count") != 0,
            protected.get("production_operation_count") != 0,
        )
    ):
        raise DocumentationStateError("scv2_px2_protected_evidence_invalid")
    if not isinstance(protected.get("pr147_merge_time"), str) or not protected[
        "pr147_merge_time"
    ]:
        raise DocumentationStateError("scv2_px2_merge_time_invalid")
    findings = protected.get("pr146_final_review_deferred_findings")
    if not isinstance(findings, list):
        raise DocumentationStateError("scv2_px2_pr146_deferred_findings_invalid")
    actual_due_map = {
        str(item.get("thread_id")): str(item.get("due_gate"))
        for item in findings
        if isinstance(item, dict)
        and item.get("status") == "deferred_exact_gate_not_closed_in_px1"
    }
    if actual_due_map != SCV2_PX1_FINAL_REVIEW_DUE_GATES:
        raise DocumentationStateError("scv2_px2_pr146_due_gate_map_invalid")

    for key in (
        "completed_checkpoints",
        "owner_decisions",
        "authorized_operations",
        "forbidden_operations",
        "durable_links",
        "deferred_debt",
    ):
        _require_list(state, key)
    for checkpoint in state["completed_checkpoints"]:
        if (
            not isinstance(checkpoint, dict)
            or not checkpoint.get("id")
            or not checkpoint.get("result")
        ):
            raise DocumentationStateError("completed_checkpoint_invalid")
    for decision in state["owner_decisions"]:
        if (
            not isinstance(decision, dict)
            or not decision.get("id")
            or not decision.get("decision")
        ):
            raise DocumentationStateError("owner_decision_invalid")
    debt_ids = {
        str(debt.get("id"))
        for debt in state["deferred_debt"]
        if isinstance(debt, dict)
        and debt.get("owner")
        and debt.get("reason")
        and debt.get("due_before")
        and isinstance(debt.get("requirements"), list)
        and debt["requirements"]
    }
    if debt_ids != SCV2_PX1_REQUIRED_DEFERRED_GATES:
        raise DocumentationStateError("scv2_px2_deferred_due_gate_set_invalid")
    if state.get("upcoming_route") != [
        {
            "phase_id": "SCV2-PX1",
            "scope": "Pixiv metadata consolidation and offline synthetic vertical slice",
            "started": True,
            "completed": True,
            "merged": True,
        },
        {
            "phase_id": "SCV2-PX2",
            "scope": "deterministic Pixiv SourceConcept clustering, candidate explanation, ambiguous ledger, and persistable cluster result",
            "started": True,
        },
        {
            "phase_id": "SCV2-PX3",
            "scope": "real source/provider, necessary migration, production persistence, API/UI, canary, rollback, and final import checkpoint",
            "started": False,
        },
    ]:
        raise DocumentationStateError("scv2_px2_fixed_route_invalid")
    if state.get("artifact_lifecycle") != [
        "frozen_px1_aggregate_and_signal_consumer",
        "existing_sourceconcept_resolver",
        "deterministic_candidate_and_ambiguous_projection",
        "task_owned_temporary_sourceconcept_persistence",
        "public_safe_cluster_result",
    ]:
        raise DocumentationStateError("scv2_px2_artifact_lifecycle_invalid")
    if state.get("public_state_boundary") != (
        "public_safe_synthetic_implementation_no_private_proof_payloads_or_paths"
    ):
        raise DocumentationStateError("public_state_boundary_invalid")
    for link in state["durable_links"]:
        if not isinstance(link, dict) or not link.get("label") or not link.get(
            "path"
        ):
            raise DocumentationStateError("durable_link_invalid")
        target = (root / str(link["path"])).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            raise DocumentationStateError(f"durable_link_missing:{link.get('path')}")
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if "\\u0000" in serialized:
        raise DocumentationStateError("public_state_redaction_failure:nul")
    for pattern in PUBLIC_FORBIDDEN:
        if pattern.search(serialized):
            raise DocumentationStateError(
                f"public_state_redaction_failure:{pattern.pattern}"
            )


def _validate_scv2_px3_state(state: dict[str, Any], *, root: Path) -> None:
    required = {
        "schema_version",
        "phase_id",
        "phase_title",
        "repository",
        "branch",
        "pr_number",
        "draft",
        "accepted_mainline_base",
        "accepted_mainline_tree",
        "implementation_evidence_head",
        "implementation_evidence_tree",
        "implementation_evidence_status",
        "current_status",
        "target_met",
        "safe_to_merge",
        "route_approved",
        "route_scope",
        "planning_authorized",
        "planning_completed",
        "planning_approved",
        "manual_acceptance_status",
        "next_phase_started",
        "previous_phase",
        "previous_phase_status",
        "previous_phase_pr_number",
        "previous_phase_merge_commit",
        "previous_phase_merge_tree",
        "previous_phase_final_head",
        "previous_phase_final_tree",
        "repository_sync_preflight",
        "pipeline_contract",
        "authorities",
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
        "upcoming_route",
        "artifact_lifecycle",
        "updated_at",
    }
    missing = sorted(required - state.keys())
    if missing:
        raise DocumentationStateError(f"missing_fields:{','.join(missing)}")
    if (
        state.get("schema_version") != SCHEMA_VERSION
        or state.get("phase_id") != "SCV2-PX3"
        or state.get("phase_title") != "Pixiv Product Integration"
        or state.get("repository") != "kyloris0660/VIOLET"
        or state.get("branch") != SCV2_PX3_BRANCH
        or state.get("accepted_mainline_base") != SCV2_PX3_ACCEPTED_MAIN
        or state.get("accepted_mainline_tree") != SCV2_PX3_ACCEPTED_MAIN_TREE
        or state.get("previous_phase") != "SCV2-PX2"
        or state.get("previous_phase_status")
        != "owner_accepted_pr148_merged_with_exact_tree_preserved"
        or state.get("previous_phase_pr_number") != 148
        or state.get("previous_phase_merge_commit") != SCV2_PX3_ACCEPTED_MAIN
        or state.get("previous_phase_merge_tree") != SCV2_PX3_ACCEPTED_MAIN_TREE
        or state.get("previous_phase_final_head") != SCV2_PX3_PR148_ACCEPTED_HEAD
        or state.get("previous_phase_final_tree") != SCV2_PX3_PR148_ACCEPTED_TREE
    ):
        raise DocumentationStateError("scv2_px3_identity_or_baseline_invalid")
    if not HEX40.fullmatch(str(state.get("implementation_evidence_head"))) or not HEX40.fullmatch(
        str(state.get("implementation_evidence_tree"))
    ):
        raise DocumentationStateError("scv2_px3_implementation_identity_invalid")
    if not isinstance(state.get("implementation_evidence_status"), str) or not state[
        "implementation_evidence_status"
    ]:
        raise DocumentationStateError("scv2_px3_implementation_status_invalid")
    if state.get("pr_number") is not None and (
        isinstance(state.get("pr_number"), bool)
        or not isinstance(state.get("pr_number"), int)
        or state["pr_number"] <= 0
    ):
        raise DocumentationStateError("pr_number_invalid")
    if not isinstance(state.get("draft"), bool):
        raise DocumentationStateError("draft_must_be_boolean")

    status = state.get("current_status")
    closure = status in {SCV2_PX3_CLOSURE_READY_STATUS, SCV2_PX3_MERGED_STATUS}
    merged = status == SCV2_PX3_MERGED_STATUS
    merge_authorized = status == SCV2_PX3_CLOSURE_READY_STATUS
    if status not in {SCV2_PX3_IN_PROGRESS_STATUS, SCV2_PX3_READY_STATUS,
                      SCV2_PX3_CLOSURE_READY_STATUS, SCV2_PX3_MERGED_STATUS}:
        raise DocumentationStateError("scv2_px3_status_invalid")
    ready = status == SCV2_PX3_READY_STATUS or closure
    expected_manual = (
        "pending_scv2_px3_owner_acceptance_and_controlled_canary"
        if ready
        else "px3_product_integration_in_progress"
    )
    if closure:
        expected_manual = 'owner_accepted_final_bounded_product_closure'
    if (
        state.get("target_met") is not ready
        or state.get("safe_to_merge") is not merge_authorized
        or state.get("route_approved") is not False
        or state.get("manual_acceptance_status") != expected_manual
        or state.get("next_phase_started") is not False
        or state.get("planning_authorized") is not True
        or state.get("planning_completed") is not True
        or state.get("planning_approved") is not True
        or (ready and (state.get("pr_number") is None or state.get("draft") is not False))
    ):
        raise DocumentationStateError("scv2_px3_status_fields_conflict")
    if state.get("authorities") != {**SCV2_PX3_EXPECTED_AUTHORITIES, 'px3_merge_authorized': merge_authorized}:
        raise DocumentationStateError("scv2_px3_authority_map_invalid")
    expected_contract = {
        "contract_id": SCV2_PX3_CONTRACT_ID,
        "public_schema": SCV2_PX3_PUBLIC_SCHEMA,
        "repository_gap_map_completed": True,
        "px1_px2_reused": ready,
        "product_persistence_verified": ready,
        "dry_run_apply_rollback_verified": ready,
        "api_ui_verified": ready,
        "provider_adapter_wired_without_execution": ready,
        "synthetic_browser_e2e_verified": ready,
        "controlled_canary_entrypoints_verified": ready,
        "machine_verifiable_ci": False,
        "owner_authority_machine_verifiable": False,
    }
    if closure:
        expected_contract.update({key: True for key in (
            'media_binding_verified', 'actual_gallery_search_verified',
            'accepted_plan_apply_verified', 'rollback_ownership_verified')})
        if (state.get('px3_target_met') is not True
            or state.get('px3_owner_accepted') is not True
            or state.get('px3_merged') is not merged
            or state.get('three_phase_implementation_route_completed') is not merged
            or state.get('conditional_expected_head_merge_authorized') is not True):
            raise DocumentationStateError('scv2_px3_closure_status_conflict')
        for key in ('controlled_canary_authorized', 'real_pixiv_network_execution_authorized',
                    'provider_credentials_authorized', 'existing_database_or_app_storage_access_authorized',
                    'real_source_or_icloud_access_authorized', 'user_data_import_authorized',
                    'production_authorized', 'full_library_import_authorized'):
            if state.get(key) is not False:
                raise DocumentationStateError('scv2_px3_closure_real_authority_forbidden')
        verification = state.get('closure_verification', {})
        if any(verification.get(key) is not True for key in (
            'media_binding_contract_passed', 'accepted_plan_exact_match_passed',
            'actual_search_and_detail_passed', 'rollback_ownership_and_cache_passed',
            'synthetic_edge_browser_passed', 'backup_restore_before_normal_startup_stop_recorded')):
            raise DocumentationStateError('scv2_px3_closure_evidence_missing')
        if verification.get('implementation_head') != state['implementation_evidence_head']:
            raise DocumentationStateError('scv2_px3_closure_head_mismatch')
    if state.get("pipeline_contract") != expected_contract:
        raise DocumentationStateError("scv2_px3_contract_projection_invalid")
    blocker = state.get("active_blocker")
    expected_blocker = SCV2_PX3_READY_BLOCKER if ready else SCV2_PX3_IN_PROGRESS_BLOCKER
    if closure:
        expected_blocker = 'controlled_canary_authorization_required' if merged else 'expected_head_merge_pending'
    if (
        not isinstance(blocker, dict)
        or blocker.get("code") != expected_blocker
        or not all(blocker.get(key) for key in ("scope", "resolution"))
    ):
        raise DocumentationStateError("scv2_px3_blocker_invalid")

    protected = state.get("protected_evidence")
    if not isinstance(protected, dict) or any(
        (
            protected.get("pr148_accepted_head") != SCV2_PX3_PR148_ACCEPTED_HEAD,
            protected.get("pr148_accepted_tree") != SCV2_PX3_PR148_ACCEPTED_TREE,
            protected.get("pr148_merge_commit") != SCV2_PX3_ACCEPTED_MAIN,
            protected.get("pr148_merge_tree") != SCV2_PX3_ACCEPTED_MAIN_TREE,
            tuple(protected.get("pr148_merge_parents", ())) != SCV2_PX3_PR148_MERGE_PARENTS,
            protected.get("pr148_merged") is not True,
            protected.get("px1_owner_accepted") is not True,
            protected.get("px1_merged") is not True,
            protected.get("px2_owner_accepted") is not True,
            protected.get("px2_merged") is not True,
            protected.get("px3_started") is not True,
            protected.get("px3_implementation_completed") is not ready,
            protected.get("product_integration_verified") is not ready,
            protected.get("px3_owner_accepted") is not closure,
            protected.get("px3_safe_to_merge") is not merge_authorized,
            protected.get("px3_merge_authorized") is not merge_authorized,
            protected.get("existing_db_or_app_storage_activity") != 0,
            protected.get("provider_network_activity") != 0,
            protected.get("real_source_activity") != 0,
            protected.get("llm_activity") != 0,
            protected.get("production_activity") != 0,
        )
    ):
        raise DocumentationStateError("scv2_px3_protected_evidence_invalid")
    if not isinstance(protected.get("pr148_merge_time"), str) or not protected[
        "pr148_merge_time"
    ]:
        raise DocumentationStateError("scv2_px3_merge_time_invalid")

    for key in (
        "completed_checkpoints",
        "owner_decisions",
        "authorized_operations",
        "forbidden_operations",
        "durable_links",
        "deferred_debt",
    ):
        _require_list(state, key)
    debt_ids = {
        str(debt.get("id"))
        for debt in state["deferred_debt"]
        if isinstance(debt, dict)
        and debt.get("owner")
        and debt.get("reason")
        and debt.get("due_before")
        and isinstance(debt.get("requirements"), list)
        and debt["requirements"]
    }
    expected_debt = SCV2_PX1_REQUIRED_DEFERRED_GATES | ({'SCV2_PX3_MULTIWORKER_APPLY_GATE'} if closure else set())
    if debt_ids != expected_debt:
        raise DocumentationStateError("scv2_px3_deferred_due_gate_set_invalid")
    if state.get("upcoming_route") != [
        {
            "phase_id": "SCV2-PX1",
            "scope": "Pixiv metadata consolidation and offline synthetic vertical slice",
            "started": True,
            "completed": True,
            "merged": True,
        },
        {
            "phase_id": "SCV2-PX2",
            "scope": "deterministic Pixiv SourceConcept clustering, candidate explanation, ambiguous ledger, and persistable cluster result",
            "started": True,
            "completed": True,
            "merged": True,
        },
        {
            "phase_id": "SCV2-PX3",
            "scope": "final Pixiv product persistence, API/UI, controlled execution boundaries, canary entrypoints, and owner acceptance checkpoint",
            "started": True,
            "completed": ready,
        },
    ]:
        raise DocumentationStateError("scv2_px3_fixed_route_invalid")
    if state.get("artifact_lifecycle") != [
        "frozen_px1_aggregate_and_signal_consumer",
        "existing_px2_sourceconcept_resolution",
        "versioned_product_integration_result",
        "sourceconcept_owned_product_persistence",
        "read_only_product_api_and_operable_admin_ui",
        "controlled_canary_and_rollback_boundary",
    ]:
        raise DocumentationStateError("scv2_px3_artifact_lifecycle_invalid")
    if state.get("public_state_boundary") != (
        "public_safe_product_integration_no_real_data_private_paths_credentials_or_raw_payloads"
    ):
        raise DocumentationStateError("public_state_boundary_invalid")
    for link in state["durable_links"]:
        if not isinstance(link, dict) or not link.get("label") or not link.get("path"):
            raise DocumentationStateError("durable_link_invalid")
        target = (root / str(link["path"])).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            raise DocumentationStateError(f"durable_link_missing:{link.get('path')}")
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if "\\u0000" in serialized:
        raise DocumentationStateError("public_state_redaction_failure:nul")
    for pattern in PUBLIC_FORBIDDEN:
        if pattern.search(serialized):
            raise DocumentationStateError(
                f"public_state_redaction_failure:{pattern.pattern}"
            )


def validate_state(state: dict[str, Any], *, root: Path = ROOT) -> None:
    if state.get("phase_id") == "SCV2-PX1":
        _validate_scv2_px1_state(state, root=root)
        return
    if state.get("phase_id") == "SCV2-PX2":
        _validate_scv2_px2_state(state, root=root)
        return
    if state.get("phase_id") == "SCV2-PX3":
        _validate_scv2_px3_state(state, root=root)
        return
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
    """Require exactly one governance projection, with optional clean merge."""

    current = _run_trusted_git(["rev-parse", "HEAD"], root=root)
    if current.returncode != 0:
        raise DocumentationStateError("fl1_i2_implementation_projection_unavailable")
    head = current.stdout.strip()
    head_parents_result = _run_trusted_git(
        ["show", "-s", "--format=%P", head], root=root
    )
    if head_parents_result.returncode != 0:
        raise DocumentationStateError("fl1_i2_implementation_projection_unavailable")
    head_parents = tuple(head_parents_result.stdout.strip().split())

    if head_parents == (FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,):
        projection = head
    elif len(head_parents) == 2:
        if head_parents[0] != FL1_I2_PLANNING_MERGE_COMMIT:
            raise DocumentationStateError("fl1_i2_implementation_merge_topology_invalid")
        projection = head_parents[1]
        projection_parents = _run_trusted_git(
            ["show", "-s", "--format=%P", projection], root=root
        )
        if (
            projection_parents.returncode != 0
            or tuple(projection_parents.stdout.strip().split())
            != (FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,)
        ):
            raise DocumentationStateError(
                "fl1_i2_implementation_merge_topology_invalid"
            )
        head_tree = _run_trusted_git(["rev-parse", f"{head}^{{tree}}"], root=root)
        projection_tree = _run_trusted_git(
            ["rev-parse", f"{projection}^{{tree}}"], root=root
        )
        if (
            head_tree.returncode != 0
            or projection_tree.returncode != 0
            or head_tree.stdout.strip() != projection_tree.stdout.strip()
        ):
            raise DocumentationStateError(
                "fl1_i2_implementation_merge_tree_mismatch"
            )
    else:
        raise DocumentationStateError("fl1_i2_implementation_projection_invalid")

    paths = _trusted_git_changed_paths(
        [
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "-z",
            FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD,
            projection,
            "--",
        ],
        root=root,
        error_code="fl1_i2_implementation_projection_diff_unavailable",
    )
    if not paths:
        raise DocumentationStateError("fl1_i2_implementation_projection_missing")
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


def _trusted_git_value(root: Path, *arguments: str) -> str:
    result = _run_trusted_git(list(arguments), root=root)
    if result.returncode != 0:
        raise DocumentationStateError("scv2_px1_trusted_git_query_failed")
    return result.stdout.strip()


def _validate_scv2_px1_git_ancestry(state: dict[str, Any], *, root: Path) -> None:
    expected_trees = {
        SCV2_PX1_ACCEPTED_MAIN: SCV2_PX1_ACCEPTED_MAIN_TREE,
        SCV2_PX1_PR146_ACCEPTED_HEAD: SCV2_PX1_ACCEPTED_MAIN_TREE,
        str(state["implementation_evidence_head"]): str(
            state["implementation_evidence_tree"]
        ),
    }
    for commit, tree in expected_trees.items():
        if _trusted_git_value(root, "rev-parse", f"{commit}^{{tree}}") != tree:
            raise DocumentationStateError("scv2_px1_trusted_tree_mismatch")
    for ancestor, descendant in (
        (SCV2_PX1_PR146_ACCEPTED_HEAD, SCV2_PX1_ACCEPTED_MAIN),
        (SCV2_PX1_ACCEPTED_MAIN, "HEAD"),
        (str(state["implementation_evidence_head"]), "HEAD"),
    ):
        result = _run_trusted_git(
            ["merge-base", "--is-ancestor", ancestor, descendant], root=root
        )
        if result.returncode != 0:
            raise DocumentationStateError("scv2_px1_trusted_ancestry_invalid")
    parents = _trusted_git_value(root, "show", "-s", "--format=%P", SCV2_PX1_ACCEPTED_MAIN).split()
    if SCV2_PX1_PR146_ACCEPTED_HEAD not in parents:
        raise DocumentationStateError("scv2_px1_pr146_merge_parent_invalid")
    if _trusted_git_value(root, "rev-parse", "--abbrev-ref", "HEAD") != state["branch"]:
        raise DocumentationStateError("scv2_px1_branch_identity_invalid")
    if state.get("current_status") == SCV2_PX1_READY_STATUS:
        try:
            validate_px1_evidence_carry_forward(
                root,
                evidence_head=str(state["implementation_evidence_head"]),
                evidence_tree=str(state["implementation_evidence_tree"]),
            )
        except Px1ValidationReceiptError as exc:
            raise DocumentationStateError(str(exc)) from exc


def _validate_scv2_px2_git_ancestry(state: dict[str, Any], *, root: Path) -> None:
    expected_trees = {
        SCV2_PX2_ACCEPTED_MAIN: SCV2_PX2_ACCEPTED_MAIN_TREE,
        SCV2_PX2_PR147_ACCEPTED_HEAD: SCV2_PX2_PR147_ACCEPTED_TREE,
        str(state["implementation_evidence_head"]): str(
            state["implementation_evidence_tree"]
        ),
    }
    for commit, tree in expected_trees.items():
        if _trusted_git_value(root, "rev-parse", f"{commit}^{{tree}}") != tree:
            raise DocumentationStateError("scv2_px2_trusted_tree_mismatch")
    for ancestor, descendant in (
        (SCV2_PX2_PR147_ACCEPTED_HEAD, SCV2_PX2_ACCEPTED_MAIN),
        (SCV2_PX2_ACCEPTED_MAIN, "HEAD"),
        (str(state["implementation_evidence_head"]), "HEAD"),
    ):
        result = _run_trusted_git(
            ["merge-base", "--is-ancestor", ancestor, descendant], root=root
        )
        if result.returncode != 0:
            raise DocumentationStateError("scv2_px2_trusted_ancestry_invalid")
    parents = tuple(
        _trusted_git_value(
            root, "show", "-s", "--format=%P", SCV2_PX2_ACCEPTED_MAIN
        ).split()
    )
    if parents != SCV2_PX2_PR147_MERGE_PARENTS:
        raise DocumentationStateError("scv2_px2_pr147_merge_parent_invalid")
    if (
        _trusted_git_value(root, "rev-parse", "--abbrev-ref", "HEAD")
        != state["branch"]
    ):
        raise DocumentationStateError("scv2_px2_branch_identity_invalid")
    if state.get("current_status") == SCV2_PX2_READY_STATUS:
        paths = _trusted_git_changed_paths(
            [
                "diff",
                "--no-ext-diff",
                "--no-renames",
                "--name-only",
                "-z",
                str(state["implementation_evidence_head"]),
                "HEAD",
                "--",
            ],
            root=root,
            error_code="scv2_px2_evidence_carry_forward_diff_unavailable",
        )
        unexpected = sorted(
            path
            for path in paths
            if path not in SCV2_PX2_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST
        )
        if unexpected:
            raise DocumentationStateError(
                "scv2_px2_evidence_behavior_drift:" + ",".join(unexpected)
            )


def _validate_scv2_px3_git_ancestry(state: dict[str, Any], *, root: Path) -> None:
    expected_trees = {
        SCV2_PX3_ACCEPTED_MAIN: SCV2_PX3_ACCEPTED_MAIN_TREE,
        SCV2_PX3_PR148_ACCEPTED_HEAD: SCV2_PX3_PR148_ACCEPTED_TREE,
        str(state["implementation_evidence_head"]): str(
            state["implementation_evidence_tree"]
        ),
    }
    for commit, tree in expected_trees.items():
        if _trusted_git_value(root, "rev-parse", f"{commit}^{{tree}}") != tree:
            raise DocumentationStateError("scv2_px3_trusted_tree_mismatch")
    for ancestor, descendant in (
        (SCV2_PX3_PR148_ACCEPTED_HEAD, SCV2_PX3_ACCEPTED_MAIN),
        (SCV2_PX3_ACCEPTED_MAIN, "HEAD"),
        (str(state["implementation_evidence_head"]), "HEAD"),
    ):
        result = _run_trusted_git(
            ["merge-base", "--is-ancestor", ancestor, descendant], root=root
        )
        if result.returncode != 0:
            raise DocumentationStateError("scv2_px3_trusted_ancestry_invalid")
    parents = tuple(
        _trusted_git_value(
            root, "show", "-s", "--format=%P", SCV2_PX3_ACCEPTED_MAIN
        ).split()
    )
    if parents != SCV2_PX3_PR148_MERGE_PARENTS:
        raise DocumentationStateError("scv2_px3_pr148_merge_parent_invalid")
    if (
        _trusted_git_value(root, "rev-parse", "--abbrev-ref", "HEAD")
        != state["branch"]
    ):
        raise DocumentationStateError("scv2_px3_branch_identity_invalid")
    if state.get("current_status") in {SCV2_PX3_READY_STATUS, SCV2_PX3_CLOSURE_READY_STATUS, SCV2_PX3_MERGED_STATUS}:
        paths = _trusted_git_changed_paths(
            [
                "diff",
                "--no-ext-diff",
                "--no-renames",
                "--name-only",
                "-z",
                str(state["implementation_evidence_head"]),
                "HEAD",
                "--",
            ],
            root=root,
            error_code="scv2_px3_evidence_carry_forward_diff_unavailable",
        )
        unexpected = sorted(
            path
            for path in paths
            if path not in SCV2_PX3_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST
        )
        if unexpected:
            raise DocumentationStateError(
                "scv2_px3_evidence_behavior_drift:" + ",".join(unexpected)
            )
    if state.get('current_status') == SCV2_PX3_MERGED_STATUS:
        merge = state['protected_evidence'].get('px3_merge_commit', '')
        accepted = state['protected_evidence'].get('px3_accepted_head', '')
        expected_base = state['protected_evidence'].get('px3_expected_merge_base', '')
        if (not HEX40.fullmatch(merge) or not HEX40.fullmatch(accepted)
            or _trusted_git_value(root, 'show', '-s', '--format=%P', merge).split() != [expected_base, accepted]
            or _trusted_git_value(root, 'rev-parse', f'{merge}^{{tree}}') != _trusted_git_value(root, 'rev-parse', f'{accepted}^{{tree}}')):
            raise DocumentationStateError('scv2_px3_merge_topology_or_tree_invalid')


def validate_git_ancestry(
    state: dict[str, Any],
    *,
    root: Path = ROOT,
    implementation_evidence: dict[str, Any] | None = None,
) -> None:
    if state.get("phase_id") == "SCV2-PX1":
        _validate_scv2_px1_git_ancestry(state, root=root)
        return
    if state.get("phase_id") == "SCV2-PX2":
        _validate_scv2_px2_git_ancestry(state, root=root)
        return
    if state.get("phase_id") == "SCV2-PX3":
        _validate_scv2_px3_git_ancestry(state, root=root)
        return
    base = str(state["accepted_mainline_base"])
    implementation = str(state["implementation_evidence_head"])
    projection = str(
        state.get("protected_evidence", {}).get(
            "fl1_i2_intermediate_post_terminal_projection_head", ""
        )
    )
    current_projection = str(
        state.get("protected_evidence", {}).get(
            "fl1_i2_post_terminal_governance_projection_head", ""
        )
    )
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
            projection: str(
                state["protected_evidence"][
                    "fl1_i2_intermediate_post_terminal_projection_tree"
                ]
            ),
            current_projection: str(
                state["protected_evidence"][
                    "fl1_i2_post_terminal_governance_projection_tree"
                ]
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
        projection_check = _run_trusted_git(
            ["merge-base", "--is-ancestor", projection, "HEAD"], root=root
        )
        if projection_check.returncode != 0:
            raise DocumentationStateError(
                "fl1_i2_intermediate_post_terminal_projection_not_ancestor"
            )
        current_projection_check = _run_trusted_git(
            ["merge-base", "--is-ancestor", current_projection, "HEAD"], root=root
        )
        if current_projection_check.returncode != 0:
            raise DocumentationStateError(
                "fl1_i2_post_terminal_projection_not_ancestor"
            )

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


def _validate_scv2_px1_roadmaps(state: dict[str, Any], *, root: Path) -> None:
    marker = "<!-- CURRENT_PHASE: SCV2-PX1 -->"
    paths = (
        Path("docs/roadmap/current-mainline-roadmap.md"),
        Path("docs/project-roadmap.md"),
        Path("docs/phase-contracts.md"),
    )
    texts: dict[Path, str] = {}
    for relative in paths:
        text = (root / relative).read_text(encoding="utf-8")
        markers = re.findall(r"<!-- CURRENT_PHASE: ([A-Z0-9-]+) -->", text)
        if markers != ["SCV2-PX1"] or text.count(marker) != 1:
            raise DocumentationStateError(
                f"current_phase_conflict:{relative.as_posix()}"
            )
        texts[relative] = text
    active_text = "\n".join(texts.values())
    required_truth = (
        state["current_status"],
        str(state["active_blocker"]["code"]),
        SCV2_PX1_CONTRACT_ID,
        SCV2_PX1_PUBLIC_SCHEMA,
        "SCV2-PX1",
        "SCV2-PX2",
        "SCV2-PX3",
        "safe_to_merge=false",
        "merge_authorized=false",
        "real_source_authorized=false",
        "production_authorized=false",
    )
    if any(value not in active_text for value in required_truth):
        raise DocumentationStateError("scv2_px1_active_route_truth_missing")
    if "phase-4.5-PX1 is historical" not in active_text:
        raise DocumentationStateError("scv2_px1_historical_name_boundary_missing")
    contract_text = texts[Path("docs/phase-contracts.md")]
    if (
        "--px1-evidence" not in contract_text
        or "run_scv2_px1_pixiv_metadata_vertical_slice.py" not in contract_text
        or state["active_blocker"]["code"] not in contract_text
    ):
        raise DocumentationStateError("scv2_px1_contract_documentation_missing")


def _validate_scv2_px2_roadmaps(state: dict[str, Any], *, root: Path) -> None:
    marker = "<!-- CURRENT_PHASE: SCV2-PX2 -->"
    paths = (
        Path("docs/roadmap/current-mainline-roadmap.md"),
        Path("docs/project-roadmap.md"),
        Path("docs/phase-contracts.md"),
    )
    texts: dict[Path, str] = {}
    for relative in paths:
        text = (root / relative).read_text(encoding="utf-8")
        markers = re.findall(r"<!-- CURRENT_PHASE: ([A-Z0-9-]+) -->", text)
        if markers != ["SCV2-PX2"] or text.count(marker) != 1:
            raise DocumentationStateError(
                f"current_phase_conflict:{relative.as_posix()}"
            )
        texts[relative] = text
    active_text = "\n".join(texts.values())
    required_truth = (
        state["current_status"],
        str(state["active_blocker"]["code"]),
        SCV2_PX2_CONTRACT_ID,
        SCV2_PX2_PUBLIC_SCHEMA,
        SCV2_PX2_PR147_ACCEPTED_HEAD,
        SCV2_PX2_ACCEPTED_MAIN,
        "SCV2_PX1_MERGED",
        "SCV2-PX2",
        "SCV2-PX3",
        "px2_started=true",
        "px2_merge_authorized=false",
        "real_source_authorized=false",
        "existing_database_authorized=false",
        "production_authorized=false",
    )
    if any(value not in active_text for value in required_truth):
        raise DocumentationStateError("scv2_px2_active_route_truth_missing")
    if "phase-4.5-PX1 is historical" not in active_text:
        raise DocumentationStateError("scv2_px2_historical_name_boundary_missing")
    if state.get("current_status") == SCV2_PX2_READY_STATUS:
        contract_text = texts[Path("docs/phase-contracts.md")]
        if (
            "run_scv2_px2_pixiv_metadata_clustering.py" not in contract_text
            or "--px2-evidence" not in contract_text
        ):
            raise DocumentationStateError(
                "scv2_px2_contract_documentation_missing"
            )


def _validate_scv2_px3_roadmaps(state: dict[str, Any], *, root: Path) -> None:
    marker = "<!-- CURRENT_PHASE: SCV2-PX3 -->"
    paths = (
        Path("docs/roadmap/current-mainline-roadmap.md"),
        Path("docs/project-roadmap.md"),
        Path("docs/phase-contracts.md"),
    )
    texts: dict[Path, str] = {}
    for relative in paths:
        text = (root / relative).read_text(encoding="utf-8")
        markers = re.findall(r"<!-- CURRENT_PHASE: ([A-Z0-9-]+) -->", text)
        if markers != ["SCV2-PX3"] or text.count(marker) != 1:
            raise DocumentationStateError(
                f"current_phase_conflict:{relative.as_posix()}"
            )
        texts[relative] = text
    active_text = "\n".join(texts.values())
    required_truth = (
        state["current_status"],
        str(state["active_blocker"]["code"]),
        SCV2_PX3_CONTRACT_ID,
        SCV2_PX3_PUBLIC_SCHEMA,
        SCV2_PX3_PR148_ACCEPTED_HEAD,
        SCV2_PX3_ACCEPTED_MAIN,
        "SCV2_PX2_MERGED",
        "SCV2-PX3",
        "px3_started=true",
        f"px3_merge_authorized={str(state['authorities']['px3_merge_authorized']).lower()}",
        "real_pixiv_network_execution_authorized=false",
        "existing_database_or_app_storage_mutation_authorized=false",
        "production_authorized=false",
    )
    if any(value not in active_text for value in required_truth):
        raise DocumentationStateError("scv2_px3_active_route_truth_missing")
    if "phase-4.5-PX1 is historical" not in active_text:
        raise DocumentationStateError("scv2_px3_historical_name_boundary_missing")
    if state.get("current_status") == SCV2_PX3_READY_STATUS:
        contract_text = texts[Path("docs/phase-contracts.md")]
        if (
            "run_scv2_px3_pixiv_product_integration.py" not in contract_text
            or "--px3-evidence" not in contract_text
        ):
            raise DocumentationStateError(
                "scv2_px3_contract_documentation_missing"
            )


def validate_roadmaps(state: dict[str, Any], *, root: Path = ROOT) -> None:
    if state.get("phase_id") == "SCV2-PX1":
        _validate_scv2_px1_roadmaps(state, root=root)
        return
    if state.get("phase_id") == "SCV2-PX2":
        _validate_scv2_px2_roadmaps(state, root=root)
        return
    if state.get("phase_id") == "SCV2-PX3":
        _validate_scv2_px3_roadmaps(state, root=root)
        return
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
    canonical_plan = (
        root
        / "docs"
        / "plans"
        / "phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md"
    ).read_text(encoding="utf-8")
    required_plan_truth = (
        state["current_status"],
        f"implementation_evidence_head={FL1_I2_IMPLEMENTATION_EVIDENCE_HEAD}",
        f"implementation_evidence_tree={FL1_I2_IMPLEMENTATION_EVIDENCE_TREE}",
        "planning_approved=true",
        "implementation_authorized=true",
        "implementation_started=true",
        "implementation_completed=true",
        "safe_to_merge=false",
        "merge_authorized=false",
        "real_source_inventory_authorized=false",
        state["active_blocker"]["code"],
        FL1_I2_FINAL_OWNER_REJECTED_HEAD,
        str(FL1_I2_FINAL_OWNER_REVIEW_ID),
        "required_fix_count=4",
        "safe_downgrade_count=2",
        "deferred_count=2",
        "additional_codex_review_authorized=false",
    )
    if any(value not in canonical_plan for value in required_plan_truth):
        raise DocumentationStateError("fl1_i2_canonical_plan_current_truth_missing")
    stale_plan_truth = (
        "fl1_i2_planning_governance_pr_corrected_ready_for_owner_reaudit",
        "planning_approved=false",
        "implementation_authorized=false",
        "implementation_started=false",
        "active_blocker=pending_fl1_i2_plan_owner_audit",
    )
    if any(value in canonical_plan for value in stale_plan_truth):
        raise DocumentationStateError("fl1_i2_canonical_plan_current_truth_conflict")
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


def _render_scv2_px1_handoff(state: dict[str, Any]) -> str:
    blocker = state["active_blocker"]
    protected = state["protected_evidence"]
    if state["pr_number"] is None:
        pr_label = "Draft PR pending creation"
    elif state["draft"]:
        pr_label = f"Draft PR #{state['pr_number']}"
    else:
        pr_label = f"normal PR #{state['pr_number']}"
    lines = [
        "# Current Handoff - V.I.O.L.E.T.",
        "",
        "> Generated from `docs/state/current-phase.json`; this file is not the fact source.",
        "",
        "## Current Facts",
        "",
        f"- Phase: `SCV2-PX1` - {state['phase_title']}.",
        f"- Repository / PR: `{state['repository']}` / {pr_label}.",
        f"- Branch: `{state['branch']}`.",
        f"- Accepted mainline HEAD/tree: `{state['accepted_mainline_base']}` / `{state['accepted_mainline_tree']}`.",
        f"- Implementation evidence HEAD/tree: `{state['implementation_evidence_head']}` / `{state['implementation_evidence_tree']}`; status: `{state['implementation_evidence_status']}`.",
        f"- Status: `{state['current_status']}`.",
        f"- `target_met={str(state['target_met']).lower()}`; `safe_to_merge={str(state['safe_to_merge']).lower()}`; `route_approved={str(state['route_approved']).lower()}`.",
        f"- Manual acceptance: `{state['manual_acceptance_status']}`; `next_phase_started={str(state['next_phase_started']).lower()}`.",
        f"- Contract: `{state['pipeline_contract']['contract_id']}`; public schema: `{state['pipeline_contract']['public_schema']}`.",
        f"- Synthetic vertical slice / deterministic replay verified: `{str(state['pipeline_contract']['synthetic_vertical_slice_verified']).lower()}` / `{str(state['pipeline_contract']['deterministic_replay_verified']).lower()}`.",
        "- Contract evidence remains a local operator receipt; it is neither CI authority nor owner acceptance.",
        "",
        "## PR #146 Merge Projection",
        "",
        f"- Accepted PR HEAD/tree: `{protected['pr146_accepted_head']}` / `{protected['pr146_accepted_tree']}`.",
        f"- Merge commit/tree: `{protected['pr146_merge_commit']}` / `{protected['pr146_merge_tree']}`; merged: `{str(protected['pr146_merged']).lower()}`.",
        f"- Final review `{protected['pr146_final_review_id']}` covered `{protected['pr146_final_reviewed_head']}` and recorded `{protected['pr146_final_review_finding_count']}` unresolved, non-outdated findings.",
        "- Merge does not silently close those findings; every finding remains attached to its exact future due gate below.",
        "",
        "## PX1 Product Slice",
        "",
        "- Synthetic Pixiv/gallery-dl metadata enters the existing canonical normalization and lifecycle authority.",
        "- Existing SourceMetadataRecord, SourceNameObservation, SourceTagObservation, and provenance models remain the only source-layer persistence seam.",
        "- A deterministic Pixiv work/page aggregate exposes stable creator ID as identity and names, title, and tags only as mutable observations.",
        "- The aggregate projects into the existing SourceConcept signal semantics without cluster materialization or Entity promotion.",
        "- The repository-owned runner uses exactly two task-owned temporary SQLite databases and performs no provider, source-root, app-storage, media, or production activity.",
        "- Historical `phase-4.5-PX1` orchestration remains historical compatibility evidence; it is not the SCV2-PX1 authority.",
        "",
        "## Current Gate And Authority Boundary",
        "",
        f"- Gate: `{blocker['code']}` ({blocker['scope']}).",
        f"- Resolution: {blocker['resolution']}",
        "- `owner_accepted=false`; `safe_to_merge=false`; `merge_authorized=false`; `px2_started=false`.",
        "- `real_provider_authorized=false`; `real_source_authorized=false`; `full_import_authorized=false`; `production_authorized=false`.",
        f"- External data-plane network, existing DB, real source, media download, and production operation counts: `{protected['external_data_plane_network_operation_count']}/{protected['existing_database_access_operation_count']}/{protected['real_source_operation_count']}/{protected['media_download_operation_count']}/{protected['production_operation_count']}`.",
        "",
        "## Fixed Near-Term Route",
        "",
    ]
    for route in state["upcoming_route"]:
        lines.append(
            f"- `{route['phase_id']}` - {route['scope']}; started: `{str(route['started']).lower()}`."
        )
    lines.extend(
        [
            "",
            "## Completed Checkpoints",
            "",
        ]
    )
    for checkpoint in state["completed_checkpoints"]:
        suffix = f" - `{checkpoint['fingerprint']}`" if checkpoint.get("fingerprint") else ""
        lines.append(f"- `{checkpoint['id']}`: `{checkpoint['result']}`{suffix}.")
    lines.extend(
        [
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
    lines.extend(["", "## Deferred Debt And Exact Due Gates", ""])
    for debt in state["deferred_debt"]:
        requirements = "; ".join(debt["requirements"])
        lines.append(
            f"- `{debt['id']}` - owner: {debt['owner']}; due before: `{debt['due_before']}`; {debt['reason']} Requirements: {requirements}."
        )
    lines.extend(["", f"Updated: `{state['updated_at']}`.", ""])
    return "\n".join(lines)


def _render_scv2_px2_handoff(state: dict[str, Any]) -> str:
    blocker = state["active_blocker"]
    protected = state["protected_evidence"]
    if state["pr_number"] is None:
        pr_label = "Draft PR pending creation"
    elif state["draft"]:
        pr_label = f"Draft PR #{state['pr_number']}"
    else:
        pr_label = f"normal PR #{state['pr_number']}"
    lines = [
        "# Current Handoff - V.I.O.L.E.T.",
        "",
        "> Generated from `docs/state/current-phase.json`; this file is not the fact source.",
        "",
        "## Current Facts",
        "",
        f"- Phase: `SCV2-PX2` - {state['phase_title']}.",
        f"- Repository / PR: `{state['repository']}` / {pr_label}.",
        f"- Branch: `{state['branch']}`.",
        f"- Accepted mainline HEAD/tree: `{state['accepted_mainline_base']}` / `{state['accepted_mainline_tree']}`.",
        f"- Implementation evidence HEAD/tree: `{state['implementation_evidence_head']}` / `{state['implementation_evidence_tree']}`; status: `{state['implementation_evidence_status']}`.",
        f"- Status: `{state['current_status']}`.",
        f"- `target_met={str(state['target_met']).lower()}`; `safe_to_merge={str(state['safe_to_merge']).lower()}`; `route_approved={str(state['route_approved']).lower()}`.",
        f"- Manual acceptance: `{state['manual_acceptance_status']}`; `next_phase_started={str(state['next_phase_started']).lower()}`.",
        f"- Contract: `{state['pipeline_contract']['contract_id']}`; public schema: `{state['pipeline_contract']['public_schema']}`.",
        "- Contract evidence remains local synthetic/operator evidence; it is neither CI nor PX2 owner acceptance.",
        "",
        "## PX1 Merge Projection",
        "",
        f"- Accepted PR #147 HEAD/tree: `{protected['pr147_accepted_head']}` / `{protected['pr147_accepted_tree']}`.",
        f"- Merge commit/tree: `{protected['pr147_merge_commit']}` / `{protected['pr147_merge_tree']}`; time: `{protected['pr147_merge_time']}`.",
        f"- Merge parents: `{' / '.join(protected['pr147_merge_parents'])}`.",
        "- `SCV2_PX1_MERGED`; `px1_owner_accepted=true`; `px1_merged=true`.",
        "",
        "## PX2 Product Slice",
        "",
        "- Consume the frozen PX1 aggregate and signal-bundle contract with strict schema, logical-key, and fingerprint validation.",
        "- Reconstruct role-aware Pixiv work/page contexts and call the existing deterministic SourceConcept resolver and graph policy.",
        "- Project every actual candidate as must_link, cannot_link, or deferred_nonblocking, with a nonblocking ambiguous ledger.",
        "- Apply and replay through existing SourceConcept models only in task-owned temporary SQLite; no migration or existing database access.",
        "- Emit one versioned, deterministic, public-safe persistable cluster result without database row IDs, paths, payloads, credentials, filenames, or wall-clock identity.",
        "- Historical phase-4.5-PX1 is historical compatibility evidence, not PX2 authority.",
        "",
        "## Current Gate And Authority Boundary",
        "",
        f"- Gate: `{blocker['code']}` ({blocker['scope']}).",
        f"- Resolution: {blocker['resolution']}",
        "- `px2_started=true`; `px2_owner_accepted=false`; `px2_safe_to_merge=false`; `px2_merge_authorized=false`; `px3_started=false`.",
        "- `real_provider_authorized=false`; `real_source_authorized=false`; `existing_database_authorized=false`; `migration_authorized=false`; `full_import_authorized=false`; `production_authorized=false`.",
        f"- Existing DB/app-storage, provider network, real source, LLM, and production activity counts: `{protected['existing_db_or_app_storage_activity']}/{protected['provider_network_activity']}/{protected['real_source_activity']}/{protected['llm_activity']}/{protected['production_activity']}`.",
        "",
        "## Fixed Near-Term Route",
        "",
    ]
    for route in state["upcoming_route"]:
        lines.append(
            f"- `{route['phase_id']}` - {route['scope']}; started: `{str(route['started']).lower()}`."
        )
    lines.extend(["", "## Completed Checkpoints", ""])
    for checkpoint in state["completed_checkpoints"]:
        suffix = (
            f" - `{checkpoint['fingerprint']}`"
            if checkpoint.get("fingerprint")
            else ""
        )
        lines.append(f"- `{checkpoint['id']}`: `{checkpoint['result']}`{suffix}.")
    lines.extend(
        [
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
    lines.extend(["", "## Deferred Debt And Exact Due Gates", ""])
    for debt in state["deferred_debt"]:
        requirements = "; ".join(debt["requirements"])
        lines.append(
            f"- `{debt['id']}` - owner: {debt['owner']}; due before: `{debt['due_before']}`; {debt['reason']} Requirements: {requirements}."
        )
    lines.extend(["", f"Updated: `{state['updated_at']}`.", ""])
    return "\n".join(lines)


def _render_scv2_px3_handoff(state: dict[str, Any]) -> str:
    protected = state["protected_evidence"]
    contract = state["pipeline_contract"]
    pr_label = (
        "PR pending creation"
        if state["pr_number"] is None
        else f"PR #{state['pr_number']}"
    )
    lines = [
        "# Current Handoff - V.I.O.L.E.T.",
        "",
        "> Generated from `docs/state/current-phase.json`; this file is not the fact source.",
        "",
        "## Current Facts",
        "",
        f"- Phase: `SCV2-PX3` - {state['phase_title']}.",
        f"- Repository / PR: `{state['repository']}` / {pr_label}.",
        f"- Branch: `{state['branch']}`.",
        f"- Status: `{state['current_status']}`.",
        f"- Implementation evidence HEAD/tree: `{state['implementation_evidence_head']}` / `{state['implementation_evidence_tree']}`.",
        f"- `target_met={str(state['target_met']).lower()}`; `safe_to_merge={str(state['safe_to_merge']).lower()}`; `route_approved=false`.",
        f"- `px3_started=true`; `px3_owner_accepted={str(protected['px3_owner_accepted']).lower()}`; `px3_merge_authorized={str(protected['px3_merge_authorized']).lower()}`.",
        "",
        "## PX2 Merge Projection",
        "",
        f"- Accepted PR #148 HEAD/tree: `{protected['pr148_accepted_head']}` / `{protected['pr148_accepted_tree']}`.",
        f"- Merge commit/tree: `{protected['pr148_merge_commit']}` / `{protected['pr148_merge_tree']}`.",
        f"- Merge parents: `{','.join(protected['pr148_merge_parents'])}`.",
        f"- Merge time: `{protected['pr148_merge_time']}`.",
        "- `SCV2_PX2_MERGED`; accepted tree equals merge tree; no parallel main commit was present.",
        "",
        "## Final Product Route",
        "",
        "- PX1 repository-owned Pixiv aggregate/signal contract remains the input authority.",
        "- PX2 existing SourceConcept resolver, graph policy, candidate dispositions, ambiguity ledger, and persistence seam are reused.",
        "- PX3 adds product-owned run persistence, dry-run/apply/rollback, read APIs, and an operable admin UI.",
        "- The real provider adapter is wired but real network, credentials, source, and user-data execution remain disabled.",
        "- Controlled provider smoke, existing-database canary, backup/restore, and 1%-5% import remain owner gates inside PX3.",
        "- Historical phase-4.5-PX1 is historical compatibility evidence, not current authority.",
        "",
        "## Executable Contract",
        "",
        f"- Contract: `{contract['contract_id']}`.",
        f"- Public schema: `{contract['public_schema']}`.",
        f"- Repository gap map completed: `{str(contract['repository_gap_map_completed']).lower()}`.",
        f"- PX1/PX2 reused: `{str(contract['px1_px2_reused']).lower()}`.",
        f"- Product persistence verified: `{str(contract['product_persistence_verified']).lower()}`.",
        f"- Dry-run/apply/rollback verified: `{str(contract['dry_run_apply_rollback_verified']).lower()}`.",
        f"- API/UI verified: `{str(contract['api_ui_verified']).lower()}`.",
        f"- Synthetic browser E2E verified: `{str(contract['synthetic_browser_e2e_verified']).lower()}`.",
        f"- Controlled canary entrypoints verified: `{str(contract['controlled_canary_entrypoints_verified']).lower()}`.",
        "- Hosted CI and owner authority remain separate and are not synthesized by local evidence.",
        "",
        "## Current Gate And Authority",
        "",
        f"- Gate: `{state['active_blocker']['code']}`.",
        f"- Scope: {state['active_blocker']['scope']}",
        f"- Resolution: {state['active_blocker']['resolution']}",
        "- `repository_migration_code_authorized=true`; migrations may be tested only on task-owned temporary databases.",
        "- `synthetic_local_server_browser_e2e_authorized=true`.",
        "- STOP: normal startup executes Base.metadata.create_all() and schema migration. Back up and successfully restore before the first normal startup against any existing database.",
        "- Next owner authorization only: backup/restore -> 1-5 work metadata-only provider smoke -> existing DB read-only dry-run -> accept exact selection/result fingerprints -> 1% apply canary -> gallery search/media detail acceptance -> replay/rollback checks.",
        "- `real_pixiv_network_execution_authorized=false`; real gallery-dl execution is likewise forbidden.",
        "- `existing_database_or_app_storage_mutation_authorized=false`.",
        "- `real_source_or_icloud_access_authorized=false`; `provider_credentials_authorized=false`.",
        "- `user_data_import_authorized=false`; LLM execution is forbidden; `production_authorized=false`; `full_library_import_authorized=false`.",
        "",
        "## Completed Checkpoints",
        "",
    ]
    for checkpoint in state["completed_checkpoints"]:
        suffix = (
            f" - `{checkpoint['fingerprint']}`"
            if checkpoint.get("fingerprint")
            else ""
        )
        lines.append(f"- `{checkpoint['id']}`: `{checkpoint['result']}`{suffix}.")
    lines.extend(
        [
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
    lines.extend(["", "## Deferred Debt And Exact Due Gates", ""])
    for debt in state["deferred_debt"]:
        lines.append(
            f"- `{debt['id']}` - due before `{debt['due_before']}`; {debt['reason']}"
        )
    lines.extend(["", f"Updated: `{state['updated_at']}`.", ""])
    return "\n".join(lines)


def render_handoff(state: dict[str, Any]) -> str:
    """Render the complete public-safe I2 planning handoff."""

    if state.get("phase_id") == "SCV2-PX1":
        return _render_scv2_px1_handoff(state)
    if state.get("phase_id") == "SCV2-PX2":
        return _render_scv2_px2_handoff(state)
    if state.get("phase_id") == "SCV2-PX3":
        return _render_scv2_px3_handoff(state)

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
        f"- Current I2 final owner-adjudicated implementation evidence HEAD/tree: `{state['implementation_evidence_head']}` / `{state['protected_evidence']['fl1_i2_implementation_evidence_tree']}`; contract: `{state['protected_evidence']['fl1_i2_contract_id']}`; fourteen delivery gates are represented only by this synthetic local-operator evidence and remain pending direct owner audit.",
        f"- Intermediate post-terminal governance projection HEAD/tree: `{state['protected_evidence']['fl1_i2_intermediate_post_terminal_projection_head']}` / `{state['protected_evidence']['fl1_i2_intermediate_post_terminal_projection_tree']}`; it was superseded after canonical receipt exposed and the implementation closed a task-owned Windows readonly cleanup gap.",
        f"- Current post-terminal governance projection HEAD/tree: `{state['protected_evidence']['fl1_i2_post_terminal_governance_projection_head']}` / `{state['protected_evidence']['fl1_i2_post_terminal_governance_projection_tree']}`; the current HEAD is a governance-only exact-binding carry-forward.",
        f"- PR #146 correction history: review `{state['protected_evidence']['fl1_i2_bounded_correction_review_id']}` and its `{len(state['protected_evidence']['fl1_i2_bounded_correction_thread_ids'])}` findings remain regression-covered; review `{state['protected_evidence']['fl1_i2_final_convergence_review_id']}` and its `{len(state['protected_evidence']['fl1_i2_final_convergence_thread_ids'])}` findings remain regression-covered; terminal review `{state['protected_evidence']['fl1_i2_post_terminal_review_id']}` rejected `{state['protected_evidence']['fl1_i2_terminal_rejected_head']}` / `{state['protected_evidence']['fl1_i2_terminal_rejected_tree']}` with `{len(state['protected_evidence']['fl1_i2_post_terminal_findings'])}` accepted findings superseded by later additive evidence.",
        f"- Final owner review `{state['protected_evidence']['fl1_i2_final_owner_review_id']}` rejected `{state['protected_evidence']['fl1_i2_final_owner_rejected_head']}` / `{state['protected_evidence']['fl1_i2_final_owner_rejected_tree']}`: required fixes `{state['protected_evidence']['fl1_i2_final_owner_required_fix_count']}`, safe downgrades `{state['protected_evidence']['fl1_i2_final_owner_safe_downgrade_count']}`, exact-gate deferrals `{state['protected_evidence']['fl1_i2_final_owner_deferred_count']}`; `additional_codex_review_authorized=false`.",
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
    maximum = (
        180
        if state.get("phase_id") in {"SCV2-PX1", "SCV2-PX2", "SCV2-PX3"}
        else 115
    )
    if not 55 <= line_count <= maximum:
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
