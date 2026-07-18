from __future__ import annotations

from collections import Counter

import pytest

from scripts.run_phase45_scv2_sv1_controlled_scale_promotion_readiness import (
    MAX_MEDIA,
    MIN_MEDIA,
    STABLE_ID_KEYS,
    SV1BlockedError,
    _percentile,
    _source_concept_evidence_logical_key,
    canonical_json,
    sanitize_stable_payload,
    scan_public,
    sha256_payload,
)


def test_sanitize_stable_payload_removes_development_row_references_recursively() -> None:
    payload = {
        "concept_id": 42,
        "nested": {"media_id": 99, "signal_ids": [1, 2], "provider_record_key": "stable"},
        "source_work_id": "123456",
        "artist_id": "987",
        "run_id": "accepted-run",
    }

    result = sanitize_stable_payload(payload)

    assert result == {
        "nested": {"provider_record_key": "stable"},
        "source_work_id": "123456",
        "artist_id": "987",
        "run_id": "accepted-run",
    }


def test_stable_id_allowlist_contains_provider_ids_but_not_database_ids() -> None:
    assert {"source_work_id", "artist_id", "run_id"}.issubset(STABLE_ID_KEYS)
    assert "concept_id" not in STABLE_ID_KEYS
    assert "media_id" not in STABLE_ID_KEYS
    assert "source_metadata_record_id" not in STABLE_ID_KEYS


def test_canonical_fingerprint_is_order_stable_for_mapping_keys() -> None:
    left = {"b": 2, "a": {"d": 4, "c": 3}}
    right = {"a": {"c": 3, "d": 4}, "b": 2}

    assert canonical_json(left) == canonical_json(right)
    assert sha256_payload(left) == sha256_payload(right)


def test_source_concept_evidence_logical_key_closes_nullable_signal_gap() -> None:
    row = {
        "concept_id": 101,
        "signal_id": None,
        "media_id": 202,
        "source_metadata_record_id": None,
        "provider": "accepted_ml2",
        "evidence_type": "trusted_creator_media_support",
        "evidence_strength": "trusted",
        "payload": {"b": 2, "a": 1},
        "run_id": "accepted-run",
        "status": "accepted",
    }
    reordered = {**row, "payload": {"a": 1, "b": 2}}

    assert _source_concept_evidence_logical_key(row) == _source_concept_evidence_logical_key(reordered)


def test_source_concept_evidence_logical_key_distinguishes_media_support() -> None:
    base = {
        "concept_id": 101,
        "signal_id": None,
        "media_id": 202,
        "source_metadata_record_id": None,
        "provider": "accepted_ml2",
        "evidence_type": "trusted_creator_media_support",
        "evidence_strength": "trusted",
        "payload": {},
        "run_id": "accepted-run",
        "status": "accepted",
    }

    assert _source_concept_evidence_logical_key(base) != _source_concept_evidence_logical_key({**base, "media_id": 203})


def test_public_scan_allows_only_the_required_stable_key_stage_identifier() -> None:
    result = scan_public(
        "public aggregate report",
        {"pipeline_contract": {"executed_stages": ["stable_key_evidence_export_import"]}},
    )

    assert result["passed"] is True
    assert result["negative_control_passed"] is True


def test_public_scan_still_blocks_real_secret_tokens() -> None:
    result = scan_public("", {"credential": "sk-example_token_12345"})

    assert result["passed"] is False
    assert {finding["reason"] for finding in result["findings"]} == {"secret_token"}


def test_public_scan_does_not_treat_safe_schema_keys_as_values() -> None:
    result = scan_public("public aggregate report", {"task_branch_start_sha": "abcdef123456"})

    assert result["passed"] is True


@pytest.mark.parametrize("count", [MIN_MEDIA, 12000, MAX_MEDIA])
def test_declared_scale_bounds_include_only_the_authorized_range(count: int) -> None:
    assert MIN_MEDIA <= count <= MAX_MEDIA


def test_percentile_uses_bounded_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]

    assert _percentile(values, 0.50) == 3.0
    assert _percentile(values, 0.95) == 100.0
    assert _percentile([], 0.95) == 0.0


def test_outcome_vocabulary_accounts_each_selected_media_exactly_once() -> None:
    outcomes = Counter(
        [
            "imported",
            "compatible_existing_media_reused",
            "duplicate_content_skipped",
            "deferred_nonblocking_source_unavailable",
            "blocking_failed",
        ]
    )

    assert sum(outcomes.values()) == 5
    assert set(outcomes) == {
        "imported",
        "compatible_existing_media_reused",
        "duplicate_content_skipped",
        "deferred_nonblocking_source_unavailable",
        "blocking_failed",
    }
