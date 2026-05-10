#!/usr/bin/env python3
"""Read-only fixture inspection helper for V.I.O.L.E.T. test workflow.

Usage:
    python scripts/inspect_test_fixture.py
    python scripts/inspect_test_fixture.py --path C:\\path\\to\\VioletTestFixture
    python scripts/inspect_test_fixture.py --json

Counts supported image files per subfolder in the fixture directory.
Never modifies, moves, or deletes any files.
"""
import argparse
import json
import os
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
EXPECTED_SUBFOLDERS = ("anime", "non_anime", "mixed")


def inspect_fixture(fixture_path: Path) -> dict:
    result = {
        "fixture_path": str(fixture_path),
        "exists": fixture_path.is_dir(),
        "subfolders": {},
        "total_supported": 0,
        "total_unsupported": 0,
        "errors": [],
    }

    if not result["exists"]:
        result["errors"].append(f"Directory does not exist: {fixture_path}")
        return result

    for subfolder in EXPECTED_SUBFOLDERS:
        sub_path = fixture_path / subfolder
        info = {
            "exists": sub_path.is_dir(),
            "supported": 0,
            "unsupported": 0,
            "supported_files": [],
            "unsupported_files": [],
        }
        if sub_path.is_dir():
            for entry in sorted(sub_path.iterdir()):
                if not entry.is_file():
                    continue
                if entry.name.startswith("."):
                    continue
                ext = entry.suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    info["supported"] += 1
                    info["supported_files"].append(entry.name)
                else:
                    info["unsupported"] += 1
                    info["unsupported_files"].append(entry.name)
        else:
            result["errors"].append(f"Expected subfolder missing: {subfolder}")

        result["subfolders"][subfolder] = info
        result["total_supported"] += info["supported"]
        result["total_unsupported"] += info["unsupported"]

    return result


def main():
    parser = argparse.ArgumentParser(description="Inspect V.I.O.L.E.T. test fixture (read-only)")
    parser.add_argument("--path", type=str, default=None,
                        help="Path to VioletTestFixture (default: VIOLET_TEST_FIXTURE_PATH env)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    fixture_path = args.path or os.getenv("VIOLET_TEST_FIXTURE_PATH", "")
    if not fixture_path:
        print("ERROR: No fixture path provided. Use --path or set VIOLET_TEST_FIXTURE_PATH.")
        sys.exit(1)

    result = inspect_fixture(Path(fixture_path))

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"Fixture path: {result['fixture_path']}")
    print(f"Exists: {result['exists']}")
    if not result["exists"]:
        print("  ERROR: directory not found")
        sys.exit(1)

    print()
    for name, info in result["subfolders"].items():
        status = "OK" if info["exists"] else "MISSING"
        print(f"  {name}/ [{status}]")
        if info["exists"]:
            print(f"    Supported images: {info['supported']}")
            if info["unsupported"] > 0:
                print(f"    Unsupported files: {info['unsupported']} ({', '.join(info['unsupported_files'])})")

    print()
    print(f"Total supported: {result['total_supported']}")
    print(f"Total unsupported: {result['total_unsupported']}")

    if result["errors"]:
        print()
        for err in result["errors"]:
            print(f"  WARNING: {err}")


if __name__ == "__main__":
    main()
