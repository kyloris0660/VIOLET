"""Focused tests for Phase 4.5-PX1 Pixiv metadata and dedup dry-run runner."""

from __future__ import annotations

import json
import inspect
import subprocess

from scripts import run_phase45_px1_pixiv_metadata_and_dedup_dry_run as px1


def candidate(
    media_id: int,
    *,
    filename: str | None = None,
    source: str = "",
    file_hash: str | None = "hash-a",
    pixiv_like: bool | None = None,
    work_ids: tuple[str, ...] | None = None,
    page_indexes: tuple[int | None, ...] = (0,),
    content_class: str = "anime",
    has_metadata: bool = False,
    has_pixiv_metadata: bool = False,
    source_metadata_count: int = 0,
    entity_risk_count: int = 0,
    manual_locked_tag_count: int = 0,
    album_count: int = 0,
) -> px1.MediaCandidate:
    filename = filename or f"{media_id}.png"
    if work_ids is None:
        matches = px1.extract_pixiv_ids_from_text(filename, source_kind="filename")
        work_ids = tuple(match.work_id for match in matches)
        page_indexes = tuple(match.page_index for match in matches) or page_indexes
    return px1.MediaCandidate(
        media_id=media_id,
        filename=filename,
        source=source,
        content_class=content_class,
        file_hash=file_hash,
        file_size=100 + media_id,
        pixiv_like=bool(pixiv_like if pixiv_like is not None else work_ids),
        pixiv_work_ids=work_ids,
        pixiv_page_indexes=page_indexes,
        pixiv_prior_reasons=("filename_pixiv_id_pattern",) if work_ids else (),
        has_any_source_metadata=has_metadata or has_pixiv_metadata,
        has_pixiv_source_metadata=has_pixiv_metadata,
        ai_tag_count=3,
        manual_locked_tag_count=manual_locked_tag_count,
        source_metadata_count=source_metadata_count,
        entity_risk_count=entity_risk_count,
        album_count=album_count,
    )


def test_pixiv_id_extraction_from_filename() -> None:
    matches = px1.extract_pixiv_ids_from_text("123456789_p12.png", source_kind="filename")

    assert [(match.work_id, match.page_index) for match in matches] == [("123456789", 12)]


def test_pixiv_like_candidate_classification_from_mock_row() -> None:
    row = {
        "id": 10,
        "filename": "987654321_p0.jpg",
        "source": "",
        "content_class": "anime",
        "file_hash": "abc",
        "file_size": 42,
        "source_metadata_total": 0,
        "source_metadata_pixiv": 0,
        "source_tag_observation_pixiv": 0,
        "source_name_observation_pixiv": 0,
        "source_assertion_pixiv": 0,
        "source_name_candidates_pixiv": 0,
        "source_concept_pixiv": 0,
        "source_concept_signal_pixiv": 0,
        "ai_tag_count": 0,
        "manual_locked_tag_count": 0,
        "source_concept_risk_count": 0,
        "entity_candidate_count": 0,
        "entity_assignment_count": 0,
        "album_count": 0,
        "description": "",
    }

    classified = px1.media_candidate_from_row(row)

    assert classified.pixiv_like is True
    assert classified.pixiv_work_ids == ("987654321",)
    assert classified.reliable_pixiv_prior is True


def test_exact_duplicate_grouping_by_hash_only() -> None:
    candidates = [
        candidate(1, file_hash="same"),
        candidate(2, file_hash="same"),
        candidate(3, file_hash=None),
        candidate(4, file_hash="solo"),
    ]

    groups = px1.group_exact_duplicates(candidates)

    assert [[item.media_id for item in group] for group in groups] == [[1, 2]]


def test_retention_policy_prefers_pixiv_like_candidate() -> None:
    plan = px1.duplicate_group_plan(
        [candidate(10, pixiv_like=False, work_ids=()), candidate(20, filename="11111111_p0.png")],
        group_index=1,
    )

    assert plan["retained_media_candidate"] == 20
    assert plan["retention_reason"] == "pixiv_like_or_source_metadata_preferred"
    assert plan["execution_allowed_in_px1"] is False


