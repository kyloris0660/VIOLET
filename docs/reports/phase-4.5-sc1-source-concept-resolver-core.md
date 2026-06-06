# Phase 4.5-SC1 Source Concept Resolver Core

## Summary

Phase 4.5-SC1 implements the provider-neutral source-layer SourceConcept resolver core. It groups multi-source name, tag, assertion, observation, alias, and structured provider signals into unconfirmed concepts through a blocking + edge-graph resolver.

This report is public/redacted: it contains counts and safety results only, not local raw names or private source values.

## Scope

- Included: additive SourceConcept schema, SourceConceptSignal adapters, blocking/edge graph resolver, run ledger, evidence/link/search-preview tables, validation pack.
- Not included: Entity truth, EntityAlias truth, MediaEntityAssignment, media_tags mutation, full search/UI integration, manual promotion UI.

## Counts

- Signals: 4602
- Concepts: 1151 ({'active': 355, 'needs_review': 796})
- Links: 3968 ({'active': 1103, 'needs_review': 2865})
- Aliases: 1737
- Evidence rows: 3968
- Search preview rows: 1737
- Edge candidates: 26664
- Undermerge violations: 0
- Overmerge violations: 0
- Fragmentation violations: 0
- Context conflict active merges: 0
- Alias context conflict active merges: 0
- Random holdout severe violations: 0
- Readiness passed: True

## Expanded Validation

{"active_concepts": 355, "ai_only_concept_count": 260, "context_conflict_active_merge_count": 0, "context_conflict_candidate_count": 1707, "general_source_tag_pollution_count": 0, "largest_concept_signal_counts": [124, 121, 97, 85, 79, 68, 68, 67, 63, 61], "needs_review_concepts": 796, "random_holdout_sample_size": 200, "random_holdout_severe_violation_count": 0, "repeated_canonical_fragmentation_violations": 0, "source_title_only_concept_count": 207, "total_concepts": 1151, "total_signals": 4602}

## Source Signal Inventory

{"f7a_source_name_candidate": 730, "media_tags": 105699, "provider_cache": 2, "provider_structured_fields": 200, "source_name_alias_candidate": 45, "source_name_observation": 459, "source_searchable_name_assertion": 300, "source_tag_observation": 710}

## F7a Final Pack Backfill Audit

- Run ID: `phase-4.4p2r-f7a-llm-source-name-candidates-20260605T110243Z-b0de7057`
- Candidate bundle count: 730
- Existing DB count before scoped import: 730
- Import needed: False
- LLM/provider calls for F7a backfill: false

## LLM Pair Adjudication

- Used: True
- Policy: `bounded_optional_primary_openai_only_after_deterministic_blocking`
- Judgments: 300
- Error count: 0
- Max calls: 300
- Budget cap USD: 50.0
- Projected cost USD: 0.097532
- Uses fallback provider: False
- Provider/source enrichment calls: false
- Image uploads: false

## Safety

- Forbidden truth table write count: 0
- Entity truth writes: false
- media_tags mutation: false
- Search/UI integration: false
- Phase 4.5-SC2 started: false

## Validation Pack

- Zip artifact: `phase-4.5-sc1-source-concept-resolver-core-final-context-guarded-v2.zip`
- Primary validation format: JSON/JSONL.
