"""Focused tests for Phase 4.5-SCV1 read-only coverage audit runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_phase45_scv1_source_concept_coverage_audit as runner  # noqa: E402


def test_runner_refuses_without_read_only_flag(tmp_path: Path) -> None:
    args = argparse.Namespace(
        read_only=False,
        output_dir=str(tmp_path),
        write_public_report=False,
    )

    with pytest.raises(runner.AuditBlockedError, match="requires --read-only"):
        runner.run_audit(args)


def test_forbidden_table_mutation_proof_detects_count_changes() -> None:
    before = {
        "tables": {
            "blombooru_media": {"status": "present", "count": 10},
            "blombooru_tags": {"status": "present", "count": 5},
        }
    }
    after = {
        "tables": {
            "blombooru_media": {"status": "present", "count": 11},
            "blombooru_tags": {"status": "present", "count": 5},
        }
    }

    proof = runner.compare_mutation_counts(before, after)

    assert proof["passed"] is False
    assert proof["changed_tables"] == [{"table": "blombooru_media", "before": 10, "after": 11}]


def test_forbidden_tables_include_source_concept_signals() -> None:
    assert "blombooru_source_concept_signals" in runner.FORBIDDEN_TABLES


def test_source_concept_signals_mutation_proof_detects_count_changes() -> None:
    before = {
        "tables": {
            "blombooru_source_concept_signals": {"status": "present", "count": 10},
        }
    }
    after = {
        "tables": {
            "blombooru_source_concept_signals": {"status": "present", "count": 11},
        }
    }

    proof = runner.compare_mutation_counts(before, after)

    assert proof["passed"] is False
    assert proof["changed_tables"] == [{"table": "blombooru_source_concept_signals", "before": 10, "after": 11}]


def test_public_redaction_scan_catches_paths_filenames_and_secrets() -> None:
    unsafe = (
        r"C:\Users\kyloris\Pictures\private.png "
        "Authorization: Bearer abcdefghijk "
        "api_key=secret-token-12345 "
        "vacation_2024_jpg"
    )

    findings = runner.scan_public_text(unsafe)
    finding_types = {item["type"] for item in findings}

    assert "local_path_or_private_root" in finding_types
    assert "media_filename_like" in finding_types
    assert "canonical_filename_like" in finding_types
    assert "secret_assignment_like" in finding_types
    assert "authorization_bearer_like" in finding_types


def test_final_public_redaction_scan_runs_after_final_public_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_md = tmp_path / "phase-report.md"
    report_json = tmp_path / "phase-summary.json"
    output_dir = tmp_path / "private"
    output_dir.mkdir()
    summary = minimal_public_summary(output_dir)
    summary["private_artifacts"] = runner.public_private_artifact_summary(bundle_created=True)
    expected_zip_path = f".local_manifests/{runner.PHASE_SLUG}.zip"
    scanned = {"called": False}

    monkeypatch.setattr(runner, "PUBLIC_REPORT_MD", report_md)
    monkeypatch.setattr(runner, "PUBLIC_REPORT_JSON", report_json)

    def fake_scan(paths, *, checked_at=None):
        scanned["called"] = True
        assert report_md in paths
        assert report_json in paths
        payload = json.loads(report_json.read_text(encoding="utf-8"))
        combined_text = report_md.read_text(encoding="utf-8") + "\n" + report_json.read_text(encoding="utf-8")
        assert payload["private_artifacts"]["private_artifact_bundle_created"] is True
        assert payload["private_artifacts"]["exact_private_paths_public"] is False
        assert payload["redaction_privacy_audit"]["checked_at"] == checked_at
        assert payload["redaction_privacy_audit"]["final_public_scan_after_public_fields_finalized"] is True
        assert "private_artifact_zip" not in combined_text
        assert expected_zip_path not in combined_text
        return {
            "checked_at": checked_at,
            "passed": True,
            "public_paths": [path.name for path in paths],
            "findings": [],
        }

    monkeypatch.setattr(runner, "scan_public_artifacts", fake_scan)

    redaction = runner.write_reports_and_redaction(summary, output_dir)

    assert scanned["called"] is True
    assert redaction["passed"] is True
    final_payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert final_payload["redaction_privacy_audit"]["checked_at"] == redaction["checked_at"]
    assert final_payload["redaction_privacy_audit"]["final_public_scan_after_public_fields_finalized"] is True


def test_public_private_artifact_summary_does_not_expose_exact_zip_path() -> None:
    public_artifacts = runner.public_private_artifact_summary(bundle_created=True)
    text_value = json.dumps(public_artifacts, ensure_ascii=False)

    assert public_artifacts["private_artifact_bundle_created"] is True
    assert public_artifacts["exact_private_paths_public"] is False
    assert "private_artifact_zip" not in text_value
    assert f"{runner.PHASE_SLUG}.zip" not in text_value


def test_search_symmetry_comparison_detects_exact_symmetry(monkeypatch: pytest.MonkeyPatch) -> None:
    concepts = [{"id": 1, "status": "active", "primary_display_name": "Kamisato Ayaka", "concept_type_hint": "character"}]
    aliases = [
        {"id": 1, "concept_id": 1, "status": "active", "display_name": "Kamisato Ayaka", "alias_key": "kamisato_ayaka"},
        {"id": 2, "concept_id": 1, "status": "active", "display_name": "kamisato_ayaka", "alias_key": "kamisato_ayaka"},
    ]
    monkeypatch.setattr(runner, "concept_ids_for_term", lambda _conn, _term, statuses=runner.VISIBLE_STATUSES: ([1], []))
    monkeypatch.setattr(runner, "concept_media_set_for_ids", lambda _conn, _ids, statuses=runner.VISIBLE_STATUSES: {100, 101})

    metrics, samples = runner.audit_search_symmetry(None, concepts, aliases)

    assert metrics["concepts_checked"] == 1
    assert metrics["aliases_checked"] == 2
    assert metrics["exact_symmetric_concepts"] == 1
    assert metrics.get("asymmetric_concepts", 0) == 0
    assert samples[0]["mismatch_type"] == "exact_symmetric"


def test_search_symmetry_comparison_detects_asymmetric_media_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    concepts = [{"id": 1, "status": "active", "primary_display_name": "Split Alias", "concept_type_hint": "character"}]
    aliases = [
        {"id": 1, "concept_id": 1, "status": "active", "display_name": "alias_a", "alias_key": "alias_a"},
        {"id": 2, "concept_id": 1, "status": "active", "display_name": "alias_b", "alias_key": "alias_b"},
    ]

    def fake_ids(_conn, term: str, statuses=runner.VISIBLE_STATUSES):
        return ([1], []) if term == "alias_a" else ([2], [])

    def fake_media(_conn, ids, statuses=runner.VISIBLE_STATUSES):
        return {100} if ids == [1] else {200}

    monkeypatch.setattr(runner, "concept_ids_for_term", fake_ids)
    monkeypatch.setattr(runner, "concept_media_set_for_ids", fake_media)

    metrics, samples = runner.audit_search_symmetry(None, concepts, aliases)

    assert metrics["asymmetric_concepts"] == 1
    assert metrics["severe_asymmetry_count"] == 1
    assert metrics["fragmentation_count"] == 1
    assert samples[0]["mismatch_type"] == "asymmetric_media_set"


def test_hidden_rejected_ambiguous_superseded_statuses_do_not_count_as_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    hidden_statuses = ["rejected", "ambiguous", "superseded"]
    concepts = [
        {"id": index + 1, "status": status, "primary_display_name": status, "concept_type_hint": "character"}
        for index, status in enumerate(hidden_statuses)
    ]
    aliases = [
        {"id": index + 1, "concept_id": index + 1, "status": status, "display_name": status, "alias_key": status}
        for index, status in enumerate(hidden_statuses)
    ]
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], []

    monkeypatch.setattr(runner, "concept_ids_for_term", fail_if_called)

    metrics, samples = runner.audit_search_symmetry(None, concepts, aliases)

    assert metrics["concepts_checked"] == 0
    assert metrics["aliases_checked"] == 0
    assert metrics["active_concepts_checked"] == 0
    assert metrics["needs_review_concepts_checked"] == 0
    assert samples == []
    assert called is False


def test_hidden_raw_match_without_visible_leak_keeps_leak_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    concepts = [
        {"id": 1, "status": "active", "primary_display_name": "Visible Alias", "concept_type_hint": "character"},
        {"id": 2, "status": "superseded", "primary_display_name": "Hidden Alias", "concept_type_hint": "character"},
    ]
    aliases = [{"id": 1, "concept_id": 1, "status": "active", "display_name": "visible_alias", "alias_key": "visible_alias"}]
    monkeypatch.setattr(
        runner,
        "concept_ids_for_term",
        lambda *_args, **_kwargs: ([1], [{"concept_id": 2, "concept_status": "superseded"}]),
    )
    monkeypatch.setattr(runner, "concept_media_set_for_ids", lambda *_args, **_kwargs: {123})

    metrics, _samples = runner.audit_search_symmetry(None, concepts, aliases)

    assert metrics["hidden_status_raw_match_count"] == 1
    assert metrics["hidden_status_leak_count"] == 0


def test_hidden_visible_closure_leak_increments_leak_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    concepts = [
        {"id": 1, "status": "active", "primary_display_name": "Visible Alias", "concept_type_hint": "character"},
        {"id": 2, "status": "superseded", "primary_display_name": "Hidden Alias", "concept_type_hint": "character"},
    ]
    aliases = [{"id": 1, "concept_id": 1, "status": "active", "display_name": "visible_alias", "alias_key": "visible_alias"}]
    monkeypatch.setattr(
        runner,
        "concept_ids_for_term",
        lambda *_args, **_kwargs: ([1, 2], [{"concept_id": 2, "concept_status": "superseded"}]),
    )
    monkeypatch.setattr(runner, "concept_media_set_for_ids", lambda *_args, **_kwargs: {123})

    metrics, samples = runner.audit_search_symmetry(None, concepts, aliases)

    assert metrics["hidden_status_raw_match_count"] == 1
    assert metrics["hidden_status_leak_count"] == 1
    assert samples[0]["hidden_leak_concept_ids"] == "2"


def test_needs_review_is_labeled_distinctly_from_active(monkeypatch: pytest.MonkeyPatch) -> None:
    concepts = [{"id": 1, "status": "needs_review", "primary_display_name": "Review Alias", "concept_type_hint": "character"}]
    aliases = [{"id": 1, "concept_id": 1, "status": "needs_review", "display_name": "review_alias", "alias_key": "review_alias"}]
    monkeypatch.setattr(runner, "concept_ids_for_term", lambda *_args, **_kwargs: ([1], []))
    monkeypatch.setattr(runner, "concept_media_set_for_ids", lambda *_args, **_kwargs: {123})

    metrics, samples = runner.audit_search_symmetry(None, concepts, aliases)

    assert metrics["active_concepts_checked"] == 0
    assert metrics["needs_review_concepts_checked"] == 1
    assert samples[0]["concept_status"] == "needs_review"


def test_decision_matrix_selects_conservative_next_steps() -> None:
    decision = runner.decide_next_phase(
        media={"total_media": 100, "eligible_media_count": 80, "media_without_ai_tags": 70, "tags": {"translation": {"total": 10}, "total_tags": 100}},
        source_layer={"source_records": {"linked_to_media": 5}},
        concepts={"total_source_concepts": 20},
        symmetry={"severe_asymmetry_count": 2, "asymmetric_concepts": 3},
        alias_gaps={"total_gap_signals": 12},
        needs_review={"total_needs_review_concepts": 6},
        redaction_passed=True,
    )

    assert decision["recommended_next_phase"] == "source_concept_alias_resolver_improvement"
    assert decision["answers"]["is_5k_10k_expansion_justified_now"] is False
    assert decision["answers"]["is_entity_bridge_justified_now"] is False
    assert any(item["key"] == "run_ledger_or_phase39_prerequisite" and item["recommended"] for item in decision["options"])


def test_alias_gap_full_counts_are_independent_of_sample_limit() -> None:
    rows = [{"key_value": f"missing_{index}", "label": f"Missing {index}", "count": 10 - index} for index in range(6)]

    sampled, detail = runner.summarize_missing_key_gap_rows(rows, set(), sample_limit=2)

    assert detail["total_distinct_keys"] == 6
    assert detail["missing_distinct_keys"] == 6
    assert detail["sampled_missing_keys"] == 2
    assert detail["sample_limit"] == 2
    assert detail["counts_are_full"] is True
    assert detail["sampling_affects"] == "examples_only"
    assert len(sampled) == 2


def test_alias_gap_sample_limit_does_not_change_total_count() -> None:
    rows = [{"key_value": f"missing_{index}", "label": f"Missing {index}", "count": 1} for index in range(8)]

    _sampled_one, detail_one = runner.summarize_missing_key_gap_rows(rows, set(), sample_limit=1)
    _sampled_three, detail_three = runner.summarize_missing_key_gap_rows(rows, set(), sample_limit=3)

    assert detail_one["missing_distinct_keys"] == 8
    assert detail_three["missing_distinct_keys"] == 8
    assert detail_one["sampled_missing_keys"] == 1
    assert detail_three["sampled_missing_keys"] == 3


def minimal_public_summary(tmp_path: Path) -> dict:
    decision = runner.decide_next_phase(
        media={"total_media": 0, "eligible_media_count": 0, "media_without_ai_tags": 0, "tags": {"translation": {"total": 0}, "total_tags": 0}},
        source_layer={"source_records": {"linked_to_media": 0}},
        concepts={"total_source_concepts": 0},
        symmetry={"severe_asymmetry_count": 0, "asymmetric_concepts": 0},
        alias_gaps={"total_gap_signals": 0},
        needs_review={"total_needs_review_concepts": 0},
        redaction_passed=True,
    )
    return runner.build_public_summary(
        db_identity={
            "violet_env": "development",
            "database": "blombooru",
            "host": "localhost",
            "port": 5432,
            "connected_database": "blombooru",
            "server_port": 5432,
            "transaction_read_only": "on",
            "transaction_read_only_ok": True,
            "git_branch": runner.BRANCH,
            "git_sha": "abc123",
            "python_executable": "python",
            "recorded_at": "2026-06-08T00:00:00Z",
        },
        proof={"passed": True, "changed_tables": [], "missing_tables": [], "forbidden_tables_checked": list(runner.FORBIDDEN_TABLES)},
        media={"total_media": 0, "eligible_policy": "content_class IN ('anime', 'unknown')", "eligible_media_count": 0, "eligible_media_pct": 0, "media_with_any_tags": 0, "media_with_ai_tag_provenance": 0, "media_without_ai_tags": 0, "media_with_source_layer_signals": 0, "media_without_source_layer_signals": 0, "media_with_source_concept_evidence_or_links": 0, "content_class_distribution": {}, "tags": {"translation": {"total": 0}, "total_tags": 0}},
        source_layer={"source_records": {"by_provider": {}, "linked_to_media": 0}, "f7a_candidate_coverage": {"distinct_media_with_candidates": 0}, "source_assertions_by_status": {}},
        concepts={"total_source_concepts": 0, "by_status": {}, "by_concept_type_hint": {}, "aliases_total": 0, "evidence_total": 0, "search_index_total": 0, "concepts_with_no_media": 0, "concepts_with_no_aliases": 0, "concepts_with_no_evidence": 0, "concepts_with_no_search_index": 0, "same_alias_key_across_multiple_concepts": []},
        symmetry={"concepts_checked": 0, "aliases_checked": 0, "exact_symmetric_concepts": 0, "explainable_no_media_concepts": 0, "asymmetric_concepts": 0, "severe_asymmetry_count": 0, "one_way_link_count": 0, "fragmentation_count": 0, "overbroad_expansion_count": 0, "hidden_status_raw_match_count": 0, "hidden_status_leak_count": 0, "metacharacter_alias_count": 0},
        alias_gaps={
            "gap_buckets": {},
            "gap_bucket_details": {},
            "sample_limit_policy": {
                "counts_are_full": True,
                "samples_are_limited_to_examples_only": True,
                "default_sample_limit": runner.ALIAS_GAP_SAMPLE_LIMIT,
            },
            "total_gap_signals": 0,
            "recommended_next_fix_category": "entity_bridge_preview_design",
            "seed_results": {"nahida_prompt_and_doc1": {"seed_values_tested": ["Nahida"], "matched_alias_values": [], "matched_concept_count": 0, "matched_media_count": 0, "gap_detected": True}},
        },
        needs_review={"total_needs_review_concepts": 0, "assessment": "none"},
        redaction={"passed": True, "findings": [], "public_paths": []},
        decision=decision,
        output_dir=tmp_path,
        validation={"operational_audit_command": "unit", "operational_audit_result": "not_run"},
    )


def test_summary_json_has_required_fields(tmp_path: Path) -> None:
    summary = minimal_public_summary(tmp_path)
    required = {
        "phase",
        "title",
        "branch",
        "generated_at",
        "db_identity",
        "read_only_proof",
        "media_coverage",
        "source_layer_coverage",
        "source_concept_inventory",
        "search_symmetry",
        "alias_gap_analysis",
        "needs_review_analysis",
        "redaction_privacy_audit",
        "seed_results",
        "decision_matrix",
        "recommended_next_phase",
        "validation",
        "safety",
        "artifact_lifecycle",
        "private_artifacts",
    }

    assert required.issubset(summary)
    assert summary["private_artifacts"]["private_artifact_bundle_created"] is False
    assert summary["private_artifacts"]["exact_private_paths_public"] is False
    assert summary["alias_gap_analysis"]["gap_bucket_details"] == {}
    assert summary["alias_gap_analysis"]["sample_limit_policy"]["counts_are_full"] is True
    json.dumps(summary, ensure_ascii=False)


def test_public_report_does_not_contain_forbidden_private_patterns(tmp_path: Path) -> None:
    report = runner.public_report_markdown(minimal_public_summary(tmp_path))

    assert runner.scan_public_text(report) == []
