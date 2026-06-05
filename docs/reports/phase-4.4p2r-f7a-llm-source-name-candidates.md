# Phase 4.4-P2R-F7a: LLM-backed source name candidate extraction rework

## Summary

- Reworked F7a around content eligibility, compact LLM schema, provider comparison, progress events, checkpoint/resume, and run-scoped persistence.
- Output remains an unconfirmed source-layer candidate pool only.
- No SourceConcept, Entity, media_tags, TagTranslation, assignment, provider/source enrichment, image upload, or source/iCloud mutation occurred.

## Run

- Run ID: `phase-4.4p2r-f7a-llm-source-name-candidates-20260605T054406Z-e7763999`
- Branch: `codex/phase44p2r-f7a-llm-source-name-candidates`
- Head SHA: `40d247b3afccec792846986c67afb2518f620a6b`
- Extractor version: `phase44p2r_f7a_source_name_candidate_extractor_v2`
- Prompt version: `phase44p2r_f7a_llm_source_name_candidate_extraction_compact_v2`
- Schema version: `source_name_candidate_extraction_compact_v2`

## Eligibility Gate

- Eligible groups collected: `5`
- Excluded counts: `{}`
- Eligibility counts: `{"eligible_anime": 5}`

## Provider Comparison

| provider_mode | records | units | raw_occ | llm_calls | avoided | wall_s | avg_s | p95_s | candidates | terminal | invalid_json | schema_fail | popularity | duplicate_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| primary_serial | 5 | 86 | 111 | 51 | 25 | 126.65 | 1.465 | 2.69 | 76 | 0 | 0 | 0 | 4 | 0.0 |
| fallback_serial | 5 | 86 | 111 | 73 | 25 | 612.116 | 7.108 | 22.857 | 53 | 14 | 0 | 1 | 7 | 0.0 |
| primary_concurrent | 5 | 86 | 111 | 51 | 25 | 57.613 | 1.887 | 4.043 | 77 | 0 | 0 | 0 | 6 | 0.0 |
| fallback_concurrent | 5 | 86 | 111 | 65 | 25 | 188.814 | 6.428 | 21.411 | 57 | 9 | 0 | 1 | 6 | 0.0 |

## Safety

- Source provider calls: `False`
- LLM provider calls: `True`
- LLM provider modes: `["primary_serial", "fallback_serial", "primary_concurrent", "fallback_concurrent"]`
- Forbidden truth table write count: `0`

## Review Pack

- Artifact directory: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates`
- Provider comparison: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/provider-comparison-summary.csv`
- Checkpoint status: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/run-checkpoint-status.json`
- Progress events: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/run-progress-events.jsonl`

## Readiness

- F7a mergeability judgment: `False`
- F7b should start: `False`
- Reason: `primary_viable_but_fallback_or_schema_failures_still_need_hardening_or_review`
