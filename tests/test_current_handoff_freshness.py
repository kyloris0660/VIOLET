from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path, PureWindowsPath

import pytest

from scripts import check_documentation_state as documentation_state


ROOT = Path(__file__).resolve().parents[1]


def _state() -> dict[str, object]:
    return json.loads(
        (ROOT / "docs" / "state" / "current-phase.json").read_text(
            encoding="utf-8"
        )
    )


def _copy_docs_root(tmp_path: Path) -> Path:
    copied = tmp_path / "repo"
    shutil.copytree(ROOT / "docs", copied / "docs")
    (copied / "AGENTS.md").write_text(
        (ROOT / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return copied


def test_current_phase_schema_and_i2_planning_boundary_are_exact() -> None:
    state = _state()

    documentation_state.validate_state(state)
    assert state["schema_version"] == "violet.current-phase.v2"
    assert state["phase_id"] == "SCV2-FL1-I2"
    assert state["pr_number"] == documentation_state.FL1_I2_PR_NUMBER
    assert state["branch"] == documentation_state.FL1_I2_BRANCH
    assert state["accepted_mainline_base"] == documentation_state.FL1_I2_ACCEPTED_MAIN
    assert state["current_status"] == documentation_state.FL1_I2_STATUS
    assert state["planning_authorized"] is True
    assert state["planning_completed"] is True
    assert state["planning_approved"] is False
    assert state["target_met"] is False
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    boundary = state["planning_boundary"]
    assert boundary["planning_only"] is True
    assert boundary["implementation_authorized"] is False
    assert boundary["implementation_started"] is False
    assert boundary["real_inventory_started"] is False
    assert boundary["real_source_inventory_authorized"] is False
    assert boundary["database_access_authorized"] is False
    assert boundary["app_storage_write_authorized"] is False
    assert boundary["provider_or_llm_authorized"] is False
    assert boundary["production_authorized"] is False


def test_fl1_i2_pr_number_exact_binding_accepts_145() -> None:
    state = copy.deepcopy(_state())
    state["pr_number"] = documentation_state.FL1_I2_PR_NUMBER

    documentation_state.validate_state(state)


@pytest.mark.parametrize("pr_number", [None, 144, 146])
def test_fl1_i2_pr_number_exact_binding_fails_closed(
    pr_number: int | None,
) -> None:
    state = copy.deepcopy(_state())
    state["pr_number"] = pr_number

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_pr_number_invalid",
    ):
        documentation_state.validate_state(state)


def test_pr144_acceptance_merge_and_terminal_review_are_exact() -> None:
    state = _state()
    prior = state["prior_phase_acceptance"]
    upstream = state["upstream_pr_state"]

    assert state["previous_phase"] == "SCV2-FL1-I1"
    assert state["previous_phase_status"] == "owner_accepted_and_merge_commit_merged"
    assert state["previous_phase_accepted_scope"] == (
        "synthetic_and_new_temporary_fixture_foundation_only"
    )
    assert state["previous_phase_real_inventory_target_met"] is False
    assert prior["merge_commit"] == documentation_state.FL1_I2_ACCEPTED_MAIN
    assert prior["final_head"] == documentation_state.FL1_I2_PREVIOUS_FINAL_HEAD
    assert prior["final_tree"] == documentation_state.FL1_I2_PREVIOUS_FINAL_TREE
    assert prior["implementation_evidence_head"] == documentation_state.FL1_I2_PREVIOUS_EVIDENCE
    assert prior["implementation_evidence_tree"] == documentation_state.FL1_I2_PREVIOUS_EVIDENCE_TREE
    assert upstream["terminal_review_id"] == documentation_state.FL1_I2_TERMINAL_REVIEW_ID
    assert upstream["terminal_review_finding_count"] == 17
    assert upstream["terminal_review_p1_count"] == 13
    assert upstream["terminal_review_p2_count"] == 4
    assert upstream["terminal_review_resolved_count"] == 0
    assert upstream["terminal_review_outdated_count"] == 0
    assert upstream["github_checks"] == 0


def test_live_git_objects_bind_frozen_i1_commit_and_tree_evidence() -> None:
    state = _state()
    documentation_state.validate_git_ancestry(state)
    commit = state["protected_evidence"]["previous_phase_implementation_evidence_head"]
    expected_tree = state["protected_evidence"]["previous_phase_implementation_evidence_tree"]
    result = documentation_state._run_trusted_git(
        ["rev-parse", f"{commit}^{{tree}}"]
    )
    assert result.returncode == 0
    assert result.stdout.strip() == expected_tree


def test_windows_git_candidates_use_os_roots_not_python_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        documentation_state.sys,
        "executable",
        r"D:\venv\Scripts\python.exe",
    )

    candidates = documentation_state._trusted_git_candidates(
        platform_name="nt",
        windows_location_provider=lambda: (
            (PureWindowsPath(r"c:\PROGRAM FILES\git"),),
            (
                PureWindowsPath(r"C:\Program Files"),
                PureWindowsPath(r"C:\Program Files (x86)"),
            ),
        ),
    )
    rendered = tuple(str(candidate).casefold() for candidate in candidates)

    assert all(isinstance(candidate, PureWindowsPath) for candidate in candidates)
    assert all(candidate.drive.casefold() == "c:" for candidate in candidates)
    assert (
        str(PureWindowsPath(r"C:\Program Files\Git\cmd\git.exe")).casefold()
        in rendered
    )
    assert (
        str(PureWindowsPath(r"C:\Program Files\Git\bin\git.exe")).casefold()
        in rendered
    )
    assert len(rendered) == len(set(rendered)) == 4
    assert PureWindowsPath(r"D:\Program Files\Git\cmd\git.exe") not in candidates