def test_retention_policy_prefers_existing_source_metadata_signal() -> None:
    plan = px1.duplicate_group_plan(
        [
            candidate(1, filename="11111111_p0.png"),
            candidate(2, filename="11111111_p1.png", has_pixiv_metadata=True),
        ],
        group_index=1,
    )

    assert plan["retained_media_candidate"] == 2


def test_duplicate_retention_labels_non_pixiv_source_metadata_preference() -> None:
    plan = px1.duplicate_group_plan(
        [
            candidate(8, pixiv_like=False, work_ids=(), has_metadata=True),
            candidate(4, pixiv_like=False, work_ids=()),
        ],
        group_index=1,
    )

    assert plan["retained_media_candidate"] == 8
    assert plan["retention_reason"] == "source_metadata_preferred"


def test_all_non_pixiv_duplicate_group_retains_lowest_id() -> None:
    plan = px1.duplicate_group_plan(
        [
            candidate(9, pixiv_like=False, work_ids=()),
            candidate(4, pixiv_like=False, work_ids=()),
        ],
        group_index=1,
    )

    assert plan["retained_media_candidate"] == 4
    assert plan["retention_reason"] == "deterministic_lowest_media_id"


def test_conflicting_pixiv_ids_need_manual_review() -> None:
    plan = px1.duplicate_group_plan(
        [candidate(1, filename="11111111_p0.png"), candidate(2, filename="22222222_p0.png")],
        group_index=1,
    )

    assert plan["conflicting_pixiv_work_ids"] is True
    assert plan["needs_manual_review"] is True
    assert plan["auto_delete_candidate"] is False


def test_attached_data_risk_blocks_auto_delete_candidate() -> None:
    plan = px1.duplicate_group_plan(
        [
            candidate(1, filename="11111111_p0.png"),
            candidate(2, pixiv_like=False, work_ids=(), entity_risk_count=1),
        ],
        group_index=1,
    )

    assert plan["blocked_from_auto_delete"] is True
    assert "would_delete_has_entity_candidate_or_assignment" in plan["risk_reasons"]


def test_duplicate_dry_run_never_emits_executable_deletion() -> None:
    plan = px1.duplicate_group_plan([candidate(1), candidate(2)], group_index=1)
    parser = px1.build_parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}

    assert plan["execution_allowed_in_px1"] is False
    assert "delete" not in " ".join(sorted(option_strings)).lower()


def test_metadata_selection_excludes_would_delete_candidates() -> None:
    keep = candidate(1, filename="11111111_p0.png", has_pixiv_metadata=True)
    delete = candidate(2, filename="11111111_p1.png")
    plan = px1.duplicate_group_plan([keep, delete], group_index=1)

    selected, summary = px1.select_metadata_targets([keep, delete], [plan], limit=10)

    assert selected == []
    assert summary["excluded_reason_counts"]["already_has_source_metadata"] == 1
    assert summary["excluded_reason_counts"]["would_delete_exact_duplicate"] == 1


def test_metadata_selection_respects_bounded_limit() -> None:
    candidates = [candidate(i, filename=f"1234567{i}_p0.png", file_hash=f"hash-{i}") for i in range(10, 15)]

    selected, summary = px1.select_metadata_targets(candidates, [], limit=3)

    assert len(selected) == 3
    assert summary["selected_count"] == 3
    assert summary["eligible_before_limit"] == 5


def test_unknown_pixiv_like_candidates_are_not_selected_for_provider_execution() -> None:
    unknown = candidate(1, filename="11111111_p0.png", content_class="unknown")
    anime = candidate(2, filename="22222222_p0.png", content_class="anime", file_hash="hash-b")

    selected, summary = px1.select_metadata_targets([unknown, anime], [], limit=10)

    assert [item.media_id for item in selected] == [2]
    assert summary["unknown_excluded_from_provider_execution"] == 1
    assert summary["provider_execution_eligible_anime_candidates"] == 1


