from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure import (
    SV1BPreflightError,
    _strict_test_database,
    _generic_credential_findings,
    audit_runtime_parser_denominator_rows,
    audit_acquisition_closure_rows,
    canonical_work_id,
    build_full_candidate_dispositions,
    build_distinct_work_page_manifest,
    candidate_manifest_database_for_stage,
    classify_retry1_payload_drift_rows,
    classify_search_runtime_membership,
    compare_creator_family_states,
    execute_provider_manifest,
    filter_nonderived_source_package,
    finalize_r2r_proposal_classifications,
    outcome_for_pair,
    open_reason_for_pair,
    public_console_summary,
    sha256_payload,
    validate_graph_derivation_checkpoint,
    validate_output_root,
    validate_owned_output_root,
    validate_writable_databases,
)


def test_outcome_for_pair_preserves_terminal_and_page_local_precedence() -> None:
    records = [
        {
            "id": 1, "provider": "pixiv", "source_work_id": "123", "source_page_index": 0,
            "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "authenticated_provider_metadata",
            "status": "metadata_complete", "raw_metadata_json": {"id": 123},
            "provenance": {"source": "gallery_dl_authenticated_metadata", "stable_identity_key": {
                "provider": "pixiv", "work_id": "123", "page_index": 0,
            }},
        },
        {
            "id": 2, "provider": "pixiv", "source_work_id": "123", "source_page_index": 1,
            "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
            "status": "terminal_remote_unavailable",
            "raw_metadata_json": {"failure_reason": "authenticated_remote_deleted_private_unavailable", "last_attempt_at": "now"},
            "provenance": {},
        },
        {
            "id": 3, "provider": "pixiv", "source_work_id": "124", "source_page_index": 0,
            "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
            "status": "deferred_nonblocking_source_page_mismatch", "raw_metadata_json": {}, "provenance": {},
        },
    ]
    evidence = [{
        "source_metadata_record_id": 3,
        "evidence_kind": "deferred_nonblocking_source_page_mismatch",
        "status": "active",
        "provenance": {
            "governance_policy_version": "source_page_mismatch_deferred_nonblocking_v1",
            "unsupported_page_link_created": False,
        },
    }]
    assert outcome_for_pair(records, "123", 0, evidence) == "trusted_exact_complete"
    assert outcome_for_pair(records, "123", 1, evidence) == "exact_terminal"
    assert outcome_for_pair(records, "124", 0, evidence) == "exact_governed_page_mismatch"
    assert outcome_for_pair(records, "124", 9, evidence) == "unacquired"
    assert outcome_for_pair(records, "125", 0) == "unacquired"


def test_outcome_for_pair_rejects_positive_status_without_canonical_trust_shape() -> None:
    row = {
        "id": 1, "provider": "pixiv", "source_work_id": "123", "source_page_index": 0,
        "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
        "status": "metadata_complete", "raw_metadata_json": {}, "provenance": {},
    }
    assert outcome_for_pair([row], "123", 0) == "unexplained"


def test_outcome_for_pair_marks_mixed_closed_truth_as_conflicting() -> None:
    complete = {
        "id": 1, "provider": "pixiv", "source_work_id": "123", "source_page_index": 0,
        "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "authenticated_provider_metadata",
        "status": "metadata_complete", "raw_metadata_json": {"id": 123},
        "provenance": {"source": "gallery_dl_authenticated_metadata", "stable_identity_key": {
            "provider": "pixiv", "work_id": "123", "page_index": 0,
        }},
    }
    terminal = {
        **complete, "id": 2, "status": "terminal_remote_unavailable",
        "raw_metadata_json": {"failure_reason": "authenticated_remote_deleted_private_unavailable", "last_attempt_at": "now"},
    }
    assert outcome_for_pair([complete, terminal], "123", 0) == "conflicting"


