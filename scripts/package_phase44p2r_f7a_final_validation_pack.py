"""Package a clean Phase 4.4-P2R-F7a final validation pack.

This script is phase-scoped operational tooling. It re-materializes the final
candidate bundle from an existing eligible manifest and saved LLM checkpoints,
then verifies that the shipped summary is computed from that exact bundle.

It does not call LLM providers, write DB rows, create SourceConcept rows, or
write Entity/media_tags truth paths.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings  # noqa: E402
from app.services.source_name_candidate_extraction_service import (  # noqa: E402
    EXTRACTOR_VERSION,
    PHASE,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ExtractionResultBundle,
    SourceCandidateInputGroup,
    SourceNameCandidateExtractionError,
    build_extraction_summary,
    build_extraction_units,
    deterministic_bundle_for_unit,
    reattach_unit_bundles_to_records,
    stable_payload_hash,
    validate_extraction_record,
)

import run_phase44p2r_f7a_llm_source_name_candidates as runner  # noqa: E402


DEFAULT_SOURCE_DIR = (
    ROOT
    / ".local_manifests"
    / "phase-4.4p2r-f7a-llm-source-name-candidates-final-validation-pack-20260605T103334Z"
)
OUTPUT_PREFIX = "phase-4.4p2r-f7a-final-validation-pack"
PRIMARY_MODE = "primary_concurrent"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{runner.PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{runner.PHASE_SLUG}-summary.json"


class FinalPackError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(runner._coerce_json_safe(value), ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _write_text(path, runner._jsonl(rows))


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(command: Sequence[str]) -> str:
    return runner._git_value(command)


def _load_manifest(source_dir: Path) -> tuple[list[SourceCandidateInputGroup], dict[str, Any]]:
    payload = _read_json(source_dir / "input-manifest.json")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("groups"), list):
        raise FinalPackError("invalid_input_manifest")
    groups = [runner._group_from_dict(row) for row in payload["groups"] if isinstance(row, Mapping)]
    if not groups:
        raise FinalPackError("empty_input_manifest")
    return groups, dict(payload)


def _revalidate_manifest(groups: Sequence[SourceCandidateInputGroup], manifest_payload: Mapping[str, Any]) -> dict[str, Any]:
    max_records = int((manifest_payload.get("input_summary") or {}).get("max_records") or len(groups))
    max_unique_strings = int((manifest_payload.get("input_summary") or {}).get("max_unique_strings") or 3000)
    args = argparse.Namespace(max_records=max_records, max_unique_strings=max_unique_strings)
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        kept, summary = runner._revalidate_cached_manifest_groups(db, groups, args)
    finally:
        db.close()
        engine.dispose()
    if len(kept) != len(groups):
        raise FinalPackError(f"cached_manifest_revalidation_dropped_groups:{summary}")
    return summary


def _parsed_llm_record(row: Mapping[str, Any]) -> Mapping[str, Any]:
    bundle = row.get("bundle") if isinstance(row.get("bundle"), Mapping) else {}
    for llm_output in bundle.get("llm_outputs") or []:
        if not isinstance(llm_output, Mapping):
            continue
        parsed = llm_output.get("parsed_response")
        if not isinstance(parsed, Mapping):
            continue
        records = parsed.get("records")
        if isinstance(records, list) and records and isinstance(records[0], Mapping):
            return records[0]
    raise FinalPackError(f"missing_parsed_llm_record:{row.get('extraction_key')}")


def _unit_bundle_from_saved_row(
    *,
    unit,
    saved_row: Mapping[str, Any],
    run_id: str,
    run_label: str,
    provider_mode: str,
) -> ExtractionResultBundle:
    if not unit.llm_required:
        return deterministic_bundle_for_unit(
            unit,
            run_id=runner._mode_run_id(run_id, provider_mode),
            run_label=run_label,
        )
    try:
        verdict, candidates, rejected, meta, ambiguous = validate_extraction_record(_parsed_llm_record(saved_row), unit.unit_group)
        validation_failures: tuple[dict[str, Any], ...] = ()
    except SourceNameCandidateExtractionError as exc:
        raise FinalPackError(f"saved_llm_output_no_longer_valid:{unit.extraction_key}:{exc}") from exc
    bundle_row = saved_row.get("bundle") if isinstance(saved_row.get("bundle"), Mapping) else {}
    summary = build_extraction_summary(
        groups=[unit.unit_group],
        record_verdicts=[verdict],
        candidates=candidates,
        rejected_tags=rejected,
        meta_tags=meta,
        ambiguous_items=ambiguous,
        llm_counters=dict((bundle_row.get("summary") or {}).get("llm") or {}),
        validation_failures=validation_failures,
    )
    return ExtractionResultBundle(
        run_id=runner._mode_run_id(run_id, provider_mode),
        run_label=run_label,
        groups=(unit.unit_group,),
        record_verdicts=(verdict,),
        candidates=candidates,
        rejected_tags=rejected,
        meta_tags=meta,
        ambiguous_items=ambiguous,
        llm_inputs=tuple(bundle_row.get("llm_inputs") or ()),
        llm_outputs=tuple(bundle_row.get("llm_outputs") or ()),
        validation_failures=validation_failures,
        summary=summary,
    )


def _rematerialize_mode_result(
    *,
    groups: Sequence[SourceCandidateInputGroup],
    old_mode_result: Mapping[str, Any],
    run_id: str,
    run_label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    units, unit_summary = build_extraction_units(groups)
    saved_rows = {
        str(row.get("extraction_key")): row
        for row in old_mode_result.get("rows") or []
        if isinstance(row, Mapping) and row.get("extraction_key")
    }
    if len(saved_rows) != len(units):
        raise FinalPackError(f"saved_row_count_mismatch:saved={len(saved_rows)} units={len(units)}")
    provider_mode = str(old_mode_result.get("provider_mode") or PRIMARY_MODE)
    provider_summary = dict(old_mode_result.get("provider_summary") or {})
    old_summary = dict(old_mode_result.get("summary") or {})
    unit_bundles: dict[str, ExtractionResultBundle] = {}
    rows: list[dict[str, Any]] = []
    for unit in units:
        saved_row = saved_rows.get(unit.extraction_key)
        if saved_row is None:
            raise FinalPackError(f"missing_saved_unit_row:{unit.extraction_key}")
        bundle = _unit_bundle_from_saved_row(
            unit=unit,
            saved_row=saved_row,
            run_id=run_id,
            run_label=run_label,
            provider_mode=provider_mode,
        )
        unit_bundles[unit.extraction_key] = bundle
        verdict = bundle.record_verdicts[0].extraction_verdict if bundle.record_verdicts else "extraction_error_terminal"
        row = dict(saved_row)
        row.update(
            {
                "bundle": runner._bundle_to_json(bundle),
                "candidate_count": len(bundle.candidates),
                "extraction_verdict": verdict,
                "validation_failure_count": len(bundle.validation_failures),
                "input_payload_hash": runner.group_input_payload_hash(unit.unit_group),
                "rematerialized_from_saved_llm_output": True,
            }
        )
        rows.append(row)
    record_bundle = reattach_unit_bundles_to_records(
        groups,
        units,
        unit_bundles,
        run_id=runner._mode_run_id(run_id, provider_mode),
        run_label=run_label,
    )
    record_result = {"bundle": runner._bundle_to_json(record_bundle)}
    summary = runner._mode_summary(
        provider_mode,
        [record_result],
        provider_summary,
        int(old_summary.get("concurrency") or 1),
    )
    timing_keys = (
        "group_count",
        "unique_string_count",
        "total_wall_time_seconds",
        "llm_wall_time_seconds",
        "average_seconds",
        "p50_seconds",
        "p95_seconds",
        "chunk_count",
        "retry_count",
        "raw_string_occurrences_total",
        "unique_extraction_units_total",
        "llm_calls_attempted",
        "llm_calls_avoided_by_dedupe",
        "inflight_dedupe_hits",
        "deterministic_resolved_units",
        "llm_required_units",
        "cache_hits",
        "unit_status_counts",
        "completed_unit_count",
        "terminal_error_count",
        "retryable_error_count",
    )
    summary.update({key: old_summary.get(key) for key in timing_keys if key in old_summary})
    summary.update({key: value for key, value in unit_summary.items() if key not in {"top_repeated_units"}})
    return {
        "provider_mode": provider_mode,
        "provider_summary": provider_summary,
        "rows": rows,
        "record_bundle": runner._bundle_to_json(record_bundle),
        "summary": summary,
    }, unit_summary


def _primary_rows(mode_result: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    bundle = mode_result.get("record_bundle") if isinstance(mode_result.get("record_bundle"), Mapping) else {}
    values = bundle.get(key)
    return [dict(row) for row in values] if isinstance(values, list) else []


def _rejected_general_meta(mode_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [{"artifact_row_type": "rejected_tag", **row} for row in _primary_rows(mode_result, "rejected_tags")]
    rows.extend({"artifact_row_type": "meta_tag", **row} for row in _primary_rows(mode_result, "meta_tags"))
    return rows


def _make_artifact_summary(output_dir: Path, zip_path: Path) -> dict[str, str]:
    rel_output = str(output_dir.resolve().relative_to(ROOT)).replace("\\", "/")
    rel_zip = str(zip_path.resolve().relative_to(ROOT)).replace("\\", "/")
    return {
        "artifact_dir": rel_output,
        "final_validation_zip": rel_zip,
        "candidate_bundle_jsonl": f"{rel_output}/candidate-bundle.jsonl",
        "record_verdicts_jsonl": f"{rel_output}/record-verdicts.jsonl",
        "quality_counters_json": f"{rel_output}/quality-counters.json",
        "artifact_consistency_check_json": f"{rel_output}/artifact-consistency-check.json",
        "provider_comparison_csv": f"{rel_output}/provider-comparison-summary.csv",
        "provider_comparison_json": f"{rel_output}/provider-comparison-summary.json",
        "checkpoint_status_json": f"{rel_output}/run-checkpoint-status.json",
        "progress_events_jsonl": f"{rel_output}/run-progress-events.jsonl",
        "public_report_md": str(PUBLIC_REPORT_MD.resolve().relative_to(ROOT)).replace("\\", "/"),
        "public_report_json": str(PUBLIC_REPORT_JSON.resolve().relative_to(ROOT)).replace("\\", "/"),
    }


def _readme(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# F7a final validation pack",
            "",
            "This local private pack is the source of truth for the final F7a candidate-pool validation.",
            "",
            f"- Run ID: `{summary['run_id']}`",
            f"- Manifest hash: `{summary['input_summary'].get('manifest_hash')}`",
            f"- Prompt version: `{summary['prompt_version']}`",
            f"- Validated code head: `{summary['validated_code_head_sha']}`",
            f"- LLM rerun in this packaging step: `{summary.get('llm_rerun')}`",
            "",
            "Primary machine-readable artifacts are JSON/JSONL. CSV files are secondary human-readable exports.",
            "No fallback-only or historical partial artifacts are included in this pack.",
        ]
    )


def _checksums(output_dir: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            rows[str(path.relative_to(output_dir)).replace("\\", "/")] = _sha256_file(path)
    return rows


def _zip_dir(output_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(output_dir)).replace("\\", "/"))


def package_final_validation_pack(source_dir: Path, output_root: Path, *, write_public_report: bool = True) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    if not source_dir.exists():
        raise FinalPackError(f"source_dir_missing:{source_dir}")
    old_details = _read_json(source_dir / "details.json")
    old_summary = dict(old_details.get("summary") or {})
    groups, manifest_payload = _load_manifest(source_dir)
    revalidation_summary = _revalidate_manifest(groups, manifest_payload)
    if old_summary.get("prompt_version") != PROMPT_VERSION:
        raise FinalPackError("prompt_version_mismatch_requires_rerun")
    if manifest_payload.get("prompt_version") != PROMPT_VERSION:
        raise FinalPackError("manifest_prompt_version_mismatch")
    old_modes = [
        row
        for row in old_details.get("mode_results") or []
        if isinstance(row, Mapping) and row.get("provider_mode") == PRIMARY_MODE
    ]
    if not old_modes:
        raise FinalPackError("primary_concurrent_mode_missing")
    run_id = str(old_summary.get("run_id") or "")
    if not run_id:
        raise FinalPackError("run_id_missing")
    run_label = str((old_modes[0].get("record_bundle") or {}).get("run_label") or "f7a_llm_source_name_candidate_extraction_rework")
    mode_result, unit_summary = _rematerialize_mode_result(
        groups=groups,
        old_mode_result=old_modes[0],
        run_id=run_id,
        run_label=run_label,
    )
    branch = _git_value(["git", "branch", "--show-current"])
    head_sha = _git_value(["git", "rev-parse", "HEAD"])
    manifest_hash = str(manifest_payload.get("manifest_hash") or stable_payload_hash([asdict(group) for group in groups]))
    input_summary = {
        **dict((manifest_payload.get("input_summary") or {})),
        **revalidation_summary,
        **unit_summary,
        "manifest_hash": manifest_hash,
        "final_pack_manifest_revalidated": True,
    }
    zip_stem = f"{OUTPUT_PREFIX}-{run_id}-{head_sha[:12]}"
    output_dir = (output_root / zip_stem).resolve()
    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_summary = _make_artifact_summary(output_dir, zip_path)
    provider_preflight = dict(old_summary.get("provider_preflight") or {"llm_preflight_calls": old_summary.get("safety", {}).get("llm_preflight_calls", 0)})
    db_write_summary = {
        **dict(old_summary.get("db_write_summary") or {}),
        "apply_db": False,
        "db_rows_written_in_final_packaging": 0,
        "forbidden_truth_table_write_count": 0,
    }
    timing = dict(old_summary.get("timing") or {})
    summary = runner._public_summary(
        run_id=run_id,
        branch=branch,
        head_sha=head_sha,
        db_identity=dict(old_summary.get("db_identity") or {}),
        input_summary=input_summary,
        mode_results=[mode_result],
        provider_preflight=provider_preflight,
        db_write_summary=db_write_summary,
        artifact_summary=artifact_summary,
        timing=timing,
    )
    candidates = _primary_rows(mode_result, "candidates")
    verdicts = _primary_rows(mode_result, "record_verdicts")
    rejected_general_meta = _rejected_general_meta(mode_result)
    ambiguous = _primary_rows(mode_result, "ambiguous_items")
    validation_failures = _primary_rows(mode_result, "validation_failures")
    quality = runner.final_quality_counters(
        candidates=candidates,
        record_verdicts=verdicts,
        rejected_general_meta_rows=rejected_general_meta,
        ambiguous_items=ambiguous,
        validation_failures=validation_failures,
    )
    summary.update(
        {
            "llm_rerun": False,
            "rematerialized_from_run_id": run_id,
            "rematerialized_from_source_dir": str(source_dir.relative_to(ROOT)).replace("\\", "/"),
            "old_fallback_only_artifacts_excluded": True,
            "fallback_artifacts_included": False,
            "prompt_changed": False,
            "prompt_diff": "unchanged",
            "final_pack_generated_at": datetime.now(timezone.utc).isoformat(),
            "quality_counters": quality,
        }
    )
    public_redaction = runner._public_redaction_check(summary)
    public_redaction_status = "pass" if public_redaction.splitlines()[0].endswith("pass") else "fail"
    consistency = runner.final_artifact_consistency_check(
        summary=summary,
        quality_counters=quality,
        run_id=run_id,
        head_sha=head_sha,
        prompt_version=PROMPT_VERSION,
        manifest_hash=manifest_hash,
        public_redaction_status=public_redaction_status,
    )
    summary["artifact_consistency_check"] = consistency["status"]
    summary["readiness"]["f7a_mergeable"] = bool(summary["readiness"]["f7a_mergeable"] and consistency["status"] == "pass")
    if not summary["readiness"]["f7a_mergeable"]:
        summary["readiness"]["reason"] = "final_artifact_bundle_has_blockers_or_consistency_failure"

    _write_text(output_dir / "README.md", _readme(summary))
    _write_json(output_dir / "manifest.json", {**manifest_payload, "final_pack_metadata": {"run_id": run_id, "head_sha": head_sha}})
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "quality-counters.json", quality)
    _write_json(output_dir / "artifact-consistency-check.json", consistency)
    _write_jsonl(output_dir / "candidate-bundle.jsonl", candidates)
    _write_jsonl(output_dir / "record-verdicts.jsonl", verdicts)
    _write_jsonl(output_dir / "rejected-general-meta.jsonl", rejected_general_meta)
    _write_jsonl(output_dir / "ambiguous-needs-review.jsonl", ambiguous)
    _write_jsonl(output_dir / "no-name-records.jsonl", [row for row in verdicts if row.get("no_name_reason")])
    _write_jsonl(output_dir / "popularity-prefix-extractions.jsonl", [row for row in candidates if row.get("extraction_action") == "popularity_suffix_stripped"])
    _write_jsonl(output_dir / "pixiv-title-candidate-review.jsonl", runner._pixiv_title_candidate_rows(candidates))
    _write_jsonl(output_dir / "role-guard-review.jsonl", runner._role_guard_rows(candidates))
    _write_jsonl(output_dir / "false-positive-guard-review.jsonl", runner._false_positive_guard_rows(candidates))
    _write_jsonl(output_dir / "extraction-errors.jsonl", [row for row in verdicts if str(row.get("extraction_verdict", "")).startswith("extraction_error")])
    _write_text(output_dir / "name-candidates-primary.csv", runner._csv(candidates))
    _write_text(output_dir / "record-verdicts-primary.csv", runner._csv(verdicts))
    _write_text(output_dir / "rejected-general-meta.csv", runner._csv(rejected_general_meta))
    _write_text(output_dir / "provider-comparison-summary.csv", runner._csv(summary["provider_comparison"]))
    _write_json(output_dir / "provider-comparison-summary.json", {"rows": summary["provider_comparison"]})
    _write_text(output_dir / "prompt-version-and-rules.md", runner._prompt_version_and_rules())
    _write_text(output_dir / "prompt-diff.md", "# Prompt diff\n\nPrompt unchanged. Reused compact v3 outputs; no LLM rerun was performed.\n")
    if (source_dir / "prompt-sample-analysis.md").exists():
        shutil.copyfile(source_dir / "prompt-sample-analysis.md", output_dir / "prompt-sample-analysis.md")
    else:
        _write_text(output_dir / "prompt-sample-analysis.md", "# Prompt sample analysis\n\nNo prior prompt sample analysis artifact found.\n")
    for name in ("run-checkpoint-status.json", "run-progress-events.jsonl"):
        if (source_dir / name).exists():
            shutil.copyfile(source_dir / name, output_dir / name)
        else:
            _write_text(output_dir / name, "{}\n" if name.endswith(".json") else "")
    _write_text(output_dir / "public-redaction-check.txt", public_redaction)
    _write_text(output_dir / "reviewer-fix-summary.md", runner._reviewer_fix_summary(summary))
    _write_json(output_dir / "checksums.json", _checksums(output_dir))
    _zip_dir(output_dir, zip_path)
    summary["artifacts"]["final_validation_zip"] = str(zip_path.resolve().relative_to(ROOT)).replace("\\", "/")
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "checksums.json", _checksums(output_dir))
    _zip_dir(output_dir, zip_path)
    if write_public_report:
        _write_json(PUBLIC_REPORT_JSON, summary)
        _write_text(PUBLIC_REPORT_MD, runner._markdown_report(summary))
    return {
        "status": "pass",
        "output_dir": str(output_dir),
        "zip_path": str(zip_path),
        "run_id": run_id,
        "manifest_hash": manifest_hash,
        "head_sha": head_sha,
        "quality_counters": quality,
        "artifact_consistency_check": consistency["status"],
        "f7a_mergeable": summary["readiness"]["f7a_mergeable"],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package F7a final validation artifacts without LLM calls.")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR), help="Existing primary validation artifact directory.")
    parser.add_argument("--output-root", default=str(ROOT / ".local_manifests"), help="Local artifact output root.")
    parser.add_argument("--skip-public-report", action="store_true", help="Do not update committed public report files.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = package_final_validation_pack(
        Path(args.source_dir),
        Path(args.output_root),
        write_public_report=not args.skip_public_report,
    )
    print(json.dumps(runner._coerce_json_safe(result), ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
