"""Apply the project-lead-approved ML1 source-page mismatch disposition.

Lifecycle: phase-scoped operational runner. It performs no network/provider
operation and is valid only for the named isolated ML1 acquisition database.
Exact membership comes from the preserved private manifests, checkpoints, and
final outcome ledger; public counts are never used to reconstruct membership.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
for candidate in (ROOT, BACKEND_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.models import SourceMetadataEvidence, SourceMetadataRecord, SourceNameObservation  # noqa: E402
from app.services.pixiv_metadata_ingestion_service import (  # noqa: E402
    DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
    DEFERRED_PAGE_MISMATCH_REASON,
    PAGE_OBSERVED_COMPLETION_EVIDENCE_KIND,
    QUEUE_METADATA_KIND,
    PixivMetadataGateError,
    PixivMetadataState,
    defer_proven_source_page_mismatch,
    supersede_untrusted_pixiv_creator_observations,
)
from app.utils.cache import invalidate_source_metadata_search_cache  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402
from scripts.run_pixiv_metadata_ingestion import executable_manifest_fingerprint  # noqa: E402


ACQUISITION_DB = "blombooru_scv2_ml1_acquisition_test_20260712"
ACQUISITION_OUTPUT_DIR = (
    ROOT / ".local_manifests/phase-4.5-scv2-ml1-pixiv-metadata-ingestion"
)
GOVERNANCE_SUMMARY = ACQUISITION_OUTPUT_DIR / "source-page-mismatch-governance-summary.json"
GOVERNANCE_LEDGER = ACQUISITION_OUTPUT_DIR / "source-page-mismatch-governance-ledger.json"
EXPECTED_MAIN_COUNT = 11
EXPECTED_CONFLICT_COUNT = 3
EXPECTED_TOTAL_COUNT = 14
OBSERVED_PROVIDER_PAGE_INDEXES = (0,)

INPUT_FILES = (
    "exact-distinct-work-manifest.json",
    "exact-conflict-resolution-manifest.json",
    "acquisition-checkpoint.json",
    "normalization-replay-checkpoint.json",
    "final-work-outcome-ledger.json",
    "execution-summary.json",
)


@dataclass(frozen=True)
class GovernedWork:
    work_id: str
    manifest_kind: str
    original_final_outcome: str
    attempted_record_ids: tuple[int, ...]
    requested_page_indexes: tuple[int, ...]
    observed_page_indexes: tuple[int, ...]
    evidence_fingerprint: str


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise PixivMetadataGateError(f"governance_private_evidence_missing:{path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_private_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _ledger_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise PixivMetadataGateError("governance_final_outcome_ledger_invalid")
    return [dict(row) for row in value]


def _checkpoint_outcomes(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not isinstance(value.get("final_outcomes"), Mapping):
        raise PixivMetadataGateError("governance_replay_checkpoint_invalid")
    return {str(key): dict(row) for key, row in value["final_outcomes"].items()}


def _input_evidence(output_dir: Path) -> dict[str, Any]:
    paths = {name: output_dir / name for name in INPUT_FILES}
    payloads = {name: _read_json(path) for name, path in paths.items()}
    main_manifest = payloads["exact-distinct-work-manifest.json"]
    conflict_manifest = payloads["exact-conflict-resolution-manifest.json"]
    acquisition_checkpoint = payloads["acquisition-checkpoint.json"]
    replay_checkpoint = payloads["normalization-replay-checkpoint.json"]
    execution_summary = payloads["execution-summary.json"]
    ledger_rows = _ledger_rows(payloads["final-work-outcome-ledger.json"])

    main_fingerprint = executable_manifest_fingerprint(main_manifest)
    conflict_fingerprint = executable_manifest_fingerprint(conflict_manifest)
    acquisition = dict(execution_summary.get("acquisition_execution") or {})
    expected_ledger_fingerprint = _canonical_sha256(ledger_rows)
    fingerprint_chain = (
        main_fingerprint
        == acquisition.get("acquisition_manifest_fingerprint")
        == acquisition.get("checkpoint_main_manifest_fingerprint")
        == acquisition_checkpoint.get("main_manifest_fingerprint")
        == replay_checkpoint.get("main_manifest_fingerprint")
        and conflict_fingerprint
        == acquisition.get("conflict_resolution_manifest_fingerprint")
        == acquisition.get("checkpoint_conflict_manifest_fingerprint")
        == acquisition_checkpoint.get("conflict_manifest_fingerprint")
        == replay_checkpoint.get("conflict_manifest_fingerprint")
        and expected_ledger_fingerprint == acquisition.get("final_outcome_ledger_fingerprint")
    )
    if not fingerprint_chain:
        raise PixivMetadataGateError("governance_manifest_checkpoint_ledger_fingerprint_mismatch")
    if len(main_manifest.get("work_ids") or ()) != 1713 or len(conflict_manifest.get("work_ids") or ()) != 3:
        raise PixivMetadataGateError("governance_executable_manifest_count_mismatch")
    required_execution = {
        "unique_work_ids_attempted_count": 1716,
        "provider_request_attempt_count": 1817,
        "gallery_dl_call_count": 1817,
        "systemic_stop": False,
        "provider_identity_mismatch_work_count": 0,
        "out_of_manifest_work_attempt_count": 0,
        "complete_work_reacquisition_count": 0,
    }
    for key, expected in required_execution.items():
        if acquisition.get(key) != expected:
            raise PixivMetadataGateError(f"governance_accepted_execution_mismatch:{key}")
    replay_outcomes = _checkpoint_outcomes(replay_checkpoint)
    if len(ledger_rows) != 1716 or set(replay_outcomes) != {
        str(row.get("work_id")) for row in ledger_rows
    }:
        raise PixivMetadataGateError("governance_outcome_membership_mismatch")
    prior_governance_ledger = _read_json(GOVERNANCE_LEDGER) if GOVERNANCE_LEDGER.is_file() else []
    if prior_governance_ledger and (
        not isinstance(prior_governance_ledger, list)
        or len(prior_governance_ledger) != EXPECTED_TOTAL_COUNT
    ):
        raise PixivMetadataGateError("governance_prior_ledger_invalid")
    governed_record_ids_by_work = {
        str(row["work_id"]): tuple(int(value) for value in row["attempted_queue_record_ids"])
        for row in prior_governance_ledger
    }
    return {
        "paths": paths,
        "file_sha256": {name: _file_sha256(path) for name, path in paths.items()},
        "main_manifest": main_manifest,
        "conflict_manifest": conflict_manifest,
        "main_manifest_fingerprint": main_fingerprint,
        "conflict_manifest_fingerprint": conflict_fingerprint,
        "ledger_rows": ledger_rows,
        "replay_outcomes": replay_outcomes,
        "execution_summary": execution_summary,
        "original_ledger_fingerprint": expected_ledger_fingerprint,
        "governed_record_ids_by_work": governed_record_ids_by_work,
    }


def select_governed_works(
    *,
    main_work_ids: Sequence[str],
    conflict_work_ids: Sequence[str],
    ledger_rows: Sequence[Mapping[str, Any]],
    replay_outcomes: Mapping[str, Mapping[str, Any]],
    queue_rows: Sequence[Mapping[str, Any]],
    governed_record_ids_by_work: Mapping[str, Sequence[int]] | None = None,
) -> tuple[GovernedWork, ...]:
    """Select only exact manifest-bound, exhausted, same-work page mismatches."""

    main_set = {str(value) for value in main_work_ids}
    conflict_set = {str(value) for value in conflict_work_ids}
    target_ledger = {
        str(row.get("work_id")): dict(row)
        for row in ledger_rows
        if row.get("final_outcome") in {"normalization_failed", "conflict_normalization_failed"}
    }
    rows_by_work: dict[str, list[dict[str, Any]]] = {}
    for row in queue_rows:
        rows_by_work.setdefault(str(row.get("source_work_id")), []).append(dict(row))

    selected: list[GovernedWork] = []
    for work_id, ledger in sorted(target_ledger.items(), key=lambda item: int(item[0])):
        manifest_kind = str(ledger.get("manifest_kind") or "")
        expected_manifest = main_set if manifest_kind == "main" else conflict_set
        expected_outcome = (
            "normalization_failed" if manifest_kind == "main" else "conflict_normalization_failed"
        )
        replay = dict(replay_outcomes.get(work_id) or {})
        records = rows_by_work.get(work_id, [])
        expected_record_ids = {
            int(value)
            for value in (governed_record_ids_by_work or {}).get(work_id, ())
        }
        if expected_record_ids:
            records = [record for record in records if int(record["id"]) in expected_record_ids]
        if (
            manifest_kind not in {"main", "conflict"}
            or work_id not in expected_manifest
            or ledger.get("final_outcome") != expected_outcome
            or ledger.get("error_class") != DEFERRED_PAGE_MISMATCH_REASON
            or replay.get("final_outcome") != expected_outcome
            or replay.get("error_class") != DEFERRED_PAGE_MISMATCH_REASON
            or not records
        ):
            raise PixivMetadataGateError("governance_exact_page_mismatch_membership_invalid")

        eligible_records = [
            record
            for record in records
            if str(record.get("status"))
            in {
                PixivMetadataState.NORMALIZATION_FAILED.value,
                PixivMetadataState.DEFERRED_PAGE_MISMATCH.value,
                PixivMetadataState.COMPLETE.value,
            }
        ]
        if not eligible_records:
            raise PixivMetadataGateError("governance_exact_page_mismatch_queue_rows_missing")
        requested_pages: set[int] = set()
        stable_rows: list[dict[str, Any]] = []
        for record in eligible_records:
            raw = dict(record.get("raw_metadata_json") or {})
            diagnostics = dict(raw.get("structural_diagnostics") or {})
            if (
                diagnostics.get("failure_code") != DEFERRED_PAGE_MISMATCH_REASON
                or diagnostics.get("provider_output_returned") is not True
                or str(diagnostics.get("work_id") or "") != work_id
                or diagnostics.get("normalizer_version") != "gallery_dl_pixiv_normalizer_v1"
            ):
                raise PixivMetadataGateError("governance_exact_page_mismatch_db_proof_invalid")
            page_index = int(record.get("source_page_index") or 0)
            requested_pages.add(page_index)
            stable_rows.append(
                {
                    "record_id": int(record["id"]),
                    "page_index": page_index,
                    "raw_metadata_json": raw,
                    "provenance": dict(record.get("provenance") or {}),
                }
            )
        if requested_pages <= set(OBSERVED_PROVIDER_PAGE_INDEXES):
            raise PixivMetadataGateError("governance_exact_page_mismatch_requested_page_present")
        evidence_fingerprint = _canonical_sha256(
            {
                "policy": DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
                "ledger": ledger,
                "replay": replay,
                "queue_rows": stable_rows,
                "observed_provider_page_indexes": OBSERVED_PROVIDER_PAGE_INDEXES,
            }
        )
        selected.append(
            GovernedWork(
                work_id=work_id,
                manifest_kind=manifest_kind,
                original_final_outcome=expected_outcome,
                attempted_record_ids=tuple(sorted(int(row["id"]) for row in eligible_records)),
                requested_page_indexes=tuple(
                    sorted(int(row.get("source_page_index") or 0) for row in eligible_records)
                ),
                observed_page_indexes=OBSERVED_PROVIDER_PAGE_INDEXES,
                evidence_fingerprint=evidence_fingerprint,
            )
        )

    split = Counter(item.manifest_kind for item in selected)
    if (
        len(selected) != EXPECTED_TOTAL_COUNT
        or split["main"] != EXPECTED_MAIN_COUNT
        or split["conflict"] != EXPECTED_CONFLICT_COUNT
        or len({item.work_id for item in selected}) != EXPECTED_TOTAL_COUNT
    ):
        raise PixivMetadataGateError(
            "governance_exact_selection_count_mismatch:"
            f"main={split['main']},conflict={split['conflict']},total={len(selected)}"
        )
    return tuple(selected)


def _queue_rows(session: Any, work_ids: Sequence[str]) -> list[dict[str, Any]]:
    statement = text(
        "SELECT id,source_work_id,source_page_index,status,raw_metadata_json,provenance "
        "FROM blombooru_source_metadata_records "
        "WHERE provider='pixiv' AND metadata_kind=:kind AND source_work_id IN :work_ids "
        "ORDER BY source_work_id,id"
    ).bindparams(bindparam("work_ids", expanding=True))
    return [
        dict(row)
        for row in session.execute(
            statement,
            {"kind": QUEUE_METADATA_KIND, "work_ids": list(work_ids)},
        ).mappings()
    ]


def _raw_history_fingerprint(queue_rows: Sequence[Mapping[str, Any]]) -> str:
    return _canonical_sha256(
        [
            {
                "id": int(row["id"]),
                "raw_metadata_json": row.get("raw_metadata_json"),
                "provenance": row.get("provenance"),
            }
            for row in queue_rows
        ]
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if str(os.getenv("VIOLET_ENV") or "").casefold() != "test":
        raise PixivMetadataGateError("blocked_environment_isolation:VIOLET_ENV_must_be_test")
    if args.database != ACQUISITION_DB:
        raise PixivMetadataGateError("blocked_environment_isolation:exact_ml1_acquisition_database_required")
    output_dir = args.output_dir.resolve()
    if ROOT not in output_dir.parents or ".local_manifests" not in output_dir.parts:
        raise PixivMetadataGateError("blocked_unsafe_private_output_path")

    evidence = _input_evidence(output_dir)
    main_ids = tuple(str(value) for value in evidence["main_manifest"].get("work_ids") or ())
    conflict_ids = tuple(str(value) for value in evidence["conflict_manifest"].get("work_ids") or ())
    governed_ids = tuple(
        str(row["work_id"])
        for row in evidence["ledger_rows"]
        if row["final_outcome"] in {"normalization_failed", "conflict_normalization_failed"}
    )

    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        identity = str(session.execute(text("SELECT current_database()")) .scalar() or "")
        if identity != args.database:
            raise PixivMetadataGateError("blocked_environment_isolation:database_identity_mismatch")
        before_rows = _queue_rows(session, governed_ids)
        selected = select_governed_works(
            main_work_ids=main_ids,
            conflict_work_ids=conflict_ids,
            ledger_rows=evidence["ledger_rows"],
            replay_outcomes=evidence["replay_outcomes"],
            queue_rows=before_rows,
            governed_record_ids_by_work=evidence["governed_record_ids_by_work"],
        )
        raw_history_before = _raw_history_fingerprint(before_rows)
        preexisting_deferred_at = session.execute(
            text(
                "SELECT provenance->>'deferred_at' FROM blombooru_source_metadata_evidence "
                "WHERE evidence_kind=:kind AND status='active' ORDER BY id LIMIT 1"
            ),
            {"kind": PixivMetadataState.DEFERRED_PAGE_MISMATCH.value},
        ).scalar()
        deferred_at = str(preexisting_deferred_at or datetime.now(timezone.utc).isoformat())

        first_counts = Counter()
        for item in selected:
            first_counts.update(
                defer_proven_source_page_mismatch(
                    session,
                    item.work_id,
                    attempted_record_ids=item.attempted_record_ids,
                    observed_page_indexes=item.observed_page_indexes,
                    original_final_outcome=item.original_final_outcome,
                    manifest_kind=item.manifest_kind,
                    evidence_fingerprint=item.evidence_fingerprint,
                    deferred_at=deferred_at,
                    governed_route_exhausted=True,
                )
            )
        first_lineage_counts = Counter(
            supersede_untrusted_pixiv_creator_observations(session)
        )
        session.commit()
        if first_counts["updated"] or first_lineage_counts["superseded_observation_count"]:
            invalidate_source_metadata_search_cache()

        second_counts = Counter()
        for item in selected:
            second_counts.update(
                defer_proven_source_page_mismatch(
                    session,
                    item.work_id,
                    attempted_record_ids=item.attempted_record_ids,
                    observed_page_indexes=item.observed_page_indexes,
                    original_final_outcome=item.original_final_outcome,
                    manifest_kind=item.manifest_kind,
                    evidence_fingerprint=item.evidence_fingerprint,
                    deferred_at=deferred_at,
                    governed_route_exhausted=True,
                )
            )
        second_lineage_counts = Counter(
            supersede_untrusted_pixiv_creator_observations(session)
        )
        session.commit()

        after_rows = _queue_rows(session, governed_ids)
        raw_history_after = _raw_history_fingerprint(after_rows)
        selected_record_ids = {
            record_id for item in selected for record_id in item.attempted_record_ids
        }
        selected_after_rows = [
            row for row in after_rows if int(row["id"]) in selected_record_ids
        ]
        deferred_record_count = sum(
            str(row.get("status")) == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value
            for row in selected_after_rows
        )
        deferred_evidence_row_count = session.query(SourceMetadataEvidence).filter(
            SourceMetadataEvidence.evidence_kind == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value,
            SourceMetadataEvidence.status == "active",
        ).count()
        page_complete_record_count = sum(
            str(row.get("status")) == PixivMetadataState.COMPLETE.value
            and int(row.get("source_page_index") or 0) in OBSERVED_PROVIDER_PAGE_INDEXES
            for row in selected_after_rows
        )
        page_complete_evidence_row_count = session.query(SourceMetadataEvidence).filter(
            SourceMetadataEvidence.evidence_kind == PAGE_OBSERVED_COMPLETION_EVIDENCE_KIND,
            SourceMetadataEvidence.status == "active",
        ).count()
        lineage_superseded_rows = session.query(SourceNameObservation).filter(
            SourceNameObservation.provider == "pixiv",
            SourceNameObservation.status == "superseded",
        ).all()
        lineage_superseded_rows = [
            row
            for row in lineage_superseded_rows
            if str((row.provenance or {}).get("lineage_disposition") or "")
            == "superseded_untrusted_pixiv_parent_v1"
        ]
        lineage_superseded_field_counts = Counter(
            str(row.source_field) for row in lineage_superseded_rows
        )
    finally:
        session.close()
        engine.dispose()

    after_file_sha256 = {name: _file_sha256(path) for name, path in evidence["paths"].items()}
    if after_file_sha256 != evidence["file_sha256"]:
        raise PixivMetadataGateError("governance_authoritative_private_evidence_mutated")
    expected_record_count = sum(len(item.attempted_record_ids) for item in selected)
    expected_returned_record_count = sum(
        page in set(item.observed_page_indexes)
        for item in selected
        for page in item.requested_page_indexes
    )
    expected_absent_record_count = expected_record_count - expected_returned_record_count
    if (
        deferred_record_count != expected_absent_record_count
        or deferred_evidence_row_count != expected_absent_record_count
        or page_complete_record_count != expected_returned_record_count
        or page_complete_evidence_row_count != expected_returned_record_count
    ):
        raise PixivMetadataGateError("governance_page_local_record_or_evidence_count_mismatch")
    if second_counts["updated"] != 0 or second_counts["evidence_created"] != 0:
        raise PixivMetadataGateError("governance_transition_not_idempotent")
    if second_lineage_counts["superseded_observation_count"] != 0:
        raise PixivMetadataGateError("creator_lineage_transition_not_idempotent")
    if raw_history_before != raw_history_after:
        raise PixivMetadataGateError("governance_raw_or_historical_queue_evidence_changed")

    private_ledger = [
        {
            "work_id": item.work_id,
            "manifest_kind": item.manifest_kind,
            "original_final_outcome": item.original_final_outcome,
            "attempted_queue_record_ids": list(item.attempted_record_ids),
            "requested_local_page_indexes": list(item.requested_page_indexes),
            "provider_observed_page_indexes": list(item.observed_page_indexes),
            "provider_response_evidence_fingerprint": item.evidence_fingerprint,
            "reason_code": DEFERRED_PAGE_MISMATCH_REASON,
            "governance_policy_version": DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
            "deferred_at": deferred_at,
            "unsupported_page_link_created": False,
            "conflict_winner_selected": False,
        }
        for item in selected
    ]
    governance_ledger_fingerprint = _canonical_sha256(private_ledger)
    summary = {
        "database_identity": args.database,
        "policy_version": DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
        "state": PixivMetadataState.DEFERRED_PAGE_MISMATCH.value,
        "selection": {
            "distinct_work_count": len(selected),
            "main_manifest_work_count": sum(item.manifest_kind == "main" for item in selected),
            "conflict_manifest_work_count": sum(item.manifest_kind == "conflict" for item in selected),
            "queue_record_count": expected_record_count,
            "deferred_requested_page_absent_row_count": expected_absent_record_count,
            "deferred_returned_page_row_count_before": expected_returned_record_count,
            "deferred_returned_page_row_count_after": 0,
            "reason_code": DEFERRED_PAGE_MISMATCH_REASON,
            "observed_provider_page_indexes": list(OBSERVED_PROVIDER_PAGE_INDEXES),
            "exact_predicate_passed": True,
            "broader_normalization_or_conflict_population_converted": False,
        },
        "transition": {
            "first_pass_updated_record_count": int(first_counts["updated"]),
            "first_pass_created_evidence_count": int(first_counts["evidence_created"]),
            "first_pass_completed_returned_page_record_count": int(first_counts["completed_returned"]),
            "cumulative_corrected_returned_page_record_count": expected_returned_record_count,
            "first_pass_superseded_deferred_evidence_count": int(first_counts["superseded_deferred_evidence"]),
            "second_pass_updated_record_count": int(second_counts["updated"]),
            "second_pass_created_evidence_count": int(second_counts["evidence_created"]),
            "second_pass_preserved_deferred_record_count": int(second_counts["preserved_deferred"]),
            "idempotent": True,
            "raw_and_historical_queue_evidence_preserved": True,
            "raw_history_fingerprint_before": raw_history_before,
            "raw_history_fingerprint_after": raw_history_after,
            "unsupported_page_link_created": False,
            "conflict_winner_selected": False,
        },
        "creator_lineage_transition": {
            "untrusted_parent_query_visible_creator_observation_count_before": max(
                int(first_lineage_counts["untrusted_parent_query_visible_creator_observation_count"]),
                len(lineage_superseded_rows),
            ),
            "untrusted_parent_query_visible_creator_observation_count_after": int(second_lineage_counts["untrusted_parent_query_visible_creator_observation_count"]),
            "creator_name_count_before": int(lineage_superseded_field_counts["pixiv_user_metadata"]),
            "creator_account_count_before": int(lineage_superseded_field_counts["pixiv_user_account"]),
            "superseded_observation_count": len(lineage_superseded_rows),
            "preserved_out_of_scope_historical_or_manual_static_count": int(first_lineage_counts["preserved_out_of_scope_historical_or_manual_static_count"]),
            "second_pass_superseded_observation_count": int(second_lineage_counts["superseded_observation_count"]),
            "idempotent": True,
        },
        "authoritative_evidence": {
            "main_manifest_fingerprint": evidence["main_manifest_fingerprint"],
            "conflict_manifest_fingerprint": evidence["conflict_manifest_fingerprint"],
            "original_final_outcome_ledger_fingerprint": evidence["original_ledger_fingerprint"],
            "governance_ledger_fingerprint": governance_ledger_fingerprint,
            "input_file_fingerprints_preserved": True,
            "input_file_sha256": evidence["file_sha256"],
        },
        "operation_delta": {
            "gallery_dl_calls": 0,
            "pixiv_provider_calls": 0,
            "provider_metadata_acquisition_calls": 0,
            "diagnostic_provider_calls": 0,
            "llm_calls": 0,
            "media_downloads": 0,
            "media_imports": 0,
        },
        "private_membership_public": False,
    }
    _write_private_json(GOVERNANCE_LEDGER, private_ledger)
    _write_private_json(GOVERNANCE_SUMMARY, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", type=Path, default=ACQUISITION_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    summary = run(build_parser().parse_args(argv))
    print(
        json.dumps(
            {
                "state": summary["state"],
                "distinct_work_count": summary["selection"]["distinct_work_count"],
                "main_manifest_work_count": summary["selection"]["main_manifest_work_count"],
                "conflict_manifest_work_count": summary["selection"]["conflict_manifest_work_count"],
                "external_call_delta": 0,
                "idempotent": summary["transition"]["idempotent"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