def test_outcome_for_pair_trusted_complete_supersedes_nonblocking_mismatch_history() -> None:
    complete = {
        "id": 1, "provider": "pixiv", "source_work_id": "123", "source_page_index": 0,
        "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "authenticated_provider_metadata",
        "status": "metadata_complete", "raw_metadata_json": {"id": 123},
        "provenance": {"source": "gallery_dl_authenticated_metadata", "stable_identity_key": {
            "provider": "pixiv", "work_id": "123", "page_index": 0,
        }},
    }
    deferred = {
        **complete, "id": 2, "data_type_label": "local_runtime_source_prior",
        "status": "deferred_nonblocking_source_page_mismatch", "raw_metadata_json": {},
        "provenance": {},
    }
    evidence = [{
        "source_metadata_record_id": 2,
        "evidence_kind": "deferred_nonblocking_source_page_mismatch",
        "status": "active",
        "provenance": {
            "governance_policy_version": "source_page_mismatch_deferred_nonblocking_v1",
            "unsupported_page_link_created": False,
        },
    }]
    assert outcome_for_pair([complete, deferred], "123", 0, evidence) == "trusted_exact_complete"


def test_acquisition_closure_aggregates_compatible_multi_record_page() -> None:
    pages = [{"media_stable_key": "a", "stable_work_id": "123", "requested_page_index": 0}]
    complete = {
        "id": 1, "media_stable_key": "a", "provider": "pixiv", "source_work_id": "123",
        "source_page_index": 0, "metadata_kind": "pixiv_ingestion_gate",
        "data_type_label": "authenticated_provider_metadata", "status": "metadata_complete",
        "raw_metadata_json": {"id": 123},
        "provenance": {"source": "gallery_dl_authenticated_metadata", "stable_identity_key": {
            "provider": "pixiv", "work_id": "123", "page_index": 0,
        }},
    }
    deferred = {
        **complete, "id": 2, "data_type_label": "local_runtime_source_prior",
        "status": "deferred_nonblocking_source_page_mismatch", "raw_metadata_json": {},
        "provenance": {},
    }
    evidence = [{
        "source_metadata_record_id": 2,
        "evidence_kind": "deferred_nonblocking_source_page_mismatch", "status": "active",
        "provenance": {
            "governance_policy_version": "source_page_mismatch_deferred_nonblocking_v1",
            "unsupported_page_link_created": False,
        },
    }]
    public, private = audit_acquisition_closure_rows(pages, [complete, deferred], evidence)
    assert public["passed"] is True
    assert public["page_outcome_counts"] == {"metadata_complete": 1}
    assert public["compatible_multi_record_page_count"] == 1
    assert public["duplicate_rows_treated_as_duplicate_requests"] is False
    assert len(private["duplicate_keys"]) == 1


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"status": "metadata_complete", "raw_metadata_json": {}, "provenance": {}}, "missing_normalized_shape"),
        ({"status": "metadata_complete", "raw_metadata_json": {"id": 1}, "provenance": {}}, "missing_trusted_provenance"),
        ({
            "status": "metadata_complete", "raw_metadata_json": {"id": 1},
            "provenance": {"source": "legacy"},
        }, "missing_stable_identity"),
        ({"status": "metadata_pending", "raw_metadata_json": {}, "provenance": {}}, "open_unacquired"),
    ],
)
def test_open_reason_for_pair_is_exact_and_deterministic(row, reason) -> None:
    record = {
        "provider": "pixiv", "source_work_id": "123", "source_page_index": 0,
        "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
        **row,
    }
    outcome = outcome_for_pair([record], "123", 0)
    assert open_reason_for_pair([record], "123", 0, outcome) == reason


def test_distinct_work_page_manifest_preserves_cross_media_outcomes_without_inheritance() -> None:
    rows = [
        {"stable_work_id": "123", "requested_page_index": 0, "media_stable_key": "a", "acquisition_state": "unacquired", "open_reason": "open_unacquired", "checkpoint_key": "1"},
        {"stable_work_id": "123", "requested_page_index": 0, "media_stable_key": "b", "acquisition_state": "trusted_exact_complete", "open_reason": None, "checkpoint_key": "2"},
    ]
    manifest = build_distinct_work_page_manifest(rows)
    assert manifest[0]["acquisition_state"] == "mixed_media_outcomes"
    assert manifest[0]["page_outcome_inherited_across_media"] is False
    assert [row["acquisition_state"] for row in manifest[0]["media_outcomes"]] == [
        "unacquired", "trusted_exact_complete",
    ]