def test_pixiv_inventory_reports_provider_execution_privacy_counts() -> None:
    anime = candidate(1, filename="11111111_p0.png", content_class="anime")
    unknown = candidate(2, filename="22222222_p0.png", content_class="unknown", file_hash="hash-b")
    non_anime = candidate(3, filename="33333333_p0.png", content_class="non_anime", file_hash="hash-c")
    with_metadata = candidate(4, filename="44444444_p0.png", has_pixiv_metadata=True, file_hash="hash-d")

    inventory = px1.build_pixiv_inventory(
        {"total_media": 4, "eligible_media": 2},
        [anime, unknown, non_anime, with_metadata],
        {},
        [],
    )

    assert inventory["pixiv_like_media_candidates"] == 4
    assert inventory["provider_execution_eligible_anime_candidates"] == 1
    assert inventory["provider_execution_excluded_unknown"] == 1
    assert inventory["provider_execution_excluded_non_anime"] == 1
    assert inventory["provider_execution_excluded_already_has_source_metadata"] == 1


def test_source_layer_allowed_mutation_table_classification() -> None:
    before = {"tables": {"blombooru_source_metadata_records": {"count": 1, "fingerprint": "a"}}}
    after = {"tables": {"blombooru_source_metadata_records": {"count": 2, "fingerprint": "b"}}}

    delta = px1.classify_table_mutations(before, after)

    assert delta["passed"] is True
    assert delta["expected_changed_table_names"] == ["blombooru_source_metadata_records"]


def test_forbidden_runtime_tables_are_forbidden() -> None:
    forbidden_tables = {
        "blombooru_media",
        "blombooru_media_tags",
        "blombooru_tags",
        "blombooru_entities",
        "blombooru_source_concept_signals",
    }

    assert forbidden_tables <= px1.FORBIDDEN_WRITE_TABLES


def test_public_redaction_catches_paths_filenames_tokens_and_cookies() -> None:
    findings = px1.scan_public_text(
        r"C:\Users\kyloris\Pictures\12345678_p0.png cookie=sessionid authorization=Bearer abcdefghijk"
    )
    finding_types = {finding["type"] for finding in findings}

    assert {"local_path_like", "media_filename_like", "secret_like"} <= finding_types


def test_summary_json_required_fields() -> None:
    summary = {field: None for field in px1.SUMMARY_REQUIRED_FIELDS}

    assert px1.validate_summary_schema(summary)["passed"] is True
    assert px1.validate_summary_schema({})["passed"] is False


def test_gallery_dl_command_enforces_no_original_download_policy() -> None:
    entrypoint = px1.GalleryDlEntrypoint(True, "unit_test", ("gallery-dl",), "1.0")

    command = px1.build_gallery_dl_metadata_command(entrypoint, "12345678", sleep_request_seconds=0)

    assert "--dump-json" in command
    assert "--no-download" in command
    assert "--dest" not in command
    assert "--directory" not in command
    assert "https://www.pixiv.net/artworks/12345678" in command


def test_gallery_dl_nested_event_array_output_is_parsed() -> None:
    stdout = json.dumps(
        [
            [1, {"category": "pixiv"}],
            [2, "https://i.pximg.net/img-master/img/2026/01/01/00/00/00/12345678_p0.jpg", {"id": 12345678, "num": 0}],
        ]
    )

    records = px1._metadata_dicts_from_gallery_dl(stdout)

    assert records == [{"id": 12345678, "num": 0}]


def test_gallery_dl_pretty_json_object_output_is_parsed_as_whole_document() -> None:
    stdout = json.dumps({"id": 12345678, "num": 0, "title": "pretty", "tags": ["tag"]}, indent=2)

    rows = px1._json_rows_from_stdout(stdout)
    records = px1._metadata_dicts_from_gallery_dl(stdout)

    assert len(rows) == 1
    assert isinstance(rows[0], dict)
    assert records == [{"id": 12345678, "num": 0, "title": "pretty", "tags": ["tag"]}]


