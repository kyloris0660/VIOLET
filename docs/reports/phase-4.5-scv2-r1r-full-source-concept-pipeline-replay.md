# Phase 4.5-SCV2-R1R Full SourceConcept Pipeline Replay

## Status

- Contract status: `target_met_full_chain`.
- Previous continuation status: `target_met_full_chain_reclassified_smoke_only`.
- Operator LLM approval used: `True`.
- Dev/test execute confirmation used: `True`.
- Provider policy: `primary_openai_compatible_only_no_fallback`.
- Complete SC1 pipeline executed: `True`.
- Deterministic pipeline executed: `True`.
- LLM adjudication requested/executed: `True` / `True`.
- LLM selected pairs / judgments: `6429` / `6429`.
- All eligible LLM pairs adjudicated: `True`.
- Input-scope fidelity gate: `matched_old_r1_scope`.
- Current run classification: `route_evidence_candidate`.
- Evidence code SHA: `67eb8576e186463ebb4720060c632b6a5e925dbd`.
- Report commit parent SHA: `67eb8576e186463ebb4720060c632b6a5e925dbd`.
- Worktree state at evidence generation: `tracked_clean_untracked_local_artifacts_ignored`.
- Code changed after evidence generation: `False`.
- A1R still required: `True`.

## Isolation

- VIOLET_ENV: `test`.
- DB target label: `blombooru_r1r_restored_test_20260618`.
- Production profile active: `False`.
- Production DB/storage/source mutation: `False`.
- Actual DB identity checked from write connection: `True`.
- Storage root checked before settings import: `True`.

## R1R Snapshot / Input Scope Recovery

- Recovery status: `ready_for_old_r1_scope_rerun`.
- Post-PX1/pre-R1 snapshot found: `False`.
- Existing dump restored: `True`.
- Restored DB label: `blombooru_r1r_restored_test_20260618`.
- Source artifact label: `r1r-private-existing-dump-20260618-blombooru`.
- Live production clone created: `False`.
- Operator approval needed for live clone: `False`.
- Operator approval needed for restore: `False`.
- Current production still has old-R1-equivalent inputs: `True`.
- R1R can continue from recovered DB: `True`.

### Recovery Search

| Searched label |
|---|
| `local-manifests-root-label` |
| `r1-post-px1-private-artifact-label` |
| `px1-private-artifact-label` |
| `a1-post-expansion-private-artifact-label` |
| `chatgpt-review-pack-labels` |
| `repo-local-backup-dump-snapshot-restore-labels` |
| `repo-data-backups-label` |
| `postgres-database-list-label` |

### Why blombooru_test Was Insufficient

- blombooru_test was a toy/unit/dev fixture: `True`.
- Previous runner chose blombooru_test because: `VIOLET_ENV=test with POSTGRES_DB=blombooru_test was the configured isolation target for the previous R1R run.`.
- Prompt/runner failed to locate old-R1-scale data: `True`.
- Existing local snapshot/dump was ignored by the smoke run: `True`.
- Post-PX1/pre-R1 DB snapshot found: `False`.
- Safest construction now: `Use the existing production-derived 2026-06-18 dump restored into a new dev/test DB; avoid live production TEMPLATE clone.`.

### Snapshot / Artifact Candidates

| Artifact label | Type | Scope | Exists | Safe to restore/use | old-R1 equivalent |
|---|---|---|---:|---:|---:|
| `r1r-private-existing-dump-20260618-blombooru` | `pg_dump_custom` | `current-production-derived-old-r1-scale` | `True` | `True` | `True` |
| `r1r-private-pre-px1-dump-20260610` | `pg_dump_custom` | `pre-px1-too-early` | `True` | `True` | `False` |
| `r1r-private-r1-artifact-pack` | `review_artifact_zip` | `old-r1-report-artifacts-not-db-dump` | `True` | `False` | `False` |
| `r1r-private-a1-chatgpt-review-pack` | `chatgpt_review_pack_zip` | `post-r1-audit-artifacts-not-db-dump` | `True` | `False` | `False` |

### Database Candidates

