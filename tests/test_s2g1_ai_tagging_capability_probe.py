from __future__ import annotations

import builtins
from types import SimpleNamespace

from scripts import run_s2g1_ai_tagging_capability_probe as probe
from scripts.s2g_s3a_job_control import ProviderCapability


def test_probe_summary_is_public_safe_and_keeps_execution_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        probe,
        "load_settings_snapshot",
        lambda _model: {
            "ai_tagging_enabled": True,
            "model_name": "wd-swinv2-tagger-v3",
            "general_threshold": 0.35,
            "character_threshold": 0.65,
            "rating_threshold": 0.5,
            "suggestion_threshold": 0.2,
            "batch_max_items": 10,
            "source": "test",
        },
    )
    monkeypatch.setattr(
        probe,
        "cached_model_files",
        lambda _model: {
            "model_name": "wd-swinv2-tagger-v3",
            "repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
            "model_file_cached": True,
            "label_file_cached": True,
            "network_download_required": False,
            "local_files_only": True,
            "error_code": None,
            "_model_path": "private-path-redacted-by-public_model_cache",
        },
    )
    monkeypatch.setattr(
        probe,
        "onnxruntime_environment",
        lambda: {
            "importable": True,
            "version": "test",
            "device": "CPU",
            "available_providers": ["CPUExecutionProvider"],
            "error_code": None,
        },
    )
    monkeypatch.setattr(
        probe,
        "inspect_current_app_backend",
        lambda: {
            "source_file": "backend/app/services/wd_tagger.py",
            "hardcoded_cpu_execution_provider": True,
            "forced_provider": "CPUExecutionProvider",
            "uses_all_cpu_threads_by_default": True,
            "uses_parallel_execution_mode": True,
            "preprocess_workers": "min(4, os.cpu_count())",
        },
    )

    def fake_benchmark(**kwargs):
        provider = kwargs["provider"]
        return ProviderCapability(
            provider=provider,
            available=provider == "CPUExecutionProvider",
            practical=provider == "CPUExecutionProvider",
            loaded=provider == "CPUExecutionProvider",
            benchmark_status="completed" if provider == "CPUExecutionProvider" else "not_available",
            throughput_items_per_second=2.0 if provider == "CPUExecutionProvider" else None,
        )

    monkeypatch.setattr(probe, "benchmark_provider", fake_benchmark)
    monkeypatch.setattr(probe, "git_value", lambda _args: "test-git-value")

    args = SimpleNamespace(
        model_name=None,
        provider=None,
        allow_model_load=True,
        synthetic_samples=3,
        batch_size=None,
    )
    summary = probe.build_summary(args)

    assert summary["pipeline_contract"]["contract_id"] == "s2g1x_probe_contract_v1"
    assert summary["pipeline_contract"]["status"] == "target_met"
    assert summary["pipeline_contract"]["claims"] == {"target_met": True, "safe_to_merge": True}
    assert "head_sha" not in summary
    assert summary["head_evidence"]["top_level_head_sha_omitted"] is True
    assert summary["head_evidence"]["current_pr_head_sha"] == "represented_by_pr_metadata_after_commit"
    assert summary["capability_probe"]["provider_matrix"]["cpu"]["loaded"] is True
    assert summary["capability_probe"]["model_identity"]["network_download_required"] is False
    assert "_model_path" not in summary["capability_probe"]["model_identity"]
    assert summary["safety"]["production_ai_tagging"] is False
    assert summary["safety"]["production_s3a_execution_enabled"] is False
    assert summary["s2g_s3a_decision"]["should_share_job_progress_throttle_ledger_architecture"] is True
    assert summary["public_redaction"]["passed"] is True


