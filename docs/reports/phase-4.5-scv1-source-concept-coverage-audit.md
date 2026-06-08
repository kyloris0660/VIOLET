# 4.5-SCV1 Expanded SourceConcept Validation and Coverage Audit

## Summary

SCV1 performed a read-only audit over the current development DB. It generated private aggregate/sample artifacts under `.local_manifests` and this public-safe report. No import, provider call, AI tagging, localization, LLM, migration, server, browser, Entity bridge, promotion, or truth-path write was run.

This report is another reviewer-fix rerun for PR #100. Pre-fix SCV1 values are superseded where DB resolution precedence, redaction/path safety, public report write ordering, tag alias-gap scoring, mutation-proof table coverage, alias gap counts, or hidden-status metrics were affected.

## Scope

- Current development DB only.
- Existing media, tags, AI tag provenance, source metadata/source-layer rows, F7a candidates, and SourceConcept tables.
- Public report uses aggregate counts and redacted labels only.

## Non-goals

- No 5k/10k/full-library run.
- No DB writes, migrations, imports, providers, LLMs, localization, AI jobs, SourceConcept editing, Entity bridge, promotion, confirmed assignments, or `media_tags` mutation.

## DB identity and read-only proof

- DB: `blombooru` on `localhost:5432`.
- Git: `codex/phase45-scv1-source-concept-coverage-audit` at `a2c6046e0da59b2480b22c5dbe0d919c3696c9c7`.
- Python: `python.exe`.
- PostgreSQL transaction_read_only: `on`.
- DB resolution mirrors app development precedence: `True`.
- `data/settings.json` database settings present: `True`; database file settings used: `True`.
- DB field sources: `{"host": "settings_json", "name": "settings_json", "password": "settings_json_present", "port": "settings_json", "user": "settings_json"}`.
- Runner/app-equivalent DB URLs match: `True`; runner URL: `postgresql://postgres:***@localhost:5432/blombooru`; app-equivalent URL: `postgresql://postgres:***@localhost:5432/blombooru`.
- Forbidden table count proof passed: `True`.
- Missing optional forbidden tables recorded: `1`.
- SourceConcept signals table included in mutation proof: `True`.

## Media coverage baseline

- Total media: `1989`.
- Eligible media policy: `content_class IN ('anime', 'unknown')`; eligible count `1936` (`97.34%`).
- Media with any tags: `1962`.
- Media with AI tag provenance: `1962`; without AI tags `27`.
- Media with source-layer signals: `1338`; without source-layer signals `651`.
- Media with SourceConcept evidence or links: `1266`.
- Content class distribution: `{"anime": 1882, "non_anime": 53, "unknown": 54}`.

## Source-layer coverage

- Source metadata records by provider: `{"danbooru": 22, "gelbooru": 21, "google_vision": 1, "no_tag_provider": 22, "pixiv": 97, "saucenao": 37}`.
- Source metadata records linked to media: `60`.
- F7a distinct media with candidates: `62`.
- Source assertions by status: `{"needs_review": 5, "rejected": 113, "searchable_active": 182}`.

## SourceConcept inventory

- Total SourceConcepts: `4214`.
- By status: `{"active": 355, "needs_review": 760, "superseded": 3099}`.
- By type hint: `{"artist": 290, "character": 2371, "person": 51, "source_title": 350, "unknown": 913, "work": 239}`.
- Aliases/evidence/search index totals: `5561` / `9317` / `5561`.
- Concepts with no media / no aliases / no evidence / no search index: `3563` / `0` / `0` / `0`.
- Same alias key across multiple concepts: `100` sampled groups.

## Search symmetry audit

- Concepts checked: `1115`; aliases checked: `1702`.
- Exact symmetric concepts: `1115`.
- Explainable no-media concepts: `416`.
- Asymmetric concepts: `0`; severe asymmetry: `0`.
- One-way links / fragmentation / overbroad: `0` / `1` / `60`.
- Hidden raw matches / actual visible hidden leakage: `1702` / `0`.
- Hidden raw matches mean a lookup encountered hidden rejected/ambiguous/superseded rows; actual leakage means hidden concepts entered the visible closure/media result and should remain zero.
- Parser/metacharacter aliases: `922`.

## Alias gap analysis

