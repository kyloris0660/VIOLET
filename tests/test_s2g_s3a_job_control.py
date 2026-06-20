from __future__ import annotations

from scripts.s2g_s3a_job_control import (
    ProviderCapability,
    build_integration_decision,
    build_s2g1x_load_control,
    build_s3a_dev_dry_run_plan,
)


def test_s2g1x_load_control_is_conservative_and_reports_pause_gap() -> None:
    config = build_s2g1x_load_control(
        cpu_count=16,
        configured_batch_max=10,
        provider_preference=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    public = config.to_public_dict()

    assert public["batch_size"] == 2
    assert public["worker_count"] == 1
    assert public["max_concurrent_jobs"] == 1
    assert public["cpu_intra_op_threads"] == 4
    assert public["cpu_inter_op_threads"] == 1
    assert "pause_resume_required_but_not_implemented" in public["validation_errors"]
    assert public["valid_for_production_execution"] is False


def test_s3a_dev_dry_run_plan_keeps_execution_disabled() -> None:
    config = build_s2g1x_load_control(
        cpu_count=4,
        configured_batch_max=1,
        provider_preference=["CPUExecutionProvider"],
    )
    plan = build_s3a_dev_dry_run_plan(config)
    public = plan.to_public_dict()

    assert public["trigger_mode"] == "manual_operator_trigger"
    assert public["production_execution_enabled"] is False
    assert public["unattended_enabled"] is False
    assert public["dry_run_only"] is True
    assert all(stage["writes_enabled"] is False for stage in public["stages"])
    assert "ai_tagging_plan" in {stage["name"] for stage in public["stages"]}


def test_integration_decision_shares_foundation_but_splits_production_execution() -> None:
    config = build_s2g1x_load_control(
        cpu_count=8,
        configured_batch_max=10,
        provider_preference=["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    decision = build_integration_decision(
        provider_capabilities={
            "DmlExecutionProvider": ProviderCapability(
                provider="DmlExecutionProvider",
                available=False,
                practical=False,
                loaded=False,
                benchmark_status="not_available",
            ),
            "CPUExecutionProvider": ProviderCapability(
                provider="CPUExecutionProvider",
                available=True,
                practical=True,
                loaded=True,
                benchmark_status="completed",
                throughput_items_per_second=1.5,
            ),
        },
        current_app_forced_provider="CPUExecutionProvider",
        load_control=config,
    )

    assert decision["decision"] == "share_foundation_split_production_execution"
    assert decision["should_share_job_progress_throttle_ledger_architecture"] is True
    assert decision["should_combine_current_production_execution"] is False
    assert decision["production_s3a_execution_enabled"] is False
    assert decision["current_app_forced_provider"] == "CPUExecutionProvider"