def test_retry1_forensic_classifier_accepts_only_queue_control_drift() -> None:
    accepted = [{
        "provider_record_key": "queue:1", "provider": "pixiv", "media_content_key": "m",
        "source_work_id": None, "source_page_index": None,
        "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
        "status": "not_applicable_non_pixiv",
        "raw_metadata_json": {"parser_version": "old"},
        "provenance": {"parser_version": "old", "updated_at": "before"},
    }]
    current = [{
        **accepted[0],
        "raw_metadata_json": {"parser_version": "new"},
        "provenance": {"parser_version": "new", "updated_at": "after"},
    }]
    ledger, aggregate = classify_retry1_payload_drift_rows(accepted, current)
    assert len(ledger) == 1
    assert aggregate["accepted_provider_fact_mutation_count"] == 0
    assert aggregate["stable_identity_change_count"] == 0
    assert aggregate["reason_code_counts"] == {"refreshed_not_applicable_queue_record": 1}


def test_retry1_forensic_classifier_governs_accepted_target_missing_media_reference() -> None:
    accepted = [{
        "provider_record_key": "queue:outside-manifest", "provider": "pixiv",
        "media_content_key": "accepted-but-outside-finite-target",
        "source_work_id": None, "source_page_index": None,
        "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
        "status": "not_applicable_non_pixiv",
        "raw_metadata_json": {"parser_version": "old"},
        "provenance": {"parser_version": "old", "updated_at": "before"},
    }]
    current = [{
        **accepted[0], "media_content_key": None,
        "raw_metadata_json": {"parser_version": "new"},
        "provenance": {"parser_version": "new", "updated_at": "after"},
    }]
    ledger, aggregate = classify_retry1_payload_drift_rows(
        accepted, current, accepted_target_media_keys={"another-media-key"},
    )
    assert aggregate["stable_identity_change_count"] == 0
    assert aggregate["governed_target_missing_media_reference_count"] == 1
    assert ledger[0]["accepted_media_reference_target_missing"] is True
    assert ledger[0]["accepted_media_content_key_fingerprint"]


def test_retry1_forensic_classifier_detects_provider_fact_mutation() -> None:
    accepted = [{
        "provider_record_key": "provider:1", "provider": "pixiv", "media_content_key": "m",
        "source_work_id": "123", "source_page_index": 0,
        "metadata_kind": "provider_metadata", "data_type_label": "authenticated_provider_metadata",
        "status": "observed", "title": "accepted",
        "raw_metadata_json": {"id": 123}, "provenance": {"source": "provider"},
    }]
    current = [{**accepted[0], "title": "mutated"}]
    _, aggregate = classify_retry1_payload_drift_rows(accepted, current)
    assert aggregate["accepted_provider_fact_mutation_count"] == 1


@pytest.mark.parametrize("database", [
    "blombooru", "postgres", "template0", "template1", "production",
    "blombooru_contest", "blombooru_latest", "blombooru_testimony",
])
def test_strict_test_database_rejects_default_and_production(database: str) -> None:
    assert _strict_test_database(database) is False


def test_strict_test_database_accepts_sv1b_identity() -> None:
    assert _strict_test_database("blombooru_scv2_sv1b_metadata_graph_closure_test_20260719") is True


def test_post_checkpoint_b_stages_rebuild_candidates_from_fresh_primary() -> None:
    primary = "blombooru_scv2_sv1b_metadata_graph_closure_test_20260721_retry2"
    assert candidate_manifest_database_for_stage("pre-network-validation", primary) == primary
    assert candidate_manifest_database_for_stage("execute-provider", primary) == primary
    assert candidate_manifest_database_for_stage("inventory", primary) != primary


