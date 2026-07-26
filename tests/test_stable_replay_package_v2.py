"""Behavioral tests for schema-aware stable Replay evidence package v2."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.pixiv_metadata_ingestion_service import (
    is_trusted_complete_pixiv_metadata_record,
)
from scripts.stable_replay_package_v2 import (
    EXTERNAL_ROUTE_BUDGET,
    SCHEMA_BY_LOGICAL,
    SCHEMA_VERSION,
    StableReplayPackageV2Error,
    build_package_from_rows,
    compare_round_trip_packages,
    cross_validate_primary_stable_identity,
    graph_effective_projection,
    sha256_payload,
    stable_source_record_fingerprint,
    validate_package,
    validate_stable_reference_integrity,
    verify_external_routes_forbidden,
)


def _blank(logical: str, row_id: int) -> dict[str, Any]:
    schema = SCHEMA_BY_LOGICAL[logical]
    row = {
        field: None
        for field in (
            *schema.stable_columns,
            *schema.references,
            *schema.null_only_local_fields,
            *schema.excluded_local_fields,
        )
    }
    row["id"] = row_id
    row["created_at"] = datetime(2026, 7, 21, tzinfo=timezone.utc)
    row["updated_at"] = datetime(2026, 7, 22, tzinfo=timezone.utc)
    return row


def _realistic_rows() -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[int, Any]]]:
    source = _blank("source_metadata_records", 10)
    source.update(
        {
            "provider": "pixiv",
            "provider_run_id": "accepted-provider-run",
            "run_label": "accepted-acquisition",
            "provider_record_key": "pixiv:123456789:p0:provider",
            "media_id": 1,
            "source_work_id": "123456789",
            "source_page_index": 0,
            "source_url": None,
            "title": "Accepted provider work",
            "artist_name": "Creator",
            "artist_id": "42",
            "confidence": 1.0,
            "similarity": None,
            "metadata_kind": "provider_metadata",
            "data_type_label": "authenticated_provider_metadata",
            "raw_metadata_json": {
                "id": 123456789,
                "user": {"id": 42, "account": "creator"},
                "series": {"id": 7, "title": "Series"},
                "tags": [{"name": "blue_hair"}],
            },
            "provenance": {
                "source": "gallery_dl_authenticated_metadata",
                "stable_identity_key": {
                    "provider": "pixiv",
                    "work_id": "123456789",
                    "page_index": 0,
                },
            },
            "status": "metadata_complete",
            "retrieved_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
        }
    )
    queue = _blank("source_metadata_records", 11)
    queue.update(
        {
            "provider": "pixiv",
            "provider_run_id": "accepted-provider-run",
            "run_label": "sv1b-queue",
            "provider_record_key": "pixiv:123456789:p0:queue:media-b",
            "media_id": 2,
            "source_work_id": "123456789",
            "source_page_index": 0,
            "source_url": None,
            "title": "Accepted provider work",
            "artist_name": "Creator",
            "artist_id": "42",
            "confidence": 1.0,
            "similarity": None,
            "metadata_kind": "pixiv_ingestion_gate",
            "data_type_label": "authenticated_provider_metadata",
            "raw_metadata_json": {
                "id": 123456789,
                "user": {"id": 42, "account": "creator"},
                "attempted_queue_record_id": 11,
                "reused_complete_record_ids": [10],
                "parser_evidence": [{"work_id": "123456789", "page_index": 0}],
                "_pixiv_ingestion_reuse": {
                    "source_metadata_record_id": 10,
                    "stable_identity_key": {
                        "provider": "pixiv",
                        "work_id": "123456789",
                        "page_index": 0,
                    },
                },
                "_sv1b_phase_delta": {
                    "envelope_version": "sv1b_phase_delta_envelope_v1",
                    "reason_code": "reopened_untrusted_complete",
                    "original_raw_metadata_json": {
                        "reused_complete_record_ids": [10],
                        "parser_evidence": [{"work_id": "123456789"}],
                    },
                    "original_provenance": {
                        "source": "accepted",
                        "stable_identity_key": {
                            "provider": "pixiv",
                            "work_id": "123456789",
                            "page_index": 0,
                        },
                    },
                    "original_values_recoverable": True,
                },
            },
            "provenance": {
                "source": "compatible_complete_record_reuse",
                "source_metadata_record_id": 10,
                "stable_identity_key": {
                    "provider": "pixiv",
                    "work_id": "123456789",
                    "page_index": 0,
                },
            },
            "status": "metadata_complete",
            "retrieved_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
        }
    )

    tag = _blank("source_tag_observations", 20)
    tag.update(
        {
            "source_metadata_record_id": 11,
            "provider": "pixiv",
            "observation_key": "pixiv:queue:tag:blue_hair",
            "raw_tag": "blue_hair",
            "normalized_tag": "blue_hair",
            "canonical_tag_key": "blue_hair",
            "source_tag_kind": "provider_tag",
            "source_category_raw": "general",
            "language_hint": "en",
            "confidence": 1.0,
            "order_index": 0,
            "taxonomy_kb_id": None,
            "status": "observed",
        }
    )
    name = _blank("source_name_observations", 30)
    name.update(
        {
            "source_metadata_record_id": 11,
            "provider": "pixiv",
            "observation_key": "pixiv:queue:name:creator",
            "media_id": 2,
            "source_work_id": "123456789",
            "source_page_index": 0,
            "raw_name": "Creator",
            "normalized_name": "Creator",
            "canonical_name_key": "creator",
            "name_role": "artist",
            "source_field": "pixiv_user_metadata",
            "language_hint": "en",
            "script_hint": "latin",
            "confidence": 1.0,
            "provenance": {
                "source": "compatible_complete_record_reuse",
                "reused_from_source_metadata_record_id": 10,
            },
            "requires_review": False,
            "status": "observed",
        }
    )
    evidence = _blank("source_metadata_evidence", 40)
    evidence.update(
        {
            "source_metadata_record_id": 11,
            "evidence_key": "pixiv:queue:evidence:creator",
            "observation_type": "source_name_observation",
            "observation_id": 30,
            "evidence_kind": "trusted_complete_metadata",
            "evidence_strength": "trusted",
            "provenance": {
                "source_work_id": "123456789",
                "page_index": 0,
            },
            "status": "accepted",
        }
    )
    assertion = _blank("source_searchable_name_assertions", 50)
    assertion.update(
        {
            "provider": "pixiv",
            "source_metadata_record_id": 11,
            "source_tag_observation_id": 20,
            "source_name_observation_id": 30,
            "assertion_key": "pixiv:queue:assertion:creator",
            "raw_input": "Creator",
            "normalized_input": "Creator",
            "canonical_name_key": "creator",
            "asserted_name": "Creator",
            "asserted_role": "artist",
            "status": "accepted",
            "confidence": "trusted",
            "confidence_score": 1.0,
            "evidence_sources_json": [
                {"kind": "provider_record", "provider_record_key": queue["provider_record_key"]}
            ],
            "model_name": None,
            "prompt_version": None,
            "structured_output_schema_version": "deterministic-v1",
            "reasoning_summary_private": None,
            "provenance_summary": {"source": "accepted_provider_evidence"},
            "requires_review": False,
        }
    )
    tag_registry = _blank("source_tag_registry", 60)
    tag_registry.update(
        {
            "provider_scope": "pixiv",
            "normalized_tag": "blue_hair",
            "canonical_tag_key": "blue_hair",
            "raw_variants_json": ["blue_hair"],
            "first_seen_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
            "last_seen_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
            "seen_count": 1,
            "example_source_metadata_id": 11,
            "taxonomy_status": "unclassified",
            "governance_status": "accepted",
        }
    )
    name_registry = _blank("source_name_registry", 70)
    name_registry.update(
        {
            "canonical_name_key": "creator",
            "primary_display_name": "Creator",
            "normalized_display_name": "Creator",
            "raw_variants_json": ["Creator", "creator"],
            "provider_coverage_json": {"pixiv": 1},
            "role_distribution_json": {"artist": 1},
            "first_seen_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
            "last_seen_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
            "seen_count": 1,
            "governance_status": "accepted",
            "manual_override_status": "none",
            "notes": None,
        }
    )
    rows = {
        "source_metadata_records": [source, queue],
        "source_tag_observations": [tag],
        "source_name_observations": [name],
        "source_metadata_evidence": [evidence],
        "source_searchable_name_assertions": [assertion],
        "source_tag_registry": [tag_registry],
        "source_name_registry": [name_registry],
    }
    record_rows = {10: source, 11: queue}
    maps = {
        "record_key_by_id": {
            key: str(row["provider_record_key"]) for key, row in record_rows.items()
        },
        "record_fingerprint_by_id": {
            key: stable_source_record_fingerprint(row)
            for key, row in record_rows.items()
        },
        "media_key_by_id": {1: "media-content-a", 2: "media-content-b"},
        "tag_observation_key_by_id": {20: tag["observation_key"]},
        "name_observation_key_by_id": {30: name["observation_key"]},
    }
    return rows, maps


def _fresh_rows_from_package(
    package: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[int, Any]]]:
    media_ids = {"media-content-a": 1001, "media-content-b": 1002}
    record_ids = {
        row["provider_record_key"]: 1100 + index
        for index, row in enumerate(package["tables"]["source_metadata_records"])
    }
    tag_ids = {
        row["observation_key"]: 1200 + index
        for index, row in enumerate(package["tables"]["source_tag_observations"])
    }
    name_ids = {
        row["observation_key"]: 1300 + index
        for index, row in enumerate(package["tables"]["source_name_observations"])
    }
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    next_id = 2000
    for logical, exported_rows in package["tables"].items():
        schema = SCHEMA_BY_LOGICAL[logical]
        rows = []
        for exported in exported_rows:
            if logical == "source_metadata_records":
                row_id = record_ids[exported["provider_record_key"]]
            elif logical == "source_tag_observations":
                row_id = tag_ids[exported["observation_key"]]
            elif logical == "source_name_observations":
                row_id = name_ids[exported["observation_key"]]
            else:
                row_id = next_id
                next_id += 1
            row = _blank(logical, row_id)
            for field in schema.stable_columns:
                row[field] = copy.deepcopy(exported[field])
            for local_field, rule in schema.references.items():
                stable = exported[rule.exported_field]
                if stable is None:
                    row[local_field] = None
                elif rule.target == "media":
                    row[local_field] = media_ids[stable]
                elif rule.target == "source_metadata_record":
                    row[local_field] = record_ids[stable]
                elif rule.target == "source_tag_observation":
                    row[local_field] = tag_ids[stable]
                elif rule.target == "source_name_observation":
                    row[local_field] = name_ids[stable]
                elif rule.target == "polymorphic_observation":
                    row[local_field] = (
                        tag_ids[stable]
                        if exported["observation_type"] == "source_tag_observation"
                        else name_ids[stable]
                    )
                else:  # pragma: no cover - schema construction guards this.
                    raise AssertionError(rule.target)
            rows.append(row)
        rows_by_table[logical] = rows
    record_rows = {
        int(row["id"]): row for row in rows_by_table["source_metadata_records"]
    }
    maps = {
        "record_key_by_id": {
            key: str(row["provider_record_key"]) for key, row in record_rows.items()
        },
        "record_fingerprint_by_id": {
            key: stable_source_record_fingerprint(row)
            for key, row in record_rows.items()
        },
        "media_key_by_id": {value: key for key, value in media_ids.items()},
        "tag_observation_key_by_id": {value: key for key, value in tag_ids.items()},
        "name_observation_key_by_id": {value: key for key, value in name_ids.items()},
    }
    return rows_by_table, maps


def _package() -> dict[str, Any]:
    rows, maps = _realistic_rows()
    return build_package_from_rows(rows, maps=maps)


def _refresh_package_fingerprint(package: dict[str, Any]) -> None:
    package["package_fingerprint"] = sha256_payload(
        {
            key: value
            for key, value in package.items()
            if key != "package_fingerprint"
        }
    )


def test_nested_provider_work_id_and_provider_payload_ids_are_preserved() -> None:
    package = _package()
    records = package["tables"]["source_metadata_records"]
    source = next(row for row in records if row["metadata_kind"] == "provider_metadata")
    queue = next(row for row in records if row["metadata_kind"] == "pixiv_ingestion_gate")

    assert source["raw_metadata_json"]["id"] == 123456789
    assert source["raw_metadata_json"]["user"]["id"] == 42
    assert queue["provenance"]["stable_identity_key"]["work_id"] == "123456789"
    assert (
        queue["raw_metadata_json"]["_sv1b_phase_delta"]["original_provenance"][
            "stable_identity_key"
        ]["work_id"]
        == "123456789"
    )


def test_missing_nested_work_id_remains_missing_and_does_not_change_trust() -> None:
    rows, maps = _realistic_rows()
    queue = rows["source_metadata_records"][1]
    queue["provenance"]["stable_identity_key"].pop("work_id")
    queue["raw_metadata_json"]["_pixiv_ingestion_reuse"][
        "stable_identity_key"
    ].pop("work_id")
    queue["raw_metadata_json"]["_sv1b_phase_delta"]["original_provenance"][
        "stable_identity_key"
    ].pop("work_id")

    assert is_trusted_complete_pixiv_metadata_record(queue) is False
    package = build_package_from_rows(rows, maps=maps)
    exported = next(
        row
        for row in package["tables"]["source_metadata_records"]
        if row["metadata_kind"] == "pixiv_ingestion_gate"
    )

    assert "work_id" not in exported["provenance"]["stable_identity_key"]
    assert "work_id" not in (
        exported["raw_metadata_json"]["_pixiv_ingestion_reuse"][
            "stable_identity_key"
        ]
    )
    assert is_trusted_complete_pixiv_metadata_record(exported) is False
    assert package["preservation_loss_ledger"]["loss_entry_count"] == 0


def test_conflicting_nested_work_id_is_detected_by_immutable_crosscheck() -> None:
    rows, maps = _realistic_rows()
    rows["source_metadata_records"][1]["provenance"]["stable_identity_key"][
        "work_id"
    ] = "different-work"
    primary = build_package_from_rows(rows, maps=maps)
    accepted_v1 = {
        "tables": {
            "source_metadata_records": copy.deepcopy(
                primary["tables"]["source_metadata_records"]
            )
        }
    }
    proof, _ = cross_validate_primary_stable_identity(
        primary,
        accepted_v1,
        candidate_pages=[],
        final_work_outcomes=[],
    )

    assert proof["passed"] is False
    assert proof["stable_identity_mismatch_count"] == 1


def test_compatible_complete_reuse_uses_stable_reference_not_numeric_row_id() -> None:
    package = _package()
    queue = next(
        row
        for row in package["tables"]["source_metadata_records"]
        if row["metadata_kind"] == "pixiv_ingestion_gate"
    )
    reuse = queue["raw_metadata_json"]["_pixiv_ingestion_reuse"]

    assert reuse["source_provider_record_key"] == "pixiv:123456789:p0:provider"
    assert reuse["source_record_fingerprint"]
    assert "source_metadata_record_id" not in reuse
    assert queue["provenance"]["source_provider_record_key"] == reuse[
        "source_provider_record_key"
    ]
    assert queue["provenance"]["source_record_fingerprint"] == reuse[
        "source_record_fingerprint"
    ]
    integrity = validate_stable_reference_integrity(package)
    assert integrity["passed"] is True
    assert integrity["reference_count"] >= 4
    assert integrity["reference_count"] == integrity[
        "discovered_reference_pair_count"
    ]
    assert integrity["failed_check_count"] == 0
    assert all(integrity["checks"].values())
    assert set(integrity["json_field_reference_counts"]) == {
        f"{logical}.{field}"
        for logical, schema in SCHEMA_BY_LOGICAL.items()
        for field in schema.json_fields
    }


@pytest.mark.parametrize(
    ("target", "error"),
    [
        ("unknown_key", "stable_reference_unknown_key"),
        ("mismatched_fingerprint", "stable_reference_fingerprint_mismatch"),
        ("incomplete_pair", "stable_reference_pair_incomplete"),
    ],
)
def test_stable_reference_integrity_fails_closed(
    target: str,
    error: str,
) -> None:
    package = _package()
    queue = next(
        row
        for row in package["tables"]["source_metadata_records"]
        if row["metadata_kind"] == "pixiv_ingestion_gate"
    )
    reference = queue["raw_metadata_json"]["_pixiv_ingestion_reuse"]
    if target == "unknown_key":
        reference["source_provider_record_key"] = "pixiv:missing:p0"
    elif target == "mismatched_fingerprint":
        reference["source_record_fingerprint"] = "0" * 64
    else:
        reference.pop("source_record_fingerprint")
    _refresh_package_fingerprint(package)

    with pytest.raises(StableReplayPackageV2Error, match=error):
        validate_package(package)


def test_nested_reused_complete_reference_is_referentially_validated() -> None:
    package = _package()
    queue = next(
        row
        for row in package["tables"]["source_metadata_records"]
        if row["metadata_kind"] == "pixiv_ingestion_gate"
    )
    nested = queue["raw_metadata_json"]["_sv1b_phase_delta"][
        "original_raw_metadata_json"
    ]["reused_complete_record_references"][0]
    nested["source_record_fingerprint"] = "f" * 64
    _refresh_package_fingerprint(package)

    with pytest.raises(
        StableReplayPackageV2Error,
        match="stable_reference_fingerprint_mismatch",
    ):
        validate_package(package)


@pytest.mark.parametrize(
    ("logical", "field", "mutation", "error"),
    [
        (
            "source_name_observations",
            "provenance",
            {
                "source_provider_record_key": "pixiv:missing:p0",
                "source_record_fingerprint": "0" * 64,
            },
            "stable_reference_unknown_key",
        ),
        (
            "source_name_observations",
            "provenance",
            {
                "source_provider_record_key": "pixiv:123456789:p0:provider",
                "source_record_fingerprint": "0" * 64,
            },
            "stable_reference_fingerprint_mismatch",
        ),
        (
            "source_metadata_evidence",
            "provenance",
            {"source_provider_record_key": "pixiv:123456789:p0:provider"},
            "stable_reference_pair_incomplete",
        ),
        (
            "source_searchable_name_assertions",
            "provenance_summary",
            {"attempted_queue_record_fingerprint": "0" * 64},
            "stable_reference_pair_incomplete",
        ),
    ],
)
def test_all_declared_json_fields_fail_closed_for_stable_references(
    logical: str,
    field: str,
    mutation: dict[str, str],
    error: str,
) -> None:
    package = _package()
    package["tables"][logical][0][field] = mutation
    _refresh_package_fingerprint(package)

    with pytest.raises(StableReplayPackageV2Error, match=error):
        validate_package(package)


def test_nonstable_numeric_reference_is_rejected() -> None:
    rows, maps = _realistic_rows()
    rows["source_tag_observations"][0]["taxonomy_kb_id"] = 999

    with pytest.raises(
        StableReplayPackageV2Error,
        match="nonstable_numeric_reference_present",
    ):
        build_package_from_rows(rows, maps=maps)


def test_same_id_field_name_has_schema_path_specific_behavior() -> None:
    package = _package()
    queue = next(
        row
        for row in package["tables"]["source_metadata_records"]
        if row["metadata_kind"] == "pixiv_ingestion_gate"
    )

    assert queue["raw_metadata_json"]["id"] == 123456789
    assert queue["raw_metadata_json"]["user"]["id"] == 42
    assert "id" not in queue
    assert "source_metadata_record_id" not in queue["provenance"]


def test_unknown_graph_effective_field_fails_closed() -> None:
    rows, maps = _realistic_rows()
    rows["source_metadata_records"][1]["provenance"]["stable_identity_key"][
        "account_id"
    ] = "42"

    with pytest.raises(
        StableReplayPackageV2Error,
        match="unknown_graph_effective_field",
    ):
        build_package_from_rows(rows, maps=maps)


def test_v1_package_input_remains_immutable_during_v2_build() -> None:
    historical_v1 = {
        "package_version": "sv1b_acquired_nonderived_source_evidence_v1",
        "tables": {"source_metadata_records": [{"provider_record_key": "v1"}]},
        "fingerprint": "historical",
    }
    before = copy.deepcopy(historical_v1)

    _package()

    assert historical_v1 == before


def test_v2_export_is_deterministic_across_source_row_order() -> None:
    rows, maps = _realistic_rows()
    first = build_package_from_rows(rows, maps=maps)
    for values in rows.values():
        values.reverse()
    second = build_package_from_rows(rows, maps=maps)

    assert first["package_fingerprint"] == second["package_fingerprint"]
    assert first == second


def test_realistic_fresh_import_shape_reexports_exactly() -> None:
    primary = _package()
    replay_rows, replay_maps = _fresh_rows_from_package(primary)
    replay = build_package_from_rows(replay_rows, maps=replay_maps)
    comparison = compare_round_trip_packages(primary, replay)

    assert primary == replay
    assert comparison["passed"] is True
    assert comparison["missing_row_count"] == 0
    assert comparison["extra_row_count"] == 0


def test_round_trip_membership_counts_are_computed_not_hardcoded() -> None:
    primary = _package()
    replay_rows, replay_maps = _fresh_rows_from_package(primary)
    replacement = copy.deepcopy(replay_rows["source_name_registry"][0])
    replacement["id"] = 987654
    replacement["canonical_name_key"] = "different-creator"
    replacement["primary_display_name"] = "Different Creator"
    replacement["normalized_display_name"] = "Different Creator"
    replay_rows["source_name_registry"] = [replacement]
    replay = build_package_from_rows(replay_rows, maps=replay_maps)

    comparison = compare_round_trip_packages(primary, replay)

    assert comparison["passed"] is False
    assert comparison["missing_row_count"] == 1
    assert comparison["extra_row_count"] == 1
    assert comparison["missing_stable_membership_by_table"][
        "source_name_registry"
    ] == 1
    assert comparison["extra_stable_membership_by_table"][
        "source_name_registry"
    ] == 1
    assert len(comparison["mismatch_membership_fingerprint"]) == 64


def test_graph_effective_projection_and_trusted_verdict_counts_match() -> None:
    primary = _package()
    replay_rows, replay_maps = _fresh_rows_from_package(primary)
    replay = build_package_from_rows(replay_rows, maps=replay_maps)
    left = graph_effective_projection(primary)
    right = graph_effective_projection(replay)

    assert left["projection_fingerprint"] == right["projection_fingerprint"]
    assert left["trusted_complete_count"] == right["trusted_complete_count"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda package: package.pop("schema_version"),
        lambda package: package.__setitem__("schema_version", "v1"),
        lambda package: package.__setitem__("schema_fingerprint", "0" * 64),
    ],
)
def test_missing_malformed_or_unsupported_schema_is_rejected(mutation) -> None:
    package = _package()
    mutation(package)

    with pytest.raises(StableReplayPackageV2Error):
        validate_package(package)


def test_loss_ledger_is_field_level_complete_and_zero_loss() -> None:
    package = _package()
    ledger = package["preservation_loss_ledger"]
    entries = ledger["entries"]

    assert ledger["entry_count"] == len(entries)
    assert ledger["field_occurrence_count"] == sum(row["count"] for row in entries)
    assert ledger["loss_entry_count"] == 0
    assert ledger["graph_effective_loss_count"] == 0
    assert ledger["silent_loss_count"] == 0
    assert any(
        row["field_path"] == "$.stable_identity_key.work_id"
        and row["outcome"] == "preserved_graph_effective"
        for row in entries
    )


def test_restart_is_idempotent_at_logical_package_boundary() -> None:
    primary = _package()
    replay_rows, replay_maps = _fresh_rows_from_package(primary)
    first_reexport = build_package_from_rows(replay_rows, maps=replay_maps)
    second_reexport = build_package_from_rows(replay_rows, maps=replay_maps)

    assert first_reexport["package_fingerprint"] == primary["package_fingerprint"]
    assert second_reexport == first_reexport
    assert compare_round_trip_packages(primary, second_reexport)["passed"] is True


def test_all_external_provider_llm_and_media_routes_are_zero_budget() -> None:
    verify_external_routes_forbidden(EXTERNAL_ROUTE_BUDGET)
    package = _package()
    assert package["external_route_budget"] == {
        "provider_requests": 0,
        "gallery_dl_requests": 0,
        "llm_calls": 0,
        "media_downloads": 0,
        "thumbnail_downloads": 0,
    }

    with pytest.raises(StableReplayPackageV2Error, match="external_route_entered"):
        verify_external_routes_forbidden(
            {**EXTERNAL_ROUTE_BUDGET, "provider_requests": 1}
        )


def test_package_v2_module_has_no_external_execution_runner_import() -> None:
    source = Path(
        __import__(
            "scripts.stable_replay_package_v2",
            fromlist=["__file__"],
        ).__file__
    ).read_text(encoding="utf-8")

    assert "gallery_adapter" not in source
    assert "run_pixiv_metadata_ingestion" not in source
    assert "llm_translation" not in source
    assert "requests." not in source
    assert "httpx." not in source


def test_primary_identity_crosscheck_uses_immutable_stable_evidence() -> None:
    primary = _package()
    accepted_v1 = {
        "tables": {
            "source_metadata_records": copy.deepcopy(
                primary["tables"]["source_metadata_records"]
            )
        }
    }

    proof, ledger = cross_validate_primary_stable_identity(
        primary,
        accepted_v1,
        candidate_pages=[
            {
                "media_stable_key": "media-content-b",
                "stable_work_id": "123456789",
                "requested_page_index": 0,
            }
        ],
        final_work_outcomes=[{"work_id": "123456789"}],
    )

    assert proof["passed"] is True
    assert proof["accepted_provider_fact_mutation_count"] == 0
    assert proof["stable_identity_mismatch_count"] == 0
    assert proof["unsupported_stable_identity_count"] == 0
    assert proof["filename_or_row_order_identity_inference_used"] is False
    assert all(row["immutable_identity_support_passed"] for row in ledger)
    assert proof["support_classification_counts"][
        "accepted_checkpoint_immutability_support"
    ] == len(ledger)
    assert proof["support_classification_counts"][
        "independent_candidate_page_support"
    ] > 0


def test_primary_identity_crosscheck_blocks_accepted_provider_fact_mutation() -> None:
    primary = _package()
    accepted_rows = copy.deepcopy(primary["tables"]["source_metadata_records"])
    accepted_rows[0]["title"] = "Immutable accepted title"

    proof, _ = cross_validate_primary_stable_identity(
        primary,
        {"tables": {"source_metadata_records": accepted_rows}},
        candidate_pages=[],
        final_work_outcomes=[{"work_id": "123456789"}],
    )

    assert proof["passed"] is False
    assert proof["accepted_provider_fact_mutation_count"] == 1


def test_primary_identity_crosscheck_allows_baseline_immutability_without_guessing() -> None:
    rows, maps = _realistic_rows()
    for row in rows["source_metadata_records"]:
        row["raw_metadata_json"] = {"filename": "123456789_p0.jpg"}
    primary = build_package_from_rows(rows, maps=maps)
    accepted_v1 = {
        "tables": {
            "source_metadata_records": copy.deepcopy(
                primary["tables"]["source_metadata_records"]
            )
        }
    }

    proof, ledger = cross_validate_primary_stable_identity(
        primary,
        accepted_v1,
        candidate_pages=[],
        final_work_outcomes=[],
    )

    assert proof["passed"] is True
    assert proof["unsupported_stable_identity_count"] == 0
    assert all(
        row["accepted_checkpoint_immutability_support"] for row in ledger
    )
    assert proof["filename_or_row_order_identity_inference_used"] is False


def test_phase_acquired_identity_requires_independent_execution_or_raw_support() -> None:
    rows, maps = _realistic_rows()
    queue = rows["source_metadata_records"][1]
    queue["provenance"]["source"] = "gallery_dl_authenticated_metadata"
    queue["raw_metadata_json"] = {"filename": "must-not-infer-identity.jpg"}
    primary = build_package_from_rows(rows, maps=maps)
    accepted_v1 = {
        "tables": {
            "source_metadata_records": copy.deepcopy(
                primary["tables"]["source_metadata_records"]
            )
        }
    }

    proof, ledger = cross_validate_primary_stable_identity(
        primary,
        accepted_v1,
        phase_acquired_membership=[
            {
                "provider_record_key": queue["provider_record_key"],
            }
        ],
        candidate_pages=[
            {
                "media_stable_key": "media-content-b",
                "stable_work_id": "123456789",
                "requested_page_index": 0,
            }
        ],
        final_work_outcomes=[],
    )

    queue_ledger = next(
        row
        for row in ledger
        if row["provider_record_key"] == queue["provider_record_key"]
    )
    assert queue_ledger["phase_acquired_identity"] is True
    assert queue_ledger["independent_persisted_raw_support"] is False
    assert queue_ledger["independent_work_outcome_support"] is False
    assert queue_ledger["immutable_identity_support_passed"] is False
    assert proof["passed"] is False
    assert proof["phase_acquired_identity_unsupported_count"] == 1


def test_phase_acquired_identity_accepts_independent_route_viability_support() -> None:
    rows, maps = _realistic_rows()
    queue = rows["source_metadata_records"][1]
    queue["provenance"]["source"] = "gallery_dl_authenticated_metadata"
    queue["raw_metadata_json"] = {"filename": "must-not-infer-identity.jpg"}
    primary = build_package_from_rows(rows, maps=maps)
    accepted_v1 = {
        "tables": {
            "source_metadata_records": copy.deepcopy(
                primary["tables"]["source_metadata_records"]
            )
        }
    }
    work_id = str(queue["source_work_id"])

    proof, ledger = cross_validate_primary_stable_identity(
        primary,
        accepted_v1,
        phase_acquired_membership=[
            {
                "provider_record_key": queue["provider_record_key"],
            }
        ],
        candidate_pages=[
            {
                "media_stable_key": "media-content-b",
                "stable_work_id": work_id,
                "requested_page_index": 0,
            }
        ],
        final_work_outcomes=[],
        route_viability_attempts=[
            {
                "private_stable_work_reference": hashlib.sha256(
                    work_id.encode("utf-8")
                ).hexdigest(),
                "result_class": "route_viable",
                "route_viability": True,
                "returned_work_consistency": True,
            }
        ],
    )

    queue_ledger = next(
        row
        for row in ledger
        if row["provider_record_key"] == queue["provider_record_key"]
    )
    assert queue_ledger["phase_acquired_identity"] is True
    assert queue_ledger["independent_persisted_raw_support"] is False
    assert queue_ledger["independent_work_outcome_support"] is False
    assert queue_ledger["independent_route_viability_support"] is True
    assert queue_ledger["immutable_identity_support_passed"] is True
    assert proof["passed"] is True
    assert proof["phase_acquired_identity_unsupported_count"] == 0


def test_phase_provenance_cannot_fall_open_when_candidate_support_is_missing() -> None:
    rows, maps = _realistic_rows()
    queue = rows["source_metadata_records"][1]
    queue["provenance"]["source"] = "gallery_dl_authenticated_metadata"
    queue["raw_metadata_json"] = {"filename": "must-not-infer-identity.jpg"}
    primary = build_package_from_rows(rows, maps=maps)
    accepted_v1 = {
        "tables": {
            "source_metadata_records": copy.deepcopy(
                primary["tables"]["source_metadata_records"]
            )
        }
    }
    membership = {"provider_record_key": queue["provider_record_key"]}

    proof, ledger = cross_validate_primary_stable_identity(
        primary,
        accepted_v1,
        phase_acquired_membership=[membership],
        candidate_pages=[],
        final_work_outcomes=[],
    )

    queue_ledger = next(
        row
        for row in ledger
        if row["provider_record_key"] == queue["provider_record_key"]
    )
    assert queue_ledger["phase_acquired_identity"] is True
    assert queue_ledger["independent_candidate_page_support"] is False
    assert queue_ledger["immutable_identity_support_passed"] is False
    assert proof["expected_phase_acquired_identity_count"] == 1
    assert proof["observed_phase_acquired_identity_count"] == 1
    assert proof["phase_acquired_identity_unsupported_count"] == 1
    assert proof["passed"] is False


def test_phase_provenance_missing_immutable_membership_fails_closed() -> None:
    rows, maps = _realistic_rows()
    queue = rows["source_metadata_records"][1]
    queue["provenance"]["source"] = "gallery_dl_authenticated_metadata"
    queue["raw_metadata_json"] = {"filename": "must-not-infer-identity.jpg"}
    primary = build_package_from_rows(rows, maps=maps)
    accepted_v1 = {
        "tables": {
            "source_metadata_records": copy.deepcopy(
                primary["tables"]["source_metadata_records"]
            )
        }
    }

    proof, ledger = cross_validate_primary_stable_identity(
        primary,
        accepted_v1,
        phase_acquired_membership=[],
        historical_baseline_provider_keys=[],
        candidate_pages=[],
        final_work_outcomes=[],
    )

    queue_ledger = next(
        row
        for row in ledger
        if row["provider_record_key"] == queue["provider_record_key"]
    )
    assert queue_ledger["phase_acquired_identity"] is False
    assert proof["phase_provenance_but_missing_phase_membership_count"] == 1
    assert proof["passed"] is False
