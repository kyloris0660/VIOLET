from __future__ import annotations

import threading
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts import run_s2g_m1_ai_manual_sync_foundation as s2g_m1_runner  # noqa: E402
from app.services.job_control import (  # noqa: E402
    AITaggingExecutionProfile,
    LoadControlConfig,
    ProviderCapability,
    ProviderProvenance,
    build_ai_tagging_load_control_config,
    build_ai_tagging_execution_profile,
    build_s3a_foundation_dry_run_plan,
    select_onnx_provider,
)
from app.services.wd_tagger import WDTagger  # noqa: E402


def _s2g_m1_runner_profile() -> dict:
    return {
        "profile_id": "ai_tagging_execution_profile_v1",
        "provider_backend": "onnxruntime",
        "model_name": "wd-swinv2-tagger-v3",
        "model_repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
        "thresholds": {"general": 0.35, "character": 0.65, "rating": 0.5},
        "provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "batch_size": 2,
        "concurrency": 1,
        "per_image_timeout_seconds": 60,
        "job_timeout_seconds": 600,
        "allow_provider_fallback": True,
        "production_writes_enabled": False,
        "local_files_only": True,
        "provider_network_calls_enabled": False,
        "llm_calls_enabled": False,
        "provenance_fields": ["source", "model_name", "provider_backend", "confidence", "thresholds", "job_id"],
    }


def _s2g_m1_runner_probe() -> dict:
    return {
        "attempted": True,
        "bounded": True,
        "synthetic_input_only": True,
        "sample_count": 2,
        "local_files_only": True,
        "model_cache": {"status": "cached"},
        "onnxruntime": {"available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"]},
        "provider_matrix": {
            "directml": {"provider": "DmlExecutionProvider", "status": "completed", "seconds_per_item": 0.1},
            "cpu": {"provider": "CPUExecutionProvider", "available": True, "status": "completed", "seconds_per_item": 1.0},
        },
        "provider_benchmark_timeout_seconds": 60,
        "gpu_acceleration_available": True,
        "cpu_fallback_available": True,
        "cpu_fallback_completed": True,
        "provider_selection": {
            "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "selected_provider": "DmlExecutionProvider",
            "fallback_occurred": False,
            "fallback_reason": None,
        },
        "provider_fallback_decision_recorded": True,
        "recommended_provider": "DmlExecutionProvider",
        "recommended_batch_size": 2,
        "recommended_concurrency": 1,
        "recommended_seconds_per_item": 0.1,
        "estimated_runtime_seconds_for_25_item_manual_batch": 2.5,
        "status": "completed",
        "blocker": None,
    }


def _s2g_m1_runner_plan() -> dict:
    return {
        "job": {"job_id": "test-plan", "mode": "dry_run", "state": "planned", "trigger_type": "manual_operator"},
        "ledger": {
            "db_write_performed": False,
            "source_mutation_performed": False,
            "app_storage_mutation_performed": False,
            "per_file_public_records": [{"safe_label": "file-00001"}],
            "persistent_tables_available": ["blombooru_dynamic_sync_runs"],
            "ledger_mode": "ephemeral_public_plan_current_phase",
        },
        "counts": {
            "state_counts": {
                "import_planned": 1,
                "skipped_unsupported": 1,
                "skipped_zero_byte": 1,
                "skipped_duplicate": 1,
                "skipped_existing_media": 1,
                "failed": 1,
            },
            "failure_reasons": {"corrupted_image": 1},
            "estimated_import_count": 1,
            "estimated_classification_count": 1,
            "estimated_ai_tagging_count": 1,
            "estimated_localization_workload": 1,
        },
        "pipeline": {
            "status": "dry_run_planned",
            "dry_run_only_this_phase": True,
            "production_execute_enabled": False,
            "estimated_runtime_seconds": 1.0,
            "stages": [
                {"name": "candidate_discovery", "state": "completed", "writes_enabled": False, "production_execution_enabled": False},
                {"name": "import", "state": "planned", "writes_enabled": False, "production_execution_enabled": False},
                {"name": "classification", "state": "planned", "writes_enabled": False, "production_execution_enabled": False},
                {"name": "ai_tagging", "state": "planned", "writes_enabled": False, "production_execution_enabled": False},
                {"name": "localization", "state": "handoff_planned", "writes_enabled": False, "production_execution_enabled": False},
                {"name": "summary", "state": "planned", "writes_enabled": False, "production_execution_enabled": False},
            ],
        },
    }


