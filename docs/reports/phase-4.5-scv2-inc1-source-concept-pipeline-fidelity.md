# 4.5-SCV2-INC1: SourceConcept Pipeline Fidelity Incident Investigation

## Summary

INC1 confirms a pipeline fidelity incident. SC1 established and actually ran bounded LLM pair adjudication as part of the full SourceConcept resolver chain. R1 executed deterministic resolver stages and persisted SourceConcept-scoped outputs, but R1 did not request or run LLM pair adjudication. Therefore R1/A1 cannot be used as full-chain route approval evidence unless the conclusion is later changed by recovered artifacts.

- Conclusion: `llm_stage_missing_incident`
- Technical severity: `S1` - phase output invalid or route decision incomplete
- Project governance severity: `P0/P1 pipeline fidelity incident`
- Route gate status: `blocked_pending_pipeline_fidelity_remediation`
- Required remediation: 4. R1/A1 invalid; rerun R1 and A1 after fixing runner/config.
- Required follow-up after R1R: Phase 4.5-SCV2-A1R: rerun A1 route audit after R1R outputs exist
- R2 status: blocked until R1R plus A1R are complete; do not start R2.

## Provenance

- Runtime audit git SHA: `12249162d47988875c7e2ce4588b0450b3dc6f01`.
- Runtime audit SHA scope: git rev-parse HEAD when the INC1 read-only file-artifact investigation runner executed.
- Public report generated from runtime SHA: `12249162d47988875c7e2ce4588b0450b3dc6f01`.
- Final PR head SHA if different: `reported by PR metadata/final delivery after the report-generation commit; a commit cannot truthfully contain its own final SHA.`.
- Dirty worktree clean at runtime: `False`; dirty entry count: `14`; status filenames redacted: `True`.

## Incident Statement

The incident concern is that Phase 4.5-SCV2-R1 may not have faithfully executed the full SourceConcept resolver pipeline established in Phase 4.5-SC1. The specific stage under investigation is bounded LLM pair adjudication.

## Why This Investigation Was Opened

SC1 explicitly reported LLM pair adjudication as used with 300 judgments and a max-call budget of 300. R1 and A1 subsequently became route-decision inputs for proposed SCV2-R2 work. If R1 omitted the SC1 LLM stage, R1/A1 route evidence is incomplete for full-chain approval.

## SC1 Established Pipeline

SC1 established the shared graph resolver pipeline with source signal adapters, blocking, edge graph generation, context compatibility, alias/context equivalence, union/component resolution, optional bounded primary-provider LLM pair adjudication after deterministic blocking, SourceConcept-scoped persistence, mutation proof, and validation-pack reporting.

SC1 evidence:

- Public LLM section: used=`True`, policy=`bounded_optional_primary_openai_only_after_deterministic_blocking`, judgments=`300`, max_calls=`300`.
- Private LLM judgment file line count: `300`.
- Resolver output records LLM same-concept edges: `True`.
- Runner supports and passes `LLMAdjudicationConfig`: `True`.
- Exact final v5 shell transcript found: `False`.
- Config-equivalent command shape proven by runner and private summary:
  `scripts/run_phase45_sc1_source_concept_resolver.py --apply-db --apply-f7a-final-pack --use-llm-adjudication --max-llm-calls 300 --max-llm-budget-usd 50 --llm-cache-dir .local_manifests/phase-4.5-sc1-llm-adjudication-cache`

## R1 Actual Pipeline

R1 executed the graph resolver in execute mode with deterministic stages and SourceConcept-scoped persistence, but its private ledger records the LLM plan as disabled:

- R1 mode: `execute`
- R1 resolver version: `source_concept_resolver_core_v2_graph`
- R1 LLM used: `False`
- R1 judgment count: `0`
- R1 LLM plan status: `disabled`
- R1 LLM missing reason: `llm_adjudication_not_requested`
- R1 public commands: `python scripts/run_phase45_scv2_r1_post_px1_source_concept_triage.py --dry-run --output-dir .local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage --write-public-report; python scripts/run_phase45_scv2_r1_post_px1_source_concept_triage.py --execute --output-dir .local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage --write-public-report --confirm-execution EXECUTE_PHASE45_SCV2_R1_SOURCE_CONCEPT_TRIAGE`
- R1 runner has LLM flag support: `False`
- R1 tests include LLM parity guard: `False`

