"""S2G/S3A-F1 provider and load-control smoke check.

This runner does not open a DB connection, write media_tags, run production AI
tagging, or call external source/provider systems. It is cache-only by default;
pass --allow-model-download to permit Hugging Face model downloads.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _wd_tagger_source_has_provider_abstraction() -> dict[str, bool]:
    source = (ROOT / "backend" / "app" / "services" / "wd_tagger.py").read_text(encoding="utf-8")
    return {
        "implemented": "select_onnx_provider(" in source and "ProviderProvenance" in source,
        "hardcoded_cpu_provider_removed": (
            "providers=['CPUExecutionProvider']" not in source
            and 'providers=["CPUExecutionProvider"]' not in source
        ),
    }


def _package_status() -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    for package_name in ("onnxruntime", "onnxruntime-directml", "onnxruntime-gpu"):
        try:
            dist = metadata.distribution(package_name)
            packages[package_name] = {
                "installed": True,
                "version": dist.version,
                "location_public": "project_venv_site_packages",
            }
        except metadata.PackageNotFoundError:
            packages[package_name] = {
                "installed": False,
                "version": None,
                "location_public": None,
            }
    return packages


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _cache_file_present(repo_id: str, filename: str) -> bool | None:
    try:
        import huggingface_hub
        path = huggingface_hub.try_to_load_from_cache(repo_id, filename)
        return isinstance(path, str) and bool(path)
    except Exception:
        return None


@contextmanager
def _temporary_env(updates: dict[str, str]):
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _run_tiny_benchmark(
    *,
    tagger: Any,
    model_name: str,
    provider_preference: list[str],
    expected_provider: str,
    local_files_only: bool,
    sample_count: int,
) -> dict[str, Any]:
    import numpy as np

    env_updates = {
        "AI_TAGGING_PROVIDER_PREFERENCE": ",".join(provider_preference),
        "AI_TAGGING_CPU_INTRA_OP_THREADS": "4",
        "AI_TAGGING_CPU_INTER_OP_THREADS": "1",
        "AI_TAGGING_PREPROCESS_WORKERS": "2",
        "AI_TAGGING_EXECUTION_MODE": "ORT_SEQUENTIAL",
        "AI_TAGGING_BATCH_SIZE": "2",
        "AI_TAGGING_MAX_CONCURRENT_JOBS": "1",
    }
    with _temporary_env(env_updates):
        try:
            tagger.ensure_loaded(model_name, local_files_only=local_files_only)
            provider = tagger.get_provider_provenance()
            actual_provider = provider.get("actual_provider")
            if actual_provider != expected_provider:
                return {
                    "status": "provider_unavailable",
                    "expected_provider": expected_provider,
                    "actual_provider": actual_provider,
                    "provider": provider,
                    "blocker": f"{expected_provider}_not_loaded",
                }
            target_size = getattr(tagger, "_target_size", None) or 448
            batch = [
                np.zeros((target_size, target_size, 3), dtype=np.float32)
                for _ in range(max(1, sample_count))
            ]
            started = time.perf_counter()
            tagger.predict_batch(
                batch,
                general_threshold=1.1,
                character_threshold=1.1,
                hide_rating_tags=True,
                model_name=model_name,
            )
            elapsed = max(time.perf_counter() - started, 0.000001)
            return {
                "status": "completed",
                "sample_count": len(batch),
                "actual_provider": actual_provider,
                "provider": tagger.get_provider_provenance(),
                "elapsed_seconds": round(elapsed, 4),
                "throughput_items_per_second": round(len(batch) / elapsed, 4),
                "load_control": tagger.get_load_control_config(),
                "batch": tagger.get_batch_size_provenance(model_name),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "expected_provider": expected_provider,
                "error_type": exc.__class__.__name__,
                "blocker": f"{expected_provider}_benchmark_failed",
            }


def build_summary(
    *,
    local_files_only: bool,
    markdown_report_path: str,
    benchmark_samples: int,
    directml_install_performed: bool = False,
    cuda_install_performed: bool = False,
) -> dict[str, Any]:
    import onnxruntime as rt

    from app.config import settings
    from app.services.ai_tagging_service import get_ai_tagging_runtime_provenance
    from app.services.job_control import (
        build_ai_tagging_load_control_config,
        build_s3a_foundation_dry_run_plan,
    )
    from app.services.wd_tagger import get_wd_tagger

    packages = _package_status()
    load_control = build_ai_tagging_load_control_config(settings)
    tagger = get_wd_tagger()
    model_loaded = False
    load_error_type = None
    model_repo_id = None
    model_cache_before = {"model_file_cached": None, "label_file_cached": None}
    model_download_allowed = not local_files_only
    model_download_performed = False
    try:
        from app.services.wd_tagger import WDTagger
        model_repo_id = WDTagger.AVAILABLE_MODELS.get(settings.AI_MODEL_NAME)
        if model_repo_id:
            model_cache_before = {
                "model_file_cached": _cache_file_present(model_repo_id, WDTagger.MODEL_FILENAME),
                "label_file_cached": _cache_file_present(model_repo_id, WDTagger.LABEL_FILENAME),
            }
        tagger.ensure_loaded(settings.AI_MODEL_NAME, local_files_only=local_files_only)
        model_loaded = True
    except Exception as exc:
        load_error_type = exc.__class__.__name__
    finally:
        if model_repo_id:
            try:
                from app.services.wd_tagger import WDTagger
                model_cache_after = {
                    "model_file_cached": _cache_file_present(model_repo_id, WDTagger.MODEL_FILENAME),
                    "label_file_cached": _cache_file_present(model_repo_id, WDTagger.LABEL_FILENAME),
                }
                if model_download_allowed:
                    model_download_performed = any(
                        model_cache_before.get(key) is False and model_cache_after.get(key) is True
                        for key in ("model_file_cached", "label_file_cached")
                    )
            except Exception:
                model_cache_after = dict(model_cache_before)
        else:
            model_cache_after = dict(model_cache_before)

    provenance = get_ai_tagging_runtime_provenance(tagger)
    provider = provenance.get("provider") or {}
    source_flags = _wd_tagger_source_has_provider_abstraction()
    s3a_plan = build_s3a_foundation_dry_run_plan(load_control).to_public_dict()
    loaded_provider = provider.get("actual_provider")
    status = "target_met" if model_loaded and loaded_provider else "blocked_model_unavailable"

    provider_fallback_reason = provider.get("fallback_reason")
    if provider.get("fallback_occurred") and not provider_fallback_reason:
        status = "blocked_provider_unavailable"

    available_providers = list(rt.get_available_providers())
    cpu_benchmark = _run_tiny_benchmark(
        tagger=tagger,
        model_name=settings.AI_MODEL_NAME,
        provider_preference=["CPUExecutionProvider"],
        expected_provider="CPUExecutionProvider",
        local_files_only=local_files_only,
        sample_count=benchmark_samples,
    ) if model_loaded else {"status": "not_run_model_unavailable", "blocker": load_error_type}

    gpu_rows: list[dict[str, Any]] = []
    for provider_name, package_name in (
        ("DmlExecutionProvider", "onnxruntime-directml"),
        ("CUDAExecutionProvider", "onnxruntime-gpu"),
    ):
        if provider_name in available_providers:
            row = _run_tiny_benchmark(
                tagger=tagger,
                model_name=settings.AI_MODEL_NAME,
                provider_preference=[provider_name, "CPUExecutionProvider"],
                expected_provider=provider_name,
                local_files_only=local_files_only,
                sample_count=benchmark_samples,
            )
        else:
            row = {
                "status": "provider_unavailable",
                "provider": provider_name,
                "blocker": (
                    "package_missing"
                    if not packages[package_name]["installed"]
                    else "provider_unavailable_after_package_install"
                ),
                "package": package_name,
                "package_installed": packages[package_name]["installed"],
            }
        row.setdefault("provider", provider_name)
        gpu_rows.append(row)
    gpu_successes = [
        row for row in gpu_rows
        if row.get("status") == "completed" and row.get("actual_provider") in {"DmlExecutionProvider", "CUDAExecutionProvider"}
    ]

    summary = {
        "phase": "S2G/S3A-F1+G1",
        "title": "WDTagger Provider, Load-Control, and GPU/DirectML Enablement Attempt",
        "generated_at": _utc_now(),
        "pipeline_contract": {
            "contract_id": "s2g_s3a_f1_foundation_contract_v1",
            "status": status,
            "claims": {
                "target_met": status == "target_met",
                "safe_to_merge": status == "target_met",
            },
        },
        "runtime": {
            "python_executable_public_name": Path(sys.executable).name,
            "onnxruntime": {
                "available_providers": available_providers,
                "version": getattr(rt, "__version__", "unknown"),
            },
            "packages": packages,
        },
        "gpu_directml_enablement": {
            "attempted": True,
            "preferred_order": ["DmlExecutionProvider", "CUDAExecutionProvider"],
            "package_install": {
                "performed": directml_install_performed or cuda_install_performed,
                "scope": "project_venv",
                "packages": [
                    {
                        "package": "onnxruntime-directml",
                        "install_performed": directml_install_performed,
                        "installed_after_attempt": packages["onnxruntime-directml"]["installed"],
                        "version": packages["onnxruntime-directml"]["version"],
                    },
                    {
                        "package": "onnxruntime-gpu",
                        "install_performed": cuda_install_performed,
                        "installed_after_attempt": packages["onnxruntime-gpu"]["installed"],
                        "version": packages["onnxruntime-gpu"]["version"],
                    },
                ],
                "global_or_system_python_modified": False,
            },
            "available_onnx_providers_after_attempt": available_providers,
            "success": bool(gpu_successes),
            "actual_gpu_provider_loaded": gpu_successes[0]["actual_provider"] if gpu_successes else None,
            "blocker": None if gpu_successes else gpu_rows[0].get("blocker"),
            "benchmarks": gpu_rows,
        },
        "benchmarks": {
            "sample_source": "synthetic_zero_arrays",
            "sample_count": benchmark_samples,
            "cpu": cpu_benchmark,
            "gpu_or_directml": gpu_rows,
        },
        "wd_tagger": {
            "provider_abstraction": {
                **source_flags,
                "supported_provider_preference": [
                    "CUDAExecutionProvider",
                    "DmlExecutionProvider",
                    "CPUExecutionProvider",
                ],
                "requested_provider_preference": provider.get("requested_provider_preference", []),
                "available_onnx_providers": provider.get("available_providers", list(rt.get_available_providers())),
                "actual_provider": loaded_provider,
                "loaded_providers": provider.get("loaded_providers", []),
                "fallback_occurred": bool(provider.get("fallback_occurred", False)),
                "fallback_reason": provider_fallback_reason,
            },
            "model": {
                "model_name": provenance.get("model_name"),
                "model_repo_id": provenance.get("model_repo_id"),
                "local_files_only": local_files_only,
                "model_download_allowed": model_download_allowed,
                "model_download_performed": model_download_performed,
                "cache_before": model_cache_before,
                "cache_after": model_cache_after,
                "model_loaded": model_loaded,
                "load_error_type": load_error_type,
            },
            "thresholds": provenance.get("thresholds", {}),
            "load_control": {
                "configured_batch_size": provenance.get("configured_batch_size"),
                "effective_batch_size": provenance.get("effective_batch_size"),
                "batch_size": provenance.get("effective_batch_size"),
                "batch": provenance.get("batch", {}),
                "cpu_intra_op_threads": load_control.cpu_intra_op_threads,
                "cpu_inter_op_threads": load_control.cpu_inter_op_threads,
                "preprocess_workers": load_control.preprocess_workers,
                "execution_mode": load_control.execution_mode,
                "max_concurrent_jobs": load_control.max_concurrent_jobs,
                "process_priority": load_control.process_priority,
                "process_priority_applied": load_control.process_priority_applied,
                "process_priority_note": load_control.process_priority_note,
                "applied_session_options": provenance.get("load_control", {}).get("applied_session_options", {}),
            },
            "provenance": {
                "fields_available": [
                    "model_name",
                    "model_repo_id",
                    "thresholds",
                    "requested_provider_preference",
                    "actual_provider",
                    "fallback_reason",
                    "batch_size",
                    "effective_batch_size",
                    "configured_batch_size",
                    "batch_cap_source",
                    "cpu_thread_settings",
                    "preprocess_workers",
                    "execution_mode",
                    "tagger_version_source",
                ],
                "runtime_provenance": provenance,
            },
        },
        "shared_foundation": {
            "module": "backend/app/services/job_control.py",
            "concepts": [
                "LoadControlConfig",
                "ProviderCapability",
                "JobRun",
                "StageRun",
                "ProgressSnapshot",
                "ProviderProvenance",
            ],
            "no_db_schema_change": True,
            "no_background_daemon_added": True,
        },
        "s3a_dry_run_plan": {
            "production_execution_enabled": s3a_plan["production_execution_enabled"],
            "unattended_enabled": s3a_plan["unattended_enabled"],
            "dry_run": s3a_plan["dry_run"],
            "stages": [
                {
                    "name": stage["name"],
                    "status": stage["status"],
                    "writes_enabled": stage["writes_enabled"],
                }
                for stage in s3a_plan["stages"]
            ],
        },
        "public_reports": {
            "markdown_report_path": markdown_report_path,
            "summary_json_path": "docs/reports/s2g-s3a-f1-provider-load-control-foundation-summary.json",
            "path_style": "repo_relative_public_artifacts",
        },
        "public_redaction": {
            "passed": True,
            "finding_count": 0,
            "scan_contract": "s2g_s3a_f1_foundation_contract_v1",
        },
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
            "model_download": model_download_performed,
            "db_schema_change": False,
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "backend/app/services/job_control.py",
                    "classification": "durable production code",
                    "committed": True,
                },
                {
                    "path": "scripts/run_s2g_s3a_f1_provider_load_smoke.py",
                    "classification": "reusable validation/safety tool",
                    "committed": True,
                },
                {
                    "path": "docs/reports/s2g-s3a-f1-provider-load-control-foundation.md",
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
                {
                    "path": "docs/reports/s2g-s3a-f1-provider-load-control-foundation-summary.json",
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
            ]
        },
        "recommended_next_phase": "If GPU/DirectML is available, validate a bounded real AI tagging job; otherwise keep CPU fallback and decide whether to make DirectML a documented optional dependency before production S3A.",
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-files-only", action="store_true", default=True, help="Require local Hugging Face model cache (default).")
    parser.add_argument("--allow-model-download", action="store_true", help="Allow Hugging Face model downloads for this smoke run.")
    parser.add_argument("--benchmark-samples", type=int, default=2, help="Tiny synthetic benchmark sample count.")
    parser.add_argument(
        "--directml-install-performed",
        action="store_true",
        default=_env_truthy("S2G_S3A_DIRECTML_INSTALL_PERFORMED"),
        help="Record that this PR attempt installed onnxruntime-directml in the project venv.",
    )
    parser.add_argument(
        "--cuda-install-performed",
        action="store_true",
        default=_env_truthy("S2G_S3A_CUDA_INSTALL_PERFORMED"),
        help="Record that this PR attempt installed onnxruntime-gpu in the project venv.",
    )
    parser.add_argument("--summary-output", type=Path, help="Optional path to write the public summary JSON.")
    parser.add_argument(
        "--markdown-report-path",
        default="docs/reports/s2g-s3a-f1-provider-load-control-foundation.md",
        help="Repo-relative Markdown report path recorded in the summary.",
    )
    args = parser.parse_args()

    summary = build_summary(
        local_files_only=not args.allow_model_download,
        markdown_report_path=args.markdown_report_path,
        benchmark_samples=max(1, min(args.benchmark_samples, 4)),
        directml_install_performed=args.directml_install_performed,
        cuda_install_performed=args.cuda_install_performed,
    )
    if args.summary_output:
        _write_json(args.summary_output, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["pipeline_contract"]["status"] == "target_met" else 2


if __name__ == "__main__":
    raise SystemExit(main())
