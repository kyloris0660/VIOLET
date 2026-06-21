#!/usr/bin/env python3
"""S2G-1X GPU AI tagging capability probe.

Lifecycle: reusable validation/safety tool.

The probe is intentionally public-report safe:
- no DB connection;
- no production media reads by default;
- no media_tags writes;
- no model download; HuggingFace files are resolved with local_files_only=True;
- no raw local paths in public JSON/Markdown output.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.s2g_s3a_job_control import (  # noqa: E402
    ProviderCapability,
    build_integration_decision,
    build_s2g1x_load_control,
    build_s3a_dev_dry_run_plan,
)

PHASE = "S2G-1X"
TITLE = "GPU AI Tagging Probe and S3A Integration Decision"
CONTRACT_ID = "s2g1x_probe_contract_v1"
REPORT_MD = ROOT / "docs" / "reports" / "s2g1x-gpu-ai-tagging-probe.md"
REPORT_JSON = ROOT / "docs" / "reports" / "s2g1x-gpu-ai-tagging-probe-summary.json"

WD_MODELS = {
    "wd-eva02-large-tagger-v3": "SmilingWolf/wd-eva02-large-tagger-v3",
    "wd-vit-tagger-v3": "SmilingWolf/wd-vit-tagger-v3",
    "wd-swinv2-tagger-v3": "SmilingWolf/wd-swinv2-tagger-v3",
    "wd-convnext-tagger-v3": "SmilingWolf/wd-convnext-tagger-v3",
    "wd-vit-large-tagger-v3": "SmilingWolf/wd-vit-large-tagger-v3",
}
MODEL_FILENAME = "model.onnx"
LABEL_FILENAME = "selected_tags.csv"
DEFAULT_PROVIDERS = ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider")
ENV_FILE = ROOT / ".env"
DEFAULT_AI_SETTINGS: dict[str, Any] = {
    "AI_TAGGING_ENABLED": False,
    "AI_MODEL_NAME": "wd-swinv2-tagger-v3",
    "AI_GENERAL_THRESHOLD": 0.35,
    "AI_CHARACTER_THRESHOLD": 0.65,
    "AI_RATING_THRESHOLD": 0.50,
    "AI_SUGGESTION_THRESHOLD": 0.20,
    "AI_TAGGING_BATCH_MAX_ITEMS": 10,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return "[redacted-path]"
    if hasattr(value, "to_public_dict"):
        return value.to_public_dict()
    return str(value)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def git_value(args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def build_head_evidence() -> dict[str, Any]:
    head = git_value(["rev-parse", "HEAD"])
    return {
        "probe_run_head_sha": head,
        "probe_run_head_sha_scope": "git HEAD used while running the local capability probe and writing this report",
        "report_generation_head_sha": head,
        "report_generation_head_sha_scope": "same local generation step; later supplemental commits may update PR metadata without rewriting this file",
        "current_pr_head_sha": "represented_by_pr_metadata_after_commit",
        "current_pr_head_sha_scope": "a committed report cannot contain the final commit SHA that includes itself",
        "top_level_head_sha_omitted": True,
    }


def dependency_status(module_name: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(module_name)
    return {"module": module_name, "available": spec is not None}


def public_error_code(exc: BaseException) -> str:
    name = exc.__class__.__name__ or "Error"
    return name[:80]


def read_dotenv_values(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def side_effect_free_setting(name: str, dotenv_values: Mapping[str, str]) -> tuple[Any, str]:
    if name in os.environ:
        return os.environ[name], "process_env"
    if name in dotenv_values:
        return dotenv_values[name], "repo_dotenv"
    return DEFAULT_AI_SETTINGS[name], "documented_default"


def parse_bool_setting(name: str, raw: Any, errors: list[dict[str, str]]) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().casefold()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    errors.append({"setting": name, "error_code": "invalid_bool_used_default"})
    return bool(DEFAULT_AI_SETTINGS[name])


def parse_float_setting(name: str, raw: Any, errors: list[dict[str, str]]) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        errors.append({"setting": name, "error_code": "invalid_float_used_default"})
        return float(DEFAULT_AI_SETTINGS[name])


def parse_int_setting(name: str, raw: Any, errors: list[dict[str, str]]) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        errors.append({"setting": name, "error_code": "invalid_int_used_default"})
        return int(DEFAULT_AI_SETTINGS[name])
    if value < 1:
        errors.append({"setting": name, "error_code": "non_positive_int_used_default"})
        return int(DEFAULT_AI_SETTINGS[name])
    return value


def load_settings_snapshot(model_override: str | None) -> dict[str, Any]:
    dotenv_values = read_dotenv_values()
    errors: list[dict[str, str]] = []
    raw_enabled, enabled_source = side_effect_free_setting("AI_TAGGING_ENABLED", dotenv_values)
    raw_model, model_source = side_effect_free_setting("AI_MODEL_NAME", dotenv_values)
    raw_general, general_source = side_effect_free_setting("AI_GENERAL_THRESHOLD", dotenv_values)
    raw_character, character_source = side_effect_free_setting("AI_CHARACTER_THRESHOLD", dotenv_values)
    raw_rating, rating_source = side_effect_free_setting("AI_RATING_THRESHOLD", dotenv_values)
    raw_suggestion, suggestion_source = side_effect_free_setting("AI_SUGGESTION_THRESHOLD", dotenv_values)
    raw_batch, batch_source = side_effect_free_setting("AI_TAGGING_BATCH_MAX_ITEMS", dotenv_values)
    return {
        "ai_tagging_enabled": parse_bool_setting("AI_TAGGING_ENABLED", raw_enabled, errors),
        "model_name": model_override or str(raw_model or DEFAULT_AI_SETTINGS["AI_MODEL_NAME"]),
        "general_threshold": parse_float_setting("AI_GENERAL_THRESHOLD", raw_general, errors),
        "character_threshold": parse_float_setting("AI_CHARACTER_THRESHOLD", raw_character, errors),
        "rating_threshold": parse_float_setting("AI_RATING_THRESHOLD", raw_rating, errors),
        "suggestion_threshold": parse_float_setting("AI_SUGGESTION_THRESHOLD", raw_suggestion, errors),
        "batch_max_items": parse_int_setting("AI_TAGGING_BATCH_MAX_ITEMS", raw_batch, errors),
        "source": "side_effect_free_env_or_defaults",
        "config_sources": {
            "AI_TAGGING_ENABLED": enabled_source,
            "AI_MODEL_NAME": "cli_override" if model_override else model_source,
            "AI_GENERAL_THRESHOLD": general_source,
            "AI_CHARACTER_THRESHOLD": character_source,
            "AI_RATING_THRESHOLD": rating_source,
            "AI_SUGGESTION_THRESHOLD": suggestion_source,
            "AI_TAGGING_BATCH_MAX_ITEMS": batch_source,
        },
        "config_parse_errors": errors,
        "app_config_imported": False,
    }


def inspect_current_app_backend() -> dict[str, Any]:
    source = ROOT / "backend" / "app" / "services" / "wd_tagger.py"
    text = source.read_text(encoding="utf-8", errors="replace")
    hardcoded_cpu = "providers=['CPUExecutionProvider']" in text or 'providers=["CPUExecutionProvider"]' in text
    uses_all_cpu_threads = "intra_op_num_threads = cpu_count" in text
    uses_parallel_execution = "ExecutionMode.ORT_PARALLEL" in text
    return {
        "source_file": "backend/app/services/wd_tagger.py",
        "hardcoded_cpu_execution_provider": hardcoded_cpu,
        "forced_provider": "CPUExecutionProvider" if hardcoded_cpu else "runtime_default_or_unknown",
        "uses_all_cpu_threads_by_default": uses_all_cpu_threads,
        "uses_parallel_execution_mode": uses_parallel_execution,
        "preprocess_workers": "min(4, os.cpu_count())" if "min(4, (os.cpu_count() or 4))" in text else "unknown",
    }


def cached_model_files(model_name: str) -> dict[str, Any]:
    repo = WD_MODELS.get(model_name)
    result: dict[str, Any] = {
        "model_name": model_name,
        "repo_id": repo,
        "model_file_cached": False,
        "label_file_cached": False,
        "network_download_required": False,
        "local_files_only": True,
        "error_code": None,
        "_model_path": None,
        "_label_path": None,
    }
    if not repo:
        result["error_code"] = "unknown_model_name"
        return result
    try:
        import huggingface_hub

        model_path = huggingface_hub.hf_hub_download(repo, MODEL_FILENAME, local_files_only=True)
        label_path = huggingface_hub.hf_hub_download(repo, LABEL_FILENAME, local_files_only=True)
        result.update(
            {
                "model_file_cached": True,
                "label_file_cached": True,
                "_model_path": model_path,
                "_label_path": label_path,
            }
        )
    except Exception as exc:
        result["network_download_required"] = True
        result["error_code"] = public_error_code(exc)
    return result


def public_model_cache(cache: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in cache.items() if not key.startswith("_")}


def onnxruntime_environment() -> dict[str, Any]:
    status = {
        "importable": False,
        "version": None,
        "device": None,
        "available_providers": [],
        "error_code": None,
    }
    try:
        import onnxruntime as rt

        status.update(
            {
                "importable": True,
                "version": getattr(rt, "__version__", None),
                "device": rt.get_device(),
                "available_providers": list(rt.get_available_providers()),
            }
        )
    except Exception as exc:
        status["error_code"] = public_error_code(exc)
    return status


def bounded_session_options(cpu_intra_op_threads: int | None, cpu_inter_op_threads: int | None):
    import onnxruntime as rt

    options = rt.SessionOptions()
    options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
    if cpu_intra_op_threads:
        options.intra_op_num_threads = cpu_intra_op_threads
    if cpu_inter_op_threads:
        options.inter_op_num_threads = cpu_inter_op_threads
    options.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
    options.enable_mem_pattern = True
    options.enable_cpu_mem_arena = True
    return options


def synthetic_input_for_session(session: Any, batch_size: int):
    import numpy as np

    shape = list(session.get_inputs()[0].shape)
    dims: list[int] = []
    for index, dim in enumerate(shape):
        if index == 0:
            dims.append(batch_size)
        elif isinstance(dim, int) and dim > 0:
            dims.append(dim)
        else:
            dims.append(1)
    if len(dims) != 4:
        raise ValueError("unexpected_model_input_rank")
    return np.zeros(dims, dtype=np.float32)


def benchmark_provider(
    *,
    provider: str,
    available_providers: Sequence[str],
    model_path: str | None,
    allow_model_load: bool,
    sample_count: int,
    batch_size: int,
    cpu_intra_op_threads: int | None,
    cpu_inter_op_threads: int | None,
) -> ProviderCapability:
    if provider not in available_providers:
        return ProviderCapability(provider, False, False, False, "not_available")
    if not allow_model_load:
        return ProviderCapability(provider, True, False, False, "model_load_not_requested")
    if not model_path:
        return ProviderCapability(provider, True, False, False, "model_not_cached")

    try:
        import onnxruntime as rt

        options = bounded_session_options(cpu_intra_op_threads, cpu_inter_op_threads)
        start_load = time.perf_counter()
        session = rt.InferenceSession(model_path, sess_options=options, providers=[provider])
        load_seconds = time.perf_counter() - start_load
        actual_providers = list(session.get_providers())
        if provider not in actual_providers:
            return ProviderCapability(provider, True, False, False, "loaded_different_provider", load_error_code="provider_not_selected")

        effective_batch = max(1, batch_size)
        loops = max(1, math.ceil(sample_count / effective_batch))
        input_name = session.get_inputs()[0].name
        batch = synthetic_input_for_session(session, effective_batch)
        session.run(None, {input_name: batch})
        start = time.perf_counter()
        processed = 0
        for _ in range(loops):
            session.run(None, {input_name: batch})
            processed += effective_batch
        elapsed = max(time.perf_counter() - start, 1e-9)
        processed = min(processed, sample_count)
        throughput = processed / elapsed
        return ProviderCapability(
            provider=provider,
            available=True,
            practical=True,
            loaded=True,
            benchmark_status="completed",
            throughput_items_per_second=round(throughput, 4),
            seconds_per_item=round(elapsed / processed, 4) if processed else None,
            load_error_code=None if load_seconds >= 0 else "unknown",
        )
    except Exception as exc:
        return ProviderCapability(
            provider=provider,
            available=True,
            practical=False,
            loaded=False,
            benchmark_status="load_or_inference_failed",
            load_error_code=public_error_code(exc),
        )


def provider_key(provider: str) -> str:
    if provider == "CUDAExecutionProvider":
        return "cuda"
    if provider == "DmlExecutionProvider":
        return "directml"
    if provider == "CPUExecutionProvider":
        return "cpu"
    return provider.lower().replace("executionprovider", "")


def load_control_risks(current_backend: Mapping[str, Any], load_control_errors: Sequence[str]) -> list[str]:
    risks: list[str] = []
    if current_backend.get("hardcoded_cpu_execution_provider"):
        risks.append("current_app_forces_cpu_provider")
    if current_backend.get("uses_all_cpu_threads_by_default"):
        risks.append("current_app_uses_all_cpu_threads_by_default")
    if "pause_resume_required_but_not_implemented" in load_control_errors:
        risks.append("pause_resume_not_yet_implemented")
    risks.extend(
        [
            "ai_tag_job_has_cancel_but_no_durable_pause_resume",
            "provider_backend_not_persisted_in_media_tag_provenance",
            "s3a_requires_shared_per_item_ledgers_before_production_execution",
        ]
    )
    return sorted(set(risks))


def positive_throughput(capability: ProviderCapability) -> bool:
    return capability.throughput_items_per_second is not None and capability.throughput_items_per_second > 0


def required_provider_checks_completed(capabilities: Mapping[str, ProviderCapability]) -> bool:
    return all(
        provider in capabilities and capabilities[provider].benchmark_status != "not_requested"
        for provider in DEFAULT_PROVIDERS
    )


def cpu_completion_evidence(capabilities: Mapping[str, ProviderCapability]) -> bool:
    cpu = capabilities.get("CPUExecutionProvider")
    return bool(
        cpu
        and cpu.available
        and cpu.loaded
        and cpu.practical
        and cpu.benchmark_status == "completed"
        and positive_throughput(cpu)
    )


def model_completion_evidence(model_cache: Mapping[str, Any]) -> bool:
    return bool(
        model_cache.get("model_file_cached")
        and model_cache.get("label_file_cached")
        and not model_cache.get("network_download_required")
    )


def build_completion_evidence(
    *,
    model_cache: Mapping[str, Any],
    capabilities: Mapping[str, ProviderCapability],
    allow_model_load: bool,
) -> dict[str, Any]:
    return {
        "allow_model_load": allow_model_load,
        "model_file_cached": bool(model_cache.get("model_file_cached")),
        "label_file_cached": bool(model_cache.get("label_file_cached")),
        "network_download_required": bool(model_cache.get("network_download_required")),
        "all_default_providers_checked": required_provider_checks_completed(capabilities),
        "checked_default_providers": [
            provider
            for provider in DEFAULT_PROVIDERS
            if provider in capabilities and capabilities[provider].benchmark_status != "not_requested"
        ],
        "cpu_loaded": bool(capabilities.get("CPUExecutionProvider") and capabilities["CPUExecutionProvider"].loaded),
        "cpu_practical": bool(capabilities.get("CPUExecutionProvider") and capabilities["CPUExecutionProvider"].practical),
        "cpu_benchmark_status": capabilities.get(
            "CPUExecutionProvider",
            ProviderCapability("CPUExecutionProvider", False, False, False, "not_requested"),
        ).benchmark_status,
        "cpu_throughput_items_per_second": capabilities.get(
            "CPUExecutionProvider",
            ProviderCapability("CPUExecutionProvider", False, False, False, "not_requested"),
        ).throughput_items_per_second,
    }


def derive_pipeline_contract(
    *,
    model_cache: Mapping[str, Any],
    capabilities: Mapping[str, ProviderCapability],
    allow_model_load: bool,
) -> dict[str, Any]:
    model_ready = model_completion_evidence(model_cache)
    cpu_ready = cpu_completion_evidence(capabilities)
    providers_checked = required_provider_checks_completed(capabilities)
    if model_ready and cpu_ready and providers_checked:
        status = "target_met"
    elif not model_ready:
        status = "blocked_model_unavailable"
    elif not allow_model_load or not providers_checked:
        status = "evidence_collected"
    else:
        status = "blocked_probe_unavailable"
    target_met = status == "target_met"
    return {
        "contract_id": CONTRACT_ID,
        "status": status,
        "claims": {"target_met": target_met, "safe_to_merge": target_met},
    }


def scan_public(summary: Mapping[str, Any], markdown: str) -> dict[str, Any]:
    try:
        from scripts.phase_contracts.contract_checks import scan_public_payload

        findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown})
    except Exception as exc:  # pragma: no cover - broken checker env only
        findings = [{"code": "redaction_scan_error", "error_code": public_error_code(exc)}]
    return {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
    }


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    settings_snapshot = load_settings_snapshot(args.model_name)
    model_name = settings_snapshot["model_name"]
    model_cache = cached_model_files(model_name)
    onnx_env = onnxruntime_environment()
    current_backend = inspect_current_app_backend()
    available = onnx_env.get("available_providers") or []
    provider_preference = tuple(args.provider or DEFAULT_PROVIDERS)
    load_control = build_s2g1x_load_control(
        cpu_count=os.cpu_count() or 1,
        configured_batch_max=int(settings_snapshot["batch_max_items"]),
        provider_preference=provider_preference,
    )
    if args.batch_size is not None:
        load_control = build_s2g1x_load_control(
            cpu_count=os.cpu_count() or 1,
            configured_batch_max=args.batch_size,
            provider_preference=provider_preference,
        )
    capabilities: dict[str, ProviderCapability] = {}
    for provider in provider_preference:
        capabilities[provider] = benchmark_provider(
            provider=provider,
            available_providers=available,
            model_path=model_cache.get("_model_path"),
            allow_model_load=args.allow_model_load,
            sample_count=args.synthetic_samples,
            batch_size=load_control.batch_size,
            cpu_intra_op_threads=load_control.cpu_intra_op_threads,
            cpu_inter_op_threads=load_control.cpu_inter_op_threads,
        )
    for provider in DEFAULT_PROVIDERS:
        capabilities.setdefault(
            provider,
            ProviderCapability(provider, provider in available, False, False, "not_requested"),
        )
    plan = build_s3a_dev_dry_run_plan(load_control)
    decision = build_integration_decision(
        provider_capabilities=capabilities,
        current_app_forced_provider=str(current_backend.get("forced_provider") or "unknown"),
        load_control=load_control,
    )
    pipeline_contract = derive_pipeline_contract(
        model_cache=model_cache,
        capabilities=capabilities,
        allow_model_load=args.allow_model_load,
    )
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": utc_now(),
        "branch": git_value(["branch", "--show-current"]),
        "head_evidence": build_head_evidence(),
        "pipeline_contract": pipeline_contract,
        "capability_probe": {
            "completed": True,
            "completion_evidence": build_completion_evidence(
                model_cache=model_cache,
                capabilities=capabilities,
                allow_model_load=args.allow_model_load,
            ),
            "safe_probe": {
                "no_db_connection": True,
                "no_production_db_writes": True,
                "no_media_tags_writes": True,
                "no_full_library_ai_tagging": True,
                "no_model_download": True,
                "local_files_only": True,
                "sample_source": "synthetic_generated_arrays",
                "sample_count": args.synthetic_samples,
            },
            "runtime": {
                "os": platform.system(),
                "windows_setup": platform.system().casefold() == "windows",
                "python_executable_public_name": Path(sys.executable).name,
                "python_version": python_version,
                "dependencies": [
                    dependency_status("onnxruntime"),
                    dependency_status("huggingface_hub"),
                    dependency_status("numpy"),
                    dependency_status("PIL"),
                ],
                "onnxruntime": onnx_env,
            },
            "model_identity": public_model_cache(model_cache),
            "thresholds": {
                "source": settings_snapshot["source"],
                "ai_tagging_enabled": settings_snapshot["ai_tagging_enabled"],
                "general_threshold": settings_snapshot["general_threshold"],
                "character_threshold": settings_snapshot["character_threshold"],
                "rating_threshold": settings_snapshot["rating_threshold"],
                "suggestion_threshold": settings_snapshot["suggestion_threshold"],
                "batch_max_items": settings_snapshot["batch_max_items"],
                "config_sources": settings_snapshot.get("config_sources", {}),
                "config_parse_errors": settings_snapshot.get("config_parse_errors", []),
                "app_config_imported": bool(settings_snapshot.get("app_config_imported", False)),
            },
            "current_app_backend": current_backend,
            "provider_matrix": {
                provider_key(provider): capability.to_public_dict()
                for provider, capability in capabilities.items()
                if provider in DEFAULT_PROVIDERS
            },
            "benchmark": {
                "allow_model_load": args.allow_model_load,
                "synthetic_sample_count": args.synthetic_samples,
                "batch_size": load_control.batch_size,
                "loaded_providers": [
                    capability.provider
                    for capability in capabilities.values()
                    if capability.loaded
                ],
            },
        },
        "load_control": {
            "recommended_config": load_control.to_public_dict(),
            "risk_flags": load_control_risks(current_backend, load_control.validation_errors()),
            "implemented_now": [
                "capability_probe_runner",
                "provider_capability_public_model",
                "load_control_config_model",
                "dry_run_only_s3a_stage_plan",
                "s2g1x_probe_contract",
            ],
            "requires_follow_up": [
                "runtime provider abstraction in WDTagger",
                "durable provider/model/batch/backend provenance for AI tag results",
                "pause/resume semantics beyond cancel",
                "failure budgets and retry ledgers shared by S2G and S3A",
                "production job runner promotion under explicit operator approval",
            ],
        },
        "s3a_dev_dry_run_plan": plan.to_public_dict(),
        "s2g_s3a_decision": decision,
        "safety": {
            "production_db_writes": False,
            "production_import": False,
            "production_classification": False,
            "production_ai_tagging": False,
            "production_localization": False,
            "production_s3a_execution_enabled": False,
            "unattended_auto_sync_enabled": False,
            "provider_pixiv_gallery_dl_saucenao_google_calls": False,
            "sourceconcept_or_entity": False,
            "confirmed_entity_assignments": False,
            "source_icloud_mutation": False,
            "cleanup_delete_reset_drop_truncate": False,
            "model_download": False,
        },
        "public_reports": {
            "summary_json_path": "docs/reports/s2g1x-gpu-ai-tagging-probe-summary.json",
            "markdown_report_path": "docs/reports/s2g1x-gpu-ai-tagging-probe.md",
            "path_style": "repo_relative_public_artifacts",
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "scripts/run_s2g1_ai_tagging_capability_probe.py",
                    "classification": "reusable validation/safety tool",
                    "committed": True,
                },
                {
                    "path": "scripts/s2g_s3a_job_control.py",
                    "classification": "reusable validation/safety tool",
                    "committed": True,
                },
                {
                    "path": "docs/reports/s2g1x-gpu-ai-tagging-probe.md",
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
                {
                    "path": "docs/reports/s2g1x-gpu-ai-tagging-probe-summary.json",
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
            ]
        },
    }
    markdown = render_report(summary)
    summary["public_redaction"] = scan_public(summary, markdown)
    return summary


def render_report(summary: Mapping[str, Any]) -> str:
    probe = summary["capability_probe"]
    provider_matrix = probe["provider_matrix"]
    decision = summary["s2g_s3a_decision"]
    load = summary["load_control"]
    runtime = probe["runtime"]
    providers = ", ".join(runtime["onnxruntime"].get("available_providers") or [])

    def provider_line(key: str, label: str) -> str:
        row = provider_matrix.get(key, {})
        throughput = row.get("throughput_items_per_second")
        throughput_text = "N/A" if throughput is None else str(throughput)
        return (
            f"| {label} | {row.get('available')} | {row.get('practical')} | "
            f"{row.get('loaded')} | {row.get('benchmark_status')} | {throughput_text} |"
        )

    lines = [
        "# S2G-1X GPU AI Tagging Probe and S3A Integration Decision",
        "",
        "## Summary",
        "",
        f"- Contract: `{summary['pipeline_contract']['contract_id']}`.",
        f"- Status: `{summary['pipeline_contract']['status']}`.",
        f"- Target met / safe to merge: `{summary['pipeline_contract']['claims']['target_met']}` / `{summary['pipeline_contract']['claims']['safe_to_merge']}`.",
        f"- Current app WD backend: `{probe['current_app_backend']['forced_provider']}`.",
        f"- Configured model: `{probe['model_identity']['model_name']}`.",
        f"- Model cached locally: `{probe['model_identity']['model_file_cached']}`.",
        f"- Network model download performed: `{not probe['safe_probe']['no_model_download']}`.",
        f"- Decision: `{decision['decision']}`.",
        "",
        "## Runtime Environment",
        "",
        f"- Python executable public name: `{runtime['python_executable_public_name']}`.",
        f"- Python version: `{runtime['python_version']}`.",
        f"- ONNX Runtime version: `{runtime['onnxruntime'].get('version')}`.",
        f"- ONNX Runtime device: `{runtime['onnxruntime'].get('device')}`.",
        f"- ONNX Runtime providers: `{providers}`.",
        "",
        "## Provider Capability",
        "",
        "| Provider | Available | Practical | Loaded | Benchmark status | Items/sec |",
        "| --- | --- | --- | --- | --- | --- |",
        provider_line("cuda", "CUDA"),
        provider_line("directml", "DirectML"),
        provider_line("cpu", "CPU"),
        "",
        "## Threshold And Model Config",
        "",
        f"- General threshold: `{probe['thresholds']['general_threshold']}`.",
        f"- Character threshold: `{probe['thresholds']['character_threshold']}`.",
        f"- Rating threshold: `{probe['thresholds']['rating_threshold']}`.",
        f"- Suggestion threshold: `{probe['thresholds']['suggestion_threshold']}`.",
        f"- Batch max items setting: `{probe['thresholds']['batch_max_items']}`.",
        "",
        "## Load-Control Evidence",
        "",
        f"- Conservative probe batch size: `{load['recommended_config']['batch_size']}`.",
        f"- Worker count: `{load['recommended_config']['worker_count']}`.",
        f"- Max concurrent jobs: `{load['recommended_config']['max_concurrent_jobs']}`.",
        f"- CPU intra/inter op thread cap: `{load['recommended_config']['cpu_intra_op_threads']}` / `{load['recommended_config']['cpu_inter_op_threads']}`.",
        f"- Current risk flags: `{', '.join(load['risk_flags'])}`.",
        "",
        "## S2G/S3A Decision",
        "",
        "- S2G and S3A should share one job/progress/throttle/ledger foundation.",
        "- Production S3A execution should stay split into a later operator-approved phase.",
        f"- Recommended boundary: `{decision['recommended_execution_boundary']}`.",
        f"- Recommended next phase: `{decision['recommended_next_phase']}`.",
        "",
        "## Safety",
        "",
        "- No DB connection or production DB writes.",
        "- No production import, classification, AI tagging, or localization.",
        "- No production S3A execution or unattended S3B automation.",
        "- No provider/Pixiv/gallery-dl/SauceNAO/Google calls.",
        "- No SourceConcept, Entity bridge, confirmed assignment, cleanup, delete, reset, drop, or truncate.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider", action="append", choices=DEFAULT_PROVIDERS)
    parser.add_argument("--allow-model-load", action="store_true")
    parser.add_argument("--synthetic-samples", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--output-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--output-md", type=Path, default=REPORT_MD)
    parser.add_argument("--no-write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.synthetic_samples < 1 or args.synthetic_samples > 16:
        parser.error("--synthetic-samples must be between 1 and 16")
    summary = build_summary(args)
    markdown = render_report(summary)
    redaction_passed = bool(summary.get("public_redaction", {}).get("passed"))
    if not redaction_passed:
        print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=json_default))
        print("S2G-1X probe redaction failed; public report files were not written.", file=sys.stderr)
        return 2
    if not args.no_write:
        write_json(args.output_json, summary)
        write_text(args.output_md, markdown)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
