from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_phase45_scv2_e1_medium_import_ai_tag_completion as e1


def sample_public_summary() -> dict[str, object]:
    return {
        "phase": e1.PHASE,
        "title": e1.PHASE_TITLE,
        "branch": "codex/phase45-scv2-e1-medium-import-ai-tag-completion",
        "generated_at": "2026-06-10T00:00:00+00:00",
        "baseline_before": {
            "total_media": 1989,
            "eligible_media": 1936,
            "eligible_media_with_ai_tag_provenance": 1936,
            "eligible_ai_tag_provenance_pct": 100.0,
            "media_with_source_metadata": 60,
        },
        "db_identity_before": {"database": "blombooru"},
        "db_identity_after": {"database": "blombooru", "total_media_after": 3750},
        "storage_identity": {},
        "source_root_safety": {
            "safe_source_roots": 2,
            "source_storage_overlap_safe": True,
            "read_only_from_e1_perspective": True,
        },
        "candidate_discovery": {
            "total_candidates_considered": 10000,
            "eligible_pre_duplicate": 4081,
            "candidate_target": 2378,
            "deferred_reason_counts": {"cloud_recall_on_data_access": 5919},
        },
        "duplicate_detection": {
            "selected_for_duplicate_detection": 2378,
            "unique_import_candidates": 2020,
            "duplicate_count": 358,
            "reason_counts": {"unique_import_candidate": 2020},
        },
        "import_results": {
            "status": "completed_recommended_target_met",
            "successful_imports": 1761,
            "acceptable_range": [1511, 2011],
        },
        "classification_results": {"anime": 1732, "unknown": 19, "non_anime": 10, "failed": 0},
        "ai_tagging_results": {
            "eligible_new_media": 1751,
            "ai_tag_success_count": 1751,
            "ai_tag_failure_count": 0,
            "coverage_ratio": 1.0,
            "coverage_pct": 100.0,
        },
        "mutation_proof": {
            "passed": True,
            "expected_changed_tables": [],
            "forbidden_changed_tables": [],
            "unexpected_changed_tables": [],
            "expected_changed_table_names": [],
            "forbidden_changed_table_names": [],
            "unexpected_changed_table_names": [],
            "missing_forbidden_tables": [],
        },
        "failure_budget": {
            "max_item_failures": 20,
            "max_failure_rate": 0.05,
            "max_same_reason_failures": 20,
            "max_consecutive_failures": 10,
        },
        "public_redaction": {"passed": False, "findings": [], "checked_paths": []},
        "decision_matrix": {"e1_target_met": True, "px1_may_start_next": True, "broad_import_deferred": True},
        "recommended_next_phase": "PX1",
        "validation": {"commands": ["python.exe runner.py"], "browser_validation": "not_run_no_ui_runtime_target"},
        "safety": {"stop_conditions": {}},
        "artifact_lifecycle": {
            "runner": "phase-scoped operational runner",
            "private_artifacts": "one-off local artifact / ignored output",
            "public_report": "public report / handoff / roadmap update",
        },
        "private_artifacts": {"private_artifact_root_label": ".local_manifests/phase", "paths_public": False},
        "status": "completed",
        "mode": "execute",
    }


def pipeline_args(tmp_path: Path, *, execute: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        dry_run=not execute,
        execute=execute,
        output_dir=tmp_path / "artifacts",
        write_public_report=False,
        run_id="test-run",
        target_successful_imports=1,
        min_successful_imports=1,
        max_successful_imports=1,
        candidate_target=1,
        max_discovery_files=1,
        max_file_size_mb=200,
        hash_timeout_seconds=30,
        copy_timeout_seconds=30,
        classification_chunk_size=10,
        ai_chunk_size=10,
        max_item_failures=20,
        max_failure_rate=0.05,
        max_same_reason_failures=20,
        max_consecutive_failures=10,
    )


