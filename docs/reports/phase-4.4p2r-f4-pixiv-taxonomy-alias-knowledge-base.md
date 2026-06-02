# 4.4-P2R-F4: Pixiv taxonomy / alias knowledge base

## Summary

- Target status: `classification_not_reached_top_high_impact_governed`.
- Unique tag coverage: `0.2`.
- High-value proper-noun resolution: `0.1394`.
- Top high-impact governance: `1.0`.
- Recommendation: `do_not_persist_LocalSourceHint_or_PixivMetadata_yet`.

## Scope

- Builds taxonomy / alias KB rows only.
- Does not write Entity, EntityAlias, EntityEvidence, MediaEntityCandidate, ProviderCache, NegativeLookupCache, media_tags, TagTranslation, LocalSourceHint, or confirmed assignments.

## PR #90 Baseline

- PR #90: `{"merge_commit": "e9d49ea921f5a98ed63e496647b89d0f322de522", "merged_at": "2026-06-02T10:50:31Z", "number": 90, "state": "MERGED", "url": "https://github.com/kyloris0660/VIOLET/pull/90"}`.
- Baseline: `{"ambiguous_unknown_candidate_count": 262, "high_value_proper_noun_like_resolution_rate": 0.1484, "high_value_proper_noun_like_resolved_count": 19, "high_value_proper_noun_like_tag_count": 128, "original_f3_ambiguous_unknown_candidate_count": 305, "resolved_unique_tag_count": 22, "resolved_unique_tag_coverage": 0.1654, "unique_tag_count": 133}`.

## Input / Strategy

- Input summary: `{"external_lookup_network_skipped": true, "local_source_prior_join": {"approved_future_source_content_classes": ["anime"], "future_eligibility_counts": {"eligible_for_future_entity_candidate": 46, "eligible_for_future_local_source_hint": 46, "ineligible_for_future_entity_candidate": 29, "ineligible_for_future_local_source_hint": 29}, "local_prior_content_class_distribution": {"anime": 546, "non_anime": 1, "unknown": 8}, "local_prior_join_ran": true, "local_prior_total_keys": 555, "local_prior_total_media": 555, "local_prior_total_media_inspected": 1989, "local_prior_without_metadata": 509, "match_content_class_counts": {"anime": 46}, "page_index_status_counts": {"page_index_within_page_count": 75}, "status_counts": {"metadata_matches_eligible_anime_local_prior": 46, "metadata_work_id_found_no_local_match": 29}}, "max_external_requests": 180, "max_records": 500, "max_unique_tags": 500, "max_work_ids": 100, "metadata_command_count": 50, "metadata_success_count": 50, "normalized_candidate_media_record_count": 75, "normalized_media_record_count": 75, "raw_record_count": 125, "raw_scope": {"current_run_raw_file_count": 50, "raw_input_scope": "current_run_only", "stale_raw_files_ignored_count": 0, "stale_raw_files_private": []}, "reuse_raw_metadata": true, "sample_size_requested": 50, "unique_normalized_tag_count": 210}`.
- Strategy layers attempted: `{"alias_cooccurrence_evidence": true, "curated_mapping_import_path": true, "danbooru_alias_canonical": true, "danbooru_exact": true, "gelbooru_or_unavailable_proof": true, "local_trusted_db_evidence": true, "multilingual_normalization": true, "pixiv_parenthetical_pattern": true, "pr90_lookup_cache": true, "safebooru": true, "unresolved_reason_bucketing": true}`.
- Lookup summary: `{"cache_error_cooldown_count": 0, "cache_expired_retryable_count": 0, "cache_hit_count": 18, "cache_hit_resolved_count": 18, "cache_miss_count": 374, "cache_negative_not_found_count": 332, "cache_write_count": 0, "cache_write_enabled": false, "enabled": true, "external_request_budget": 0, "hard_lookup_limit": 500, "hit_count": 18, "lookup_delay_seconds": 0.25, "lookup_error_count": 66, "lookup_limit": 500, "lookup_source": "f4_taxonomy_alias_cache_only_lookup_v1", "lookup_sources_attempted": ["danbooru_tags_api_v2", "danbooru_tag_aliases_api_v2", "safebooru_tags_xml_api_v1", "gelbooru_tags_xml_api_v1"], "lookup_timeout_seconds": 20, "negative_cache_hit_count": 332, "not_found_count": 110, "provider_block_reason": "external_lookup_network_skipped_after_bounded_live_timeout", "provider_blocked": true, "provider_blocked_sources": ["danbooru_tags_api_v2", "danbooru_tag_aliases_api_v2", "safebooru_tags_xml_api_v1", "gelbooru_tags_xml_api_v1"], "request_budget_exhausted": false, "request_count": 0, "resolved_namespace_counts": {"artist": 1, "character": 1, "copyright": 3, "general": 13}, "source_cache_hit_counts": {"danbooru_tags_api_v2": 17, "safebooru_tags_xml_api_v1": 1}, "source_cache_miss_counts": {"danbooru_tag_aliases_api_v2": 66, "danbooru_tags_api_v2": 66, "gelbooru_tags_xml_api_v1": 176, "safebooru_tags_xml_api_v1": 66}, "source_cache_write_counts": {}, "source_hit_counts": {"danbooru_tags_api_v2": 17, "safebooru_tags_xml_api_v1": 1}, "source_lookup_error_counts": {"f4_cache_only_network_skipped": 66}, "source_negative_cache_hit_counts": {"danbooru_tag_aliases_api_v2": 111, "danbooru_tags_api_v2": 111, "safebooru_tags_xml_api_v1": 110}, "source_not_found_counts": {"safebooru_tags_xml_api_v1": 110}, "source_request_counts": {}, "unique_normalized_tag_count": 194}`.

