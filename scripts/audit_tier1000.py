#!/usr/bin/env python3
"""Read-only manifest-vs-disk verification for Tier-1000 staging.

Phase 3.4 — Pre-import Audit.
Reads the frozen CSV manifest from Phase 3.3a.1, compares each copy-row
against the actual files on disk in the Tier-1000 staging directory, and
reports any discrepancies.

Hard rules:
  - NEVER modifies, copies, moves, or deletes any file.
  - NEVER accesses any database.
  - Read-only verification only.

Usage:
    python scripts/audit_tier1000.py \
        --manifest ".local_manifests/phase-3.3a.1-candidate-manifest.csv" \
        --target-root "E:\\VioletPilotData_1000"

    python scripts/audit_tier1000.py \
        --manifest ".local_manifests/phase-3.3a.1-candidate-manifest.csv" \
        --target-root "E:\\VioletPilotData_1000" \
        --check-source \
        --audit-csv ".local_manifests/phase-3.4-audit.csv" \
        --json-output "docs/reports/phase-3.4-audit-summary.json"

Exit codes:
    0 — All checks pass
    1 — Manifest error (file not found, parse error, target_root not a directory)
    2 — CLI argument error (missing required args)
    4 — Verification discrepancies found
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Self-contained helpers (no cross-script imports)
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

KNOWN_EXCLUSION_CODES = {"stat_error", "placeholder"}
KNOWN_EXCLUSION_PREFIXES = ("unsupported_format:",)

_REQUIRED_FIELDS = {
    "source_path", "proposed_target_path", "extension",
    "size_bytes", "selection_reason", "exclusion_reason",
}


def _clean_field(row: dict, key: str, default: str = "") -> str:
    val = row.get(key)
    if val is None:
        return default
    return str(val).strip()


def _is_known_exclusion(reason: str) -> bool:
    if reason in KNOWN_EXCLUSION_CODES:
        return True
    for prefix in KNOWN_EXCLUSION_PREFIXES:
        if reason.startswith(prefix):
            return True
    return False


def _path_key(p: Path) -> str:
    s = str(p.resolve())
    return s.lower() if os.name == "nt" else s


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _row_has_required_values(row: dict) -> bool:
    for field in _REQUIRED_FIELDS:
        if field not in row or row[field] is None:
            return False
    return True


# ---------------------------------------------------------------------------
# Audit output schema
# ---------------------------------------------------------------------------

AUDIT_FIELDNAMES = [
    "row_id", "proposed_target_path", "source_path",
    "expected_size", "actual_size", "size_delta",
    "expected_ext", "actual_ext", "status", "detail",
]


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------

def audit_manifest_vs_disk(
    manifest_path: Path,
    target_root: Path,
    *,
    check_source: bool = False,
) -> dict:
    """Verify each manifest copy-row against on-disk state.

    Returns a dict with per-row audit records and aggregate counters.
    """
    result = {
        "manifest_path": str(manifest_path),
        "target_root": str(target_root),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "manifest_total_rows": 0,
        "copy_rows": 0,
        "excluded_rows": 0,
        "truncated_rows": 0,
        "target_pass": 0,
        "target_missing": 0,
        "size_mismatches": 0,
        "extension_mismatches": 0,
        "target_escapes": 0,
        "duplicate_target_paths": 0,
        "invalid_size_rows": 0,
        "source_checked": check_source,
        "source_missing": 0,
        "total_verified_bytes": 0,
        "total_expected_bytes": 0,
        "expected_targets": set(),
        "audit_rows": [],
        "errors": [],
        "warnings": [],
    }

    try:
        with open(manifest_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        result["errors"].append(f"Manifest read error: {exc}")
        return result

    result["manifest_total_rows"] = len(rows)

    try:
        resolved_root = target_root.resolve()
    except OSError as exc:
        result["errors"].append(f"Cannot resolve target root: {exc}")
        return result

    for row in rows:
        audit_rec = {
            "row_id": "",
            "proposed_target_path": "",
            "source_path": "",
            "expected_size": "",
            "actual_size": "",
            "size_delta": "",
            "expected_ext": "",
            "actual_ext": "",
            "status": "",
            "detail": "",
        }

        if not _row_has_required_values(row):
            result["truncated_rows"] += 1
            audit_rec["row_id"] = row.get("row_id", "")
            audit_rec["status"] = "SKIPPED_TRUNCATED"
            audit_rec["detail"] = "Missing required CSV fields"
            result["audit_rows"].append(audit_rec)
            continue

        row_id = _clean_field(row, "row_id")
        source_path = _clean_field(row, "source_path")
        target_path = _clean_field(row, "proposed_target_path")
        extension = _clean_field(row, "extension")
        size_str = _clean_field(row, "size_bytes")
        exclusion = _clean_field(row, "exclusion_reason")

        audit_rec["row_id"] = row_id
        audit_rec["proposed_target_path"] = target_path
        audit_rec["source_path"] = source_path
        audit_rec["expected_ext"] = extension

        if exclusion and _is_known_exclusion(exclusion):
            result["excluded_rows"] += 1
            audit_rec["status"] = "SKIPPED_EXCLUDED"
            audit_rec["detail"] = f"exclusion_reason={exclusion}"
            result["audit_rows"].append(audit_rec)
            continue

        result["copy_rows"] += 1

        try:
            if not size_str:
                raise ValueError("blank")
            expected_size = int(size_str)
            if expected_size < 0:
                raise ValueError("negative")
        except (ValueError, TypeError):
            expected_size = 0
            result["invalid_size_rows"] += 1
            audit_rec["expected_size"] = size_str
            audit_rec["status"] = "INVALID_SIZE"
            audit_rec["detail"] = f"Invalid size_bytes value: {size_str!r}"
            result["audit_rows"].append(audit_rec)
            continue

        audit_rec["expected_size"] = str(expected_size)
        result["total_expected_bytes"] += expected_size

        tp = Path(target_path)

        try:
            resolved_tp = tp.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            result["target_escapes"] += 1
            audit_rec["status"] = "TARGET_RESOLVE_ERROR"
            audit_rec["detail"] = f"Cannot resolve target path: {exc}"
            result["audit_rows"].append(audit_rec)
            continue

        if not _is_under(resolved_tp, resolved_root):
            result["target_escapes"] += 1
            audit_rec["status"] = "TARGET_ESCAPE"
            audit_rec["detail"] = "Target path resolves outside target_root"
            result["audit_rows"].append(audit_rec)
            continue

        key = _path_key(tp)
        if key in result["expected_targets"]:
            result["duplicate_target_paths"] += 1
            audit_rec["status"] = "DUPLICATE_TARGET"
            audit_rec["detail"] = "Duplicate proposed_target_path"
            result["audit_rows"].append(audit_rec)
            continue
        result["expected_targets"].add(key)

        if not tp.is_file():
            result["target_missing"] += 1
            audit_rec["status"] = "MISSING_TARGET"
            audit_rec["detail"] = "File not found on disk"
            result["audit_rows"].append(audit_rec)
            continue

        try:
            actual_size = tp.stat().st_size
        except OSError as exc:
            result["target_missing"] += 1
            audit_rec["status"] = "MISSING_TARGET"
            audit_rec["detail"] = f"stat failed: {exc}"
            result["audit_rows"].append(audit_rec)
            continue

        audit_rec["actual_size"] = str(actual_size)
        audit_rec["size_delta"] = str(actual_size - expected_size)
        result["total_verified_bytes"] += actual_size

        actual_ext = tp.suffix.lower()
        audit_rec["actual_ext"] = actual_ext

        failures = []

        if actual_size != expected_size:
            result["size_mismatches"] += 1
            failures.append(f"Size mismatch: expected {expected_size}, got {actual_size}")

        if extension and actual_ext != extension.lower():
            result["extension_mismatches"] += 1
            failures.append(f"Extension mismatch: expected {extension}, got {actual_ext}")

        if check_source and source_path:
            sp = Path(source_path)
            if not sp.is_file():
                result["source_missing"] += 1
                failures.append("Source file no longer exists")

        if failures:
            audit_rec["status"] = failures[0].split(":")[0].upper().replace(" ", "_")
            audit_rec["detail"] = "; ".join(failures)
        else:
            result["target_pass"] += 1
            audit_rec["status"] = "PASS"

        result["audit_rows"].append(audit_rec)

    if result["copy_rows"] == 0 and result["manifest_total_rows"] > 0:
        result["warnings"].append("No copy rows found (all excluded or truncated)")

    return result


def scan_unexpected_files(target_root: Path, expected_targets: set[str]) -> list[str]:
    """Find files on disk that are NOT in the manifest expected set.

    Returns relative paths (from target_root) of unexpected files.
    """
    unexpected = []
    for root, _dirs, files in os.walk(target_root):
        for fname in files:
            fpath = Path(root) / fname
            if _path_key(fpath) not in expected_targets:
                try:
                    rel = str(fpath.relative_to(target_root))
                except ValueError:
                    rel = str(fpath)
                unexpected.append(rel)
    return unexpected


def generate_audit_csv(audit_rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(audit_rows)


def generate_audit_json(summary: dict, output_path: Path) -> None:
    safe = {
        "phase": "3.4",
        "timestamp": summary.get("timestamp", ""),
        "manifest_total_rows": summary.get("manifest_total_rows", 0),
        "copy_rows": summary.get("copy_rows", 0),
        "excluded_rows": summary.get("excluded_rows", 0),
        "truncated_rows": summary.get("truncated_rows", 0),
        "target_pass": summary.get("target_pass", 0),
        "target_missing": summary.get("target_missing", 0),
        "size_mismatches": summary.get("size_mismatches", 0),
        "extension_mismatches": summary.get("extension_mismatches", 0),
        "target_escapes": summary.get("target_escapes", 0),
        "duplicate_target_paths": summary.get("duplicate_target_paths", 0),
        "invalid_size_rows": summary.get("invalid_size_rows", 0),
        "source_checked": summary.get("source_checked", False),
        "source_missing": summary.get("source_missing", 0),
        "total_verified_bytes": summary.get("total_verified_bytes", 0),
        "total_expected_bytes": summary.get("total_expected_bytes", 0),
        "unexpected_files_on_disk": summary.get("unexpected_files_on_disk", 0),
        "unexpected_file_samples": summary.get("unexpected_file_samples", []),
        "result": summary.get("result", "UNKNOWN"),
        "errors": summary.get("errors", []),
        "warnings": summary.get("warnings", []),
        "paths_redacted": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Read-only manifest-vs-disk verification for Tier-1000 staging")
    parser.add_argument("--manifest", type=str, required=True,
                        help="Path to CSV manifest")
    parser.add_argument("--target-root", type=str, required=True,
                        help="Staging directory to verify")
    parser.add_argument("--check-source", action="store_true",
                        help="Also verify source files still exist")
    parser.add_argument("--audit-csv", type=str, default=None,
                        help="Output path for per-row CSV audit log")
    parser.add_argument("--json", action="store_true",
                        help="Print JSON summary to stdout")
    parser.add_argument("--json-output", type=str, default=None,
                        help="Save JSON summary to file")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    target_root = Path(args.target_root)

    if not manifest_path.is_file():
        print(f"ERROR: Manifest file not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    if not target_root.is_dir():
        print(f"ERROR: Target root is not a directory: {target_root}", file=sys.stderr)
        sys.exit(1)

    if not args.json:
        print(f"Phase 3.4 — Tier-1000 Pre-import Audit")
        print(f"  Manifest:    {manifest_path}")
        print(f"  Target root: {target_root}")
        print(f"  Check source: {args.check_source}")
        print()

    result = audit_manifest_vs_disk(
        manifest_path, target_root, check_source=args.check_source,
    )

    if result["errors"]:
        for err in result["errors"]:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    unexpected = scan_unexpected_files(target_root, result["expected_targets"])
    result["unexpected_files_on_disk"] = len(unexpected)
    result["unexpected_file_samples"] = unexpected[:20]

    has_discrepancy = (
        result["target_missing"] > 0
        or result["size_mismatches"] > 0
        or result["extension_mismatches"] > 0
        or result["target_escapes"] > 0
        or result["duplicate_target_paths"] > 0
        or result["invalid_size_rows"] > 0
        or result["unexpected_files_on_disk"] > 0
        or result["truncated_rows"] > 0
        or (result["source_checked"] and result["source_missing"] > 0)
    )
    result["result"] = "FAIL" if has_discrepancy else "PASS"

    if args.json:
        safe = {k: v for k, v in result.items()
                if k not in ("expected_targets", "audit_rows",
                             "manifest_path", "target_root")}
        safe["paths_redacted"] = True
        print(json.dumps(safe, indent=2, ensure_ascii=False))
    else:
        print("=== Manifest Summary ===")
        print(f"  Total rows:        {result['manifest_total_rows']}")
        print(f"  Copy rows:         {result['copy_rows']}")
        print(f"  Excluded rows:     {result['excluded_rows']}")
        print(f"  Truncated rows:    {result['truncated_rows']}")
        print()
        print("=== Verification Results ===")
        print(f"  Target PASS:       {result['target_pass']}")
        print(f"  Target MISSING:    {result['target_missing']}")
        print(f"  Size mismatches:   {result['size_mismatches']}")
        print(f"  Ext mismatches:    {result['extension_mismatches']}")
        print(f"  Target escapes:    {result['target_escapes']}")
        print(f"  Duplicate targets: {result['duplicate_target_paths']}")
        print(f"  Invalid sizes:     {result['invalid_size_rows']}")
        if result["source_checked"]:
            print(f"  Source missing:    {result['source_missing']}")
        print(f"  Truncated rows:    {result['truncated_rows']}")
        print(f"  Expected bytes:    {_fmt_bytes(result['total_expected_bytes'])}")
        print(f"  Verified bytes:    {_fmt_bytes(result['total_verified_bytes'])}")
        print()
        print("=== Unexpected Files ===")
        print(f"  Count: {result['unexpected_files_on_disk']}")
        if unexpected:
            for p in unexpected[:20]:
                print(f"    {p}")
            if len(unexpected) > 20:
                print(f"    ... and {len(unexpected) - 20} more")
        print()
        print(f"=== Result: {result['result']} ===")

        if result["warnings"]:
            print()
            for w in result["warnings"]:
                print(f"WARNING: {w}")

    if args.audit_csv:
        generate_audit_csv(result["audit_rows"], Path(args.audit_csv))
        if not args.json:
            print(f"\nAudit CSV written to: {args.audit_csv}")

    if args.json_output:
        generate_audit_json(result, Path(args.json_output))
        if not args.json:
            print(f"JSON summary written to: {args.json_output}")

    sys.exit(4 if has_discrepancy else 0)


if __name__ == "__main__":
    main()
