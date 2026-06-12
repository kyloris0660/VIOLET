"""Focused tests for Phase 4.5-SCV2-INC1 pipeline fidelity investigation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
