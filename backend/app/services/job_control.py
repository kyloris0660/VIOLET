"""Shared job, provider, and load-control primitives for S2G/S3A.

This module is intentionally side-effect free. It defines the runtime
vocabulary used by WD tagging and future S3A planning without opening a DB
connection, starting workers, or enabling production sync execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SUPPORTED_ONNX_PROVIDERS: tuple[str, ...] = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)
DEFAULT_PROVIDER_PREFERENCE: tuple[str, ...] = SUPPORTED_ONNX_PROVIDERS
ALLOWED_EXECUTION_MODES = {"ORT_SEQUENTIAL", "ORT_PARALLEL"}
ALLOWED_PROCESS_PRIORITIES = {"low", "below_normal", "normal"}

DEFAULT_AI_TAGGING_BATCH_SIZE = 2
DEFAULT_CPU_INTRA_OP_THREADS = 4
DEFAULT_CPU_INTER_OP_THREADS = 1
DEFAULT_PREPROCESS_WORKERS = 2
DEFAULT_MAX_CONCURRENT_AI_JOBS = 1
DEFAULT_EXECUTION_MODE = "ORT_SEQUENTIAL"
DEFAULT_PROCESS_PRIORITY = "below_normal"

S3A_FOUNDATION_STAGES: tuple[str, ...] = (
    "update_check",
    "hydration_read",
    "import_reuse",
    "classification",
    "ai_tagging",
    "localization",
    "summary",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_provider_preference(raw: str | Sequence[str] | None) -> tuple[str, ...]:
    """Parse a comma-separated or sequence provider preference value."""
    if raw is None:
        return DEFAULT_PROVIDER_PREFERENCE
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",")]
    else:
        values = [str(part).strip() for part in raw]

    parsed: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        parsed.append(value)
        seen.add(value)
    return tuple(parsed) or DEFAULT_PROVIDER_PREFERENCE


def coerce_positive_int(value: Any, *, default: int, maximum: int | None = None) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = default
    coerced = max(1, coerced)
    if maximum is not None:
        coerced = min(maximum, coerced)
    return coerced


@dataclass(frozen=True)
class ProviderSelection:
    """Provider chosen from requested preference and ONNX Runtime availability."""

    requested_provider_preference: tuple[str, ...]
    available_providers: tuple[str, ...]
    candidate_provider_order: tuple[str, ...]
    selected_provider: str | None
    unsupported_requested_providers: tuple[str, ...] = ()
    unavailable_requested_providers: tuple[str, ...] = ()
    fallback_occurred: bool = False
    fallback_reason: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "requested_provider_preference": list(self.requested_provider_preference),
            "available_providers": list(self.available_providers),
            "candidate_provider_order": list(self.candidate_provider_order),
            "selected_provider": self.selected_provider,
            "unsupported_requested_providers": list(self.unsupported_requested_providers),
            "unavailable_requested_providers": list(self.unavailable_requested_providers),
            "fallback_occurred": self.fallback_occurred,
            "fallback_reason": self.fallback_reason,
        }


def select_onnx_provider(
    requested_provider_preference: str | Sequence[str] | None,
    available_providers: Sequence[str],
) -> ProviderSelection:
    requested = parse_provider_preference(requested_provider_preference)
    available = tuple(str(provider) for provider in available_providers)
    available_set = set(available)
    supported_requested = tuple(
        provider for provider in requested if provider in SUPPORTED_ONNX_PROVIDERS
    )
    unsupported = tuple(
        provider for provider in requested if provider not in SUPPORTED_ONNX_PROVIDERS
    )
    effective_requested = supported_requested or DEFAULT_PROVIDER_PREFERENCE
    unavailable = tuple(
        provider for provider in effective_requested if provider not in available_set
    )
    candidates = tuple(
        provider for provider in effective_requested if provider in available_set
    )
    selected = candidates[0] if candidates else None

    fallback_reasons: list[str] = []
    if unsupported:
        fallback_reasons.append(
            "unsupported_requested_providers="
            + ",".join(unsupported)
        )
    if unavailable:
        fallback_reasons.append(
            "unavailable_requested_providers="
            + ",".join(unavailable)
        )
    if selected is None:
        fallback_reasons.append("no_requested_providers_available")

    if selected is None and "CPUExecutionProvider" in available_set:
        selected = "CPUExecutionProvider"
        candidates = ("CPUExecutionProvider",)
    elif selected is None and available:
        selected = available[0]
        candidates = (selected,)

    first_requested = requested[0] if requested else None
    fallback_occurred = bool(fallback_reasons) or (
        selected is not None and first_requested is not None and selected != first_requested
    )
    fallback_reason = "; ".join(fallback_reasons) if fallback_occurred else None

    return ProviderSelection(
        requested_provider_preference=requested,
        available_providers=available,
        candidate_provider_order=candidates,
        selected_provider=selected,
        unsupported_requested_providers=unsupported,
        unavailable_requested_providers=unavailable,
        fallback_occurred=fallback_occurred,
        fallback_reason=fallback_reason,
    )


@dataclass(frozen=True)
class LoadControlConfig:
    """Bounded execution controls for model or pipeline work."""

    batch_size: int = DEFAULT_AI_TAGGING_BATCH_SIZE
    worker_count: int = 1
    max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_AI_JOBS
    preprocess_workers: int = DEFAULT_PREPROCESS_WORKERS
    provider_preference: tuple[str, ...] = DEFAULT_PROVIDER_PREFERENCE
    cpu_intra_op_threads: int = DEFAULT_CPU_INTRA_OP_THREADS
    cpu_inter_op_threads: int = DEFAULT_CPU_INTER_OP_THREADS
    execution_mode: str = DEFAULT_EXECUTION_MODE
    allow_provider_fallback: bool = True
    process_priority: str = DEFAULT_PROCESS_PRIORITY
    process_priority_applied: bool = False
    process_priority_note: str = (
        "not_applied_in_shared_app_process; bounded_threads_and_single_job_are_active_controls"
    )

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if self.batch_size < 1:
            errors.append("batch_size_must_be_positive")
        if self.worker_count != 1:
            errors.append("worker_count_must_remain_one")
        if self.max_concurrent_jobs != 1:
            errors.append("max_concurrent_jobs_must_remain_one")
        if self.preprocess_workers < 1:
            errors.append("preprocess_workers_must_be_positive")
        if self.cpu_intra_op_threads < 1:
            errors.append("cpu_intra_op_threads_must_be_positive")
        if self.cpu_inter_op_threads < 1:
            errors.append("cpu_inter_op_threads_must_be_positive")
        if not self.provider_preference:
            errors.append("provider_preference_required")
        if self.execution_mode not in ALLOWED_EXECUTION_MODES:
            errors.append("execution_mode_must_be_ort_sequential_or_parallel")
        if self.process_priority not in ALLOWED_PROCESS_PRIORITIES:
            errors.append("process_priority_must_be_low_below_normal_or_normal")
        return errors

    def runtime_signature(self) -> tuple[Any, ...]:
        return (
            self.batch_size,
            self.worker_count,
            self.max_concurrent_jobs,
            self.preprocess_workers,
            self.provider_preference,
            self.cpu_intra_op_threads,
            self.cpu_inter_op_threads,
            self.execution_mode,
            self.allow_provider_fallback,
            self.process_priority,
        )

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_preference"] = list(self.provider_preference)
        payload["validation_errors"] = self.validation_errors()
        payload["valid_for_runtime"] = not payload["validation_errors"]
        return payload


def build_ai_tagging_load_control_config(settings_obj: Any) -> LoadControlConfig:
    """Build bounded AI tagging controls from settings-like object values."""
    return LoadControlConfig(
        batch_size=coerce_positive_int(
            getattr(settings_obj, "AI_TAGGING_BATCH_SIZE", DEFAULT_AI_TAGGING_BATCH_SIZE),
            default=DEFAULT_AI_TAGGING_BATCH_SIZE,
            maximum=getattr(settings_obj, "AI_TAGGING_BATCH_MAX_ITEMS", 10),
        ),
        worker_count=1,
        max_concurrent_jobs=coerce_positive_int(
            getattr(settings_obj, "AI_TAGGING_MAX_CONCURRENT_JOBS", DEFAULT_MAX_CONCURRENT_AI_JOBS),
            default=DEFAULT_MAX_CONCURRENT_AI_JOBS,
            maximum=1,
        ),
        preprocess_workers=coerce_positive_int(
            getattr(settings_obj, "AI_TAGGING_PREPROCESS_WORKERS", DEFAULT_PREPROCESS_WORKERS),
            default=DEFAULT_PREPROCESS_WORKERS,
        ),
        provider_preference=parse_provider_preference(
            getattr(settings_obj, "AI_TAGGING_PROVIDER_PREFERENCE", DEFAULT_PROVIDER_PREFERENCE)
        ),
        cpu_intra_op_threads=coerce_positive_int(
            getattr(settings_obj, "AI_TAGGING_CPU_INTRA_OP_THREADS", DEFAULT_CPU_INTRA_OP_THREADS),
            default=DEFAULT_CPU_INTRA_OP_THREADS,
        ),
        cpu_inter_op_threads=coerce_positive_int(
            getattr(settings_obj, "AI_TAGGING_CPU_INTER_OP_THREADS", DEFAULT_CPU_INTER_OP_THREADS),
            default=DEFAULT_CPU_INTER_OP_THREADS,
        ),
        execution_mode=str(
            getattr(settings_obj, "AI_TAGGING_EXECUTION_MODE", DEFAULT_EXECUTION_MODE)
        ).strip().upper() or DEFAULT_EXECUTION_MODE,
        process_priority=str(
            getattr(settings_obj, "AI_TAGGING_PROCESS_PRIORITY", DEFAULT_PROCESS_PRIORITY)
        ).strip().lower() or DEFAULT_PROCESS_PRIORITY,
    )


@dataclass(frozen=True)
class ProviderCapability:
    """Public-safe capability evidence for one ONNX Runtime provider."""

    provider: str
    available: bool
    practical: bool = False
    loaded: bool = False
    benchmark_status: str = "not_run"
    throughput_items_per_second: float | None = None
    seconds_per_item: float | None = None
    load_error_code: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderProvenance:
    """How a model session selected and loaded an execution provider."""

    requested_provider_preference: tuple[str, ...]
    available_providers: tuple[str, ...]
    actual_provider: str | None
    loaded_providers: tuple[str, ...] = ()
    fallback_occurred: bool = False
    fallback_reason: str | None = None
    provider_load_errors: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "requested_provider_preference": list(self.requested_provider_preference),
            "available_providers": list(self.available_providers),
            "actual_provider": self.actual_provider,
            "actual_onnx_provider_loaded": self.actual_provider,
            "loaded_providers": list(self.loaded_providers),
            "fallback_occurred": self.fallback_occurred,
            "fallback_reason": self.fallback_reason,
            "provider_load_errors": list(self.provider_load_errors),
        }


@dataclass(frozen=True)
class StageRun:
    """Public-safe summary for one planned or executed stage."""

    name: str
    status: str = "planned"
    dry_run: bool = True
    writes_enabled: bool = False
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    stop_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class ProgressSnapshot:
    """Pollable progress view for CLI/API/UI surfaces."""

    status: str
    total: int = 0
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    current_stage: str | None = None
    updated_at: str = field(default_factory=utc_now_iso)

    def to_public_dict(self) -> dict[str, Any]:
        percent = 0.0
        if self.total > 0:
            percent = round((self.processed / self.total) * 100, 2)
        payload = asdict(self)
        payload["percent"] = percent
        return payload


@dataclass(frozen=True)
class JobRun:
    """Shared job summary vocabulary for S2G and future S3A runs."""

    job_type: str
    status: str
    trigger_mode: str = "manual_operator_trigger"
    dry_run: bool = True
    production_execution_enabled: bool = False
    unattended_enabled: bool = False
    stages: tuple[StageRun, ...] = ()
    progress: ProgressSnapshot = field(
        default_factory=lambda: ProgressSnapshot(status="planned")
    )
    load_control: LoadControlConfig = field(default_factory=LoadControlConfig)
    provider_provenance: ProviderProvenance | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validation_errors(self) -> list[str]:
        errors = list(self.load_control.validation_errors())
        if self.trigger_mode != "manual_operator_trigger":
            errors.append("trigger_mode_must_be_manual_operator_trigger")
        if self.production_execution_enabled:
            errors.append("production_execution_must_remain_disabled")
        if self.unattended_enabled:
            errors.append("unattended_execution_must_remain_disabled")
        if any(stage.writes_enabled for stage in self.stages):
            errors.append("planned_stages_must_not_enable_writes")
        return errors

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_type": self.job_type,
            "status": self.status,
            "trigger_mode": self.trigger_mode,
            "dry_run": self.dry_run,
            "production_execution_enabled": self.production_execution_enabled,
            "unattended_enabled": self.unattended_enabled,
            "stages": [stage.to_public_dict() for stage in self.stages],
            "progress": self.progress.to_public_dict(),
            "load_control": self.load_control.to_public_dict(),
            "provider_provenance": (
                self.provider_provenance.to_public_dict()
                if self.provider_provenance
                else None
            ),
            "metadata": dict(self.metadata),
            "validation_errors": self.validation_errors(),
        }
        payload["safe_for_current_phase"] = not payload["validation_errors"]
        return payload


def build_s3a_foundation_dry_run_plan(load_control: LoadControlConfig) -> JobRun:
    """Build the future S3A stage skeleton with every write path disabled."""
    stages = tuple(
        StageRun(
            name=name,
            status="planned",
            dry_run=True,
            writes_enabled=False,
            metadata={"production_execution": False},
        )
        for name in S3A_FOUNDATION_STAGES
    )
    return JobRun(
        job_type="s3a_foundation_plan",
        status="planned",
        dry_run=True,
        production_execution_enabled=False,
        unattended_enabled=False,
        stages=stages,
        progress=ProgressSnapshot(status="planned", total=len(stages), current_stage=stages[0].name),
        load_control=load_control,
        metadata={
            "production_db_writes": False,
            "production_s3a_execution": False,
            "unattended_s3b": False,
        },
    )
