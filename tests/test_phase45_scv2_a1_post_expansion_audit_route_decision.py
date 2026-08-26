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
        "transaction_isolation": "repeatable read",
        "snapshot_id_present": True,
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
        transaction_proof={
            "passed": True,
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
            "snapshot_id_present": True,
        },
        snapshot_proof={
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
            "snapshot_id_present": True,
            "stable_snapshot": True,
        },
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
        px1={
            "px1_strict_influenced_concepts": 1692,
            "pixiv_all_influenced_concepts": 3510,
            "non_px1_pixiv_influenced_concepts": 1818,
            "route_decision_px1_impact_metric": "px1_strict_influenced_concepts",
        },
        route=route,
        thresholds=thresholds,
        mutation={"passed": True, "changed_tables": []},
        output_dir=tmp_path,
        validation={"operational_audit_command": "cmd", "operational_audit_result": "passed", "browser_validation": "not_run_no_ui_runtime_change"},
        review_pack_info=review_pack_info,
    )


def _fake_audit_data(summary: dict) -> dict:
    return {
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
        "db_snapshot_proof": summary["db_snapshot_proof"],
    }


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
    assert proof["required_isolation"] == "repeatable read or serializable"


def test_repeatable_read_readonly_snapshot_proof_is_required() -> None:
    good = runner.transaction_readonly_proof(
        {
            "transaction_read_only": "on",
            "transaction_read_only_ok": True,
            "transaction_isolation": "repeatable read",
            "snapshot_id_present": True,
        }
    )
    weak = runner.transaction_readonly_proof(
        {
            "transaction_read_only": "on",
            "transaction_read_only_ok": True,
            "transaction_isolation": "read committed",
            "snapshot_id_present": True,
        }
    )

    assert good["passed"] is True
    assert good["stable_snapshot"] is True
    assert weak["passed"] is False


def test_summary_json_required_fields(tmp_path: Path) -> None:
    summary = _fake_summary(tmp_path)
    validation = runner.validate_summary_schema(summary)

    assert validation["passed"] is True
    assert runner.SUMMARY_REQUIRED_FIELDS.issubset(summary)
    assert summary["final_route_decision_status"] == runner.FINAL_ROUTE_DECISION_STATUS


def test_route_decision_matrix_schema_and_defaults() -> None:
    route = _fake_route()

    assert route["runner_report_recommendation"] == "blocked_pending_pipeline_fidelity_remediation"
    assert route["pre_incident_runner_recommendation"] == "SCV2-R2 targeted resolver/gap reduction"
    assert route["recommended_next_phase"] == runner.PIPELINE_FIDELITY_REMEDIATION_NEXT_PHASE
    assert route["requires_external_pack_review"] is False
    assert route["route_approval_blocked"] is True
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
        assert option["recommended"] is False


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
    assert runner.safe_label(raw_short_source_label, fallback="[redacted source value]") == "[redacted source value]"
    assert runner.safe_label(public_seed_label, fallback="[redacted seed]", allow_public_seed=True) == public_seed_label
    refs = runner.ReviewPackRefBuilder()
    assert refs.label(raw_short_source_label) == "label_ref_000001"
    assert refs.label(raw_short_source_label) == "label_ref_000001"
    assert refs.ref("concept", 42) == "concept_ref_000001"
    assert raw_short_source_label not in refs.label(raw_short_source_label)

    findings = runner.scan_json_payload_for_review_pack_leaks(
        {
            "display_label": raw_short_source_label,
            "search_seed_label": raw_short_source_label,
            "canonical_key_hash": "abcdef1234567890",
            "stable_private_concept_ref": "concept_0123456789abcdef",
        }
    )
    finding_types = {item["type"] for item in findings}

    assert "display_label_raw_private_label" in finding_types
    assert "search_seed_label_raw_private_label" in finding_types
    assert "unsalted_or_dictionary_attackable_key_hash" in finding_types
    assert "fixed_salt_or_hash_ref" in finding_types
    assert runner.scan_json_payload_for_review_pack_leaks({"display_label": "label_ref_000001", "stable_private_concept_ref": "concept_ref_000001"}) == []
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
    monkeypatch.setattr(runner, "px1_strict_influenced_concept_ids", lambda _conn: set())
    monkeypatch.setattr(runner, "pixiv_all_influenced_concept_ids", lambda _conn: set())

    state = runner.build_source_concept_current_state(None, concepts, [], [], {})

    assert state["concepts_with_media"] == 1
    assert state["concepts_without_media"] == 1
    assert state["concepts_without_media_status_scope"] == "visible_statuses_active_or_needs_review"
    assert state["visible_status_source_concept_count"] == 2
    assert state["all_status_source_concept_count"] == 3