| DB label | read_only | media | eligible | source metadata | PX1 records | SourceConcept | old-R1 scale likely |
|---|---:|---:|---:|---:|---:|---:|---:|
| `blombooru` | `on` | `35545` | `35314` | `671` | `471` | `6094` | `True` |
| `blombooru_r1r_restored_test_20260618` | `on` | `3750` | `3687` | `671` | `471` | `2767` | `True` |
| `blombooru_test` | `on` | `307` | `206` | `1` | `0` | `58` | `False` |
| `blombooru_test_medium` | `on` | `522` | `508` | `None` | `None` | `None` | `False` |
| `blombooru_test_pilot` | `on` | `265` | `229` | `None` | `None` | `None` | `False` |
| `violet_s3a_m2_copy_e2e_055122_test` | `on` | `None` | `None` | `None` | `None` | `None` | `False` |
| `violet_s3a_m2_copy_e2e_055158_test` | `on` | `8` | `8` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_055228_test` | `on` | `27` | `27` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_055314_test` | `on` | `30` | `30` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_055408_test` | `on` | `958` | `958` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_061056_test` | `on` | `30` | `28` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_061143_test` | `on` | `466` | `466` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_062345_test` | `on` | `56` | `54` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_062455_test` | `on` | `973` | `971` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_110557_test` | `on` | `895` | `893` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_112914_test` | `on` | `898` | `894` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_121150_test` | `on` | `898` | `893` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_174906_test` | `on` | `None` | `None` | `None` | `None` | `None` | `False` |
| `violet_s3a_m2_copy_e2e_174934_test` | `on` | `848` | `846` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_180210_test` | `on` | `214` | `212` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_180537_test` | `on` | `214` | `212` | `0` | `0` | `0` | `False` |
| `violet_s3a_m2_copy_e2e_180903_test` | `on` | `848` | `846` | `0` | `0` | `0` | `False` |

### Old R1 Contamination Handling

- Restored DB contains old R1 SourceConcept outputs: `True`.
- Old R1 used as baseline only: `True`.
- Production SourceConcept mutation allowed: `False`.
- Required before target_met_full_chain:
  - `preserve media/media_tags/source metadata/PX1 input tables`
  - `run R1R with a new run label`
  - `do not treat restored SourceConcept rows as fresh R1R proof`
  - `in dev/test only, either clear/rebuild SourceConcept-owned output tables or use an isolated R1R run namespace before execute`

## Input Scope Fidelity

| Metric | Category | Required | Old R1 expected | Current R1R actual | Ratio | Status |
|---|---|---:|---:|---:|---:|---|
| `total_media` | `input_data_scale` | `True` | `3750` | `3750` | `1.0` | `matched` |
| `eligible_media` | `input_data_scale` | `True` | `3687` | `3687` | `1.0` | `matched` |
| `source_metadata_records_total` | `input_data_scale` | `True` | `671` | `671` | `1.0` | `matched` |
| `px1_source_metadata_records` | `input_data_scale` | `True` | `471` | `471` | `1.0` | `matched` |
| `source_tag_observations` | `input_data_scale` | `True` | `3727` | `4437` | `1.1905` | `matched` |
| `source_name_observations` | `input_data_scale` | `True` | `918` | `1377` | `1.5` | `matched` |
| `source_searchable_name_assertions` | `input_data_scale` | `True` | `918` | `1218` | `1.3268` | `matched` |
| `source_metadata_evidence` | `input_data_scale` | `True` | `3727` | `4386` | `1.1768` | `matched` |
| `resolver_input_signals` | `input_data_scale` | `True` | `12249` | `12249` | `1.0` | `matched` |
| `deterministic_edge_count` | `input_data_scale` | `True` | `42751` | `42751` | `1.0` | `matched` |
| `source_concept_replay_total` | `current_r1r_replay_output_scale` | `True` | `2887` | `2861` | `0.991` | `matched` |
| `source_concept_replay_active` | `current_r1r_replay_output_scale` | `True` | `1078` | `1078` | `1.0` | `matched` |
| `source_concept_replay_needs_review` | `current_r1r_replay_output_scale` | `True` | `1809` | `1783` | `0.9856` | `matched` |
| `source_concept_total` | `old_r1_persisted_baseline_scale` | `False` | `6094` | `2767` | `0.4541` | `baseline_only_insufficient` |
| `source_concept_active` | `old_r1_persisted_baseline_scale` | `False` | `1078` | `1064` | `0.987` | `matched` |
| `source_concept_needs_review` | `old_r1_persisted_baseline_scale` | `False` | `1809` | `1703` | `0.9414` | `matched` |
| `source_concept_superseded` | `old_r1_persisted_baseline_scale` | `False` | `3207` | `0` | `0.0` | `baseline_only_insufficient` |
| `llm_eligible_pair_count` | `llm_selected_accounting` | `True` | `6429` | `6429` | `1.0` | `matched` |
| `llm_selected_pair_count` | `llm_selected_accounting` | `True` | `6429` | `6429` | `1.0` | `matched` |

## Preserved Smoke Run

- Classification: `smoke_only_not_route_evidence`.
- Run id: `r1r-20260706T163719Z`.
- Signal / edge count: `99` / `170`.
- Selected pairs / judgments: `33` / `33`.

## Old R1 Baseline Isolation

- Baseline artifact label: `old-r1-sourceconcept-baseline`.
- Isolation artifact label: `old-r1-contamination-isolation`.
- SourceConcept-owned tables cleared/rebuilt in dev/test: `True`.
- Old R1 isolated before R1R persistence: `True`.
- Contamination handling method: `dev_test_sourceconcept_owned_delete_rebuild`.
- Baseline SourceConcept total/active/needs_review/superseded: `2767` / `1064` / `1703` / `0`.

## LLM Readiness

- Operator approved: `True`.
- Provider available: `True`.
- Provider/model configured: `primary_openai` / `gpt-4.1-mini`.
- Primary OpenAI-compatible adjudication calls made: `False`.
- Fallback provider used: `False`.
- Cache ready: `True`.
- Budget ready: `True`.
- Eligible pairs: `6429`.
- Selected pairs: `6429`.
- Selection policy: `budget_driven_all_eligible`.
- All eligible pairs selected/adjudicated: `True` / `True`.
- Judgment/error/cache counts: `6429` / `0` / `6429` hits, `0` misses.
- Estimated actual cost USD: `0.0`; exact provider cost available: `False`.
- Projected full eligible cost USD / budget cap USD: `2.058618` / `15.0`.
- Projected new-call cost after cache USD: `0.0`.
- Emergency call ceiling: `20000`.
- Fixed call cap is primary limiter: `False`.
- Provider required for cache-missing pairs: `False`.
- Provider not required for fully cached pairs: `True`.

## Durable LLM Cache

- Cache policy version: `source_concept_llm_adjudication_cache_v1`.
- Durable cache root label: `source-concept-llm-adjudication-cache`.
- Atomic cache writes: `True`.
- Compatible cache hits: `6429`.
- Exact-compatible cache hits: `6429`.
- Imported previous judgments: `0`.
- New provider calls / failures / remaining: `0` / `0` / `0`.
- Cost spent this run / avoided by cache USD: `0.0` / `2.058618`.
- Semantic/prior judgments counted as full-chain proof: `False`.

## Stage Manifest

| Stage | Status | Input | Output | Evidence |
|---|---:|---:|---:|---|
| `source_signal_adapters` | `verified` | `205447` | `12249` | `r1r-private-source_signal_adapters` |
| `media_tags_adapter` | `verified` | `196794` | `196794` | `r1r-private-media_tags_adapter` |
| `source_metadata_record_structured_field_adapter` | `verified` | `671` | `671` | `r1r-private-source_metadata_record_structured_field_adapter` |
| `source_tag_observation_adapter` | `verified` | `4437` | `4437` | `r1r-private-source_tag_observation_adapter` |
| `source_name_observation_adapter` | `verified` | `1377` | `1377` | `r1r-private-source_name_observation_adapter` |
| `source_searchable_name_assertion_adapter` | `verified` | `1218` | `1218` | `r1r-private-source_searchable_name_assertion_adapter` |
| `source_name_candidate_f7a_adapter` | `verified` | `903` | `903` | `r1r-private-source_name_candidate_f7a_adapter` |
| `provider_cache_adapter_or_zero_eligible_proof` | `verified` | `2` | `2` | `r1r-private-provider_cache_adapter_or_zero_eligible_proof` |
| `deterministic_blocking_key_generation` | `verified` | `12249` | `42751` | `r1r-private-deterministic_blocking_key_generation` |
| `deterministic_edge_graph_generation` | `verified` | `12249` | `42751` | `r1r-private-deterministic_edge_graph_generation` |
| `context_compatibility_guards` | `verified` | `42751` | `42751` | `r1r-private-context_compatibility_guards` |
| `alias_context_equivalence` | `verified` | `42751` | `42751` | `r1r-private-alias_context_equivalence` |
| `union_component_resolution` | `verified` | `42751` | `2767` | `r1r-private-union_component_resolution` |
| `bounded_llm_pair_planning` | `verified` | `42751` | `6429` | `r1r-private-bounded_llm_pair_planning` |
| `bounded_llm_provider_cache_budget_readiness` | `verified` | `6429` | `1` | `r1r-private-bounded_llm_provider_cache_budget_readiness` |
| `bounded_llm_pair_selection` | `verified` | `42751` | `6429` | `r1r-private-bounded_llm_pair_selection` |
| `bounded_llm_judgment_execution` | `verified` | `6429` | `6429` | `r1r-private-bounded_llm_judgment_execution` |
| `llm_decision_recording` | `verified` | `6429` | `6429` | `r1r-private-llm_decision_recording` |
| `llm_decision_effects_applied_or_recorded` | `verified` | `6429` | `6429` | `r1r-private-llm_decision_effects_applied_or_recorded` |
| `source_concept_owned_persistence` | `verified` | `2767` | `2767` | `r1r-private-source_concept_owned_persistence` |
| `mutation_proof` | `verified` | `29` | `1` | `r1r-private-mutation_proof` |
| `post_commit_verification` | `verified` | `2767` | `1` | `r1r-private-post_commit_verification` |
| `validation_pack_review_pack_generation` | `verified` | `24` | `1` | `r1r-private-validation_pack_review_pack_generation` |
| `public_redaction` | `verified` | `2` | `1` | `r1r-private-public_redaction` |

## SC1 vs old R1 vs R1R

| Pipeline step | SC1 expected | SC1 actual evidence | old R1 actual evidence | R1R actual evidence | R1R status | Impact if missing | Contract guard |
|---|---|---|---|---|---|---|---|
| source signal adapters | all SC1 adapters | SC1 public/private artifacts | media/source/PX1 adapters | R1R adapter inventory | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| deterministic blocking key generation | required | SC1 edge graph metrics | R1 resolver v2 graph | R1R edge count 42751 | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| deterministic edge graph generation | required | SC1 resolver summary | R1 edge graph generated | R1R edge count 42751 | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| context compatibility | required | SC1 shared service guards | R1 shared service | R1R shared service | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| alias/context equivalence | required | SC1 alias/context tests | R1 shared service | R1R shared service | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| union/component resolution | required | SC1 concept/link counts | R1 persisted concepts | R1R deterministic concepts | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| bounded LLM pair planning | required after blocking | SC1 selected 300 | old R1 disabled | R1R selected 6429 | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| bounded LLM pair adjudication | required for full chain | SC1 300 judgments | old R1 0 judgments | R1R judgments 6429 | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| LLM decision effects | record/apply source-layer only | SC1 LLM edges | old R1 none | R1R recorded decisions | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| SourceConcept persistence | SourceConcept-owned only | SC1 allowed tables | old R1 SourceConcept tables | R1R ready for execute gate | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| mutation proof | required | SC1 mutation proof | R1 mutation proof | R1R mutation proof | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| review pack | required | SC1 validation pack | R1 validation pack | R1R review pack | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |

## Safety

- Mutation proof passed: `True`.
- Public redaction passed: `True`.
- Review pack label: `r1r-private-review-pack`.
- This phase does not authorize R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion.

## Result

R1R produced full-chain SourceConcept replay evidence and may feed A1R.
