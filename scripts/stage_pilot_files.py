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

KNOWN_SELECTION_REASONS = {"existing_tier500", "new_candidate"}
KNOWN_EXCLUSION_CODES = {"stat_error", "placeholder"}
KNOWN_EXCLUSION_PREFIXES = ("unsupported_format:",)


def _is_known_exclusion(reason: str) -> bool:
    """Check if an exclusion_reason is a recognized value."""
    if reason in KNOWN_EXCLUSION_CODES:
        return True
    for prefix in KNOWN_EXCLUSION_PREFIXES:
        if reason.startswith(prefix):
            return True
    return False


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
        "target_root_escapes": 0,
        "target_root_escape_paths": [],
        "unsupported_extensions": 0,
        "blank_source_paths": 0,
        "blank_target_paths": 0,
        "invalid_selection_reasons": 0,
        "invalid_exclusion_reasons": 0,
        "extension_mismatches": 0,
        "suffix_missing": 0,
        "target_existing_files": 0,
        "target_existing_paths": [],
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

    try:
        resolved_root = target_root.resolve()
    except (RuntimeError, OSError) as exc:
        result["errors"].append(f"Cannot resolve target_root: {exc}")
        result["valid"] = False
        return result
    # Collision detection by full resolved target path (case-insensitive on Windows)
    seen_targets: dict[str, str] = {}

    for row in rows:
        # 2A: Normalize all string fields
        selection = row.get("selection_reason", "").strip()
        exclusion = row.get("exclusion_reason", "").strip()
        source_path = row.get("source_path", "").strip()
        proposed_target = row.get("proposed_target_path", "").strip()
        ext = row.get("extension", "").strip().lower()
        size_str = row.get("size_bytes", "0").strip()

        try:
            size = int(size_str)
        except (ValueError, TypeError):
            size = 0

        # 2C: Exclusion reason validation
        if exclusion:
            if not _is_known_exclusion(exclusion):
                result["invalid_exclusion_reasons"] += 1
            else:
                result["excluded_rows"] += 1
                continue
        # Note: whitespace-only exclusion was already stripped to "", so falls through

        # --- Non-excluded (copy) row processing ---

        # 2B: Selection reason validation
        if selection == "existing_tier500":
            result["existing_tier500_rows"] += 1
        elif selection == "new_candidate":
            result["new_candidate_rows"] += 1

        if selection not in KNOWN_SELECTION_REASONS:
            result["invalid_selection_reasons"] += 1

        # Reject non-excluded copy rows with blank source_path
        if not source_path:
            result["blank_source_paths"] += 1

        # Reject non-excluded copy rows with blank proposed_target_path
        if not proposed_target:
            result["blank_target_paths"] += 1

        # Validate source file exists — missing source is an ERROR for copy rows
        source_suffix = ""
        if source_path:
            sp = Path(source_path)
            if sp.is_file():
                result["source_files_exist"] += 1
                source_suffix = sp.suffix.lower()
            else:
                result["source_files_missing"] += 1
                if len(result["source_files_missing_paths"]) < 10:
                    result["source_files_missing_paths"].append(source_path)
                # Derive suffix even for missing source (for consistency check)
                source_suffix = sp.suffix.lower()

        # Validate extension from CSV field
        if ext and ext not in SUPPORTED_EXTENSIONS:
            result["unsupported_extensions"] += 1

        # 2F: Suffix consistency — all three must agree and be supported
        target_suffix = ""
        if proposed_target:
            target_suffix = Path(proposed_target).suffix.lower()

        if source_path:
            # Source suffix must be non-empty and supported
            if not source_suffix:
                result["suffix_missing"] += 1
            elif source_suffix not in SUPPORTED_EXTENSIONS:
                result["extension_mismatches"] += 1

            # CSV extension must match source suffix
            if ext and source_suffix and ext != source_suffix:
                result["extension_mismatches"] += 1

        if proposed_target:
            # Target suffix must be non-empty and supported
            if not target_suffix:
                result["suffix_missing"] += 1
            elif target_suffix not in SUPPORTED_EXTENSIONS:
                result["extension_mismatches"] += 1

            # Target suffix must equal source suffix
            if source_suffix and target_suffix and target_suffix != source_suffix:
                result["extension_mismatches"] += 1

        # Check target path escapes target_root
        if proposed_target:
            try:
                resolved_target = Path(proposed_target).resolve()
                resolved_target.relative_to(resolved_root)
            except (ValueError, RuntimeError, OSError):
                result["target_root_escapes"] += 1
                if len(result["target_root_escape_paths"]) < 10:
                    result["target_root_escape_paths"].append(proposed_target)
        else:
            # Empty/blank target on a copy row — counts as escape
            if not proposed_target:
                # Already counted in blank_target_paths; also count as escape
                result["target_root_escapes"] += 1

        # 2H: Collision by full resolved target path (case-insensitive)
        if proposed_target:
            try:
                collision_key = str(Path(proposed_target).resolve()).lower()
            except (OSError, ValueError, RuntimeError):
                collision_key = proposed_target.lower()
            if collision_key in seen_targets:
                result["target_filename_collisions"] += 1
                if len(result["target_collision_paths"]) < 10:
                    result["target_collision_paths"].append(proposed_target)
            else:
                seen_targets[collision_key] = proposed_target

        # 2I: Check if proposed target already exists on disk
        if proposed_target:
            try:
                resolved_target = Path(proposed_target).resolve()
                if resolved_target.exists():
                    result["target_existing_files"] += 1
                    if len(result["target_existing_paths"]) < 10:
                        result["target_existing_paths"].append(proposed_target)
            except (OSError, ValueError, RuntimeError):
                pass  # resolve/exists failure — already caught by escape check

        result["total_copy_bytes"] += size

    # Validation checks — errors that set valid=False
    if result["source_files_missing"] > 0:
        result["errors"].append(
            f"{result['source_files_missing']} source files not found"
        )
        result["valid"] = False

    if result["target_root_escapes"] > 0:
        result["errors"].append(
            f"{result['target_root_escapes']} target paths escape target_root"
        )
        result["valid"] = False

    if result["blank_source_paths"] > 0:
        result["errors"].append(
            f"{result['blank_source_paths']} non-excluded rows have blank source_path"
        )
        result["valid"] = False

    if result["blank_target_paths"] > 0:
        result["errors"].append(
            f"{result['blank_target_paths']} non-excluded rows have blank proposed_target_path"
        )
        result["valid"] = False

    if result["target_filename_collisions"] > 0:
        result["errors"].append(
            f"{result['target_filename_collisions']} target path collisions detected"
        )
        result["valid"] = False

    if result["unsupported_extensions"] > 0:
        result["errors"].append(
            f"{result['unsupported_extensions']} rows have unsupported extensions"
        )
        result["valid"] = False

    if result["invalid_selection_reasons"] > 0:
        result["errors"].append(
            f"{result['invalid_selection_reasons']} non-excluded rows have invalid selection_reason"
        )
        result["valid"] = False

    if result["invalid_exclusion_reasons"] > 0:
        result["errors"].append(
            f"{result['invalid_exclusion_reasons']} rows have invalid exclusion_reason"
        )
        result["valid"] = False

    if result["extension_mismatches"] > 0:
        result["errors"].append(
            f"{result['extension_mismatches']} rows have extension mismatches"
        )
        result["valid"] = False

    if result["suffix_missing"] > 0:
        result["errors"].append(
            f"{result['suffix_missing']} copy rows have missing file suffix"
        )
        result["valid"] = False

    if result["target_existing_files"] > 0:
        result["errors"].append(
            f"{result['target_existing_files']} proposed target files already exist on disk"
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
    print(f"  Target-root escapes:       {result['target_root_escapes']}")
    print(f"  Unsupported extensions:    {result['unsupported_extensions']}")
    print(f"  Blank source paths:        {result['blank_source_paths']}")
    print(f"  Blank target paths:        {result['blank_target_paths']}")
    print(f"  Suffix missing:            {result['suffix_missing']}")
    print(f"  Target files exist on disk: {result['target_existing_files']}")
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