def test_generic_credential_scan_detects_delimited_values_without_old_fingerprint() -> None:
    secret_value = "".join(("credential", "_material", "_123456789012345"))
    bearer_value = "".join(("another", "_material", "_123456789012345"))
    key = "".join(("refresh", "_token"))
    secret_count, raw_config_count = _generic_credential_findings(
        f'{key}="{secret_value}"\n'
        f'Authorization: Bearer {bearer_value}\n'
        f'"pixiv": {{"{key}": "{secret_value}"}}'
    )
    assert secret_count >= 2
    assert raw_config_count == 1
    assert _generic_credential_findings(
        'credential_risk_waiver_policy="operator_accepted_existing_local_pixiv_credential_risk_sv1b_v1"'
    ) == (0, 0)


def test_work_id_canonicalization_removes_historical_leading_zeroes() -> None:
    assert canonical_work_id("000123") == "123"
    with pytest.raises(SV1BPreflightError, match="out_of_range"):
        canonical_work_id("0")


def test_runtime_parser_audit_accepts_exact_scv2_denominator_membership() -> None:
    result = audit_runtime_parser_denominator_rows([
        {"id": 1, "hash": "a", "filename": "1234567.jpg", "path": "media/original/1234567.jpg"},
        {"id": 2, "hash": "b", "filename": "001234567-P2.png", "path": "media/original/001234567-P2.png"},
        {"id": 3, "hash": "c", "filename": "plain.jpg", "path": "media/original/plain.jpg"},
    ])
    assert result["passed"] is True
    assert result["accepted_candidate_media_count"] == 2
    assert result["durable_candidate_media_count"] == 2


def test_runtime_parser_audit_fails_closed_on_parser_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.parse_approved_fields",
        lambda _fields: (),
    )
    with pytest.raises(SV1BPreflightError, match="runtime_parser_denominator_mismatch"):
        audit_runtime_parser_denominator_rows([
            {"id": 1, "hash": "a", "filename": "1234567.jpg", "path": "media/original/1234567.jpg"},
        ])


def test_acquisition_closure_requires_exact_trusted_page_outcomes() -> None:
    pages = [
        {"media_stable_key": "a", "stable_work_id": "1234567", "requested_page_index": 0},
        {"media_stable_key": "b", "stable_work_id": "2234567", "requested_page_index": 1},
        {"media_stable_key": "c", "stable_work_id": "3234567", "requested_page_index": 2},
    ]
    queue = [
        {
            "id": 1, "media_stable_key": "a", "provider": "pixiv",
            "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "authenticated_provider_metadata",
            "status": "metadata_complete", "source_work_id": "1234567", "source_page_index": 0,
            "provenance": {
                "source": "gallery_dl_authenticated_metadata",
                "stable_identity_key": {"provider": "pixiv", "work_id": "1234567", "page_index": 0},
            },
            "raw_metadata_json": {"id": 1234567},
        },
        {
            "id": 2, "media_stable_key": "b", "provider": "pixiv",
            "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
            "status": "terminal_remote_unavailable", "source_work_id": "2234567", "source_page_index": 1,
            "provenance": {}, "raw_metadata_json": {
                "failure_reason": "authenticated_remote_deleted_private_unavailable", "last_attempt_at": "now"
            },
        },
        {
            "id": 3, "media_stable_key": "c", "provider": "pixiv",
            "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
            "status": "deferred_nonblocking_source_page_mismatch", "source_work_id": "3234567", "source_page_index": 2,
            "provenance": {}, "raw_metadata_json": {},
        },
    ]
    evidence = [{
        "source_metadata_record_id": 3,
        "evidence_kind": "deferred_nonblocking_source_page_mismatch",
        "status": "active",
        "provenance": {
            "governance_policy_version": "source_page_mismatch_deferred_nonblocking_v1",
            "unsupported_page_link_created": False,
        },
    }]
    public, private = audit_acquisition_closure_rows(pages, queue, evidence)
    assert public["passed"] is True
    assert public["page_outcome_counts"] == {
        "deferred_nonblocking_source_page_mismatch": 1,
        "metadata_complete": 1,
        "terminal_remote_unavailable": 1,
    }
    assert private == {"missing_keys": [], "unexpected_keys": [], "duplicate_keys": []}


