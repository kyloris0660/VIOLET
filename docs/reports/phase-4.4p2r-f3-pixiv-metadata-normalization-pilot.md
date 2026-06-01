# Phase 4.4-P2R-F3 - Pixiv Metadata Normalization and Tag/Entity Candidate Middleware Pilot

## Why This Stage Exists

PR #89 and F2 validated real gallery-dl metadata retrieval and local Pixiv filename-prior correspondence. F3 tests whether those raw Pixiv metadata fields can become a structured, non-persistent tag/entity candidate middleware view without treating raw Pixiv tags as confirmed truth.

## PR #89 Merge Confirmation

- PR #89 state: `MERGED`.
- PR #89 merged at: `2026-06-01T10:31:55Z`.
- PR #89 merge commit: `1d123172fd0e064e38e0ffe01e13611aa8bcc8e6`.
- PR #89 URL: `https://github.com/kyloris0660/VIOLET/pull/89`.

## Sample / Record Scope

- Sample selection: `{"content_class_distribution": {"anime": 30}, "default_sample_size": 30, "duplicate_or_ambiguous_count": 0, "exact_filenames_public": false, "exact_media_ids_public": false, "exact_work_ids_public": false, "max_normalized_records": 200, "max_sample_size": 50, "multi_local_page_work_count": 2, "page_case_distribution": {"non_p0_only": 1, "p0_and_non_p0": 2, "p0_only": 27}, "prior_total_keys": 555, "prior_total_media": 555, "prior_total_media_inspected": 1989, "requested_sample_size": 30, "sample_gate_status": "passed", "selected_count": 30, "selection_strategy": "anime_only_cover_p0_non_p0_multi_page_then_fill_by_work_id"}`.
- Input summary: `{"current_run_raw_file_count": 30, "directory_context_event_count": 26, "invalid_json_count": 0, "normalized_candidate_media_record_count": 41, "normalized_media_record_count": 41, "raw_event_count": 71, "raw_file_count": 30, "raw_input_scope": "current_run_only", "raw_record_count": 71, "skipped_invalid_count": 0, "stale_raw_files_ignored_count": 0, "unsupported_shape_count": 0, "url_media_event_count": 41}`.

## gallery-dl Command Mode / Version

- Mode: `explicit_operator_command_mode`.
- Version: `1.32.1`.
- Reproducibility status: `conditional_explicit_operator_command`.
- Command label: `explicit operator command`.

## Metadata Richness

- Metadata richness: `{"artist_candidate_coverage_count": 41, "metadata_richness_distribution": {"rich_structured_metadata": 41}, "records_with_page_count": 41, "records_with_title": 41, "schema_field_availability": {"artist_id": 41, "artist_name": 41, "caption": 22, "extractor_category": 41, "gallery_dl_filename": 41, "image_url_kinds": 41, "page_count": 41, "page_index": 41, "tags": 41, "title": 41, "translated_tags": 0, "work_id": 41}}`.
- Raw Pixiv tag availability: `{"exact_raw_tags_public": false, "normalized_unicode_tag_occurrences": 342, "raw_tag_order_preserved": true, "records_with_raw_pixiv_tags": 41, "total_raw_pixiv_tag_occurrences": 342}`.

## Candidate Classification

- Distribution: `{"candidate_kind_counts": {"ambiguous_proper_noun_candidate": 294, "entity_candidate": 41, "original_work_context": 3, "sensitive_or_meta_tag_candidate": 13, "tag_candidate": 24, "unknown_or_unresolved_pixiv_tag": 8}, "candidate_namespace_counts": {"ambiguous": 297, "artist": 41, "general": 24, "meta": 13, "unknown": 8}, "candidate_reason_counts": {"deterministic_general_descriptor_fallback": 20, "deterministic_original_marker": 3, "deterministic_proper_noun_shape_unverified": 294, "deterministic_sensitive_or_meta_descriptor": 13, "existing_tag_category_match": 4, "no_provenance_backed_category": 8, "pixiv_user_metadata": 41}, "db_write_allowed_count": 0, "lookup_source_counts": {"deterministic_fallback": 330, "external_category_lookup_disabled_in_f3": 8, "local_db_read_only": 4, "pixiv_metadata": 41}, "manual_review_required_count": 325}`.
- Artist candidates: `41`.
- Copyright/series candidates: `0`.
- Character candidates: `0`.
- General descriptive candidates: `24`.
- Ambiguous/unknown candidates: `305`.
- Sensitive/meta candidates: `13`.
- Alias-group candidates: `0`.
- Lookup source coverage: `{"deterministic_fallback": 330, "external_category_lookup_disabled_in_f3": 8, "external_category_lookup_enabled": false, "external_category_lookup_reason": "not_enabled_in_f3_default_run; no broad tag lookup or credentials", "local_db_read_only": 4}`.

## Original / Ambiguous Handling

- Original/unknown handling: `{"original_work_status_distribution": {"original_or_unknown_work_context": 38, "original_work_context_claimed_by_pixiv_tag": 3}, "raw_pixiv_original_tag_is_not_confirmed_truth": true, "records_without_forced_copyright": 41}`.

## Manual Review Needs

- Manual review: `{"candidate_rows_requiring_manual_review": 325, "manual_review_is_sparse_correction_oriented": true, "media_records_requiring_manual_review": 41, "private_manual_review_guide": ".local_manifests/phase-4.4p2r-f3-pixiv-metadata-normalization-pilot/manual-review-guide.md"}`.

## Output Containment

- Containment: `{"gitignored_private_artifacts": true, "output_path_violation": false, "phase_output_root": ".local_manifests/<phase-private-root>", "private_artifacts_under_phase_root": true, "public_reports_under_docs_reports": true}`.

## Recommended Next Phase

- Decision: `A_proceed_to_LocalSourceHint_PixivMetadata_persistence_design`.
- Reason: `metadata fields are rich and the middleware produced a safe PixivMetadata/LocalSourceHint-shaped view; EntityCandidate persistence should wait because copyright/character category evidence remains sparse`.
- DB persistence: `LocalSourceHint_design_justified_next_EntityCandidate_persistence_should_wait`.

## Safety Confirmation

- `public_report_contains_exact_pixiv_ids`: `False`.
- `public_report_contains_exact_media_ids`: `False`.
- `public_report_contains_exact_local_filenames`: `False`.
- `public_report_contains_exact_local_paths`: `False`.
- `public_report_contains_raw_gallery_dl_json`: `False`.
- `public_report_contains_raw_image_urls`: `False`.
- `public_report_contains_raw_pixiv_tags`: `False`.
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
- `localization_execution`: `False`.
- `entity_resolver`: `False`.
- `source_or_icloud_mutation`: `False`.
- `app_managed_storage_mutation`: `False`.
- `reference_download`: `False`.
- `image_download`: `False`.
- `broad_gallery_dl_run`: `False`.
- `full_library_gallery_dl_run`: `False`.
- `local_manifests_committed`: `False`.
- `push_main`: `False`.
- `merge`: `False`.
- `next_phase_started`: `False`.
