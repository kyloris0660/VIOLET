from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.job_control import (  # noqa: E402
    LoadControlConfig,
    ProviderCapability,
    ProviderProvenance,
    build_ai_tagging_load_control_config,
    build_s3a_foundation_dry_run_plan,
    select_onnx_provider,
)


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
        AI_TAGGING_EXECUTION_MODE="ORT_SEQUENTIAL",
        AI_TAGGING_PROCESS_PRIORITY="below_normal",
    )

    config = build_ai_tagging_load_control_config(settings)
    public = config.to_public_dict()

    assert public["batch_size"] == 10
    assert public["max_concurrent_jobs"] == 1
    assert public["preprocess_workers"] == 2
    assert public["cpu_intra_op_threads"] == 4
    assert public["cpu_inter_op_threads"] == 1
    assert public["execution_mode"] == "ORT_SEQUENTIAL"
    assert public["validation_errors"] == []


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
