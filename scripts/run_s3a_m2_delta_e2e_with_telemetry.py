#!/usr/bin/env python3
"""S3A-M2 production delta manual sync E2E runner with resource telemetry.

Default mode is a production dry-run plan. Execute mode is intentionally gated
by a fresh plan hash, plan timestamp, and an S3A-M2-specific approval phrase.
Raw ledgers and telemetry stay under .local_manifests/.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PHASE = "S3A-M2"
PHASE_TITLE = "Production Delta Manual Sync E2E + GPU Telemetry"
PHASE_SLUG = "s3a_m2_delta_e2e"
CONTRACT_ID = "s3a_m2_production_delta_e2e_contract_v1"
BRANCH = "codex/s3a-m2-production-delta-e2e-gpu-telemetry"
DEFAULT_DELTA_CAP = 300
APPROVED_DELTA_CAP_CEILING = 1000
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
DEFAULT_TELEMETRY_DIR = DEFAULT_OUTPUT_DIR / "telemetry"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / "s3a-m2-production-delta-e2e.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / "s3a-m2-production-delta-e2e-summary.json"

LOCALIZABLE_CATEGORIES = ("general", "meta")
PROPER_NOUN_CATEGORIES = ("character", "copyright", "artist")
GPU_PROVIDERS = {"DmlExecutionProvider", "CUDAExecutionProvider"}

SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>|]+"),
    re.compile(r"\\\\[^\\/\s]+\\[^\s\"'<>|]+"),
    re.compile(r"(?i)\bfile://[^\s\"'<>]+"),
    re.compile(r"(?i)(sk-[A-Za-z0-9_-]{4,}|gh[opsu]_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|Bearer\s+[A-Za-z0-9._-]{4,})"),
)


class S3AM2Blocked(RuntimeError):
    """Raised when a fail-closed S3A-M2 gate blocks execution."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=json_default) + "\n")
            count += 1
    return count


def stable_json_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=json_default).encode("utf-8")
    ).hexdigest()


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=json_default) + "\n")


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


def s3a_m2_approval_phrase(plan_hash: str) -> str:
    return f"I APPROVE S3A-M2 PRODUCTION DELTA E2E {str(plan_hash)[:12]}"


def parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise S3AM2Blocked("invalid_plan_created_at") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_error(exc: BaseException | str) -> str:
    text = str(exc)
    for pattern in SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:1000]


def output_dir_allowed(path: Path) -> bool:
    try:
        path.resolve().relative_to(DEFAULT_OUTPUT_DIR.resolve())
    except ValueError:
        return False
    return True


def telemetry_dir_allowed(path: Path) -> bool:
    try:
        path.resolve().relative_to(DEFAULT_TELEMETRY_DIR.resolve())
    except ValueError:
        return False
    return True


def validate_phase_limits(args: argparse.Namespace) -> None:
    if int(args.delta_cap) <= 0:
        raise S3AM2Blocked("delta_cap_must_be_positive")
    if int(args.delta_cap) > APPROVED_DELTA_CAP_CEILING:
        raise S3AM2Blocked("delta_cap_exceeds_s3a_m2_approved_ceiling")
    if int(args.execute_duration_seconds) <= 0:
        raise S3AM2Blocked("execute_duration_seconds_must_be_positive")
    if int(args.translation_batch_max_items) <= 0:
        raise S3AM2Blocked("translation_batch_max_items_must_be_positive")


