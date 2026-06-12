#!/usr/bin/env python3
"""Investigate Phase 4.5-SCV2 SourceConcept pipeline fidelity.

Lifecycle: phase-scoped read-only incident investigation.

This runner reads committed reports, committed runner code, and local private
manifests. It does not import application code, connect to the database, call
providers, run LLMs, or mutate SourceConcept/Entity/media tables.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parent.parent

PHASE = "4.5-SCV2-INC1"
PHASE_TITLE = "SourceConcept Pipeline Fidelity Incident Investigation"
PHASE_SLUG = "phase-4.5-scv2-inc1-source-concept-pipeline-fidelity"
BRANCH = "codex/phase45-scv2-inc1-source-concept-pipeline-fidelity"
BASE_BRANCH = "codex/phase45-scv2-a1-post-expansion-audit-route-decision"

PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG

SC1_REPORT = ROOT / "docs" / "reports" / "phase-4.5-sc1-source-concept-resolver-core.md"
SC1_SUMMARY = ROOT / "docs" / "reports" / "phase-4.5-sc1-source-concept-resolver-core-summary.json"
SC1_RUNNER = ROOT / "scripts" / "run_phase45_sc1_source_concept_resolver.py"
SC1_SERVICE = ROOT / "backend" / "app" / "services" / "source_concept_resolver_service.py"
SC1_TESTS = ROOT / "tests" / "test_phase45_sc1_source_concept_resolver.py"

R1_REPORT = ROOT / "docs" / "reports" / "phase-4.5-scv2-r1-post-px1-source-concept-triage.md"
R1_SUMMARY = ROOT / "docs" / "reports" / "phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json"
R1_RUNNER = ROOT / "scripts" / "run_phase45_scv2_r1_post_px1_source_concept_triage.py"
R1_TESTS = ROOT / "tests" / "test_phase45_scv2_r1_post_px1_source_concept_triage.py"

A1_REPORT = ROOT / "docs" / "reports" / "phase-4.5-scv2-a1-post-expansion-audit-route-decision.md"
A1_SUMMARY = ROOT / "docs" / "reports" / "phase-4.5-scv2-a1-post-expansion-audit-route-decision-summary.json"
A1_RUNNER = ROOT / "scripts" / "run_phase45_scv2_a1_post_expansion_audit_route_decision.py"

HANDOFF = ROOT / "docs" / "current-handoff.md"
ROADMAP = ROOT / "docs" / "project-roadmap.md"

SC1_PRIVATE_ROOT = ROOT / ".local_manifests" / "phase-4.5-sc1-source-concept-resolver-core-final-lifecycle-scope-v5"
R1_PRIVATE_ROOT = ROOT / ".local_manifests" / "phase-4.5-scv2-r1-post-px1-source-concept-triage"
A1_PRIVATE_ROOT = ROOT / ".local_manifests" / "phase-4.5-scv2-a1-post-expansion-audit-route-decision"

SC1_PRIVATE_SUMMARY = SC1_PRIVATE_ROOT / "resolver-run-summary.json"
SC1_LLM_JUDGMENTS = SC1_PRIVATE_ROOT / "llm-judgments.jsonl"
SC1_SOURCE_CONCEPTS = SC1_PRIVATE_ROOT / "source-concepts.jsonl"
R1_PRIVATE_LEDGER = R1_PRIVATE_ROOT / "resolver-run-ledger.json"
R1_PRIVATE_INVENTORY = R1_PRIVATE_ROOT / "resolver-input-inventory.json"

REQUIRED_SOURCE_FILES = (
    SC1_REPORT,
    SC1_SUMMARY,
    SC1_RUNNER,
    SC1_SERVICE,
    SC1_TESTS,
    R1_REPORT,
    R1_SUMMARY,
    R1_RUNNER,
    R1_TESTS,
    A1_REPORT,
    A1_SUMMARY,
    A1_RUNNER,
    HANDOFF,
    ROADMAP,
)

SUMMARY_REQUIRED_FIELDS = {
    "phase",
    "title",
    "branch",
    "generated_at",
    "base_branch",
    "report_provenance",
    "git",
    "read_files",
    "private_artifacts_checked",
    "source_references",
    "sc1_established_pipeline",
    "r1_actual_pipeline",
    "pipeline_comparison",
    "llm_adjudication_fidelity",
    "severity_classification",
    "remediation_decision",
    "impacts",
    "missing_artifacts",
    "validation",
    "safety",
}

PUBLIC_LEAK_PATTERNS = (
    re.compile(r"[A-Za-z]:\\Users\\", re.IGNORECASE),
    re.compile(r"\\\\[A-Za-z0-9_.-]+\\"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"Authorization\s*:", re.IGNORECASE),
    re.compile(r"(api[_-]?key|secret|password)\s*[:=]\s*[^,\s]+", re.IGNORECASE),
)


def public_dirty_worktree_summary(raw_status: str) -> dict[str, Any]:
    lines = [line for line in str(raw_status or "").splitlines() if line.strip()]
    return {
        "clean": not lines,
        "dirty_count": len(lines),
        "status_redacted": bool(lines),
        "status_public": "clean" if not lines else f"redacted_dirty_entries:{len(lines)}",
        "raw_status_available_private": bool(lines),
    }


def repo_path(path: Path) -> str:
    resolved = path if path.is_absolute() else ROOT / path
    return resolved.resolve().relative_to(ROOT.resolve()).as_posix()


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def count_jsonl_lines(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def file_contains(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    return needle in read_text(path)


def line_ref(path: Path, pattern: str) -> str:
    if not path.exists():
        return f"{repo_path(path)}:missing"
    folded_pattern = pattern.casefold()
    for index, line in enumerate(read_text(path).splitlines(), start=1):
        if folded_pattern in line.casefold():
            return f"{repo_path(path)}:{index}"
    return f"{repo_path(path)}:pattern-not-found:{pattern}"


def git_value(args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except Exception:
        return "unavailable"
    return completed.stdout.strip()


def scan_public_text_for_leaks(text: str) -> dict[str, Any]:
    findings: list[str] = []
    for pattern in PUBLIC_LEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(match.group(0))
    return {"passed": not findings, "finding_count": len(findings), "findings": findings[:10]}


def scan_public_payload_for_leaks(payload: Any, *, path: str = "$") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text == "dirty_worktree_status":
                status = str(value or "")
                if status not in {"", "clean"} and not status.startswith("redacted_dirty_entries:"):
                    findings.append({"type": "raw_dirty_worktree_status", "match": child_path})
            findings.extend(scan_public_payload_for_leaks(value, path=child_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(scan_public_payload_for_leaks(item, path=f"{path}[{index}]"))
    elif isinstance(payload, str):
        scan = scan_public_text_for_leaks(payload)
        findings.extend({"type": "public_json_text_leak", "match": f"{path}:{item}"} for item in scan["findings"])
    return findings


def validate_summary_schema(summary: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(SUMMARY_REQUIRED_FIELDS.difference(summary))
    return {"passed": not missing, "missing_fields": missing, "required_fields": sorted(SUMMARY_REQUIRED_FIELDS)}


def parse_sc1_llm_adjudication(public_summary: Mapping[str, Any], report_text: str) -> dict[str, Any]:
    public_llm = public_summary.get("llm_adjudication", {}) if isinstance(public_summary, Mapping) else {}
    resolver_llm = public_summary.get("resolver_summary", {}).get("llm_usage", {}) if isinstance(public_summary, Mapping) else {}
    report_used = bool(re.search(r"(?im)^\s*-\s*Used:\s*true\s*$", report_text))
    report_judgments = re.search(r"(?im)^\s*-\s*Judgments:\s*(\d+)\s*$", report_text)
    report_max_calls = re.search(r"(?im)^\s*-\s*Max calls:\s*(\d+)\s*$", report_text)
    report_policy = re.search(r"(?im)^\s*-\s*Policy:\s*`?([^`\n]+)`?\s*$", report_text)
    return {
        "used": bool(public_llm.get("used")) or report_used,
        "judgment_count": int(public_llm.get("judgment_count") or (report_judgments.group(1) if report_judgments else 0) or 0),
        "max_calls": int(public_llm.get("max_calls") or (report_max_calls.group(1) if report_max_calls else 0) or 0),
        "max_budget_usd": public_llm.get("max_budget_usd"),
        "policy": public_llm.get("policy") or (report_policy.group(1).strip() if report_policy else None),
        "uses_fallback_provider": bool(public_llm.get("uses_fallback_provider")),
        "provider_mode": resolver_llm.get("provider", {}).get("provider_mode"),
        "provider_access_configured": resolver_llm.get("provider", {}).get("llm_access_configured"),
        "plan_status": resolver_llm.get("plan", {}).get("status"),
        "selected_block_count": resolver_llm.get("plan", {}).get("selected_block_count"),
        "skipped_block_count": resolver_llm.get("plan", {}).get("skipped_block_count"),
    }


def extract_sc1_pipeline_evidence(root: Path = ROOT) -> dict[str, Any]:
    summary_path = root / repo_path(SC1_SUMMARY)
    private_summary_path = root / repo_path(SC1_PRIVATE_SUMMARY)
    llm_jsonl_path = root / repo_path(SC1_LLM_JUDGMENTS)
    source_concepts_path = root / repo_path(SC1_SOURCE_CONCEPTS)
    runner_path = root / repo_path(SC1_RUNNER)
    service_path = root / repo_path(SC1_SERVICE)
    tests_path = root / repo_path(SC1_TESTS)

    public_summary = read_json(summary_path)
    report_text = read_text(root / repo_path(SC1_REPORT))
    private_summary = read_json(private_summary_path) if private_summary_path.exists() else {}
    public_llm = parse_sc1_llm_adjudication(public_summary, report_text)
    resolver_llm = public_summary.get("resolver_summary", {}).get("llm_usage", {})
    private_llm = private_summary.get("llm_usage") or private_summary.get("summary", {}).get("llm_usage", {})

    runner_text = read_text(runner_path)
    service_text = read_text(service_path)
    tests_text = read_text(tests_path)

    exact_command_config = (
        "scripts/run_phase45_sc1_source_concept_resolver.py "
        "--apply-db --apply-f7a-final-pack --use-llm-adjudication "
        "--max-llm-calls 300 --max-llm-budget-usd 50 "
        "--llm-cache-dir .local_manifests/phase-4.5-sc1-llm-adjudication-cache"
    )

    return {
        "summary_file": repo_path(summary_path),
        "private_summary_file": repo_path(private_summary_path),
        "resolver_version": public_summary.get("resolver_summary", {}).get("resolver_version"),
        "signal_count": public_summary.get("resolver_summary", {}).get("signal_count"),
        "edge_graph": public_summary.get("resolver_summary", {}).get("edge_graph"),
        "source_signal_adapters": public_summary.get("resolver_summary", {}).get("signal_counts_by_origin", {}),
        "llm_adjudication": {
            "used": bool(public_llm.get("used")),
            "judgment_count": int(public_llm.get("judgment_count") or 0),
            "max_calls": int(public_llm.get("max_calls") or 0),
            "max_budget_usd": public_llm.get("max_budget_usd"),
            "policy": public_llm.get("policy"),
            "uses_fallback_provider": bool(public_llm.get("uses_fallback_provider")),
            "provider_mode": resolver_llm.get("provider", {}).get("provider_mode"),
            "provider_access_configured": resolver_llm.get("provider", {}).get("llm_access_configured"),
            "plan_status": resolver_llm.get("plan", {}).get("status"),
            "selected_block_count": resolver_llm.get("plan", {}).get("selected_block_count"),
            "skipped_block_count": resolver_llm.get("plan", {}).get("skipped_block_count"),
            "private_used": bool(private_llm.get("used")) if private_llm else None,
            "private_judgment_count": private_llm.get("judgment_count"),
            "judgment_jsonl_line_count": count_jsonl_lines(llm_jsonl_path),
            "llm_same_concept_recorded": file_contains(source_concepts_path, "llm_same_concept"),
            "cache_dir": resolver_llm.get("cache_dir") or private_llm.get("cache_dir"),
        },
        "runner_config": {
            "exact_final_v5_shell_transcript_found": False,
            "config_equivalent_command_shape": exact_command_config,
            "has_use_llm_flag": "--use-llm-adjudication" in runner_text,
            "has_max_llm_calls_flag": "--max-llm-calls" in runner_text,
            "has_max_llm_budget_flag": "--max-llm-budget-usd" in runner_text,
            "has_llm_cache_flag": "--llm-cache-dir" in runner_text,
            "passes_llm_config": "llm_config=LLMAdjudicationConfig" in runner_text,
        },
        "service_support": {
            "has_llm_config": "class LLMAdjudicationConfig" in service_text,
            "has_plan_function": "def plan_llm_adjudication" in service_text,
            "has_selection_function": "def select_llm_adjudication_edges" in service_text,
            "has_runner_function": "def run_bounded_llm_adjudication" in service_text,
            "has_default_disabled_reason": "llm_adjudication_not_requested" in service_text,
        },
        "tests": {
            "has_llm_budget_cache_test": "test_llm_budget_cache_and_judgment_edges_are_source_layer_only" in tests_text,
            "has_llm_must_link_test": "test_llm_must_link_materializes_after_deterministic_guard" in tests_text,
            "has_llm_block_guard_test": "test_llm_must_link_blocked_by_short_name_guard_is_not_undermerge" in tests_text,
            "has_llm_cross_script_test": "test_llm_same_scope_cross_script_canonical_bridge_groups_for_review" in tests_text,
            "has_llm_cannot_link_test": "test_llm_cannot_link_does_not_fragment_stable_identity_anchor" in tests_text,
            "has_budget_block_test": "test_llm_budget_block_returns_before_provider_initialization" in tests_text,
        },
    }


def extract_r1_pipeline_evidence(root: Path = ROOT) -> dict[str, Any]:
    summary_path = root / repo_path(R1_SUMMARY)
    ledger_path = root / repo_path(R1_PRIVATE_LEDGER)
    inventory_path = root / repo_path(R1_PRIVATE_INVENTORY)
    runner_path = root / repo_path(R1_RUNNER)
    tests_path = root / repo_path(R1_TESTS)
    report_path = root / repo_path(R1_REPORT)

    public_summary = read_json(summary_path)
    private_ledger = read_json(ledger_path) if ledger_path.exists() else {}
    private_inventory = read_json(inventory_path) if inventory_path.exists() else {}
    runner_text = read_text(runner_path)
    tests_text = read_text(tests_path)
    report_text = read_text(report_path)

    llm_usage = private_ledger.get("result_summary", {}).get("llm_usage", {})
    commands = public_summary.get("validation", {}).get("commands", [])
    adapter_accounting = public_summary.get("resolver_input_inventory", {}).get("resolver_adapter_accounting", {})

    return {
        "summary_file": repo_path(summary_path),
        "private_ledger_file": repo_path(ledger_path),
        "private_inventory_file": repo_path(inventory_path),
        "mode": public_summary.get("mode"),
        "resolver_version": private_ledger.get("resolver_version")
        or public_summary.get("resolver_input_inventory", {}).get("resolver_version"),
        "commands": commands,
        "non_goals_include_llm": "no provider calls" in report_text and "LLM" in report_text,
        "runner_llm_support": {
            "has_use_llm_flag": "--use-llm-adjudication" in runner_text,
            "imports_llm_config": "LLMAdjudicationConfig" in runner_text,
            "passes_llm_config": "llm_config=" in runner_text,
        },
        "adapter_accounting": adapter_accounting,
        "signal_summary": public_summary.get("resolver_input_inventory", {}).get("signal_summary")
        or private_inventory.get("signal_summary", {}),
        "llm_adjudication": {
            "used": bool(llm_usage.get("used")),
            "judgment_count": int(llm_usage.get("judgment_count") or 0),
            "max_calls": int(llm_usage.get("plan", {}).get("max_calls") or 0),
            "policy": llm_usage.get("policy"),
            "provider_mode": llm_usage.get("provider", {}).get("provider_mode"),
            "plan_enabled": bool(llm_usage.get("plan", {}).get("enabled")),
            "plan_status": llm_usage.get("plan", {}).get("status"),
            "plan_reason": llm_usage.get("plan", {}).get("reason"),
            "selected_block_count": int(llm_usage.get("plan", {}).get("selected_block_count") or 0),
        },
        "deterministic_execution": {
            "edge_graph": private_ledger.get("result_summary", {}).get("edge_graph"),
            "blocking_oversized_blocks": private_ledger.get("result_summary", {}).get("blocking_oversized_blocks"),
            "concept_count": private_ledger.get("result_summary", {}).get("concept_count"),
            "link_count": private_ledger.get("result_summary", {}).get("link_count"),
            "mutation_proof": public_summary.get("mutation_proof", {}),
            "execute_transaction_committed": bool(public_summary.get("execute_transaction_committed")),
            "post_commit_verification_passed": bool(public_summary.get("post_commit_verification_passed")),
            "truth_path_write_count": private_ledger.get("truth_path_write_count"),
        },
        "tests": {
            "has_provider_import_guard": "test_runner_does_not_import_provider_network_or_truth_promoters" in tests_text,
            "has_mutation_proof_guard": "test_mutation_proof_allows_only_source_concept_tables" in tests_text,
            "has_forbidden_write_guard": "test_mutation_proof_fails_on_forbidden_truth_writes" in tests_text,
            "has_summary_schema_guard": "test_summary_schema_contains_required_fields" in tests_text,
            "has_llm_parity_guard": "llm_adjudication" in tests_text or "LLMAdjudicationConfig" in tests_text,
        },
    }


def extract_a1_evidence(root: Path = ROOT) -> dict[str, Any]:
    summary = read_json(root / repo_path(A1_SUMMARY))
    report_text = read_text(root / repo_path(A1_REPORT))
    return {
        "summary_file": repo_path(A1_SUMMARY),
        "route_decision": summary.get("route_decision_matrix", {}).get("recommended_route")
        or summary.get("route_decision", {}).get("recommended_route"),
        "route_status": summary.get("final_route_decision_status")
        or summary.get("route_decision_matrix", {}).get("status"),
        "mentions_provisional": "provisional" in report_text.lower(),
        "uses_r1_trusted_transition": summary.get("r1_transition_interpretation", {}),
        "px1_strict_influenced_concepts": summary.get("px1_evidence_impact", {}).get("px1_strict_influenced_concepts"),
        "pixiv_all_influenced_concepts": summary.get("px1_evidence_impact", {}).get("pixiv_all_influenced_concepts"),
    }


def build_llm_adjudication_fidelity(
    sc1: Mapping[str, Any],
    r1: Mapping[str, Any],
    missing_artifacts: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    missing_required = [item for item in (missing_artifacts or []) if item.get("classification") == "blocking"]
    if missing_required:
        conclusion = "inconclusive_requires_private_artifacts"
    else:
        sc1_used = bool(sc1.get("llm_adjudication", {}).get("used"))
        r1_used = bool(r1.get("llm_adjudication", {}).get("used"))
        if sc1_used and r1_used:
            conclusion = "full_chain_faithfully_rerun"
        elif sc1_used and not r1_used:
            conclusion = "llm_stage_missing_incident"
        elif not sc1_used and not r1_used:
            conclusion = "deterministic_only_rerun_confirmed"
        else:
            conclusion = "blocked_missing_artifacts"

    sc1_llm = sc1.get("llm_adjudication", {})
    r1_llm = r1.get("llm_adjudication", {})
    return {
        "sc1_used_llm_adjudication": bool(sc1_llm.get("used")),
        "sc1_judgment_count": int(sc1_llm.get("judgment_count") or 0),
        "sc1_max_calls": int(sc1_llm.get("max_calls") or 0),
        "sc1_policy": sc1_llm.get("policy"),
        "sc1_provider_mode": sc1_llm.get("provider_mode"),
        "r1_used_llm_adjudication": bool(r1_llm.get("used")),
        "r1_judgment_count": int(r1_llm.get("judgment_count") or 0),
        "r1_max_calls": int(r1_llm.get("max_calls") or 0),
        "r1_policy": r1_llm.get("policy"),
        "r1_provider_mode": r1_llm.get("provider_mode"),
        "r1_evidence_source": repo_path(R1_PRIVATE_LEDGER),
        "r1_missing_or_unproven_reason": (
            "R1 private resolver-run-ledger records llm_usage.used=false, "
            "judgment_count=0, plan.status=disabled, and reason=llm_adjudication_not_requested; "
            "R1 public commands omit LLM flags and the public report lists LLM as a non-goal."
        ),
        "conclusion": conclusion,
    }


def classify_severity(fidelity: Mapping[str, Any]) -> dict[str, Any]:
    conclusion = fidelity.get("conclusion")
    if conclusion == "llm_stage_missing_incident":
        return {
            "severity": "S1",
            "technical_severity": "S1",
            "project_governance_severity": "P0/P1 pipeline fidelity incident",
            "route_gate_status": "blocked_pending_pipeline_fidelity_remediation",
            "r2_blocked": True,
            "label": "phase output invalid or route decision incomplete",
            "rationale": (
                "SC1 established bounded LLM pair adjudication as part of the full resolver chain, "
                "but R1 disabled that stage while feeding A1 route-decision evidence."
            ),
        }
    if conclusion == "full_chain_faithfully_rerun":
        return {
            "severity": "S3",
            "technical_severity": "S3",
            "project_governance_severity": "none",
            "route_gate_status": "not_blocked_by_inc1",
            "r2_blocked": False,
            "label": "documentation/test weakness only",
            "rationale": "Full-chain rerun evidence is present.",
        }
    if conclusion == "inconclusive_requires_private_artifacts":
        return {
            "severity": "S2",
            "technical_severity": "S2",
            "project_governance_severity": "P1 evidence recovery blocker",
            "route_gate_status": "blocked_pending_private_artifact_recovery",
            "r2_blocked": True,
            "label": "report incompleteness pending private artifact recovery",
            "rationale": "Required artifacts are missing, so the chain cannot be proven from current evidence.",
        }
    return {
        "severity": "S2",
        "technical_severity": "S2",
        "project_governance_severity": "P1 pipeline fidelity uncertainty",
        "route_gate_status": "blocked_pending_pipeline_fidelity_resolution",
        "r2_blocked": True,
        "label": "report incompleteness but deterministic execution valid",
        "rationale": "The available evidence proves deterministic execution but not full SC1 chain fidelity.",
    }


def build_pipeline_comparison(sc1: Mapping[str, Any], r1: Mapping[str, Any]) -> list[dict[str, str]]:
    adapters = r1.get("adapter_accounting", {})
    r1_llm = r1.get("llm_adjudication", {})
    sc1_llm = sc1.get("llm_adjudication", {})

    def row(
        step: str,
        sc1_expected: str,
        sc1_actual: str,
        r1_expected: str,
        r1_actual: str,
        status: str,
        impact: str,
        remediation: str,
    ) -> dict[str, str]:
        return {
            "pipeline_step": step,
            "sc1_expected": sc1_expected,
            "sc1_actual_evidence": sc1_actual,
            "r1_expected": r1_expected,
            "r1_actual_evidence": r1_actual,
            "status": status,
            "impact_if_missing": impact,
            "remediation_required": remediation,
        }

    matched_adapter = "matched"
    return [
        row(
            "Source signal adapters",
            "Consume resolver-supported source-layer signals.",
            f"SC1 summary signal origins include {sorted((sc1.get('source_signal_adapters') or {}).keys())}.",
            "R1 should reuse SC1 adapters for post-PX1 evidence.",
            f"R1 adapter accounting lists {sorted(adapters.keys())}.",
            matched_adapter,
            "Missing adapters would undercount or misroute source-layer evidence.",
            "Keep adapter inventory in R1R and fail if required adapters are absent.",
        ),
        row(
            "media_tags adapter",
            "Consume eligible identity/category/parenthetical media_tags as weak source signals.",
            "SC1 runner/service signal counts include media_tags-origin signals.",
            "R1 should consume eligible media_tags without mutating media_tags.",
            adapters.get("media_tags", "not reported"),
            "matched",
            "Missing media_tags would change deterministic candidate coverage.",
            "Preserve R1 media_tags adapter proof and mutation guard.",
        ),
        row(
            "SourceMetadataRecord structured-field adapter",
            "Consume source metadata structured fields where available.",
            "SC1 source signal adapters include provider structured fields.",
            "R1 should consume SourceMetadataRecord title/artist/raw fields.",
            adapters.get("SourceMetadataRecord", "not reported"),
            "matched",
            "Missing structured fields would reduce PX1/source-backed evidence.",
            "Keep adapter-specific accounting.",
        ),
        row(
            "SourceTagObservation adapter",
            "Consume source tag observations with general/meta pollution guards.",
            "SC1 resolver tests cover source-layer signal handling.",
            "R1 should consume SourceTagObservation while rejecting general/meta-only concepts.",
            adapters.get("SourceTagObservation", "not reported"),
            "matched",
            "Missing source tags would reduce source-backed concept recall.",
            "Keep general/meta rejection proof in R1R.",
        ),
        row(
            "SourceNameObservation adapter",
            "Consume observed source names.",
            "SC1 resolver accepts source-name observation signals.",
            "R1 should include SourceNameObservation evidence.",
            adapters.get("SourceNameObservation", "not reported"),
            "matched",
            "Missing observations would leave name evidence unresolved.",
            "Keep adapter accounting and sampled evidence.",
        ),
        row(
            "SourceSearchableNameAssertion adapter",
            "Consume searchable-name assertions with review-scoped handling.",
            "SC1 source signal pipeline supports assertion-like name evidence.",
            "R1 should consume assertions while keeping needs_review rows review-scoped.",
            adapters.get("SourceSearchableNameAssertion", "not reported"),
            "matched",
            "Missing assertions would affect search seed symmetry and gap counts.",
            "Keep assertion-specific tests.",
        ),
        row(
            "SourceNameCandidate / F7a adapter",
            "Consume final F7a source-name candidates as source-layer evidence.",
            "SC1 public summary records F7a final pack import.",
            "R1 should consume active F7a candidates.",
            adapters.get("source_name_candidates", "not reported"),
            "matched",
            "Missing F7a candidates would reduce post-PX1 expansion fidelity.",
            "Keep final-pack and candidate status checks.",
        ),
        row(
            "ProviderCache adapter",
            "Provider cache may provide provider-neutral source metadata evidence without provider calls.",
            "SC1 report forbids provider enrichment calls but resolver service can consume cached provider-neutral signals.",
            "R1 should either prove ProviderCache adapter use or document it as not in input scope.",
            "R1 adapter accounting does not list ProviderCache; mutation proof shows no provider_cache writes.",
            "unproven_or_not_reported_in_r1",
            "If cached provider evidence was expected, R1 may have missed a source signal family.",
            "R1R must explicitly prove ProviderCache input accounting or document zero eligible records.",
        ),
        row(
            "blocking key generation",
            "Generate deterministic blocking keys before graph resolution.",
            f"SC1 resolver version {sc1.get('resolver_version')} reports edge graph metrics.",
            "R1 should use the same graph resolver blocking path.",
            f"R1 resolver version {r1.get('resolver_version')} reports blocking/edge metrics.",
            "matched",
            "Missing blocking changes graph topology and LLM candidate pool.",
            "Keep resolver-version and edge-graph proof.",
        ),
        row(
            "edge graph generation",
            "Build deterministic edges from compatible source signals.",
            "SC1 summary includes resolver edge_graph.",
            "R1 should produce deterministic edge_graph before any optional LLM stage.",
            f"R1 deterministic execution edge_graph={r1.get('deterministic_execution', {}).get('edge_graph')}.",
            "matched",
            "Missing graph stage invalidates resolver output.",
            "No remediation for deterministic graph; retain proof in R1R.",
        ),
        row(
            "context compatibility",
            "Apply context compatibility before linking ambiguous names.",
            "SC1 service contains context compatibility guards and related tests.",
            "R1 should inherit resolver service context compatibility.",
            "R1 used source_concept_resolver_core_v2_graph; no R1-specific override found.",
            "matched_by_shared_service",
            "Missing context guards risks overmerge.",
            "R1R should include same resolver version and overmerge checks.",
        ),
        row(
            "alias component / context equivalence",
            "Resolve alias/context equivalence conservatively.",
            "SC1 summary reports alias/context conflict counters.",
            "R1 should preserve alias/context equivalence behavior.",
            "R1 private ledger reports alias/context conflict counters in result_summary.",
            "matched_by_shared_service",
            "Missing equivalence changes active/review split.",
            "Keep counters in R1R report.",
        ),
        row(
            "union/component resolution",
            "Union graph components into SourceConcept outputs.",
            "SC1 resolver summary reports concept/link/evidence counts.",
            "R1 should persist deterministic component outputs to SourceConcept tables.",
            "R1 private ledger and summary report concept/link counts plus SourceConcept table deltas.",
            "matched",
            "Missing union/component resolution would invalidate all concept counts.",
            "No deterministic remediation needed beyond rerun with LLM enabled.",
        ),
        row(
            "LLM pair adjudication planning",
            "Plan bounded optional primary OpenAI-only pairs after deterministic blocking.",
            f"SC1 plan status={sc1_llm.get('plan_status')}, selected_block_count={sc1_llm.get('selected_block_count')}.",
            "For full-chain fidelity, R1 should enable the same bounded LLM planning stage.",
            f"R1 plan status={r1_llm.get('plan_status')}, reason={r1_llm.get('plan_reason')}.",
            "missing_in_r1",
            "Full-chain candidate adjudication was not performed.",
            "R1R replay with explicit LLM adjudication config and approval.",
        ),
        row(
            "LLM pair selection",
            "Select up to the configured bounded LLM pair/block budget.",
            f"SC1 selected {sc1_llm.get('selected_block_count')} blocks and {sc1_llm.get('judgment_count')} judgments.",
            "R1 should select comparable eligible pairs if rerunning the full chain.",
            f"R1 selected_block_count={r1_llm.get('selected_block_count')}.",
            "missing_in_r1",
            "Potential same-concept bridges remained unadjudicated.",
            "R1R must report selected/skipped pair counts.",
        ),
        row(
            "LLM provider availability",
            "Use primary OpenAI-compatible provider only after deterministic blocking; no fallback.",
            f"SC1 provider_mode={sc1_llm.get('provider_mode')}, access_configured={sc1_llm.get('provider_access_configured')}.",
            "R1 full-chain replay should prove provider availability or fail loudly before execute.",
            f"R1 provider_mode={r1_llm.get('provider_mode')}; stage disabled before provider init.",
            "missing_in_r1",
            "Provider unavailability was not tested because R1 did not request LLM.",
            "R1R needs explicit provider/cache readiness gate and budget approval.",
        ),
        row(
            "LLM judgment count",
            "Record bounded LLM judgments.",
            f"SC1 judgment_count={sc1_llm.get('judgment_count')}; jsonl_lines={sc1_llm.get('judgment_jsonl_line_count')}.",
            "R1 should record nonzero judgments for full-chain fidelity when eligible pairs exist.",
            f"R1 judgment_count={r1_llm.get('judgment_count')}.",
            "missing_in_r1",
            "R1 cannot claim SC1 full-chain parity.",
            "R1R must regenerate judgments or prove zero eligible pairs with planning evidence.",
        ),
        row(
            "LLM cache use",
            "Use local LLM cache without exposing raw provider secrets.",
            f"SC1 cache_dir={sc1_llm.get('cache_dir') or 'reported in private config'}; no fallback provider.",
            "R1 should use the approved cache path or explain why no LLM stage ran.",
            "R1 llm_usage has no cache because plan.status=disabled.",
            "missing_in_r1",
            "Repeatability and cost controls for LLM adjudication were absent.",
            "R1R must define cache path and cache-hit/miss reporting.",
        ),
        row(
            "LLM decisions applied or recorded",
            "Apply or record LLM same/cannot/uncertain decisions as source-layer evidence only.",
            f"SC1 llm_same_concept_recorded={sc1_llm.get('llm_same_concept_recorded')}.",
            "R1 should record LLM decision effects if full chain is rerun.",
            "R1 has no LLM judgments or LLM decision edges.",
            "missing_in_r1",
            "Component topology may differ from full-chain expected output.",
            "R1R must report LLM edge counts and decision outcomes.",
        ),
        row(
            "persistence to SourceConcept tables",
            "Persist resolver outputs only to SourceConcept-owned tables in execute mode.",
            "SC1 final lifecycle pack is apply-db and reports SourceConcept output artifacts.",
            "R1 execute should persist only allowed SourceConcept table changes.",
            "R1 mutation proof and post-commit checks passed; truth_path_write_count=0.",
            "matched",
            "Missing persistence would make route counts stale.",
            "R1R should use execute only after dry-run/readiness approval.",
        ),
        row(
            "mutation proof",
            "Prove no Entity truth/media_tags/provider/source/iCloud mutation.",
            "SC1 reports forbidden truth table write count and no provider/image uploads.",
            "R1 should prove no forbidden writes.",
            "R1 summary mutation proof passed; no provider/import/classification/AI/localization/Entity.",
            "matched",
            "Missing proof would make the incident higher severity.",
            "Retain and broaden mutation proof in R1R.",
        ),
        row(
            "post-commit verification",
            "Verify committed outputs and counts after execute.",
            "SC1 final validation pack and readiness checks are reported.",
            "R1 should run post-commit verification after execute.",
            f"R1 post_commit_verification_passed={r1.get('deterministic_execution', {}).get('post_commit_verification_passed')}.",
            "matched",
            "Without verification, R1 output may be unverifiable.",
            "R1R must rerun post-commit verification after full chain.",
        ),
        row(
            "validation pack/reporting",
            "Produce public report, summary, and local validation artifacts.",
            "SC1 public report/summary and private validation pack exist.",
            "R1 should report full pipeline stages actually executed.",
            "R1 report exists and honestly lists LLM as non-goal, but A1 did not treat that as a fidelity incident.",
            "present_but_incomplete_for_full_chain",
            "Reviewer could mistake deterministic-only R1 for full SC1 pipeline replay.",
            "R1R/A1 rerun reports must explicitly separate deterministic-only and full-chain statuses.",
        ),
    ]


def collect_missing_artifacts() -> list[dict[str, str]]:
    checks = [
        (SC1_PRIVATE_SUMMARY, "SC1 final lifecycle private resolver-run-summary", "blocking"),
        (SC1_LLM_JUDGMENTS, "SC1 final lifecycle llm-judgments.jsonl", "blocking"),
        (SC1_SOURCE_CONCEPTS, "SC1 final lifecycle source-concepts.jsonl", "supporting"),
        (R1_PRIVATE_LEDGER, "R1 private resolver-run-ledger", "blocking"),
        (R1_PRIVATE_INVENTORY, "R1 private resolver-input-inventory", "supporting"),
    ]
    missing = [
        {
            "artifact": repo_path(path),
            "description": description,
            "classification": classification,
        }
        for path, description, classification in checks
        if not path.exists()
    ]
    missing.append(
        {
            "artifact": ".local_manifests/phase-4.5-sc1-source-concept-resolver-core-final-lifecycle-scope-v5/console-transcript.txt",
            "description": "Exact final v5 SC1 shell transcript containing the full CLI command.",
            "classification": "non_blocking",
            "reason": "SC1 execution is proven by summary, private resolver-run-summary, 300 judgment lines, and LLM edge output; only the exact shell transcript is unavailable.",
        }
    )
    return missing


def build_source_references() -> list[dict[str, str]]:
    return [
        {"id": "sc1_public_llm_section", "ref": line_ref(SC1_REPORT, "## LLM Pair Adjudication")},
        {"id": "sc1_public_llm_used", "ref": line_ref(SC1_REPORT, "- Used: true")},
        {"id": "sc1_summary_llm", "ref": line_ref(SC1_SUMMARY, '"llm_adjudication"')},
        {"id": "sc1_summary_llm_usage", "ref": line_ref(SC1_SUMMARY, '"llm_usage"')},
        {"id": "sc1_runner_llm_flag", "ref": line_ref(SC1_RUNNER, "--use-llm-adjudication")},
        {"id": "sc1_runner_llm_config", "ref": line_ref(SC1_RUNNER, "llm_config=LLMAdjudicationConfig")},
        {"id": "service_llm_config", "ref": line_ref(SC1_SERVICE, "class LLMAdjudicationConfig")},
        {"id": "service_llm_plan", "ref": line_ref(SC1_SERVICE, "def plan_llm_adjudication")},
        {"id": "service_llm_run", "ref": line_ref(SC1_SERVICE, "def run_bounded_llm_adjudication")},
        {"id": "service_llm_default_disabled", "ref": line_ref(SC1_SERVICE, "llm_adjudication_not_requested")},
        {"id": "sc1_tests_llm", "ref": line_ref(SC1_TESTS, "test_llm_budget_cache_and_judgment_edges_are_source_layer_only")},
        {"id": "r1_public_non_goals", "ref": line_ref(R1_REPORT, "Non-goals: no provider calls")},
        {"id": "r1_public_commands", "ref": line_ref(R1_REPORT, "Commands recorded")},
        {"id": "r1_public_safety", "ref": line_ref(R1_REPORT, "No push main")},
        {"id": "r1_summary_commands", "ref": line_ref(R1_SUMMARY, '"commands"')},
        {"id": "r1_summary_no_llm", "ref": line_ref(R1_SUMMARY, '"localization_or_llm"')},
        {"id": "r1_private_ledger_llm", "ref": line_ref(R1_PRIVATE_LEDGER, '"llm_usage"')},
        {"id": "r1_private_ledger_reason", "ref": line_ref(R1_PRIVATE_LEDGER, '"llm_adjudication_not_requested"')},
        {"id": "r1_tests_provider_guard", "ref": line_ref(R1_TESTS, "test_runner_does_not_import_provider_network_or_truth_promoters")},
        {"id": "a1_public_route", "ref": line_ref(A1_REPORT, "Route Decision")},
        {"id": "a1_summary_route_status", "ref": line_ref(A1_SUMMARY, '"final_route_decision_status"')},
        {"id": "handoff_r1", "ref": line_ref(HANDOFF, "SCV2-R1")},
        {"id": "roadmap_scv2", "ref": line_ref(ROADMAP, "SCV2")},
    ]


def build_summary() -> dict[str, Any]:
    missing_artifacts = collect_missing_artifacts()
    sc1 = extract_sc1_pipeline_evidence()
    r1 = extract_r1_pipeline_evidence()
    a1 = extract_a1_evidence()
    fidelity = build_llm_adjudication_fidelity(sc1, r1, missing_artifacts)
    severity = classify_severity(fidelity)
    comparison = build_pipeline_comparison(sc1, r1)
    head_sha = git_value(["rev-parse", "HEAD"])
    raw_dirty_status = git_value(["status", "--short"])
    dirty = public_dirty_worktree_summary(raw_dirty_status)
    report_provenance = {
        "runtime_audit_git_sha": head_sha,
        "runtime_audit_git_sha_scope": "git rev-parse HEAD when the INC1 read-only file-artifact investigation runner executed.",
        "public_report_generated_from_runtime_sha": head_sha,
        "final_pr_head_sha_if_different": "reported by PR metadata/final delivery after the report-generation commit; a commit cannot truthfully contain its own final SHA.",
        "final_pr_head_sha_if_different_scope": "If the final PR head differs from runtime_audit_git_sha, the difference is expected to be the later committed report/summary artifact regeneration.",
        "operational_result_reused_older_artifacts": False,
        "dirty_worktree": dirty,
        "dirty_worktree_status": dirty["status_public"],
    }

    remediation_decision = {
        "selected_option": "4. R1/A1 invalid; rerun R1 and A1 after fixing runner/config.",
        "required_next_phase": "Phase 4.5-SCV2-R1R: Full SourceConcept Pipeline Replay / Remediation",
        "required_followup_after_r1r": "Phase 4.5-SCV2-A1R: rerun A1 route audit after R1R outputs exist",
        "r2_blocked_until": ["R1R full-chain replay/remediation complete", "A1R route audit rerun complete"],
        "do_not_implement_in_inc1": True,
        "rationale": (
            "R1 deterministic evidence remains useful, but R1 did not faithfully rerun the SC1 full chain because "
            "bounded LLM pair adjudication was disabled. A1 route approval must stay blocked until R1R and an "
            "A1R route audit rerun are complete."
        ),
        "r1r_plan": [
            "Start from the latest approved base after INC1.",
            "Add explicit R1R runner/config path for deterministic resolver plus bounded LLM pair adjudication.",
            "Require dry-run/readiness proof before execute.",
            "Use primary OpenAI-compatible adjudication only if explicitly approved for R1R; no provider/source enrichment and no image uploads.",
            "Persist only SourceConcept-owned tables after approval; preserve no Entity truth/media_tags/source metadata mutation.",
            "Regenerate R1R public report/private artifacts, then rerun A1 route audit from R1R outputs.",
        ],
    }

    impacts = {
        "r1": {
            "invalid_for_full_chain_claim": True,
            "deterministic_results_still_valid": True,
            "details": "R1 executed deterministic graph resolver stages and allowed SourceConcept persistence, but omitted the SC1 LLM adjudication stage.",
        },
        "a1": {
            "route_decision_incomplete": True,
            "route_approval_blocked_pending_remediation": True,
            "a1r_required_after_r1r": True,
            "details": "A1 used R1 outputs as the post-expansion state without treating deterministic-only R1 as a fidelity incident.",
        },
        "r2": {
            "blocked": True,
            "details": "SCV2-R2 must not start until R1R full-chain replay/remediation and refreshed A1 route audit are complete.",
        },
        "still_valid": [
            "R1 adapter accounting for listed adapters.",
            "R1 deterministic resolver execution and mutation proof.",
            "R1 post-commit verification for the deterministic-only output.",
            "A1 read-only audit mechanics and strict PX1-vs-all-Pixiv metric distinction.",
        ],
        "depends_on_full_chain": [
            "Any claim that R1 faithfully reran the SC1 full resolver chain.",
            "A1 route readiness based on current R1 SourceConcept topology.",
            "R2 target prioritization if LLM adjudication would change components, aliases, or needs_review distribution.",
        ],
    }

    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "branch": BRANCH,
        "base_branch": BASE_BRANCH,
        "generated_at": now_utc(),
        "report_provenance": report_provenance,
        "runtime_audit_git_sha": report_provenance["runtime_audit_git_sha"],
        "public_report_generated_from_runtime_sha": report_provenance["public_report_generated_from_runtime_sha"],
        "final_pr_head_sha_if_different": report_provenance["final_pr_head_sha_if_different"],
        "operational_result_reused_older_artifacts": report_provenance["operational_result_reused_older_artifacts"],
        "dirty_worktree": dirty,
        "dirty_worktree_status": dirty["status_public"],
        "git": {
            "current_branch": git_value(["branch", "--show-current"]),
            "head_sha": head_sha,
            "origin_main_sha": git_value(["rev-parse", "origin/main"]),
            "base_branch_sha": git_value(["rev-parse", f"origin/{BASE_BRANCH}"]),
            "dirty_worktree": dirty,
            "dirty_worktree_status": dirty["status_public"],
        },
        "read_files": [repo_path(path) for path in REQUIRED_SOURCE_FILES],
        "private_artifacts_checked": [
            {"artifact": repo_path(SC1_PRIVATE_SUMMARY), "exists": SC1_PRIVATE_SUMMARY.exists()},
            {"artifact": repo_path(SC1_LLM_JUDGMENTS), "exists": SC1_LLM_JUDGMENTS.exists()},
            {"artifact": repo_path(SC1_SOURCE_CONCEPTS), "exists": SC1_SOURCE_CONCEPTS.exists()},
            {"artifact": repo_path(R1_PRIVATE_LEDGER), "exists": R1_PRIVATE_LEDGER.exists()},
            {"artifact": repo_path(R1_PRIVATE_INVENTORY), "exists": R1_PRIVATE_INVENTORY.exists()},
            {"artifact": repo_path(A1_PRIVATE_ROOT), "exists": A1_PRIVATE_ROOT.exists()},
        ],
        "source_references": build_source_references(),
        "sc1_established_pipeline": sc1,
        "r1_actual_pipeline": r1,
        "a1_route_evidence": a1,
        "pipeline_comparison": comparison,
        "llm_adjudication_fidelity": fidelity,
        "severity_classification": severity,
        "remediation_decision": remediation_decision,
        "impacts": impacts,
        "missing_artifacts": missing_artifacts,
        "validation": {
            "runner_mode": "read_only_file_artifact_investigation",
            "db_accessed": False,
            "provider_or_llm_called": False,
            "summary_schema": {},
            "public_redaction": {},
        },
        "safety": {
            "no_r2_started": True,
            "no_db_writes": True,
            "no_provider_pixiv_gallery_dl": True,
            "no_media_import": True,
            "no_classification_ai_localization_llm_calls": True,
            "no_entity_truth_or_media_tags_mutation": True,
            "no_source_metadata_or_sourceconcept_table_mutation": True,
        },
    }
    summary["validation"]["summary_schema"] = validate_summary_schema(summary)
    return summary


def markdown_table(rows: Sequence[Mapping[str, str]]) -> str:
    headers = [
        "pipeline step",
        "SC1 expected",
        "SC1 actual evidence",
        "R1 expected",
        "R1 actual evidence",
        "status",
        "impact if missing",
        "remediation required",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        values = [
            row["pipeline_step"],
            row["sc1_expected"],
            row["sc1_actual_evidence"],
            row["r1_expected"],
            row["r1_actual_evidence"],
            row["status"],
            row["impact_if_missing"],
            row["remediation_required"],
        ]
        escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines)


def render_public_report(summary: Mapping[str, Any]) -> str:
    fidelity = summary["llm_adjudication_fidelity"]
    severity = summary["severity_classification"]
    remediation = summary["remediation_decision"]
    impacts = summary["impacts"]
    sc1 = summary["sc1_established_pipeline"]
    r1 = summary["r1_actual_pipeline"]
    provenance = summary["report_provenance"]
    confirmed_incident = fidelity["conclusion"] == "llm_stage_missing_incident"
    if confirmed_incident:
        summary_sentence = (
            "INC1 confirms a pipeline fidelity incident. SC1 established and actually ran bounded LLM pair adjudication "
            "as part of the full SourceConcept resolver chain. R1 executed deterministic resolver stages and persisted "
            "SourceConcept-scoped outputs, but R1 did not request or run LLM pair adjudication."
        )
    else:
        summary_sentence = (
            "INC1 cannot assert a full pipeline fidelity incident from the currently available artifacts. The route remains "
            "blocked until the missing private evidence is recovered or the pipeline is rerun."
        )

    refs = "\n".join(f"- `{item['id']}`: `{item['ref']}`" for item in summary["source_references"])
    missing = "\n".join(
        f"- `{item['artifact']}`: {item['description']} ({item['classification']})"
        for item in summary["missing_artifacts"]
    )
    if not missing:
        missing = "- None."

    llm_json = json.dumps(fidelity, indent=2, sort_keys=True)

    return f"""# {PHASE}: {PHASE_TITLE}

