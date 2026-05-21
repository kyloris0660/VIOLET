import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "plan_phase38d_i3_recovery.py"
_spec = importlib.util.spec_from_file_location("plan_phase38d_i3_recovery", SCRIPT_PATH)
recovery = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["plan_phase38d_i3_recovery"] = recovery
_spec.loader.exec_module(recovery)


def _protected(label: str, path: Path):
    return recovery.ProtectedRoot(label=label, path=path)


def _manifest_rows(tmp_path: Path):
    def row(row_id: int, bucket: str, selected: bool):
        return {
            "row_id": str(row_id),
            "source_path": str(tmp_path / f"source_{row_id}.jpg"),
            "proposed_target_path": str(tmp_path / "target" / f"target_{row_id}.jpg"),
            "extension": ".jpg",
            "size_bytes": "10",
            "selection_reason": "new_candidate" if selected else "",
            "duplicate_key": "",
            "exclusion_reason": "" if selected else "not_selected_temporal_stratified",
            "placeholder_flag": "False",
            "stat_error": "False",
            "temporal_bucket": bucket,
        }

    return [
        row(1, "b01", True),
        row(2, "b02", True),
        row(3, "b02", False),
        row(4, "b01", False),
    ]


def test_final_delivery_report_standard_docs_presence():
    repo = Path(__file__).resolve().parent.parent
    for rel in ["AGENTS.md", "CLAUDE.md", "docs/test-workflow.md"]:
        text = (repo / rel).read_text(encoding="utf-8")
        assert "Final Delivery Report Standard" in text
        assert "PR URL, branch, head SHA" in text
        assert "exact sys.executable" in text
        assert "If any item is not applicable" in text


def test_cleanup_dry_run_refuses_unsafe_target(tmp_path: Path):
    target = tmp_path / "source_root" / "target"
    target.mkdir(parents=True)
    (target / "copied.jpg").write_bytes(b"x")

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        protected_roots=[
            _protected("source_root", tmp_path / "source_root"),
            _protected("repo_root", tmp_path / "repo"),
            _protected("app_storage_root", tmp_path / "app_storage"),
        ],
        expected_file_count=1,
        expected_total_bytes=1,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=None,
    )

    assert public["status"] == "needs_manual_review"
    assert public["target_is_not_source_icloud"] is False
    assert public["deletion_plan"]["actual_delete_performed"] is False
    assert (target / "copied.jpg").exists()


def test_cleanup_dry_run_does_not_delete_and_requires_confirmation(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    copied = target / "copied.png"
    copied.write_bytes(b"123")
    log = tmp_path / "copy.log"
    log.write_text(f"Target: {target}\nExpected files: 1000\n", encoding="utf-8")

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        protected_roots=[
            _protected("source_root", tmp_path / "source"),
            _protected("repo_root", tmp_path / "repo"),
            _protected("app_storage_root", tmp_path / "storage"),
        ],
        expected_file_count=1,
        expected_total_bytes=3,
        execute_cleanup_requested=True,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "dry_run_passed"
    assert public["deletion_plan"]["execute_requested"] is True
    assert public["deletion_plan"]["execute_allowed"] is False
    assert public["deletion_plan"]["confirmation_phrase_valid"] is False
    assert public["deletion_plan"]["confirmation_phrase_required"] == recovery.CLEANUP_CONFIRM_PHRASE
    assert copied.exists()


def test_cleanup_report_public_summary_is_privacy_safe(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret_file_name.jpg").write_bytes(b"x")
    log = tmp_path / "copy.log"
    log.write_text(f"Target: {target}\nExpected files: 1000\n", encoding="utf-8")

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        protected_roots=[
            _protected("source_root", tmp_path / "source"),
            _protected("repo_root", tmp_path / "repo"),
            _protected("app_storage_root", tmp_path / "storage"),
        ],
        expected_file_count=1,
        expected_total_bytes=1,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )
    backfill = recovery.build_backfill_policy(
        manifest_rows=_manifest_rows(tmp_path),
        failed_row_id=2,
        selected_total=4,
    )
    report = recovery.build_recovery_report(
        cleanup_dry_run=public,
        backfill_policy=backfill,
        local_details_artifact="local-details.json",
    )

    text = str(report)
    assert str(tmp_path) not in text
    assert "secret_file_name" not in text
    assert report["privacy"]["passed"] is True


def test_read_probe_policy_is_opt_in():
    policy = recovery.controlled_read_probe_policy()

    assert policy["default_enabled"] is False
    assert policy["approval_required_before_run"] is True
    assert policy["may_trigger_provider_hydration"] is True
    assert policy["cfhydrateplaceholder"]["not_implemented_in_phase_3_8d_i3"] is True


def test_backfill_policy_preserves_bucket_distribution(tmp_path: Path):
    policy = recovery.build_backfill_policy(
        manifest_rows=_manifest_rows(tmp_path),
        failed_row_id=2,
        selected_total=4,
    )

    dry_run = policy["dry_run_plan"]
    assert policy["dry_run_only"] is True
    assert policy["actual_manifest_replacement_performed"] is False
    assert dry_run["selected_total_preserved"] is True
    assert dry_run["replacement_count"] == 1
    assert dry_run["replacements"][0]["bucket"] == "b02"
    assert dry_run["replacements"][0]["replacement_row_id"] == 3