## Knowledge Base

- Taxonomy KB: `{"candidate_namespace_counts": {"ambiguous": 162, "artist": 1, "character": 19, "copyright": 9, "general": 13, "unknown": 6}, "entry_count": 210, "manual_override_entry_count": 0, "resolved_entry_count": 42, "status_counts": {"resolved": 42, "unresolved_governed": 168}, "unresolved_reason_buckets": {"provider_limited_or_lookup_error": 62, "provider_not_found": 106}}`.
- Alias KB: `{"entry_count": 76, "evidence_source_counts": {"multilingual_normalization": 5, "pixiv_parenthetical_pattern": 18, "pixiv_same_work_tag_cooccurrence": 53}, "manual_override_entry_count": 0, "relation_type_counts": {"cooccurrence_candidate": 53, "parenthetical_character_of_work": 18, "translation": 5}}`.
- KB writes: `{"alias_insert_count": 0, "alias_update_count": 76, "cache_migration_ran": false, "external_cache_table_available": true, "external_cache_write_count": 0, "kb_migration_ran": true, "table_names": ["blombooru_pixiv_tag_taxonomy_kb", "blombooru_pixiv_tag_alias_kb", "blombooru_external_tag_category_lookup_cache"], "taxonomy_alias_tables_available": true, "taxonomy_insert_count": 0, "taxonomy_update_count": 210}`.

## Coverage

- Coverage target: `{"ambiguous_unknown_candidate_count_after_f4": 528, "ambiguous_unknown_reduction_vs_pr90": -1.0153, "classification_target_reached": false, "coverage_target_thresholds": {"ambiguous_reduction": 0.6, "high_value_resolution": 0.8, "top_high_impact_governance": 1.0, "unique_tag_coverage": 0.6}, "governance_target_reached": true, "high_value_proper_noun_like_resolution_rate": 0.1394, "high_value_proper_noun_like_resolved_count": 29, "high_value_proper_noun_like_tag_count": 208, "resolved_unique_tag_count": 42, "resolved_unique_tag_coverage": 0.2, "target_reached": false, "target_status": "classification_not_reached_top_high_impact_governed", "thresholds_reached": {"ambiguous_reduction": false, "high_value_proper_noun_classification": false, "top_high_impact_governance": true, "unique_tag_classification": false}, "top_high_impact_count": 50, "top_high_impact_governance_rate": 1.0, "top_high_impact_governed_count": 50, "unique_tag_count": 210}`.
- Before/after: `{"f4": {"ambiguous_unknown_candidate_count": 528, "high_value_resolution_rate": 0.1394, "resolved_unique_tag_coverage": 0.2, "top_high_impact_governance_rate": 1.0}, "pr90": {"ambiguous_unknown_candidate_count": 262, "high_value_resolution_rate": 0.1484, "resolved_unique_tag_coverage": 0.1654}}`.
- Unresolved reason buckets: `{"provider_limited_or_lookup_error": 62, "provider_not_found": 106}`.
- Exact top unresolved tags are private-only: `private_unresolved_tags_analysis_csv`.

## Curated Mapping

- Curated mapping: `{"input_mapping_count": 0, "no_mapping_invented": true, "template_private_artifact": "private_curated_mapping_template_csv"}`.

## Recommendation

- Recommendation: `{"entity_candidate_persistence": "blocked_until_category_coverage_and_provenance_improve", "next_route": "continue_taxonomy_alias_kb_with_curated_mapping_or_new_source", "persistence_recommendation": "do_not_persist_LocalSourceHint_or_PixivMetadata_yet", "reason": "coverage_target_not_reached"}`.

## Safety Confirmation

- Safety: `{"additive_db_migration": true, "app_managed_storage_mutation": false, "automatic_entity_creation": false, "confirmed_assignment": false, "db_write_limited_to_taxonomy_alias_external_tag_category_cache": true, "entity_alias_write": false, "entity_evidence_write": false, "entity_external_identity_write": false, "entity_resolver": false, "entity_write": false, "llm_classification": false, "local_source_hint_write": false, "media_entity_assignment_write": false, "media_entity_candidate_write": false, "media_tags_mutation": false, "merge": false, "negative_lookup_cache_write": false, "provider_cache_write": false, "push_main": false, "sample_specific_hardcoded_mapping": false, "source_or_icloud_mutation": false, "tag_translation_mutation": false}`.
- Redaction: `{"contains_exact_local_paths": false, "contains_exact_media_ids": false, "contains_exact_pixiv_ids": false, "contains_raw_gallery_dl_json": false, "contains_raw_image_urls": false, "contains_raw_pixiv_tags": false, "exact_unresolved_tags_private_only": true}`.