def test_probe_without_model_load_does_not_claim_target_met(monkeypatch) -> None:
    monkeypatch.setattr(
        probe,
        "load_settings_snapshot",
        lambda _model: {
            "ai_tagging_enabled": True,
            "model_name": "wd-swinv2-tagger-v3",
            "general_threshold": 0.35,
            "character_threshold": 0.65,
            "rating_threshold": 0.5,
            "suggestion_threshold": 0.2,
            "batch_max_items": 10,
            "source": "test",
        },
    )
    monkeypatch.setattr(
        probe,
        "cached_model_files",
        lambda _model: {
            "model_name": "wd-swinv2-tagger-v3",
            "repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
            "model_file_cached": True,
            "label_file_cached": True,
            "network_download_required": False,
            "local_files_only": True,
            "error_code": None,
            "_model_path": "private-path-redacted-by-public_model_cache",
        },
    )
    monkeypatch.setattr(
        probe,
        "onnxruntime_environment",
        lambda: {
            "importable": True,
            "version": "test",
            "device": "CPU",
            "available_providers": ["CPUExecutionProvider"],
            "error_code": None,
        },
    )
    monkeypatch.setattr(probe, "inspect_current_app_backend", lambda: {"forced_provider": "CPUExecutionProvider"})

    def fake_benchmark(**kwargs):
        provider = kwargs["provider"]
        if provider == "CPUExecutionProvider":
            return ProviderCapability(provider, True, False, False, "model_load_not_requested")
        return ProviderCapability(provider, False, False, False, "not_available")

    monkeypatch.setattr(probe, "benchmark_provider", fake_benchmark)
    monkeypatch.setattr(probe, "git_value", lambda _args: "test-git-value")

    summary = probe.build_summary(
        SimpleNamespace(
            model_name=None,
            provider=None,
            allow_model_load=False,
            synthetic_samples=3,
            batch_size=None,
        )
    )

    assert summary["pipeline_contract"]["status"] == "evidence_collected"
    assert summary["pipeline_contract"]["claims"] == {"target_met": False, "safe_to_merge": False}


def test_load_settings_snapshot_avoids_app_config_import_and_handles_malformed_env(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "app.config":
            raise AssertionError("probe must not import app.config")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        probe,
        "read_dotenv_values",
        lambda _path=probe.ENV_FILE: {
            "AI_TAGGING_ENABLED": "maybe",
            "AI_MODEL_NAME": "wd-swinv2-tagger-v3",
            "AI_GENERAL_THRESHOLD": "bad-float",
            "AI_CHARACTER_THRESHOLD": "0.65",
            "AI_RATING_THRESHOLD": "0.50",
            "AI_SUGGESTION_THRESHOLD": "0.20",
            "AI_TAGGING_BATCH_MAX_ITEMS": "not-int",
        },
    )
    for key in probe.DEFAULT_AI_SETTINGS:
        monkeypatch.delenv(key, raising=False)

    snapshot = probe.load_settings_snapshot(None)

    assert snapshot["app_config_imported"] is False
    assert snapshot["general_threshold"] == probe.DEFAULT_AI_SETTINGS["AI_GENERAL_THRESHOLD"]
    assert snapshot["batch_max_items"] == probe.DEFAULT_AI_SETTINGS["AI_TAGGING_BATCH_MAX_ITEMS"]
    assert {item["error_code"] for item in snapshot["config_parse_errors"]} == {
        "invalid_bool_used_default",
        "invalid_float_used_default",
        "invalid_int_used_default",
    }


def test_probe_redaction_failure_does_not_write_public_outputs(tmp_path, monkeypatch) -> None:
    output_json = tmp_path / "probe-summary.json"
    output_md = tmp_path / "probe-report.md"
    monkeypatch.setattr(
        probe,
        "build_summary",
        lambda _args: {
            "public_redaction": {"passed": False, "finding_count": 1, "findings_redacted": True},
            "pipeline_contract": {"contract_id": "s2g1x_probe_contract_v1", "status": "blocked_probe_unavailable"},
            "capability_probe": {"provider_matrix": {}, "runtime": {"onnxruntime": {}}},
            "s2g_s3a_decision": {},
            "load_control": {"recommended_config": {}, "risk_flags": []},
        },
    )
    monkeypatch.setattr(probe, "render_report", lambda _summary: r"leak C:\Users\example\private.png")

    exit_code = probe.main(["--output-json", str(output_json), "--output-md", str(output_md)])

    assert exit_code == 2
    assert not output_json.exists()
    assert not output_md.exists()


def test_render_report_includes_provider_table(monkeypatch) -> None:
    args = SimpleNamespace(
        model_name="unknown-model",
        provider=["CPUExecutionProvider"],
        allow_model_load=False,
        synthetic_samples=1,
        batch_size=1,
    )
    monkeypatch.setattr(probe, "git_value", lambda _args: "test-git-value")
    summary = probe.build_summary(args)
    report = probe.render_report(summary)

    assert "| Provider | Available | Practical | Loaded | Benchmark status | Items/sec |" in report
    assert "## Runtime Environment" in report
    assert "ONNX Runtime providers" in report
    assert "S2G/S3A Decision" in report
    assert "No DB connection or production DB writes." in report
