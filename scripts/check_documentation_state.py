"""Fail-closed current-phase documentation state checker and handoff renderer."""

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
ROADMAP_PATHS = (
    ROOT / "docs" / "roadmap" / "current-mainline-roadmap.md",
    ROOT / "docs" / "project-roadmap.md",
)
CONTRACT_PATH = ROOT / "docs" / "phase-contracts.md"
SCHEMA_VERSION = "violet.current-phase.v1"
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
    *STATUS_FIELDS,
    "completed_checkpoints",
    "active_blocker",
    "owner_decisions",
    "authorized_operations",
    "forbidden_operations",
    "protected_evidence",
    "public_state_boundary",
    "current_replay_strategy",
    "next_required_checkpoint",
    "durable_links",
    "deferred_debt",
    "updated_at",
}
MANUAL_STATUSES = {
    "not_started_replay_recovery",
    "pending_user",
    "accepted_user",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_FORBIDDEN = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"(?i)\b(?:authorization|cookie|set-cookie)\s*[:=]"),
    re.compile(r"(?i)\b(?:api[_-]?key|refresh[_-]?token|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\.local_manifests"),
)
PENDING_USER_STATUS = "automated_sv1b_candidate_ready_manual_acceptance_pending"
PENDING_USER_BLOCKER = "pending_user_manual_acceptance"
PENDING_USER_FORBIDDEN_AUTHORIZATION_TERMS = (
    "create",
    "creation",
    "import",
    "derive",
    "derivation",
    "rebuild",
    "re-derive",
)
COMPLETED_FUTURE_COMMANDS = (
    "commit this final public state",
    "create the fresh replay",
    "import the acquired",
    "derive the replay",
    "write the one non-overwriting final git binding",
)
AUTHORITATIVE_STATUS_MARKER = "AUTHORITATIVE_CURRENT_STATUS"
AUTHORITATIVE_MANUAL_MARKER = "AUTHORITATIVE_MANUAL_ACCEPTANCE_STATUS"
HISTORICAL_STATUS_MARKER = "HISTORICAL_STATUSES_BELOW: historical_superseded"


class DocumentationStateError(ValueError):
    """Raised when the public current-state contract is inconsistent."""


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentationStateError(f"current_phase_unreadable:{exc}") from exc
    if not isinstance(payload, dict):
        raise DocumentationStateError("current_phase_root_must_be_object")
    return payload


def _require_nonempty_list(state: dict[str, Any], key: str) -> list[Any]:
    value = state.get(key)
    if not isinstance(value, list) or not value:
        raise DocumentationStateError(f"{key}_must_be_nonempty_list")
    return value