def test_windows_git_candidates_ignore_hostile_ordinary_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hostile = tmp_path / "hostile-git-root"
    for variable in ("PATH", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        monkeypatch.setenv(variable, str(hostile))

    candidates = documentation_state._trusted_git_candidates(
        platform_name="nt",
        windows_location_provider=lambda: (
            (PureWindowsPath(r"C:\TrustedGit"),),
            (PureWindowsPath(r"C:\TrustedProgramFiles"),),
        ),
    )

    assert all(isinstance(candidate, PureWindowsPath) for candidate in candidates)
    assert candidates == (
        PureWindowsPath(r"C:\TrustedGit\cmd\git.exe"),
        PureWindowsPath(r"C:\TrustedGit\bin\git.exe"),
        PureWindowsPath(r"C:\TrustedProgramFiles\Git\cmd\git.exe"),
        PureWindowsPath(r"C:\TrustedProgramFiles\Git\bin\git.exe"),
    )
    assert all(
        str(hostile).casefold() not in str(candidate).casefold()
        for candidate in candidates
    )


def test_windows_registry_discovery_reads_hklm_64_and_32_bit_views() -> None:
    class FakeRegistry:
        HKEY_LOCAL_MACHINE = object()
        KEY_READ = 0x20019
        KEY_WOW64_64KEY = 0x0100
        KEY_WOW64_32KEY = 0x0200

        def __init__(self) -> None:
            self.closed: list[tuple[str, int]] = []
            self.values = {
                (r"SOFTWARE\GitForWindows", self.KEY_WOW64_64KEY, "InstallPath"): (
                    r"C:\Program Files\Git"
                ),
                (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion",
                    self.KEY_WOW64_64KEY,
                    "ProgramFilesDir",
                ): r"C:\Program Files",
                (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion",
                    self.KEY_WOW64_32KEY,
                    "ProgramFilesDir (x86)",
                ): r"C:\Program Files (x86)",
            }

        def OpenKey(
            self,
            hive: object,
            key_path: str,
            reserved: int,
            access: int,
        ) -> tuple[str, int]:
            assert hive is self.HKEY_LOCAL_MACHINE
            assert reserved == 0
            view = access & (self.KEY_WOW64_64KEY | self.KEY_WOW64_32KEY)
            if not any(
                key == key_path and stored_view == view
                for key, stored_view, _ in self.values
            ):
                raise FileNotFoundError(key_path)
            return key_path, view

        def QueryValueEx(
            self,
            handle: tuple[str, int],
            value_name: str,
        ) -> tuple[str, int]:
            try:
                value = self.values[(*handle, value_name)]
            except KeyError as exc:
                raise FileNotFoundError(value_name) from exc
            return value, 1

        def CloseKey(self, handle: tuple[str, int]) -> None:
            self.closed.append(handle)

    registry = FakeRegistry()
    git_roots, program_files_roots = documentation_state._windows_system_git_roots(
        registry=registry
    )

    assert git_roots == (Path(r"C:\Program Files\Git"),)
    assert program_files_roots == (
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)"),
    )
    assert {view for _, view in registry.closed} == {
        registry.KEY_WOW64_64KEY,
        registry.KEY_WOW64_32KEY,
    }


def test_windows_git_discovery_unavailable_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="trusted_git_executable_unavailable",
    ):
        documentation_state._trusted_git_executable(
            root=tmp_path,
            platform_name="nt",
            windows_location_provider=lambda: ((), ()),
        )


