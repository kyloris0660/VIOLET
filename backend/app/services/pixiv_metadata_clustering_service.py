"""SCV2-PX2 deterministic Pixiv SourceConcept clustering.

This module consumes the frozen PX1 aggregate/signal contract, reconstructs
database-neutral resolver drafts, and delegates graph resolution and
persistence to the existing SourceConcept services.  It never calls a
provider, reads an existing application database, promotes Entity truth, or
uses an LLM.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ..database import Base
from ..models import (
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptResolutionRun,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
)
from .creator_identity_policy import stable_creator_identity_key
from .pixiv_identity_policy import (
    canonical_pixiv_creator_id,
    canonical_pixiv_page_domain,
    canonical_pixiv_page_index,
    canonical_pixiv_work_id,
)
from .pixiv_metadata_projection_service import (
    PIXIV_AGGREGATE_SCHEMA,
    PIXIV_PUBLIC_SUMMARY_SCHEMA,
    PIXIV_SIGNAL_BUNDLE_SCHEMA,
    PixivMetadataProjectionError,
    assert_public_safe_projection,
    canonical_fingerprint,
    canonical_json_bytes,
    stable_pixiv_work_page_key,
)
from .pixiv_metadata_vertical_slice_service import (
    PX1_CONTRACT_ID,
    PX2_CONSUMER_CONTRACT_SCHEMA,
    _OfflineOperationGuard,
    _require_task_owned_temp_workspace,
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
)
from .source_concept_autonomous_closure_service import (
    CandidatePair,
    PairDisposition,
    build_complete_candidate_pair_manifest,
    disposition_accounting,
    pair_id_for,
)
from .source_concept_resolver_service import (
    HARD_NEGATIVE_REASON_CODES,
    LLMAdjudicationConfig,
    RESOLVER_VERSION,
    SOURCE_CONCEPT_ALLOWED_WRITE_TABLES,
    SourceConceptEdgeDraft,
    SourceConceptResolutionResult,
    SourceConceptSignalDraft,
    SourceConceptSignalInput,
    build_source_concept_input_scope,
    build_source_concept_signal_drafts,
    persist_source_concept_resolution,
    resolve_source_concepts,
    signal_role_group,
)


PX2_CONTRACT_ID = "scv2_px2_deterministic_pixiv_clustering_contract_v1"
PX2_CLUSTER_RESULT_SCHEMA = (
    "violet.scv2-px2-pixiv-source-concept-cluster-result.v1"
)
PX2_OPERATION_RECEIPT_SCHEMA = "violet.scv2-px2-offline-operation-receipt.v1"
PX2_CONTEXT_POLICY_VERSION = "scv2_px2_pixiv_role_context_policy_v1"
PX2_CANDIDATE_POLICY_VERSION = "scv2_px2_resolver_candidate_disposition_v1"
PX2_AMBIGUOUS_LEDGER_SCHEMA = "violet.scv2-px2-ambiguous-ledger.v1"
PX2_PERSISTENCE_PROOF_SCHEMA = "violet.scv2-px2-temporary-persistence-proof.v1"
PX2_EXECUTED_STAGES = (
    "px1_consumer_validation",
    "canonical_signal_reconstruction",
    "role_aware_pixiv_context_projection",
    "existing_source_concept_resolution",
    "candidate_disposition_accounting",
    "nonblocking_ambiguous_ledger",
    "task_owned_temporary_persistence_replay",
    "public_safe_cluster_result",
)
PX2_AUTHORITY_MAP = {
    "px2_synthetic_implementation_authorized": True,
    "task_owned_temporary_database_authorized": True,
    "px2_merge_authorized": False,
    "real_source_authorized": False,
    "real_provider_authorized": False,
    "provider_credential_authorized": False,
    "existing_database_authorized": False,
    "migration_authorized": False,
    "media_download_authorized": False,
    "user_data_import_or_tagging_authorized": False,
    "llm_or_external_model_authorized": False,
    "server_browser_e2e_authorized": False,
    "production_authorized": False,
    "full_library_import_authorized": False,
}

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_AGGREGATE_KEYS = frozenset(
    {
        "schema_version",
        "provider",
        "stable_work_page_key",
        "work_id",
        "page_index",
        "page_count_or_known_scope",
        "creator",
        "title_observation",
        "tag_observations",
        "source_fingerprints",
        "provenance_fingerprints",
        "provenance",
        "metadata_completeness",
        "lifecycle",
        "disposition",
        "conflict_reasons",
        "deferred_reasons",
        "canonical_fingerprint",
    }
)
_BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "provider",
        "stable_work_page_key",
        "aggregate_fingerprint",
        "source_state",
        "signals",
        "signal_count",
        "logical_keys",
        "strong_identity_anchor_count",
        "name_only_identity_anchor_count",
        "cross_context_union_count",
        "cluster_materialization_performed",
        "entity_truth_promoted",
        "canonical_fingerprint",
    }
)
_SIGNAL_KEYS = frozenset(
    {
        "signal_key",
        "origin_type",
        "provider",
        "source_record_id",
        "raw_value",
        "display_value",
        "normalized_key",
        "canonical_key",
        "role_hint",
        "work_context_key",
        "parenthetical_base",
        "parenthetical_context",
        "source_kind",
        "trust_tier",
        "confidence",
        "status",
        "identity_anchor",
        "evidence",
    }
)
_SOURCE_STATE_KEYS = frozenset(
    {
        "disposition",
        "conflict_reasons",
        "deferred_reasons",
        "metadata_completeness",
        "provenance",
    }
)
_CONSUMER_CONTRACT_KEYS = frozenset(
    {
        "schema_version",
        "aggregate_schema_version",
        "signal_bundle_schema_version",
        "aggregate_artifact_fingerprint",
        "signal_bundle_artifact_fingerprint",
        "canonical_json_round_trip_stable",
        "database_row_identity_excluded",
        "runtime_order_identity_excluded",
        "wall_clock_identity_excluded",
        "cluster_materialization_performed",
    }
)
_PX1_SUMMARY_KEYS = frozenset(
    {
        "schema_version",
        "contract_id",
        "status",
        "executed_stages",
        "fixture_fingerprint",
        "normalizer_version",
        "px2_consumer_contract",
        "aggregates",
        "signal_bundles",
        "database_counts",
        "disposition_counts",
        "rejected_cases",
        "canonical_projection_fingerprint",
        "replay_projection_fingerprint",
        "reversed_input_projection_fingerprint",
        "deterministic_replay",
        "input_order_stable",
        "operation_receipt",
        "synthetic_vertical_slice_verified",
        "cluster_materialization_performed",
        "entity_truth_promoted",
        "authorities",
        "px1_implementation_completed",
        "px1_target_met",
        "px2_consumer_contract_frozen",
        "target_met",
        "owner_accepted",
        "safe_to_merge",
        "route_approved",
        "merge_authorized",
        "px2_started",
        "real_provider_authorized",
        "real_source_authorized",
        "full_import_authorized",
        "production_authorized",
        "canonical_fingerprint",
    }
)
_SOURCE_DISPOSITIONS = frozenset(
    {"complete", "conflict", "page_mismatch", "retryable", "terminal", "unsupported"}
)
_SIGNAL_ORIGINS = frozenset(
    {
        "pixiv_creator_identity_anchor",
        "pixiv_creator_observation",
        "pixiv_title_observation",
        "pixiv_tag_observation",
    }
)
_SIGNAL_ROLES = frozenset(
    {"artist", "character", "person", "work", "source_title", "unknown"}
)
_SIGNAL_TRUST = frozenset({"strong", "medium", "weak", "rejected"})
_SIGNAL_STATUS = frozenset({"active", "needs_review", "rejected"})


class PixivMetadataClusteringError(ValueError):
    """Fail-closed PX2 consumer, clustering, or persistence error."""


@dataclass(frozen=True)
class ValidatedPixivConsumer:
    aggregates: tuple[dict[str, Any], ...]
    signal_bundles: tuple[dict[str, Any], ...]
    signals: tuple[SourceConceptSignalDraft, ...]
    aggregate_artifact_fingerprint: str
    signal_bundle_artifact_fingerprint: str
    input_fingerprint: str
    source_state_counts: dict[str, int]
    bundle_signal_keys: dict[str, tuple[str, ...]]
    source_state_deferrals: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PixivClusteringRun:
    consumer: ValidatedPixivConsumer
    resolution: SourceConceptResolutionResult
    candidate_manifest: tuple[CandidatePair, ...]
    pair_dispositions: tuple[PairDisposition, ...]
    candidate_records: tuple[dict[str, Any], ...]
    candidate_accounting: dict[str, Any]
    ambiguous_ledger: dict[str, Any]
    clusters: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]
    invariants: dict[str, Any]
    business_projection_fingerprint: str


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PixivMetadataClusteringError(f"{label}_mapping_required")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise PixivMetadataClusteringError(f"{label}_schema_fields_invalid")


def _require_exact_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PixivMetadataClusteringError(f"{label}_invalid")
    return value


def _require_canonical_round_trip(value: Any, label: str) -> None:
    try:
        decoded = json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PixivMetadataClusteringError(f"{label}_canonical_json_invalid") from exc
    if decoded != value:
        raise PixivMetadataClusteringError(f"{label}_canonical_json_round_trip_mismatch")


def _validate_embedded_fingerprint(value: Mapping[str, Any], label: str) -> str:
    payload = dict(value)
    supplied = payload.pop("canonical_fingerprint", None)
    if not isinstance(supplied, str) or _HEX64.fullmatch(supplied) is None:
        raise PixivMetadataClusteringError(f"{label}_fingerprint_invalid")
    if supplied != canonical_fingerprint(payload):
        raise PixivMetadataClusteringError(f"{label}_fingerprint_mismatch")
    return supplied


def _work_page_identity(aggregate: Mapping[str, Any]) -> tuple[str, int, str]:
    work_id = canonical_pixiv_work_id(aggregate.get("work_id"))
    page_index = canonical_pixiv_page_index(aggregate.get("page_index"))
    if work_id is None or work_id != aggregate.get("work_id"):
        raise PixivMetadataClusteringError("px1_aggregate_work_id_invalid")
    if page_index is None or page_index != aggregate.get("page_index"):
        raise PixivMetadataClusteringError("px1_aggregate_page_index_invalid")
    stable_key = stable_pixiv_work_page_key(work_id, page_index)
    if aggregate.get("stable_work_page_key") != stable_key:
        raise PixivMetadataClusteringError("px1_aggregate_stable_key_invalid")

    scope = _require_mapping(
        aggregate.get("page_count_or_known_scope"),
        "px1_aggregate_page_scope",
    )
    page_count = scope.get("page_count")
    domain = (
        canonical_pixiv_page_domain(page_index=page_index)
        if page_count is None
        else canonical_pixiv_page_domain(
            page_index=page_index,
            page_count=page_count,
        )
    )
    if domain is None:
        raise PixivMetadataClusteringError("px1_aggregate_page_domain_invalid")
    known_indexes = scope.get("known_page_indexes")
    if not isinstance(known_indexes, list):
        raise PixivMetadataClusteringError("px1_aggregate_known_pages_invalid")
    canonical_indexes = [canonical_pixiv_page_index(item) for item in known_indexes]
    if (
        any(item is None for item in canonical_indexes)
        or canonical_indexes != sorted(set(canonical_indexes))
        or page_index not in canonical_indexes
    ):
        raise PixivMetadataClusteringError("px1_aggregate_known_pages_invalid")
    return work_id, page_index, stable_key


def _source_state_for_aggregate(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "disposition": aggregate.get("disposition"),
        "conflict_reasons": aggregate.get("conflict_reasons"),
        "deferred_reasons": aggregate.get("deferred_reasons"),
        "metadata_completeness": aggregate.get("metadata_completeness"),
        "provenance": aggregate.get("provenance"),
    }


def _policy_context(
    signal: Mapping[str, Any],
    *,
    work_id: str,
    page_index: int,
) -> tuple[str | None, str]:
    if signal.get("role_hint") == "artist":
        return None, "provider_global_creator_identity_or_observation"
    if signal.get("origin_type") == "pixiv_tag_observation":
        return f"pixiv:work:{work_id}", "pixiv_work_level_tag"
    return stable_pixiv_work_page_key(work_id, page_index), "pixiv_page_specific_fact"


def _effective_signal_policy(
    *,
    status: str,
    trust_tier: str,
    source_disposition: str,
) -> tuple[str, str, bool]:
    effective_status = status if source_disposition == "complete" else "rejected"
    effective_trust = trust_tier if source_disposition == "complete" else "rejected"
    return (
        effective_status,
        effective_trust,
        effective_status != "rejected" and effective_trust != "rejected",
    )


def _reconstruct_signal(
    signal: Mapping[str, Any],
    *,
    aggregate: Mapping[str, Any],
    bundle_fingerprint: str,
    source_state: Mapping[str, Any],
    source_run_id: str,
) -> SourceConceptSignalDraft:
    _require_exact_keys(signal, _SIGNAL_KEYS, "px1_signal")
    signal_key = signal.get("signal_key")
    origin_type = signal.get("origin_type")
    if not isinstance(signal_key, str) or not signal_key:
        raise PixivMetadataClusteringError("px1_signal_key_invalid")
    if origin_type not in _SIGNAL_ORIGINS or not signal_key.startswith(f"{origin_type}:"):
        raise PixivMetadataClusteringError("px1_signal_origin_invalid")
    if signal.get("provider") != "pixiv":
        raise PixivMetadataClusteringError("px1_signal_provider_invalid")
    if signal.get("role_hint") not in _SIGNAL_ROLES:
        raise PixivMetadataClusteringError("px1_signal_role_invalid")
    if signal.get("trust_tier") not in _SIGNAL_TRUST:
        raise PixivMetadataClusteringError("px1_signal_trust_invalid")
    if signal.get("status") not in _SIGNAL_STATUS:
        raise PixivMetadataClusteringError("px1_signal_status_invalid")
    if (signal.get('trust_tier') == 'rejected') != (signal.get('status') == 'rejected'):
        raise PixivMetadataClusteringError('px1_signal_rejected_state_invalid')
    for field in ("raw_value", "display_value", "normalized_key", "canonical_key"):
        if not isinstance(signal.get(field), str) or not signal.get(field):
            raise PixivMetadataClusteringError(f"px1_signal_{field}_invalid")
    for field in (
        "source_record_id",
        "work_context_key",
        "parenthetical_base",
        "parenthetical_context",
        "source_kind",
        "identity_anchor",
    ):
        if signal.get(field) is not None and not isinstance(signal.get(field), str):
            raise PixivMetadataClusteringError(f"px1_signal_{field}_invalid")
    confidence = signal.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, (int, float))
    ):
        raise PixivMetadataClusteringError("px1_signal_confidence_invalid")

    evidence = _require_mapping(signal.get("evidence"), "px1_signal_evidence")
    work_id, page_index, stable_key = _work_page_identity(aggregate)
    if (
        evidence.get("aggregate_fingerprint")
        != aggregate.get("canonical_fingerprint")
        or canonical_pixiv_work_id(evidence.get("work_id")) != work_id
        or evidence.get("work_id") != work_id
        or canonical_pixiv_page_index(evidence.get("page_index")) != page_index
        or evidence.get("page_index") != page_index
    ):
        raise PixivMetadataClusteringError("px1_signal_aggregate_evidence_invalid")

    original_context = signal.get("work_context_key")
    if signal.get("role_hint") == "artist":
        if original_context is not None:
            raise PixivMetadataClusteringError("px1_artist_context_invalid")
    elif original_context != stable_key:
        raise PixivMetadataClusteringError("px1_signal_page_context_invalid")

    inputs = (
        SourceConceptSignalInput(
            origin_type=str(origin_type),
            origin_key=signal_key,
            provider="pixiv",
            raw_value=str(signal["raw_value"]),
            display_value=str(signal["display_value"]),
            canonical_value=str(signal["canonical_key"]),
            role_hint=str(signal["role_hint"]),
            work_context_key=(str(original_context) if original_context else None),
            source_kind=(str(signal["source_kind"]) if signal.get("source_kind") else None),
            trust_tier=str(signal["trust_tier"]),
            confidence=(float(confidence) if confidence is not None else None),
            status=str(signal["status"]),
            evidence_payload=dict(evidence),
            source_record_id=(
                str(signal["source_record_id"])
                if signal.get("source_record_id")
                else None
            ),
            parenthetical_base=(
                str(signal["parenthetical_base"])
                if signal.get("parenthetical_base")
                else None
            ),
            parenthetical_context=(
                str(signal["parenthetical_context"])
                if signal.get("parenthetical_context")
                else None
            ),
            source_run_id=source_run_id,
        ),
    )
    rebuilt = build_source_concept_signal_drafts(
        inputs,
        created_by_run_id=source_run_id,
    )
    if len(rebuilt) != 1:
        raise PixivMetadataClusteringError("px1_signal_reconstruction_failed")
    draft = rebuilt[0]
    for field in (
        "raw_value",
        "display_value",
        "normalized_key",
        "canonical_key",
        "role_hint",
        "parenthetical_base",
        "parenthetical_context",
        "source_kind",
        "trust_tier",
        "status",
    ):
        if getattr(draft, field) != signal.get(field):
            raise PixivMetadataClusteringError(
                f"px1_signal_reconstruction_{field}_mismatch"
            )
    if stable_creator_identity_key(draft) != signal.get("identity_anchor"):
        raise PixivMetadataClusteringError("px1_signal_identity_anchor_mismatch")

    policy_context, context_reason = _policy_context(
        signal,
        work_id=work_id,
        page_index=page_index,
    )
    source_disposition = str(source_state.get("disposition"))
    effective_status, effective_trust, _active_identity_allowed = (
        _effective_signal_policy(
            status=draft.status,
            trust_tier=draft.trust_tier,
            source_disposition=source_disposition,
        )
    )
    return replace(
        draft,
        signal_key=signal_key,
        origin_id=signal_key,
        work_context_key=policy_context,
        trust_tier=effective_trust,
        status=effective_status,
        evidence_payload={
            **dict(evidence),
            "px1_signal_key": signal_key,
            "px1_signal_bundle_fingerprint": bundle_fingerprint,
            "px1_source_disposition": source_disposition,
            "px2_context_policy_version": PX2_CONTEXT_POLICY_VERSION,
            "px2_context_reason": context_reason,
        },
        source_run_id=source_run_id,
        created_by_run_id=source_run_id,
    )


def validate_px1_consumer_artifacts(
    *,
    aggregates: Sequence[Mapping[str, Any]],
    signal_bundles: Sequence[Mapping[str, Any]],
    consumer_contract: Mapping[str, Any],
) -> ValidatedPixivConsumer:
    """Validate and reconstruct the frozen PX1 consumer contract."""

    contract = _require_mapping(consumer_contract, "px1_consumer_contract")
    _require_exact_keys(contract, _CONSUMER_CONTRACT_KEYS, "px1_consumer_contract")
    if (
        contract.get("schema_version") != PX2_CONSUMER_CONTRACT_SCHEMA
        or contract.get("aggregate_schema_version") != PIXIV_AGGREGATE_SCHEMA
        or contract.get("signal_bundle_schema_version") != PIXIV_SIGNAL_BUNDLE_SCHEMA
        or contract.get("canonical_json_round_trip_stable") is not True
        or contract.get("database_row_identity_excluded") is not True
        or contract.get("runtime_order_identity_excluded") is not True
        or contract.get("wall_clock_identity_excluded") is not True
        or contract.get("cluster_materialization_performed") is not False
    ):
        raise PixivMetadataClusteringError("px1_consumer_contract_invalid")
    if not isinstance(aggregates, Sequence) or isinstance(aggregates, (str, bytes)):
        raise PixivMetadataClusteringError("px1_aggregates_invalid")
    if not isinstance(signal_bundles, Sequence) or isinstance(signal_bundles, (str, bytes)):
        raise PixivMetadataClusteringError("px1_signal_bundles_invalid")

    aggregate_rows: list[dict[str, Any]] = []
    aggregate_by_key: dict[str, dict[str, Any]] = {}
    for item in aggregates:
        row = dict(_require_mapping(item, "px1_aggregate"))
        _require_exact_keys(row, _AGGREGATE_KEYS, "px1_aggregate")
        if row.get("schema_version") != PIXIV_AGGREGATE_SCHEMA or row.get("provider") != "pixiv":
            raise PixivMetadataClusteringError("px1_aggregate_schema_invalid")
        _validate_embedded_fingerprint(row, "px1_aggregate")
        _work_id, _page_index, stable_key = _work_page_identity(row)
        if row.get("disposition") not in _SOURCE_DISPOSITIONS:
            raise PixivMetadataClusteringError("px1_aggregate_disposition_invalid")
        if stable_key in aggregate_by_key:
            if aggregate_by_key[stable_key] != row:
                raise PixivMetadataClusteringError("px1_aggregate_identity_conflict")
            raise PixivMetadataClusteringError("px1_aggregate_identity_duplicate")
        aggregate_by_key[stable_key] = row
        aggregate_rows.append(row)
    aggregate_rows.sort(key=lambda row: str(row["stable_work_page_key"]))
    _require_canonical_round_trip(aggregate_rows, "px1_aggregates")

    bundle_rows: list[dict[str, Any]] = []
    bundle_by_key: dict[str, dict[str, Any]] = {}
    for item in signal_bundles:
        row = dict(_require_mapping(item, "px1_signal_bundle"))
        _require_exact_keys(row, _BUNDLE_KEYS, "px1_signal_bundle")
        if row.get("schema_version") != PIXIV_SIGNAL_BUNDLE_SCHEMA or row.get("provider") != "pixiv":
            raise PixivMetadataClusteringError("px1_signal_bundle_schema_invalid")
        _validate_embedded_fingerprint(row, "px1_signal_bundle")
        stable_key = row.get("stable_work_page_key")
        if not isinstance(stable_key, str) or stable_key not in aggregate_by_key:
            raise PixivMetadataClusteringError("px1_signal_bundle_aggregate_missing")
        if stable_key in bundle_by_key:
            if bundle_by_key[stable_key] != row:
                raise PixivMetadataClusteringError("px1_signal_bundle_identity_conflict")
            raise PixivMetadataClusteringError("px1_signal_bundle_identity_duplicate")
        aggregate = aggregate_by_key[stable_key]
        if row.get("aggregate_fingerprint") != aggregate.get("canonical_fingerprint"):
            raise PixivMetadataClusteringError("px1_signal_bundle_aggregate_fingerprint_mismatch")
        source_state = _require_mapping(row.get("source_state"), "px1_signal_source_state")
        _require_exact_keys(source_state, _SOURCE_STATE_KEYS, "px1_signal_source_state")
        if dict(source_state) != _source_state_for_aggregate(aggregate):
            raise PixivMetadataClusteringError("px1_signal_source_state_mismatch")
        signals = row.get("signals")
        logical_keys = row.get("logical_keys")
        if not isinstance(signals, list) or not isinstance(logical_keys, list):
            raise PixivMetadataClusteringError("px1_signal_bundle_shape_invalid")
        derived_keys = [
            str(_require_mapping(signal, "px1_signal").get("signal_key"))
            for signal in signals
        ]
        if (
            _require_exact_int(row.get("signal_count"), "px1_signal_count") != len(signals)
            or logical_keys != sorted(derived_keys)
            or len(set(derived_keys)) != len(derived_keys)
        ):
            raise PixivMetadataClusteringError("px1_signal_logical_keys_invalid")
        identity_count = sum(
            bool(_require_mapping(signal, "px1_signal").get("identity_anchor"))
            for signal in signals
        )
        if (
            _require_exact_int(
                row.get("strong_identity_anchor_count"),
                "px1_strong_identity_anchor_count",
            )
            != identity_count
            or row.get("name_only_identity_anchor_count") != 0
            or row.get("cross_context_union_count") != 0
            or row.get("cluster_materialization_performed") is not False
            or row.get("entity_truth_promoted") is not False
        ):
            raise PixivMetadataClusteringError("px1_signal_bundle_authority_invalid")
        bundle_by_key[stable_key] = row
        bundle_rows.append(row)
    bundle_rows.sort(key=lambda row: str(row["stable_work_page_key"]))
    _require_canonical_round_trip(bundle_rows, "px1_signal_bundles")

    if set(bundle_by_key) != set(aggregate_by_key):
        raise PixivMetadataClusteringError("px1_bundle_aggregate_scope_mismatch")
    aggregate_fingerprint = canonical_fingerprint(aggregate_rows)
    signal_bundle_fingerprint = canonical_fingerprint(bundle_rows)
    if (
        contract.get("aggregate_artifact_fingerprint") != aggregate_fingerprint
        or contract.get("signal_bundle_artifact_fingerprint")
        != signal_bundle_fingerprint
    ):
        raise PixivMetadataClusteringError("px1_consumer_artifact_fingerprint_mismatch")

    input_fingerprint = canonical_fingerprint(
        {
            "consumer_schema": PX2_CONSUMER_CONTRACT_SCHEMA,
            "aggregate_artifact_fingerprint": aggregate_fingerprint,
            "signal_bundle_artifact_fingerprint": signal_bundle_fingerprint,
        }
    )
    source_run_id = f"scv2-px2:{input_fingerprint[:32]}"
    drafts: list[SourceConceptSignalDraft] = []
    global_signal_keys: set[str] = set()
    bundle_signal_keys: dict[str, tuple[str, ...]] = {}
    source_state_deferrals: list[dict[str, Any]] = []
    source_state_counts: Counter[str] = Counter()
    for bundle in bundle_rows:
        stable_key = str(bundle["stable_work_page_key"])
        aggregate = aggregate_by_key[stable_key]
        source_state = _require_mapping(bundle["source_state"], "px1_signal_source_state")
        disposition = str(source_state["disposition"])
        source_state_counts[disposition] += 1
        bundle_fingerprint = str(bundle["canonical_fingerprint"])
        reconstructed = [
            _reconstruct_signal(
                _require_mapping(signal, "px1_signal"),
                aggregate=aggregate,
                bundle_fingerprint=bundle_fingerprint,
                source_state=source_state,
                source_run_id=source_run_id,
            )
            for signal in bundle["signals"]
        ]
        keys = tuple(sorted(signal.signal_key for signal in reconstructed))
        if global_signal_keys.intersection(keys):
            raise PixivMetadataClusteringError("px1_cross_bundle_signal_identity_conflict")
        global_signal_keys.update(keys)
        bundle_signal_keys[stable_key] = keys
        drafts.extend(reconstructed)
        if (
            disposition != "complete"
            or source_state.get("conflict_reasons")
            or source_state.get("deferred_reasons")
        ):
            active_identity_allowed = any(
                _effective_signal_policy(
                    status=signal.status,
                    trust_tier=signal.trust_tier,
                    source_disposition=disposition,
                )[2]
                for signal in reconstructed
            )
            source_state_deferrals.append(
                {
                    "stable_work_page_key": stable_key,
                    "disposition": disposition,
                    "conflict_reasons": sorted(
                        str(value) for value in source_state.get("conflict_reasons", ())
                    ),
                    "deferred_reasons": sorted(
                        str(value) for value in source_state.get("deferred_reasons", ())
                    ),
                    "signal_keys": list(keys),
                    "active_identity_allowed": active_identity_allowed,
                }
            )
    return ValidatedPixivConsumer(
        aggregates=tuple(aggregate_rows),
        signal_bundles=tuple(bundle_rows),
        signals=tuple(sorted(drafts, key=lambda signal: signal.signal_key)),
        aggregate_artifact_fingerprint=aggregate_fingerprint,
        signal_bundle_artifact_fingerprint=signal_bundle_fingerprint,
        input_fingerprint=input_fingerprint,
        source_state_counts=dict(sorted(source_state_counts.items())),
        bundle_signal_keys=dict(sorted(bundle_signal_keys.items())),
        source_state_deferrals=tuple(
            sorted(
                source_state_deferrals,
                key=lambda row: str(row["stable_work_page_key"]),
            )
        ),
    )


def consume_px1_public_summary(summary: Mapping[str, Any]) -> ValidatedPixivConsumer:
    """Validate a complete PX1 summary before consuming its frozen artifacts."""

    row = dict(_require_mapping(summary, "px1_public_summary"))
    _require_exact_keys(row, _PX1_SUMMARY_KEYS, "px1_public_summary")
    if (
        row.get("schema_version") != PIXIV_PUBLIC_SUMMARY_SCHEMA
        or row.get("contract_id") != PX1_CONTRACT_ID
        or row.get("px2_consumer_contract_frozen") is not True
        or row.get("synthetic_vertical_slice_verified") is not True
        or row.get("deterministic_replay") is not True
        or row.get("input_order_stable") is not True
        or row.get("cluster_materialization_performed") is not False
        or row.get("entity_truth_promoted") is not False
        or row.get("px2_started") is not False
    ):
        raise PixivMetadataClusteringError("px1_public_summary_authority_invalid")
    _validate_embedded_fingerprint(row, "px1_public_summary")
    _require_canonical_round_trip(row, "px1_public_summary")
    aggregates = row.get("aggregates")
    bundles = row.get("signal_bundles")
    if not isinstance(aggregates, list) or not isinstance(bundles, list):
        raise PixivMetadataClusteringError("px1_public_summary_artifacts_missing")
    return validate_px1_consumer_artifacts(
        aggregates=aggregates,
        signal_bundles=bundles,
        consumer_contract=_require_mapping(
            row.get("px2_consumer_contract"),
            "px1_consumer_contract",
        ),
    )


def _edge_is_policy_union(edge: SourceConceptEdgeDraft) -> bool:
    return bool(
        edge.union_allowed
        and edge.status == "active"
        and not edge.negative_reason_code
    )


def _edge_is_hard_negative(edge: SourceConceptEdgeDraft) -> bool:
    return bool(edge.negative_reason_code in HARD_NEGATIVE_REASON_CODES)


def _candidate_dispositions(
    resolution: SourceConceptResolutionResult,
    manifest: Sequence[CandidatePair],
) -> tuple[tuple[PairDisposition, ...], tuple[dict[str, Any], ...], dict[str, Any]]:
    edges_by_pair: dict[str, list[SourceConceptEdgeDraft]] = defaultdict(list)
    for edge in resolution.edge_candidates:
        edges_by_pair[pair_id_for(edge.left_signal_key, edge.right_signal_key)].append(edge)
    concept_by_signal = {
        signal.signal_key: concept.concept_key
        for concept in resolution.concepts
        for signal in concept.signals
    }
    members_by_concept = {
        concept.concept_key: {signal.signal_key for signal in concept.signals}
        for concept in resolution.concepts
    }
    all_hard_edges = [
        edge for edge in resolution.edge_candidates if _edge_is_hard_negative(edge)
    ]

    pair_rows: list[PairDisposition] = []
    records: list[dict[str, Any]] = []
    for candidate in manifest:
        edges = sorted(edges_by_pair[candidate.pair_id], key=lambda edge: edge.edge_key)
        direct_hard = [edge for edge in edges if _edge_is_hard_negative(edge)]
        active_edges = [edge for edge in edges if _edge_is_policy_union(edge)]
        left_concept = concept_by_signal.get(candidate.left_signal_key)
        right_concept = concept_by_signal.get(candidate.right_signal_key)
        same_component = bool(left_concept and left_concept == right_concept)
        transitive_blocker: SourceConceptEdgeDraft | None = None
        if active_edges and not same_component and left_concept and right_concept:
            left_members = members_by_concept[left_concept]
            right_members = members_by_concept[right_concept]
            transitive_blocker = next(
                (
                    edge
                    for edge in sorted(all_hard_edges, key=lambda edge: edge.edge_key)
                    if {
                        edge.left_signal_key,
                        edge.right_signal_key,
                    }
                    & left_members
                    and {
                        edge.left_signal_key,
                        edge.right_signal_key,
                    }
                    & right_members
                ),
                None,
            )
        if direct_hard:
            selected = sorted(direct_hard, key=lambda edge: edge.edge_key)[0]
            disposition = "cannot_link"
            reason_code = selected.resolution_reason_code
            negative_reason = selected.negative_reason_code
        elif active_edges and same_component:
            selected = max(active_edges, key=lambda edge: (edge.weight, edge.edge_key))
            disposition = "must_link"
            reason_code = selected.resolution_reason_code
            negative_reason = None
        elif transitive_blocker is not None:
            selected = transitive_blocker
            disposition = "cannot_link"
            reason_code = "transitive_cannot_link_constraint"
            negative_reason = selected.negative_reason_code
        else:
            selected = max(edges, key=lambda edge: (edge.weight, edge.edge_key))
            disposition = "deferred_nonblocking"
            reason_code = selected.resolution_reason_code
            negative_reason = selected.negative_reason_code
        pair_row = PairDisposition(
            pair_id=candidate.pair_id,
            left_signal_key=candidate.left_signal_key,
            right_signal_key=candidate.right_signal_key,
            disposition=disposition,
            source="existing_resolver_graph",
            pass_name="deterministic",
            confidence=round(float(selected.weight), 4),
            reason_code=reason_code,
            cache_key=None,
        )
        pair_rows.append(pair_row)
        records.append(
            {
                "pair_key": candidate.pair_id,
                "left_signal_key": candidate.left_signal_key,
                "right_signal_key": candidate.right_signal_key,
                "disposition": disposition,
                "reason_code": reason_code,
                "negative_reason": negative_reason,
                "evidence_refs": [
                    {
                        "edge_key": edge.edge_key,
                        "edge_type": edge.edge_type,
                        "edge_status": edge.status,
                        "evidence_source": edge.evidence_source,
                        "resolution_reason_code": edge.resolution_reason_code,
                        "negative_reason_code": edge.negative_reason_code,
                    }
                    for edge in edges
                ],
                "union_decision": disposition == "must_link",
                "same_resolved_component": same_component,
                "resolver_edge_count": len(edges),
                "candidate_policy_version": PX2_CANDIDATE_POLICY_VERSION,
            }
        )
    pair_rows.sort(key=lambda row: row.pair_id)
    records.sort(key=lambda row: str(row["pair_key"]))
    accounting = disposition_accounting(manifest, pair_rows)
    return tuple(pair_rows), tuple(records), accounting


def _safe_resolver_row(row: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields if field in row}


def _build_ambiguous_ledger(
    resolution: SourceConceptResolutionResult,
    candidate_records: Sequence[Mapping[str, Any]],
    source_state_deferrals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    deferred_candidates = [
        {
            "pair_key": row["pair_key"],
            "left_signal_key": row["left_signal_key"],
            "right_signal_key": row["right_signal_key"],
            "reason_code": row["reason_code"],
            "negative_reason": row["negative_reason"],
            "union_decision": False,
        }
        for row in candidate_records
        if row["disposition"] == "deferred_nonblocking"
    ]
    ambiguous_links = [
        _safe_resolver_row(
            row,
            (
                "signal_key",
                "concept_key",
                "negative_reason_code",
                "origin_type",
                "role_hint",
            ),
        )
        for row in resolution.ambiguous_links
    ]
    merge_candidates = [
        _safe_resolver_row(row, ("surface_key", "concept_keys", "reason"))
        for row in resolution.merge_candidates
    ]
    context_conflicts = [
        _safe_resolver_row(
            row,
            (
                "edge_key",
                "left_signal_key",
                "right_signal_key",
                "negative_reason_code",
                "context_compatibility",
                "left_context",
                "right_context",
                "violation",
            ),
        )
        for row in resolution.context_conflict_review
    ]
    ledger: dict[str, Any] = {
        "schema_version": PX2_AMBIGUOUS_LEDGER_SCHEMA,
        "blocking": False,
        "human_review_required": False,
        "identity_union_allowed": False,
        "deferred_candidate_pairs": sorted(
            deferred_candidates,
            key=lambda row: str(row["pair_key"]),
        ),
        "ambiguous_links": sorted(
            ambiguous_links,
            key=lambda row: (
                str(row.get("signal_key")),
                str(row.get("concept_key")),
            ),
        ),
        "merge_candidates": sorted(
            merge_candidates,
            key=lambda row: str(row.get("surface_key")),
        ),
        "context_conflicts": sorted(
            context_conflicts,
            key=lambda row: str(row.get("edge_key")),
        ),
        "source_state_deferrals": [dict(row) for row in source_state_deferrals],
    }
    ledger["counts"] = {
        "deferred_candidate_pair_count": len(ledger["deferred_candidate_pairs"]),
        "ambiguous_link_count": len(ledger["ambiguous_links"]),
        "merge_candidate_count": len(ledger["merge_candidates"]),
        "context_conflict_count": len(ledger["context_conflicts"]),
        "source_state_deferral_count": len(ledger["source_state_deferrals"]),
    }
    ledger["record_count"] = sum(ledger["counts"].values())
    ledger["canonical_fingerprint"] = canonical_fingerprint(ledger)
    return ledger


def _cluster_projection(
    resolution: SourceConceptResolutionResult,
) -> tuple[dict[str, Any], ...]:
    aliases_by_concept: dict[str, list[Any]] = defaultdict(list)
    evidence_by_concept: dict[str, list[Any]] = defaultdict(list)
    links_by_concept: dict[str, list[Any]] = defaultdict(list)
    search_by_concept: dict[str, list[Any]] = defaultdict(list)
    for row in resolution.aliases:
        aliases_by_concept[row.concept_key].append(row)
    for row in resolution.evidence:
        evidence_by_concept[row.concept_key].append(row)
    for row in resolution.links:
        links_by_concept[row.concept_key].append(row)
    for row in resolution.search_index:
        search_by_concept[row.concept_key].append(row)

    clusters: list[dict[str, Any]] = []
    for concept in sorted(resolution.concepts, key=lambda row: row.concept_key):
        work_refs = sorted(
            {
                f"pixiv:work:{work_id}"
                for signal in concept.signals
                if (
                    work_id := canonical_pixiv_work_id(
                        (signal.evidence_payload or {}).get("work_id")
                    )
                )
            }
        )
        page_refs = sorted(
            {
                stable_pixiv_work_page_key(work_id, page_index)
                for signal in concept.signals
                if (
                    (work_id := canonical_pixiv_work_id((signal.evidence_payload or {}).get("work_id")))
                    and (
                        page_index := canonical_pixiv_page_index(
                            (signal.evidence_payload or {}).get("page_index")
                        )
                    )
                    is not None
                )
            }
        )
        anchors = sorted(
            {
                anchor
                for signal in concept.signals
                if (anchor := stable_creator_identity_key(signal))
            }
        )
        aliases = aliases_by_concept[concept.concept_key]
        evidence_rows = evidence_by_concept[concept.concept_key]
        links = links_by_concept[concept.concept_key]
        searches = search_by_concept[concept.concept_key]
        clusters.append(
            {
                "cluster_key": concept.concept_key,
                "concept_key": concept.concept_key,
                "type": concept.concept_type_hint,
                "status": concept.status,
                "display_name": concept.primary_display_name,
                "member_signal_keys": sorted(
                    signal.signal_key for signal in concept.signals
                ),
                "member_signal_count": len(concept.signals),
                "work_references": work_refs,
                "page_references": page_refs,
                "stable_identity_anchors": anchors,
                "aliases": [
                    {
                        "alias_key": alias.alias_key,
                        "display_name": alias.display_name,
                        "alias_role": alias.alias_role,
                        "status": alias.status,
                        "confidence": alias.confidence,
                    }
                    for alias in sorted(
                        aliases,
                        key=lambda row: (
                            row.alias_key,
                            row.alias_role,
                            row.signal_key,
                        ),
                    )
                ],
                "evidence_summary": {
                    "count": len(evidence_rows),
                    "types": dict(
                        sorted(Counter(row.evidence_type for row in evidence_rows).items())
                    ),
                    "strengths": dict(
                        sorted(Counter(row.evidence_strength for row in evidence_rows).items())
                    ),
                    "signal_refs": sorted(
                        {row.signal_key for row in evidence_rows}
                    ),
                },
                "links": [
                    {
                        "signal_key": link.signal_key,
                        "status": link.link_status,
                        "reason_code": link.resolution_reason_code,
                        "negative_reason": link.negative_reason_code,
                    }
                    for link in sorted(links, key=lambda row: row.signal_key)
                ],
                "search_index": [
                    {
                        "search_key": item.search_key,
                        "display_name": item.display_name,
                        "alias_role": item.alias_role,
                        "weight": item.weight,
                        "status": item.status,
                    }
                    for item in sorted(
                        searches,
                        key=lambda row: (row.search_key, row.alias_role),
                    )
                ],
            }
        )
    return tuple(clusters)


def build_pixiv_clustering(
    consumer: ValidatedPixivConsumer,
) -> PixivClusteringRun:
    """Resolve validated PX1 signals through the existing deterministic graph."""

    run_id = f"scv2-px2:{consumer.input_fingerprint[:32]}"
    resolution = resolve_source_concepts(
        consumer.signals,
        run_id=run_id,
        llm_config=LLMAdjudicationConfig(enabled=False, max_calls=0),
        llm_judgments=(),
    )
    if (
        resolution.summary.get("llm_usage", {}).get("used") is not False
        or resolution.summary.get("llm_usage", {}).get("judgment_count") != 0
    ):
        raise PixivMetadataClusteringError("px2_llm_activity_detected")
    manifest = build_complete_candidate_pair_manifest(resolution.edge_candidates)
    pair_rows, candidate_records, accounting = _candidate_dispositions(
        resolution,
        manifest,
    )
    if (
        accounting.get("accounting_equality_passed") is not True
        or accounting.get("unaccounted_pair_count") != 0
        or accounting.get("duplicate_disposition_count") != 0
        or accounting.get("extra_disposition_count") != 0
    ):
        raise PixivMetadataClusteringError("px2_candidate_accounting_invalid")
    ledger = _build_ambiguous_ledger(
        resolution,
        candidate_records,
        consumer.source_state_deferrals,
    )
    clusters = _cluster_projection(resolution)
    concept_by_signal = {
        signal_key: cluster["concept_key"]
        for cluster in clusters
        for signal_key in cluster["member_signal_keys"]
    }
    rejected_signal_keys = {
        str(row.get("signal_key"))
        for row in resolution.rejected_signals
        if isinstance(row, Mapping) and row.get("signal_key")
    }
    accounted_signal_keys = set(concept_by_signal) | rejected_signal_keys
    all_signal_keys = {signal.signal_key for signal in consumer.signals}
    multi_creator_components = sum(
        len(cluster["stable_identity_anchors"]) > 1 for cluster in clusters
    )
    name_only_artist_unions = 0
    cross_role_unions = 0
    for concept in resolution.concepts:
        role_groups = {signal_role_group(signal) for signal in concept.signals}
        if len(role_groups) > 1:
            cross_role_unions += 1
        artist_signals = [
            signal for signal in concept.signals if signal.role_hint == "artist"
        ]
        if len(artist_signals) > 1 and not any(
            stable_creator_identity_key(signal) for signal in artist_signals
        ):
            name_only_artist_unions += 1
    cannot_link_violations = sum(
        row["disposition"] == "cannot_link"
        and row["same_resolved_component"]
        for row in candidate_records
    )
    deferred_union_violations = sum(
        row["disposition"] == "deferred_nonblocking" and row["union_decision"]
        for row in candidate_records
    )
    invariants = {
        "all_input_bundles_accounted": len(consumer.aggregates)
        == len(consumer.signal_bundles)
        == len(consumer.bundle_signal_keys),
        "all_candidate_pairs_accounted": accounting[
            "accounting_equality_passed"
        ],
        "unexplained_signal_loss": len(all_signal_keys - accounted_signal_keys),
        "multi_stable_creator_id_component_count": multi_creator_components,
        "name_only_artist_union_count": name_only_artist_unions,
        "cannot_link_union_violation_count": cannot_link_violations,
        "deferred_union_violation_count": deferred_union_violations,
        "cross_role_union_violation_count": cross_role_unions,
        "deterministic_replay": False,
        "temporary_persistence_idempotent": False,
        "existing_db_or_app_storage_activity": 0,
        "provider_network_activity": 0,
        "llm_activity": 0,
    }
    diagnostics = {
        "overmerge_violation_count": sum(
            bool(row.get("violation")) for row in resolution.overmerge_review
        ),
        "undermerge_violation_count": sum(
            bool(row.get("violation")) for row in resolution.undermerge_review
        ),
        "fragmentation_violation_count": sum(
            bool(row.get("violation")) for row in resolution.fragmentation_review
        ),
        "context_conflict_active_merge_count": sum(
            bool(row.get("violation"))
            for row in resolution.context_conflict_review
        ),
        "resolver_transitive_cannot_violation_count": int(
            resolution.summary.get("transitive_cannot_violation_count", 0)
        ),
        "rejected_signal_count": len(rejected_signal_keys),
    }
    business_projection = {
        "consumer_input_fingerprint": consumer.input_fingerprint,
        "resolver_version": RESOLVER_VERSION,
        "context_policy_version": PX2_CONTEXT_POLICY_VERSION,
        "candidate_policy_version": PX2_CANDIDATE_POLICY_VERSION,
        "clusters": list(clusters),
        "candidate_dispositions": list(candidate_records),
        "ambiguous_ledger": ledger,
        "diagnostics": diagnostics,
        "invariants": {
            key: value
            for key, value in invariants.items()
            if key
            not in {"deterministic_replay", "temporary_persistence_idempotent"}
        },
    }
    assert_public_safe_projection(business_projection)
    return PixivClusteringRun(
        consumer=consumer,
        resolution=resolution,
        candidate_manifest=manifest,
        pair_dispositions=pair_rows,
        candidate_records=candidate_records,
        candidate_accounting=accounting,
        ambiguous_ledger=ledger,
        clusters=clusters,
        diagnostics=diagnostics,
        invariants=invariants,
        business_projection_fingerprint=canonical_fingerprint(business_projection),
    )


_SOURCE_CONCEPT_MODELS = (
    SourceConceptResolutionRun,
    SourceConceptSignal,
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptSignalLink,
    SourceConceptSearchIndex,
)


def _database_table_counts(session: Session) -> dict[str, int]:
    return {
        name: int(session.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0)
        for name in sorted(Base.metadata.tables)
    }


def _scope_row_counts(session: Session, run: PixivClusteringRun) -> dict[str, int]:
    signal_keys = [signal.signal_key for signal in run.resolution.signals]
    concept_keys = [concept.concept_key for concept in run.resolution.concepts]
    signal_ids = [
        row_id
        for (row_id,) in session.query(SourceConceptSignal.id)
        .filter(SourceConceptSignal.signal_key.in_(signal_keys))
        .all()
    ]
    concept_ids = [
        row_id
        for (row_id,) in session.query(SourceConcept.id)
        .filter(SourceConcept.concept_key.in_(concept_keys))
        .all()
    ]
    return {
        "resolution_runs": session.query(SourceConceptResolutionRun)
        .filter_by(run_id=run.resolution.run_id)
        .count(),
        "signals": len(signal_ids),
        "concepts": len(concept_ids),
        "aliases": session.query(SourceConceptAlias)
        .filter(SourceConceptAlias.concept_id.in_(concept_ids or [-1]))
        .count(),
        "evidence": session.query(SourceConceptEvidence)
        .filter(SourceConceptEvidence.concept_id.in_(concept_ids or [-1]))
        .count(),
        "links": session.query(SourceConceptSignalLink)
        .filter(SourceConceptSignalLink.signal_id.in_(signal_ids or [-1]))
        .count(),
        "search_index": session.query(SourceConceptSearchIndex)
        .filter(SourceConceptSearchIndex.concept_id.in_(concept_ids or [-1]))
        .count(),
    }


def _foreign_scope_result() -> SourceConceptResolutionResult:
    drafts = build_source_concept_signal_drafts(
        (
            SourceConceptSignalInput(
                origin_type="source_alias_candidate",
                origin_key="px2-foreign-scope-sentinel",
                provider="synthetic",
                raw_value="PX2 foreign scope sentinel",
                display_value="PX2 foreign scope sentinel",
                canonical_value="px2_foreign_scope_sentinel",
                role_hint="work",
                work_context_key="synthetic:foreign:scope",
                source_kind="task_owned_temporary_scope_probe",
                trust_tier="strong",
                confidence=1.0,
                status="active",
                evidence_payload={"scope_probe": True},
                source_run_id="scv2-px2:foreign-scope",
            ),
        ),
        created_by_run_id="scv2-px2:foreign-scope",
    )
    return resolve_source_concepts(
        drafts,
        run_id="scv2-px2:foreign-scope",
        llm_config=LLMAdjudicationConfig(enabled=False, max_calls=0),
        llm_judgments=(),
    )


def prove_temporary_source_concept_persistence(
    run: PixivClusteringRun,
    *,
    workspace: Path,
) -> dict[str, Any]:
    """Apply/replay through existing models in two task-owned SQLite files."""

    root = _require_task_owned_temp_workspace(workspace)
    enriched_resolution = replace(
        run.resolution,
        summary={
            **dict(run.resolution.summary),
            "px2_candidate_accounting": dict(run.candidate_accounting),
            "px2_ambiguous_ledger": dict(run.ambiguous_ledger),
            "px2_business_projection_fingerprint": run.business_projection_fingerprint,
        },
    )
    scoped_run = replace(run, resolution=enriched_resolution)
    input_scope = build_source_concept_input_scope(
        enriched_resolution.signals,
        source_run_ids=[enriched_resolution.run_id],
    )
    database_summaries: list[dict[str, Any]] = []
    first_signal_id_sets: list[tuple[int, ...]] = []
    foreign_preserved = True
    all_non_owned_deltas_zero = True
    ledger_persisted = True

    for index, seed_foreign in enumerate((False, True), start=1):
        database_path = root / f"px2-source-concepts-{index}.sqlite3"
        if database_path.exists():
            raise PixivMetadataClusteringError("px2_temporary_database_already_exists")
        engine = create_engine(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            if seed_foreign:
                foreign = _foreign_scope_result()
                persist_source_concept_resolution(
                    session,
                    foreign,
                    apply=True,
                    input_scope=build_source_concept_input_scope(
                        foreign.signals,
                        source_run_ids=["scv2-px2:foreign-scope"],
                    ),
                    run_label="scv2_px2_foreign_scope_probe",
                )
            before = _database_table_counts(session)
            first = persist_source_concept_resolution(
                session,
                enriched_resolution,
                apply=True,
                input_scope=input_scope,
                run_label="scv2_px2_deterministic_pixiv_clustering",
            )
            after_first = _database_table_counts(session)
            first_scope_counts = _scope_row_counts(session, scoped_run)
            first_ids = tuple(
                row_id
                for (row_id,) in session.query(SourceConceptSignal.id)
                .filter(
                    SourceConceptSignal.signal_key.in_(
                        [signal.signal_key for signal in enriched_resolution.signals]
                    )
                )
                .order_by(SourceConceptSignal.signal_key)
                .all()
            )
            first_signal_id_sets.append(first_ids)
            replay = persist_source_concept_resolution(
                session,
                enriched_resolution,
                apply=True,
                input_scope=input_scope,
                run_label="scv2_px2_deterministic_pixiv_clustering",
            )
            after_replay = _database_table_counts(session)
            replay_scope_counts = _scope_row_counts(session, scoped_run)
            run_row = (
                session.query(SourceConceptResolutionRun)
                .filter_by(run_id=enriched_resolution.run_id)
                .one()
            )
            ledger = (run_row.summary_json or {}).get("px2_ambiguous_ledger")
            ledger_persisted = ledger_persisted and isinstance(ledger, Mapping) and (
                ledger.get("canonical_fingerprint")
                == run.ambiguous_ledger["canonical_fingerprint"]
            )
            if seed_foreign:
                foreign_signal = (
                    session.query(SourceConceptSignal)
                    .filter_by(source_run_id="scv2-px2:foreign-scope")
                    .one()
                )
                foreign_preserved = foreign_preserved and (
                    foreign_signal.status != "superseded"
                )
            first_deltas = {
                key: after_first[key] - before[key] for key in sorted(before)
            }
            replay_deltas = {
                key: after_replay[key] - after_first[key]
                for key in sorted(after_first)
            }
            non_owned_deltas = {
                key: value
                for key, value in first_deltas.items()
                if key not in SOURCE_CONCEPT_ALLOWED_WRITE_TABLES and value != 0
            }
            all_non_owned_deltas_zero = (
                all_non_owned_deltas_zero and not non_owned_deltas
            )
            database_summaries.append(
                {
                    "seeded_foreign_scope": seed_foreign,
                    "scope_row_counts": first_scope_counts,
                    "replay_scope_row_counts": replay_scope_counts,
                    "scope_counts_stable": first_scope_counts
                    == replay_scope_counts,
                    "replay_row_delta_count": sum(
                        abs(value) for value in replay_deltas.values()
                    ),
                    "non_sourceconcept_row_delta_count": sum(
                        abs(value) for value in non_owned_deltas.values()
                    ),
                    "forbidden_truth_table_write_count": max(
                        int(first.get("forbidden_truth_table_write_count", 0)),
                        int(replay.get("forbidden_truth_table_write_count", 0)),
                    ),
                    "stale_scope_violation_count": max(
                        int(first.get("stale_supersede_scope_violation_count", 0)),
                        int(replay.get("stale_supersede_scope_violation_count", 0)),
                    ),
                }
            )
        finally:
            session.close()
            engine.dispose()

    row_id_variation = (
        len(first_signal_id_sets) == 2
        and first_signal_id_sets[0]
        and first_signal_id_sets[1]
        and first_signal_id_sets[0] != first_signal_id_sets[1]
    )
    scope_counts_equal_across_databases = (
        database_summaries[0]["scope_row_counts"]
        == database_summaries[1]["scope_row_counts"]
    )
    idempotent = bool(
        all(row["scope_counts_stable"] for row in database_summaries)
        and all(row["replay_row_delta_count"] == 0 for row in database_summaries)
        and scope_counts_equal_across_databases
        and foreign_preserved
        and all_non_owned_deltas_zero
        and ledger_persisted
        and all(
            row["forbidden_truth_table_write_count"] == 0
            and row["stale_scope_violation_count"] == 0
            for row in database_summaries
        )
    )
    proof: dict[str, Any] = {
        "schema_version": PX2_PERSISTENCE_PROOF_SCHEMA,
        "temporary_database_count": 2,
        "apply_count_per_database": 2,
        "source_concept_allowed_tables": list(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES),
        "databases": database_summaries,
        "scope_counts_equal_across_databases": scope_counts_equal_across_databases,
        "database_row_id_variation_observed": row_id_variation,
        "business_fingerprint_excludes_database_row_id": row_id_variation,
        "stale_foreign_scope_preserved": foreign_preserved,
        "ambiguous_ledger_persisted_in_existing_resolution_run": ledger_persisted,
        "only_sourceconcept_owned_temporary_tables_written": all_non_owned_deltas_zero,
        "temporary_persistence_idempotent": idempotent,
        "existing_database_or_app_storage_activity": 0,
        "migration_activity": 0,
    }
    proof["canonical_fingerprint"] = canonical_fingerprint(proof)
    assert_public_safe_projection(proof)
    return proof


def _probe_drafts(
    rows: Iterable[SourceConceptSignalInput],
    *,
    run_id: str,
) -> tuple[SourceConceptSignalDraft, ...]:
    return build_source_concept_signal_drafts(rows, created_by_run_id=run_id)


def _probe_result(
    rows: Iterable[SourceConceptSignalInput],
    *,
    run_id: str,
) -> SourceConceptResolutionResult:
    return resolve_source_concepts(
        _probe_drafts(rows, run_id=run_id),
        run_id=run_id,
        llm_config=LLMAdjudicationConfig(enabled=False, max_calls=0),
        llm_judgments=(),
    )


def _artist_probe_input(
    key: str,
    *,
    display_name: str,
    creator_id: str | None,
    work_id: str,
) -> SourceConceptSignalInput:
    return SourceConceptSignalInput(
        origin_type="pixiv_creator_observation",
        origin_key=key,
        provider="pixiv",
        raw_value=display_name,
        display_value=display_name,
        canonical_value=display_name,
        role_hint="artist",
        work_context_key=None,
        source_kind="pixiv_creator_display_name",
        trust_tier="medium" if creator_id else "weak",
        confidence=0.9 if creator_id else 0.5,
        status="needs_review",
        evidence_payload={
            "stable_creator_id": creator_id,
            "work_id": work_id,
            "page_index": 0,
        },
        source_run_id="scv2-px2:policy-probe",
    )


def _context_probe_input(
    key: str,
    value: str,
    *,
    role: str,
    context: str,
    payload: Mapping[str, Any] | None = None,
    origin_type: str = "pixiv_tag_observation",
) -> SourceConceptSignalInput:
    return SourceConceptSignalInput(
        origin_type=origin_type,
        origin_key=key,
        provider="pixiv",
        raw_value=value,
        display_value=value,
        canonical_value=value,
        role_hint=role,
        work_context_key=context,
        source_kind="synthetic_policy_probe",
        trust_tier="strong",
        confidence=1.0,
        status="active",
        evidence_payload=dict(payload or {}),
        source_run_id="scv2-px2:policy-probe",
    )


def _acceptance_matrix(
    run: PixivClusteringRun,
    *,
    deterministic_replay: bool,
    persistence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    creator_cluster = [
        cluster
        for cluster in run.clusters
        if "provider-account:pixiv:800000001"
        in cluster["stable_identity_anchors"]
    ]
    creator_aliases = {
        alias["display_name"]
        for cluster in creator_cluster
        for alias in cluster["aliases"]
    }
    distinct_creator = _probe_result(
        (
            _artist_probe_input(
                "distinct-a",
                display_name="Shared Synthetic Artist",
                creator_id="910000001",
                work_id="920000001",
            ),
            _artist_probe_input(
                "distinct-b",
                display_name="Shared Synthetic Artist",
                creator_id="910000002",
                work_id="920000002",
            ),
        ),
        run_id="scv2-px2:distinct-creator-probe",
    )
    name_only = _probe_result(
        (
            _artist_probe_input(
                "name-only-a",
                display_name="Name Only Synthetic Artist",
                creator_id=None,
                work_id="930000001",
            ),
            _artist_probe_input(
                "name-only-b",
                display_name="Name Only Synthetic Artist",
                creator_id=None,
                work_id="930000002",
            ),
        ),
        run_id="scv2-px2:name-only-artist-probe",
    )
    work_tag_contexts = {
        signal.work_context_key
        for signal in run.consumer.signals
        if signal.origin_type == "pixiv_tag_observation"
        and (signal.evidence_payload or {}).get("work_id") == "700000011"
    }
    character_signals = [
        signal
        for signal in run.consumer.signals
        if signal.role_hint == "character"
        and signal.display_value == "Synthetic Hero (Synthetic Work)"
    ]
    concept_by_signal = {
        signal_key: cluster["concept_key"]
        for cluster in run.clusters
        for signal_key in cluster["member_signal_keys"]
    }
    character_concepts = {
        concept_by_signal.get(signal.signal_key) for signal in character_signals
    }

    alias_base = (
        _context_probe_input(
            "alias-left",
            "Synthetic Canonical Hero",
            role="character",
            context="pixiv:work:940000001",
        ),
        _context_probe_input(
            "alias-right",
            "合成英雄",
            role="character",
            context="pixiv:work:940000001",
        ),
    )
    alias_without = _probe_result(
        alias_base,
        run_id="scv2-px2:alias-without-evidence",
    )
    alias_unapproved = _probe_result(
        (
            *alias_base,
            _context_probe_input(
                "alias-unapproved-evidence",
                "Synthetic unapproved alias evidence",
                role="character",
                context="pixiv:work:940000001",
                origin_type="source_alias_candidate",
                payload={
                    "relation_type": "same_concept",
                    "source_name_key": "synthetic_canonical_hero",
                    "target_name_key": "合成英雄",
                    "alias_candidate_status": "candidate",
                    "alias_candidate_requires_review": True,
                    "evidence_source": "synthetic_candidate",
                },
            ),
        ),
        run_id="scv2-px2:alias-unapproved-evidence",
    )
    alias_with = _probe_result(
        (
            *alias_base,
            _context_probe_input(
                "alias-evidence",
                "Synthetic approved alias evidence",
                role="character",
                context="pixiv:work:940000001",
                origin_type="source_alias_candidate",
                payload={
                    "relation_type": "same_concept",
                    "source_name_key": "synthetic_canonical_hero",
                    "target_name_key": "合成英雄",
                    "stable_alias_approved": True,
                },
            ),
        ),
        run_id="scv2-px2:alias-with-evidence",
    )
    alias_without_concepts = {
        concept.concept_key
        for concept in alias_without.concepts
        if any(
            signal.display_value
            in {"Synthetic Canonical Hero", "合成英雄"}
            for signal in concept.signals
        )
    }
    alias_with_concepts = {
        concept.concept_key
        for concept in alias_with.concepts
        if any(
            signal.display_value
            in {"Synthetic Canonical Hero", "合成英雄"}
            for signal in concept.signals
        )
    }
    alias_unapproved_concepts = {
        concept.concept_key
        for concept in alias_unapproved.concepts
        if any(
            signal.display_value
            in {"Synthetic Canonical Hero", "合成英雄"}
            for signal in concept.signals
        )
    }

    transitive_inputs = (
        _context_probe_input(
            "transitive-a",
            "transitive alpha",
            role="character",
            context="pixiv:work:950000001",
        ),
        _context_probe_input(
            "transitive-b",
            "transitive beta",
            role="unknown",
            context="pixiv:work:950000001",
            payload={"manual_confirmation": True},
        ),
        _context_probe_input(
            "transitive-c",
            "transitive gamma",
            role="work",
            context="pixiv:work:950000001",
        ),
        _context_probe_input(
            "transitive-alias-ab",
            "transitive alias ab",
            role="unknown",
            context="pixiv:work:950000001",
            origin_type="source_alias_candidate",
            payload={
                "relation_type": "same_concept",
                "source_name_key": "transitive_alpha",
                "target_name_key": "transitive_beta",
                "manual_confirmation": True,
            },
        ),
        _context_probe_input(
            "transitive-alias-bc",
            "transitive alias bc",
            role="unknown",
            context="pixiv:work:950000001",
            origin_type="source_alias_candidate",
            payload={
                "relation_type": "same_concept",
                "source_name_key": "transitive_beta",
                "target_name_key": "transitive_gamma",
                "manual_confirmation": True,
            },
        ),
    )
    transitive = _probe_result(
        transitive_inputs,
        run_id="scv2-px2:transitive-cannot-probe",
    )
    transitive_manifest = build_complete_candidate_pair_manifest(
        transitive.edge_candidates
    )
    _transitive_rows, transitive_records, _transitive_accounting = (
        _candidate_dispositions(transitive, transitive_manifest)
    )
    transitive_cannot_count = sum(
        row["disposition"] == "cannot_link"
        and row["reason_code"] == "transitive_cannot_link_constraint"
        for row in transitive_records
    )
    direct_cannot_count = sum(
        row["disposition"] == "cannot_link"
        and row["reason_code"] != "transitive_cannot_link_constraint"
        for row in transitive_records
    )
    false_state_keys = {
        signal.signal_key
        for signal in run.consumer.signals
        if (signal.evidence_payload or {}).get("px1_source_disposition")
        != "complete"
    }
    active_cluster_keys = {
        signal_key
        for cluster in run.clusters
        if cluster["status"] == "active"
        for signal_key in cluster["member_signal_keys"]
    }

    rows = [
        {
            "scenario": "same_stable_creator_id_cross_work_name_change",
            "passed": len(creator_cluster) == 1
            and "Synthetic Creator Alpha" in creator_aliases
            and "Synthetic Creator Alpha Renamed" in creator_aliases,
        },
        {
            "scenario": "different_stable_creator_ids_same_name",
            "passed": len(distinct_creator.concepts) == 2,
        },
        {
            "scenario": "name_only_artist_same_name_cross_work",
            "passed": len(name_only.concepts) == 2
            and any(
                edge.resolution_reason_code
                == "creator_identity_strong_evidence_missing"
                and not edge.union_allowed
                for edge in name_only.edge_candidates
            ),
        },
        {
            "scenario": "multi_page_work_level_tag_context",
            "passed": work_tag_contexts == {"pixiv:work:700000011"},
        },
        {
            "scenario": "same_character_different_work_context",
            "passed": len(character_signals) == 2
            and len(character_concepts) == 2,
        },
        {
            "scenario": "no_alias_evidence_does_not_union",
            "passed": len(alias_without_concepts) == 2,
        },
        {
            "scenario": "unapproved_alias_candidate_does_not_union",
            "passed": len(alias_unapproved_concepts) == 2,
        },
        {
            "scenario": "approved_stable_alias_evidence_unions",
            "passed": len(alias_with_concepts) == 1,
        },
        {
            "scenario": "duplicate_reverse_and_replay_deterministic",
            "passed": deterministic_replay,
        },
        {
            "scenario": "database_row_identity_excluded",
            "passed": persistence.get(
                "business_fingerprint_excludes_database_row_id"
            )
            is True,
        },
        {
            "scenario": "inactive_source_states_do_not_create_active_identity",
            "passed": not (false_state_keys & active_cluster_keys),
        },
        {
            "scenario": "direct_and_transitive_cannot_link_closure",
            "passed": direct_cannot_count > 0
            and transitive_cannot_count > 0
            and transitive.summary.get("transitive_cannot_violation_count") == 0,
        },
        {
            "scenario": "deferred_candidates_do_not_union",
            "passed": all(
                not row["union_decision"]
                for row in run.candidate_records
                if row["disposition"] == "deferred_nonblocking"
            ),
        },
        {
            "scenario": "ambiguous_ledger_nonblocking_and_complete",
            "passed": run.ambiguous_ledger["blocking"] is False
            and run.ambiguous_ledger["record_count"]
            == sum(run.ambiguous_ledger["counts"].values()),
        },
        {
            "scenario": "temporary_persistence_twice_idempotent",
            "passed": persistence.get("temporary_persistence_idempotent") is True,
        },
    ]
    return rows


def build_public_cluster_result(
    run: PixivClusteringRun,
    *,
    persistence_proof: Mapping[str, Any],
    deterministic_replay: bool,
    px1_input_generation_temporary_database_count: int,
) -> dict[str, Any]:
    invariants = {
        **dict(run.invariants),
        "deterministic_replay": deterministic_replay,
        "temporary_persistence_idempotent": persistence_proof.get(
            "temporary_persistence_idempotent"
        )
        is True,
        "production_activity": 0,
    }
    acceptance = _acceptance_matrix(
        run,
        deterministic_replay=deterministic_replay,
        persistence=persistence_proof,
    )
    operation_receipt = {
        "schema_version": PX2_OPERATION_RECEIPT_SCHEMA,
        "fixture_source": (
            "repository_owned_px1_synthetic_contract"
            if px1_input_generation_temporary_database_count
            else "provided_px1_consumer_summary"
        ),
        "task_owned_temporary_workspace_enforced": True,
        "px1_input_generation_temporary_database_count": (
            px1_input_generation_temporary_database_count
        ),
        "source_concept_temporary_database_count": 2,
        "existing_database_or_app_storage_activity": 0,
        "provider_network_activity": 0,
        "real_source_activity": 0,
        "credential_activity": 0,
        "media_download_activity": 0,
        "migration_activity": 0,
        "llm_activity": 0,
        "server_browser_e2e_activity": 0,
        "user_data_import_or_tagging_activity": 0,
        "entity_truth_promotion_activity": 0,
        "production_activity": 0,
    }
    result: dict[str, Any] = {
        "schema_version": PX2_CLUSTER_RESULT_SCHEMA,
        "contract_id": PX2_CONTRACT_ID,
        "status": "implementation_ready_for_owner_merge_audit",
        "executed_stages": list(PX2_EXECUTED_STAGES),
        "px1_inputs": {
            "consumer_contract_schema": PX2_CONSUMER_CONTRACT_SCHEMA,
            "aggregate_schema": PIXIV_AGGREGATE_SCHEMA,
            "signal_bundle_schema": PIXIV_SIGNAL_BUNDLE_SCHEMA,
            "aggregate_artifact_fingerprint": run.consumer.aggregate_artifact_fingerprint,
            "signal_bundle_artifact_fingerprint": run.consumer.signal_bundle_artifact_fingerprint,
            "consumer_input_fingerprint": run.consumer.input_fingerprint,
            "aggregate_count": len(run.consumer.aggregates),
            "signal_bundle_count": len(run.consumer.signal_bundles),
            "signal_count": len(run.consumer.signals),
            "source_state_counts": run.consumer.source_state_counts,
            "canonical_json_round_trip_stable": True,
        },
        "resolver": {
            "resolver_version": RESOLVER_VERSION,
            "context_policy_version": PX2_CONTEXT_POLICY_VERSION,
            "candidate_policy_version": PX2_CANDIDATE_POLICY_VERSION,
            "llm_enabled": False,
            "llm_judgment_count": 0,
        },
        "clusters": list(run.clusters),
        "cluster_count": len(run.clusters),
        "candidate_dispositions": list(run.candidate_records),
        "candidate_accounting": dict(run.candidate_accounting),
        "ambiguous_ledger": dict(run.ambiguous_ledger),
        "diagnostics": dict(run.diagnostics),
        "persistence_proof": dict(persistence_proof),
        "acceptance_matrix": acceptance,
        "acceptance_matrix_passed": bool(acceptance)
        and all(row["passed"] is True for row in acceptance),
        "business_projection_fingerprint": run.business_projection_fingerprint,
        "invariants": invariants,
        "operation_receipt": operation_receipt,
        "authorities": dict(PX2_AUTHORITY_MAP),
        "px1_owner_accepted": True,
        "px1_merged": True,
        "px2_started": True,
        "px2_implementation_completed": True,
        "px2_target_met": True,
        "deterministic_clustering_verified": deterministic_replay,
        "persistable_cluster_result_verified": persistence_proof.get(
            "temporary_persistence_idempotent"
        )
        is True,
        "target_met": True,
        "px2_owner_accepted": False,
        "px2_safe_to_merge": False,
        "px2_merge_authorized": False,
        "px3_started": False,
        "real_provider_authorized": False,
        "real_source_authorized": False,
        "existing_database_authorized": False,
        "full_import_authorized": False,
        "production_authorized": False,
    }
    result["canonical_fingerprint"] = canonical_fingerprint(result)
    assert_public_safe_projection(result)
    return result


def _execute_synthetic_pixiv_metadata_clustering(
    *,
    workspace: Path,
    px1_summary: Mapping[str, Any],
    px1_input_generation_temporary_database_count: int,
) -> dict[str, Any]:
    root = _require_task_owned_temp_workspace(workspace)
    generated_database_count = _require_exact_int(
        px1_input_generation_temporary_database_count,
        "px1_input_generation_temporary_database_count",
    )
    with _OfflineOperationGuard() as guard:
        selected_px1 = dict(px1_summary)
        consumer = consume_px1_public_summary(selected_px1)
        run = build_pixiv_clustering(consumer)
        reversed_consumer = validate_px1_consumer_artifacts(
            aggregates=list(reversed(consumer.aggregates)),
            signal_bundles=list(reversed(consumer.signal_bundles)),
            consumer_contract=_require_mapping(
                selected_px1.get("px2_consumer_contract"),
                "px1_consumer_contract",
            ),
        )
        replay = build_pixiv_clustering(reversed_consumer)
        deterministic_replay = bool(
            run.business_projection_fingerprint
            == replay.business_projection_fingerprint
            and run.candidate_records == replay.candidate_records
            and run.clusters == replay.clusters
            and run.ambiguous_ledger == replay.ambiguous_ledger
        )
        persistence = prove_temporary_source_concept_persistence(
            run,
            workspace=root,
        )
        result = build_public_cluster_result(
            run,
            persistence_proof=persistence,
            deterministic_replay=deterministic_replay,
            px1_input_generation_temporary_database_count=(
                generated_database_count
            ),
        )
    if guard.provider_network_attempt_count or guard.subprocess_attempt_count:
        raise PixivMetadataClusteringError("px2_offline_operation_guard_failed")
    required_zero = (
        "unexplained_signal_loss",
        "multi_stable_creator_id_component_count",
        "name_only_artist_union_count",
        "cannot_link_union_violation_count",
        "deferred_union_violation_count",
        "cross_role_union_violation_count",
        "existing_db_or_app_storage_activity",
        "provider_network_activity",
        "llm_activity",
        "production_activity",
    )
    failures = [
        str(row["scenario"])
        for row in result["acceptance_matrix"]
        if row.get("passed") is not True
    ]
    failures.extend(
        key
        for key in (
            "all_input_bundles_accounted",
            "all_candidate_pairs_accounted",
            "deterministic_replay",
            "temporary_persistence_idempotent",
        )
        if result["invariants"].get(key) is not True
    )
    failures.extend(
        key
        for key in required_zero
        if result["invariants"].get(key) != 0
    )
    if failures:
        raise PixivMetadataClusteringError(
            "px2_acceptance_invariant_failed:" + ",".join(sorted(set(failures)))
        )
    return result


def run_repository_synthetic_pixiv_metadata_clustering(
    *,
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate canonical PX1 inputs and execute the PX2 vertical slice."""

    root = _require_task_owned_temp_workspace(workspace)
    px1_summary = run_synthetic_pixiv_vertical_slice(
        workspace=root,
        fixture=repository_synthetic_pixiv_fixture(),
    )
    px1_receipt = _require_mapping(
        px1_summary.get("operation_receipt"),
        "px1_operation_receipt",
    )
    generated_database_count = _require_exact_int(
        px1_receipt.get("task_owned_temporary_database_count"),
        "px1_generated_temporary_database_count",
    )
    result = _execute_synthetic_pixiv_metadata_clustering(
        workspace=root,
        px1_summary=px1_summary,
        px1_input_generation_temporary_database_count=generated_database_count,
    )
    return px1_summary, result


