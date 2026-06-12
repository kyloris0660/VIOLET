# 4.5-SCV2-A1 Post-expansion Audit, Route Decision, and Durable ChatGPT Review Pack Policy

## Summary

- Status: `provisional_pending_chatgpt_pack_audit`.
- Branch/head: `codex/phase45-scv2-a1-post-expansion-audit-route-decision` / `e48b6d446f59916cd68bfa53557bd94d8c87c68c`.
- Recommendation: `SCV2-R2 targeted resolver/gap reduction`.
- Review pack required before final route approval: `True`.

## Scope and non-goals

- Scope: read-only post-R1 SourceConcept/source metadata/search audit plus durable ChatGPT review pack policy.
- Non-goals: no DB write, provider call, import, classification, AI tagging, localization, LLM, resolver execute, Entity bridge, truth-path write, media_tags mutation, source storage mutation, iCloud mutation, DEDUP execution, or browser validation.

## Durable ChatGPT review pack policy update

- Added `docs/chatgpt-review-pack-policy.md`.
- A1 recommendations remain `provisional_pending_chatgpt_pack_audit` until the user uploads the review pack to ChatGPT and receives independent audit.

## Current DB/source baseline

- Total / eligible media: `3750` / `3687`.
- Eligible AI tag coverage: `3687` / `3687` = `100.0`%.
- Source metadata rows / distinct eligible media: `671` / `531`.
- Source metadata coverage percent: `14.4`%.

## R1 transition interpretation

- R1 trusted transition: SourceConcept total `4214 -> 6094`, active `355 -> 1078`, needs_review `760 -> 1809`, PX1-influenced concepts `1692`.
- The latest current-head execute rerun was idempotent over the already committed R1 state. It must not be interpreted as R1 having no effect.

## SourceConcept current state

- Total SourceConcept: `6094`.
- By status: `{"active": 1078, "needs_review": 1809, "superseded": 3207}`.
- PX1-influenced concepts: `3510`.
- Duplicate/fragment candidate groups: `1088`.

## Gap audit

- Total gap signals: `4622`.
- Gap buckets: `{"cjk_alias_without_english_romaji_sibling": 1647, "danbooru_parenthetical_without_cjk_sibling": 254, "high_frequency_source_tag_or_name_unlinked": 13, "identity_tag_present_no_source_concept_alias": 7, "needs_review_cluster_with_no_active_alias_path": 640, "same_display_name_split_across_contexts": 544, "same_normalized_alias_key_split_across_multiple_concepts": 544, "source_assertion_present_not_connected": 24, "source_name_present_no_source_concept_alias": 2, "source_tag_present_no_source_concept_alias": 947}`.
- Increased total gap signals are interpreted against changed denominators and newly exposed PX1/R1 evidence, not as a simple regression.

## Search seed symmetry audit

- Groups / seeds / matched / unmatched: `10` / `59` / `43` / `16`.
- Symmetric / asymmetric groups: `0` / `10`.
- Asymmetry reason buckets: `{"active_only_vs_needs_review_contrast": 5, "concept_split": 33, "hidden_or_superseded_raw_match": 35, "missing_alias_or_unmatched_seed": 10, "needs_review_not_included_in_active_search": 8, "unmatched_alias": 16}`.
- Unmatched aliases are counted as asymmetry or explicit unmatched failures.

## needs_review triage audit

- Total needs_review concepts: `1809`.
- needs_review with media / high evidence / sharing active alias: `1552` / `122` / `1169`.
- Assessment: `needs_review retains recall value but should be triaged/scored before broader truth or management work`.

## PX1 evidence impact

- Current PX1-influenced concepts: `3510`.
- PX1 remains review-scoped evidence/backlog input, not active Entity or media_tags truth.

## Comparison with SCV1/P0/E1/PX1/R1

- SCV1 total gap signals -> current: `1571` -> `4622`.
- Not directly comparable buckets: `["cjk_alias_without_english_romaji_sibling", "danbooru_parenthetical_without_cjk_sibling", "identity_tag_present_no_source_concept_alias", "needs_review_cluster_with_no_active_alias_path", "same_display_name_split_across_contexts", "same_normalized_alias_key_split_across_multiple_concepts", "source_assertion_present_not_connected", "source_name_present_no_source_concept_alias", "source_tag_present_no_source_concept_alias"]`.

## Route decision matrix