## Summary

{summary_sentence} Therefore R1/A1 cannot be used as full-chain route approval evidence unless the conclusion is later changed by recovered artifacts.

- Conclusion: `{fidelity['conclusion']}`
- Technical severity: `{severity['technical_severity']}` - {severity['label']}
- Project governance severity: `{severity['project_governance_severity']}`
- Route gate status: `{severity['route_gate_status']}`
- Required remediation: {remediation['selected_option']}
- Required follow-up after R1R: {remediation['required_followup_after_r1r']}
- R2 status: blocked until R1R plus A1R are complete; do not start R2.

## Provenance

- Runtime audit git SHA: `{provenance['runtime_audit_git_sha']}`.
- Runtime audit SHA scope: {provenance['runtime_audit_git_sha_scope']}
- Public report generated from runtime SHA: `{provenance['public_report_generated_from_runtime_sha']}`.
- Final PR head SHA if different: `{provenance['final_pr_head_sha_if_different']}`.
- Dirty worktree clean at runtime: `{provenance['dirty_worktree']['clean']}`; dirty entry count: `{provenance['dirty_worktree']['dirty_count']}`; status filenames redacted: `{provenance['dirty_worktree']['status_redacted']}`.

## Incident Statement

The incident concern is that Phase 4.5-SCV2-R1 may not have faithfully executed the full SourceConcept resolver pipeline established in Phase 4.5-SC1. The specific stage under investigation is bounded LLM pair adjudication.