def validate_state(state: dict[str, Any], *, root: Path = ROOT) -> None:
    missing = sorted(REQUIRED_FIELDS - state.keys())
    if missing:
        raise DocumentationStateError(f"missing_fields:{','.join(missing)}")
    if state["schema_version"] != SCHEMA_VERSION:
        raise DocumentationStateError("unsupported_schema_version")
    if not HEX40.fullmatch(str(state["accepted_mainline_base"])):
        raise DocumentationStateError("accepted_mainline_base_invalid")
    if not HEX40.fullmatch(str(state["implementation_evidence_head"])):
        raise DocumentationStateError("implementation_evidence_head_invalid")
    if state["repository"] != "kyloris0660/VIOLET":
        raise DocumentationStateError("repository_mismatch")
    if state["pr_number"] != 139 or state["draft"] is not True:
        raise DocumentationStateError("pr_or_draft_mismatch")
    for key in ("target_met", "safe_to_merge", "route_approved", "next_phase_started"):
        if not isinstance(state[key], bool):
            raise DocumentationStateError(f"{key}_must_be_boolean")
    if state["manual_acceptance_status"] not in MANUAL_STATUSES:
        raise DocumentationStateError("manual_acceptance_status_invalid")
    if state["phase_id"] == "SCV2-SV1B" and state["next_phase_started"]:
        raise DocumentationStateError("sv1b_cannot_start_next_phase")
    if state["manual_acceptance_status"] == "pending_user":
        if (
            state["current_status"] != PENDING_USER_STATUS
            or state["target_met"]
            or state["safe_to_merge"]
            or state["route_approved"]
            or state["next_phase_started"]
        ):
            raise DocumentationStateError("pending_user_status_fields_conflict")
    blocker = state["active_blocker"]
    if not isinstance(blocker, dict) or not blocker.get("code") or not blocker.get("resolution"):
        raise DocumentationStateError("active_blocker_invalid")
    _require_nonempty_list(state, "owner_decisions")
    authorized = _require_nonempty_list(state, "authorized_operations")
    forbidden = _require_nonempty_list(state, "forbidden_operations")
    if not state["next_required_checkpoint"]:
        raise DocumentationStateError("next_required_checkpoint_missing")
    joined_authorized = "\n".join(map(str, authorized))
    joined_forbidden = "\n".join(map(str, forbidden))
    if state["manual_acceptance_status"] == "pending_user":
        if blocker.get("code") != PENDING_USER_BLOCKER:
            raise DocumentationStateError("pending_user_blocker_conflict")
        forbidden_authorizations = [
            term
            for term in PENDING_USER_FORBIDDEN_AUTHORIZATION_TERMS
            if re.search(rf"\b{re.escape(term)}\b", joined_authorized, re.IGNORECASE)
        ]
        if forbidden_authorizations:
            raise DocumentationStateError(
                "pending_user_database_operation_authorized:"
                + ",".join(forbidden_authorizations)
            )
        binding_authorizations = [
            operation
            for operation in authorized
            if re.search(
                r"\bfinal binding v\d+(?:-r\d+)?\b",
                str(operation),
                re.IGNORECASE,
            )
        ]
        binding_versions = (
            re.findall(
                r"\bfinal binding (v\d+(?:-r\d+)?)\b",
                str(binding_authorizations[0]),
                re.IGNORECASE,
            )
            if len(binding_authorizations) == 1
            else []
        )
        if len(binding_authorizations) != 1 or len(binding_versions) != 1:
            raise DocumentationStateError(
                "pending_user_active_binding_authorization_invalid"
            )
        stale_commands = [
            command
            for command in COMPLETED_FUTURE_COMMANDS
            if command in str(blocker["resolution"]).casefold()
        ]
        if stale_commands:
            raise DocumentationStateError(
                "blocker_resolution_contains_completed_future_command:"
                + ",".join(stale_commands)
            )
    for required in ("provider", "LLM", "failed retry2 Replay", "merge"):
        if required.lower() not in joined_forbidden.lower():
            raise DocumentationStateError(f"forbidden_operation_missing:{required}")
    strategy = state["current_replay_strategy"]
    if strategy.get("package_schema_version") != "sv1b.stable-replay-evidence.v2":
        raise DocumentationStateError("replay_package_version_invalid")
    if strategy.get("fresh_replay_database_creation_limit") != 1:
        raise DocumentationStateError("fresh_replay_creation_limit_invalid")
    if strategy.get("external_call_budget") != 0:
        raise DocumentationStateError("external_call_budget_must_be_zero")
    if state.get("public_state_boundary") != (
        "public_safe_governance_only_no_private_proof_payloads_or_paths"
    ):
        raise DocumentationStateError("public_state_boundary_invalid")
    if state["phase_id"] == "SCV2-SV1B":
        protected = state["protected_evidence"]
        if (
            protected.get("canonical_phase_acquired_membership_count")
            != 7271
            or protected.get("canonical_phase_acquired_missing_count") != 0
            or protected.get(
                "canonical_phase_acquired_unsupported_count"
            )
            != 0
            or not HEX64.fullmatch(
                str(
                    protected.get(
                        "canonical_phase_acquired_membership_fingerprint"
                    )
                    or ""
                )
            )
            or protected.get(
                "superseded_candidate_provenance_membership_count"
            )
            != 7257
            or protected.get("production_library_consumed_or_modified")
            is not False
        ):
            raise DocumentationStateError(
                "sv1b_canonical_phase_membership_state_invalid"
            )
    for link in _require_nonempty_list(state, "durable_links"):
        if not isinstance(link, dict) or not link.get("label") or not link.get("path"):
            raise DocumentationStateError("durable_link_invalid")
        target = (root / link["path"]).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            raise DocumentationStateError(f"durable_link_missing:{link.get('path')}")
    debts = _require_nonempty_list(state, "deferred_debt")
    for debt in debts:
        if not all(debt.get(key) for key in ("id", "owner", "reason", "due_before")):
            raise DocumentationStateError("deferred_debt_invalid")
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    for pattern in PUBLIC_FORBIDDEN:
        if pattern.search(serialized):
            raise DocumentationStateError(f"public_state_redaction_failure:{pattern.pattern}")


