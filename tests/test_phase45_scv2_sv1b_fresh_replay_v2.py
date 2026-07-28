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
    assert runner.PRIMARY_DATABASE == (
        "blombooru_scv2_sv1b_metadata_graph_closure_test_20260721_retry2"
    )
    assert runner.FAILED_REPLAY_DATABASE == (
        "blombooru_scv2_sv1b_replay_verification_test_20260721_retry2"
    )
    assert runner.FRESH_REPLAY_DATABASE == (
        "blombooru_scv2_sv1b_replay_v2_verification_test_20260725"
    )
    assert all(
        "production" not in database.casefold()
        for database in (
            runner.PRIMARY_DATABASE,
            runner.FAILED_REPLAY_DATABASE,
            runner.FRESH_REPLAY_DATABASE,
        )
    )


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
        "audit-closeout-binding-v4",
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


def _audit_v4_fixture(head: str) -> dict:
    return {
        "proof_version": "sv1b_audit_closeout_read_only_validation_v4",
        "git_head": head,
        "checks": {"all": True},
        "protected_source_evidence": {
            "membership_fingerprint": "1" * 64,
        },
        "stable_reference_integrity": {
            "passed": True,
            "reference_membership_fingerprint": "2" * 64,
        },
        "primary_identity_crosscheck": {
            "passed": True,
            "phase_acquired_membership_fingerprint": "3" * 64,
        },
        "round_trip": {
            "passed": True,
            "mismatch_membership_fingerprint": "4" * 64,
        },
        "passed": True,
    }


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_git_head",
        "old_git_head",
        "arbitrary_fingerprint",
        "payload_tamper",
        "file_sha_mismatch",
    ),
)
def test_audit_v4_self_head_payload_and_file_validation_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from scripts import (
        run_phase45_scv2_sv1b_manual_acceptance_harness as harness,
    )

    head = "a" * 40
    payload = _audit_v4_fixture(head)
    payload["proof_fingerprint"] = runner.sha256_payload(payload)
    if mutation == "missing_git_head":
        payload.pop("git_head")
        payload["proof_fingerprint"] = runner.sha256_payload(
            {
                key: value
                for key, value in payload.items()
                if key != "proof_fingerprint"
            }
        )
    elif mutation == "old_git_head":
        payload["git_head"] = "b" * 40
        payload["proof_fingerprint"] = runner.sha256_payload(
            {
                key: value
                for key, value in payload.items()
                if key != "proof_fingerprint"
            }
        )
    elif mutation == "arbitrary_fingerprint":
        payload["proof_fingerprint"] = "e" * 64
    elif mutation == "payload_tamper":
        payload["checks"]["all"] = False
    path = tmp_path / harness.AUDIT_CLOSEOUT_VALIDATION_PROOF_V4_NAME
    runner.write_json(path, payload)
    monkeypatch.setattr(runner, "git", lambda *_args: head)
    monkeypatch.setattr(
        runner,
        "_build_audit_closeout_v4_payload",
        lambda _output: _audit_v4_fixture(head),
    )

    with pytest.raises(
        runner.FreshReplayV2Error,
        match="audit_closeout_validation_v4_invalid",
    ):
        runner.validate_audit_closeout_v4(
            tmp_path,
            expected_file_sha256=(
                "0" * 64
                if mutation == "file_sha_mismatch"
                else None
            ),
        )


def test_exclusive_atomic_proof_write_rejects_second_create(
    tmp_path: Path,
) -> None:
    path = tmp_path / "proof.json"
    runner.write_json_exclusive_atomic(path, {"version": 1})
    original = path.read_bytes()

    with pytest.raises(
        runner.FreshReplayV2Error,
        match="exclusive_proof_already_exists",
    ):
        runner.write_json_exclusive_atomic(path, {"version": 2})

    assert path.read_bytes() == original


