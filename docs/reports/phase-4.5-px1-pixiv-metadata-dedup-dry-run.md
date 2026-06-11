# 4.5-PX1 Bounded Pixiv Metadata Extraction and Exact-Duplicate Cleanup Dry-Run

## Summary

- Status: `inventory_and_dedup_completed_provider_metadata_not_written`.
- Current total media: `3750`.
- Pixiv-like candidates: `2287`.
- Metadata extraction selected/success/failure: `5` / `0` / `5`.
- Exact duplicate groups: `0`; would-delete if later approved: `0`.
- Provider execution policy: `anime_only_source_metadata_missing_reliable_single_pixiv_filename_prior`.

## Scope and non-goals

- PX1 only inventories DB-derived Pixiv-like media, runs exact-hash duplicate dry-run, and may run bounded metadata-only Pixiv/gallery-dl requests.
- It does not import media, delete duplicates, mutate source/iCloud storage, run AI/classification/localization/LLM, run SourceConcept resolver, create Entity truth, or write media_tags.

## Current post-E1 baseline

- Total media: `3750`.
- Eligible media: `3687`.
- Eligible AI tag coverage: `3687` / `3687` (`100.0%`).

## Current Pixiv-like inventory

- Pixiv-like media candidates: `2287`.
- With existing source metadata: `60`.
- Without source metadata: `2227`.
- Distinct Pixiv work IDs: `2237`.
- Duplicate Pixiv work/page candidates: `0`.
- Invalid or ambiguous Pixiv ID candidates: `0`.
- Eligible for metadata extraction before limit: `2194`.
- Anime provider execution eligible: `2194`.
- Unknown excluded from provider execution: `26`.
- Non-anime excluded from provider execution: `10`.
- Already has source metadata excluded: `57`.
- Exclusion reasons: `{"already_has_source_metadata": 60, "ineligible_content_class": 10}`.
- Provider execution exclusion reasons: `{"already_has_source_metadata": 57, "non_anime_excluded_from_provider_execution": 10, "unknown_excluded_from_provider_execution": 26}`.

## Exact duplicate dry-run summary

- Exact duplicate groups: `0`.
- Duplicate media involved: `0`.
- Would-delete count if later approved: `0`.
- Estimated reclaim bytes if safely computable: `0`.

## Duplicate retention policy result

- Pixiv-retained groups: `0`.
- All-non-Pixiv groups: `0`.
- Ambiguous/conflicting Pixiv groups: `0`.
- Attached-data risk groups: `0`.
- Duplicate deletion may be proposed later only as a separate destructive phase.

## Metadata extraction candidate selection

- Selected count: `5`.
- Eligible before limit: `2194`.
- Requested limit: `5`.
- Excluded reason counts: `{"already_has_source_metadata": 57, "non_anime_excluded_from_provider_execution": 10, "unknown_excluded_from_provider_execution": 26}`.
- Unknown excluded from provider execution: `26`.
- Non-anime excluded from provider execution: `10`.

## Provider/auth/cache/rate-limit preflight

- gallery-dl available: `True`.
- Entry mode: `external_executable`.
- Original download policy: `forbidden; command uses --dump-json --no-download`.
- Provider cache: `{"cache_hit_count": 5, "cache_miss_count": 0, "cache_no_metadata_records_count": 5, "cache_parse_failure_count": 0, "db_provider_cache_used": false, "failure_budget": {"attempts": 5, "auth_failures": 0, "consecutive_failures": 5, "max_auth_failures": 3, "max_consecutive_failures": 5, "max_failure_rate": 0.25, "max_rate_limit_failures": 3, "max_total_failures": 20, "rate_limit_failures": 0, "stop_reason": "max_failure_rate", "stopped": true, "total_failures": 5}, "failure_count": 5, "failure_reason_counts": {"cache_no_metadata_records": 5}, "filesystem_provider_cache_used": true, "original_downloaded": false, "provider_called_count": 0, "provider_output_diagnosis": {"auth_config_failure_count": 0, "command_option_issue_count": 0, "diagnostic_class_counts": {"unavailable_private_or_deleted": 5}, "failure_reason_counts": {"cache_no_metadata_records": 5}, "no_metadata_records_count": 5, "other_provider_failure_count": 0, "parser_mismatch_count": 0, "provider_error_event_count": 5, "provider_error_type_counts": {"NotFoundError": 5}, "public_shapes": [{"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 1, "stderr_present": true, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}], "rate_limited_count": 0, "raw_stdout_stderr_public": false, "stdout_empty_count": 0, "stdout_nonempty_unparsed_count": 0, "unavailable_private_deleted_count": 5}, "raw_failure_artifact_count": 5, "raw_json_cache_dir_private": true, "raw_json_cache_dir_public_label": ".local_manifests/phase-4.5-px1-pixiv-metadata-dedup-dry-run/provider-cache/raw-gallery-dl-json", "request_count": 5, "success_count": 0}`.

