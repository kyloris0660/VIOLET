"""Reconstruct this repair's local engineering result from protected evidence.

This grants no owner acceptance, hosted CI, merge or next-phase authority.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CONTRACT = "production_import_recovery_v1"
ROOT = Path(__file__).resolve().parents[1]


def require(value, reason):
    if not value:
        raise ValueError("import_recovery_" + reason)


def bounded_bytes(root, name):
    relative = Path(name)
    require(not relative.is_absolute() and ".." not in relative.parts, "evidence_path")
    path = root / relative
    require(path.is_file() and not path.is_symlink(), "evidence_file")
    require(path.stat().st_size <= 32 * 1024 * 1024, "evidence_size")
    return path.read_bytes()


def read(root, name):
    return json.loads(bounded_bytes(root, name))


def check_public_result(result, root=None):
    require(result.get("contract_id") == CONTRACT and result.get("target_met") is True, "contract_target")
    require(result.get("safe_to_merge") is False and result.get("route_approved") is False, "owner_authority")
    require(result.get("project_lead_acceptance") == "pending", "acceptance")
    require(re.fullmatch("[0-9a-f]{40}", result.get("candidate_head", "")), "candidate")
    require(set(result) == {"contract_id", "target_met", "safe_to_merge", "route_approved",
        "project_lead_acceptance", "candidate_head", "validation", "recovery", "browser", "launcher"}, "public_fields")
    require(result["launcher"]["restarted"] is True and result["launcher"]["apply_enabled"] is False,
        "launcher_restart")
    require(result["browser"]["samples"] == 5 and result["browser"]["bindings"] == 51, "a1_preserved")
    require(result["validation"]["focused"]["passed"] >= 200 and result["validation"]["postgresql"]["passed"] >= 70,
        "validation_coverage")
    require(result["recovery"]["original_unattempted"] == 108 and result["recovery"]["original_failed"] == 82,
        "original_scope")
    require(not re.search(r"(?i)([A-Z]:[\\/]|postgres(?:ql)?://|password|relative_path|source_url)",
        json.dumps(result, ensure_ascii=False)), "public_privacy")
    if root is not None:
        from scripts.trusted_git import candidate_behavior_carry_forward
        require(candidate_behavior_carry_forward(root, result["candidate_head"]), "candidate_behavior_drift")


def derive_result(private, root=ROOT):
    manifest = read(private, "repair-evidence-private.json")
    candidate = manifest["candidate_head"]
    from scripts.trusted_git import candidate_behavior_carry_forward
    require(candidate_behavior_carry_forward(root, candidate), "candidate_behavior_drift")
    require(manifest["boundary"] == dict(merge=False, push_main=False, source_mutation=False,
        pixiv_apply=False, new_provider=False, new_model_download=False), "operation_scope")
    validation = {}
    for name, minimum in (("focused", 200), ("postgresql", 70)):
        entry = manifest["validation"][name]
        require(entry["head"] == candidate, "validation_head")
        data = bounded_bytes(private, entry["xml"])
        require(hashlib.sha256(data).hexdigest() == entry["sha256"], "validation_digest")
        tree = ET.fromstring(data)
        cases = tree.findall(".//testcase")
        require(not tree.findall(".//failure") and not tree.findall(".//error"), "validation_failures")
        skipped = len(tree.findall(".//skipped"))
        require(len(cases) - skipped >= minimum, "validation_count")
        require(any("test_real_cap_one_runs_reach_old_retry_tail" in c.get("name", "") for c in cases), "rotation_test")
        require(sum("test_independent_failures_never_truncate" in c.get("name", "") for c in cases) == 3, "failure_positions")
        validation[name] = dict(passed=len(cases)-skipped, skipped=skipped)

    runtime = read(private, "production-runtime-private.json")
    require(runtime["candidate_head"] == candidate, "runtime_candidate")
    require(runtime["before_pid"] != runtime["after_pid"], "fresh_process")
    require(runtime["anchor"]["repo_root"] == runtime["profile_target"]["repo_root"], "daily_anchor")
    require(runtime["anchor"]["python"] == runtime["profile_target"]["python"], "daily_python")
    require(runtime["database_identity"]["name"] == "blombooru" and
        runtime["database_identity"]["server_system_identifier"] == "7635635488443479756", "original_database")
    status = runtime["status"]["data"]
    require(status["running"] and status["health_ok"] and status["managed_by_launcher"] and
        status["pid"] == runtime["after_pid"] and status["env"] == "production", "runtime_status")
    identity = runtime["identity"]
    require(identity["code_root"] == runtime["profile_target"]["repo_root"] and
        identity["storage_root"] == runtime["profile_target"]["storage_root"] and
        identity["python_executable"] == runtime["profile_target"]["python"], "runtime_identity")
    require(runtime["product_enabled"] and runtime["apply_enabled"] is False, "product_flags")

    browser = read(private, "production-browser/browser-raw-private.json")
    require(browser["candidate_head"] == candidate and browser["passed"] is True, "browser_pass")
    require(browser["headed"] is True and browser["channel"] == "msedge", "browser_surface")
    require(len(browser["samples"]) == 5 and not browser["page_errors"], "browser_samples")
    require({row["media_id"] for row in browser["samples"]} == {788, 842, 846, 1869, 2431}, "sample_identity")
    for row in browser["samples"]:
        require(all(row[key]["naturalWidth"] > 0 for key in ("thumbnail", "original", "fullscreen")), "media_loaded")
    protected = read(private, "protected-a1-after-private.json")
    require(protected["active_runs"] == 1 and protected["bindings"] == 51 and
        set(protected["media_ids"]) == {788, 842, 846, 1869, 2431}, "binding_identity")

    accounting = read(private, "recovery-accounting-private.json")
    require(accounting["candidate_head"] == candidate, "accounting_candidate")
    items = accounting["items"]
    require(len({row["source_item_id"] for row in items}) == len(items), "unique_items")
    counts = Counter(row["cohort"] for row in items)
    require(counts["original_unattempted"] == 108 and counts["original_failed"] == 82, "cohort_coverage")
    outcomes = Counter()
    for row in items:
        require(row["outcome"] in {"imported", "existing_media", "retryable", "deferred_diagnosis",
            "terminal", "ignored", "unexecuted", "followup_pending"}, "item_outcome")
        if row["outcome"] in {"imported", "existing_media", "followup_pending"}:
            require(row.get("media_id") and row.get("app_media_exists"), "media_evidence")
        elif row["outcome"] == "unexecuted":
            require(row.get("boundary_reason") and row.get("reachable_in_next_plan"), "continuation")
        else:
            require(row.get("reason") and row.get("reentry_condition"), "failure_disposition")
            require(row.get("new_attempt_run_ids") or row.get("historical_attempt_run_ids"), "actual_attempt_evidence")
        outcomes[row["outcome"]] += 1
    result = dict(contract_id=CONTRACT, target_met=True, safe_to_merge=False, route_approved=False,
        project_lead_acceptance="pending", candidate_head=candidate, validation=validation,
        recovery=dict(original_unattempted=counts["original_unattempted"], original_failed=counts["original_failed"],
            verified_gaps=counts["verified_gap"], observed_new=counts["observed_new"], outcomes=dict(outcomes)),
        browser=dict(samples=5, bindings=51, headed=True),
        launcher=dict(restarted=True, product_enabled=True, apply_enabled=False, main_merge_pending=True))
    check_public_result(result, root=root)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = derive_result(args.evidence)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