def test_provider_selection_records_cpu_fallback_when_gpu_providers_unavailable() -> None:
    selection = select_onnx_provider(
        ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"],
        ["AzureExecutionProvider", "CPUExecutionProvider"],
    )

    public = selection.to_public_dict()

    assert public["selected_provider"] == "CPUExecutionProvider"
    assert public["fallback_occurred"] is True
    assert "CUDAExecutionProvider" in public["fallback_reason"]
    assert "DmlExecutionProvider" in public["fallback_reason"]
    assert public["available_providers"] == ["AzureExecutionProvider", "CPUExecutionProvider"]


def test_provider_selection_does_not_claim_fallback_when_first_requested_provider_is_usable() -> None:
    selection = select_onnx_provider(
        ["CPUExecutionProvider", "CUDAExecutionProvider"],
        ["AzureExecutionProvider", "CPUExecutionProvider"],
    )

    public = selection.to_public_dict()

    assert public["selected_provider"] == "CPUExecutionProvider"
    assert public["fallback_occurred"] is False
    assert public["fallback_reason"] is None
    assert public["unavailable_requested_providers"] == []


def test_load_control_config_uses_conservative_defaults_from_settings() -> None:
    settings = SimpleNamespace(
        AI_TAGGING_BATCH_SIZE=20,
        AI_TAGGING_BATCH_MAX_ITEMS=10,
        AI_TAGGING_MAX_CONCURRENT_JOBS=4,
        AI_TAGGING_PREPROCESS_WORKERS=2,
        AI_TAGGING_PROVIDER_PREFERENCE=(
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ),
        AI_TAGGING_CPU_INTRA_OP_THREADS=4,
        AI_TAGGING_CPU_INTER_OP_THREADS=1,
        AI_TAGGING_EXECUTION_MODE="ORT_PARALLEL",
        AI_TAGGING_PROCESS_PRIORITY="realtime",
    )

    config = build_ai_tagging_load_control_config(settings)
    public = config.to_public_dict()

    assert public["batch_size"] == 10
    assert public["configured_batch_size"] == 20
    assert public["effective_batch_size"] == 10
    assert public["batch_cap_source"] == "AI_TAGGING_BATCH_MAX_ITEMS"
    assert public["max_concurrent_jobs"] == 1
    assert public["preprocess_workers"] == 2
    assert public["cpu_intra_op_threads"] == 4
    assert public["cpu_inter_op_threads"] == 1
    assert public["execution_mode"] == "ORT_SEQUENTIAL"
    assert public["validation_errors"] == []


def test_load_control_config_clamps_operator_overrides_before_runtime_use() -> None:
    settings = SimpleNamespace(
        AI_TAGGING_BATCH_SIZE=999,
        AI_TAGGING_BATCH_MAX_ITEMS=999,
        AI_TAGGING_MAX_CONCURRENT_JOBS=8,
        AI_TAGGING_PREPROCESS_WORKERS=99,
        AI_TAGGING_PROVIDER_PREFERENCE=(
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ),
        AI_TAGGING_CPU_INTRA_OP_THREADS=99,
        AI_TAGGING_CPU_INTER_OP_THREADS=99,
        AI_TAGGING_EXECUTION_MODE="ORT_SEQUENTIAL",
        AI_TAGGING_PROCESS_PRIORITY="below_normal",
    )

    public = build_ai_tagging_load_control_config(settings).to_public_dict()

    assert public["configured_batch_size"] == 999
    assert public["batch_size"] == 16
    assert public["effective_batch_size"] == 16
    assert public["batch_cap_source"] == "phase_max_batch_size"
    assert public["max_concurrent_jobs"] == 1
    assert public["preprocess_workers"] == 2
    assert public["cpu_intra_op_threads"] == 4
    assert public["cpu_inter_op_threads"] == 1
    assert public["execution_mode"] == "ORT_SEQUENTIAL"
    assert public["process_priority"] == "below_normal"
    assert public["validation_errors"] == []


