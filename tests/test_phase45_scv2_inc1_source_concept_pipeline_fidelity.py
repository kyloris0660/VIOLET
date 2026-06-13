"""Focused tests for Phase 4.5-SCV2-INC1 pipeline fidelity investigation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_phase45_scv2_inc1_source_concept_pipeline_fidelity as runner  # noqa: E402


def _sc1_llm_summary(used: bool = True) -> dict:
    return {
        "llm_adjudication": {
            "used": used,
            "judgment_count": 300 if used else 0,
            "max_calls": 300 if used else 0,
            "policy": "bounded_optional_primary_openai_only_after_deterministic_blocking",
            "provider_mode": "primary_openai" if used else None,
        }
    }


def _r1_llm_summary(used: bool = False) -> dict:
    return {
        "llm_adjudication": {
            "used": used,
            "judgment_count": 12 if used else 0,
            "max_calls": 300 if used else 0,
            "policy": "bounded_optional_primary_openai_only_after_deterministic_blocking",
            "provider_mode": "primary_openai" if used else None,
        }
    }


def test_sc1_summary_with_llm_used_true_is_recognized() -> None:
    fidelity = runner.build_llm_adjudication_fidelity(_sc1_llm_summary(True), _r1_llm_summary(True))

    assert fidelity["sc1_used_llm_adjudication"] is True
    assert fidelity["sc1_judgment_count"] == 300
    assert fidelity["sc1_max_calls"] == 300
    assert fidelity["conclusion"] == "full_chain_faithfully_rerun"


def test_sc1_llm_evidence_is_parsed_from_summary_and_report() -> None:
    parsed = runner.parse_sc1_llm_adjudication(
        {"llm_adjudication": {"used": False}},
        """
## LLM Pair Adjudication
- Used: True
- Policy: `bounded_optional_primary_openai_only_after_deterministic_blocking`
- Judgments: 300
- Max calls: 300
""",
    )

    assert parsed["used"] is True
    assert parsed["judgment_count"] == 300
    assert parsed["max_calls"] == 300
    assert parsed["policy"] == "bounded_optional_primary_openai_only_after_deterministic_blocking"


def test_r1_summary_lacking_llm_usage_cannot_be_full_chain() -> None:
    fidelity = runner.build_llm_adjudication_fidelity(_sc1_llm_summary(True), {})

    assert fidelity["r1_used_llm_adjudication"] is False
    assert fidelity["r1_judgment_count"] == 0
    assert fidelity["conclusion"] != "full_chain_faithfully_rerun"


def test_missing_private_artifacts_yields_inconclusive() -> None:
    fidelity = runner.build_llm_adjudication_fidelity(
        _sc1_llm_summary(True),
        _r1_llm_summary(False),
        missing_artifacts=[
            {
                "artifact": ".local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage/resolver-run-ledger.json",
                "classification": "blocking",
            }
        ],
    )

    assert fidelity["conclusion"] == "inconclusive_requires_private_artifacts"


def test_severity_is_s1_when_required_llm_stage_is_missing() -> None:
    fidelity = runner.build_llm_adjudication_fidelity(_sc1_llm_summary(True), _r1_llm_summary(False))
    severity = runner.classify_severity(fidelity)

    assert severity["severity"] == "S1"
    assert severity["technical_severity"] == "S1"
    assert severity["project_governance_severity"] == "P0/P1 pipeline fidelity incident"
    assert severity["r2_blocked"] is True


def test_r2_remains_blocked_until_r1r_and_a1r() -> None:
    summary = runner.build_summary()

    assert summary["severity_classification"]["r2_blocked"] is True
    assert "R1R" in summary["remediation_decision"]["required_next_phase"]
    assert "A1R" in summary["remediation_decision"]["required_followup_after_r1r"]
    assert summary["impacts"]["r2"]["blocked"] is True


def test_public_json_redaction_catches_raw_dirty_status() -> None:
    findings = runner.scan_public_payload_for_leaks({"git": {"dirty_worktree_status": "?? docs/private-new-file.md"}})

    assert findings
    assert findings[0]["type"] == "raw_dirty_worktree_status"


def test_public_json_redaction_allows_redacted_dirty_status() -> None:
    findings = runner.scan_public_payload_for_leaks({"git": {"dirty_worktree_status": "redacted_dirty_entries:2"}})

    assert findings == []


def test_public_json_redaction_scans_key_names_and_key_value_context() -> None:
    findings = runner.scan_public_payload_for_leaks(
        {
            r"C:\private\raw-key": "key names are scanned",
            "audit": {"source_path": r"D:\private\library\image.png"},
            "auth": {"api_key": "sk-rawsecret12345"},
        }
    )
    finding_types = {item["type"] for item in findings}

    assert "public_json_key_text_leak" in finding_types
    assert "public_json_key_value_context_leak" in finding_types
    assert "public_json_sensitive_key_unredacted" in finding_types


def test_public_json_redaction_allows_safe_public_path_labels() -> None:
    findings = runner.scan_public_payload_for_leaks(
        {
            "zip_path_label": ".local_manifests/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity/out.zip",
            "dirty_worktree_status": "clean",
        }
    )

    assert findings == []


def test_summary_json_required_fields_exist() -> None:
    assert runner.PUBLIC_REPORT_JSON.exists(), "Run the INC1 runner before validation."
    summary = json.loads(runner.PUBLIC_REPORT_JSON.read_text(encoding="utf-8"))

    schema = runner.validate_summary_schema(summary)

    assert schema["passed"] is True


def test_public_redaction_catches_local_paths_and_secrets() -> None:
    result = runner.scan_public_text_for_leaks(
        r"private path C:\Users\example\library plus Authorization: Bearer sk-testsecret12345"
    )

    assert result["passed"] is False
    assert result["finding_count"] >= 1


def test_public_redaction_catches_broadened_local_path_shapes() -> None:
    leak_examples = [
        r"D:\library\private.png",
        r"\\nas-host\share\private.png",
        "file:///Users/example/private.png",
        "/Users/example/private.png",
        "/home/example/private.png",
        "/mnt/nas/private.png",
        "/Volumes/Archive/private.png",
    ]

    for leak in leak_examples:
        result = runner.scan_public_text_for_leaks(f"public payload leaked {leak}")
        assert result["passed"] is False, leak


def test_write_public_report_fails_before_writing_when_blocking_artifacts_missing(tmp_path: Path) -> None:
    summary = {
        "missing_artifacts": [
            {
                "artifact": ".local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage/resolver-run-ledger.json",
                "classification": "blocking",
            }
        ]
    }
    output_dir = tmp_path / "phase-output"

    with pytest.raises(RuntimeError, match="blocking private artifacts"):
        runner.write_outputs(summary, output_dir, write_public_report=True)

    assert not output_dir.exists()


def test_external_output_dir_is_rejected_before_writing(tmp_path: Path) -> None:
    external_output_dir = tmp_path / "outside-phase-root"

    with pytest.raises(RuntimeError, match="Refusing --output-dir outside"):
        runner.resolve_output_dir(external_output_dir)

    assert not external_output_dir.exists()