def test_non_windows_git_candidates_remain_fixed() -> None:
    def unexpected_windows_provider() -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        raise AssertionError("Windows location provider must not run")

    assert documentation_state._trusted_git_candidates(
        platform_name="posix",
        windows_location_provider=unexpected_windows_provider,
    ) == (
        Path("/usr/bin/git"),
        Path("/usr/local/bin/git"),
        Path("/opt/homebrew/bin/git"),
    )


@pytest.mark.parametrize("variable", ["GIT_DIR", "GIT_WORK_TREE"])
def test_trusted_git_proof_ignores_hostile_repository_redirection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variable: str,
) -> None:
    monkeypatch.setenv(variable, str(tmp_path / "hostile-not-a-repository"))
    documentation_state.validate_git_ancestry(_state())


def test_trusted_git_environment_scrubs_mixed_case_control_keys() -> None:
    scrubbed = documentation_state._trusted_git_environment(
        {
            "Path": "trusted-path-value",
            "gIt_DiR": "hostile",
            "Git_Work_Tree": "hostile",
            "GIT_CONFIG_COUNT": "1",
        }
    )
    assert scrubbed["Path"] == "trusted-path-value"
    assert "gIt_DiR" not in scrubbed
    assert "Git_Work_Tree" not in scrubbed
    assert scrubbed["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert scrubbed["GIT_CONFIG_NOSYSTEM"] == "1"
    assert scrubbed["GIT_CONFIG_GLOBAL"] == os.devnull
    assert scrubbed["GIT_CONFIG_SYSTEM"] == os.devnull
    assert not any(
        key.casefold().startswith("git_")
        for key in scrubbed
        if key not in {
            "GIT_NO_REPLACE_OBJECTS",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
            "GIT_OPTIONAL_LOCKS",
            "GIT_TERMINAL_PROMPT",
        }
    )


def test_injected_fsmonitor_config_is_not_executed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "fsmonitor-executed"
    if os.name == "nt":
        monitor = tmp_path / "hostile-fsmonitor.cmd"
        monitor.write_text(
            f"@echo off\r\necho executed>\"{marker}\"\r\n",
            encoding="utf-8",
        )
    else:
        monitor = tmp_path / "hostile-fsmonitor.sh"
        monitor.write_text(
            f"#!/bin/sh\nprintf executed > '{marker}'\n",
            encoding="utf-8",
        )
        monitor.chmod(0o700)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(monitor))

    result = documentation_state._run_trusted_git(
        ["status", "--porcelain=v1", "--untracked-files=no"]
    )

    assert result.returncode == 0
    assert not marker.exists()


def test_frozen_i1_tree_mismatch_fails_closed() -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"]["previous_phase_implementation_evidence_tree"] = (
        "f" * 40
    )
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="frozen_i1_evidence_tree_mismatch",
    ):
        documentation_state.validate_git_ancestry(state)


def test_terminal_review_use_before_register_is_complete() -> None:
    findings = _state()["terminal_review_findings"]
    assert [finding["number"] for finding in findings] == list(range(1, 18))
    assert sum(finding["severity"] == "P1" for finding in findings) == 13
    assert sum(finding["severity"] == "P2" for finding in findings) == 4
    classifications = [finding["classification"] for finding in findings]
    assert classifications.count("closed_in_current_governance_pr") == 2
    assert classifications.count(
        "must_close_during_i2_before_i2_completion_merge_or_i3"
    ) == 14
    assert classifications.count(
        "claim_boundary_local_evidence_not_tamper_resistant_attestation"
    ) == 1


def test_i2_and_i3_gates_are_strictly_sequenced() -> None:
    state = _state()
    assert state["active_blocker"]["code"] == "pending_fl1_i2_plan_owner_audit"
    preconditions = state["next_phase_authorization"]["required_preconditions"]
    assert preconditions[0].startswith("project owner audits and approves")
    assert preconditions[1] == "I2 implementation is separately authorized"
    assert "synthetic or adversarial newly created temporary fixtures" in preconditions[2]
    assert "all fourteen I2 delivery gates close" in preconditions[3]
    assert "I2 passes owner audit and merges" in preconditions[4]
    assert "FL1_I3_REAL_SOURCE_SCOPE_GATE" in preconditions[5]


def test_network_truth_separates_data_plane_from_governance_control_plane() -> None:
    protected = _state()["protected_evidence"]
    assert "network_operation_count" not in protected
    assert protected["external_data_plane_network_operation_count"] == 0
    assert (
        protected["authorized_git_github_governance_control_plane_operations_occurred"]
        is True
    )


