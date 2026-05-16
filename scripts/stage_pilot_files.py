#!/usr/bin/env python3
"""Validate a candidate manifest for Tier-1000 pilot staging.

Phase 3.3a.1 — dry-run only. Reads a CSV manifest produced by
generate_candidate_manifest.py and validates that the proposed staging
operation is safe and consistent.

Hard rules:
  - NEVER copies, moves, or deletes files in dry-run mode.
  - NEVER modifies the source or existing directories.
  - NEVER modifies any database.
  - --execute is reserved for Phase 3.3b and is NOT implemented yet.

Usage:
    python scripts/stage_pilot_files.py \
        --manifest ".local_manifests/phase-3.3a.1-candidate-manifest.csv" \
        --target-root "E:\\VioletPilotData_1000" \
        --dry-run
"""
import argparse
import csv
import os
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def validate_manifest(manifest_path: Path, target_root: Path) -> dict:
    """Validate a staging manifest (dry-run).

    Returns a dict with validation results.
    """
    result = {
        "manifest_path": str(manifest_path),
        "target_root": str(target_root),
        "total_rows": 0,
        "existing_tier500_rows": 0,
        "new_candidate_rows": 0,
        "excluded_rows": 0,
        "source_files_exist": 0,
        "source_files_missing": 0,
        "source_files_missing_paths": [],
        "target_filename_collisions": 0,
        "target_collision_paths": [],
        "unsupported_extensions": 0,
        "total_copy_bytes": 0,
        "target_root_exists": target_root.is_dir(),
        "target_root_is_local": True,
        "errors": [],
        "warnings": [],
        "valid": True,
    }

    if not manifest_path.is_file():
        result["errors"].append(f"Manifest file not found: {manifest_path}")
        result["valid"] = False
        return result

    rows = []
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception as e:
        result["errors"].append(f"Failed to read manifest: {e}")
        result["valid"] = False
        return result

    result["total_rows"] = len(rows)

    seen_targets = {}

    for row in rows:
        selection = row.get("selection_reason", "")
        exclusion = row.get("exclusion_reason", "")
        source_path = row.get("source_path", "")
        proposed_target = row.get("proposed_target_path", "")
        ext = row.get("extension", "").lower()
        size_str = row.get("size_bytes", "0")

        try:
            size = int(size_str)
        except (ValueError, TypeError):
            size = 0

        if exclusion:
            result["excluded_rows"] += 1
            continue

        if selection == "existing_tier500":
            result["existing_tier500_rows"] += 1
        elif selection == "new_candidate":
            result["new_candidate_rows"] += 1

        # Validate source file exists
        if source_path:
            sp = Path(source_path)
            if sp.is_file():
                result["source_files_exist"] += 1
            else:
                result["source_files_missing"] += 1
                if len(result["source_files_missing_paths"]) < 10:
                    result["source_files_missing_paths"].append(source_path)

        # Validate extension
        if ext and ext not in SUPPORTED_EXTENSIONS:
            result["unsupported_extensions"] += 1

        # Check target filename collisions
        if proposed_target:
            target_name = Path(proposed_target).name.lower()
            if target_name in seen_targets:
                result["target_filename_collisions"] += 1
                if len(result["target_collision_paths"]) < 10:
                    result["target_collision_paths"].append(proposed_target)
            else:
                seen_targets[target_name] = proposed_target

        result["total_copy_bytes"] += size

    # Validation checks
    if result["source_files_missing"] > 0:
        result["warnings"].append(
            f"{result['source_files_missing']} source files not found (may be iCloud-only)"
        )

    if result["target_filename_collisions"] > 0:
        result["errors"].append(
            f"{result['target_filename_collisions']} target filename collisions detected"
        )
        result["valid"] = False

    if result["unsupported_extensions"] > 0:
        result["errors"].append(
            f"{result['unsupported_extensions']} rows have unsupported extensions"
        )
        result["valid"] = False

    copy_count = result["existing_tier500_rows"] + result["new_candidate_rows"]
    if copy_count == 0:
        result["warnings"].append("No files to copy (manifest may be empty or all excluded)")

    return result


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"


def main():
    parser = argparse.ArgumentParser(
        description="Validate a staging manifest for Tier-1000 pilot (dry-run only)")
    parser.add_argument("--manifest", type=str, required=True,
                        help="Path to CSV manifest from generate_candidate_manifest.py")
    parser.add_argument("--target-root", type=str, required=True,
                        help="Proposed staging directory path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dry-run mode: validate only, do not copy")
    parser.add_argument("--execute", action="store_true",
                        help="Execute mode: NOT IMPLEMENTED (reserved for Phase 3.3b)")
    args = parser.parse_args()

    if args.execute:
        print("ERROR: --execute is not implemented in Phase 3.3a.1.", file=sys.stderr)
        print("       File copying will be implemented in Phase 3.3b.", file=sys.stderr)
        sys.exit(2)

    if not args.dry_run:
        print("ERROR: --dry-run is required in Phase 3.3a.1.", file=sys.stderr)
        print("       Use: --dry-run", file=sys.stderr)
        sys.exit(2)

    manifest_path = Path(args.manifest)
    target_root = Path(args.target_root)

    print(f"Manifest:    {manifest_path}")
    print(f"Target root: {target_root}")
    print(f"Mode:        dry-run (validation only)")
    print()

    result = validate_manifest(manifest_path, target_root)

    print("=== Staging Validation ===")
    print(f"  Total manifest rows:       {result['total_rows']}")
    print(f"  Existing (Tier-500):       {result['existing_tier500_rows']}")
    print(f"  New candidates:            {result['new_candidate_rows']}")
    print(f"  Excluded:                  {result['excluded_rows']}")
    print(f"  Source files found:        {result['source_files_exist']}")
    print(f"  Source files missing:      {result['source_files_missing']}")
    print(f"  Target collisions:         {result['target_filename_collisions']}")
    print(f"  Unsupported extensions:    {result['unsupported_extensions']}")
    print(f"  Total copy size:           {_fmt_bytes(result['total_copy_bytes'])}")
    print(f"  Target root exists:        {result['target_root_exists']}")
    print()

    copy_count = result["existing_tier500_rows"] + result["new_candidate_rows"]
    print(f"  Files to copy (total):     {copy_count}")

    if result["warnings"]:
        print()
        print("  Warnings:")
        for w in result["warnings"]:
            print(f"    - {w}")

    if result["errors"]:
        print()
        print("  Errors:")
        for e in result["errors"]:
            print(f"    - {e}")

    print()
    if result["valid"]:
        print("  RESULT: VALID — manifest is safe to stage in Phase 3.3b")
    else:
        print("  RESULT: INVALID — fix errors before proceeding")

    print()
    print("  [DRY-RUN] No files were copied.")

    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
