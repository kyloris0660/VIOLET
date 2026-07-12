"""Queue or explicitly execute durable Pixiv metadata-on-import closure.

This is a reusable operational entrypoint, not a daemon.  It operates only on
an explicitly named isolated test/dev database, never downloads media, and
checkpoints each distinct work through the source metadata registry.
"""

from __future__ import annotations

import argparse
from collections import Counter
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
    build_gallery_dl_metadata_command,
    classify_gallery_dl_failure,
    parse_gallery_dl_stdout,
    pending_distinct_work_ids,
    persist_complete_work,
    promotion_manifest,
    queue_media_for_pixiv_metadata,
    require_rotation_confirmation,
    run_bounded_acquisition,
    mark_work_state,
)
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as gallery_adapter  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402


ACCEPTED_IMMUTABLE_DATABASES = {
    "blombooru_scv2_r2r_dryrun_test_20260710",
    "blombooru_scv2_r2_review4_test_20260710",
}
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests/phase-4.5-scv2-ml1-pixiv-metadata-ingestion"
TOKEN_CANDIDATE_RE = re.compile(r"[A-Za-z0-9._~+\-/]{8,}")


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


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
    return {
        item.casefold()
        for item in re.split(r"[,;\s]+", raw)
        if re.fullmatch(r"[0-9a-fA-F]{64}", item)
    }


def scan_text_for_fingerprints(text_value: str, fingerprints: set[str]) -> int:
    matches = 0
    for candidate in TOKEN_CANDIDATE_RE.findall(text_value):
        if hashlib.sha256(candidate.encode("utf-8")).hexdigest().casefold() in fingerprints:
            matches += 1
    return matches


def scan_paths_for_fingerprints(paths: Iterable[Path], fingerprints: set[str]) -> dict[str, int]:
    scanned = 0
    unreadable = 0
    matches = 0
    for path in sorted({item.resolve() for item in paths if item.exists() and item.is_file()}):
        try:
            value = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            unreadable += 1
            continue
        scanned += 1
        matches += scan_text_for_fingerprints(value, fingerprints)
    return {"files_scanned": scanned, "unreadable_files": unreadable, "fingerprint_match_count": matches}


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


def _redacted_auth_and_first_acquisition(
    session,
    *,
    entrypoint: Sequence[str],
    work_id: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], bool]:
    command = build_gallery_dl_metadata_command(entrypoint, work_id)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        shell=False,
    )
    if completed.returncode != 0:
        state, reason = classify_gallery_dl_failure(completed.stderr or "", authentication_passed=False)
        mark_work_state(session, work_id, state, reason=reason)
        session.commit()
        return {
            "configuration_present": True,
            "provider_profile_available": True,
            "authentication_test_passed": False,
            "error_class": reason,
            "raw_values_exposed": False,
        }, False
    try:
        pages = parse_gallery_dl_stdout(completed.stdout or "", work_id)
        linked = persist_complete_work(session, work_id, pages)
        if linked <= 0:
            raise PixivMetadataGateError("metadata_normalization_failed_no_local_page_link")
        session.commit()
    except Exception as exc:
        session.rollback()
        mark_work_state(
            session,
            work_id,
            PixivMetadataState.NORMALIZATION_FAILED.value,
            reason=exc.__class__.__name__,
        )
        session.commit()
        return {
            "configuration_present": True,
            "provider_profile_available": True,
            "authentication_test_passed": False,
            "error_class": exc.__class__.__name__,
            "raw_values_exposed": False,
        }, False
    return {
        "configuration_present": True,
        "provider_profile_available": True,
        "authentication_test_passed": True,
        "first_manifest_work_completed": True,
        "raw_values_exposed": False,
    }, True


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
        session.commit()
        manifest = pending_distinct_work_ids(session)
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
            "operation_counts": {"gallery_dl_calls": 0, "media_downloads": 0},
            "promotion_manifest": promotion_manifest(),
        }
        _write_private_json(output_dir / "exact-distinct-work-manifest.json", {"work_ids": manifest})
        if not args.execute:
            _write_private_json(output_dir / "queue-summary.json", summary)
            return summary

        require_rotation_confirmation()
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
        preflight, authenticated = _redacted_auth_and_first_acquisition(
            session,
            entrypoint=entrypoint.command,
            work_id=manifest[0],
            timeout_seconds=args.timeout,
        )
        summary["operation_counts"]["gallery_dl_calls"] += 1
        summary["redacted_authentication_preflight"] = {"performed": True, **preflight}
        if not authenticated:
            _write_private_json(output_dir / "execution-summary.json", summary)
            raise PixivMetadataGateError("blocked_gallery_dl_redacted_authentication_preflight_failed")
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
        summary["operation_counts"]["gallery_dl_calls"] += sum(item.attempt_count for item in results)
        summary["result_state_counts"] = dict(sorted(Counter(item.state for item in results).items()))
        summary["remaining_distinct_work_count"] = len(pending_distinct_work_ids(session))
        summary["fixed_point_reached"] = summary["remaining_distinct_work_count"] == 0
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
