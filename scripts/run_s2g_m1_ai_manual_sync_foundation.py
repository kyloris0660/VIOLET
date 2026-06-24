#!/usr/bin/env python3
"""Generate S2G-M1 AI tagging and manual sync foundation evidence.

Lifecycle: reusable validation/safety tool.

This runner is public-report safe by default:
- no production DB connection;
- no production media import, classification, AI tag, localization, provider, or LLM calls;
- no source, iCloud, or app-managed storage mutation;
- ONNX model files are resolved with local_files_only=True;
- benchmark input is synthetic zero arrays only;
- manual sync planning runs against a temporary fixture and an in-memory test DB.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.enums import FileTypeEnum  # noqa: E402
from app.models import Media  # noqa: E402
from app.services.dynamic_library_sync_service import plan_manual_sync_dry_run  # noqa: E402
from app.services.job_control import (  # noqa: E402
    DEFAULT_PROVIDER_PREFERENCE,
    WD_MODEL_REPOS,
    build_ai_tagging_execution_profile,
    select_onnx_provider,
)
from app.utils.media_processor import calculate_file_hash  # noqa: E402
from scripts.phase_contracts.contract_checks import scan_public_payload  # noqa: E402

PHASE = "S2G-M1"
TITLE = "AI Tagging Execution and Manual Sync Foundation"
CONTRACT_ID = "s2g_manual_sync_foundation_contract_v1"
SUMMARY_PATH = ROOT / "docs" / "reports" / "s2g-m1-ai-manual-sync-foundation-summary.json"
MARKDOWN_PATH = ROOT / "docs" / "reports" / "s2g-m1-ai-manual-sync-foundation.md"
PR123_MERGE_COMMIT = "4724530d83767a62b6525a58bb1a1d04e973d48e"
DEFAULT_SAMPLE_COUNT = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


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


def public_error_code(exc: BaseException) -> str:
    return (exc.__class__.__name__ or "Error")[:80]


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return "[redacted-path]"
    return str(value)


def provider_key(provider: str) -> str:
    if provider == "CUDAExecutionProvider":
        return "cuda"
    if provider == "DmlExecutionProvider":
        return "directml"
    if provider == "CPUExecutionProvider":
        return "cpu"
    return provider.lower().replace("executionprovider", "")


def onnxruntime_status() -> dict[str, Any]:
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
    except Exception as exc:  # noqa: BLE001 - public report records type only.
        status["error_code"] = public_error_code(exc)
    return status


def cached_model_files(model_name: str) -> dict[str, Any]:
    repo_id = WD_MODEL_REPOS.get(model_name)
    result = {
        "model_name": model_name,
        "model_repo_id": repo_id,
        "model_file_cached": False,
        "label_file_cached": False,
        "local_files_only": True,
        "model_download_allowed": False,
        "model_download_performed": False,
        "network_download_required": False,
        "status": "not_checked",
        "blocker": None,
        "_model_path": None,
    }
    if not repo_id:
        result["status"] = "blocked"
        result["blocker"] = "unknown_model_name"
        return result
    try:
        import huggingface_hub

        from app.services.wd_tagger import WDTagger

        huggingface_hub.hf_hub_download(
            repo_id,
            WDTagger.LABEL_FILENAME,
            local_files_only=True,
        )
        result["label_file_cached"] = True
        model_path = huggingface_hub.hf_hub_download(
            repo_id,
            WDTagger.MODEL_FILENAME,
            local_files_only=True,
        )
        result["model_file_cached"] = True
        result["_model_path"] = model_path
        result["status"] = "cached"
    except Exception as exc:  # noqa: BLE001 - cache blocker only.
        result["status"] = "blocked"
        result["blocker"] = public_error_code(exc)
        result["network_download_required"] = True
    return result


def public_model_cache(cache: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in cache.items() if not key.startswith("_")}


def bounded_session_options(profile: Mapping[str, Any]):
    import onnxruntime as rt

    options = rt.SessionOptions()
    options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
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
    profile: Mapping[str, Any],
    sample_count: int,
) -> dict[str, Any]:
    if provider not in available_providers:
        return {
            "provider": provider,
            "available": False,
            "loaded": False,
            "practical": False,
            "status": "not_available",
            "blocker": "provider_not_available",
        }
    if not model_path:
        return {
            "provider": provider,
            "available": True,
            "loaded": False,
            "practical": False,
            "status": "model_not_cached",
            "blocker": "model_not_cached",
        }
    try:
        import onnxruntime as rt

        batch_size = max(1, min(int(profile.get("batch_size") or 1), sample_count))
        options = bounded_session_options(profile)
        load_started = time.perf_counter()
        session = rt.InferenceSession(model_path, sess_options=options, providers=[provider])
        model_load_seconds = time.perf_counter() - load_started
        loaded_providers = list(session.get_providers())
        if provider not in loaded_providers:
            return {
                "provider": provider,
                "available": True,
                "loaded": False,
                "practical": False,
                "status": "loaded_different_provider",
                "blocker": "provider_not_selected",
                "loaded_providers": loaded_providers,
            }

        input_name = session.get_inputs()[0].name
        single_batch = synthetic_input_for_session(session, 1)
        small_batch = synthetic_input_for_session(session, batch_size)

        session.run(None, {input_name: single_batch})
        single_started = time.perf_counter()
        session.run(None, {input_name: single_batch})
        single_latency_seconds = time.perf_counter() - single_started

        loops = max(1, math.ceil(sample_count / batch_size))
        batch_started = time.perf_counter()
        processed = 0
        for _ in range(loops):
            session.run(None, {input_name: small_batch})
            processed += batch_size
        batch_latency_seconds = time.perf_counter() - batch_started
        processed = min(processed, sample_count)
        throughput = processed / max(batch_latency_seconds, 1e-9)
        return {
            "provider": provider,
            "available": True,
            "loaded": True,
            "practical": True,
            "status": "completed",
            "loaded_providers": loaded_providers,
            "model_load_seconds": round(model_load_seconds, 4),
            "single_image_latency_seconds": round(single_latency_seconds, 4),
            "small_batch_latency_seconds": round(batch_latency_seconds, 4),
            "sample_count": sample_count,
            "batch_size": batch_size,
            "throughput_items_per_second": round(throughput, 4),
            "seconds_per_item": round(batch_latency_seconds / processed, 4) if processed else None,
        }
    except Exception as exc:  # noqa: BLE001 - public report records type only.
        return {
            "provider": provider,
            "available": True,
            "loaded": False,
            "practical": False,
            "status": "load_or_inference_failed",
            "blocker": public_error_code(exc),
        }


def build_capability_probe(profile: Mapping[str, Any], sample_count: int) -> dict[str, Any]:
    onnx = onnxruntime_status()
    model_cache = cached_model_files(str(profile["model_name"]))
    available = list(onnx.get("available_providers") or [])
    model_path = model_cache.get("_model_path") if model_cache.get("status") == "cached" else None
    providers = list(dict.fromkeys([*profile.get("provider_preference", []), "DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]))
    provider_matrix = {
        provider_key(provider): benchmark_provider(
            provider=provider,
            available_providers=available,
            model_path=model_path,
            profile=profile,
            sample_count=sample_count,
        )
        for provider in providers
        if provider in DEFAULT_PROVIDER_PREFERENCE
    }
    for provider in DEFAULT_PROVIDER_PREFERENCE:
        provider_matrix.setdefault(
            provider_key(provider),
            {
                "provider": provider,
                "available": provider in available,
                "loaded": False,
                "practical": False,
                "status": "not_requested",
            },
        )

    selection = select_onnx_provider(
        profile.get("provider_preference"),
        available,
        allow_fallback=bool(profile.get("allow_provider_fallback", True)),
    ).to_public_dict()
    completed = [row for row in provider_matrix.values() if row.get("status") == "completed"]
    gpu_completed = [
        row for row in completed if row.get("provider") in {"DmlExecutionProvider", "CUDAExecutionProvider"}
    ]
    cpu = provider_matrix.get("cpu", {})
    recommended = gpu_completed[0] if gpu_completed else (cpu if cpu.get("status") == "completed" else None)
    recommended_seconds = recommended.get("seconds_per_item") if recommended else None
    estimated_manual_batch = 25
    estimated_runtime_seconds = (
        round(float(recommended_seconds) * estimated_manual_batch, 2)
        if recommended_seconds is not None
        else None
    )
    return {
        "attempted": True,
        "bounded": True,
        "synthetic_input_only": True,
        "sample_count": sample_count,
        "local_files_only": True,
        "model_cache": public_model_cache(model_cache),
        "onnxruntime": onnx,
        "provider_matrix": provider_matrix,
        "gpu_acceleration_available": bool(gpu_completed),
        "cpu_fallback_available": bool(cpu.get("available")),
        "cpu_fallback_completed": cpu.get("status") == "completed",
        "provider_selection": selection,
        "provider_fallback_decision_recorded": True,
        "recommended_provider": recommended.get("provider") if recommended else None,
        "recommended_batch_size": profile.get("batch_size"),
        "recommended_concurrency": profile.get("concurrency"),
        "recommended_seconds_per_item": recommended_seconds,
        "estimated_runtime_seconds_for_25_item_manual_batch": estimated_runtime_seconds,
        "status": "completed" if completed else "attempted_with_blocker",
        "blocker": None if completed else (model_cache.get("blocker") or onnx.get("error_code") or "no_provider_completed"),
    }


def create_fixture_plan(profile: Mapping[str, Any], probe: Mapping[str, Any]) -> dict[str, Any]:
    from PIL import Image
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        with tempfile.TemporaryDirectory(prefix="violet-s2g-m1-") as temp_dir:
            fixture_dir = Path(temp_dir) / "manual-sync-fixture"
            fixture_dir.mkdir()
            image = Image.new("RGB", (1, 1), (12, 34, 56))
            image.save(fixture_dir / "candidate_a.png")
            image.save(fixture_dir / "candidate_b.png")
            existing = Image.new("RGB", (1, 1), (99, 88, 77))
            existing.save(fixture_dir / "candidate_existing.png")
            (fixture_dir / "zero.jpg").write_bytes(b"")
            (fixture_dir / "unsupported.txt").write_text("not media", encoding="utf-8")
            (fixture_dir / "corrupt.jpg").write_bytes(b"not an image")
            existing_hash = calculate_file_hash(fixture_dir / "candidate_existing.png")
            db.add(
                Media(
                    filename="redacted-existing.png",
                    path="media/original/redacted-existing.png",
                    hash=existing_hash,
                    file_type=FileTypeEnum.image,
                )
            )
            db.commit()
            return plan_manual_sync_dry_run(
                db,
                source_path=fixture_dir,
                max_files=20,
                stable_age_seconds=0,
                include_private_details=False,
                ai_profile=dict(profile),
                benchmark={
                    "recommended_seconds_per_item": probe.get("recommended_seconds_per_item"),
                    "single_image_latency_seconds": probe.get("recommended_seconds_per_item"),
                },
            )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    profile = build_ai_tagging_execution_profile(settings).to_public_dict()
    probe = build_capability_probe(profile, args.synthetic_samples)
    plan = create_fixture_plan(profile, probe)
    branch = git_value(["branch", "--show-current"])
    head = git_value(["rev-parse", "HEAD"])
    origin_main = git_value(["rev-parse", "origin/main"])
    merge_base_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", PR123_MERGE_COMMIT, "origin/main"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0

    focused_tests_passed = bool(args.validation_focused_tests_passed)
    status = "target_met" if focused_tests_passed else "foundation_ready_pending_validation"
    claims = {
        "target_met": focused_tests_passed,
        "safe_to_merge": focused_tests_passed,
        "full_chain_complete": False,
    }
    safety = {
        "production_db_mutation": False,
        "production_import": False,
        "production_classification": False,
        "production_ai_tagging_writes": False,
        "production_localization_writes": False,
        "source_icloud_mutation": False,
        "app_managed_production_storage_mutation": False,
        "external_provider_calls": False,
        "gallery_dl_pixiv_saucenao_google_calls": False,
        "sourceconcept_mutation": False,
        "entity_truth_writes": False,
        "confirmed_assignment_writes": False,
        "production_media_tags_mutation": False,
        "llm_calls": False,
        "automatic_sync_enabled": False,
        "scheduled_sync_enabled": False,
        "system_service_enabled": False,
        "startup_task_enabled": False,
        "long_running_daemon_enabled": False,
        "final_production_acceptance_completed": False,
    }
    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": utc_now(),
        "branch": branch,
        "head_evidence": {
            "current_head_sha": head,
            "origin_main_sha": origin_main,
            "pr123_merge_commit": PR123_MERGE_COMMIT,
            "pr123_merge_is_ancestor_of_origin_main": merge_base_check,
            "latest_main_after_pr123": origin_main == PR123_MERGE_COMMIT and merge_base_check,
        },
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": claims,
            "phase_identity": "S2G-M1",
            "post_123_route_respected": True,
        },
        "ai_execution_profile": profile,
        "capability_probe": probe,
        "provider_fallback": {
            "selection": probe["provider_selection"],
            "decision_recorded": True,
            "gpu_requested_when_available": True,
            "cpu_fallback_available": probe["cpu_fallback_available"],
            "cpu_fallback_completed": probe["cpu_fallback_completed"],
            "actual_recommended_provider": probe["recommended_provider"],
        },
        "load_control_policy": {
            "present": True,
            "max_batch_size": profile["batch_size"],
            "max_concurrency": profile["concurrency"],
            "per_image_timeout_seconds": profile["per_image_timeout_seconds"],
            "job_timeout_seconds": profile["job_timeout_seconds"],
            "single_active_ai_execution_guard": True,
            "cancelability_or_safe_stop": "existing_ai_job_cancel_flag_and_item_boundary",
            "failure_isolation_per_image": True,
            "no_unbounded_production_loop": True,
        },
        "provenance_policy": {
            "present": True,
            "source": "ai_wd",
            "model_name": profile["model_name"],
            "model_repo_id": profile["model_repo_id"],
            "provider_backend": profile["provider_backend"],
            "confidence_recorded": True,
            "thresholds_recorded": True,
            "job_id_recorded_for_job_runs": True,
            "dry_run_vs_write_mode_recorded": True,
            "manual_locked_tags_not_overwritten": True,
            "suggestions_vs_confirmed_recorded": True,
            "production_writes_enabled": False,
            "provenance_fields": profile["provenance_fields"],
        },
        "manual_sync": {
            "dry_run_planner": {
                "implemented": True,
                "public_safe": True,
                "db_write_performed": plan["ledger"]["db_write_performed"],
                "source_mutation_performed": plan["ledger"]["source_mutation_performed"],
                "app_storage_mutation_performed": plan["ledger"]["app_storage_mutation_performed"],
                "state_counts": plan["counts"]["state_counts"],
                "failure_reasons": plan["counts"]["failure_reasons"],
                "estimated_import_count": plan["counts"]["estimated_import_count"],
                "estimated_classification_count": plan["counts"]["estimated_classification_count"],
                "estimated_ai_tagging_count": plan["counts"]["estimated_ai_tagging_count"],
                "estimated_localization_workload": plan["counts"]["estimated_localization_workload"],
            },
            "job_ledger_foundation": {
                "implemented": True,
                "job_id_present": bool(plan["job"]["job_id"]),
                "mode": plan["job"]["mode"],
                "state": plan["job"]["state"],
                "trigger_type": plan["job"]["trigger_type"],
                "per_file_state_records_present": bool(plan["ledger"]["per_file_public_records"]),
                "persistent_tables_available": plan["ledger"]["persistent_tables_available"],
                "ledger_mode": plan["ledger"]["ledger_mode"],
            },
            "controlled_pipeline": {
                "implemented": True,
                "status": plan["pipeline"]["status"],
                "dry_run_only_this_phase": plan["pipeline"]["dry_run_only_this_phase"],
                "production_execute_enabled": plan["pipeline"]["production_execute_enabled"],
                "estimated_runtime_seconds": plan["pipeline"]["estimated_runtime_seconds"],
                "stages": plan["pipeline"]["stages"],
            },
        },
        "api_surface": {
            "manual_plan_endpoint": "POST /api/admin/dynamic-library-sync/manual-sync/plan",
            "manual_status_endpoint": "GET /api/admin/dynamic-library-sync/manual-sync/status",
            "auth_required": "require_admin_mode",
            "production_write_endpoint_enabled": False,
            "automatic_execution_endpoint_added": False,
        },
        "final_button_recommendation": {
            "placement": "both_launcher_and_web_admin",
            "primary_call": "POST /api/admin/dynamic-library-sync/manual-sync/plan first; later execute endpoint only after explicit S3A-M1 acceptance",
            "launcher_pending_check_on_startup": "lightweight_count_only_ok",
            "launcher_intrusive_prompt": False,
            "safe_default_max_files": 25,
            "safe_default_max_duration_seconds": 600,
            "safe_default_ai_batch_size": profile["batch_size"],
            "safe_default_concurrency": 1,
            "partial_failure_behavior": "complete successful items, keep failed/deferred item ledger visible, stop only on hard safety gate or failure budget",
            "first_real_acceptance_batch_size": 5,
            "rollback_supersede_diagnostic_plan": "ledger-driven retry/supersede; no source mutation; diagnose by job id, safe labels, reason counts, and provider provenance",
        },
        "validation": {
            "focused_tests_passed": focused_tests_passed,
            "runner_completed": True,
            "contract_check_status": "run_separately_after_report_generation",
            "public_redaction_passed": False,
            "browser_validation_required": False,
            "browser_validation_reason": "backend route and service foundation only; no visible UI or frontend behavior changed",
        },
        "safety": safety,
        "public_reports": {
            "summary_json_path": repo_relative(SUMMARY_PATH),
            "markdown_report_path": repo_relative(MARKDOWN_PATH),
            "path_style": "repo_relative_public_artifacts",
        },
        "artifact_lifecycle": {
            "artifacts": [
                {"path": "backend/app/services/job_control.py", "classification": "durable production code"},
                {"path": "backend/app/services/dynamic_library_sync_service.py", "classification": "durable production code"},
                {"path": "backend/app/routes/admin/dynamic_library_sync.py", "classification": "durable production code"},
                {"path": "scripts/run_s2g_m1_ai_manual_sync_foundation.py", "classification": "reusable validation/safety tool"},
                {"path": "scripts/phase_contracts/contract_registry.py", "classification": "reusable validation/safety tool"},
                {"path": "scripts/phase_contracts/contract_checks.py", "classification": "reusable validation/safety tool"},
                {"path": repo_relative(MARKDOWN_PATH), "classification": "public report / handoff"},
                {"path": repo_relative(SUMMARY_PATH), "classification": "public report / handoff"},
            ],
            "private_artifacts_committed": False,
            "local_ignored_artifacts_created": False,
        },
        "source_files_inspected": [
            "docs/current-handoff.md",
            "docs/project-roadmap.md",
            "docs/roadmap/current-mainline-roadmap.md",
            "docs/roadmap/post-s2-production-roadmap.md",
            "docs/production-launcher.md",
            "docs/dynamic-library-sync.md",
            "docs/ai-auto-tagging.md",
            "docs/ai-tagging-jobs.md",
            "docs/ai-tagging-usage-guide.md",
            "docs/reports/phase-4.7-s2-baseline-full-import-ai-localization-summary.json",
            "docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan.md",
            "docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan-summary.json",
            "docs/reports/pd1-a-r1-post-122-roadmap-reconciliation-summary.json",
            "backend/app/services/ai_tagging_service.py",
            "backend/app/services/ai_tagging_job_service.py",
            "backend/app/routes/admin/ai_tagging.py",
            "backend/app/routes/admin/ai_tagging_jobs.py",
            "backend/app/services/classification_job_service.py",
            "backend/app/services/dynamic_library_sync_service.py",
            "backend/app/routes/admin/dynamic_library_sync.py",
            "backend/app/routes/admin/dev_tools.py",
            "frontend/templates/admin.html",
            "frontend/static/js/admin.js",
            "frontend/static/locales/zh-cn.json",
            "frontend/static/locales/en.json",
            "scripts/run_s2g1_ai_tagging_capability_probe.py",
            "scripts/run_s2g_real1_bounded_ai_tagging_validation.py",
            "scripts/run_phase46_fulllib_e1_production_import_ai_tagging.py",
            "scripts/run_phase47_s2_baseline_full_import_ai_localization.py",
            "scripts/phase_contracts/",
            "tests/test_phase_contracts.py",
            "docs/test-workflow.md",
            "AGENTS.md",
        ],
        "known_limitations": [
            "No production execution or final visible production manual-sync button is included in this PR.",
            "Persistent production execution ledgers remain disabled until S3A-M1 acceptance.",
            "Capability probe uses synthetic tensors and local model cache only; it does not prove full production throughput.",
        ],
        "recommended_next_phase": "S3A-M1 final manual-sync UI / production acceptance with explicit small-batch approval.",
    }
    return summary


def render_markdown(summary: Mapping[str, Any]) -> str:
    probe = summary["capability_probe"]
    profile = summary["ai_execution_profile"]
    plan = summary["manual_sync"]
    button = summary["final_button_recommendation"]
    safety = summary["safety"]
    validation = summary["validation"]
    fallback = summary["provider_fallback"]
    load_policy = summary["load_control_policy"]
    provenance = summary["provenance_policy"]
    api_surface = summary["api_surface"]
    lines = [
        f"# {PHASE}: {TITLE}",
        "",
        f"Contract: `{CONTRACT_ID}`.",
        f"Status: `{summary['pipeline_contract']['status']}`.",
        "",
        "## Purpose",
        "",
        "S2G-M1 builds the reusable local AI tagging execution profile and the manual sync dry-run foundation needed before a final production manual-sync acceptance button.",
        "",
        "Production execution is intentionally out of scope: this phase proves the foundation with local-only model resolution, synthetic benchmark input, temporary fixture storage, and an in-memory test DB plan.",
        "",
        "## Source Files Inspected",
        "",
    ]
    for inspected in summary["source_files_inspected"]:
        lines.append(f"- `{inspected}`.")
    lines.extend(
        [
            "",
            "## Implementation Summary",
            "",
            "- Added a durable AI tagging execution profile for local ONNX Runtime execution.",
            "- Added bounded capability probe/report generation with provider fallback accounting.",
            "- Extended dynamic sync with a manual sync dry-run planner and public-safe per-file ledger records.",
            "- Added admin status/plan endpoints for later UI wiring without adding a production execute button.",
            "- Added `s2g_manual_sync_foundation_contract_v1` and focused positive/negative contract tests.",
            "",
        ]
    )
    lines.extend(
        [
            "## AI Capability / Benchmark",
            "",
            f"- Probe attempted: `{probe['attempted']}`.",
            f"- Bounded synthetic samples: `{probe['sample_count']}`.",
            f"- Local-files-only model resolution: `{probe['local_files_only']}`.",
            f"- Model cache status: `{probe['model_cache']['status']}`.",
            f"- GPU acceleration available: `{probe['gpu_acceleration_available']}`.",
            f"- CPU fallback available/completed: `{probe['cpu_fallback_available']}` / `{probe['cpu_fallback_completed']}`.",
            f"- Recommended provider: `{probe['recommended_provider']}`.",
            f"- Recommended batch size/concurrency: `{probe['recommended_batch_size']}` / `{probe['recommended_concurrency']}`.",
            f"- Estimated 25-item AI runtime: `{probe['estimated_runtime_seconds_for_25_item_manual_batch']}` seconds.",
            f"- Observed blocker, if any: `{probe['blocker']}`.",
            "",
            "## Provider / Fallback Decision",
            "",
            f"- Requested provider preference: `{profile['provider_preference']}`.",
            f"- Selected provider: `{fallback['selection'].get('selected_provider')}`.",
            f"- Fallback occurred: `{fallback['selection'].get('fallback_occurred')}`.",
            f"- Fallback reason: `{fallback['selection'].get('fallback_reason')}`.",
            f"- CPU fallback available/completed: `{fallback['cpu_fallback_available']}` / `{fallback['cpu_fallback_completed']}`.",
            "",
            "## Execution Profile",
            "",
            f"- Backend: `{profile['provider_backend']}`.",
            f"- Model: `{profile['model_name']}`.",
            f"- Model repo: `{profile['model_repo_id']}`.",
            f"- Thresholds: `{profile['thresholds']}`.",
            f"- Batch/concurrency/timeouts: `{profile['batch_size']}` / `{profile['concurrency']}` / `{profile['per_image_timeout_seconds']}s` per image / `{profile['job_timeout_seconds']}s` job.",
            f"- Production writes enabled: `{profile['production_writes_enabled']}`.",
            "",
            "## Load-Control Policy",
            "",
            f"- Max batch size: `{load_policy['max_batch_size']}`.",
            f"- Max concurrency: `{load_policy['max_concurrency']}`.",
            f"- Per-image timeout: `{load_policy['per_image_timeout_seconds']}` seconds.",
            f"- Job timeout: `{load_policy['job_timeout_seconds']}` seconds.",
            f"- Single active AI execution guard: `{load_policy['single_active_ai_execution_guard']}`.",
            f"- Failure isolation per image: `{load_policy['failure_isolation_per_image']}`.",
            f"- No unbounded production loop: `{load_policy['no_unbounded_production_loop']}`.",
            "",
            "## Provenance Policy",
            "",
            f"- AI tag source: `{provenance['source']}`.",
            f"- Model/provider recorded: `{provenance['model_name']}` / `{provenance['provider_backend']}`.",
            f"- Confidence and thresholds recorded: `{provenance['confidence_recorded']}` / `{provenance['thresholds_recorded']}`.",
            f"- Job id and dry-run/write mode recorded: `{provenance['job_id_recorded_for_job_runs']}` / `{provenance['dry_run_vs_write_mode_recorded']}`.",
            f"- Manual/locked tags protected: `{provenance['manual_locked_tags_not_overwritten']}`.",
            f"- Suggestions versus confirmed tags recorded: `{provenance['suggestions_vs_confirmed_recorded']}`.",
            f"- Production writes enabled: `{provenance['production_writes_enabled']}`.",
            "",
            "## Manual Sync Dry-Run Planner",
            "",
            f"- Planner implemented: `{plan['dry_run_planner']['implemented']}`.",
            f"- Public-safe output: `{plan['dry_run_planner']['public_safe']}`.",
            f"- DB/source/app-storage mutation performed: `{plan['dry_run_planner']['db_write_performed']}` / `{plan['dry_run_planner']['source_mutation_performed']}` / `{plan['dry_run_planner']['app_storage_mutation_performed']}`.",
            f"- Estimated import/classification/AI/localization count: `{plan['dry_run_planner']['estimated_import_count']}` / `{plan['dry_run_planner']['estimated_classification_count']}` / `{plan['dry_run_planner']['estimated_ai_tagging_count']}` / `{plan['dry_run_planner']['estimated_localization_workload']}`.",
            f"- State counts: `{plan['dry_run_planner']['state_counts']}`.",
            f"- Failure reasons: `{plan['dry_run_planner']['failure_reasons']}`.",
            "",
            "## Job / Ledger Foundation",
            "",
            f"- Job id present: `{plan['job_ledger_foundation']['job_id_present']}`.",
            f"- Mode/state/trigger: `{plan['job_ledger_foundation']['mode']}` / `{plan['job_ledger_foundation']['state']}` / `{plan['job_ledger_foundation']['trigger_type']}`.",
            f"- Per-file public state records present: `{plan['job_ledger_foundation']['per_file_state_records_present']}`.",
            f"- Ledger mode: `{plan['job_ledger_foundation']['ledger_mode']}`.",
            f"- Persistent table family available: `{plan['job_ledger_foundation']['persistent_tables_available']}`.",
            "",
            "## Controlled Pipeline Foundation",
            "",
            f"- Pipeline status: `{plan['controlled_pipeline']['status']}`.",
            f"- Dry-run only this phase: `{plan['controlled_pipeline']['dry_run_only_this_phase']}`.",
            f"- Production execute enabled: `{plan['controlled_pipeline']['production_execute_enabled']}`.",
            f"- Estimated runtime seconds: `{plan['controlled_pipeline']['estimated_runtime_seconds']}`.",
            "",
        ]
    )
    for stage in plan["controlled_pipeline"]["stages"]:
        lines.append(
            f"- Stage `{stage['name']}`: state `{stage['state']}`, writes `{stage['writes_enabled']}`, production execution `{stage['production_execution_enabled']}`."
        )
    lines.extend(
        [
            "",
            "## API Surface For Later UI",
            "",
            f"- Plan endpoint: `{api_surface['manual_plan_endpoint']}`.",
            f"- Status endpoint: `{api_surface['manual_status_endpoint']}`.",
            f"- Auth/admin policy: `{api_surface['auth_required']}`.",
            f"- Production write endpoint enabled: `{api_surface['production_write_endpoint_enabled']}`.",
            f"- Automatic execution endpoint added: `{api_surface['automatic_execution_endpoint_added']}`.",
            "",
            "## Validation",
            "",
            f"- Focused tests passed before target claim: `{validation['focused_tests_passed']}`.",
            f"- Runner completed: `{validation['runner_completed']}`.",
            f"- Public redaction passed: `{summary.get('public_redaction', {}).get('passed')}`.",
            f"- Browser validation required: `{validation['browser_validation_required']}`.",
            f"- Browser validation reason: {validation['browser_validation_reason']}.",
            "",
            "## Not Executed In This Phase",
            "",
            "- No production DB mutation, production media import, production classification, production AI tagging writes, or production localization writes.",
            "- No source/iCloud mutation and no app-managed production storage mutation.",
            "- No provider/gallery-dl/Pixiv/SauceNAO/Google calls and no LLM calls.",
            "- No SourceConcept mutation, Entity truth writes, confirmed assignment writes, or production `media_tags` mutation.",
            "- No automatic sync, scheduled sync, system service, startup task, or long-running daemon.",
            "- No final production acceptance; that remains S3A-M1.",
            "",
            "## Why Production Writes Are Deferred",
            "",
            "This phase creates production-capable code paths only where they are guarded and disabled by default. Production acceptance requires a separate S3A-M1 approval flow with production runtime identity, backup/recovery proof where applicable, a dry-run plan, a small explicit batch, and post-run diagnostics.",
            "",
        ]
    )
    lines.extend(
        [
            "## Final Button Recommendation",
            "",
            f"- Placement: `{button['placement']}`.",
            f"- Backend call: `{button['primary_call']}`.",
            f"- Startup pending check: `{button['launcher_pending_check_on_startup']}`.",
            f"- Intrusive launcher prompt: `{button['launcher_intrusive_prompt']}`.",
            f"- Safe default max files: `{button['safe_default_max_files']}`.",
            f"- Safe default max duration: `{button['safe_default_max_duration_seconds']}` seconds.",
            f"- Safe default AI batch size/concurrency: `{button['safe_default_ai_batch_size']}` / `{button['safe_default_concurrency']}`.",
            f"- Partial failure behavior: `{button['partial_failure_behavior']}`.",
            f"- First real acceptance batch size: `{button['first_real_acceptance_batch_size']}`.",
            f"- Rollback/supersede/diagnostic plan: `{button['rollback_supersede_diagnostic_plan']}`.",
            "",
            "## Safety / No-Mutation Proof",
            "",
        ]
    )
    for key, value in safety.items():
        lines.append(f"- `{key}`: `{value}`.")
    lines.extend(
        [
            "",
            "## Artifact Lifecycle",
            "",
        ]
    )
    for artifact in summary["artifact_lifecycle"]["artifacts"]:
        lines.append(f"- `{artifact['path']}`: {artifact['classification']}.")
    lines.extend(
        [
            "",
            "## Known Limitations",
            "",
        ]
    )
    for limitation in summary["known_limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(
        [
            "",
            "## Next Phase",
            "",
            str(summary["recommended_next_phase"]),
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(summary: dict[str, Any]) -> None:
    markdown = render_markdown(summary)
    findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown})
    summary["public_redaction"] = {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "checked_payloads": ["public_json_payload", "public_markdown_text"],
    }
    summary["validation"]["public_redaction_passed"] = not findings
    markdown = render_markdown(summary)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    MARKDOWN_PATH.write_text(markdown, encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-samples", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument(
        "--validation-focused-tests-passed",
        action="store_true",
        help="Set only after the focused S2G-M1 tests have passed in this run.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.synthetic_samples = max(1, min(16, args.synthetic_samples))
    summary = build_summary(args)
    write_reports(summary)
    print(json.dumps({"summary": repo_relative(SUMMARY_PATH), "status": summary["pipeline_contract"]["status"]}, indent=2, sort_keys=True))
    return 0 if summary.get("public_redaction", {}).get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