def install_pipeline_mocks(tmp_path: Path, monkeypatch, *, classification_status: str, ai_status: str = "completed"):
    source_root = tmp_path / "source"
    source_root.mkdir()
    storage_root = tmp_path / "storage"
    original_dir = storage_root / "media" / "original"
    thumbnail_dir = storage_root / "media" / "thumbnails"

    class FakeConnect:
        def __enter__(self):
            return object()

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConnect()

    table_counts = iter(
        [
            {"recorded_at": "before", "tables": {"blombooru_media": {"status": "present", "count": 10}}},
            {"recorded_at": "after", "tables": {"blombooru_media": {"status": "present", "count": 11}}},
        ]
    )

    monkeypatch.setattr(e1, "apply_phase_env_overrides", lambda: {})
    monkeypatch.setattr(e1, "create_engine_for_phase", lambda: (FakeEngine(), {}))
    monkeypatch.setattr(
        e1,
        "load_runtime_context",
        lambda _args, _identity: e1.RuntimeContext(
            run_id="test-run",
            mode="execute",
            output_dir=tmp_path / "artifacts",
            storage_root=storage_root,
            original_dir=original_dir,
            thumbnail_dir=thumbnail_dir,
            database_url_safe="db",
            db_identity_source={},
        ),
    )
    monkeypatch.setattr(e1, "env_local_library_paths", lambda: [source_root])
    monkeypatch.setattr(e1, "source_roots_from_local_manifests", lambda: [])
    monkeypatch.setattr(
        e1,
        "build_storage_identity",
        lambda _roots, _storage: {
            "storage_root_exists": True,
            "original_path_exists": True,
            "thumbnails_path_exists": True,
            "source_storage_overlap_safe": True,
        },
    )
    monkeypatch.setattr(
        e1,
        "build_source_root_inventory",
        lambda _roots, _storage: [
            {
                "source_root_private": str(source_root),
                "exists": True,
                "is_dir": True,
                "under_app_storage": False,
                "icloud_or_cloud_backed": False,
            }
        ],
    )
    monkeypatch.setattr(e1, "assert_storage_preflight", lambda _storage, _inventory: None)
    monkeypatch.setattr(e1, "git_value", lambda args: e1.PHASE_SLUG if args[:2] != ["branch", "--show-current"] else "codex/phase45-scv2-e1-medium-import-ai-tag-completion")
    monkeypatch.setattr(
        e1,
        "public_db_identity",
        lambda _identity, _conn: {
            "database": "blombooru",
            "connected_database": "blombooru",
            "host": "localhost",
            "violet_env": "development",
        },
    )
    monkeypatch.setattr(e1, "assert_db_preflight", lambda _identity, _public_identity: None)
    monkeypatch.setattr(e1, "active_job_counts", lambda _conn: {})
    monkeypatch.setattr(
        e1,
        "build_baseline",
        lambda _conn: {
            "total_media": 11,
            "eligible_media": 1,
            "eligible_media_with_ai_tag_provenance": 1,
            "eligible_ai_tag_provenance_pct": 100.0,
            "media_with_source_metadata": 0,
        },
    )
    monkeypatch.setattr(e1, "build_table_counts", lambda _conn: next(table_counts))
    monkeypatch.setattr(
        e1,
        "discover_candidates",
        lambda **_kwargs: (
            [],
            {
                "total_candidates_considered": 1,
                "eligible_pre_duplicate": 1,
                "deferred_pre_duplicate": 0,
                "deferred_reason_counts": {},
            },
        ),
    )
    monkeypatch.setattr(
        e1,
        "run_duplicate_detection",
        lambda _conn, _candidates, candidate_target, hash_timeout_seconds: (
            [],
            {
                "selected_for_duplicate_detection": 1,
                "unique_import_candidates": 1,
                "duplicate_count": 0,
                "reason_counts": {"unique_import_candidate": 1},
            },
        ),
    )
    monkeypatch.setattr(
        e1,
        "preflight_local_model_availability",
        lambda: {"ai_model": {"available": True}, "classification_model": {"loaded": True}},
    )
    monkeypatch.setattr(
        e1,
        "execute_imports",
        lambda *_args, **_kwargs: (
            [{"candidate_id": "candidate_1", "status": "imported", "media_id": 1}],
            {
                "status": "completed_recommended_target_met",
                "successful_imports": 1,
                "target_successful_imports": 1,
                "acceptable_range": [1, 1],
                "target_met": True,
                "recommended_target_met": True,
                "attempted_imports": 1,
                "failure_count": 0,
                "failure_reason_counts": {},
                "app_managed_storage_writes": 1,
                "source_root_mutation": False,
                "db_import": True,
            },
            [1],
        ),
    )
    if classification_status == "completed":
        monkeypatch.setattr(
            e1,
            "run_classification",
            lambda _ids, _chunk: (
                [{"media_id": 1, "content_class": "anime", "classification_success": True}],
                {
                    "status": "completed",
                    "processed": 1,
                    "failed": 0,
                    "distribution": {"anime": 1},
                    "anime": 1,
                    "unknown": 0,
                    "non_anime": 0,
                    "eligible": 1,
                    "ineligible": 0,
                },
            ),
        )
    else:
        monkeypatch.setattr(
            e1,
            "run_classification",
            lambda _ids, _chunk: (
                [{"media_id": 1, "content_class": None, "classification_success": False}],
                {
                    "status": "failed",
                    "processed": 1,
                    "failed": 1,
                    "distribution": {},
                    "anime": 0,
                    "unknown": 0,
                    "non_anime": 0,
                    "eligible": 0,
                    "ineligible": 0,
                },
            ),
        )
    if ai_status == "completed":
        monkeypatch.setattr(
            e1,
            "run_ai_tagging",
            lambda _ids, _chunk, failure_budget: (
                [{"media_id": 1, "ai_tag_success": True}],
                [],
                {
                    "status": "completed",
                    "eligible_new_media": 1,
                    "ai_tag_success_count": 1,
                    "ai_tag_failure_count": 0,
                    "coverage_ratio": 1.0,
                    "coverage_pct": 100.0,
                },
            ),
        )
    else:
        monkeypatch.setattr(
            e1,
            "run_ai_tagging",
            lambda _ids, _chunk, failure_budget: (
                [{"media_id": 1, "ai_tag_success": False}],
                [{"media_id": 1, "failure_reason": "model_error"}],
                {
                    "status": "failed",
                    "eligible_new_media": 1,
                    "ai_tag_success_count": 0,
                    "ai_tag_failure_count": 1,
                    "coverage_ratio": 0.0,
                    "coverage_pct": 0.0,
                },
            ),
        )


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


