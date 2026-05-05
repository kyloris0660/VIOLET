#!/usr/bin/env python3
"""Reset E2E test data imported from a specific source directory.

Usage:
    # Dry-run (default) - shows what would be deleted:
    python scripts/reset_e2e_test_data.py --source-path "C:\\Users\\kyloris\\Pictures\\VioletTest100"

    # Real deletion:
    python scripts/reset_e2e_test_data.py --source-path "C:\\Users\\kyloris\\Pictures\\VioletTest100" --yes

This script calls the V.I.O.L.E.T. admin API to safely remove media
imported from the specified source directory, along with associated
scan jobs, AI tag jobs, copied files, and thumbnails.

The original source directory is NEVER modified.
"""
import argparse
import sys

import requests


def main():
    parser = argparse.ArgumentParser(
        description="Reset V.I.O.L.E.T. E2E test data from a specific source path"
    )
    parser.add_argument(
        "--source-path", required=True,
        help="Source directory path to reset (e.g., C:\\Users\\kyloris\\Pictures\\VioletTest100)"
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Actually delete data (default is dry-run)"
    )
    parser.add_argument("--host", default="http://localhost:8000", help="Server URL")
    parser.add_argument("--username", default="admin", help="Admin username")
    parser.add_argument("--password", default="admin123", help="Admin password")
    args = parser.parse_args()

    base = args.host.rstrip("/")
    session = requests.Session()

    # 1. Health check
    print(f"[1/4] Checking server at {base} ...")
    try:
        r = session.get(f"{base}/", timeout=5)
        r.raise_for_status()
        print("      Server is running.")
    except Exception as e:
        print(f"      ERROR: Server not reachable: {e}")
        sys.exit(1)

    # 2. Login
    print(f"[2/4] Logging in as '{args.username}' ...")
    try:
        r = session.post(
            f"{base}/api/admin/login",
            json={"username": args.username, "password": args.password},
            timeout=10,
        )
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            print("      ERROR: No access_token in response")
            sys.exit(1)
        session.headers["Authorization"] = f"Bearer {token}"
        session.cookies.set("admin_mode", "true")
        print("      Login successful.")
    except Exception as e:
        print(f"      ERROR: Login failed: {e}")
        sys.exit(1)

    # 3. Dry-run first
    print(f"[3/4] Computing reset summary for: {args.source_path} ...")
    try:
        r = session.post(
            f"{base}/api/admin/dev/reset-e2e-test-data",
            json={
                "source_path": args.source_path,
                "dry_run": True,
                "confirm": False,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        summary = data.get("summary", {})

        print("\n      === Dry-Run Summary ===")
        print(f"      Media to delete:        {summary.get('media_count', 0)}")
        print(f"      Copied files to remove: {summary.get('copied_files_count', 0)}")
        print(f"      Thumbnails to remove:   {summary.get('thumbnail_files_count', 0)}")
        print(f"      Tag associations:       {summary.get('tag_associations_count', 0)}")
        print(f"      Affected tags:          {summary.get('affected_tags_count', 0)}")
        print(f"      Scan jobs to delete:    {summary.get('scan_job_count', 0)}")
        print(f"      AI tag jobs to delete:  {summary.get('ai_tag_job_count', 0)}")
        print(f"      Scan-job-media links:   {summary.get('scan_job_media_count', 0)}")
        print()

        if summary.get("media_count", 0) == 0:
            print("      No data found to reset. Nothing to do.")
            sys.exit(0)

    except Exception as e:
        print(f"      ERROR: Dry-run failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"      Response: {e.response.text[:500]}")
        sys.exit(1)

    # 4. Real deletion (only if --yes)
    if not args.yes:
        print("      Dry-run only. Use --yes to actually delete data.")
        print("      WARNING: Original source directory is NEVER modified.")
        sys.exit(0)

    print("[4/4] Executing real reset ...")
    print()
    print("      *** WARNING ***")
    print(f"      This will DELETE all data imported from: {args.source_path}")
    print("      Original source directory will NOT be modified.")
    print()
    confirm = input("      Type 'yes' to confirm deletion: ").strip()
    if confirm != "yes":
        print("      Cancelled.")
        sys.exit(0)

    try:
        r = session.post(
            f"{base}/api/admin/dev/reset-e2e-test-data",
            json={
                "source_path": args.source_path,
                "dry_run": False,
                "confirm": True,
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("summary", {})

        print("\n      === Reset Complete ===")
        print(f"      Media deleted:            {result.get('media_deleted', 0)}")
        print(f"      Files deleted:            {result.get('files_deleted', 0)}")
        print(f"      Thumbnails deleted:       {result.get('thumbnails_deleted', 0)}")
        print(f"      Tag associations deleted: {result.get('tag_associations_deleted', 0)}")
        print(f"      Scan-job-media deleted:   {result.get('scan_job_media_deleted', 0)}")
        print(f"      Scan jobs deleted:        {result.get('scan_jobs_deleted', 0)}")
        print(f"      AI tag jobs deleted:      {result.get('ai_tag_jobs_deleted', 0)}")
        print(f"      Tags recalculated:        {result.get('tags_recalculated', 0)}")
        print()
        print("      You can now re-run E2E validation from scratch.")

    except Exception as e:
        print(f"      ERROR: Reset failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"      Response: {e.response.text[:500]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
