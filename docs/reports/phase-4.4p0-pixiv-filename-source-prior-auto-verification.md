# Phase 4.4-P0 - Pixiv Filename Source-Prior Auto-Verification Design

## Why P0 Exists

PR #84 showed that Pixiv-like filename tokens are a non-trivial local source prior. P0 corrects the route from manual per-image validation toward an automated pre-persistence correspondence gate.

The user-facing strategy is: human review may validate the stage outcome and threshold direction, but future filename-ID pairs must not rely on long-term per-item manual review.

## Source-Prior Concept

- Recommended concept name: `LocalSourceHint` (`SourcePrior` / `FilenameSourcePrior` remain acceptable aliases).
- `pixiv_filename` is local deterministic source-prior data, not a provider result, not `ProviderCache`, not confirmed `EntityEvidence`, not a confirmed assignment, and not an automatic `Entity`.
- Lifecycle: `extracted -> reference_lookup_attempted -> auto_verified_high_confidence / rejected / uncertain -> eligible_for_persistence`.
- P0 sets `db_write_allowed=false`; future P1 should persist only `auto_verified_high_confidence` hints after a separate DB-write approval.

## Parser Policy

- Stable token regex: `(?<!\d)(?P<pixiv_work_id>[1-9]\d{5,11})_p(?P<page_index>\d+)(?!\d)`.
- Work ID policy: positive integer, 6-12 digits, no leading zero.
- `_p` must be literal lowercase; uppercase variants are detected as possible variants and not silently accepted.
- Prefixes and suffixes around the token are allowed; no character may appear between numeric ID and `_pN`.

## Aggregate Extraction Metrics

- Total media inspected: `1989`.
- Total candidate filename source-prior occurrences: `1665`.
- Media with one or more Pixiv-like tokens: `555` (`27.9`%).
- Distinct candidate Pixiv work IDs: `551`.
- Duplicate work ID count: `3`.
- Page index distribution: `{"0": 498, "1": 40, "2": 11, "3": 2, "4": 1, "5": 3}`.
- Content class distribution: `{"anime": 546, "non_anime": 1, "unknown": 8}`.
- Field kind distribution: `{"app_managed_basename": 555, "stored_filename": 555, "stored_path_basename": 555}`.
- Invalid / variant token count: `0`.
- Multiple-token-in-one-media count: `0`.
- Approved five-sample Pixiv-prior count: `0`.

## Metadata Retention Assessment

- Assessment: `filename_and_app_managed_basenames_available_but_no_dedicated_original_basename_or_source_prior_ledger`.
- Current DB/app-managed metadata retains enough filename/basename signal to recover many Pixiv-style priors.
- There is still no dedicated `original_basename` or source-prior ledger, so missing tokens remain a metadata retention gap rather than proof the Pixiv route is weak.

## Safe Reference Lookup Policy

- Result: `reference_lookup_policy_blocked`.
- Request count: `0`.
- Blocker: No official, documented, unauthenticated Pixiv metadata or preview endpoint was accepted for P0. Pixiv artwork HTML pages, cookies/login, browser automation, scraping, hotlink bypasses, and unofficial authenticated APIs remain forbidden.
- Researched public Pixiv artwork pages, embed/oEmbed-style possibilities, and unofficial/authenticated API paths.
- No live Pixiv lookup, browser automation, cookies, login session, scraping, hotlink bypass, unofficial authenticated API, or reference-image download was performed.
- Policy references used for route assessment: [Pixiv Help Center](https://www.pixiv.help/hc/en-us), [oEmbed specification](https://oembed.com/).

## Automated Verification Gate

- Intended input: local app-managed thumbnail or derived/resized image plus safe low-resolution reference preview, if a future documented route is approved.
- Implemented local helper signals: orientation normalization, aspect ratio delta, average hash distance, difference hash distance, and average color distance.
- Proposed high-confidence policy requires multiple agreeing signals; thresholds are design-only until safe reference samples exist.
- Proposed statuses: `auto_verified_high_confidence`, `auto_rejected_mismatch`, `uncertain_needs_manual_or_lookup`, `reference_unavailable`, `policy_blocked`, `unsupported_media_type`.

## Feasibility Sample

- Selected local sample count: `30`.
- Selection strategy: `cover_simple_suffix_duplicate_marker_prefix_non_p0_duplicate_work_id_then_fill_anime_first`.
- Sample category counts: `{"content_class_anime": 30, "duplicate_work_id_case": 2, "non_p0_page": 2, "p0_page": 28, "prefixed_token": 1, "simple_exact_token_basename": 1, "suffix_timestamp_case": 2, "token_at_basename_start": 29}`.
- Exact sample details are stored only in ignored local artifacts.

## Verification Result

- Status: `not_run_reference_lookup_policy_blocked`.
- Live reference lookup sample size: `0`.
- Reference images available: `0`.
- Auto-verified high-confidence count: `0`.
- Result distribution: `{"policy_blocked": 30}`.
- Because no safe reference route was accepted, P0 did not test live correspondence against Pixiv reference images. The automated gate is designed and locally unit-tested, but production thresholds remain future work.

## Future DB Persistence Recommendation

- Recommended next phase: `Phase 4.4-P1 - Pixiv Source-Prior Persistence for Auto-Verified High-Confidence Items`.
- P1 should persist `LocalSourceHint` / `SourcePrior` rows only for `auto_verified_high_confidence` items.
- P1 must not create confirmed assignments, automatic `Entity` rows, Pixiv metadata lookups, or public exact ID exposure unless separately approved.
- If a schema gap remains, use an additive schema or JSON payload design with rollback/idempotency; P0 does not implement a migration.

## Privacy Policy

- Public report includes aggregate metrics, design decisions, policy status, and safety confirmation only.
- Public report excludes exact local filenames, exact local paths, source/iCloud paths, exact media-to-Pixiv mappings, raw Pixiv ID lists, image bytes, credentials, and raw private artifact details.
- Exact mappings and verification rows are kept in ignored `.local_manifests` artifacts.

## Safety Confirmation

- db_write: `False`
- db_migration: `False`
- provider_cache_write: `False`
- entity_evidence_write: `False`
- media_entity_candidate_write: `False`
- confirmed_assignment: `False`
- automatic_entity_creation: `False`
- media_tags_mutation: `False`
- tag_translation_mutation: `False`
- localization_execution: `False`
- entity_resolver: `False`
- broad_similarity_or_clustering: `False`
- pixiv_scraping: `False`
- browser_automation: `False`
- cookies_or_login: `False`
- source_or_icloud_mutation: `False`
- app_managed_storage_mutation: `False`
- public_exact_pixiv_mapping: `False`

## Local Artifacts

- source_prior_details_json: `.local_manifests/phase-4.4p0-pixiv-source-prior-details.json`
- auto_verify_details_json: `.local_manifests/phase-4.4p0-pixiv-auto-verify-details.json`
- auto_verify_sheet_md: `.local_manifests/phase-4.4p0-pixiv-auto-verify-sheet.md`
- auto_verify_sheet_csv: `.local_manifests/phase-4.4p0-pixiv-auto-verify-sheet.csv`
- artifacts_are_gitignored: `True`
- public_report_contains_exact_mappings: `False`