- Total gap signals: `1571`.
- Gap buckets: `{"cjk_alias_without_english_romaji_sibling": 367, "danbooru_parenthetical_without_cjk_sibling": 199, "high_frequency_source_tag_or_name_unlinked": 13, "identity_tag_present_no_source_concept_alias": 14, "needs_review_cluster_with_no_active_alias_path": 523, "same_display_name_split_across_contexts": 176, "same_normalized_alias_key_split_across_multiple_concepts": 176, "source_assertion_present_not_connected": 22, "source_name_present_no_source_concept_alias": 0, "source_tag_present_no_source_concept_alias": 81}`.
- Gap bucket details: `{"cjk_alias_without_english_romaji_sibling": {"counts_are_full": true, "missing_distinct_keys": 367, "sample_limit": 30, "sampled_missing_keys": 30, "sampling_affects": "examples_only", "total_distinct_keys": 1115}, "danbooru_parenthetical_without_cjk_sibling": {"counts_are_full": true, "missing_distinct_keys": 199, "sample_limit": 30, "sampled_missing_keys": 30, "sampling_affects": "examples_only", "total_distinct_keys": 1115}, "high_frequency_source_tag_or_name_unlinked": {"counts_are_full": true, "missing_distinct_keys": 13, "sample_limit": 25, "sampled_missing_keys": 13, "sampling_affects": "examples_only", "total_distinct_keys": 418}, "identity_tag_present_no_source_concept_alias": {"category_counts": {"character": 248, "general": 3637, "meta": 4}, "counts_are_full": true, "excluded_category_counts": {"general": 3637, "meta": 4}, "excluded_visual_or_meta_distinct_keys": 3641, "identity_category_counts": {"character": 248}, "identity_category_policy": "include character/copyright/artist and other identity/source-like categories; exclude general/meta/rating/visual descriptors; missing categories use conservative name-like heuristics", "identity_eligible_distinct_keys": 248, "missing_distinct_keys": 14, "sample_limit": 30, "sampled_missing_keys": 14, "sampling_affects": "examples_only", "total_distinct_keys": 3889}, "needs_review_cluster_with_no_active_alias_path": {"counts_are_full": true, "missing_distinct_keys": 523, "sample_limit": 30, "sampled_missing_keys": 30, "sampling_affects": "examples_only", "total_distinct_keys": 760}, "same_display_name_split_across_contexts": {"counts_are_full": true, "missing_distinct_keys": 176, "sample_limit": 30, "sampled_missing_keys": 30, "sampling_affects": "examples_only", "total_distinct_keys": 849}, "same_normalized_alias_key_split_across_multiple_concepts": {"counts_are_full": true, "missing_distinct_keys": 176, "sample_limit": 30, "sampled_missing_keys": 30, "sampling_affects": "examples_only", "total_distinct_keys": 851}, "source_assertion_present_not_connected": {"counts_are_full": true, "missing_distinct_keys": 22, "sample_limit": 30, "sampled_missing_keys": 22, "sampling_affects": "examples_only", "total_distinct_keys": 266}, "source_name_present_no_source_concept_alias": {"counts_are_full": true, "missing_distinct_keys": 0, "sample_limit": 30, "sampled_missing_keys": 0, "sampling_affects": "examples_only", "total_distinct_keys": 370}, "source_tag_present_no_source_concept_alias": {"counts_are_full": true, "missing_distinct_keys": 81, "sample_limit": 30, "sampled_missing_keys": 30, "sampling_affects": "examples_only", "total_distinct_keys": 418}}`.
- Sample policy: `{"counts_are_full": true, "default_sample_limit": 30, "high_frequency_sample_limit": 25, "samples_are_limited_to_examples_only": true}`.
- Alias/source gap counts are full grouped-key counts; sample limits affect examples only, not totals or the decision matrix.
- SourceConcept is an identity/source-layer concept system. Normal visual/general/meta tags are intentionally excluded from SourceConcept alias-gap scoring; tag localization and visual tag search remain separate systems.
- Normal tag policy: total tags `3889`, identity-eligible `248`, excluded visual/general/meta `3641`, missing identity aliases `14`.
- Identity tag bucket detail: `{"category_counts": {"character": 248, "general": 3637, "meta": 4}, "counts_are_full": true, "excluded_category_counts": {"general": 3637, "meta": 4}, "excluded_visual_or_meta_distinct_keys": 3641, "identity_category_counts": {"character": 248}, "identity_category_policy": "include character/copyright/artist and other identity/source-like categories; exclude general/meta/rating/visual descriptors; missing categories use conservative name-like heuristics", "identity_eligible_distinct_keys": 248, "missing_distinct_keys": 14, "sample_limit": 30, "sampled_missing_keys": 14, "sampling_affects": "examples_only", "total_distinct_keys": 3889}`.
- Full-count correction supersedes the pre-fix limited total gap signal value `2025` with `1571`.
- Visual-tag exclusion supersedes the pre-fix all-tag total gap signal value `5192` with `1571`.
- Route impact: the corrected identity/source-relevant count still supports `source_concept_alias_resolver_improvement` if alias/source gaps and needs_review remain the dominant current-stage risks.
- Recommended fix category: `source_concept_alias_resolver_improvement`.

## Needs-review cluster analysis

- Total needs_review concepts: `760`.
- With media / high evidence / sharing active alias: `567` / `84` / `237`.
- CJK alias / parenthetical context / empty cluster: `331` / `167` / `0`.
- Assessment: needs_review retains recall value but should be triaged/scored before broader truth or management work.

## Redaction/privacy audit

