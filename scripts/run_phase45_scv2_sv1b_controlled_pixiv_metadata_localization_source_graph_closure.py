#!/usr/bin/env python3
"""SCV2-SV1B read-only candidate inventory and provider-gate preflight.

This phase-scoped operational runner deliberately stops before authentication
or acquisition.  It re-derives the finite Pixiv page/work manifests from the
accepted 12,000-media membership and emits only private, local evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services.pixiv_metadata_ingestion_service import (  # noqa: E402
    MIN_REQUEST_SPACING_SECONDS,
    manifest_scoped_outcome_key,
)
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as gallery_adapter  # noqa: E402
from scripts.run_pixiv_metadata_ingestion import validate_gallery_dl_profile  # noqa: E402
from scripts.run_phase45_scv2_sv1_controlled_scale_promotion_readiness import (  # noqa: E402
    CORE_SOURCE_TABLES,
    PROTECTED_TABLES,
    classify_pixiv_denominator,
    database_fingerprint,
    engine_for,
)

PHASE = "SCV2-SV1B"
BRANCH = "codex/scv2-sv1b-pixiv-metadata-localization-source-graph-closure"
ACCEPTED_MERGE = "46861489fa0b3b05ae917a99a3932897efd70365"
ACCEPTED_EVIDENCE_HEAD = "af073ca0ad2a9df9418cf072dc381d7b2c10216a"
ACCEPTED_MANIFEST_FINGERPRINT = "5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f"
ACCEPTED_SCALE_DB = "blombooru_scv2_sv1_controlled_scale_test_20260718"
ACCEPTED_DATABASES = (
    "blombooru_scv2_r2r_dryrun_test_20260710",
    "blombooru_scv2_ml1_acquisition_test_20260712",
    "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715",
    ACCEPTED_SCALE_DB,
    "blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1",
    "blombooru_scv2_sv1_rebuild_verification_test_20260718",
)
ACCEPTED_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness"
ACCEPTED_STORAGE = ROOT / ".local_test_storage/phase-4.5-scv2-sv1-controlled-scale"
DEFAULT_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-sv1b-pixiv-metadata-localization-source-graph-closure-20260719"
EXPECTED_MEDIA_COUNT = 12_000

COMPLETE_STATUSES = frozenset({"metadata_complete", "observed", "active", "accepted"})
TERMINAL_STATUSES = frozenset({"terminal_remote_unavailable"})
DEFERRED_STATUSES = frozenset({"deferred_nonblocking_source_page_mismatch"})


class SV1BPreflightError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_payload(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
    os.replace(temporary, path)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def _strict_test_database(database: str) -> bool:
    value = str(database or "").strip().casefold()
    return bool(value and "test" in value and value not in {"blombooru", "postgres", "template0", "template1"})


def canonical_work_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw.isdigit():
        raise SV1BPreflightError("pixiv_work_id_not_decimal")
    canonical = str(int(raw))
    if canonical == "0" or len(canonical) > 12:
        raise SV1BPreflightError("pixiv_work_id_out_of_range")
    return canonical


def validate_output_root(output: Path) -> Path:
    resolved = output.resolve()
    private_root = (ROOT / ".local_manifests").resolve()
    if private_root not in resolved.parents:
        raise SV1BPreflightError("private_output_root_escape")
    if resolved.exists():
        raise SV1BPreflightError("private_output_root_already_exists_ownership_unproven")
    return resolved


def validate_repository_and_inputs(output: Path) -> dict[str, Any]:
    branch = git("branch", "--show-current")
    if branch != BRANCH:
        raise SV1BPreflightError(f"wrong_branch:{branch}")
    if os.getenv("VIOLET_ENV") != "test":
        raise SV1BPreflightError("violet_env_not_test")
    if not _strict_test_database(ACCEPTED_SCALE_DB):
        raise SV1BPreflightError("accepted_scale_database_identity_invalid")
    summary = read_json(ACCEPTED_OUTPUT / "inventory-and-manifest-summary.json")
    manifest_fingerprint = str(summary["scale_manifest"]["manifest_fingerprint"])
    if manifest_fingerprint != ACCEPTED_MANIFEST_FINGERPRINT:
        raise SV1BPreflightError("accepted_manifest_fingerprint_mismatch")
    if int(summary["scale_manifest"]["selected_eligible_media_count"]) != EXPECTED_MEDIA_COUNT:
        raise SV1BPreflightError("accepted_manifest_media_count_mismatch")
    validate_output_root(output)
    return {
        "repository_root_fingerprint": sha256_payload(str(ROOT.resolve()).casefold()),
        "branch": branch,
        "head": git("rev-parse", "HEAD"),
        "accepted_merge_is_ancestor": subprocess.run(
            ["git", "merge-base", "--is-ancestor", ACCEPTED_MERGE, "HEAD"], cwd=ROOT, check=False
        ).returncode == 0,
        "accepted_evidence_head_is_ancestor": subprocess.run(
            ["git", "merge-base", "--is-ancestor", ACCEPTED_EVIDENCE_HEAD, "HEAD"], cwd=ROOT, check=False
        ).returncode == 0,
        "accepted_manifest_fingerprint": manifest_fingerprint,
        "accepted_scale_database": ACCEPTED_SCALE_DB,
        "output_root_validated_before_write": True,
    }


def outcome_for_pair(records: list[Mapping[str, Any]], work_id: str, page_index: int) -> str:
    exact = [
        row for row in records
        if str(row.get("source_work_id") or "") == work_id
        and row.get("source_page_index") is not None
        and int(row["source_page_index"]) == page_index
    ]
    statuses = {str(row.get("status") or "") for row in exact}
    if statuses & COMPLETE_STATUSES:
        return "accepted_metadata_complete"
    if statuses & TERMINAL_STATUSES:
        return "accepted_terminal_remote_unavailable"
    if statuses & DEFERRED_STATUSES:
        return "accepted_deferred_nonblocking_source_page_mismatch"
    same_work = {str(row.get("status") or "") for row in records if str(row.get("source_work_id") or "") == work_id}
    if same_work & DEFERRED_STATUSES:
        return "accepted_deferred_nonblocking_source_page_mismatch"
    return "unacquired"


def build_candidate_manifests() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    manifest = read_jsonl(ACCEPTED_OUTPUT / "scale-selection-manifest.jsonl")
    manifest_keys = [str(row["file_hash"]) for row in manifest]
    engine = engine_for(ACCEPTED_SCALE_DB)
    try:
        with engine.connect() as connection:
            media_rows = list(connection.execute(text(
                "SELECT id,hash,filename,path FROM blombooru_media ORDER BY hash"
            )).mappings())
            metadata_rows = list(connection.execute(text(
                "SELECT media_id,source_work_id,source_page_index,status "
                "FROM blombooru_source_metadata_records WHERE provider='pixiv'"
            )).mappings())
    finally:
        engine.dispose()

    media_by_hash = {str(row["hash"]): row for row in media_rows if row["hash"] is not None}
    if len(manifest_keys) != len(set(manifest_keys)) or set(manifest_keys) != set(media_by_hash):
        raise SV1BPreflightError("accepted_manifest_database_membership_mismatch")
    records_by_media: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in metadata_rows:
        if row["media_id"] is not None:
            records_by_media[int(row["media_id"])].append(row)

    pages: list[dict[str, Any]] = []
    non_candidate_count = 0
    classification_counts: Counter[str] = Counter()
    for media_hash in manifest_keys:
        media = media_by_hash[media_hash]
        classification, filename_ids, stored_ids = classify_pixiv_denominator(media["filename"], media["path"])
        classification_counts[classification] += 1
        if classification == "non_candidate":
            non_candidate_count += 1
            continue
        pairs = sorted({(canonical_work_id(row["work_id"]), int(row["page_index"])) for row in [*filename_ids, *stored_ids]})
        for work_id, page_index in pairs:
            pages.append({
                "provider": "pixiv",
                "stable_work_id": work_id,
                "requested_page_index": page_index,
                "media_stable_key": media_hash,
                "media_safe_label": sha256_payload({"media": media_hash}),
                "identity_classification": classification,
                "acquisition_state": outcome_for_pair(records_by_media[int(media["id"])], work_id, page_index),
                "checkpoint_key": manifest_scoped_outcome_key(
                    ACCEPTED_MANIFEST_FINGERPRINT, "pixiv", work_id, page_index
                ),
                "retry_count": 0,
                "last_request_time": None,
                "final_outcome": None,
            })

    works_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pages:
        works_by_id[row["stable_work_id"]].append(row)
    works = []
    for work_id, rows in sorted(works_by_id.items(), key=lambda item: int(item[0])):
        states = Counter(str(row["acquisition_state"]) for row in rows)
        work_state = next(iter(states)) if len(states) == 1 else "mixed_page_outcomes"
        works.append({
            "provider": "pixiv",
            "stable_work_id": work_id,
            "requested_local_page_indexes": sorted({int(row["requested_page_index"]) for row in rows}),
            "media_stable_keys": sorted({str(row["media_stable_key"]) for row in rows}),
            "acquisition_state": work_state,
            "checkpoint_key": sha256_payload({
                "manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
                "provider": "pixiv",
                "stable_work_id": work_id,
                "page_checkpoint_keys": sorted(str(row["checkpoint_key"]) for row in rows),
            }),
            "retry_count": 0,
            "last_request_time": None,
            "final_outcome": None,
        })

    candidate_media = {str(row["media_stable_key"]) for row in pages}
    outcome_counts = Counter(str(row["acquisition_state"]) for row in pages)
    exact_closed_media = {
        str(row["media_stable_key"]) for row in pages if row["acquisition_state"] != "unacquired"
    }
    media_id_by_hash = {str(row["hash"]): int(row["id"]) for row in media_rows if row["hash"] is not None}
    any_accepted_metadata_media = {
        media_hash for media_hash in candidate_media if records_by_media.get(media_id_by_hash[media_hash])
    }
    distinct_work_pages = {
        (str(row["stable_work_id"]), int(row["requested_page_index"])) for row in pages
    }
    summary = {
        "manifest_media_count": len(manifest_keys),
        "canonical_candidate_media_count": len(candidate_media),
        "explicit_non_candidate_media_count": non_candidate_count,
        "distinct_requested_page_count": len(pages),
        "page_media_manifest_row_count": len(pages),
        "distinct_work_page_count": len(distinct_work_pages),
        "distinct_work_count": len(works),
        "distinct_work_manifest_row_count": len(works),
        "classification_counts": dict(sorted(classification_counts.items())),
        "accepted_page_outcome_counts": dict(sorted(outcome_counts.items())),
        "candidate_media_with_any_accepted_metadata_record": len(any_accepted_metadata_media),
        "candidate_media_with_exact_page_closure": len(exact_closed_media),
        "candidate_media_with_accepted_but_non_exact_page_evidence": len(any_accepted_metadata_media - exact_closed_media),
        "candidate_media_requiring_acquisition": len(candidate_media - exact_closed_media),
        "candidate_accounting_passed": len(candidate_media) + non_candidate_count == len(manifest_keys),
        "page_manifest_fingerprint": sha256_payload(pages),
        "work_manifest_fingerprint": sha256_payload(works),
    }
    if not summary["candidate_accounting_passed"]:
        raise SV1BPreflightError("candidate_accounting_failed")
    return pages, works, summary


def immutable_input_fingerprints() -> dict[str, Any]:
    tables = tuple(dict.fromkeys((*PROTECTED_TABLES, "blombooru_tags", *CORE_SOURCE_TABLES)))
    databases = {database: database_fingerprint(database, tables) for database in ACCEPTED_DATABASES}
    original = ACCEPTED_STORAGE / "media/original"
    storage_rows = sorted((path.name, int(path.stat().st_size)) for path in original.iterdir() if path.is_file())
    return {
        "database_fingerprints": {database: value["fingerprint"] for database, value in databases.items()},
        "protected_tables": list(tables),
        "blombooru_tags_included": True,
        "storage_object_count": len(storage_rows),
        "storage_membership_fingerprint": sha256_payload(storage_rows),
        "accepted_manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
    }


def provider_gate_preflight() -> dict[str, Any]:
    entrypoint = gallery_adapter.probe_gallery_dl_entrypoint(None)
    profile = validate_gallery_dl_profile(entrypoint.command, timeout_seconds=30)
    rotation = str(os.getenv("VIOLET_CREDENTIAL_ROTATION_CONFIRMED") or "").strip().casefold() in {"1", "true", "yes"}
    fingerprints = [
        value.strip() for value in str(os.getenv("VIOLET_COMPROMISED_SECRET_SHA256") or "").replace(";", ",").split(",")
        if value.strip()
    ]
    fingerprint_shape_valid = bool(fingerprints) and all(
        len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)
        for value in fingerprints
    )
    passed = bool(
        profile["provider_profile_available"]
        and profile["configuration_status_command_passed"]
        and rotation
        and fingerprint_shape_valid
    )
    return {
        "metadata_only_command_semantics": {"dump_json": True, "no_download": True},
        "persistent_cross_process_spacing_required_seconds": MIN_REQUEST_SPACING_SECONDS,
        "provider_profile": profile,
        "credential_rotation_confirmation_present": rotation,
        "compromised_secret_fingerprint_count": len(fingerprints),
        "compromised_secret_fingerprint_shape_valid": fingerprint_shape_valid,
        "delimiter_aware_secret_scan_executed": False,
        "redacted_authentication_canary_executed": False,
        "provider_request_count": 0,
        "provider_attempt_count": 0,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    repository = validate_repository_and_inputs(output)
    immutable_before = immutable_input_fingerprints()
    pages, works, candidates = build_candidate_manifests()
    provider_gate = provider_gate_preflight()
    immutable_after = immutable_input_fingerprints()
    immutable_proof = {
        "before": immutable_before,
        "after": immutable_after,
        "unchanged": immutable_before == immutable_after,
        "database_write_count": 0,
        "storage_write_count": 0,
    }
    if not immutable_proof["unchanged"]:
        raise SV1BPreflightError("immutable_input_drift_detected")

    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "run-identity.json", {"phase": PHASE, **repository})
    write_jsonl(output / "candidate-page-media-manifest-private.jsonl", pages)
    write_jsonl(output / "distinct-work-acquisition-manifest-private.jsonl", works)
    write_json(output / "candidate-manifest-summary.json", candidates)
    write_json(output / "provider-hardening-preflight.json", provider_gate)
    write_json(output / "immutable-input-proof.json", immutable_proof)
    status = "provider_hardening_preflight_passed_auth_canary_pending" if provider_gate["passed"] else "blocked_sv1b_provider_authentication"
    result = {
        "phase": PHASE,
        "status": status,
        "candidate_manifest": candidates,
        "provider_hardening": provider_gate,
        "immutable_inputs_unchanged": immutable_proof["unchanged"],
        "active_blockers": [] if provider_gate["passed"] else ["nonwaived_provider_credential_hardening_inputs_missing"],
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "manual_acceptance_required": True,
        "manual_acceptance_status": "not_generated_provider_gate_blocked",
        "next_phase_started": False,
    }
    write_json(output / "preflight-result.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if provider_gate["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
