"""Fail-closed current-phase documentation state checker and handoff renderer."""

from __future__ import annotations

import argparse
import json
import re
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
PUBLIC_FORBIDDEN = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"(?i)\b(?:authorization|cookie|set-cookie)\s*[:=]"),
    re.compile(r"(?i)\b(?:api[_-]?key|refresh[_-]?token|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\.local_manifests"),
)


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
        if state["target_met"] or state["safe_to_merge"] or state["route_approved"]:
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
    if "fresh isolated Replay" not in joined_authorized:
        raise DocumentationStateError("fresh_replay_authorization_missing")
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
            f"- Package strategy: `{strategy['package_schema_version']}` with stable source keys/fingerprints only.",
            f"- Fresh Replay creation limit: `{strategy['fresh_replay_database_creation_limit']}`; external-call budget: `{strategy['external_call_budget']}`.",
            "- `enpera` remains one governed localization manual case and is not a Replay blocker.",
            "",
            "## Allowed / Forbidden",
            "",
            "- Allowed: DOC-GOV-01, package-v2 offline validation, immutable-evidence cross-check, one fresh Replay, independent graph/search validation.",
            "- Forbidden: mutation of failed retry2 Replay; provider/Pixiv/gallery-dl/LLM/media calls; Primary/acquisition/localization replay.",
            "- Forbidden: production, FL1, Provider-2, Entity/truth/media_tags promotion, merge, Ready transition, reviewer trigger, main push, or force-push.",
            "",
            "## Next Action",
            "",
            f"- Required checkpoint: `{state['next_required_checkpoint']}`.",
            "- After package-v2 and immutable-evidence gates pass, create the single fresh Replay and continue to owner manual acceptance.",
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
            "",
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


def check_documentation_state(*, root: Path = ROOT) -> dict[str, Any]:
    state = load_state(root / "docs" / "state" / "current-phase.json")
    validate_state(state, root=root)
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
    args = parser.parse_args(argv)
    try:
        state = load_state()
        if args.render:
            validate_state(state)
            sys.stdout.write(render_handoff(state))
            return 0
        result = check_documentation_state()
    except DocumentationStateError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
