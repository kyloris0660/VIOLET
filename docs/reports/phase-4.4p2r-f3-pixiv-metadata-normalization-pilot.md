# Phase 4.4-P2R-F3b - Automated Pixiv Tag Category Lookup and Classification Cache Pilot

## Why This Stage Exists

PR #89 and F2 validated real gallery-dl metadata retrieval and local Pixiv filename-prior correspondence. F3 tests whether those raw Pixiv metadata fields can become a structured, tag/entity candidate middleware view backed by automated category lookup/cache evidence without treating raw Pixiv tags as confirmed truth.

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

- Metadata richness: `{"artist_candidate_coverage_count": 42, "metadata_richness_distribution": {"rich_structured_metadata": 41}, "records_with_page_count": 41, "records_with_title": 41, "schema_field_availability": {"artist_id": 41, "artist_name": 41, "caption": 22, "extractor_category": 41, "gallery_dl_filename": 41, "image_url_kinds": 41, "page_count": 41, "page_index": 41, "tags": 41, "title": 41, "translated_tags": 0, "work_id": 41}}`.
- Raw Pixiv tag availability: `{"exact_raw_tags_public": false, "normalized_unicode_tag_occurrences": 342, "raw_tag_order_preserved": true, "records_with_raw_pixiv_tags": 41, "total_raw_pixiv_tag_occurrences": 342}`.

## Candidate Classification

- Distribution: `{"candidate_kind_counts": {"ambiguous_proper_noun_candidate": 253, "entity_candidate": 104, "original_work_context": 3, "tag_candidate": 69, "unknown_or_unresolved_pixiv_tag": 6}, "candidate_namespace_counts": {"ambiguous": 256, "artist": 42, "character": 27, "copyright": 35, "general": 69, "unknown": 6}, "candidate_reason_counts": {"deterministic_general_descriptor_fallback": 8, "deterministic_original_marker": 3, "deterministic_proper_noun_shape_unverified": 253, "existing_tag_category_match": 4, "external_tag_category_lookup": 68, "no_provenance_backed_category": 6, "pixiv_parenthetical_character_work_pattern": 52, "pixiv_user_metadata": 41}, "db_write_allowed_count": 0, "lookup_source_counts": {"danbooru_tags_api_v2": 56, "deterministic_fallback": 264, "external_category_lookup_disabled_in_f3": 6, "local_db_read_only": 4, "pixiv_metadata": 41, "pixiv_parenthetical_pattern": 52, "safebooru_tags_xml_api_v1": 12}, "manual_review_required_count": 333}`.
- Artist candidates: `42`.
- Copyright/series candidates: `35`.
- Character candidates: `27`.
- General descriptive candidates: `69`.
- Ambiguous/unknown candidates: `262`.
- Sensitive/meta candidates: `0`.
- Alias-group candidates: `0`.
- Lookup source coverage: `{"deterministic_fallback": 264, "external_category_lookup_disabled_in_f3": 6, "external_category_lookup_enabled": true, "external_category_lookup_public_docs": {"danbooru": "https://danbooru.donmai.us/wiki_pages/help%3Aapi", "safebooru": "https://safebooru.org/index.php"}, "external_category_lookup_sources": ["danbooru_tags_api_v2", "danbooru_tag_aliases_api_v2", "safebooru_tags_xml_api_v1"], "legacy_source_candidate_count": 0, "local_db_read_only": 4, "source_candidate_counts": {"danbooru_tags_api_v2": 56, "safebooru_tags_xml_api_v1": 12}}`.
- Automated category lookup: `{"cache_hit_count": 18, "cache_miss_count": 0, "cache_write_count": 0, "cache_write_enabled": true, "enabled": true, "hard_lookup_limit": 500, "hit_count": 18, "lookup_delay_seconds": 0.05, "lookup_error_count": 0, "lookup_limit": 200, "lookup_source": "multisource_tag_category_lookup_v1", "lookup_sources_attempted": ["danbooru_tags_api_v2", "danbooru_tag_aliases_api_v2", "safebooru_tags_xml_api_v1"], "lookup_timeout_seconds": 5, "negative_cache_hit_count": 347, "not_found_count": 115, "provider_block_reason": null, "provider_blocked": false, "request_count": 0, "resolved_namespace_counts": {"artist": 1, "character": 1, "copyright": 3, "general": 13}, "source_cache_hit_counts": {"danbooru_tags_api_v2": 17, "safebooru_tags_xml_api_v1": 1}, "source_cache_miss_counts": {}, "source_cache_write_counts": {}, "source_hit_counts": {"danbooru_tags_api_v2": 17, "safebooru_tags_xml_api_v1": 1}, "source_lookup_error_counts": {}, "source_negative_cache_hit_counts": {"danbooru_tag_aliases_api_v2": 116, "danbooru_tags_api_v2": 116, "safebooru_tags_xml_api_v1": 115}, "source_not_found_counts": {"safebooru_tags_xml_api_v1": 115}, "source_request_counts": {}, "unique_normalized_tag_count": 133}`.
- Classification improvement vs previous F3: `{"ambiguous_unknown_delta_vs_previous_f3": -43, "baseline_previous_f3": {"ambiguous_unknown_candidate_count": 305, "character_candidate_count": 0, "copyright_series_candidate_count": 0}, "character_delta_vs_previous_f3": 27, "copyright_series_delta_vs_previous_f3": 35, "current_ambiguous_unknown_candidate_count": 262, "current_character_candidate_count": 27, "current_copyright_series_candidate_count": 35, "unique_tag_lookup_hit_count": 18, "unique_tag_lookup_total_count": 133}`.
- Before/after comparison: `{"current_multisource_pattern_f3b": {"ambiguous_unknown_candidate_count": 262, "character_candidate_count": 27, "copyright_series_candidate_count": 35, "unique_tag_lookup_hit_count": 18, "unique_tag_lookup_total_count": 133}, "original_f3_baseline": {"ambiguous_unknown_candidate_count": 305, "character_candidate_count": 0, "copyright_series_candidate_count": 0}, "previous_danbooru_only_f3b": {"ambiguous_unknown_candidate_count": 262, "character_candidate_count": 1, "copyright_series_candidate_count": 9, "unique_tag_lookup_hit_count": 17, "unique_tag_lookup_total_count": 133}}`.
- Coverage target: `{"additional_source_evaluation": {"gelbooru_dapi": "rejected_for_this_pilot_live_probe_returned_http_401_without_credentials", "safebooru_dapi": "implemented_public_xml_tag_type_lookup"}, "ambiguous_unknown_reduction_vs_original_f3": 0.141, "automated_paths_attempted": ["local_db_read_only", "danbooru_tags_api_v2", "danbooru_tag_aliases_api_v2", "safebooru_tags_xml_api_v1", "pixiv_parenthetical_pattern_parser", "deterministic_fallback"], "automated_paths_exhausted": true, "coverage_target_thresholds": {"ambiguous_unknown_reduction": 0.6, "high_value_resolution": 0.8, "unique_tag_coverage": 0.6}, "high_value_proper_noun_like_resolution_rate": 0.1484, "high_value_proper_noun_like_resolved_count": 19, "high_value_proper_noun_like_tag_count": 128, "lookup_limit": 200, "lookup_limit_covers_all_unique_tags": true, "resolved_unique_tag_count": 22, "resolved_unique_tag_coverage": 0.1654, "target_status": "not_reached_after_exhaustion", "unique_tag_count": 133}`.
- Unresolved reason buckets: `{"deterministic_original_marker": 3, "deterministic_proper_noun_shape_unverified": 253, "no_provenance_backed_category": 6}`.
- Parenthetical pattern summary: `{"parenthetical_candidate_count": 52, "parenthetical_character_candidate_count": 26, "parenthetical_confirmed_assignment_count": 0, "parenthetical_copyright_candidate_count": 26, "parenthetical_resolved_count": 52}`.

