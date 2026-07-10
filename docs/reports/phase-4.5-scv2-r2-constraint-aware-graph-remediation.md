# SCV2-R2: Constraint-Aware SourceConcept Graph Remediation

## Status

- Contract status: `target_met_constraint_aware_r2`.
- Working DB: `blombooru_scv2_r2_test_20260710b`.
- R1R restored evidence DB preserved: `True`.
- Resolver evidence code SHA: `4b7b57c0d66299620322e9c653524788e376c0fe`.
- Resolver code changed after evidence: `False`.
- Final closeout regenerated reports/contracts only; database rerun: `False`.
- Browser validation: not required; no UI/runtime surface changed.

## Fixed upstream evidence

- Tables fingerprinted: `15`.
- Baseline-to-clone match: `True`.
- Before/after row-content match: `True`.
- Table row counts: `{'blombooru_media': 3750, 'blombooru_media_tags': 196794, 'blombooru_provider_cache': 2, 'blombooru_source_metadata_evidence': 4386, 'blombooru_source_metadata_records': 671, 'blombooru_source_name_alias_candidates': 45, 'blombooru_source_name_candidate_extraction_runs': 2, 'blombooru_source_name_candidate_record_verdicts': 112, 'blombooru_source_name_candidates': 903, 'blombooru_source_name_observations': 1377, 'blombooru_source_name_registry': 371, 'blombooru_source_searchable_name_assertions': 1218, 'blombooru_source_tag_observations': 4437, 'blombooru_source_tag_registry': 418, 'blombooru_tags': 4549}`.
- Raw rows and fingerprint values remain private.

## Constraint-aware graph

- Review-only edges used in Union-Find: `0`.
- Direct LLM cannot pairs inside materialized components: `0`.
- Transitive cannot violations: `0`.
- Unknown-role bridge candidates/materialized: `4455` / `0`.
- Oversized-block diagnostics: `{'blocking_block_count': 7683, 'processed_block_count': 4811, 'oversized_block_count': 40, 'oversized_partition_count': 73, 'oversized_largest_sub_block': 112, 'oversized_hub_edges_prevented': 3443, 'oversized_truncated_or_skipped_count': 34, 'edge_count': 45010, 'edge_counts_by_status': {'active': 10931, 'needs_review': 28104, 'weak': 4400, 'rejected': 1575}, 'edge_counts_by_type': {'stable_identity_anchor': 32322, 'same_surface_context': 246, 'context_only': 1551, 'unknown_role_review': 4455, 'same_scope_duplicate_review': 319, 'negative_guard': 2935, 'exact_canonical_key': 333, 'cooccurrence_context': 2849}}`.
- Context-equivalence diagnostics: `{'candidate_pair_count': 38, 'accepted_pair_count': 0, 'held_for_review_pair_count': 0, 'rejected_pair_count': 38, 'accepted_support_reasons': {}, 'materialized_context_alias_key_count': 0, 'independence_policy': 'two_distinct_records_and_two_media_provider_or_run_units_unless_normalized_equivalent'}`.

## Existing LLM judgment reuse

- Existing judgments: `6429`.
- Exact-compatible / stable-pair / semantic-prior / invalidated: `0` / `2080` / `4349` / `0`.
- Genuinely new or missing pairs: `2284`.
- New provider calls: `0`.
- New-pair portion status: `blocked_llm_approval_required`; projected future cost: `$0.73088`.

## Baseline vs post-R2

- Concepts total/active/needs_review: `2767` / `1064` / `1703` -> `5396` / `1092` / `4304`.
- Gap total: `4443` -> `9344`.
- Search aggregate before: `{'groups_tested': 10, 'seeds_tested': 58, 'matched_seeds': 42, 'unmatched_seeds': 16, 'symmetric_groups': 0, 'asymmetric_groups': 10, 'asymmetry_reason_buckets': {'concept_split': 33, 'needs_review_not_included_in_active_search': 9, 'unmatched_alias': 16, 'missing_alias_or_unmatched_seed': 10, 'active_only_vs_needs_review_contrast': 5}, 'unmatched_aliases_count_as_asymmetry': True, 'media_result_overlap_metrics': {'pairwise_jaccard_count': 32, 'average_pairwise_jaccard': 0.3752, 'min_pairwise_jaccard': 0.0}}`.
- Search aggregate after: `{'groups_tested': 10, 'seeds_tested': 58, 'matched_seeds': 42, 'unmatched_seeds': 16, 'symmetric_groups': 0, 'asymmetric_groups': 10, 'asymmetry_reason_buckets': {'concept_split': 33, 'needs_review_not_included_in_active_search': 7, 'unmatched_alias': 16, 'missing_alias_or_unmatched_seed': 10, 'active_only_vs_needs_review_contrast': 4}, 'unmatched_aliases_count_as_asymmetry': True, 'media_result_overlap_metrics': {'pairwise_jaccard_count': 32, 'average_pairwise_jaccard': 0.1539, 'min_pairwise_jaccard': 0.0}}`.
- Search symmetry: `0 / 10` -> `0 / 10`.
- Unmatched seeds: `16` -> `16`.
- Average pairwise Jaccard: `0.3752` -> `0.1539`.
- Metric deltas: `{'scalar_delta': {'concept_total': 2629, 'active': 28, 'needs_review': 2601, 'superseded': 0, 'gap_total': 4901}, 'gap_bucket_delta': {'cjk_alias_without_english_romaji_sibling': 194, 'danbooru_parenthetical_without_cjk_sibling': 2044, 'high_frequency_source_tag_or_name_unlinked': 0, 'identity_tag_present_no_source_concept_alias': 0, 'needs_review_cluster_with_no_active_alias_path': 2260, 'same_display_name_split_across_contexts': 201, 'same_normalized_alias_key_split_across_multiple_concepts': 202, 'source_assertion_present_not_connected': 0, 'source_name_present_no_source_concept_alias': 0, 'source_tag_present_no_source_concept_alias': 0}, 'search_delta': {'matched_seeds': 0, 'unmatched_seeds': 0, 'symmetric_groups': 0, 'asymmetric_groups': 0}}`.

## Quality

- Compatible same recall: `1.0`; regressions: `0`.
- Transitively incompatible same labels held apart with private reasons: `3`.
- Known cannot avoidance: `1.0`.
- Meaningful structural improvement: `True`.
- Constraint target met: `True`.
- Search quality improved: `False`.
- Gap quality improved: `False`.
- Recall closure complete: `False`.
- Route quality ready for scale: `False`.
- R2R follow-up required: `True`; R2R was not started or authorized by this closeout.
- Broad route/search quality non-regression: `False`.
- Interpretation: R2 met the constraint-aware graph-remediation target but intentionally produced a more conservative and fragmented graph. Search, gap, and recall closure remain incomplete.

## Safety

- Operation counts: `{'gallery_dl_calls': 0, 'provider_pixiv_network_calls': 0, 'ai_tagging_calls': 0, 'media_imports': 0, 'upstream_observation_mutations': 0, 'new_llm_provider_calls': 0, 'production_writes': 0, 'truth_path_writes': 0}`.
- No PX1-B, Provider-2, scale-up, Entity bridge, production, full-library execution, or truth promotion was started or authorized.
- Closeout calls: gallery-dl `0`, provider/Pixiv `0`, AI tagging `0`, media import `0`, LLM provider `0`.

## Validation

- R2 contract passed: `True`.
- Public redaction passed: `True`.
- Review pack integrity passed: `True`.
- Existing summary was rechecked through the corrected contract; no resolver or database execution was performed.
