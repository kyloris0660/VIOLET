"""Focused tests for Phase 4.5-SCV1 read-only coverage audit runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_phase45_scv1_source_concept_coverage_audit as runner  # noqa: E402


def test_runner_source_has_no_app_imports() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "from app." not in source
    assert "import app." not in source
    assert "BACKEND_ROOT" not in source
    assert "source_concept_search_service" not in source
    assert "source_metadata_registry_service" not in source


def test_importing_runner_does_not_create_app_settings_or_storage(tmp_path: Path) -> None:
    storage_root = tmp_path / "fresh-storage-root"
    code = f"""
import json
import os
import sys
from pathlib import Path
os.environ["VIOLET_STORAGE_ROOT"] = {str(storage_root)!r}
sys.path.insert(0, {str(ROOT)!r})
import scripts.run_phase45_scv1_source_concept_coverage_audit as runner
root = Path(os.environ["VIOLET_STORAGE_ROOT"])
print(json.dumps({{
    "settings_json_exists": (root / "data" / "settings.json").exists(),
    "data_dir_exists": (root / "data").exists(),
    "media_dir_exists": (root / "media").exists(),
    "app_config_imported": "app.config" in sys.modules,
    "helper": runner.canonical_source_key("Kamisato Ayaka"),
}}))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, "VIOLET_STORAGE_ROOT": str(storage_root)},
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["helper"] == "kamisato_ayaka"
    assert payload["settings_json_exists"] is False
    assert payload["data_dir_exists"] is False
    assert payload["media_dir_exists"] is False
    assert payload["app_config_imported"] is False


def test_script_local_search_key_helpers_cover_sc2_style_aliases() -> None:
    assert runner.normalize_source_text("  Kamisato   Ayaka  ") == "Kamisato Ayaka"
    assert runner.canonical_source_key("Kamisato Ayaka") == "kamisato_ayaka"
    assert runner.canonical_source_key("神里綾華") == "神里綾華"
    assert "nahida_(genshin_impact)" in runner._search_keys_for_term("nahida_(genshin_impact)")
    assert "nahida" in runner._search_keys_for_term("nahida_(genshin_impact)")
    assert "kamisato_ayaka" in runner._search_keys_for_term("Kamisato Ayaka")
    assert "神里綾華" in runner._search_keys_for_term("神里綾華")


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


def test_db_resolution_settings_json_beats_stale_dotenv() -> None:
    resolved = runner.resolve_app_database_config(
        file_db={"host": "settings-host", "port": 15432, "name": "blombooru", "user": "settings_user", "password": "settings_pw"},
        settings_json_exists=True,
        dotenv={
            "POSTGRES_HOST": "stale-dotenv-host",
            "POSTGRES_PORT": "25432",
            "POSTGRES_DB": "stale_db",
            "POSTGRES_USER": "stale_user",
            "POSTGRES_PASSWORD": "stale_pw",
            "VIOLET_ENV": "development",
        },
        process_env={},
    )

    assert resolved["host"] == "settings-host"
    assert resolved["port"] == 15432
    assert resolved["name"] == "blombooru"
    assert resolved["user"] == "settings_user"
    assert resolved["field_sources"]["host"] == "settings_json"
    assert resolved["field_sources"]["password"] == "settings_json_present"
    assert resolved["runner_url_without_password"] == resolved["app_equivalent_url_without_password"]
    assert resolved["urls_match"] is True


def test_db_resolution_uses_env_and_dotenv_only_for_missing_settings_fields() -> None:
    resolved = runner.resolve_app_database_config(
        file_db={"name": "blombooru"},
        settings_json_exists=True,
        dotenv={"POSTGRES_USER": "dotenv_user", "VIOLET_ENV": "development"},
        process_env={"POSTGRES_HOST": "env-host", "POSTGRES_PORT": "16432"},
    )

    assert resolved["host"] == "env-host"
    assert resolved["port"] == 16432
    assert resolved["name"] == "blombooru"
    assert resolved["user"] == "dotenv_user"
    assert resolved["password"] == ""
    assert resolved["field_sources"]["host"] == "process_env"
    assert resolved["field_sources"]["user"] == ".env"
    assert resolved["field_sources"]["password"] == "app_default_empty"