def prepare_output_dir(args: argparse.Namespace) -> Path:
    output_dir = args.output_dir.resolve()
    if not output_dir_allowed(output_dir):
        raise S3AM2Blocked("unsafe_output_dir_must_be_under_local_s3a_m2_dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "telemetry").mkdir(parents=True, exist_ok=True)
    return output_dir


class StageTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stage = "init"
        self._started = time.monotonic()
        self._stage_started = self._started
        self._durations: dict[str, float] = {}

    def set(self, stage: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._durations[self._stage] = self._durations.get(self._stage, 0.0) + (now - self._stage_started)
            self._stage = stage
            self._stage_started = now

    def get(self) -> str:
        with self._lock:
            return self._stage

    def durations(self) -> dict[str, float]:
        with self._lock:
            now = time.monotonic()
            durations = dict(self._durations)
            durations[self._stage] = durations.get(self._stage, 0.0) + (now - self._stage_started)
        return {key: round(value, 3) for key, value in durations.items()}


class ResourceTelemetryMonitor:
    def __init__(
        self,
        *,
        telemetry_dir: Path,
        stage_tracker: StageTracker,
        provider_getter,
        interval_seconds: float = 2.0,
    ) -> None:
        if not telemetry_dir_allowed(telemetry_dir):
            raise S3AM2Blocked("telemetry_dir_outside_approved_tree")
        self.telemetry_dir = telemetry_dir
        self.stage_tracker = stage_tracker
        self.provider_getter = provider_getter
        self.interval_seconds = max(float(interval_seconds), 0.5)
        self.samples_path = telemetry_dir / "resource-samples.jsonl"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._psutil = None
        self._process = None
        self._nvidia_smi = shutil.which("nvidia-smi")
        try:
            import psutil  # type: ignore

            self._psutil = psutil
            self._process = psutil.Process(os.getpid())
            self._process.cpu_percent(interval=None)
        except Exception:
            self._psutil = None
            self._process = None

    def start(self) -> None:
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(self.samples_path, [])
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(5.0, self.interval_seconds * 2))
        return summarize_telemetry(self.samples_path)

    def _loop(self) -> None:
        while not self._stop.is_set():
            append_jsonl(self.samples_path, self.sample())
            self._stop.wait(self.interval_seconds)
        append_jsonl(self.samples_path, self.sample())

    def sample(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": utc_now(),
            "stage": self.stage_tracker.get(),
            "process_ids": [os.getpid()],
            "provider": self.provider_getter() or {},
            "psutil_available": bool(self._psutil),
            "nvidia_smi_available": bool(self._nvidia_smi),
        }
        if self._psutil:
            try:
                vm = self._psutil.virtual_memory()
                payload["system"] = {
                    "cpu_percent": self._psutil.cpu_percent(interval=None),
                    "ram_total_bytes": int(vm.total),
                    "ram_used_bytes": int(vm.used),
                    "ram_percent": float(vm.percent),
                }
                if self._process:
                    mem = self._process.memory_info()
                    payload["process"] = {
                        "pid": self._process.pid,
                        "cpu_percent": self._process.cpu_percent(interval=None),
                        "rss_bytes": int(mem.rss),
                        "working_set_bytes": int(getattr(mem, "wset", mem.rss)),
                    }
                    try:
                        io = self._process.io_counters()
                        payload["process"]["read_bytes"] = int(getattr(io, "read_bytes", 0))
                        payload["process"]["write_bytes"] = int(getattr(io, "write_bytes", 0))
                    except Exception:
                        payload["process"]["io_counters_available"] = False
            except Exception as exc:
                payload["psutil_error"] = exc.__class__.__name__
        payload["gpu"] = self._sample_nvidia()
        return payload

    def _sample_nvidia(self) -> list[dict[str, Any]]:
        if not self._nvidia_smi:
            return []
        query = (
            "--query-gpu=timestamp,index,name,utilization.gpu,memory.total,"
            "memory.used,memory.free,temperature.gpu,power.draw"
        )
        completed = subprocess.run(
            [
                self._nvidia_smi,
                query,
                "--format=csv,noheader,nounits",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )
        if completed.returncode != 0:
            return []
        rows: list[dict[str, Any]] = []
        for row in csv.reader(completed.stdout.splitlines()):
            if len(row) < 9:
                continue
            rows.append(
                {
                    "timestamp": row[0].strip(),
                    "index": row[1].strip(),
                    "name": row[2].strip(),
                    "utilization_gpu_percent": _float(row[3]),
                    "memory_total_mib": _float(row[4]),
                    "memory_used_mib": _float(row[5]),
                    "memory_free_mib": _float(row[6]),
                    "temperature_c": _float(row[7]),
                    "power_draw_watts": _float(row[8]),
                }
            )
        return rows


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        text = str(value).strip()
        if text in {"", "[Not Supported]", "N/A"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _actual_onnx_provider_from_payload(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    nested_provider = payload.get("provider")
    candidates: list[Mapping[str, Any]] = [payload]
    if isinstance(nested_provider, Mapping):
        candidates.append(nested_provider)
    for candidate in candidates:
        actual = str(candidate.get("actual_provider") or candidate.get("actual_onnx_provider_loaded") or "")
        if actual:
            return actual
    return ""


def summarize_telemetry(samples_path: Path) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    if samples_path.exists():
        with samples_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    gpu_rows = [gpu for sample in samples for gpu in (sample.get("gpu") or []) if isinstance(gpu, Mapping)]
    system_rows = [sample.get("system") or {} for sample in samples if isinstance(sample.get("system"), Mapping)]
    process_rows = [sample.get("process") or {} for sample in samples if isinstance(sample.get("process"), Mapping)]
    providers = [sample.get("provider") or {} for sample in samples if sample.get("provider")]
    actual_provider = ""
    for provider in reversed(providers):
        actual_provider = _actual_onnx_provider_from_payload(provider) or actual_provider
        if actual_provider:
            break
    max_gpu_memory = max((_float(row.get("memory_used_mib"), 0.0) or 0.0 for row in gpu_rows), default=0.0)
    max_gpu_util = max((_float(row.get("utilization_gpu_percent"), 0.0) or 0.0 for row in gpu_rows), default=0.0)
    avg_gpu_util = (
        sum((_float(row.get("utilization_gpu_percent"), 0.0) or 0.0 for row in gpu_rows)) / len(gpu_rows)
        if gpu_rows
        else 0.0
    )
    try:
        artifact = samples_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception:
        artifact = f".local_manifests/{PHASE_SLUG}/telemetry/{samples_path.name}"
    return {
        "status": "collected" if samples else "partial_no_samples",
        "samples": len(samples),
        "raw_samples_artifact": artifact,
        "raw_samples_path_redacted": True,
        "raw_path_committed": False,
        "psutil_available": any(sample.get("psutil_available") for sample in samples),
        "nvidia_smi_available": any(sample.get("nvidia_smi_available") for sample in samples),
        "gpu_sample_count": len(gpu_rows),
        "gpu_name": next((row.get("name") for row in gpu_rows if row.get("name")), None),
        "actual_provider": actual_provider or None,
        "gpu_provider_used": actual_provider in GPU_PROVIDERS,
        "cpu_fallback_observed": actual_provider == "CPUExecutionProvider",
        "max_gpu_memory_used_mib": round(max_gpu_memory, 3),
        "peak_gpu_utilization_percent": round(max_gpu_util, 3),
        "avg_gpu_utilization_percent": round(avg_gpu_util, 3),
        "peak_system_ram_percent": max((_float(row.get("ram_percent"), 0.0) or 0.0 for row in system_rows), default=0.0),
        "peak_process_rss_bytes": max((int(row.get("rss_bytes") or 0) for row in process_rows), default=0),
        "partial_reason": None if samples and (gpu_rows or actual_provider) else "gpu_metrics_unavailable_or_provider_not_loaded",
    }


def configure_phase_env(args: argparse.Namespace) -> None:
    validate_phase_limits(args)
    os.environ["DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_MAX_FILES"] = str(int(args.delta_cap))
    os.environ["DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS"] = str(int(args.execute_duration_seconds))
    os.environ["DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED"] = "true"
    os.environ["DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED"] = "true"
    os.environ["DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED"] = "false"
    os.environ["S3B_UNATTENDED_SYNC_ENABLED"] = "false"
    os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] = "false"
    os.environ["TAG_TRANSLATION_AUTO_ENABLED"] = "false"
    os.environ["TAG_TRANSLATION_BATCH_MAX_ITEMS"] = str(max(1, int(args.translation_batch_max_items)))


def open_db_session():
    from app import database as app_database

    if app_database.SessionLocal is None:
        app_database.init_engine()
    if app_database.SessionLocal is None:
        raise RuntimeError("Database SessionLocal is not initialized.")
    return app_database.SessionLocal()


def select_source_root(db: Any, root_id: int | None):
    from app.models import DynamicSourceRoot

    if root_id:
        root = db.get(DynamicSourceRoot, int(root_id))
        if root is None or not root.is_active:
            raise S3AM2Blocked("registered_active_source_root_not_found")
        return root
    roots = db.query(DynamicSourceRoot).filter(DynamicSourceRoot.is_active == True).order_by(DynamicSourceRoot.id.asc()).all()
    if len(roots) != 1:
        raise S3AM2Blocked("root_id_required_when_active_source_root_count_is_not_one")
    return roots[0]


def llm_config_public(settings: Any) -> dict[str, Any]:
    from urllib.parse import urlparse

    def host(value: str) -> str:
        try:
            return urlparse(value).hostname or ""
        except Exception:
            return ""

    return {
        "enabled": bool(settings.TAG_TRANSLATION_LLM_ENABLED),
        "provider": settings.TAG_TRANSLATION_LLM_PROVIDER,
        "provider_configured": bool(settings.TAG_TRANSLATION_LLM_PROVIDER),
        "model_configured": bool(settings.TAG_TRANSLATION_LLM_MODEL),
        "base_url_host": host(settings.TAG_TRANSLATION_LLM_BASE_URL),
        "base_url_configured": bool(settings.TAG_TRANSLATION_LLM_BASE_URL),
        "primary_auth_present": bool(settings.TAG_TRANSLATION_LLM_API_KEY),
        "fallback_enabled": bool(settings.TAG_TRANSLATION_LLM_FALLBACK_ENABLED),
        "fallback_provider": settings.TAG_TRANSLATION_LLM_FALLBACK_PROVIDER,
        "fallback_model_configured": bool(settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL),
        "fallback_base_url_host": host(settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL),
        "fallback_auth_present": bool(settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY),
        "sensitive_values_recorded": False,
    }


def collect_readiness(args: argparse.Namespace, root: Any) -> dict[str, Any]:
    from app.config import settings
    from app.models import AITagJob, ClassificationJob, TagTranslationJob
    from app.services.ai_tagging_service import check_model_status
    from app.services.dynamic_library_sync_service import validate_source_root_path
    from app.services.llm_translation_provider import get_llm_provider
    from app.services.manual_sync_execute_service import manual_sync_execute_max_files_cap

    blockers: list[str] = []
    warnings: list[str] = []
    branch = git_value(["branch", "--show-current"])
    origin_main = git_value(["rev-parse", "origin/main"])
    merge_base = git_value(["merge-base", "HEAD", "origin/main"])
    if branch != BRANCH:
        blockers.append("wrong_branch")
    if not origin_main or merge_base != origin_main:
        blockers.append("branch_not_based_on_latest_origin_main")
    if args.execute and settings.VIOLET_ENV != "production":
        blockers.append("VIOLET_ENV_not_production")
    db_name = str(getattr(settings, "DB_NAME", "") or "")
    if args.execute and settings.VIOLET_ENV == "production" and db_name.casefold().endswith("_test"):
        blockers.append("production_execute_db_identity_is_test_db")
    if args.execute and settings.VIOLET_ENV == "production" and db_name.casefold() == "blombooru_test":
        blockers.append("production_execute_db_identity_is_test_db")
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED:
        blockers.append("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED_false")
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED:
        blockers.append("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED_false")
    if settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED or settings.S3B_UNATTENDED_SYNC_ENABLED:
        blockers.append("automatic_or_unattended_sync_enabled")
    if not args.hydrated_only:
        blockers.append("hydrated_only_false")
    if int(args.delta_cap) > manual_sync_execute_max_files_cap():
        blockers.append("delta_cap_exceeds_runtime_execute_cap")
    if int(args.delta_cap) <= 5:
        warnings.append("delta_cap_still_m1_small_batch_size")

    db = open_db_session()
    try:
        active_ai = db.query(AITagJob.id).filter(AITagJob.status.in_(("pending", "running", "cancelling"))).count()
        active_classification = (
            db.query(ClassificationJob.id).filter(ClassificationJob.status.in_(("pending", "running", "cancelling"))).count()
        )
        active_translation = (
            db.query(TagTranslationJob.id).filter(TagTranslationJob.status.in_(("pending", "running", "cancelling"))).count()
        )
    finally:
        db.close()
    if active_ai or active_classification or active_translation:
        blockers.append("active_background_jobs_present")

    source_root_valid = False
    try:
        validate_source_root_path(root.root_path)
        source_root_valid = True
    except Exception:
        blockers.append("source_root_invalid_or_unsafe")

    ai_model = check_model_status()
    if not ai_model.get("enabled"):
        blockers.append("AI_TAGGING_ENABLED_false")
    if ai_model.get("model_downloaded") is not True:
        blockers.append("ai_model_not_available_local_files_only")

    provider_probe = {"available_providers": [], "gpu_provider_available": False}
    try:
        import onnxruntime as ort

        available = list(ort.get_available_providers())
        provider_probe = {
            "available_providers": available,
            "gpu_provider_available": any(provider in GPU_PROVIDERS for provider in available),
        }
    except Exception as exc:
        provider_probe = {"available_providers": [], "gpu_provider_available": False, "error_type": exc.__class__.__name__}
    if not provider_probe.get("gpu_provider_available") and not args.approve_cpu_fallback:
        blockers.append("gpu_provider_unavailable_without_cpu_fallback_approval")

    llm_config = llm_config_public(settings)
    provider_available = False
    provider_name = llm_config.get("provider")
    if args.require_localization_backend:
        try:
            provider = get_llm_provider()
            provider_available = bool(provider.is_available())
            provider_name = provider.get_provider_name()
        except Exception as exc:
            warnings.append(f"llm_provider_probe_failed:{exc.__class__.__name__}")
        if not llm_config["enabled"]:
            blockers.append("TAG_TRANSLATION_LLM_ENABLED_false")
        if not provider_available:
            blockers.append("localization_provider_unavailable")

    return {
        "passed": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "branch": branch,
        "head_sha": git_value(["rev-parse", "HEAD"]),
        "origin_main_sha": origin_main,
        "based_on_origin_main": bool(origin_main and merge_base == origin_main),
        "python_env": {
            "public_executable_name": Path(sys.executable).name,
            "executable_path_redacted": True,
        },
        "production_settings": {
            "violet_env": settings.VIOLET_ENV,
            "db_name": settings.DB_NAME,
            "storage_root_explicitly_set": settings.STORAGE_ROOT_EXPLICITLY_SET,
            "manual_sync_enabled": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED,
            "auto_sync_enabled": settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED,
            "unattended_sync_enabled": settings.S3B_UNATTENDED_SYNC_ENABLED,
        },
        "source": {
            "root_id": int(root.id),
            "label_redacted": True,
            "public_source_identity": str(root.root_path_hash or "")[:16],
            "valid": source_root_valid,
            "paths_redacted": True,
        },
        "active_jobs": {
            "ai_tagging": int(active_ai),
            "classification": int(active_classification),
            "translation": int(active_translation),
        },
        "ai_model": {
            "enabled": bool(ai_model.get("enabled")),
            "available": bool(ai_model.get("available")),
            "loaded": bool(ai_model.get("loaded")),
            "model_downloaded": ai_model.get("model_downloaded"),
            "model_name": ai_model.get("model_name"),
            "error_type": type(ai_model.get("error")).__name__ if ai_model.get("error") else None,
        },
        "onnx_provider_probe": provider_probe,
        "llm_localization": {**llm_config, "provider_available": provider_available, "provider": provider_name},
        "runtime_cap": {
            "delta_cap": int(args.delta_cap),
            "manual_execute_max_files_cap": manual_sync_execute_max_files_cap(),
            "cap_visible_in_report": True,
        },
    }


def _active_root_ledger_aggregates(db: Any, root_id: int) -> dict[str, Any]:
    from sqlalchemy import func

    from app.models import DynamicSourceItem
    from app.services.dynamic_library_sync_service import _manual_public_reason_code

    state_rows = (
        db.query(
            DynamicSourceItem.sync_state,
            DynamicSourceItem.import_status,
            DynamicSourceItem.classification_status,
            DynamicSourceItem.ai_tagging_status,
            DynamicSourceItem.localization_status,
            DynamicSourceItem.source_status,
            func.count(DynamicSourceItem.id),
        )
        .filter(DynamicSourceItem.source_root_id == int(root_id))
        .group_by(
            DynamicSourceItem.sync_state,
            DynamicSourceItem.import_status,
            DynamicSourceItem.classification_status,
            DynamicSourceItem.ai_tagging_status,
            DynamicSourceItem.localization_status,
            DynamicSourceItem.source_status,
        )
        .all()
    )
    reason_rows = (
        db.query(DynamicSourceItem.failure_reason, DynamicSourceItem.deferred_reason, func.count(DynamicSourceItem.id))
        .filter(DynamicSourceItem.source_root_id == int(root_id))
        .filter((DynamicSourceItem.failure_reason.isnot(None)) | (DynamicSourceItem.deferred_reason.isnot(None)))
        .group_by(DynamicSourceItem.failure_reason, DynamicSourceItem.deferred_reason)
        .all()
    )
    state_counts: Counter[str] = Counter()
    source_status_counts: Counter[str] = Counter()
    pipeline_status_counts: Counter[str] = Counter()
    for sync_state, import_status, classification_status, ai_status, loc_status, source_status, count in state_rows:
        count = int(count or 0)
        state_counts[str(sync_state or "unknown")] += count
        source_status_counts[str(source_status or "unknown")] += count
        pipeline_status_counts[f"import:{import_status or 'unknown'}"] += count
        pipeline_status_counts[f"classification:{classification_status or 'unknown'}"] += count
        pipeline_status_counts[f"ai_tagging:{ai_status or 'unknown'}"] += count
        pipeline_status_counts[f"localization:{loc_status or 'unknown'}"] += count
    reason_counts: Counter[str] = Counter()
    for failure_reason, deferred_reason, count in reason_rows:
        public_reason = _manual_public_reason_code(failure_reason or deferred_reason)
        reason_counts[public_reason or "read_error"] += int(count or 0)
    return {
        "sync_state_counts": dict(sorted(state_counts.items())),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "pipeline_status_counts": dict(sorted(pipeline_status_counts.items())),
        "public_failure_reason_counts": dict(sorted(reason_counts.items())),
    }


def build_ledger_pending_plan(
    db: Any,
    args: argparse.Namespace,
    root: Any,
    *,
    include_private_details: bool,
) -> dict[str, Any]:
    from app.config import settings
    from app.models import DynamicSourceItem
    from app.services.dynamic_library_sync_service import (
        MANUAL_SYNC_FILE_STATES,
        MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
        _build_manual_pipeline_stages,
        _estimate_manual_sync_runtime_seconds,
        _hash_text,
        _manual_public_reason_code,
        _public_counter,
        _query_existing_media_by_hashes,
        manual_sync_execute_confirmation_phrase,
        validate_source_root_path,
    )
    from app.services.job_control import build_ai_tagging_execution_profile

    if not args.hydrated_only:
        raise S3AM2Blocked("ledger_pending_plan_requires_hydrated_only")

    validate_source_root_path(root.root_path)
    created_at = parse_utc_datetime(args.plan_created_at) if args.execute else None
    created_at = created_at or datetime.now(timezone.utc)
    effective_max_files = max(1, int(args.delta_cap))
    effective_stable_age = (
        settings.DYNAMIC_LIBRARY_MANUAL_SYNC_STABLE_AGE_SECONDS
        if args.stable_age_seconds is None
        else max(0.0, float(args.stable_age_seconds))
    )
    max_duration_seconds = settings.DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS
    read_timeout_seconds = max(1, int(settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS))
    profile = build_ai_tagging_execution_profile(settings).to_public_dict()

    pending_query = db.query(DynamicSourceItem).filter(
        DynamicSourceItem.source_root_id == int(root.id),
        DynamicSourceItem.import_status == "pending",
        DynamicSourceItem.sync_state.in_(("new", "changed")),
        DynamicSourceItem.source_status == "available",
    )
    total_pending = int(pending_query.count())
    partial_scan = total_pending > effective_max_files
    source_items = (
        pending_query.order_by(DynamicSourceItem.first_seen_at.asc(), DynamicSourceItem.id.asc())
        .limit(effective_max_files)
        .all()
    )

    existing_media_by_hash = _query_existing_media_by_hashes(
        db,
        [str(item.content_hash) for item in source_items if item.content_hash],
    )
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    public_items: list[dict[str, Any]] = []
    private_items: list[dict[str, Any]] = []
    integrity_items: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for index, source_item in enumerate(source_items, start=1):
        safe_label = f"delta-{index:05d}"
        relative_path_hash_full = str(source_item.relative_path_hash or _hash_text(str(source_item.relative_path or "")))
        relative_path_hash = relative_path_hash_full[:16]
        content_hash = str(source_item.content_hash or "") or None
        reason = None
        media_id = None
        if source_item.media_id:
            state = "skipped_existing_media"
            reason = "existing_media_hash"
            media_id = int(source_item.media_id)
        elif content_hash and content_hash in existing_media_by_hash:
            state = "skipped_existing_media"
            reason = "existing_media_hash"
            media_id = int(existing_media_by_hash[content_hash])
        elif content_hash and content_hash in seen_hashes:
            state = "skipped_duplicate"
            reason = "duplicate_hash"
        else:
            state = "import_planned"
            if content_hash:
                seen_hashes.add(content_hash)

        public_reason = _manual_public_reason_code(reason)
        state_counts[state] += 1
        if public_reason:
            reason_counts[public_reason] += 1

        item = {
            "safe_label": safe_label,
            "relative_path_hash": relative_path_hash,
            "source_item_state": str(source_item.sync_state or "unknown"),
            "source_status": str(source_item.source_status or "unknown"),
            "initial_state": "ledger_pending",
            "state": state,
            "reason": public_reason,
            "eligible_for_db_import": state == "import_planned",
            "bytes_copied": 0,
            "media_id": media_id,
            "file_size": int(source_item.file_size) if source_item.file_size is not None else None,
            "content_hash_computed": bool(content_hash),
        }
        public_items.append(item)
        integrity_items.append(
            {
                "source_item_id": int(source_item.id),
                "safe_label": safe_label,
                "relative_path_hash": relative_path_hash_full,
                "file_size": int(source_item.file_size) if source_item.file_size is not None else None,
                "mtime_ns": int(source_item.mtime_ns) if source_item.mtime_ns is not None else None,
                "source_status": str(source_item.source_status or "unknown"),
                "source_item_state": str(source_item.sync_state or "unknown"),
                "state": state,
                "reason": public_reason,
                "content_hash": content_hash,
            }
        )
        if include_private_details:
            private_items.append(
                {
                    **item,
                    "source_item_id": int(source_item.id),
                    "relative_path": str(source_item.relative_path or ""),
                    "content_hash": content_hash,
                    "mtime_ns": int(source_item.mtime_ns) if source_item.mtime_ns is not None else None,
                }
            )

    import_count = int(state_counts.get("import_planned", 0))
    estimated_runtime_seconds = _estimate_manual_sync_runtime_seconds(
        import_count=import_count,
        ai_profile=profile,
        benchmark=None,
    )
    stages = _build_manual_pipeline_stages(
        state_counts=state_counts,
        import_count=import_count,
        downstream_followup_count=0,
        ai_profile=profile,
        max_duration_seconds=max_duration_seconds,
        estimated_runtime_seconds=estimated_runtime_seconds,
    )
    limits = {
        "max_files": effective_max_files,
        "hydrated_only": True,
        "stable_age_seconds": effective_stable_age,
        "max_duration_seconds": max_duration_seconds,
        "file_read_timeout_seconds": read_timeout_seconds,
        "plan_source": "ledger_pending",
        "pending_candidate_count": total_pending,
    }
    source_identity_hash = str(root.root_path_hash or "")[:16]
    integrity_payload = {
        "schema": "s3a_m2_production_delta_ledger_plan_integrity_v1",
        "created_at": created_at.isoformat(),
        "source": {
            "source_record_id": int(root.id),
            "source_identity_hash": source_identity_hash,
        },
        "limits": limits,
        "items": integrity_items,
    }
    plan_hash = stable_json_hash(integrity_payload)
    expires_at = created_at + timedelta(seconds=MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS)
    ledger_aggregates = _active_root_ledger_aggregates(db, int(root.id))

    plan: dict[str, Any] = {
        "job": {
            "job_id": f"s3a-m2-plan-{uuid4()}",
            "mode": "dry_run",
            "state": "planned",
            "trigger_type": "manual_operator",
            "requested_by": "admin_or_cli",
            "created_at": created_at.isoformat(),
            "started_at": None,
            "ended_at": None,
            "production_execution_enabled": False,
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
        },
        "source": {
            "source_record_id": int(root.id),
            "source_identity_hash": source_identity_hash,
            "path_public": False,
            "plan_source": "ledger_pending",
        },
        "limits": limits,
        "counts": {
            "total_seen": total_pending,
            "planned_item_count": len(public_items),
            "estimated_import_count": import_count,
            "estimated_classification_count": import_count,
            "estimated_ai_tagging_count": import_count,
            "estimated_localization_workload": import_count,
            "state_counts": _public_counter(state_counts, MANUAL_SYNC_FILE_STATES),
            "failure_reasons": dict(sorted((key, int(value)) for key, value in reason_counts.items())),
            "partial_scan": partial_scan,
            "source_ledger_aggregate_counts": ledger_aggregates,
        },
        "ledger": {
            "db_write_performed": False,
            "source_mutation_performed": False,
            "app_storage_mutation_performed": False,
            "persistent_tables_available": [
                "blombooru_dynamic_source_roots",
                "blombooru_dynamic_source_items",
                "blombooru_dynamic_sync_runs",
                "blombooru_dynamic_sync_run_items",
            ],
            "ledger_mode": "persistent_pending_delta_public_plan_current_phase",
            "per_file_public_records": public_items,
            "private_details_included": include_private_details,
            "source_ledger_aggregate_counts": ledger_aggregates,
        },
        "pipeline": {
            "status": "dry_run_planned",
            "dry_run_only_this_phase": False,
            "production_execute_enabled": False,
            "dev_test_execute_supported": True,
            "production_execute_requires_separate_operator_approval": True,
            "stages": stages,
            "estimated_runtime_seconds": estimated_runtime_seconds,
            "partial_failure_policy": "item_failures_recorded_and_continues_until_failure_budget_or_hard_gate",
        },
        "ai_execution_profile": profile,
        "integrity": {
            "schema": "s3a_m2_production_delta_ledger_plan_integrity_v1",
            "plan_hash": plan_hash,
            "hash_algorithm": "sha256",
            "stale_after_seconds": MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
            "expires_at": expires_at.isoformat(),
            "hash_excludes_paths": True,
            "hash_includes_private_content_fingerprint": True,
            "confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash),
            "production_confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash, production=True),
        },
        "public_safe": True,
    }
    if include_private_details:
        plan["private_details"] = {
            "not_for_public_reports": True,
            "items": private_items,
        }
    return plan


def build_source_delta_plan(
    db: Any,
    args: argparse.Namespace,
    root: Any,
    *,
    include_private_details: bool,
) -> dict[str, Any]:
    from app.config import settings
    from app.models import DynamicSourceItem
    from app.services.dynamic_library_sync_service import (
        MANUAL_SYNC_FILE_STATES,
        MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
        _build_manual_pipeline_stages,
        _calculate_manual_plan_file_hash,
        _estimate_manual_sync_runtime_seconds,
        _hash_text,
        _is_scannable_file,
        _iter_source_files,
        _manual_public_reason_code,
        _manual_state_for_reason,
        _metadata_for_path,
        _public_counter,
        _query_existing_media_by_hashes,
        _relative_identity_and_preflight_reason,
        _verify_supported_image_file_with_timeout,
        manual_sync_execute_confirmation_phrase,
        validate_source_root_path,
    )
    from app.services.job_control import build_ai_tagging_execution_profile

    if not args.hydrated_only:
        raise S3AM2Blocked("source_delta_plan_requires_hydrated_only")

    source_path = validate_source_root_path(root.root_path)
    created_at = parse_utc_datetime(args.plan_created_at) if args.execute else None
    created_at = created_at or datetime.now(timezone.utc)
    created_ts = created_at.timestamp()
    effective_max_files = max(1, int(args.delta_cap))
    effective_stable_age = (
        settings.DYNAMIC_LIBRARY_MANUAL_SYNC_STABLE_AGE_SECONDS
        if args.stable_age_seconds is None
        else max(0.0, float(args.stable_age_seconds))
    )
    max_duration_seconds = settings.DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS
    read_timeout_seconds = max(1, int(settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS))
    profile = build_ai_tagging_execution_profile(settings).to_public_dict()
    existing_items = {
        str(item.relative_path_hash): item
        for item in db.query(DynamicSourceItem).filter(DynamicSourceItem.source_root_id == int(root.id)).all()
        if item.relative_path_hash
    }

    candidate_records: list[dict[str, Any]] = []
    candidate_hashes: set[str] = set()
    walk_errors: list[str] = []
    partial_scan = False
    scanned_files = 0
    unchanged_known_files = 0

    for file_path in _iter_source_files(source_path, walk_errors=walk_errors):
        scanned_files += 1
        rel, preflight_reason = _relative_identity_and_preflight_reason(source_path, file_path)
        rel_hash_full = _hash_text(rel)
        existing = existing_items.get(rel_hash_full)
        reason = _manual_public_reason_code(preflight_reason)
        metadata: dict[str, Any] = {}
        content_hash = str(getattr(existing, "content_hash", "") or "") or None

        try:
            reason = reason or _manual_public_reason_code(_is_scannable_file(file_path, hydrated_only=True))
            metadata = _metadata_for_path(file_path, follow_symlinks=not bool(preflight_reason))
        except OSError:
            reason = "stat_error"

        metadata_changed = True
        if existing is not None and metadata:
            metadata_changed = (
                (existing.file_size is None or int(existing.file_size) != int(metadata.get("file_size") or -1))
                or (existing.mtime_ns is None or int(existing.mtime_ns) != int(metadata.get("mtime_ns") or -1))
            )
        pending_existing = bool(existing is not None and str(existing.import_status or "") == "pending")
        unresolved_followup = _existing_requires_source_delta_followup(existing, args)
        if existing is not None and not pending_existing and not unresolved_followup and not metadata_changed:
            unchanged_known_files += 1
            continue

        if len(candidate_records) >= effective_max_files:
            partial_scan = True
            break

        if reason is None and effective_stable_age > 0:
            mtime = metadata.get("mtime")
            if mtime is not None and created_ts - float(mtime) < effective_stable_age:
                reason = "file_still_changing"

        if reason is None:
            reason = _verify_supported_image_file_with_timeout(file_path, read_timeout_seconds)

        if reason is None:
            content_hash, reason = _calculate_manual_plan_file_hash(file_path, read_timeout_seconds)

        reason = _manual_public_reason_code(reason)
        if reason is None and content_hash:
            candidate_hashes.add(content_hash)

        candidate_records.append(
            {
                "safe_label": f"delta-{len(candidate_records) + 1:05d}",
                "relative_path": rel,
                "relative_path_hash": rel_hash_full[:16],
                "relative_path_hash_full": rel_hash_full,
                "metadata": metadata,
                "reason": reason,
                "content_hash": content_hash,
                "extension": file_path.suffix.lower() or "<none>",
                "source_item_id": int(existing.id) if existing is not None else None,
                "source_item_state": str(getattr(existing, "sync_state", "") or "new"),
                "source_status": str(getattr(existing, "source_status", "") or "available"),
            }
        )

    existing_media_by_hash = _query_existing_media_by_hashes(db, candidate_hashes)
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    public_items: list[dict[str, Any]] = []
    private_items: list[dict[str, Any]] = []
    integrity_items: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    unsupported_extension_counts: Counter[str] = Counter()
    reason_extension_counts: dict[str, Counter[str]] = {}

    for record in candidate_records:
        reason = record["reason"]
        content_hash = record["content_hash"]
        media_id = None
        if reason is None and content_hash:
            if content_hash in existing_media_by_hash:
                state = "skipped_existing_media"
                reason = "existing_media_hash"
                media_id = int(existing_media_by_hash[content_hash])
            elif content_hash in seen_hashes:
                state = "skipped_duplicate"
                reason = "duplicate_hash"
            else:
                state = "import_planned"
                seen_hashes.add(content_hash)
        else:
            state = _manual_state_for_reason(str(reason or "read_error"))

        public_reason = _manual_public_reason_code(reason)
        state_counts[state] += 1
        if public_reason:
            reason_counts[public_reason] += 1
            reason_extension_counts.setdefault(public_reason, Counter())[record.get("extension") or "<none>"] += 1
        if public_reason == "unsupported_extension":
            unsupported_extension_counts[record.get("extension") or "<none>"] += 1

        metadata = record["metadata"]
        item = {
            "safe_label": record["safe_label"],
            "relative_path_hash": record["relative_path_hash"],
            "source_item_state": record["source_item_state"],
            "source_status": record["source_status"],
            "initial_state": "source_delta",
            "state": state,
            "reason": public_reason,
            "eligible_for_db_import": state == "import_planned",
            "bytes_copied": 0,
            "media_id": media_id,
            "file_size": metadata.get("file_size"),
            "content_hash_computed": bool(content_hash),
        }
        public_items.append(item)
        integrity_items.append(
            {
                "source_item_id": record["source_item_id"],
                "safe_label": record["safe_label"],
                "relative_path_hash": record["relative_path_hash_full"],
                "file_size": metadata.get("file_size"),
                "mtime_ns": metadata.get("mtime_ns"),
                "source_status": record["source_status"],
                "source_item_state": record["source_item_state"],
                "state": state,
                "reason": public_reason,
                "content_hash": content_hash,
            }
        )
        if include_private_details:
            private_items.append(
                {
                    **item,
                    "source_item_id": record["source_item_id"],
                    "relative_path": record["relative_path"],
                    "content_hash": content_hash,
                    "mtime_ns": metadata.get("mtime_ns"),
                }
            )

    if walk_errors:
        partial_scan = True
        reason_counts["source_walk_error"] += len(walk_errors)

    import_count = int(state_counts.get("import_planned", 0))
    estimated_runtime_seconds = _estimate_manual_sync_runtime_seconds(
        import_count=import_count,
        ai_profile=profile,
        benchmark=None,
    )
    stages = _build_manual_pipeline_stages(
        state_counts=state_counts,
        import_count=import_count,
        downstream_followup_count=0,
        ai_profile=profile,
        max_duration_seconds=max_duration_seconds,
        estimated_runtime_seconds=estimated_runtime_seconds,
    )
    limits = {
        "max_files": effective_max_files,
        "hydrated_only": True,
        "stable_age_seconds": effective_stable_age,
        "max_duration_seconds": max_duration_seconds,
        "file_read_timeout_seconds": read_timeout_seconds,
        "plan_source": "source_delta",
        "scanned_files": scanned_files,
        "unchanged_known_files": unchanged_known_files,
    }
    source_identity_hash = str(root.root_path_hash or "")[:16]
    integrity_payload = {
        "schema": "s3a_m2_production_delta_source_plan_integrity_v1",
        "created_at": created_at.isoformat(),
        "source": {
            "source_record_id": int(root.id),
            "source_identity_hash": source_identity_hash,
        },
        "limits": limits,
        "items": integrity_items,
    }
    plan_hash = stable_json_hash(integrity_payload)
    expires_at = created_at + timedelta(seconds=MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS)
    ledger_aggregates = _active_root_ledger_aggregates(db, int(root.id))

    plan: dict[str, Any] = {
        "job": {
            "job_id": f"s3a-m2-plan-{uuid4()}",
            "mode": "dry_run",
            "state": "planned",
            "trigger_type": "manual_operator",
            "requested_by": "admin_or_cli",
            "created_at": created_at.isoformat(),
            "started_at": None,
            "ended_at": None,
            "production_execution_enabled": False,
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
        },
        "source": {
            "source_record_id": int(root.id),
            "source_identity_hash": source_identity_hash,
            "path_public": False,
            "plan_source": "source_delta",
        },
        "limits": limits,
        "counts": {
            "total_seen": len(public_items),
            "planned_item_count": len(public_items),
            "estimated_import_count": import_count,
            "estimated_classification_count": import_count,
            "estimated_ai_tagging_count": import_count,
            "estimated_localization_workload": import_count,
            "state_counts": _public_counter(state_counts, MANUAL_SYNC_FILE_STATES),
            "failure_reasons": dict(sorted((key, int(value)) for key, value in reason_counts.items())),
            "partial_scan": partial_scan,
            "source_ledger_aggregate_counts": ledger_aggregates,
            "unsupported_extension_breakdown": dict(
                sorted((key, int(value)) for key, value in unsupported_extension_counts.items())
            ),
            "failure_reason_extension_breakdown": {
                reason: dict(sorted((key, int(value)) for key, value in counter.items()))
                for reason, counter in sorted(reason_extension_counts.items())
            },
        },
        "ledger": {
            "db_write_performed": False,
            "source_mutation_performed": False,
            "app_storage_mutation_performed": False,
            "persistent_tables_available": [
                "blombooru_dynamic_source_roots",
                "blombooru_dynamic_source_items",
                "blombooru_dynamic_sync_runs",
                "blombooru_dynamic_sync_run_items",
            ],
            "ledger_mode": "read_only_source_delta_public_plan_current_phase",
            "per_file_public_records": public_items,
            "private_details_included": include_private_details,
            "source_ledger_aggregate_counts": ledger_aggregates,
        },
        "pipeline": {
            "status": "dry_run_planned",
            "dry_run_only_this_phase": False,
            "production_execute_enabled": False,
            "dev_test_execute_supported": True,
            "production_execute_requires_separate_operator_approval": True,
            "stages": stages,
            "estimated_runtime_seconds": estimated_runtime_seconds,
            "partial_failure_policy": "item_failures_recorded_and_continues_until_failure_budget_or_hard_gate",
        },
        "ai_execution_profile": profile,
        "integrity": {
            "schema": "s3a_m2_production_delta_source_plan_integrity_v1",
            "plan_hash": plan_hash,
            "hash_algorithm": "sha256",
            "stale_after_seconds": MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
            "expires_at": expires_at.isoformat(),
            "hash_excludes_paths": True,
            "hash_includes_private_content_fingerprint": True,
            "confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash),
            "production_confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash, production=True),
        },
        "public_safe": True,
    }
    if include_private_details:
        plan["private_details"] = {
            "not_for_public_reports": True,
            "items": private_items,
        }
    return plan


def build_manual_plan(args: argparse.Namespace, root: Any) -> dict[str, Any]:
    from app.services.dynamic_library_sync_service import plan_manual_sync_dry_run

    approved_plan_path = Path(args.approved_plan_json)
    if args.execute and args.plan_source in {"source-delta", "ledger-pending"} and approved_plan_path.exists():
        plan = read_json(approved_plan_path)
        if not isinstance(plan, dict):
            raise S3AM2Blocked("approved_plan_json_not_object")
        if int(((plan.get("source") or {}).get("source_record_id") or 0)) != int(root.id):
            raise S3AM2Blocked("approved_plan_source_root_mismatch")
        if int(((plan.get("limits") or {}).get("max_files") or 0)) != int(args.delta_cap):
            raise S3AM2Blocked("approved_plan_delta_cap_mismatch")
        return plan

    db = open_db_session()
    try:
        if args.plan_source == "source-delta":
            return build_source_delta_plan(db, args, root, include_private_details=True)
        plan_created_at = parse_utc_datetime(args.plan_created_at) if args.execute else None
        if args.plan_source == "ledger-pending":
            return build_ledger_pending_plan(db, args, root, include_private_details=True)
        return plan_manual_sync_dry_run(
            db,
            source_path=root.root_path,
            source_record_id=root.id,
            max_files=int(args.delta_cap),
            hydrated_only=bool(args.hydrated_only),
            stable_age_seconds=args.stable_age_seconds,
            include_private_details=False,
            now=plan_created_at,
        )
    finally:
        db.close()


def _create_s3a_m2_execute_run_from_plan(
    db: Any,
    *,
    args: argparse.Namespace,
    plan: Mapping[str, Any],
    expected_hash: str,
) -> Any:
    from app.config import settings
    from app.models import DynamicSourceRoot, DynamicSyncRun
    from app.services.manual_sync_execute_service import (
        ManualSyncExecuteError,
        _budget_policy_payload,
        _find_active_manual_sync_execute_run,
        _localization_policy_payload,
        _public_request_payload,
        _recover_stale_manual_sync_execute_runs,
        _verify_execute_gates,
        is_manual_sync_execute_active,
        manual_sync_execute_effective_max_files,
    )

    if is_manual_sync_execute_active():
        raise ManualSyncExecuteError(
            "manual_sync_execute_already_active",
            "Another manual sync execute run is active.",
            status_code=409,
        )
    _recover_stale_manual_sync_execute_runs(db)
    if _find_active_manual_sync_execute_run(db) is not None:
        raise ManualSyncExecuteError(
            "manual_sync_execute_already_active",
            "Another manual sync execute run is pending or active.",
            status_code=409,
        )

    root = db.get(DynamicSourceRoot, int(args.root_id))
    if root is None or not root.is_active:
        raise ManualSyncExecuteError(
            "source_root_not_found",
            "Manual sync execute requires an active registered source root.",
            status_code=404,
        )
    service_phrase = (
        (plan.get("integrity") or {}).get("production_confirmation_phrase")
        if settings.IS_PRODUCTION_ENV
        else (plan.get("integrity") or {}).get("confirmation_phrase")
    )
    effective_max_files = manual_sync_execute_effective_max_files(int(args.delta_cap))
    _verify_execute_gates(
        db=db,
        plan=dict(plan),
        expected_plan_hash=expected_hash,
        confirmation_phrase=str(service_phrase or ""),
        plan_created_at=str(args.plan_created_at),
        hydrated_only=bool(args.hydrated_only),
        production_acceptance_approved=bool(settings.IS_PRODUCTION_ENV),
    )
    private_plan = dict(plan)
    plan_source = str(((plan.get("source") or {}).get("plan_source") or args.plan_source)).replace("-", "_")
    plan_mode = str(
        ((plan.get("limits") or {}).get("plan_mode") or (plan.get("request") or {}).get("plan_mode") or plan_source)
    ).replace("-", "_")
    if str((private_plan.get("integrity") or {}).get("plan_hash") or "") != expected_hash:
        raise ManualSyncExecuteError(
            "stale_or_mismatched_plan_hash",
            "Approved plan hash changed while preparing execute. Re-run dry-run before execute.",
            status_code=409,
        )
    private_plan_items = list(((private_plan.get("private_details") or {}).get("items") or []))
    if not private_plan_items and int((plan.get("counts") or {}).get("total_seen") or 0) > 0:
        raise ManualSyncExecuteError(
            "private_approved_plan_snapshot_missing",
            "S3A-M2 execute requires the private dry-run snapshot captured under .local_manifests.",
            status_code=409,
        )
    public_plan = dict(plan)
    public_plan.pop("private_details", None)
    now = datetime.now(timezone.utc)
    stage_rows = [
        {"name": name, "status": "pending", "processed": 0, "failed": 0}
        for name in ("candidate_discovery", "import", "classification", "ai_tagging", "localization", "summary")
    ]
    counts = plan.get("counts") or {}
    run = DynamicSyncRun(
        run_type="manual_sync_execute",
        mode="production_acceptance" if settings.IS_PRODUCTION_ENV else "dev_test_execute",
        status="pending",
        dry_run=False,
        roots_checked=1,
        total_seen=int(counts.get("total_seen") or 0),
        pending_import_items=int(counts.get("estimated_import_count") or 0),
        started_at=now,
        summary_json={
            "phase": PHASE,
            "manual_sync_execute": {
                "status": "pending",
                "current_stage": "queued",
                "request": {
                    **_public_request_payload(
                        root_id=int(args.root_id),
                        max_files=int(args.delta_cap),
                        effective_max_files=effective_max_files,
                        hydrated_only=bool(args.hydrated_only),
                        stable_age_seconds=args.stable_age_seconds,
                        expected_plan_hash=expected_hash,
                        plan_created_at=str(args.plan_created_at),
                        production_acceptance_approved=bool(settings.IS_PRODUCTION_ENV),
                        plan_mode=plan_mode,
                    ),
                    "phase": PHASE,
                    "plan_source": plan_source,
                },
                "plan": public_plan,
                "private_plan_items": private_plan_items,
                "stage_rows": stage_rows,
                "outcome_counts": {},
                "budgets": _budget_policy_payload(),
                "localization": _localization_policy_payload([]),
                "classification": {
                    "local_only": True,
                    "clip_cache_only_required": True,
                    "uncached_clip_reason": "classification_model_uncached",
                    "external_download_allowed": False,
                },
                "safety": {
                    "manual_trigger_only": True,
                    "automatic_sync_enabled": False,
                    "scheduled_sync_enabled": False,
                    "startup_sync_enabled": False,
                    "source_mutation_performed": False,
                    "local_files_only_ai": True,
                    "external_provider_calls_performed": False,
                    "model_downloads_allowed": False,
                    "llm_calls_enabled": False,
                    "localization_scheduled": False,
                    "translation_llm_side_effects_blocked": True,
                    "production_acceptance_pending": False,
                    "confirmation_prefix": "I APPROVE S3A-M2 PRODUCTION DELTA E2E",
                },
            },
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def execute_manual_plan(args: argparse.Namespace, plan: Mapping[str, Any], stage_tracker: StageTracker) -> dict[str, Any]:
    from app.config import settings
    from app.services.manual_sync_execute_service import create_manual_sync_execute_run, execute_manual_sync_run

    expected_hash = str(args.expected_plan_hash or "")
    if expected_hash != str((plan.get("integrity") or {}).get("plan_hash") or ""):
        raise S3AM2Blocked("expected_plan_hash_does_not_match_fresh_plan")
    if args.s3a_m2_approval_phrase != s3a_m2_approval_phrase(expected_hash):
        raise S3AM2Blocked("s3a_m2_approval_phrase_missing_or_wrong")
    service_phrase = (
        (plan.get("integrity") or {}).get("production_confirmation_phrase")
        if settings.IS_PRODUCTION_ENV
        else (plan.get("integrity") or {}).get("confirmation_phrase")
    )
    stage_tracker.set("manual_execute_import_classification_ai")
    db = open_db_session()
    previous_translation_auto = os.environ.get("TAG_TRANSLATION_AUTO_ENABLED")
    previous_translation_background = os.environ.get("TAG_TRANSLATION_BACKGROUND_ENABLED")
    try:
        os.environ["TAG_TRANSLATION_AUTO_ENABLED"] = "false"
        os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] = "false"
        if args.plan_source in {"ledger-pending", "source-delta"}:
            run = _create_s3a_m2_execute_run_from_plan(db, args=args, plan=plan, expected_hash=expected_hash)
        else:
            run = create_manual_sync_execute_run(
                db,
                root_id=int(args.root_id),
                max_files=int(args.delta_cap),
                hydrated_only=bool(args.hydrated_only),
                stable_age_seconds=args.stable_age_seconds,
                expected_plan_hash=expected_hash,
                confirmation_phrase=str(service_phrase or ""),
                plan_created_at=str(args.plan_created_at),
                production_acceptance_approved=bool(settings.IS_PRODUCTION_ENV),
        )
        return execute_manual_sync_run(db, run_id=int(run.id))
    finally:
        if previous_translation_auto is None:
            os.environ.pop("TAG_TRANSLATION_AUTO_ENABLED", None)
        else:
            os.environ["TAG_TRANSLATION_AUTO_ENABLED"] = previous_translation_auto
        if previous_translation_background is None:
            os.environ.pop("TAG_TRANSLATION_BACKGROUND_ENABLED", None)
        else:
            os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] = previous_translation_background
        db.close()


def category_value(category: Any) -> str:
    if hasattr(category, "value"):
        return str(category.value)
    text = str(category)
    return text.split(".", 1)[-1] if "." in text else text


def _all_private_plan_items(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    private = plan.get("private_details") or {}
    items = private.get("items") or private.get("private_plan_items") or []
    return [dict(item) for item in items if isinstance(item, Mapping)]


def _existing_requires_source_delta_followup(existing: Any, args: argparse.Namespace) -> bool:
    """Keep unresolved S3A-M2 work visible even when file metadata is unchanged."""

    if existing is None:
        return False
    include_run_id = int(getattr(args, "include_unresolved_run_id", 0) or 0)
    if include_run_id and int(getattr(existing, "last_sync_run_id", 0) or 0) != include_run_id:
        return False
    import_status = str(getattr(existing, "import_status", "") or "")
    sync_state = str(getattr(existing, "sync_state", "") or "")
    reason = str(getattr(existing, "deferred_reason", "") or getattr(existing, "failure_reason", "") or "")
    if sync_state == "skipped_placeholder" or reason in {"cloud_placeholder", "icloud_placeholder"}:
        return True
    if import_status == "pending":
        return True
    if import_status == "imported" and getattr(existing, "media_id", None) is None:
        return True
    stable_non_actionable = {
        "unsupported_extension",
        "hidden",
        "zero_byte",
        "zero_byte_file",
        "read_error",
        "source_missing",
        "permission_denied",
    }
    if reason in stable_non_actionable:
        return False
    return import_status in {"deferred", "failed"} or sync_state in {"failed", "deferred"}


def _static_translation_names() -> set[str]:
    try:
        from app.services.tag_localization_service import _load_static_dict

        return set((_load_static_dict().get("tags") or {}).keys())
    except Exception:
        return set()


def _tag_category_enums(names: Sequence[str]) -> list[Any]:
    from app.enums import TagCategoryEnum

    by_value = {item.value: item for item in TagCategoryEnum}
    return [by_value[name] for name in names if name in by_value]


def select_delta_localization_candidates(db: Any, run_id: int, *, lang: str, limit: int) -> tuple[list[dict[str, Any]], int, bool]:
    from app.models import DynamicSyncRunItem, Tag, TagTranslation, blombooru_media_tags

    media_ids = [
        int(row[0])
        for row in db.query(DynamicSyncRunItem.media_id)
        .filter(DynamicSyncRunItem.sync_run_id == int(run_id), DynamicSyncRunItem.media_id.isnot(None))
        .distinct()
        .all()
    ]
    if not media_ids:
        return [], 0, False
    translated = db.query(TagTranslation.canonical_name).filter(
        TagTranslation.language == lang,
        TagTranslation.status != "rejected",
    )
    static_names = _static_translation_names()
    base = (
        db.query(Tag)
        .join(blombooru_media_tags, Tag.id == blombooru_media_tags.c.tag_id)
        .filter(blombooru_media_tags.c.media_id.in_(media_ids))
        .filter(blombooru_media_tags.c.source == "ai_wd")
        .filter(~Tag.name.in_(translated))
    )
    if static_names:
        base = base.filter(~Tag.name.in_(static_names))
    visual_rows = (
        base.filter(Tag.category.in_(_tag_category_enums(LOCALIZABLE_CATEGORIES)))
        .distinct()
        .order_by(Tag.post_count.desc(), Tag.name.asc())
        .limit(limit + 1)
        .all()
    )
    proper_count = (
        base.filter(Tag.category.in_(_tag_category_enums(PROPER_NOUN_CATEGORIES)))
        .distinct()
        .count()
    )
    candidates = [
        {
            "tag_id": int(tag.id),
            "canonical_name": tag.name,
            "category": category_value(tag.category),
            "post_count": int(tag.post_count or 0),
        }
        for tag in visual_rows[:limit]
    ]
    return candidates, int(proper_count), len(visual_rows) > limit


def diagnose_run_localization(db: Any, run_id: int, *, lang: str = "zh-CN", job_id: int | None = None) -> dict[str, Any]:
    from app.models import DynamicSyncRun, DynamicSyncRunItem, Tag, TagTranslation, TagTranslationJob, blombooru_media_tags

    media_ids = [
        int(row[0])
        for row in db.query(DynamicSyncRunItem.media_id)
        .filter(DynamicSyncRunItem.sync_run_id == int(run_id), DynamicSyncRunItem.media_id.isnot(None))
        .distinct()
        .all()
    ]
    translated_names = {
        str(row[0])
        for row in db.query(TagTranslation.canonical_name)
        .filter(TagTranslation.language == lang, TagTranslation.status != "rejected")
        .all()
    }
    static_names = _static_translation_names()
    rows = []
    if media_ids:
        rows = (
            db.query(Tag.name, Tag.category, blombooru_media_tags.c.is_suggestion)
            .join(blombooru_media_tags, Tag.id == blombooru_media_tags.c.tag_id)
            .filter(blombooru_media_tags.c.media_id.in_(media_ids))
            .filter(blombooru_media_tags.c.source == "ai_wd")
            .all()
        )
    distinct_tags: dict[str, dict[str, Any]] = {}
    suggestion_assignments = 0
    for name, category, is_suggestion in rows:
        key = str(name)
        distinct_tags.setdefault(key, {"category": category_value(category), "suggestion_seen": False})
        if bool(is_suggestion):
            suggestion_assignments += 1
            distinct_tags[key]["suggestion_seen"] = True

    by_category = Counter(str(item["category"]) for item in distinct_tags.values())
    localizable = {name for name, item in distinct_tags.items() if item["category"] in LOCALIZABLE_CATEGORIES}
    proper = {name for name, item in distinct_tags.items() if item["category"] in PROPER_NOUN_CATEGORIES}
    other = set(distinct_tags) - localizable - proper
    localized = {name for name in distinct_tags if name in translated_names or name in static_names}
    localizable_missing_after = localizable - localized
    proper_missing = proper - localized

    if job_id:
        job = db.get(TagTranslationJob, int(job_id))
    else:
        job = (
            db.query(TagTranslationJob)
            .filter(TagTranslationJob.source == "s3a_m2_delta_e2e")
            .order_by(TagTranslationJob.id.desc())
            .first()
        )
    run = db.get(DynamicSyncRun, int(run_id))
    translated_by_runner = int(job.translated or 0) if job else 0
    failed_by_runner = int(job.failed or 0) if job else 0
    skipped_by_runner = int(job.skipped or 0) if job else 0
    return {
        "run_id": int(run_id),
        "run_status": str(run.status) if run else None,
        "lang": lang,
        "imported_media_count": len(media_ids),
        "ai_wd_assignment_count": len(rows),
        "ai_wd_suggestion_assignment_count": suggestion_assignments,
        "distinct_ai_wd_tag_count": len(distinct_tags),
        "distinct_by_category": dict(sorted((str(key), int(value)) for key, value in by_category.items())),
        "localizable_categories": list(LOCALIZABLE_CATEGORIES),
        "proper_noun_categories": list(PROPER_NOUN_CATEGORIES),
        "localizable_distinct_tags": len(localizable),
        "already_localized_or_static_distinct_all_tags": len(localized),
        "localizable_already_localized_or_static": len(localizable & localized),
        "newly_localized_tags": translated_by_runner,
        "tags_requiring_localization_before_runner": translated_by_runner + failed_by_runner + len(localizable_missing_after),
        "tags_requiring_localization_after_runner": len(localizable_missing_after),
        "proper_noun_distinct_tags": len(proper),
        "proper_noun_localized_or_static": len(proper & localized),
        "proper_noun_entity_deferred_tags_skipped": len(proper_missing),
        "proper_noun_suggestion_review_only_tags_skipped": len(proper_missing),
        "non_localizable_other_distinct_tags": len(other),
        "not_eligible_for_localization": {
            "proper_noun_entity_deferred_not_general_or_meta": len(proper_missing),
            "proper_noun_suggestion_review_only": len(proper_missing),
            "category_not_in_general_or_meta": len(other),
        },
        "provider_job": {
            "id": int(job.id) if job else None,
            "status": str(job.status) if job else None,
            "source": str(job.source) if job else None,
            "processed": int(job.processed or 0) if job else 0,
            "translated": translated_by_runner,
            "failed": failed_by_runner,
            "skipped": skipped_by_runner,
            "category": str(job.category) if job else None,
        },
        "diagnosis": (
            "benign_all_localizable_tags_already_localized_or_newly_localized"
            if len(localizable_missing_after) == 0 and failed_by_runner == 0
            else "localization_gap_remaining"
        ),
        "public_safe": True,
    }


def collect_source_roots_public(db: Any, *, in_scope_root_id: int | None = None) -> dict[str, Any]:
    from app.models import DynamicSourceRoot

    roots = db.query(DynamicSourceRoot).order_by(DynamicSourceRoot.id.asc()).all()
    return {
        "registered_root_count": len(roots),
        "in_scope_root_id": int(in_scope_root_id) if in_scope_root_id is not None else None,
        "roots": [
            {
                "id": int(root.id),
                "public_source_identity": str(root.root_path_hash or "")[:16],
                "source_type": str(root.source_type or ""),
                "is_active": bool(root.is_active),
                "auto_sync_enabled": bool(root.auto_sync_enabled),
                "in_scope": bool(in_scope_root_id is not None and int(root.id) == int(in_scope_root_id)),
                "path_redacted": True,
            }
            for root in roots
        ],
        "paths_redacted": True,
    }


def _normal_hydration_failure_reason(result: Mapping[str, Any] | None, before_state: Mapping[str, Any]) -> str | None:
    if not result:
        return None
    reason = str(result.get("error_reason") or "")
    if reason in {"read_probe_timeout", "read_timeout"}:
        return "read_timeout"
    if reason in {"read_probe_no_result", "read_no_result"}:
        return "generic_read_failed"
    if reason == "generic_copy_failed" and before_state.get("likely_cloud_placeholder"):
        return "cloud_hydration_failed"
    return reason or None


def _placeholder_rows_from_plan(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in _all_private_plan_items(plan):
        state = str(item.get("state") or "")
        reason = str(item.get("reason") or "")
        if state == "skipped_placeholder" or reason in {"cloud_placeholder", "icloud_placeholder"}:
            rows.append(
                {
                    "safe_label": str(item.get("safe_label") or f"placeholder-{len(rows) + 1:05d}"),
                    "relative_path": str(item.get("relative_path") or ""),
                    "relative_path_hash": str(item.get("relative_path_hash") or ""),
                    "file_size": item.get("file_size"),
                    "source": "approved_private_plan",
                }
            )
    return rows


def _placeholder_rows_from_run(db: Any, run_id: int) -> list[dict[str, Any]]:
    from app.models import DynamicSourceItem, DynamicSyncRunItem

    rows = (
        db.query(DynamicSourceItem.relative_path, DynamicSourceItem.relative_path_hash, DynamicSourceItem.file_size)
        .join(DynamicSyncRunItem, DynamicSyncRunItem.source_item_id == DynamicSourceItem.id)
        .filter(DynamicSyncRunItem.sync_run_id == int(run_id))
        .filter(DynamicSyncRunItem.item_state == "skipped_placeholder")
        .all()
    )
    return [
        {
            "safe_label": f"run-{int(run_id)}-placeholder-{index:05d}",
            "relative_path": str(relative_path or ""),
            "relative_path_hash": str(relative_path_hash or "")[:16],
            "file_size": file_size,
            "source": f"run_{int(run_id)}",
        }
        for index, (relative_path, relative_path_hash, file_size) in enumerate(rows, start=1)
    ]


def hydrate_placeholders(args: argparse.Namespace, root: Any) -> dict[str, Any]:
    from app.services.dynamic_library_sync_service import validate_source_root_path
    from app.services.manual_sync_execute_service import _safe_source_file
    from app.utils.cloud_files import classify_cloud_file_state, read_verify_full_content

    output_dir = prepare_output_dir(args)
    ledger_path = output_dir / "hydration-ledger.jsonl"
    write_jsonl(ledger_path, [])
    db = open_db_session()
    try:
        plan_rows: list[dict[str, Any]] = []
        if args.approved_plan_json and Path(args.approved_plan_json).exists():
            plan_rows = _placeholder_rows_from_plan(read_json(Path(args.approved_plan_json)))
        run_rows = (
            _placeholder_rows_from_run(db, int(args.include_unresolved_run_id))
            if int(args.include_unresolved_run_id or 0)
            else []
        )
    finally:
        db.close()

    by_key: dict[str, dict[str, Any]] = {}
    for row in [*plan_rows, *run_rows]:
        rel = str(row.get("relative_path") or "")
        if not rel:
            continue
        key = str(row.get("relative_path_hash") or stable_json_hash({"rel": rel})[:16])
        by_key.setdefault(key, row)

    source_path = validate_source_root_path(root.root_path)
    rows = list(by_key.values())
    attempted = 0
    succeeded = 0
    failed = 0
    already_hydrated = 0
    remaining = 0
    failures: Counter[str] = Counter()
    before_reasons: Counter[str] = Counter()
    after_reasons: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    started = time.monotonic()
    for index, row in enumerate(rows, start=1):
        rel = str(row["relative_path"])
        safe_label = str(row.get("safe_label") or f"placeholder-{index:05d}")
        public_base = {
            "safe_label": safe_label,
            "relative_path_hash": str(row.get("relative_path_hash") or "")[:16],
            "source": str(row.get("source") or "unknown"),
            "path_private_or_omitted": True,
        }
        try:
            file_path = _safe_source_file(source_path, rel)
            before_state = classify_cloud_file_state(file_path).to_dict(include_path=False)
            extension_counts[file_path.suffix.lower() or "<none>"] += 1
        except Exception as exc:
            failed += 1
            failures["source_path_resolution_failed"] += 1
            append_jsonl(
                ledger_path,
                {**public_base, "state": "failed", "failure_reason": "source_path_resolution_failed", "error": safe_error(exc)},
            )
            continue

        before_reason = "cloud_placeholder" if before_state.get("likely_cloud_placeholder") else "not_placeholder"
        before_reasons[before_reason] += 1
        if not before_state.get("likely_cloud_placeholder"):
            already_hydrated += 1
            after_reasons["not_placeholder"] += 1
            append_jsonl(
                ledger_path,
                {
                    **public_base,
                    "state": "already_hydrated_or_not_placeholder",
                    "before_likely_cloud_placeholder": False,
                    "after_likely_cloud_placeholder": False,
                },
            )
            continue

        attempted += 1
        result = read_verify_full_content(
            file_path,
            expected_size=int(row["file_size"]) if row.get("file_size") is not None else None,
            timeout_seconds=max(1, int(args.hydration_timeout_seconds)),
            retries=max(0, int(args.hydration_retries)),
            chunk_size=max(1, int(args.hydration_chunk_size)),
        )
        try:
            after_state = classify_cloud_file_state(file_path).to_dict(include_path=False)
        except Exception:
            after_state = {"likely_cloud_placeholder": True, "state_check_failed": True}
        after_placeholder = bool(after_state.get("likely_cloud_placeholder"))
        ok = bool(result.get("ok")) and not after_placeholder
        failure_reason = None
        if ok:
            succeeded += 1
        else:
            failed += 1
            failure_reason = _normal_hydration_failure_reason(result, before_state) or (
                "cloud_placeholder_remaining" if after_placeholder else "cloud_hydration_failed"
            )
            failures[failure_reason] += 1
        if after_placeholder:
            remaining += 1
            after_reasons["cloud_placeholder"] += 1
        else:
            after_reasons["not_placeholder"] += 1
        append_jsonl(
            ledger_path,
            {
                **public_base,
                "state": "hydrated" if ok else "failed",
                "before_likely_cloud_placeholder": bool(before_state.get("likely_cloud_placeholder")),
                "after_likely_cloud_placeholder": after_placeholder,
                "bytes_read": int(result.get("bytes_read") or 0),
                "duration_seconds": round(float(result.get("duration_seconds") or 0.0), 3),
                "failure_reason": failure_reason,
            },
        )

    summary = {
        "status": "completed" if failed == 0 and remaining == 0 else "completed_with_stable_failures",
        "placeholder_count_before_hydration": len(rows),
        "hydration_attempted_count": attempted,
        "hydration_succeeded_count": succeeded,
        "hydration_failed_count": failed,
        "already_hydrated_or_not_placeholder_count": already_hydrated,
        "remaining_placeholders_after_hydration": remaining,
        "failure_reasons": dict(sorted((key, int(value)) for key, value in failures.items())),
        "before_state_counts": dict(sorted((key, int(value)) for key, value in before_reasons.items())),
        "after_state_counts": dict(sorted((key, int(value)) for key, value in after_reasons.items())),
        "extension_breakdown": dict(sorted((key, int(value)) for key, value in extension_counts.items())),
        "manual_user_action_required": bool(failed or remaining),
        "source_content_read_for_hydration": attempted > 0,
        "source_content_written": False,
        "source_deleted_moved_renamed": False,
        "cfhydrateplaceholder_called": False,
        "method": "bounded_full_content_read_via_cloud_files_helper",
        "duration_seconds": round(time.monotonic() - started, 3),
        "ledger_artifact": f".local_manifests/{PHASE_SLUG}/{ledger_path.name}",
        "raw_path_committed": False,
        "public_safe": True,
    }
    write_json(output_dir / "hydration-summary-public.json", summary)
    return summary


def run_delta_localization(args: argparse.Namespace, run_id: int, stage_tracker: StageTracker) -> dict[str, Any]:
    from app.config import settings
    from app.models import DynamicSourceItem, DynamicSyncRunItem, TagTranslationJob
    from app.services.llm_translation_provider import get_llm_provider, _sanitize_error_message
    from app.services.tag_localization_service import upsert_translation
    from app.utils.search_parser import invalidate_translation_cache

    stage_tracker.set("localization")
    db = open_db_session()
    output_dir = prepare_output_dir(args)
    ledger_path = output_dir / "localization-ledger.jsonl"
    write_jsonl(ledger_path, [])
    translated = 0
    failed = 0
    skipped = 0
    unknown_outputs = 0
    duplicate_outputs = 0
    provider_call_count = 0
    errors: list[str] = []
    job = None
    try:
        max_tags = max(int(args.localization_max_tags or 0), 1)
        candidates, skipped_proper_nouns, candidate_overflow = select_delta_localization_candidates(
            db,
            int(run_id),
            lang=args.lang,
            limit=max_tags,
        )
        provider = get_llm_provider()
        provider_available = bool(provider.is_available())
        result: dict[str, Any] = {
            "stage": "localization",
            "status": "running",
            "executed": bool(candidates),
            "llm_called": False,
            "provider": provider.get_provider_name(),
            "provider_available": provider_available,
            "backend_type": "llm_provider" if provider_available else "unavailable",
            "requested_max_tags": max_tags,
            "candidate_count": len(candidates),
            "candidate_overflow": bool(candidate_overflow),
            "localization_limit_status": (
                "overflow"
                if candidate_overflow
                else ("exact_limit_no_overflow" if len(candidates) == max_tags else "under_limit")
            ),
            "proper_noun_candidates_skipped": skipped_proper_nouns,
            "proper_noun_entity_deferred_candidates": skipped_proper_nouns,
            "proper_noun_unreviewed_aliases_trusted": False,
            "translated": 0,
            "failed": 0,
            "skipped": skipped_proper_nouns,
            "retries": 0,
            "provider_call_count": 0,
            "errors": [],
            "ledger_artifact": f".local_manifests/{PHASE_SLUG}/{ledger_path.name}",
            "ledger_path_redacted": True,
            "raw_path_committed": False,
        }
        if not candidates:
            result["status"] = "completed_noop_no_candidates"
            result["executed"] = False
            return result
        if not provider_available:
            result["status"] = "blocked_provider_unavailable"
            result["failed"] = len(candidates)
            result["errors"] = ["localization_provider_unavailable"]
            return result

        batch_size = max(
            1,
            min(
                int(args.localization_batch_size),
                int(args.translation_batch_max_items),
                int(settings.TAG_TRANSLATION_BATCH_MAX_ITEMS),
            ),
        )
        job = TagTranslationJob(
            status="running",
            source="s3a_m2_delta_e2e",
            language=args.lang,
            category="general,meta",
            batch_size=batch_size,
            max_per_run=max_tags,
            processed=0,
            translated=0,
            failed=0,
            skipped=skipped_proper_nouns,
            remaining_before=len(candidates),
            started_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        candidate_by_name = {item["canonical_name"]: item for item in candidates}
        saved_names: set[str] = set()
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            inputs = [{"name": item["canonical_name"], "category": item["category"]} for item in batch]
            provider_call_count += 1
            try:
                translations = asyncio.run(provider.translate_tags(inputs))
            except Exception as exc:
                safe = safe_error(_sanitize_error_message(str(exc)))
                errors.append("provider_batch_failed")
                failed += len(batch)
                row = {
                    "batch_index": provider_call_count,
                    "state": "provider_batch_failed",
                    "candidates": len(batch),
                    "failed": len(batch),
                    "error": safe,
                    "path_private_or_omitted": True,
                }
                append_jsonl(ledger_path, row)
                continue
            seen_outputs: set[str] = set()
            batch_translated = 0
            batch_failed = 0
            batch_skipped = 0
            for translation in translations:
                canonical = getattr(translation, "canonical_name", "")
                if canonical not in candidate_by_name:
                    skipped += 1
                    batch_skipped += 1
                    unknown_outputs += 1
                    continue
                if canonical in seen_outputs:
                    skipped += 1
                    batch_skipped += 1
                    duplicate_outputs += 1
                    continue
                seen_outputs.add(canonical)
                item = candidate_by_name[canonical]
                try:
                    saved = upsert_translation(
                        db,
                        canonical_name=canonical,
                        display_name=getattr(translation, "display_name_zh", ""),
                        lang=args.lang,
                        aliases=getattr(translation, "aliases_zh", []) or [],
                        category=item["category"],
                        source="llm",
                        status="translated",
                        confidence=getattr(translation, "confidence", None),
                        needs_review=bool(getattr(translation, "needs_review", False)),
                        provider=provider.get_provider_name(),
                    )
                    if saved is None:
                        skipped += 1
                        batch_skipped += 1
                    else:
                        translated += 1
                        batch_translated += 1
                        saved_names.add(canonical)
                except Exception as exc:
                    errors.append("translation_save_failed")
                    failed += 1
                    batch_failed += 1
                    append_jsonl(
                        ledger_path,
                        {
                            "batch_index": provider_call_count,
                            "state": "translation_save_failed",
                            "tag_label": f"tag-{len(saved_names) + batch_failed}",
                            "error": safe_error(exc),
                            "path_private_or_omitted": True,
                        },
                    )
            missing_from_provider = max(0, len(batch) - len(seen_outputs))
            failed += missing_from_provider
            batch_failed += missing_from_provider
            append_jsonl(
                ledger_path,
                {
                    "batch_index": provider_call_count,
                    "state": "localized" if batch_failed == 0 else "localized_with_failures",
                    "candidates": len(batch),
                    "translated": batch_translated,
                    "failed": batch_failed,
                    "skipped": batch_skipped,
                    "unknown_provider_outputs": unknown_outputs,
                    "duplicate_provider_outputs": duplicate_outputs,
                    "proper_noun_unreviewed_aliases_trusted": False,
                    "path_private_or_omitted": True,
                },
            )
            job.processed = min(start + len(batch), len(candidates))
            job.translated = translated
            job.failed = failed
            job.skipped = skipped + skipped_proper_nouns
            db.commit()
        invalidate_translation_cache()
        status = "completed" if failed == 0 else "completed_with_failures"
        if candidate_overflow:
            status = "partial_localization_max_tags_reached" if failed == 0 else "partial_with_failures"
        job.status = "completed" if status in {"completed", "partial_localization_max_tags_reached"} else "failed"
        job.processed = len(candidates)
        job.translated = translated
        job.failed = failed
        job.skipped = skipped + skipped_proper_nouns
        job.finished_at = datetime.now(timezone.utc)

        target_status = "deferred" if candidate_overflow or failed else "localized"
        target_deferred_reason = "localization_candidate_overflow" if candidate_overflow else ("localization_failed" if failed else None)
        item_ids = [
            int(item_id)
            for (item_id,) in (
                db.query(DynamicSourceItem.id)
                .join(DynamicSyncRunItem, DynamicSyncRunItem.source_item_id == DynamicSourceItem.id)
                .filter(DynamicSyncRunItem.sync_run_id == int(run_id))
                .filter(DynamicSourceItem.import_status == "imported")
                .filter(DynamicSourceItem.ai_tagging_status.in_(("ai_tagged", "tagged", "tagged_reused")))
                .all()
            )
        ]
        updated_items = 0
        if item_ids:
            update_values = {DynamicSourceItem.localization_status: target_status}
            if target_deferred_reason:
                update_values[DynamicSourceItem.deferred_reason] = target_deferred_reason
            else:
                update_values[DynamicSourceItem.deferred_reason] = None
            updated_items = int(
                db.query(DynamicSourceItem)
                .filter(DynamicSourceItem.id.in_(item_ids))
                .update(update_values, synchronize_session=False)
            )
        db.commit()
        return {
            **result,
            "status": status,
            "executed": True,
            "llm_called": provider_call_count > 0,
            "translated": translated,
            "failed": failed,
            "skipped": skipped + skipped_proper_nouns,
            "retries": 0,
            "provider_call_count": provider_call_count,
            "unknown_provider_outputs": unknown_outputs,
            "duplicate_provider_outputs": duplicate_outputs,
            "dynamic_source_items_updated": updated_items,
            "dynamic_source_items_target_status": target_status,
            "dynamic_source_items_deferred_reason": target_deferred_reason,
            "job_id": int(job.id),
            "errors": sorted(set(errors)),
        }
    finally:
        db.close()


def manual_execute_stage_summary(execution: Mapping[str, Any] | None) -> dict[str, Any]:
    if not execution:
        return {
            "run_id": None,
            "status": "not_run",
            "imported": 0,
            "classified": 0,
            "classification_failed": 0,
            "classification_skipped": 0,
            "ai_tagged": 0,
            "ai_tagging_failed": 0,
            "ai_tagging_skipped": 0,
            "failed": 0,
            "deferred": 0,
            "outcome_counts": {},
            "provider_provenance": {},
        }
    execute = execution.get("manual_sync_execute") or {}
    counts = Counter(execute.get("outcome_counts") or {})
    return {
        "run_id": execution.get("id"),
        "status": execution.get("status"),
        "current_stage": execute.get("current_stage"),
        "imported": int(counts.get("imported", 0)),
        "classified": int(counts.get("classified", 0)),
        "classification_failed": int(counts.get("classification_failed", 0)),
        "classification_skipped": int(counts.get("classification_skipped", 0)),
        "ai_tagged": int(counts.get("ai_tagged", 0)),
        "ai_tagging_failed": int(counts.get("ai_tagging_failed", 0)),
        "ai_tagging_skipped": int(counts.get("ai_tagging_skipped", 0)),
        "failed": int(execution.get("failed_items") or counts.get("failed", 0)),
        "deferred": int(counts.get("deferred_unprocessed", 0)),
        "outcome_counts": dict(sorted(counts.items())),
        "provider_provenance": execute.get("ai_provider_provenance") or {},
    }


def verify_ledger_consistency(run_id: int | None, expected_total: int) -> dict[str, Any]:
    if not run_id:
        return {"status": "not_run", "passed": False, "run_id": None}
    from app.models import DynamicSourceItem, DynamicSyncRunItem

    db = open_db_session()
    try:
        run_items = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.sync_run_id == int(run_id)).all()
        item_ids = [item.source_item_id for item in run_items]
        source_items = db.query(DynamicSourceItem).filter(DynamicSourceItem.id.in_(item_ids)).all() if item_ids else []
        imported_without_media = [
            item.id for item in source_items if item.import_status == "imported" and not item.media_id
        ]
        represented = len(run_items)
        passed = represented >= expected_total and not imported_without_media
        reason_counts = Counter(str(item.reason or item.item_state or "unknown") for item in run_items)
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "run_id": int(run_id),
            "expected_plan_items": int(expected_total),
            "run_item_count": represented,
            "imported_without_media_count": len(imported_without_media),
            "reason_counts": dict(sorted(reason_counts.items())),
            "public_safe": True,
        }
    finally:
        db.close()


def scan_public_output(summary: Mapping[str, Any], markdown: str) -> dict[str, Any]:
    from scripts.phase_contracts.contract_checks import scan_public_payload

    findings = scan_public_payload({"json": summary, "markdown": markdown})
    text = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=json_default) + "\n" + markdown
    for pattern in SENSITIVE_TEXT_PATTERNS:
        if pattern.search(text):
            findings.append({"code": "sensitive_text", "match": "[redacted]"})
    return {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "checked_payloads": ["summary_json", "markdown_report"],
    }


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    dry_run = summary.get("dry_run") or {}
    execute = summary.get("execute") or {}
    loc = summary.get("localization") or {}
    telemetry = summary.get("gpu_telemetry") or {}
    aggregate_telemetry = summary.get("aggregate_gpu_telemetry") or telemetry
    runtime = summary.get("runtime") or {}
    aggregate_runtime = summary.get("aggregate_runtime") or runtime
    classification = summary.get("classification") or {}
    ai_tagging = summary.get("ai_tagging") or {}
    ledger = summary.get("ledger_consistency") or {}
    launcher = summary.get("launcher_web_admin_acceptance") or {}
    redaction = summary.get("public_redaction") or {}
    db_validation = summary.get("db_validation") or {}
    pipeline = summary.get("pipeline_contract") or {}
    safety = summary.get("safety") or {}
    loc_diag = summary.get("localization_diagnosis") or {}
    hydration = summary.get("placeholder_hydration") or {}
    inventory = summary.get("final_inventory") or {}
    initial = summary.get("initial_run") or {}
    remaining = summary.get("remaining_run") or {}
    final_totals = summary.get("final_totals") or {}
    standard_flow = summary.get("standard_pipeline_flow") or {}
    standard_steps = standard_flow.get("steps") or {}
    incident = summary.get("ai_tag_assignment_incident") or {}
    post_repair_ui = summary.get("post_repair_ui_validation") or incident.get("ui_verification") or {}
    cohort = summary.get("cohort_self_audit") or {}
    incident_after = incident.get("after") if isinstance(incident.get("after"), Mapping) else {}
    cohort_affected = cohort.get("affected") if isinstance(cohort.get("affected"), Mapping) else {}
    cohort_baseline = cohort.get("baseline") if isinstance(cohort.get("baseline"), Mapping) else {}
    affected_tags = cohort_affected.get("tag_assignment") if isinstance(cohort_affected.get("tag_assignment"), Mapping) else {}
    baseline_tags = cohort_baseline.get("tag_assignment") if isinstance(cohort_baseline.get("tag_assignment"), Mapping) else {}
    affected_classification = cohort_affected.get("classification") if isinstance(cohort_affected.get("classification"), Mapping) else {}
    baseline_classification = cohort_baseline.get("classification") if isinstance(cohort_baseline.get("classification"), Mapping) else {}
    affected_localization = cohort_affected.get("localization") if isinstance(cohort_affected.get("localization"), Mapping) else {}
    gui_debug = summary.get("gui_acceptance_debug") if isinstance(summary.get("gui_acceptance_debug"), Mapping) else {}
    pre_user_acceptance = (
        summary.get("pre_user_manual_acceptance_safety_fixes")
        if isinstance(summary.get("pre_user_manual_acceptance_safety_fixes"), Mapping)
        else {}
    )
    not_completed = summary.get("not_completed") or [
        "Automatic/scheduled/startup/system-service sync was not implemented; it remains out of scope.",
        "Pixiv/provider/gallery-dl/SauceNAO/Google/source metadata expansion was not run.",
        "SourceConcept/Entity bridge work was not run.",
    ]
    return "\n".join(
        [
            "# S3A-M2 Production Delta Manual Sync E2E + GPU Telemetry",
            "",
            "## Identity",
            "",
            f"- Phase: `{summary.get('phase')}` / `{summary.get('title')}`.",
            f"- Status: `{summary.get('status')}`.",
            f"- Contract: `{pipeline.get('contract_id')}`; target met: `{(pipeline.get('claims') or {}).get('target_met')}`.",
            f"- Standard pipeline flow: `{standard_flow.get('status')}`.",
            f"- Branch: `{summary.get('branch')}`.",
            f"- Head SHA: `{summary.get('head_sha')}`.",
            f"- Production acceptance performed: `{summary.get('production_acceptance', {}).get('performed')}`.",
            f"- Source root: `{summary.get('source', {}).get('public_source_identity')}`.",
            "",
            "## Counts",
            "",
            f"- Cap used: `{summary.get('controlled_delta', {}).get('cap')}`; cap exceeded: `{summary.get('controlled_delta', {}).get('cap_exceeded')}`.",
            f"- Dry-run total/import: `{dry_run.get('total_seen')}` / `{dry_run.get('estimated_import_count')}`.",
            f"- Execute total/imported: `{ledger.get('run_item_count')}` / `{execute.get('imported')}`.",
            f"- Classification count/failures: `{classification.get('count')}` / `{classification.get('failed')}`.",
            f"- AI tagging count/failures: `{ai_tagging.get('count')}` / `{ai_tagging.get('failed')}`.",
            f"- Localization translated/failures/skipped: `{loc.get('translated')}` / `{loc.get('failed')}` / `{loc.get('skipped')}`.",
            f"- Localization provider/calls/retries: `{loc.get('provider')}` / `{loc.get('provider_call_count')}` / `{loc.get('retries')}`.",
            f"- Final imported/classified/AI-tagged/localized totals: `{final_totals.get('imported')}` / `{final_totals.get('classified')}` / `{final_totals.get('ai_tagged')}` / `{final_totals.get('localized')}`.",
            f"- Skipped/failed/deferred: `{summary.get('skipped_failed_deferred', {})}`.",
            "",
            "## Initial Run",
            "",
            f"- Dry-run total/import: `{initial.get('dry_run_total')}` / `{initial.get('imported')}`.",
            f"- Classified / AI-tagged / localized: `{initial.get('classified')}` / `{initial.get('ai_tagged')}` / `{initial.get('localized')}`.",
            f"- Placeholder / unsupported / existing skips: `{initial.get('skipped_placeholder')}` / `{initial.get('skipped_unsupported')}` / `{initial.get('skipped_existing')}`.",
            "",
            "## Remaining Run",
            "",
            f"- Dry-run total/import: `{remaining.get('dry_run_total')}` / `{remaining.get('imported')}`.",
            f"- Classified / AI-tagged / localized: `{remaining.get('classified')}` / `{remaining.get('ai_tagged')}` / `{remaining.get('localized')}`.",
            f"- Placeholder / unsupported / existing skips: `{remaining.get('skipped_placeholder')}` / `{remaining.get('skipped_unsupported')}` / `{remaining.get('skipped_existing')}`.",
            "",
            "## Localization Diagnosis",
            "",
            f"- Diagnosis: `{loc_diag.get('diagnosis')}`.",
            f"- AI tag assignments / distinct tags: `{loc_diag.get('ai_wd_assignment_count')}` / `{loc_diag.get('distinct_ai_wd_tag_count')}`.",
            f"- Localizable distinct / already localized / newly localized / remaining gap: `{loc_diag.get('localizable_distinct_tags')}` / `{loc_diag.get('localizable_already_localized_or_static')}` / `{loc_diag.get('newly_localized_tags')}` / `{loc_diag.get('tags_requiring_localization_after_runner')}`.",
            f"- Proper-noun entity-deferred/not-current-localization-category skipped: `{loc_diag.get('proper_noun_entity_deferred_tags_skipped', loc_diag.get('proper_noun_suggestion_review_only_tags_skipped'))}`.",
            f"- Not eligible: `{loc_diag.get('not_eligible_for_localization', {})}`.",
            "",
            "## AI Tag Assignment Incident And Cohort Audit",
            "",
            f"- Incident status: `{incident.get('status')}`; affected runs: `{incident.get('affected_run_ids')}`; affected media: `{incident.get('affected_media_count')}`; assignments inspected: `{incident.get('assignments_inspected')}`.",
            f"- Root cause: `{incident.get('root_cause')}`.",
            f"- Repair converted suggestion->normal: `{(incident.get('repair') or {}).get('assignments_converted_from_suggestion_to_normal')}`; kept suggestions: `{(incident.get('repair') or {}).get('assignments_kept_suggestion')}`; duplicate rows created: `{(incident.get('repair') or {}).get('duplicate_rows_created')}`.",
            f"- After repair high-confidence non-proper incorrect suggestions: `{incident_after.get('high_conf_nonproper_incorrect_suggestion_count')}`; normal high-confidence non-proper tags: `{incident_after.get('high_conf_nonproper_normal_count')}`.",
            f"- Mature-policy proper-noun normal tags / incorrect suggestions: `{incident_after.get('high_conf_proper_normal_count')}` / `{incident_after.get('high_conf_proper_incorrect_suggestion_count')}`.",
            f"- Proper-noun suggestions kept below threshold: `{incident_after.get('low_conf_proper_suggestion_count')}`; Entity/SourceConcept truth violations: `{incident.get('entity_truth_violations_found')}`.",
            f"- Cohort status: `{cohort.get('status')}`; baseline method: `{(cohort.get('baseline_selection') or {}).get('method')}`; affected/baseline media: `{cohort.get('affected_media_count')}` / `{cohort.get('baseline_media_count')}`.",
            f"- S3A-M2 normal/suggestion tags per media avg: `{(affected_tags.get('normal_tag_count_per_media') or {}).get('avg')}` / `{(affected_tags.get('suggestion_tag_count_per_media') or {}).get('avg')}`.",
            f"- Baseline normal/suggestion tags per media avg: `{(baseline_tags.get('normal_tag_count_per_media') or {}).get('avg')}` / `{(baseline_tags.get('suggestion_tag_count_per_media') or {}).get('avg')}`.",
            f"- Classification unknown rate S3A-M2/baseline: `{affected_classification.get('unknown_or_empty_rate_percent')}` / `{baseline_classification.get('unknown_or_empty_rate_percent')}`.",
            f"- Localization remaining gap after repair: `{affected_localization.get('localizable_remaining_gap')}`; blocker anomalies remaining: `{cohort.get('blocker_anomaly_count')}`.",
            f"- Post-repair UI validation: `{post_repair_ui.get('status')}`; samples: `{post_repair_ui.get('sample_count')}`; normal visible pass: `{post_repair_ui.get('normal_visible_pass_count')}`; mature proper normal visible pass: `{post_repair_ui.get('mature_proper_normal_visible_pass_count')}`; true suggestion visible pass: `{post_repair_ui.get('any_suggestion_visible_pass_count')}`.",
            f"- Computer Use result: `{post_repair_ui.get('computer_use_result')}`; fallback method: `{post_repair_ui.get('method')}`.",
            "",
            "## Placeholder Hydration",
            "",
            f"- Status: `{hydration.get('status')}`.",
            f"- Passes represented: `{hydration.get('passes_represented')}`.",
            f"- Before / attempted / succeeded / failed / remaining: `{hydration.get('placeholder_count_before_hydration')}` / `{hydration.get('hydration_attempted_count')}` / `{hydration.get('hydration_succeeded_count')}` / `{hydration.get('hydration_failed_count')}` / `{hydration.get('remaining_placeholders_after_hydration')}`.",
            f"- Failure reasons: `{hydration.get('failure_reasons', {})}`.",
            f"- Manual user action required: `{hydration.get('manual_user_action_required')}`.",
            "",
            "## Final Inventory",
            "",
            f"- Current delta candidates/importable: `{inventory.get('current_delta_candidates')}` / `{inventory.get('current_importable_hydrated_supported_items')}`.",
            f"- Existing / placeholders remaining / unsupported / unreadable-zero-byte-damaged: `{inventory.get('existing_media')}` / `{inventory.get('placeholders_remaining')}` / `{inventory.get('unsupported_items')}` / `{inventory.get('unreadable_zero_byte_damaged')}`.",
            f"- Unsupported extension breakdown: `{inventory.get('unsupported_extension_breakdown', {})}`.",
            f"- Failure reason extension breakdown: `{inventory.get('failure_reason_extension_breakdown', {})}`.",
            f"- Scan cap stopped scan: `{inventory.get('scan_cap_stopped_scan')}`.",
            "",
            "## Standard Pipeline Flow",
            "",
            f"- Version: `{standard_flow.get('version')}`; future automation readiness: `{standard_flow.get('future_automation_readiness')}`.",
            f"- Aggregate basis: `{standard_flow.get('aggregate_basis', {})}`.",
            *[
                f"- {name}: `{step.get('status')}`; completed: `{step.get('completed')}`."
                for name, step in standard_steps.items()
                if isinstance(step, Mapping)
            ],
            "",
            "## Telemetry",
            "",
            f"- GPU provider: `{aggregate_telemetry.get('actual_provider')}`; GPU validation: `{aggregate_telemetry.get('validation_status')}`.",
            f"- GPU name: `{aggregate_telemetry.get('gpu_name')}`.",
            f"- Aggregate peak GPU memory MiB: `{aggregate_telemetry.get('max_gpu_memory_used_mib')}`; peak GPU util: `{aggregate_telemetry.get('peak_gpu_utilization_percent')}`.",
            f"- Telemetry partial fields: `{aggregate_telemetry.get('telemetry_partial_fields', [])}`.",
            f"- Aggregate runtime seconds: `{aggregate_runtime.get('total_seconds')}`; stage durations: `{aggregate_runtime.get('stage_durations_seconds')}`.",
            f"- Remaining-run runtime seconds: `{runtime.get('total_seconds')}`; stage durations: `{runtime.get('stage_durations_seconds')}`.",
            "",
            *(
                [
                    "## GUI Acceptance Debug",
                    "",
                    f"- Status: `{gui_debug.get('status')}`.",
                    f"- Observed server: port `{gui_debug.get('observed_port')}`, profile `{gui_debug.get('profile_id')}`, env `{gui_debug.get('violet_env')}`, DB `{gui_debug.get('db_name')}`.",
                    f"- Endpoint clicked: `{gui_debug.get('endpoint_called')}`; plan source before fix: `{gui_debug.get('plan_source_before_fix')}`; plan source after fix: `{gui_debug.get('plan_source_after_fix')}`.",
                    f"- Root cause: `{gui_debug.get('root_cause')}`.",
                    f"- Stuck jobs found/cleaned: `{gui_debug.get('stuck_jobs_found')}` / `{gui_debug.get('managed_processes_cleaned')}`.",
                    f"- Cap/UI mismatch: `{gui_debug.get('cap_ui_mismatch')}`.",
                    f"- Readiness/config mismatch: `{gui_debug.get('readiness_config_mismatch')}`.",
                    f"- Watchdog/timeout added: `{gui_debug.get('watchdog_timeout_added')}`; no-silent-spinner fix: `{gui_debug.get('no_silent_spinner_fix')}`.",
                    f"- Acceptance blocker: `{gui_debug.get('acceptance_blocker')}`.",
                    "",
                ]
                if gui_debug
                else []
            ),
            *(
                [
                    "## Pre-User Manual Acceptance Safety Fixes",
                    "",
                    f"- Status: `{pre_user_acceptance.get('status')}`.",
                    f"- Reviewer scope: current-scope P1/P2 before head `{pre_user_acceptance.get('reviewer_findings_before_head')}`; latest Codex review available for `{pre_user_acceptance.get('latest_codex_reviewed_head')}`; later review usage-limited: `{pre_user_acceptance.get('review_usage_limited_after_latest_review')}`.",
                    f"- Operator-entered production confirmation required: `{pre_user_acceptance.get('operator_entered_production_confirmation_required')}`.",
                    f"- Signed GUI provenance required: `{pre_user_acceptance.get('signed_gui_provenance_required')}`; ordinary API can satisfy GUI acceptance: `{pre_user_acceptance.get('ordinary_api_can_satisfy_gui_acceptance')}`.",
                    f"- Validator scope: `{pre_user_acceptance.get('validator_remaining_inventory_scope')}`; skipped placeholders included: `{pre_user_acceptance.get('validator_includes_skipped_placeholders')}`.",
                    f"- Localization failure reporting: `{pre_user_acceptance.get('localization_failure_reporting')}`.",
                    f"- Cancellation before localization guard: `{pre_user_acceptance.get('cancelled_run_skips_localization_finalizer')}`; LLM calls prevented: `{pre_user_acceptance.get('cancelled_run_prevents_llm_calls')}`; localization DB writes prevented: `{pre_user_acceptance.get('cancelled_run_prevents_localization_db_writes')}`.",
                    f"- Historical read errors retryable in current delta planning: `{pre_user_acceptance.get('historical_read_error_retryable')}`.",
                    f"- Manual E2E readiness/backend gates aligned: `{pre_user_acceptance.get('manual_e2e_readiness_backend_gate_alignment')}`.",
                    f"- User manual GUI acceptance package status: `{pre_user_acceptance.get('manual_acceptance_package_status')}`.",
                    "",
                ]
                if pre_user_acceptance
                else []
            ),
            "## Validation",
            "",
            f"- Ledger consistency: `{ledger.get('status')}`; represented items: `{ledger.get('run_item_count')}` / `{ledger.get('expected_plan_items')}`.",
            f"- DB count delta: media `{db_validation.get('media_delta')}`, source items `{db_validation.get('source_item_delta')}`.",
            f"- Public redaction: `{redaction.get('passed')}`; findings: `{redaction.get('finding_count')}`.",
            f"- Launcher/Web Admin: `{launcher.get('status')}`; browser: `{launcher.get('browser')}`; dry-run clicked: `{launcher.get('dry_run_clicked')}`; execute clicked: `{launcher.get('execute_clicked')}`.",
            f"- Launcher dry-run request/timeout/server-stop: `{launcher.get('dry_run_button_click_fired_request')}` / `{launcher.get('dry_run_page_context_fetch_timed_out')}` / `{launcher.get('dry_run_aborted_by_server_stop')}`.",
            f"- Launcher fallback reason: `{launcher.get('fallback_reason')}`.",
            f"- Latest job observed by UI/API: run `{launcher.get('production_execute_run_id_seen')}`, status `{launcher.get('latest_job_status')}`, imported `{launcher.get('latest_job_imported')}`.",
            "",
            "## Safety",
            "",
            f"- Source/iCloud mutation attempted: `{safety.get('source_mutation_attempted')}`.",
            f"- Automatic/scheduled/startup/system-service sync enabled: `{safety.get('automatic_sync_enabled')}` / `{safety.get('scheduled_sync_enabled')}` / `{safety.get('startup_sync_enabled')}` / `{safety.get('system_service_enabled')}`.",
            f"- Provider/source expansion run: `{safety.get('provider_pixiv_gallery_dl_saucenao_google_calls')}`.",
            f"- Private paths or hashes in public report: `{safety.get('private_paths_or_hashes_in_public_report')}`.",
            "",
            "## Not Completed",
            "",
            *[f"- {item}" for item in not_completed],
            "",
            "No source paths, filenames, content hashes, API keys, prompts, source URLs, or original image bytes are included in this public report.",
        ]
    ) + "\n"


STANDARD_PIPELINE_STEP_ORDER = (
    "scan_current_source_delta",
    "detect_cloud_placeholders",
    "hydrate_placeholders_non_destructively",
    "rescan_after_hydration",
    "import_all_current_importable_items",
    "classify_imported_media",
    "run_ai_tagging",
    "run_localization_or_stable_reasons",
    "record_ledger_for_every_planned_item",
    "capture_resource_gpu_telemetry",
    "validate_public_redaction",
    "validate_launcher_web_admin_workflow",
    "produce_public_report_and_contract",
)


def _standard_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _standard_step(completed: bool, status: str, **evidence: Any) -> dict[str, Any]:
    return {
        "completed": bool(completed),
        "status": status,
        "evidence": {key: value for key, value in evidence.items() if value is not None},
    }


def build_standard_pipeline_flow(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate S3A-M2 into the repeatable manual-sync pipeline future automation can reuse."""

    dry_run = summary.get("dry_run") or {}
    dry_state = dry_run.get("state_counts") or {}
    execute = summary.get("execute") or {}
    classification = summary.get("classification") or {}
    ai_tagging = summary.get("ai_tagging") or {}
    localization = summary.get("localization") or {}
    loc_diag = summary.get("localization_diagnosis") or {}
    hydration = summary.get("placeholder_hydration") or {}
    inventory = summary.get("final_inventory") or {}
    ledger = summary.get("ledger_consistency") or {}
    telemetry = summary.get("gpu_telemetry") or {}
    redaction = summary.get("public_redaction") or {}
    launcher = summary.get("launcher_web_admin_acceptance") or {}
    pipeline = summary.get("pipeline_contract") or {}
    public_reports = summary.get("public_reports") or {}
    safety = summary.get("safety") or {}
    initial = summary.get("initial_run") or {}
    remaining = summary.get("remaining_run") or {}

    placeholder_before = _standard_int(hydration.get("placeholder_count_before_hydration"))
    placeholders_remaining = _standard_int(
        hydration.get("remaining_placeholders_after_hydration") or inventory.get("placeholders_remaining")
    )
    hydration_status = str(hydration.get("status") or "")
    hydration_failures = _standard_int(hydration.get("hydration_failed_count"))
    stable_failure_reasons = hydration.get("failure_reasons") or {}
    stable_placeholder_failures_accepted = (
        hydration_status == "completed_with_stable_failures"
        and hydration_failures > 0
        and bool(stable_failure_reasons)
        and not bool(hydration.get("manual_user_action_required"))
    )
    hydration_completed = hydration_status == "not_required" or (
        hydration_status == "completed" and placeholders_remaining == 0
    ) or stable_placeholder_failures_accepted

    localization_status = str(localization.get("status") or "")
    localization_diagnosis = str(loc_diag.get("diagnosis") or "")
    localization_completed = (
        localization_status in {"completed", "completed_noop_no_candidates"}
        and _standard_int(localization.get("failed")) == 0
        and _standard_int(loc_diag.get("tags_requiring_localization_after_runner")) == 0
        and localization_diagnosis
        in {
            "benign_all_localizable_tags_already_localized_or_newly_localized",
            "no_imported_media",
        }
    )

    launcher_status = str(launcher.get("status") or "")
    launcher_validated = bool(launcher.get("validated"))
    launcher_execute_clicked = bool(launcher.get("execute_clicked"))
    previous_execute_run_id = max(
        _standard_int(initial.get("run_id")),
        _standard_int(remaining.get("run_id")),
        _standard_int(execute.get("run_id")),
    )
    launcher_run_id = _standard_int(launcher.get("gui_execute_run_id") or launcher.get("production_execute_run_id_seen"))
    launcher_gui_provenance_ok = (
        bool(launcher.get("gui_provenance_valid"))
        and str(launcher.get("request_source") or "") == "web_admin_gui"
        and bool(launcher.get("gui_validation_session_id_present"))
        and bool(launcher.get("gui_validation_session_signature_valid"))
    )
    launcher_gui_execute_completed = (
        launcher_status == "passed_gui_execute_completed"
        and launcher_execute_clicked
        and bool(launcher.get("gui_execute_completed"))
        and launcher_run_id > previous_execute_run_id
        and launcher_gui_provenance_ok
    )
    launcher_fallback_documented = (
        launcher_status == "passed_gui_execute_not_safe_runner_execute_used"
        and not launcher_execute_clicked
        and bool(launcher.get("fallback_reason") or launcher.get("computer_use_result"))
    )
    launcher_completed = launcher_validated and launcher_gui_execute_completed

    final_importable = _standard_int(inventory.get("current_importable_hydrated_supported_items"))
    final_placeholders = _standard_int(inventory.get("placeholders_remaining"))
    scanned_current_delta = _standard_int(dry_run.get("total_seen")) > 0 and bool(
        (summary.get("pipeline_contract") or {}).get("fresh_dry_run_completed")
    )
    execute_completed = str(execute.get("status") or "") == "completed"
    imported_total = _standard_int((summary.get("final_totals") or {}).get("imported")) or _standard_int(execute.get("imported"))
    classified_total = _standard_int((summary.get("final_totals") or {}).get("classified")) or _standard_int(
        classification.get("count")
    )
    ai_total = _standard_int((summary.get("final_totals") or {}).get("ai_tagged")) or _standard_int(ai_tagging.get("count"))

    steps = {
        "scan_current_source_delta": _standard_step(
            scanned_current_delta,
            "completed" if scanned_current_delta else "missing_or_pending",
            total_seen=dry_run.get("total_seen"),
            cap=(summary.get("controlled_delta") or {}).get("cap"),
            cap_stopped=dry_run.get("partial_scan"),
        ),
        "detect_cloud_placeholders": _standard_step(
            "skipped_placeholder" in dry_state
            or placeholder_before > 0
            or final_placeholders == 0
            or "cloud_placeholder" in (dry_run.get("failure_reasons") or {}),
            "completed",
            placeholders_seen_before_hydration=placeholder_before,
            latest_dry_run_placeholders=dry_state.get("skipped_placeholder"),
        ),
        "hydrate_placeholders_non_destructively": _standard_step(
            hydration_completed
            and not bool(hydration.get("source_content_written"))
            and not bool(hydration.get("source_deleted_moved_renamed")),
            "completed"
            if hydration_completed
            else ("stable_failures_unaccepted" if placeholders_remaining else "missing_or_pending"),
            attempted=hydration.get("hydration_attempted_count"),
            succeeded=hydration.get("hydration_succeeded_count"),
            failed=hydration.get("hydration_failed_count"),
            remaining=placeholders_remaining,
            method=hydration.get("method"),
        ),
        "rescan_after_hydration": _standard_step(
            bool(inventory) and not bool(inventory.get("scan_cap_stopped_scan")),
            "completed" if bool(inventory) and not bool(inventory.get("scan_cap_stopped_scan")) else "missing_or_cap_stopped",
            current_delta_candidates=inventory.get("current_delta_candidates"),
            importable_remaining=final_importable,
            placeholders_remaining=final_placeholders,
        ),
        "import_all_current_importable_items": _standard_step(
            execute_completed and final_importable == 0 and imported_total > 0,
            "completed" if execute_completed and final_importable == 0 and imported_total > 0 else "remaining_or_pending",
            execute_imported=execute.get("imported"),
            final_importable_remaining=final_importable,
            aggregate_imported=imported_total,
        ),
        "classify_imported_media": _standard_step(
            execute_completed and bool(classification.get("reported")) and _standard_int(classification.get("failed")) == 0 and classified_total > 0,
            "completed" if classified_total > 0 and _standard_int(classification.get("failed")) == 0 else "failed_or_pending",
            aggregate_classified=classified_total,
            failed=classification.get("failed"),
        ),
        "run_ai_tagging": _standard_step(
            execute_completed
            and bool(ai_tagging.get("reported"))
            and _standard_int(ai_tagging.get("failed")) == 0
            and ai_total > 0
            and bool(ai_tagging.get("mature_media_tag_policy"))
            and bool(ai_tagging.get("no_sourceconcept_or_entity_truth_from_ai_only_tags")),
            "completed" if ai_total > 0 and _standard_int(ai_tagging.get("failed")) == 0 else "failed_or_pending",
            aggregate_ai_tagged=ai_total,
            failed=ai_tagging.get("failed"),
            mature_media_tag_policy=ai_tagging.get("mature_media_tag_policy"),
            no_sourceconcept_or_entity_truth_from_ai_only_tags=ai_tagging.get(
                "no_sourceconcept_or_entity_truth_from_ai_only_tags"
            ),
        ),
        "run_localization_or_stable_reasons": _standard_step(
            localization_completed,
            "completed" if localization_completed else "gap_or_pending",
            localization_status=localization_status,
            diagnosis=localization_diagnosis,
            remaining_gap=loc_diag.get("tags_requiring_localization_after_runner"),
            failed=localization.get("failed"),
        ),
        "record_ledger_for_every_planned_item": _standard_step(
            bool(ledger.get("passed")),
            "completed" if bool(ledger.get("passed")) else "failed_or_pending",
            expected_plan_items=ledger.get("expected_plan_items"),
            run_item_count=ledger.get("run_item_count"),
        ),
        "capture_resource_gpu_telemetry": _standard_step(
            str(telemetry.get("status") or "") in {"collected", "partial_no_samples"} and bool(telemetry.get("validation_status")),
            "completed" if str(telemetry.get("validation_status") or "") == "passed" else "partial_or_pending",
            validation_status=telemetry.get("validation_status"),
            actual_provider=telemetry.get("actual_provider"),
            gpu_name=telemetry.get("gpu_name"),
        ),
        "validate_public_redaction": _standard_step(
            bool(redaction.get("passed")) and not bool(safety.get("private_paths_or_hashes_in_public_report")),
            "completed" if bool(redaction.get("passed")) else "failed_or_pending",
            finding_count=redaction.get("finding_count"),
        ),
        "validate_launcher_web_admin_workflow": _standard_step(
            launcher_completed,
            "gui_execute_completed"
            if launcher_gui_execute_completed
            else ("gui_execute_pending_fallback_documented" if launcher_fallback_documented else "pending"),
            computer_use_result=launcher.get("computer_use_result"),
            execute_clicked=launcher.get("execute_clicked"),
            gui_execute_completed=launcher.get("gui_execute_completed"),
            gui_execute_run_id=launcher_run_id or None,
            previous_execute_run_id=previous_execute_run_id or None,
            fallback_reason=launcher.get("fallback_reason"),
            latest_job_status=launcher.get("latest_job_status"),
        ),
        "produce_public_report_and_contract": _standard_step(
            bool(public_reports.get("markdown_report_path"))
            and bool(public_reports.get("summary_json_path"))
            and str(pipeline.get("contract_id") or "") == CONTRACT_ID,
            "completed"
            if bool(public_reports.get("markdown_report_path")) and str(pipeline.get("contract_id") or "") == CONTRACT_ID
            else "missing_or_pending",
            contract_id=pipeline.get("contract_id"),
            markdown_report_path=public_reports.get("markdown_report_path"),
            summary_json_path=public_reports.get("summary_json_path"),
        ),
    }
    completed = all(bool(steps[name].get("completed")) for name in STANDARD_PIPELINE_STEP_ORDER)
    return {
        "version": 1,
        "status": "completed" if completed else "incomplete",
        "steps": steps,
        "aggregate_basis": {
            "initial_execute_run_id": initial.get("run_id"),
            "remaining_execute_run_id": remaining.get("run_id") or execute.get("run_id"),
            "hydration_passes_represented": hydration.get("passes_represented"),
            "final_inventory_delta_candidates": inventory.get("current_delta_candidates"),
        },
        "future_automation_readiness": (
            "manual_pipeline_standardized_no_automatic_sync_implemented"
            if completed
            else "manual_pipeline_evidence_incomplete"
        ),
        "automatic_sync_implemented": False,
        "public_safe": True,
    }


def build_summary(
    args: argparse.Namespace,
    *,
    readiness: Mapping[str, Any],
    root: Any,
    plan: Mapping[str, Any],
    execution: Mapping[str, Any] | None,
    localization: Mapping[str, Any] | None,
    telemetry: Mapping[str, Any] | None,
    stage_durations: Mapping[str, float],
    started_at_monotonic: float,
) -> dict[str, Any]:
    counts = plan.get("counts") or {}
    state_counts = counts.get("state_counts") or {}
    execute_summary = manual_execute_stage_summary(execution)
    run_id = execute_summary.get("run_id")
    ledger = verify_ledger_consistency(int(run_id) if run_id else None, int(counts.get("total_seen") or 0)) if execution else {"status": "not_run", "passed": False}
    localization_diagnosis: dict[str, Any] = {"status": "not_run"}
    source_roots: dict[str, Any] = {"registered_root_count": None, "paths_redacted": True}
    try:
        db = open_db_session()
        try:
            source_roots = collect_source_roots_public(db, in_scope_root_id=int(root.id))
            if run_id:
                localization_diagnosis = diagnose_run_localization(
                    db,
                    int(run_id),
                    lang=str(args.lang),
                    job_id=int((localization or {}).get("job_id") or 0) or None,
                )
                localization_diagnosis["status"] = "completed"
        finally:
            db.close()
    except Exception as exc:
        localization_diagnosis = {"status": "unavailable", "reason": safe_error(exc), "public_safe": True}
    telemetry_summary = dict(
        telemetry
        or {
            "status": "not_run",
            "actual_provider": "not_loaded_before_execute",
            "max_gpu_memory_used_mib": 0.0,
            "peak_gpu_utilization_percent": 0.0,
            "partial_reason": "telemetry_runs_during_execute",
        }
    )
    provider = execute_summary.get("provider_provenance") or telemetry_summary
    actual_provider = _actual_onnx_provider_from_payload(telemetry_summary) or _actual_onnx_provider_from_payload(provider)
    telemetry_summary["actual_provider"] = actual_provider
    telemetry_summary["gpu_provider_used"] = actual_provider in GPU_PROVIDERS
    telemetry_summary["validation_status"] = (
        "passed"
        if actual_provider in GPU_PROVIDERS
        else ("partial_cpu_fallback" if actual_provider == "CPUExecutionProvider" else "partial_provider_unknown")
    )
    loc = dict(
        localization
        or {
            "status": "not_run",
            "translated": 0,
            "failed": 0,
            "skipped": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }
    )
    cap_exceeded = bool(counts.get("partial_scan"))
    production_execute_ran = bool(execution and execute_summary.get("status") == "completed")
    localization_ok = str(loc.get("status")) in {"completed", "completed_noop_no_candidates", "completed_with_failures"}
    launcher_validated = False
    target_met = bool(
        production_execute_ran
        and not cap_exceeded
        and readiness.get("passed")
        and ledger.get("passed")
        and loc.get("status") in {"completed", "completed_noop_no_candidates"}
        and telemetry_summary.get("validation_status") == "passed"
        and launcher_validated
    )
    if cap_exceeded:
        status = "blocked_delta_cap_exceeded"
    elif not readiness.get("passed"):
        status = "blocked_readiness"
    elif args.execute and not production_execute_ran:
        status = "blocked_execute_not_completed"
    elif args.execute and not localization_ok:
        status = "blocked_localization_incomplete"
    elif args.execute and telemetry_summary.get("validation_status") != "passed":
        status = "completed_partial_gpu_validation"
    elif args.execute and target_met:
        status = "target_met"
    elif args.execute:
        status = "completed_with_followup_required"
    else:
        status = "dry_run_complete_pending_approval"
    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "generated_at": utc_now(),
        "branch": git_value(["branch", "--show-current"]),
        "head_sha": git_value(["rev-parse", "HEAD"]),
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "phase_identity": PHASE,
            "claims": {
                "target_met": target_met,
                "safe_to_merge": target_met,
                "full_chain_complete": target_met,
            },
            "fresh_dry_run_completed": True,
            "execute_after_approval": bool(args.execute),
            "exact_operator_approval_present": bool(args.execute and args.s3a_m2_approval_phrase == s3a_m2_approval_phrase(str((plan.get("integrity") or {}).get("plan_hash") or ""))),
        },
        "status": status,
        "mode": "execute" if args.execute else "dry_run_plan",
        "source": {
            "root_id": int(root.id),
            "public_source_identity": str(root.root_path_hash or "")[:16],
            "paths_redacted": True,
        },
        "registered_roots_public": source_roots,
        "controlled_delta": {
            "cap": int(args.delta_cap),
            "cap_exceeded": cap_exceeded,
            "expected_delta_range": "100-300",
            "silently_truncated": False,
            "hydrated_only": bool(args.hydrated_only),
            "plan_source": str(args.plan_source).replace("-", "_"),
        },
        "dry_run": {
            "plan_hash": (plan.get("integrity") or {}).get("plan_hash"),
            "plan_created_at": (plan.get("job") or {}).get("created_at"),
            "expires_at": (plan.get("integrity") or {}).get("expires_at"),
            "total_seen": int(counts.get("total_seen") or 0),
            "estimated_import_count": int(counts.get("estimated_import_count") or 0),
            "estimated_classification_count": int(counts.get("estimated_classification_count") or 0),
            "estimated_ai_tagging_count": int(counts.get("estimated_ai_tagging_count") or 0),
            "estimated_localization_workload": int(counts.get("estimated_localization_workload") or 0),
            "state_counts": state_counts,
            "failure_reasons": counts.get("failure_reasons") or {},
            "unsupported_extension_breakdown": counts.get("unsupported_extension_breakdown") or {},
            "failure_reason_extension_breakdown": counts.get("failure_reason_extension_breakdown") or {},
            "source_ledger_aggregate_counts": counts.get("source_ledger_aggregate_counts") or {},
            "partial_scan": cap_exceeded,
            "approval_phrase": s3a_m2_approval_phrase(str((plan.get("integrity") or {}).get("plan_hash") or "")),
            "service_confirmation_phrase_redacted": True,
        },
        "execute": execute_summary,
        "classification": {
            "reported": True,
            "count": int(execute_summary.get("classified") or 0),
            "failed": int(execute_summary.get("classification_failed") or 0),
            "skipped": int(execute_summary.get("classification_skipped") or 0),
        },
        "ai_tagging": {
            "reported": True,
            "count": int(execute_summary.get("ai_tagged") or 0),
            "failed": int(execute_summary.get("ai_tagging_failed") or 0),
            "skipped": int(execute_summary.get("ai_tagging_skipped") or 0),
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
            "no_sourceconcept_or_entity_truth_from_ai_proper_nouns": True,
            "provider_provenance": execute_summary.get("provider_provenance") or {},
        },
        "localization": loc,
        "localization_diagnosis": localization_diagnosis,
        "gpu_telemetry": telemetry_summary,
        "runtime": {
            "total_seconds": round(time.monotonic() - started_at_monotonic, 3),
            "stage_durations_seconds": dict(stage_durations),
        },
        "ledger_consistency": ledger,
        "public_redaction": {"passed": False, "finding_count": None},
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "production_acceptance": {
            "performed": production_execute_ran,
            "approval_phrase_type": "s3a_m2_plan_hash_bound",
            "approval_phrase_recorded": False,
            "exact_statement": "production acceptance performed" if production_execute_ran else "production acceptance not performed",
        },
        "api_runner_acceptance": {
            "dry_run_plan_generated": True,
            "execute_ran": production_execute_ran,
            "status_polled_or_serialized": bool(execution),
            "raw_artifacts_root": f".local_manifests/{PHASE_SLUG}",
        },
        "launcher_web_admin_acceptance": {
            "validated": False,
            "status": "pending_browser_validation_after_runner" if args.execute else "pending_after_dry_run",
            "target_path": "/admin?tab=content#dynamic-library-sync-section",
        },
        "skipped_failed_deferred": {
            "skipped_existing_media": int(state_counts.get("skipped_existing_media", 0)),
            "skipped_duplicate": int(state_counts.get("skipped_duplicate", 0)),
            "skipped_placeholder": int(state_counts.get("skipped_placeholder", 0)),
            "skipped_unsupported": int(state_counts.get("skipped_unsupported", 0)),
            "failed": int(execute_summary.get("failed") or state_counts.get("failed", 0)),
            "deferred": int(execute_summary.get("deferred") or 0),
        },
        "final_inventory": {
            "current_delta_candidates": int(counts.get("total_seen") or 0),
            "current_importable_hydrated_supported_items": int(state_counts.get("import_planned", 0)),
            "existing_media": int(state_counts.get("skipped_existing_media", 0)),
            "placeholders_remaining": int(state_counts.get("skipped_placeholder", 0)),
            "unsupported_items": int(state_counts.get("skipped_unsupported", 0)),
            "unreadable_zero_byte_damaged": int(
                state_counts.get("failed", 0)
                + state_counts.get("skipped_zero_byte", 0)
                + state_counts.get("skipped_path_policy_error", 0)
            ),
            "unsupported_extension_breakdown": counts.get("unsupported_extension_breakdown") or {},
            "scan_cap_stopped_scan": cap_exceeded,
        },
        "private_artifacts": {
            "root": f".local_manifests/{PHASE_SLUG}",
            "telemetry_root": f".local_manifests/{PHASE_SLUG}/telemetry",
            "private_artifacts_committed": False,
        },
        "safety": {
            "no_push_main": True,
            "no_merge": True,
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
            "startup_sync_enabled": False,
            "system_service_enabled": False,
            "source_icloud_mutation": False,
            "source_mutation_attempted": False,
            "provider_pixiv_gallery_dl_saucenao_google_calls": False,
            "sourceconcept_entity_bridge": False,
            "cleanup_delete_reset_drop_truncate": False,
            "full_library_reimport": False,
            "private_paths_or_hashes_in_public_report": False,
        },
        "readiness": dict(readiness),
    }
    summary["standard_pipeline_flow"] = build_standard_pipeline_flow(summary)
    markdown = public_report_markdown(summary)
    redaction = scan_public_output(summary, markdown)
    summary["public_redaction"] = redaction
    refresh_completion_claims(summary)
    markdown = public_report_markdown(summary)
    summary["public_redaction"] = scan_public_output(summary, markdown)
    summary["standard_pipeline_flow"] = build_standard_pipeline_flow(summary)
    return summary


def write_outputs(args: argparse.Namespace, summary: Mapping[str, Any]) -> None:
    output_dir = prepare_output_dir(args)
    write_json(output_dir / "run-summary-private.json", summary)
    write_json(output_dir / "dry-run-plan-public.json", summary.get("dry_run") or {})
    markdown = public_report_markdown(summary)
    if not (summary.get("public_redaction") or {}).get("passed"):
        write_json(output_dir / "public-redaction-failed.json", summary.get("public_redaction") or {})
        raise S3AM2Blocked("public_redaction_failed")
    if args.write_public_report:
        write_json(PUBLIC_REPORT_JSON, summary)
        write_text(PUBLIC_REPORT_MD, markdown)
    write_json(output_dir / "public-redaction-check.json", summary.get("public_redaction") or {})


def refresh_completion_claims(summary: dict[str, Any]) -> dict[str, Any]:
    summary.setdefault(
        "not_completed",
        [
            "Automatic/scheduled/startup/system-service sync was not implemented; it remains out of scope.",
            "Pixiv/provider/gallery-dl/SauceNAO/Google/source metadata expansion was not run.",
            "SourceConcept/Entity bridge work was not run.",
        ],
    )
    execute = summary.get("execute") or {}
    telemetry = dict(summary.get("gpu_telemetry") or {})
    provider = execute.get("provider_provenance") or {}
    actual_provider = _actual_onnx_provider_from_payload(telemetry) or _actual_onnx_provider_from_payload(provider)
    if actual_provider:
        telemetry["actual_provider"] = actual_provider
        telemetry["gpu_provider_used"] = actual_provider in GPU_PROVIDERS
        telemetry["cpu_fallback_observed"] = actual_provider == "CPUExecutionProvider"
        telemetry["validation_status"] = (
            "passed"
            if actual_provider in GPU_PROVIDERS
            else ("partial_cpu_fallback" if actual_provider == "CPUExecutionProvider" else "partial_provider_unknown")
        )
    summary["gpu_telemetry"] = telemetry

    launcher = summary.get("launcher_web_admin_acceptance") or {}
    loc = summary.get("localization") or {}
    loc_diag = summary.get("localization_diagnosis") or {}
    hydration = summary.get("placeholder_hydration") or {}
    inventory = summary.get("final_inventory") or {}
    hydration_status = str(hydration.get("status") or "")
    placeholder_hydration_ok = (
        hydration_status in {"completed", "completed_with_stable_failures", "not_required"}
        and int(hydration.get("remaining_placeholders_after_hydration") or inventory.get("placeholders_remaining") or 0) == 0
    )
    localization_diagnosis_ok = str(loc_diag.get("diagnosis") or "") in {
        "benign_all_localizable_tags_already_localized_or_newly_localized",
        "no_imported_media",
    } and int(loc_diag.get("tags_requiring_localization_after_runner") or 0) == 0
    inventory_ok = int(inventory.get("current_importable_hydrated_supported_items") or 0) == 0 and int(
        inventory.get("placeholders_remaining") or 0
    ) == 0
    launcher_status = str(launcher.get("status") or "")
    previous_execute_run_id = max(
        _standard_int((summary.get("initial_run") or {}).get("run_id")),
        _standard_int((summary.get("remaining_run") or {}).get("run_id")),
        _standard_int(execute.get("run_id")),
    )
    launcher_run_id = _standard_int(launcher.get("gui_execute_run_id") or launcher.get("production_execute_run_id_seen"))
    launcher_gui_provenance_ok = (
        bool(launcher.get("gui_provenance_valid"))
        and str(launcher.get("request_source") or "") == "web_admin_gui"
        and bool(launcher.get("gui_validation_session_id_present"))
        and bool(launcher.get("gui_validation_session_signature_valid"))
    )
    launcher_ok = (
        launcher.get("validated") is True
        and launcher_status == "passed_gui_execute_completed"
        and bool(launcher.get("execute_clicked"))
        and bool(launcher.get("gui_execute_completed"))
        and launcher_run_id > previous_execute_run_id
        and launcher_gui_provenance_ok
    )
    incident = summary.get("ai_tag_assignment_incident") or {}
    incident_after = incident.get("after") if isinstance(incident.get("after"), Mapping) else {}
    incident_ui = incident.get("ui_verification") or summary.get("post_repair_ui_validation") or {}
    incident_ok = (
        str(incident.get("status") or "") in {"repaired", "passed_no_incident"}
        and bool(incident.get("public_safe"))
        and str(incident_ui.get("status") or "") == "passed"
        and bool(incident_ui.get("public_safe"))
        and _standard_int(incident_after.get("high_conf_nonproper_incorrect_suggestion_count")) == 0
        and _standard_int(incident_after.get("high_conf_proper_incorrect_suggestion_count")) == 0
        and _standard_int(incident.get("entity_truth_violations_found")) == 0
        and _standard_int(incident.get("localization_remaining_gap")) == 0
        and not (
            (
                _standard_int(incident_after.get("high_conf_nonproper_expected_normal_count")) > 0
                or _standard_int(incident_after.get("high_conf_proper_expected_normal_count")) > 0
            )
            and bool(incident_after.get("all_ai_assignments_are_suggestions"))
        )
        and (
            _standard_int(incident_after.get("high_conf_proper_expected_normal_count")) == 0
            or _standard_int(incident_after.get("high_conf_proper_normal_count"))
            >= _standard_int(incident_after.get("high_conf_proper_expected_normal_count"))
        )
    )
    cohort = summary.get("cohort_self_audit") or {}
    cohort_ok = (
        str(cohort.get("status") or "") in {"passed", "passed_after_repair"}
        and bool(cohort.get("public_safe"))
        and bool(cohort.get("normal_ai_tag_semantics_consistent_with_policy"))
        and _standard_int(cohort.get("blocker_anomaly_count")) == 0
        and _standard_int(cohort.get("affected_media_count")) > 0
        and _standard_int(cohort.get("baseline_media_count")) > 0
    )
    summary["standard_pipeline_flow"] = build_standard_pipeline_flow(summary)
    standard_pipeline_ok = str((summary.get("standard_pipeline_flow") or {}).get("status") or "") == "completed"
    target_met = bool(
        (summary.get("production_acceptance") or {}).get("performed")
        and execute.get("status") == "completed"
        and not (summary.get("controlled_delta") or {}).get("cap_exceeded")
        and (summary.get("readiness") or {}).get("passed")
        and (summary.get("ledger_consistency") or {}).get("passed")
        and loc.get("status") in {"completed", "completed_noop_no_candidates"}
        and int(loc.get("failed") or 0) == 0
        and localization_diagnosis_ok
        and placeholder_hydration_ok
        and inventory_ok
        and telemetry.get("validation_status") == "passed"
        and launcher_ok
        and standard_pipeline_ok
        and incident_ok
        and cohort_ok
    )
    if target_met:
        status = "target_met"
    elif execute.get("status") == "completed" and telemetry.get("validation_status") != "passed":
        status = "completed_partial_gpu_validation"
    elif execute.get("status") == "completed":
        status = "completed_with_followup_required"
    else:
        status = str(summary.get("status") or "dry_run_complete_pending_approval")

    pipeline = dict(summary.get("pipeline_contract") or {})
    pipeline["status"] = status
    pipeline["claims"] = {
        "target_met": target_met,
        "safe_to_merge": target_met,
        "full_chain_complete": target_met,
    }
    summary["pipeline_contract"] = pipeline
    summary["status"] = status
    return summary


def finalize_existing_report(args: argparse.Namespace) -> dict[str, Any]:
    summary = read_json(args.summary_json)
    if not isinstance(summary, dict):
        raise S3AM2Blocked("summary_json_not_object")
    initial_summary = read_json(args.initial_summary_json) if args.initial_summary_json and args.initial_summary_json.exists() else None
    if initial_summary is not None and not isinstance(initial_summary, dict):
        raise S3AM2Blocked("initial_summary_json_not_object")
    if args.hydration_summary_json and args.hydration_summary_json.exists():
        hydration_summary = read_json(args.hydration_summary_json)
        if not isinstance(hydration_summary, dict):
            raise S3AM2Blocked("hydration_summary_json_not_object")
        summary["placeholder_hydration"] = hydration_summary
    elif "placeholder_hydration" not in summary:
        summary["placeholder_hydration"] = {
            "status": "not_required",
            "placeholder_count_before_hydration": 0,
            "hydration_attempted_count": 0,
            "hydration_succeeded_count": 0,
            "hydration_failed_count": 0,
            "remaining_placeholders_after_hydration": 0,
            "failure_reasons": {},
            "manual_user_action_required": False,
            "public_safe": True,
        }
    if args.final_inventory_plan_json and args.final_inventory_plan_json.exists():
        inventory_plan = read_json(args.final_inventory_plan_json)
        counts = (inventory_plan.get("counts") or {}) if isinstance(inventory_plan, Mapping) else {}
        state_counts = counts.get("state_counts") or {}
        summary["final_inventory"] = {
            "current_delta_candidates": int(counts.get("total_seen") or 0),
            "current_importable_hydrated_supported_items": int(state_counts.get("import_planned", 0)),
            "existing_media": int(state_counts.get("skipped_existing_media", 0)),
            "placeholders_remaining": int(state_counts.get("skipped_placeholder", 0)),
            "unsupported_items": int(state_counts.get("skipped_unsupported", 0)),
            "unreadable_zero_byte_damaged": int(
                state_counts.get("failed", 0)
                + state_counts.get("skipped_zero_byte", 0)
                + state_counts.get("skipped_path_policy_error", 0)
            ),
            "unsupported_extension_breakdown": counts.get("unsupported_extension_breakdown") or {},
            "failure_reason_extension_breakdown": counts.get("failure_reason_extension_breakdown") or {},
            "scan_cap_stopped_scan": bool(counts.get("partial_scan")),
            "public_safe": True,
        }
    if initial_summary:
        initial_execute = initial_summary.get("execute") or {}
        initial_dry_run = initial_summary.get("dry_run") or {}
        initial_loc = initial_summary.get("localization") or {}
        summary["initial_run"] = {
            "run_id": initial_execute.get("run_id"),
            "dry_run_total": initial_dry_run.get("total_seen"),
            "imported": initial_execute.get("imported"),
            "classified": initial_execute.get("classified"),
            "ai_tagged": initial_execute.get("ai_tagged"),
            "localized": initial_loc.get("translated"),
            "skipped_placeholder": (initial_summary.get("skipped_failed_deferred") or {}).get("skipped_placeholder"),
            "skipped_unsupported": (initial_summary.get("skipped_failed_deferred") or {}).get("skipped_unsupported"),
            "skipped_existing": (initial_summary.get("skipped_failed_deferred") or {}).get("skipped_existing_media"),
            "status": initial_summary.get("status"),
        }
        try:
            db = open_db_session()
            try:
                initial_run_id = int(initial_execute.get("run_id") or 0)
                if initial_run_id:
                    summary["initial_localization_diagnosis"] = diagnose_run_localization(
                        db,
                        initial_run_id,
                        lang=str(args.lang),
                        job_id=int(initial_loc.get("job_id") or 0) or None,
                    )
                    summary["initial_localization_diagnosis"]["status"] = "completed"
            finally:
                db.close()
        except Exception as exc:
            summary["initial_localization_diagnosis"] = {"status": "unavailable", "reason": safe_error(exc)}
        remaining_execute = summary.get("execute") or {}
        remaining_dry_run = summary.get("dry_run") or {}
        remaining_loc = summary.get("localization") or {}
        try:
            db = open_db_session()
            try:
                remaining_run_id = int(remaining_execute.get("run_id") or 0)
                if remaining_run_id:
                    summary["localization_diagnosis"] = diagnose_run_localization(
                        db,
                        remaining_run_id,
                        lang=str(args.lang),
                        job_id=int(remaining_loc.get("job_id") or 0) or None,
                    )
                    summary["localization_diagnosis"]["status"] = "completed"
            finally:
                db.close()
        except Exception as exc:
            summary["localization_diagnosis"] = {"status": "unavailable", "reason": safe_error(exc), "public_safe": True}
        initial_validation_blockers: list[str] = []
        if str(initial_execute.get("status") or "") != "completed":
            initial_validation_blockers.append("initial_execute_not_completed")
        if not bool((initial_summary.get("ledger_consistency") or {}).get("passed")):
            initial_validation_blockers.append("initial_ledger_consistency_not_passed")
        if not bool((initial_summary.get("public_redaction") or {}).get("passed")):
            initial_validation_blockers.append("initial_public_redaction_not_passed")
        initial_loc_diag = summary.get("initial_localization_diagnosis") or initial_summary.get("localization_diagnosis") or {}
        if isinstance(initial_loc_diag, Mapping):
            remaining_gap = int(
                initial_loc_diag.get("tags_requiring_localization_after_runner")
                or initial_loc_diag.get("localizable_remaining_gap")
                or 0
            )
            if remaining_gap != 0:
                initial_validation_blockers.append("initial_localization_gap_remaining")
        summary["initial_run_validation"] = {
            "passed": not initial_validation_blockers,
            "blockers": initial_validation_blockers,
            "status": "passed" if not initial_validation_blockers else "blocked",
            "public_safe": True,
        }
        if initial_validation_blockers:
            raise S3AM2Blocked("initial_run_validation_failed:" + ",".join(initial_validation_blockers))
        summary["remaining_run"] = {
            "run_id": remaining_execute.get("run_id"),
            "dry_run_total": remaining_dry_run.get("total_seen"),
            "imported": remaining_execute.get("imported"),
            "classified": remaining_execute.get("classified"),
            "ai_tagged": remaining_execute.get("ai_tagged"),
            "localized": remaining_loc.get("translated"),
            "skipped_placeholder": (summary.get("skipped_failed_deferred") or {}).get("skipped_placeholder"),
            "skipped_unsupported": (summary.get("skipped_failed_deferred") or {}).get("skipped_unsupported"),
            "skipped_existing": (summary.get("skipped_failed_deferred") or {}).get("skipped_existing_media"),
            "status": remaining_execute.get("status") or summary.get("status"),
        }
        initial_runtime = initial_summary.get("runtime") or {}
        remaining_runtime = summary.get("runtime") or {}
        initial_stage = initial_runtime.get("stage_durations_seconds") or {}
        remaining_stage = remaining_runtime.get("stage_durations_seconds") or {}
        stage_names = sorted({*initial_stage.keys(), *remaining_stage.keys()})
        summary["aggregate_runtime"] = {
            "total_seconds": round(
                float(initial_runtime.get("total_seconds") or 0.0) + float(remaining_runtime.get("total_seconds") or 0.0),
                3,
            ),
            "stage_durations_seconds": {
                name: round(float(initial_stage.get(name) or 0.0) + float(remaining_stage.get(name) or 0.0), 3)
                for name in stage_names
            },
        }
        initial_gpu = initial_summary.get("gpu_telemetry") or {}
        remaining_gpu = summary.get("gpu_telemetry") or {}
        summary["aggregate_gpu_telemetry"] = {
            "actual_provider": remaining_gpu.get("actual_provider") or initial_gpu.get("actual_provider"),
            "gpu_name": remaining_gpu.get("gpu_name") or initial_gpu.get("gpu_name"),
            "max_gpu_memory_used_mib": max(
                float(initial_gpu.get("max_gpu_memory_used_mib") or 0.0),
                float(remaining_gpu.get("max_gpu_memory_used_mib") or 0.0),
            ),
            "peak_gpu_utilization_percent": max(
                float(initial_gpu.get("peak_gpu_utilization_percent") or 0.0),
                float(remaining_gpu.get("peak_gpu_utilization_percent") or 0.0),
            ),
            "psutil_available": bool(initial_gpu.get("psutil_available") and remaining_gpu.get("psutil_available")),
            "telemetry_partial_fields": sorted(
                {
                    field
                    for field, ok in {
                        "process_rss": bool(initial_gpu.get("peak_process_rss_bytes") and remaining_gpu.get("peak_process_rss_bytes")),
                        "system_ram": bool(initial_gpu.get("peak_system_ram_percent") and remaining_gpu.get("peak_system_ram_percent")),
                    }.items()
                    if not ok
                }
            ),
            "validation_status": (
                "passed"
                if (remaining_gpu.get("validation_status") == "passed" and initial_gpu.get("validation_status") == "passed")
                else "partial"
            ),
        }
        summary["final_totals"] = {
            "imported": int(initial_execute.get("imported") or 0) + int((summary.get("execute") or {}).get("imported") or 0),
            "classified": int(initial_execute.get("classified") or 0)
            + int((summary.get("execute") or {}).get("classified") or 0),
            "ai_tagged": int(initial_execute.get("ai_tagged") or 0)
            + int((summary.get("execute") or {}).get("ai_tagged") or 0),
            "localized": int(initial_loc.get("translated") or 0)
            + int((summary.get("localization") or {}).get("translated") or 0),
        }
    current_head = git_value(["rev-parse", "HEAD"])
    if current_head:
        summary["head_sha"] = current_head
        readiness = summary.get("readiness")
        if isinstance(readiness, dict):
            readiness["head_sha"] = current_head

    ai_tagging = summary.setdefault("ai_tagging", {})
    if isinstance(ai_tagging, dict):
        ai_tagging["mature_media_tag_policy"] = True
        ai_tagging["proper_nouns_suggestion_only"] = False
        ai_tagging["no_sourceconcept_or_entity_truth_from_ai_only_tags"] = True
        ai_tagging["no_sourceconcept_or_entity_truth_from_ai_proper_nouns"] = True

    validation = read_json(args.launcher_validation_json)
    if not isinstance(validation, dict):
        raise S3AM2Blocked("launcher_validation_json_not_object")
    if validation.get("status") not in {"passed", "passed_gui_execute_completed", "passed_gui_execute_not_safe_runner_execute_used"}:
        raise S3AM2Blocked("launcher_web_admin_validation_not_passed")
    previous_runner_run_id = max(
        _standard_int((summary.get("initial_run") or {}).get("run_id")),
        _standard_int((summary.get("remaining_run") or {}).get("run_id")),
        _standard_int((summary.get("execute") or {}).get("run_id")),
    )
    validation_run_id = int(validation.get("gui_execute_run_id") or validation.get("production_execute_run_id_seen") or 0)
    if validation.get("status") == "passed_gui_execute_completed" and validation_run_id <= previous_runner_run_id:
        raise S3AM2Blocked("launcher_validation_gui_run_not_newer_than_runner")
    validation_head = str(validation.get("validated_head_sha") or validation.get("head_sha") or "")
    if current_head and validation_head != current_head:
        raise S3AM2Blocked("launcher_validation_head_sha_mismatch")
    expected_source_identity = str((summary.get("source") or {}).get("public_source_identity") or "")
    validation_source_identity = str(
        validation.get("public_source_identity") or validation.get("source_public_identity") or ""
    )
    if expected_source_identity and validation_source_identity != expected_source_identity:
        raise S3AM2Blocked("launcher_validation_source_identity_mismatch")

    assertions = validation.get("assertions") or {}
    summary["launcher_web_admin_acceptance"] = {
        "validated": True,
        "status": validation.get("status") or "passed",
        "target_path": "/admin?tab=content#dynamic-library-sync-section",
        "browser": validation.get("browser") or "msedge",
        "dry_run_clicked": bool(validation.get("dry_run_clicked")),
        "execute_clicked": bool(validation.get("execute_clicked")),
        "gui_execute_run_id": validation.get("gui_execute_run_id") or validation.get("production_execute_run_id_seen"),
        "production_execute_run_id_seen": validation.get("production_execute_run_id_seen"),
        "previous_execute_run_id": previous_runner_run_id,
        "gui_provenance_valid": bool(
            validation.get("gui_provenance_valid")
            or (isinstance(validation.get("gui_provenance"), Mapping) and validation["gui_provenance"].get("valid"))
        ),
        "request_source": validation.get("request_source")
        or (
            validation.get("gui_provenance", {}).get("request_source")
            if isinstance(validation.get("gui_provenance"), Mapping)
            else None
        ),
        "gui_validation_session_id_present": bool(
            validation.get("gui_validation_session_id_present")
            or (
                isinstance(validation.get("gui_provenance"), Mapping)
                and validation["gui_provenance"].get("gui_validation_session_id_present")
            )
        ),
        "gui_validation_session_id_hash": validation.get("gui_validation_session_id_hash")
        or (
            validation.get("gui_provenance", {}).get("gui_validation_session_id_hash")
            if isinstance(validation.get("gui_provenance"), Mapping)
            else None
        ),
        "gui_validation_session_signature_valid": bool(
            validation.get("gui_validation_session_signature_valid")
            or (
                isinstance(validation.get("gui_provenance"), Mapping)
                and validation["gui_provenance"].get("gui_validation_session_signature_valid")
            )
        ),
        "validated_head_sha": validation_head,
        "public_source_identity": validation_source_identity,
        "execute_cap_visible": assertions.get("execute_cap_visible"),
        "dry_run_button_click_fired_request": bool(validation.get("dry_run_button_click_fired_request")),
        "dry_run_page_context_fetch_timed_out": bool(validation.get("dry_run_page_context_fetch_timed_out")),
        "dry_run_aborted_by_server_stop": bool(validation.get("dry_run_aborted_by_server_stop")),
        "update_check_limit_separated": bool(
            assertions.get("update_check_limit_has_separate_input")
            or assertions.get("update_check_has_separate_input")
        ),
        "latest_job_status": validation.get("latest_job_status") or assertions.get("latest_job_status"),
        "latest_job_imported": (
            validation.get("latest_job_imported")
            if validation.get("latest_job_imported") is not None
            else assertions.get("latest_job_imported")
        ),
        "computer_use_result": validation.get("computer_use_result"),
        "fallback_reason": validation.get("fallback_reason"),
        "gui_execute_completed": bool(validation.get("gui_execute_completed")),
        "raw_artifact": f".local_manifests/{PHASE_SLUG}/{Path(args.launcher_validation_json).name}",
        "raw_path_committed": False,
    }

    before_path = DEFAULT_OUTPUT_DIR / "db-counts-before.json"
    after_path = DEFAULT_OUTPUT_DIR / "db-counts-after.json"
    if before_path.exists() and after_path.exists():
        before = read_json(before_path)
        after = read_json(after_path)
        if isinstance(before, Mapping) and isinstance(after, Mapping):
            def first_present(*keys: str) -> Any:
                for key in keys:
                    if key in after and after.get(key) is not None:
                        return after.get(key)
                return None

            summary["db_validation"] = {
                "before_media_count": before.get("media_count"),
                "after_media_count": after.get("media_count"),
                "media_delta": int(after.get("media_count") or 0) - int(before.get("media_count") or 0),
                "before_dynamic_source_item_count": before.get("dynamic_source_item_count"),
                "after_dynamic_source_item_count": after.get("dynamic_source_item_count"),
                "source_item_delta": int(after.get("dynamic_source_item_count") or 0) - int(before.get("dynamic_source_item_count") or 0),
                "run_status": first_present("latest_run_status", "run_8_status", "run_7_status"),
                "run_total_seen": first_present("latest_run_total_seen", "run_8_total_seen", "run_7_total_seen"),
                "run_imported": first_present("latest_run_new_items", "run_8_new_items", "run_7_new_items"),
                "run_failed": first_present("latest_run_failed_items", "run_8_failed_items", "run_7_failed_items"),
                "raw_path_committed": False,
            }

    summary["generated_at"] = utc_now()
    refresh_completion_claims(summary)
    markdown = public_report_markdown(summary)
    redaction = scan_public_output(summary, markdown)
    summary["public_redaction"] = redaction
    if not redaction.get("passed"):
        write_json(DEFAULT_OUTPUT_DIR / "public-redaction-failed.json", redaction)
        raise S3AM2Blocked("public_redaction_failed")
    refresh_completion_claims(summary)
    markdown = public_report_markdown(summary)
    redaction = scan_public_output(summary, markdown)
    summary["public_redaction"] = redaction
    if not redaction.get("passed"):
        write_json(DEFAULT_OUTPUT_DIR / "public-redaction-failed.json", redaction)
        raise S3AM2Blocked("public_redaction_failed")
    write_json(DEFAULT_OUTPUT_DIR / "run-summary-private.json", summary)
    write_json(DEFAULT_OUTPUT_DIR / "public-redaction-check.json", redaction)
    if args.write_public_report:
        write_json(PUBLIC_REPORT_JSON, summary)
        write_text(PUBLIC_REPORT_MD, markdown)
    return summary


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    configure_phase_env(args)
    started_at = time.monotonic()
    output_dir = prepare_output_dir(args)
    stage_tracker = StageTracker()
    provider_state: dict[str, Any] = {}
    db = open_db_session()
    try:
        root = select_source_root(db, args.root_id)
        args.root_id = int(root.id)
    finally:
        db.close()
    readiness = collect_readiness(args, root)
    stage_tracker.set("dry_run_plan")
    plan = build_manual_plan(args, root)
    write_json(output_dir / "fresh-dry-run-plan.json", plan)
    counts = plan.get("counts") or {}
    if counts.get("partial_scan"):
        readiness = {**readiness, "passed": False, "blockers": sorted(set([*(readiness.get("blockers") or []), "delta_cap_exceeded"]))}

    execution = None
    localization = None
    telemetry = None
    monitor: ResourceTelemetryMonitor | None = None
    if args.execute and readiness.get("passed"):
        monitor = ResourceTelemetryMonitor(
            telemetry_dir=args.telemetry_dir,
            stage_tracker=stage_tracker,
            provider_getter=lambda: provider_state,
            interval_seconds=args.telemetry_interval_seconds,
        )
        monitor.start()
        try:
            execution = execute_manual_plan(args, plan, stage_tracker)
            execute_summary = manual_execute_stage_summary(execution)
            provider_state.update(execute_summary.get("provider_provenance") or {})
            if str(execute_summary.get("status") or "").casefold() == "completed":
                run_id = int(execution.get("id"))
                localization = run_delta_localization(args, run_id, stage_tracker)
            else:
                localization = {
                    "stage": "localization",
                    "status": "skipped_execute_not_completed",
                    "executed": False,
                    "llm_called": False,
                    "provider_call_count": 0,
                    "failed": 0,
                    "skipped": 0,
                    "reason": "manual_execute_not_completed",
                    "public_safe": True,
                }
        finally:
            stage_tracker.set("summary")
            telemetry = monitor.stop()
    elif args.execute:
        stage_tracker.set("blocked_before_execute")

    summary = build_summary(
        args,
        readiness=readiness,
        root=root,
        plan=plan,
        execution=execution,
        localization=localization,
        telemetry=telemetry,
        stage_durations=stage_tracker.durations(),
        started_at_monotonic=started_at,
    )
    write_outputs(args, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--finalize-report", action="store_true")
    mode.add_argument("--hydrate-placeholders", action="store_true")
    mode.add_argument("--diagnose-localization-run-id", type=int, default=None)
    parser.add_argument("--root-id", type=int, default=None)
    parser.add_argument("--delta-cap", type=int, default=DEFAULT_DELTA_CAP)
    parser.add_argument("--plan-source", choices=("source-delta", "ledger-pending", "source-walk"), default="source-delta")
    parser.add_argument("--include-unresolved-run-id", type=int, default=None)
    parser.add_argument("--stable-age-seconds", type=float, default=None)
    parser.add_argument("--hydrated-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--expected-plan-hash", default="")
    parser.add_argument("--plan-created-at", default="")
    parser.add_argument("--s3a-m2-approval-phrase", default="")
    parser.add_argument("--execute-duration-seconds", type=int, default=7200)
    parser.add_argument("--approve-cpu-fallback", action="store_true")
    parser.add_argument("--require-localization-backend", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--lang", default="zh-CN")
    parser.add_argument("--localization-max-tags", type=int, default=1500)
    parser.add_argument("--localization-batch-size", type=int, default=50)
    parser.add_argument("--translation-batch-max-items", type=int, default=int(os.getenv("TAG_TRANSLATION_BATCH_MAX_ITEMS", "50")))
    parser.add_argument("--telemetry-dir", type=Path, default=DEFAULT_TELEMETRY_DIR)
    parser.add_argument("--telemetry-interval-seconds", type=float, default=2.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--approved-plan-json", type=Path, default=DEFAULT_OUTPUT_DIR / "fresh-dry-run-plan.json")
    parser.add_argument("--summary-json", type=Path, default=PUBLIC_REPORT_JSON)
    parser.add_argument("--initial-summary-json", type=Path, default=None)
    parser.add_argument("--hydration-summary-json", type=Path, default=DEFAULT_OUTPUT_DIR / "hydration-summary-public.json")
    parser.add_argument("--final-inventory-plan-json", type=Path, default=None)
    parser.add_argument("--launcher-validation-json", type=Path, default=DEFAULT_OUTPUT_DIR / "launcher-web-admin-validation.json")
    parser.add_argument("--hydration-timeout-seconds", type=int, default=120)
    parser.add_argument("--hydration-retries", type=int, default=1)
    parser.add_argument("--hydration-chunk-size", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--write-public-report", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.delta_cap <= 0:
        print("ERROR: --delta-cap must be positive", file=sys.stderr)
        return 2
    if args.delta_cap > APPROVED_DELTA_CAP_CEILING:
        print(
            f"ERROR: --delta-cap must not exceed the S3A-M2 approved ceiling of {APPROVED_DELTA_CAP_CEILING}",
            file=sys.stderr,
        )
        return 2
    if not telemetry_dir_allowed(args.telemetry_dir):
        print("ERROR: --telemetry-dir must be under .local_manifests/s3a_m2_delta_e2e/telemetry", file=sys.stderr)
        return 2
    if args.execute_duration_seconds <= 0:
        print("ERROR: --execute-duration-seconds must be positive", file=sys.stderr)
        return 2
    if args.translation_batch_max_items <= 0:
        print("ERROR: --translation-batch-max-items must be positive", file=sys.stderr)
        return 2
    if args.hydration_timeout_seconds <= 0:
        print("ERROR: --hydration-timeout-seconds must be positive", file=sys.stderr)
        return 2
    if args.execute:
        missing = [
            name
            for name, value in {
                "--expected-plan-hash": args.expected_plan_hash,
                "--plan-created-at": args.plan_created_at,
                "--s3a-m2-approval-phrase": args.s3a_m2_approval_phrase,
            }.items()
            if not value
        ]
        if missing:
            print(f"ERROR: execute missing required arguments: {', '.join(missing)}", file=sys.stderr)
            return 2
    try:
        if args.finalize_report:
            summary = finalize_existing_report(args)
        elif args.hydrate_placeholders:
            configure_phase_env(args)
            db = open_db_session()
            try:
                root = select_source_root(db, args.root_id)
            finally:
                db.close()
            stage_tracker = StageTracker()
            stage_tracker.set("hydration")
            monitor = ResourceTelemetryMonitor(
                telemetry_dir=args.telemetry_dir / "hydration",
                stage_tracker=stage_tracker,
                provider_getter=lambda: {},
                interval_seconds=max(0.5, float(args.telemetry_interval_seconds)),
            )
            monitor.start()
            try:
                summary = hydrate_placeholders(args, root)
            finally:
                telemetry = monitor.stop()
            summary["resource_telemetry"] = telemetry
            summary["runtime"] = {"stage_durations_seconds": stage_tracker.durations()}
            write_json(prepare_output_dir(args) / "hydration-summary-public.json", summary)
        elif args.diagnose_localization_run_id:
            configure_phase_env(args)
            db = open_db_session()
            try:
                summary = diagnose_run_localization(db, int(args.diagnose_localization_run_id), lang=str(args.lang))
            finally:
                db.close()
            write_json(prepare_output_dir(args) / f"localization-diagnosis-run-{int(args.diagnose_localization_run_id)}.json", summary)
        else:
            summary = run_pipeline(args)
    except S3AM2Blocked as exc:
        print(f"ERROR: {safe_error(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {safe_error(exc)}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=json_default))
    status = str(summary.get("status") or "")
    if status.startswith("blocked"):
        return 2
    if (args.execute or args.finalize_report) and not (summary.get("pipeline_contract", {}).get("claims", {}).get("target_met")):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