def test_existing_raw_cache_prevents_provider_call(tmp_path) -> None:
    cached = candidate(42, filename="12345678_p0.png")
    paths = px1.raw_cache_paths(tmp_path, cached)
    paths["stdout"].parent.mkdir(parents=True, exist_ok=True)
    paths["stdout"].write_text(
        json.dumps([2, "https://example.invalid", {"id": 12345678, "num": 0, "title": "cached", "tags": ["tag"]}]),
        encoding="utf-8",
    )
    paths["stderr"].write_text("", encoding="utf-8")

    def fail_runner(*args, **kwargs):
        raise AssertionError("provider should not be called when raw cache normalizes")

    request, success, failure = px1.run_single_metadata_request(
        cached,
        px1.GalleryDlEntrypoint(True, "unit_test", ("gallery-dl",), "1.0"),
        raw_dir=tmp_path,
        timeout=1,
        sleep_request_seconds=0,
        runner=fail_runner,
    )

    assert failure is None
    assert success is not None
    assert success["metadata_request_status"] == "success_cache_hit"
    assert request["cache_hit"] is True
    assert request["provider_called"] is False


def write_cached_not_found(raw_dir, target: px1.MediaCandidate) -> None:
    paths = px1.raw_cache_paths(raw_dir, target)
    paths["stdout"].parent.mkdir(parents=True, exist_ok=True)
    paths["stdout"].write_text(
        json.dumps([[-1, {"error": "NotFoundError", "message": "Requested resource could not be found"}]], indent=2),
        encoding="utf-8",
    )
    paths["stderr"].write_text("", encoding="utf-8")


def test_cached_failures_do_not_consume_provider_budget_or_block_uncached_candidates(tmp_path) -> None:
    cached = [candidate(i, filename=f"{10000000 + i}_p0.png", file_hash=f"hash-{i}") for i in range(1, 4)]
    uncached = candidate(50, filename="20000000_p0.png", file_hash="hash-50")
    raw_dir = tmp_path / "provider-cache" / "raw-gallery-dl-json"
    for item in cached:
        write_cached_not_found(raw_dir, item)
    calls: list[list[str]] = []

    def fake_runner(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps([2, "https://example.invalid", {"id": 20000000, "num": 0, "title": "ok", "tags": ["tag"]}]),
            stderr="",
        )

    budget = px1.ProviderFailureBudget(max_total_failures=1, max_failure_rate=0.0, max_consecutive_failures=1)
    requests, successes, failures, summary = px1.execute_metadata_requests(
        [*cached, uncached],
        px1.GalleryDlEntrypoint(True, "unit_test", ("gallery-dl",), "1.0"),
        output_dir=tmp_path,
        timeout=1,
        sleep_request_seconds=0,
        failure_budget=budget,
        runner=fake_runner,
    )

    assert len(calls) == 1
    assert len(requests) == 4
    assert len(successes) == 1
    assert len(failures) == 3
    assert summary["cache_failure_count"] == 3
    assert summary["cache_no_metadata_records_count"] == 3
    assert summary["cache_unavailable_private_or_deleted_count"] == 3
    assert summary["provider_called_count"] == 1
    assert summary["failure_budget"]["attempts"] == 1
    assert summary["failure_budget"]["total_failures"] == 0
    assert budget.stopped is False


def test_cache_only_replay_reports_network_not_attempted(tmp_path) -> None:
    cached = [candidate(i, filename=f"{30000000 + i}_p0.png", file_hash=f"hash-{i}") for i in range(1, 3)]
    raw_dir = tmp_path / "provider-cache" / "raw-gallery-dl-json"
    for item in cached:
        write_cached_not_found(raw_dir, item)

    def fail_runner(*args, **kwargs):
        raise AssertionError("cache-only replay must not call provider")

    budget = px1.ProviderFailureBudget(max_total_failures=1, max_failure_rate=0.0, max_consecutive_failures=1)
    requests, successes, failures, summary = px1.execute_metadata_requests(
        cached,
        px1.GalleryDlEntrypoint(True, "unit_test", ("gallery-dl",), "1.0"),
        output_dir=tmp_path,
        timeout=1,
        sleep_request_seconds=0,
        failure_budget=budget,
        runner=fail_runner,
    )

    assert len(requests) == 2
    assert successes == []
    assert len(failures) == 2
    assert summary["provider_called_count"] == 0
    assert summary["cache_hit_count"] == 2
    assert summary["failure_budget"]["attempts"] == 0
    assert px1.provider_network_attempted(summary) is False