## Why This Investigation Was Opened

SC1 explicitly reported LLM pair adjudication as used with 300 judgments and a max-call budget of 300. R1 and A1 subsequently became route-decision inputs for proposed SCV2-R2 work. If R1 omitted the SC1 LLM stage, R1/A1 route evidence is incomplete for full-chain approval.

## SC1 Established Pipeline

SC1 established the shared graph resolver pipeline with source signal adapters, blocking, edge graph generation, context compatibility, alias/context equivalence, union/component resolution, optional bounded primary-provider LLM pair adjudication after deterministic blocking, SourceConcept-scoped persistence, mutation proof, and validation-pack reporting.

SC1 evidence:

- Public LLM section: used=`{sc1['llm_adjudication']['used']}`, policy=`{sc1['llm_adjudication']['policy']}`, judgments=`{sc1['llm_adjudication']['judgment_count']}`, max_calls=`{sc1['llm_adjudication']['max_calls']}`.
- Private LLM judgment file line count: `{sc1['llm_adjudication']['judgment_jsonl_line_count']}`.
- Resolver output records LLM same-concept edges: `{sc1['llm_adjudication']['llm_same_concept_recorded']}`.
- Runner supports and passes `LLMAdjudicationConfig`: `{sc1['runner_config']['passes_llm_config']}`.
- Exact final v5 shell transcript found: `{sc1['runner_config']['exact_final_v5_shell_transcript_found']}`.
- Config-equivalent command shape proven by runner and private summary:
  `{sc1['runner_config']['config_equivalent_command_shape']}`

