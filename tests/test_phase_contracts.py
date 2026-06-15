"""Tests for executable phase contracts and phase gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_contracts import REQUIRED_CONTRACT_IDS, check_phase_contract, list_contracts, load_summary_file  # noqa: E402
from scripts.phase_contracts.contract_registry import SOURCE_CONCEPT_FULL_CHAIN_STAGES  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "phase_contracts"


def _source_concept_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "source_concept_full_chain_contract_v1",
            "status": "full_chain_completed",
            "claims": {"full_chain_complete": True},
        },
        "required_stages": list(SOURCE_CONCEPT_FULL_CHAIN_STAGES),
        "executed_stages": list(SOURCE_CONCEPT_FULL_CHAIN_STAGES),
        "missing_required_stages": [],
        "full_chain_fidelity_passed": True,
        "deterministic_stage_summary": {"resolver_version": "source_concept_resolver_core_v2_graph"},
        "llm_adjudication_plan": {
            "required": True,
            "status": "ready",
            "eligible_pair_count": 12,
            "selected_pair_count": 12,
            "max_calls": 300,
            "budget_usd": 50.0,
            "projected_budget_usd": 0.2,
        },
        "llm_adjudication_used": True,
        "llm_judgment_count": 12,
        "llm_max_calls": 300,
        "llm_budget_usd": 50.0,
        "llm_provider_mode": "primary_openai",
        "llm_cache_summary": {"cache_enabled": True, "cache_hits": 0, "cache_misses": 12},
        "mutation_proof": {"passed": True, "forbidden_changed_tables": [], "unexpected_changed_tables": []},
        "post_commit_verification": {"passed": True},
        "validation_pack": {"generated": True},
        "review_pack": {"generated": True},
        "conclusion": "full_chain_completed",
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _route_audit_summary(**overrides: object) -> dict:
    summary = {
        "final_route_decision_status": "blocked_pending_pipeline_fidelity_remediation",
        "transaction_readonly_proof": {
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
            "stable_snapshot": True,
            "snapshot_id_present": True,
        },
        "mutation_proof": {"passed": True, "changed_tables": []},
        "chatgpt_review_pack": _complete_review_pack_proof(),
        "pipeline_contract": {"contract_id": "route_audit_contract_v1"},
        "upstream_pipeline_contract": {"passed": False, "full_chain_fidelity_passed": False},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _route_full_chain_upstream(**overrides: object) -> dict:
    upstream = {
        "contract_id": "source_concept_full_chain_contract_v1",
        "passed": True,
        "status": "full_chain_completed",
        "full_chain_fidelity_passed": True,
        "missing_required_stages": [],
    }
    upstream.update(overrides)
    return upstream


def _complete_review_pack_proof(**overrides: object) -> dict:
    pack = {
        "generated": True,
        "manifest_present": True,
        "checksums_present": True,
        "checksum_count": 3,
        "manifest_checksum_count": 3,
        "redaction_passed": True,
        "redaction_scan_covers_final_file_set": True,
        "public_report_copy_current": True,
        "zip_generated": True,
        "not_committed": True,
    }
    pack.update(overrides)
    return pack


def _review_pack_summary(**pack_overrides: object) -> dict:
    return {"review_pack": _complete_review_pack_proof(**pack_overrides)}


def _error_codes(result) -> set[str]:
    return {error.code for error in result.errors}


def _serialized_result(result) -> str:
    return json.dumps(result.to_dict(), sort_keys=True)


def test_registry_contains_all_required_contracts() -> None:
    registered = {contract.contract_id for contract in list_contracts()}

    assert set(REQUIRED_CONTRACT_IDS).issubset(registered)
    assert len(registered) >= 15


def test_source_concept_full_chain_fails_when_llm_required_but_missing() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False)

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_missing" in _error_codes(result)


def test_source_concept_full_chain_fails_zero_judgments_when_completion_claimed() -> None:
    summary = _source_concept_summary(llm_judgment_count=0)

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_zero_llm_judgments_full_chain" in _error_codes(result)


def test_source_concept_full_chain_fails_required_false_with_eligible_pairs() -> None:
    summary = _source_concept_summary()
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 12,
        "selected_pair_count": 12,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_opt_out_with_eligible_pairs" in _error_codes(result)


def test_source_concept_deterministic_only_fails_safe_to_merge_claim() -> None:
    summary = _source_concept_summary(
        pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": "deterministic_only"},
        safe_to_merge=True,
        full_chain_fidelity_passed=False,
        llm_adjudication_used=False,
        llm_judgment_count=0,
        llm_provider_mode="not_applicable_deterministic_only",
    )

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "deterministic_only_claimed_completion" in _error_codes(result)


def test_source_concept_blocked_or_inconclusive_fails_safe_to_merge_claim() -> None:
    for status in ("full_chain_blocked_llm_unavailable", "full_chain_inconclusive_missing_artifacts"):
        summary = _source_concept_summary(
            pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": status},
            safe_to_merge=True,
            full_chain_fidelity_passed=False,
            llm_adjudication_used=False,
            llm_judgment_count=0,
        )

        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

        assert result.passed is False
        assert "blocked_status_claimed_completion" in _error_codes(result)


def test_source_concept_full_chain_fails_required_false_with_eligible_omitted_without_zero_proof() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    summary["llm_adjudication_plan"] = {
        "required": False,
        "status": "ready",
        "selected_pair_count": 0,
        "max_calls": 300,
        "budget_usd": 50.0,
        "projected_budget_usd": 0.0,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_opt_out_without_zero_eligible_proof" in _error_codes(result)
    assert "source_concept_llm_required_missing" in _error_codes(result)


def test_source_concept_full_chain_fails_required_false_zero_eligible_without_zero_proof() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_opt_out_without_zero_eligible_proof" in _error_codes(result)
    assert "source_concept_zero_llm_judgments_full_chain" in _error_codes(result)


def test_source_concept_full_chain_allows_required_false_only_with_valid_zero_eligible_proof() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
        "zero_eligible_proof": True,
        "zero_eligible_reason": "ProviderCache adapter had no eligible LLM comparison pairs after deterministic resolution.",
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is True


def test_source_concept_full_chain_fails_eligible_pair_count_over_max_calls() -> None:
    summary = _source_concept_summary()
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "eligible_pair_count": 301,
        "selected_pair_count": 300,
        "max_calls": 300,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_call_cap_exceeded" in _error_codes(result)


def test_source_concept_full_chain_fails_selected_pair_count_over_max_calls() -> None:
    summary = _source_concept_summary()
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "eligible_pair_count": 12,
        "selected_pair_count": 301,
        "max_calls": 300,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_selected_call_cap_exceeded" in _error_codes(result)


def test_source_concept_full_chain_fails_judgment_count_over_zero_max_calls() -> None:
    summary = _source_concept_summary(llm_judgment_count=300, llm_max_calls=0)
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "max_calls": 0,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_judgment_call_cap_exceeded" in _error_codes(result)


def test_source_concept_full_chain_fails_judgment_count_over_max_calls_unless_approved() -> None:
    failing = _source_concept_summary(llm_judgment_count=301)
    failing["llm_adjudication_plan"] = {
        **failing["llm_adjudication_plan"],
        "max_calls": 300,
    }
    approved = _source_concept_summary(llm_judgment_count=301)
    approved["llm_adjudication_plan"] = {
        **approved["llm_adjudication_plan"],
        "max_calls": 300,
        "explicit_over_budget_or_call_cap_approval": True,
    }

    fail_result = check_phase_contract("source_concept_full_chain_contract_v1", failing)
    approved_result = check_phase_contract("source_concept_full_chain_contract_v1", approved)

    assert fail_result.passed is False
    assert "source_concept_llm_judgment_call_cap_exceeded" in _error_codes(fail_result)
    assert approved_result.passed is True


def test_source_concept_full_chain_rejects_partial_llm_pair_resolution() -> None:
    partial = _source_concept_summary(llm_judgment_count=1)
    complete = _source_concept_summary(llm_judgment_count=12)
    cached = _source_concept_summary(llm_judgment_count=10, llm_cache_summary={"cached_decision_count": 2})
    missing_cache = _source_concept_summary(llm_judgment_count=10)

    partial_result = check_phase_contract("source_concept_full_chain_contract_v1", partial)
    complete_result = check_phase_contract("source_concept_full_chain_contract_v1", complete)
    cached_result = check_phase_contract("source_concept_full_chain_contract_v1", cached)
    missing_cache_result = check_phase_contract("source_concept_full_chain_contract_v1", missing_cache)

    assert partial_result.passed is False
    assert "source_concept_llm_selected_pairs_not_resolved" in _error_codes(partial_result)
    assert complete_result.passed is True
    assert cached_result.passed is True
    assert missing_cache_result.passed is False
    assert "source_concept_llm_selected_pairs_not_resolved" in _error_codes(missing_cache_result)


def test_source_concept_full_chain_fails_missing_llm_counters() -> None:
    missing_eligible = _source_concept_summary()
    missing_eligible["llm_adjudication_plan"] = dict(missing_eligible["llm_adjudication_plan"])
    missing_eligible["llm_adjudication_plan"].pop("eligible_pair_count")
    missing_selected = _source_concept_summary()
    missing_selected["llm_adjudication_plan"] = dict(missing_selected["llm_adjudication_plan"])
    missing_selected["llm_adjudication_plan"].pop("selected_pair_count")
    missing_judgments = _source_concept_summary()
    missing_judgments.pop("llm_judgment_count")

    for summary in (missing_eligible, missing_selected, missing_judgments):
        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)
        assert result.passed is False
        assert "source_concept_missing_llm_counter" in _error_codes(result)


def test_source_concept_zero_eligible_proof_requires_consistent_counters() -> None:
    selected_nonzero = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    selected_nonzero["llm_adjudication_plan"] = {
        **selected_nonzero["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 1,
        "zero_eligible_proof": True,
        "zero_eligible_reason": "No eligible pairs.",
    }
    judgments_nonzero = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=1)
    judgments_nonzero["llm_adjudication_plan"] = {
        **judgments_nonzero["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
        "zero_eligible_proof": True,
        "zero_eligible_reason": "No eligible pairs.",
    }
    no_reason = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    no_reason["llm_adjudication_plan"] = {
        **no_reason["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
        "zero_eligible_proof": True,
    }

    for summary in (selected_nonzero, judgments_nonzero, no_reason):
        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)
        assert result.passed is False
        assert "source_concept_zero_eligible_proof_incomplete" in _error_codes(result)


def test_source_concept_full_chain_fails_missing_validation_pack() -> None:
    summary = _source_concept_summary()
    summary.pop("validation_pack")

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_required_proof_missing" in _error_codes(result)


def test_source_concept_deterministic_only_allowed_only_without_completion_claim() -> None:
    executed = [
        stage
        for stage in SOURCE_CONCEPT_FULL_CHAIN_STAGES
        if not stage.startswith("llm_") and not stage.startswith("bounded_llm")
    ]
    summary = _source_concept_summary(
        pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": "deterministic_only"},
        executed_stages=executed,
        missing_required_stages=[stage for stage in SOURCE_CONCEPT_FULL_CHAIN_STAGES if stage not in executed],
        full_chain_fidelity_passed=False,
        llm_adjudication_used=False,
        llm_judgment_count=0,
        llm_max_calls=0,
        llm_budget_usd=0.0,
        llm_provider_mode="not_applicable_deterministic_only",
        llm_cache_summary={"cache_enabled": False},
        conclusion="deterministic_only",
    )

    allowed = check_phase_contract("source_concept_full_chain_contract_v1", summary)
    assert allowed.passed is True
    assert len(allowed.warnings) >= 1

    summary["pipeline_contract"] = {
        "contract_id": "source_concept_full_chain_contract_v1",
        "status": "deterministic_only",
        "claims": {"full_chain_complete": True},
    }
    blocked = check_phase_contract("source_concept_full_chain_contract_v1", summary)
    assert blocked.passed is False
    assert "deterministic_only_claimed_completion" in _error_codes(blocked)


def test_source_concept_blocked_required_stage_does_not_count_as_completed() -> None:
    for stage_status in ("blocked_before_write", "skipped"):
        summary = _source_concept_summary(
            stages={"llm_provider_availability_check": {"status": stage_status}},
        )

        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

        assert result.passed is False
        assert "source_concept_required_stage_missing" in _error_codes(result)


def test_source_concept_blocked_status_with_blocked_stage_cannot_claim_safe_to_merge() -> None:
    summary = _source_concept_summary(
        pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": "full_chain_blocked_llm_unavailable"},
        safe_to_merge=True,
        stages={"llm_provider_availability_check": {"status": "blocked_before_write"}},
        full_chain_fidelity_passed=False,
        llm_adjudication_used=False,
        llm_judgment_count=0,
    )

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "blocked_status_claimed_completion" in _error_codes(result)


def test_forbidden_stage_executed_true_fails_even_with_negative_status() -> None:
    provider = check_phase_contract(
        "source_concept_full_chain_contract_v1",
        {"stages": {"provider_enrichment_call": {"executed": True, "status": "skipped"}}},
    )
    upload = check_phase_contract(
        "source_concept_full_chain_contract_v1",
        {"stages": {"image_upload": {"executed": True, "status": "blocked"}}},
    )
    not_executed = check_phase_contract(
        "source_concept_full_chain_contract_v1",
        {"stages": {"image_upload": {"executed": False, "status": "skipped"}}},
    )

    assert provider.passed is False
    assert "forbidden_stage_executed" in _error_codes(provider)
    assert upload.passed is False
    assert "forbidden_stage_executed" in _error_codes(upload)
    assert "forbidden_stage_executed" not in _error_codes(not_executed)


def test_route_audit_blocks_route_approval_if_upstream_pipeline_incomplete() -> None:
    summary = _route_audit_summary(final_route_decision_status="route_approved", route_approved=True)

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert result.route_approved is True
    assert "route_approval_upstream_incomplete" in _error_codes(result)


def test_route_audit_route_status_takes_priority_over_pipeline_status() -> None:
    summary = _route_audit_summary(
        pipeline_contract={"contract_id": "route_audit_contract_v1", "status": "passed"},
        final_route_decision_status="route_approved",
        upstream_pipeline_contract=_route_full_chain_upstream(status="deterministic_only"),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.route_approved is True
    assert result.status == "route_approved"
    assert result.passed is False
    assert "route_approval_upstream_blocked_or_deterministic" in _error_codes(result)


def test_route_audit_route_status_approval_requires_review_pack_even_with_pipeline_passed() -> None:
    summary = _route_audit_summary(
        pipeline_contract={"contract_id": "route_audit_contract_v1", "status": "passed"},
        final_route_decision_status="route_approved",
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )
    summary.pop("chatgpt_review_pack")

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.route_approved is True
    assert result.passed is False
    assert "route_audit_route_approval_missing_review_pack" in _error_codes(result)


def test_route_audit_inconclusive_status_cannot_claim_route_approved() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="inconclusive_missing_artifacts",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_approval_blocked_or_provisional_status" in _error_codes(result)


def test_route_audit_fails_blocked_status_with_route_approved_true() -> None:
    summary = _route_audit_summary(
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_approval_blocked_or_provisional_status" in _error_codes(result)


def test_route_audit_fails_mutation_proof_false() -> None:
    summary = _route_audit_summary(mutation_proof={"passed": False, "changed_tables": []})

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_audit_mutation_proof_failed" in _error_codes(result)


def test_route_audit_requires_positive_mutation_proof_for_blocked_routes() -> None:
    empty = check_phase_contract("route_audit_contract_v1", _route_audit_summary(mutation_proof={}))
    changed_only = check_phase_contract("route_audit_contract_v1", _route_audit_summary(mutation_proof={"changed_tables": []}))
    passed = check_phase_contract("route_audit_contract_v1", _route_audit_summary(mutation_proof={"passed": True, "changed_tables": []}))

    assert empty.passed is False
    assert "route_audit_mutation_proof_failed" in _error_codes(empty)
    assert changed_only.passed is False
    assert "route_audit_mutation_proof_failed" in _error_codes(changed_only)
    assert passed.passed is True


def test_route_audit_fails_forbidden_mutation_tables() -> None:
    summary = _route_audit_summary(
        mutation_proof={"passed": True, "forbidden_changed_tables": ["blombooru_media_tags"], "unexpected_changed_tables": []}
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_audit_mutation_forbidden_table_changed" in _error_codes(result)


def test_route_audit_fails_route_approved_without_review_pack() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )
    summary.pop("chatgpt_review_pack")

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_audit_route_approval_missing_review_pack" in _error_codes(result)


def test_route_audit_route_approved_requires_complete_review_pack_proof() -> None:
    generated_only = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
        chatgpt_review_pack={"generated": True},
    )
    complete = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
        chatgpt_review_pack=_complete_review_pack_proof(),
    )

    generated_only_result = check_phase_contract("route_audit_contract_v1", generated_only)
    complete_result = check_phase_contract("route_audit_contract_v1", complete)

    assert generated_only_result.passed is False
    assert "route_audit_route_approval_incomplete_review_pack" in _error_codes(generated_only_result)
    assert complete_result.passed is True


def test_route_audit_fails_route_approved_with_deterministic_only_upstream() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(status="deterministic_only"),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_approval_upstream_not_full_chain_completed" in _error_codes(result)
    assert "route_approval_upstream_blocked_or_deterministic" in _error_codes(result)


def test_route_audit_allows_route_approved_with_full_chain_upstream() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is True
    assert result.route_approved is True


def test_route_audit_requires_upstream_contract_passed_and_missing_stages_list() -> None:
    passed_false = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(passed=False, full_chain_fidelity_passed=True),
    )
    missing_absent = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract={
            "contract_id": "source_concept_full_chain_contract_v1",
            "passed": True,
            "status": "full_chain_completed",
            "full_chain_fidelity_passed": True,
        },
    )

    passed_false_result = check_phase_contract("route_audit_contract_v1", passed_false)
    missing_absent_result = check_phase_contract("route_audit_contract_v1", missing_absent)

    assert passed_false_result.passed is False
    assert "route_approval_upstream_contract_not_passed" in _error_codes(passed_false_result)
    assert missing_absent_result.passed is False
    assert "route_approval_upstream_missing_required_stages_absent" in _error_codes(missing_absent_result)


def test_review_pack_contract_fails_missing_manifest_checksum_redaction_scan() -> None:
    result = check_phase_contract("review_pack_contract_v1", {"review_pack": {"generated": True}})

    assert result.passed is False
    assert "review_pack_required_flag_missing" in _error_codes(result)
    assert "review_pack_checksum_count_missing" in _error_codes(result)


def test_review_pack_contract_fails_missing_public_report_copy_proof() -> None:
    summary = _review_pack_summary(
        public_report_copy_current=False,
        public_report_copy_fresh=False,
        public_report_copy_rendered_from_current_summary=False,
        public_report_copy_generated_from_current_summary=False,
    )

    result = check_phase_contract("review_pack_contract_v1", summary)

    assert result.passed is False
    assert "review_pack_public_report_copy_missing" in _error_codes(result)


def test_review_pack_public_report_copy_must_be_current() -> None:
    present_only = _review_pack_summary(
        public_report_copy_present=True,
        public_report_copy_current=False,
        public_report_copy_fresh=False,
        public_report_copy_rendered_from_current_summary=False,
        public_report_copy_generated_from_current_summary=False,
    )
    current = _review_pack_summary(public_report_copy_present=True, public_report_copy_current=True)
    rendered = _review_pack_summary(
        public_report_copy_current=False,
        public_report_copy_rendered_from_current_summary=True,
    )

    present_only_result = check_phase_contract("review_pack_contract_v1", present_only)
    current_result = check_phase_contract("review_pack_contract_v1", current)
    rendered_result = check_phase_contract("review_pack_contract_v1", rendered)

    assert present_only_result.passed is False
    assert "review_pack_public_report_copy_missing" in _error_codes(present_only_result)
    assert current_result.passed is True
    assert rendered_result.passed is True


def test_review_pack_contract_fails_fixed_salt_hashes_or_raw_labels() -> None:
    summary = _review_pack_summary(review_samples=[{"raw_label": "private filename label"}], fixed_salt_hash="abc123")

    result = check_phase_contract("review_pack_contract_v1", summary)

    assert result.passed is False
    assert "review_pack_private_label_leak" in _error_codes(result)


def test_public_redaction_contract_catches_markdown_and_json_leaks() -> None:
    summary = {
        "public_markdown_text": r"Leaked local path C:\Users\example\Pictures\secret.png",
        "public_json_payload": {"source_path": "/Users/example/private/secret.png"},
    }

    result = check_phase_contract("public_redaction_contract_v1", summary)

    assert result.passed is False
    assert any(error.code.startswith("public_redaction_") for error in result.errors)


def test_public_redaction_contract_catches_bare_filenames_in_markdown() -> None:
    result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "sample IMG_1234.JPG leaked"})

    assert result.passed is False
    assert "public_redaction_bare_filename" in _error_codes(result)


def test_public_redaction_contract_does_not_echo_sensitive_matches() -> None:
    leaks = [
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "xoxb-1234567890-abcdefghijk",
        r"C:\Users\name\secret.png",
        "/tmp/private/file.png",
        "IMG_1234.JPG",
    ]

    for leak in leaks:
        result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": f"leak {leak}"})
        serialized = _serialized_result(result)
        assert result.passed is False, leak
        assert leak not in serialized
        assert "[redacted-match]" in serialized


def test_public_redaction_contract_sanitizes_sensitive_json_key_paths() -> None:
    payloads = [
        {r"C:\Users\name\secret.png": "value"},
        {"/tmp/private/file.png": "value"},
        {"IMG_1234.JPG": "value"},
        {"source_url": {"https://example.com/source/123": "value"}},
    ]
    leaks = [
        r"C:\Users\name\secret.png",
        "/tmp/private/file.png",
        "IMG_1234.JPG",
        "https://example.com/source/123",
    ]

    for payload, leak in zip(payloads, leaks):
        result = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": payload})
        serialized = _serialized_result(result)
        assert result.passed is False, leak
        assert leak not in serialized
        assert "[redacted-key]" in serialized or "[redacted-match]" in serialized


def test_public_redaction_contract_catches_sensitive_filename_json_keys() -> None:
    result = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"raw_filename": "IMG_1234.JPG"}})

    assert result.passed is False
    codes = _error_codes(result)
    assert "public_redaction_bare_filename" in codes
    assert "public_redaction_private_provenance_value_unredacted" in codes


def test_public_redaction_contract_catches_sensitive_public_urls_unless_redacted() -> None:
    leaked = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_url": "https://example.invalid/post/1"}})
    redacted = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_url": "[redacted]"}})

    assert leaked.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(leaked)
    assert redacted.passed is True


def test_public_redaction_contract_allows_public_api_route_text() -> None:
    api_route = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "GET /api/admin/media"})
    generic_route = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "/foo/bar"})
    provenance_route = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_path": "/foo/bar"}})
    tmp_path = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "/tmp/private/file.png"})
    workspace_path = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "/workspace/VIOLET/.env"})

    assert api_route.passed is True
    assert generic_route.passed is True
    assert provenance_route.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(provenance_route)
    assert tmp_path.passed is False
    assert workspace_path.passed is False


def test_public_redaction_contract_scans_sensitive_non_string_values() -> None:
    api_key_number = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"api_key": 123456}})
    password_bool = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"password": True}})
    source_url_number = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_url": 123456}})
    redacted = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"api_key": "[redacted]"}})

    assert api_key_number.passed is False
    assert "public_redaction_secret_key_name_with_unredacted_value" in _error_codes(api_key_number)
    assert "123456" not in _serialized_result(api_key_number)
    assert password_bool.passed is False
    assert "public_redaction_secret_key_name_with_unredacted_value" in _error_codes(password_bool)
    assert source_url_number.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(source_url_number)
    assert redacted.passed is True


def test_public_redaction_contract_propagates_private_provenance_context() -> None:
    leaked_url = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"source_url": {"value": "https://example.com/x"}}},
    )
    redacted_url = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"source_url": {"value": "[redacted]"}}},
    )
    leaked_filename = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"raw_filename": {"value": "IMG_1234.JPG"}}},
    )

    assert leaked_url.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(leaked_url)
    assert redacted_url.passed is True
    assert leaked_filename.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(leaked_filename)
    assert "public_redaction_bare_filename" in _error_codes(leaked_filename)


def test_public_redaction_contract_propagates_secret_parent_context() -> None:
    leaked = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"api_key": {"value": "internal-prod-key"}}},
    )
    redacted = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"api_key": {"value": "[redacted]"}}},
    )

    assert leaked.passed is False
    assert "public_redaction_secret_key_name_with_unredacted_value" in _error_codes(leaked)
    assert "internal-prod-key" not in _serialized_result(leaked)
    assert redacted.passed is True


def test_public_redaction_contract_catches_secret_key_names_and_token_formats() -> None:
    summary = {"public_json_payload": {"api_key": "sk-testsecret12345", "auth": "Authorization: Bearer abcdefghijk"}}

    result = check_phase_contract("public_redaction_contract_v1", summary)

    assert result.passed is False
    codes = _error_codes(result)
    assert "public_redaction_secret_key_name_with_unredacted_value" in codes
    assert "public_redaction_common_secret_or_token" in codes


def test_public_redaction_contract_catches_bare_token_formats() -> None:
    leaks = [
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "xoxb-1234567890-abcdefghijk",
    ]

    for leak in leaks:
        result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": f"token {leak}"})
        assert result.passed is False, leak
        assert "public_redaction_common_secret_or_token" in _error_codes(result)


def test_public_redaction_contract_catches_private_path_shapes() -> None:
    leaks = [
        r"D:\library\private.png",
        r"\\nas-host\share\private.png",
        "file:///Users/example/private.png",
        "/Users/example/private.png",
        "/home/example/private.png",
        "/mnt/nas/private.png",
        "/Volumes/Archive/private.png",
        "/tmp/private.png",
        "/workspace/private.png",
        "/workspace/VIOLET/.env",
        "/opt/private.png",
        "/var/private.png",
        "/tmp/private/file.png",
    ]

    for leak in leaks:
        result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": f"leak {leak}"})
        assert result.passed is False, leak


def test_mutation_safety_contract_fails_unexpected_forbidden_table_changes() -> None:
    summary = {
        "mutation_proof": {
            "passed": False,
            "forbidden_changed_tables": ["blombooru_media_tags"],
            "unexpected_changed_tables": [{"table": "blombooru_media", "allowed": False}],
        }
    }

    result = check_phase_contract("mutation_safety_contract_v1", summary)

    assert result.passed is False
    assert "mutation_forbidden_table_changed" in _error_codes(result)
    assert "mutation_unexpected_table_changed" in _error_codes(result)


def test_mutation_safety_contract_fails_false_passed_without_table_deltas() -> None:
    result = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {"passed": False, "changed_tables": []}})

    assert result.passed is False
    assert "mutation_proof_failed" in _error_codes(result)


def test_mutation_safety_contract_requires_positive_passed_proof() -> None:
    empty = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {}})
    missing = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {"changed_tables": []}})
    passed = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {"passed": True, "changed_tables": []}})

    assert empty.passed is False
    assert "mutation_proof_failed" in _error_codes(empty)
    assert missing.passed is False
    assert "mutation_proof_failed" in _error_codes(missing)
    assert passed.passed is True


def test_mutation_safety_contract_fails_non_list_table_violations() -> None:
    forbidden_string = check_phase_contract(
        "mutation_safety_contract_v1",
        {"mutation_proof": {"passed": True, "forbidden_changed_tables": "media_tags"}},
    )
    unexpected_dict = check_phase_contract(
        "mutation_safety_contract_v1",
        {"mutation_proof": {"passed": True, "unexpected_changed_tables": {"table": "media_tags"}}},
    )

    assert forbidden_string.passed is False
    assert "mutation_forbidden_table_changed" in _error_codes(forbidden_string)
    assert unexpected_dict.passed is False
    assert "mutation_unexpected_table_changed" in _error_codes(unexpected_dict)


def test_artifact_lifecycle_contract_distinguishes_public_and_private_artifacts() -> None:
    good = {
        "artifact_lifecycle": {
            "artifacts": [
                {"path": "docs/reports/gov3.md", "classification": "public report/handoff", "committed": True, "redacted": True},
                {"path": ".local_manifests/gov3", "classification": "one-off local/private", "committed": False},
            ]
        }
    }
    bad = {
        "artifact_lifecycle": {
            "artifacts": [
                {"path": ".local_manifests/private.zip", "classification": "one-off local/private", "committed": True}
            ]
        }
    }

    assert check_phase_contract("artifact_lifecycle_contract_v1", good).passed is True
    result = check_phase_contract("artifact_lifecycle_contract_v1", bad)
    assert result.passed is False
    assert "private_artifact_committed" in _error_codes(result)


def test_artifact_lifecycle_contract_requires_public_redaction_evidence() -> None:
    missing_redaction = {
        "artifact_lifecycle": {
            "artifacts": [{"path": "docs/reports/gov3.md", "classification": "public report/handoff", "committed": True}]
        }
    }
    redacted = {
        "artifact_lifecycle": {
            "artifacts": [
                {"path": "docs/reports/gov3.md", "classification": "public report/handoff", "committed": True, "redacted": True}
            ]
        }
    }

    missing_result = check_phase_contract("artifact_lifecycle_contract_v1", missing_redaction)
    redacted_result = check_phase_contract("artifact_lifecycle_contract_v1", redacted)

    assert missing_result.passed is False
    assert "public_artifact_redaction_evidence_missing" in _error_codes(missing_result)
    assert redacted_result.passed is True


def test_artifact_lifecycle_contract_normalizes_review_pack_classification() -> None:
    for classification in ("review pack", "review_pack", "review-pack"):
        result = check_phase_contract(
            "artifact_lifecycle_contract_v1",
            {"artifact_lifecycle": {"artifacts": [{"path": ".local_manifests/pack.zip", "classification": classification, "committed": True}]}},
        )
        assert result.passed is False
        assert "review_pack_committed" in _error_codes(result)


def test_artifact_lifecycle_contract_normalizes_public_classification() -> None:
    for classification in ("public_report", "public-report", "public handoff", "public_handoff", "public-handoff"):
        result = check_phase_contract(
            "artifact_lifecycle_contract_v1",
            {"artifact_lifecycle": {"artifacts": [{"path": "docs/reports/gov3.md", "classification": classification, "committed": True}]}},
        )
        assert result.passed is False
        assert "public_artifact_redaction_evidence_missing" in _error_codes(result)

    passed = check_phase_contract(
        "artifact_lifecycle_contract_v1",
        {"artifact_lifecycle": {"artifacts": [{"path": "docs/reports/gov3.md", "classification": "public report", "committed": True, "redacted": True}]}},
    )
    assert passed.passed is True


def test_required_summary_fields_do_not_accept_null_values() -> None:
    result = check_phase_contract(
        "python_env_contract_v1",
        {
            "python_env": {
                "expected_python_checked": None,
                "check_python_env_passed": True,
                "public_executable_name": "python.exe",
                "executable_path_redacted": True,
            }
        },
    )

    assert result.passed is False
    assert "missing_required_summary_field" in _error_codes(result)


def test_claimed_completion_requires_matching_contract_id() -> None:
    result = check_phase_contract(
        "route_audit_contract_v1",
        _route_audit_summary(
            pipeline_contract={"contract_id": "wrong_contract_v1"},
            final_route_decision_status="route_approved",
            route_approved=True,
            upstream_pipeline_contract=_route_full_chain_upstream(),
        ),
    )

    assert result.passed is False
    assert "claimed_completion_contract_id_mismatch" in _error_codes(result)


def test_required_artifact_and_ledger_fields_must_be_non_empty() -> None:
    source_metadata_base = {
        "provider_policy": {"explicitly_approved": True},
        "provider_identity": {"no_secret_logging": True},
        "request_ledger": {"entry_count": 1},
        "failure_ledger": {"entry_count": 0, "zero_failure_reason": "No provider calls failed."},
        "cache_retry_rate_limit_accounting": {"passed": True},
        "source_metadata_write_allowlist": {"passed": True},
        "entity_truth_proof": {"no_entity_truth": True},
        "media_tags_mutation_proof": {"no_media_tags_mutation": True},
        "image_upload_policy": {"uploaded_images": False},
        "public_private_artifact_boundary": {"passed": True},
    }
    empty_request = {**source_metadata_base, "request_ledger": {}}
    empty_failure = {**source_metadata_base, "failure_ledger": {}}
    media_import_null = {
        "source_root_safety_proof": {"passed": True},
        "staging_root_safety_proof": {"passed": True},
        "import_ledger": None,
        "media_counts": {"before": 0, "after": 0},
        "duplicate_path_leak_proof": {"passed": True},
        "mutation_proof": {"passed": True},
        "rollback_recovery_notes": "No import run.",
    }
    media_import_empty = {**media_import_null, "import_ledger": {}}

    empty_request_result = check_phase_contract("source_metadata_contract_v1", empty_request)
    empty_failure_result = check_phase_contract("source_metadata_contract_v1", empty_failure)
    null_import_result = check_phase_contract("media_import_contract_v1", media_import_null)
    empty_import_result = check_phase_contract("media_import_contract_v1", media_import_empty)

    assert "empty_required_artifact_or_proof" in _error_codes(empty_request_result)
    assert "empty_required_artifact_or_proof" in _error_codes(empty_failure_result)
    assert "missing_required_summary_field" in _error_codes(null_import_result)
    assert "empty_required_artifact_or_proof" in _error_codes(empty_import_result)


def test_postgres_db_contract_rejects_nested_password_fields() -> None:
    summary = {
        "db_identity": {
            "db_resolution": {
                "runner_matches_app_equivalent": True,
                "password_value_recorded": False,
                "password": "secret-db-password",
            }
        }
    }

    result = check_phase_contract("postgres_db_contract_v1", summary)

    assert result.passed is False
    assert "db_secret_field_recorded" in _error_codes(result)


def test_postgres_db_contract_allows_password_presence_boolean_without_value() -> None:
    summary = {
        "db_identity": {
            "db_resolution": {
                "runner_matches_app_equivalent": True,
                "password_present": True,
                "password_value_recorded": False,
            }
        }
    }

    result = check_phase_contract("postgres_db_contract_v1", summary)

    assert result.passed is True


def test_destructive_operation_contract_fails_without_explicit_approval() -> None:
    summary = {
        "destructive_operation": {
            "dry_run_first": True,
            "backup_recovery_plan": True,
            "exact_target_set": True,
            "no_broad_wildcard_deletion": True,
            "post_run_verification": True,
        }
    }

    result = check_phase_contract("destructive_operation_contract_v1", summary)

    assert result.passed is False
    assert "destructive_operation_gate_missing" in _error_codes(result)


def test_entity_truth_bridge_contract_fails_without_preview_manual_audit_rollback_gates() -> None:
    result = check_phase_contract("entity_truth_bridge_contract_v1", {"entity_truth_bridge": {"route_approval": True}})

    assert result.passed is False
    assert "entity_truth_bridge_gate_missing" in _error_codes(result)


def test_existing_a1_and_inc1_summaries_remain_blocked_not_route_approved() -> None:
    a1 = json.loads((ROOT / "docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision-summary.json").read_text(encoding="utf-8"))
    inc1 = json.loads((ROOT / "docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity-summary.json").read_text(encoding="utf-8"))

    a1_result = check_phase_contract("route_audit_contract_v1", a1)
    inc1_redaction = check_phase_contract("public_redaction_contract_v1", inc1)

    assert a1_result.route_approved is False
    assert "blocked" in str(a1_result.status)
    assert inc1_redaction.route_approved is False
    assert inc1["llm_adjudication_fidelity"]["conclusion"] == "llm_stage_missing_incident"


def test_mock_future_r1r_summary_passes_only_with_all_stages_and_llm_proof() -> None:
    passing = load_summary_file(FIXTURE_DIR / "mock_source_concept_full_chain_pass.json")
    failing = dict(passing)
    failing["executed_stages"] = [stage for stage in passing["executed_stages"] if stage != "llm_cache_accounting"]
    failing["missing_required_stages"] = ["llm_cache_accounting"]

    pass_result = check_phase_contract("source_concept_full_chain_contract_v1", passing)
    fail_result = check_phase_contract("source_concept_full_chain_contract_v1", failing)

    assert pass_result.passed is True
    assert fail_result.passed is False
    assert "source_concept_required_stage_missing" in _error_codes(fail_result)
