"""Focused SCV2-R2 constraint-aware SourceConcept graph tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.source_concept_resolver_service import (  # noqa: E402
    SourceConceptSignalDraft,
    build_data_aware_ambiguity_profiles,
    resolve_source_concepts,
)
from scripts.phase_contracts.contract_checks import check_phase_contract  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as runner  # noqa: E402


def _signal(
    key: str,
    value: str,
    *,
    role: str = "character",
    trust: str = "strong",
    status: str = "active",
    provider: str = "fixture",
    media_id: int | None = None,
    record_id: int | None = None,
    work_context: str | None = None,
    canonical: str | None = None,
    payload: dict | None = None,
) -> SourceConceptSignalDraft:
    canonical_key = canonical or value.casefold().replace(" ", "_")
    return SourceConceptSignalDraft(
        signal_key=key,
        origin_type="normal_media_tag",
        origin_table="fixture",
        origin_id=key,
        provider=provider,
        media_id=media_id,
        source_metadata_record_id=record_id,
        source_record_id=str(record_id) if record_id is not None else None,
        raw_value=value,
        display_value=value,
        normalized_key=canonical_key,
        canonical_key=canonical_key,
        role_hint=role,
        work_context_key=work_context,
        parenthetical_base=None,
        parenthetical_context=None,
        source_kind="fixture",
        trust_tier=trust,
        confidence=0.9,
        status=status,
        evidence_payload=payload or {},
    )


def test_review_only_edges_never_materialize_identity_components() -> None:
    left = _signal("left", "alexandria", work_context="work_a")
    right = _signal("right", "alexandria")

    result = resolve_source_concepts([left, right], run_id="r2-test")

    assert len(result.concepts) == 2
    assert any(edge.status == "needs_review" for edge in result.edge_candidates)
    assert result.summary["review_only_edge_used_in_union_count"] == 0


def test_llm_cannot_is_enforced_across_transitive_component_merge() -> None:
    signals = [
        _signal("a", "long_identity_name", work_context="work"),
        _signal("b", "long_identity_name", work_context="work"),
        _signal("c", "long_identity_name", work_context="work"),
    ]
    judgments = [
        {
            "left_signal_key": "a",
            "right_signal_key": "c",
            "decision": "cannot_link",
            "confidence": 0.91,
            "judgment_id": "cannot-a-c",
        }
    ]

    result = resolve_source_concepts(signals, run_id="r2-test", llm_judgments=judgments)

    assert len(result.concepts) == 2
    assert result.summary["direct_llm_cannot_pair_in_materialized_component_count"] == 0
    assert result.summary["transitive_cannot_violation_count"] == 0


def test_unknown_role_defaults_to_review_overlay() -> None:
    signals = [
        _signal("known", "identity_name", role="character", provider="source_a", record_id=1),
        _signal("unknown", "identity_name", role="unknown", provider="source_a", record_id=1),
    ]

    result = resolve_source_concepts(signals, run_id="r2-test")

    assert len(result.concepts) == 2
    edges = [edge for edge in result.edge_candidates if edge.edge_type == "unknown_role_review"]
    assert edges and all(not edge.union_allowed for edge in edges)
    assert result.summary["unknown_role_bridge_candidate_count_before"] >= 1
    assert result.summary["unknown_role_bridge_materialized_count_after"] == 0


def test_llm_unknown_role_cross_script_alone_stays_review_only_and_is_diagnosed() -> None:
    signals = [
        _signal(
            "unknown",
            "神里綾華",
            role="unknown",
            provider="source_a",
            record_id=1,
            canonical="kamisato_ayaka",
        ),
        _signal(
            "known",
            "Kamisato Ayaka",
            role="character",
            provider="source_a",
            record_id=1,
            canonical="kamisato_ayaka",
        ),
    ]
    judgments = [
        {
            "left_signal_key": "unknown",
            "right_signal_key": "known",
            "decision": "must_link",
            "confidence": 0.99,
            "judgment_id": "cached-same",
        }
    ]

    result = resolve_source_concepts(signals, run_id="r2-test", llm_judgments=judgments)

    llm_edge = next(edge for edge in result.edge_candidates if edge.edge_type == "llm_same_concept")
    assert llm_edge.status == "needs_review"
    assert llm_edge.union_allowed is False
    assert llm_edge.payload["review_reason"] == "unknown_role_llm_same_requires_independent_corroboration"
    assert llm_edge.payload["cross_script_observed"] is True
    assert llm_edge.payload["independent_non_ai_sources"] is False
    assert len(result.concepts) == 2
    assert result.summary["llm_unknown_role_must_link_candidate_count"] == 1
    assert result.summary["unknown_role_corroboration_distribution"]["cross_script_observed_count"] >= 1
    assert result.summary["unauthorized_unknown_role_materialization_count"] == 0


def test_llm_unknown_role_may_materialize_with_independent_evidence_and_explicit_context() -> None:
    signals = [
        _signal(
            "unknown",
            "identity_name",
            role="unknown",
            provider="source_a",
            record_id=1,
            work_context="work_a",
        ),
        _signal(
            "known",
            "identity_name",
            role="character",
            provider="source_b",
            record_id=2,
            work_context="work_a",
        ),
    ]
    judgments = [
        {
            "left_signal_key": "unknown",
            "right_signal_key": "known",
            "decision": "must_link",
            "confidence": 0.99,
            "judgment_id": "cached-same",
        }
    ]

    result = resolve_source_concepts(signals, run_id="r2-test", llm_judgments=judgments)

    llm_edge = next(edge for edge in result.edge_candidates if edge.edge_type == "llm_same_concept")
    assert llm_edge.status == "active"
    assert llm_edge.union_allowed is True
    assert llm_edge.payload["independent_non_ai_sources"] is True
    assert llm_edge.payload["explicit_compatible_context"] is True
    assert llm_edge.payload["unknown_role_materialization_authorized"] is True
    assert len(result.concepts) == 1
    assert result.summary["unauthorized_unknown_role_materialization_count"] == 0


def _same_quality_for(
    signals: list[SourceConceptSignalDraft],
    judgment: dict,
) -> tuple[dict, list[dict]]:
    result = resolve_source_concepts(signals, run_id="r2-test", llm_judgments=[judgment])
    return runner.same_and_cannot_quality(
        result,
        [judgment],
        [{"reuse_level": "stable_pair_identity", "decision": "must_link"}],
    )


def test_judgment_derived_same_benchmark_keeps_downgraded_must_link() -> None:
    signals = [
        _signal("left", "different_identity_left", trust="medium_ai"),
        _signal("right", "different_identity_right", trust="medium_ai"),
    ]
    judgment = {
        "left_signal_key": "left",
        "right_signal_key": "right",
        "decision": "must_link",
        "confidence": 0.5,
        "judgment_id": "cached-same",
    }

    quality, _ledger = _same_quality_for(signals, judgment)

    assert quality["same_benchmark_source"] == "compatible_reused_r1r_judgments"
    assert quality["compatible_must_link_benchmark_count"] == 1
    assert quality["retained_same_component_count"] == 0
    assert quality["unexplained_same_regression_count"] == 1


def test_judgment_derived_same_benchmark_classifies_valid_hard_constraint_split() -> None:
    signals = [
        _signal("left", "identity_name", role="character"),
        _signal("right", "identity_name", role="work"),
    ]
    judgment = {
        "left_signal_key": "left",
        "right_signal_key": "right",
        "decision": "must_link",
        "confidence": 0.99,
        "judgment_id": "cached-same",
    }

    quality, ledger = _same_quality_for(signals, judgment)

    assert quality["intentionally_split_with_valid_constraint_count"] == 1
    assert quality["unexplained_same_regression_count"] == 0
    assert ledger[0]["blocker_classes"] == ["role_conflict"]


def test_judgment_derived_same_benchmark_flags_split_without_blocker() -> None:
    signals = [
        _signal("left", "different_identity_left", trust="medium_ai"),
        _signal("right", "different_identity_right", trust="medium_ai"),
    ]
    judgment = {
        "left_signal_key": "left",
        "right_signal_key": "right",
        "decision": "must_link",
        "confidence": 0.99,
        "judgment_id": "cached-same",
    }

    quality, ledger = _same_quality_for(signals, judgment)

    assert quality["compatible_must_link_benchmark_count"] == 1
    assert quality["unexplained_same_regression_count"] == 1
    assert quality["compatible_same_accounting_complete"] is True
    assert ledger[0]["classification"] == "unexplained_same_regression"


def test_data_aware_ambiguity_distinguishes_common_long_and_contextual_short_names() -> None:
    common = [
        _signal(f"common:{index}", "alexandria", media_id=index, work_context=f"work_{index % 3}")
        for index in range(9)
    ]
    contextual_short = [
        _signal(f"short:{index}", "mona", media_id=100 + index, work_context="genshin_impact")
        for index in range(3)
    ]

    profiles = build_data_aware_ambiguity_profiles([*common, *contextual_short])

    assert profiles["alexandria"]["ambiguous"] is True
    assert profiles["alexandria"]["distinct_work_contexts"] == 3
    assert profiles["mona"]["ambiguous"] is False
    assert profiles["mona"]["short_length_prior"] is True


def test_context_equivalence_requires_independent_evidence_units() -> None:
    signals = [
        _signal("work:1", "原神", role="work", media_id=1, record_id=10),
        _signal("tag:1", "barbara_(genshin_impact)", trust="weak", status="needs_review", media_id=1, record_id=10),
        _signal("work:2", "原神", role="work", media_id=2, record_id=11),
        _signal("tag:2", "ganyu_(genshin_impact)", trust="weak", status="needs_review", media_id=2, record_id=11),
    ]

    result = resolve_source_concepts(signals, run_id="r2-test")

    diagnostics = result.summary["context_equivalence"]
    assert diagnostics["accepted_pair_count"] == 1
    assert diagnostics["accepted_support_reasons"]["independent_evidence_units"] == 1
    assert result.summary["context_alias_count"] == 2


def test_oversized_identity_block_is_partitioned_and_retains_active_anchor() -> None:
    signals = [
        _signal(
            f"signal:{index}",
            "long_identity_name",
            media_id=index,
            work_context="work",
        )
        for index in range(65)
    ]

    result = resolve_source_concepts(signals, run_id="r2-test")

    assert len(result.concepts) == 1
    graph = result.summary["edge_graph"]
    assert graph["oversized_block_count"] >= 1
    assert graph["oversized_partition_count"] >= 1
    assert graph["oversized_hub_edges_prevented"] >= 1


def _passing_contract_summary() -> dict:
    allowed = [
        "blombooru_source_concept_resolution_runs",
        "blombooru_source_concept_signals",
        "blombooru_source_concepts",
        "blombooru_source_concept_aliases",
        "blombooru_source_concept_evidence",
        "blombooru_source_concept_signal_links",
        "blombooru_source_concept_search_index",
    ]
    return {
        "pipeline_contract": {
            "contract_id": "r2_source_concept_graph_remediation_contract_v1",
            "status": "target_met_constraint_aware_r2",
            "claims": {"target_met": True, "safe_to_merge": False, "route_approved": False},
        },
        "environment_isolation": {
            "passed": True,
            "working_db_is_separate_from_r1r_baseline": True,
            "r1r_baseline_preserved": True,
            "dev_test_only": True,
            "production_profile_active": False,
            "production_write_attempted": False,
            "protected_source_write_attempted": False,
        },
        "fixed_input_manifest": {
            "present": True,
            "private_manifest_generated": True,
            "baseline_to_working_clone_match": True,
            "before_after_match": True,
            "row_counts_match": True,
            "content_fingerprints_match": True,
            "provenance_unchanged": True,
            "table_count": 15,
            "content_fingerprint_count": 15,
            "changed_tables": [],
        },
        "operation_counts": {
            "gallery_dl_calls": 0,
            "provider_pixiv_network_calls": 0,
            "ai_tagging_calls": 0,
            "media_imports": 0,
            "upstream_observation_mutations": 0,
            "new_llm_provider_calls": 0,
            "production_writes": 0,
            "truth_path_writes": 0,
        },
        "source_concept_write_scope": {
            "allowed_tables": allowed,
            "rebuilt_tables": allowed,
            "changed_tables": allowed,
            "forbidden_changed_tables": [],
            "unexpected_changed_tables": [],
            "persistence_forbidden_truth_table_write_count": 0,
            "truncate_drop_reset_used": False,
        },
        "llm_judgment_accounting": {
            "existing_r1r_judgment_count": 6429,
            "exact_compatible_reuse_count": 6000,
            "stable_pair_identity_reuse_count": 429,
            "semantic_prior_count": 0,
            "invalidated_count": 0,
            "genuinely_new_or_missing_pair_count": 12,
            "new_provider_call_count": 0,
            "same_decision_counts": {
                "all_existing_r1r": 3,
                "compatible_proof_grade": 2,
                "semantic_prior": 1,
                "invalidated": 0,
            },
        },
        "new_pair_adjudication": {
            "status": "blocked_llm_approval_required",
            "pair_count": 12,
            "projected_cost_usd": 0.01,
            "provider_calls_made": 0,
            "provider_initialized": False,
            "execution_scope_excludes_unadjudicated_review_pairs": True,
            "separate_operator_approval_required": True,
        },
        "graph_invariants": {
            "review_only_edge_used_in_union_count": 0,
            "direct_llm_cannot_pair_in_materialized_component_count": 0,
            "deterministic_hard_conflict_in_materialized_component_count": 0,
            "transitive_cannot_violation_count": 0,
            "unauthorized_unknown_role_materialization_count": 0,
        },
        "baseline_metrics": {
            "concept_total": 2767,
            "gap_total": 4443,
            "search_aggregate": {
                "symmetric_groups": 0,
                "unmatched_seeds": 16,
                "media_result_overlap_metrics": {"average_pairwise_jaccard": 0.3752},
            },
        },
        "post_r2_metrics": {
            "concept_total": 2800,
            "gap_total": 9344,
            "search_aggregate": {
                "symmetric_groups": 0,
                "unmatched_seeds": 16,
                "media_result_overlap_metrics": {"average_pairwise_jaccard": 0.1539},
            },
        },
        "quality_evaluation": {
            "route_metrics_recomputed": True,
            "meaningful_structural_improvement": True,
            "known_same_recall_protected": True,
            "compatible_same_accounting_complete": True,
            "constraint_safety_target_met": True,
            "fixed_evidence_preserved": True,
            "known_same_constraint_regression": False,
            "known_cannot_constraint_regression": False,
            "giant_component_remediation_improved": True,
            "search_quality_improved": False,
            "gap_quality_improved": False,
            "recall_closure_complete": False,
            "route_quality_ready_for_scale": False,
            "r2r_followup_required": True,
            "no_major_quality_regression": False,
            "quality_interpretation": (
                "R2 met the constraint-aware graph-remediation target but intentionally produced a more "
                "conservative and fragmented graph. Search, gap, and recall closure remain incomplete."
            ),
            "known_same_regression_count": 0,
            "same_pair_reason_ledger_count": 0,
            "same_benchmark_source": "compatible_reused_r1r_judgments",
            "same_benchmark_constructed_from_current_output": False,
            "same_benchmark_compatibility_policy": "exact_or_stable_pair_identity_only;semantic_prior_excluded",
            "all_existing_r1r_same_decision_count": 3,
            "compatible_must_link_benchmark_count": 2,
            "semantic_prior_same_decision_count": 1,
            "invalidated_same_decision_count": 0,
            "retained_same_component_count": 2,
            "intentionally_split_with_valid_constraint_count": 0,
            "unexplained_same_regression_count": 0,
            "missing_signal_or_pair_count": 0,
            "same_benchmark_accounting_total_count": 2,
            "intentionally_split_reason_ledger_count": 0,
            "split_same_reason_ledger_count": 0,
        },
        "evidence_version_boundary": {
            "resolver_evidence_code_sha": "a" * 40,
            "report_commit_parent_sha": "a" * 40,
            "post_evidence_resolver_code_changed": False,
            "report_only_commit": True,
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
            "source_concept_truth_promotion_authorized": False,
        },
    }


def test_r2_contract_accepts_complete_constraint_aware_proof() -> None:
    result = check_phase_contract(
        "r2_source_concept_graph_remediation_contract_v1",
        _passing_contract_summary(),
    )

    assert result.passed is True


@pytest.mark.parametrize(
    "field",
    [
        "passed",
        "working_db_is_separate_from_r1r_baseline",
        "r1r_baseline_preserved",
        "dev_test_only",
        "production_profile_active",
        "production_write_attempted",
        "protected_source_write_attempted",
    ],
)
def test_r2_contract_requires_every_explicit_isolation_field(field: str) -> None:
    summary = _passing_contract_summary()
    del summary["environment_isolation"][field]

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_environment_isolation_proof_missing_or_invalid" in {error.code for error in result.errors}


@pytest.mark.parametrize(
    "field",
    [
        "passed",
        "working_db_is_separate_from_r1r_baseline",
        "r1r_baseline_preserved",
        "dev_test_only",
        "production_profile_active",
        "production_write_attempted",
        "protected_source_write_attempted",
    ],
)
def test_r2_contract_rejects_null_isolation_field(field: str) -> None:
    summary = _passing_contract_summary()
    summary["environment_isolation"][field] = None

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_environment_isolation_proof_missing_or_invalid" in {error.code for error in result.errors}


@pytest.mark.parametrize("value", [1, None, "0", 0.0, False, "malformed"])
def test_r2_contract_rejects_missing_positive_or_malformed_truth_write_count(value: object) -> None:
    summary = _passing_contract_summary()
    if value is None:
        del summary["source_concept_write_scope"]["persistence_forbidden_truth_table_write_count"]
    else:
        summary["source_concept_write_scope"]["persistence_forbidden_truth_table_write_count"] = value

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_persistence_forbidden_truth_write_count_invalid" in {error.code for error in result.errors}


@pytest.mark.parametrize(
    "field",
    [
        "px1_b_authorized",
        "provider_2_authorized",
        "scale_up_authorized",
        "entity_bridge_authorized",
        "production_authorized",
        "full_library_execution_authorized",
        "source_concept_truth_promotion_authorized",
    ],
)
def test_r2_contract_requires_every_downstream_authorization_flag(field: str) -> None:
    summary = _passing_contract_summary()
    del summary["route_authorization"][field]

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_route_authorization_flag_missing_or_invalid" in {error.code for error in result.errors}


@pytest.mark.parametrize("value", [None, "false", 0, 1, True])
def test_r2_contract_rejects_non_boolean_or_true_downstream_authorization(value: object) -> None:
    summary = _passing_contract_summary()
    summary["route_authorization"]["entity_bridge_authorized"] = value

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_route_authorization_flag_missing_or_invalid" in {error.code for error in result.errors}


@pytest.mark.parametrize("field", ["route_approved", "safe_to_merge"])
def test_r2_contract_requires_explicit_false_pipeline_non_authorization_claim(field: str) -> None:
    summary = _passing_contract_summary()
    del summary["pipeline_contract"]["claims"][field]

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_pipeline_non_authorization_claim_missing_or_invalid" in {error.code for error in result.errors}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("search_quality_improved", True),
        ("gap_quality_improved", True),
        ("recall_closure_complete", True),
        ("route_quality_ready_for_scale", True),
        ("r2r_followup_required", False),
        ("no_major_quality_regression", True),
    ],
)
def test_r2_contract_requires_truthful_degraded_route_quality_dimensions(field: str, value: bool) -> None:
    summary = _passing_contract_summary()
    summary["quality_evaluation"][field] = value

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_quality_dimension_missing_or_invalid" in {error.code for error in result.errors}


def test_r2_narrow_target_does_not_require_broad_route_quality_improvement() -> None:
    summary = _passing_contract_summary()

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is True
    assert summary["quality_evaluation"]["constraint_safety_target_met"] is True
    assert summary["quality_evaluation"]["search_quality_improved"] is False
    assert summary["quality_evaluation"]["gap_quality_improved"] is False
    assert summary["quality_evaluation"]["recall_closure_complete"] is False
    assert runner.determine_status(
        {"before_after_match": True},
        summary["llm_judgment_accounting"],
        summary["graph_invariants"],
        summary["quality_evaluation"],
    ) == "target_met_constraint_aware_r2"


def test_r2_contract_enforces_judgment_derived_same_accounting_equality() -> None:
    summary = _passing_contract_summary()
    summary["quality_evaluation"]["same_benchmark_accounting_total_count"] = 1

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_same_benchmark_accounting_mismatch" in {error.code for error in result.errors}


def test_r2_contract_rejects_unexplained_same_regression() -> None:
    summary = _passing_contract_summary()
    quality = summary["quality_evaluation"]
    quality["retained_same_component_count"] = 1
    quality["unexplained_same_regression_count"] = 1
    quality["split_same_reason_ledger_count"] = 1
    quality["same_benchmark_accounting_total_count"] = 2
    quality["known_same_constraint_regression"] = True
    quality["known_same_recall_protected"] = False

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_unexplained_same_regression" in {error.code for error in result.errors}


def test_r2_contract_rejects_missing_intentional_split_reason_ledger() -> None:
    summary = _passing_contract_summary()
    quality = summary["quality_evaluation"]
    quality["retained_same_component_count"] = 1
    quality["intentionally_split_with_valid_constraint_count"] = 1
    quality["intentionally_split_reason_ledger_count"] = 0
    quality["split_same_reason_ledger_count"] = 0

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_same_split_reason_ledger_incomplete" in {error.code for error in result.errors}


def test_r2_contract_rejects_output_derived_same_benchmark() -> None:
    summary = _passing_contract_summary()
    summary["quality_evaluation"]["same_benchmark_constructed_from_current_output"] = True

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_same_benchmark_source_invalid" in {error.code for error in result.errors}


def test_r2_contract_rejects_unauthorized_unknown_role_materialization() -> None:
    summary = _passing_contract_summary()
    summary["graph_invariants"]["unauthorized_unknown_role_materialization_count"] = 1

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    assert "r2_graph_invariant_failed" in {error.code for error in result.errors}


def test_r2_contract_requires_non_ambiguous_evidence_version_boundary() -> None:
    summary = _passing_contract_summary()
    summary["head_sha"] = "b" * 40
    summary["evidence_version_boundary"]["report_commit_parent_sha"] = "c" * 40

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    codes = {error.code for error in result.errors}
    assert result.passed is False
    assert "r2_evidence_version_sha_invalid" in codes
    assert "r2_ambiguous_top_level_head_sha_present" in codes


def test_r2_contract_fails_closed_on_review_union_cannot_or_upstream_change() -> None:
    summary = _passing_contract_summary()
    summary["graph_invariants"]["review_only_edge_used_in_union_count"] = 1
    summary["graph_invariants"]["transitive_cannot_violation_count"] = 1
    summary["fixed_input_manifest"]["before_after_match"] = False
    summary["fixed_input_manifest"]["changed_tables"] = ["blombooru_source_tag_observations"]

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    error_codes = {error.code for error in result.errors}
    assert "r2_fixed_input_gate_failed" in error_codes
    assert "r2_upstream_evidence_changed" in error_codes
    assert "r2_graph_invariant_failed" in error_codes


def test_r2_contract_fails_closed_if_new_pair_provider_boundary_is_not_explicit() -> None:
    summary = _passing_contract_summary()
    summary["new_pair_adjudication"]["status"] = "executed"
    summary["new_pair_adjudication"]["provider_initialized"] = True

    result = check_phase_contract("r2_source_concept_graph_remediation_contract_v1", summary)

    assert result.passed is False
    error_codes = {error.code for error in result.errors}
    assert "r2_new_pair_adjudication_status_invalid" in error_codes
    assert "r2_new_pair_provider_boundary_violated" in error_codes


def test_fixed_input_fingerprint_comparison_detects_same_count_content_change() -> None:
    before = {
        "tables": {
            "blombooru_source_tag_observations": {
                "count": 2,
                "row_content_sha256": "a",
                "columns": ["id", "status"],
            }
        }
    }
    after = {
        "tables": {
            "blombooru_source_tag_observations": {
                "count": 2,
                "row_content_sha256": "b",
                "columns": ["id", "status"],
            }
        }
    }

    comparison = runner.compare_fingerprints(before, after)

    assert comparison["passed"] is False
    assert comparison["row_counts_match"] is True
    assert comparison["content_fingerprints_match"] is False
    assert comparison["changed_tables"] == ["blombooru_source_tag_observations"]


def test_stable_pair_identity_cache_reuse_needs_no_provider_call(tmp_path: Path) -> None:
    left = _signal("left", "alexandria", work_context="work_a")
    right = _signal("right", "alexandria")
    deterministic = resolve_source_concepts([left, right], run_id="r2-test")
    records = tmp_path / "records"
    records.mkdir()
    record = {
        "cache_key": "fixture-cache-key",
        "compatible_for_exact_reuse": True,
        "error_state": None,
        "resolver_version": "source_concept_resolver_core_v2_graph",
        "decision": "cannot",
        "resolver_decision": "cannot_link",
        "confidence": 0.9,
        "left_signal_key": "left",
        "right_signal_key": "right",
        "input_signal_summary": {
            "left": runner._signal_identity_payload(left),
            "right": runner._signal_identity_payload(right),
        },
    }
    (records / "fixture.json").write_text(json.dumps(record), encoding="utf-8")

    judgments, accounting, candidate_comparison, _rows = runner.load_cached_judgments(
        tmp_path,
        [left, right],
        deterministic,
    )

    assert len(judgments) == 1
    assert accounting["stable_pair_identity_reuse_count"] == 1
    assert accounting["new_provider_call_count"] == 0
    assert accounting["genuinely_new_or_missing_pair_count"] == 0
    assert candidate_comparison["current_pairs_without_compatible_legacy_judgment"] == 0


def test_runner_has_no_acquisition_provider_or_truth_path_imports() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "import gallery_dl" not in source
    assert "import requests" not in source
    assert "primary_openai_provider_from_settings" not in source
    assert "MediaEntityAssignment" not in source
    assert "TagTranslation" not in source
    assert set(runner.SOURCE_CONCEPT_TABLES) == {
        "blombooru_source_concept_resolution_runs",
        "blombooru_source_concept_signals",
        "blombooru_source_concepts",
        "blombooru_source_concept_aliases",
        "blombooru_source_concept_evidence",
        "blombooru_source_concept_signal_links",
        "blombooru_source_concept_search_index",
    }


def test_public_isolation_field_names_do_not_trigger_canonical_path_redaction() -> None:
    isolation = runner.environment_isolation(
        runner.R1R_BASELINE_DB,
        "blombooru_scv2_r2_test_fixture",
    )

    findings = runner.scv1.scan_public_text(json.dumps(isolation, sort_keys=True))

    assert not [finding for finding in findings if finding["type"] == "canonical_path_like"]


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        "foo/../../docs/pwn",
        "..\\..\\docs\\pwn",
        r"C:\\temp\\pwn",
        "/tmp/pwn",
        "control\ncharacter",
        "a" * 129,
        "safe..but-forbidden",
    ],
)
def test_run_id_rejects_traversal_absolute_control_empty_and_overlong_values(run_id: str) -> None:
    with pytest.raises(runner.R2BlockedError, match="blocked_invalid_run_id"):
        runner.validate_run_id(run_id)


def test_run_id_accepts_bounded_normal_value_and_artifact_stays_contained(tmp_path: Path) -> None:
    run_id = runner.validate_run_id("r2-closeout_20260710.final-1")

    artifact = runner.artifact_path(tmp_path, f"execute-result-{run_id}.json")

    assert run_id == "r2-closeout_20260710.final-1"
    assert artifact.is_relative_to(tmp_path.resolve())
    assert len(runner.derived_run_id("a" * 128, "public-write")) <= 128


def test_main_rejects_traversal_run_id_before_any_runner_or_db_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    output_dir = tmp_path / ".local_manifests" / "r2-test"

    exit_code = runner.main(
        [
            "--mode",
            "dry-run",
            "--output-dir",
            str(output_dir),
            "--run-id",
            "foo/../../docs/pwn",
        ]
    )

    assert exit_code == 2
    assert list(output_dir.glob("blocked-dry-run-invalid-run-id.json"))
    assert not (tmp_path / "docs" / "pwn").exists()


def test_redaction_failure_blocks_public_report_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "private-output"
    output_dir.mkdir()
    public_md = tmp_path / "public-report.md"
    public_json = tmp_path / "public-summary.json"
    public_md.write_text("original markdown\n", encoding="utf-8")
    public_json.write_text('{"original": true}\n', encoding="utf-8")
    monkeypatch.setattr(runner, "PUBLIC_REPORT_MD", public_md)
    monkeypatch.setattr(runner, "PUBLIC_REPORT_JSON", public_json)
    summary = _passing_contract_summary()
    summary["public_redaction"] = runner.public_redaction_success_record()
    summary["injected_private_value"] = r"C:\\Users\\private\\secret.json"
    markdown = runner.public_report_markdown(summary)

    with pytest.raises(runner.R2BlockedError, match="blocked_public_redaction"):
        runner.write_public_outputs_after_redaction(markdown, summary, output_dir, "safe-run-id")

    assert summary["public_redaction"]["passed"] is False
    assert public_md.read_text(encoding="utf-8") == "original markdown\n"
    assert public_json.read_text(encoding="utf-8") == '{"original": true}\n'
    diagnostics = list(output_dir.glob("blocked-public-redaction-*.json"))
    assert len(diagnostics) == 1
    assert diagnostics[0].is_relative_to(output_dir.resolve())


def test_snapshot_reuse_policy_preserves_acquisition_rebuild_boundary() -> None:
    policy = (ROOT / "docs" / "source-evidence-snapshot-reuse-policy.md").read_text(encoding="utf-8")

    assert "Provider/source observations are durable facts once acquired" in policy
    assert "resolver rerun starts cache-first and input-snapshot-first" in policy
    assert "not called again merely because" in policy
    assert "SourceConcept-derived concepts" in policy
    assert "stable pair-identity reuse" in policy
    assert "separate evidence acquisition from graph recomputation" in policy
    assert all(value == 0 for value in runner.operation_counts().values())