def test_handoff_is_exact_generated_projection() -> None:
    state = _state()
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    assert handoff == documentation_state.render_handoff(state)
    assert 55 <= len(handoff.splitlines()) <= 115
    assert "SCV2-FL1-I2" in handoff
    assert "All 17 findings remain historical audit records" in handoff
    assert "machine_verifiable_ci=false" in handoff
    assert documentation_state.FL1_I2_BLOCKER in handoff


def test_active_markers_and_remote_sync_policy_are_consistent() -> None:
    state = _state()
    documentation_state.validate_roadmaps(state)
    for relative in (
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/project-roadmap.md",
        "docs/phase-contracts.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert text.count("<!-- CURRENT_PHASE: SCV2-FL1-I2 -->") == 1
    for relative in (
        "AGENTS.md",
        "docs/development/agent-runbook.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/project-roadmap.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "fast-forward" in text
        assert "local-only" in text
        assert "reset" in text
        assert "untracked" in text


def test_conflicting_current_roadmap_phase_fails_closed(tmp_path: Path) -> None:
    copied_root = _copy_docs_root(tmp_path)
    roadmap = copied_root / "docs" / "project-roadmap.md"
    roadmap.write_text(
        roadmap.read_text(encoding="utf-8").replace(
            "<!-- CURRENT_PHASE: SCV2-FL1-I2 -->",
            "<!-- CURRENT_PHASE: SCV2-FL1-I1 -->",
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="current_phase_conflict",
    ):
        documentation_state.validate_roadmaps(_state(), root=copied_root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_status", "fl1_i2_implementation_in_progress"),
        ("planning_approved", True),
        ("target_met", True),
        ("safe_to_merge", True),
        ("route_approved", True),
        ("manual_acceptance_status", "owner_accepted"),
    ],
)
def test_i2_positive_status_claims_fail_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state[field] = value
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_status_fields_conflict",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    "field",
    [
        "implementation_authorized",
        "implementation_started",
        "owner_acceptance_valid",
        "merge_authorized",
        "real_inventory_started",
        "real_source_inventory_authorized",
        "source_root_access_authorized",
        "database_access_authorized",
        "app_storage_write_authorized",
        "import_authorized",
        "classification_or_tagging_execution_authorized",
        "provider_or_llm_authorized",
        "media_authorized",
        "stable_replay_authorized",
        "production_authorized",
    ],
)
def test_i2_execution_authority_fails_closed(field: str) -> None:
    state = copy.deepcopy(_state())
    state["planning_boundary"][field] = True
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_boundary_invalid",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("previous_phase_implementation_evidence_head", "f" * 40),
        ("previous_phase_implementation_evidence_tree", "f" * 40),
        ("machine_verifiable_ci", True),
        ("github_checks_observed", 1),
        ("ci_authority", True),
        ("preflight_remote_sync_is_contract_proof", True),
    ],
)
def test_frozen_evidence_and_ci_boundary_fail_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"][field] = value
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_protected_evidence_invalid",
    ):
        documentation_state.validate_state(state)


def test_terminal_finding_tamper_fails_closed() -> None:
    state = copy.deepcopy(_state())
    state["terminal_review_findings"][0]["classification"] = "closed"
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_terminal_review_findings_invalid",
    ):
        documentation_state.validate_state(state)


def test_public_state_rejects_private_path() -> None:
    state = copy.deepcopy(_state())
    state["owner_decisions"].append(
        {"id": "unsafe", "decision": "private C:\\Users\\person\\source"}
    )
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="public_state_redaction_failure",
    ):
        documentation_state.validate_state(state)


def test_plan_contains_canonical_architecture_threat_model_and_full_route() -> None:
    plan = (
        ROOT
        / "docs"
        / "plans"
        / "phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md"
    ).read_text(encoding="utf-8")
    for concept in (
        "Canonical Architecture Convergence",
        "backend/app/utils/cloud_files.py",
        "backend/app/services/source_ingestion_gate.py",
        "legacy `scan_and_import(dry_run=True)`",
        "OS or kernel compromise",
        "SCV2-FL1-I2 - Pre-Real Hardening",
        "SCV2-FL1-I3 - Bounded Real-Source Inventory Canary",
        "SCV2-FL1-I4 - Full-Library Read-Only Inventory",
        "SCV2-FL1-E1 - Isolated Import Rehearsal",
        "SCV2-FL1-E2 - Local Classification And AI Tagging",
        "SCV2-FL1-V1 - Product And Owner Validation",
    ):
        assert concept in plan
    assert "same verified, no-follow, identity-bound directory handle" in plan
    assert "path-based `os.scandir()` plus a post-check cannot close this gate" in plan
    assert "FL1_I2_PLANNING_GOVERNANCE_PR_CORRECTED_READY_FOR_OWNER_REAUDIT" in plan
