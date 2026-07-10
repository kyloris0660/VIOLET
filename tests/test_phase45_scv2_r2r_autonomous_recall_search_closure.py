"""Focused SCV2-R2R autonomous closure contract tests."""

from __future__ import annotations

from copy import deepcopy
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
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
)
from app.services.source_concept_autonomous_closure_service import (
    CandidatePair,
    PairDisposition,
    build_candidate_pair_manifest,
    build_second_pass_payload,
    disposition_accounting,
    execute_autonomous_missing_pairs,
    project_autonomous_materialization,
)
from app.services.source_concept_resolver_service import (
    SourceConceptEdgeDraft,
    SourceConceptSignalDraft,
    resolve_source_concepts,
)
from app.services.source_concept_search_service import source_layer_search_path_media_ids
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
        "candidate_population": {"total_candidate_pairs": total},
        "cache_reuse": {
            "exact_compatible_cache_hit_count": 0,
            "stable_compatible_reuse_count": 5,
            "semantic_prior_count": 7,
            "genuinely_missing_pair_count": 7,
        },
        "llm_execution": {
            "all_approved_missing_pairs_accounted": target,
            "remaining_unaccounted_missing_pairs": 0 if target else 7,
            "provider_failure_count": 0,
            "failed_judgments_counted_as_success": False,
            "primary_provider_only": True,
            "fallback_provider_used": False,
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
            "giant_component_recurrence": False,
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


def test_initial_r2r_runner_has_no_provider_execution_surface() -> None:
    source = Path(r2r_runner.__file__).read_text(encoding="utf-8")

    assert "primary_openai_provider_from_settings" not in source
    assert 'choices=("prepare", "dry-run")' in source
    assert "import gallery_dl" not in source
    assert "import requests" not in source


def test_public_retention_projection_field_does_not_trigger_path_redaction() -> None:
    payload = {
        "evidence_retention_projection": (
            "SourceConceptSignal projection plus private versioned pair overlay"
        )
    }

    findings = r2r_runner.scv1.scan_public_text(json.dumps(payload, sort_keys=True))

    assert not [finding for finding in findings if finding["type"] == "canonical_path_like"]


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
            return {"decision": "deferred_nonblocking", "confidence": 0.5}
        assert payload["human_escalation_allowed"] is False
        return {"decision": "cannot_link", "confidence": 0.91}

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
        return {"decision": "ask_a_human", "confidence": 0.5}

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
        canonical_key="shared_alias",
        role_hint="character",
        trust_tier="strong",
        status="materialized_identity",
        media_id=media_identity.id,
        evidence_payload={
            "r2r_search_overlay": {
                "identity_union_allowed": False,
                "neighbors": [
                    {
                        "relation": "deferred_nonblocking",
                        "fallback_alias_keys": ["remote_alias"],
                    }
                ],
            }
        },
    )
    signal_fallback = SourceConceptSignal(
        signal_key="fallback-signal",
        origin_type="source_name_observation",
        raw_value="Shared Alias",
        display_value="Shared Alias",
        normalized_key="shared_alias",
        canonical_key="shared_alias",
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

    paths = source_layer_search_path_media_ids(db_session, "Shared Alias")

    assert paths["identity"] == {media_identity.id}
    assert paths["evidence_fallback"] == {media_identity.id, media_fallback.id}
    assert paths["combined"] == {media_identity.id, media_fallback.id}

    overlay_paths = source_layer_search_path_media_ids(db_session, "Remote Alias")
    assert overlay_paths["identity"] == set()
    assert overlay_paths["evidence_fallback"] == {media_identity.id}