def test_no_metadata_records_saves_private_raw_diagnostics(tmp_path) -> None:
    target = candidate(43, filename="12345679_p0.png")

    def fake_runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    request, success, failure = px1.run_single_metadata_request(
        target,
        px1.GalleryDlEntrypoint(True, "unit_test", ("gallery-dl",), "1.0"),
        raw_dir=tmp_path,
        timeout=1,
        sleep_request_seconds=0,
        runner=fake_runner,
    )

    assert success is None
    assert failure is not None
    assert failure["failure_reason"] == "no_metadata_records"
    assert failure["public_provider_output_shape"]["stdout_empty"] is True
    assert request["provider_called"] is True
    assert px1.raw_cache_paths(tmp_path, target)["stdout"].exists()
    assert px1.raw_cache_paths(tmp_path, target)["stderr"].exists()
    assert px1.raw_cache_paths(tmp_path, target)["diagnostics"].exists()


def test_public_provider_diagnosis_does_not_expose_raw_stdout_or_stderr() -> None:
    stdout = "raw provider stdout with private details"
    stderr = "cookie=sessionid"
    shape = px1.provider_output_shape(stdout, stderr, "no_metadata_records")

    public = px1.provider_output_diagnosis_summary(
        [{"failure_reason": "no_metadata_records", "public_provider_output_shape": shape}]
    )
    public_text = json.dumps(public, sort_keys=True)

    assert stdout not in public_text
    assert stderr not in public_text
    assert public["raw_stdout_stderr_public"] is False


def test_public_command_label_redacts_gallery_dl_auth_args() -> None:
    label = px1.public_command_label(
        [
            "scripts/run_phase45_px1_pixiv_metadata_and_dedup_dry_run.py",
            "--gallery-dl-command",
            r"gallery-dl --cookies C:\Users\kyloris\secret-cookie.txt -u alice -p hunter2 --config C:\Users\kyloris\gallery.conf --cookies-from-browser firefox:C:\Users\kyloris\profile",
            "--header",
            "Authorization: Bearer abcdefghijklmnop",
        ]
    )

    assert "alice" not in label
    assert "hunter2" not in label
    assert "secret-cookie" not in label
    assert "gallery.conf" not in label
    assert "profile" not in label
    assert "abcdefghijklmnop" not in label
    assert "[sensitive-arg-redacted]" in label


def test_gallery_dl_error_event_classifies_unavailable_not_parser_or_auth() -> None:
    stdout = json.dumps(
        [[-1, {"error": "NotFoundError", "message": "Requested resource could not be found"}]],
        indent=2,
    )
    stderr = "[pixiv][info] Refreshing access token\n"
    shape = px1.provider_output_shape(stdout, stderr, "no_metadata_records")
    summary = px1.provider_output_diagnosis_summary(
        [{"failure_reason": "no_metadata_records", "public_provider_output_shape": shape}]
    )

    assert shape["diagnostic_class"] == "unavailable_private_or_deleted"
    assert shape["provider_error_type"] == "NotFoundError"
    assert summary["unavailable_private_deleted_count"] == 1
    assert summary["auth_config_failure_count"] == 0
    assert summary["parser_mismatch_count"] == 0
    assert summary["provider_error_type_counts"] == {"NotFoundError": 1}


