"""Synthetic/offline coverage for the SCV2-PX1 vertical slice."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.pixiv_metadata_ingestion_service import (
    PixivMetadataGateError,
    parse_gallery_dl_stdout,
)
from app.services.pixiv_metadata_projection_service import (
    PIXIV_AGGREGATE_SCHEMA,
    PIXIV_PUBLIC_SUMMARY_SCHEMA,
    PIXIV_SIGNAL_BUNDLE_SCHEMA,
    PixivMetadataProjectionError,
    build_canonical_pixiv_work_page_aggregate,
    canonical_fingerprint,
    project_pixiv_aggregate_to_source_concept_signals,
)
from app.services.pixiv_metadata_vertical_slice_service import (
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
    write_synthetic_vertical_slice_evidence,
)


def _stdout(records: object) -> str:
    return json.dumps(records, ensure_ascii=False)


def test_canonical_normalizer_is_order_and_duplicate_stable() -> None:
    left = {
        "provider": "pixiv",
        "id": 700000101,
        "num": 0,
        "page_count": 1,
        "title": "Ｓynthetic Title",
        "user": {"id": 800000101, "name": "Synthetic Creator"},
        "tags": ["tag_b", "tag_a", "tag_b"],
        "private_extra": "z",
    }
    right = copy.deepcopy(left)
    right["tags"] = ["tag_a", "tag_b"]
    right["private_extra"] = "a"

    forward = parse_gallery_dl_stdout(_stdout([left, right]), "700000101")
    reverse = parse_gallery_dl_stdout(_stdout([right, left]), "700000101")

    assert forward == reverse
    assert forward[0]["title"] == "Synthetic Title"
    assert forward[0]["tags"] == ("tag_a", "tag_b")
    assert forward[0]["raw"]["private_extra"] == "a"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            [
                {"provider": "pixiv", "id": 700000102, "num": 0, "user_id": 1},
                {"provider": "pixiv", "id": 700000102, "num": 0, "user_id": 2},
            ],
            "metadata_normalization_failed_conflicting_duplicate_page",
        ),
        (
            [
                {"provider": "pixiv", "id": 700000102, "num": 0},
                {"provider": "pixiv", "id": 700000103, "num": 0},
            ],
            "provider_identity_mismatch",
        ),
        (
            [{"provider": "synthetic_unknown", "id": 700000102, "num": 0}],
            "metadata_normalization_failed_unknown_provider",
        ),
        (
            [{"provider": "pixiv", "id": 700000102, "num": 2, "page_count": 2}],
            "metadata_normalization_failed_page_count_mismatch",
        ),
    ],
)
def test_canonical_normalizer_rejects_conflict_identity_provider_and_page_mismatch(
    payload: object,
    expected: str,
) -> None:
    with pytest.raises(PixivMetadataGateError, match=expected):
        parse_gallery_dl_stdout(_stdout(payload), "700000102")


def test_canonical_normalizer_rejects_malformed_json() -> None:
    with pytest.raises(
        PixivMetadataGateError,
        match="metadata_normalization_failed_malformed_json",
    ):
        parse_gallery_dl_stdout("{synthetic malformed", "700000104")


def test_aggregate_rejects_unknown_provider_and_mixed_work_page() -> None:
    base = {
        "provider": "pixiv",
        "source_work_id": "700000201",
        "source_page_index": 0,
        "metadata_kind": "provider_metadata",
        "data_type_label": "fixture",
        "status": "observed",
        "raw_metadata_json": {},
        "provenance": {},
    }
    wrong_provider = {**base, "provider": "synthetic_unknown"}
    with pytest.raises(PixivMetadataProjectionError, match="provider_invalid"):
        build_canonical_pixiv_work_page_aggregate([wrong_provider])

    mixed = {**base, "source_work_id": "700000202"}
    with pytest.raises(PixivMetadataProjectionError, match="scope_mixed"):
        build_canonical_pixiv_work_page_aggregate([base, mixed])


def _run(tmp_path: Path, name: str) -> dict[str, object]:
    return run_synthetic_pixiv_vertical_slice(
        workspace=tmp_path / name,
        fixture=repository_synthetic_pixiv_fixture(),
    )


def test_single_command_vertical_slice_is_stable_public_and_offline(tmp_path: Path) -> None:
    first = _run(tmp_path, "first")
    second = _run(tmp_path, "second")

    assert first == second
    assert first["schema_version"] == PIXIV_PUBLIC_SUMMARY_SCHEMA
    assert first["synthetic_vertical_slice_verified"] is True
    assert first["deterministic_replay"] is True
    assert first["input_order_stable"] is True
    assert first["canonical_projection_fingerprint"] == first[
        "replay_projection_fingerprint"
    ]
    assert first["canonical_projection_fingerprint"] == first[
        "reversed_input_projection_fingerprint"
    ]
    assert first["operation_receipt"] == {
        "schema_version": "violet.scv2-px1-offline-operation-receipt.v1",
        "fixture_source": "repository_owned_new_synthetic_only",
        "temporary_workspace_enforced": True,
        "task_owned_temporary_database_count": 2,
        "existing_database_read_count": 0,
        "existing_database_write_count": 0,
        "app_storage_access_count": 0,
        "provider_network_activity_count": 0,
        "media_network_activity_count": 0,
        "subprocess_activity_count": 0,
        "credential_access_count": 0,
        "source_root_access_count": 0,
        "entity_truth_write_count": 0,
        "source_concept_materialization_count": 0,
        "media_tag_write_count": 0,
        "real_provider_authorized": False,
        "real_source_authorized": False,
        "production_authorized": False,
    }

    text = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert "synthetic-secret-sentinel" not in text
    assert "synthetic-cookie-never-published" not in text
    assert "C:\\Private" not in text
    assert "raw_metadata_json" not in text
    assert "local_path" not in text


def test_vertical_slice_covers_lifecycle_completeness_and_rejections(
    tmp_path: Path,
) -> None:
    summary = _run(tmp_path, "coverage")
    aggregates = summary["aggregates"]
    assert all(item["schema_version"] == PIXIV_AGGREGATE_SCHEMA for item in aggregates)
    assert summary["disposition_counts"] == {
        "complete": 3,
        "conflict": 1,
        "page_mismatch": 1,
        "retryable": 1,
        "terminal": 1,
        "unsupported": 2,
    }
    assert summary["rejected_cases"] == [
        {
            "case_id": "malformed_json",
            "reason": "metadata_normalization_failed_malformed_json",
        },
        {
            "case_id": "unknown_provider",
            "reason": "metadata_normalization_failed_unknown_provider",
        },
    ]

    by_key = {item["stable_work_page_key"]: item for item in aggregates}
    sparse = by_key["pixiv:work:700000002:page:1"]
    assert sparse["disposition"] == "complete"
    assert sparse["metadata_completeness"]["classification"] == "partial_or_uncertain"
    assert sparse["metadata_completeness"]["missing_fields"] == [
        "title_observation",
        "tag_observations",
    ]
    mismatch = by_key["pixiv:work:700000005:page:1"]
    assert mismatch["disposition"] == "page_mismatch"
    assert mismatch["creator"]["provider_creator_id"] is None


def test_creator_id_anchors_mutable_names_and_conflicts_never_name_union(
    tmp_path: Path,
) -> None:
    summary = _run(tmp_path, "creator")
    bundles = {
        item["stable_work_page_key"]: item for item in summary["signal_bundles"]
    }
    p0 = bundles["pixiv:work:700000002:page:0"]
    p1 = bundles["pixiv:work:700000002:page:1"]
    p0_anchors = {
        item["identity_anchor"] for item in p0["signals"] if item["identity_anchor"]
    }
    p1_anchors = {
        item["identity_anchor"] for item in p1["signals"] if item["identity_anchor"]
    }
    assert p0_anchors == p1_anchors == {"provider-account:pixiv:800000001"}
    assert {
        item["raw_value"]
        for item in p0["signals"] + p1["signals"]
        if item["role_hint"] == "artist"
    } >= {
        "Synthetic Creator Alpha",
        "Synthetic Creator Alpha Renamed",
        "synthetic_alpha",
        "synthetic_alpha_new",
    }

    conflict = bundles["pixiv:work:700000006:page:0"]
    assert conflict["strong_identity_anchor_count"] == 0
    assert conflict["name_only_identity_anchor_count"] == 0
    assert all(
        item["identity_anchor"] is None
        for item in conflict["signals"]
        if item["role_hint"] == "artist"
    )


def test_work_page_context_prevents_cross_context_signal_union(tmp_path: Path) -> None:
    summary = _run(tmp_path, "context")
    bundles = summary["signal_bundles"]
    assert all(item["schema_version"] == PIXIV_SIGNAL_BUNDLE_SCHEMA for item in bundles)
    contextual_keys_by_work: dict[str, set[str]] = {}
    for bundle in bundles:
        work_id = bundle["stable_work_page_key"].split(":")[2]
        contextual_keys_by_work.setdefault(work_id, set()).update(
            signal["signal_key"]
            for signal in bundle["signals"]
            if signal["work_context_key"] is not None
        )
        assert bundle["cross_context_union_count"] == 0
    work_ids = sorted(contextual_keys_by_work)
    for index, left in enumerate(work_ids):
        for right in work_ids[index + 1 :]:
            assert contextual_keys_by_work[left].isdisjoint(
                contextual_keys_by_work[right]
            )


def test_aggregate_and_signal_fingerprints_do_not_use_database_row_ids(
    tmp_path: Path,
) -> None:
    fixture = repository_synthetic_pixiv_fixture()
    shifted = copy.deepcopy(fixture)
    for case in shifted["cases"]:
        case["media_id"] += 10_000
    first = run_synthetic_pixiv_vertical_slice(
        workspace=tmp_path / "original", fixture=fixture
    )
    second = run_synthetic_pixiv_vertical_slice(
        workspace=tmp_path / "shifted", fixture=shifted
    )
    assert first["canonical_projection_fingerprint"] == second[
        "canonical_projection_fingerprint"
    ]
    assert [item["canonical_fingerprint"] for item in first["aggregates"]] == [
        item["canonical_fingerprint"] for item in second["aggregates"]
    ]


def test_aggregate_fingerprint_mutation_is_rejected(tmp_path: Path) -> None:
    summary = _run(tmp_path, "mutation")
    aggregate = copy.deepcopy(summary["aggregates"][0])
    aggregate["work_id"] = "700009999"
    with pytest.raises(PixivMetadataProjectionError, match="fingerprint_invalid"):
        project_pixiv_aggregate_to_source_concept_signals(aggregate)


def test_evidence_writer_uses_fixed_files_and_refuses_overwrite(tmp_path: Path) -> None:
    fixture = repository_synthetic_pixiv_fixture()
    evidence_dir = tmp_path / "evidence"
    summary = run_synthetic_pixiv_vertical_slice(
        workspace=evidence_dir,
        fixture=fixture,
    )
    fingerprints = write_synthetic_vertical_slice_evidence(
        evidence_dir,
        fixture=fixture,
        summary=summary,
    )
    assert set(fingerprints) == {
        "aggregates.json",
        "operation-receipt.json",
        "public-summary.json",
        "signal-bundles.json",
        "synthetic-fixture.json",
    }
    for name, expected in fingerprints.items():
        payload = json.loads((evidence_dir / name).read_text(encoding="utf-8"))
        assert canonical_fingerprint(payload) == expected
    with pytest.raises(PixivMetadataGateError, match="already_exists"):
        write_synthetic_vertical_slice_evidence(
            evidence_dir,
            fixture=fixture,
            summary=summary,
        )


def test_task_workspace_outside_os_temp_fails_closed() -> None:
    with pytest.raises(PixivMetadataGateError, match="task_owned_temporary"):
        run_synthetic_pixiv_vertical_slice(
            workspace=Path(__file__).resolve().parents[1] / ".not-authorized-px1",
            fixture=repository_synthetic_pixiv_fixture(),
        )