## Lookup Cache

- Cache/mapping table: `{"cache_hit_count": 18, "cache_miss_count": 0, "cache_write_count": 0, "cache_write_enabled": true, "cache_write_mode": "db_cache_enabled", "confirmed_assignment_write_count": 0, "entity_evidence_write_count": 0, "media_entity_candidate_write_count": 0, "provider_cache_write_count": 0, "table": "blombooru_external_tag_category_lookup_cache", "table_available": true, "truth_table_write_count": 0}`.

## Original / Ambiguous Handling

- Original/unknown handling: `{"original_work_status_distribution": {"known_or_candidate_work_context": 28, "original_or_unknown_work_context": 11, "original_work_context_claimed_by_pixiv_tag": 2}, "raw_pixiv_original_tag_is_not_confirmed_truth": true, "records_without_forced_copyright": 13}`.

## Manual Review Needs

- Manual review: `{"candidate_rows_requiring_manual_review": 333, "manual_review_is_sparse_correction_oriented": true, "media_records_requiring_manual_review": 41, "private_manual_review_guide": ".local_manifests/phase-4.4p2r-f3-pixiv-metadata-normalization-pilot/manual-review-guide.md"}`.

## Output Containment

- Containment: `{"gitignored_private_artifacts": true, "output_path_violation": false, "phase_output_root": ".local_manifests/<phase-private-root>", "private_artifacts_under_phase_root": true, "public_reports_under_docs_reports": true}`.

## Recommended Next Phase

- Decision: `B_harden_lookup_cache_and_gallery_dl_command_boundary_before_persistence`.
- Reason: `gallery_dl_command_boundary_is_conditional_or_dry_run,automated_category_lookup_coverage_below_threshold`.
- DB persistence: `cache_table_only_current_stage_LocalSourceHint_and_PixivMetadata_wait`.

## Safety Confirmation

- `public_report_contains_exact_pixiv_ids`: `False`.
- `public_report_contains_exact_media_ids`: `False`.
- `public_report_contains_exact_local_filenames`: `False`.
- `public_report_contains_exact_local_paths`: `False`.
- `public_report_contains_raw_gallery_dl_json`: `False`.
- `public_report_contains_raw_image_urls`: `False`.
- `public_report_contains_raw_pixiv_tags`: `False`.
- `db_write`: `False`.
- `db_write_limited_to_external_tag_category_lookup_cache`: `True`.
- `db_migration`: `True`.
- `db_migration_limited_to_external_tag_category_lookup_cache`: `True`.
- `local_source_hint_write`: `False`.
- `provider_cache_write`: `False`.
- `external_tag_category_lookup_cache_write`: `0`.
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