def validate_git_ancestry(state: dict[str, Any], *, root: Path = ROOT) -> None:
    for field in ("accepted_mainline_base", "implementation_evidence_head"):
        completed = subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                str(state[field]),
                "HEAD",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            raise DocumentationStateError(f"{field}_not_ancestor_of_head")


def validate_linked_incident_state(
    state: dict[str, Any],
    *,
    root: Path = ROOT,
) -> None:
    incident_links = [
        link
        for link in state["durable_links"]
        if "incident" in str(link.get("label", "")).casefold()
    ]
    if len(incident_links) != 1:
        raise DocumentationStateError("active_incident_link_count_invalid")
    incident = (root / incident_links[0]["path"]).read_text(encoding="utf-8")
    top = "\n".join(incident.splitlines()[:20])
    required = (
        f"<!-- {AUTHORITATIVE_STATUS_MARKER}: {state['current_status']} -->",
        (
            f"<!-- {AUTHORITATIVE_MANUAL_MARKER}: "
            f"{state['manual_acceptance_status']} -->"
        ),
    )
    if any(marker not in top for marker in required):
        raise DocumentationStateError("incident_authoritative_state_mismatch")
    if HISTORICAL_STATUS_MARKER not in incident:
        raise DocumentationStateError("incident_historical_status_marker_missing")

    summary_path = (
        root
        / "docs"
        / "reports"
        / "phase-4.5-scv2-sv1b-replay-trusted-provenance-checkpoint-summary.json"
    )
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentationStateError(f"incident_summary_unreadable:{exc}") from exc
    if summary.get("record_role") != "historical_forensic_checkpoint":
        raise DocumentationStateError("incident_summary_role_not_historical")
    if summary.get("authoritative_current_state_path") != (
        "docs/state/current-phase.json"
    ):
        raise DocumentationStateError("incident_summary_authoritative_path_invalid")
    if not summary.get("captured_status") or not summary.get(
        "captured_manual_acceptance_status"
    ):
        raise DocumentationStateError("incident_summary_captured_state_missing")
    if summary.get("status") != summary.get("captured_status"):
        raise DocumentationStateError("incident_summary_status_role_ambiguous")
    if summary.get("superseded_by") != state["current_status"]:
        raise DocumentationStateError("incident_summary_supersession_mismatch")


def validate_roadmaps(state: dict[str, Any], *, root: Path = ROOT) -> None:
    marker = f"<!-- CURRENT_PHASE: {state['phase_id']} -->"
    for path in (
        root / "docs" / "roadmap" / "current-mainline-roadmap.md",
        root / "docs" / "project-roadmap.md",
        root / "docs" / "phase-contracts.md",
    ):
        text = path.read_text(encoding="utf-8")
        if text.count(marker) != 1:
            raise DocumentationStateError(
                f"current_phase_marker_count:{path.relative_to(root)}:{text.count(marker)}"
            )
        conflicting = re.findall(r"<!-- CURRENT_PHASE: ([A-Z0-9-]+) -->", text)
        if conflicting != [state["phase_id"]]:
            raise DocumentationStateError(f"current_phase_conflict:{path.relative_to(root)}")
    contract = (root / "docs" / "phase-contracts.md").read_text(encoding="utf-8")
    if state["active_blocker"]["code"] not in contract:
        raise DocumentationStateError("active_blocker_missing_from_contract")


