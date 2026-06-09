"""Focused tests for Phase 4.5-SCV2-P0 read-only inventory/policy runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_phase45_scv2_p0_controlled_medium_expansion_policy as runner  # noqa: E402


def _fake_db_identity() -> dict:
    return {
        "violet_env": "development",
        "database": "blombooru",
        "connected_database": "blombooru",
        "host": "localhost",
        "port": 5432,
        "connected_user": "postgres",
        "server_port": 5432,
        "transaction_read_only": "on",
        "transaction_read_only_ok": True,
        "git_branch": runner.BRANCH,
        "git_sha": "abc123",
        "python_executable": "python.exe",
        "recorded_at": "2026-06-08T00:00:00Z",
        "db_resolution": {
            "app_compatible": True,
            "settings_json_exists": True,
            "database_file_settings_used": True,
            "field_sources": {"host": "settings_json", "port": "settings_json", "name": "settings_json"},
            "violet_env_source": "process_env",
            "runner_matches_app_equivalent": True,
            "urls_match": True,
            "password_present": True,
            "password_value_recorded": False,
        },
    }


def _fake_baseline() -> dict:
    return {
        "total_media": 1989,
        "eligible_policy": "content_class IN ('anime', 'unknown')",
        "eligible_media": 1936,
        "eligible_media_pct": 97.34,
        "non_anime_count": 53,
        "content_class_distribution": {"anime": 1882, "unknown": 54, "non_anime": 53},
        "media_with_any_tags": 1962,
        "media_with_any_tags_pct": 98.64,
        "media_with_ai_tag_provenance": 1962,
        "eligible_media_with_ai_tag_provenance": 1936,
        "eligible_media_without_ai_tag_provenance": 0,
        "eligible_ai_tag_provenance_pct": 100.0,
        "current_eligible_ai_tag_coverage_effectively_complete": True,
        "media_with_source_concept_evidence_or_links": 1266,
        "media_with_source_layer_signals": 1338,
        "media_with_source_metadata": 60,
        "media_with_source_metadata_by_distinct_media": 60,
        "eligible_media_with_source_metadata_by_distinct_media": 60,
        "source_metadata_total_rows": 200,
        "media_without_source_metadata": 1929,
        "comparison_baseline_media_count": 1989,
        "comparison_baseline_policy": "Use current DB media count as the denominator for pre/post medium expansion comparisons.",
    }


def _fake_pixiv_inventory() -> dict:
    return {
        "method": "DB-derived signals only",
        "signals_considered": [],
        "total_pixiv_like_media_candidates": 420,
        "distinct_pixiv_like_media_candidates": 420,
        "pixiv_like_candidates_with_existing_source_metadata": 60,
        "pixiv_like_candidates_without_source_metadata": 360,
        "pixiv_like_candidates_with_pixiv_source_metadata": 45,
        "pixiv_like_candidates_with_ai_tag_provenance": 420,
        "pixiv_like_candidates_without_ai_tag_provenance": 0,
        "pixiv_like_eligible_media": 415,
        "pixiv_like_non_eligible_media": 5,
        "pixiv_like_candidate_pct_of_total_media": 21.12,
        "pixiv_like_metadata_backlog_pct": 85.71,
        "reason_category_counts": {"filename_pixiv_id_pattern": 400},
        "top_source_prior_categories": {"filename_pixiv_id_pattern": 400},
        "detected_distinct_pixiv_ids": 400,
        "duplicate_pixiv_id_candidate_groups": 10,
        "duplicate_pixiv_id_candidate_media_count": 25,
        "invalid_or_marker_only_pixiv_id_candidates": 3,
        "ambiguous_pixiv_id_candidates": 2,
        "user_claim_assessment": {
            "claim": "Already-imported Pixiv-like media likely exceed source metadata-covered media by a wide margin.",
            "confirmed": True,
            "evidence": "420 Pixiv-like candidates; 60 with any source metadata; 360 metadata backlog.",
        },
        "private_candidates": [{"media_id": 1}],
        "private_duplicate_pixiv_id_groups": {},
    }


def _fake_gap() -> dict:
    return {
        "source_metadata_total_rows": 200,
        "source_metadata_linked_rows": 60,
        "source_metadata_distinct_media_covered": 60,
        "source_metadata_distinct_media_pct": 3.02,
        "source_metadata_distinct_eligible_media_covered": 60,
        "source_metadata_distinct_eligible_media_pct": 3.1,
        "distinct_pixiv_like_media_covered_by_source_metadata": 60,
        "distinct_pixiv_like_media_missing_source_metadata": 360,
        "already_imported_pixiv_like_media_lacking_metadata": 360,
        "already_imported_non_pixiv_media_lacking_metadata": 1569,
        "external_new_media_expansion_candidates_not_yet_in_db": {"known_in_p0": False},
        "source_metadata_by_provider": {"pixiv": 97},
        "source_metadata_by_status": {"observed": 200},
        "source_tag_observation_counts_by_provider": {"pixiv": 556},
        "source_name_observation_counts_by_provider": {"pixiv": 236},
        "source_assertions_by_provider": {"pixiv": 283},
        "source_assertions_by_status": {"searchable_active": 182},
        "source_name_candidates_by_provider": {"pixiv": 852},
        "source_name_candidates_by_status": {"active": 903},
        "source_name_candidates_by_candidate_status": {"active_candidate": 495},
        "f7a_candidates_by_provider_status": {"pixiv:active": 852},
        "source_concept_signals_by_provider": {"pixiv": 100},
        "source_concept_signals_by_source_kind": {"pixiv_tag": 100},
        "source_concept_signals_by_status": {"active": 100},
        "source_concept_evidence_by_provider": {"pixiv": 100},
        "source_concept_evidence_by_status": {"active": 100},
        "source_concept_evidence_by_type": {"source_signal": 100},
        "gap_buckets": {
            "already_imported_pixiv_like_media_lacking_metadata": 360,
            "already_imported_non_pixiv_media_lacking_metadata": 1569,
            "external_new_media_expansion_candidates_not_yet_in_db": None,
        },
        "private_pixiv_like_missing_metadata_media_refs": ["media_hash"],
    }


def _fake_summary() -> dict:
    baseline = _fake_baseline()
    pixiv = _fake_pixiv_inventory()
    gap = _fake_gap()
    ai_policy = runner.build_ai_tag_continuity_policy(baseline)
    medium_policy = runner.build_medium_expansion_policy(baseline["total_media"])
    return runner.build_public_summary(
        db_identity=_fake_db_identity(),
        baseline=baseline,
        pixiv=pixiv,
        gap=gap,
        ai_policy=ai_policy,
        medium_policy=medium_policy,
        medium_ledger=runner.build_medium_expansion_ledger_schema(),
        pixiv_ledger=runner.build_pixiv_metadata_ledger_schema(),
        phase_split=runner.build_phase_split_plan(),
        safety_gates=runner.build_risk_and_stop_conditions(),
        decision=runner.build_decision_matrix(
            baseline,
            runner.public_pixiv_inventory(pixiv),
            runner.public_source_metadata_gap(gap),
            ai_policy,
            medium_policy,
        ),
        validation={
            "operational_inventory_command": "python scripts/run_phase45_scv2_p0_controlled_medium_expansion_policy.py --read-only",
            "operational_inventory_result": "passed",
            "transaction_read_only": "on",
            "forbidden_table_count_changes": [],
        },
    )


def test_runner_source_has_no_app_imports_or_provider_clients() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "from app." not in source
    assert "import app." not in source
    assert "from backend.app" not in source
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "import gallery_dl" not in source


def test_runner_refuses_without_read_only_flag(tmp_path: Path) -> None:
    args = argparse.Namespace(
        read_only=False,
        output_dir=str(tmp_path),
        write_public_report=False,
    )

    with pytest.raises(runner.PolicyBlockedError, match="requires --read-only"):
        runner.run_inventory(args)


def test_pixiv_id_pattern_extraction_from_filename_and_source() -> None:
    filename_matches = runner.extract_pixiv_ids("12345678_p0.png", source_kind="filename")
    source_matches = runner.extract_pixiv_ids("https://www.pixiv.net/en/artworks/987654321", source_kind="source")
    prefixed = runner.extract_pixiv_ids("pixiv_24681357_p12.webp", source_kind="filename")

    assert filename_matches[0]["pixiv_id"] == "12345678"
    assert filename_matches[0]["page_index"] == 0
    assert source_matches[0]["pixiv_id"] == "987654321"
    assert prefixed[0]["pixiv_id"] == "24681357"
    assert prefixed[0]["page_index"] == 12


def test_pixiv_like_media_classification_from_db_like_payload() -> None:
    row = {
        "filename": "12345678_p1.jpg",
        "source": "",
        "content_class": "anime",
    }

    classification = runner.classify_pixiv_like_media_row(
        row,
        db_signal_categories=["source_metadata_provider_pixiv"],
        db_pixiv_ids=["12345678"],
    )

    assert classification["is_pixiv_like"] is True
    assert "filename_pixiv_id_pattern" in classification["reasons"]
    assert "source_metadata_provider_pixiv" in classification["reasons"]
    assert classification["detected_pixiv_ids"] == ["12345678"]
    assert classification["ambiguous_pixiv_id"] is False


def test_pixiv_like_marker_without_id_is_invalid_or_ambiguous_signal() -> None:
    classification = runner.classify_pixiv_like_media_row(
        {"filename": "pixiv_export_unknown.png", "source": "", "content_class": "anime"}
    )

    assert classification["is_pixiv_like"] is True
    assert classification["detected_pixiv_ids"] == []
    assert classification["has_pixiv_marker_without_id"] is True


def test_public_redaction_of_source_path_and_filename_like_labels() -> None:
    unsafe = r"C:\Users\kyloris\Pictures\iCloud Photos\secret_12345678_p0.png"

    redacted = runner.redact_public_label(unsafe)

    assert "C:" not in redacted
    assert "secret_12345678_p0.png" not in redacted
    assert runner.scan_public_text(unsafe)
    assert not runner.scan_public_text(redacted)


def test_ledger_schema_required_fields() -> None:
    medium = runner.build_medium_expansion_ledger_schema()
    pixiv = runner.build_pixiv_metadata_ledger_schema()

    for field in runner.MEDIUM_IMPORT_LEDGER_REQUIRED_FIELDS:
        assert field in medium["required_fields"]
    for field in runner.PIXIV_METADATA_LEDGER_REQUIRED_FIELDS:
        assert field in pixiv["required_fields"]
    assert medium["db_schema_implemented_in_p0"] is False
    assert pixiv["db_schema_implemented_in_p0"] is False


def test_medium_expansion_policy_computes_target_buffer_and_failure_budget() -> None:
    policy = runner.build_medium_expansion_policy(1989)

    assert policy["target_successful_imported_media_min"] == 1511
    assert policy["target_successful_imported_media_count"] == 1761
    assert policy["target_successful_imported_media_max"] == 2011
    assert policy["candidate_over_selection_count"] == 2378
    assert policy["failure_budget"]["max_item_failures"] == 20
    assert policy["failure_budget"]["max_failure_rate"] == 0.05


def test_ai_tag_continuity_policy_requires_new_eligible_ai_tags() -> None:
    policy = runner.build_ai_tag_continuity_policy(_fake_baseline())

    assert policy["current_coverage_effectively_complete"] is True
    assert "Every newly imported eligible media item" in policy["future_invariant"]
    assert policy["e1_acceptance_criterion"]["target_pct"] == 100.0
    assert policy["localization_policy"].startswith("AI tag expansion must not auto-trigger")


def test_phase_split_forbids_provider_gallery_dl_in_e1() -> None:
    split = runner.build_phase_split_plan()
    forbidden = " ".join(split["SCV2-E1"]["must_not_do"]).casefold()

    assert "pixiv" in forbidden
    assert "gallery-dl" in forbidden
    assert "provider" in forbidden


def test_phase_split_forbids_import_and_ai_jobs_in_px1() -> None:
    split = runner.build_phase_split_plan()
    forbidden = " ".join(split["PX1"]["must_not_do"]).casefold()

    assert "import media" in forbidden
    assert "ai jobs" in forbidden
    assert "classification jobs" in forbidden


def test_summary_json_schema_required_fields() -> None:
    summary = _fake_summary()

    assert runner.SUMMARY_REQUIRED_FIELDS.issubset(summary)
    assert summary["phase"] == runner.PHASE
    assert summary["recommended_next_phase"] == "SCV2-E1"
    assert summary["safety"]["db_write"] is False
    assert summary["safety"]["provider_gallery_dl_pixiv_network_llm_calls"] is False


def test_public_report_does_not_include_private_path_patterns() -> None:
    summary = _fake_summary()
    report = runner.public_report_markdown(summary)

    assert "C:\\Users" not in report
    assert "file://" not in report
    assert "12345678_p0.png" not in report
    assert runner.scan_public_text(report) == []
