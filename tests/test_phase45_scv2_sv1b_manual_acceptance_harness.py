from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from scripts import run_phase45_scv2_sv1b_manual_acceptance_harness as harness


def _write_required_proofs(output: Path) -> None:
    values = {
        "acquisition-closure-and-package-proof.json": {
            "passed": True,
            "accepted_acquisition_package_fingerprint": "9" * 64,
            "package": {"acquired_metadata_package_fingerprint": "a" * 64},
        },
        "localization-closure-proof.json": {
            "passed": True,
            "localization_accounting_closed": True,
            "localization_translation_complete": False,
            "downstream_progression_allowed": True,
            "accepted_translation_state": {"fingerprint": "b" * 64},
            "vocabulary": {"fingerprint": "c" * 64},
        },
        "primary-source-graph-derivation-proof.json": {"passed": True},
        "replay-source-graph-derivation-proof.json": {"passed": True},
        "primary-replay-source-graph-comparison-proof.json": {
            "passed": True,
            "primary": {"fingerprint": "d" * 64},
            "replay": {"fingerprint": "d" * 64},
        },
        "primary-search-validation-proof.json": {
            "passed": True,
            "logical_result_fingerprint": "e" * 64,
        },
        "replay-search-validation-proof.json": {
            "passed": True,
            "logical_result_fingerprint": "e" * 64,
        },
        "primary-replay-search-comparison-proof.json": {"passed": True},
    }
    for name, value in values.items():
        (output / name).write_text(json.dumps(value), encoding="utf-8")


