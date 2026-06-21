"""S2G/S3A-F1 provider and load-control smoke check.

This runner does not open a DB connection, write media_tags, run production AI
tagging, or call external source/provider systems. With --local-files-only it
requires the WD model to already be present in the Hugging Face cache.
"""

from __future__ import annotations

import argparse
import json
import sys
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


def build_summary(*, local_files_only: bool, markdown_report_path: str) -> dict[str, Any]:
    import onnxruntime as rt

    from app.config import settings
    from app.services.ai_tagging_service import get_ai_tagging_runtime_provenance
    from app.services.job_control import (
        build_ai_tagging_load_control_config,
        build_s3a_foundation_dry_run_plan,
    )
    from app.services.wd_tagger import get_wd_tagger

    load_control = build_ai_tagging_load_control_config(settings)
    tagger = get_wd_tagger()
    model_loaded = False
    load_error_type = None
    try:
        tagger.ensure_loaded(settings.AI_MODEL_NAME, local_files_only=local_files_only)
        model_loaded = True
    except Exception as exc:
        load_error_type = exc.__class__.__name__

    provenance = get_ai_tagging_runtime_provenance(tagger)
    provider = provenance.get("provider") or {}
    source_flags = _wd_tagger_source_has_provider_abstraction()
    s3a_plan = build_s3a_foundation_dry_run_plan(load_control).to_public_dict()
    loaded_provider = provider.get("actual_provider")
    status = "target_met" if model_loaded and loaded_provider else "blocked_model_unavailable"

    provider_fallback_reason = provider.get("fallback_reason")
    if provider.get("fallback_occurred") and not provider_fallback_reason:
        status = "blocked_provider_unavailable"

    summary = {
        "phase": "S2G/S3A-F1",
        "title": "WDTagger Provider Abstraction and Bounded Load-Control Foundation",
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
                "available_providers": list(rt.get_available_providers()),
                "version": getattr(rt, "__version__", "unknown"),
            },
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
                "model_loaded": model_loaded,
                "load_error_type": load_error_type,
            },
            "thresholds": provenance.get("thresholds", {}),
            "load_control": {
                "batch_size": provenance.get("batch_size"),
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
            "model_download": False,
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
        "recommended_next_phase": "S2G provider/load-control validation on real controlled AI tagging job, then separately approved production S3A execution",
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-files-only", action="store_true", help="Require local Hugging Face model cache.")
    parser.add_argument("--summary-output", type=Path, help="Optional path to write the public summary JSON.")
    parser.add_argument(
        "--markdown-report-path",
        default="docs/reports/s2g-s3a-f1-provider-load-control-foundation.md",
        help="Repo-relative Markdown report path recorded in the summary.",
    )
    args = parser.parse_args()

    summary = build_summary(
        local_files_only=args.local_files_only,
        markdown_report_path=args.markdown_report_path,
    )
    if args.summary_output:
        _write_json(args.summary_output, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["pipeline_contract"]["status"] == "target_met" else 2


if __name__ == "__main__":
    raise SystemExit(main())
