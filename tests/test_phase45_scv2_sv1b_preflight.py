from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure import (
    SV1BPreflightError,
    _strict_test_database,
    audit_runtime_parser_denominator_rows,
    canonical_work_id,
    finalize_r2r_proposal_classifications,
    outcome_for_pair,
    public_console_summary,
    validate_output_root,
    validate_owned_output_root,
    validate_writable_databases,
)


def test_outcome_for_pair_preserves_terminal_and_page_local_precedence() -> None:
    records = [
        {"source_work_id": "123", "source_page_index": 0, "status": "metadata_complete"},
        {"source_work_id": "123", "source_page_index": 1, "status": "terminal_remote_unavailable"},
        {"source_work_id": "124", "source_page_index": 0, "status": "deferred_nonblocking_source_page_mismatch"},
    ]
    assert outcome_for_pair(records, "123", 0) == "accepted_metadata_complete"
    assert outcome_for_pair(records, "123", 1) == "accepted_terminal_remote_unavailable"
    assert outcome_for_pair(records, "124", 9) == "accepted_deferred_nonblocking_source_page_mismatch"
    assert outcome_for_pair(records, "125", 0) == "unacquired"


@pytest.mark.parametrize("database", ["blombooru", "postgres", "template0", "template1", "production"])
def test_strict_test_database_rejects_default_and_production(database: str) -> None:
    assert _strict_test_database(database) is False


def test_strict_test_database_accepts_sv1b_identity() -> None:
    assert _strict_test_database("blombooru_scv2_sv1b_metadata_graph_closure_test_20260719") is True


def test_work_id_canonicalization_removes_historical_leading_zeroes() -> None:
    assert canonical_work_id("000123") == "123"
    with pytest.raises(SV1BPreflightError, match="out_of_range"):
        canonical_work_id("0")


def test_runtime_parser_audit_accepts_exact_scv2_denominator_membership() -> None:
    result = audit_runtime_parser_denominator_rows([
        {"id": 1, "hash": "a", "filename": "1234567.jpg", "path": "media/original/1234567.jpg"},
        {"id": 2, "hash": "b", "filename": "001234567-P2.png", "path": "media/original/001234567-P2.png"},
        {"id": 3, "hash": "c", "filename": "plain.jpg", "path": "media/original/plain.jpg"},
    ])
    assert result["passed"] is True
    assert result["accepted_candidate_media_count"] == 2
    assert result["durable_candidate_media_count"] == 2


def test_runtime_parser_audit_fails_closed_on_parser_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.parse_approved_fields",
        lambda _fields: (),
    )
    with pytest.raises(SV1BPreflightError, match="runtime_parser_denominator_mismatch"):
        audit_runtime_parser_denominator_rows([
            {"id": 1, "hash": "a", "filename": "1234567.jpg", "path": "media/original/1234567.jpg"},
        ])


def test_output_root_must_be_new_and_private(tmp_path: Path) -> None:
    with pytest.raises(SV1BPreflightError, match="private_output_root_escape"):
        validate_output_root(tmp_path / "outside")


def test_writable_database_validation_rejects_overlap_before_exists_check(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.database_exists",
        lambda _database: False,
    )
    with pytest.raises(SV1BPreflightError, match="overlaps_accepted"):
        validate_writable_databases(
            "blombooru_scv2_sv1_controlled_scale_test_20260718",
            "blombooru_new_replay_test",
        )


def test_writable_database_validation_rejects_preexisting_unowned_database(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.database_exists",
        lambda database: database.endswith("primary_test"),
    )
    with pytest.raises(SV1BPreflightError, match="ownership_unproven"):
        validate_writable_databases("blombooru_sv1b_primary_test", "blombooru_sv1b_replay_test")


def test_owned_output_requires_exact_phase_and_database_ownership(tmp_path: Path) -> None:
    output = tmp_path / ".local_manifests" / "run"
    output.mkdir(parents=True)
    with pytest.raises(SV1BPreflightError, match="private_output_root_invalid|proof_missing"):
        validate_owned_output_root(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )


def test_public_console_summary_is_aggregate_allowlist() -> None:
    result = {
        "phase": "SCV2-SV1B",
        "status": "blocked_sv1b_provider_authentication",
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "manual_acceptance_status": "not_generated_provider_gate_blocked",
        "candidate_manifest": {
            "canonical_candidate_media_count": 6496,
            "page_media_manifest_row_count": 7757,
            "distinct_work_count": 7028,
            "private_media_id": "must-not-escape",
        },
        "provider_hardening": {"provider_request_count": 0, "provider_attempt_count": 0},
        "environment_isolation": {"passed": True, "absolute_path": "must-not-escape"},
        "accepted_nonderived_evidence": {
            "primary_import": {"inserted_total": 36342, "raw_rows": ["must-not-escape"]},
            "replay_import": {"inserted_total": 36342},
            "primary_reconciliation_passed": True,
            "replay_reconciliation_passed": True,
        },
    }
    public = public_console_summary(result)
    serialized = json.dumps(public, sort_keys=True)
    assert "must-not-escape" not in serialized
    assert public["private_values_exposed"] is False


@pytest.mark.parametrize(
    ("accepted", "expected"),
    [
        ({"a": "must_link"}, {"a": "comparable"}),
        ({"a": "must_link", "b": "must_link"}, {"a": "ambiguous_remap", "b": "ambiguous_remap"}),
        ({"a": "must_link", "b": "cannot_link"}, {"a": "conflicting_remap", "b": "conflicting_remap"}),
    ],
)
def test_r2r_target_collision_classification_is_fail_closed(accepted, expected) -> None:
    preliminary = {pair_id: {"classification": "proposed_comparable"} for pair_id in accepted}
    finalize_r2r_proposal_classifications(
        preliminary,
        {"target": list(accepted)},
        accepted,
    )
    assert {pair_id: row["classification"] for pair_id, row in preliminary.items()} == expected
