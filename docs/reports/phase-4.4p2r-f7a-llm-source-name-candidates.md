# Phase 4.4-P2R-F7a: LLM-backed source name candidate extraction rework

## Summary

- Reworked F7a around content eligibility, compact LLM schema, provider comparison, progress events, checkpoint/resume, and run-scoped persistence.
- Output remains an unconfirmed source-layer candidate pool only.
- No SourceConcept, Entity, media_tags, TagTranslation, assignment, provider/source enrichment, image upload, or source/iCloud mutation occurred.

## Run

- Run ID: `phase-4.4p2r-f7a-llm-source-name-candidates-20260605T110243Z-b0de7057`
- Branch: `codex/phase44p2r-f7a-llm-source-name-candidates`
- Head SHA: `e8d487679b58cfacdea05b62ab156f6c76200a69`
- Validated code head SHA: `e8d487679b58cfacdea05b62ab156f6c76200a69`
- Extractor version: `phase44p2r_f7a_source_name_candidate_extractor_v2`
- Prompt version: `phase44p2r_f7a_llm_source_name_candidate_extraction_compact_v3`
- Schema version: `source_name_candidate_extraction_compact_v2`
- Recommended default provider: `primary_openai`
- Fallback provider mode: `not_run`

## Eligibility Gate

- Eligible groups collected: `100`
- Excluded counts: `{"excluded_unknown_or_unclassified": 3, "media_missing_or_unlinked": 140}`
- Eligibility counts: `{"eligible_anime": 100, "excluded_unknown_or_unclassified": 3, "media_missing_or_unlinked": 140}`

## Provider Comparison

| provider_mode | records | units | raw_occ | llm_calls | avoided | wall_s | avg_s | p95_s | candidates | terminal | invalid_json | schema_fail | popularity | duplicate_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| primary_concurrent | 100 | 667 | 1079 | 375 | 412 | 421.296 | 1.824 | 4.314 | 730 | 0 | 0 | 0 | 49 | 0.0 |

## Safety

- Source provider calls: `False`
- LLM provider calls: `True`
- LLM preflight calls: `1`
- LLM extraction calls attempted: `375`
- LLM provider modes: `["primary_concurrent"]`
- Forbidden truth table write count: `0`

## Review Pack

- Artifact directory: `.local_manifests/phase-4.4p2r-f7a-final-validation-pack-phase-4.4p2r-f7a-llm-source-name-candidates-20260605T110243Z-b0de7057-e8d487679b58`
- Provider comparison: `.local_manifests/phase-4.4p2r-f7a-final-validation-pack-phase-4.4p2r-f7a-llm-source-name-candidates-20260605T110243Z-b0de7057-e8d487679b58/provider-comparison-summary.csv`
- Checkpoint status: `.local_manifests/phase-4.4p2r-f7a-final-validation-pack-phase-4.4p2r-f7a-llm-source-name-candidates-20260605T110243Z-b0de7057-e8d487679b58/run-checkpoint-status.json`
- Progress events: `.local_manifests/phase-4.4p2r-f7a-final-validation-pack-phase-4.4p2r-f7a-llm-source-name-candidates-20260605T110243Z-b0de7057-e8d487679b58/run-progress-events.jsonl`

## Readiness

- F7a mergeability judgment: `True`
- F7b should start: `False`
- Reason: `primary_default_provider_completed_without_retryable_terminal_or_schema_errors`
