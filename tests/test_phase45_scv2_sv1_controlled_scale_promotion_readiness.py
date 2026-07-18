from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_phase45_scv2_sv1_controlled_scale_promotion_readiness import (
    MAX_MEDIA,
    MIN_MEDIA,
    CANONICAL_ALL_STAGES,
    Paths,
    ROOT,
    STABLE_ID_KEYS,
    SV1BlockedError,
    _percentile,
    _source_concept_evidence_logical_key,
    audit_connected_component_graph,
    accepted_media_public_wording,
    canonical_json,
    classify_pixiv_denominator,
    denominator_audit,
    derive_eligible_media_count,
    exact_resume_accounting,
    is_strict_test_database_name,
    recompute_inventory_accounting,
    require_resolved_descendant,
    run_stage,
    sanitize_stable_payload,
    scan_public,
    separated_ai_accounting,
    sha256_payload,
    validate_actual_rebuild_ledger,
    write_json,
    write_jsonl,
)


def test_sanitize_stable_payload_removes_development_row_references_recursively() -> None:
    payload = {
        "concept_id": 42,
        "nested": {"media_id": 99, "signal_ids": [1, 2], "provider_record_key": "stable"},
        "source_work_id": "123456",
        "artist_id": "987",
        "run_id": "accepted-run",
    }

    result = sanitize_stable_payload(payload)

    assert result == {
        "nested": {"provider_record_key": "stable"},
        "source_work_id": "123456",
        "artist_id": "987",
        "run_id": "accepted-run",
    }


def test_stable_id_allowlist_contains_provider_ids_but_not_database_ids() -> None:
    assert {"source_work_id", "artist_id", "run_id"}.issubset(STABLE_ID_KEYS)
    assert "concept_id" not in STABLE_ID_KEYS
    assert "media_id" not in STABLE_ID_KEYS
    assert "source_metadata_record_id" not in STABLE_ID_KEYS


def test_canonical_fingerprint_is_order_stable_for_mapping_keys() -> None:
    left = {"b": 2, "a": {"d": 4, "c": 3}}
    right = {"a": {"c": 3, "d": 4}, "b": 2}

    assert canonical_json(left) == canonical_json(right)
    assert sha256_payload(left) == sha256_payload(right)


def test_source_concept_evidence_logical_key_closes_nullable_signal_gap() -> None:
    row = {
        "concept_id": 101,
        "signal_id": None,
        "media_id": 202,
        "source_metadata_record_id": None,
        "provider": "accepted_ml2",
        "evidence_type": "trusted_creator_media_support",
        "evidence_strength": "trusted",
        "payload": {"b": 2, "a": 1},
        "run_id": "accepted-run",
        "status": "accepted",
    }
    reordered = {**row, "payload": {"a": 1, "b": 2}}

    assert _source_concept_evidence_logical_key(row) == _source_concept_evidence_logical_key(reordered)


def test_source_concept_evidence_logical_key_distinguishes_media_support() -> None:
    base = {
        "concept_id": 101,
        "signal_id": None,
        "media_id": 202,
        "source_metadata_record_id": None,
        "provider": "accepted_ml2",
        "evidence_type": "trusted_creator_media_support",
        "evidence_strength": "trusted",
        "payload": {},
        "run_id": "accepted-run",
        "status": "accepted",
    }

    assert _source_concept_evidence_logical_key(base) != _source_concept_evidence_logical_key({**base, "media_id": 203})


def test_public_scan_allows_only_the_required_stable_key_stage_identifier() -> None:
    result = scan_public(
        "public aggregate report",
        {"pipeline_contract": {"executed_stages": ["stable_key_evidence_export_import"]}},
    )

    assert result["passed"] is True
    assert result["negative_control_passed"] is True


def test_public_scan_still_blocks_real_secret_tokens() -> None:
    result = scan_public("", {"credential": "sk-example_token_12345"})

    assert result["passed"] is False
    assert {finding["reason"] for finding in result["findings"]} == {"secret_token"}


def test_public_scan_does_not_treat_safe_schema_keys_as_values() -> None:
    result = scan_public("public aggregate report", {"task_branch_start_sha": "abcdef123456"})

    assert result["passed"] is True