## SC1 vs R1 Comparison Table

| pipeline step | SC1 expected | SC1 actual evidence | R1 expected | R1 actual evidence | status | impact if missing | remediation required |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Source signal adapters | Consume resolver-supported source-layer signals. | SC1 summary signal origins include ['ai_model_tag', 'f7a_candidate', 'provider_structured_field', 'source_alias_candidate', 'source_assertion', 'source_name_observation', 'source_tag_observation']. | R1 should reuse SC1 adapters for post-PX1 evidence. | R1 adapter accounting lists ['SourceMetadataEvidence', 'SourceMetadataRecord', 'SourceNameObservation', 'SourceSearchableNameAssertion', 'SourceTagObservation', 'media_tags', 'source_name_candidates']. | matched | Missing adapters would undercount or misroute source-layer evidence. | Keep adapter inventory in R1R and fail if required adapters are absent. |
| media_tags adapter | Consume eligible identity/category/parenthetical media_tags as weak source signals. | SC1 runner/service signal counts include media_tags-origin signals. | R1 should consume eligible media_tags without mutating media_tags. | consumed as normal_media_tag or ai_model_tag signals only for concept-eligible identity/category/parenthetical tags | matched | Missing media_tags would change deterministic candidate coverage. | Preserve R1 media_tags adapter proof and mutation guard. |
| SourceMetadataRecord structured-field adapter | Consume source metadata structured fields where available. | SC1 source signal adapters include provider structured fields. | R1 should consume SourceMetadataRecord title/artist/raw fields. | consumed via provider_structured_field signals from explicit title/artist/raw metadata fields | matched | Missing structured fields would reduce PX1/source-backed evidence. | Keep adapter-specific accounting. |
| SourceTagObservation adapter | Consume source tag observations with general/meta pollution guards. | SC1 resolver tests cover source-layer signal handling. | R1 should consume SourceTagObservation while rejecting general/meta-only concepts. | consumed via source_tag_observation signals; general/meta tags stay rejected and do not enter concept buckets | matched | Missing source tags would reduce source-backed concept recall. | Keep general/meta rejection proof in R1R. |
| SourceNameObservation adapter | Consume observed source names. | SC1 resolver accepts source-name observation signals. | R1 should include SourceNameObservation evidence. | consumed via source_name_observation signals; requires_review rows stay needs_review | matched | Missing observations would leave name evidence unresolved. | Keep adapter accounting and sampled evidence. |
| SourceSearchableNameAssertion adapter | Consume searchable-name assertions with review-scoped handling. | SC1 source signal pipeline supports assertion-like name evidence. | R1 should consume assertions while keeping needs_review rows review-scoped. | consumed via source_assertion signals; needs_review rows stay review-scoped | matched | Missing assertions would affect search seed symmetry and gap counts. | Keep assertion-specific tests. |
| SourceNameCandidate / F7a adapter | Consume final F7a source-name candidates as source-layer evidence. | SC1 public summary records F7a final pack import. | R1 should consume active F7a candidates. | consumed as f7a_candidate signals when active | matched | Missing F7a candidates would reduce post-PX1 expansion fidelity. | Keep final-pack and candidate status checks. |
| ProviderCache adapter | Provider cache may provide provider-neutral source metadata evidence without provider calls. | SC1 report forbids provider enrichment calls but resolver service can consume cached provider-neutral signals. | R1 should either prove ProviderCache adapter use or document it as not in input scope. | R1 adapter accounting does not list ProviderCache; mutation proof shows no provider_cache writes. | unproven_or_not_reported_in_r1 | If cached provider evidence was expected, R1 may have missed a source signal family. | R1R must explicitly prove ProviderCache input accounting or document zero eligible records. |
| blocking key generation | Generate deterministic blocking keys before graph resolution. | SC1 resolver version source_concept_resolver_core_v2_graph reports edge graph metrics. | R1 should use the same graph resolver blocking path. | R1 resolver version source_concept_resolver_core_v2_graph reports blocking/edge metrics. | matched | Missing blocking changes graph topology and LLM candidate pool. | Keep resolver-version and edge-graph proof. |
| edge graph generation | Build deterministic edges from compatible source signals. | SC1 summary includes resolver edge_graph. | R1 should produce deterministic edge_graph before any optional LLM stage. | R1 deterministic execution edge_graph={'blocking_block_count': 7670, 'edge_count': 42751, 'edge_counts_by_status': {'active': 10400, 'needs_review': 23471, 'rejected': 1537, 'weak': 7343}, 'edge_counts_by_type': {'context_only': 1551, 'cooccurrence_context': 5788, 'exact_canonical_key': 358, 'negative_guard': 2842, 'same_scope_duplicate_review': 381, 'same_surface_context': 470, 'stable_identity_anchor': 31361}, 'oversized_block_count': 40, 'processed_block_count': 4801}. | matched | Missing graph stage invalidates resolver output. | No remediation for deterministic graph; retain proof in R1R. |
| context compatibility | Apply context compatibility before linking ambiguous names. | SC1 service contains context compatibility guards and related tests. | R1 should inherit resolver service context compatibility. | R1 used source_concept_resolver_core_v2_graph; no R1-specific override found. | matched_by_shared_service | Missing context guards risks overmerge. | R1R should include same resolver version and overmerge checks. |
| alias component / context equivalence | Resolve alias/context equivalence conservatively. | SC1 summary reports alias/context conflict counters. | R1 should preserve alias/context equivalence behavior. | R1 private ledger reports alias/context conflict counters in result_summary. | matched_by_shared_service | Missing equivalence changes active/review split. | Keep counters in R1R report. |
| union/component resolution | Union graph components into SourceConcept outputs. | SC1 resolver summary reports concept/link/evidence counts. | R1 should persist deterministic component outputs to SourceConcept tables. | R1 private ledger and summary report concept/link counts plus SourceConcept table deltas. | matched | Missing union/component resolution would invalidate all concept counts. | No deterministic remediation needed beyond rerun with LLM enabled. |
| LLM pair adjudication planning | Plan bounded optional primary OpenAI-only pairs after deterministic blocking. | SC1 plan status=ready, selected_block_count=300. | For full-chain fidelity, R1 should enable the same bounded LLM planning stage. | R1 plan status=disabled, reason=llm_adjudication_not_requested. | missing_in_r1 | Full-chain candidate adjudication was not performed. | R1R replay with explicit LLM adjudication config and approval. |
| LLM pair selection | Select up to the configured bounded LLM pair/block budget. | SC1 selected 300 blocks and 300 judgments. | R1 should select comparable eligible pairs if rerunning the full chain. | R1 selected_block_count=0. | missing_in_r1 | Potential same-concept bridges remained unadjudicated. | R1R must report selected/skipped pair counts. |
| LLM provider availability | Use primary OpenAI-compatible provider only after deterministic blocking; no fallback. | SC1 provider_mode=primary_openai, access_configured=True. | R1 full-chain replay should prove provider availability or fail loudly before execute. | R1 provider_mode=None; stage disabled before provider init. | missing_in_r1 | Provider unavailability was not tested because R1 did not request LLM. | R1R needs explicit provider/cache readiness gate and budget approval. |
| LLM judgment count | Record bounded LLM judgments. | SC1 judgment_count=300; jsonl_lines=300. | R1 should record nonzero judgments for full-chain fidelity when eligible pairs exist. | R1 judgment_count=0. | missing_in_r1 | R1 cannot claim SC1 full-chain parity. | R1R must regenerate judgments or prove zero eligible pairs with planning evidence. |
| LLM cache use | Use local LLM cache without exposing raw provider secrets. | SC1 cache_dir=.local_manifests\phase-4.5-sc1-llm-adjudication-cache; no fallback provider. | R1 should use the approved cache path or explain why no LLM stage ran. | R1 llm_usage has no cache because plan.status=disabled. | missing_in_r1 | Repeatability and cost controls for LLM adjudication were absent. | R1R must define cache path and cache-hit/miss reporting. |
| LLM decisions applied or recorded | Apply or record LLM same/cannot/uncertain decisions as source-layer evidence only. | SC1 llm_same_concept_recorded=True. | R1 should record LLM decision effects if full chain is rerun. | R1 has no LLM judgments or LLM decision edges. | missing_in_r1 | Component topology may differ from full-chain expected output. | R1R must report LLM edge counts and decision outcomes. |
| persistence to SourceConcept tables | Persist resolver outputs only to SourceConcept-owned tables in execute mode. | SC1 final lifecycle pack is apply-db and reports SourceConcept output artifacts. | R1 execute should persist only allowed SourceConcept table changes. | R1 mutation proof and post-commit checks passed; truth_path_write_count=0. | matched | Missing persistence would make route counts stale. | R1R should use execute only after dry-run/readiness approval. |
| mutation proof | Prove no Entity truth/media_tags/provider/source/iCloud mutation. | SC1 reports forbidden truth table write count and no provider/image uploads. | R1 should prove no forbidden writes. | R1 summary mutation proof passed; no provider/import/classification/AI/localization/Entity. | matched | Missing proof would make the incident higher severity. | Retain and broaden mutation proof in R1R. |
| post-commit verification | Verify committed outputs and counts after execute. | SC1 final validation pack and readiness checks are reported. | R1 should run post-commit verification after execute. | R1 post_commit_verification_passed=True. | matched | Without verification, R1 output may be unverifiable. | R1R must rerun post-commit verification after full chain. |
| validation pack/reporting | Produce public report, summary, and local validation artifacts. | SC1 public report/summary and private validation pack exist. | R1 should report full pipeline stages actually executed. | R1 report exists and honestly lists LLM as non-goal, but A1 did not treat that as a fidelity incident. | present_but_incomplete_for_full_chain | Reviewer could mistake deterministic-only R1 for full SC1 pipeline replay. | R1R/A1 rerun reports must explicitly separate deterministic-only and full-chain statuses. |