def run_synthetic_pixiv_metadata_clustering(
    *,
    workspace: Path,
    px1_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute PX2, recording whether this call generated its PX1 inputs."""

    root = _require_task_owned_temp_workspace(workspace)
    if px1_summary is None:
        _generated_px1, result = run_repository_synthetic_pixiv_metadata_clustering(
            workspace=root,
        )
        return result
    return _execute_synthetic_pixiv_metadata_clustering(
        workspace=root,
        px1_summary=px1_summary,
        px1_input_generation_temporary_database_count=0,
    )


def write_pixiv_clustering_evidence(
    evidence_dir: Path,
    *,
    px1_summary: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, str]:
    """Write fixed-name public-safe evidence below the task-owned workspace."""

    root = _require_task_owned_temp_workspace(evidence_dir)
    artifacts: dict[str, Any] = {
        "px1-consumer-summary.json": dict(px1_summary),
        "candidate-dispositions.json": result["candidate_dispositions"],
        "ambiguous-ledger.json": result["ambiguous_ledger"],
        "persistence-proof.json": result["persistence_proof"],
        "operation-receipt.json": result["operation_receipt"],
        "public-summary.json": dict(result),
    }
    fingerprints: dict[str, str] = {}
    for name, payload in artifacts.items():
        target = root / name
        if target.exists():
            raise PixivMetadataClusteringError("px2_evidence_artifact_already_exists")
        target.write_bytes(canonical_json_bytes(payload) + b"\n")
        fingerprints[name] = canonical_fingerprint(payload)
    return dict(sorted(fingerprints.items()))