def test_invalid_import_bounds_fail_before_pipeline(monkeypatch):
    called = {"run_pipeline": False}

    def fail_if_called(_args):
        called["run_pipeline"] = True
        raise AssertionError("run_pipeline must not start for invalid bounds")

    monkeypatch.setattr(e1, "run_pipeline", fail_if_called)
    with pytest.raises(SystemExit) as exc_info:
        e1.main(
            [
                "--dry-run",
                "--min-successful-imports",
                "10",
                "--target-successful-imports",
                "9",
                "--max-successful-imports",
                "20",
                "--candidate-target",
                "20",
            ]
        )
    assert exc_info.value.code == 2
    assert not called["run_pipeline"]


def test_import_bounds_require_positive_ordered_values_and_candidate_capacity():
    result = e1.validate_import_bounds_values(
        min_successful_imports=1,
        target_successful_imports=5,
        max_successful_imports=4,
        candidate_target=3,
    )
    assert not result["passed"]
    assert "min_target_max_order_invalid" in result["errors"]
    assert "candidate_target_below_target_successful_imports" in result["errors"]

    result = e1.validate_import_bounds_values(
        min_successful_imports=0,
        target_successful_imports=1,
        max_successful_imports=2,
        candidate_target=2,
    )
    assert not result["passed"]
    assert "min_successful_imports_must_be_positive" in result["errors"]