- `SCV2-R2 targeted resolver/gap reduction`: priority `P1`, recommended `True`; writes DB `True`; truth path `False`; why: Current audit shows gap signals=4622, asymmetric search groups=10, needs_review=1809.
- `PX1-B additional Pixiv metadata extraction`: priority `P2`, recommended `False`; writes DB `True`; truth path `False`; why: Metadata coverage is 14.4% distinct eligible media, but resolver/search gaps are still dominant=True.
- `Provider-2-P0 taxonomy/alias enrichment metadata-only`: priority `P2`, recommended `False`; writes DB `False`; truth path `False`; why: Source tag gap=947 and alias split gap=544 suggest resolver/taxonomy questions, but Provider-2 needs a separate P0 policy after A1/R2.
- `SCV2-E2 controlled scale-up import to about 6000-6500 media`: priority `P3`, recommended `False`; writes DB `True`; truth path `False`; why: Scale-up would multiply current retrieval noise before resolver/search stability is proven.
- `SourceConcept management/editing UI/design`: priority `P2`, recommended `False`; writes DB `True`; truth path `False`; why: Manual correction may help later, but current dominant issue is automated resolver/gap reduction rather than UI processing.
- `Entity bridge preview`: priority `P3`, recommended `False`; writes DB `True`; truth path `True`; why: Entity bridge remains blocked by search asymmetry, gap signals, and high needs_review volume.
- `DEDUP1 exact duplicate cleanup execution`: priority `P3`, recommended `False`; writes DB `True`; truth path `False`; why: PX1 exact duplicate dry-run groups remained zero.
- `Full-library / 10k expansion`: priority `P3`, recommended `False`; writes DB `True`; truth path `False`; why: Full-library expansion is blocked by current quality gates and ledger prerequisites.

## Entity bridge blocker analysis

- Blocked: `True`.
- Reason: search asymmetry, gap signals, needs_review volume, and missing truth-path preview/manual-confirmation guards remain unresolved.

## PX1-B decision

- `deferred_pending_r2_or_pack_audit`: Metadata coverage is 14.4% distinct eligible media, but resolver/search gaps are still dominant=True.

## Provider-2 decision

- `deferred_pending_resolver_gap_reduction`: Source tag gap=947 and alias split gap=544 suggest resolver/taxonomy questions, but Provider-2 needs a separate P0 policy after A1/R2.

## Scale-up decision

- `blocked_quality_and_ledger_thresholds`: Scale-up would multiply current retrieval noise before resolver/search stability is proven.

## DEDUP1 decision

- `not_useful_zero_exact_duplicate_groups`: PX1 exact duplicate dry-run groups remained zero.

## Recommended next phase

`SCV2-R2 targeted resolver/gap reduction` is the runner recommendation and remains `provisional_pending_chatgpt_pack_audit` pending ChatGPT pack audit.

## ChatGPT independent review pack

- Generated: `True`.
- Not committed: `True`.
- Zip path label: `.local_manifests/phase-4.5-scv2-a1-post-expansion-audit-route-decision/chatgpt-review-pack.zip`.
- Exact private paths are not exposed in this public report.
- The user should upload the local `chatgpt-review-pack.zip` to ChatGPT before final route approval.
- Final route decision should be made only after reviewing both this PR/report and the review pack.

## Validation

- Operational command: `python scripts/run_phase45_scv2_a1_post_expansion_audit_route_decision.py --output-dir .local_manifests/phase-4.5-scv2-a1-post-expansion-audit-route-decision --write-public-report --read-only --write-chatgpt-review-pack`.
- Operational result: `passed`.
- Browser validation: `not_run_no_ui_runtime_change`.

## Mutation proof / read-only proof

- PostgreSQL transaction_read_only: `on`.
- Mutation proof passed: `True`.
- Changed forbidden tables: `[]`.

## Public/private artifact boundary

- Public report/summary contain aggregate counts and redacted labels only.
- Private `.local_manifests` artifacts are ignored and not committed.
- Public redaction passed: `True`.

## Engineering judgment / operator notes

- Artifact lifecycle: A1 runner and focused tests are phase-scoped; policy doc is durable project policy; public report/summary are public report/handoff artifacts; `.local_manifests` outputs and review pack are one-off ignored local artifacts.
- Phase boundary is appropriate: A1 answers the route question without executing another resolver/provider/import/truth phase.
- Remaining risks: the route recommendation is provisional until independent ChatGPT review pack audit completes.
- Recommended next step: review the PR and upload the generated review pack to ChatGPT before approving a final route.
