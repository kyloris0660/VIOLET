"""Repository-owned synthetic/offline SCV2-PX1 vertical slice."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from typing import Any, Mapping, Sequence
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..database import Base
from ..models import (
    SourceMetadataEvidence,
    SourceMetadataRecord,
    SourceNameObservation,
    SourceTagObservation,
)
from .pixiv_filename_prior_service import PARSER_VERSION
from .pixiv_metadata_ingestion_service import (
    DEFERRED_PAGE_MISMATCH_REASON,
    PIXIV_METADATA_NORMALIZER_VERSION,
    PixivMetadataGateError,
    PixivMetadataState,
    defer_proven_source_page_mismatch,
    mark_work_state,
    parse_gallery_dl_stdout,
    persist_page_local_work_disposition,
    queue_media_for_pixiv_metadata,
)
from .pixiv_metadata_projection_service import (
    PIXIV_PUBLIC_SUMMARY_SCHEMA,
    assert_public_safe_projection,
    build_canonical_pixiv_aggregates_from_session,
    canonical_fingerprint,
    canonical_json_bytes,
    project_pixiv_aggregate_to_source_concept_signals,
    summarize_pixiv_aggregate_dispositions,
)


SYNTHETIC_FIXTURE_SCHEMA = "violet.scv2-px1-synthetic-pixiv-fixture.v1"
VERTICAL_SLICE_RECEIPT_SCHEMA = "violet.scv2-px1-offline-operation-receipt.v2"
PX1_CONTRACT_ID = "scv2_px1_pixiv_metadata_consolidation_contract_v1"
PX1_AUTHORITY_MAP = {
    "px1_implementation_authorized": True,
    "repository_read_authorized": True,
    "synthetic_fixture_execution_authorized": True,
    "task_owned_temporary_database_authorized": True,
    "isolated_worktree_authorized": True,
    "branch_commit_push_authorized": True,
    "one_normal_pull_request_authorized": True,
    "documentation_state_transition_authorized": True,
    "merge_authorized": False,
    "real_source_inventory_authorized": False,
    "source_root_or_icloud_access_authorized": False,
    "existing_database_access_authorized": False,
    "app_storage_write_authorized": False,
    "real_pixiv_or_gallery_dl_network_execution_authorized": False,
    "provider_credentials_authorized": False,
    "media_or_thumbnail_download_authorized": False,
    "import_authorized": False,
    "classification_or_tagging_execution_on_user_data_authorized": False,
    "llm_or_external_model_authorized": False,
    "server_browser_or_e2e_authorized": False,
    "production_authorized": False,
    "full_library_import_authorized": False,
}
PX1_EXECUTED_STAGES = (
    "synthetic_fixture_creation",
    "canonical_pixiv_normalization",
    "source_metadata_persistence",
    "canonical_work_page_aggregate",
    "source_concept_signal_projection",
    "deterministic_replay",
    "public_safe_summary",
)


def repository_synthetic_pixiv_fixture() -> dict[str, Any]:
    """Return obviously fictional data that has no historical evidence origin."""

    return {
        "schema_version": SYNTHETIC_FIXTURE_SCHEMA,
        "fixture_origin": "repository_owned_new_synthetic_only",
        "cases": [
            {
                "case_id": "single_page_duplicate_payload_and_redaction",
                "kind": "complete",
                "media_id": 101,
                "work_id": "700000001",
                "page_index": 0,
                "payload": [
                    {
                        "provider": "pixiv",
                        "id": 700000001,
                        "num": 0,
                        "page_count": 1,
                        "title": "Synthetic Aurora Study",
                        "user": {
                            "id": 800000001,
                            "name": "Synthetic Creator Alpha",
                            "account": "synthetic_alpha",
                        },
                        "tags": [
                            "synthetic_blue",
                            "Synthetic Hero (Synthetic Work)",
                            "synthetic_blue",
                            "Authorization: Bearer synthetic-secret-sentinel",
                        ],
                        "local_path": "C:\\Private\\synthetic-never-published.png",
                        "cookie": "synthetic-cookie-never-published",
                    },
                    {
                        "provider": "pixiv",
                        "id": 700000001,
                        "num": 0,
                        "page_count": 1,
                        "title": "Synthetic Aurora Study",
                        "user": {
                            "id": 800000001,
                            "name": "Synthetic Creator Alpha",
                            "account": "synthetic_alpha",
                        },
                        "tags": [
                            "Synthetic Hero (Synthetic Work)",
                            "synthetic_blue",
                            "Authorization: Bearer synthetic-secret-sentinel",
                        ],
                        "local_path": "C:\\Private\\synthetic-never-published.png",
                        "cookie": "synthetic-cookie-never-published",
                    },
                ],
            },
            {
                "case_id": "multipage_creator_observation_v1",
                "kind": "complete",
                "media_id": 102,
                "work_id": "700000002",
                "page_index": 0,
                "payload": [{
                    "category": "pixiv",
                    "id": 700000002,
                    "num": 0,
                    "page_count": 2,
                    "title": "Synthetic Two Page Work",
                    "user": {
                        "id": 800000001,
                        "name": "Synthetic Creator Alpha",
                        "account": "synthetic_alpha",
                    },
                    "tags": ["synthetic_pair", "Synthetic Hero (Synthetic Work)"],
                }],
            },
            {
                "case_id": "multipage_creator_observation_v2_sparse",
                "kind": "complete",
                "media_id": 103,
                "work_id": "700000002",
                "page_index": 1,
                "payload": [{
                    "extractor": "pixiv",
                    "id": 700000002,
                    "num": 1,
                    "page_count": 2,
                    "title": None,
                    "user": {
                        "id": 800000001,
                        "name": "Synthetic Creator Alpha Renamed",
                        "account": "synthetic_alpha_new",
                    },
                    "tags": [],
                }],
            },
            {
                "case_id": "retryable_lifecycle",
                "kind": "state",
                "media_id": 104,
                "work_id": "700000003",
                "page_index": 0,
                "state": PixivMetadataState.RETRYABLE.value,
                "reason": "synthetic_retryable_transport",
            },
            {
                "case_id": "terminal_lifecycle",
                "kind": "state",
                "media_id": 105,
                "work_id": "700000004",
                "page_index": 0,
                "state": PixivMetadataState.TERMINAL.value,
                "reason": "synthetic_terminal_unavailable",
            },
            {
                "case_id": "page_mismatch",
                "kind": "page_mismatch",
                "media_id": 106,
                "work_id": "700000005",
                "page_index": 1,
                "payload": [{
                    "provider": "pixiv",
                    "id": 700000005,
                    "num": 0,
                    "page_count": 2,
                    "title": "Synthetic Mismatch Returned Page",
                    "user": {"id": 800000005, "name": "Synthetic Mismatch Creator"},
                    "tags": ["synthetic_mismatch"],
                }],
            },
            {
                "case_id": "creator_conflict_a",
                "kind": "complete",
                "media_id": 107,
                "work_id": "700000006",
                "page_index": 0,
                "payload": [{
                    "provider": "pixiv",
                    "id": 700000006,
                    "num": 0,
                    "title": "Synthetic Creator Conflict",
                    "user": {"id": 800000006, "name": "Synthetic Conflicted Name"},
                    "tags": ["synthetic_conflict"],
                }],
            },
            {
                "case_id": "creator_conflict_b",
                "kind": "complete",
                "media_id": 108,
                "work_id": "700000006",
                "page_index": 0,
                "payload": [{
                    "provider": "pixiv",
                    "id": 700000006,
                    "num": 0,
                    "title": "Synthetic Creator Conflict",
                    "user": {"id": 800000099, "name": "Synthetic Conflicted Name"},
                    "tags": ["synthetic_conflict"],
                }],
            },
            {
                "case_id": "malformed_json",
                "kind": "rejected_payload",
                "media_id": 109,
                "work_id": "700000007",
                "page_index": 0,
                "stdout": "{synthetic malformed json",
                "expected_reason": "metadata_normalization_failed_malformed_json",
            },
            {
                "case_id": "unknown_provider",
                "kind": "rejected_payload",
                "media_id": 110,
                "work_id": "700000008",
                "page_index": 0,
                "payload": [{
                    "provider": "synthetic_unknown_provider",
                    "id": 700000008,
                    "num": 0,
                    "title": "Synthetic Unknown Provider",
                }],
                "expected_reason": "metadata_normalization_failed_unknown_provider",
            },
        ],
    }


def _require_task_owned_temp_workspace(workspace: Path) -> Path:
    resolved = workspace.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(temp_root)
    except ValueError as exc:
        raise PixivMetadataGateError("px1_workspace_not_task_owned_temporary") from exc
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _require_task_owned_runtime_storage_environment() -> Path:
    raw = os.environ.get("VIOLET_STORAGE_ROOT", "").strip()
    if (
        not raw
        or os.environ.get("VIOLET_SKIP_DOTENV", "").strip() != "1"
        or os.environ.get("VIOLET_ENV", "").strip().casefold() != "test"
    ):
        raise PixivMetadataGateError("px1_task_runtime_storage_environment_required")
    storage = Path(raw).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        storage.relative_to(temp_root)
    except ValueError as exc:
        raise PixivMetadataGateError("px1_task_runtime_storage_not_temporary") from exc
    if not storage.is_dir():
        raise PixivMetadataGateError("px1_task_runtime_storage_unavailable")
    return storage


class _OfflineOperationGuard:
    def __init__(self) -> None:
        self.provider_network_attempt_count = 0
        self.subprocess_attempt_count = 0
        self._stack = ExitStack()

    def _blocked_network(self, *_args: Any, **_kwargs: Any) -> None:
        self.provider_network_attempt_count += 1
        raise PixivMetadataGateError("px1_offline_guard_blocked_network")

    def _blocked_subprocess(self, *_args: Any, **_kwargs: Any) -> None:
        self.subprocess_attempt_count += 1
        raise PixivMetadataGateError("px1_offline_guard_blocked_subprocess")

    def __enter__(self) -> "_OfflineOperationGuard":
        self._stack.enter_context(patch.object(socket, "create_connection", self._blocked_network))
        self._stack.enter_context(patch.object(socket.socket, "connect", self._blocked_network))
        self._stack.enter_context(patch.object(subprocess, "Popen", self._blocked_subprocess))
        self._stack.enter_context(patch.object(subprocess, "run", self._blocked_subprocess))
        return self

    def __exit__(self, *args: Any) -> None:
        self._stack.close()


def _queue_case(session: Session, case: Mapping[str, Any]) -> SourceMetadataRecord:
    queue_media_for_pixiv_metadata(
        session,
        {
            "id": int(case["media_id"]),
            "filename": (
                f"synthetic_{case['work_id']}_p{int(case['page_index'])}.png"
            ),
            "path": (
                f"synthetic-fixture/{case['work_id']}_p{int(case['page_index'])}.png"
            ),
        },
    )
    session.flush()
    record = (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.media_id == int(case["media_id"]),
            SourceMetadataRecord.source_work_id == str(case["work_id"]),
            SourceMetadataRecord.source_page_index == int(case["page_index"]),
        )
        .one_or_none()
    )
    if record is None:
        raise PixivMetadataGateError("px1_synthetic_queue_record_missing")
    return record


def _payload_stdout(case: Mapping[str, Any]) -> str:
    if case.get("stdout") is not None:
        return str(case["stdout"])
    return json.dumps(case.get("payload") or [], ensure_ascii=False)


def _apply_fixture_case(
    session: Session,
    case: Mapping[str, Any],
    *,
    record: SourceMetadataRecord,
    rejected_cases: list[dict[str, str]],
) -> None:
    kind = str(case["kind"])
    work_id = str(case["work_id"])
    if kind == "complete":
        pages = parse_gallery_dl_stdout(_payload_stdout(case), work_id)
        result = persist_page_local_work_disposition(
            session,
            work_id,
            pages,
            attempted_record_ids=[int(record.id)],
        )
        if result.missing_record_ids:
            raise PixivMetadataGateError("px1_complete_case_page_missing")
        return
    if kind == "state":
        mark_work_state(
            session,
            work_id,
            str(case["state"]),
            reason=str(case["reason"]),
            attempted_record_ids=[int(record.id)],
        )
        return
    if kind == "page_mismatch":
        pages = parse_gallery_dl_stdout(_payload_stdout(case), work_id)
        result = persist_page_local_work_disposition(
            session,
            work_id,
            pages,
            attempted_record_ids=[int(record.id)],
        )
        if not result.missing_record_ids:
            raise PixivMetadataGateError("px1_page_mismatch_not_reproduced")
        mark_work_state(
            session,
            work_id,
            PixivMetadataState.NORMALIZATION_FAILED.value,
            reason=DEFERRED_PAGE_MISMATCH_REASON,
            attempted_record_ids=[int(record.id)],
            structural_diagnostics={
                "failure_code": DEFERRED_PAGE_MISMATCH_REASON,
                "provider_output_returned": True,
                "work_id": work_id,
                "parser_version": PARSER_VERSION,
                "normalizer_version": "gallery_dl_pixiv_normalizer_v1",
            },
        )
        defer_proven_source_page_mismatch(
            session,
            work_id,
            attempted_record_ids=[int(record.id)],
            observed_page_indexes=[int(page["page_index"]) for page in pages],
            original_final_outcome="normalization_failed",
            manifest_kind="main",
            evidence_fingerprint=canonical_fingerprint(
                {
                    "case_id": case["case_id"],
                    "work_id": work_id,
                    "observed_pages": [int(page["page_index"]) for page in pages],
                }
            ),
            deferred_at="2000-01-01T00:00:00+00:00",
            governed_route_exhausted=True,
        )
        return
    if kind == "rejected_payload":
        try:
            parse_gallery_dl_stdout(_payload_stdout(case), work_id)
        except PixivMetadataGateError as exc:
            reason = str(exc).split(":", 1)[0]
        else:
            raise PixivMetadataGateError("px1_rejected_payload_was_accepted")
        if reason != str(case["expected_reason"]):
            raise PixivMetadataGateError("px1_rejected_payload_reason_mismatch")
        mark_work_state(
            session,
            work_id,
            PixivMetadataState.NORMALIZATION_FAILED.value,
            reason=reason,
            attempted_record_ids=[int(record.id)],
        )
        rejected_cases.append({"case_id": str(case["case_id"]), "reason": reason})
        return
    raise PixivMetadataGateError("px1_synthetic_case_kind_unknown")


def _run_fixture_once(
    fixture: Mapping[str, Any],
    *,
    database_path: Path,
    reverse_input_order: bool,
) -> dict[str, Any]:
    if database_path.exists():
        raise PixivMetadataGateError("px1_temporary_database_already_exists")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            SourceMetadataRecord.__table__,
            SourceMetadataEvidence.__table__,
            SourceNameObservation.__table__,
            SourceTagObservation.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    rejected_cases: list[dict[str, str]] = []
    try:
        cases = list(fixture["cases"])
        if reverse_input_order:
            cases.reverse()
        records_by_case = {
            str(case["case_id"]): _queue_case(session, case) for case in cases
        }
        session.flush()
        for case in cases:
            _apply_fixture_case(
                session,
                case,
                record=records_by_case[str(case["case_id"])],
                rejected_cases=rejected_cases,
            )
        session.commit()
        aggregates = list(build_canonical_pixiv_aggregates_from_session(session))
        signals = [
            project_pixiv_aggregate_to_source_concept_signals(aggregate)
            for aggregate in aggregates
        ]
        database_counts = {
            "source_metadata_records": session.query(SourceMetadataRecord).count(),
            "source_name_observations": session.query(SourceNameObservation).count(),
            "source_tag_observations": session.query(SourceTagObservation).count(),
            "source_metadata_evidence": session.query(SourceMetadataEvidence).count(),
        }
        first_projection_fingerprint = canonical_fingerprint(
            {"aggregates": aggregates, "signal_bundles": signals}
        )
        replay_aggregates = list(build_canonical_pixiv_aggregates_from_session(session))
        replay_signals = [
            project_pixiv_aggregate_to_source_concept_signals(aggregate)
            for aggregate in replay_aggregates
        ]
        replay_projection_fingerprint = canonical_fingerprint(
            {"aggregates": replay_aggregates, "signal_bundles": replay_signals}
        )
        return {
            "aggregates": aggregates,
            "signal_bundles": signals,
            "database_counts": database_counts,
            "disposition_counts": summarize_pixiv_aggregate_dispositions(aggregates),
            "rejected_cases": sorted(rejected_cases, key=lambda item: item["case_id"]),
            "projection_fingerprint": first_projection_fingerprint,
            "replay_projection_fingerprint": replay_projection_fingerprint,
            "replay_stable": first_projection_fingerprint
            == replay_projection_fingerprint,
        }
    finally:
        session.close()
        engine.dispose()


def run_synthetic_pixiv_vertical_slice(
    *,
    workspace: Path,
    fixture: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run two isolated temp databases and return stable public-safe evidence."""

    _require_task_owned_runtime_storage_environment()
    task_workspace = _require_task_owned_temp_workspace(workspace)
    selected_fixture = dict(fixture or repository_synthetic_pixiv_fixture())
    if selected_fixture.get("schema_version") != SYNTHETIC_FIXTURE_SCHEMA:
        raise PixivMetadataGateError("px1_synthetic_fixture_schema_invalid")
    if selected_fixture.get("fixture_origin") != "repository_owned_new_synthetic_only":
        raise PixivMetadataGateError("px1_synthetic_fixture_origin_invalid")

    with _OfflineOperationGuard() as guard:
        first = _run_fixture_once(
            selected_fixture,
            database_path=task_workspace / "px1-first.sqlite3",
            reverse_input_order=False,
        )
        reversed_run = _run_fixture_once(
            selected_fixture,
            database_path=task_workspace / "px1-reversed.sqlite3",
            reverse_input_order=True,
        )

    input_order_stable = (
        first["projection_fingerprint"] == reversed_run["projection_fingerprint"]
    )
    receipt = {
        "schema_version": VERTICAL_SLICE_RECEIPT_SCHEMA,
        "fixture_source": "repository_owned_new_synthetic_only",
        "temporary_workspace_enforced": True,
        "task_owned_temporary_database_count": 2,
        "existing_database_read_count": 0,
        "existing_database_write_count": 0,
        "existing_app_storage_access_count": 0,
        "task_owned_temporary_runtime_storage_root_count": 1,
        "provider_network_activity_count": guard.provider_network_attempt_count,
        "media_network_activity_count": 0,
        "subprocess_activity_count": guard.subprocess_attempt_count,
        "credential_access_count": 0,
        "source_root_access_count": 0,
        "entity_truth_write_count": 0,
        "source_concept_materialization_count": 0,
        "media_tag_write_count": 0,
        "real_provider_authorized": False,
        "real_source_authorized": False,
        "production_authorized": False,
    }
    summary: dict[str, Any] = {
        "schema_version": PIXIV_PUBLIC_SUMMARY_SCHEMA,
        "contract_id": PX1_CONTRACT_ID,
        "status": "implementation_ready_for_owner_audit",
        "executed_stages": list(PX1_EXECUTED_STAGES),
        "fixture_fingerprint": canonical_fingerprint(selected_fixture),
        "normalizer_version": PIXIV_METADATA_NORMALIZER_VERSION,
        "aggregates": first["aggregates"],
        "signal_bundles": first["signal_bundles"],
        "database_counts": first["database_counts"],
        "disposition_counts": first["disposition_counts"],
        "rejected_cases": first["rejected_cases"],
        "canonical_projection_fingerprint": first["projection_fingerprint"],
        "replay_projection_fingerprint": first["replay_projection_fingerprint"],
        "reversed_input_projection_fingerprint": reversed_run[
            "projection_fingerprint"
        ],
        "deterministic_replay": first["replay_stable"],
        "input_order_stable": input_order_stable,
        "operation_receipt": receipt,
        "synthetic_vertical_slice_verified": bool(
            first["replay_stable"]
            and input_order_stable
            and guard.provider_network_attempt_count == 0
            and guard.subprocess_attempt_count == 0
        ),
        "cluster_materialization_performed": False,
        "entity_truth_promoted": False,
        "authorities": dict(PX1_AUTHORITY_MAP),
        "px1_implementation_completed": True,
        "px1_target_met": True,
        "target_met": True,
        "owner_accepted": False,
        "safe_to_merge": False,
        "route_approved": False,
        "merge_authorized": False,
        "px2_started": False,
        "real_provider_authorized": False,
        "real_source_authorized": False,
        "full_import_authorized": False,
        "production_authorized": False,
    }
    summary["canonical_fingerprint"] = canonical_fingerprint(summary)
    assert_public_safe_projection(summary)
    return summary


def write_synthetic_vertical_slice_evidence(
    evidence_dir: Path,
    *,
    fixture: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, str]:
    """Write fixed-name evidence beneath the already-confined temp workspace."""

    root = _require_task_owned_temp_workspace(evidence_dir)
    artifacts: dict[str, Any] = {
        "synthetic-fixture.json": fixture,
        "aggregates.json": summary["aggregates"],
        "signal-bundles.json": summary["signal_bundles"],
        "operation-receipt.json": summary["operation_receipt"],
        "public-summary.json": summary,
    }
    fingerprints: dict[str, str] = {}
    for name, payload in artifacts.items():
        encoded = canonical_json_bytes(payload) + b"\n"
        target = root / name
        if target.exists():
            raise PixivMetadataGateError("px1_evidence_artifact_already_exists")
        target.write_bytes(encoded)
        fingerprints[name] = canonical_fingerprint(payload)
    return dict(sorted(fingerprints.items()))