## LLM Adjudication Fidelity

```json
{
  "conclusion": "llm_stage_missing_incident",
  "r1_evidence_source": ".local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage/resolver-run-ledger.json",
  "r1_judgment_count": 0,
  "r1_max_calls": 0,
  "r1_missing_or_unproven_reason": "R1 private resolver-run-ledger records llm_usage.used=false, judgment_count=0, plan.status=disabled, and reason=llm_adjudication_not_requested; R1 public commands omit LLM flags and the public report lists LLM as a non-goal.",
  "r1_policy": "bounded_optional_primary_openai_only_after_deterministic_blocking",
  "r1_provider_mode": null,
  "r1_used_llm_adjudication": false,
  "sc1_judgment_count": 300,
  "sc1_max_calls": 300,
  "sc1_policy": "bounded_optional_primary_openai_only_after_deterministic_blocking",
  "sc1_provider_mode": "primary_openai",
  "sc1_used_llm_adjudication": true
}
```

## Missing Artifacts / Uncertainty

- `.local_manifests/phase-4.5-sc1-source-concept-resolver-core-final-lifecycle-scope-v5/console-transcript.txt`: Exact final v5 SC1 shell transcript containing the full CLI command. (non_blocking)

The missing SC1 final-v5 shell transcript prevents quoting the exact historical terminal command. It does not block the incident conclusion because SC1 LLM execution is independently proven by the public summary, private resolver-run-summary, 300 judgment lines, runner config, and LLM edge output.

