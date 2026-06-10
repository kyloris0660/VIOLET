from __future__ import annotations

from pathlib import Path

from scripts import run_phase45_scv2_e1_medium_import_ai_tag_completion as e1


def test_candidate_ledger_required_fields():
    row = {
        "run_id": "run",
        "candidate_id": "candidate_1",
        "source_root_label": "source_root_1",
        "source_locator_private_ref": r"C:\private\source\12345678_p0.jpg",
        "original_filename_sha256": "abc",
        "extension": ".jpg",
        "size": 123,
        "detected_pixiv_ids": [{"work_id": "12345678", "page_index": 0}],
        "candidate_source_reason": "configured",
        "cloud_state": {"exists": True},
        "readable_status": "not_read_yet",
        "unsupported_reason": None,
        "duplicate_check_status": "not_checked",
        "existing_media_match": None,
        "eligible_for_import": True,
        "deferred_reason": None,
        "public_safe_label": "candidate_000001.jpg",
    }
    assert e1.CANDIDATE_LEDGER_REQUIRED_FIELDS <= set(row)


def test_import_item_ledger_required_fields():
    row = {
        "run_id": "run",
        "candidate_id": "candidate_1",
        "public_safe_label": "candidate_000001.jpg",
        "status": "imported",
        "media_id": 10,
        "duplicate_of_media_id": None,
        "failure_reason": None,
        "bytes_copied": 123,
        "eligible_for_db_import": True,
    }
    assert e1.IMPORT_LEDGER_REQUIRED_FIELDS <= set(row)


def test_ai_tagging_ledger_required_fields():
    row = {
        "run_id": "run",
        "media_id": 10,
        "ai_tag_attempted": True,
        "ai_tag_success": True,
        "failure_reason": None,
        "job_id": 20,
        "output_tag_count": 42,
        "has_ai_tag_provenance": True,
    }
    assert e1.AI_LEDGER_REQUIRED_FIELDS <= set(row)


def test_pixiv_id_extraction_from_filenames_for_px1_marking_only():
    assert e1.extract_pixiv_ids("123456789_p0.jpg") == [{"work_id": "123456789", "page_index": 0}]
    assert e1.extract_pixiv_ids("artist_98765432_p12.png") == [{"work_id": "98765432", "page_index": 12}]
    assert e1.extract_pixiv_ids("IMG_0043.JPG") == []


def test_candidate_selection_priority_and_over_selection_calculation():
    assert e1.candidate_target_count(1761, 1.35) == 2378
    pixiv = {
        "source_gate_allowed": True,
        "detected_pixiv_ids": [{"work_id": "12345678", "page_index": 0}],
        "extension": ".jpg",
        "size": 100,
        "source_locator_private_ref": "b",
    }
    ordinary = {
        "source_gate_allowed": True,
        "detected_pixiv_ids": [],
        "extension": ".jpg",
        "size": 100,
        "source_locator_private_ref": "a",
    }
    blocked = {
        "source_gate_allowed": False,
        "detected_pixiv_ids": [{"work_id": "99999999", "page_index": 0}],
        "extension": ".jpg",
        "size": 100,
        "source_locator_private_ref": "c",
    }
    ordered = sorted([ordinary, blocked, pixiv], key=e1.candidate_priority_key)
    assert ordered == [pixiv, ordinary, blocked]


def test_duplicate_classification_from_mock_candidate_rows():
    existing = {
        "hash_to_media": {"hash1": 1},
        "pixiv_key_to_media": {("12345678", 0): 2},
        "filename_size_to_media": {"same.jpg:10": 3},
    }
    assert e1.classify_duplicate({"file_hash": "hash1"}, existing, set())["reason"] == "duplicate_by_hash"
    assert (
        e1.classify_duplicate({"pixiv_key": ("12345678", 0), "file_hash": "new"}, existing, set())["reason"]
        == "duplicate_by_pixiv_id_page"
    )
    assert (
        e1.classify_duplicate({"filename_size_key": "same.jpg:10", "file_hash": "new"}, existing, set())["reason"]
        == "duplicate_by_filename_size"
    )
    assert e1.classify_duplicate({"file_hash": "new"}, existing, {"new"})["reason"] == "duplicate_by_manifest_hash"
    assert e1.classify_duplicate({"file_hash": "unique"}, existing, set())["status"] == "unique"


