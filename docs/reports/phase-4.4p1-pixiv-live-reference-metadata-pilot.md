# Phase 4.4-P1 - Pixiv Live Reference / Metadata / Correspondence Pilot

## Why P1 Exists

P0 showed significant filename prior coverage and designed the gate; P1 makes a bounded real public-page probe attempt without persistence.

P0's `reference_lookup_policy_blocked` result was a policy stop, not evidence that the Pixiv filename-prior route is technically invalid.

## Sample Selection

- Selected sample size: `5`.
- Strategy: `cover_non_p0_suffix_prefix_duplicate_work_id_and_p0_then_fill_anime_first`.
- Category counts: `{"content_class_anime": 5, "duplicate_work_id_case": 2, "non_p0_page": 1, "p0_page": 4, "prefixed_token": 1, "suffix_timestamp_case": 2, "token_at_basename_start": 4}`.
- Page case distribution: `{"non_p0": 1, "p0": 4}`.
- Exact sample details are local-only.

## Public Pixiv Page Probe Policy

- Concurrency: `1`.
- Timeout seconds: `10.0`.
- Delay seconds: `2.0`.
- Cookies/login/browser automation/referer bypass: `False`.
- Stop condition: 403, 429, login/captcha/consent/anti-bot marker.

## Pixiv Public-Page Probe Result

- Requests attempted: `5`.
- HTTP status distribution: `{"200": 5}`.
- Final URL host distribution: `{"www.pixiv.net": 5}`.
- Blocked count: `0`.
- Stopped early: `False`.

## Metadata Availability

- Metadata richness distribution: `{"preview_only": 5}`.
- Field availability counts: `{"canonical_url": 5, "description": 5, "preview_image_candidates": 5, "title": 5}`.

## Preview / Reference Availability

- Preview status distribution: `{"reference_preview_fetched": 5}`.

## Correspondence Verification

- Result distribution: `{"auto_rejected_mismatch": 4, "auto_verified_high_confidence": 1}`.
- Threshold policy: `phase44p1-pilot-v1-not-production`.

## Optional No-Upload Booru Lookup

- Status: `no_upload_booru_lookup_policy_blocked`.
- Requests attempted: `0`.
- Reason: P1 inspected the option but did not enable a no-upload booru source-URL lookup route by default; exact source query syntax and disclosure policy should be approved separately before calls.

## Future Persistence Recommendation

- Recommended next route: `Phase 4.4-P2 - Pixiv LocalSourceHint Persistence for Verified Source Priors`.
- Reason: At least one sample reached auto_verified_high_confidence.
- P2 should persist LocalSourceHint now: `True`.
- Any P2 persistence still requires explicit DB-write approval and must not treat filename-token-only rows as confirmed evidence.

## Local Artifacts

- report_md: `docs/reports/phase-4.4p1-pixiv-live-reference-metadata-pilot.md`
- report_json: `docs/reports/phase-4.4p1-pixiv-live-reference-metadata-pilot-summary.json`
- reference_details_json: `.local_manifests/phase-4.4p1-pixiv-live-reference-details.json`
- correspondence_details_json: `.local_manifests/phase-4.4p1-pixiv-correspondence-details.json`
- metadata_sheet_md: `.local_manifests/phase-4.4p1-pixiv-metadata-sheet.md`
- metadata_sheet_csv: `.local_manifests/phase-4.4p1-pixiv-metadata-sheet.csv`
- preview_dir: `.local_manifests/phase-4.4p1-pixiv-preview-derived`
- full_local_paths_public: `False`
- artifacts_are_gitignored: `True`

## Privacy and Safety Confirmation

- db_write: `False`
- db_migration: `False`
- provider_cache_write: `False`
- entity_evidence_write: `False`
- media_entity_candidate_write: `False`
- media_entity_assignment_write: `False`
- confirmed_assignment: `False`
- automatic_entity_creation: `False`
- media_tags_mutation: `False`
- tag_translation_mutation: `False`
- localization_execution: `False`
- entity_resolver: `False`
- broad_similarity_or_clustering: `False`
- source_or_icloud_mutation: `False`
- app_managed_storage_mutation: `False`
- original_image_download: `False`
- original_image_upload: `False`
- cookies_or_login: `False`
- browser_automation: `False`
- hotlink_or_referer_bypass: `False`
- high_volume_requests: `False`
- public_exact_media_to_pixiv_mapping: `False`
- push_main: `False`
- merge: `False`
