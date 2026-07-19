"""Queue or explicitly execute durable Pixiv metadata-on-import closure.

This is a reusable operational entrypoint, not a daemon.  It operates only on
an explicitly named isolated test/dev database, never downloads media, and
checkpoints each distinct work through the source metadata registry.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
for candidate in (ROOT, BACKEND_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.models import Media  # noqa: E402
from app.services.pixiv_metadata_ingestion_service import (  # noqa: E402
    MIN_REQUEST_SPACING_SECONDS,
    PersistentRequestSpacing,
    PixivMetadataGateError,
    PixivMetadataState,
    backfill_creator_source_observations,
    acquisition_work_lifecycle_counts,
    conflicted_distinct_work_ids,
    pending_distinct_work_ids,
    promotion_manifest,
    queue_media_for_pixiv_metadata,
    require_rotation_confirmation,
    run_bounded_acquisition,
    manifest_scoped_outcome_key,
)
from app.services.pixiv_filename_prior_service import PARSER_VERSION  # noqa: E402
from app.utils.cache import invalidate_source_metadata_search_cache  # noqa: E402
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as gallery_adapter  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402


ACCEPTED_IMMUTABLE_DATABASES = {
    "blombooru_scv2_r2r_dryrun_test_20260710",
    "blombooru_scv2_r2_review4_test_20260710",
    "blombooru_scv2_ml1_acquisition_test_20260712",
    "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715",
    "blombooru_scv2_sv1_controlled_scale_test_20260718",
    "blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1",
    "blombooru_scv2_sv1_rebuild_verification_test_20260718",
}
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests/phase-4.5-scv2-ml1-pixiv-metadata-ingestion"
TOKEN_CANDIDATE_RE = re.compile(r"[A-Za-z0-9_.=+/\-:%;,@!?$^&*(){}\[\]~]{8,}")
OWNER_SAMPLE_CSV = ROOT / ".local_manifests/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure/owner-review/pixiv-missing-work-owner-review-sample.csv"
CANARY_BATCH_SIZE = 20
MAX_CANARY_WORKS = 60
LOCAL_CREDENTIAL_RISK_ENV = "VIOLET_LOCAL_CREDENTIAL_RISK_ACCEPTED"
EXECUTABLE_MANIFEST_VERSION = "ml1_pixiv_executable_manifest_v1"
EXCLUSION_POLICY_VERSION = "ml1_complete_terminal_normalization_conflict_exclusion_v1"
SOURCE_SNAPSHOT_FINGERPRINT = "40747ed5faed0515cf5077211ed0aa6806825b1d6e16e9944051892e17474baf"


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def build_executable_manifest(work_ids: Sequence[str], *, manifest_kind: str) -> dict[str, Any]:
    return {
        "manifest_version": EXECUTABLE_MANIFEST_VERSION,
        "manifest_kind": str(manifest_kind),
        "provider": "pixiv",
        "work_ids": sorted({str(value) for value in work_ids}, key=int),
        "source_snapshot_fingerprint": SOURCE_SNAPSHOT_FINGERPRINT,
        "parser_version": PARSER_VERSION,
        "exclusion_policy_version": EXCLUSION_POLICY_VERSION,
    }


def executable_manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(manifest), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _manifest_fingerprint(work_ids: Sequence[str], *, manifest_kind: str = "main") -> str:
    return executable_manifest_fingerprint(
        build_executable_manifest(work_ids, manifest_kind=manifest_kind)
    )


def _local_credential_risk_accepted(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "accept_local_credential_risk", False)) or str(
        os.getenv(LOCAL_CREDENTIAL_RISK_ENV, "")
    ).strip().casefold() == "true"


def credential_waiver_evidence(
    *, accepted: bool, policy: str | None = None, scope: str | None = None
) -> dict[str, Any]:
    selected_policy = str(policy or "operator_accepted_local_credential_risk_v1")
    if accepted and re.fullmatch(r"operator_accepted_[a-z0-9_]+", selected_policy) is None:
        raise PixivMetadataGateError("blocked_credential_waiver_policy_identity_invalid")
    return {
        "policy": selected_policy if accepted else "default_rotation_and_fingerprint_gate_v1",
        "project_owner_authorized": accepted,
        "credential_rotation_required": not accepted,
        "fingerprint_scan_required": not accepted,
        "existing_profile_use_authorized": accepted,
        "scope": str(scope or "isolated_ml1_pixiv_metadata_only_execution") if accepted else "default_provider_execution",
        "production_allowed": False,
        "raw_secret_exposure_allowed": False,
    }


def systemic_stop_result(results: Sequence[Any]) -> Any | None:
    return next((item for item in results if bool(getattr(item, "systemic_stop", False))), None)


def conflict_manifest_may_start(main_results: Sequence[Any]) -> bool:
    return systemic_stop_result(main_results) is None


def final_outcome_for_result(result: Any, *, conflict: bool) -> str:
    state = str(result.state)
    if conflict:
        return {
            PixivMetadataState.COMPLETE.value: "conflict_resolved_metadata_complete",
            PixivMetadataState.TERMINAL.value: "conflict_resolved_terminal_unavailable",
            PixivMetadataState.NORMALIZATION_FAILED.value: "conflict_normalization_failed",
            PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value: "conflict_unresolved_after_exact_provider_evidence",
            PixivMetadataState.RETRYABLE.value: "conflict_retryable_exhausted",
        }[state]
    return {
        PixivMetadataState.COMPLETE.value: "metadata_complete",
        PixivMetadataState.TERMINAL.value: "terminal_remote_unavailable",
        PixivMetadataState.RETRYABLE.value: "retryable_exhausted_or_systemically_stopped",
        PixivMetadataState.NORMALIZATION_FAILED.value: "normalization_failed",
        PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value: "provider_identity_mismatch",
    }[state]


def select_deterministic_canary_work_ids(
    manifest: Sequence[str], sample_csv: Path = OWNER_SAMPLE_CSV, *, limit: int = MAX_CANARY_WORKS
) -> tuple[str, ...]:
    manifest_set = {str(value) for value in manifest}
    selected: list[str] = []
    if sample_csv.is_file():
        with sample_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                work_id = str(row.get("pixiv_work_id") or "")
                if work_id in manifest_set and work_id not in selected:
                    selected.append(work_id)
    selected.extend(work_id for work_id in sorted(manifest_set, key=int) if work_id not in selected)
    return tuple(selected[:limit])


def governed_page_outcome(statuses: Iterable[str]) -> str:
    values = {str(value or "") for value in statuses}
    complete = {"metadata_complete", "observed", "active", "accepted"}
    terminal = {"terminal_remote_unavailable"}
    deferred = {"deferred_nonblocking_source_page_mismatch"}
    if values and values.issubset(complete):
        return "metadata_complete"
    if values and values.issubset(terminal):
        return "terminal_remote_unavailable"
    if values and values.issubset(deferred):
        return "deferred_nonblocking_source_page_mismatch"
    return "unresolved_mixed_or_open_page_state"


def validate_gallery_dl_profile(
    entrypoint: Sequence[str], *, command_runner=subprocess.run, timeout_seconds: int = 30
) -> dict[str, Any]:
    version = command_runner(
        [*entrypoint, "--version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_seconds, shell=False,
    )
    completed = command_runner(
        [*entrypoint, "--config-status"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_seconds, shell=False,
    )
    output = str(completed.stdout or "")
    return {
        "entrypoint_label": Path(str(entrypoint[0])).name,
        "version_command_passed": version.returncode == 0,
        "version": str(version.stdout or "").strip().splitlines()[0] if version.returncode == 0 and str(version.stdout or "").strip() else "unavailable",
        "configuration_status_exit_code": int(completed.returncode),
        "configuration_status_command_passed": completed.returncode == 0,
        "provider_profile_available": completed.returncode == 0 and bool(output.strip()),
        "raw_configuration_output_exposed": False,
    }


def run_deterministic_auth_canary(
    session,
    work_ids: Sequence[str],
    *,
    entrypoint: Sequence[str],
    env: Mapping[str, str] | None = None,
    acquire=run_bounded_acquisition,
    batch_size: int = CANARY_BATCH_SIZE,
    accept_local_credential_risk: bool = False,
    result_callback=None,
    sleeper=time.sleep,
    persistent_spacing: PersistentRequestSpacing | None = None,
    prior_attempt_counts: Mapping[str, int] | None = None,
    max_attempts_per_work: int = 3,
    credential_risk_waiver_policy: str | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    started = time.monotonic()
    selected = tuple(dict.fromkeys(str(value) for value in work_ids))[:MAX_CANARY_WORKS]
    all_results: list[Any] = []

    def proof(*, passed: bool, safe_reason_code: str, **extra: Any) -> dict[str, Any]:
        attempted = [item for item in all_results if item.request_attempted]
        return {
            "passed": passed,
            "authenticated_success": passed,
            "safe_reason_code": safe_reason_code,
            "selected_work_count": len(selected),
            "attempted_work_count": len(attempted),
            "success_count": sum(
                item.state == PixivMetadataState.COMPLETE.value for item in all_results
            ),
            "terminal_count": sum(
                item.state == PixivMetadataState.TERMINAL.value for item in all_results
            ),
            "returned_page_consistency_count": sum(
                int(getattr(item, "page_count", 0) or 0) for item in attempted
            ),
            "private_stable_work_reference": (
                hashlib.sha256(selected[0].encode("utf-8")).hexdigest() if selected else None
            ),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "credential_risk_waiver_policy": credential_risk_waiver_policy,
            "raw_values_exposed": False,
            **extra,
        }

    for offset in range(0, len(selected), batch_size):
        batch = selected[offset : offset + batch_size]
        if offset and persistent_spacing is None and any(item.request_attempted for item in all_results):
            sleeper(MIN_REQUEST_SPACING_SECONDS)
        acquire_kwargs = dict(
            entrypoint=entrypoint,
            authentication_passed=True,
            env=env,
            accept_local_credential_risk=accept_local_credential_risk,
            result_callback=result_callback,
            max_attempts_per_work=max_attempts_per_work,
        )
        if persistent_spacing is not None:
            acquire_kwargs["persistent_spacing"] = persistent_spacing
        if prior_attempt_counts is not None:
            acquire_kwargs["prior_attempt_counts"] = prior_attempt_counts
        results = acquire(
            session,
            batch,
            **acquire_kwargs,
        )
        all_results.extend(results)
        stop = systemic_stop_result(results)
        if stop is not None:
            return all_results, proof(
                passed=False,
                safe_reason_code="provider_authentication_or_route_rejected",
                systemic_stop=True,
                systemic_stop_class=str(stop.error_class or "provider_route_failure"),
            )
        if any(item.state == PixivMetadataState.COMPLETE.value for item in results):
            return all_results, proof(
                passed=True,
                safe_reason_code="authenticated_metadata_consistency_confirmed",
                systemic_stop=False,
                systemic_stop_class=None,
            )
    normalization_count = sum(item.state == PixivMetadataState.NORMALIZATION_FAILED.value for item in all_results)
    return all_results, proof(
        passed=False,
        safe_reason_code="authenticated_route_viability_unresolved",
        systemic_stop=False,
        systemic_stop_class=None,
        route_viability_unresolved=True,
        normalization_failed_count=normalization_count,
    )


def _isolated_database_allowed(database: str) -> bool:
    lowered = database.casefold()
    return (
        database not in ACCEPTED_IMMUTABLE_DATABASES
        and any(lane in lowered for lane in ("ml1", "sv1b"))
        and "test" in lowered
        and not any(marker in lowered for marker in ("prod", "production"))
        and lowered not in {"blombooru", "postgres", "production", "prod"}
    )


def _known_secret_fingerprints(env: Mapping[str, str] | None = None) -> set[str]:
    values = env if env is not None else os.environ
    raw = str(values.get("VIOLET_COMPROMISED_SECRET_SHA256", ""))
    values_raw = [item for item in re.split(r"[,;\s]+", raw) if item]
    if any(re.fullmatch(r"[0-9a-fA-F]{64}", item) is None for item in values_raw):
        raise PixivMetadataGateError("blocked_malformed_compromised_secret_fingerprint")
    return {item.casefold() for item in values_raw}


def scan_text_for_fingerprints(text_value: str, fingerprints: set[str]) -> int:
    return len(scan_text_fingerprint_ids(text_value, fingerprints))


def scan_text_fingerprint_ids(text_value: str, fingerprints: set[str]) -> set[str]:
    matches: set[str] = set()
    for candidate in TOKEN_CANDIDATE_RE.findall(text_value):
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest().casefold()
        if digest in fingerprints:
            matches.add(digest[:12])
    return matches


def record_attempt_accounting(
    summary: dict[str, Any],
    outcome_ledger: Mapping[str, Mapping[str, Any]],
    all_results: Sequence[Any],
    output_dir: Path,
) -> tuple[int, Counter]:
    """Persist truthful provider-call accounting for success and early-stop paths."""

    attempted_results = [item for item in all_results if item.request_attempted]
    request_attempt_count = sum(int(value["attempt_count"]) for value in outcome_ledger.values())
    for key in ("gallery_dl_calls", "pixiv_provider_calls", "provider_metadata_acquisition_calls"):
        summary["operation_counts"][key] = request_attempt_count

    acquisition = summary["acquisition_execution"]
    acquisition["unique_work_ids_attempted_count"] = len(outcome_ledger)
    acquisition["normal_manifest_work_ids_attempted_count"] = sum(
        value["manifest_kind"] == "main" for value in outcome_ledger.values()
    )
    acquisition["conflict_manifest_work_ids_attempted_count"] = sum(
        value["manifest_kind"] == "conflict" for value in outcome_ledger.values()
    )
    acquisition["provider_request_attempt_count"] = request_attempt_count
    acquisition["gallery_dl_call_count"] = request_attempt_count
    acquisition["max_observed_attempts_for_one_work"] = max(
        [0, *(int(value["attempt_count"]) for value in outcome_ledger.values())]
    )

    outcome_counts = Counter(value["final_outcome"] for value in outcome_ledger.values())
    acquisition["final_outcome_counts"] = dict(sorted(outcome_counts.items()))
    ledger_payload = [
        {"work_id": work_id, **value}
        for work_id, value in sorted(outcome_ledger.items(), key=lambda item: int(item[0]))
    ]
    _write_private_json(output_dir / "final-work-outcome-ledger.json", ledger_payload)
    acquisition["final_outcome_ledger_fingerprint"] = hashlib.sha256(
        json.dumps(ledger_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    acquisition["checkpoint_write_count"] = len(outcome_ledger)
    acquisition["retry_distribution"] = dict(
        sorted(
            Counter(str(value["attempt_count"]) for value in outcome_ledger.values()).items(),
            key=lambda item: int(item[0]),
        )
    )
    acquisition["successful_work_count"] = sum(
        outcome_counts[key] for key in ("metadata_complete", "conflict_resolved_metadata_complete")
    )
    acquisition["terminal_work_count"] = sum(
        outcome_counts[key]
        for key in ("terminal_remote_unavailable", "conflict_resolved_terminal_unavailable")
    )
    acquisition["retryable_work_count"] = sum(
        outcome_counts[key]
        for key in ("retryable_exhausted_or_systemically_stopped", "conflict_retryable_exhausted")
    )
    acquisition["normalization_failed_work_count"] = sum(
        outcome_counts[key] for key in ("normalization_failed", "conflict_normalization_failed")
    )
    acquisition["provider_identity_mismatch_work_count"] = sum(
        outcome_counts[key]
        for key in ("provider_identity_mismatch", "conflict_unresolved_after_exact_provider_evidence")
    )
    acquisition["attempts_by_work"] = {
        item.work_id: int(item.attempt_count) for item in attempted_results
    }
    summary["result_state_counts"] = dict(
        sorted(Counter(item.state for item in all_results).items())
    )
    return request_attempt_count, outcome_counts


def scan_paths_for_fingerprints(paths: Iterable[Path], fingerprints: set[str]) -> dict[str, Any]:
    scanned = 0
    unreadable = 0
    matches = 0
    matched_file_labels: list[str] = []
    matched_fingerprint_ids: set[str] = set()
    for path in sorted({item.resolve() for item in paths if item.exists() and item.is_file()}):
        try:
            value = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            unreadable += 1
            continue
        scanned += 1
        file_match_ids = scan_text_fingerprint_ids(value, fingerprints)
        matches += len(file_match_ids)
        matched_fingerprint_ids.update(file_match_ids)
        if file_match_ids:
            matched_file_labels.append(path.name)
    return {
        "files_scanned": scanned,
        "unreadable_files": unreadable,
        "fingerprint_match_count": matches,
        "matched_file_labels": sorted(set(matched_file_labels)),
        "matched_fingerprint_ids": sorted(matched_fingerprint_ids),
        "matched_values_exposed": False,
    }


def _git_paths() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    paths = [ROOT / line for line in completed.stdout.splitlines() if line.strip()]
    local_root = ROOT / ".local_manifests"
    if local_root.exists():
        for phase_dir in local_root.glob("phase-4.5-scv2-ml1*"):
            paths.extend(path for path in phase_dir.rglob("*") if path.is_file())
    return paths


def redacted_secret_scan(output_dir: Path, *, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    fingerprints = _known_secret_fingerprints(env)
    if not fingerprints:
        raise PixivMetadataGateError("blocked_compromised_secret_fingerprints_required")
    paths = _git_paths()
    if output_dir.exists():
        paths.extend(path for path in output_dir.rglob("*") if path.is_file())
    result = scan_paths_for_fingerprints(paths, fingerprints)
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    result["current_diff_fingerprint_match_count"] = scan_text_for_fingerprints(diff.stdout, fingerprints)
    result["fingerprint_count"] = len(fingerprints)
    result["passed"] = result["fingerprint_match_count"] == 0 and result["current_diff_fingerprint_match_count"] == 0
    return result


def run_normalization_replay(
    args: argparse.Namespace,
    session,
    output_dir: Path,
    *,
    waiver_accepted: bool,
) -> dict[str, Any]:
    """Explicit corrected replay for previously final normalization outcomes only."""

    additional_diagnostic_calls = int(getattr(args, "additional_diagnostic_calls", 0) or 0)
    if additional_diagnostic_calls < 0:
        raise PixivMetadataGateError("blocked_negative_additional_diagnostic_calls")

    summary_path = output_dir / "execution-summary.json"
    ledger_path = output_dir / "final-work-outcome-ledger.json"
    checkpoint_path = output_dir / "acquisition-checkpoint.json"
    main_manifest_path = output_dir / "exact-distinct-work-manifest.json"
    conflict_manifest_path = output_dir / "exact-conflict-resolution-manifest.json"
    required = (summary_path, ledger_path, checkpoint_path, main_manifest_path, conflict_manifest_path)
    if any(not path.is_file() for path in required):
        raise PixivMetadataGateError("blocked_normalization_replay_missing_prior_evidence")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    ledger_rows = json.loads(ledger_path.read_text(encoding="utf-8"))
    prior_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    main_manifest_payload = json.loads(main_manifest_path.read_text(encoding="utf-8"))
    conflict_manifest_payload = json.loads(conflict_manifest_path.read_text(encoding="utf-8"))
    main_fingerprint = executable_manifest_fingerprint(main_manifest_payload)
    conflict_fingerprint = executable_manifest_fingerprint(conflict_manifest_payload)
    if not (
        prior_checkpoint.get("main_manifest_fingerprint") == main_fingerprint
        and prior_checkpoint.get("conflict_manifest_fingerprint") == conflict_fingerprint
    ):
        raise PixivMetadataGateError("blocked_normalization_replay_manifest_checkpoint_mismatch")

    ledger = {str(row["work_id"]): dict(row) for row in ledger_rows}
    main_ids = tuple(
        row["work_id"]
        for row in ledger_rows
        if row["final_outcome"] == "normalization_failed"
    )
    conflict_ids = tuple(
        row["work_id"]
        for row in ledger_rows
        if row["final_outcome"] == "conflict_normalization_failed"
    )
    if not main_ids and not conflict_ids:
        return summary

    entrypoint = gallery_adapter.probe_gallery_dl_entrypoint(args.gallery_dl_command or None)
    profile = validate_gallery_dl_profile(entrypoint.command, timeout_seconds=min(args.timeout, 30))
    if not profile["provider_profile_available"]:
        raise PixivMetadataGateError("blocked_gallery_dl_profile_unavailable")
    replay_checkpoint_path = output_dir / "normalization-replay-checkpoint.json"
    replay_attempts = 0

    def replace_outcome(item: Any, *, conflict: bool) -> None:
        nonlocal replay_attempts
        if not item.request_attempted:
            return
        prior = ledger.get(item.work_id)
        if not prior or prior.get("final_outcome") not in {
            "normalization_failed",
            "conflict_normalization_failed",
        }:
            raise PixivMetadataGateError("normalization_replay_outside_prior_failed_manifest")
        replay_attempts += int(item.attempt_count)
        ledger[item.work_id] = {
            "manifest_kind": "conflict" if conflict else "main",
            "final_outcome": final_outcome_for_result(item, conflict=conflict),
            "attempt_count": int(item.attempt_count),
            "systemic_stop": bool(item.systemic_stop),
            "error_class": item.error_class,
            "corrected_replay": True,
        }
        _write_private_json(
            replay_checkpoint_path,
            {
                "checkpoint_version": "ml1_pixiv_normalization_replay_v1",
                "main_manifest_fingerprint": main_fingerprint,
                "conflict_manifest_fingerprint": conflict_fingerprint,
                "replay_main_count": len(main_ids),
                "replay_conflict_count": len(conflict_ids),
                "replay_provider_request_attempt_count": replay_attempts,
                "final_outcomes": ledger,
            },
        )

    replay_started = time.monotonic()
    main_results = run_bounded_acquisition(
        session,
        main_ids,
        entrypoint=entrypoint.command,
        authentication_passed=True,
        timeout_seconds=args.timeout,
        accept_local_credential_risk=waiver_accepted,
        allow_normalization_replay=True,
        result_callback=lambda item: replace_outcome(item, conflict=False),
    )
    main_stop = systemic_stop_result(main_results)
    conflict_results: list[Any] = []
    if main_stop is None:
        conflict_results = run_bounded_acquisition(
            session,
            conflict_ids,
            entrypoint=entrypoint.command,
            authentication_passed=True,
            timeout_seconds=args.timeout,
            accept_local_credential_risk=waiver_accepted,
            allow_conflict_resolution=True,
            allow_normalization_replay=True,
            result_callback=lambda item: replace_outcome(item, conflict=True),
        )

    final_ledger_rows = [
        {"work_id": work_id, **value}
        for work_id, value in sorted(ledger.items(), key=lambda item: int(item[0]))
    ]
    _write_private_json(ledger_path, final_ledger_rows)
    ledger_fingerprint = hashlib.sha256(
        json.dumps(final_ledger_rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    outcome_counts = Counter(row["final_outcome"] for row in final_ledger_rows)
    acquisition = summary["acquisition_execution"]
    total_diagnostic_calls = int(acquisition.get("diagnostic_provider_request_count") or 0) + additional_diagnostic_calls
    total_requests = int(acquisition.get("provider_request_attempt_count") or 0) + additional_diagnostic_calls + replay_attempts
    acquisition.update(
        provider_request_attempt_count=total_requests,
        gallery_dl_call_count=total_requests,
        final_outcome_counts=dict(sorted(outcome_counts.items())),
        final_outcome_ledger_fingerprint=ledger_fingerprint,
        successful_work_count=sum(outcome_counts[key] for key in ("metadata_complete", "conflict_resolved_metadata_complete")),
        terminal_work_count=sum(outcome_counts[key] for key in ("terminal_remote_unavailable", "conflict_resolved_terminal_unavailable")),
        retryable_work_count=sum(outcome_counts[key] for key in ("retryable_exhausted_or_systemically_stopped", "conflict_retryable_exhausted")),
        normalization_failed_work_count=sum(outcome_counts[key] for key in ("normalization_failed", "conflict_normalization_failed")),
        provider_identity_mismatch_work_count=sum(outcome_counts[key] for key in ("provider_identity_mismatch", "conflict_unresolved_after_exact_provider_evidence")),
        systemic_stop=main_stop is not None or systemic_stop_result(conflict_results) is not None,
        systemic_stop_stage="normalization_replay_main" if main_stop is not None else "normalization_replay_conflict" if systemic_stop_result(conflict_results) is not None else None,
        diagnostic_provider_request_count=total_diagnostic_calls,
        diagnostic_private_work_ref="76c0ee4cadc1a00a",
        normalization_replay_main_work_count=len(main_ids),
        normalization_replay_conflict_work_count=len(conflict_ids),
        normalization_replay_request_count=replay_attempts,
        normalization_replay_elapsed_seconds=round(time.monotonic() - replay_started, 6),
    )
    for key in ("gallery_dl_calls", "pixiv_provider_calls", "provider_metadata_acquisition_calls"):
        summary["operation_counts"][key] = total_requests
    summary["gallery_dl_configuration_check"] = {"performed": True, **profile}
    lifecycle_counts = acquisition_work_lifecycle_counts(session)
    summary["final_work_lifecycle_counts"] = lifecycle_counts
    summary["remaining_distinct_work_count"] = sum(
        int(lifecycle_counts.get(key, 0))
        for key in ("pending", "retryable", "normalization_failed", "provider_identity_mismatch", "conflict")
    )
    summary["metadata_fixed_point_reached"] = summary["remaining_distinct_work_count"] == 0
    summary["fixed_point_reached"] = summary["metadata_fixed_point_reached"]
    _write_private_json(summary_path, summary)
    return summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_started_monotonic = time.monotonic()
    if str(os.getenv("VIOLET_ENV", "")).casefold() != "test":
        raise PixivMetadataGateError("blocked_environment_isolation:VIOLET_ENV_must_be_test")
    if not _isolated_database_allowed(args.database):
        raise PixivMetadataGateError("blocked_environment_isolation:isolated_ml1_test_or_dev_database_required")
    output_dir = args.output_dir.resolve()
    if ROOT not in output_dir.parents or ".local_manifests" not in output_dir.parts:
        raise PixivMetadataGateError("blocked_unsafe_private_output_path")

    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        identity = str(session.execute(text("SELECT current_database()")) .scalar() or "")
        if identity != args.database:
            raise PixivMetadataGateError("blocked_environment_isolation:database_identity_mismatch")
        media = session.query(Media).order_by(Media.id.asc()).all()
        decisions = [queue_media_for_pixiv_metadata(session, item) for item in media]
        creator_backfill = backfill_creator_source_observations(session)
        session.commit()
        invalidate_source_metadata_search_cache()
        manifest = pending_distinct_work_ids(session)
        conflict_manifest = conflicted_distinct_work_ids(session)
        main_manifest_payload = build_executable_manifest(manifest, manifest_kind="main")
        conflict_manifest_payload = build_executable_manifest(conflict_manifest, manifest_kind="conflict")
        main_manifest_fingerprint = executable_manifest_fingerprint(main_manifest_payload)
        conflict_manifest_fingerprint = executable_manifest_fingerprint(conflict_manifest_payload)
        phase_manifest_fingerprint = str(
            getattr(args, "phase_manifest_fingerprint", "") or ""
        ).strip().casefold()
        if phase_manifest_fingerprint and not re.fullmatch(r"[0-9a-f]{64}", phase_manifest_fingerprint):
            raise PixivMetadataGateError("blocked_phase_manifest_fingerprint_invalid")
        waiver_accepted = _local_credential_risk_accepted(args)
        if getattr(args, "replay_normalization_failures", False):
            if not args.execute:
                raise PixivMetadataGateError("normalization_replay_requires_execute")
            if not waiver_accepted:
                require_rotation_confirmation()
            return run_normalization_replay(
                args,
                session,
                output_dir,
                waiver_accepted=waiver_accepted,
            )
        waiver_policy = str(getattr(args, "credential_risk_waiver_policy", "") or "") or None
        waiver_scope = str(getattr(args, "credential_risk_waiver_scope", "") or "") or None
        summary: dict[str, Any] = {
            "database_label": "isolated-ml1-dev-test",
            "queued_media_count": len(decisions),
            "queue_state_counts": dict(sorted(Counter(item.state for item in decisions).items())),
            "exact_distinct_work_manifest_count": len(manifest),
            "exact_work_ids_public": False,
            "execution_requested": bool(args.execute),
            "credential_rotation_confirmation_present": False,
            "credential_safety": credential_waiver_evidence(
                accepted=waiver_accepted, policy=waiver_policy, scope=waiver_scope
            ),
            "redacted_secret_scan": {"performed": False},
            "redacted_authentication_preflight": {"performed": False},
            "gallery_dl_configuration_check": {"performed": False},
            "operation_counts": {
                "gallery_dl_calls": 0,
                "pixiv_provider_calls": 0,
                "provider_metadata_acquisition_calls": 0,
                "media_downloads": 0,
            },
            "acquisition_execution": {
                "acquisition_route_active": False,
                "acquisition_manifest_distinct_work_count": len(manifest),
                "acquisition_manifest_fingerprint": main_manifest_fingerprint,
                "conflict_resolution_manifest_count": len(conflict_manifest),
                "conflict_resolution_manifest_fingerprint": conflict_manifest_fingerprint,
                "checkpoint_main_manifest_fingerprint": main_manifest_fingerprint,
                "checkpoint_conflict_manifest_fingerprint": conflict_manifest_fingerprint,
                "phase_manifest_fingerprint": phase_manifest_fingerprint or None,
                "max_attempts_per_work": 3,
                "unique_work_ids_attempted_count": 0,
                "normal_manifest_work_ids_attempted_count": 0,
                "conflict_manifest_work_ids_attempted_count": 0,
                "provider_request_attempt_count": 0,
                "gallery_dl_call_count": 0,
                "successful_work_count": 0,
                "terminal_work_count": 0,
                "retryable_work_count": 0,
                "skipped_complete_work_count": 0,
                "resumed_work_count": 0,
                "duplicate_unexpected_work_attempt_count": 0,
                "out_of_manifest_work_attempt_count": 0,
                "complete_work_reacquisition_count": 0,
                "max_observed_attempts_for_one_work": 0,
                "retry_attempts_attributable_to_manifest_work": True,
                "resume_only_remaining_open_works": True,
                "attempts_by_work": {},
                "final_outcome_counts": {},
                "final_outcome_ledger_fingerprint": None,
                "systemic_stop": False,
                "systemic_stop_class": None,
                "systemic_stop_stage": None,
                "conflict_manifest_started": False,
                "checkpoint_resume_cycles": 0,
                "checkpoint_write_count": 0,
                "retry_distribution": {},
                "elapsed_seconds": 0.0,
                "average_request_interval_seconds": None,
            },
            "promotion_manifest": promotion_manifest(),
            "creator_source_backfill": creator_backfill,
        }
        _write_private_json(output_dir / "exact-distinct-work-manifest.json", main_manifest_payload)
        _write_private_json(output_dir / "exact-conflict-resolution-manifest.json", conflict_manifest_payload)
        if not args.execute:
            _write_private_json(output_dir / "queue-summary.json", summary)
            return summary

        if not waiver_accepted:
            require_rotation_confirmation()
        summary["acquisition_execution"]["acquisition_route_active"] = True
        if waiver_accepted:
            summary["redacted_secret_scan"] = {
                "performed": False,
                "waived_by_policy": waiver_policy or "operator_accepted_local_credential_risk_v1",
                "raw_values_exposed": False,
            }
        else:
            summary["credential_rotation_confirmation_present"] = True
            scan = redacted_secret_scan(output_dir)
            summary["redacted_secret_scan"] = scan
            if not scan["passed"]:
                raise PixivMetadataGateError("blocked_compromised_secret_fingerprint_detected")
        entrypoint = gallery_adapter.probe_gallery_dl_entrypoint(args.gallery_dl_command or None)
        profile = validate_gallery_dl_profile(entrypoint.command, timeout_seconds=min(args.timeout, 30))
        summary["gallery_dl_configuration_check"] = {"performed": True, **profile}
        if not profile["provider_profile_available"]:
            raise PixivMetadataGateError("blocked_gallery_dl_profile_unavailable")
        outcome_ledger: dict[str, dict[str, Any]] = {}
        page_outcome_ledger: dict[str, dict[str, Any]] = {}
        checkpoint_path = output_dir / "acquisition-checkpoint.json"
        if checkpoint_path.is_file():
            prior_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if not (
                prior_checkpoint.get("main_manifest_fingerprint") == main_manifest_fingerprint
                and prior_checkpoint.get("conflict_manifest_fingerprint") == conflict_manifest_fingerprint
                and prior_checkpoint.get("phase_manifest_fingerprint") == (phase_manifest_fingerprint or None)
            ):
                raise PixivMetadataGateError("blocked_stale_acquisition_checkpoint_fingerprint_mismatch")
            outcome_ledger.update(prior_checkpoint.get("final_outcomes") or {})
            page_outcome_ledger.update(prior_checkpoint.get("manifest_scoped_page_outcomes") or {})

        persistent_spacing = None
        if phase_manifest_fingerprint:
            persistent_spacing = PersistentRequestSpacing(
                output_dir / "persistent-request-spacing.json",
                phase_manifest_fingerprint=phase_manifest_fingerprint,
            )

        def requested_page_statuses_for_work(work_id: str) -> dict[int, set[str]]:
            page_statuses: dict[int, set[str]] = {}
            for page_index, status in session.execute(text("""
                    SELECT source_page_index,status FROM blombooru_source_metadata_records
                    WHERE provider='pixiv' AND metadata_kind=:kind AND source_work_id=:work_id
                """), {"kind": "pixiv_ingestion_gate", "work_id": str(work_id)}):
                page_statuses.setdefault(int(page_index or 0), set()).add(str(status or ""))
            return page_statuses

        def checkpoint_result(item: Any, *, conflict: bool = False) -> None:
            if not item.request_attempted:
                return
            prior_outcome = outcome_ledger.get(item.work_id)
            if prior_outcome is not None and not (
                "retryable" in str(prior_outcome.get("final_outcome") or "")
                and int(prior_outcome.get("attempt_count") or 0) < int(item.attempt_count) <= 3
            ):
                raise PixivMetadataGateError("duplicate_final_outcome_for_attempted_work")
            outcome_ledger[item.work_id] = {
                "manifest_kind": "conflict" if conflict else "main",
                "final_outcome": final_outcome_for_result(item, conflict=conflict),
                "attempt_count": int(item.attempt_count),
                "systemic_stop": bool(item.systemic_stop),
                "error_class": item.error_class,
            }
            if phase_manifest_fingerprint:
                for page_index, statuses in requested_page_statuses_for_work(item.work_id).items():
                    page_key = manifest_scoped_outcome_key(
                        phase_manifest_fingerprint, "pixiv", item.work_id, page_index
                    )
                    if page_key in page_outcome_ledger:
                        prior_page = page_outcome_ledger[page_key]
                        if not (
                            str(prior_page.get("final_outcome") or "") == "unresolved_mixed_or_open_page_state"
                            and int(prior_page.get("attempt_count") or 0) < int(item.attempt_count) <= 3
                        ):
                            raise PixivMetadataGateError("duplicate_manifest_scoped_page_outcome")
                    page_outcome_ledger[page_key] = {
                        "provider": "pixiv",
                        "work_id": item.work_id,
                        "requested_page_index": page_index,
                        "manifest_kind": "conflict" if conflict else "main",
                        "final_outcome": governed_page_outcome(statuses),
                        "record_statuses": sorted(statuses),
                        "attempt_count": int(item.attempt_count),
                    }
            _write_private_json(
                checkpoint_path,
                {
                    "checkpoint_version": "ml1_pixiv_acquisition_checkpoint_v1",
                    "main_manifest_fingerprint": main_manifest_fingerprint,
                    "conflict_manifest_fingerprint": conflict_manifest_fingerprint,
                    "phase_manifest_fingerprint": phase_manifest_fingerprint or None,
                    "final_outcomes": outcome_ledger,
                    "manifest_scoped_page_outcomes": page_outcome_ledger,
                    "provider_request_attempt_count": sum(
                        int(value["attempt_count"]) for value in outcome_ledger.values()
                    ),
                },
            )

        canary_results: list[Any] = []
        canary: dict[str, Any]
        if manifest:
            canary_limit = int(getattr(args, "canary_work_limit", MAX_CANARY_WORKS) or MAX_CANARY_WORKS)
            if canary_limit < 1 or canary_limit > MAX_CANARY_WORKS:
                raise PixivMetadataGateError("blocked_auth_canary_work_limit_invalid")
            canary_ids = select_deterministic_canary_work_ids(manifest, limit=canary_limit)
            canary_results, canary = run_deterministic_auth_canary(
                session,
                canary_ids,
                entrypoint=entrypoint.command,
                env=os.environ,
                accept_local_credential_risk=waiver_accepted,
                result_callback=lambda item: checkpoint_result(item, conflict=False),
                persistent_spacing=persistent_spacing,
                prior_attempt_counts={key: int(value.get("attempt_count") or 0) for key, value in outcome_ledger.items()},
                max_attempts_per_work=int(getattr(args, "canary_max_attempts_per_work", 3) or 3),
                credential_risk_waiver_policy=waiver_policy,
            )
            summary["redacted_authentication_preflight"] = {"performed": True, **canary}
            if not canary.get("passed"):
                summary["acquisition_execution"]["systemic_stop"] = bool(canary.get("systemic_stop"))
                summary["acquisition_execution"]["systemic_stop_class"] = canary.get("systemic_stop_class")
                summary["acquisition_execution"]["systemic_stop_stage"] = "canary" if canary.get("systemic_stop") else None
                record_attempt_accounting(summary, outcome_ledger, canary_results, output_dir)
                _write_private_json(output_dir / "execution-summary.json", summary)
                raise PixivMetadataGateError(
                    "blocked_gallery_dl_canary_systemic_stop"
                    if canary.get("systemic_stop")
                    else "blocked_gallery_dl_canary_route_viability_unresolved"
                )
        else:
            canary = {"passed": True, "performed": False, "reason": "empty_main_manifest"}
            summary["redacted_authentication_preflight"] = canary
        remaining = pending_distinct_work_ids(session)
        results = run_bounded_acquisition(
            session,
            remaining,
            entrypoint=entrypoint.command,
            authentication_passed=True,
            timeout_seconds=args.timeout,
            accept_local_credential_risk=waiver_accepted,
            result_callback=lambda item: checkpoint_result(item, conflict=False),
            persistent_spacing=persistent_spacing,
            prior_attempt_counts={key: int(value.get("attempt_count") or 0) for key, value in outcome_ledger.items()},
        )
        main_stop = systemic_stop_result(results)
        conflict_results: list[Any] = []
        if conflict_manifest_may_start(results):
            summary["acquisition_execution"]["conflict_manifest_started"] = bool(conflict_manifest)
            conflict_results = run_bounded_acquisition(
                session,
                conflict_manifest,
                entrypoint=entrypoint.command,
                authentication_passed=True,
                timeout_seconds=args.timeout,
                allow_conflict_resolution=True,
                accept_local_credential_risk=waiver_accepted,
                result_callback=lambda item: checkpoint_result(item, conflict=True),
                persistent_spacing=persistent_spacing,
                prior_attempt_counts={key: int(value.get("attempt_count") or 0) for key, value in outcome_ledger.items()},
            )
            conflict_stop = systemic_stop_result(conflict_results)
            if conflict_stop is not None:
                summary["acquisition_execution"]["systemic_stop"] = True
                summary["acquisition_execution"]["systemic_stop_class"] = conflict_stop.error_class
                summary["acquisition_execution"]["systemic_stop_stage"] = "conflict_manifest"
        else:
            summary["acquisition_execution"]["systemic_stop"] = True
            summary["acquisition_execution"]["systemic_stop_class"] = main_stop.error_class
            summary["acquisition_execution"]["systemic_stop_stage"] = "main_manifest"
        all_results = [*canary_results, *results, *conflict_results]
        request_attempt_count, outcome_counts = record_attempt_accounting(
            summary, outcome_ledger, all_results, output_dir
        )
        elapsed_seconds = round(time.monotonic() - run_started_monotonic, 6)
        summary["acquisition_execution"]["elapsed_seconds"] = elapsed_seconds
        summary["acquisition_execution"]["average_request_interval_seconds"] = (
            round(elapsed_seconds / (request_attempt_count - 1), 6)
            if request_attempt_count > 1
            else None
        )
        summary["acquisition_execution"]["terminal_reason_distribution"] = dict(sorted(Counter(
            str(value["error_class"] or "unspecified")
            for value in outcome_ledger.values()
            if value["final_outcome"] in {
                "terminal_remote_unavailable",
                "conflict_resolved_terminal_unavailable",
            }
        ).items()))
        summary["acquisition_execution"]["manifest_scoped_page_outcome_count"] = len(page_outcome_ledger)
        summary["acquisition_execution"]["manifest_scoped_page_outcome_fingerprint"] = executable_manifest_fingerprint({
            "manifest_kind": "sv1b-page-outcomes",
            "work_ids": sorted(page_outcome_ledger),
        })
        summary["acquisition_execution"]["persistent_spacing"] = (
            persistent_spacing.public_evidence() if persistent_spacing is not None else {"enabled": False}
        )
        summary["acquisition_execution"]["skipped_complete_work_count"] = sum(not item.request_attempted for item in [*results, *conflict_results])
        lifecycle_counts = acquisition_work_lifecycle_counts(session)
        summary["final_work_lifecycle_counts"] = lifecycle_counts
        summary["remaining_distinct_work_count"] = sum(
            int(lifecycle_counts.get(key, 0))
            for key in ("pending", "retryable", "normalization_failed", "provider_identity_mismatch")
        )
        summary["metadata_fixed_point_reached"] = summary["remaining_distinct_work_count"] == 0
        summary["fixed_point_reached"] = (
            summary["metadata_fixed_point_reached"]
            and int(lifecycle_counts.get("conflict", 0)) == 0
        )
        _write_private_json(output_dir / "execution-summary.json", summary)
        return summary
    finally:
        session.close()
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--accept-local-credential-risk",
        action="store_true",
        help="Owner-authorized waiver valid only for the explicitly isolated ML1 metadata-only test execution.",
    )
    parser.add_argument(
        "--replay-normalization-failures",
        action="store_true",
        help="Explicit corrected replay of only prior normalization final outcomes.",
    )
    parser.add_argument(
        "--additional-diagnostic-calls",
        type=int,
        default=0,
        help="Additional redacted diagnostic provider calls to include when resuming corrected replay.",
    )
    parser.add_argument("--gallery-dl-command", default=os.getenv("VIOLET_GALLERY_DL_COMMAND", ""))
    parser.add_argument(
        "--phase-manifest-fingerprint",
        default=os.getenv("VIOLET_PHASE_MANIFEST_FINGERPRINT", ""),
        help="Optional 64-hex phase manifest fingerprint enabling restart-safe spacing and page outcome keys.",
    )
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except PixivMetadataGateError as exc:
        print(json.dumps({"status": str(exc), "raw_values_exposed": False}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": "complete" if summary.get("fixed_point_reached") else "queued_or_incomplete",
                "queued_media_count": summary["queued_media_count"],
                "exact_distinct_work_manifest_count": summary["exact_distinct_work_manifest_count"],
                "gallery_dl_calls": summary["operation_counts"]["gallery_dl_calls"],
                "raw_values_exposed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