def test_prior_v1_v2_v3_exact_sha_drift_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SimpleNamespace(
        FINAL_HARNESS_PROOF_NAME="v1.json",
        AUDIT_CLOSEOUT_FINAL_BINDING_V2_NAME="v2.json",
        AUDIT_CLOSEOUT_VALIDATION_PROOF_V3_NAME="audit-v3.json",
        AUDIT_CLOSEOUT_FINAL_BINDING_V3_NAME="binding-v3.json",
    )
    values = {
        "v1.json": {"passed": True},
        "v2.json": {"passed": True},
        "audit-v3.json": {"proof_fingerprint": "a" * 64},
        "binding-v3.json": {
            "bindings": {"binding_fingerprint": "b" * 64}
        },
        (
            "fresh-replay-v2-audit-closeout-v3-"
            "strict-browser-prevalidation-proof.json"
        ): {"passed": True},
    }
    for name, value in values.items():
        runner.write_json(tmp_path / name, value)
    monkeypatch.setattr(
        runner,
        "EXPECTED_FINAL_BINDING_V1_FILE_SHA256",
        runner.sha256_file(tmp_path / "v1.json"),
    )
    monkeypatch.setattr(
        runner,
        "EXPECTED_FINAL_BINDING_V2_FILE_SHA256",
        runner.sha256_file(tmp_path / "v2.json"),
    )
    monkeypatch.setattr(
        runner,
        "EXPECTED_AUDIT_V3_FILE_SHA256",
        runner.sha256_file(tmp_path / "audit-v3.json"),
    )
    monkeypatch.setattr(
        runner,
        "EXPECTED_FINAL_BINDING_V3_FILE_SHA256",
        runner.sha256_file(tmp_path / "binding-v3.json"),
    )
    browser_name = (
        "fresh-replay-v2-audit-closeout-v3-"
        "strict-browser-prevalidation-proof.json"
    )
    monkeypatch.setattr(
        runner,
        "EXPECTED_BROWSER_V3_FILE_SHA256",
        runner.sha256_file(tmp_path / browser_name),
    )
    monkeypatch.setattr(
        runner,
        "EXPECTED_AUDIT_V3_FINGERPRINT",
        "a" * 64,
    )
    monkeypatch.setattr(
        runner,
        "EXPECTED_FINAL_BINDING_V3_FINGERPRINT",
        "b" * 64,
    )

    files, declared = runner._prior_v1_v2_v3_proof_bindings(
        tmp_path,
        harness,
    )
    assert all(row["unchanged"] for row in files.values())
    assert all(declared.values())

    runner.write_json(tmp_path / browser_name, {"passed": False})
    files, _declared = runner._prior_v1_v2_v3_proof_bindings(
        tmp_path,
        harness,
    )
    assert files[browser_name]["unchanged"] is False


def test_audit_v4_recovery_revalidates_before_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import (
        run_phase45_scv2_sv1b_manual_acceptance_harness as harness,
    )

    (tmp_path / harness.AUDIT_CLOSEOUT_FINAL_BINDING_V3_NAME).write_text(
        "{}",
        encoding="utf-8",
    )
    audit = _audit_v4_fixture("a" * 40)
    audit["proof_fingerprint"] = runner.sha256_payload(audit)
    runner.write_json(
        tmp_path / harness.AUDIT_CLOSEOUT_VALIDATION_PROOF_V4_NAME,
        audit,
    )
    monkeypatch.setattr(runner, "_validate_resume_ownership", lambda _o: None)
    calls = {"validate": 0, "finalize": 0}

    def validate(_output, *, expected_file_sha256=None):
        calls["validate"] += 1
        assert expected_file_sha256 is None
        return {
            "proof_path": "audit-v4.json",
            "proof_fingerprint": audit["proof_fingerprint"],
            "proof_file_sha256": "f" * 64,
            "git_head": "a" * 40,
        }

    monkeypatch.setattr(runner, "validate_audit_closeout_v4", validate)
    monkeypatch.setattr(
        harness,
        "finalize_audit_closeout_binding_v4",
        lambda *_args, **_kwargs: {
            "passed": True,
            "bindings": {"binding_fingerprint": "c" * 64},
            "supersedes_final_binding_fingerprint": "d" * 64,
        },
    )

    result = runner.execute_audit_closeout_binding_v4(output=tmp_path)

    assert result["passed"] is True
    assert calls["validate"] == 1