def test_failure_budget_stop_conditions():
    ok = e1.evaluate_failure_budget(
        attempted=100,
        failure_reasons={"read_timeout": 4},
        consecutive_failures=2,
        max_item_failures=20,
        max_failure_rate=0.05,
        max_same_reason_failures=20,
        max_consecutive_failures=10,
    )
    assert ok["passed"]
    exceeded = e1.evaluate_failure_budget(
        attempted=100,
        failure_reasons={"read_timeout": 21},
        consecutive_failures=11,
        max_item_failures=20,
        max_failure_rate=0.05,
        max_same_reason_failures=20,
        max_consecutive_failures=10,
    )
    assert not exceeded["passed"]
    assert set(exceeded["exceeded"]) == {
        "max_item_failures",
        "max_failure_rate",
        "max_same_reason_failures",
        "max_consecutive_failures",
    }


def test_allowed_vs_forbidden_table_mutation_proof_classification():
    before = {
        "tables": {
            "blombooru_media": {"status": "present", "count": 10},
            "blombooru_media_tags": {"status": "present", "count": 100},
            "blombooru_entities": {"status": "present", "count": 1},
            "blombooru_tag_translations": {"status": "present", "count": 3},
            "blombooru_albums": {"status": "present", "count": 0},
        }
    }
    after = {
        "tables": {
            "blombooru_media": {"status": "present", "count": 12},
            "blombooru_media_tags": {"status": "present", "count": 130},
            "blombooru_entities": {"status": "present", "count": 1},
            "blombooru_tag_translations": {"status": "present", "count": 4},
            "blombooru_albums": {"status": "present", "count": 1},
        }
    }
    proof = e1.classify_table_mutations(before, after)
    assert [row["table"] for row in proof["expected_changed_tables"]] == [
        "blombooru_media",
        "blombooru_media_tags",
    ]
    assert [row["table"] for row in proof["forbidden_changed_tables"]] == ["blombooru_tag_translations"]
    assert [row["table"] for row in proof["unexpected_changed_tables"]] == ["blombooru_albums"]
    assert not proof["passed"]


def test_ai_tag_continuity_policy_requires_eligible_new_media_to_be_tagged():
    passed = e1.ai_tag_continuity_result(eligible_new_media=3, tagged_eligible_new_media=3)
    assert passed["passed"]
    failed = e1.ai_tag_continuity_result(eligible_new_media=3, tagged_eligible_new_media=2)
    assert failed["coverage_pct"] == 66.67
    assert not failed["passed"]


def test_non_anime_media_are_excluded_from_required_ai_tag_continuity():
    ledger = [
        {"media_id": 1, "content_class": "anime", "classification_success": True},
        {"media_id": 2, "content_class": "unknown", "classification_success": True},
        {"media_id": 3, "content_class": "non_anime", "classification_success": True},
        {"media_id": 4, "content_class": None, "classification_success": False},
    ]
    assert e1.eligible_media_ids_from_classification(ledger) == [1, 2]


def test_public_redaction_catches_source_paths_and_filenames():
    findings = e1.scan_public_text(r"C:\Users\name\Pictures\iCloud Photos\Photos\12345678_p0.jpg")
    reasons = {finding["reason"] for finding in findings}
    assert "windows_absolute_path" in reasons
    assert "image_filename" in reasons


def test_summary_json_schema_required_fields():
    summary = {key: None for key in e1.SUMMARY_REQUIRED_FIELDS}
    assert e1.validate_summary_schema(summary)["passed"]
    summary.pop("phase")
    result = e1.validate_summary_schema(summary)
    assert not result["passed"]
    assert result["missing_fields"] == ["phase"]


def test_phase_boundaries_forbid_provider_gallery_dl_pixiv_in_e1():
    boundary = e1.phase_boundary_status()
    assert boundary["provider_calls"] is False
    assert boundary["pixiv"] is False
    assert boundary["gallery_dl"] is False
    assert boundary["source_metadata_extraction"] is False


def test_phase_boundaries_forbid_sourceconcept_entity_and_localization_in_e1():
    boundary = e1.phase_boundary_status()
    assert boundary["source_concept_resolver"] is False
    assert boundary["entity_bridge"] is False
    assert boundary["localization"] is False
    assert boundary["llm"] is False
    assert boundary["browser_validation"] is False
    assert boundary["server_start"] is False
