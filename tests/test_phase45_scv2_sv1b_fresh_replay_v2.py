"""Safety tests for the one authorized SCV2-SV1B fresh Replay v2."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_phase45_scv2_sv1b_fresh_replay_v2 as runner
from scripts import (
    run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure
    as sv1b,
)


def test_fresh_database_identity_is_strict_and_distinct() -> None:
    assert runner.is_strict_test_database_name(runner.FRESH_REPLAY_DATABASE)
    assert len(
        {
            runner.PRIMARY_DATABASE,
            runner.FAILED_REPLAY_DATABASE,
            runner.FRESH_REPLAY_DATABASE,
        }
    ) == 3
    for unsafe in (
        "blombooru",
        "blombooru_contest",
        "blombooru_latest",
        "blombooru_testimony",
    ):
        assert runner.is_strict_test_database_name(unsafe) is False


def test_single_fresh_database_policy_rejects_any_second_database() -> None:
    runner.validate_single_fresh_database_membership(
        [],
        allow_target=False,
    )
    runner.validate_single_fresh_database_membership(
        [runner.FRESH_REPLAY_DATABASE],
        allow_target=True,
    )
    runner.validate_single_fresh_database_membership(
        [],
        allow_target=True,
    )

    with pytest.raises(
        runner.FreshReplayV2Error,
        match="creation_limit_violation",
    ):
        runner.validate_single_fresh_database_membership(
            [
                runner.FRESH_REPLAY_DATABASE,
                runner.FRESH_DATABASE_PREFIX + "retry2",
            ],
            allow_target=True,
        )


def test_no_external_execution_stage_exists() -> None:
    assert runner.STAGES == (
        "validate",
        "create-import",
        "derive-compare",
        "rederive-compare",
        "search",
        "build-harness",
        "finalize-harness-binding",
        "audit-closeout-binding-v2",
        "audit-closeout-binding-v3",
    )
    assert runner.EXTERNAL_ROUTE_BUDGET == {
        "provider_requests": 0,
        "gallery_dl_requests": 0,
        "llm_calls": 0,
        "media_downloads": 0,
        "thumbnail_downloads": 0,
    }


def test_external_route_guards_fail_closed() -> None:
    adapter = SimpleNamespace(execute=lambda: "unsafe")
    ingestion = SimpleNamespace(run_provider=lambda: "unsafe")
    module = SimpleNamespace(
        validate_gallery_dl_profile=lambda: "unsafe",
        execute_provider_manifest=lambda: "unsafe",
        audit_acquisition_and_package=lambda: "unsafe",
        gallery_adapter=adapter,
        ingestion_runner=ingestion,
    )

    runner._install_external_route_guards(module)

    for operation in (
        module.validate_gallery_dl_profile,
        module.execute_provider_manifest,
        module.audit_acquisition_and_package,
        module.gallery_adapter.execute,
        module.ingestion_runner.run_provider,
    ):
        with pytest.raises(
            runner.FreshReplayV2Error,
            match="external_execution_route_forbidden",
        ):
            operation()


def test_public_summary_cannot_claim_external_calls_or_completion() -> None:
    summary = runner.public_summary(
        "create-import",
        {"passed": True},
    )

    assert summary["provider_request_count"] == 0
    assert summary["llm_call_count"] == 0
    assert summary["media_download_count"] == 0
    assert summary["private_values_exposed"] is False
    assert "target_met" not in summary


def test_runner_source_has_no_provider_or_llm_execution_command() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "--dump-json" not in source
    assert "--no-download" not in source
    assert "complete_chat(" not in source
    assert "complete_json(" not in source
    assert "requests.get(" not in source
    assert "httpx." not in source


def test_reconciliation_and_mismatch_proofs_are_computed_not_hardcoded() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert '"primary_reconciliation_passed": True' not in source
    assert '"replay_reconciliation_passed": True' not in source
    assert '"stable_identity_mismatch_count": 0' not in source
    assert '"trusted_complete_verdict_mismatch_count": 0' not in source
    assert '"stable_identity_mismatch_count"' in source
    assert '"trusted_complete_verdict_mismatch_count"' in source
    assert 'round_trip[' in source
    assert 'comparison[' in source


def test_failed_replay_is_never_a_write_target() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "DROP DATABASE" not in source.upper()
    assert "TRUNCATE " not in source.upper()
    assert "DELETE FROM" not in source.upper()
    assert "UPDATE blombooru_" not in source
    assert (
        "engine_for(FAILED_REPLAY_DATABASE)"
        not in source.replace(
            "forensic_database_state(FAILED_REPLAY_DATABASE)",
            "",
        )
    )


def test_stable_family_projection_ignores_only_local_concept_refs() -> None:
    primary = runner._stable_family_projection(
        [
            {
                "family_id": "family:stable",
                "identity_fingerprint": "stable",
                "outcome": "deterministic_must_link_materialized",
                "concept_ref": "primary-local-id",
            }
        ]
    )
    replay = runner._stable_family_projection(
        [
            {
                "family_id": "family:stable",
                "identity_fingerprint": "stable",
                "outcome": "already_materialized",
                "concept_ref": "replay-local-id",
            }
        ]
    )

    assert primary == replay


def test_stable_signal_rederive_checkpoint_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = {
        "proof_version": "fixture",
        "passed": True,
    }
    checkpoint = (
        tmp_path
        / "fresh-replay-v2-stable-signal-rederive-compare-proof.json"
    )
    runner.write_json(checkpoint, proof)
    monkeypatch.setattr(
        runner,
        "_require_import_checkpoint",
        lambda _output: {"passed": True},
    )

    assert runner.execute_rederive_compare(output=tmp_path) == proof


def test_persisted_core_projection_excludes_superseded_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, str]]] = []

    class EmptyResult:
        def mappings(self) -> "EmptyResult":
            return self

        def __iter__(self):
            return iter(())

    class Connection:
        def execute(self, statement, parameters):
            queries.append((str(statement), dict(parameters)))
            return EmptyResult()

    class ConnectionContext:
        def __enter__(self) -> Connection:
            return Connection()

        def __exit__(self, *_args) -> None:
            return None

    class Engine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

        def dispose(self) -> None:
            return None

    monkeypatch.setattr(sv1b, "engine_for", lambda _database: Engine())

    result = sv1b._stable_core_graph_projection_from_database(
        "blombooru_strict_test",
        run_id="stable-run",
    )

    assert result["groups"]
    assert len(queries) == 6
    for query, parameters in queries:
        assert "<>:superseded_status" in query
        assert parameters == {
            "run_id": "stable-run",
            "superseded_status": "superseded",
        }


def test_failed_scope_projection_checkpoint_recovers_without_graph_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned = {
        "projection_version": "fixture",
        "groups": {"signal": {"count": 1, "fingerprint": "current"}},
        "fingerprint": "current",
    }
    failed = {
        "passed": False,
        "planned_core_graph_projection": planned,
        "persisted_core_graph_projection": {
            "projection_version": "fixture",
            "groups": {
                "signal": {"count": 2, "fingerprint": "with-history"}
            },
            "fingerprint": "with-history",
        },
        "graph_audit": {
            "passed": True,
            "deferred_identity_union_count": 0,
            "direct_cannot_link_violation_count": 0,
            "transitive_cannot_link_violation_count": 0,
        },
        "baseline_preservation": {
            "passed": True,
            "accepted_family_count": 606,
            "accepted_family_traceable_count": 606,
        },
        "candidate_disposition_accounting": {
            "equation_balanced": True,
        },
    }
    failed_path = (
        tmp_path
        / f"{runner.CORRECTED_GRAPH_LABEL}-source-graph-derivation-proof.json"
    )
    runner.write_json(failed_path, failed)
    original_bytes = failed_path.read_bytes()
    monkeypatch.setattr(
        runner,
        "CORRECTED_GRAPH_FAILED_SCOPE_PROOF_FINGERPRINT",
        runner.sha256_payload(failed),
    )
    fake_sv1b = SimpleNamespace(
        _stable_core_graph_projection_from_database=(
            lambda _database, run_id: planned
        )
    )

    recovered = runner._recover_scope_filtered_graph_checkpoint(
        output=tmp_path,
        sv1b=fake_sv1b,
    )

    assert recovered["passed"] is True
    assert recovered["planned_persisted_core_graph_equal"] is True
    assert recovered["scope_reconciliation_database_write_count"] == 0
    assert failed_path.read_bytes() == original_bytes
    reconciliation = runner.read_json(
        tmp_path
        / "fresh-replay-v2-stable-signal-projection-scope-reconciliation-proof.json"
    )
    assert reconciliation["passed"] is True
    assert reconciliation["database_write_count"] == 0
    assert reconciliation["historical_failed_proof_rewritten"] is False


def test_first_graph_checkpoint_accepts_exact_corrected_stage_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {
        "passed": False,
        "graph_audit": {
            "deferred_identity_union_count": 1,
            "direct_cannot_link_violation_count": 0,
            "transitive_cannot_link_violation_count": 0,
            "multi_stable_id_creator_component_count": 0,
            "unauthorized_cross_role_component_count": 0,
            "unknown_role_materialization_count": 0,
            "duplicate_active_stable_identity_count": 0,
            "unsafe_large_component_count": 0,
            "giant_component_recurrence": False,
        },
    }
    corrected = {
        "passed": False,
        "graph_audit": {
            "passed": True,
            "deferred_identity_union_count": 0,
        },
    }
    runner.write_json(
        tmp_path / "replay-v2-source-graph-derivation-proof.json",
        first,
    )
    runner.write_json(
        tmp_path
        / f"{runner.CORRECTED_GRAPH_LABEL}-source-graph-derivation-proof.json",
        corrected,
    )
    monkeypatch.setattr(
        runner,
        "FAILED_FIRST_GRAPH_PROOF_FINGERPRINT",
        runner.sha256_payload(first),
    )
    monkeypatch.setattr(
        runner,
        "CORRECTED_GRAPH_FAILED_SCOPE_PROOF_FINGERPRINT",
        runner.sha256_payload(corrected),
    )
    monkeypatch.setattr(
        runner,
        "CORRECTED_GRAPH_FAILED_SCOPE_DATABASE_STATE_FINGERPRINT",
        "corrected-state",
    )
    monkeypatch.setattr(
        runner,
        "_logical_graph_state",
        lambda _database: {"fingerprint": "corrected-state"},
    )

    result = runner._validate_failed_first_graph_checkpoint(tmp_path)

    assert result["passed"] is True
    assert result["database_state_stage"] == (
        "corrected_graph_committed_projection_scope_failed"
    )
    assert result["database_state_fingerprint"] == "corrected-state"


def test_pinned_history_does_not_follow_evolved_live_artifact(
    tmp_path: Path,
) -> None:
    live = tmp_path / "live.json"
    historical = tmp_path / "historical.json"
    historical.write_text('{"stage":"first"}\n', encoding="utf-8")
    pinned = runner.sha256_file(historical)
    live.write_text('{"stage":"current"}\n', encoding="utf-8")

    runner._preserve_pinned_history(
        live,
        historical,
        expected_file_fingerprint=pinned,
    )

    assert historical.read_text(encoding="utf-8") == (
        '{"stage":"first"}\n'
    )
    assert live.read_text(encoding="utf-8") == '{"stage":"current"}\n'


def test_pinned_history_drift_fails_closed(tmp_path: Path) -> None:
    live = tmp_path / "live.json"
    historical = tmp_path / "historical.json"
    live.write_text('{"stage":"current"}\n', encoding="utf-8")
    historical.write_text('{"stage":"drift"}\n', encoding="utf-8")

    with pytest.raises(
        runner.FreshReplayV2Error,
        match="historical_artifact_copy_drift",
    ):
        runner._preserve_pinned_history(
            live,
            historical,
            expected_file_fingerprint=runner.sha256_payload(
                {"not": "the file hash"}
            ),
        )
