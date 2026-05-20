#!/usr/bin/env python3
"""Dry-run the classification-first E2E workflow contract.

Phase 3.8b is read-only.  This CLI writes privacy-safe reports and refuses any
execute request.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, TextIO


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.classification_first_workflow import (  # noqa: E402
    DEFAULT_SOURCE_LABEL,
    NULL_POLICY_HARD_FAIL,
    WorkflowScope,
    build_dry_run_report,
    collect_mutation_snapshot,
    write_json_report,
    write_markdown_report,
)


DEFAULT_REPORT_JSON = REPO_ROOT / "docs" / "reports" / "phase-3.8b-classification-first-e2e-dry-run-summary.json"
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8b-classification-first-e2e-dry-run.md"
EXECUTE_REJECTION = "Phase 3.8b supports dry-run planning only; execute is not implemented in this phase."


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="Run read-only dry-run planning.")
    mode.add_argument("--execute", action="store_true", help="Rejected in Phase 3.8b.")
    parser.add_argument("--source-label", default=DEFAULT_SOURCE_LABEL)
    parser.add_argument("--expected-current-media-count", type=_non_negative_int)
    parser.add_argument("--expected-eligible-count", type=_non_negative_int)
    parser.add_argument("--expected-ineligible-count", type=_non_negative_int)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--strict", action="store_true")
    return parser


def _load_app_context() -> tuple[Callable[[], Any], Any]:
    from app import database as database_mod
    from app.config import settings

    if database_mod.SessionLocal is None:
        database_mod.init_engine()
    if database_mod.SessionLocal is None:
        raise RuntimeError("Database is not initialized")
    return database_mod.SessionLocal, settings


def _print_summary(report: dict[str, Any], out: TextIO) -> None:
    identity = report["identity"]
    counts = report["counts"]
    mutation = report["mutation_safety"]
    print("Phase 3.8b classification-first E2E dry-run", file=out)
    print(f"mode={report['mode']} status={report['status']} success={report['success']}", file=out)
    print(
        f"python={identity['python']['executable_label']} {identity['python']['version']} "
        f"repo={identity['repo']['branch']}@{identity['repo']['report_git_head_before_commit']}",
        file=out,
    )
    print(
        f"db={identity['database']['violet_env']}/{identity['database']['db_name']} "
        f"storage={identity['storage']['storage_root_label']}",
        file=out,
    )
    print(
        f"target={counts['target_media_count']} eligible={counts['eligible_media_count']} "
        f"ineligible={counts['ineligible_media_count']} null={counts['null_content_class_count']}",
        file=out,
    )
    print(
        "legacy_ineligible_ai_associations="
        f"{report['legacy_contamination']['ineligible_ai_associations']}",
        file=out,
    )
    print(f"mutation_delta={mutation['delta']} mutation_passed={mutation['passed']}", file=out)
    print(f"privacy_passed={report['privacy']['passed']} leaks={report['privacy']['leaks']}", file=out)
    if report["contract_failures"]:
        print(f"contract_failures={report['contract_failures']}", file=out)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return "<outside-repo-report-path>"


def main(
    argv: list[str] | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
    settings_obj: Any | None = None,
    repo_root: Path = REPO_ROOT,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = out or sys.stdout
    err = err or sys.stderr

    if args.execute:
        print(EXECUTE_REJECTION, file=err)
        return 2

    if session_factory is None or settings_obj is None:
        loaded_factory, loaded_settings = _load_app_context()
        session_factory = session_factory or loaded_factory
        settings_obj = settings_obj or loaded_settings

    scope = WorkflowScope(
        source_label=args.source_label,
        expected_current_media_count=args.expected_current_media_count,
        expected_eligible_count=args.expected_eligible_count,
        expected_ineligible_count=args.expected_ineligible_count,
        strict=args.strict,
        dry_run=True,
        null_content_class_policy=NULL_POLICY_HARD_FAIL,
    )

    db = session_factory()
    try:
        before = collect_mutation_snapshot(db)
        report = build_dry_run_report(
            db,
            scope,
            repo_root=repo_root,
            settings=settings_obj,
            before_snapshot=before,
        )
        after = collect_mutation_snapshot(db)
        if after != before:
            report = build_dry_run_report(
                db,
                scope,
                repo_root=repo_root,
                settings=settings_obj,
                before_snapshot=before,
                after_snapshot=after,
            )
    finally:
        db.close()

    write_json_report(args.report_json, report)
    write_markdown_report(args.report_md, report)
    _print_summary(report, out)
    print(f"report_json={_display_path(args.report_json)}", file=out)
    print(f"report_md={_display_path(args.report_md)}", file=out)

    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