## R1 Actual Pipeline

R1 executed the graph resolver in execute mode with deterministic stages and SourceConcept-scoped persistence, but its private ledger records the LLM plan as disabled:

- R1 mode: `{r1['mode']}`
- R1 resolver version: `{r1['resolver_version']}`
- R1 LLM used: `{r1['llm_adjudication']['used']}`
- R1 judgment count: `{r1['llm_adjudication']['judgment_count']}`
- R1 LLM plan status: `{r1['llm_adjudication']['plan_status']}`
- R1 LLM missing reason: `{r1['llm_adjudication']['plan_reason']}`
- R1 public commands: `{'; '.join(r1['commands'])}`
- R1 runner has LLM flag support: `{r1['runner_llm_support']['has_use_llm_flag']}`
- R1 tests include LLM parity guard: `{r1['tests']['has_llm_parity_guard']}`

## SC1 vs R1 Comparison Table

{markdown_table(summary['pipeline_comparison'])}

## LLM Adjudication Fidelity

```json
{llm_json}
```

## Missing Artifacts / Uncertainty

{missing}

The missing SC1 final-v5 shell transcript prevents quoting the exact historical terminal command. It does not block the incident conclusion because SC1 LLM execution is independently proven by the public summary, private resolver-run-summary, 300 judgment lines, runner config, and LLM edge output.