## Impact on R1

- Full-chain fidelity claim: invalid.
- Deterministic-only output: still useful and supported by adapter accounting, mutation proof, and post-commit verification.
- R1 status for route approval: incomplete because bounded LLM pair adjudication was not run.

## Impact on A1

- A1 remains useful as a read-only audit of the current post-R1 state.
- A1 route approval is incomplete because it did not treat deterministic-only R1 as a fidelity incident.
- A1 should be rerun after R1R full-chain replay.

## Impact on Proposed R2

SCV2-R2 remains blocked until R1R full-chain remediation and A1R rerun are both complete. R2 target buckets, needs_review priorities, and route readiness may change after LLM adjudication changes component topology or records same/cannot/uncertain decisions.

## Severity Classification

- Technical severity: `S1`
- Project governance severity: `P0/P1 pipeline fidelity incident`
- Label: phase output invalid or route decision incomplete
- Rationale: SC1 established bounded LLM pair adjudication as part of the full resolver chain, but R1 disabled that stage while feeding A1 route-decision evidence.

## Remediation Decision

Selected decision: 4. R1/A1 invalid; rerun R1 and A1 after fixing runner/config.

Required next phase: `Phase 4.5-SCV2-R1R: Full SourceConcept Pipeline Replay / Remediation`

Required follow-up after R1R: `Phase 4.5-SCV2-A1R: rerun A1 route audit after R1R outputs exist`

