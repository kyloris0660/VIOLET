# Phase 4.4-P2R-F7a: LLM-backed source name candidate extraction

## Summary

- Built a bounded source-layer name candidate extraction run over existing DB/source-layer metadata.
- Output is an unconfirmed candidate pool only; no SourceConcept, Entity, media_tags, TagTranslation, or assignment truth paths are written.
- Private review pack is stored under `.local_manifests` and is intentionally not committed.

## Run

- Run ID: `phase-4.4p2r-f7a-llm-source-name-candidates-20260604T172539Z-e3a9fcfc`
- Branch: `codex/phase44p2r-f7a-llm-source-name-candidates`
- Head SHA: `fddedef0c835e511c26d5b65e83cb7b5f23b358e`
- Extractor version: `phase44p2r_f7a_source_name_candidate_extractor_v1`
- Prompt version: `phase44p2r_f7a_llm_source_name_candidate_extraction_v1`

## Data Sample

- Groups processed: `12`
- Unique raw strings: `122`
- Groups by provider: `{"pixiv": 12}`
- Groups by data origin: `{"real_dev_db": 12}`

## Scale / Known Limitations

- A larger `50`-group / `500`-unique-string run was attempted first, but it exceeded a `1800s` tool timeout and was stopped before persistence. No F7a candidate rows were written by that interrupted run.
- The completed persisted run was reduced to `12` real dev DB Pixiv/source groups and `122` unique raw strings so the phase could produce a reviewable LLM-backed extraction pack within the available runtime.
- `7` records produced valid normalized LLM extraction records; `5` records received explicit `extraction_error_terminal` verdicts after invalid JSON output and retained deterministic guardrail candidates where possible.
- Candidate quality is sufficient to audit the F7a pipeline and artifacts, but not yet sufficient to start F7b SourceConcept linking without manual review and prompt/schema hardening.

## LLM

- Provider mode: `fallback_only`
- Uses fallback provider: `True`
- Uses primary model: `False`
- API call attempts: `11`
- Chunks attempted: `11`
- Cache hits: `1`
- Elapsed seconds: `682.254`
- Cost estimate: `not_available_from_provider_response`

## Extraction Results

- Record verdict counts: `{"extraction_error_terminal": 5, "multiple_candidates_found": 7}`
- Candidate total: `173`
- Candidate by role: `{"artist_creator": 41, "character": 75, "source_title": 14, "unknown_name_like": 5, "work_title": 38}`
- Candidate by status: `{"active_candidate": 154, "needs_review": 19}`
- Candidate by action: `{"ai_model_character_tag": 16, "direct_name": 66, "normal_tag_candidate": 6, "parenthetical_split": 18, "popularity_suffix_stripped": 10, "provider_structured_field": 57}`
- Popularity prefix extractions: `10`
- Rejected tags: `44`
- Rejected by reason: `{"descriptive_general": 24, "duplicate": 1, "explicit_r18_meta": 3, "not_name_like": 7, "popularity_meta": 9}`
- Meta tags: `10`
- Ambiguous items: `5`
- Validation failures: `5`

## DB Write Summary

- Apply DB: `True`
- Forbidden truth table write count: `0`
- Allowed table deltas: `{"blombooru_source_name_candidate_extraction_runs": 1, "blombooru_source_name_candidate_record_verdicts": 12, "blombooru_source_name_candidates": 173}`

## Review Pack

- Artifact directory: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates`
- Manual review guide: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/manual-review-guide.md`
- Name candidates CSV: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/name-candidates.csv`
- Record verdicts CSV: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/record-verdicts.csv`
- LLM inputs JSONL: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/llm-inputs.jsonl`
- LLM outputs JSONL: `.local_manifests/phase-4.4p2r-f7a-llm-source-name-candidates/llm-outputs.jsonl`

## Safety Confirmation

- No SourceConcept linking.
- No Entity, EntityAlias, EntityEvidence, MediaEntityCandidate, MediaEntityAssignment, LocalSourceHint, confirmed assignment, media_tags, or TagTranslation writes.
- No provider/gallery-dl/source enrichment run.
- No image upload and no source/iCloud/app-managed storage mutation.
- No push to main and no merge.

## Next Step

Review the private candidate pack before deciding whether F7b SourceConcept linking is ready.
