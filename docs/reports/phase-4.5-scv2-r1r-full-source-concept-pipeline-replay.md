# Phase 4.5-SCV2-R1R Full SourceConcept Pipeline Replay

## Status

- Contract status: `smoke_only_not_route_evidence`.
- Previous continuation status: `target_met_full_chain_reclassified_smoke_only`.
- Operator LLM approval used: `True`.
- Dev/test execute confirmation used: `False`.
- Provider policy: `primary_openai_compatible_only_no_fallback`.
- Complete SC1 pipeline executed: `False`.
- Deterministic pipeline executed: `True`.
- LLM adjudication requested/executed: `True` / `False`.
- LLM selected pairs / judgments: `33` / `0`.
- Input-scope fidelity gate: `insufficient_input_scope`.
- Current run classification: `smoke_only_not_route_evidence`.
- A1R still required: `True`.

## Isolation

- VIOLET_ENV: `test`.
- DB target label: `blombooru_test`.
- Production profile active: `False`.
- Production DB/storage/source mutation: `False`.
- Actual DB identity checked from write connection: `True`.
- Storage root checked before settings import: `True`.

## Input Scope Fidelity

| Metric | Old R1 expected | Current R1R actual | Ratio | Status |
|---|---:|---:|---:|---|
| `total_media` | `3750` | `307` | `0.0819` | `insufficient` |
| `eligible_media` | `3687` | `206` | `0.0559` | `insufficient` |
| `source_metadata_records_total` | `671` | `1` | `0.0015` | `insufficient` |
| `px1_source_metadata_records` | `471` | `0` | `0.0` | `insufficient` |
| `source_tag_observations` | `3727` | `1` | `0.0003` | `insufficient` |
| `source_name_observations` | `918` | `0` | `0.0` | `insufficient` |
| `source_searchable_name_assertions` | `918` | `2` | `0.0022` | `insufficient` |
| `source_metadata_evidence` | `3727` | `0` | `0.0` | `insufficient` |
| `resolver_input_signals` | `12249` | `99` | `0.0081` | `insufficient` |
| `deterministic_edge_count` | `42751` | `170` | `0.004` | `insufficient` |
| `source_concept_total` | `6094` | `58` | `0.0095` | `insufficient` |
| `source_concept_active` | `1078` | `5` | `0.0046` | `insufficient` |
| `source_concept_needs_review` | `1809` | `53` | `0.0293` | `insufficient` |
| `source_concept_superseded` | `3207` | `0` | `0.0` | `insufficient` |
| `llm_eligible_pair_count` | `300` | `35` | `0.1167` | `insufficient` |
| `llm_selected_pair_count` | `300` | `33` | `0.11` | `insufficient` |

## Preserved Smoke Run

- Classification: `smoke_only_not_route_evidence`.
- Run id: `r1r-20260706T163719Z`.
- Signal / edge count: `99` / `170`.
- Selected pairs / judgments: `33` / `33`.

## LLM Readiness

- Operator approved: `True`.
- Provider available: `True`.
- Provider/model configured: `primary_openai` / `gpt-4.1-mini`.
- Primary OpenAI-compatible adjudication calls made: `False`.
- Fallback provider used: `False`.
- Cache ready: `True`.
- Budget ready: `True`.
- Eligible pairs: `35`.
- Selected pairs: `33`.
- Judgment/error/cache counts: `0` / `0` / `0` hits, `33` misses.
- Estimated actual cost USD: `0.011604`; exact provider cost available: `False`.
- Max calls / budget USD: `300` / `50.0`.

## Stage Manifest