def test_build_database_url_requires_development_blombooru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "resolve_app_database_config",
        lambda: {
            "violet_env": "development",
            "name": "other_db",
            "url": "unused",
            "settings_json_exists": False,
            "database_file_settings_used": False,
            "field_sources": {},
            "runner_url_without_password": "postgresql://postgres@localhost:5432/other_db",
            "app_equivalent_url_without_password": "postgresql://postgres@localhost:5432/other_db",
            "urls_match": True,
            "runner_matches_app_equivalent": True,
            "app_compatible": True,
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password_present": False,
        },
    )

    with pytest.raises(runner.AuditBlockedError, match="development DB 'blombooru'"):
        runner.build_database_url()


def test_db_resolution_mismatch_fails_closed() -> None:
    with pytest.raises(runner.AuditBlockedError, match="does not match app-equivalent"):
        runner.assert_db_resolution_parity({"app_compatible": True, "urls_match": False, "runner_matches_app_equivalent": False})


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


def test_public_redaction_scan_catches_broadened_private_path_shapes() -> None:
    unsafe_values = [
        "/mnt/storage/foo",
        "/Volumes/Anime/foo",
        "/storage/private/foo",
        "/media/original/foo",
        "/original/foo",
        "/thumbnails/foo",
        "mnt_storage_private",
        "media_original_foo",
        r"\\server\share\folder",
        "file:///Users/name/Pictures/private",
        "OpenAI key sk-abcdefghijklmnop",
    ]

    for value in unsafe_values:
        assert runner.scan_public_text(value), value
        assert runner.safe_public_value(value) == "[redacted]"


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

    def fake_scan(paths, *, checked_at=None, public_path_labels=None):
        scanned["called"] = True
        assert report_md not in paths
        assert report_json not in paths
        assert public_path_labels == [runner.root_relative_or_name(report_md), runner.root_relative_or_name(report_json)]
        temp_md, temp_json = paths
        assert temp_md.parent == output_dir / "_public_report_staging"
        assert temp_json.parent == output_dir / "_public_report_staging"
        assert not report_md.exists()
        assert not report_json.exists()
        payload = json.loads(temp_json.read_text(encoding="utf-8"))
        combined_text = temp_md.read_text(encoding="utf-8") + "\n" + temp_json.read_text(encoding="utf-8")
        assert payload["private_artifacts"]["private_artifact_bundle_created"] is True
        assert payload["private_artifacts"]["exact_private_paths_public"] is False
        assert payload["redaction_privacy_audit"]["checked_at"] == checked_at
        assert payload["redaction_privacy_audit"]["final_public_scan_after_public_fields_finalized"] is True
        assert "private_artifact_zip" not in combined_text
        assert expected_zip_path not in combined_text
        return {
            "checked_at": checked_at,
            "passed": True,
            "public_paths": list(public_path_labels or []),
            "findings": [],
        }

    monkeypatch.setattr(runner, "scan_public_artifacts", fake_scan)

    redaction = runner.write_reports_and_redaction(summary, output_dir)

    assert scanned["called"] is True
    assert redaction["passed"] is True
    final_payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert final_payload["redaction_privacy_audit"]["checked_at"] == redaction["checked_at"]
    assert final_payload["redaction_privacy_audit"]["final_public_scan_after_public_fields_finalized"] is True


def test_public_report_redaction_failure_leaves_tracked_files_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_md = tmp_path / "phase-report.md"
    report_json = tmp_path / "phase-summary.json"
    report_md.write_text("old safe markdown\n", encoding="utf-8")
    report_json.write_text('{"old": "safe"}\n', encoding="utf-8")
    output_dir = tmp_path / "private"
    output_dir.mkdir()
    summary = minimal_public_summary(output_dir)
    summary["source_concept_inventory"]["by_status"] = {"leak": "/mnt/storage/foo"}

    monkeypatch.setattr(runner, "PUBLIC_REPORT_MD", report_md)
    monkeypatch.setattr(runner, "PUBLIC_REPORT_JSON", report_json)

    with pytest.raises(runner.AuditBlockedError, match="Public redaction scan failed"):
        runner.write_reports_and_redaction(summary, output_dir)

    assert report_md.read_text(encoding="utf-8") == "old safe markdown\n"
    assert report_json.read_text(encoding="utf-8") == '{"old": "safe"}\n'
    assert not (output_dir / "_public_report_staging" / report_md.name).exists()
    assert not (output_dir / "_public_report_staging" / report_json.name).exists()