def test_execute_imports_defensively_stops_at_max_cap(tmp_path, monkeypatch):
    from app.utils import media_helpers, media_processor, thumbnail_generator

    storage_root = tmp_path / "storage"
    original_dir = storage_root / "media" / "original"
    thumbnail_dir = storage_root / "media" / "thumbnails"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    rows = []
    for index in range(3):
        source = source_dir / f"candidate_{index}.jpg"
        source.write_text(f"hash-{index}", encoding="utf-8")
        rows.append(
            {
                "candidate_id": f"candidate_{index}",
                "public_safe_label": f"candidate_{index}.jpg",
                "source_locator_private_ref": str(source),
                "file_hash": f"hash-{index}",
                "eligible_for_import": True,
            }
        )

    monkeypatch.setattr(media_helpers, "get_unique_filename", lambda _directory, filename: filename)

    def fake_process_media_file(path):
        return {
            "hash": Path(path).read_text(encoding="utf-8"),
            "file_type": "image",
            "mime_type": "image/jpeg",
            "file_size": Path(path).stat().st_size,
            "width": 1,
            "height": 1,
            "duration": None,
        }

    def fake_generate_thumbnail(_source, target, _file_type):
        Path(target).write_text("thumb", encoding="utf-8")
        return True

    monkeypatch.setattr(media_processor, "process_media_file", fake_process_media_file)
    monkeypatch.setattr(thumbnail_generator, "generate_thumbnail", fake_generate_thumbnail)

    inserted: list[dict[str, object]] = []

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar(self):
            return self.value

        def scalar_one(self):
            return self.value

    class FakeConnection:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT id FROM blombooru_media" in sql:
                return FakeResult(None)
            if "INSERT INTO blombooru_media" in sql:
                inserted.append(dict(params or {}))
                return FakeResult(len(inserted))
            raise AssertionError(sql)

    class FakeBegin:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    context = e1.RuntimeContext(
        run_id="run",
        mode="execute",
        output_dir=tmp_path / "artifacts",
        storage_root=storage_root,
        original_dir=original_dir,
        thumbnail_dir=thumbnail_dir,
        database_url_safe="db",
        db_identity_source={},
    )
    ledger, results, imported_ids = e1.execute_imports(
        FakeEngine(),
        context,
        rows,
        execute=True,
        target_successful_imports=3,
        min_successful_imports=1,
        max_successful_imports=2,
        copy_timeout_seconds=30,
        failure_budget={
            "max_item_failures": 20,
            "max_failure_rate": 0.05,
            "max_same_reason_failures": 20,
            "max_consecutive_failures": 10,
        },
    )
    assert len(imported_ids) == 2
    assert len(inserted) == 2
    assert len(ledger) == 2
    assert results["successful_imports"] == 2


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


def test_scan_job_table_changes_are_unexpected_for_e1():
    before = {"tables": {"blombooru_scan_jobs": {"status": "present", "count": 1}}}
    after = {"tables": {"blombooru_scan_jobs": {"status": "present", "count": 2}}}
    proof = e1.classify_table_mutations(before, after)
    assert not proof["passed"]
    assert proof["expected_changed_tables"] == []
    assert [row["table"] for row in proof["unexpected_changed_tables"]] == ["blombooru_scan_jobs"]


def test_forbidden_fingerprint_change_fails_with_same_row_count():
    before = {
        "tables": {
            "blombooru_entities": {
                "status": "present",
                "count": 1,
                "fingerprint": {"max_updated_at": "2026-01-01T00:00:00"},
            }
        }
    }
    after = {
        "tables": {
            "blombooru_entities": {
                "status": "present",
                "count": 1,
                "fingerprint": {"max_updated_at": "2026-01-02T00:00:00"},
            }
        }
    }
    proof = e1.classify_table_mutations(before, after)
    assert not proof["passed"]
    assert proof["forbidden_changed_tables"][0]["change_reason"] == "forbidden_fingerprint_changed"


def test_forbidden_max_id_change_fails_with_same_row_count():
    before = {
        "tables": {
            "blombooru_source_concepts": {
                "status": "present",
                "count": 1,
                "fingerprint": {"max_id": "10"},
            }
        }
    }
    after = {
        "tables": {
            "blombooru_source_concepts": {
                "status": "present",
                "count": 1,
                "fingerprint": {"max_id": "11"},
            }
        }
    }
    proof = e1.classify_table_mutations(before, after)
    assert not proof["passed"]
    assert [row["table"] for row in proof["forbidden_changed_tables"]] == ["blombooru_source_concepts"]


