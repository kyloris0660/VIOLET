# Phase 4.5-SC1 Source Concept Resolver Core

## Summary

Phase 4.5-SC1 implements the provider-neutral source-layer SourceConcept resolver core. It groups multi-source name, tag, assertion, observation, alias, and structured provider signals into unconfirmed concepts.

This report is public/redacted: it contains counts and safety results only, not local raw names or private source values.

## Scope

- Included: additive SourceConcept schema, SourceConceptSignal adapters, deterministic resolver, run ledger, evidence/link/search-preview tables, validation pack.
- Not included: Entity truth, EntityAlias truth, MediaEntityAssignment, media_tags mutation, full search/UI integration, manual promotion UI.

## Counts

- Signals: 4549
- Concepts: 1502 ({'active': 423, 'needs_review': 1079})
- Links: 4438 ({'active': 1802, 'needs_review': 2636})
- Aliases: 2138
- Evidence rows: 4438
- Search preview rows: 2138

## Source Signal Inventory

{"f7a_source_name_candidate": 730, "media_tags": 105699, "provider_cache": 2, "provider_structured_fields": 200, "source_name_alias_candidate": 45, "source_name_observation": 459, "source_searchable_name_assertion": 300, "source_tag_observation": 710}

## F7a Final Pack Backfill Audit

- Run ID: `phase-4.4p2r-f7a-llm-source-name-candidates-20260605T110243Z-b0de7057`
- Candidate bundle count: 730
- Existing DB count before scoped import: 730
- Import needed: False
- LLM/provider calls: false

## Safety

- Forbidden truth table write count: 0
- Entity truth writes: false
- media_tags mutation: false
- Search/UI integration: false
- Phase 4.5-SC2 started: false

## Validation Pack

- Zip artifact: `phase-4.5-sc1-source-concept-resolver-core-phase-4.5-sc1-source-concept-resolver-core-20260605T142153Z-c733d62b.zip`
- Primary validation format: JSON/JSONL.