def _link_for_handoff(link: dict[str, str]) -> str:
    path = link["path"]
    if not path.startswith("docs/"):
        raise DocumentationStateError(f"handoff_link_outside_docs:{path}")
    return f"[{link['label']}]({path.removeprefix('docs/')})"


def render_handoff(state: dict[str, Any]) -> str:
    blocker = state["active_blocker"]
    strategy = state["current_replay_strategy"]
    lines = [
        "# Current Handoff - V.I.O.L.E.T.",
        "",
        "> Generated from `docs/state/current-phase.json`; this file is not the fact source.",
        "",
        "## Current Facts",
        "",
        f"- Phase: `{state['phase_id']}` — {state['phase_title']}.",
        f"- Repository / PR: `{state['repository']}` / Draft PR #{state['pr_number']}.",
        f"- Branch: `{state['branch']}`.",
        f"- Accepted mainline base: `{state['accepted_mainline_base']}`.",
        f"- Implementation evidence HEAD: `{state['implementation_evidence_head']}`.",
        f"- Status: `{state['current_status']}`.",
        f"- `target_met={str(state['target_met']).lower()}`; `safe_to_merge={str(state['safe_to_merge']).lower()}`; `route_approved={str(state['route_approved']).lower()}`.",
        f"- `manual_acceptance_status={state['manual_acceptance_status']}`; `next_phase_started={str(state['next_phase_started']).lower()}`.",
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
            "## Current Blocker And Owner Decision",
            "",
            f"- Blocker: `{blocker['code']}` ({blocker['scope']}).",
            f"- Resolution: {blocker['resolution']}",
            f"- Failed retry2 Replay: `{strategy['failed_replay_disposition']}`; no in-place repair.",
            f"- Package strategy: `{strategy['package_schema_version']}` with stable source keys/fingerprints only; public state boundary: `{state['public_state_boundary']}`.",
            f"- Fresh Replay creation limit: `{strategy['fresh_replay_database_creation_limit']}`; external-call budget: `{strategy['external_call_budget']}`.",
            "",
            "## Allowed / Forbidden",
            "",
            "- Allowed: "
            + "; ".join(
                str(operation)
                for operation in state["authorized_operations"]
            )
            + ".",
            "- Forbidden: mutation of failed retry2 Replay; provider/Pixiv/gallery-dl/LLM/media calls; Primary/acquisition/localization replay.",
            "- Forbidden: production, FL1, Provider-2, Entity/truth/media_tags promotion, merge, Ready transition, reviewer trigger, main push, or force-push.",
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
    lines.extend(
        [
            "",
            "## Deferred Debt",
            "",
        ]
    )
    for debt in state["deferred_debt"]:
        lines.append(
            f"- `{debt['id']}` — owner: {debt['owner']}; due before: `{debt['due_before']}`; {debt['reason']}"
        )
    lines.extend(
        [
            f"Updated: `{state['updated_at']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def check_handoff(state: dict[str, Any], *, path: Path = HANDOFF_PATH) -> None:
    expected = render_handoff(state)
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        raise DocumentationStateError("generated_handoff_drift")
    line_count = len(actual.splitlines())
    if not 40 <= line_count <= 60:
        raise DocumentationStateError(f"handoff_line_count_out_of_range:{line_count}")


def write_handoff(
    state: dict[str, Any],
    *,
    path: Path = HANDOFF_PATH,
) -> None:
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
    validate_linked_incident_state(state, root=root)
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