def test_acquisition_closure_rejects_untrusted_complete_and_missing_membership() -> None:
    pages = [
        {"media_stable_key": "a", "stable_work_id": "1234567", "requested_page_index": 0},
        {"media_stable_key": "b", "stable_work_id": "2234567", "requested_page_index": 1},
    ]
    queue = [{
        "id": 1, "media_stable_key": "a", "provider": "pixiv",
        "metadata_kind": "pixiv_ingestion_gate", "data_type_label": "local_runtime_source_prior",
        "status": "metadata_complete", "source_work_id": "1234567", "source_page_index": 0,
        "provenance": {"source": "canonical_pixiv_filename_path_prior"}, "raw_metadata_json": {},
    }]
    public, private = audit_acquisition_closure_rows(pages, queue, [])
    assert public["passed"] is False
    assert public["missing_page_queue_count"] == 1
    assert public["blocking_outcome_counts"] == {"blocking_metadata_complete": 1}
    assert len(private["missing_keys"]) == 1


def test_execute_provider_stops_before_runner_when_credential_gate_fails(tmp_path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "provider-queue-manifest-proof.json").write_text(
        json.dumps({"exact_open_work_membership_passed": True}), encoding="utf-8"
    )
    (output / "waiver-aware-secret-redaction-scan-proof.json").write_text(
        json.dumps({
            "passed": True,
            "credential_risk_waiver_policy": "operator_accepted_existing_local_pixiv_credential_risk_sv1b_v1",
            "raw_credential_exposure_count": 0,
            "raw_config_exposure_count": 0,
        }),
        encoding="utf-8",
    )
    (output / "full-pre-network-validation-proof.json").write_text(
        json.dumps({
            "passed": True,
            "head": "test-head",
            "provider_tooling_executed_before_validation": False,
            "failed_test_count": 0,
            "unexplained_skip_count": 0,
            "changed_python_py_compile_passed": True,
            "focused_stage_aware_tests_passed": True,
            "affected_regressions_passed": True,
            "full_default_non_e2e_passed": True,
            "exact_approved_skip_membership_passed": True,
            "environment_specific_profiles_passed": True,
            "json_parse_passed": True,
            "git_diff_check_passed": True,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.validate_owned_output_root",
        lambda *_args, **_kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.git",
        lambda *_args: "test-head",
    )
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.provider_gate_preflight",
        lambda: {"passed": False},
    )
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.correct_prior_inconclusive_canary_state",
        lambda *_args, **_kwargs: ("123456789", {"passed": True}),
    )
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.ingestion_runner.run",
        lambda _args: pytest.fail("provider runner must not be called"),
    )
    with pytest.raises(SV1BPreflightError, match="blocked_sv1b_provider_authentication"):
        execute_provider_manifest(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )


def test_execute_provider_requires_full_validation_before_profile_inspection(tmp_path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "provider-queue-manifest-proof.json").write_text(
        json.dumps({"exact_open_work_membership_passed": True}), encoding="utf-8"
    )
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.validate_owned_output_root",
        lambda *_args, **_kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.provider_gate_preflight",
        lambda: pytest.fail("profile inspection must remain unreachable"),
    )
    with pytest.raises(SV1BPreflightError, match="full_pre_network_validation_proof_missing"):
        execute_provider_manifest(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )


