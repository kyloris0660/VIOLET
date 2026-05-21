#!/usr/bin/env python3
"""Generate a candidate manifest for iCloud-safe Tier-1000 pilot staging.

Phase 3.3a.1 — read-only scan of a source directory (e.g. iCloud Photos),
compare against an existing dataset, and produce a CSV manifest of candidates
to be copied to a staging directory.

Hard rules:
  - NEVER writes to the source directory.
  - NEVER imports, classifies, tags, or runs LLM on any file.
  - NEVER modifies any database.
  - Output goes to --output (CSV) and --summary-output (JSON).

Usage:
    python scripts/generate_candidate_manifest.py \
        --source-root "C:\\Users\\kyloris\\Pictures\\iCloud Photos\\Photos" \
        --existing-root "E:\\VioletPilotData" \
        --target-root "E:\\VioletPilotData_1000" \
        --target-total 1000 \
        --seed 3301 \
        --output ".local_manifests/phase-3.3a.1-candidate-manifest.csv" \
        --summary-output "docs/reports/phase-3.3a.1-icloud-staging-summary.json" \
        --dry-run
"""
import argparse
import csv
import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.source_ingestion_gate import SourceIngestionGate  # noqa: E402

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

PLACEHOLDER_SIZE_THRESHOLD = 1024  # bytes


def validate_output_paths(
    output_path: Path,
    summary_path: Path,
    source_root: Path,
    target_root: Path,
) -> None:
    """Ensure output/summary files are NOT inside source_root or target_root.

    source_root is a read-only source (may be iCloud Photos) and target_root is
    the staging copy destination.  Writing manifest / summary inside either would
    pollute the source or contaminate the staging area.

    Raises ValueError if either output resolves inside source_root or target_root.
    """
    try:
        abs_output = output_path.resolve()
        abs_summary = summary_path.resolve()
        abs_source = source_root.resolve()
        abs_target = target_root.resolve()
    except (RuntimeError, OSError) as exc:
        raise ValueError(
            f"Cannot resolve paths for output validation: {exc}"
        ) from exc

    # Reject identical output and summary paths (CSV would be overwritten by JSON)
    if abs_output == abs_summary:
        raise ValueError(
            f"output and summary-output resolve to the same file ({abs_output}). "
            f"They must be distinct to avoid overwriting the CSV manifest with JSON."
        )

    for label, p in [("output", abs_output), ("summary", abs_summary)]:
        for root_label, root_path in [("source_root", abs_source), ("target_root", abs_target)]:
            # Check if p equals or is inside root_path
            try:
                p.relative_to(root_path)
            except ValueError:
                continue  # not inside — good
            raise ValueError(
                f"{label} path ({p}) must not be inside {root_label} ({root_path}). "
                f"Move {label} outside of {root_label} to avoid polluting it."
            )


def is_icloud_placeholder(path: Path) -> bool:
    """Detect iCloud placeholder / stub files that haven't been downloaded."""
    gate = SourceIngestionGate.evaluate_path_source(
        path,
        safe_label=path.name,
        hydration_policy_enabled=False,
    )
    if gate.blocked and gate.reason.startswith("cloud_"):
        return True
    try:
        size = path.stat().st_size
    except OSError:
        return True  # can't stat => treat as placeholder
    if size == 0:
        return True
    if path.suffix.lower() == ".icloud" or path.name.startswith("."):
        return True
    if size < PLACEHOLDER_SIZE_THRESHOLD:
        return True
    return False


def _scan_existing_dataset(existing_root: Path) -> tuple:
    """Scan the existing dataset directory.

    Returns (existing_rows, duplicate_index, existing_count, existing_total_bytes).
    - existing_rows: list of dicts with path, filename, extension, size_bytes
    - duplicate_index: set of (lowercase_filename, size) for candidate exclusion
    - existing_count: actual number of supported files (may differ from len(duplicate_index))
    - existing_total_bytes: sum of all supported file sizes
    """
    rows = []
    dupes = set()
    total_bytes = 0
    if not existing_root.is_dir():
        return rows, dupes, 0, 0
    for root, dirs, files in os.walk(existing_root):
        dirs[:] = sorted(dirs, key=str.lower)
        for fname in sorted(files, key=str.lower):
            fpath = Path(root) / fname
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            ext = fpath.suffix.lower()
            if ext in SUPPORTED_EXTENSIONS:
                dupes.add((fname.lower(), size))
                total_bytes += size
                rows.append({
                    "path": fpath,
                    "filename": fname,
                    "extension": ext,
                    "size_bytes": size,
                })
    return rows, dupes, len(rows), total_bytes


