"""Safety tests for the one authorized SCV2-SV1B fresh Replay v2."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_phase45_scv2_sv1b_fresh_replay_v2 as runner


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
        "search",
        "build-harness",
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
