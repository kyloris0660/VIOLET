"""Regression coverage for the owner-adjudicated SCV2-PX1 correction."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    SourceMetadataEvidence,
    SourceMetadataRecord,
    SourceNameObservation,
    SourceTagObservation,
)
from app.services.creator_identity_policy import stable_creator_identity_key
from app.services.pixiv_identity_policy import (
    canonical_pixiv_creator_id,
    canonical_pixiv_work_id,
    is_allowlisted_pixiv_provider_marker,
)
from app.services.pixiv_metadata_ingestion_service import (
    PIXIV_LEGACY_NORMALIZER_VERSION,
    PIXIV_METADATA_NORMALIZER_VERSION,
    PixivMetadataGateError,
    PixivMetadataState,
    normalize_gallery_dl_records,
    parse_gallery_dl_stdout,
    persist_complete_work,
    queue_media_for_pixiv_metadata,
)
from app.services.pixiv_metadata_projection_service import (
    PIXIV_AGGREGATE_VERSION,
    PixivMetadataProjectionError,
    assert_public_safe_projection,
    build_canonical_pixiv_aggregates_from_session,
    build_canonical_pixiv_work_page_aggregate,
    canonical_fingerprint,
    canonical_json_bytes,
    derive_pixiv_work_consistency,
    project_pixiv_aggregate_to_source_concept_signals,
)
from app.services.source_concept_resolver_service import (
    SourceConceptSignalInput,
    build_source_concept_signal_drafts,
)
from scripts import run_phase45_px1_pixiv_metadata_and_dedup_dry_run as legacy_px1


@pytest.fixture(autouse=True)
def px1_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = tmp_path / "runtime-storage"
    storage.mkdir()
    monkeypatch.setenv("VIOLET_SKIP_DOTENV", "1")
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(storage))


@pytest.mark.parametrize("value", [1, "1", 999999999999, "999999999999"])
def test_canonical_pixiv_numeric_identity_accepts_full_ingestion_range(value: object) -> None:
    expected = str(value)
    assert canonical_pixiv_work_id(value) == expected
    assert canonical_pixiv_creator_id(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, "", " 1", "1 ", "01", 0, "0", -1, "-1", True, False, 1.0, "1.0", "x", "1000000000000"],
)
def test_canonical_pixiv_numeric_identity_rejects_noncanonical_values(value: object) -> None:
    assert canonical_pixiv_work_id(value) is None
    assert canonical_pixiv_creator_id(value) is None


def test_pixiv_provider_markers_use_exact_allowlist() -> None:
    assert is_allowlisted_pixiv_provider_marker("pixiv") is True
    assert is_allowlisted_pixiv_provider_marker(" PIXIV ") is True
    for marker in ("not_pixiv", "fakepixiv", "untrusted-pixiv-proxy", "", None):
        assert is_allowlisted_pixiv_provider_marker(marker) is False

    markerless = [{"id": 700000020, "num": 0, "user": {"id": 800000020}}]
    assert normalize_gallery_dl_records(
        markerless, "700000020", provider_marker="pixiv"
    )[0]["creator_id"] == "800000020"
    with pytest.raises(PixivMetadataGateError, match="unknown_provider"):
        normalize_gallery_dl_records(
            markerless, "700000020", provider_marker="fakepixiv"
        )
    with pytest.raises(PixivMetadataGateError, match="unknown_provider"):
        parse_gallery_dl_stdout(
            json.dumps(
                [{"provider": "not_pixiv", "id": 700000020, "num": 0}]
            ),
            "700000020",
        )


@pytest.mark.parametrize("creator_id", [True, -1, "-1", "01", "name-only"])
def test_normalizer_rejects_noncanonical_creator_identity(creator_id: object) -> None:
    with pytest.raises(PixivMetadataGateError, match="creator_id_invalid"):
        normalize_gallery_dl_records(
            [
                {
                    "provider": "pixiv",
                    "id": 700000021,
                    "num": 0,
                    "user": {"id": creator_id},
                }
            ],
            "700000021",
        )


@pytest.mark.parametrize("creator_id", [None, "", "name-only", "01", -1, True])
def test_invalid_or_name_only_pixiv_creator_never_forms_strong_anchor(
    creator_id: object,
) -> None:
    signal = SimpleNamespace(
        provider="pixiv",
        raw_value="Mutable Display Name",
        display_value="Mutable Display Name",
        evidence_payload={"stable_creator_id": creator_id},
    )
    assert stable_creator_identity_key(signal) is None


def _source_record(
    *,
    token: str,
    work_id: str = "700000030",
    page_index: int = 0,
    creator_id: object = "800000030",
    creator_name: str | None = "Synthetic Creator",
    creator_account: str | None = "synthetic_creator",
    page_count: object = 1,
    status: str = "observed",
    normalizer_version: str | None = PIXIV_METADATA_NORMALIZER_VERSION,
    parser_version: str | None = "synthetic_parser_v1",
    provenance_source: str = "gallery_dl_authenticated_metadata",
    provenance_work_id: str | None = None,
    provenance_page_index: int | None = None,
    title: str | None = "Synthetic Work",
) -> dict[str, object]:
    raw: dict[str, object] = {
        "provider": "pixiv",
        "id": int(work_id),
        "num": page_index,
        "page_count": page_count,
    }
    if creator_account is not None:
        raw["creator_account"] = creator_account
    if normalizer_version is not None:
        raw["_pixiv_metadata_normalizer_version"] = normalizer_version
    return {
        "record_token": token,
        "provider": "pixiv",
        "source_work_id": work_id,
        "source_page_index": page_index,
        "metadata_kind": "provider_metadata",
        "data_type_label": "authenticated_provider_metadata",
        "status": status,
        "title": title,
        "artist_id": creator_id,
        "artist_name": creator_name,
        "raw_metadata_json": raw,
        "provenance": {
            "source": provenance_source,
            "parser_version": parser_version,
            "stable_identity_key": {
                "provider": "pixiv",
                "work_id": provenance_work_id or work_id,
                "page_index": (
                    page_index
                    if provenance_page_index is None
                    else provenance_page_index
                ),
            },
        },
    }


def test_valid_creator_id_without_names_remains_px2_stable_anchor() -> None:
    aggregate = build_canonical_pixiv_work_page_aggregate(
        [
            _source_record(
                token="anchor-only",
                creator_name=None,
                creator_account=None,
            )
        ]
    )
    bundle = project_pixiv_aggregate_to_source_concept_signals(aggregate)
    assert aggregate["creator"]["provider_creator_id"] == "800000030"
    assert {
        signal["identity_anchor"]
        for signal in bundle["signals"]
        if signal["identity_anchor"]
    } == {"provider-account:pixiv:800000030"}


def test_invalid_creator_id_is_explicit_conflict_and_name_does_not_anchor() -> None:
    aggregate = build_canonical_pixiv_work_page_aggregate(
        [_source_record(token="invalid-creator", creator_id="name-only")]
    )
    bundle = project_pixiv_aggregate_to_source_concept_signals(aggregate)
    assert aggregate["disposition"] == "conflict"
    assert "invalid_trusted_provider_creator_id" in aggregate["conflict_reasons"]
    assert aggregate["creator"]["provider_creator_id"] is None
    assert bundle["strong_identity_anchor_count"] == 0
    assert all(signal["identity_anchor"] is None for signal in bundle["signals"])


@pytest.mark.parametrize("page_count", [1, 2, 4])
@pytest.mark.parametrize("reverse", [False, True])
def test_historical_parser_compatibility_selects_every_requested_page_for_3_plus(
    page_count: int,
    reverse: bool,
) -> None:
    records = [
        {
            "id": 700000040,
            "num": page_index,
            "page_count": page_count,
            "title": f"Synthetic Page {page_index}",
            "user": {"id": 800000040},
        }
        for page_index in range(page_count)
    ]
    records.append(dict(records[-1]))
    if reverse:
        records.reverse()
    requested_indexes = sorted({0, page_count // 2, page_count - 1})
    for requested in requested_indexes:
        candidate = legacy_px1.MediaCandidate(
            media_id=100 + requested,
            filename=f"700000040_p{requested}.png",
            content_class="anime",
            pixiv_like=True,
            pixiv_work_ids=("700000040",),
            pixiv_page_indexes=(requested,),
            pixiv_prior_reasons=("filename_pixiv_id_pattern",),
        )
        projected = legacy_px1.normalize_gallery_dl_metadata(
            records,
            candidate=candidate,
            raw_stdout_path=None,
        )
        assert projected is not None
        assert projected["page_index"] == requested
        assert projected["title"] == f"Synthetic Page {requested}"


def _new_sqlite_session(database_path: Path):
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            SourceMetadataRecord.__table__,
            SourceMetadataEvidence.__table__,
            SourceNameObservation.__table__,
            SourceTagObservation.__table__,
        ],
    )
    return engine, sessionmaker(bind=engine)()


def test_mixed_database_excludes_not_applicable_before_identity_requirement(
    tmp_path: Path,
) -> None:
    engine, session = _new_sqlite_session(tmp_path / "mixed.sqlite3")
    try:
        session.add(
            SourceMetadataRecord(
                provider="pixiv",
                provider_record_key="synthetic:not-applicable",
                media_id=1,
                metadata_kind="pixiv_ingestion_gate",
                data_type_label="local_runtime_source_prior",
                status=PixivMetadataState.NOT_APPLICABLE.value,
                raw_metadata_json={"synthetic": True},
                provenance={"source": "synthetic_fixture"},
            )
        )
        session.add(
            SourceMetadataRecord(
                provider="pixiv",
                provider_record_key="synthetic:valid",
                media_id=2,
                source_work_id="700000050",
                source_page_index=0,
                metadata_kind="provider_metadata",
                data_type_label="authenticated_provider_metadata",
                status="observed",
                title="Synthetic Mixed DB",
                artist_id="800000050",
                raw_metadata_json={
                    "id": 700000050,
                    "num": 0,
                    "page_count": 1,
                    "_pixiv_metadata_normalizer_version": PIXIV_METADATA_NORMALIZER_VERSION,
                },
                provenance={
                    "source": "gallery_dl_authenticated_metadata",
                    "parser_version": "synthetic_parser_v1",
                    "stable_identity_key": {
                        "provider": "pixiv",
                        "work_id": "700000050",
                        "page_index": 0,
                    },
                },
            )
        )
        session.commit()
        aggregates = build_canonical_pixiv_aggregates_from_session(session)
        assert [item["stable_work_page_key"] for item in aggregates] == [
            "pixiv:work:700000050:page:0"
        ]
    finally:
        session.close()
        engine.dispose()


def _cross_database_reuse_projection(
    database_path: Path,
    *,
    media_ids: tuple[int, int],
    filler_count: int,
) -> tuple[dict[str, object], dict[str, object], tuple[int, ...]]:
    engine, session = _new_sqlite_session(database_path)
    try:
        for index in range(filler_count):
            session.add(
                SourceMetadataRecord(
                    provider="pixiv",
                    provider_record_key=f"synthetic:filler:{index}",
                    media_id=10_000 + index,
                    metadata_kind="pixiv_ingestion_gate",
                    data_type_label="local_runtime_source_prior",
                    status=PixivMetadataState.NOT_APPLICABLE.value,
                    raw_metadata_json={"synthetic": True},
                    provenance={"source": "synthetic_fixture"},
                )
            )
        session.flush()
        queue_media_for_pixiv_metadata(
            session,
            {
                "id": media_ids[0],
                "filename": "700000060_p0.png",
                "path": "synthetic/700000060_p0.png",
            },
        )
        session.flush()
        first = (
            session.query(SourceMetadataRecord)
            .filter(
                SourceMetadataRecord.media_id == media_ids[0],
                SourceMetadataRecord.source_work_id == "700000060",
            )
            .one()
        )
        persist_complete_work(
            session,
            "700000060",
            [
                {
                    "normalizer_version": PIXIV_METADATA_NORMALIZER_VERSION,
                    "work_id": "700000060",
                    "page_index": 0,
                    "page_count": 1,
                    "title": "Synthetic Reuse",
                    "creator_id": "800000060",
                    "creator_name": "Synthetic Reuse Creator",
                    "creator_account": "synthetic_reuse",
                    "creator_profile_identity": "https://www.pixiv.net/users/800000060",
                    "creator_profile_identity_source": "derived_from_stable_creator_id",
                    "tags": ("synthetic_reuse",),
                    "raw": {
                        "provider": "pixiv",
                        "id": 700000060,
                        "num": 0,
                        "page_count": 1,
                    },
                }
            ],
            attempted_record_ids=[int(first.id)],
        )
        session.commit()
        decision = queue_media_for_pixiv_metadata(
            session,
            {
                "id": media_ids[1],
                "filename": "700000060_p0.png",
                "path": "synthetic/reused-700000060_p0.png",
            },
        )
        session.commit()
        assert decision.state == PixivMetadataState.COMPLETE.value
        assert decision.reused_complete_record_ids
        aggregates = build_canonical_pixiv_aggregates_from_session(session)
        assert len(aggregates) == 1
        aggregate = aggregates[0]
        bundle = project_pixiv_aggregate_to_source_concept_signals(aggregate)
        row_ids = tuple(
            int(row.id)
            for row in session.query(SourceMetadataRecord)
            .filter(SourceMetadataRecord.source_work_id == "700000060")
            .order_by(SourceMetadataRecord.id)
        )
        return aggregate, bundle, row_ids
    finally:
        session.close()
        engine.dispose()


def test_cross_database_reuse_excludes_media_provider_and_source_row_identity(
    tmp_path: Path,
) -> None:
    left_aggregate, left_bundle, left_rows = _cross_database_reuse_projection(
        tmp_path / "left.sqlite3", media_ids=(100, 101), filler_count=0
    )
    right_aggregate, right_bundle, right_rows = _cross_database_reuse_projection(
        tmp_path / "right.sqlite3", media_ids=(9000, 9001), filler_count=5
    )
    assert left_rows != right_rows
    assert left_aggregate["canonical_fingerprint"] == right_aggregate[
        "canonical_fingerprint"
    ]
    assert left_bundle["canonical_fingerprint"] == right_bundle[
        "canonical_fingerprint"
    ]
    assert canonical_fingerprint(
        {"aggregates": [left_aggregate], "signal_bundles": [left_bundle]}
    ) == canonical_fingerprint(
        {"aggregates": [right_aggregate], "signal_bundles": [right_bundle]}
    )
    serialized = canonical_json_bytes(
        {"aggregate": left_aggregate, "bundle": left_bundle}
    ).decode("utf-8")
    assert '"media_id"' not in serialized
    assert '"provider_record_key"' not in serialized
    assert '"source_metadata_record_id"' not in serialized


def test_work_level_creator_conflict_is_page_order_stable_and_suppresses_anchors() -> None:
    pages = [
        _source_record(
            token="page-0",
            work_id="700000070",
            page_index=0,
            page_count=2,
            creator_id="800000070",
        ),
        _source_record(
            token="page-1",
            work_id="700000070",
            page_index=1,
            page_count=2,
            creator_id="800000071",
        ),
    ]
    forward = derive_pixiv_work_consistency(pages)
    reverse = derive_pixiv_work_consistency(list(reversed(pages)))
    assert forward == reverse
    assert "work_conflicting_provider_creator_ids" in forward["conflict_reasons"]
    aggregates = [
        build_canonical_pixiv_work_page_aggregate(
            [page], work_consistency=forward, known_page_indexes=(0, 1)
        )
        for page in pages
    ]
    assert all(item["disposition"] == "conflict" for item in aggregates)
    assert all(item["creator"]["provider_creator_id"] is None for item in aggregates)
    assert all(
        project_pixiv_aggregate_to_source_concept_signals(item)[
            "strong_identity_anchor_count"
        ]
        == 0
        for item in aggregates
    )


def test_current_work_facts_override_legacy_and_retryable_conflicts_deterministically() -> None:
    records = [
        _source_record(
            token="current",
            work_id="700000080",
            page_count=2,
            creator_id="800000080",
        ),
        _source_record(
            token="legacy",
            work_id="700000080",
            page_index=1,
            page_count=3,
            creator_id="800000081",
            normalizer_version=None,
        ),
        _source_record(
            token="retryable",
            work_id="700000080",
            page_index=1,
            page_count=4,
            creator_id="800000082",
            status=PixivMetadataState.RETRYABLE.value,
        ),
    ]
    consistency = derive_pixiv_work_consistency(records)
    assert consistency["provider_creator_id"] == "800000080"
    assert consistency["provider_page_count"] == 2
    assert consistency["conflict_reasons"] == []
    assert {
        "legacy_creator_id_conflict_ignored",
        "legacy_page_count_conflict_ignored",
        "untrusted_work_fact_ignored",
    } <= set(consistency["deferred_reasons"])


def test_page_count_and_current_provenance_incompatibility_are_explicit() -> None:
    page_count_conflict = derive_pixiv_work_consistency(
        [
            _source_record(
                token="p0", work_id="700000090", page_index=0, page_count=2
            ),
            _source_record(
                token="p1", work_id="700000090", page_index=1, page_count=3
            ),
        ]
    )
    assert "work_conflicting_provider_page_counts" in page_count_conflict[
        "conflict_reasons"
    ]
    provenance_conflict = derive_pixiv_work_consistency(
        [
            _source_record(
                token="bad-provenance",
                work_id="700000091",
                provenance_work_id="700000099",
            )
        ]
    )
    assert "current_v2_provenance_identity_incompatible" in provenance_conflict[
        "conflict_reasons"
    ]
    parser_conflict_records = [
        _source_record(
            token="parser-a",
            work_id="700000092",
            page_index=0,
            page_count=2,
            parser_version="synthetic_parser_v1",
        ),
        _source_record(
            token="parser-b",
            work_id="700000092",
            page_index=1,
            page_count=2,
            parser_version="synthetic_parser_v2",
        ),
    ]
    parser_conflict = derive_pixiv_work_consistency(parser_conflict_records)
    assert "work_current_v2_parser_version_conflict" in parser_conflict[
        "conflict_reasons"
    ]
    aggregate = build_canonical_pixiv_work_page_aggregate(
        [parser_conflict_records[0]],
        work_consistency=parser_conflict,
        known_page_indexes=(0, 1),
    )
    assert aggregate["creator"]["provider_creator_id"] is None
    assert project_pixiv_aggregate_to_source_concept_signals(aggregate)[
        "strong_identity_anchor_count"
    ] == 0

    unsupported_source = derive_pixiv_work_consistency(
        [
            _source_record(
                token="bad-source",
                work_id="700000093",
                provenance_source="synthetic_unknown_current_source",
            )
        ]
    )
    assert "current_v2_provenance_source_incompatible" in unsupported_source[
        "conflict_reasons"
    ]


def _signal_input(*, status: str, evidence: dict[str, object]) -> SourceConceptSignalInput:
    return SourceConceptSignalInput(
        origin_type="synthetic_signal",
        origin_key="same-logical-identity",
        provider="pixiv",
        raw_value="Synthetic Signal",
        display_value="Synthetic Signal",
        canonical_value="synthetic_signal",
        role_hint="artist",
        work_context_key=None,
        source_kind="synthetic",
        trust_tier="medium",
        confidence=0.5,
        status=status,
        evidence_payload=evidence,
        source_record_id="stable-source",
    )


def test_equal_signal_identity_conflicts_fail_closed_in_both_orders() -> None:
    left = _signal_input(status="active", evidence={"stable_creator_id": "800000100"})
    right = _signal_input(
        status="rejected", evidence={"stable_creator_id": "800000101"}
    )
    for ordered in ((left, right), (right, left)):
        with pytest.raises(ValueError, match="source_concept_signal_identity_conflict"):
            build_source_concept_signal_drafts(ordered)
    assert len(build_source_concept_signal_drafts((left, left))) == 1


def test_legacy_unknown_provenance_remains_truthful_and_current_v2_is_exact() -> None:
    legacy = build_canonical_pixiv_work_page_aggregate(
        [_source_record(token="legacy", normalizer_version=None)]
    )
    current = build_canonical_pixiv_work_page_aggregate(
        [_source_record(token="current")]
    )
    assert legacy["provenance"]["normalizer_versions"] == [
        PIXIV_LEGACY_NORMALIZER_VERSION
    ]
    assert "legacy_unknown_provenance" in legacy["deferred_reasons"]
    assert current["provenance"]["normalizer_versions"] == [
        PIXIV_METADATA_NORMALIZER_VERSION
    ]
    assert PIXIV_AGGREGATE_VERSION == "scv2_px1_pixiv_aggregate_v2"


@pytest.mark.parametrize(
    "private_text",
    [
        r"C:\Synthetic\private.png",
        r"\\synthetic-server\share\private.png",
        "/tmp/synthetic-private.png",
        "/var/lib/synthetic-private",
        "/Volumes/Synthetic/private.png",
        "path=/tmp/synthetic-private.png",
        r"path=C:\Synthetic\private.png",
        "file:///tmp/synthetic-private.png",
        "file://synthetic-server/share/private.png",
        "synthetic\x00marker",
        "Authorization: Bearer synthetic-secret",
        "client_secret=synthetic-secret",
        "password=synthetic-secret",
        "cookie=synthetic-secret",
    ],
)
def test_public_projection_rejects_bounded_private_path_uri_and_secret_markers(
    private_text: str,
) -> None:
    with pytest.raises(PixivMetadataProjectionError, match="private_text"):
        assert_public_safe_projection({"value": private_text})


def test_unsafe_observations_are_redacted_before_public_projection() -> None:
    aggregate = build_canonical_pixiv_work_page_aggregate(
        [_source_record(token="redacted", title="/tmp/synthetic-private.png")]
    )
    assert aggregate["title_observation"]["value"] is None
    assert aggregate["metadata_completeness"]["redacted_observation_count"] == 1
    assert "unsafe_observation_redacted" in aggregate["deferred_reasons"]
