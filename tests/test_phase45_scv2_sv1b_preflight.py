from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure import (
    SV1BPreflightError,
    _strict_test_database,
    canonical_work_id,
    outcome_for_pair,
    validate_output_root,
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


def test_output_root_must_be_new_and_private(tmp_path: Path) -> None:
    with pytest.raises(SV1BPreflightError, match="private_output_root_escape"):
        validate_output_root(tmp_path / "outside")
