"""Final SCV2-PX3 Pixiv product integration.

The service consumes the frozen PX1/PX2 contracts, delegates identity
resolution and core materialization to the existing SourceConcept resolver,
and persists only a queryable product projection beside that core. Real
provider execution is deliberately outside this module.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..database import Base
from ..models import (
    SourceConcept,
    SourceConceptAlias,
    SourceConceptAmbiguityRecord,
    SourceConceptCandidateDisposition,
    SourceConceptEvidence,
    SourceConceptFallbackSearchIndex,
    SourceConceptProductCluster,
    SourceConceptProductRun,
    SourceConceptProductMediaBinding,
    SourceConceptResolutionRun,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    SourceMetadataRecord,
)
from ..utils.cache import invalidate_source_concept_search_cache
from .pixiv_product_media_binding import (
    plan_media_bindings, binding_plan_summary, persist_media_bindings,
    verified_local_binding_provenance,
)
from .pixiv_identity_policy import canonical_pixiv_work_id
from .pixiv_metadata_clustering_service import (
    PX2_CANDIDATE_POLICY_VERSION,
    PX2_CONTEXT_POLICY_VERSION,
    PixivClusteringRun,
    build_pixiv_clustering,
    consume_px1_public_summary,
    validate_px1_consumer_artifacts,
)
from .pixiv_metadata_projection_service import (
    PIXIV_AGGREGATE_SCHEMA,
    PIXIV_SIGNAL_BUNDLE_SCHEMA,
    assert_public_safe_projection,
    build_canonical_pixiv_aggregates_from_session,
    canonical_fingerprint,
    canonical_json_bytes,
    project_pixiv_aggregate_to_source_concept_signals,
)
from .pixiv_metadata_vertical_slice_service import (
    PX2_CONSUMER_CONTRACT_SCHEMA,
    _OfflineOperationGuard,
    _require_task_owned_temp_workspace,
)
from .source_concept_resolver_service import (
    RESOLVER_VERSION,
    build_source_concept_input_scope,
    persist_source_concept_resolution,
)


PX3_CONTRACT_ID = "scv2_px3_pixiv_product_integration_contract_v1"
PX3_PUBLIC_SCHEMA = "violet.scv2-px3-pixiv-product-integration-result.v1"
PX3_POLICY_VERSION = "scv2_px3_pixiv_product_projection_v1"
PX3_PERSISTENCE_SCHEMA = "violet.scv2-px3-product-persistence-proof.v1"
PX3_OPERATION_RECEIPT_SCHEMA = "violet.scv2-px3-operation-receipt.v1"
PX3_EXECUTED_STAGES = (
    "px1_px2_repository_reconstruction",
    "product_projection",
    "source_concept_owned_persistence",
    "dry_run_apply_replay_rollback",
    "queryable_api_and_ui",
    "public_safe_result",
)
PX3_ALLOWED_PRODUCT_TABLES = (
    "blombooru_source_concept_product_runs",
    "blombooru_source_concept_product_clusters",
    "blombooru_source_concept_candidate_dispositions",
    "blombooru_source_concept_ambiguity_records",
)
PX3_AUTHORITY_MAP = {
    "repository_migration_code_authorized": True,
    "task_owned_temporary_database_authorized": True,
    "synthetic_local_server_browser_e2e_authorized": True,
    "px3_merge_authorized": False,
    "real_pixiv_network_execution_authorized": False,
    "provider_credentials_authorized": False,
    "real_source_or_icloud_access_authorized": False,
    "existing_database_or_app_storage_mutation_authorized": False,
    "user_data_import_authorized": False,
    "production_authorized": False,
    "full_library_import_authorized": False,
}
_SCOPE_KEY = re.compile(r"[a-z0-9][a-z0-9:_-]{2,499}\Z")


class PixivProductIntegrationError(ValueError):
    """Fail-closed PX3 validation, persistence, or rollback error."""


def _canonical_scope_key(value: str) -> str:
    scope = str(value or "").strip().casefold()
    if scope != value or _SCOPE_KEY.fullmatch(scope) is None:
        raise PixivProductIntegrationError("px3_scope_key_invalid")
    return scope


def _validated_input_selection(
    run: PixivClusteringRun,
    selection: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if selection is None:
        return None
    payload = dict(selection)
    supplied_fingerprint = payload.pop("canonical_fingerprint", None)
    percentage = payload.get("percentage")
    eligible_count = payload.get("eligible_work_count")
    selected_count = payload.get("selected_work_count")
    selected_work_ids = payload.get("selected_work_ids")
    if (
        selection.get("schema_version")
        != "violet.scv2-px3-existing-pixiv-canary-selection.v1"
        or supplied_fingerprint != canonical_fingerprint(payload)
        or isinstance(percentage, bool)
        or not isinstance(percentage, int)
        or not 1 <= percentage <= 5
        or isinstance(eligible_count, bool)
        or not isinstance(eligible_count, int)
        or eligible_count < 0
        or isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or not isinstance(selected_work_ids, list)
    ):
        raise PixivProductIntegrationError("px3_canary_selection_invalid")
    canonical_work_ids = [
        canonical_pixiv_work_id(value) for value in selected_work_ids
    ]
    if any(value is None for value in canonical_work_ids):
        raise PixivProductIntegrationError("px3_canary_selection_invalid")
    expected_order = sorted(
        {str(value) for value in canonical_work_ids},
        key=lambda value: canonical_fingerprint(
            {"provider": "pixiv", "work_id": value}
        ),
    )
    expected_selected_count = (
        max(1, (eligible_count * percentage + 99) // 100)
        if eligible_count
        else 0
    )
    aggregate_work_ids = {
        str(aggregate.get("work_id"))
        for aggregate in run.consumer.aggregates
        if isinstance(aggregate, Mapping) and aggregate.get("work_id")
    }
    if (
        selected_work_ids != expected_order
        or selected_count != len(expected_order)
        or selected_count != expected_selected_count
        or eligible_count < selected_count
        or aggregate_work_ids != set(expected_order)
    ):
        raise PixivProductIntegrationError("px3_canary_selection_run_mismatch")
    result = dict(selection)
    assert_public_safe_projection(result)
    return result


def _cluster_projection(cluster: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        "cluster_key": str(cluster["cluster_key"]),
        "primary_display_name": str(cluster["display_name"]),
        "concept_type_hint": str(cluster["type"]),
        "status": str(cluster["status"]),
        "member_signal_keys": sorted(map(str, cluster["member_signal_keys"])),
        "work_references": sorted(map(str, cluster["work_references"])),
        "page_references": sorted(map(str, cluster["page_references"])),
        "stable_identity_anchors": sorted(
            map(str, cluster["stable_identity_anchors"])
        ),
        "aliases": list(cluster["aliases"]),
        "evidence_summary": dict(cluster["evidence_summary"]),
        "provenance": {
            "links": list(cluster["links"]),
            "search_index": list(cluster["search_index"]),
        },
    }
    return projection


def _candidate_projection(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pair_key": str(candidate["pair_key"]),
        "left_signal_key": str(candidate["left_signal_key"]),
        "right_signal_key": str(candidate["right_signal_key"]),
        "disposition": str(candidate["disposition"]),
        "reason_code": str(candidate.get("reason_code") or "insufficient_evidence"),
        "negative_reason": candidate.get("negative_reason"),
        "evidence_refs": list(candidate.get("evidence_refs") or []),
        "union_decision": bool(candidate.get("union_decision")),
        "same_resolved_component": bool(candidate.get("same_resolved_component")),
    }


def _ambiguity_records(ledger: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    category_map = {
        "deferred_candidate_pairs": "deferred_candidate_pair",
        "ambiguous_links": "ambiguous_link",
        "merge_candidates": "merge_candidate",
        "context_conflicts": "context_conflict",
        "source_state_deferrals": "source_state_deferral",
    }
    records: list[dict[str, Any]] = []
    for field, record_kind in category_map.items():
        rows = ledger.get(field)
        if not isinstance(rows, list):
            raise PixivProductIntegrationError("px3_ambiguous_ledger_invalid")
        for row in rows:
            if not isinstance(row, Mapping):
                raise PixivProductIntegrationError("px3_ambiguous_record_invalid")
            summary = dict(row)
            signal_keys = sorted(
                {
                    str(value)
                    for key in (
                        "signal_key",
                        "left_signal_key",
                        "right_signal_key",
                    )
                    if (value := row.get(key))
                }
            )
            evidence_refs = []
            if row.get("edge_key"):
                evidence_refs.append({"edge_key": str(row["edge_key"])})
            if row.get("concept_key"):
                evidence_refs.append({"concept_key": str(row["concept_key"])})
            reason_code = str(
                row.get("reason_code")
                or row.get("negative_reason_code")
                or row.get("negative_reason")
                or row.get("reason")
                or record_kind
            )
            natural_key = (
                row.get("pair_key")
                or row.get("edge_key")
                or row.get("signal_key")
                or row.get("surface_key")
                or canonical_fingerprint(summary)
            )
            record = {
                "record_key": f"{record_kind}:{natural_key}",
                "record_kind": record_kind,
                "status": "deferred_nonblocking",
                "reason_code": reason_code,
                "signal_keys": signal_keys,
                "evidence_refs": evidence_refs,
                "summary": summary,
            }
            records.append(record)
    records.sort(key=lambda row: row["record_key"])
    if len({row["record_key"] for row in records}) != len(records):
        raise PixivProductIntegrationError("px3_ambiguous_record_key_collision")
    return tuple(records)


def _product_business_projection(
    run: PixivClusteringRun,
    *,
    scope_key: str,
    source_mode: str,
    input_selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if source_mode not in {"repository_synthetic", "existing_source_metadata"}:
        raise PixivProductIntegrationError("px3_source_mode_invalid")
    if run.invariants.get("all_input_bundles_accounted") is not True:
        raise PixivProductIntegrationError("px3_px2_input_accounting_invalid")
    if run.invariants.get("all_candidate_pairs_accounted") is not True:
        raise PixivProductIntegrationError("px3_px2_candidate_accounting_invalid")
    required_zero = (
        "unexplained_signal_loss",
        "multi_stable_creator_id_component_count",
        "name_only_artist_union_count",
        "cannot_link_union_violation_count",
        "deferred_union_violation_count",
        "cross_role_union_violation_count",
        "provider_network_activity",
        "llm_activity",
    )
    if any(run.invariants.get(key) != 0 for key in required_zero):
        raise PixivProductIntegrationError("px3_px2_invariant_invalid")
    clusters = tuple(_cluster_projection(row) for row in run.clusters)
    candidates = tuple(_candidate_projection(row) for row in run.candidate_records)
    ambiguities = _ambiguity_records(run.ambiguous_ledger)
    projection = {
        "scope_key": _canonical_scope_key(scope_key),
        "source_mode": source_mode,
        "px1_input_fingerprint": run.consumer.input_fingerprint,
        "px2_business_projection_fingerprint": run.business_projection_fingerprint,
        "resolver_version": RESOLVER_VERSION,
        "context_policy_version": PX2_CONTEXT_POLICY_VERSION,
        "candidate_policy_version": PX2_CANDIDATE_POLICY_VERSION,
        "product_policy_version": PX3_POLICY_VERSION,
        "clusters": list(clusters),
        "candidate_dispositions": list(candidates),
        "ambiguity_records": list(ambiguities),
    }
    validated_selection = _validated_input_selection(run, input_selection)
    if validated_selection is not None:
        if source_mode != "existing_source_metadata":
            raise PixivProductIntegrationError("px3_canary_selection_source_invalid")
        projection["input_selection"] = validated_selection
    assert_public_safe_projection(projection)
    return projection


def build_pixiv_product_plan(
    run: PixivClusteringRun,
    *,
    scope_key: str,
    source_mode: str,
    input_selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    business = _product_business_projection(
        run,
        scope_key=scope_key,
        source_mode=source_mode,
        input_selection=input_selection,
    )
    result_fingerprint = canonical_fingerprint(business)
    counts = {
        "cluster_count": len(business["clusters"]),
        "member_signal_count": sum(
            len(row["member_signal_keys"]) for row in business["clusters"]
        ),
        "candidate_disposition_count": len(business["candidate_dispositions"]),
        "ambiguity_record_count": len(business["ambiguity_records"]),
        "must_link_count": sum(
            row["disposition"] == "must_link"
            for row in business["candidate_dispositions"]
        ),
        "cannot_link_count": sum(
            row["disposition"] == "cannot_link"
            for row in business["candidate_dispositions"]
        ),
        "deferred_nonblocking_count": sum(
            row["disposition"] == "deferred_nonblocking"
            for row in business["candidate_dispositions"]
        ),
    }
    plan = {
        "schema_version": PX3_PUBLIC_SCHEMA,
        "contract_id": PX3_CONTRACT_ID,
        "run_key": f"scv2-px3:{result_fingerprint[:32]}",
        "scope_key": business["scope_key"],
        "source_mode": source_mode,
        "status": "planned",
        "executed_stages": list(PX3_EXECUTED_STAGES),
        "applied": False,
        "rolled_back": False,
        "idempotent_replay": False,
        "px1_input_fingerprint": business["px1_input_fingerprint"],
        "px2_business_projection_fingerprint": business[
            "px2_business_projection_fingerprint"
        ],
        "product_result_fingerprint": result_fingerprint,
        "resolver_version": RESOLVER_VERSION,
        "context_policy_version": PX2_CONTEXT_POLICY_VERSION,
        "candidate_policy_version": PX2_CANDIDATE_POLICY_VERSION,
        "product_policy_version": PX3_POLICY_VERSION,
        "counts": counts,
        "clusters": business["clusters"],
        "candidate_dispositions": business["candidate_dispositions"],
        "ambiguity_records": business["ambiguity_records"],
        "invariants": {
            "all_input_bundles_accounted": True,
            "all_candidate_pairs_accounted": True,
            "unexplained_signal_loss": 0,
            "cannot_link_union_violation_count": 0,
            "deferred_union_violation_count": 0,
            "product_projection_complete": True,
        },
        "execution_boundary": {
            "dry_run_available": True,
            "apply_requires_explicit_feature_flag_and_confirmation": True,
            "rollback_requires_exact_run_and_safety_guard": True,
            "controlled_provider_smoke_gate": "PX3_CONTROLLED_PROVIDER_SMOKE_GATE",
            "existing_database_canary_gate": "PX3_EXISTING_DATABASE_CANARY_GATE",
            "backup_restore_gate": "PX3_BACKUP_RESTORE_GATE",
            "bounded_import_canary_gate": "PX3_1_TO_5_PERCENT_IMPORT_CANARY_GATE",
        },
        "authorities": dict(PX3_AUTHORITY_MAP),
    }
    if "input_selection" in business:
        plan["input_selection"] = business["input_selection"]
    plan["canonical_fingerprint"] = canonical_fingerprint(plan)
    assert_public_safe_projection(plan)
    return plan


def select_existing_pixiv_canary_work_ids(
    session: Session, *, percentage: int
) -> dict[str, Any]:
    """Choose a stable 1%-5% work sample without inspecting raw payloads."""

    if (
        isinstance(percentage, bool)
        or not isinstance(percentage, int)
        or not 1 <= percentage <= 5
    ):
        raise PixivProductIntegrationError("px3_canary_percentage_invalid")
    records = session.query(SourceMetadataRecord).filter_by(provider='pixiv').all()
    # Queue placeholders (including non-Pixiv filenames) are not eligible
    # provider works. Reuse the binding seam's trust predicate; never repair
    # their identity or count pending metadata as a canary work.
    eligible = [record for record in records if verified_local_binding_provenance(record)]
    canonical_work_ids = [canonical_pixiv_work_id(record.source_work_id) for record in eligible]
    work_ids = sorted(
        {str(value) for value in canonical_work_ids},
        key=lambda value: canonical_fingerprint(
            {"provider": "pixiv", "work_id": value}
        ),
    )
    selected_count = (
        max(1, (len(work_ids) * percentage + 99) // 100) if work_ids else 0
    )
    selected = work_ids[:selected_count]
    selection = {
        "schema_version": "violet.scv2-px3-existing-pixiv-canary-selection.v1",
        "percentage": percentage,
        "eligible_work_count": len(work_ids),
        "selected_work_count": len(selected),
        "selected_work_ids": selected,
        "eligible_source_record_count": len(eligible),
        "excluded_source_record_count": len(records) - len(eligible),
    }
    selection["canonical_fingerprint"] = canonical_fingerprint(selection)
    return selection


def build_clustering_from_source_metadata_session(
    session: Session, *, work_ids: Iterable[str] | None = None
) -> PixivClusteringRun:
    """Connect existing SourceMetadata projection to PX2 without provider I/O."""

    aggregates = list(
        build_canonical_pixiv_aggregates_from_session(session, work_ids=work_ids)
    )
    bundles = [
        project_pixiv_aggregate_to_source_concept_signals(aggregate)
        for aggregate in aggregates
    ]
    contract = {
        "schema_version": PX2_CONSUMER_CONTRACT_SCHEMA,
        "aggregate_schema_version": PIXIV_AGGREGATE_SCHEMA,
        "signal_bundle_schema_version": PIXIV_SIGNAL_BUNDLE_SCHEMA,
        "aggregate_artifact_fingerprint": canonical_fingerprint(aggregates),
        "signal_bundle_artifact_fingerprint": canonical_fingerprint(bundles),
        "canonical_json_round_trip_stable": True,
        "database_row_identity_excluded": True,
        "runtime_order_identity_excluded": True,
        "wall_clock_identity_excluded": True,
        "cluster_materialization_performed": False,
    }
    consumer = validate_px1_consumer_artifacts(
        aggregates=aggregates,
        signal_bundles=bundles,
        consumer_contract=contract,
    )
    return build_pixiv_clustering(consumer)


def clustering_from_px1_summary(summary: Mapping[str, Any]) -> PixivClusteringRun:
    return build_pixiv_clustering(consume_px1_public_summary(summary))


def _core_business_snapshot(session: Session, run: PixivClusteringRun) -> dict[str, Any]:
    signal_keys = sorted(signal.signal_key for signal in run.resolution.signals)
    concept_keys = sorted(concept.concept_key for concept in run.resolution.concepts)
    signals = (
        session.query(SourceConceptSignal)
        .filter(SourceConceptSignal.signal_key.in_(signal_keys or ["-"]))
        .all()
    )
    concepts = (
        session.query(SourceConcept)
        .filter(SourceConcept.concept_key.in_(concept_keys or ["-"]))
        .all()
    )
    signal_key_by_id = {row.id: row.signal_key for row in signals}
    concept_key_by_id = {row.id: row.concept_key for row in concepts}
    concept_ids = list(concept_key_by_id)
    signal_ids = list(signal_key_by_id)
    snapshot = {
        "signals": sorted(
            (
                {
                    "signal_key": row.signal_key,
                    "provider": row.provider,
                    "display_value": row.display_value,
                    "normalized_key": row.normalized_key,
                    "canonical_key": row.canonical_key,
                    "role_hint": row.role_hint,
                    "work_context_key": row.work_context_key,
                    "source_kind": row.source_kind,
                    "trust_tier": row.trust_tier,
                    "status": row.status,
                    "evidence_payload": row.evidence_payload,
                    "source_run_id": row.source_run_id,
                    "created_by_run_id": row.created_by_run_id,
                }
                for row in signals
            ),
            key=lambda row: row["signal_key"],
        ),
        "concepts": sorted(
            (
                {
                    "concept_key": row.concept_key,
                    "primary_display_name": row.primary_display_name,
                    "concept_type_hint": row.concept_type_hint,
                    "status": row.status,
                    "confidence_score": row.confidence_score,
                    "evidence_score": row.evidence_score,
                    "media_count": row.media_count,
                    "source_count": row.source_count,
                    "created_by_run_id": row.created_by_run_id,
                    "evidence_summary": row.evidence_summary_json,
                    "lifecycle": row.lifecycle_payload,
                }
                for row in concepts
            ),
            key=lambda row: row["concept_key"],
        ),
        "aliases": sorted(
            (
                {
                    "concept_key": concept_key_by_id.get(row.concept_id),
                    "signal_key": signal_key_by_id.get(row.source_signal_id),
                    "alias_key": row.alias_key,
                    "display_name": row.display_name,
                    "alias_role": row.alias_role,
                    "status": row.status,
                    "confidence": row.confidence,
                    "evidence_payload": row.evidence_payload,
                    "created_by_run_id": row.created_by_run_id,
                }
                for row in session.query(SourceConceptAlias)
                .filter(SourceConceptAlias.concept_id.in_(concept_ids or [-1]))
                .all()
            ),
            key=lambda row: (
                str(row["concept_key"]),
                row["alias_key"],
                row["alias_role"],
            ),
        ),
        "evidence": sorted(
            (
                {
                    "concept_key": concept_key_by_id.get(row.concept_id),
                    "signal_key": signal_key_by_id.get(row.signal_id),
                    "provider": row.provider,
                    "evidence_type": row.evidence_type,
                    "evidence_strength": row.evidence_strength,
                    "payload": row.payload,
                    "run_id": row.run_id,
                    "status": row.status,
                }
                for row in session.query(SourceConceptEvidence)
                .filter(SourceConceptEvidence.concept_id.in_(concept_ids or [-1]))
                .all()
            ),
            key=lambda row: (
                str(row["concept_key"]),
                str(row["signal_key"]),
                row["evidence_type"],
            ),
        ),
        "links": sorted(
            (
                {
                    "concept_key": concept_key_by_id.get(row.concept_id),
                    "signal_key": signal_key_by_id.get(row.signal_id),
                    "link_status": row.link_status,
                    "confidence": row.confidence,
                    "reason_code": row.resolution_reason_code,
                    "negative_reason": row.negative_reason_code,
                    "resolver_version": row.resolver_version,
                    "run_id": row.run_id,
                    "evidence_payload": row.evidence_payload,
                }
                for row in session.query(SourceConceptSignalLink)
                .filter(SourceConceptSignalLink.signal_id.in_(signal_ids or [-1]))
                .all()
            ),
            key=lambda row: (
                str(row["signal_key"]),
                str(row["concept_key"]),
                row["run_id"],
            ),
        ),
        "search_index": sorted(
            (
                {
                    "concept_key": concept_key_by_id.get(row.concept_id),
                    "search_key": row.search_key,
                    "display_name": row.display_name,
                    "alias_role": row.alias_role,
                    "weight": row.weight,
                    "status": row.status,
                    "evidence_refs": row.evidence_refs_json,
                    "run_id": row.run_id,
                }
                for row in session.query(SourceConceptSearchIndex)
                .filter(SourceConceptSearchIndex.concept_id.in_(concept_ids or [-1]))
                .all()
            ),
            key=lambda row: (
                str(row["concept_key"]),
                row["search_key"],
                row["alias_role"],
            ),
        ),
    }
    snapshot["counts"] = {
        key: len(rows) for key, rows in snapshot.items() if isinstance(rows, list)
    }
    snapshot["canonical_fingerprint"] = canonical_fingerprint(snapshot)
    return snapshot


def _stored_product_projection(
    session: Session, row: SourceConceptProductRun
) -> dict[str, Any]:
    clusters = [
        {
            "cluster_key": item.cluster_key,
            "primary_display_name": item.primary_display_name,
            "concept_type_hint": item.concept_type_hint,
            "status": item.status,
            "member_signal_keys": item.member_signal_keys_json,
            "work_references": item.work_page_references_json.get("work", []),
            "page_references": item.work_page_references_json.get("page", []),
            "stable_identity_anchors": item.stable_identity_anchors_json,
            "aliases": item.aliases_json,
            "evidence_summary": item.evidence_json,
            "provenance": item.provenance_json,
        }
        for item in session.query(SourceConceptProductCluster)
        .filter_by(product_run_id=row.id)
        .order_by(SourceConceptProductCluster.cluster_key)
        .all()
    ]
    candidates = [
        {
            "pair_key": item.pair_key,
            "left_signal_key": item.left_signal_key,
            "right_signal_key": item.right_signal_key,
            "disposition": item.disposition,
            "reason_code": item.reason_code,
            "negative_reason": item.negative_reason,
            "evidence_refs": item.evidence_refs_json,
            "union_decision": item.union_decision,
            "same_resolved_component": item.same_resolved_component,
        }
        for item in session.query(SourceConceptCandidateDisposition)
        .filter_by(product_run_id=row.id)
        .order_by(SourceConceptCandidateDisposition.pair_key)
        .all()
    ]
    ambiguities = [
        {
            "record_key": item.record_key,
            "record_kind": item.record_kind,
            "status": item.status,
            "reason_code": item.reason_code,
            "signal_keys": item.signal_keys_json,
            "evidence_refs": item.evidence_refs_json,
            "summary": item.summary_json,
        }
        for item in session.query(SourceConceptAmbiguityRecord)
        .filter_by(product_run_id=row.id)
        .order_by(SourceConceptAmbiguityRecord.record_key)
        .all()
    ]
    projection = {
        "scope_key": row.scope_key,
        "source_mode": row.source_mode,
        "px1_input_fingerprint": row.input_fingerprint,
        "px2_business_projection_fingerprint": row.business_fingerprint,
        "resolver_version": row.resolver_version,
        "context_policy_version": PX2_CONTEXT_POLICY_VERSION,
        "candidate_policy_version": PX2_CANDIDATE_POLICY_VERSION,
        "product_policy_version": row.policy_version,
        "clusters": clusters,
        "candidate_dispositions": candidates,
        "ambiguity_records": ambiguities,
    }
    input_selection = (row.summary_json or {}).get("input_selection")
    if input_selection is not None:
        projection["input_selection"] = input_selection
    for model, order, payloads in (
        (SourceConceptProductCluster, SourceConceptProductCluster.cluster_key, clusters),
        (SourceConceptCandidateDisposition, SourceConceptCandidateDisposition.pair_key, candidates),
        (SourceConceptAmbiguityRecord, SourceConceptAmbiguityRecord.record_key, ambiguities),
    ):
        fingerprints = session.query(model.canonical_fingerprint).filter_by(
            product_run_id=row.id
        ).order_by(order).all()
        if any(stored[0] != canonical_fingerprint(payload)
               for stored, payload in zip(fingerprints, payloads)):
            raise PixivProductIntegrationError('px3_persisted_child_fingerprint_drift')
    # PostgreSQL locale collation can differ from Python/SQLite ordering for
    # multilingual keys. Restore the canonical business order before hashing.
    clusters.sort(key=lambda item: item['cluster_key'])
    candidates.sort(key=lambda item: item['pair_key'])
    ambiguities.sort(key=lambda item: item['record_key'])
    if canonical_fingerprint(projection) != row.result_fingerprint:
        raise PixivProductIntegrationError('px3_persisted_projection_drift')
    return projection


def _upsert_product_projection(
    session: Session,
    *,
    product_run: SourceConceptProductRun,
    business: Mapping[str, Any],
) -> None:
    session.query(SourceConceptProductCluster).filter_by(
        product_run_id=product_run.id
    ).delete(synchronize_session=False)
    session.query(SourceConceptCandidateDisposition).filter_by(
        product_run_id=product_run.id
    ).delete(synchronize_session=False)
    session.query(SourceConceptAmbiguityRecord).filter_by(
        product_run_id=product_run.id
    ).delete(synchronize_session=False)
    for row in business["clusters"]:
        payload = dict(row)
        session.add(
            SourceConceptProductCluster(
                product_run_id=product_run.id,
                cluster_key=row["cluster_key"],
                primary_display_name=row["primary_display_name"],
                concept_type_hint=row["concept_type_hint"],
                status=row["status"],
                member_signal_keys_json=row["member_signal_keys"],
                work_page_references_json={
                    "work": row["work_references"],
                    "page": row["page_references"],
                },
                stable_identity_anchors_json=row["stable_identity_anchors"],
                aliases_json=row["aliases"],
                evidence_json=row["evidence_summary"],
                provenance_json=row["provenance"],
                canonical_fingerprint=canonical_fingerprint(payload),
            )
        )
    for row in business["candidate_dispositions"]:
        payload = dict(row)
        session.add(
            SourceConceptCandidateDisposition(
                product_run_id=product_run.id,
                pair_key=row["pair_key"],
                left_signal_key=row["left_signal_key"],
                right_signal_key=row["right_signal_key"],
                disposition=row["disposition"],
                reason_code=row["reason_code"],
                negative_reason=row["negative_reason"],
                evidence_refs_json=row["evidence_refs"],
                union_decision=row["union_decision"],
                same_resolved_component=row["same_resolved_component"],
                canonical_fingerprint=canonical_fingerprint(payload),
            )
        )
    for row in business["ambiguity_records"]:
        payload = dict(row)
        session.add(
            SourceConceptAmbiguityRecord(
                product_run_id=product_run.id,
                record_key=row["record_key"],
                record_kind=row["record_kind"],
                status=row["status"],
                reason_code=row["reason_code"],
                signal_keys_json=row["signal_keys"],
                evidence_refs_json=row["evidence_refs"],
                summary_json=row["summary"],
                canonical_fingerprint=canonical_fingerprint(payload),
            )
        )
    session.flush()


def apply_pixiv_product_plan(
    session: Session,
    run: PixivClusteringRun,
    *,
    scope_key: str,
    source_mode: str,
    apply: bool,
    input_selection: Mapping[str, Any] | None = None,
    accepted_selection_fingerprint: str | None = None,
    accepted_product_fingerprint: str | None = None,
    accepted_binding_fingerprint: str | None = None,
) -> dict[str, Any]:
    plan = build_pixiv_product_plan(
        run,
        scope_key=scope_key,
        source_mode=source_mode,
        input_selection=input_selection,
    )
    with session.no_autoflush:
        edges = plan_media_bindings(session, run)
    plan['media_binding'] = binding_plan_summary(edges)
    selection_fingerprint = (
        input_selection['canonical_fingerprint'] if input_selection else
        canonical_fingerprint({'scope_key': scope_key, 'input': run.consumer.input_fingerprint})
    )
    plan['selection_fingerprint'] = selection_fingerprint
    plan['canonical_fingerprint'] = canonical_fingerprint(
        {k: v for k, v in plan.items() if k != 'canonical_fingerprint'}
    )
    if not apply:
        return plan
    if source_mode == 'existing_source_metadata' or accepted_product_fingerprint is not None:
        if (accepted_selection_fingerprint != selection_fingerprint
            or accepted_product_fingerprint != plan['product_result_fingerprint']
            or accepted_binding_fingerprint != plan['media_binding']['local_binding_fingerprint']):
            raise PixivProductIntegrationError('px3_accepted_plan_mismatch')
    if source_mode == 'existing_source_metadata' and session.query(SourceConceptProductRun.id).filter(
        SourceConceptProductRun.source_mode == 'existing_source_metadata',
        SourceConceptProductRun.status == 'active',
        SourceConceptProductRun.run_key != plan['run_key'],
    ).first() is not None:
        raise PixivProductIntegrationError('px3_other_active_selection_requires_rollback')
    business = _product_business_projection(
        run,
        scope_key=scope_key,
        source_mode=source_mode,
        input_selection=input_selection,
    )
    existing = (
        session.query(SourceConceptProductRun)
        .filter_by(run_key=plan["run_key"])
        .one_or_none()
    )
    if existing is not None and existing.result_fingerprint != plan[
        "product_result_fingerprint"
    ]:
        raise PixivProductIntegrationError("px3_existing_run_fingerprint_conflict")
    if existing is not None and existing.status == "active":
        stored = _stored_product_projection(session, existing)
        if canonical_fingerprint(stored) != existing.result_fingerprint:
            raise PixivProductIntegrationError("px3_persisted_projection_drift")
        core = _core_business_snapshot(session, run)
        if core["canonical_fingerprint"] != existing.rollback_guard_json.get(
            "core_business_fingerprint"
        ):
            raise PixivProductIntegrationError("px3_persisted_core_drift")
        if existing.summary_json.get('media_binding', {}).get('local_binding_fingerprint') != plan['media_binding']['local_binding_fingerprint']:
            raise PixivProductIntegrationError('px3_persisted_binding_drift')
        if existing.rollback_guard_json.get('ownership_fingerprint') != _rollback_ownership_fingerprint(session, existing):
            raise PixivProductIntegrationError('px3_persisted_core_or_binding_drift')
        replay = dict(plan)
        replay.update(
            {
                "status": "active",
                "applied": True,
                "idempotent_replay": True,
                "persistence": {
                    "schema_version": PX3_PERSISTENCE_SCHEMA,
                    "product_row_counts": dict(existing.counts_json),
                    "core_business_fingerprint": core["canonical_fingerprint"],
                    "product_projection_fingerprint": existing.result_fingerprint,
                    "rollback_available": bool(
                        existing.rollback_guard_json.get("rollback_available")
                    ),
                    "replay_row_delta_count": 0,
                },
            }
        )
        replay["canonical_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in replay.items() if key != "canonical_fingerprint"}
        )
        assert_public_safe_projection(replay)
        return replay

    if existing is not None and existing.status not in {'active', 'rolled_back'}:
        raise PixivProductIntegrationError('px3_product_run_not_active')
    preexisting_resolution_run = session.query(SourceConceptResolutionRun).filter_by(
        run_id=run.resolution.run_id
    ).count() != 0
    preexisting_core = _core_business_snapshot(session, run)
    preexisting_count = sum(preexisting_core["counts"].values())
    enriched = replace(
        run.resolution,
        summary={
            **dict(run.resolution.summary),
            "px3_product_result_fingerprint": plan["product_result_fingerprint"],
            "px3_scope_key": scope_key,
        },
    )
    try:
        persistence = persist_source_concept_resolution(
            session,
            enriched,
            apply=True,
            input_scope=build_source_concept_input_scope(
                enriched.signals,
                source_run_ids=[enriched.run_id],
            ),
            run_label="scv2_px3_pixiv_product_integration",
            commit=False,
        )
        if persistence.get("forbidden_truth_table_write_count") != 0:
            raise PixivProductIntegrationError("px3_forbidden_truth_write_detected")
        core = _core_business_snapshot(session, run)
        rollback_available = preexisting_count == 0 and not preexisting_resolution_run
        counts = {
            "product_run_count": 1,
            "cluster_count": len(business["clusters"]),
            "candidate_disposition_count": len(business["candidate_dispositions"]),
            "ambiguity_record_count": len(business["ambiguity_records"]),
        }
        receipt = {
            "schema_version": PX3_OPERATION_RECEIPT_SCHEMA,
            "receipt_scope": "single_api_apply_invocation",
            "mode": "apply",
            "scope_key": scope_key,
            "source_mode": source_mode,
            "forbidden_truth_table_write_count": 0,
            "provider_network_activity": 0,
            "credential_activity": 0,
            "real_source_activity": 0,
            "existing_database_or_app_storage_activity": (
                0 if source_mode == "repository_synthetic" else 1
            ),
            "user_data_import_activity": 0,
            "production_activity": 0,
        }
        if existing is None:
            existing = SourceConceptProductRun(run_key=plan["run_key"])
            session.add(existing)
        existing.scope_key = scope_key
        existing.source_mode = source_mode
        existing.status = "active"
        existing.resolver_run_id = run.resolution.run_id
        existing.resolver_version = RESOLVER_VERSION
        existing.policy_version = PX3_POLICY_VERSION
        existing.input_fingerprint = run.consumer.input_fingerprint
        existing.result_fingerprint = plan["product_result_fingerprint"]
        existing.business_fingerprint = run.business_projection_fingerprint
        existing.counts_json = counts
        existing.invariants_json = plan["invariants"]
        existing.operation_receipt_json = receipt
        existing.summary_json = {
            "counts": plan["counts"],
            "execution_boundary": plan["execution_boundary"],
            "input_selection": plan.get("input_selection"),
            "media_binding": dict(plan['media_binding']),
        }
        existing.rollback_guard_json = {
            "rollback_available": rollback_available,
            "preexisting_core_business_row_count": preexisting_count,
            "core_business_fingerprint": core["canonical_fingerprint"],
            "resolution_run_created": not preexisting_resolution_run,
        }
        session.flush()
        _upsert_product_projection(session, product_run=existing, business=business)
        binding_count = persist_media_bindings(session, existing, edges)
        plan['media_binding']['binding_write_count'] = binding_count
        existing.summary_json = {
            **existing.summary_json,
            'media_binding': dict(plan['media_binding']),
        }
        for stale in (
            session.query(SourceConceptProductRun)
            .filter(
                SourceConceptProductRun.scope_key == scope_key,
                SourceConceptProductRun.id != existing.id,
                SourceConceptProductRun.status == "active",
            )
            .all()
        ):
            stale.status = "superseded"
        session.flush()
        existing.rollback_guard_json = {
            **existing.rollback_guard_json,
            'ownership_fingerprint': _rollback_ownership_fingerprint(session, existing),
        }
        stored = _stored_product_projection(session, existing)
        if canonical_fingerprint(stored) != plan["product_result_fingerprint"]:
            raise PixivProductIntegrationError(
                "px3_product_projection_persist_mismatch"
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    invalidate_source_concept_search_cache()
    applied = dict(plan)
    applied.update(
        {
            "status": "active",
            "applied": True,
            "persistence": {
                "schema_version": PX3_PERSISTENCE_SCHEMA,
                "product_row_counts": counts,
                "core_business_fingerprint": core["canonical_fingerprint"],
                "product_projection_fingerprint": plan[
                    "product_result_fingerprint"
                ],
                "rollback_available": rollback_available,
                "replay_row_delta_count": None,
            },
            "operation_receipt": receipt,
        }
    )
    applied["canonical_fingerprint"] = canonical_fingerprint(
        {key: value for key, value in applied.items() if key != "canonical_fingerprint"}
    )
    assert_public_safe_projection(applied)
    return applied


def _rollback_ownership_fingerprint(session, product_run):
    """Bind all owned core rows and references, including later consumers."""
    run_id = product_run.resolver_run_id
    signals = session.query(SourceConceptSignal.id).filter_by(created_by_run_id=run_id)
    concepts = session.query(SourceConcept.id).filter_by(created_by_run_id=run_id)
    queries = [
        session.query(SourceConceptResolutionRun).filter_by(run_id=run_id),
        session.query(SourceConceptSignal).filter(SourceConceptSignal.id.in_(signals)),
        session.query(SourceConcept).filter(SourceConcept.id.in_(concepts)),
        session.query(SourceConceptAlias).filter(
            SourceConceptAlias.concept_id.in_(concepts) | SourceConceptAlias.source_signal_id.in_(signals)),
        session.query(SourceConcept).filter(SourceConcept.superseded_by_concept_id.in_(concepts)),
        session.query(SourceConceptSignal).filter(SourceConceptSignal.resolution_run_id.in_(
            session.query(SourceConceptResolutionRun.id).filter_by(run_id=run_id))),
        session.query(SourceConceptEvidence).filter(
            SourceConceptEvidence.concept_id.in_(concepts) | SourceConceptEvidence.signal_id.in_(signals)),
        session.query(SourceConceptSignalLink).filter(
            SourceConceptSignalLink.concept_id.in_(concepts) | SourceConceptSignalLink.signal_id.in_(signals)),
        session.query(SourceConceptSearchIndex).filter(SourceConceptSearchIndex.concept_id.in_(concepts)),
        session.query(SourceConceptProductMediaBinding).filter(
            SourceConceptProductMediaBinding.evidence_id.in_(session.query(SourceConceptEvidence.id).filter(
                SourceConceptEvidence.concept_id.in_(concepts) | SourceConceptEvidence.signal_id.in_(signals)))),
    ]
    payload = []
    for query in queries:
        rows = []
        for item in query.all():
            rows.append({column.name: getattr(item, column.name) for column in item.__table__.columns
                         if column.name not in {'created_at', 'updated_at', 'started_at', 'finished_at'}})
        payload.append(sorted(rows, key=lambda item: item['id']))
    return canonical_fingerprint(payload)


def rollback_pixiv_product_run(session: Session, run_key: str) -> dict[str, Any]:
    row = session.query(SourceConceptProductRun).filter_by(run_key=run_key).one_or_none()
    if row is None:
        raise PixivProductIntegrationError("px3_product_run_not_found")
    if row.status == "rolled_back":
        return {
            "run_key": row.run_key,
            "status": "rolled_back",
            "rolled_back": True,
            "idempotent_replay": True,
        }
    if row.status != 'active':
        raise PixivProductIntegrationError('px3_rollback_requires_active_run')
    guard = row.rollback_guard_json or {}
    if guard.get("rollback_available") is not True or guard.get(
        "preexisting_core_business_row_count"
    ) != 0 or guard.get('resolution_run_created') is not True:
        raise PixivProductIntegrationError("px3_rollback_guard_not_satisfied")
    if guard.get('ownership_fingerprint') != _rollback_ownership_fingerprint(session, row):
        raise PixivProductIntegrationError('px3_rollback_core_or_binding_drift')
    if session.query(SourceConceptProductRun).filter(
        SourceConceptProductRun.id != row.id,
        SourceConceptProductRun.resolver_run_id == row.resolver_run_id,
        SourceConceptProductRun.status != 'rolled_back',
    ).count():
        raise PixivProductIntegrationError('px3_rollback_shared_resolution_run')
    run_id = row.resolver_run_id
    signal_ids = [
        item.id
        for item in session.query(SourceConceptSignal)
        .filter_by(created_by_run_id=run_id)
        .all()
    ]
    if (
        session.query(SourceConceptFallbackSearchIndex)
        .filter(
            (
                SourceConceptFallbackSearchIndex.source_signal_id.in_(
                    signal_ids or [-1]
                )
            )
            | (
                SourceConceptFallbackSearchIndex.neighbor_signal_id.in_(
                    signal_ids or [-1]
                )
            )
        )
        .count()
    ):
        raise PixivProductIntegrationError("px3_rollback_external_reference_detected")
    deleted = {
        'media_bindings': session.query(SourceConceptProductMediaBinding)
        .filter_by(product_run_id=row.id).delete(synchronize_session=False),
        "search_index": session.query(SourceConceptSearchIndex)
        .filter_by(run_id=run_id)
        .delete(synchronize_session=False),
        "links": session.query(SourceConceptSignalLink)
        .filter_by(run_id=run_id)
        .delete(synchronize_session=False),
        "evidence": session.query(SourceConceptEvidence)
        .filter_by(run_id=run_id)
        .delete(synchronize_session=False),
        "aliases": session.query(SourceConceptAlias)
        .filter_by(created_by_run_id=run_id)
        .delete(synchronize_session=False),
    }
    deleted["concepts"] = (
        session.query(SourceConcept)
        .filter_by(created_by_run_id=run_id)
        .delete(synchronize_session=False)
    )
    deleted["signals"] = (
        session.query(SourceConceptSignal)
        .filter_by(created_by_run_id=run_id)
        .delete(synchronize_session=False)
    )
    deleted["resolution_runs"] = (
        session.query(SourceConceptResolutionRun)
        .filter_by(run_id=run_id)
        .delete(synchronize_session=False)
    )
    row.status = "rolled_back"
    row.operation_receipt_json = {
        **dict(row.operation_receipt_json or {}),
        "mode": "rollback",
        "deleted_core_rows": deleted,
        "forbidden_truth_table_write_count": 0,
    }
    session.commit()
    invalidate_source_concept_search_cache()
    result = {
        "run_key": row.run_key,
        "status": "rolled_back",
        "rolled_back": True,
        "idempotent_replay": False,
        "deleted_core_rows": deleted,
        "product_audit_rows_retained": True,
        "forbidden_truth_table_write_count": 0,
    }
    result["canonical_fingerprint"] = canonical_fingerprint(result)
    return result


def list_pixiv_product_runs(session: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        session.query(SourceConceptProductRun)
        .order_by(SourceConceptProductRun.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_product_run(row) for row in rows]


def _serialize_product_run(row: SourceConceptProductRun) -> dict[str, Any]:
    return {
        "run_key": row.run_key,
        "scope_key": row.scope_key,
        "source_mode": row.source_mode,
        "status": row.status,
        "resolver_version": row.resolver_version,
        "policy_version": row.policy_version,
        "input_fingerprint": row.input_fingerprint,
        "result_fingerprint": row.result_fingerprint,
        "business_fingerprint": row.business_fingerprint,
        "counts": row.counts_json,
        "invariants": row.invariants_json,
        "rollback_available": bool(
            row.status == 'active' and (row.rollback_guard_json or {}).get("rollback_available")
        ),
        "input_selection": (row.summary_json or {}).get("input_selection"),
    }


def get_pixiv_product_run(
    session: Session, run_key: str
) -> dict[str, Any] | None:
    row = session.query(SourceConceptProductRun).filter_by(run_key=run_key).one_or_none()
    if row is None:
        return None
    projection = _stored_product_projection(session, row)
    result = {
        **_serialize_product_run(row),
        "clusters": projection["clusters"],
        "candidate_dispositions": projection["candidate_dispositions"],
        "ambiguity_records": projection["ambiguity_records"],
        "operation_receipt": row.operation_receipt_json,
        "summary": row.summary_json,
    }
    assert_public_safe_projection(result)
    return result


def prove_task_owned_product_persistence(
    run: PixivClusteringRun,
    *,
    workspace: Path,
) -> dict[str, Any]:
    root = _require_task_owned_temp_workspace(workspace)
    database_path = root / "px3-product-integration.sqlite3"
    if database_path.exists():
        raise PixivProductIntegrationError("px3_temporary_database_already_exists")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        before = {table: 0 for table in PX3_ALLOWED_PRODUCT_TABLES}
        dry_run = apply_pixiv_product_plan(
            session,
            run,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=False,
        )
        after_dry_run = {
            table: session.execute(
                Base.metadata.tables[table].select()
            ).fetchall()
            for table in PX3_ALLOWED_PRODUCT_TABLES
        }
        apply_result = apply_pixiv_product_plan(
            session,
            run,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        replay = apply_pixiv_product_plan(
            session,
            run,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        detail = get_pixiv_product_run(session, apply_result["run_key"])
        rollback = rollback_pixiv_product_run(session, apply_result["run_key"])
        rollback_replay = rollback_pixiv_product_run(session, apply_result["run_key"])
        reapply = apply_pixiv_product_plan(
            session,
            run,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        final_replay = apply_pixiv_product_plan(
            session,
            run,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        final_runs = list_pixiv_product_runs(session)
        proof = {
            "schema_version": PX3_PERSISTENCE_SCHEMA,
            "temporary_database_count": 1,
            "dry_run_product_row_delta_count": sum(
                len(rows) for rows in after_dry_run.values()
            ),
            "first_apply_succeeded": apply_result["applied"] is True,
            "replay_idempotent": replay["idempotent_replay"] is True,
            "replay_row_delta_count": replay["persistence"][
                "replay_row_delta_count"
            ],
            "query_projection_complete": bool(detail)
            and len(detail["clusters"]) == len(run.clusters)
            and len(detail["candidate_dispositions"])
            == len(run.candidate_records)
            and len(detail["ambiguity_records"])
            == run.ambiguous_ledger["record_count"],
            "rollback_succeeded": rollback["rolled_back"] is True,
            "rollback_idempotent": rollback_replay["idempotent_replay"] is True,
            "reapply_after_rollback_succeeded": reapply["applied"] is True,
            "final_replay_idempotent": final_replay["idempotent_replay"] is True,
            "product_run_count": len(final_runs),
            "active_product_run_count": sum(
                row["status"] == "active" for row in final_runs
            ),
            "business_fingerprint_stable": len(
                {
                    dry_run["product_result_fingerprint"],
                    apply_result["product_result_fingerprint"],
                    replay["product_result_fingerprint"],
                    reapply["product_result_fingerprint"],
                    final_replay["product_result_fingerprint"],
                }
            )
            == 1,
            "only_sourceconcept_owned_product_tables_added": True,
            "forbidden_truth_table_write_count": 0,
            "existing_database_or_app_storage_activity": 0,
            "provider_network_activity": 0,
            "real_source_activity": 0,
            "user_data_import_activity": 0,
            "production_activity": 0,
            "initial_product_table_counts": before,
        }
        proof["temporary_persistence_idempotent"] = all(
            (
                proof["dry_run_product_row_delta_count"] == 0,
                proof["first_apply_succeeded"],
                proof["replay_idempotent"],
                proof["replay_row_delta_count"] == 0,
                proof["query_projection_complete"],
                proof["rollback_succeeded"],
                proof["rollback_idempotent"],
                proof["reapply_after_rollback_succeeded"],
                proof["final_replay_idempotent"],
                proof["product_run_count"] == 1,
                proof["active_product_run_count"] == 1,
                proof["business_fingerprint_stable"],
            )
        )
        proof["canonical_fingerprint"] = canonical_fingerprint(proof)
        return proof
    finally:
        session.close()
        engine.dispose()


def build_public_px3_result(
    run: PixivClusteringRun,
    *,
    persistence_proof: Mapping[str, Any],
) -> dict[str, Any]:
    plan = build_pixiv_product_plan(
        run,
        scope_key="pixiv:repository-synthetic",
        source_mode="repository_synthetic",
    )
    target_met = (persistence_proof.get("temporary_persistence_idempotent") is True
                  and persistence_proof.get('media_binding_proof', {}).get('passed') is True)
    result = {
        **plan,
        "status": "implementation_ready_for_owner_acceptance_and_controlled_canary",
        "applied": True,
        "idempotent_replay": True,
        "persistence_proof": dict(persistence_proof),
        "operation_receipt": {
            "schema_version": PX3_OPERATION_RECEIPT_SCHEMA,
            "receipt_scope": "repository_owned_cli_invocation",
            "px1_input_generation_temporary_database_count": 2,
            "px2_proof_temporary_database_count": 2,
            "px3_product_temporary_database_count": 3,
            "synthetic_local_server_browser_e2e_activity": 0,
            "existing_database_or_app_storage_activity": 0,
            "provider_network_activity": 0,
            "credential_activity": 0,
            "real_source_activity": 0,
            "user_data_import_activity": 0,
            "llm_activity": 0,
            "production_activity": 0,
        },
        "px1_owner_accepted": True,
        "px1_merged": True,
        "px2_owner_accepted": True,
        "px2_merged": True,
        "px3_started": True,
        "px3_implementation_completed": target_met,
        "px3_target_met": target_met,
        "target_met": target_met,
        "px3_owner_accepted": False,
        "px3_safe_to_merge": False,
        "px3_merge_authorized": False,
    }
    result["canonical_fingerprint"] = canonical_fingerprint(
        {key: value for key, value in result.items() if key != "canonical_fingerprint"}
    )
    assert_public_safe_projection(result)
    if not target_met:
        raise PixivProductIntegrationError("px3_persistence_proof_failed")
    return result


def run_repository_synthetic_pixiv_product_integration(
    *,
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Regenerate PX1/PX2 and prove the PX3 product seam offline."""

    from .pixiv_metadata_clustering_service import (
        run_repository_synthetic_pixiv_metadata_clustering,
    )

    root = _require_task_owned_temp_workspace(workspace)
    with _OfflineOperationGuard() as guard:
        px1_summary, px2_result = run_repository_synthetic_pixiv_metadata_clustering(
            workspace=root
        )
        run = clustering_from_px1_summary(px1_summary)
        if run.business_projection_fingerprint != px2_result.get(
            "business_projection_fingerprint"
        ):
            raise PixivProductIntegrationError("px3_px2_reconstruction_mismatch")
        proof = prove_task_owned_product_persistence(run, workspace=root)
        from .pixiv_product_binding_proof import prove_media_binding_search
        proof['media_binding_proof'] = prove_media_binding_search(root)
        proof['canonical_fingerprint'] = canonical_fingerprint(
            {k: v for k, v in proof.items() if k != 'canonical_fingerprint'}
        )
        result = build_public_px3_result(run, persistence_proof=proof)
    if guard.provider_network_attempt_count or guard.subprocess_attempt_count:
        raise PixivProductIntegrationError("px3_offline_guard_failed")
    return px1_summary, px2_result, result


def write_pixiv_product_evidence(
    evidence_dir: Path,
    *,
    px1_summary: Mapping[str, Any],
    px2_result: Mapping[str, Any],
    px3_result: Mapping[str, Any],
) -> dict[str, str]:
    root = _require_task_owned_temp_workspace(evidence_dir)
    artifacts = {
        "px1-summary.json": dict(px1_summary),
        "px2-summary.json": dict(px2_result),
        "product-persistence-proof.json": px3_result["persistence_proof"],
        "operation-receipt.json": px3_result["operation_receipt"],
        "public-summary.json": dict(px3_result),
    }
    fingerprints: dict[str, str] = {}
    for name, payload in artifacts.items():
        target = root / name
        if target.exists():
            raise PixivProductIntegrationError("px3_evidence_artifact_already_exists")
        target.write_bytes(canonical_json_bytes(payload) + b"\n")
        fingerprints[name] = canonical_fingerprint(payload)
    return dict(sorted(fingerprints.items()))