## Impact on R1

- Full-chain fidelity claim: invalid.
- Deterministic-only output: still useful and supported by adapter accounting, mutation proof, and post-commit verification.
- R1 status for route approval: incomplete because bounded LLM pair adjudication was not run.

## Impact on A1

- A1 remains useful as a read-only audit of the current post-R1 state.
- A1 route approval is incomplete because it did not treat deterministic-only R1 as a fidelity incident.
- A1 should be rerun after R1R full-chain replay.

## Impact on Proposed R2

SCV2-R2 remains blocked until R1R full-chain remediation and A1R rerun are both complete. R2 target buckets, needs_review priorities, and route readiness may change after LLM adjudication changes component topology or records same/cannot/uncertain decisions.

## Severity Classification

- Technical severity: `{severity['technical_severity']}`
- Project governance severity: `{severity['project_governance_severity']}`
- Label: {severity['label']}
- Rationale: {severity['rationale']}

## Remediation Decision

Selected decision: {remediation['selected_option']}

Required next phase: `{remediation['required_next_phase']}`

Required follow-up after R1R: `{remediation['required_followup_after_r1r']}`

R1R should replay the full deterministic + bounded LLM adjudication chain under explicit approval. A1R must rerun after R1R. INC1 does not implement R1R or A1R.

