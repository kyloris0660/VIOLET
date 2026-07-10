from __future__ import annotations

from pathlib import Path

from scripts import run_phase45_scv2_a1r_route_audit_after_r1r as a1r


def test_r1r_evidence_intake_accepts_committed_r1r_report() -> None:
    result = a1r.verify_r1r_evidence(a1r.R1R_SUMMARY, a1r.R1R_REPORT)

    assert result["passed"] is True
    assert result["status"] == "target_met_full_chain"
    assert result["llm_accounting"] == {
        "eligible": 6429,
        "selected": 6429,
        "judged": 6429,
        "all_eligible_pairs_adjudicated": True,
    }
    assert result["cache_accounting"]["exact_compatible_cache_hit_count"] == 6429
    assert result["cache_accounting"]["new_provider_call_count"] == 0
    assert result["decisions"] == {"same": 2072, "cannot": 3815, "uncertain": 542}


def test_r1r_evidence_intake_blocks_missing_report(tmp_path: Path) -> None:
    missing_report = tmp_path / "missing-r1r-report.md"

    result = a1r.verify_r1r_evidence(a1r.R1R_SUMMARY, missing_report)

    assert result["passed"] is False
    assert result["status_if_failed"] == "blocked_invalid_r1r_evidence"
    failed_checks = {item["check"] for item in result["checks"] if not item["passed"]}
    assert "r1r_report_file_exists" in failed_checks


def test_route_matrix_recommends_r2_when_resolver_gaps_dominate() -> None:
    route = a1r.build_route_matrix(
        {"total_gap_signals": 4443, "gap_buckets": {"source_tag_present_no_source_concept_alias": 947}},
        {"aggregate": {"asymmetric_groups": 10, "unmatched_seeds": 16}},
        {"total_needs_review_concepts": 1703, "needs_review_high_evidence_count": 94},
        {"source_metadata_distinct_eligible_media_pct": 14.4},
        {"decision_counts": {"cannot": 3815, "uncertain": 542, "total": 6429}},
    )

    assert route["final_route_decision_status"] == "route_partially_approved_for_one_next_phase"
    assert route["recommended_next_phase"] == "SCV2-R2 targeted resolver / gap reduction"
    assert route["required_operator_approval_for_next_phase"] is True
    assert sum(1 for option in route["options"] if option["recommended"]) == 1
    assert all(option["a1r_itself_starts_it"] is False for option in route["options"])


def test_route_authorization_keeps_downstream_and_truth_flags_false() -> None:
    route = {
        "recommended_next_phase": "SCV2-R2 targeted resolver / gap reduction",
        "required_contract_for_next_phase": "focused SCV2-R2 resolver/gap contract",
        "required_operator_approval_for_next_phase": True,
        "authorized_now": {
            "single_next_phase": "SCV2-R2 targeted resolver / gap reduction",
            "broad_downstream_work": False,
            "production_or_truth_work": False,
        },
        "still_blocked_routes": ["Entity bridge preview"],
    }

    authorization = a1r.route_authorization(route)

    for key in a1r.FORBIDDEN_FALSE_FLAGS:
        assert authorization[key] is False