def test_strict_px1_impact_excludes_non_px1_pixiv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "strict_px1_source_metadata_record_ids", lambda _conn: {100, 101})
    monkeypatch.setattr(runner, "concepts_with_source_metadata_record_ids", lambda _conn, _record_ids: {1, 2})
    monkeypatch.setattr(runner, "pixiv_all_influenced_concept_ids", lambda _conn: {1, 2, 3, 4})

    impact = runner.build_px1_evidence_impact(None, {"source_concept_delta": {"concepts_influenced_by_px1_evidence_after": 1692}})

    assert impact["px1_slug"] == runner.PX1_SLUG
    assert impact["px1_strict_influenced_concepts"] == 2
    assert impact["pixiv_all_influenced_concepts"] == 4
    assert impact["non_px1_pixiv_influenced_concepts"] == 2
    assert impact["route_decision_px1_impact_metric"] == "px1_strict_influenced_concepts"


def test_dynamic_px1_seed_groups_use_strict_px1_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict]] = []

    monkeypatch.setattr(runner.scv1, "table_exists", lambda _conn, _table: True)
    monkeypatch.setattr(runner.scv1, "column_exists", lambda _conn, _table, _column: True)

    def fake_rows_dict(_conn, sql, params=None):
        captured.append((sql, dict(params or {})))
        if "source_name_observations" in sql:
            return [{"label": "px1 name", "canonical_name_key": "px1_name", "name_role": "character", "count": 5}]
        if "source_tag_observations" in sql:
            return [{"label": "px1 tag", "canonical_tag_key": "px1_tag", "count": 4}]
        if "source_searchable_name_assertions" in sql:
            return [{"label": "px1 title", "canonical_name_key": "px1_title", "asserted_role": "work_title", "count": 3}]
        return []

    monkeypatch.setattr(runner.scv1, "rows_dict", fake_rows_dict)

    groups = runner.build_dynamic_px1_seed_groups(None)

    assert groups["px1_high_frequency_source_names_private"] == ["px1 name"]
    assert groups["px1_high_frequency_source_tags_private"] == ["px1 tag"]
    assert groups["px1_title_or_work_assertions_private"] == ["px1 title"]
    assert captured
    for sql, params in captured:
        assert "JOIN blombooru_source_metadata_records r" in sql
        assert "r.run_label = :px1_slug" in sql
        assert "r.provider_run_id" in sql
        assert "WHERE provider = 'pixiv'" not in sql
        assert params["px1_slug"] == runner.PX1_SLUG


def test_public_dirty_worktree_status_is_redacted() -> None:
    dirty = runner.public_dirty_worktree_summary("?? docs/private-file.md\n M scripts/secret.py")
    provenance = runner.build_report_provenance(_fake_db_identity(), {"dirty_worktree": dirty})

    assert dirty["clean"] is False
    assert dirty["dirty_count"] == 2
    assert dirty["status_redacted"] is True
    assert "private-file" not in provenance["dirty_worktree_status"]
    assert "secret.py" not in provenance["dirty_worktree_status"]
    assert provenance["dirty_worktree_status"] == "redacted_dirty_entries:2"


def test_handoff_roadmap_and_test_workflow_updates_are_factual() -> None:
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    current_state = json.loads(
        (ROOT / "docs" / "state" / "current-phase.json").read_text(encoding="utf-8")
    )
    roadmap = (ROOT / "docs" / "project-roadmap.md").read_text(encoding="utf-8")
    archived_roadmap = (
        ROOT / "docs" / "roadmap" / "archive" / "project-roadmap-through-scv2-sv1b.md"
    ).read_text(encoding="utf-8")
    workflow = (ROOT / "docs" / "test-workflow.md").read_text(encoding="utf-8")

    assert current_state["phase_id"] in handoff
    assert "SCV2-PX1" in handoff
    if current_state["pr_number"] is None:
        assert "PR pending creation" in handoff
    else:
        label = "Draft PR" if current_state["draft"] else "PR"
        assert f"{label} #{current_state['pr_number']}" in handoff
    assert f"target_met={str(current_state['target_met']).lower()}" in handoff
    assert f"safe_to_merge={str(current_state['safe_to_merge']).lower()}" in handoff
    assert current_state["active_blocker"]["code"] in handoff
    assert "Phase 4.5-SCV2-A1" in archived_roadmap
    assert "ChatGPT review pack" in workflow
    assert "provider metadata, SourceConcept, or model output as Entity truth" in roadmap


def test_review_pack_policy_document_includes_required_categories() -> None:
    policy = (ROOT / "docs" / "chatgpt-review-pack-policy.md").read_text(encoding="utf-8")

    assert "Review Pack Required" in policy
    assert "Review Pack Recommended" in policy
    assert "Review Pack Not Normally Required" in policy
    assert "provisional_pending_chatgpt_pack_audit" in policy
    assert "blocked_pending_pipeline_fidelity_remediation" in policy
    assert "route-decision phases" in policy