@pytest.mark.parametrize("count", [MIN_MEDIA, 12000, MAX_MEDIA])
def test_declared_scale_bounds_include_only_the_authorized_range(count: int) -> None:
    assert MIN_MEDIA <= count <= MAX_MEDIA


def test_percentile_uses_bounded_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]

    assert _percentile(values, 0.50) == 3.0
    assert _percentile(values, 0.95) == 100.0
    assert _percentile([], 0.95) == 0.0


def test_outcome_vocabulary_accounts_each_selected_media_exactly_once() -> None:
    outcomes = Counter(
        [
            "imported",
            "compatible_existing_media_reused",
            "duplicate_content_skipped",
            "deferred_nonblocking_source_unavailable",
            "blocking_failed",
        ]
    )

    assert sum(outcomes.values()) == 5
    assert set(outcomes) == {
        "imported",
        "compatible_existing_media_reused",
        "duplicate_content_skipped",
        "deferred_nonblocking_source_unavailable",
        "blocking_failed",
    }


@pytest.mark.parametrize("name", ["blombooru_test", "blombooru_scv2_sv1_test_20260718", "blombooru_foo_test"])
def test_strict_test_database_identity_accepts_delimited_segment(name: str) -> None:
    assert is_strict_test_database_name(name)


@pytest.mark.parametrize("name", ["blombooru", "blombooru_contest", "blombooru_latest", "blombooru_testimony", "other_test"])
def test_strict_test_database_identity_rejects_substrings_and_production(name: str) -> None:
    assert not is_strict_test_database_name(name)