def test_ai_tagging_execution_profile_defaults_are_safe_for_s2g_m1() -> None:
    settings = SimpleNamespace(
        AI_MODEL_NAME="wd-swinv2-tagger-v3",
        AI_GENERAL_THRESHOLD=0.35,
        AI_CHARACTER_THRESHOLD=0.65,
        AI_RATING_THRESHOLD=0.50,
        AI_SUGGESTION_THRESHOLD=0.20,
        AI_TAGGING_BATCH_SIZE=20,
        AI_TAGGING_BATCH_MAX_ITEMS=10,
        AI_TAGGING_MAX_CONCURRENT_JOBS=4,
        AI_TAGGING_PREPROCESS_WORKERS=4,
        AI_TAGGING_PROVIDER_PREFERENCE=(
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ),
        AI_TAGGING_ALLOW_PROVIDER_FALLBACK=True,
        AI_TAGGING_CPU_INTRA_OP_THREADS=8,
        AI_TAGGING_CPU_INTER_OP_THREADS=8,
        AI_TAGGING_EXECUTION_MODE="ORT_PARALLEL",
        AI_TAGGING_PROCESS_PRIORITY="normal",
        AI_TAGGING_IMAGE_TIMEOUT_SECONDS=60,
        AI_TAGGING_JOB_TIMEOUT_SECONDS=600,
    )

    profile = build_ai_tagging_execution_profile(settings).to_public_dict()

    assert profile["provider_backend"] == "onnxruntime"
    assert profile["model_name"] == "wd-swinv2-tagger-v3"
    assert profile["model_repo_id"] == "SmilingWolf/wd-swinv2-tagger-v3"
    assert profile["batch_size"] == 10
    assert profile["concurrency"] == 1
    assert profile["preprocess_workers"] == 2
    assert profile["per_image_timeout_seconds"] == 60
    assert profile["job_timeout_seconds"] == 600
    assert profile["production_capable"] is True
    assert profile["production_writes_enabled"] is False
    assert profile["local_files_only"] is True
    assert profile["provider_network_calls_enabled"] is False
    assert profile["llm_calls_enabled"] is False
    assert profile["safe_for_current_phase"] is True
    assert "actual_provider" in profile["provenance_fields"]


def test_ai_tagging_execution_profile_reports_forbidden_write_and_network_modes() -> None:
    unsafe = AITaggingExecutionProfile(
        production_writes_enabled=True,
        local_files_only=False,
        provider_network_calls_enabled=True,
        llm_calls_enabled=True,
        concurrency=2,
    ).to_public_dict()

    assert unsafe["safe_for_current_phase"] is False
    assert "production_writes_must_remain_disabled" in unsafe["validation_errors"]
    assert "model_loading_must_remain_local_files_only" in unsafe["validation_errors"]
    assert "provider_network_calls_forbidden" in unsafe["validation_errors"]
    assert "llm_calls_forbidden" in unsafe["validation_errors"]
    assert "concurrency_must_remain_one" in unsafe["validation_errors"]


def test_predict_from_file_loads_model_before_preprocessing() -> None:
    tagger = object.__new__(WDTagger)
    calls: list[tuple[str, object]] = []

    class FakeModel:
        def run(self, _outputs, _feed):
            calls.append(("run", None))
            return [np.zeros((1, 1), dtype=np.float32)]

    def fake_ensure_loaded(model_name: str, *, local_files_only: bool = False) -> None:
        calls.append(("ensure_loaded", (model_name, local_files_only)))
        tagger._target_size = 448

    def fake_prepare_image_from_path(file_path: str):
        calls.append(("prepare", tagger._target_size))
        return file_path, np.zeros((448, 448, 3), dtype=np.float32)

    def fake_extract_tags(*_args, **_kwargs):
        calls.append(("extract", None))
        return [{"name": "safe_test_tag"}]

    tagger.ensure_loaded = fake_ensure_loaded
    tagger._prepare_image_from_path = fake_prepare_image_from_path
    tagger._extract_tags_from_scores = fake_extract_tags
    tagger._model = FakeModel()
    tagger._input_name = "input"
    tagger._inference_lock = threading.Lock()
    tagger._target_size = None

    result = WDTagger.predict_from_file(
        tagger,
        "sample.png",
        model_name="wd-swinv2-tagger-v3",
        local_files_only=True,
    )

    assert result == [{"name": "safe_test_tag"}]
    assert calls[:2] == [
        ("ensure_loaded", ("wd-swinv2-tagger-v3", True)),
        ("prepare", 448),
    ]


