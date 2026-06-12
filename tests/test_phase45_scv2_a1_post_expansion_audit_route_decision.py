"""Focused tests for Phase 4.5-SCV2-A1 post-expansion audit runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_phase45_scv2_a1_post_expansion_audit_route_decision as runner  # noqa: E402


def _fake_db_identity() -> dict:
    return {
        "violet_env": "development",
        "database": "blombooru",
        "connected_database": "blombooru",
        "connected_user": "postgres",
        "host": "localhost",
        "port": 5432,
        "server_port": 5432,
        "transaction_read_only": "on",
        "transaction_read_only_ok": True,
        "git_branch": runner.BRANCH,
        "git_sha": "abc123",
        "python_executable": "python.exe",
        "recorded_at": "2026-06-12T00:00:00Z",
        "db_resolution": {
            "app_compatible": True,
            "settings_json_exists": True,
            "database_file_settings_used": True,
            "field_sources": {},
            "password_present": True,
            "password_value_recorded": False,
            "urls_match": True,
            "runner_matches_app_equivalent": True,
        },
    }


def _fake_route() -> dict:
    thresholds = {
        "entity_bridge": {
            "blocked": True,
            "current_values": {
                "search_asymmetric_groups": 10,
                "unmatched_search_seeds": 2,
                "total_gap_signals": 4622,
                "needs_review_concepts": 1809,
            },
        }
    }
    return runner.build_route_decision_matrix(
        {
            "total_gap_signals": 4622,
            "gap_buckets": {
                "source_tag_present_no_source_concept_alias": 947,
                "same_normalized_alias_key_split_across_multiple_concepts": 544,
            },
        },
        {"aggregate": {"asymmetric_groups": 10, "unmatched_seeds": 2}},
        {"total_needs_review_concepts": 1809},
        {"source_metadata_distinct_eligible_media_pct": 14.4},
        thresholds,
    )


def _fake_summary(tmp_path: Path, review_pack_info: dict | None = None) -> dict:
    route = _fake_route()
    thresholds = {"entity_bridge": {"blocked": True}}
    return runner.build_summary(
        db_identity=_fake_db_identity(),
        transaction_proof={"passed": True, "transaction_read_only": "on"},
        baseline={
            "total_media": 3750,
            "eligible_media": 3687,
            "eligible_ai_tag_coverage": {"covered": 3687, "total": 3687, "percent": 100.0},
        },
        source_metadata={"source_metadata_distinct_eligible_media_pct": 14.4},
        concepts={"total_source_concepts": 6094, "by_status": {"active": 1078, "needs_review": 1809}},
        r1_transition=runner.build_r1_transition_interpretation({"mode": "execute", "source_concept_delta": {"total_source_concepts_delta": 0}}),
        gap={
            "total_gap_signals": 4622,
            "gap_buckets": {"source_tag_present_no_source_concept_alias": 947},
            "gap_bucket_details": {},
        },
        gap_vs_scv1={"scv1_total_gap_signals": 1571, "current_total_gap_signals": 4622, "not_comparable_buckets": []},
        search={
            "aggregate": {
                "groups_tested": 10,
                "seeds_tested": 67,
                "matched_seeds": 49,
                "unmatched_seeds": 18,
                "symmetric_groups": 0,
                "asymmetric_groups": 10,
            },
            "groups": {},
        },
        needs={"total_needs_review_concepts": 1809, "assessment": "review"},
        px1={"current_px1_influenced_concepts": 1692},
        route=route,
        thresholds=thresholds,
        mutation={"passed": True, "changed_tables": []},
        output_dir=tmp_path,
        validation={"operational_audit_command": "cmd", "operational_audit_result": "passed", "browser_validation": "not_run_no_ui_runtime_change"},
        review_pack_info=review_pack_info,
    )


def test_runner_is_read_only_only_and_has_no_execute_write_flags(tmp_path: Path) -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    parser = runner.build_parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}

    assert "--read-only" in option_strings
    assert "--write-chatgpt-review-pack" in option_strings
    assert "--execute" not in option_strings
    assert "--write-db" not in option_strings
    assert "--provider" not in option_strings
    assert "--mutation" not in option_strings
    assert "confirm-execution" not in source

    args = argparse.Namespace(read_only=False, output_dir=str(tmp_path), write_public_report=False, write_chatgpt_review_pack=True)
    with pytest.raises(runner.A1BlockedError, match="requires --read-only"):
        runner.run_audit(args)


def test_transaction_readonly_proof_is_required() -> None:
    proof = runner.transaction_readonly_proof({"transaction_read_only": "off", "transaction_read_only_ok": False})

    assert proof["passed"] is False
    assert proof["required"] == "on"


def test_summary_json_required_fields(tmp_path: Path) -> None:
    summary = _fake_summary(tmp_path)
    validation = runner.validate_summary_schema(summary)

    assert validation["passed"] is True
    assert runner.SUMMARY_REQUIRED_FIELDS.issubset(summary)
    assert summary["final_route_decision_status"] == runner.FINAL_ROUTE_DECISION_STATUS


def test_route_decision_matrix_schema_and_defaults() -> None:
    route = _fake_route()

    assert route["runner_report_recommendation"] == "SCV2-R2 targeted resolver/gap reduction"
    assert route["requires_external_pack_review"] is True
    assert route["final_route_decision_status"] == runner.FINAL_ROUTE_DECISION_STATUS
    for option in route["options"]:
        assert {
            "key",
            "recommended",
            "priority",
            "why",
            "blockers",
            "prerequisites",
            "expected_value",
            "risk",
            "writes_db",
            "touches_truth_path",
            "browser_validation_required",
            "user_manual_approval_required",
        }.issubset(option)


def test_unmatched_aliases_count_as_asymmetric(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_concepts(_conn, term, statuses=runner.VISIBLE_STATUSES):
        return ([1], []) if term == "Nahida" else ([], [])

    monkeypatch.setattr(runner.scv1, "concept_ids_for_term", fake_concepts)
    monkeypatch.setattr(runner.scv1, "concept_media_set_for_ids", lambda _conn, ids, statuses=runner.VISIBLE_STATUSES: {10} if ids else set())

    result = runner.evaluate_seed_groups(None, {"nahida": ["Nahida", "missing_alias"]})

    assert result["aggregate"]["matched_seeds"] == 1
    assert result["aggregate"]["unmatched_seeds"] == 1
    assert result["aggregate"]["asymmetric_groups"] == 1
    assert result["aggregate"]["asymmetry_reason_buckets"]["unmatched_alias"] == 1
    assert result["aggregate"]["unmatched_aliases_count_as_asymmetry"] is True


def test_r1_trusted_transition_is_distinguished_from_final_idempotent_rerun() -> None:
    interpretation = runner.build_r1_transition_interpretation(
        {"mode": "execute", "source_concept_delta": {"total_source_concepts_delta": 0, "concepts_newly_influenced_by_px1_evidence": 0}}
    )

    assert interpretation["trusted_transition"]["total_source_concepts_before"] == 4214
    assert interpretation["trusted_transition"]["total_source_concepts_after"] == 6094
    assert interpretation["final_current_head_execute_rerun"]["delta"] == 0
    assert interpretation["r1_had_effect"] is True
    assert interpretation["final_rerun_idempotent"] is True


def test_gap_comparison_handles_denominator_changes() -> None:
    current = {
        "total_gap_signals": 20,
        "gap_buckets": {"source_tag_present_no_source_concept_alias": 12},
        "gap_bucket_details": {"source_tag_present_no_source_concept_alias": {"total_distinct_keys": 100}},
    }
    scv1_summary = {
        "alias_gap_analysis": {
            "total_gap_signals": 5,
            "gap_buckets": {"source_tag_present_no_source_concept_alias": 2},
            "gap_bucket_details": {"source_tag_present_no_source_concept_alias": {"total_distinct_keys": 50}},
        }
    }

    comparison = runner.build_gap_vs_scv1(current, scv1_summary)

    assert comparison["total_gap_delta"] == 15
    assert comparison["bucket_comparisons"][0]["comparable"] is False
    assert "source_tag_present_no_source_concept_alias" in comparison["not_comparable_buckets"]


def test_entity_bridge_remains_blocked_when_thresholds_are_not_met() -> None:
    thresholds = runner.build_blocker_thresholds(
        {"total_gap_signals": 4622, "gap_buckets": {"source_assertion_present_not_connected": 24}},
        {"aggregate": {"asymmetric_groups": 10, "unmatched_seeds": 18}},
        {"total_needs_review_concepts": 1809},
        {"source_metadata_distinct_eligible_media_pct": 14.4},
    )
    route = _fake_route()
    blockers = runner.entity_bridge_blockers(route, thresholds)

    assert thresholds["entity_bridge"]["blocked"] is True
    assert blockers["blocked"] is True


def test_dedup1_remains_not_useful_when_exact_duplicate_groups_are_zero() -> None:
    route = _fake_route()
    dedup = runner.decision_for_option(route, "DEDUP1", status_key="not_useful_zero_exact_duplicate_groups")

    assert dedup["recommended"] is False
    assert dedup["status"] == "not_useful_zero_exact_duplicate_groups"
    assert "zero" in dedup["why"].lower()


def test_px1_b_can_be_deferred_when_resolver_gaps_dominate() -> None:
    route = _fake_route()
    px1_b = runner.decision_for_option(route, "PX1-B", status_key="deferred_pending_r2_or_pack_audit")

    assert px1_b["recommended"] is False
    assert px1_b["status"] == "deferred_pending_r2_or_pack_audit"


def test_review_pack_redaction_catches_paths_ids_tokens_and_filenames() -> None:
    unsafe = (
        r'C:\Users\kyloris\Pictures\private.png '
        '"media_id": 123, "source_metadata_record_id": 456, '
        "Authorization: Bearer abcdefghijk "
        "secret_12345678_p0.jpg"
    )

    findings = runner.scan_text_for_review_pack_leaks(unsafe)
    finding_types = {item["type"] for item in findings}

    assert "local_path_or_private_root" in finding_types
    assert "private_json_key_or_raw_field" in finding_types
    assert "secret_or_auth_like" in finding_types
    assert "media_filename_like" in finding_types


def test_review_pack_labels_are_private_refs_except_allowlisted_public_seed_labels() -> None:
    raw_short_source_label = "miku"
    public_seed_label = "Nahida"

    assert runner.safe_label(raw_short_source_label, fallback="[redacted source value]") != raw_short_source_label
    assert runner.safe_label(raw_short_source_label, fallback="[redacted source value]").startswith("[redacted source value]:label_")
    assert runner.safe_label(public_seed_label, fallback="[redacted seed]", allow_public_seed=True) == public_seed_label

    findings = runner.scan_json_payload_for_review_pack_leaks(
        {
            "display_label": raw_short_source_label,
            "search_seed_label": raw_short_source_label,
            "canonical_key_hash": "abcdef1234567890",
        }
    )
    finding_types = {item["type"] for item in findings}

    assert "display_label_raw_private_label" in finding_types
    assert "search_seed_label_raw_private_label" in finding_types
    assert "unsalted_or_dictionary_attackable_key_hash" in finding_types
    assert runner.scan_json_payload_for_review_pack_leaks({"search_seed_label": public_seed_label}) == []


def test_concepts_without_media_uses_visible_status_denominator(monkeypatch: pytest.MonkeyPatch) -> None:
    concepts = [
        {"id": 1, "status": "active"},
        {"id": 2, "status": "needs_review"},
        {"id": 3, "status": "superseded"},
    ]

    monkeypatch.setattr(runner.scv1, "concept_media_set_for_ids", lambda _conn, ids: {10} if ids == [1] else set())
    monkeypatch.setattr(runner, "rows_by_keys", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "alias_count_by_role_status_provider", lambda _conn: [])
    monkeypatch.setattr(runner.scv1, "count_table", lambda *_args, **_kwargs: {"count": 0})
    monkeypatch.setattr(runner.scv1, "group_count", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "px1_influenced_concept_ids", lambda _conn: set())

    state = runner.build_source_concept_current_state(None, concepts, [], [], {})

    assert state["concepts_with_media"] == 1
    assert state["concepts_without_media"] == 1
    assert state["concepts_without_media_status_scope"] == "visible_statuses_active_or_needs_review"
    assert state["visible_status_source_concept_count"] == 2
    assert state["all_status_source_concept_count"] == 3


def test_handoff_roadmap_and_test_workflow_updates_are_factual() -> None:
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "docs" / "project-roadmap.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs" / "test-workflow.md").read_text(encoding="utf-8")

    assert "SCV2-A1" in handoff
    assert "Phase 4.5-SCV2-A1" in roadmap
    assert "ChatGPT review pack" in workflow
    assert "Entity bridge" in handoff


def test_review_pack_policy_document_includes_required_categories() -> None:
    policy = (ROOT / "docs" / "chatgpt-review-pack-policy.md").read_text(encoding="utf-8")

    assert "Review Pack Required" in policy
    assert "Review Pack Recommended" in policy
    assert "Review Pack Not Normally Required" in policy
    assert "provisional_pending_chatgpt_pack_audit" in policy
    assert "route-decision phases" in policy


def test_review_pack_manifest_checksums_readme_and_directories_are_generated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_md = tmp_path / "report.md"
    report_json = tmp_path / "summary.json"
    report_md.write_text("# Safe report\n", encoding="utf-8")
    report_json.write_text('{"safe": true}\n', encoding="utf-8")
    monkeypatch.setattr(runner, "PUBLIC_REPORT_MD", report_md)
    monkeypatch.setattr(runner, "PUBLIC_REPORT_JSON", report_json)
    summary = _fake_summary(tmp_path)
    audit_data = {
        "current_baseline": summary["current_baseline"],
        "source_metadata_coverage": summary["source_metadata_coverage"],
        "source_concept_current_state": summary["source_concept_current_state"],
        "gap_audit": summary["gap_audit"],
        "gap_vs_scv1": summary["gap_vs_scv1"],
        "search_seed_symmetry": summary["search_seed_symmetry"],
        "needs_review_triage": summary["needs_review_triage"],
        "px1_evidence_impact": summary["px1_evidence_impact"],
        "route_decision_matrix": summary["route_decision_matrix"],
        "blocker_thresholds": {"entity_bridge": {"blocked": True}},
        "mutation_proof": summary["mutation_proof"],
        "transaction_readonly_proof": summary["transaction_readonly_proof"],
    }
    samples = {filename: [{"stable_private_concept_ref": "concept_abc", "reason_bucket": filename}] for filename in runner.REVIEW_PACK_SAMPLE_FILES}

    pack = runner.generate_review_pack(tmp_path, summary, audit_data, samples)
    pack_dir = tmp_path / "chatgpt-review-pack"

    assert pack["generated"] is True
    assert pack["redaction_passed"] is True
    assert (pack_dir / "manifest.json").exists()
    assert (pack_dir / "checksums.json").exists()
    assert (pack_dir / "README_FOR_CHATGPT_REVIEW.md").exists()
    assert (pack_dir / "public-report-copy").is_dir()
    assert (pack_dir / "audit-data").is_dir()
    assert (pack_dir / "review-samples").is_dir()
    assert (pack_dir / "redaction").is_dir()
    assert pack["sample_files_present"] is True
    assert pack["zip_path"].exists()
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    checksums = json.loads((pack_dir / "checksums.json").read_text(encoding="utf-8"))
    redaction_report = json.loads((pack_dir / "redaction" / "redaction-report.json").read_text(encoding="utf-8"))
    copied_summary = json.loads((pack_dir / "public-report-copy" / report_json.name).read_text(encoding="utf-8"))

    assert manifest["checksum_count"] == len(checksums)
    assert pack["checksum_count"] == len(checksums)
    assert set(redaction_report["scanned_files"]) == set(manifest["included_files"])
    assert pack["redaction_scan_covers_final_file_set"] is True
    assert manifest["public_report_copy_source"] == "rendered_from_current_summary"
    assert copied_summary["generated_at"] == summary["generated_at"]
    assert "canonical_key_hash" not in "\n".join(path.read_text(encoding="utf-8") for path in (pack_dir / "review-samples").glob("*.jsonl"))


def test_review_pack_redaction_scans_every_file(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    (pack_dir / "nested").mkdir(parents=True)
    (pack_dir / "safe.json").write_text('{"ok": true}\n', encoding="utf-8")
    (pack_dir / "nested" / "unsafe.json").write_text(r'{"path": "C:\Users\kyloris\Pictures\private.png"}', encoding="utf-8")
    (pack_dir / "nested" / "unsafe-label.jsonl").write_text('{"display_label": "miku"}\n', encoding="utf-8")

    scan = runner.scan_review_pack_directory(pack_dir)
    finding_types = {item["type"] for item in scan["findings"]}

    assert scan["scanned_file_count"] == 3
    assert scan["passed"] is False
    assert any(item["path"] == "nested/unsafe.json" for item in scan["findings"])
    assert "display_label_raw_private_label" in finding_types


def test_final_route_decision_status_is_provisional_for_a1(tmp_path: Path) -> None:
    summary = _fake_summary(tmp_path)

    assert summary["final_route_decision_status"] == "provisional_pending_chatgpt_pack_audit"
    assert summary["chatgpt_review_pack"]["upload_required_for_final_audit"] is True


def test_public_report_requires_user_upload_of_chatgpt_review_pack(tmp_path: Path) -> None:
    summary = _fake_summary(tmp_path, review_pack_info={"generated": True, "redaction_passed": True})
    report = runner.public_report_markdown(summary)

    assert "upload the local" in report
    assert "before final route approval" in report
    assert "provisional_pending_chatgpt_pack_audit" in report