def test_resolved_private_path_requires_true_descendant(tmp_path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    child = root / "run"
    assert require_resolved_descendant(child, root, label="output") == child.resolve()
    with pytest.raises(SV1BlockedError):
        require_resolved_descendant(root, root, label="output")
    with pytest.raises(SV1BlockedError):
        require_resolved_descendant(tmp_path / "private-sibling", root, label="output")
    with pytest.raises(SV1BlockedError):
        require_resolved_descendant(root / ".." / "escape", root, label="output")


def test_inventory_accounting_recomputes_final_rows() -> None:
    rows = [
        {"preselection_outcome": "eligible_unique", "inventory_outcome": "selected"},
        {"preselection_outcome": "eligible_unique", "inventory_outcome": "eligible_not_selected"},
        {"preselection_outcome": "excluded_duplicate", "inventory_outcome": "excluded_duplicate"},
    ]
    result = recompute_inventory_accounting(rows)
    assert result["preselection_outcome_counts"]["eligible_unique"] == 2
    assert result["final_outcome_counts"] == {
        "selected": 1, "eligible_not_selected": 1, "excluded_duplicate": 1,
        "excluded_ineligible": 0, "excluded_unreadable": 0, "excluded_out_of_scope": 0,
    }
    assert result["preselection_accounting_equality_passed"]
    assert result["final_accounting_equality_passed"]


@pytest.mark.parametrize("count", [10000, 15000])
def test_eligible_media_count_is_parameter_safe(count: int) -> None:
    assert derive_eligible_media_count(manifest_count=count, database_count=count, import_ledger_count=count, ai_ledger_count=count) == count


def test_eligible_media_count_fails_closed_on_ledger_mismatch() -> None:
    with pytest.raises(SV1BlockedError):
        derive_eligible_media_count(manifest_count=10000, database_count=10000, import_ledger_count=10000, ai_ledger_count=9999)


def test_exact_resume_reports_zero_current_writes_and_separate_cumulative_totals() -> None:
    result = exact_resume_accounting(
        checkpoint_media=12000, checkpoint_storage=12000,
        current_runtime_seconds=0.125, original_runtime_seconds=3599.5,
    )
    assert result["current_invocation"] == {
        "new_import_count": 0, "storage_write_count": 0,
        "runtime_seconds": 0.125, "resumed_exact_checkpoint": True,
    }
    assert result["cumulative_checkpoint_state"]["imported_media_count"] == 12000
    assert result["original_execution"]["runtime_seconds"] == 3599.5


def test_filename_and_stored_path_are_parsed_independently() -> None:
    assert classify_pixiv_denominator("12345678_p0.jpg", "media/original/12345678_p0.jpg")[0] == "filename_and_stored_path_agree"
    assert classify_pixiv_denominator("12345678_p0.jpg", "media/original/no-id.jpg")[0] == "filename_only_candidate"
    assert classify_pixiv_denominator("no-id.jpg", "media/original/12345678_p0.jpg")[0] == "stored_path_only_candidate"
    assert classify_pixiv_denominator("12345678_p0.jpg", "media/original/87654321_p0.jpg")[0] == "filename_stored_path_work_id_conflict"
    assert classify_pixiv_denominator("12345678_p0.jpg", "media/original/12345678_p1.jpg")[0] == "filename_stored_path_page_index_conflict"


def test_public_accepted_media_wording_does_not_hide_unavailable_rows() -> None:
    wording = accepted_media_public_wording()
    assert wording == "All accepted current media that remained available and fingerprint-compatible were included."
    assert "all accepted current media were included" not in wording.casefold()


def _graph_fixture(extra_links=(), pairs=(), stable_ids=None, roles=None):
    stable_ids = stable_ids or {}
    roles = roles or {}
    concepts = {key: {"status": "active", "stable_identity_fingerprint": stable_ids.get(key)} for key in ("a", "b", "c")}
    signals = {key: {"status": "active", "role_hint": roles.get(key, "artist")} for key in ("x", "y", "z")}
    links = [
        {"concept_key": concept, "signal_key": signal, "link_status": "active"}
        for concept, signal in extra_links
    ]
    return audit_connected_component_graph(concepts, signals, links, pairs)


def test_graph_audit_detects_direct_cannot_conflict() -> None:
    result = _graph_fixture(extra_links=(("a", "x"), ("a", "y")), pairs=({"left_signal_key": "x", "right_signal_key": "y", "disposition": "cannot_link"},))
    assert result["direct_cannot_link_violation_count"] == 1
    assert result["transitive_cannot_link_violation_count"] == 0


def test_graph_audit_detects_multihop_transitive_and_deferred_union() -> None:
    links = (("a", "x"), ("a", "z"), ("b", "z"), ("b", "y"))
    cannot = _graph_fixture(extra_links=links, pairs=({"left_signal_key": "x", "right_signal_key": "y", "disposition": "cannot_link"},))
    deferred = _graph_fixture(extra_links=links, pairs=({"left_signal_key": "x", "right_signal_key": "y", "disposition": "deferred_nonblocking"},))
    assert cannot["direct_cannot_link_violation_count"] == 0
    assert cannot["transitive_cannot_link_violation_count"] == 1
    assert deferred["deferred_identity_union_count"] == 1


def test_graph_audit_accepts_disconnected_endpoints() -> None:
    result = _graph_fixture(extra_links=(("a", "x"), ("b", "y")), pairs=({"left_signal_key": "x", "right_signal_key": "y", "disposition": "cannot_link"},))
    assert result["direct_cannot_link_violation_count"] == 0
    assert result["transitive_cannot_link_violation_count"] == 0


def test_graph_audit_detects_multi_stable_identity_and_cross_role() -> None:
    result = _graph_fixture(
        extra_links=(("a", "x"), ("a", "z"), ("b", "z"), ("b", "y")),
        stable_ids={"a": "stable-a", "b": "stable-b"}, roles={"x": "artist", "y": "character", "z": "artist"},
    )
    assert result["multi_stable_id_creator_component_count"] == 1
    assert result["unauthorized_cross_role_component_count"] == 1


def test_public_scan_rejects_raw_local_absolute_path_in_exact_json_bytes() -> None:
    result = scan_public("public report", {"python_identity": {"sys_executable": r"C:\Users\private\venv\Scripts\python.exe"}})
    assert result["passed"] is False
    assert result["exact_final_bytes_scanned"] is True


def test_unsafe_output_root_is_rejected_before_creation() -> None:
    outside = ROOT.parent / "AnimeLocalBooru-outside-finalization-safety-test"
    assert not outside.exists(), "test target must start absent"
    args = SimpleNamespace(
        output_dir=outside,
        storage_root=ROOT / ".local_test_storage" / "root-before-write-test",
    )
    with pytest.raises(SV1BlockedError, match="output_root_outside_private_root"):
        run_stage(args)
    assert not outside.exists()


def test_canonical_all_orchestration_covers_every_required_finalization_stage() -> None:
    assert CANONICAL_ALL_STAGES == (
        "prepare", "import", "ai", "evidence", "promotion", "benchmark", "rebuild",
        "connected-graph-audits", "repair-benchmark", "finalization-accounting",
        "validation", "repair-finalize",
    )


def _write_rebuild_ledger(path: Path, **overrides: object) -> None:
    ledger = {
        "blocking_creator_gap_count": 0,
        "actual_r2r_ml2_derivation_replayed": True,
        "derived_row_import_count": 0,
        "accepted_creator_family_traceability": 1.0,
        "accepted_r2r_disposition_compatibility": 1.0,
        "logical_subset_comparison": {
            "graph_logical_mismatch_count": 0,
            "search_logical_mismatch_count": 0,
            "numeric_row_id_equality_claimed": False,
        },
        "ledger_algorithm_version": "test-v2",
        "derivation_algorithm_identity": "test-derivation",
        **overrides,
    }
    ledger["ledger_fingerprint"] = sha256_payload(ledger)
    write_json(path, ledger)


def test_rebuild_ledger_nonzero_gap_cannot_be_masked(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    _write_rebuild_ledger(tmp_path / "actual-derived-rebuild-verification.json", blocking_creator_gap_count=1)
    with pytest.raises(SV1BlockedError, match="blocking_creator_gap_count"):
        validate_actual_rebuild_ledger(paths)


def test_rebuild_ledger_missing_replay_proof_cannot_be_masked(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    _write_rebuild_ledger(tmp_path / "actual-derived-rebuild-verification.json")
    ledger = __import__("json").loads((tmp_path / "actual-derived-rebuild-verification.json").read_text(encoding="utf-8"))
    ledger.pop("actual_r2r_ml2_derivation_replayed")
    ledger["ledger_fingerprint"] = sha256_payload({key: value for key, value in ledger.items() if key != "ledger_fingerprint"})
    write_json(tmp_path / "actual-derived-rebuild-verification.json", ledger)
    with pytest.raises(SV1BlockedError, match="missing_fields"):
        validate_actual_rebuild_ledger(paths)


def test_ai_accounting_keeps_original_and_current_invocation_separate() -> None:
    result = separated_ai_accounting(
        {"coverage": 1.0, "reused_media_count": 12000, "newly_inferred_media_count": 0},
        checkpoint_existing=12000, newly_inferred=0,
    )
    assert result["original_accepted_execution"]["reused_media_count"] == 3420
    assert result["original_accepted_execution"]["newly_inferred_media_count"] == 8580
    assert result["current_repair_invocation"] == {
        "checkpoint_existing_covered_media_count": 12000,
        "newly_inferred_media_count": 0,
        "ai_inference_rerun": False,
    }
    assert "reused_media_count" not in result


def test_custom_scale_database_is_the_only_denominator_membership_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = Paths(tmp_path)
    write_jsonl(paths.manifest, [{"file_hash": "hash-1"}])
    write_json(paths.package, {"tables": {"source_metadata_records": []}})
    consulted: list[str] = []

    class Result:
        def mappings(self):
            return [{"hash": "hash-1", "filename": "12345678_p0.jpg", "path": "media/12345678_p0.jpg"}]

    class Connection:
        def execute(self, _statement):
            return Result()

    class Context:
        def __enter__(self): return Connection()
        def __exit__(self, *_args): return False

    class Engine:
        def connect(self): return Context()
        def dispose(self): return None

    def fake_engine(database: str):
        consulted.append(database)
        return Engine()

    monkeypatch.setattr("scripts.run_phase45_scv2_sv1_controlled_scale_promotion_readiness.engine_for", fake_engine)
    result = denominator_audit(paths, "blombooru_custom_test_scale")
    assert consulted == ["blombooru_custom_test_scale"]
    assert result["database_identity"] == "blombooru_custom_test_scale"
