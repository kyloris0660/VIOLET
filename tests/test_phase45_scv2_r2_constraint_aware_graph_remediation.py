"""Focused SCV2-R2 constraint-aware SourceConcept graph tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
    payload: dict | None = None,
) -> SourceConceptSignalDraft:
    canonical = value.casefold().replace(" ", "_")
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
        normalized_key=canonical,
        canonical_key=canonical,
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
        },
        "llm_judgment_accounting": {
            "existing_r1r_judgment_count": 6429,
            "exact_compatible_reuse_count": 6000,
            "stable_pair_identity_reuse_count": 429,
            "semantic_prior_count": 0,
            "invalidated_count": 0,
            "genuinely_new_or_missing_pair_count": 12,
            "new_provider_call_count": 0,
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
        },
        "baseline_metrics": {"concept_total": 2767},
        "post_r2_metrics": {"concept_total": 2800},
        "quality_evaluation": {
            "route_metrics_recomputed": True,
            "meaningful_structural_improvement": True,
            "known_same_recall_protected": True,
            "no_major_quality_regression": True,
            "known_same_regression_count": 0,
            "same_pair_reason_ledger_count": 0,
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
            "source_concept_truth_promotion_authorized": False,
        },
    }


def test_r2_contract_accepts_complete_constraint_aware_proof() -> None:
    result = check_phase_contract(
        "r2_source_concept_graph_remediation_contract_v1",
        _passing_contract_summary(),
    )

    assert result.passed is True


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
    assert all(value == 0 for value in runner.operation_counts().values())
