# Phase 4.4-P2R-F2 - External gallery-dl Metadata / Reference Adapter Pilot

## Why This Stage Exists

PR #88 validated local gallery-dl JSON import. F2 tests whether V.I.O.L.E.T. can safely invoke a user-installed gallery-dl boundary with bounded samples, redaction, read-only local prior joins, and reportable run metadata without DB persistence.

## PR #88 Merge Confirmation

- PR #88 state: `MERGED`.
- PR #88 merged at: `2026-06-01T05:51:44Z`.
- PR #88 merge commit: `a9ea099d08b0fb51213cb3e82177d57f3200c627`.
- PR #88 URL: `https://github.com/kyloris0660/VIOLET/pull/88`.

## gallery-dl Command Mode

- Mode: `explicit_operator_command_mode`.
- Version: `1.32.1`.
- Reproducibility status: `conditional_explicit_operator_command`.
- Command label: `explicit operator command`.

## DB Identity / Config Labels

- DB identity: `{"actual_db_name": "blombooru", "configured_db_host": "localhost", "configured_db_name": "blombooru", "configured_db_port": 5432, "configured_db_user": "postgres", "database_url_source": "settings_env_or_default", "db_read_only_guard_installed": true, "db_sensitive_value_included": false, "local_paths_redacted": true, "settings_file_exists": false, "settings_source": "defaults_without_settings_file", "storage_root_mode": "code_root_default", "violet_env": "development"}`.

## Sample Gate

- Sample selection: `{"content_class_distribution": {"anime": 5}, "duplicate_or_ambiguous_count": 0, "exact_filenames_public": false, "exact_media_ids_public": false, "exact_work_ids_public": false, "max_sample_size": 10, "page_case_distribution": {"p0_and_non_p0": 1, "p0_only": 4}, "prior_total_keys": 555, "prior_total_media": 555, "prior_total_media_inspected": 1989, "requested_sample_size": 5, "sample_gate_status": "passed", "selected_count": 5, "selection_strategy": "anime_first_cover_p0_and_non_p0_then_fill_by_work_id"}`.
- Sample gate: `{"default_sample_size": 5, "enforced_before_local_join": true, "enforced_before_metadata_processing": true, "enforced_before_private_mapping_artifacts": true, "max_records_without_renewed_approval": 10, "max_sample_size_without_renewed_approval": 10, "status": "passed"}`.

## Command Results

- Command summary: `{"exact_commands_private_only": true, "metadata_auth_or_config_failure_count": 0, "metadata_command_count": 5, "metadata_command_template": "<gallery_dl_entrypoint> --dump-json --no-download https://www.pixiv.net/artworks/<WORK_ID>", "metadata_failure_count": 0, "metadata_success_count": 5, "per_item_results_public": [{"command_kind": "metadata", "error_is_auth_or_config": false, "exit_code": 0, "item_index": 1, "stderr_error_class": null, "stdout_bytes": 3587, "success": true}, {"command_kind": "metadata", "error_is_auth_or_config": false, "exit_code": 0, "item_index": 2, "stderr_error_class": null, "stdout_bytes": 17103, "success": true}, {"command_kind": "metadata", "error_is_auth_or_config": false, "exit_code": 0, "item_index": 3, "stderr_error_class": null, "stdout_bytes": 3687, "success": true}, {"command_kind": "metadata", "error_is_auth_or_config": false, "exit_code": 0, "item_index": 4, "stderr_error_class": null, "stdout_bytes": 6081, "success": true}, {"command_kind": "metadata", "error_is_auth_or_config": false, "exit_code": 0, "item_index": 5, "stderr_error_class": null, "stdout_bytes": 3785, "success": true}], "reference_command_count": 0, "reference_command_template": null, "reference_failure_count": 0, "reference_success_count": 0, "subprocess_uses_shell": false}`.

## Metadata Records

