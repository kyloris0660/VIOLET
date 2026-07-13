# SCV2-R2R: Autonomous Recall and Search Closure

## Status

- Contract status: `partial_autonomous_closure`.
- Branch: `codex/scv2-r2r-autonomous-recall-search-closure`.
- Evidence code SHA: `56a654bb16509c72d3b90c86ceee9d57b5e40f0e`.
- autonomous_candidate_closure_completed = `True`.
- no_human_review_dependency = `True`.
- materialized_needs_review_eliminated = `True`.
- graph_constraint_safety_passed = `True`.
- search_closure_completed = `False`.
- experimental_fallback_enabled_by_default = `False`.
- context_aware_search_followup_required = `True`.
- full_library_scale_approved = `False`.

## Accepted autonomous closure

- Candidate population: `3319`.
- must_link / cannot_link / deferred_nonblocking: `1522` / `1791` / `6`.
- Coverage / unaccounted: `1.0` / `0`.
- Materialized SourceConcept / needs_review: `1083` / `0`.
- Graph invariants: `{'deferred_nonblocking_split_count': 6, 'deterministic_hard_conflict_count': 0, 'direct_cannot_violation_count': 0, 'directly_blocked_split_count': 0, 'largest_component_signal_count': 88, 'review_or_deferred_edge_used_in_union_count': 0, 'transitive_cannot_violation_count': 0, 'transitively_blocked_split_count': 233, 'true_unexplained_undermerge_count': 0, 'unauthorized_unknown_role_materialization_count': 0, 'unexplained_proof_grade_same_regression_count': 0}`.

## Zero-provider closeout

- Provider calls / surface initialized: `0` / `False`.
- Existing working DB reused: `True`.
- Graph/materialization rebuilt: `False` / `False`.
- Historical measured tokens / cost lower bound: `1744200` / `$3.4884`.
- Historical missing-usage calls / complete actual cost: `99` / `None`.

## Persisted runtime search benchmark

- Expanded families / seeds: `9488` / `17424`.
- Identity path: `{'matched_seed_count': 2727, 'unmatched_seed_count': 14697, 'symmetric_family_count': 7254, 'asymmetric_family_count': 2234, 'recall': 0.156508, 'average_pairwise_jaccard': 0.7252}`.
- Experimental fallback path: `{'matched_seed_count': 11236, 'unmatched_seed_count': 6188, 'symmetric_family_count': 2833, 'asymmetric_family_count': 6655, 'recall': 0.644858, 'average_pairwise_jaccard': 0.2572}`.
- False broad-union indicators / unexpected media: `9186` / `217739`.
- Identity/fallback cannot contamination: `1370` / `7398`.
- Legacy 58-seed benchmark: `{'group_count': 10, 'seed_count': 58, 'r2_baseline': {'symmetric_group_count': 0, 'unmatched_seed_count': 16, 'average_pairwise_jaccard': 0.1552}, 'identity_path': {'symmetric_group_count': 0, 'unmatched_seed_count': 51, 'average_pairwise_jaccard': 0.7725}, 'evidence_fallback_path': {'symmetric_group_count': 0, 'unmatched_seed_count': 42, 'average_pairwise_jaccard': 0.5245}, 'symmetry_improved_vs_r2': False, 'unmatched_seeds_decreased_vs_r2': False, 'average_overlap_improved_vs_r2': True}`.
- benchmark_uses_persisted_runtime_index = `True`.
- runtime_benchmark_equality_passed = `True`.
- persisted_fallback_index = `{'index_version': 'source_concept_deferred_overlay_v1', 'row_count': 6596, 'active_row_count': 4947, 'blocked_row_count': 1649, 'relation_counts': {'cannot_link': 1649, 'deferred_nonblocking': 5, 'direct_evidence': 3942, 'must_link': 1000}, 'deterministic_content_fingerprint': 'b100ba9f93bdb242f41916454a2013f87d74baa984a2c4de091409fa690c544b'}`.

## Interpretation erratum (SCV2-ML1)

The numeric fields above are preserved exactly as measured, but their original
one-name/one-family interpretation is superseded. Search-result union is not
identity union. `cannot_link` prevents identity materialization and unsupported
alias propagation; it does not make independently supported same-name media an
invalid bare-name result.

Consequently, `false_broad_union_indicator_count`,
`cannot_linked_search_contamination_count`, the two path-specific cannot
contamination counts, `seeds_with_false_broad_union`, and their derived
`unexpected_media_count` are historical diagnostics under an overly restrictive
interpretation. They must not be used as generic proof of product-search failure
until ML1 reclassifies each result by direct/accepted-alias support, rejected
evidence, AND leakage, role/source constraints, and identity mutation. Shared
bare-name results across distinct identities are legitimate; additional terms
disambiguate through media-level AND intersection.

## Mutation and lifecycle proof

- R2R output mutation proof: `{'r2r_output_table_count': 8, 'tables': ['blombooru_source_concept_resolution_runs', 'blombooru_source_concept_signals', 'blombooru_source_concepts', 'blombooru_source_concept_aliases', 'blombooru_source_concept_evidence', 'blombooru_source_concept_signal_links', 'blombooru_source_concept_search_index', 'blombooru_source_concept_fallback_search_index'], 'changed_allowed_output_tables': [], 'unexpected_changed_tables': [], 'fallback_index_table_included': True, 'accepted_execution_before_row_count': 2654, 'closeout_run_before_row_count': 6596, 'fallback_index_before_row_count': 6596, 'fallback_index_after_row_count': 6596, 'fallback_index_first_fingerprint': 'e060ac072d74f57801b3a91037341ca890e030112df2ba7077c0d40b66525e7d', 'fallback_index_second_fingerprint': 'e060ac072d74f57801b3a91037341ca890e030112df2ba7077c0d40b66525e7d', 'fallback_index_second_fingerprint_match': True}`.
- Deferred overlay versioned / atomic: `True` / `True`.
- Fallback index generated / idempotent / identity union allowed: `True` / `True` / `False`.
- Final judgment regeneration cache-only / provider calls: `True` / `0`.
- Provider authorization remains recorded: `pr_135_autonomous_pair_closure`.

## Boundary

SCV2-R2R is an autonomous pair-closure and non-human materialization foundation with experimental source-layer fallback infrastructure. It does not claim ML1 multilingual/source-metadata closure, production search readiness, or full-library readiness. The next approved phase is SCV2-ML1, not the superseded SR1 route.
