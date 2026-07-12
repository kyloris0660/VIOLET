# SCV2-ML1: Multilingual Alias and Source-Metadata Closure

## Status

- Contract status: `blocked_pixiv_incremental_acquisition_approval_required`.
- Evidence code SHA: `838e02d086c1e075205bd702a61adaec5fcd704a`.
- Initial execution: read-only, zero-network, accepted R2R evidence reused.

## Corrected search semantics

Search-result union is not identity union. `cannot_link` blocks identity materialization and unsupported alias propagation, but does not suppress direct supported same-name media. Additional terms intersect at media-result level.

## Canonical Pixiv accounting

- Candidate media / distinct works: `2285` / `2235`.
- Metadata-complete media / works: `527` / `519`.
- Terminal-unavailable media / works: `0` / `0`.
- Retryable / parse-or-identity / not-attempted / unexplained: `0` / `3` / `1755` / `0`.
- Incremental acquisition approval required: `True`; projected work requests: `1713`.

## Creator preservation

- Records with creator ID / name / account: `536` / `545` / `459`.
- Retained ID / name / account: `536` / `545` / `24`.
- Silently dropped creator fields / role misclassifications: `435` / `0`.
- Creator search cases / pass: `50` / `True`.
- Creator AND character/work cases / accuracy / leakage: `94` / `0.62766` / `0`.
- Creator AND failure causes: `{'source_work_observation_missing': 17, 'work_title_runtime_under_recall': 18}`.

## Real multilingual benchmark

- Families / observed aliases: `3939` / `11229`.
- Signal / candidate-connectivity / search-equivalence coverage: `0.036958` / `0.002793` / `0.773547`.
- Candidate-not-generated / unexplained split: `3928` / `0`.
- Candidate miss causes: `{'creator_identity_not_consumed': 303, 'source_registry_relationship_not_consumed': 3625}`.
- New pair manifest / LLM approval required: `0` / `False`.

## Runtime search

- Shared-name cases / union passed: `25` / `True`.
- AND cases / leakage: `8` / `0`.
- Unsupported / rejected results: `0` / `0`.
- Search-caused identity union: `0`.

## Safety boundary

No gallery-dl, Pixiv, provider, LLM, production, Entity, truth, media-import, AI-tagging, classification, or localization operation occurred. Raw names, IDs, URLs, filenames, and local paths remain only in ignored private artifacts.

## Validation

- Changed Python py_compile: `passed`.
- Focused pytest passed / failed: `630` / `0`.
- ML1 contract: `True`.
- Real browser validation: `not_required_no_ui_change`.
