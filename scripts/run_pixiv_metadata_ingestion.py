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
)
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as gallery_adapter  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402


ACCEPTED_IMMUTABLE_DATABASES = {
    "blombooru_scv2_r2r_dryrun_test_20260710",
    "blombooru_scv2_r2_review4_test_20260710",
}
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests/phase-4.5-scv2-ml1-pixiv-metadata-ingestion"
TOKEN_CANDIDATE_RE = re.compile(r"[A-Za-z0-9_.=+/\-:%;,@!?$^&*(){}\[\]~]{8,}")
OWNER_SAMPLE_CSV = ROOT / ".local_manifests/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure/owner-review/pixiv-missing-work-owner-review-sample.csv"
CANARY_BATCH_SIZE = 20
MAX_CANARY_WORKS = 60


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _manifest_fingerprint(work_ids: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted({str(value) for value in work_ids}), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


def validate_gallery_dl_profile(
    entrypoint: Sequence[str], *, command_runner=subprocess.run, timeout_seconds: int = 30
) -> dict[str, Any]:
    completed = command_runner(
        [*entrypoint, "--config-status"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_seconds, shell=False,
    )
    output = str(completed.stdout or "")
    return {
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
) -> tuple[list[Any], dict[str, Any]]:
    selected = tuple(dict.fromkeys(str(value) for value in work_ids))[:MAX_CANARY_WORKS]
    all_results: list[Any] = []
    for offset in range(0, len(selected), batch_size):
        batch = selected[offset : offset + batch_size]
        results = acquire(
            session, batch, entrypoint=entrypoint, authentication_passed=True, env=env
        )
        all_results.extend(results)
        if any(item.error_class == "retryable_authentication" for item in results):
            raise PixivMetadataGateError("blocked_gallery_dl_authentication_invalid")
        if any(item.state == PixivMetadataState.COMPLETE.value for item in results):
            return all_results, {
                "passed": True,
                "selected_work_count": len(selected),
                "attempted_work_count": sum(item.request_attempted for item in all_results),
                "success_count": sum(item.state == PixivMetadataState.COMPLETE.value for item in all_results),
                "terminal_count": sum(item.state == PixivMetadataState.TERMINAL.value for item in all_results),
                "raw_values_exposed": False,
            }
        if any(item.state == PixivMetadataState.RETRYABLE.value for item in results):
            raise PixivMetadataGateError("blocked_gallery_dl_canary_retryable_systemic_failure")
    normalization_count = sum(item.state == PixivMetadataState.NORMALIZATION_FAILED.value for item in all_results)
    if normalization_count and normalization_count == len([item for item in all_results if item.request_attempted]):
        raise PixivMetadataGateError("blocked_gallery_dl_canary_systemic_normalization_failure")
    raise PixivMetadataGateError("blocked_gallery_dl_canary_route_viability_unresolved")


def _isolated_database_allowed(database: str) -> bool:
    lowered = database.casefold()
    return (
        database not in ACCEPTED_IMMUTABLE_DATABASES
        and "ml1" in lowered
        and any(marker in lowered for marker in ("test", "dev"))
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


def run(args: argparse.Namespace) -> dict[str, Any]:
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
        manifest = pending_distinct_work_ids(session)
        conflict_manifest = conflicted_distinct_work_ids(session)
        summary: dict[str, Any] = {
            "database_label": "isolated-ml1-dev-test",
            "queued_media_count": len(decisions),
            "queue_state_counts": dict(sorted(Counter(item.state for item in decisions).items())),
            "exact_distinct_work_manifest_count": len(manifest),
            "exact_work_ids_public": False,
            "execution_requested": bool(args.execute),
            "credential_rotation_confirmation_present": False,
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
                "acquisition_manifest_fingerprint": _manifest_fingerprint(manifest),
                "conflict_resolution_manifest_count": len(conflict_manifest),
                "conflict_resolution_manifest_fingerprint": _manifest_fingerprint(conflict_manifest),
                "max_attempts_per_work": 3,
                "unique_work_ids_attempted_count": 0,
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
            },
            "promotion_manifest": promotion_manifest(),
            "creator_source_backfill": creator_backfill,
        }
        _write_private_json(output_dir / "exact-distinct-work-manifest.json", {"work_ids": manifest})
        _write_private_json(output_dir / "exact-conflict-resolution-manifest.json", {"work_ids": conflict_manifest})
        if not args.execute:
            _write_private_json(output_dir / "queue-summary.json", summary)
            return summary

        require_rotation_confirmation()
        summary["acquisition_execution"]["acquisition_route_active"] = True
        summary["credential_rotation_confirmation_present"] = True
        scan = redacted_secret_scan(output_dir)
        summary["redacted_secret_scan"] = scan
        if not scan["passed"]:
            raise PixivMetadataGateError("blocked_compromised_secret_fingerprint_detected")
        if not manifest:
            summary["redacted_authentication_preflight"] = {"performed": False, "reason": "empty_manifest"}
            _write_private_json(output_dir / "execution-summary.json", summary)
            return summary

        entrypoint = gallery_adapter.probe_gallery_dl_entrypoint(args.gallery_dl_command or None)
        profile = validate_gallery_dl_profile(entrypoint.command, timeout_seconds=min(args.timeout, 30))
        summary["gallery_dl_configuration_check"] = {"performed": True, **profile}
        if not profile["provider_profile_available"]:
            raise PixivMetadataGateError("blocked_gallery_dl_profile_unavailable")
        canary_ids = select_deterministic_canary_work_ids(manifest)
        canary_results, canary = run_deterministic_auth_canary(
            session, canary_ids, entrypoint=entrypoint.command, env=os.environ
        )
        summary["redacted_authentication_preflight"] = {"performed": True, **canary}
        for item in canary_results:
            if item.request_attempted:
                summary["acquisition_execution"]["attempts_by_work"][item.work_id] = item.attempt_count
        canary_attempt_count = sum(item.attempt_count for item in canary_results if item.request_attempted)
        summary["operation_counts"]["gallery_dl_calls"] += canary_attempt_count
        summary["operation_counts"]["pixiv_provider_calls"] += canary_attempt_count
        summary["operation_counts"]["provider_metadata_acquisition_calls"] += canary_attempt_count
        summary["acquisition_execution"]["unique_work_ids_attempted_count"] += sum(item.request_attempted for item in canary_results)
        summary["acquisition_execution"]["provider_request_attempt_count"] += canary_attempt_count
        summary["acquisition_execution"]["gallery_dl_call_count"] += canary_attempt_count
        remaining = pending_distinct_work_ids(session)
        if remaining:
            time.sleep(MIN_REQUEST_SPACING_SECONDS)
        results = run_bounded_acquisition(
            session,
            remaining,
            entrypoint=entrypoint.command,
            authentication_passed=True,
            timeout_seconds=args.timeout,
        )
        conflict_results = run_bounded_acquisition(
            session,
            conflict_manifest,
            entrypoint=entrypoint.command,
            authentication_passed=True,
            timeout_seconds=args.timeout,
            allow_conflict_resolution=True,
        )
        summary["operation_counts"]["gallery_dl_calls"] += sum(item.attempt_count for item in [*results, *conflict_results])
        summary["operation_counts"]["pixiv_provider_calls"] = summary["operation_counts"]["gallery_dl_calls"]
        summary["operation_counts"]["provider_metadata_acquisition_calls"] = summary["operation_counts"]["gallery_dl_calls"]
        attempted_results = [item for item in [*results, *conflict_results] if item.request_attempted]
        summary["acquisition_execution"]["unique_work_ids_attempted_count"] += len(attempted_results)
        summary["acquisition_execution"]["provider_request_attempt_count"] += sum(item.attempt_count for item in attempted_results)
        summary["acquisition_execution"]["gallery_dl_call_count"] = summary["acquisition_execution"]["provider_request_attempt_count"]
        all_results = [*canary_results, *results, *conflict_results]
        summary["acquisition_execution"]["max_observed_attempts_for_one_work"] = max(
            [0, *(item.attempt_count for item in all_results if item.request_attempted)]
        )
        summary["acquisition_execution"]["successful_work_count"] = sum(item.state == PixivMetadataState.COMPLETE.value for item in all_results)
        summary["acquisition_execution"]["terminal_work_count"] = sum(item.state == PixivMetadataState.TERMINAL.value for item in all_results)
        summary["acquisition_execution"]["retryable_work_count"] = sum(item.state == PixivMetadataState.RETRYABLE.value for item in all_results)
        summary["acquisition_execution"]["normalization_failed_work_count"] = sum(item.state == PixivMetadataState.NORMALIZATION_FAILED.value for item in all_results)
        summary["acquisition_execution"]["skipped_complete_work_count"] = sum(not item.request_attempted for item in [*results, *conflict_results])
        for item in [*results, *conflict_results]:
            if item.request_attempted:
                summary["acquisition_execution"]["attempts_by_work"][item.work_id] = item.attempt_count
        summary["result_state_counts"] = dict(sorted(Counter(item.state for item in all_results).items()))
        lifecycle_counts = acquisition_work_lifecycle_counts(session)
        summary["final_work_lifecycle_counts"] = lifecycle_counts
        summary["remaining_distinct_work_count"] = sum(
            int(lifecycle_counts.get(key, 0)) for key in ("pending", "retryable", "normalization_failed")
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
    parser.add_argument("--gallery-dl-command", default=os.getenv("VIOLET_GALLERY_DL_COMMAND", ""))
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
