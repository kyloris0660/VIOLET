# 4.5-PX1 Bounded Pixiv Metadata Extraction and Exact-Duplicate Cleanup Dry-Run

## Summary

- Status: `metadata_execution_completed`.
- Current total media: `3750`.
- Pixiv-like candidates: `2287`.
- Metadata extraction selected/success/failure: `500` / `470` / `30`.
- Exact duplicate groups: `0`; would-delete if later approved: `0`.
- Provider execution policy: `anime_only_source_metadata_missing_reliable_single_pixiv_filename_prior`.
- Report generation git branch/head: `codex/phase45-px1-pixiv-metadata-dedup-dry-run` / `cfaf69116b1b765efc8ce13c3f02d388bc8ac366`.

## Scope and non-goals

- PX1 only inventories DB-derived Pixiv-like media, runs exact-hash duplicate dry-run, and may run bounded metadata-only Pixiv/gallery-dl requests.
- It does not import media, delete duplicates, mutate source/iCloud storage, run AI/classification/localization/LLM, run SourceConcept resolver, create Entity truth, or write media_tags.

## Current post-E1 baseline

- Total media: `3750`.
- Eligible media: `3687`.
- Eligible AI tag coverage: `3687` / `3687` (`100.0%`).

## Current Pixiv-like inventory

- Pixiv-like media candidates: `2287`.
- With existing source metadata: `61`.
- Without source metadata: `2226`.
- Distinct Pixiv work IDs: `2237`.
- Duplicate Pixiv work/page candidates: `0`.
- Invalid or ambiguous Pixiv ID candidates: `0`.
- Eligible for metadata extraction before limit: `2193`.
- Anime provider execution eligible: `2193`.
- Unknown excluded from provider execution: `26`.
- Non-anime excluded from provider execution: `10`.
- Already has source metadata excluded: `58`.
- Exclusion reasons: `{"already_has_source_metadata": 61, "ineligible_content_class": 10}`.
- Provider execution exclusion reasons: `{"already_has_source_metadata": 58, "non_anime_excluded_from_provider_execution": 10, "unknown_excluded_from_provider_execution": 26}`.

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

- Selected count: `500`.
- Eligible before limit: `2193`.
- Requested limit: `500`.
- Excluded reason counts: `{"already_has_source_metadata": 58, "non_anime_excluded_from_provider_execution": 10, "unknown_excluded_from_provider_execution": 26}`.
- Unknown excluded from provider execution: `26`.
- Non-anime excluded from provider execution: `10`.
- Sample work/page pattern summary: `{"e1_imported_vs_pre_e1_detectability": "not_detected_from_safe_db_fields", "exact_filenames_public": false, "exact_work_ids_public": false, "page_index_counts": {"0": 450, "1": 35, "2": 10, "3": 1, "4": 1, "5": 3}, "work_id_digit_length_counts": {"8": 84, "9": 416}}`.

## Provider/auth/cache/rate-limit preflight

- gallery-dl available: `True`.
- Entry mode: `external_executable`.
- Network attempted: `True`.
- Request/cache/provider counts: request=`500`, provider_called=`446`, cache_hit=`54`.
- Original download policy: `forbidden; command uses --dump-json --no-download`.
- Provider cache: `{"cache_failure_count": 8, "cache_hit_count": 54, "cache_miss_count": 446, "cache_no_metadata_records_count": 8, "cache_parse_failure_count": 0, "cache_unavailable_private_or_deleted_count": 8, "db_provider_cache_used": false, "failure_budget": {"attempts": 446, "auth_failures": 0, "consecutive_failures": 0, "max_auth_failures": 3, "max_consecutive_failures": 500, "max_failure_rate": 1.0, "max_rate_limit_failures": 3, "max_total_failures": 500, "rate_limit_failures": 0, "stop_reason": null, "stopped": false, "total_failures": 22}, "failure_count": 30, "failure_reason_counts": {"cache_no_metadata_records": 8, "no_metadata_records": 22}, "filesystem_provider_cache_used": true, "original_downloaded": false, "provider_budget_consumed_count": 446, "provider_called_count": 446, "provider_failure_count": 22, "provider_output_diagnosis": {"auth_config_failure_count": 0, "command_option_issue_count": 0, "diagnostic_class_counts": {"unavailable_private_or_deleted": 30}, "failure_reason_counts": {"cache_no_metadata_records": 8, "no_metadata_records": 22}, "no_metadata_records_count": 30, "other_provider_failure_count": 0, "parser_mismatch_count": 0, "provider_error_event_count": 30, "provider_error_type_counts": {"NotFoundError": 30}, "public_shapes": [{"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 1, "stderr_present": true, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 1, "stderr_present": true, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}], "rate_limited_count": 0, "raw_stdout_stderr_public": false, "stdout_empty_count": 0, "stdout_nonempty_unparsed_count": 0, "unavailable_private_deleted_count": 30}, "provider_success_count": 424, "raw_failure_artifact_count": 30, "raw_json_cache_dir_private": true, "raw_json_cache_dir_public_label": ".local_manifests/phase-4.5-px1-pixiv-metadata-dedup-dry-run/provider-cache/raw-gallery-dl-json", "request_count": 500, "success_count": 470}`.

## Metadata extraction execution results