def test_s3a_foundation_plan_remains_dry_run_and_write_disabled() -> None:
    plan = build_s3a_foundation_dry_run_plan(LoadControlConfig())
    public = plan.to_public_dict()

    assert public["job_type"] == "s3a_foundation_plan"
    assert public["dry_run"] is True
    assert public["production_execution_enabled"] is False
    assert public["unattended_enabled"] is False
    assert public["safe_for_current_phase"] is True
    assert all(stage["writes_enabled"] is False for stage in public["stages"])
    assert {stage["name"] for stage in public["stages"]} == {
        "update_check",
        "hydration_read",
        "import_reuse",
        "classification",
        "ai_tagging",
        "localization",
        "summary",
    }


def test_provider_and_job_vocabulary_serializes_public_dicts() -> None:
    capability = ProviderCapability(
        provider="CPUExecutionProvider",
        available=True,
        practical=True,
        loaded=True,
        benchmark_status="completed",
    )
    provenance = ProviderProvenance(
        requested_provider_preference=("CUDAExecutionProvider", "CPUExecutionProvider"),
        available_providers=("CPUExecutionProvider",),
        actual_provider="CPUExecutionProvider",
        loaded_providers=("CPUExecutionProvider",),
        fallback_occurred=True,
        fallback_reason="unavailable_requested_providers=CUDAExecutionProvider",
    )

    assert capability.to_public_dict()["provider"] == "CPUExecutionProvider"
    assert provenance.to_public_dict()["actual_onnx_provider_loaded"] == "CPUExecutionProvider"
    assert provenance.to_public_dict()["fallback_reason"]


def test_s2g_m1_provider_probe_timeout_preserves_cpu_fallback(monkeypatch) -> None:
    profile = _s2g_m1_runner_profile()
    monkeypatch.setattr(
        s2g_m1_runner,
        "onnxruntime_status",
        lambda: {
            "importable": True,
            "version": "test",
            "device": "GPU",
            "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "error_code": None,
        },
    )
    monkeypatch.setattr(
        s2g_m1_runner,
        "cached_model_files",
        lambda _model_name: {
            "status": "cached",
            "model_name": "wd-swinv2-tagger-v3",
            "model_file_cached": True,
            "label_file_cached": True,
            "local_files_only": True,
            "network_download_required": False,
            "_model_path": "redacted-test-model.onnx",
        },
    )

    def fake_benchmark(**kwargs):
        provider = kwargs["provider"]
        if provider == "DmlExecutionProvider":
            time.sleep(0.2)
        return {
            "provider": provider,
            "available": True,
            "loaded": provider == "CPUExecutionProvider",
            "practical": provider == "CPUExecutionProvider",
            "status": "completed" if provider == "CPUExecutionProvider" else "should_have_timed_out",
            "seconds_per_item": 0.5 if provider == "CPUExecutionProvider" else None,
        }

    probe = s2g_m1_runner.build_capability_probe(
        profile,
        2,
        benchmark_func=fake_benchmark,
        provider_timeout_seconds=0.01,
    )

    assert probe["provider_matrix"]["directml"]["status"] == "timeout"
    assert probe["provider_matrix"]["directml"]["blocker"] == "provider_benchmark_timeout"
    assert probe["provider_matrix"]["cpu"]["status"] == "completed"
    assert probe["cpu_fallback_available"] is True
    assert probe["cpu_fallback_completed"] is True
    assert probe["recommended_provider"] == "CPUExecutionProvider"