## Metadata extraction execution results

- Execution mode: `execute_metadata`.
- Attempted: `5`.
- Success: `0`.
- Failure: `5`.
- Failure reason counts: `{"cache_no_metadata_records": 5}`.
- Provider output diagnosis: `{"auth_config_failure_count": 0, "command_option_issue_count": 0, "diagnostic_class_counts": {"unavailable_private_or_deleted": 5}, "failure_reason_counts": {"cache_no_metadata_records": 5}, "no_metadata_records_count": 5, "other_provider_failure_count": 0, "parser_mismatch_count": 0, "provider_error_event_count": 5, "provider_error_type_counts": {"NotFoundError": 5}, "public_shapes": [{"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 1, "stderr_present": true, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}], "rate_limited_count": 0, "raw_stdout_stderr_public": false, "stdout_empty_count": 0, "stdout_nonempty_unparsed_count": 0, "unavailable_private_deleted_count": 5}`.
- Stop reason: `max_failure_rate`.

## Source-layer write results

- Writes applied: `False`.
- Source metadata records affected: `0`.
- Tag observations affected: `0`.
- Name observations affected: `0`.
- Assertions affected: `0`.
- Searchable assertion status policy: `{"new_px1_assertion_status": "needs_review", "new_px1_requires_review": true, "new_px1_searchable_active": false, "preserve_existing_reviewed_active_or_accepted": true, "schema_version": "phase45_px1_direct_source_metadata_v2"}`.

## Failure budget and stop conditions

`{"attempts": 5, "auth_failures": 0, "consecutive_failures": 5, "max_auth_failures": 3, "max_consecutive_failures": 5, "max_failure_rate": 0.25, "max_rate_limit_failures": 3, "max_total_failures": 20, "rate_limit_failures": 0, "stop_reason": "max_failure_rate", "stopped": true, "total_failures": 5}`

## Mutation proof

- Passed: `True`.
- Expected changed tables: `[]`.
- Forbidden changed tables: `[]`.
- Unexpected changed tables: `[]`.

## Public/private artifact boundary

- Public report contains aggregate counts only; exact media IDs, hashes, Pixiv IDs, filenames, local paths, raw provider JSON, cookies, and tokens remain private.
- Private artifact root label: `.local_manifests/phase-4.5-px1-pixiv-metadata-dedup-dry-run`.

## Decision matrix

`{"dedup1_may_be_proposed": false, "metadata_extraction_succeeded_for_bounded_batch": false, "provider_auth_or_rate_limit_blocked": false, "px1_target_met": false, "recommended_next_phase": "PX1 precondition resolution", "scv2_r1_may_start": false}`

## Whether PX1 target was met

- PX1 target met: `False`.

## Whether duplicate deletion may be proposed as a later destructive phase

- May propose later destructive DEDUP1: `False`.

## Whether SCV2-R1 may start next

- SCV2-R1 may start next: `False`.

## Deferred work

- Duplicate deletion execution is deferred.
- PX1-B is deferred unless separately approved.
- SCV2-R1 must not start inside this PR.

## Validation

`{"browser_validation": "not_run_no_ui_runtime_target", "commands": ["python.exe scripts/run_phase45_px1_pixiv_metadata_and_dedup_dry_run.py --execute-metadata --metadata-limit 5 --output-dir .local_manifests/phase-4.5-px1-pixiv-metadata-dedup-dry-run --write-public-report --read-only-dedup --confirm-metadata-execution EXECUTE_PHASE45_PX1_PIXIV_METADATA_ONLY"], "dedup_read_only": true, "operational_mode": "execute-metadata", "provider_network_attempted": true, "server_started": false}`

## Safety confirmation

- No push main and no merge.
- No duplicate deletion, media deletion, DB row deletion, reset, cleanup, drop, or truncate.
- No media import, classification, AI tagging, localization, LLM, SourceConcept resolver, Entity bridge, media_tags mutation, source/iCloud mutation, or original image download.

## Artifact lifecycle

`{"private_artifacts": "one-off local artifact / ignored output", "public_report": "public report / handoff / roadmap update", "runner": "phase-scoped operational runner", "tests": "phase-scoped validation test"}`

## Engineering judgment / operator notes

PX1 keeps the provider run bounded and source-layer-only. The dedup plan is useful for avoiding wasted metadata extraction, but deletion remains destructive and must be split into a separately approved phase. If provider/auth fails, the correct next step is provider/auth hardening rather than forcing SCV2-R1 with weak source metadata coverage.
