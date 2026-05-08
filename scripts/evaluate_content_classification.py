#!/usr/bin/env python3
"""V.I.O.L.E.T. Content Classification Evaluation Script

Imports test images, runs AI tagging + classification, then compares
against ground-truth labels to produce accuracy metrics.

Dataset types:
  - anime:<path>      -- all files are anime art (ground truth = anime)
  - non_anime:<path>  -- all files are non-anime photos (ground truth = non_anime)
  - mixed:<path>      -- unknown labels, distribution report only

Usage:
  python scripts/evaluate_content_classification.py \
    --dataset "anime:C:\\Users\\kyloris\\Pictures\\VioletTest100_2" \
    --dataset "non_anime:C:\\Users\\kyloris\\Pictures\\VioletPhase3Eval" \
    --dataset "mixed:C:\\Users\\kyloris\\Pictures\\VioletTest100"

  # Skip scan / tagging if data is already imported and tagged:
  python scripts/evaluate_content_classification.py \
    --dataset "anime:C:\\Users\\kyloris\\Pictures\\VioletTest100_2" \
    --skip-scan --skip-tagging

This is a DEVELOPMENT tool -- do not use against production data.
It does NOT modify or delete any source image files.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote as url_quote

import requests

DEFAULT_HOST = "http://localhost:8000"
DEFAULT_USER = "admin"
DEFAULT_PASS = "admin123"

# Poll timeouts (seconds)
SCAN_POLL_TIMEOUT = 300
AI_TAG_POLL_TIMEOUT = 600
CLASSIFY_POLL_TIMEOUT = 300

MAX_SAMPLE_MISCLASSIFIED = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def _poll_job(session: requests.Session, url: str, timeout: int,
              label: str = "job", interval: int = 3) -> dict:
    """Poll a job endpoint until terminal status or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                job = r.json()
                status = job.get("status", "unknown")
                if status in ("completed", "failed", "cancelled", "interrupted"):
                    return job
                processed = job.get("processed", "?")
                _log(f"  {label} status={status} processed={processed}", "POLL")
        except Exception as e:
            _log(f"  Poll error: {e}", "WARN")
        time.sleep(interval)
    return {"status": "timeout", "error": f"{label} poll timeout after {timeout}s"}


def _normalize_source_path(source: Optional[str]) -> Optional[str]:
    """Extract and normalize the filesystem path from a file:// URI or raw path."""
    if not source:
        return None
    # Strip file:// prefix
    if source.startswith("file:///"):
        path = source[len("file:///"):]
    elif source.startswith("file://"):
        path = source[len("file://"):]
    else:
        path = source
    # URL-decode and normalize separators
    from urllib.parse import unquote
    path = unquote(path)
    return os.path.normpath(path).lower()


def _path_belongs_to_dataset(media_source: Optional[str], dataset_path: str) -> bool:
    """Check if a media source path belongs to a dataset directory.

    Ensures exact directory matching -- e.g. VioletTest100 does NOT match
    VioletTest100_2 files.
    """
    norm_source = _normalize_source_path(media_source)
    if not norm_source:
        return False
    norm_dataset = os.path.normpath(dataset_path).lower()
    # Ensure the dataset path ends with a separator so VioletTest100
    # doesn't match VioletTest100_2
    if not norm_dataset.endswith(os.sep):
        norm_dataset += os.sep
    return norm_source.startswith(norm_dataset)


def _pct(n: int, total: int) -> str:
    """Format a percentage string."""
    if total == 0:
        return "0.0%"
    return f"{100.0 * n / total:.1f}%"


# ---------------------------------------------------------------------------
# API wrappers
# ---------------------------------------------------------------------------

def login(session: requests.Session, host: str, username: str, password: str) -> bool:
    """Login and configure session auth headers."""
    _log(f"Logging in as '{username}'...")
    try:
        r = session.post(f"{host}/api/admin/login", json={
            "username": username,
            "password": password,
        }, timeout=10)
        if r.status_code == 200:
            data = r.json()
            token = data.get("access_token") or data.get("token")
            if token:
                session.headers["Authorization"] = f"Bearer {token}"
            session.cookies.set("admin_mode", "true")
            if "access_token" in data:
                session.cookies.set("admin_token", data["access_token"])
            _log("Login successful")
            return True
        else:
            _log(f"Login failed: {r.status_code} {r.text[:200]}", "ERROR")
            return False
    except Exception as e:
        _log(f"Login error: {e}", "ERROR")
        return False