def test_public_report_writer_replaces_only_after_temp_scan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_md = tmp_path / "phase-report.md"
    report_json = tmp_path / "phase-summary.json"
    report_md.write_text("old safe markdown\n", encoding="utf-8")
    report_json.write_text('{"old": "safe"}\n', encoding="utf-8")
    output_dir = tmp_path / "private"
    output_dir.mkdir()
    summary = minimal_public_summary(output_dir)

    monkeypatch.setattr(runner, "PUBLIC_REPORT_MD", report_md)
    monkeypatch.setattr(runner, "PUBLIC_REPORT_JSON", report_json)

    def fake_scan(paths, *, checked_at=None, public_path_labels=None):
        assert report_md.read_text(encoding="utf-8") == "old safe markdown\n"
        assert report_json.read_text(encoding="utf-8") == '{"old": "safe"}\n'
        assert all(path.parent == output_dir / "_public_report_staging" for path in paths)
        return {"checked_at": checked_at, "passed": True, "public_paths": list(public_path_labels or []), "findings": []}

    monkeypatch.setattr(runner, "scan_public_artifacts", fake_scan)

    runner.write_reports_and_redaction(summary, output_dir)

    assert "old safe markdown" not in report_md.read_text(encoding="utf-8")
    assert json.loads(report_json.read_text(encoding="utf-8"))["phase"] == runner.PHASE


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


def test_direct_media_with_empty_alias_lookup_is_reachability_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    concepts = [{"id": 1, "status": "active", "primary_display_name": "Reachability Gap", "concept_type_hint": "character"}]
    aliases = [{"id": 1, "concept_id": 1, "status": "active", "display_name": "reachability_gap", "alias_key": "reachability_gap"}]

    monkeypatch.setattr(runner, "concept_ids_for_term", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(runner, "concept_media_set_for_ids", lambda _conn, ids, statuses=runner.VISIBLE_STATUSES: {100} if list(ids) == [1] else set())

    metrics, samples = runner.audit_search_symmetry(None, concepts, aliases)

    assert metrics["direct_media_unreachable_by_alias_count"] == 1
    assert metrics["direct_media_unreachable_active_count"] == 1
    assert metrics["exact_symmetric_concepts"] == 0
    assert metrics["explainable_no_media_concepts"] == 0
    assert samples[0]["mismatch_type"] == "direct_media_unreachable_by_alias"
    assert samples[0]["concept_media_count"] == 1
    assert samples[0]["max_alias_media_count"] == 0


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
        media={
            "total_media": 100,
            "eligible_media_count": 80,
            "media_without_ai_tags": 70,
            "eligible_media_without_ai_tag_provenance": 60,
            "eligible_ai_tag_provenance_pct": 25,
            "tags": {"translation": {"total": 10}, "total_tags": 100},
        },
        source_layer={"source_records": {"linked_to_media": 5}, "source_metadata_distinct_eligible_media_count": 5, "source_metadata_distinct_media_count": 5},
        concepts={"total_source_concepts": 20},
        symmetry={"severe_asymmetry_count": 2, "asymmetric_concepts": 3, "direct_media_unreachable_by_alias_count": 1},
        alias_gaps={"total_gap_signals": 12},
        needs_review={"total_needs_review_concepts": 6},
        redaction_passed=True,
    )

    assert decision["recommended_next_phase"] == "source_concept_alias_resolver_improvement"
    assert decision["answers"]["is_5k_10k_expansion_justified_now"] is False
    assert decision["answers"]["is_entity_bridge_justified_now"] is False
    assert any(item["key"] == "run_ledger_or_phase39_prerequisite" and item["recommended"] for item in decision["options"])


