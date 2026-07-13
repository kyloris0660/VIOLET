# SCV2-ML1: Multilingual Alias and Source-Metadata Closure

## Status

- Contract status: `partial_ml1_pixiv_metadata_foundation_complete`.
- Claims: `target_met=false`; `safe_to_merge=true`; `route_approved=true`.
- Active blockers: `[]`.
- Approved route scope / next phase: `SCV2-ML2_next_phase_only` / `SCV2-ML2: Multilingual Identity Candidate Closure`.
- Evidence code SHA: `88a157688929d13d85eba09789b462de0f882ceb`.
- Provider execution requests: `1817`; accepted R2R evidence remained immutable.
- Closeout external-call delta: `0`.

## Corrected search semantics

Search-result union is not identity union. `cannot_link` blocks identity materialization and unsupported alias propagation, but does not suppress direct supported same-name media. Additional terms intersect at media-result level.

## Canonical Pixiv accounting

- Candidate media / distinct works: `2285` / `2235`.
- Metadata-complete media / works: `2196` / `2155`.
- Terminal-unavailable media / works: `66` / `66`.
- Deferred nonblocking source-page-mismatch media / works: `23` / `14`.
- Exhaustive work equation: `2235 = 2155 complete + 66 terminal + 14 deferred`; equality holds: `True`.
- Retryable / parse-or-identity / no-durable-result / unexplained media: `0` / `0` / `0` / `0`.
- Conflict media / field-token memberships / distinct works / unresolved works: `0` / `0` / `0` / `0`.
- Origin breakdown: `{'filename_origin': {'candidate_media_count': 2285, 'distinct_work_count': 2235}, 'stored_path_origin': {'candidate_media_count': 2285, 'distinct_work_count': 2235}, 'thumbnail_origin': {'candidate_media_count': 2285, 'distinct_work_count': 2235}, 'source_field_origin': {'candidate_media_count': 0, 'distinct_work_count': 0}}`; agreement: `{'filename_path_agreement': 2285, 'multi_field_agreement': 2285}`.
- Incremental acquisition required: `False`; corrected exact work requests: `0`.
- Pixiv acquisition authorized / credential rotation confirmed / local-risk waiver: `False` / `False` / `True`.
- Continuous import gate implemented / current stock closed: `True` / `True`.

## Optional owner sample evidence

- Sample generated / size / conflicts exported: `True` / `0` / `0`.
- Owner-review manifest fingerprint: `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`.
- Owner validation confirmed / normal-pipeline human dependency: `False` / `False`.
- Ignored private artifacts are under `.local_manifests/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure/owner-review/`; no raw work IDs, URLs, or basenames are published here.
- `missing` means no durable complete/terminal/result evidence; it does not mean remotely deleted.

## Creator preservation

- Records with creator ID / name / account: `2205` / `2194` / `2108`.
- Retained ID / name / account: `2205` / `2194` / `2108`.
- Creator profile available / retained: `2205` / `2205`.
- Creator name/account search support: `1.0` / `1.0`.
- Silently dropped creator fields / role misclassifications: `0` / `0`.
- Creator search cases / pass: `50` / `True`.
- Creator AND character/work cases / accuracy / leakage: `94` / `0.62766` / `0`.
- Creator AND failure causes: `{'source_work_observation_missing': 17, 'work_title_runtime_under_recall': 18}`.

## Real multilingual benchmark

- Families / observed aliases: `4248` / `11860`.
- Identity-eligible / search-only families: `606` / `3642`.
- Signal / candidate-connectivity / search-equivalence coverage: `0.275124` / `0.019802` / `0.897363`.
- Real AND-work evaluable families / equivalence coverage: `3677` / `0.915692`.
- Unsupported runtime result occurrences: `0`.
- Candidate-not-generated / unexplained split: `30` / `0`.
- Candidate miss causes: `{'identity_alias_missing_sourceconcept_signal': 30}`.
- New pair manifest / LLM approval required: `0` / `False`.

## Runtime search

- Shared-name cases / union passed: `25` / `True`.
- AND cases / leakage: `9` / `0`.
- Runtime / supported results and coverage: `465` / `465` / `1.0`.
- Unsupported / rejected / superseded results: `0` / `0` / `0`.
- Search-caused identity union: `0`.

## Safety boundary

Pixiv/gallery-dl execution was metadata-only in the isolated ML1 database. No media download, LLM, production, Entity, truth, media-import, AI-tagging, classification, or localization operation occurred. Raw names, IDs, URLs, filenames, and local paths remain only in ignored private artifacts.
Acquisition manifest / requests / gallery-dl calls: `1713` / `1817` / `1817`.
Production evidence manifest generated / derived graph recomputation required: `True` / `True`.
Default bounded LLM policy / aggregate cap: `bounded_phase_primary_llm_usd10_v1` / `$10.0`.

## Validation

- Changed Python py_compile: `passed`.
- Focused pytest passed / failed: `172` / `0`.
- ML1 contract: `True`.
- Real browser validation: `not_run`.
