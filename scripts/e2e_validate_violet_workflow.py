#!/usr/bin/env python3
"""V.I.O.L.E.T. End-to-End Workflow Validation Script

Validates the full pipeline:
  Local Library Scan → AI Tagging → Tag Localization → Review → Search

Usage:
  python scripts/e2e_validate_violet_workflow.py
  python scripts/e2e_validate_violet_workflow.py --path "D:\\TestImages" --max-files 50

This is a DEVELOPMENT tool — do not use against production data.
It does NOT read .env or API keys. The server must be running.
"""
import argparse
import json
import sys
import time

import requests

DEFAULT_HOST = "http://localhost:8000"
DEFAULT_USER = "admin"
DEFAULT_PASS = "admin123"
DEFAULT_PATH = r"C:\Users\kyloris\Pictures\VioletTest100"
DEFAULT_MAX_FILES = 20


def main():
    parser = argparse.ArgumentParser(description="V.I.O.L.E.T. E2E Validation")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--username", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASS)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--skip-scan", action="store_true", help="Skip scan, only check AI jobs")
    args = parser.parse_args()

    session = requests.Session()
    results = {}

    print("=" * 60)
    print("V.I.O.L.E.T. End-to-End Workflow Validation")
    print("=" * 60)

    # Step 1: Health check
    print("\n[1/8] Checking server...")
    try:
        r = session.get(f"{args.host}/api/admin/ai-tagging/model-status", timeout=5)
        if r.status_code == 401 or r.status_code == 403:
            print("  Server is running (auth required)")
        elif r.status_code == 200:
            print("  Server is running")
        else:
            print(f"  Warning: unexpected status {r.status_code}")
    except requests.ConnectionError:
        print(f"  ERROR: Cannot connect to {args.host}")
        print("  Start the server: python run.py --debug")
        sys.exit(1)

    # Step 2: Login
    print("\n[2/8] Logging in...")
    try:
        r = session.post(f"{args.host}/api/admin/login", json={
            "username": args.username,
            "password": args.password,
        })
        if r.status_code == 200:
            data = r.json()
            token = data.get("access_token") or data.get("token")
            if token:
                session.headers["Authorization"] = f"Bearer {token}"
            session.cookies.set("admin_mode", "true")
            if "access_token" in data:
                session.cookies.set("access_token", data["access_token"])
            print("  Login successful")
        else:
            print(f"  Login failed: {r.status_code} {r.text[:200]}")
            sys.exit(1)
    except Exception as e:
        print(f"  Login error: {e}")
        sys.exit(1)

    # Step 3: Check auto-tag config
    print("\n[3/8] Checking auto-tag configuration...")
    try:
        r = session.get(f"{args.host}/api/admin/ai-tagging/auto-config")
        if r.status_code == 200:
            config = r.json()
            for k, v in config.items():
                print(f"  {k}: {v}")
            results["config"] = config
        else:
            print(f"  Warning: {r.status_code}")
    except Exception as e:
        print(f"  Error: {e}")

    if args.skip_scan:
        print("\n  Skipping scan (--skip-scan)")
    else:
        # Step 4: Dry-run scan
        print(f"\n[4/8] Creating dry-run scan job ({args.path}, max_files={args.max_files})...")
        try:
            r = session.post(f"{args.host}/api/admin/scan-local-library/jobs", json={
                "paths": [args.path],
                "dry_run": True,
                "max_files": args.max_files,
            })
            if r.status_code == 200:
                job = r.json()
                job_id = job["id"]
                print(f"  Dry-run job #{job_id} created")
                job = _poll_scan_job(session, args.host, job_id)
                results["dry_run"] = job
                print(f"  Status: {job['status']}, would import: {job.get('imported', 0)}")
            else:
                print(f"  Failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  Error: {e}")

        # Step 5: Real scan
        print(f"\n[5/8] Creating real scan job ({args.path}, max_files={args.max_files})...")
        try:
            r = session.post(f"{args.host}/api/admin/scan-local-library/jobs", json={
                "paths": [args.path],
                "dry_run": False,
                "max_files": args.max_files,
            })
            if r.status_code == 200:
                job = r.json()
                job_id = job["id"]
                print(f"  Scan job #{job_id} created")
                job = _poll_scan_job(session, args.host, job_id)
                results["scan"] = job
                print(f"  Status: {job['status']}, imported: {job.get('imported', 0)}")
            elif r.status_code == 409:
                print("  Another scan is running, skipping...")
            else:
                print(f"  Failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  Error: {e}")

    # Step 6: Check AI tag jobs
    print("\n[6/8] Checking AI tagging jobs...")
    time.sleep(2)
    try:
        r = session.get(f"{args.host}/api/admin/ai-tagging/jobs")
        if r.status_code == 200:
            jobs = r.json()
            if jobs:
                latest = jobs[0]
                print(f"  Latest AI job: #{latest['id']} status={latest['status']} trigger={latest['trigger_source']}")
                if latest["status"] in ("pending", "running", "cancelling"):
                    print("  Polling AI job...")
                    latest = _poll_ai_job(session, args.host, latest["id"])
                results["ai_job"] = latest
                print(f"  Processed: {latest.get('processed', 0)}")
                print(f"  Tags added: {latest.get('tags_added', 0)}")
                print(f"  Suggestions: {latest.get('suggestions_added', 0)}")
                print(f"  Failed: {latest.get('failed', 0)}")
                print(f"  Localization: {latest.get('localization_status', 'N/A')}")
            else:
                print("  No AI tag jobs found")
                results["ai_job"] = None
        else:
            print(f"  Failed: {r.status_code}")
    except Exception as e:
        print(f"  Error: {e}")

    # Step 7: Check translations
    print("\n[7/8] Checking tag translations...")
    try:
        r = session.get(f"{args.host}/api/admin/tag-localization/stats")
        if r.status_code == 200:
            stats = r.json()
            print(f"  Total tags: {stats.get('total_tags', 0)}")
            print(f"  Translated (DB): {stats.get('translated_db', 0)}")
            print(f"  Missing: {stats.get('missing', 0)}")
            results["translations"] = stats
        else:
            print(f"  Warning: {r.status_code}")
    except Exception as e:
        print(f"  Error: {e}")

    # Step 8: Test Chinese search
    print("\n[8/8] Testing Chinese search...")
    try:
        r = session.get(f"{args.host}/api/search", params={"q": "蓝眼睛"})
        if r.status_code == 200:
            data = r.json()
            total = data.get("total", 0) if isinstance(data, dict) else len(data)
            print(f"  Search '蓝眼睛': {total} results")
            results["search_zh"] = total
        else:
            print(f"  Search returned: {r.status_code}")
            results["search_zh"] = -1
    except Exception as e:
        print(f"  Error: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    checks = []
    scan = results.get("scan", {})
    ai_job = results.get("ai_job")

    checks.append(("Scan imported > 0", scan.get("imported", 0) > 0 if scan else None))
    checks.append(("AI job created", ai_job is not None))
    checks.append(("AI job processed > 0", (ai_job or {}).get("processed", 0) > 0))
    checks.append(("Tags or suggestions > 0",
                    ((ai_job or {}).get("tags_added", 0) + (ai_job or {}).get("suggestions_added", 0)) > 0))
    checks.append(("Translations exist",
                    results.get("translations", {}).get("translated_db", 0) > 0))
    checks.append(("Chinese search works", results.get("search_zh", -1) >= 0))

    all_pass = True
    for name, result in checks:
        if result is True:
            print(f"  \u2705 {name}")
        elif result is False:
            print(f"  \u274c {name}")
            all_pass = False
        else:
            print(f"  \u26a0\ufe0f  {name} (skipped)")

    print()
    if all_pass:
        print("\U0001f389 All checks passed!")
    else:
        print("\u26a0\ufe0f  Some checks failed \u2014 review output above.")
    print()

    return 0 if all_pass else 1


def _poll_scan_job(session, host, job_id, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = session.get(f"{host}/api/admin/scan-local-library/jobs/{job_id}")
            if r.status_code == 200:
                job = r.json()
                if job["status"] in ("completed", "failed", "cancelled", "interrupted"):
                    return job
                print(f"    ... {job['status']} processed={job.get('processed', 0)} imported={job.get('imported', 0)}")
        except Exception:
            pass
        time.sleep(2)
    return {"status": "timeout", "error": "Poll timeout"}


def _poll_ai_job(session, host, job_id, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = session.get(f"{host}/api/admin/ai-tagging/jobs/{job_id}")
            if r.status_code == 200:
                job = r.json()
                if job["status"] in ("completed", "failed", "cancelled", "interrupted"):
                    return job
                print(f"    ... {job['status']} processed={job.get('processed', 0)} tags={job.get('tags_added', 0)}")
        except Exception:
            pass
        time.sleep(3)
    return {"status": "timeout", "error": "Poll timeout"}


if __name__ == "__main__":
    sys.exit(main())
