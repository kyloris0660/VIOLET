from __future__ import annotations

import threading
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

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
from app.services.wd_tagger import WDTagger  # noqa: E402


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
