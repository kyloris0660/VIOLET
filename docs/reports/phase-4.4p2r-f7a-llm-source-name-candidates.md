# Phase 4.4-P2R-F7a: LLM-backed source name candidate extraction rework

## Summary

- Reworked F7a around content eligibility, compact LLM schema, provider comparison, progress events, checkpoint/resume, and run-scoped persistence.
- Output remains an unconfirmed source-layer candidate pool only.
- No SourceConcept, Entity, media_tags, TagTranslation, assignment, provider/source enrichment, image upload, or source/iCloud mutation occurred.

## Run

- Run ID: `phase-4.4p2r-f7a-llm-source-name-candidates-20260605T082610Z-0eebe915`
- Branch: `codex/phase44p2r-f7a-llm-source-name-candidates`
- Head SHA: `ec0b71584d410ed36eef38381457b8c2e5c6dea0`
- Extractor version: `phase44p2r_f7a_source_name_candidate_extractor_v2`
- Prompt version: `phase44p2r_f7a_llm_source_name_candidate_extraction_compact_v2`
- Schema version: `source_name_candidate_extraction_compact_v2`
- Recommended default provider: `primary_openai`
- Fallback provider mode: `not_run`

## Eligibility Gate

- Eligible groups collected: `50`
- Excluded counts: `{"excluded_unknown_or_unclassified": 3}`
- Eligibility counts: `{"eligible_anime": 50, "excluded_unknown_or_unclassified": 3}`

## Provider Comparison

| provider_mode | records | units | raw_occ | llm_calls | avoided | wall_s | avg_s | p95_s | candidates | terminal | invalid_json | schema_fail | popularity | duplicate_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| primary_concurrent | 50 | 581 | 915 | 337 | 334 | 401.686 | 2.017 | 4.409 | 663 | 0 | 0 | 0 | 45 | 0.0 |

## Safety

- Source provider calls: `False`
- LLM provider calls: `True`
- LLM preflight calls: `1`
- LLM extraction calls attempted: `337`
- LLM provider modes: `["primary_concurrent"]`
- Forbidden truth table write count: `0`

## Review Pack

- Artifact directory: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates-primary-only-50groups-fresh-20260605T1628`
- Provider comparison: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates-primary-only-50groups-fresh-20260605T1628/provider-comparison-summary.csv`
- Checkpoint status: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates-primary-only-50groups-fresh-20260605T1628/run-checkpoint-status.json`
- Progress events: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates-primary-only-50groups-fresh-20260605T1628/run-progress-events.jsonl`

## Readiness

- F7a mergeability judgment: `True`
- F7b should start: `False`
- Reason: `primary_default_provider_completed_without_retryable_terminal_or_schema_errors`