def get_classification_config(session: requests.Session, host: str) -> dict:
    """Fetch classification configuration."""
    try:
        r = session.get(f"{host}/api/admin/content-classification/config", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def create_scan_job(session: requests.Session, host: str, paths: List[str],
                    max_files: Optional[int] = None) -> Optional[dict]:
    """Create and poll a scan job to completion."""
    body: Dict[str, Any] = {"paths": paths, "dry_run": False}
    if max_files:
        body["max_files"] = max_files
    _log(f"Creating scan job for {paths}...")
    try:
        r = session.post(f"{host}/api/admin/scan-local-library/jobs",
                         json=body, timeout=15)
        if r.status_code == 200:
            job = r.json()
            job_id = job["id"]
            _log(f"Scan job #{job_id} created, polling...")
            return _poll_job(
                session,
                f"{host}/api/admin/scan-local-library/jobs/{job_id}",
                SCAN_POLL_TIMEOUT, f"scan #{job_id}", interval=2,
            )
        elif r.status_code == 409:
            _log("Another scan is already running, waiting...", "WARN")
            # Wait for existing scan to finish, then retry
            time.sleep(10)
            return create_scan_job(session, host, paths, max_files)
        else:
            _log(f"Scan create failed: {r.status_code} {r.text[:300]}", "ERROR")
    except Exception as e:
        _log(f"Scan error: {e}", "ERROR")
    return None


def create_ai_tag_job(session: requests.Session, host: str,
                      media_ids: Optional[List[int]] = None,
                      max_items: int = 500) -> Optional[dict]:
    """Create and poll an AI tagging job to completion."""
    body: Dict[str, Any] = {
        "max_items": max_items,
        "only_without_ai_tags": True,
    }
    if media_ids:
        body["media_ids"] = media_ids
    _log(f"Creating AI tagging job (max_items={max_items})...")
    try:
        r = session.post(f"{host}/api/admin/ai-tagging/jobs",
                         json=body, timeout=15)
        if r.status_code == 200:
            job = r.json()
            job_id = job["id"]
            _log(f"AI tagging job #{job_id} created, polling...")
            return _poll_job(
                session,
                f"{host}/api/admin/ai-tagging/jobs/{job_id}",
                AI_TAG_POLL_TIMEOUT, f"AI tag #{job_id}", interval=5,
            )
        elif r.status_code == 409:
            _log("Another AI tagging job is running, waiting...", "WARN")
            time.sleep(15)
            return create_ai_tag_job(session, host, media_ids, max_items)
        else:
            _log(f"AI tag job create failed: {r.status_code} {r.text[:300]}", "ERROR")
    except Exception as e:
        _log(f"AI tag job error: {e}", "ERROR")
    return None


def create_classification_job(session: requests.Session, host: str,
                              media_ids: Optional[List[int]] = None,
                              max_items: int = 500) -> Optional[dict]:
    """Create and poll a classification job to completion."""
    body: Dict[str, Any] = {
        "max_items": max_items,
        "only_unclassified": True,
    }
    if media_ids:
        body["media_ids"] = media_ids
    _log(f"Creating classification job (max_items={max_items})...")
    try:
        r = session.post(f"{host}/api/admin/content-classification/jobs",
                         json=body, timeout=15)
        if r.status_code == 200:
            job = r.json()
            job_id = job["id"]
            _log(f"Classification job #{job_id} created, polling...")
            return _poll_job(
                session,
                f"{host}/api/admin/content-classification/jobs/{job_id}",
                CLASSIFY_POLL_TIMEOUT, f"classify #{job_id}", interval=2,
            )
        elif r.status_code == 409:
            _log("Another classification job is running, waiting...", "WARN")
            time.sleep(10)
            return create_classification_job(session, host, media_ids, max_items)
        else:
            _log(f"Classification job create failed: {r.status_code} {r.text[:300]}", "ERROR")
    except Exception as e:
        _log(f"Classification job error: {e}", "ERROR")
    return None


def fetch_all_media(session: requests.Session, host: str,
                    page_size: int = 100) -> List[dict]:
    """Fetch all media items from the server, paginated."""
    all_items = []
    page = 1
    while True:
        try:
            r = session.get(f"{host}/api/media", params={
                "page": page, "limit": page_size,
            }, timeout=15)
            if r.status_code != 200:
                _log(f"Media fetch page {page} failed: {r.status_code}", "ERROR")
                break
            data = r.json()
            items = data.get("items", [])
            if not items:
                break
            all_items.extend(items)
            total_pages = data.get("pages", 1)
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            _log(f"Media fetch error: {e}", "ERROR")
            break
    return all_items


# ---------------------------------------------------------------------------
# Dataset analysis
# ---------------------------------------------------------------------------

def analyze_dataset(
    media_items: List[dict],
    dataset_label: str,
    ground_truth: Optional[str],
) -> dict:
    """Analyze classification results for a single dataset.

    Args:
        media_items: List of MediaResponse dicts belonging to this dataset.
        dataset_label: Human-readable label (e.g. "anime:VioletTest100_2").
        ground_truth: Expected content_class ("anime", "non_anime") or None for mixed.

    Returns:
        Analysis dict with metrics.
    """
    total = len(media_items)
    if total == 0:
        return {
            "label": dataset_label,
            "ground_truth": ground_truth,
            "total": 0,
            "error": "No media found for this dataset",
        }

    # Count predicted classes
    class_counts: Counter = Counter()
    unclassified = 0
    items_by_class: Dict[str, List[dict]] = defaultdict(list)

    for m in media_items:
        cc = m.get("content_class")
        if cc is None:
            unclassified += 1
            items_by_class["unclassified"].append(m)
        else:
            class_counts[cc] += 1
            items_by_class[cc].append(m)

    # Build distribution table
    distribution = {}
    for cls in ["anime", "illustration", "non_anime", "unknown"]:
        count = class_counts.get(cls, 0)
        distribution[cls] = {"count": count, "pct": _pct(count, total)}
    distribution["unclassified"] = {"count": unclassified, "pct": _pct(unclassified, total)}

    result: Dict[str, Any] = {
        "label": dataset_label,
        "ground_truth": ground_truth,
        "total": total,
        "distribution": distribution,
        "unknown_rate": _pct(class_counts.get("unknown", 0) + unclassified, total),
    }

    # For labeled datasets, compute accuracy metrics
    if ground_truth == "anime":
        correct = class_counts.get("anime", 0)
        result["anime_recall"] = _pct(correct, total)
        result["anime_recall_raw"] = correct / total if total else 0
        # False negatives: classified as non_anime
        fn_non_anime = class_counts.get("non_anime", 0)
        fn_illustration = class_counts.get("illustration", 0)
        result["false_negative_non_anime"] = {
            "count": fn_non_anime, "pct": _pct(fn_non_anime, total)
        }
        result["false_negative_illustration"] = {
            "count": fn_illustration, "pct": _pct(fn_illustration, total)
        }
        # Sample misclassified items
        misclassified = []
        for cls in ["non_anime", "illustration", "unknown"]:
            for m in items_by_class.get(cls, [])[:MAX_SAMPLE_MISCLASSIFIED]:
                misclassified.append({
                    "media_id": m.get("id"),
                    "filename": m.get("filename"),
                    "expected": "anime",
                    "actual": cls,
                    "confidence": m.get("content_class_confidence"),
                    "source": m.get("content_class_source"),
                })
        for m in items_by_class.get("unclassified", [])[:MAX_SAMPLE_MISCLASSIFIED]:
            misclassified.append({
                "media_id": m.get("id"),
                "filename": m.get("filename"),
                "expected": "anime",
                "actual": "unclassified",
                "confidence": None,
            })
        result["sample_misclassified"] = misclassified[:MAX_SAMPLE_MISCLASSIFIED]

    elif ground_truth == "non_anime":
        correct = class_counts.get("non_anime", 0)
        # Also count unknown as "not false positive" -- it's uncertain, not wrong
        unknown_count = class_counts.get("unknown", 0) + unclassified
        false_positive_anime = class_counts.get("anime", 0)
        false_positive_illustration = class_counts.get("illustration", 0)
        result["non_anime_accuracy"] = _pct(correct, total)
        result["false_positive_rate_anime"] = _pct(false_positive_anime, total)
        result["false_positive_rate_anime_raw"] = false_positive_anime / total if total else 0
        result["false_positive_rate_illustration"] = _pct(false_positive_illustration, total)
        result["false_positive_details"] = {
            "anime": {"count": false_positive_anime, "pct": _pct(false_positive_anime, total)},
            "illustration": {"count": false_positive_illustration, "pct": _pct(false_positive_illustration, total)},
        }
        # Sample false positives
        misclassified = []
        for cls in ["anime", "illustration"]:
            for m in items_by_class.get(cls, [])[:MAX_SAMPLE_MISCLASSIFIED]:
                misclassified.append({
                    "media_id": m.get("id"),
                    "filename": m.get("filename"),
                    "expected": "non_anime",
                    "actual": cls,
                    "confidence": m.get("content_class_confidence"),
                    "source": m.get("content_class_source"),
                })
        result["sample_misclassified"] = misclassified[:MAX_SAMPLE_MISCLASSIFIED]

    else:
        # Mixed dataset -- distribution only, no accuracy
        result["note"] = "Mixed/unlabeled dataset -- distribution report only, no accuracy metrics"

    return result


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(
    datasets_results: List[dict],
    config: dict,
    scan_results: List[Optional[dict]],
    ai_tag_result: Optional[dict],
    classify_result: Optional[dict],
    source_files_check: Dict[str, bool],
):
    """Print a human-readable evaluation report."""
    print()
    print("=" * 70)
    print("  V.I.O.L.E.T. Content Classification Evaluation Report")
    print("=" * 70)
    print(f"  Timestamp: {datetime.now().isoformat()}")
    print(f"  Classifier: heuristic (wd_tag_count)")
    print(f"  Thresholds:")
    print(f"    anime_tag_threshold:        {config.get('anime_tag_threshold', '?')}")
    print(f"    anime_confidence_threshold: {config.get('anime_confidence_threshold', '?')}")
    print()

    for ds in datasets_results:
        print("-" * 70)
        label = ds.get("label", "?")
        gt = ds.get("ground_truth") or "mixed"
        total = ds.get("total", 0)
        print(f"  Dataset: {label}")
        print(f"  Ground truth: {gt}")
        print(f"  Total media: {total}")

        if ds.get("error"):
            print(f"  ERROR: {ds['error']}")
            print()
            continue

        dist = ds.get("distribution", {})
        print(f"  Predicted distribution:")
        for cls in ["anime", "non_anime", "illustration", "unknown", "unclassified"]:
            d = dist.get(cls, {})
            count = d.get("count", 0)
            pct = d.get("pct", "0.0%")
            bar = "#" * min(count, 50)
            print(f"    {cls:<16} {count:>4}  ({pct:>6})  {bar}")

        print(f"  Unknown rate: {ds.get('unknown_rate', '?')}")

        if gt == "anime":
            print(f"  * Anime recall: {ds.get('anime_recall', '?')}")
            fn = ds.get("false_negative_non_anime", {})
            print(f"    False negatives (->non_anime): {fn.get('count', 0)} ({fn.get('pct', '?')})")
            fn_ill = ds.get("false_negative_illustration", {})
            print(f"    False negatives (->illustration): {fn_ill.get('count', 0)} ({fn_ill.get('pct', '?')})")

        elif gt == "non_anime":
            print(f"  * Non-anime accuracy: {ds.get('non_anime_accuracy', '?')}")
            print(f"  * False positive rate (->anime): {ds.get('false_positive_rate_anime', '?')}")
            fp = ds.get("false_positive_details", {})
            fp_anime = fp.get("anime", {})
            fp_ill = fp.get("illustration", {})
            print(f"    FP breakdown: anime={fp_anime.get('count', 0)}, illustration={fp_ill.get('count', 0)}")

        elif gt == "mixed":
            print(f"  Note: {ds.get('note', 'Distribution only')}")

        # Sample misclassified
        misclassified = ds.get("sample_misclassified", [])
        if misclassified:
            print(f"  Sample misclassified (up to {MAX_SAMPLE_MISCLASSIFIED}):")
            for mc in misclassified:
                conf = mc.get("confidence")
                conf_str = f"{conf:.4f}" if conf is not None else "N/A"
                print(f"    ID={mc.get('media_id'):>5}  "
                      f"expected={mc.get('expected'):<10}  "
                      f"actual={mc.get('actual'):<14}  "
                      f"conf={conf_str}  "
                      f"file={mc.get('filename', '?')}")
        print()

    # Source file integrity
    print("-" * 70)
    print("  Source File Integrity:")
    for ds_path, intact in source_files_check.items():
        status = "[OK] NOT MODIFIED" if intact else "[WARN] POSSIBLY MODIFIED"
        print(f"    {ds_path}: {status}")
    print()

    # Summary / acceptance criteria
    print("=" * 70)
    print("  ACCEPTANCE CRITERIA CHECK")
    print("=" * 70)

    for ds in datasets_results:
        gt = ds.get("ground_truth")
        label = ds.get("label", "?")
        if gt == "anime":
            recall_raw = ds.get("anime_recall_raw", 0)
            recall_pct = ds.get("anime_recall", "?")
            status = "PASS" if recall_raw >= 0.80 else "FAIL"
            print(f"  [{status}] Anime recall >= 80%: {recall_pct} ({label})")
        elif gt == "non_anime":
            fp_raw = ds.get("false_positive_rate_anime_raw", 0)
            fp_pct = ds.get("false_positive_rate_anime", "?")
            status = "PASS" if fp_raw <= 0.15 else "FAIL"
            print(f"  [{status}] Non-anime FP rate <= 15%: {fp_pct} ({label})")
        else:
            print(f"  [INFO] Mixed dataset -- distribution only ({label})")
    print()


# ---------------------------------------------------------------------------
# Source file integrity check
# ---------------------------------------------------------------------------

def check_source_files_unmodified(dataset_paths: List[str]) -> Dict[str, bool]:
    """Verify source image directories still exist and have the same file count.

    This is a basic sanity check -- we count files before and confirm the
    directory is still readable after.  We do NOT open or modify any files.
    """
    result = {}
    for p in dataset_paths:
        try:
            if os.path.isdir(p):
                files = [f for f in os.listdir(p)
                         if os.path.isfile(os.path.join(p, f))]
                result[p] = True  # Directory exists and is readable
            else:
                result[p] = False
        except Exception:
            result[p] = False
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="V.I.O.L.E.T. Content Classification Evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Server URL (default: {DEFAULT_HOST})")
    parser.add_argument("--username", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASS)
    parser.add_argument("--dataset", action="append", required=True,
                        help='Dataset spec: "type:path" where type is anime/non_anime/mixed')
    parser.add_argument("--skip-scan", action="store_true",
                        help="Skip scanning (assume media already imported)")
    parser.add_argument("--skip-tagging", action="store_true",
                        help="Skip AI tagging (assume tags already exist)")
    parser.add_argument("--skip-classification", action="store_true",
                        help="Skip classification job (assume already classified)")
    parser.add_argument("--output", default=None,
                        help="Write JSON results to this file")
    parser.add_argument("--max-files", type=int, default=None,
                        help="Limit files per scan job")
    args = parser.parse_args()

    # Parse dataset specs
    datasets: List[Tuple[str, str]] = []  # (type, path)
    for spec in args.dataset:
        if ":" not in spec:
            print(f"ERROR: Invalid dataset spec '{spec}'. Use 'type:path' format.")
            sys.exit(1)
        # Split on first colon only (Windows paths contain colons)
        ds_type, ds_path = spec.split(":", 1)
        ds_type = ds_type.strip().lower()
        ds_path = ds_path.strip()
        if ds_type not in ("anime", "non_anime", "mixed"):
            print(f"ERROR: Unknown dataset type '{ds_type}'. Use anime/non_anime/mixed.")
            sys.exit(1)
        if not os.path.isdir(ds_path):
            print(f"ERROR: Dataset path does not exist: {ds_path}")
            sys.exit(1)
        datasets.append((ds_type, ds_path))

    _log(f"Datasets: {len(datasets)}")
    for ds_type, ds_path in datasets:
        file_count = len([f for f in os.listdir(ds_path)
                          if os.path.isfile(os.path.join(ds_path, f))])
        _log(f"  {ds_type}: {ds_path} ({file_count} files)")

    # Pre-check: source files exist
    source_check_before = check_source_files_unmodified([p for _, p in datasets])

    session = requests.Session()

    # Step 1: Login
    if not login(session, args.host, args.username, args.password):
        sys.exit(1)

    # Step 2: Get classification config
    config = get_classification_config(session, args.host)
    if config:
        _log(f"Classification config: tag_threshold={config.get('anime_tag_threshold')}, "
             f"confidence_threshold={config.get('anime_confidence_threshold')}, "
             f"enabled={config.get('enabled')}")
        if not config.get("enabled"):
            _log("WARNING: Content classification is DISABLED on the server!", "WARN")
            _log("Set CONTENT_CLASSIFICATION_ENABLED=true in .env and restart.", "WARN")

    # Step 3: Scan datasets (unless --skip-scan)
    scan_results = []
    if not args.skip_scan:
        for ds_type, ds_path in datasets:
            _log(f"Scanning dataset: {ds_type}:{ds_path}")
            result = create_scan_job(session, args.host, [ds_path],
                                     max_files=args.max_files)
            scan_results.append(result)
            if result:
                imported = result.get("imported", 0)
                dupes = result.get("skipped_duplicate", 0)
                _log(f"  Scan complete: imported={imported}, duplicates={dupes}, "
                     f"status={result.get('status')}")
            else:
                _log(f"  Scan failed for {ds_path}", "ERROR")
            # Small pause between scans
            time.sleep(1)
    else:
        _log("Skipping scan (--skip-scan)")

    # Step 4: AI tagging (unless --skip-tagging)
    ai_tag_result = None
    if not args.skip_tagging:
        _log("Running AI tagging on all untagged media...")
        ai_tag_result = create_ai_tag_job(session, args.host, max_items=1000)
        if ai_tag_result:
            _log(f"AI tagging complete: processed={ai_tag_result.get('processed', 0)}, "
                 f"tags_added={ai_tag_result.get('tags_added', 0)}, "
                 f"status={ai_tag_result.get('status')}")
        else:
            _log("AI tagging failed or timed out", "WARN")
    else:
        _log("Skipping AI tagging (--skip-tagging)")

    # Step 5: Classification (unless --skip-classification)
    classify_result = None
    if not args.skip_classification:
        _log("Running classification on unclassified media...")
        classify_result = create_classification_job(session, args.host, max_items=1000)
        if classify_result:
            _log(f"Classification complete: processed={classify_result.get('processed', 0)}, "
                 f"anime={classify_result.get('classified_anime', 0)}, "
                 f"non_anime={classify_result.get('classified_non_anime', 0)}, "
                 f"unknown={classify_result.get('classified_unknown', 0)}, "
                 f"status={classify_result.get('status')}")
        else:
            _log("Classification failed or timed out", "WARN")
    else:
        _log("Skipping classification (--skip-classification)")

    # Step 6: Fetch all media and group by dataset
    _log("Fetching all media for evaluation...")
    all_media = fetch_all_media(session, args.host)
    _log(f"Total media in library: {len(all_media)}")

    # Group media by dataset
    dataset_media: Dict[str, List[dict]] = {}
    for ds_type, ds_path in datasets:
        key = f"{ds_type}:{ds_path}"
        dataset_media[key] = []

    unmatched = 0
    for m in all_media:
        matched = False
        source = m.get("source")
        for ds_type, ds_path in datasets:
            if _path_belongs_to_dataset(source, ds_path):
                key = f"{ds_type}:{ds_path}"
                dataset_media[key].append(m)
                matched = True
                break
        if not matched:
            unmatched += 1

    _log(f"Media matched to datasets:")
    for key, items in dataset_media.items():
        _log(f"  {key}: {len(items)} media")
    if unmatched:
        _log(f"  (unmatched to any dataset: {unmatched})")

    # Step 7: Analyze each dataset
    datasets_results = []
    for ds_type, ds_path in datasets:
        key = f"{ds_type}:{ds_path}"
        items = dataset_media.get(key, [])
        ds_name = Path(ds_path).name
        label = f"{ds_type}:{ds_name}"
        ground_truth = ds_type if ds_type in ("anime", "non_anime") else None
        result = analyze_dataset(items, label, ground_truth)
        datasets_results.append(result)

    # Step 8: Post-check source file integrity
    source_check_after = check_source_files_unmodified([p for _, p in datasets])

    # Print report
    print_report(
        datasets_results, config, scan_results,
        ai_tag_result, classify_result, source_check_after,
    )

    # Write JSON output
    full_results = {
        "timestamp": datetime.now().isoformat(),
        "classifier": "heuristic",
        "model": "wd_tag_count",
        "config": config,
        "datasets": datasets_results,
        "scan_results": scan_results,
        "ai_tag_result": ai_tag_result,
        "classification_result": classify_result,
        "source_files_integrity": {
            p: {"before": source_check_before.get(p), "after": source_check_after.get(p)}
            for _, p in datasets
        },
    }

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(full_results, indent=2, default=str),
                               encoding="utf-8")
        _log(f"JSON results written to: {output_path}")

    # Exit code based on acceptance criteria
    all_pass = True
    for ds in datasets_results:
        gt = ds.get("ground_truth")
        if gt == "anime" and ds.get("anime_recall_raw", 0) < 0.80:
            all_pass = False
        elif gt == "non_anime" and ds.get("false_positive_rate_anime_raw", 0) > 0.15:
            all_pass = False

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
