from __future__ import annotations

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