def test_replay_package_filter_excludes_all_derived_sourceconcept_rows() -> None:
    package = filter_nonderived_source_package(
        {
            "package_version": "old",
            "source": "test",
            "tables": {
                "source_metadata_records": [{"provider_record_key": "a"}],
                "source_tag_observations": [],
                "source_name_observations": [],
                "source_metadata_evidence": [],
                "source_searchable_name_assertions": [],
                "source_tag_registry": [],
                "source_name_registry": [],
                "source_concept_signals": [{"signal_key": "must-not-import"}],
                "source_concepts": [{"concept_key": "must-not-import"}],
            },
        },
        package_version="sv1b_acquired_nonderived_source_evidence_v1",
    )
    assert package["package_version"] == "sv1b_acquired_nonderived_source_evidence_v1"
    assert package["tables"]["source_metadata_records"] == [{"provider_record_key": "a"}]
    assert package["tables"]["source_concept_signals"] == []
    assert package["tables"]["source_concepts"] == []


def test_full_candidate_dispositions_replay_accepted_and_defer_only_new_pairs() -> None:
    from types import SimpleNamespace

    candidates = [
        SimpleNamespace(pair_id="p1", left_signal_key="a", right_signal_key="b"),
        SimpleNamespace(pair_id="p2", left_signal_key="c", right_signal_key="d"),
    ]
    dispositions, accounting = build_full_candidate_dispositions(
        candidates,
        [
            {
                "classification": "comparable", "target_pair_id": "p1",
                "accepted_pair_id": "old1", "accepted_disposition": "must_link",
            },
            {"classification": "genuine_target_missing", "accepted_pair_id": "old2"},
        ],
    )
    assert {row.pair_id: row.disposition for row in dispositions} == {
        "p1": "must_link",
        "p2": "deferred_nonblocking",
    }
    assert accounting["accepted_comparable_pair_count"] == 1
    assert accounting["accepted_genuine_target_missing_count"] == 1
    assert accounting["new_deferred_nonblocking_pair_count"] == 1
    assert accounting["normal_needs_review_count"] == 0
    assert accounting["llm_call_count"] == 0
    assert accounting["equation_balanced"] is True


@pytest.mark.parametrize("classification", ["ambiguous_remap", "conflicting_remap"])
def test_full_candidate_dispositions_reject_unsafe_accepted_remap(classification) -> None:
    with pytest.raises(SV1BPreflightError, match="postacquisition_r2r_remap_not_safe"):
        build_full_candidate_dispositions([], [{"classification": classification}])


def test_creator_family_comparison_accepts_only_monotonic_trusted_growth() -> None:
    accepted = {
        "stable-a": {
            "concept_key": "concept-a", "status": "active",
            "aliases": (("a", "creator_identity_alias", "active"),),
            "media_support": ("hash-a",),
        }
    }
    current = {
        "stable-a": {
            "concept_key": "concept-a", "status": "active",
            "aliases": (
                ("a", "creator_identity_alias", "active"),
                ("a2", "creator_identity_alias", "active"),
            ),
            "media_support": ("hash-a", "hash-b"),
        },
        "stable-b": {
            "concept_key": "concept-b", "status": "active",
            "aliases": (("b", "creator_identity_alias", "active"),),
            "media_support": ("hash-c",),
        },
    }
    result = compare_creator_family_states(accepted, current)
    assert result["accepted_family_traceable_count"] == 1
    assert result["changed_accepted_family_count"] == 1
    assert result["new_creator_family_count"] == 1
    assert result["new_alias_signal_count"] == 2
    assert result["new_media_support_count"] == 2
    assert result["every_changed_family_has_governed_reason"] is True


@pytest.mark.parametrize("mutation", ["removed_alias", "concept_key_changed", "disappeared"])
def test_creator_family_comparison_rejects_nonmonotonic_or_missing_identity(mutation) -> None:
    accepted = {
        "stable-a": {
            "concept_key": "concept-a", "status": "active",
            "aliases": (("a", "creator_identity_alias", "active"),),
            "media_support": ("hash-a",),
        }
    }
    if mutation == "disappeared":
        current = {}
    else:
        current = {
            "stable-a": {
                "concept_key": "concept-b" if mutation == "concept_key_changed" else "concept-a",
                "status": "active",
                "aliases": () if mutation == "removed_alias" else accepted["stable-a"]["aliases"],
                "media_support": ("hash-a",),
            }
        }
    result = compare_creator_family_states(accepted, current)
    if mutation == "disappeared":
        assert result["accepted_stable_identity_disappeared_count"] == 1
    else:
        assert result["every_changed_family_has_governed_reason"] is False


