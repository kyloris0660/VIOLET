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
    require(path.stat().st_size <= 64 * 1024 * 1024, "evidence_size")
    return path.read_bytes()


def read(root, name):
    return json.loads(bounded_bytes(root, name))


def unique_rows(rows, key, label):
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), label + "_duplicate_identity")
    return result


def reconstruct_recovery(before, original, snapshot, accounting, metadata, downstream, storage_root, history=None, inventory=None):
    """Rebuild identity/cohort/outcome from independent observations, not totals."""
    baseline = unique_rows(before["affected_rows"], "id", "before")
    original_sources = unique_rows(original["sources"], "id", "original_source")
    original_items = unique_rows(original["run_items"], "id", "original_run_item")
    original_ids = set(baseline) | {row["source_item_id"] for row in original_items.values() if row["source_item_id"]}
    require(set(original_sources) == original_ids, "original_exhaustive_scope")
    historical_extra = set()
    if history is not None:
        require(inventory is not None and inventory["enumeration_count"] == 1
            and inventory["content_reads"] == 0 and not inventory["directory_errors"], "history_inventory_coverage")
        prior = unique_rows(history["sources"], "id", "history_source")
        prior_hashes = {(r["source_root_id"], r["relative_path_hash"]): r for r in prior.values()}
        current_hashes = {(r["source_root_id"], r["relative_path_hash"]): r for r in snapshot["sources"]}
        # Ordinary image policy; unsupported/hidden/zero-byte entries remain
        # visible in the independent history coverage ledger, not success counts.
        supported = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        for entry in inventory["entries"]:
            observed = entry.get("metadata") or {}
            require("diagnostic" not in entry, "history_member_coverage_unknown")
            if (entry["suffix"] not in supported or not observed.get("is_file") or observed.get("is_symlink")
                    or not observed.get("file_size") or Path(entry["relative_path"]).name.startswith(".")):
                continue
            key = (entry["source_root_id"], entry["relative_path_hash"])
            old = prior_hashes.get(key)
            if old is None or (not old.get("media_id") and old["id"] not in original_ids):
                require(key in current_hashes, "history_confirmed_gap_not_processed")
                if old is not None:
                    historical_extra.add(old["id"])
        require(history["sync_run_count"] == len(history["sync_runs"])
            and history["run_item_count"] == sum(r["run_item_count"] for r in history["sync_runs"]), "history_run_totals")
    require(set(snapshot.get("additional_history_ids", [])) == historical_extra, "history_extra_scope")
    later_ids = {row["source_item_id"] for row in snapshot["run_items"] if row["source_item_id"]} - original_ids - historical_extra
    expected = original_ids | historical_extra | later_ids
    sources = unique_rows(snapshot["sources"], "id", "current_source")
    rows = unique_rows(accounting["items"], "source_item_id", "accounting")
    require(set(sources) == expected == set(rows), "exhaustive_scope")
    media = unique_rows(downstream["media"], "id", "downstream_media")
    tags = unique_rows(downstream["tags"], "id", "tag")
    associations = downstream["media_tags"]
    require(len({(r["media_id"], r["tag_id"]) for r in associations}) == len(associations), "tag_association_duplicate")
    counts, outcomes, identities = Counter(), Counter(), []
    actual = snapshot["run_items"]
    unique_rows(actual, "id", "current_run_item")
    for run in snapshot.get("runs", []):
        require(run.get("status") not in {"pending", "running", "cancelling"}, "recovery_running")
        require(run.get("failed_items", 0) == sum(r["item_state"] == "failed" for r in actual if r["sync_run_id"] == run["id"]),
            "run_failure_summary_mismatch")
    imported = {r["source_item_id"] for r in actual if r["action"] == "import" and r["item_state"] in {"imported", "imported_in_test"}}
    successes = 0
    for ident, row in rows.items():
        source = sources[ident]
        old = baseline.get(ident)
        cohort = ("original_unattempted" if old["sync_state"] == "deferred_unprocessed" else
                  "original_failed" if old["sync_state"] == "failed" else "verified_gap") if old else (
                  "observed_new" if ident in original_ids else "additional_history" if ident in historical_extra else "observed_later")
        require(row["cohort"] == cohort, "cohort_identity")
        anchor = original_sources.get(ident, source)
        for key in ("source_root_id", "relative_path_hash", "relative_path"):
            require(source[key] == anchor[key], "source_identity_replaced")
        require(row.get("after") == source, "accounting_snapshot_mismatch")
        require(row.get("media_id") == source.get("media_id"), "media_identity_mismatch")
        if source.get("media_id"):
            observed = media.get(source["media_id"])
            require(observed is not None, "downstream_media_missing")
            # App-managed originals only. Never read/hash original source content.
            path = Path(observed["resolved_app_path"])
            require(observed["path"] == source["app_media_path"], "app_file_identity")
            require(path.resolve() == (Path(storage_root) / observed["path"].replace("\\", "/")).resolve(), "app_file_path_binding")
            require(path.is_absolute() and path.resolve().is_relative_to(Path(storage_root).resolve()), "app_file_confinement")
            require(path.is_file() and path.stat().st_size == observed["file_size"], "app_file_missing_or_changed")
            require(source.get("app_media_exists") is True and row.get("app_media_exists") is True, "app_file_observation_mismatch")
            require(source.get("content_hash") == observed["hash"], "media_hash_identity")
            require(source.get("classification_status") in {"classified", "classified_reused"}
                and observed.get("content_class") not in {None, "", "unknown", "uncertain", "unclassified"},
                "classification_pending_normal_followup_required")
            non_target = observed["content_class"] == "non_anime"
            if non_target:
                require(source.get("ai_tagging_status") in {"ai_tagging_skipped_non_target", "skipped_non_target"}
                    and source.get("localization_status") == "localization_not_applicable_non_target", "non_target_policy")
            else:
                media_tags = [r for r in associations if r["media_id"] == source["media_id"]]
                require(source.get("ai_tagging_status") in {"ai_tagged", "tagged", "tagged_reused"}
                    and any(r["source"] == "ai_wd" for r in media_tags), "tagging_pending_normal_followup_required")
                require(source.get("localization_status") in {"localized", "completed", "skipped_no_localizable_tags",
                    "skipped_no_new_tags", "skipped_static_coverage"}, "localization_pending_normal_followup_required")
                require(all(r["tag_id"] in tags for r in media_tags), "localization_scope")
                for tag in (tags[r["tag_id"]] for r in media_tags if tags[r["tag_id"]]["category"] in {"general", "meta"}):
                    require(tag["canonical_name"] in downstream["static_coverage"] or
                        any(t["canonical_name"] == tag["canonical_name"] and t["language"] == "zh-CN"
                            and t["status"] != "rejected" and t["translation"] for t in downstream["translations"]),
                        "localization_coverage_pending_normal_followup_required")
            outcome = "imported" if ident in imported else "existing_media"
            successes += 1
        else:
            recovery = (source.get("metadata_json") or {}).get("manual_sync_recovery") or {}
            outcome = recovery.get("disposition", "retryable")
            if outcome not in {"deferred_diagnosis", "terminal", "ignored"}:
                outcome = "retryable"
            if source.get("sync_state") == "deferred_unprocessed" or row.get("metadata_observation", {}).get("available") is False:
                if source.get("sync_state") != "deferred_unprocessed":
                    require(ident in {r["source_item_id"] for r in metadata["metadata_only_items"]}, "missing_source_identity")
                outcome = "unexecuted"
            if outcome == "unexecuted":
                require(row.get("boundary_reason") and row.get("reachable_in_next_plan") and row.get("reentry_condition"), "continuation")
            else:
                require(row.get("reason") and row.get("reentry_condition"), "failure_disposition")
                require(row["reason"] == (recovery.get("reason") or source.get("failure_reason") or source.get("deferred_reason")),
                    "failure_reason_mismatch")
                attempts = {r["sync_run_id"] for r in snapshot["attempt_history"] if r["source_item_id"] == ident
                    and r["action"] in {"import", "retry_source", "attempt"}
                    and r["item_state"] not in {"not_executed", "deferred_unprocessed"}}
                require(attempts and set(row.get("new_attempt_run_ids", [])) | set(row.get("historical_attempt_run_ids", [])) == attempts,
                    "actual_attempt_identity")
        require(row["outcome"] == outcome, "outcome_or_downstream_pending_normal_followup_required")
        counts[cohort] += 1
        outcomes[outcome] += 1
        identities.append([ident, cohort, source["source_root_id"], source["relative_path_hash"]])
    latest = next(r for r in reversed(original["runs"]) if r["run_type"] == "manual_sync_execute")
    dispositions = latest["summary_json"]["manual_sync_execute"]["private_discovery"]["metadata_dispositions"]
    missing = {r["source_item_id"] for r in dispositions if r["reason"] == "stat_error"}
    metadata_rows = unique_rows(metadata["metadata_only_items"], "source_item_id", "metadata")
    require(set(metadata_rows) == missing, "metadata_exhaustive_scope")
    policy = {(r.get("source_item_id"), r["relative_path"], r["reason"]) for r in dispositions if r["reason"] == "unsupported_extension"}
    observed_policy = [(r["observation"].get("source_item_id"), r["observation"]["relative_path"], r["observation"]["reason"])
        for r in metadata["policy_observations"]]
    require(set(observed_policy) == policy and len(observed_policy) == len(policy), "policy_exhaustive_scope")
    return dict(original_unattempted=counts["original_unattempted"], original_failed=counts["original_failed"],
        verified_gaps=counts["verified_gap"], observed_new=counts["observed_new"], additional_history=counts["additional_history"],
        observed_later=counts["observed_later"], original_total=len(original_ids), total=len(rows), outcomes=dict(outcomes),
        downstream_complete=successes, identity_digest=hashlib.sha256(json.dumps(sorted(identities)).encode()).hexdigest(),
        metadata_missing=len(missing), metadata_overlap=len(missing & original_ids), policy_observations=len(policy))


