"""S2G/S3A shared job and load-control planning primitives.

Lifecycle: reusable validation/safety tool.

This module is deliberately stdlib-only and side-effect free.  It gives S2G
GPU/load-control work and S3A incremental sync planning a common vocabulary
without enabling production writes, background workers, DB schema changes, or
provider calls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


ALLOWED_PROCESS_PRIORITIES = {"low", "below_normal", "normal"}


@dataclass(frozen=True)
class LoadControlConfig:
    """Bounded execution controls for model or pipeline work."""

    batch_size: int
    worker_count: int
    max_concurrent_jobs: int
    preprocess_workers: int
    provider_preference: tuple[str, ...]
    cpu_intra_op_threads: int | None = None
    cpu_inter_op_threads: int | None = None
    allow_provider_fallback: bool = True
    process_priority: str = "below_normal"
    pause_resume_required: bool = True
    pause_supported: bool = False
    resume_supported: bool = False

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if self.batch_size < 1:
            errors.append("batch_size_must_be_positive")
        if self.worker_count != 1:
            errors.append("worker_count_must_remain_one_until_gpu_load_control_is_proven")
        if self.max_concurrent_jobs != 1:
            errors.append("max_concurrent_jobs_must_remain_one")
        if self.preprocess_workers < 1:
            errors.append("preprocess_workers_must_be_positive")
        if self.cpu_intra_op_threads is not None and self.cpu_intra_op_threads < 1:
            errors.append("cpu_intra_op_threads_must_be_positive")
        if self.cpu_inter_op_threads is not None and self.cpu_inter_op_threads < 1:
            errors.append("cpu_inter_op_threads_must_be_positive")
        if not self.provider_preference:
            errors.append("provider_preference_required")
        if self.process_priority not in ALLOWED_PROCESS_PRIORITIES:
            errors.append("process_priority_must_be_low_below_normal_or_normal")
        if self.pause_resume_required and not (self.pause_supported and self.resume_supported):
            errors.append("pause_resume_required_but_not_implemented")
        return errors

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_preference"] = list(self.provider_preference)
        payload["validation_errors"] = self.validation_errors()
        payload["valid_for_production_execution"] = not payload["validation_errors"]
        return payload


@dataclass(frozen=True)
class ProviderCapability:
    """Public-safe capability evidence for one ONNX Runtime provider."""

    provider: str
    available: bool
    practical: bool
    loaded: bool
    benchmark_status: str
    throughput_items_per_second: float | None = None
    seconds_per_item: float | None = None
    load_error_code: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PipelineStagePlan:
    """Dry-run stage plan for future S3A/S2G pipeline execution."""

    name: str
    purpose: str
    writes_enabled: bool
    durable_ledger_required: bool
    failure_budget_required: bool
    retry_policy_required: bool

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PipelinePlan:
    """Manual-triggered, dry-run-only plan skeleton for the shared runner."""

    trigger_mode: str
    execution_boundary: str
    production_execution_enabled: bool
    unattended_enabled: bool
    dry_run_only: bool
    stages: tuple[PipelineStagePlan, ...]
    load_control: LoadControlConfig

    def validation_errors(self) -> list[str]:
        errors = list(self.load_control.validation_errors())
        if self.trigger_mode != "manual_operator_trigger":
            errors.append("trigger_mode_must_be_manual_operator_trigger")
        if self.production_execution_enabled:
            errors.append("production_execution_must_remain_disabled")
        if self.unattended_enabled:
            errors.append("unattended_execution_must_remain_disabled")
        if not self.dry_run_only:
            errors.append("current_scaffold_must_remain_dry_run_only")
        if any(stage.writes_enabled for stage in self.stages):
            errors.append("current_scaffold_stages_must_not_enable_writes")
        return errors

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "trigger_mode": self.trigger_mode,
            "execution_boundary": self.execution_boundary,
            "production_execution_enabled": self.production_execution_enabled,
            "unattended_enabled": self.unattended_enabled,
            "dry_run_only": self.dry_run_only,
            "stages": [stage.to_public_dict() for stage in self.stages],
            "load_control": self.load_control.to_public_dict(),
            "validation_errors": self.validation_errors(),
            "safe_for_current_phase": not self.validation_errors(),
        }


def build_s2g1x_load_control(
    *,
    cpu_count: int,
    configured_batch_max: int,
    provider_preference: Sequence[str],
) -> LoadControlConfig:
    """Return conservative controls for the S2G-1X probe/foundation phase."""

    bounded_cpu = max(1, cpu_count)
    return LoadControlConfig(
        batch_size=max(1, min(configured_batch_max, 2)),
        worker_count=1,
        max_concurrent_jobs=1,
        preprocess_workers=max(1, min(2, bounded_cpu)),
        provider_preference=tuple(provider_preference) or ("CPUExecutionProvider",),
        cpu_intra_op_threads=max(1, min(4, bounded_cpu // 2 or 1)),
        cpu_inter_op_threads=1,
        allow_provider_fallback=True,
        process_priority="below_normal",
        pause_resume_required=True,
        pause_supported=False,
        resume_supported=False,
    )


def build_s3a_dev_dry_run_plan(load_control: LoadControlConfig) -> PipelinePlan:
    """Build the shared S3A skeleton without enabling production execution."""

    stages = (
        PipelineStagePlan("update_check", "Detect new, changed, missing, and deferred source items.", False, True, True, True),
        PipelineStagePlan("hydration_read_plan", "Plan cloud hydration/read attempts and item-level failures.", False, True, True, True),
        PipelineStagePlan("import_reuse_plan", "Plan import/reuse decisions without copying or DB media writes.", False, True, True, True),
        PipelineStagePlan("classification_plan", "Plan classification workload and failure budgets.", False, True, True, True),
        PipelineStagePlan("ai_tagging_plan", "Plan AI tagging provider, provenance, load controls, and retry budget.", False, True, True, True),
        PipelineStagePlan("localization_plan", "Plan localization gap handling without LLM/provider calls.", False, True, True, True),
        PipelineStagePlan("summary", "Produce public aggregate summary and private-ledger manifest.", False, True, False, False),
    )
    return PipelinePlan(
        trigger_mode="manual_operator_trigger",
        execution_boundary="background_job_runner_with_admin_api_trigger_and_cli_probe",
        production_execution_enabled=False,
        unattended_enabled=False,
        dry_run_only=True,
        stages=stages,
        load_control=load_control,
    )


def build_integration_decision(
    *,
    provider_capabilities: Mapping[str, ProviderCapability],
    current_app_forced_provider: str,
    load_control: LoadControlConfig,
) -> dict[str, Any]:
    """Summarize the S2G/S3A architecture decision in public-safe terms."""

    gpu_practical = any(
        provider_capabilities.get(name, ProviderCapability(name, False, False, False, "not_checked")).practical
        for name in ("CUDAExecutionProvider", "DmlExecutionProvider")
    )
    load_control_gaps = load_control.validation_errors()
    return {
        "decision": "share_foundation_split_production_execution",
        "should_share_job_progress_throttle_ledger_architecture": True,
        "should_combine_current_production_execution": False,
        "recommended_execution_boundary": "background_job_runner_with_admin_api_trigger_and_cli_probe",
        "gpu_load_control_before_s3a_production_execution": True,
        "production_s3a_execution_enabled": False,
        "unattended_s3b_enabled": False,
        "current_app_forced_provider": current_app_forced_provider,
        "gpu_provider_practical_in_probe": gpu_practical,
        "load_control_gaps": load_control_gaps,
        "recommended_next_phase": "combined_s2g_s3a_foundation_before_any_production_s3a_execution",
        "rationale": [
            "Both S2G and S3A need the same manual trigger, progress, throttle, failure-budget, retry, and per-item ledger semantics.",
            "The current AI tagger forces CPU execution and needs provider provenance plus bounded resource controls before production S3A chains AI tagging.",
            "S3A production execution should stay disabled until the shared foundation is promoted under a separate operator-approved phase.",
        ],
    }
