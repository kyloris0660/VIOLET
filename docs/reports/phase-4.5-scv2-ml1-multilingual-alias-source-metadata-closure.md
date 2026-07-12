# SCV2-ML1: Multilingual Alias and Source-Metadata Closure

## Status

- Contract status: `blocked_credential_rotation_confirmation_required`.
- Active blockers: `['blocked_credential_rotation_confirmation_required', 'blocked_candidate_generation_gap']`.
- Evidence code SHA: `957f2945986209739a568efbb3fc75b8721654e4`.
- Initial execution: read-only, zero-network, accepted R2R evidence reused.

## Corrected search semantics

Search-result union is not identity union. `cannot_link` blocks identity materialization and unsupported alias propagation, but does not suppress direct supported same-name media. Additional terms intersect at media-result level.

## Canonical Pixiv accounting

- Candidate media / distinct works: `2285` / `2235`.
- Metadata-complete media / works: `527` / `519`.
- Terminal-unavailable media / works: `0` / `0`.
- Retryable / parse-or-identity / no-durable-result / unexplained media: `0` / `3` / `0` / `0`.
- Conflict media / field-token memberships / distinct works / unresolved works: `3` / `9` / `3` / `3`.
- Origin breakdown: `{'filename_origin': {'candidate_media_count': 2285, 'distinct_work_count': 2235}, 'stored_path_origin': {'candidate_media_count': 2285, 'distinct_work_count': 2235}, 'thumbnail_origin': {'candidate_media_count': 2285, 'distinct_work_count': 2235}, 'source_field_origin': {'candidate_media_count': 0, 'distinct_work_count': 0}}`; agreement: `{'filename_path_agreement': 2285, 'multi_field_agreement': 2285}`.
- Incremental acquisition required: `True`; corrected exact work requests: `1713`.
- Pixiv acquisition authorized / credential rotation confirmed: `True` / `False`.
- Continuous import gate implemented / current stock closed: `True` / `False`.

## Owner sample gate

- Sample generated / size / conflicts exported: `True` / `60` / `3`.
- Sample fingerprint: `104ea1c9c0fea7e32221cd4d231bb1c6f8c76a616f1d0d8b8043a03384ea3881`.
- Owner validation confirmed / normal-pipeline human dependency: `False` / `False`.
- Ignored private artifacts are under `.local_manifests/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure/owner-review/`; no raw work IDs, URLs, or basenames are published here.
- `missing` means no durable complete/terminal/result evidence; it does not mean remotely deleted.

## Creator preservation

- Records with creator ID / name / account: `536` / `545` / `459`.
- Retained ID / name / account: `536` / `545` / `459`.
- Creator profile available / retained: `536` / `536`.
- Creator name/account search support: `1.0` / `1.0`.
- Silently dropped creator fields / role misclassifications: `0` / `0`.
- Creator search cases / pass: `50` / `True`.
- Creator AND character/work cases / accuracy / leakage: `94` / `0.62766` / `0`.
- Creator AND failure causes: `{'source_work_observation_missing': 17, 'work_title_runtime_under_recall': 18}`.

## Real multilingual benchmark

- Families / observed aliases: `3939` / `11229`.
- Identity-eligible / search-only families: `314` / `3625`.
- Signal / candidate-connectivity / search-equivalence coverage: `0.515924` / `0.035032` / `0.894136`.
- Real AND-work evaluable families / equivalence coverage: `2629` / `0.900342`.
- Unsupported runtime result occurrences: `0`.
- Candidate-not-generated / unexplained split: `16` / `0`.
- Candidate miss causes: `{'identity_alias_missing_sourceconcept_signal': 16}`.
- New pair manifest / LLM approval required: `0` / `False`.

## Runtime search

- Shared-name cases / union passed: `25` / `True`.
- AND cases / leakage: `8` / `0`.
- Runtime / supported results and coverage: `209` / `209` / `1.0`.
- Unsupported / rejected / superseded results: `0` / `0` / `0`.
- Search-caused identity union: `0`.

## Safety boundary

No gallery-dl, Pixiv, provider, LLM, production, Entity, truth, media-import, AI-tagging, classification, or localization operation occurred. Raw names, IDs, URLs, filenames, and local paths remain only in ignored private artifacts.
Acquisition manifest / requests / gallery-dl calls: `1713` / `0` / `0`.
Production evidence manifest generated / derived graph recomputation required: `True` / `True`.
Default bounded LLM policy / aggregate cap: `bounded_phase_primary_llm_usd10_v1` / `$10.0`.

## Validation

- Changed Python py_compile: `true`.
- Focused pytest passed / failed: `126` / `0`.
- ML1 contract: `True`.
- Real browser validation: `passed`.