## Required Next Phase, If Any

`Phase 4.5-SCV2-R1R: Full SourceConcept Pipeline Replay / Remediation`

Minimum R1R plan:

{chr(10).join(f"- {item}" for item in remediation['r1r_plan'])}

## Validation

- Investigation runner mode: `{summary['validation']['runner_mode']}`
- DB accessed: `{summary['validation']['db_accessed']}`
- Provider or LLM called: `{summary['validation']['provider_or_llm_called']}`
- Summary schema passed: `{summary['validation']['summary_schema']['passed']}`
- Public redaction passed: `{summary['validation']['public_redaction'].get('passed')}`

## Safety / Non-goals

- No R2 started.
- No DB writes.
- No provider, Pixiv, or gallery-dl execution.
- No media import.
- No classification, AI tagging, localization, or LLM calls.
- No Entity truth, `media_tags`, confirmed assignment, source metadata, SourceConcept table, source root, iCloud, or storage mutation.
- No merge and no push to main.

## Evidence References

{refs}

## Engineering Judgment

This is a real technical S1 and project-governance P0/P1 fidelity incident, not just a wording mismatch. R1 appears to have intentionally scoped out LLM in its prompt/report, but that means it was not a faithful full-chain replay of the SC1 resolver pipeline. The root cause is a phase-scope/config omission plus insufficient reporting/tests to distinguish deterministic-only execution from full-chain execution before A1 route approval. The correct next move is a separate R1R remediation phase, then A1R; R2 must remain blocked until both are complete.
"""


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_outputs(summary: dict[str, Any], output_dir: Path, write_public_report: bool) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    report_text = render_public_report(summary)
    json_findings = scan_public_payload_for_leaks(summary)
    text_redaction = scan_public_text_for_leaks(report_text)
    redaction = {
        "passed": text_redaction["passed"] and not json_findings,
        "markdown": text_redaction,
        "json_finding_count": len(json_findings),
        "json_findings": json_findings[:10],
        "finding_count": text_redaction["finding_count"] + len(json_findings),
        "findings": text_redaction["findings"] + json_findings[:10],
        "policy": "scan public Markdown and public summary JSON before writing tracked artifacts",
    }
    summary["validation"]["public_redaction"] = redaction
    summary["validation"]["summary_schema"] = validate_summary_schema(summary)
    report_text = render_public_report(summary)
    json_findings = scan_public_payload_for_leaks(summary)
    text_redaction = scan_public_text_for_leaks(report_text)
    redaction = {
        "passed": text_redaction["passed"] and not json_findings,
        "markdown": text_redaction,
        "json_finding_count": len(json_findings),
        "json_findings": json_findings[:10],
        "finding_count": text_redaction["finding_count"] + len(json_findings),
        "findings": text_redaction["findings"] + json_findings[:10],
        "policy": "scan public Markdown and public summary JSON before writing tracked artifacts",
    }
    summary["validation"]["public_redaction"] = redaction

    if not redaction["passed"]:
        raise RuntimeError(f"public report redaction failed: {redaction['findings']}")

    write_json(output_dir / "sc1-pipeline-evidence.json", summary["sc1_established_pipeline"])
    write_json(output_dir / "r1-pipeline-evidence.json", summary["r1_actual_pipeline"])
    write_json(output_dir / "pipeline-step-comparison.json", {"rows": summary["pipeline_comparison"]})
    write_json(output_dir / "llm-adjudication-fidelity.json", summary["llm_adjudication_fidelity"])
    write_json(output_dir / "incident-severity.json", summary["severity_classification"])
    write_json(output_dir / "remediation-plan.json", summary["remediation_decision"])
    write_json(output_dir / "missing-artifacts.json", {"missing_artifacts": summary["missing_artifacts"]})
    (output_dir / "public-redaction-check.txt").write_text(
        "passed\n" if redaction["passed"] else f"failed: {redaction['findings']}\n",
        encoding="utf-8",
    )

    if write_public_report:
        PUBLIC_REPORT_MD.write_text(report_text, encoding="utf-8")
        write_json(PUBLIC_REPORT_JSON, summary)

    return {
        "public_report": repo_path(PUBLIC_REPORT_MD),
        "summary_json": repo_path(PUBLIC_REPORT_JSON),
        "private_output_dir": repo_path(output_dir),
        "public_redaction": redaction,
        "summary_schema": summary["validation"]["summary_schema"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-public-report", action="store_true")
    args = parser.parse_args(argv)

    summary = build_summary()
    result = write_outputs(summary, args.output_dir, write_public_report=args.write_public_report)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