def _write_graph_checkpoint_fixture(output: Path) -> None:
    package = {"package_version": "test", "tables": {}}
    fingerprint = sha256_payload(package)
    values = {
        "acquisition-closure-and-package-proof.json": {
            "passed": True,
            "package": {"acquired_metadata_package_fingerprint": fingerprint},
        },
        "replay-acquired-evidence-import-proof.json": {
            "passed": True,
            "acquired_metadata_package_fingerprint": fingerprint,
            "localization_package_fingerprint": "localization-fingerprint",
            "primary_replay_nonderived_logical_fingerprint_equal": True,
        },
        "localization-closure-proof.json": {
            "passed": True,
            "localization_accounting_closed": True,
            "downstream_progression_allowed": True,
            "accepted_translation_state": {"fingerprint": "localization-fingerprint"},
        },
        "r2r-exact-remap-audit.json": {
            "target_completion_ready": True,
            "primary": {
                "accepted_snapshot_fingerprint": (
                    "25090761abff2c2ae9f7ef8d9ea04904c47a9f3a43ce03ab660a39502ae792fc"
                )
            },
        },
        "accepted-nonderived-evidence-proof.json": {
            "primary_reconciliation_passed": True,
            "replay_reconciliation_passed": True,
        },
        "acquired-nonderived-evidence-package-private.json": package,
    }
    for name, value in values.items():
        (output / name).write_text(json.dumps(value), encoding="utf-8")


def test_graph_derivation_checkpoint_binds_all_reusable_memberships(tmp_path: Path) -> None:
    _write_graph_checkpoint_fixture(tmp_path)
    result = validate_graph_derivation_checkpoint(tmp_path)
    assert result["passed"] is True
    assert result["acquired_package_fingerprint_match"] is True
    assert result["accepted_r2r_snapshot_pinned"] is True


def test_graph_derivation_checkpoint_rejects_localization_package_drift(tmp_path: Path) -> None:
    _write_graph_checkpoint_fixture(tmp_path)
    path = tmp_path / "replay-acquired-evidence-import-proof.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["localization_package_fingerprint"] = "drifted"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SV1BPreflightError, match="graph_derivation_checkpoint_failed"):
        validate_graph_derivation_checkpoint(tmp_path)


@pytest.mark.parametrize(
    ("filename", "field"),
    [
        ("localization-closure-proof.json", "localization_accounting_closed"),
        ("localization-closure-proof.json", "downstream_progression_allowed"),
        ("replay-acquired-evidence-import-proof.json", "primary_replay_nonderived_logical_fingerprint_equal"),
        ("r2r-exact-remap-audit.json", "target_completion_ready"),
    ],
)
def test_graph_derivation_checkpoint_fails_closed_on_reusable_state_drift(
    tmp_path: Path,
    filename: str,
    field: str,
) -> None:
    _write_graph_checkpoint_fixture(tmp_path)
    path = tmp_path / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    value[field] = False
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SV1BPreflightError, match="graph_derivation_checkpoint_failed"):
        validate_graph_derivation_checkpoint(tmp_path)


def test_search_runtime_membership_classifies_returned_lifecycle_rows_and_and_leakage() -> None:
    result = classify_search_runtime_membership(
        [
            {1: {"direct"}, 2: {"direct"}, 4: {"direct"}, 5: {"direct"}, 6: {"direct"}},
            {2: {"direct"}, 3: {"direct"}},
        ],
        [{"term-a"}, {"term-b"}],
        {2, 4, 5, 6},
        rejected_index={"term-b": {4}},
        superseded_index={"term-b": {5}},
        invalid_index={"term-b": {6}},
    )
    assert result["expected"] == {2}
    assert result["supported"] == {2}
    assert result["unsupported"] == {4, 5, 6}
    assert result["rejected_only"] == {4}
    assert result["superseded_only"] == {5}
    assert result["invalid_only"] == {6}
    assert result["lifecycle_violations"] == {4, 5, 6}
    assert result["and_leakage"] == {4, 5, 6}