def test_s2g_m1_write_reports_does_not_publish_unsafe_payload(monkeypatch, tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    markdown_path = tmp_path / "report.md"
    monkeypatch.setattr(s2g_m1_runner, "SUMMARY_PATH", summary_path)
    monkeypatch.setattr(s2g_m1_runner, "MARKDOWN_PATH", markdown_path)
    monkeypatch.setattr(
        s2g_m1_runner,
        "render_markdown",
        lambda _summary: r"unsafe C:\Users\kyloris\Pictures\secret.png",
    )
    summary = {
        "pipeline_contract": {
            "contract_id": "s2g_manual_sync_foundation_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": False},
        },
        "validation": {},
    }

    assert s2g_m1_runner.write_reports(summary) is False
    assert summary["pipeline_contract"]["status"] == "blocked_public_redaction_failed"
    assert summary["pipeline_contract"]["claims"]["target_met"] is False
    assert summary["pipeline_contract"]["claims"]["safe_to_merge"] is False
    assert summary["public_redaction"]["passed"] is False
    assert summary["public_redaction"]["unsafe_public_report_written"] is False
    assert not summary_path.exists()
    assert not markdown_path.exists()


def test_s2g_m1_runner_blocks_target_claims_when_head_evidence_is_stale(monkeypatch) -> None:
    base = s2g_m1_runner.PR123_MERGE_COMMIT
    monkeypatch.setattr(
        s2g_m1_runner,
        "build_ai_tagging_execution_profile",
        lambda _settings: SimpleNamespace(to_public_dict=lambda: _s2g_m1_runner_profile()),
    )
    monkeypatch.setattr(s2g_m1_runner, "build_capability_probe", lambda _profile, _samples: _s2g_m1_runner_probe())
    monkeypatch.setattr(s2g_m1_runner, "create_fixture_plan", lambda _profile, _probe: _s2g_m1_runner_plan())
    monkeypatch.setattr(
        s2g_m1_runner,
        "git_value",
        lambda args: {
            ("branch", "--show-current"): "codex/s2g-m1-ai-manual-sync-foundation",
            ("rev-parse", "HEAD"): "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ("rev-parse", "origin/main"): base,
        }.get(tuple(args), ""),
    )
    monkeypatch.setattr(s2g_m1_runner, "git_is_ancestor", lambda _ancestor, _descendant: True)

    summary = s2g_m1_runner.build_summary(
        SimpleNamespace(
            synthetic_samples=2,
            validation_focused_tests_passed=True,
            validated_implementation_sha=base,
            post_validation_changes_report_only=True,
        )
    )

    assert summary["pipeline_contract"]["status"] == "blocked_stale_head_evidence"
    assert summary["pipeline_contract"]["claims"]["target_met"] is False
    assert summary["pipeline_contract"]["claims"]["safe_to_merge"] is False
    assert summary["head_evidence"]["validated_implementation_is_not_base_main"] is False
    assert summary["head_evidence"]["head_evidence_valid"] is False


def test_s2g_m1_runner_allows_target_claims_when_head_evidence_is_current(monkeypatch) -> None:
    base = s2g_m1_runner.PR123_MERGE_COMMIT
    implementation_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    monkeypatch.setattr(
        s2g_m1_runner,
        "build_ai_tagging_execution_profile",
        lambda _settings: SimpleNamespace(to_public_dict=lambda: _s2g_m1_runner_profile()),
    )
    monkeypatch.setattr(s2g_m1_runner, "build_capability_probe", lambda _profile, _samples: _s2g_m1_runner_probe())
    monkeypatch.setattr(s2g_m1_runner, "create_fixture_plan", lambda _profile, _probe: _s2g_m1_runner_plan())
    monkeypatch.setattr(
        s2g_m1_runner,
        "git_value",
        lambda args: {
            ("branch", "--show-current"): "codex/s2g-m1-ai-manual-sync-foundation",
            ("rev-parse", "HEAD"): implementation_sha,
            ("rev-parse", "origin/main"): base,
        }.get(tuple(args), ""),
    )
    monkeypatch.setattr(s2g_m1_runner, "git_is_ancestor", lambda _ancestor, _descendant: True)

    summary = s2g_m1_runner.build_summary(
        SimpleNamespace(
            synthetic_samples=2,
            validation_focused_tests_passed=True,
            validated_implementation_sha=implementation_sha,
            post_validation_changes_report_only=True,
        )
    )

    assert summary["pipeline_contract"]["status"] == "target_met"
    assert summary["pipeline_contract"]["claims"]["target_met"] is True
    assert summary["pipeline_contract"]["claims"]["safe_to_merge"] is True
    assert summary["head_evidence"]["head_evidence_valid"] is True