| Stage | Status | Input | Output | Evidence |
|---|---:|---:|---:|---|
| `source_signal_adapters` | `verified` | `8081` | `99` | `r1r-private-source_signal_adapters` |
| `media_tags_adapter` | `verified` | `8077` | `8077` | `r1r-private-media_tags_adapter` |
| `source_metadata_record_structured_field_adapter` | `verified` | `1` | `1` | `r1r-private-source_metadata_record_structured_field_adapter` |
| `source_tag_observation_adapter` | `verified` | `1` | `1` | `r1r-private-source_tag_observation_adapter` |
| `source_name_observation_adapter` | `verified` | `0` | `0` | `r1r-private-source_name_observation_adapter` |
| `source_searchable_name_assertion_adapter` | `verified` | `2` | `2` | `r1r-private-source_searchable_name_assertion_adapter` |
| `source_name_candidate_f7a_adapter` | `verified` | `0` | `0` | `r1r-private-source_name_candidate_f7a_adapter` |
| `provider_cache_adapter_or_zero_eligible_proof` | `skipped_not_applicable` | `0` | `0` | `[blocked]` |
| `deterministic_blocking_key_generation` | `verified` | `99` | `170` | `r1r-private-deterministic_blocking_key_generation` |
| `deterministic_edge_graph_generation` | `verified` | `99` | `170` | `r1r-private-deterministic_edge_graph_generation` |
| `context_compatibility_guards` | `verified` | `170` | `170` | `r1r-private-context_compatibility_guards` |
| `alias_context_equivalence` | `verified` | `170` | `170` | `r1r-private-alias_context_equivalence` |
| `union_component_resolution` | `verified` | `170` | `54` | `r1r-private-union_component_resolution` |
| `bounded_llm_pair_planning` | `verified` | `170` | `33` | `r1r-private-bounded_llm_pair_planning` |
| `bounded_llm_provider_cache_budget_readiness` | `blocked` | `33` | `0` | `[blocked]` |
| `bounded_llm_pair_selection` | `verified` | `170` | `33` | `r1r-private-bounded_llm_pair_selection` |
| `bounded_llm_judgment_execution` | `blocked` | `33` | `0` | `[blocked]` |
| `llm_decision_recording` | `blocked` | `0` | `0` | `[blocked]` |
| `llm_decision_effects_applied_or_recorded` | `blocked` | `0` | `0` | `[blocked]` |
| `source_concept_owned_persistence` | `blocked` | `54` | `0` | `[blocked]` |
| `mutation_proof` | `verified` | `29` | `1` | `r1r-private-mutation_proof` |
| `post_commit_verification` | `blocked` | `54` | `0` | `[blocked]` |
| `validation_pack_review_pack_generation` | `verified` | `24` | `1` | `r1r-private-validation_pack_review_pack_generation` |
| `public_redaction` | `verified` | `2` | `1` | `r1r-private-public_redaction` |

## SC1 vs old R1 vs R1R

| Pipeline step | SC1 expected | SC1 actual evidence | old R1 actual evidence | R1R actual evidence | R1R status | Impact if missing | Contract guard |
|---|---|---|---|---|---|---|---|
| source signal adapters | all SC1 adapters | SC1 public/private artifacts | media/source/PX1 adapters | R1R adapter inventory | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| deterministic blocking key generation | required | SC1 edge graph metrics | R1 resolver v2 graph | R1R edge count 170 | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| deterministic edge graph generation | required | SC1 resolver summary | R1 edge graph generated | R1R edge count 170 | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| context compatibility | required | SC1 shared service guards | R1 shared service | R1R shared service | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| alias/context equivalence | required | SC1 alias/context tests | R1 shared service | R1R shared service | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| union/component resolution | required | SC1 concept/link counts | R1 persisted concepts | R1R deterministic concepts | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| bounded LLM pair planning | required after blocking | SC1 selected 300 | old R1 disabled | R1R selected 33 | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| bounded LLM pair adjudication | required for full chain | SC1 300 judgments | old R1 0 judgments | R1R judgments 0 | blocked | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| LLM decision effects | record/apply source-layer only | SC1 LLM edges | old R1 none | R1R blocked by input-scope gate | blocked | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| SourceConcept persistence | SourceConcept-owned only | SC1 allowed tables | old R1 SourceConcept tables | R1R dry-run no writes | blocked | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| mutation proof | required | SC1 mutation proof | R1 mutation proof | R1R mutation proof | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |
| review pack | required | SC1 validation pack | R1 validation pack | R1R review pack | verified | route approval remains blocked | r1r_full_source_concept_pipeline_contract_v1 |

## Safety

- Mutation proof passed: `True`.
- Public redaction passed: `True`.
- Review pack label: `r1r-private-review-pack`.
- This phase does not authorize R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion.

## Result

R1R did not produce route-evidence-grade full-chain replay evidence; A1R must not start as a route approval rerun yet.