def _scan_source(source_root: Path):
    """Read-only recursive scan of source directory.

    Yields dicts with keys: path, filename, extension, size_bytes,
    is_placeholder, stat_error.
    """
    for root, dirs, files in os.walk(source_root):
        dirs[:] = sorted([d for d in dirs if not d.startswith(".")], key=str.lower)
        for fname in sorted(files, key=str.lower):
            fpath = Path(root) / fname
            entry = {
                "path": fpath,
                "filename": fname,
                "extension": fpath.suffix.lower(),
                "size_bytes": 0,
                "is_placeholder": False,
                "stat_error": False,
            }
            try:
                entry["size_bytes"] = fpath.stat().st_size
            except OSError:
                entry["stat_error"] = True
                yield entry
                continue

            entry["is_placeholder"] = is_icloud_placeholder(fpath)
            gate = SourceIngestionGate.evaluate_path_source(
                fpath,
                safe_label=fname,
                hydration_policy_enabled=False,
            )
            entry["source_gate_blocked"] = gate.blocked and gate.reason.startswith("cloud_")
            entry["source_gate_reason"] = gate.reason if entry["source_gate_blocked"] else ""
            yield entry


def generate_manifest(
    source_root: Path,
    existing_root: Path,
    target_root: Path,
    target_total: int = 1000,
    seed: int = 3301,
) -> dict:
    """Generate the candidate manifest.

    Returns a dict with:
      - candidates: list of row dicts (the CSV rows)
      - summary: dict of aggregate counts
    """
    existing_rows, duplicate_index, existing_count, existing_total_bytes = \
        _scan_existing_dataset(existing_root)

    if existing_count > target_total:
        raise ValueError(
            f"existing_supported_count ({existing_count}) exceeds "
            f"target_total ({target_total}). Cannot build a manifest "
            f"within the cap. Increase --target-total or reduce existing dataset."
        )

    needed = max(0, target_total - existing_count)

    all_source_entries = []
    stat_errors = 0
    placeholders = 0
    unsupported = 0
    duplicates = 0
    hidden = 0
    eligible = []

    for entry in _scan_source(source_root):
        if entry["stat_error"]:
            stat_errors += 1
            all_source_entries.append({
                **entry,
                "selection_reason": "",
                "exclusion_reason": "stat_error",
            })
            continue

        if entry["filename"].startswith("."):
            hidden += 1
            continue

        ext = entry["extension"]
        if ext not in SUPPORTED_EXTENSIONS:
            unsupported += 1
            all_source_entries.append({
                **entry,
                "selection_reason": "",
                "exclusion_reason": f"unsupported_format:{ext}",
            })
            continue

        if entry["is_placeholder"]:
            placeholders += 1
            all_source_entries.append({
                **entry,
                "selection_reason": "",
                "exclusion_reason": "placeholder",
            })
            continue

        dup_key = (entry["filename"].lower(), entry["size_bytes"])
        if dup_key in duplicate_index:
            duplicates += 1
            entry["possible_duplicate_with_existing"] = True
        else:
            entry["possible_duplicate_with_existing"] = False

        # Eligible candidate (possible duplicates are NOT excluded)
        eligible.append(entry)

    # Sort eligible by stable key before seeded sampling
    eligible.sort(key=lambda e: (
        str(e["path"].relative_to(source_root)).lower()
        if source_root in e["path"].parents or e["path"].parent == source_root
        else str(e["path"]).lower()
    ))

    # Deterministic selection
    if len(eligible) <= needed:
        selected = list(eligible)
        strategy = "all_eligible"
    else:
        rng = random.Random(seed)
        selected = rng.sample(eligible, needed)
        strategy = f"random_seed_{seed}"

    # Sort selected deterministically for stable output
    selected.sort(key=lambda e: e["filename"].lower())

    # Build collision-free target paths
    used_target_names: dict[str, int] = {}

    def _unique_target(fname: str, source_path: Path) -> Path:
        stem = Path(fname).stem
        ext = Path(fname).suffix
        key = fname.lower()
        if key not in used_target_names:
            used_target_names[key] = 0
            return target_root / fname
        used_target_names[key] += 1
        path_hash = hashlib.sha256(
            f"{source_path}|{fname}".encode()
        ).hexdigest()[:8]
        new_name = f"{stem}__{path_hash}{ext}"
        while new_name.lower() in used_target_names:
            path_hash = hashlib.sha256(
                f"{source_path}|{fname}|{used_target_names[key]}".encode()
            ).hexdigest()[:8]
            new_name = f"{stem}__{path_hash}{ext}"
            used_target_names[key] += 1
        used_target_names[new_name.lower()] = 0
        return target_root / new_name

    rows = []
    row_id = 0

    # Existing files (to be copied from existing_root)
    for ex_entry in existing_rows:
        row_id += 1
        proposed = _unique_target(ex_entry["filename"], ex_entry["path"])
        rows.append({
            "row_id": row_id,
            "source_path": str(ex_entry["path"]),
            "proposed_target_path": str(proposed),
            "extension": ex_entry["extension"],
            "size_bytes": ex_entry["size_bytes"],
            "selection_reason": "existing_tier500",
            "duplicate_key": "",
            "exclusion_reason": "",
            "placeholder_flag": False,
            "stat_error": False,
        })

    # New candidates from source
    selected_possible_duplicates = 0
    for entry in selected:
        row_id += 1
        proposed = _unique_target(entry["filename"], entry["path"])
        is_dup = entry.get("possible_duplicate_with_existing", False)
        if is_dup:
            selected_possible_duplicates += 1
        dup_key_str = ""
        if is_dup:
            dup_key_str = f"possible_duplicate:{entry['filename'].lower()}|{entry['size_bytes']}"
        rows.append({
            "row_id": row_id,
            "source_path": str(entry["path"]),
            "proposed_target_path": str(proposed),
            "extension": entry["extension"],
            "size_bytes": entry["size_bytes"],
            "selection_reason": "new_candidate",
            "duplicate_key": dup_key_str,
            "exclusion_reason": "",
            "placeholder_flag": False,
            "stat_error": False,
        })

    # Excluded rows (for the full manifest)
    for entry in all_source_entries:
        if entry.get("exclusion_reason"):
            row_id += 1
            rows.append({
                "row_id": row_id,
                "source_path": str(entry["path"]),
                "proposed_target_path": "",
                "extension": entry["extension"],
                "size_bytes": entry["size_bytes"],
                "selection_reason": "",
                "duplicate_key": entry.get("exclusion_reason", ""),
                "exclusion_reason": entry.get("exclusion_reason", ""),
                "placeholder_flag": entry.get("is_placeholder", False),
                "stat_error": entry.get("stat_error", False),
            })

    selected_total_bytes = sum(e["size_bytes"] for e in selected)

    summary = {
        "source_root": str(source_root),
        "existing_root": str(existing_root),
        "target_root": str(target_root),
        "target_total": target_total,
        "seed": seed,
        "strategy": strategy,
        "existing_supported_count": existing_count,
        "needed_new": needed,
        "source_total_scanned": (
            len(eligible) + stat_errors + placeholders + unsupported + hidden
        ),
        "source_supported_eligible": len(eligible),
        "source_placeholders": placeholders,
        "source_unsupported": unsupported,
        "possible_duplicates_with_existing": duplicates,
        "selected_possible_duplicates": selected_possible_duplicates,
        "source_stat_errors": stat_errors,
        "source_hidden": hidden,
        "selected_new_count": len(selected),
        "selected_new_total_bytes": selected_total_bytes,
        "existing_total_bytes": existing_total_bytes,
        "combined_total": existing_count + len(selected),
        "manifest_total_rows": len(rows),
        "source_ingestion_gate": {
            "source_kind": "path_source",
            "metadata_only": True,
            "applied": True,
            "cloud_blocked_count": sum(1 for entry in all_source_entries if entry.get("source_gate_blocked")),
            "manual_hydrate_formal_workflow": False,
            "paths_redacted": True,
        },
    }

    return {"candidates": rows, "summary": summary}