def recovery_inputs(private, manifest):
    inputs = {}
    for key in ("before", "original", "snapshot", "metadata", "downstream", "history", "inventory"):
        record = manifest["recovery_inputs"][key]
        data = bounded_bytes(private, record["file"])
        require(hashlib.sha256(data).hexdigest() == record["sha256"], "recovery_input_digest_" + key)
        inputs[key] = json.loads(data)
        require(isinstance(inputs[key], dict), "recovery_input_required_" + key)
    return inputs


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
    recovery = result["recovery"]
    require(recovery["verified_gaps"] == 268 and recovery["observed_new"] == 40 and recovery["original_total"] == 498,
        "all_original_cohorts")
    require(recovery["total"] == recovery["original_total"] + recovery["additional_history"] + recovery["observed_later"]
        == sum(recovery["outcomes"].values()), "exhaustive_public_totals")
    require(recovery["downstream_complete"] == recovery["outcomes"].get("imported", 0) + recovery["outcomes"].get("existing_media", 0)
        and not recovery["outcomes"].get("followup_pending"), "public_downstream_completion")
    require(re.fullmatch("[0-9a-f]{64}", recovery["identity_digest"]), "public_identity_digest")
    require(recovery["metadata_missing"] == 5 and recovery["metadata_overlap"] == 1 and recovery["policy_observations"] == 173,
        "public_metadata_scope")
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
    recovery = reconstruct_recovery(**recovery_inputs(private, manifest), accounting=accounting,
        storage_root=runtime["profile_target"]["storage_root"])
    result = dict(contract_id=CONTRACT, target_met=True, safe_to_merge=False, route_approved=False,
        project_lead_acceptance="pending", candidate_head=candidate, validation=validation,
        recovery=recovery,
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
