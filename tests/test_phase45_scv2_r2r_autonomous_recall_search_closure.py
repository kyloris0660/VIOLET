"""Focused SCV2-R2R autonomous closure contract tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import sys
from pathlib import Path
from typing import Mapping

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base
from app.enums import FileTypeEnum
from app.models import (
    Media,
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptFallbackSearchIndex,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
)
from app.services.source_concept_autonomous_closure_service import (
    FIRST_PASS_VERSION,
    CandidatePair,
    PairDisposition,
    build_candidate_pair_manifest,
    build_second_pass_payload,
    disposition_accounting,
    estimate_autonomous_budget,
    execute_autonomous_missing_pairs,
    project_autonomous_materialization,
)
from app.services.source_concept_resolver_service import (
    SourceConceptEdgeDraft,
    SourceConceptSignalDraft,
    resolve_source_concepts,
)
from app.services.source_concept_search_service import (
    R2R_FALLBACK_DISPOSITION_VERSION,
    R2R_FALLBACK_INDEX_VERSION,
    _query_overlay_fallback_rows,
    rebuild_source_concept_fallback_search_index,
    source_layer_search_path_media_ids,
)
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2_runner
from scripts import run_phase45_scv2_r2r_autonomous_recall_search_closure as r2r_runner
from scripts.phase_contracts.contract_checks import check_phase_contract
from scripts.phase_contracts.contract_registry import get_contract


CONTRACT_ID = "r2r_autonomous_recall_search_closure_contract_v1"


def _summary(*, target: bool = True) -> dict:
    status = (
        "target_met_autonomous_recall_search_closure"
        if target
        else "blocked_llm_approval_required"
    )
    total = 12
    accounted = total if target else 5
    unaccounted = 0 if target else total - accounted
    return {
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {
                "target_met": target,
                "safe_to_merge": False,
                "route_approved": False,
            },
        },
        "environment_isolation": {
            "passed": True,
            "dev_test_only": True,
            "working_db_is_separate_from_r2_baseline": True,
            "r2_baseline_preserved": True,
            "production_profile_active": False,
            "canonical_production_profile_flag_checked": True,
            "production_write_attempted": False,
            "protected_source_write_attempted": False,
        },
        "fixed_input_proof": {
            "present": True,
            "baseline_to_working_clone_match": True,
            "before_after_match": True,
            "row_counts_match": True,
            "schemas_match": True,
            "content_fingerprints_match": True,
            "forbidden_truth_content_unchanged": True,
            "changed_tables": [],
            "forbidden_truth_changed_tables": [],
        },
        "operation_counts": {
            "gallery_dl_calls": 0,
            "provider_metadata_acquisition_calls": 0,
            "pixiv_provider_calls": 0,
            "ai_tagging_calls": 0,
            "media_imports": 0,
            "classification_calls": 0,
            "localization_calls": 0,
            "upstream_observation_mutations": 0,
            "production_writes": 0,
            "truth_path_writes": 0,
            "fallback_provider_calls": 0,
            "primary_provider_calls": 12 if target else 0,
        },
        "candidate_population": {
            "total_candidate_pairs": total,
            "candidate_manifest_pair_count": total,
            "unique_budget_eligible_pair_count": total,
        },
        "cache_reuse": {
            "exact_compatible_cache_hit_count": 0,
            "stable_compatible_reuse_count": 5,
            "semantic_prior_count": 7,
            "genuinely_missing_pair_count": 7,
        },
        "provider_authorization": {
            "status": "approved" if target else "pending",
            "approved_scope": "pr_135_autonomous_pair_closure" if target else None,
            "primary_provider_only": True,
            "fixed_monetary_cap": None,
            "further_budget_approval_required": False if target else True,
            "first_pass_authorized": target,
            "second_pass_authorized": target,
            "compatible_deferred_reescalation_authorized": target,
            "post_rebuild_new_pair_authorized": target,
            "bounded_retry_authorized": target,
            "fallback_provider_authorized": False,
            "metadata_acquisition_authorized": False,
            "other_phase_authorized": False,
        },
        "llm_execution": {
            "all_approved_missing_pairs_accounted": target,
            "remaining_unaccounted_missing_pairs": 0 if target else 7,
            "provider_failure_count": 0,
            "failed_judgments_counted_as_success": False,
            "primary_provider_only": True,
            "fallback_provider_used": False,
            "usage_accounting_complete": True,
        },
        "candidate_dispositions": {
            "must_link_count": 3,
            "cannot_link_count": 2,
            "deferred_nonblocking_count": 7 if target else 0,
            "unaccounted_pair_count": unaccounted,
            "candidate_disposition_coverage": 1.0 if target else round(accounted / total, 6),
            "accounting_equality_passed": target,
            "duplicate_disposition_count": 0,
            "silently_dropped_pair_count": 0,
        },
        "automation_invariants": {
            "manual_review_required_count": 0,
            "operator_blocking_review_count": 0,
            "manual_review_queue_generated": False,
        },
        "materialization_projection": {
            "materialized_needs_review_count": 0,
            "unresolved_evidence_retained": True,
            "idempotent_fingerprint_match": True,
            "deferred_overlay_versioned": True,
            "deferred_overlay_atomic": True,
        },
        "graph_invariants": {
            "review_or_deferred_edge_used_in_union_count": 0,
            "direct_cannot_violation_count": 0,
            "transitive_cannot_violation_count": 0,
            "deterministic_hard_conflict_count": 0,
            "unauthorized_unknown_role_materialization_count": 0,
            "unexplained_proof_grade_same_regression_count": 0,
        },
        "search_benchmark": {
            "generated": True,
            "reproducible": True,
            "identity_and_fallback_reported_separately": True,
            "symmetry_improved_vs_r2": True,
            "unmatched_seeds_decreased_vs_r2": True,
            "average_overlap_improved_vs_r2": True,
            "cannot_linked_search_contamination_count": 0,
            "false_broad_union_indicator_count": 0,
            "seeds_with_false_broad_union": 0,
            "unexpected_media_count": 0,
            "identity_path_cannot_contamination_count": 0,
            "evidence_fallback_cannot_contamination_count": 0,
            "giant_component_recurrence": False,
            "indexed_fallback": {
                "generated": True,
                "deterministic": True,
                "idempotent": True,
                "full_signal_python_scan_per_query": False,
                "source_layer_only": True,
                "identity_union_allowed": False,
            },
        },
        "checkpoint_proof": {
            "durable_checkpoint_passed": True,
            "atomic_per_success_persistence": True,
            "final_regeneration_cache_only": True,
            "final_regeneration_provider_calls": 0,
        },
        "public_redaction": {"passed": True},
        "review_pack": {
            "generated": True,
            "manifest_present": True,
            "checksums_present": True,
            "integrity_passed": True,
            "not_committed": True,
        },
        "route_authorization": {
            "px1_b_authorized": False,
            "provider_2_authorized": False,
            "scale_up_authorized": False,
            "entity_bridge_authorized": False,
            "production_authorized": False,
            "full_library_execution_authorized": False,
            "truth_promotion_authorized": False,
        },
    }


def test_r2r_contract_is_registered_before_execution() -> None:
    contract = get_contract(CONTRACT_ID)

    assert contract.contract_id == CONTRACT_ID
    assert "human_review_queue_generation" in contract.forbidden_stages
    assert "provider_metadata_acquisition" in contract.forbidden_stages


def test_r2r_target_contract_accepts_complete_autonomous_proof() -> None:
    result = check_phase_contract(CONTRACT_ID, _summary())

    assert result.passed, [finding.to_dict() for finding in result.errors]


def test_r2r_cache_only_approval_block_is_truthful_and_noncompleting() -> None:
    result = check_phase_contract(CONTRACT_ID, _summary(target=False))

    assert result.passed, [finding.to_dict() for finding in result.errors]
    assert result.target_met_claimed is False


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("automation_invariants", "manual_review_required_count"), 1, "r2r_human_review_dependency_present"),
        (("automation_invariants", "manual_review_queue_generated"), True, "r2r_human_review_dependency_present"),
        (("materialization_projection", "materialized_needs_review_count"), 1, "r2r_materialization_projection_failed"),
        (("graph_invariants", "transitive_cannot_violation_count"), 1, "r2r_constraint_regression"),
        (("search_benchmark", "cannot_linked_search_contamination_count"), 1, "r2r_search_target_failed"),
        (("search_benchmark", "false_broad_union_indicator_count"), 1, "r2r_search_target_failed"),
        (("checkpoint_proof", "final_regeneration_cache_only"), False, "r2r_llm_checkpoint_incomplete"),
    ],
)
def test_r2r_contract_fails_closed_on_target_regressions(path: tuple[str, str], value: object, code: str) -> None:
    summary = deepcopy(_summary())
    summary[path[0]][path[1]] = value

    result = check_phase_contract(CONTRACT_ID, summary)

    assert not result.passed
    assert code in {finding.code for finding in result.errors}


def test_r2r_contract_enforces_exact_candidate_disposition_equality() -> None:
    summary = _summary()
    summary["candidate_dispositions"]["deferred_nonblocking_count"] -= 1

    result = check_phase_contract(CONTRACT_ID, summary)

    assert not result.passed
    assert "r2r_candidate_disposition_accounting_incomplete" in {
        finding.code for finding in result.errors
    }


def test_r2r_approval_block_rejects_any_provider_call() -> None:
    summary = _summary(target=False)
    summary["operation_counts"]["primary_provider_calls"] = 1

    result = check_phase_contract(CONTRACT_ID, summary)

    assert not result.passed
    assert "r2r_provider_called_before_approval" in {finding.code for finding in result.errors}


def test_r2r_contract_accepts_scope_bounded_authorized_execution_state() -> None:
    summary = _summary(target=False)
    summary["pipeline_contract"]["status"] = "partial_autonomous_closure"
    summary["provider_authorization"] = r2r_runner.provider_authorization()
    summary["operation_counts"]["primary_provider_calls"] = 1
    summary["llm_execution"]["operator_approval_required"] = False
    summary["llm_execution"]["fixed_monetary_cap"] = None
    summary["llm_execution"]["further_budget_approval_required"] = False

    result = check_phase_contract(CONTRACT_ID, summary)

    assert result.passed, [finding.to_dict() for finding in result.errors]


def test_canonical_production_profile_flag_blocks_reused_r2_and_r2r_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("VIOLET_PRODUCTION_PROFILE_ACTIVE", "true")

    r2_isolation = r2_runner.environment_isolation(
        r2_runner.R1R_BASELINE_DB,
        "blombooru_scv2_r2_test_fixture",
    )
    r2r_isolation = r2r_runner.environment_isolation(
        r2r_runner.R2_BASELINE_DB,
        "blombooru_scv2_r2r_test_fixture",
    )

    assert r2_isolation["passed"] is False
    assert r2_isolation["canonical_production_profile_flag_checked"] is True
    assert r2r_isolation["passed"] is False
    assert r2r_isolation["canonical_production_profile_flag_checked"] is True


def test_r2r_runner_records_scope_bounded_primary_provider_authorization() -> None:
    source = Path(r2r_runner.__file__).read_text(encoding="utf-8")
    authorization = r2r_runner.provider_authorization()

    assert authorization["status"] == "approved"
    assert authorization["approved_scope"] == "pr_135_autonomous_pair_closure"
    assert authorization["fixed_monetary_cap"] is None
    assert authorization["further_budget_approval_required"] is False
    assert authorization["primary_provider_only"] is True
    assert authorization["fallback_provider_authorized"] is False
    assert 'choices=("prepare", "dry-run", "execute")' in source
    assert "import gallery_dl" not in source
    assert "import requests" not in source


def test_primary_provider_executor_records_usage_without_fallback() -> None:
    class FakePrimaryProvider:
        last_usage = {}

        async def complete_json(self, messages, *, temperature, max_tokens):
            self.last_usage = {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            }
            payload = json.loads(messages[1]["content"])
            return {
                "pair_id": payload["pair_id"],
                "pass_version": payload["pass_version"],
                "decision": "cannot_link",
                "confidence": 0.95,
                "reason_code": "context_conflict",
            }

    executor = r2r_runner.PrimaryProviderJudgmentExecutor(
        FakePrimaryProvider(),
        {"uses_fallback_provider": False, "provider_mode": "primary_openai"},
    )
    candidate = _candidate()
    payload = {
        "candidate": candidate.__dict__,
        "pass_version": FIRST_PASS_VERSION,
    }

    response = executor("first", payload)
    summary = executor.public_summary()

    assert response["decision"] == "cannot_link"
    assert summary["attempted_calls"] == 1
    assert summary["total_tokens"] == 150
    assert summary["actual_cost_usd"] == 0.0003
    assert summary["fallback_provider_used"] is False


def test_durable_provider_usage_summary_detects_preinstrumentation_gap(tmp_path: Path) -> None:
    first_records = tmp_path / "first" / "records"
    first_failures = tmp_path / "first" / "failures"
    first_records.mkdir(parents=True)
    first_failures.mkdir(parents=True)
    (first_records / "measured.json").write_text(
        json.dumps(
            {
                "provider_usage": {
                    "usage_reported": True,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                }
            }
        ),
        encoding="utf-8",
    )
    (first_records / "missing.json").write_text(
        json.dumps({"success": True}),
        encoding="utf-8",
    )
    (first_failures / "failed.json").write_text(
        json.dumps(
            {
                "provider_call_attempted": True,
                "provider_usage": {
                    "usage_reported": True,
                    "prompt_tokens": 80,
                    "completion_tokens": 10,
                    "total_tokens": 90,
                },
            }
        ),
        encoding="utf-8",
    )

    summary = r2r_runner.summarize_durable_provider_usage(tmp_path)

    assert summary["attempted_calls"] == 3
    assert summary["usage_reported_call_count"] == 2
    assert summary["usage_missing_call_count"] == 1
    assert summary["total_tokens"] == 210
    assert summary["measured_cost_usd"] == 0.00042
    assert summary["actual_cost_usd"] is None
    assert summary["usage_accounting_complete"] is False


def test_public_retention_projection_field_does_not_trigger_path_redaction() -> None:
    payload = {
        "evidence_retention_projection": (
            "SourceConceptSignal projection plus private versioned pair overlay"
        )
    }

    findings = r2r_runner.scv1.scan_public_text(json.dumps(payload, sort_keys=True))

    assert not [finding for finding in findings if finding["type"] == "canonical_path_like"]


def test_public_budget_field_names_do_not_look_like_secret_tokens() -> None:
    budget = estimate_autonomous_budget(
        [],
        missing_pair_ids=[],
        signal_by_key={},
        historical_uncertain_rate=0.0,
    )

    redaction = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_redaction": {"passed": True}, "budget_projection": budget},
    )

    assert budget["usage_unit"] == "tokens"
    assert redaction.passed, [finding.to_dict() for finding in redaction.errors]


def _signal(
    key: str,
    value: str,
    *,
    status: str = "active",
    trust: str = "strong",
    work_context: str | None = "work_a",
    media_id: int | None = None,
) -> SourceConceptSignalDraft:
    normalized = value.casefold().replace(" ", "_")
    return SourceConceptSignalDraft(
        signal_key=key,
        origin_type="source_name_observation",
        origin_table="fixture",
        origin_id=key,
        provider="fixture",
        media_id=media_id,
        source_metadata_record_id=None,
        source_record_id=f"fixture:{key}",
        raw_value=value,
        display_value=value,
        normalized_key=normalized,
        canonical_key=normalized,
        role_hint="character",
        work_context_key=work_context,
        parenthetical_base=None,
        parenthetical_context=None,
        source_kind="source_name",
        trust_tier=trust,
        confidence=0.9,
        status=status,
        evidence_payload={},
    )


def _candidate(left: str = "left", right: str = "right") -> CandidatePair:
    return CandidatePair(
        pair_id="pair-1",
        left_signal_key=left,
        right_signal_key=right,
        edge_key="edge-1",
        edge_type="same_surface_context",
        edge_status="needs_review",
        weight=0.5,
        evidence_source="fixture",
        resolution_reason_code="fixture",
        negative_reason_code=None,
        payload_hash="payload-hash",
    )


def test_candidate_manifest_and_disposition_accounting_are_unique() -> None:
    left = _signal("left", "Alias Name")
    right = _signal("right", "Alias Name")
    duplicate_edges = [
        SourceConceptEdgeDraft(
            edge_key=f"edge-{idx}",
            left_signal_key="left",
            right_signal_key="right",
            edge_type="same_surface_context",
            weight=0.4 + idx / 10,
            evidence_source="fixture",
            status="needs_review",
            resolution_reason_code="fixture",
            negative_reason_code=None,
            union_allowed=False,
            payload={},
        )
        for idx in range(2)
    ]

    candidates = build_candidate_pair_manifest(duplicate_edges, signals=[left, right])
    accounting = disposition_accounting(
        candidates,
        [
            PairDisposition(
                pair_id=candidates[0].pair_id,
                left_signal_key="left",
                right_signal_key="right",
                disposition="deferred_nonblocking",
                source="fixture",
                pass_name="second",
                confidence=0.5,
                reason_code="insufficient_evidence",
            )
        ],
    )

    assert len(candidates) == 1
    assert accounting["accounting_equality_passed"] is True
    assert accounting["candidate_disposition_coverage"] == 1.0


def test_candidate_manifest_deduplicates_before_emergency_ceiling() -> None:
    signals = [
        _signal("left", "Alias Name"),
        _signal("right", "Alias Name"),
        _signal("later", "Alias Name"),
    ]
    edges = [
        SourceConceptEdgeDraft(
            edge_key="duplicate-weaker",
            left_signal_key="left",
            right_signal_key="right",
            edge_type="cooccurrence_context",
            weight=0.3,
            evidence_source="fixture",
            status="needs_review",
            resolution_reason_code="fixture",
            negative_reason_code=None,
            union_allowed=False,
            payload={},
        ),
        SourceConceptEdgeDraft(
            edge_key="duplicate-stronger",
            left_signal_key="right",
            right_signal_key="left",
            edge_type="exact_canonical_key",
            weight=0.9,
            evidence_source="fixture",
            status="needs_review",
            resolution_reason_code="fixture",
            negative_reason_code=None,
            union_allowed=False,
            payload={},
        ),
        SourceConceptEdgeDraft(
            edge_key="later-unique",
            left_signal_key="left",
            right_signal_key="later",
            edge_type="same_surface_context",
            weight=0.7,
            evidence_source="fixture",
            status="needs_review",
            resolution_reason_code="fixture",
            negative_reason_code=None,
            union_allowed=False,
            payload={},
        ),
    ]

    candidates = build_candidate_pair_manifest(edges, signals=signals, max_calls=2)

    assert len(candidates) == 2
    assert {candidate.edge_key for candidate in candidates} == {
        "duplicate-stronger",
        "later-unique",
    }


def test_projection_eliminates_materialized_needs_review_without_dropping_signals() -> None:
    active_left = _signal("active-left", "Stable Identity")
    active_right = _signal("active-right", "Stable Identity")
    isolated = _signal(
        "isolated",
        "Ambiguous",
        trust="weak",
        work_context=None,
    )
    result = resolve_source_concepts(
        [active_left, active_right, isolated],
        run_id="r2r-projection-test",
    )

    projected, proof = project_autonomous_materialization(result, dispositions=[])

    assert all(concept.status == "active" for concept in projected.concepts)
    assert proof["materialized_needs_review_count"] == 0
    assert proof["source_signal_count_before"] == proof["source_signal_count_after"] == 3
    assert proof["unresolved_evidence_retained"] is True
    assert "storage_model" not in proof
    assert "evidence_retention_projection" in proof
    assert {signal.status for signal in projected.signals} <= {
        "materialized_identity",
        "isolated_evidence",
        "rejected_evidence",
    }


def test_deferred_nonblocking_is_not_a_human_queue_or_union() -> None:
    left = _signal("left", "Ambiguous Name", work_context="work_a")
    right = _signal("right", "Ambiguous Name", work_context="work_b")
    result = resolve_source_concepts(
        [left, right],
        run_id="r2r-deferred-test",
        llm_judgments=[
            {
                "judgment_id": "fixture-deferred",
                "left_signal_key": "left",
                "right_signal_key": "right",
                "decision": "deferred_nonblocking",
                "confidence": 0.5,
            }
        ],
    )

    edge = next(edge for edge in result.edge_candidates if edge.edge_type == "deferred_nonblocking")
    assert edge.union_allowed is False
    assert edge.status == "deferred_nonblocking"
    assert len(result.concepts) == 2
    assert result.summary["review_only_edge_used_in_union_count"] == 0


def test_undermerge_diagnostic_classifies_transitive_cannot_blocker() -> None:
    signals = [
        _signal("a", "Stable Name"),
        _signal("b", "Stable Name"),
        _signal("c", "Stable Name"),
    ]
    result = resolve_source_concepts(
        signals,
        run_id="r2r-undermerge-test",
        llm_judgments=[
            {
                "judgment_id": "cannot-a-c",
                "left_signal_key": "a",
                "right_signal_key": "c",
                "decision": "cannot_link",
                "confidence": 0.99,
            }
        ],
    )

    classifications = result.summary["undermerge_split_classification_counts"]
    assert classifications.get("true_unexplained_undermerge", 0) == 0
    assert classifications.get("transitively_blocked_split", 0) >= 1
    assert result.summary["undermerge_violation_count"] == 0


def test_autonomous_passes_checkpoint_resume_and_never_count_failure_as_success(tmp_path: Path) -> None:
    candidate = _candidate()
    signals = {"left": _signal("left", "Alias"), "right": _signal("right", "Alias")}
    calls: list[str] = []

    def executor(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        calls.append(pass_name)
        if pass_name == "first":
            return {
                "pair_id": payload["candidate"]["pair_id"],
                "pass_version": payload["pass_version"],
                "decision": "deferred_nonblocking",
                "confidence": 0.5,
                "reason_code": "insufficient_fixed_evidence",
            }
        assert payload["human_escalation_allowed"] is False
        return {
            "pair_id": payload["candidate"]["pair_id"],
            "pass_version": payload["pass_version"],
            "decision": "cannot_link",
            "confidence": 0.91,
            "reason_code": "context_conflict",
        }

    dispositions, proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={},
        signal_by_key=signals,
        cache_root=tmp_path,
        executor=executor,
    )
    assert calls == ["first", "second"]
    assert dispositions[candidate.pair_id].disposition == "cannot_link"
    assert proof["accounting"]["accounting_equality_passed"] is True

    resumed_calls: list[str] = []

    def must_not_run(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        resumed_calls.append(pass_name)
        raise AssertionError("cache-only resume called provider")

    resumed, resumed_proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={},
        signal_by_key=signals,
        cache_root=tmp_path,
        executor=must_not_run,
    )
    assert resumed_calls == []
    assert resumed[candidate.pair_id].disposition == "cannot_link"
    assert resumed_proof["provider_failure"] == 0

    failing_root = tmp_path / "failing"

    def fails(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        raise RuntimeError("provider unavailable")

    failed, failed_proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={},
        signal_by_key=signals,
        cache_root=failing_root,
        executor=fails,
    )
    assert failed == {}
    assert failed_proof["provider_failure"] == 1
    assert failed_proof["accounting"]["unaccounted_pair_count"] == 1
    assert failed_proof["accounting"]["silently_dropped_pair_count"] == 0
    assert not list((failing_root / "first" / "records").glob("*.json"))

    malformed_root = tmp_path / "malformed"

    def malformed(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        return {
            "pair_id": payload["candidate"]["pair_id"],
            "pass_version": payload["pass_version"],
            "decision": "ask_a_human",
            "confidence": 0.5,
        }

    malformed_dispositions, malformed_proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={},
        signal_by_key=signals,
        cache_root=malformed_root,
        executor=malformed,
    )
    assert malformed_dispositions == {}
    assert malformed_proof["provider_failure"] == 1
    assert malformed_proof["accounting"]["unaccounted_pair_count"] == 1
    assert not list((malformed_root / "first" / "records").glob("*.json"))


@pytest.mark.parametrize("confidence", ["0.9", True, float("nan"), float("inf"), float("-inf"), -0.1, 1.1, None])
def test_invalid_confidence_never_writes_success_checkpoint(
    tmp_path: Path,
    confidence: object,
) -> None:
    candidate = _candidate()
    signals = {"left": _signal("left", "Alias"), "right": _signal("right", "Alias")}

    def invalid_executor(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        response = {
            "pair_id": candidate.pair_id,
            "pass_version": FIRST_PASS_VERSION,
            "decision": "must_link",
        }
        if confidence is not None:
            response["confidence"] = confidence
        return response

    dispositions, proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={},
        signal_by_key=signals,
        cache_root=tmp_path,
        executor=invalid_executor,
    )

    assert dispositions == {}
    assert proof["provider_failure"] == 1
    assert proof["accounting"]["unaccounted_pair_count"] == 1
    assert not list((tmp_path / "first" / "records").glob("*.json"))
    assert len(list((tmp_path / "first" / "failures").glob("*.json"))) == 1


def test_valid_numeric_confidence_writes_success_checkpoint(tmp_path: Path) -> None:
    candidate = _candidate()
    signals = {"left": _signal("left", "Alias"), "right": _signal("right", "Alias")}

    def valid_executor(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        return {
            "pair_id": candidate.pair_id,
            "pass_version": FIRST_PASS_VERSION,
            "decision": "must_link",
            "confidence": 0.9,
            "reason_code": "strong_fixed_evidence",
        }

    dispositions, proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={},
        signal_by_key=signals,
        cache_root=tmp_path,
        executor=valid_executor,
    )

    assert dispositions[candidate.pair_id].disposition == "must_link"
    assert proof["provider_failure"] == 0
    assert len(list((tmp_path / "first" / "records").glob("*.json"))) == 1


def test_malformed_attempt_is_retryable_and_valid_retry_writes_success(tmp_path: Path) -> None:
    candidate = _candidate()
    signals = {"left": _signal("left", "Alias"), "right": _signal("right", "Alias")}
    attempts = 0

    def retrying_executor(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal attempts
        attempts += 1
        return {
            "pair_id": candidate.pair_id,
            "pass_version": FIRST_PASS_VERSION,
            "decision": "must_link",
            "confidence": "0.9" if attempts == 1 else 0.9,
            "reason_code": "strong_fixed_evidence",
        }

    dispositions, proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={},
        signal_by_key=signals,
        cache_root=tmp_path,
        executor=retrying_executor,
        max_attempts_per_pass=3,
    )

    assert attempts == 2
    assert dispositions[candidate.pair_id].disposition == "must_link"
    assert proof["retry_count"] == 1
    assert proof["malformed_response_attempt_count"] == 1
    assert proof["provider_failure"] == 0
    assert len(list((tmp_path / "first" / "records").glob("*.json"))) == 1
    assert len(list((tmp_path / "first" / "failures").glob("*.json"))) == 1


def test_compatible_reused_deferred_pair_gets_current_second_pass(tmp_path: Path) -> None:
    candidate = _candidate()
    signals = {"left": _signal("left", "Alias"), "right": _signal("right", "Alias")}
    reused = PairDisposition(
        pair_id=candidate.pair_id,
        left_signal_key=candidate.left_signal_key,
        right_signal_key=candidate.right_signal_key,
        disposition="deferred_nonblocking",
        source="legacy_cache_compatible",
        pass_name="reused",
        confidence=0.5,
        reason_code="legacy_uncertain",
    )
    calls: list[str] = []

    def second_only(pass_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        calls.append(pass_name)
        return {
            "pair_id": candidate.pair_id,
            "pass_version": payload["pass_version"],
            "decision": "deferred_nonblocking",
            "confidence": 0.6,
            "reason_code": "insufficient_fixed_evidence",
        }

    dispositions, proof = execute_autonomous_missing_pairs(
        [candidate],
        initial_dispositions={candidate.pair_id: reused},
        signal_by_key=signals,
        cache_root=tmp_path,
        executor=second_only,
    )

    assert calls == ["second"]
    assert proof["reused_deferred_selected_for_second_pass"] == 1
    assert dispositions[candidate.pair_id].pass_name == "second"
    assert dispositions[candidate.pair_id].disposition == "deferred_nonblocking"


def test_second_pass_payload_contains_required_fixed_evidence_context() -> None:
    candidate = _candidate()
    signals = {"left": _signal("left", "Alias"), "right": _signal("right", "Alias")}

    payload = build_second_pass_payload(
        candidate,
        signal_by_key=signals,
        all_candidates=[candidate],
        existing_dispositions={},
    )

    assert payload["human_escalation_allowed"] is False
    assert "evidence_independence" in payload
    assert "component_level_constraints" in payload
    assert "script_family" in payload["left"]
    assert "must_link_cannot_link_neighborhood" in payload["right"]


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_dual_search_path_uses_direct_evidence_without_alias_closure(db_session) -> None:
    media_identity = Media(
        filename="identity.jpg",
        path="original/identity.jpg",
        hash="r2r-identity",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=10,
    )
    media_fallback = Media(
        filename="fallback.jpg",
        path="original/fallback.jpg",
        hash="r2r-fallback",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=10,
    )
    db_session.add_all([media_identity, media_fallback])
    db_session.flush()
    concept = SourceConcept(
        concept_key="character:identity",
        primary_display_name="Identity Alias",
        concept_type_hint="character",
        status="active",
        media_count=1,
        source_count=1,
    )
    db_session.add(concept)
    db_session.flush()
    signal_identity = SourceConceptSignal(
        signal_key="identity-signal",
        origin_type="source_name_observation",
        raw_value="Identity Alias",
        display_value="Identity Alias",
        normalized_key="identity_alias",
        canonical_key="identity_alias",
        role_hint="character",
        trust_tier="strong",
        status="materialized_identity",
        media_id=media_identity.id,
        evidence_payload={},
    )
    signal_fallback = SourceConceptSignal(
        signal_key="fallback-signal",
        origin_type="source_name_observation",
        raw_value="Shared Alias",
        display_value="Shared Alias",
        normalized_key="remote_alias",
        canonical_key="remote_alias",
        role_hint="character",
        trust_tier="weak",
        status="isolated_evidence",
        media_id=media_fallback.id,
        work_context_key="different_work",
    )
    db_session.add_all([signal_identity, signal_fallback])
    db_session.flush()
    db_session.add_all(
        [
            SourceConceptAlias(
                concept_id=concept.id,
                alias_value="Shared Alias",
                alias_key="shared_alias",
                display_name="Shared Alias",
                alias_role="source_name_observation",
                status="active",
                source_signal_id=signal_identity.id,
            ),
            SourceConceptSearchIndex(
                concept_id=concept.id,
                search_key="shared_alias",
                display_name="Shared Alias",
                alias_role="source_name_observation",
                status="active",
            ),
            SourceConceptEvidence(
                concept_id=concept.id,
                signal_id=signal_identity.id,
                media_id=media_identity.id,
                evidence_type="source_name_observation",
                evidence_strength="strong",
                status="active",
            ),
            SourceConceptSignalLink(
                signal_id=signal_identity.id,
                concept_id=concept.id,
                link_status="active",
                resolver_version="fixture",
                run_id="fixture",
            ),
        ]
    )
    db_session.commit()

    index_proof = rebuild_source_concept_fallback_search_index(
        db_session,
        signals=[signal_identity, signal_fallback],
        dispositions=[
            PairDisposition(
                pair_id="0" * 64,
                left_signal_key=signal_identity.signal_key,
                right_signal_key=signal_fallback.signal_key,
                disposition="deferred_nonblocking",
                source="fixture",
                pass_name="second",
                confidence=0.5,
                reason_code="insufficient_fixed_evidence",
            )
        ],
        run_id="fixture-index",
    )
    db_session.commit()
    assert index_proof["row_count"] == 2
    assert index_proof["full_signal_python_scan_per_query"] is False

    paths = source_layer_search_path_media_ids(db_session, "Shared Alias")

    assert paths["identity"] == {media_identity.id}
    assert paths["evidence_fallback"] == set()
    assert paths["combined"] == {media_identity.id}

    overlay_paths = source_layer_search_path_media_ids(db_session, "Remote Alias")
    assert overlay_paths["identity"] == set()
    assert overlay_paths["evidence_fallback"] == {media_identity.id, media_fallback.id}


def test_indexed_fallback_loads_only_matching_rows_with_many_irrelevant_signals(db_session) -> None:
    medias = []
    signals = []
    for index in range(101):
        media = Media(
            filename=f"indexed-{index}.jpg",
            path=f"original/indexed-{index}.jpg",
            hash=f"r2r-indexed-{index}",
            file_type=FileTypeEnum.image,
            mime_type="image/jpeg",
            file_size=10,
        )
        signal = SourceConceptSignal(
            signal_key=f"indexed-signal-{index}",
            origin_type="source_name_observation",
            raw_value=f"Alias {index}",
            display_value=f"Alias {index}",
            normalized_key=f"alias_{index}",
            canonical_key=f"alias_{index}",
            role_hint="character",
            trust_tier="weak",
            status="isolated_evidence",
            media_id=None,
            evidence_payload={},
        )
        medias.append(media)
        signals.append(signal)
        db_session.add(media)
        db_session.flush()
        signal.media_id = media.id
        db_session.add(signal)
    db_session.flush()
    target = signals[-1]
    anchor = signals[0]
    db_session.add(
        SourceConceptFallbackSearchIndex(
            alias_key="needle_alias",
            media_id=target.media_id,
            source_signal_id=target.id,
            neighbor_signal_id=anchor.id,
            pair_id="1" * 64,
            relation="deferred_nonblocking",
            overlay_version=R2R_FALLBACK_INDEX_VERSION,
            disposition_version=R2R_FALLBACK_DISPOSITION_VERSION,
            role_hint="character",
            status="active",
            run_id="fixture-index-scaling",
            provenance_payload={"source_layer_only": True},
        )
    )
    db_session.commit()

    rows = _query_overlay_fallback_rows(db_session, {"needle_alias"})

    assert db_session.query(SourceConceptSignal).count() == 101
    assert rows == [(target.media_id,)]


def test_identity_benchmark_excludes_isolated_signal_even_with_active_link(db_session) -> None:
    media = Media(
        filename="isolated-link.jpg",
        path="original/isolated-link.jpg",
        hash="r2r-isolated-link",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=10,
    )
    db_session.add(media)
    db_session.flush()
    concept = SourceConcept(
        concept_key="character:isolated-link",
        primary_display_name="Isolated Alias",
        concept_type_hint="character",
        status="active",
        media_count=0,
        source_count=1,
    )
    db_session.add(concept)
    db_session.flush()
    signal = SourceConceptSignal(
        signal_key="isolated-active-link-signal",
        origin_type="source_name_observation",
        raw_value="Isolated Alias",
        display_value="Isolated Alias",
        normalized_key="isolated_alias",
        canonical_key="isolated_alias",
        role_hint="character",
        trust_tier="weak",
        status="isolated_evidence",
        media_id=media.id,
        evidence_payload={},
    )
    db_session.add(signal)
    db_session.flush()
    db_session.add_all(
        [
            SourceConceptAlias(
                concept_id=concept.id,
                alias_value="Isolated Alias",
                alias_key="isolated_alias",
                display_name="Isolated Alias",
                alias_role="source_name_observation",
                status="active",
                source_signal_id=signal.id,
            ),
            SourceConceptSearchIndex(
                concept_id=concept.id,
                search_key="isolated_alias",
                display_name="Isolated Alias",
                alias_role="source_name_observation",
                status="active",
            ),
            SourceConceptSignalLink(
                signal_id=signal.id,
                concept_id=concept.id,
                link_status="active",
                resolver_version="fixture",
                run_id="fixture-isolated-link",
            ),
        ]
    )
    db_session.commit()

    paths = source_layer_search_path_media_ids(db_session, "Isolated Alias")

    assert paths["identity"] == set()
    assert paths["evidence_fallback"] == {media.id}


def test_cannot_ambiguous_alias_guard_blocks_identity_and_fallback_paths(db_session) -> None:
    left_draft = replace(_signal("guard-left", "Shared Guard"), media_id=None)
    right_draft = replace(_signal("guard-right", "Shared Guard"), media_id=None)
    left = _persist_benchmark_signal(db_session, left_draft, "guard-left")
    right = _persist_benchmark_signal(db_session, right_draft, "guard-right")
    left_draft = replace(left_draft, media_id=left.media_id)
    right_draft = replace(right_draft, media_id=right.media_id)

    proof = rebuild_source_concept_fallback_search_index(
        db_session,
        signals=[left_draft, right_draft],
        dispositions=[],
        cannot_pairs=[(left_draft.signal_key, right_draft.signal_key)],
        run_id="fixture-cannot-alias-guard",
    )
    db_session.commit()

    paths = source_layer_search_path_media_ids(db_session, "Shared Guard")

    assert proof["blocked_cannot_alias_key_count"] == 1
    assert paths["identity"] == set()
    assert paths["evidence_fallback"] == set()
    assert paths["combined"] == set()


def _persist_benchmark_signal(db_session, draft: SourceConceptSignalDraft, suffix: str) -> SourceConceptSignal:
    media = Media(
        filename=f"benchmark-{suffix}.jpg",
        path=f"original/benchmark-{suffix}.jpg",
        hash=f"r2r-benchmark-{suffix}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=10,
    )
    db_session.add(media)
    db_session.flush()
    draft = replace(draft, media_id=media.id)
    row = SourceConceptSignal(
        signal_key=draft.signal_key,
        origin_type=draft.origin_type,
        origin_table=draft.origin_table,
        origin_id=draft.origin_id,
        provider=draft.provider,
        media_id=media.id,
        source_record_id=draft.source_record_id,
        raw_value=draft.raw_value,
        display_value=draft.display_value,
        normalized_key=draft.normalized_key,
        canonical_key=draft.canonical_key,
        role_hint=draft.role_hint,
        work_context_key=draft.work_context_key,
        source_kind=draft.source_kind,
        trust_tier=draft.trust_tier,
        confidence=draft.confidence,
        status="active",
        evidence_payload={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_broad_union_metric_uses_independent_allowed_family_universe(db_session) -> None:
    left = replace(
        _signal("broad-left", "Alias One"),
        canonical_key="family_alias",
        media_id=None,
    )
    right = replace(
        _signal("broad-right", "別名"),
        canonical_key="family_alias",
        media_id=None,
    )
    unrelated = replace(
        _signal("broad-unrelated", "Unrelated Returned"),
        canonical_key="alias_one",
        normalized_key="alias_one",
        media_id=None,
    )
    left_row = _persist_benchmark_signal(db_session, left, "broad-left")
    right_row = _persist_benchmark_signal(db_session, right, "broad-right")
    unrelated_row = _persist_benchmark_signal(db_session, unrelated, "broad-unrelated")
    left = replace(left, media_id=left_row.media_id)
    right = replace(right, media_id=right_row.media_id)
    unrelated = replace(unrelated, media_id=unrelated_row.media_id)
    concept = SourceConcept(
        concept_key="character:broad-family",
        primary_display_name="Alias One",
        concept_type_hint="character",
        status="active",
        media_count=2,
        source_count=2,
    )
    db_session.add(concept)
    db_session.flush()
    for row, value, key in (
        (left_row, "Alias One", "alias_one"),
        (right_row, "別名", "別名"),
    ):
        db_session.add(
            SourceConceptAlias(
                concept_id=concept.id,
                alias_value=value,
                alias_key=key,
                display_name=value,
                alias_role="source_name_observation",
                status="active",
                source_signal_id=row.id,
            )
        )
        db_session.add(
            SourceConceptEvidence(
                concept_id=concept.id,
                signal_id=row.id,
                media_id=row.media_id,
                evidence_type="source_name_observation",
                evidence_strength="strong",
                status="active",
            )
        )
    db_session.commit()

    public, private = r2r_runner.build_automated_search_benchmark(
        db_session,
        [left, right, unrelated],
        dispositions=[],
        legacy_analysis_rows=[],
        legacy_seed_groups_override={},
        apply_cannot_alias_guards=False,
    )

    assert public["seeds_with_false_broad_union"] > 0
    assert public["false_broad_union_indicator_count"] > 0
    assert public["unexpected_media_count"] > 0
    assert private["false_broad_union_samples"]
    assert private["false_broad_union_samples"][0]["unexpected_media_ids_redacted"] is True
    summary = _summary()
    summary["search_benchmark"]["false_broad_union_indicator_count"] = public[
        "false_broad_union_indicator_count"
    ]
    contract = check_phase_contract(CONTRACT_ID, summary)
    assert not contract.passed
    assert "r2r_search_target_failed" in {finding.code for finding in contract.errors}


def test_new_current_cannot_disposition_contaminates_fallback_benchmark(db_session) -> None:
    left = replace(_signal("cannot-left", "Shared Name"), media_id=None)
    right = replace(_signal("cannot-right", "Shared Name"), media_id=None)
    left_row = _persist_benchmark_signal(db_session, left, "cannot-left")
    right_row = _persist_benchmark_signal(db_session, right, "cannot-right")
    left = replace(left, media_id=left_row.media_id)
    right = replace(right, media_id=right_row.media_id)
    disposition = PairDisposition(
        pair_id="2" * 64,
        left_signal_key=left.signal_key,
        right_signal_key=right.signal_key,
        disposition="cannot_link",
        source="current_second_pass",
        pass_name="second",
        confidence=0.99,
        reason_code="context_conflict",
    )
    db_session.commit()

    public, _private = r2r_runner.build_automated_search_benchmark(
        db_session,
        [left, right],
        dispositions=[disposition],
        legacy_analysis_rows=[],
        legacy_seed_groups_override={},
        apply_cannot_alias_guards=False,
    )

    assert public["complete_current_cannot_pair_count"] == 1
    assert public["identity_path_cannot_contamination_count"] == 0
    assert public["evidence_fallback_cannot_contamination_count"] > 0
    assert public["cannot_linked_search_contamination_count"] > 0

    guarded, _guarded_private = r2r_runner.build_automated_search_benchmark(
        db_session,
        [left, right],
        dispositions=[disposition],
        legacy_analysis_rows=[],
        legacy_seed_groups_override={},
    )
    assert guarded["blocked_cannot_ambiguous_alias_key_count"] == 1
    assert guarded["cannot_linked_search_contamination_count"] == 0