def test_new_metadata_membership_uses_accepted_package_phase_delta_only(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    package = {
        "schema_version": "sv1b.stable-replay-evidence.v2",
        "package_fingerprint": "1" * 64,
        "external_route_budget": {
            "gallery_dl_requests": 0,
            "llm_calls": 0,
            "media_downloads": 0,
            "provider_requests": 0,
            "thumbnail_downloads": 0,
        },
        "tables": {
            "source_metadata_records": [
                *[
                    {
                        "media_content_key": f"media-{index}",
                        "provider_record_key": f"record-{index}",
                        "provider": "pixiv",
                        "source_work_id": str(1000 + index),
                        "source_page_index": index,
                        "metadata_kind": "pixiv_ingestion_gate",
                        "data_type_label": (
                            "authenticated_provider_metadata"
                        ),
                        "status": "metadata_complete",
                        "provenance": {
                            "source": (
                                "gallery_dl_authenticated_metadata"
                            )
                        },
                    }
                    for index in range(12)
                ],
                {
                    "media_content_key": "baseline-media",
                    "provider_record_key": "baseline-record",
                    "provider": "pixiv",
                    "source_work_id": "42",
                    "source_page_index": 0,
                    "metadata_kind": "pixiv_ingestion_gate",
                    "data_type_label": "local_runtime_source_prior",
                    "status": "metadata_complete",
                    "provenance": {
                        "source": "canonical_pixiv_filename_path_prior"
                    },
                },
            ]
        },
    }
    (output / "acquired-nonderived-evidence-package-private.json").write_text(
        json.dumps(package), encoding="utf-8"
    )
    (
        output / "acquisition-closure-and-package-proof.json"
    ).write_text(
        json.dumps(
            {
                "passed": True,
                "accepted_acquisition_package_fingerprint": "2" * 64,
                "package": {"stable_package_fingerprint": "1" * 64},
            }
        ),
        encoding="utf-8",
    )
    membership = harness._newly_acquired_exact_metadata_membership(
        output
    )
    assert len(membership) == 12
    assert all(row[0] != "baseline-media" for row in membership)


def test_new_metadata_membership_fails_on_nonzero_external_route_budget(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (
        output / "acquired-nonderived-evidence-package-private.json"
    ).write_text(
        json.dumps(
            {
                "schema_version": "sv1b.stable-replay-evidence.v2",
                "package_fingerprint": "1" * 64,
                "external_route_budget": {"provider_requests": 1},
                "tables": {"source_metadata_records": []},
            }
        ),
        encoding="utf-8",
    )
    (
        output / "acquisition-closure-and-package-proof.json"
    ).write_text(
        json.dumps(
            {
                "passed": True,
                "accepted_acquisition_package_fingerprint": "2" * 64,
                "package": {"stable_package_fingerprint": "1" * 64},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        harness.ManualAcceptanceHarnessError,
        match="acquisition_package_binding_invalid",
    ):
        harness._newly_acquired_exact_metadata_membership(output)


def _cases(category: str, count: int, prefix: str) -> list[dict]:
    values = []
    for index in range(1, count + 1):
        actual = {"passed": True}
        provenance = {"source_layer_only": True, "derived_from_current_proofs": True}
        if category == "pixiv_metadata":
            provenance["phase_delta"] = "newly_acquired_exact_metadata"
        elif category == "creator_clustering":
            provenance.update(
                phase_delta="new_or_materially_changed_creator_component",
                available_changed_component_count=8,
            )
            actual["lifecycle_correct"] = True
        elif category == "shared_name_cannot_link":
            provenance.update(
                phase_delta="newly_acquired_alias_or_graph_edge",
                available_phase_delta_case_count=6,
            )
            actual.update(
                identity_union_created=False,
                cannot_link_safety_passed=True,
                lifecycle_correct=True,
            )
        elif category == "ai_tag_localization":
            provenance["phase_delta"] = (
                "newly_generated_translation" if index <= 6 else "proper_noun_exclusion_display"
            )
        elif category == "search_and_negative":
            provenance.update(
                phase_delta="new_localization",
                supported_by_phase_delta=True,
            )
        values.append(harness._case(
            f"{prefix}{index:02d}",
            category,
            media_hash=f"hash-{prefix}-{index}",
            title=f"case {index}",
            expected_behavior="expected",
            actual_result=actual,
            provenance=provenance,
        ))
    return values


def _patch_case_builders(monkeypatch, *, metadata_count: int = 12) -> None:
    monkeypatch.setattr(harness, "_pixiv_metadata_cases", lambda _session, _output: _cases("pixiv_metadata", metadata_count, "A"))
    monkeypatch.setattr(harness, "_creator_clustering_cases", lambda _session, _output, _database: _cases("creator_clustering", 8, "B"))
    monkeypatch.setattr(harness, "_shared_name_cases", lambda _session, _output: _cases("shared_name_cannot_link", 6, "C"))
    monkeypatch.setattr(harness, "_localization_cases", lambda _session, _output: _cases("ai_tag_localization", 8, "D"))
    monkeypatch.setattr(harness, "_search_cases", lambda _session, _output: _cases("search_and_negative", 6, "E"))


def test_build_harness_emits_exact_bound_40_case_pending_user_candidate(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    _write_required_proofs(output)
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(harness.sv1b, "validate_owned_output_root", lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(harness.sv1b, "engine_for", lambda _database: engine)
    monkeypatch.setattr(
        harness,
        "_database_binding",
        lambda database: {"database_identity": database, "fingerprint": database + "-fp", "media_count": 12000},
    )
    monkeypatch.setattr(harness, "_git_head", lambda: "f" * 40)
    _patch_case_builders(monkeypatch)

    proof = harness.build_harness(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
    )
    assert proof["case_count"] == 40
    assert proof["category_case_counts"] == harness.CATEGORY_COUNTS
    assert proof["status"] == "pending_user"
    assert proof["manual_acceptance_status"] == "pending_user"
    assert proof["target_met"] is False
    assert proof["safe_to_merge"] is False
    assert proof["route_approved"] is False
    assert proof["absolute_paths_exposed"] is False
    assert len(proof["acceptance_case_manifest_fingerprint"]) == 64

    monkeypatch.setattr(harness, "_git_head", lambda: "0" * 40)
    with pytest.raises(harness.ManualAcceptanceHarnessError, match="binding_invalidated"):
        harness._current_bindings(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )
    engine.dispose()


def test_build_harness_fails_closed_on_case_composition_gap(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    _write_required_proofs(output)
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(harness.sv1b, "validate_owned_output_root", lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(harness.sv1b, "engine_for", lambda _database: engine)
    monkeypatch.setattr(
        harness,
        "_database_binding",
        lambda database: {
            "database_identity": database,
            "fingerprint": database + "-fp",
            "media_count": 12000,
        },
    )
    monkeypatch.setattr(harness, "_git_head", lambda: "f" * 40)
    _patch_case_builders(monkeypatch, metadata_count=11)
    with pytest.raises(harness.ManualAcceptanceHarnessError, match="case_composition_invalid"):
        harness.build_harness(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )
    engine.dispose()


def test_build_harness_fails_closed_on_database_membership_gap(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    _write_required_proofs(output)
    monkeypatch.setattr(harness.sv1b, "validate_owned_output_root", lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(
        harness,
        "_database_binding",
        lambda database: {
            "database_identity": database,
            "fingerprint": database + "-fp",
            "media_count": 11999 if "primary" in database else 12000,
        },
    )
    with pytest.raises(harness.ManualAcceptanceHarnessError, match="database_membership_invalid"):
        harness.build_harness(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )


def test_current_bindings_invalidates_on_proof_drift(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    _write_required_proofs(output)
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(harness.sv1b, "validate_owned_output_root", lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(harness.sv1b, "engine_for", lambda _database: engine)
    monkeypatch.setattr(
        harness,
        "_database_binding",
        lambda database: {"database_identity": database, "fingerprint": database + "-fp", "media_count": 12000},
    )
    monkeypatch.setattr(harness, "_git_head", lambda: "f" * 40)
    _patch_case_builders(monkeypatch)
    harness.build_harness(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
    )
    proof_path = output / "primary-search-validation-proof.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["runtime_ms"] = 1
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    with pytest.raises(harness.ManualAcceptanceHarnessError, match="binding_invalidated"):
        harness._current_bindings(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )
    engine.dispose()


def test_harness_uses_explicit_relative_proof_sources_and_detects_drift(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    _write_required_proofs(output)
    fresh_graph = output / "fresh-replay-v2-final-graph-proof.json"
    fresh_graph.write_text(
        json.dumps({"passed": True, "logical_graph": "fresh-v2"}),
        encoding="utf-8",
    )
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(
        harness.sv1b,
        "validate_owned_output_root",
        lambda *_args, **_kwargs: {"passed": True},
    )
    monkeypatch.setattr(harness.sv1b, "engine_for", lambda _database: engine)
    monkeypatch.setattr(
        harness,
        "_database_binding",
        lambda database: {
            "database_identity": database,
            "fingerprint": database + "-fp",
            "media_count": 12000,
        },
    )
    monkeypatch.setattr(harness, "_git_head", lambda: "f" * 40)
    _patch_case_builders(monkeypatch)
    graph_slots = {
        "primary-source-graph-derivation-proof.json": fresh_graph.name,
        "replay-source-graph-derivation-proof.json": fresh_graph.name,
        "primary-replay-source-graph-comparison-proof.json": fresh_graph.name,
    }
    proof = harness.build_harness(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        proof_sources=graph_slots,
    )
    assert all(
        proof["proof_sources"][slot] == fresh_graph.name
        for slot in graph_slots
    )
    assert len(proof["bindings"]["proof_source_map_fingerprint"]) == 64

    fresh_graph.write_text(
        json.dumps(
            {"passed": True, "logical_graph": "unexpected-drift"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        harness.ManualAcceptanceHarnessError,
        match="binding_invalidated",
    ):
        harness._current_bindings(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )
    engine.dispose()


def test_final_binding_regenerates_cases_and_preserves_source_proof(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    _write_required_proofs(output)
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(
        harness.sv1b,
        "validate_owned_output_root",
        lambda *_args, **_kwargs: {"passed": True},
    )
    monkeypatch.setattr(harness.sv1b, "engine_for", lambda _database: engine)
    monkeypatch.setattr(
        harness,
        "_database_binding",
        lambda database: {
            "database_identity": database,
            "fingerprint": database + "-fp",
            "media_count": 12000,
        },
    )
    monkeypatch.setattr(harness, "_git_head", lambda: "a" * 40)
    _patch_case_builders(monkeypatch)
    source = harness.build_harness(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
    )
    source_text = (
        output / "manual-acceptance-harness-proof.json"
    ).read_text(encoding="utf-8")

    monkeypatch.setattr(harness, "_git_head", lambda: "b" * 40)
    final = harness.finalize_harness_binding(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        port=8012,
    )
    assert final["case_manifest_regenerated_equal"] is True
    assert final["bindings"]["git_head"] == "b" * 40
    assert final["localhost_url"] == "http://127.0.0.1:8012"
    assert (
        output / "manual-acceptance-harness-proof.json"
    ).read_text(encoding="utf-8") == source_text
    assert final[
        "supersedes_harness_proof_fingerprint"
    ] == harness.sv1b.sha256_payload(source)
    assert harness._current_bindings(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
    ) == final["bindings"]
    with pytest.raises(
        harness.ManualAcceptanceHarnessError,
        match="final_binding_already_exists",
    ):
        harness.finalize_harness_binding(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )
    engine.dispose()


def test_harness_rejects_proof_source_escape(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    _write_required_proofs(output)
    monkeypatch.setattr(
        harness.sv1b,
        "validate_owned_output_root",
        lambda *_args, **_kwargs: {"passed": True},
    )
    with pytest.raises(
        harness.ManualAcceptanceHarnessError,
        match="proof_source_escape",
    ):
        harness.build_harness(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            proof_sources={
                "primary-source-graph-derivation-proof.json": (
                    "../outside.json"
                )
            },
        )


def test_normalize_submission_requires_exact_case_membership_and_bounded_decisions() -> None:
    case_ids = {"A01", "A02"}
    normalized = harness.normalize_submission(
        {
            "results": [
                {"case_id": "A02", "decision": "FAIL", "comment": "problem"},
                {"case_id": "A01", "decision": "PASS", "comment": "ok"},
            ]
        },
        case_ids,
    )
    assert normalized == [
        {"case_id": "A01", "decision": "pass", "comment": "ok"},
        {"case_id": "A02", "decision": "fail", "comment": "problem"},
    ]
    with pytest.raises(harness.ManualAcceptanceHarnessError, match="case_membership_mismatch"):
        harness.normalize_submission(
            {"results": [{"case_id": "A01"}, {"case_id": "A03"}]},
            case_ids,
        )
    with pytest.raises(harness.ManualAcceptanceHarnessError, match="decision_invalid"):
        harness.normalize_submission(
            {"results": [{"case_id": "A01", "decision": "accept"}, {"case_id": "A02"}]},
            case_ids,
        )


def test_phase_delta_composition_rejects_underived_shared_name_safety() -> None:
    cases = [
        *_cases("pixiv_metadata", 12, "A"),
        *_cases("creator_clustering", 8, "B"),
        *_cases("shared_name_cannot_link", 6, "C"),
        *_cases("ai_tag_localization", 8, "D"),
        *_cases("search_and_negative", 6, "E"),
    ]
    cases[20]["actual_result"]["identity_union_created"] = True
    with pytest.raises(harness.ManualAcceptanceHarnessError, match="shared_name_safety_invalid"):
        harness.validate_phase_delta_case_composition(cases)


def test_phase_delta_composition_includes_every_manual_pending_localization() -> None:
    localization = _cases("ai_tag_localization", 8, "D")
    localization[0]["provenance"].update(
        phase_delta="manual_localization_review_pending",
        available_manual_pending_count=1,
    )
    for row in localization[1:6]:
        row["provenance"]["phase_delta"] = "newly_generated_translation"
        row["provenance"]["available_manual_pending_count"] = 1
    for row in localization[6:]:
        row["provenance"]["phase_delta"] = "proper_noun_exclusion_display"
        row["provenance"]["available_manual_pending_count"] = 1
    cases = [
        *_cases("pixiv_metadata", 12, "A"),
        *_cases("creator_clustering", 8, "B"),
        *_cases("shared_name_cannot_link", 6, "C"),
        *localization,
        *_cases("search_and_negative", 6, "E"),
    ]
    composition = harness.validate_phase_delta_case_composition(cases)
    assert composition["manual_localization_review_pending_case_count"] == 1
    assert composition["new_translation_case_count"] == 5
    assert composition["proper_noun_exclusion_display_case_count"] == 2


def test_harness_server_is_loopback_only_and_never_embeds_paths() -> None:
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert 'uvicorn.run(app, host="127.0.0.1"' in source
    assert "storage_root in path.parents" in source
    assert '"source_url"' not in source
    assert '"path":' not in source
    assert "SCV2-SV1B 40-case 手工验收" in harness._HTML
    assert "导出不会修改数据库" in harness._HTML