def test_ai_decision_uses_eligible_media_denominator_not_total_media() -> None:
    decision = runner.decide_next_phase(
        media={
            "total_media": 1000,
            "eligible_media_count": 10,
            "media_without_ai_tags": 901,
            "eligible_media_without_ai_tag_provenance": 1,
            "eligible_ai_tag_provenance_pct": 90,
            "tags": {"translation": {"total": 0}, "total_tags": 0},
        },
        source_layer={
            "source_records": {"linked_to_media": 1000},
            "source_metadata_distinct_media_count": 1000,
            "source_metadata_distinct_eligible_media_count": 10,
        },
        concepts={"total_source_concepts": 20},
        symmetry={"severe_asymmetry_count": 0, "asymmetric_concepts": 0, "direct_media_unreachable_by_alias_count": 0},
        alias_gaps={"total_gap_signals": 0},
        needs_review={"total_needs_review_concepts": 0},
        redaction_passed=True,
    )
    ai_option = next(item for item in decision["options"] if item["key"] == "bounded_ai_tag_expansion")

    assert ai_option["priority"] == "P3"
    assert ai_option["recommended"] is False
    assert "eligible media without AI tag provenance=1/10 (10.0%)" in ai_option["reasons"]


def test_source_metadata_coverage_counts_distinct_media_not_rows() -> None:
    coverage = runner.build_source_metadata_coverage_summary(
        total_media=10,
        eligible_media=8,
        total_rows=3,
        linked_rows=3,
        distinct_media=1,
        distinct_eligible_media=1,
    )

    assert coverage["source_metadata_records_linked_to_media"] == 3
    assert coverage["source_metadata_distinct_media_count"] == 1
    assert coverage["source_metadata_distinct_media_pct"] == 10.0
    assert coverage["source_metadata_distinct_eligible_media_pct"] == 12.5


def test_metadata_decision_uses_distinct_media_not_linked_row_count() -> None:
    decision = runner.decide_next_phase(
        media={
            "total_media": 100,
            "eligible_media_count": 100,
            "media_without_ai_tags": 0,
            "eligible_media_without_ai_tag_provenance": 0,
            "eligible_ai_tag_provenance_pct": 100,
            "tags": {"translation": {"total": 0}, "total_tags": 0},
        },
        source_layer={
            "source_records": {"linked_to_media": 80},
            "source_metadata_distinct_media_count": 1,
            "source_metadata_distinct_eligible_media_count": 1,
        },
        concepts={"total_source_concepts": 20},
        symmetry={"severe_asymmetry_count": 0, "asymmetric_concepts": 0, "direct_media_unreachable_by_alias_count": 0},
        alias_gaps={"total_gap_signals": 0},
        needs_review={"total_needs_review_concepts": 0},
        redaction_passed=True,
    )
    metadata_option = next(item for item in decision["options"] if item["key"] == "bounded_pixiv_metadata_expansion")

    assert metadata_option["priority"] == "P2"
    assert metadata_option["recommended"] is True
    assert "source metadata distinct-media coverage=1/100 (1.0%)" in metadata_option["reasons"]
    assert "row counts kept for context; decision uses distinct covered media" in metadata_option["reasons"]


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


def test_identity_tag_gap_excludes_visual_general_meta_tags() -> None:
    rows = [
        {"key_value": "1girl", "label": "1girl", "category": "general", "count": 20},
        {"key_value": "solo", "label": "solo", "category": "general", "count": 18},
        {"key_value": "highres", "label": "highres", "category": "meta", "count": 10},
        {"key_value": "nahida_(genshin_impact)", "label": "nahida_(genshin_impact)", "category": "character", "count": 3},
        {"key_value": "genshin_impact", "label": "genshin_impact", "category": "copyright", "count": 3},
    ]

    sampled, detail = runner.summarize_identity_tag_gap_rows(rows, {"genshin_impact"}, sample_limit=10)

    assert detail["total_distinct_keys"] == 5
    assert detail["identity_eligible_distinct_keys"] == 2
    assert detail["excluded_visual_or_meta_distinct_keys"] == 3
    assert detail["missing_distinct_keys"] == 1
    assert sampled == [{"key_value": "nahida_(genshin_impact)", "label": "nahida_(genshin_impact)", "category": "character", "count": 3}]