@pytest.mark.parametrize(
    "relative",
    (
        "acquired-nonderived-evidence-package-private.json",
        (
            "canary-route-viability-resume-r1/"
            "current-primary-read-only-export/"
            "stable-key-evidence-package.json"
        ),
        "candidate-page-media-manifest-private.jsonl",
        (
            "provider-execution-checkpoint-r2-route-viability/"
            "final-work-outcome-ledger.json"
        ),
        (
            "provider-execution-checkpoint-r2-route-viability/"
            "route-viability-canary-ledger.json"
        ),
    ),
)
def test_protected_raw_evidence_tamper_changes_v4_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    root = tmp_path / "immutable"
    files = (
        "acquired-nonderived-evidence-package-private.json",
        (
            "canary-route-viability-resume-r1/"
            "current-primary-read-only-export/"
            "stable-key-evidence-package.json"
        ),
        "candidate-page-media-manifest-private.jsonl",
        (
            "provider-execution-checkpoint-r2-route-viability/"
            "final-work-outcome-ledger.json"
        ),
        (
            "provider-execution-checkpoint-r2-route-viability/"
            "route-viability-canary-ledger.json"
        ),
    )
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"value":1}\n',
            encoding="utf-8",
        )
    monkeypatch.setattr(runner, "OLD_OUTPUT", root)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    before = runner._protected_source_evidence_manifest()
    (root / relative).write_text('{"value":2}\n', encoding="utf-8")
    after = runner._protected_source_evidence_manifest()

    assert before["membership_fingerprint"] != after[
        "membership_fingerprint"
    ]


def test_production_path_cannot_become_v4_evidence_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "production-evidence"
    paths = (
        root / "acquired-nonderived-evidence-package-private.json",
        root
        / "canary-route-viability-resume-r1"
        / "current-primary-read-only-export"
        / "stable-key-evidence-package.json",
        root / "candidate-page-media-manifest-private.jsonl",
        root
        / "provider-execution-checkpoint-r2-route-viability"
        / "final-work-outcome-ledger.json",
        root
        / "provider-execution-checkpoint-r2-route-viability"
        / "route-viability-canary-ledger.json",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(runner, "OLD_OUTPUT", root)
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    with pytest.raises(
        runner.FreshReplayV2Error,
        match="production_path_forbidden_in_evidence",
    ):
        runner._protected_source_evidence_manifest()


def test_owner_reconciliation_asserts_canonical_membership_and_support_separately() -> None:
    expected_count = sum(
        count
        for _before, _after, count in runner.EXPECTED_PHASE_STATUS_TRANSITIONS
    )
    crosscheck = {
        "expected_phase_acquired_identity_count": expected_count,
        "observed_phase_acquired_identity_count": expected_count,
        "missing_phase_acquired_identity_count": 0,
        "phase_acquired_identity_unsupported_count": 0,
        "phase_acquired_membership_fingerprint": (
            runner.EXPECTED_CANONICAL_PHASE_MEMBERSHIP_FINGERPRINT
        ),
        "canonical_phase_membership": {
            "accepted_unique_stable_key_count": 17193,
            "pre_provider_unique_stable_key_count": 17193,
            "duplicate_stable_key_count": 0,
            "missing_stable_key_or_fingerprint_count": 0,
            "conflicting_stable_fingerprint_count": 0,
            "accepted_only_stable_key_count": 0,
            "pre_provider_only_stable_key_count": 0,
            "changed_canonical_fingerprint_count": expected_count,
            "phase_acquired_membership_fingerprint": (
                runner.EXPECTED_CANONICAL_PHASE_MEMBERSHIP_FINGERPRINT
            ),
            "status_transition_counts": [
                {"from": before, "to": after, "count": count}
                for before, after, count in (
                    runner.EXPECTED_PHASE_STATUS_TRANSITIONS
                )
            ],
            "candidate_or_support_used_to_derive_membership": False,
        },
    }
    evidence = {
        "files": {
            "accepted_stable_package_and_persisted_raw": {
                "file_sha256": runner.EXPECTED_ACCEPTED_PACKAGE_FILE_SHA256,
                "canonical_payload_fingerprint": (
                    runner.ACCEPTED_ACQUISITION_PACKAGE_FINGERPRINT
                ),
            },
            "immutable_pre_provider_package": {
                "file_sha256": (
                    runner.EXPECTED_PRE_PROVIDER_PACKAGE_FILE_SHA256
                ),
                "canonical_payload_fingerprint": (
                    runner.EXPECTED_PRE_PROVIDER_PACKAGE_FINGERPRINT
                ),
            },
        },
        "all_database_inputs_are_strict_test_identities": True,
        "production_database_access_count": 0,
        "production_path_input_count": 0,
    }

    checks = runner._validate_owner_phase_membership_reconciliation(
        crosscheck,
        evidence,
    )
    assert all(checks.values())

    crosscheck["phase_acquired_identity_unsupported_count"] = 1
    checks = runner._validate_owner_phase_membership_reconciliation(
        crosscheck,
        evidence,
    )
    assert checks["canonical_count_derived"] is True
    assert checks["canonical_membership_complete_and_supported"] is False