def test_allowed_table_count_changes_still_pass_and_missing_forbidden_tables_are_recorded():
    before = {
        "tables": {
            "blombooru_media": {"status": "present", "count": 1},
            "blombooru_entities": {"status": "missing_table", "count": None},
        }
    }
    after = {
        "tables": {
            "blombooru_media": {"status": "present", "count": 2},
            "blombooru_entities": {"status": "missing_table", "count": None},
        }
    }
    proof = e1.classify_table_mutations(before, after)
    assert proof["passed"]
    assert [row["table"] for row in proof["expected_changed_tables"]] == ["blombooru_media"]
    assert "blombooru_entities" in proof["missing_forbidden_tables"]


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


def test_redaction_failure_writes_safe_public_json_stub(tmp_path):
    summary = sample_public_summary()
    leaked = r"C:\Users\name\Pictures\iCloud Photos\Photos\12345678_p0.jpg"
    summary["validation"] = {"commands": [f"python.exe runner.py --source {leaked}"]}
    report_md = tmp_path / "report.md"
    report_json = tmp_path / "summary.json"
    redaction, public_result = e1.write_public_outputs(
        summary,
        report_md=report_md,
        report_json=report_json,
        temp_dir=tmp_path / "tmp",
    )
    assert not redaction["passed"]
    assert public_result["status"] == "public_redaction_failed"
    public_json_text = report_json.read_text(encoding="utf-8")
    public_md_text = report_md.read_text(encoding="utf-8")
    assert leaked not in public_json_text
    assert "12345678_p0.jpg" not in public_json_text
    assert leaked not in public_md_text
    loaded = json.loads(public_json_text)
    assert loaded["public_redaction"]["passed"] is False
    assert set(loaded) == {"phase", "title", "status", "public_redaction", "private_artifacts"}


def test_classification_failure_after_import_still_writes_mutation_proof(tmp_path, monkeypatch):
    install_pipeline_mocks(tmp_path, monkeypatch, classification_status="failed")
    summary = e1.run_pipeline(pipeline_args(tmp_path, execute=True))
    artifact_root = tmp_path / "artifacts"
    assert summary["status"] == "classification_failed"
    assert summary["import_results"]["successful_imports"] == 1
    assert summary["mutation_proof"]["passed"] is True
    assert (artifact_root / "db-identity-after.json").exists()
    assert (artifact_root / "mutation-proof-after.json").exists()
    assert (artifact_root / "mutation-proof-delta.json").exists()
    assert (artifact_root / "safety-stop-conditions.json").exists()


def test_ai_failure_after_classification_still_writes_mutation_proof(tmp_path, monkeypatch):
    install_pipeline_mocks(tmp_path, monkeypatch, classification_status="completed", ai_status="failed")
    summary = e1.run_pipeline(pipeline_args(tmp_path, execute=True))
    artifact_root = tmp_path / "artifacts"
    assert summary["status"] == "ai_tagging_failed"
    assert summary["import_results"]["successful_imports"] == 1
    assert summary["classification_results"]["status"] == "completed"
    assert summary["ai_tagging_results"]["status"] == "failed"
    assert summary["mutation_proof"]["passed"] is True
    assert (artifact_root / "db-identity-after.json").exists()
    assert (artifact_root / "mutation-proof-after.json").exists()
    assert (artifact_root / "mutation-proof-delta.json").exists()
    assert (artifact_root / "safety-stop-conditions.json").exists()


def test_hash_worker_pipe_failure_marks_pending_without_unbounded_hash(monkeypatch):
    class FakeParent:
        def poll(self, timeout=None):
            raise BrokenPipeError("pipe failed")

        def close(self):
            pass

    class FakeChild:
        def close(self):
            pass

    class FakeProcess:
        exitcode = 1

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(e1.multiprocessing, "Pipe", lambda duplex=False: (FakeParent(), FakeChild()))
    monkeypatch.setattr(e1.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(e1, "_hash_one", lambda _path: (_ for _ in ()).throw(AssertionError("_hash_one called")))
    rows = [
        {"candidate_id": "candidate_1", "source_locator_private_ref": r"C:\private\a.jpg"},
        {"candidate_id": "candidate_2", "source_locator_private_ref": r"C:\private\b.jpg"},
    ]
    results = e1.hash_candidates_with_timeout(rows, timeout_seconds=30)
    assert set(results) == {"candidate_1", "candidate_2"}
    assert {row["error_reason"] for row in results.values()} == {"hash_worker_failed"}


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