- Input summary: `{"directory_context_event_count": 5, "invalid_json_count": 0, "normalized_media_record_count": 10, "raw_event_count": 15, "raw_file_count": 5, "raw_record_count": 15, "skipped_invalid_count": 0, "unsupported_shape_count": 0, "url_media_event_count": 10}`.
- Schema field availability: `{"artist_id": 10, "artist_name": 10, "caption": 7, "extractor_category": 10, "gallery_dl_filename": 10, "image_url_kinds": 10, "page_count": 10, "page_index": 10, "tags": 10, "title": 10, "translated_tags": 0, "work_id": 10}`.
- Metadata richness distribution: `{"rich_structured_metadata": 10}`.
- Raw record shape distribution: `{"gallery_dl_directory_context_event": 5, "gallery_dl_url_media_event": 10}`.

## Local Source-Prior Join

- Join status counts: `{"metadata_matches_eligible_anime_local_prior": 6, "metadata_work_id_found_no_local_match": 4}`.
- Content-class eligibility: `{"approved_content_classes": ["anime"], "future_eligibility_counts": {"eligible_for_future_local_source_hint": 6, "ineligible_for_future_local_source_hint": 4}, "unknown_non_anime_future_eligible": false}`.

## Page Index Validation

- Page-index status counts: `{"page_index_within_page_count": 10}`.

## Reference Download / Artifact Accounting

- Download summary: `{"cleanup_file_count": 0, "cleanup_performed": false, "cleanup_total_bytes": 0, "download_root_phase_specific": true, "downloaded_artifact_type_distribution": {}, "downloaded_artifacts_committed": false, "downloaded_file_count": 0, "downloaded_total_bytes": 0, "reference_download_enabled": false}`.

## Correspondence Feasibility

- Correspondence: `{"image_correspondence_is_blocker": false, "status_counts": {"metadata_work_page_match_no_visual_check": 10}, "visual_check_performed": false}`.

## Output Containment

- Containment: `{"gitignored_private_artifacts": true, "output_path_violation": false, "phase_output_root": ".local_manifests/<phase-private-root>", "private_artifacts_under_phase_root": true, "public_reports_under_docs_reports": true}`.

## External Adapter Route Readiness

- Readiness: `{"engineering_ready": false, "metadata_adapter_logic_ready": true, "persistence_blocker": "F2 is not a DB persistence stage", "persistence_ready": false}`.

## Recommended Next Phase

- Decision: `C_harden_external_adapter_command_config_first`.
- Reason: `metadata and local joins worked, but the only working command mode is an explicit operator command rather than project-python or discovered gallery-dl`.
- DB persistence: `not_recommended_until_command_boundary_hardened`.

## Safety Confirmation

- `public_report_contains_exact_pixiv_ids`: `False`.
- `public_report_contains_exact_media_ids`: `False`.
- `public_report_contains_exact_local_filenames`: `False`.
- `public_report_contains_exact_local_paths`: `False`.
- `public_report_contains_raw_gallery_dl_json`: `False`.
- `public_report_contains_raw_image_urls`: `False`.
- `sensitive_material_leaked`: `False`.
- `db_write`: `False`.
- `db_migration`: `False`.
- `local_source_hint_write`: `False`.
- `provider_cache_write`: `False`.
- `entity_evidence_write`: `False`.
- `media_entity_candidate_write`: `False`.
- `negative_lookup_cache_write`: `False`.
- `confirmed_assignment`: `False`.
- `automatic_entity`: `False`.
- `media_tags_mutation`: `False`.
- `tag_translation_mutation`: `False`.
- `entity_resolver`: `False`.
- `source_or_icloud_mutation`: `False`.
- `app_managed_storage_mutation`: `False`.
- `broad_gallery_dl_run`: `False`.
- `full_library_gallery_dl_run`: `False`.
- `local_manifests_committed`: `False`.
- `push_main`: `False`.
- `merge`: `False`.
- `next_phase_started`: `False`.