def test_search_runtime_membership_derives_missing_supported_rows() -> None:
    result = classify_search_runtime_membership(
        [{1: {"direct"}, 2: {"direct"}}, {2: {"direct"}, 3: {"direct"}}],
        [{"term-a"}, {"term-b"}],
        set(),
        rejected_index={},
        superseded_index={},
        invalid_index={},
    )
    assert result["expected"] == {2}
    assert result["missing"] == {2}
    assert result["unsupported"] == set()


def test_output_root_must_be_new_and_private(tmp_path: Path) -> None:
    with pytest.raises(SV1BPreflightError, match="private_output_root_escape"):
        validate_output_root(tmp_path / "outside")


def test_writable_database_validation_rejects_overlap_before_exists_check(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.database_exists",
        lambda _database: False,
    )
    with pytest.raises(SV1BPreflightError, match="overlaps_accepted"):
        validate_writable_databases(
            "blombooru_scv2_sv1_controlled_scale_test_20260718",
            "blombooru_new_replay_test",
        )


def test_writable_database_validation_rejects_preexisting_unowned_database(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure.database_exists",
        lambda database: database.endswith("primary_test"),
    )
    with pytest.raises(SV1BPreflightError, match="ownership_unproven"):
        validate_writable_databases("blombooru_sv1b_primary_test", "blombooru_sv1b_replay_test")


def test_owned_output_requires_exact_phase_and_database_ownership(tmp_path: Path) -> None:
    output = tmp_path / ".local_manifests" / "run"
    output.mkdir(parents=True)
    with pytest.raises(SV1BPreflightError, match="private_output_root_invalid|proof_missing"):
        validate_owned_output_root(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )


def test_public_console_summary_is_aggregate_allowlist() -> None:
    result = {
        "phase": "SCV2-SV1B",
        "status": "blocked_sv1b_provider_authentication",
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "manual_acceptance_status": "not_generated_provider_gate_blocked",
        "candidate_manifest": {
            "canonical_candidate_media_count": 6496,
            "page_media_manifest_row_count": 7757,
            "distinct_work_count": 7028,
            "private_media_id": "must-not-escape",
        },
        "provider_hardening": {"provider_request_count": 0, "provider_attempt_count": 0},
        "environment_isolation": {"passed": True, "absolute_path": "must-not-escape"},
        "accepted_nonderived_evidence": {
            "primary_import": {"inserted_total": 36342, "raw_rows": ["must-not-escape"]},
            "replay_import": {"inserted_total": 36342},
            "primary_reconciliation_passed": True,
            "replay_reconciliation_passed": True,
        },
    }
    public = public_console_summary(result)
    serialized = json.dumps(public, sort_keys=True)
    assert "must-not-escape" not in serialized
    assert public["private_values_exposed"] is False


@pytest.mark.parametrize(
    ("accepted", "expected"),
    [
        ({"a": "must_link"}, {"a": "comparable"}),
        ({"a": "must_link", "b": "must_link"}, {"a": "ambiguous_remap", "b": "ambiguous_remap"}),
        ({"a": "must_link", "b": "cannot_link"}, {"a": "conflicting_remap", "b": "conflicting_remap"}),
    ],
)
def test_r2r_target_collision_classification_is_fail_closed(accepted, expected) -> None:
    preliminary = {pair_id: {"classification": "proposed_comparable"} for pair_id in accepted}
    finalize_r2r_proposal_classifications(
        preliminary,
        {"target": list(accepted)},
        accepted,
    )
    assert {pair_id: row["classification"] for pair_id, row in preliminary.items()} == expected