R1R should replay the full deterministic + bounded LLM adjudication chain under explicit approval. A1R must rerun after R1R. INC1 does not implement R1R or A1R.

## Required Next Phase, If Any

`Phase 4.5-SCV2-R1R: Full SourceConcept Pipeline Replay / Remediation`

Minimum R1R plan:

- Start from the latest approved base after INC1.
- Add explicit R1R runner/config path for deterministic resolver plus bounded LLM pair adjudication.
- Require dry-run/readiness proof before execute.
- Use primary OpenAI-compatible adjudication only if explicitly approved for R1R; no provider/source enrichment and no image uploads.
- Persist only SourceConcept-owned tables after approval; preserve no Entity truth/media_tags/source metadata mutation.
- Regenerate R1R public report/private artifacts, then rerun A1 route audit from R1R outputs.

## Validation

- Investigation runner mode: `read_only_file_artifact_investigation`
- DB accessed: `False`
- Provider or LLM called: `False`
- Summary schema passed: `True`
- Public redaction passed: `True`

## Safety / Non-goals

- No R2 started.
- No DB writes.
- No provider, Pixiv, or gallery-dl execution.
- No media import.
- No classification, AI tagging, localization, or LLM calls.
- No Entity truth, `media_tags`, confirmed assignment, source metadata, SourceConcept table, source root, iCloud, or storage mutation.
- No merge and no push to main.

## Evidence References

- `sc1_public_llm_section`: `docs/reports/phase-4.5-sc1-source-concept-resolver-core.md:47`
- `sc1_public_llm_used`: `docs/reports/phase-4.5-sc1-source-concept-resolver-core.md:49`
- `sc1_summary_llm`: `docs/reports/phase-4.5-sc1-source-concept-resolver-core-summary.json:66`
- `sc1_summary_llm_usage`: `docs/reports/phase-4.5-sc1-source-concept-resolver-core-summary.json:143`
- `sc1_runner_llm_flag`: `scripts/run_phase45_sc1_source_concept_resolver.py:920`
- `sc1_runner_llm_config`: `scripts/run_phase45_sc1_source_concept_resolver.py:959`
- `service_llm_config`: `backend/app/services/source_concept_resolver_service.py:236`
- `service_llm_plan`: `backend/app/services/source_concept_resolver_service.py:2199`
- `service_llm_run`: `backend/app/services/source_concept_resolver_service.py:2631`
- `service_llm_default_disabled`: `backend/app/services/source_concept_resolver_service.py:2217`
- `sc1_tests_llm`: `tests/test_phase45_sc1_source_concept_resolver.py:1719`
- `r1_public_non_goals`: `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md:17`
- `r1_public_commands`: `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md:131`
- `r1_public_safety`: `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md:135`
- `r1_summary_commands`: `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json:5004`
- `r1_summary_no_llm`: `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json:1862`
- `r1_private_ledger_llm`: `.local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage/resolver-run-ledger.json:942`
- `r1_private_ledger_reason`: `.local_manifests/phase-4.5-scv2-r1-post-px1-source-concept-triage/resolver-run-ledger.json:952`
- `r1_tests_provider_guard`: `tests/test_phase45_scv2_r1_post_px1_source_concept_triage.py:431`
- `a1_public_route`: `docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision.md:1`
- `a1_summary_route_status`: `docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision-summary.json:183`
- `handoff_r1`: `docs/current-handoff.md:25`
- `roadmap_scv2`: `docs/project-roadmap.md:32`

## Engineering Judgment

This is a real technical S1 and project-governance P0/P1 fidelity incident, not just a wording mismatch. R1 appears to have intentionally scoped out LLM in its prompt/report, but that means it was not a faithful full-chain replay of the SC1 resolver pipeline. The root cause is a phase-scope/config omission plus insufficient reporting/tests to distinguish deterministic-only execution from full-chain execution before A1 route approval. The correct next move is a separate R1R remediation phase, then A1R; R2 must remain blocked until both are complete.