def test_identity_tag_gap_full_counts_are_independent_of_sample_limit() -> None:
    rows = [
        {"key_value": f"character_{index}_(work)", "label": f"character_{index}_(work)", "category": "character", "count": 1}
        for index in range(8)
    ] + [{"key_value": "1girl", "label": "1girl", "category": "general", "count": 8}]

    _sampled_one, detail_one = runner.summarize_identity_tag_gap_rows(rows, set(), sample_limit=1)
    _sampled_three, detail_three = runner.summarize_identity_tag_gap_rows(rows, set(), sample_limit=3)

    assert detail_one["missing_distinct_keys"] == 8
    assert detail_three["missing_distinct_keys"] == 8
    assert detail_one["sampled_missing_keys"] == 1
    assert detail_three["sampled_missing_keys"] == 3
    assert detail_one["excluded_visual_or_meta_distinct_keys"] == 1


def test_decision_matrix_uses_identity_source_relevant_gap_count_not_visual_tags() -> None:
    visual_rows = [{"key_value": f"visual_{index}", "label": f"visual_{index}", "category": "general", "count": 1} for index in range(20)]
    identity_rows = [{"key_value": "nahida_(genshin_impact)", "label": "nahida_(genshin_impact)", "category": "character", "count": 1}]
    _sampled, detail = runner.summarize_identity_tag_gap_rows(visual_rows + identity_rows, set(), sample_limit=5)
    alias_gaps = {"total_gap_signals": detail["missing_distinct_keys"]}

    decision = runner.decide_next_phase(
        media={"total_media": 100, "eligible_media_count": 80, "media_without_ai_tags": 0, "tags": {"translation": {"total": 0}, "total_tags": 21}},
        source_layer={"source_records": {"linked_to_media": 100}},
        concepts={"total_source_concepts": 20},
        symmetry={"severe_asymmetry_count": 0, "asymmetric_concepts": 0},
        alias_gaps=alias_gaps,
        needs_review={"total_needs_review_concepts": 0},
        redaction_passed=True,
    )

    assert detail["missing_distinct_keys"] == 1
    assert detail["excluded_visual_or_meta_distinct_keys"] == 20
    assert "alias/cross-language/source linkage gap signals=1" in decision["options"][0]["reasons"]


