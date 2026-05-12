#!/usr/bin/env python3
"""Read-only pilot dataset inspector for V.I.O.L.E.T. medium-scale pilots.

Usage:
    python scripts/inspect_pilot_dataset.py --path "D:\\VioletPilotData\\500"
    python scripts/inspect_pilot_dataset.py --path "D:\\VioletPilotData\\500" --json

Recursively scans an arbitrary directory (flat or nested) and reports:
- Total files, supported images, unsupported files, hidden/system files
- Extension distribution
- Total size in bytes
- Symlink count
- Sample unsupported file paths

Never modifies, moves, or deletes any files. No database access. No imports.

Unlike inspect_test_fixture.py (which requires anime/non_anime/mixed subfolders),
this script works with any directory structure.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

HIDDEN_PATTERNS = frozenset({
    "thumbs.db", "desktop.ini", ".ds_store",
})


def _is_hidden(entry: Path) -> bool:
    name = entry.name
    if name.startswith("."):
        return True
    if name.lower() in HIDDEN_PATTERNS:
        return True
    return False


def inspect_dataset(dataset_path: Path) -> dict:
    result = {
        "dataset_path": str(dataset_path),
        "exists": dataset_path.is_dir(),
        "total_files": 0,
        "supported": 0,
        "unsupported": 0,
        "hidden": 0,
        "symlinks": 0,
        "total_bytes": 0,
        "extension_distribution": {},
        "sample_unsupported": [],
        "errors": [],
    }

    if not result["exists"]:
        result["errors"].append(f"Directory does not exist: {dataset_path}")
        return result

    ext_counter: Counter = Counter()
    max_unsupported_samples = 10

    for root, dirs, files in os.walk(dataset_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        for fname in files:
            fpath = Path(root) / fname

            if fpath.is_symlink():
                result["symlinks"] += 1

            if _is_hidden(fpath):
                result["hidden"] += 1
                continue

            result["total_files"] += 1

            try:
                result["total_bytes"] += fpath.stat().st_size
            except OSError:
                pass

            ext = fpath.suffix.lower()
            ext_counter[ext] += 1

            if ext in SUPPORTED_EXTENSIONS:
                result["supported"] += 1
            else:
                result["unsupported"] += 1
                if len(result["sample_unsupported"]) < max_unsupported_samples:
                    try:
                        rel = fpath.relative_to(dataset_path)
                    except ValueError:
                        rel = fpath
                    result["sample_unsupported"].append(str(rel))

    result["extension_distribution"] = dict(ext_counter.most_common())
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
        description="Inspect a pilot dataset directory (read-only)")
    parser.add_argument("--path", type=str, required=True,
                        help="Path to pilot dataset directory")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    dataset_path = Path(args.path)
    result = inspect_dataset(dataset_path)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"Dataset path: {result['dataset_path']}")
    print(f"Exists: {result['exists']}")
    if not result["exists"]:
        print("  ERROR: directory not found")
        sys.exit(1)

    print()
    print(f"  Total files (non-hidden): {result['total_files']}")
    print(f"  Supported images:         {result['supported']}")
    print(f"  Unsupported files:        {result['unsupported']}")
    print(f"  Hidden/system files:      {result['hidden']}")
    print(f"  Symlinks encountered:     {result['symlinks']}")
    print(f"  Total size:               {_fmt_bytes(result['total_bytes'])}")

    if result["extension_distribution"]:
        print()
        print("  Extension distribution:")
        for ext, count in result["extension_distribution"].items():
            label = ext if ext else "(no extension)"
            print(f"    {label}: {count}")

    if result["sample_unsupported"]:
        print()
        print("  Sample unsupported files:")
        for p in result["sample_unsupported"]:
            print(f"    {p}")

    if result["errors"]:
        print()
        for err in result["errors"]:
            print(f"  ERROR: {err}")


if __name__ == "__main__":
    main()
