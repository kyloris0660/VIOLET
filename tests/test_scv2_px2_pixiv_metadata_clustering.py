"""Focused acceptance and mutation tests for SCV2-PX2 clustering."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.creator_identity_policy import stable_creator_identity_key  # noqa: E402
from app.services.pixiv_metadata_clustering_service import (  # noqa: E402
    PX2_AMBIGUOUS_LEDGER_SCHEMA,
    PX2_CANDIDATE_POLICY_VERSION,
    PX2_CLUSTER_RESULT_SCHEMA,
    PX2_CONTEXT_POLICY_VERSION,
    PX2_CONTRACT_ID,
    PX2_PERSISTENCE_PROOF_SCHEMA,
    PixivMetadataClusteringError,
    build_pixiv_clustering,
    consume_px1_public_summary,
    run_synthetic_pixiv_metadata_clustering,
    validate_px1_consumer_artifacts,
)
from app.services.pixiv_metadata_projection_service import (  # noqa: E402
    PixivMetadataProjectionError,
    assert_public_safe_projection,
    canonical_fingerprint,
)
from app.services.pixiv_metadata_vertical_slice_service import (  # noqa: E402
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
)
from app.services.source_concept_autonomous_closure_service import (  # noqa: E402
    build_candidate_pair_manifest,
    build_complete_candidate_pair_manifest,
)
from app.services.source_concept_resolver_service import RESOLVER_VERSION  # noqa: E402


@pytest.fixture(scope="module")
def px2_fixture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    workspace = tmp_path_factory.mktemp("scv2-px2-fixture")
    runtime_storage = tmp_path_factory.mktemp("scv2-px2-runtime")
    keys = (
        "VIOLET_SKIP_DOTENV",
        "VIOLET_ENV",
        "POSTGRES_DB",
        "TEST_DATABASE_URL",
        "VIOLET_STORAGE_ROOT",
        "VIOLET_TEST_STORAGE_ROOT",
    )
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(
        {
            "VIOLET_SKIP_DOTENV": "1",
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "scv2_px2_test_temp",
            "TEST_DATABASE_URL": "",
            "VIOLET_STORAGE_ROOT": os.fspath(runtime_storage),
            "VIOLET_TEST_STORAGE_ROOT": os.fspath(runtime_storage),
        }
    )
    try:
        px1 = run_synthetic_pixiv_vertical_slice(
            workspace=workspace,
            fixture=repository_synthetic_pixiv_fixture(),
        )
        consumer = consume_px1_public_summary(px1)
        clustering = build_pixiv_clustering(consumer)
        result = run_synthetic_pixiv_metadata_clustering(
            workspace=workspace,
            px1_summary=px1,
        )
        return {
            "px1": px1,
            "consumer": consumer,
            "clustering": clustering,
            "result": result,
        }
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _consumer_parts(px1: dict[str, object]) -> tuple[list[dict], list[dict], dict]:
    return (
        copy.deepcopy(px1["aggregates"]),
        copy.deepcopy(px1["signal_bundles"]),
        copy.deepcopy(px1["px2_consumer_contract"]),
    )


def _rebind_bundle(bundle: dict) -> None:
    payload = dict(bundle)
    payload.pop("canonical_fingerprint", None)
    bundle["canonical_fingerprint"] = canonical_fingerprint(payload)


def _rebind_consumer(aggregates: list[dict], bundles: list[dict], contract: dict) -> None:
    contract["aggregate_artifact_fingerprint"] = canonical_fingerprint(
        sorted(aggregates, key=lambda row: row["stable_work_page_key"])
    )
    contract["signal_bundle_artifact_fingerprint"] = canonical_fingerprint(
        sorted(bundles, key=lambda row: row["stable_work_page_key"])
    )


def test_consumes_exact_px1_fixture_and_reconstructs_all_signals(px2_fixture) -> None:
    consumer = px2_fixture["consumer"]
    assert len(consumer.aggregates) == 14
    assert len(consumer.signal_bundles) == 14
    assert len(consumer.signals) == 40
    assert sum(consumer.source_state_counts.values()) == 14
    assert consumer.source_state_counts == {
        "complete": 8,
        "conflict": 1,
        "page_mismatch": 1,
        "retryable": 1,
        "terminal": 1,
        "unsupported": 2,
    }
    assert len({signal.signal_key for signal in consumer.signals}) == 40


def test_role_aware_context_policy_is_work_and_page_exact(px2_fixture) -> None:
    consumer = px2_fixture["consumer"]
    tag_contexts = {
        signal.work_context_key
        for signal in consumer.signals
        if signal.origin_type == "pixiv_tag_observation"
        and signal.evidence_payload["work_id"] == "700000011"
    }
    title_contexts = {
        signal.work_context_key
        for signal in consumer.signals
        if signal.origin_type == "pixiv_title_observation"
        and signal.evidence_payload["work_id"] == "700000011"
    }
    artist_contexts = {
        signal.work_context_key
        for signal in consumer.signals
        if signal.role_hint == "artist"
    }
    assert tag_contexts == {"pixiv:work:700000011"}
    assert title_contexts == {
        "pixiv:work:700000011:page:0",
        "pixiv:work:700000011:page:1",
        "pixiv:work:700000011:page:2",
    }
    assert artist_contexts == {None}
    assert {
        signal.evidence_payload["px2_context_policy_version"]
        for signal in consumer.signals
    } == {PX2_CONTEXT_POLICY_VERSION}


def test_existing_resolver_merges_stable_creator_not_name_only(px2_fixture) -> None:
    run = px2_fixture["clustering"]
    stable = [
        cluster
        for cluster in run.clusters
        if "provider-account:pixiv:800000001"
        in cluster["stable_identity_anchors"]
    ]
    assert len(stable) == 1
    assert {
        alias["display_name"] for alias in stable[0]["aliases"]
    }.issuperset(
        {"Synthetic Creator Alpha", "Synthetic Creator Alpha Renamed"}
    )
    assert run.invariants["multi_stable_creator_id_component_count"] == 0
    assert run.invariants["name_only_artist_union_count"] == 0
    assert run.resolution.summary["resolver_version"] == RESOLVER_VERSION


def test_candidate_dispositions_are_complete_stable_and_union_safe(px2_fixture) -> None:
    run = px2_fixture["clustering"]
    accounting = run.candidate_accounting
    assert accounting == {
        "total_candidate_pairs": 59,
        "must_link_count": 52,
        "cannot_link_count": 4,
        "deferred_nonblocking_count": 3,
        "unaccounted_pair_count": 0,
        "duplicate_disposition_count": 0,
        "extra_disposition_count": 0,
        "silently_dropped_pair_count": 0,
        "candidate_disposition_coverage": 1.0,
        "accounting_equality_passed": True,
    }
    assert [row["pair_key"] for row in run.candidate_records] == sorted(
        row["pair_key"] for row in run.candidate_records
    )
    required = {
        "pair_key",
        "left_signal_key",
        "right_signal_key",
        "disposition",
        "reason_code",
        "negative_reason",
        "evidence_refs",
        "union_decision",
    }
    assert all(required.issubset(row) for row in run.candidate_records)
    assert all(
        row["candidate_policy_version"] == PX2_CANDIDATE_POLICY_VERSION
        for row in run.candidate_records
    )
    assert all(
        row["union_decision"] is False
        for row in run.candidate_records
        if row["disposition"] != "must_link"
    )
    assert all(
        row["same_resolved_component"] is False
        for row in run.candidate_records
        if row["disposition"] == "cannot_link"
    )


def test_complete_manifest_includes_edges_excluded_from_llm_selection(px2_fixture) -> None:
    resolution = px2_fixture["clustering"].resolution
    complete = build_complete_candidate_pair_manifest(resolution.edge_candidates)
    historical_llm_eligible = build_candidate_pair_manifest(
        resolution.edge_candidates,
        signals=resolution.signals,
    )
    assert len(complete) == 59
    assert len(complete) >= len(historical_llm_eligible)
    assert {row.pair_id for row in historical_llm_eligible}.issubset(
        {row.pair_id for row in complete}
    )


def test_ambiguous_ledger_is_nonblocking_complete_and_deterministic(px2_fixture) -> None:
    run = px2_fixture["clustering"]
    ledger = run.ambiguous_ledger
    assert ledger["schema_version"] == PX2_AMBIGUOUS_LEDGER_SCHEMA
    assert ledger["blocking"] is False
    assert ledger["human_review_required"] is False
    assert ledger["identity_union_allowed"] is False
    assert ledger["record_count"] == 29
    assert ledger["record_count"] == sum(ledger["counts"].values())
    assert ledger["counts"]["deferred_candidate_pair_count"] == 3
    payload = dict(ledger)
    supplied = payload.pop("canonical_fingerprint")
    assert supplied == canonical_fingerprint(payload)


def test_noncomplete_source_states_are_explained_and_not_active(px2_fixture) -> None:
    run = px2_fixture["clustering"]
    deferred_signal_keys = {
        signal.signal_key
        for signal in run.consumer.signals
        if signal.evidence_payload["px1_source_disposition"] != "complete"
    }
    active_keys = {
        key
        for cluster in run.clusters
        if cluster["status"] == "active"
        for key in cluster["member_signal_keys"]
    }
    resolved_keys = {
        key
        for cluster in run.clusters
        for key in cluster["member_signal_keys"]
    }
    assert deferred_signal_keys
    assert deferred_signal_keys.isdisjoint(active_keys)
    assert len(run.consumer.source_state_deferrals) == 8
    assert {
        row["disposition"]
        for row in run.consumer.source_state_deferrals
        if row["disposition"] != "complete"
    } == {"conflict", "page_mismatch", "retryable", "terminal", "unsupported"}
    noncomplete_rows = [
        row
        for row in run.consumer.source_state_deferrals
        if row["disposition"] != "complete"
    ]
    complete_deferred_rows = [
        row
        for row in run.consumer.source_state_deferrals
        if row["disposition"] == "complete"
    ]
    assert noncomplete_rows
    assert complete_deferred_rows
    assert all(row["active_identity_allowed"] is False for row in noncomplete_rows)
    assert all(
        row["active_identity_allowed"] is True for row in complete_deferred_rows
    )
    assert all(
        set(row["signal_keys"]).issubset(resolved_keys)
        for row in complete_deferred_rows
    )
    assert any(
        set(row["signal_keys"]).intersection(active_keys)
        for row in complete_deferred_rows
    )
    assert all(
        not row["union_decision"]
        for row in run.candidate_records
        if row["left_signal_key"] in deferred_signal_keys
        or row["right_signal_key"] in deferred_signal_keys
    )


def test_public_result_and_persistence_proof_close_required_invariants(px2_fixture) -> None:
    result = px2_fixture["result"]
    assert result["schema_version"] == PX2_CLUSTER_RESULT_SCHEMA
    assert result["contract_id"] == PX2_CONTRACT_ID
    assert result["cluster_count"] == 20
    assert result["acceptance_matrix_passed"] is True
    assert len(result["acceptance_matrix"]) == 15
    assert result["persistence_proof"]["schema_version"] == PX2_PERSISTENCE_PROOF_SCHEMA
    assert result["persistence_proof"]["temporary_persistence_idempotent"] is True
    assert result["persistence_proof"]["database_row_id_variation_observed"] is True
    assert result["persistence_proof"]["stale_foreign_scope_preserved"] is True
    assert result["persistence_proof"]["only_sourceconcept_owned_temporary_tables_written"] is True
    assert result["persistence_proof"]["ambiguous_ledger_persisted_in_existing_resolution_run"] is True
    assert result["invariants"] == {
        "all_input_bundles_accounted": True,
        "all_candidate_pairs_accounted": True,
        "unexplained_signal_loss": 0,
        "multi_stable_creator_id_component_count": 0,
        "name_only_artist_union_count": 0,
        "cannot_link_union_violation_count": 0,
        "deferred_union_violation_count": 0,
        "cross_role_union_violation_count": 0,
        "deterministic_replay": True,
        "temporary_persistence_idempotent": True,
        "existing_db_or_app_storage_activity": 0,
        "provider_network_activity": 0,
        "llm_activity": 0,
        "production_activity": 0,
    }


def test_public_result_fingerprint_round_trip_and_redaction(px2_fixture) -> None:
    result = px2_fixture["result"]
    payload = copy.deepcopy(result)
    supplied = payload.pop("canonical_fingerprint")
    assert supplied == canonical_fingerprint(payload)
    assert json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False)) == result
    assert_public_safe_projection(result)
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True).casefold()
    for marker in (
        "raw_provider_payload",
        "raw_metadata_json",
        "local_path",
        "source_url",
        "access_token",
        "refresh_token",
        "client_secret",
    ):
        assert marker not in serialized
    assert "c:\\" not in serialized
    assert "file://" not in serialized
    assert "\x00" not in serialized
    assert "\ufffd" not in serialized


def test_reverse_bundle_order_replays_identically(px2_fixture) -> None:
    px1 = px2_fixture["px1"]
    aggregates, bundles, contract = _consumer_parts(px1)
    reverse = validate_px1_consumer_artifacts(
        aggregates=list(reversed(aggregates)),
        signal_bundles=list(reversed(bundles)),
        consumer_contract=contract,
    )
    replay = build_pixiv_clustering(reverse)
    original = px2_fixture["clustering"]
    assert reverse.input_fingerprint == original.consumer.input_fingerprint
    assert replay.business_projection_fingerprint == original.business_projection_fingerprint
    assert replay.candidate_records == original.candidate_records
    assert replay.clusters == original.clusters


@pytest.mark.parametrize(
    "mutation",
    (
        "aggregate_schema",
        "aggregate_fingerprint",
        "bundle_schema",
        "bundle_fingerprint",
        "consumer_fingerprint",
        "logical_keys",
        "signal_count",
        "identity_count",
        "source_state",
        "stable_key",
        "signal_evidence_work",
        "mixed_schema",
    ),
)
def test_consumer_contract_mutations_fail_closed(px2_fixture, mutation: str) -> None:
    px1 = px2_fixture["px1"]
    aggregates, bundles, contract = _consumer_parts(px1)
    if mutation == "aggregate_schema":
        aggregates[0]["schema_version"] = "mixed.schema"
    elif mutation == "aggregate_fingerprint":
        aggregates[0]["disposition"] = "retryable"
    elif mutation == "bundle_schema":
        bundles[0]["schema_version"] = "mixed.schema"
    elif mutation == "bundle_fingerprint":
        bundles[0]["canonical_fingerprint"] = "0" * 64
    elif mutation == "consumer_fingerprint":
        contract["aggregate_artifact_fingerprint"] = "0" * 64
    elif mutation == "logical_keys":
        bundles[0]["logical_keys"] = bundles[0]["logical_keys"][:-1]
        _rebind_bundle(bundles[0])
        _rebind_consumer(aggregates, bundles, contract)
    elif mutation == "signal_count":
        bundles[0]["signal_count"] += 1
        _rebind_bundle(bundles[0])
        _rebind_consumer(aggregates, bundles, contract)
    elif mutation == "identity_count":
        bundles[0]["strong_identity_anchor_count"] += 1
        _rebind_bundle(bundles[0])
        _rebind_consumer(aggregates, bundles, contract)
    elif mutation == "source_state":
        bundles[0]["source_state"]["disposition"] = "retryable"
        _rebind_bundle(bundles[0])
        _rebind_consumer(aggregates, bundles, contract)
    elif mutation == "stable_key":
        aggregates[0]["stable_work_page_key"] = "pixiv:work:700000001:page:01"
        payload = dict(aggregates[0])
        payload.pop("canonical_fingerprint")
        aggregates[0]["canonical_fingerprint"] = canonical_fingerprint(payload)
        _rebind_consumer(aggregates, bundles, contract)
    elif mutation == "signal_evidence_work":
        bundles[0]["signals"][0]["evidence"]["work_id"] = "700000099"
        _rebind_bundle(bundles[0])
        _rebind_consumer(aggregates, bundles, contract)
    elif mutation == "mixed_schema":
        contract["signal_bundle_schema_version"] = "mixed.schema"
    with pytest.raises((PixivMetadataClusteringError, PixivMetadataProjectionError)):
        validate_px1_consumer_artifacts(
            aggregates=aggregates,
            signal_bundles=bundles,
            consumer_contract=contract,
        )


@pytest.mark.parametrize("conflicting", (False, True))
def test_duplicate_or_conflicting_bundle_identity_fails_closed(
    px2_fixture,
    conflicting: bool,
) -> None:
    aggregates, bundles, contract = _consumer_parts(px2_fixture["px1"])
    duplicate = copy.deepcopy(bundles[0])
    if conflicting:
        duplicate["source_state"]["deferred_reasons"].append("conflicting_copy")
        _rebind_bundle(duplicate)
    bundles.append(duplicate)
    _rebind_consumer(aggregates, bundles, contract)
    expected = "identity_conflict" if conflicting else "identity_duplicate"
    with pytest.raises(PixivMetadataClusteringError, match=expected):
        validate_px1_consumer_artifacts(
            aggregates=aggregates,
            signal_bundles=bundles,
            consumer_contract=contract,
        )


def test_missing_bundle_and_noncanonical_numeric_inputs_fail_closed(px2_fixture) -> None:
    aggregates, bundles, contract = _consumer_parts(px2_fixture["px1"])
    bundles.pop()
    _rebind_consumer(aggregates, bundles, contract)
    with pytest.raises(PixivMetadataClusteringError, match="scope_mismatch"):
        validate_px1_consumer_artifacts(
            aggregates=aggregates,
            signal_bundles=bundles,
            consumer_contract=contract,
        )

    for invalid in (False, 1.0, "01", "+1", " 1", "1 "):
        aggregates, bundles, contract = _consumer_parts(px2_fixture["px1"])
        aggregates[0]["page_index"] = invalid
        payload = dict(aggregates[0])
        payload.pop("canonical_fingerprint")
        aggregates[0]["canonical_fingerprint"] = canonical_fingerprint(payload)
        _rebind_consumer(aggregates, bundles, contract)
        with pytest.raises(PixivMetadataClusteringError):
            validate_px1_consumer_artifacts(
                aggregates=aggregates,
                signal_bundles=bundles,
                consumer_contract=contract,
            )


def test_full_px1_summary_schema_and_fingerprint_are_strict(px2_fixture) -> None:
    px1 = copy.deepcopy(px2_fixture["px1"])
    px1["caller_passed"] = True
    with pytest.raises(PixivMetadataClusteringError, match="schema_fields_invalid"):
        consume_px1_public_summary(px1)

    px1 = copy.deepcopy(px2_fixture["px1"])
    px1["canonical_fingerprint"] = "0" * 64
    with pytest.raises(PixivMetadataClusteringError, match="fingerprint_mismatch"):
        consume_px1_public_summary(px1)


def test_no_authority_expansion_or_external_activity(px2_fixture) -> None:
    result = px2_fixture["result"]
    assert result["operation_receipt"]["fixture_source"] == (
        "provided_px1_consumer_summary"
    )
    assert (
        result["operation_receipt"][
            "px1_input_generation_temporary_database_count"
        ]
        == 0
    )
    assert result["px1_owner_accepted"] is True
    assert result["px1_merged"] is True
    assert result["px2_started"] is True
    assert result["px2_implementation_completed"] is True
    assert result["px2_target_met"] is True
    assert result["px2_owner_accepted"] is False
    assert result["px2_safe_to_merge"] is False
    assert result["px2_merge_authorized"] is False
    assert result["px3_started"] is False
    assert all(
        value == 0
        for key, value in result["operation_receipt"].items()
        if key.endswith("_activity")
    )
    assert all(
        value is False
        for key, value in result["authorities"].items()
        if key.endswith("_authorized")
        and key
        not in {
            "px2_synthetic_implementation_authorized",
            "task_owned_temporary_database_authorized",
        }
    )
