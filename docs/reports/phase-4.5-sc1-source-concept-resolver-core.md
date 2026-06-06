# Phase 4.5-SC1 Source Concept Resolver Core

## Summary

Phase 4.5-SC1 implements the provider-neutral source-layer SourceConcept resolver core. It groups multi-source name, tag, assertion, observation, alias, and structured provider signals into unconfirmed concepts through a blocking + edge-graph resolver.

This report is public/redacted: it contains counts and safety results only, not local raw names or private source values.

## Scope

- Included: additive SourceConcept schema, SourceConceptSignal adapters, blocking/edge graph resolver, run ledger, evidence/link/search-preview tables, validation pack.
- Not included: Entity truth, EntityAlias truth, MediaEntityAssignment, media_tags mutation, full search/UI integration, manual promotion UI.

## Counts

- Signals: 4602
- Concepts: 1151 ({'active': 362, 'needs_review': 789})
- Links: 3968 ({'active': 1237, 'needs_review': 2731})
- Aliases: 1737
- Evidence rows: 3968
- Search preview rows: 1737
- Edge candidates: 26379
- Undermerge violations: 0
- Overmerge violations: 0
- Fragmentation violations: 0
- Readiness passed: True

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
- Projected cost USD: 0.097544
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

- Zip artifact: `phase-4.5-sc1-source-concept-resolver-core-final-semantic.zip`
- Primary validation format: JSON/JSONL.