def test_review_pack_manifest_checksums_readme_and_directories_are_generated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_md = tmp_path / "report.md"
    report_json = tmp_path / "summary.json"
    report_md.write_text("# Safe report\n", encoding="utf-8")
    report_json.write_text('{"safe": true}\n', encoding="utf-8")
    monkeypatch.setattr(runner, "PUBLIC_REPORT_MD", report_md)
    monkeypatch.setattr(runner, "PUBLIC_REPORT_JSON", report_json)
    summary = _fake_summary(tmp_path)
    audit_data = _fake_audit_data(summary)
    samples = {filename: [{"stable_private_concept_ref": "concept_ref_000001", "reason_bucket": filename}] for filename in runner.REVIEW_PACK_SAMPLE_FILES}

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
    assert manifest["runtime_audit_git_sha"] == summary["runtime_audit_git_sha"]
    assert manifest["report_provenance"]["public_report_generated_from_runtime_sha"] == summary["runtime_audit_git_sha"]
    assert copied_summary["generated_at"] == summary["generated_at"]
    assert "canonical_key_hash" not in "\n".join(path.read_text(encoding="utf-8") for path in (pack_dir / "review-samples").glob("*.jsonl"))
    assert "concept_0123456789abcdef" not in "\n".join(path.read_text(encoding="utf-8") for path in (pack_dir / "review-samples").glob("*.jsonl"))


def test_review_pack_cleanup_refuses_unmarked_external_pack_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_md = tmp_path / "report.md"
    report_json = tmp_path / "summary.json"
    report_md.write_text("# Safe report\n", encoding="utf-8")
    report_json.write_text('{"safe": true}\n', encoding="utf-8")
    monkeypatch.setattr(runner, "PUBLIC_REPORT_MD", report_md)
    monkeypatch.setattr(runner, "PUBLIC_REPORT_JSON", report_json)
    summary = _fake_summary(tmp_path)
    audit_data = _fake_audit_data(summary)
    samples = {filename: [{"stable_private_concept_ref": "concept_ref_000001", "reason_bucket": filename}] for filename in runner.REVIEW_PACK_SAMPLE_FILES}
    pack_dir = tmp_path / "chatgpt-review-pack"
    pack_dir.mkdir()
    (pack_dir / "keep.txt").write_text("do not delete\n", encoding="utf-8")

    with pytest.raises(runner.A1BlockedError, match="Refusing to remove existing chatgpt-review-pack"):
        runner.generate_review_pack(tmp_path, summary, audit_data, samples)

    assert (pack_dir / "keep.txt").exists()
    (pack_dir / runner.REVIEW_PACK_GENERATED_MARKER).write_text('{"safe_to_replace": true}\n', encoding="utf-8")

    pack = runner.generate_review_pack(tmp_path, summary, audit_data, samples)

    assert pack["generated"] is True
    assert not (pack_dir / "keep.txt").exists()
    assert (pack_dir / runner.REVIEW_PACK_GENERATED_MARKER).exists()


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


def test_a1_route_approval_is_blocked_by_inc1_r1r_not_r2(tmp_path: Path) -> None:
    summary = _fake_summary(tmp_path)

    assert summary["final_route_decision_status"] == "blocked_pending_pipeline_fidelity_remediation"
    assert summary["runner_report_recommendation"] == "blocked_pending_pipeline_fidelity_remediation"
    assert summary["recommended_next_phase"] == runner.PIPELINE_FIDELITY_REMEDIATION_NEXT_PHASE
    assert summary["pre_incident_runner_recommendation"] == "SCV2-R2 targeted resolver/gap reduction"
    assert summary["route_decision_matrix"]["route_approval_blocked"] is True
    assert not any(option["recommended"] for option in summary["route_decision_matrix"]["options"])
    assert summary["chatgpt_review_pack"]["upload_required_for_final_audit"] is True
    assert summary["runtime_audit_git_sha"] == "abc123"
    assert summary["public_report_generated_from_runtime_sha"] == "abc123"
    assert summary["operational_result_reused_older_artifacts"] is False
    assert "commit cannot truthfully contain its own final SHA" in summary["final_pr_head_sha_if_different"]
    assert summary["db_snapshot_proof"]["transaction_isolation"] == "repeatable read"


def test_public_report_requires_user_upload_of_chatgpt_review_pack(tmp_path: Path) -> None:
    summary = _fake_summary(tmp_path, review_pack_info={"generated": True, "redaction_passed": True})
    report = runner.public_report_markdown(summary)

    assert "Uploading the pack does not approve R2" in report
    assert "blocked_pending_pipeline_fidelity_remediation" in report
    assert "Provenance / SHA boundary" in report
    assert "Strict PX1-influenced concepts" in report
    assert "All Pixiv-influenced concepts" in report
    assert "snapshot id redacted" in report