def test_report_generation_metadata_records_runtime_head() -> None:
    original = px1.git_value

    def fake_git_value(args):
        return "branch-x" if args == ["branch", "--show-current"] else "sha-x"

    try:
        px1.git_value = fake_git_value
        metadata = px1.report_generation_metadata()
    finally:
        px1.git_value = original

    assert metadata["git_branch"] == "branch-x"
    assert metadata["git_sha"] == "sha-x"
    assert metadata["git_sha_scope"] == "runtime_head_at_report_generation"
    assert metadata["operational_result_reused_older_artifact"] is False


def test_px1_searchable_assertions_use_needs_review_policy() -> None:
    policy = px1.searchable_assertion_write_policy()
    source = inspect.getsource(px1._upsert_searchable_assertion)

    assert policy["new_px1_assertion_status"] == "needs_review"
    assert policy["new_px1_requires_review"] is True
    assert policy["new_px1_searchable_active"] is False
    assert px1.PX1_SEARCHABLE_ASSERTION_STATUS != "searchable_active"
    assert ":assertion_status, :confidence" in source
    assert "'searchable_active', :confidence" not in source


def test_provider_failure_budget_stops_repeated_auth_and_rate_limit_failures() -> None:
    auth_budget = px1.ProviderFailureBudget(max_auth_failures=2, max_rate_limit_failures=5)
    auth_budget.record_failure("auth_or_config_failure")
    auth_budget.record_failure("auth_or_config_failure")

    rate_budget = px1.ProviderFailureBudget(max_auth_failures=5, max_rate_limit_failures=2)
    rate_budget.record_failure("rate_limited")
    rate_budget.record_failure("rate_limited")

    assert auth_budget.stopped is True
    assert auth_budget.stop_reason == "repeated_auth_or_config_failures"
    assert rate_budget.stopped is True
    assert rate_budget.stop_reason == "repeated_rate_limit_failures"


def test_provider_budget_uses_auth_or_rate_limit_diagnostic_class() -> None:
    assert (
        px1.provider_budget_failure_reason(
            {
                "failure_reason": "no_metadata_records",
                "public_provider_output_shape": {"diagnostic_class": "auth_or_config_failure"},
            }
        )
        == "auth_or_config_failure"
    )
    assert (
        px1.provider_budget_failure_reason(
            {
                "failure_reason": "no_metadata_records",
                "public_provider_output_shape": {"diagnostic_class": "rate_limited"},
            }
        )
        == "rate_limited"
    )


def test_transaction_mutation_proof_rolls_back_unexpected_changes() -> None:
    class FakeTransaction:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

    class FakeConnection:
        def __init__(self) -> None:
            self.transaction = FakeTransaction()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def begin(self):
            return self.transaction

    class FakeEngine:
        def __init__(self) -> None:
            self.connection = FakeConnection()

        def connect(self):
            return self.connection

    states = [
        {"tables": {"blombooru_media": {"count": 10, "fingerprint": "a"}}},
        {"tables": {"blombooru_media": {"count": 11, "fingerprint": "b"}}},
    ]

    def fake_state_builder(_conn):
        return states.pop(0)

    def fake_persister(_conn, successes, *, run_id):
        return [{"media_id": 1, "write_counts": {"SourceMetadataRecord_inserted": 1}}]

    engine = FakeEngine()
    write_rows, mutation_delta, committed = px1.persist_source_metadata_successes_with_mutation_proof(
        engine,
        [{"media_id": 1}],
        run_id="unit-test",
        state_builder=fake_state_builder,
        persister=fake_persister,
    )

    assert write_rows
    assert committed is False
    assert mutation_delta["passed"] is False
    assert mutation_delta["forbidden_changed_table_names"] == ["blombooru_media"]
    assert engine.connection.transaction.rolled_back is True
    assert engine.connection.transaction.committed is False


def test_inventory_only_mode_does_not_write_db() -> None:
    policy = px1.build_execution_policy(inventory_only=True, execute_metadata=False)

    assert policy["db_write_allowed"] is False
    assert policy["dedup_write_allowed"] is False
    assert policy["duplicate_deletion_option_present"] is False