- Execution mode: `execute_metadata`.
- Attempted: `500`.
- Request/provider/cache/network counts: request=`500`, provider_called=`446`, cache_hit=`54`, network_attempted=`True`.
- Success: `470`.
- Failure: `30`.
- Failure reason counts: `{"cache_no_metadata_records": 8, "no_metadata_records": 22}`.
- Provider output diagnosis: `{"auth_config_failure_count": 0, "command_option_issue_count": 0, "diagnostic_class_counts": {"unavailable_private_or_deleted": 30}, "failure_reason_counts": {"cache_no_metadata_records": 8, "no_metadata_records": 22}, "no_metadata_records_count": 30, "other_provider_failure_count": 0, "parser_mismatch_count": 0, "provider_error_event_count": 30, "provider_error_type_counts": {"NotFoundError": 30}, "public_shapes": [{"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 1, "stderr_present": true, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 1, "stderr_present": true, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "cache_no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}, {"diagnostic_class": "unavailable_private_or_deleted", "failure_reason": "no_metadata_records", "first_json_type": "array", "json_line_count": 1, "provider_error_present": true, "provider_error_type": "NotFoundError", "stderr_line_count": 0, "stderr_present": false, "stdout_empty": false, "stdout_line_count": 9}], "rate_limited_count": 0, "raw_stdout_stderr_public": false, "stdout_empty_count": 0, "stdout_nonempty_unparsed_count": 0, "unavailable_private_deleted_count": 30}`.
- Stop reason: `None`.

## Source-layer write results

- Writes applied: `True`.
- Source metadata records affected: `470`.
- Tag observations affected: `3727`.
- Name observations affected: `918`.
- Assertions affected: `918`.
- Searchable assertion status policy: `{"new_px1_assertion_status": "needs_review", "new_px1_requires_review": true, "new_px1_searchable_active": false, "preserve_existing_reviewed_active_or_accepted": true, "schema_version": "phase45_px1_direct_source_metadata_v2"}`.

## Failure budget and stop conditions

`{"attempts": 446, "auth_failures": 0, "consecutive_failures": 0, "max_auth_failures": 3, "max_consecutive_failures": 500, "max_failure_rate": 1.0, "max_rate_limit_failures": 3, "max_total_failures": 500, "rate_limit_failures": 0, "stop_reason": null, "stopped": false, "total_failures": 22}`

## Mutation proof

- Passed: `True`.
- Expected changed tables: `["blombooru_source_metadata_evidence", "blombooru_source_metadata_records", "blombooru_source_name_observations", "blombooru_source_searchable_name_assertions", "blombooru_source_tag_observations"]`.
- Forbidden changed tables: `[]`.
- Unexpected changed tables: `[]`.

## Public/private artifact boundary

- Public report contains aggregate counts only; exact media IDs, hashes, Pixiv IDs, filenames, local paths, raw provider JSON, cookies, and tokens remain private.
- Private artifact root label: `.local_manifests/phase-4.5-px1-pixiv-metadata-dedup-dry-run`.

## Decision matrix

`{"dedup1_may_be_proposed": false, "metadata_extraction_succeeded_for_bounded_batch": true, "provider_auth_or_rate_limit_blocked": false, "px1_target_met": true, "recommended_next_phase": "SCV2-R1", "scv2_r1_may_start": true, "source_write_blocked_by_mutation_proof": false}`

## Whether PX1 target was met

- PX1 target met: `True`.

## Whether duplicate deletion may be proposed as a later destructive phase

- May propose later destructive DEDUP1: `False`.

## Whether SCV2-R1 may start next

- SCV2-R1 may start next: `True`.

## Deferred work

- Duplicate deletion execution is deferred.
- PX1-B is deferred unless separately approved.
- SCV2-R1 must not start inside this PR.

## Validation

`{"browser_validation": "not_run_no_ui_runtime_target", "commands": ["python.exe scripts/run_phase45_px1_pixiv_metadata_and_dedup_dry_run.py --execute-metadata --metadata-limit 500 --output-dir .local_manifests/phase-4.5-px1-pixiv-metadata-dedup-dry-run --write-public-report --read-only-dedup --confirm-metadata-execution EXECUTE_PHASE45_PX1_PIXIV_METADATA_ONLY --sleep-request-seconds 1 --max-provider-failures 500 --max-provider-failure-rate 1.0 --max-consecutive-provider-failures 500"], "dedup_read_only": true, "operational_mode": "execute-metadata", "provider_network_attempted": true, "server_started": false}`

## Safety confirmation

- No push main and no merge.
- No duplicate deletion, media deletion, DB row deletion, reset, cleanup, drop, or truncate.
- No media import, classification, AI tagging, localization, LLM, SourceConcept resolver, Entity bridge, media_tags mutation, source/iCloud mutation, or original image download.

## Artifact lifecycle

`{"private_artifacts": "one-off local artifact / ignored output", "public_report": "public report / handoff / roadmap update", "runner": "phase-scoped operational runner", "tests": "phase-scoped validation test"}`

## Engineering judgment / operator notes

PX1 keeps the provider run bounded and source-layer-only. The dedup plan is useful for avoiding wasted metadata extraction, but deletion remains destructive and must be split into a separately approved phase. If provider/auth fails, the correct next step is provider/auth hardening rather than forcing SCV2-R1 with weak source metadata coverage.