def minimal_public_summary(tmp_path: Path) -> dict:
    decision = runner.decide_next_phase(
        media={
            "total_media": 0,
            "eligible_media_count": 0,
            "media_without_ai_tags": 0,
            "eligible_media_without_ai_tag_provenance": 0,
            "eligible_ai_tag_provenance_pct": 0,
            "tags": {"translation": {"total": 0}, "total_tags": 0},
        },
        source_layer={
            "source_records": {"linked_to_media": 0},
            "source_metadata_distinct_media_count": 0,
            "source_metadata_distinct_eligible_media_count": 0,
        },
        concepts={"total_source_concepts": 0},
        symmetry={"severe_asymmetry_count": 0, "asymmetric_concepts": 0, "direct_media_unreachable_by_alias_count": 0},
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
            "db_resolution": {
                "app_compatible": True,
                "settings_json_exists": True,
                "database_file_settings_used": True,
                "field_sources": {
                    "host": "settings_json",
                    "port": "settings_json",
                    "name": "settings_json",
                    "user": "settings_json",
                    "password": "settings_json_empty",
                },
                "runner_url_without_password": "postgresql://postgres@localhost:5432/blombooru",
                "app_equivalent_url_without_password": "postgresql://postgres@localhost:5432/blombooru",
                "urls_match": True,
                "runner_matches_app_equivalent": True,
                "password_present": False,
                "password_value_recorded": False,
            },
        },
        proof={"passed": True, "changed_tables": [], "missing_tables": [], "forbidden_tables_checked": list(runner.FORBIDDEN_TABLES)},
        media={
            "total_media": 0,
            "eligible_policy": "content_class IN ('anime', 'unknown')",
            "eligible_media_count": 0,
            "eligible_media_pct": 0,
            "media_with_any_tags": 0,
            "media_with_ai_tag_provenance": 0,
            "media_without_ai_tags": 0,
            "eligible_media_with_ai_tag_provenance": 0,
            "eligible_media_without_ai_tag_provenance": 0,
            "eligible_ai_tag_provenance_pct": 0,
            "ai_expansion_denominator_policy": "eligible_media",
            "media_with_source_layer_signals": 0,
            "media_without_source_layer_signals": 0,
            "media_with_source_concept_evidence_or_links": 0,
            "content_class_distribution": {},
            "tags": {"translation": {"total": 0}, "total_tags": 0},
        },
        source_layer={
            "source_records": {"by_provider": {}, "linked_to_media": 0, "distinct_media": 0, "distinct_eligible_media": 0, "coverage_denominator_policy": "distinct_media"},
            "source_metadata_records_total": 0,
            "source_metadata_records_linked_to_media": 0,
            "source_metadata_distinct_media_count": 0,
            "source_metadata_distinct_eligible_media_count": 0,
            "source_metadata_distinct_media_pct": 0,
            "source_metadata_distinct_eligible_media_pct": 0,
            "source_metadata_coverage_denominator_policy": "distinct_media",
            "f7a_candidate_coverage": {"distinct_media_with_candidates": 0},
            "source_assertions_by_status": {},
        },
        concepts={"total_source_concepts": 0, "by_status": {}, "by_concept_type_hint": {}, "aliases_total": 0, "evidence_total": 0, "search_index_total": 0, "concepts_with_no_media": 0, "concepts_with_no_aliases": 0, "concepts_with_no_evidence": 0, "concepts_with_no_search_index": 0, "same_alias_key_across_multiple_concepts": []},
        symmetry={
            "concepts_checked": 0,
            "aliases_checked": 0,
            "exact_symmetric_concepts": 0,
            "explainable_no_media_concepts": 0,
            "asymmetric_concepts": 0,
            "severe_asymmetry_count": 0,
            "one_way_link_count": 0,
            "fragmentation_count": 0,
            "overbroad_expansion_count": 0,
            "direct_media_unreachable_by_alias_count": 0,
            "direct_media_unreachable_active_count": 0,
            "direct_media_unreachable_needs_review_count": 0,
            "direct_media_unreachable_sample_count": 0,
            "hidden_status_raw_match_count": 0,
            "hidden_status_leak_count": 0,
            "metacharacter_alias_count": 0,
        },
        alias_gaps={
            "gap_buckets": {},
            "gap_bucket_details": {},
            "sample_limit_policy": {
                "counts_are_full": True,
                "samples_are_limited_to_examples_only": True,
                "default_sample_limit": runner.ALIAS_GAP_SAMPLE_LIMIT,
            },
            "normal_tag_gap_policy": {
                "bucket": "identity_tag_present_no_source_concept_alias",
                "table_present": True,
                "total_normal_tags": 0,
                "identity_eligible_normal_tags": 0,
                "excluded_visual_or_meta_tags": 0,
                "missing_identity_tags_without_source_concept_alias": 0,
                "visual_tags_counted_in_total_gap_signals": False,
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
        "runner_import_safety",
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
    assert summary["runner_import_safety"]["app_module_imports"] is False
    assert summary["runner_import_safety"]["settings_json_write_on_import"] is False
    assert summary["media_coverage"]["ai_expansion_denominator_policy"] == "eligible_media"
    assert summary["source_layer_coverage"]["source_metadata_coverage_denominator_policy"] == "distinct_media"
    assert "direct_media_unreachable_by_alias_count" in summary["search_symmetry"]
    assert summary["alias_gap_analysis"]["gap_bucket_details"] == {}
    assert summary["alias_gap_analysis"]["sample_limit_policy"]["counts_are_full"] is True
    json.dumps(summary, ensure_ascii=False)


def test_public_report_does_not_contain_forbidden_private_patterns(tmp_path: Path) -> None:
    report = runner.public_report_markdown(minimal_public_summary(tmp_path))

    assert runner.scan_public_text(report) == []
