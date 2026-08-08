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
    "updated_at",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_FORBIDDEN = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"(?i)\b(?:authorization|cookie|set-cookie)\s*[:=]"),
    re.compile(r"(?i)\b(?:api[_-]?key|refresh[_-]?token|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\.local_manifests"),
)
FL1_STATUS = "fl1_i1_synthetic_implementation_ready_for_owner_audit"
FL1_BLOCKER = "pending_owner_audit_and_real_source_scope_authorization"
FL1_MANUAL_STATUS = "pending_owner_audit"
FL1_APPROVED_PLANNING_HEAD = "db90457d51a39b5dc930afc2a92a6ef3139a2760"
FL1_ROUTE_SCOPE = "FL1-I1 planning and synthetic read-only inventory implementation only"
FL1_COMPLETED_SCOPE = "FL1-P1 isolation/safety/contract/ledger implementation only"
FL1_PLAN_MERGE_COMMIT = "9ce1128be643c0eaa998ccdff8890d76196ce7db"
FL1_P1_MERGE_COMMIT = "36100bfa0317387e064cd87b2e753eca3a201b5e"
FL1_P1_FINAL_HEAD = "495a6506b25bb27747ebc27e341a06de4860aaa4"
FL1_P1_ACCEPTED_TREE = "05ad4b4829cd2b556a3644ceaf4620b98200f042"
FL1_I1_FOUNDATION_HEAD = "b0d0d205431eae5d7de6a5b7fc84e104b46f8a72"
FL1_I1_IMPLEMENTATION_HEAD = "102be797f845d518817d7d01d17d65b65a4fc243"


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
    if (
        state["draft"] is not True
        or state["current_status"] != FL1_STATUS
        or state["target_met"] is not False
        or state["safe_to_merge"] is not False
        or state["route_approved"] is not False
        or state["route_scope"] != FL1_ROUTE_SCOPE
        or state["planning_approved"] is not True
        or state["approved_planning_head"] != FL1_APPROVED_PLANNING_HEAD
        or state["manual_acceptance_status"] != FL1_MANUAL_STATUS
        or state["next_phase_started"] is not True
    ):
        raise DocumentationStateError("fl1_i1_status_fields_conflict")
    blocker = state["active_blocker"]
    if blocker.get("code") != FL1_BLOCKER:
        raise DocumentationStateError("fl1_i1_blocker_conflict")

    boundary = state["planning_boundary"]
    expected_boundary = {
        "planning_only": False,
        "implementation_authorized": True,
        "implementation_scope": FL1_ROUTE_SCOPE,
        "completed_implementation_scope": FL1_COMPLETED_SCOPE,
        "implementation_completed": True,
        "owner_audit_pending": True,
        "data_execution_authorized": False,
        "production_authorized": False,
        "database_access_authorized": False,
        "database_data_execution_authorized": False,
        "source_root_access_authorized": False,
        "synthetic_source_fixture_access_authorized": True,
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
        raise DocumentationStateError("fl1_i1_boundary_invalid")

    prior = state["prior_phase_acceptance"]
    if not isinstance(prior, dict) or any(
        (
            prior.get("phase_id") != "SCV2-FL1-P1",
            prior.get("merge_commit") != FL1_P1_MERGE_COMMIT,
            prior.get("accepted_tree") != FL1_P1_ACCEPTED_TREE,
            prior.get("final_pr_head") != FL1_P1_FINAL_HEAD,
            prior.get("status") != "fl1_p1_owner_accepted_and_squash_merged",
            prior.get("review_observation_seconds_at_least", 0) < 300,
            prior.get("review_count") != 0,
            prior.get("unresolved_thread_count") != 0,
            prior.get("failing_or_pending_check_count") != 0,
            prior.get("real_source_inventory_operation_count") != 0,
            prior.get("database_operation_count") != 0,
            prior.get("external_operation_count") != 0,
        )
    ):
        raise DocumentationStateError("fl1_prior_phase_acceptance_invalid")

    protected = state["protected_evidence"]
    if not isinstance(protected, dict) or any(
        protected.get(key) != 0
        for key in (
            "database_operation_count",
            "existing_database_read_operation_count",
            "existing_database_write_operation_count",
            "app_storage_write_operation_count",
            "real_source_inventory_operation_count",
            "provider_operation_count",
            "llm_operation_count",
            "media_or_thumbnail_operation_count",
            "stable_replay_operation_count",
        )
    ):
        raise DocumentationStateError("fl1_operation_counts_nonzero")
    if protected.get("production_consumed_or_modified_during_fl1_i1") is not False:
        raise DocumentationStateError("fl1_production_boundary_invalid")
    if protected.get("synthetic_source_fixture_activity_performed") is not True:
        raise DocumentationStateError("fl1_i1_synthetic_fixture_evidence_invalid")
    if protected.get("approved_planning_head") != FL1_APPROVED_PLANNING_HEAD:
        raise DocumentationStateError("fl1_approved_planning_head_evidence_invalid")
    if (
        state["accepted_mainline_base"] != FL1_P1_MERGE_COMMIT
        or protected.get("fl1_plan_merge_commit") != FL1_PLAN_MERGE_COMMIT
        or protected.get("fl1_p1_merge_commit") != FL1_P1_MERGE_COMMIT
        or protected.get("fl1_p1_final_pr_head") != FL1_P1_FINAL_HEAD
        or protected.get("fl1_p1_accepted_tree") != FL1_P1_ACCEPTED_TREE
    ):
        raise DocumentationStateError("fl1_i1_mainline_base_invalid")
    if (
        state["implementation_evidence_head"] != FL1_I1_IMPLEMENTATION_HEAD
        or protected.get("fl1_i1_implementation_evidence_head")
        != FL1_I1_IMPLEMENTATION_HEAD
        or protected.get("fl1_i1_foundation_head") != FL1_I1_FOUNDATION_HEAD
    ):
        raise DocumentationStateError("fl1_i1_implementation_head_invalid")


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
    for pattern in PUBLIC_FORBIDDEN:
        if pattern.search(serialized):
            raise DocumentationStateError(f"public_state_redaction_failure:{pattern.pattern}")


def validate_git_ancestry(state: dict[str, Any], *, root: Path = ROOT) -> None:
    for field in ("accepted_mainline_base", "implementation_evidence_head"):
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", str(state[field]), "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            raise DocumentationStateError(f"{field}_not_ancestor_of_head")


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
        f"- Phase: `{state['phase_id']}` — {state['phase_title']}.",
        f"- Repository / PR: `{state['repository']}` / {pr_label}.",
        f"- Branch: `{state['branch']}`.",
        f"- Accepted mainline base: `{state['accepted_mainline_base']}`.",
        f"- Implementation evidence HEAD: `{state['implementation_evidence_head']}`.",
        f"- Status: `{state['current_status']}`.",
        f"- `target_met={str(state['target_met']).lower()}`; `safe_to_merge={str(state['safe_to_merge']).lower()}`; `route_approved={str(state['route_approved']).lower()}`.",
        f"- `manual_acceptance_status={state['manual_acceptance_status']}`; `next_phase_started={str(state['next_phase_started']).lower()}` (I1 synthetic foundation implemented; owner audit and exact real-source scope remain pending).",
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
            f"- Public state boundary: `{state['public_state_boundary']}`.",
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


def check_documentation_state(*, root: Path = ROOT) -> dict[str, Any]:
    state = load_state(root / "docs" / "state" / "current-phase.json")
    validate_state(state, root=root)
    if root.resolve() == ROOT.resolve():
        validate_git_ancestry(state, root=root)
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
    args = parser.parse_args(argv)
    try:
        state = load_state()
        if args.render:
            validate_state(state)
            sys.stdout.write(render_handoff(state))
            return 0
        if args.write:
            validate_state(state)
            validate_roadmaps(state)
            write_handoff(state)
        result = check_documentation_state()
    except DocumentationStateError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
