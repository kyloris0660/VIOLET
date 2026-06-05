"""Run Phase 4.5-SC1 source concept resolver validation.

This runner is phase-scoped operational tooling. It can apply the additive
SourceConcept schema, optionally backfill the final F7a candidate artifact into
the existing F7a source-name-candidate tables without LLM/API/provider calls,
run the deterministic multi-source resolver, and generate the final validation
pack.

It does not create Entity truth, mutate media_tags, run provider calls, or
implement search/UI integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import check_and_migrate_schema  # noqa: E402
from app.services.source_concept_resolver_service import (  # noqa: E402
    RESOLVER_VERSION,
    SOURCE_CONCEPT_SCHEMA_VERSION,
    build_artifact_consistency_check,
    canonical_key,
    import_f7a_final_pack_candidates,
    result_to_artifact_payload,
    run_source_concept_resolution,
    utc_now_iso,
)

PHASE_SLUG = "phase-4.5-sc1-source-concept-resolver-core"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
F7A_PUBLIC_SUMMARY = ROOT / "docs" / "reports" / "phase-4.4p2r-f7a-llm-source-name-candidates-summary.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    write_text(path, "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) for row in rows) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksums(output_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            result[str(path.relative_to(output_dir)).replace("\\", "/")] = sha256_file(path)
    return result


def zip_directory(output_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir))


def git_value(command: Sequence[str]) -> str:
    return subprocess.check_output(command, cwd=ROOT, text=True, encoding="utf-8").strip()


def default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{PHASE_SLUG}-{stamp}-{uuid4().hex[:8]}"


def find_f7a_final_pack(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else ROOT / path
    if F7A_PUBLIC_SUMMARY.exists():
        summary = read_json(F7A_PUBLIC_SUMMARY)
        final_pack = (
            summary.get("final_pack")
            or summary.get("final_validation_pack")
            or summary.get("artifacts", {}).get("artifact_dir")
            or summary.get("artifacts", {}).get("final_validation_pack_dir")
            or summary.get("artifacts", {}).get("final_pack_dir")
        )
        if isinstance(final_pack, str) and final_pack:
            path = Path(final_pack)
            candidate = path if path.is_absolute() else ROOT / path
            if candidate.exists():
                return candidate
        zip_path = (
            summary.get("final_pack_zip")
            or summary.get("artifacts", {}).get("final_validation_pack_zip")
            or summary.get("artifacts", {}).get("final_pack_zip")
        )
        if isinstance(zip_path, str) and zip_path:
            candidate_zip = Path(zip_path)
            candidate_zip = candidate_zip if candidate_zip.is_absolute() else ROOT / candidate_zip
            if candidate_zip.exists():
                sibling = candidate_zip.with_suffix("")
                if sibling.exists():
                    return sibling
    candidates = sorted(
        (ROOT / ".local_manifests").glob("phase-4.4p2r-f7a-final-validation-pack*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0].parent
    return None


def f7a_run_id_from_pack(pack_dir: Path | None) -> str | None:
    if not pack_dir:
        return None
    summary_path = pack_dir / "summary.json"
    if not summary_path.exists():
        return None
    summary = read_json(summary_path)
    return str(summary.get("run_id") or "") or None


def public_redaction_check(public_paths: Sequence[Path]) -> dict[str, Any]:
    forbidden_fragments = [
        "C:\\Users\\kyloris",
        "C:/Users/kyloris",
        "\\\\192.168.",
        "Z:\\",
        "source-signals.jsonl",
        "raw_value",
        "candidate-bundle.jsonl",
    ]
    findings: list[dict[str, str]] = []
    for path in public_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for fragment in forbidden_fragments:
            if fragment in text:
                findings.append({"path": str(path.relative_to(ROOT)), "fragment": fragment})
    return {
        "checked_at": utc_now_iso(),
        "passed": not findings,
        "public_paths": [str(path.relative_to(ROOT)) for path in public_paths],
        "forbidden_fragment_findings": findings,
        "note": "Public report is summary/count-only; private names stay in .local_manifests pack.",
    }


def concept_case_review(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    concepts = payload["concepts"]
    aliases = payload["aliases"]
    aliases_by_concept: dict[str, set[str]] = {}
    status_by_concept = {row["concept_key"]: row["status"] for row in concepts}
    for alias in aliases:
        aliases_by_concept.setdefault(alias["concept_key"], set()).add(alias["alias_key"])

    positive_cases = [
        {
            "case_id": "kamisato_ayaka_multi_origin",
            "required_any_keys": [canonical_key("神里綾華"), canonical_key("Kamisato Ayaka"), canonical_key("kamisato_ayaka")],
            "expected": "aliases should converge when work/source context supports them",
        },
        {
            "case_id": "barbara_genshin_context",
            "required_any_keys": [canonical_key("バーバラ"), canonical_key("Barbara"), canonical_key("barbara_(genshin_impact)")],
            "expected": "short English name should require Genshin/work context",
        },
        {
            "case_id": "mona_genshin_context",
            "required_any_keys": [canonical_key("モナ"), canonical_key("Mona"), canonical_key("mona_(genshin_impact)")],
            "expected": "short English name should require Genshin/work context",
        },
        {
            "case_id": "ganyu_genshin_context",
            "required_any_keys": [canonical_key("甘雨"), canonical_key("Ganyu"), canonical_key("ganyu_(genshin_impact)")],
            "expected": "multi-language aliases should converge with work context",
        },
        {
            "case_id": "nilou_multilingual_context",
            "required_any_keys": [canonical_key("ニィロウ"), canonical_key("Nilou"), canonical_key("妮露"), canonical_key("nilou_(genshin_impact)")],
            "expected": "multi-language aliases should converge with work context",
        },
        {
            "case_id": "provider_structured_field_case",
            "required_origin": "provider_structured_field",
            "expected": "structured provider fields should be present as SourceConcept signals when data exists",
        },
    ]
    positive_rows: list[dict[str, Any]] = []
    for case in positive_cases:
        matched: list[dict[str, Any]] = []
        if "required_origin" in case:
            matched = [
                {"concept_key": alias["concept_key"], "alias_key": alias["alias_key"], "status": status_by_concept.get(alias["concept_key"])}
                for alias in aliases
                if alias["alias_role"] == case["required_origin"]
            ][:20]
        else:
            required = set(case["required_any_keys"])
            for concept_key, alias_keys in aliases_by_concept.items():
                if alias_keys.intersection(required):
                    matched.append(
                        {
                            "concept_key": concept_key,
                            "matched_keys": sorted(alias_keys.intersection(required)),
                            "concept_status": status_by_concept.get(concept_key),
                        }
                    )
        positive_rows.append({**case, "matched": bool(matched), "matches": matched[:10]})

    negative_keys = {canonical_key(value) for value in ["Mona", "Nicole", "2B", "Barbara"]}
    negative_rows: list[dict[str, Any]] = []
    for concept_key, alias_keys in aliases_by_concept.items():
        if not alias_keys.intersection(negative_keys):
            continue
        has_work_context = ":work:" in concept_key
        status = status_by_concept.get(concept_key)
        negative_rows.append(
            {
                "concept_key": concept_key,
                "matched_short_keys": sorted(alias_keys.intersection(negative_keys)),
                "concept_status": status,
                "has_work_context": has_work_context,
                "passes_guard": has_work_context or status != "active",
                "expected": "short ambiguous names without work context must not become active overmerges",
            }
        )
    if not negative_rows:
        negative_rows.append(
            {
                "concept_key": None,
                "matched_short_keys": [],
                "concept_status": None,
                "has_work_context": False,
                "passes_guard": True,
                "expected": "no short-name guard candidates found in current DB",
            }
        )
    return positive_rows, negative_rows


def write_artifacts(
    *,
    output_dir: Path,
    payload: Mapping[str, Any],
    inventory: Mapping[str, Any],
    persistence: Mapping[str, Any],
    f7a_import: Mapping[str, Any] | None,
    run_id: str,
    apply_db: bool,
) -> tuple[dict[str, Any], Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    positive_rows, negative_rows = concept_case_review(payload)
    consistency = build_artifact_consistency_check(payload, persistence)
    git_head = git_value(["git", "rev-parse", "HEAD"])
    git_branch = git_value(["git", "branch", "--show-current"])
    summary = {
        "phase": PHASE_SLUG,
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "resolver_version": RESOLVER_VERSION,
        "schema_version": SOURCE_CONCEPT_SCHEMA_VERSION,
        "apply_db": apply_db,
        "git": {"branch": git_branch, "head": git_head},
        "f7a_final_pack_import": f7a_import,
        "resolver_summary": payload["summary"],
        "persistence": persistence,
        "artifact_consistency": consistency,
        "positive_case_summary": {
            "total": len(positive_rows),
            "matched": sum(1 for row in positive_rows if row.get("matched")),
        },
        "negative_case_summary": {
            "total": len(negative_rows),
            "passed": sum(1 for row in negative_rows if row.get("passes_guard")),
        },
        "no_llm_or_provider_calls": True,
        "no_truth_boundary": {
            "entity_tables_written": False,
            "media_tags_mutated": False,
            "search_ui_integration": False,
            "sc2_started": False,
        },
    }

    write_text(
        output_dir / "README.md",
        "\n".join(
            [
                "# Phase 4.5-SC1 Source Concept Resolver Core Validation Pack",
                "",
                "Machine-readable JSON/JSONL files are authoritative.",
                "This pack contains unconfirmed source-layer concepts only.",
                "It does not contain Entity truth, confirmed assignments, or search/UI integration.",
                "",
            ]
        ),
    )
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "source-signal-inventory.json", inventory)
    write_jsonl(output_dir / "source-signals.jsonl", payload["signals"])
    write_jsonl(output_dir / "source-concepts.jsonl", payload["concepts"])
    write_jsonl(output_dir / "source-concept-aliases.jsonl", payload["aliases"])
    write_jsonl(output_dir / "source-concept-evidence.jsonl", payload["evidence"])
    write_jsonl(output_dir / "candidate-links.jsonl", payload["links"])
    write_jsonl(output_dir / "concept-search-index-preview.jsonl", payload["search_index"])
    write_jsonl(output_dir / "positive-case-review.jsonl", positive_rows)
    write_jsonl(output_dir / "negative-case-review.jsonl", negative_rows)
    write_jsonl(output_dir / "ambiguous-needs-review.jsonl", payload["ambiguous_links"])
    write_jsonl(output_dir / "rejected-link-review.jsonl", payload["rejected_signals"])
    write_jsonl(output_dir / "merge-candidate-review.jsonl", payload["merge_candidates"])
    write_json(output_dir / "resolver-run-summary.json", {**payload["summary"], "persistence": persistence})
    write_json(output_dir / "artifact-consistency-check.json", consistency)
    write_text(
        output_dir / "manual-review-guide.md",
        "\n".join(
            [
                "# Manual Review Guide",
                "",
                "Review `ambiguous-needs-review.jsonl` first for guarded short-name/context cases.",
                "Review `merge-candidate-review.jsonl` for same-surface aliases across contexts.",
                "Use `source-concept-evidence.jsonl` to inspect provenance before any future manual Entity promotion.",
                "Do not treat any SourceConcept row as confirmed Entity truth.",
                "",
            ]
        ),
    )
    write_text(
        output_dir / "public-redaction-check.txt",
        "pending; public report redaction is checked after report generation\n",
    )
    write_json(output_dir / "checksums.json", checksums(output_dir))
    zip_path = Path(f"{output_dir}.zip")
    zip_directory(output_dir, zip_path)
    return summary, zip_path


def write_public_report(summary: Mapping[str, Any], inventory: Mapping[str, Any], zip_path: Path) -> None:
    resolver_summary = summary["resolver_summary"]
    f7a_import = summary.get("f7a_final_pack_import") or {}
    inventory_counts = {
        key: value.get("count", value.get("total", value.get("structured_record_count", 0)))
        for key, value in inventory.get("sources", {}).items()
        if isinstance(value, Mapping)
    }
    report = "\n".join(
        [
            "# Phase 4.5-SC1 Source Concept Resolver Core",
            "",
            "## Summary",
            "",
            "Phase 4.5-SC1 implements the provider-neutral source-layer SourceConcept resolver core. "
            "It groups multi-source name, tag, assertion, observation, alias, and structured provider signals into unconfirmed concepts.",
            "",
            "This report is public/redacted: it contains counts and safety results only, not local raw names or private source values.",
            "",
            "## Scope",
            "",
            "- Included: additive SourceConcept schema, SourceConceptSignal adapters, deterministic resolver, run ledger, evidence/link/search-preview tables, validation pack.",
            "- Not included: Entity truth, EntityAlias truth, MediaEntityAssignment, media_tags mutation, full search/UI integration, manual promotion UI.",
            "",
            "## Counts",
            "",
            f"- Signals: {resolver_summary.get('signal_count')}",
            f"- Concepts: {resolver_summary.get('concept_count')} ({resolver_summary.get('concept_counts_by_status')})",
            f"- Links: {resolver_summary.get('link_count')} ({resolver_summary.get('link_counts_by_status')})",
            f"- Aliases: {resolver_summary.get('alias_count')}",
            f"- Evidence rows: {resolver_summary.get('evidence_count')}",
            f"- Search preview rows: {resolver_summary.get('search_index_preview_count')}",
            "",
            "## Source Signal Inventory",
            "",
            json.dumps(inventory_counts, ensure_ascii=True, sort_keys=True),
            "",
            "## F7a Final Pack Backfill Audit",
            "",
            f"- Run ID: `{f7a_import.get('run_id')}`",
            f"- Candidate bundle count: {f7a_import.get('candidate_bundle_count')}",
            f"- Existing DB count before scoped import: {f7a_import.get('existing_db_candidate_count_for_run')}",
            f"- Import needed: {f7a_import.get('needs_import')}",
            f"- LLM/provider calls: false",
            "",
            "## Safety",
            "",
            f"- Forbidden truth table write count: {summary.get('persistence', {}).get('forbidden_truth_table_write_count')}",
            "- Entity truth writes: false",
            "- media_tags mutation: false",
            "- Search/UI integration: false",
            "- Phase 4.5-SC2 started: false",
            "",
            "## Validation Pack",
            "",
            f"- Zip artifact: `{zip_path.name}`",
            "- Primary validation format: JSON/JSONL.",
            "",
        ]
    )
    write_text(PUBLIC_REPORT_MD, report)
    public_summary = {
        "phase": PHASE_SLUG,
        "run_id": summary["run_id"],
        "generated_at": summary["generated_at"],
        "resolver_summary": resolver_summary,
        "inventory_counts": inventory_counts,
        "artifact_consistency": summary["artifact_consistency"],
        "positive_case_summary": summary["positive_case_summary"],
        "negative_case_summary": summary["negative_case_summary"],
        "f7a_final_pack_import": {
            "run_id": f7a_import.get("run_id"),
            "candidate_bundle_count": f7a_import.get("candidate_bundle_count"),
            "existing_db_candidate_count_for_run": f7a_import.get("existing_db_candidate_count_for_run"),
            "needs_import": f7a_import.get("needs_import"),
            "llm_or_provider_calls": False,
        },
        "zip_artifact_name": zip_path.name,
        "forbidden_truth_table_write_count": summary.get("persistence", {}).get("forbidden_truth_table_write_count"),
        "no_llm_or_provider_calls": True,
        "public_redacted": True,
    }
    write_json(PUBLIC_REPORT_JSON, public_summary)


def update_public_redaction_artifact(output_dir: Path) -> dict[str, Any]:
    redaction = public_redaction_check([PUBLIC_REPORT_MD, PUBLIC_REPORT_JSON])
    write_text(
        output_dir / "public-redaction-check.txt",
        json.dumps(redaction, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
    )
    write_json(output_dir / "checksums.json", checksums(output_dir))
    zip_path = Path(f"{output_dir}.zip")
    zip_directory(output_dir, zip_path)
    return redaction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--apply-db", action="store_true")
    parser.add_argument("--apply-f7a-final-pack", action="store_true")
    parser.add_argument("--f7a-final-pack-dir", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / ".local_manifests" / f"{PHASE_SLUG}-{args.run_id}"
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    check_and_migrate_schema(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        f7a_pack_dir = find_f7a_final_pack(args.f7a_final_pack_dir)
        f7a_import = None
        f7a_run_id = f7a_run_id_from_pack(f7a_pack_dir)
        if f7a_pack_dir and args.apply_f7a_final_pack:
            f7a_import = import_f7a_final_pack_candidates(db, pack_dir=f7a_pack_dir, apply=args.apply_db)
            f7a_run_id = f7a_import.get("run_id") or f7a_run_id
        elif f7a_pack_dir:
            f7a_import = {
                "run_id": f7a_run_id,
                "pack_dir": str(f7a_pack_dir),
                "apply": False,
                "needs_import": None,
                "skipped": True,
                "reason": "apply_f7a_final_pack_not_requested",
            }
        result, inventory, persistence = run_source_concept_resolution(
            db,
            run_id=args.run_id,
            f7a_run_id=f7a_run_id,
            apply=args.apply_db,
        )
    finally:
        db.close()
    payload = result_to_artifact_payload(result)
    summary, zip_path = write_artifacts(
        output_dir=output_dir,
        payload=payload,
        inventory=inventory,
        persistence=persistence,
        f7a_import=f7a_import,
        run_id=args.run_id,
        apply_db=args.apply_db,
    )
    write_public_report(summary, inventory, zip_path)
    redaction = update_public_redaction_artifact(output_dir)
    final_summary = {
        **summary,
        "output_dir": str(output_dir),
        "zip_path": str(zip_path),
        "public_report_md": str(PUBLIC_REPORT_MD),
        "public_report_json": str(PUBLIC_REPORT_JSON),
        "public_redaction_check": redaction,
    }
    print(json.dumps(final_summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