def write_csv(rows: list, output_path: Path):
    """Write manifest rows to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "row_id", "source_path", "proposed_target_path", "extension",
        "size_bytes", "selection_reason", "duplicate_key", "exclusion_reason",
        "placeholder_flag", "stat_error",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(summary: dict, output_path: Path):
    """Write summary JSON (privacy-safe — absolute paths are redacted)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    redacted = dict(summary)
    redacted["source_root_label"] = "iCloud Photos source"
    redacted["source_root_redacted"] = True
    redacted.pop("source_root", None)
    redacted["existing_root_label"] = "existing pilot dataset"
    redacted["existing_root_redacted"] = True
    redacted.pop("existing_root", None)
    redacted["target_root_label"] = "staging target directory"
    redacted["target_root_redacted"] = True
    redacted.pop("target_root", None)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(redacted, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description="Generate candidate manifest for iCloud-safe Tier-1000 staging (read-only)")
    parser.add_argument("--source-root", type=str, required=True,
                        help="Path to iCloud / source directory (read-only scan)")
    parser.add_argument("--existing-root", type=str, required=True,
                        help="Path to existing Tier-500 dataset")
    parser.add_argument("--target-root", type=str, required=True,
                        help="Proposed staging directory path")
    parser.add_argument("--target-total", type=int, default=1000,
                        help="Target total files (existing + new)")
    parser.add_argument("--seed", type=int, default=3301,
                        help="Random seed for deterministic selection")
    parser.add_argument("--output", type=str, required=True,
                        help="Output CSV manifest path")
    parser.add_argument("--summary-output", type=str, required=True,
                        help="Output JSON summary path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dry-run mode (read-only, generate manifest but do not copy)")
    args = parser.parse_args()

    source_root = Path(args.source_root)
    existing_root = Path(args.existing_root)
    target_root = Path(args.target_root)
    output_path = Path(args.output)
    summary_path = Path(args.summary_output)

    if not source_root.is_dir():
        print(f"ERROR: Source root does not exist: {source_root}", file=sys.stderr)
        sys.exit(1)

    if not existing_root.is_dir():
        print(f"ERROR: Existing root does not exist: {existing_root}", file=sys.stderr)
        sys.exit(1)

    print(f"Source root:   {source_root}")
    print(f"Existing root: {existing_root}")
    print(f"Target root:   {target_root}")
    print(f"Target total:  {args.target_total}")
    print(f"Seed:          {args.seed}")
    print(f"Mode:          {'dry-run' if args.dry_run else 'generate'}")
    print()

    try:
        validate_output_paths(output_path, summary_path, source_root, target_root)
        result = generate_manifest(
            source_root=source_root,
            existing_root=existing_root,
            target_root=target_root,
            target_total=args.target_total,
            seed=args.seed,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    write_csv(result["candidates"], output_path)
    write_summary(result["summary"], summary_path)

    s = result["summary"]
    print("=== Manifest Summary ===")
    print(f"  Existing (Tier-500):     {s['existing_supported_count']}")
    print(f"  Needed new:              {s['needed_new']}")
    print(f"  Source scanned:          {s['source_total_scanned']}")
    print(f"  Source eligible:         {s['source_supported_eligible']}")
    print(f"  Source placeholders:     {s['source_placeholders']}")
    print(f"  Source unsupported:      {s['source_unsupported']}")
    print(f"  Source possible dupes:   {s['possible_duplicates_with_existing']}")
    print(f"  Selected possible dupes: {s['selected_possible_duplicates']}")
    print(f"  Source stat errors:      {s['source_stat_errors']}")
    print(f"  Source hidden:           {s['source_hidden']}")
    print(f"  Selected new:            {s['selected_new_count']}")
    print(f"  Strategy:                {s['strategy']}")
    print(f"  Combined total:          {s['combined_total']}")
    print(f"  Manifest rows:           {s['manifest_total_rows']}")
    print()
    print(f"  CSV manifest:   {output_path}")
    print(f"  JSON summary:   {summary_path}")

    if args.dry_run:
        print()
        print("  [DRY-RUN] No files were copied. Review the manifest and summary.")

    sys.exit(0)


if __name__ == "__main__":
    main()