- Public redaction passed: `True`.
- Public artifacts checked: `["docs/reports/phase-4.5-scv1-source-concept-coverage-audit.md", "docs/reports/phase-4.5-scv1-source-concept-coverage-audit-summary.json"]`.
- Final scan after public fields finalized: `True`.
- Checked at: `2026-06-08T11:39:21Z`.
- Findings: `[]`.
- Private artifact bundle created: `True`; exact private paths public: `False`.
- Private artifact count: `17` under `.local_manifests/phase-4.5-scv1-source-concept-coverage-audit`.
- Public redaction covers Windows, UNC, file URL, POSIX/NAS/macOS volume, app-managed storage-like roots, canonicalized private path tokens, filenames, and secret/token patterns.
- Public Markdown/JSON are rendered to ignored temp files, scanned first, and only then atomically replace tracked report paths; failed scans leave old tracked public files unchanged.
- Public samples are privacy-redacted; false redaction is acceptable for public reports.

## Nahida / 纳西妲 / 草神 seed result

- Seed values tested: `["Nahida", "纳西妲", "草神", "nahida_(genshin_impact)", "绾宠タ濡瞏", "鑽夬"]`.
- Matched aliases: `["Nahida", "纳西妲", "草神", "nahida_(genshin_impact)"]`.
- Matched concept count: `10`; matched media count: `33`.
- Gap detected: `False`.

## Decision matrix

- `source_concept_alias_resolver_improvement`: priority `P1`, recommended `True`; reasons: search asymmetry concepts=0, severe=0; alias/cross-language/source linkage gap signals=1571; needs_review concepts=760
- `bounded_ai_tag_expansion`: priority `P3`, recommended `False`; reasons: media without AI tag provenance=27/1989; would be a separate approved run
- `bounded_pixiv_metadata_expansion`: priority `P2`, recommended `True`; reasons: source metadata records linked to media=60/1989; coverage gap may limit SourceConcept evidence
- `tag_localization_catchup`: priority `P3`, recommended `False`; reasons: tag translations=3732, total tags=3889; separate from SourceConcept identity linking
- `source_concept_management_or_editing_design`: priority `P2`, recommended `False`; reasons: manual correction may help after alias/resolver quality is acceptable
- `entity_bridge_preview_design`: priority `P3`, recommended `False`; reasons: requires strong coverage, redaction, search symmetry, and low needs_review noise
- `run_ledger_or_phase39_prerequisite`: priority `P1`, recommended `True`; reasons: any 5k/10k or provider/AI/source expansion needs checkpoint/failure-budget discipline

## Recommended next phase

`source_concept_alias_resolver_improvement` is the highest impact/risk-adjusted next route from this audit.

## Expansion and bridge answers

- Is 5k/10k expansion justified now? `False`.
- If yes, expansion of what exactly? `N/A; broad 5k/10k expansion is not justified inside or immediately after SCV1 without a separate ledger and bounded phase.`.
- Must add before any 5k/10k run: `["run ledger/checkpoint/failure budget", "read-only identity and mutation proof per run", "redaction-safe reporting boundary", "separate approval for AI/provider/import/localization execution"]`.
- Is Entity bridge justified now? `False`.
- Is SourceConcept editing justified now? `False`.
- Should Pixiv/source metadata extraction be next? `True`.
- Should local AI tagging be next? `False`.
- Should tag localization be next? `False`.

## Deferred work

- Any AI tagging, provider/Pixiv/source metadata expansion, localization, SourceConcept editing, Entity bridge, promotion, or broad-library work remains a separate approved phase.
- Entity bridge still requires preview, manual confirmation, audit trail, rollback/supersede behavior, and no truth-path pollution guards.

## Validation

- Operational audit command: `python scripts/run_phase45_scv1_source_concept_coverage_audit.py --output-dir .local_manifests/phase-4.5-scv1-source-concept-coverage-audit --write-public-report --read-only`.
- Operational audit result: `passed`.
- Focused test results are recorded in the PR/final delivery report.
- Real browser validation: N/A, no UI/runtime behavior changed.

## Safety confirmation

- No push to main.
- No merge.
- No DB write, migration, import, cleanup/delete/reset/drop/truncate, source storage mutation, cloud-file mutation, app-storage mutation, AI tagging, localization, LLM, provider call, Entity Resolver, similarity, SourceConcept editing, Entity bridge, promotion, confirmed assignment, or media_tags mutation.

## Artifact lifecycle

- `scripts/run_phase45_scv1_source_concept_coverage_audit.py`: phase-scoped operational runner.
- `tests/test_phase45_scv1_source_concept_coverage_audit.py`: phase-scoped validation test.
- Private `.local_manifests` outputs: one-off local artifacts / ignored output.
- Public report and summary JSON: public report / handoff / roadmap update.

## Engineering judgment / operator notes

SCV1 achieved the intended audit shape if the read-only proof and redaction scan pass. The prompt scope is appropriate: broad enough to answer the next-route question, but correctly narrow because it forbids writes, providers, LLMs, localization, imports, SourceConcept editing, and Entity bridge work. The next phase should address the highest-priority audited gap rather than starting broad 5k/10k execution.
