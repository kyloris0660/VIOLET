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
            "package": {"acquired_metadata_package_fingerprint": "a" * 64},
        },
        "localization-closure-proof.json": {
            "passed": True,
            "localization_complete": True,
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


def _cases(category: str, count: int, prefix: str) -> list[dict]:
    return [
        harness._case(
            f"{prefix}{index:02d}",
            category,
            media_hash=f"hash-{prefix}-{index}",
            title=f"case {index}",
            expected_behavior="expected",
            actual_result={"passed": True},
            provenance={"source_layer_only": True},
        )
        for index in range(1, count + 1)
    ]


def _patch_case_builders(monkeypatch, *, metadata_count: int = 12) -> None:
    monkeypatch.setattr(harness, "_pixiv_metadata_cases", lambda _session: _cases("pixiv_metadata", metadata_count, "A"))
    monkeypatch.setattr(harness, "_creator_clustering_cases", lambda _session: _cases("creator_clustering", 8, "B"))
    monkeypatch.setattr(harness, "_shared_name_cases", lambda _session: _cases("shared_name_cannot_link", 6, "C"))
    monkeypatch.setattr(harness, "_localization_cases", lambda _session: _cases("ai_tag_localization", 8, "D"))
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


def test_harness_server_is_loopback_only_and_never_embeds_paths() -> None:
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert 'uvicorn.run(app, host="127.0.0.1"' in source
    assert "storage_root in path.parents" in source
    assert '"source_url"' not in source
    assert '"path":' not in source
    assert "SCV2-SV1B 40-case 手工验收" in harness._HTML
    assert "导出不会修改数据库" in harness._HTML
