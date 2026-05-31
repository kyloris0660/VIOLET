# Phase 4.4-P1 - Pixiv Live Reference / Metadata / Correspondence Pilot

## Why P1 Exists

P0 showed significant filename prior coverage and designed the gate; P1 makes a bounded real public-page probe attempt without persistence.

P0's `reference_lookup_policy_blocked` result was a policy stop, not evidence that the Pixiv filename-prior route is technically invalid.

## Prior-Art Audit

- Sources inspected: `[{"license_or_status": "GPL project; conceptual reference only", "name": "gallery-dl Pixiv extractor", "url": "https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/pixiv.py"}, {"license_or_status": "third-party unofficial API wrapper; no dependency added", "name": "pixivpy app API wrapper and models", "url": "https://github.com/upbit/pixivpy"}, {"license_or_status": "third-party downloader; conceptual reference only", "name": "PixivUtil2 downloader model/parser", "url": "https://github.com/Nandaka/PixivUtil2"}, {"license_or_status": "official public terms; no public unauthenticated metadata API found in bounded search", "name": "pixiv Service Master Terms of Use", "url": "https://www.pixiv.net/terms/?lang=en"}]`.
- Concepts adopted: `["Treat Pixiv identity as artwork work_id plus zero-based page_index, matching the common id_pN filename convention.", "For multi-page works, keep the selected page_index from the filename prior instead of silently falling back to p0.", "When preload metadata is available, select the illust entry keyed by the requested work_id before reading title, artist, tags, page_count, or preview URLs.", "Keep preview URL handling allowlisted, HTTPS-only, redirect-checked, and counted separately from artwork page requests."]`.
- Routes rejected for P1: `["gallery-dl-style authenticated/cookie/refresh-token flows and original image URL expansion.", "pixivpy app API illust_detail as a default path because it is an unofficial authenticated API route.", "PixivUtil2 downloader behavior that depends on login cookies, Referer, or original/medium image downloads.", "Any browser automation, Pixiv login, cookie import, hotlink bypass, or original image download."]`.
- Current P1 public-page probe validity: Still valid only as a tiny bounded public-page metadata/preview probe. It is not a durable Pixiv API contract and must not persist filename-token-only rows or non-auto-verified rows without manual validation and separate DB-write approval.
- Remaining unknowns: `["Whether Pixiv will consistently expose public preload metadata for all relevant public works without login.", "Whether preview thumbnails are representative enough for automated correspondence across crops, page variants, and manga pages.", "Whether a future approved no-upload booru/source adapter can provide richer entity metadata after a Pixiv/source candidate exists."]`.

## Sample Selection

- Selected sample size: `5`.
- Requested sample size: `5`.
- Anime-only sample: `True`.
- Insufficient anime candidates: `False`.
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

- Page requests attempted: `10`.
- Preview requests attempted: `4`.
- Total network attempts including failures: `14`.
- Requests attempted (legacy total): `14`.
- HTTP status distribution: `{"200": 5}`.
- Status-none count: `0`.
- Network error distribution: `{}`.
- Final URL host distribution: `{"www.pixiv.net": 5}`.
- Blocked count: `0`.
- Stopped early: `False`.

## Metadata Availability

- Metadata richness distribution: `{"preview_only": 5}`.
- Field availability counts: `{"canonical_url": 5, "description": 5, "preview_image_candidates": 5, "title": 5}`.

## Preview / Reference Availability

- Preview status distribution: `{"preview_fetch_blocked_unexpected_host": 1, "reference_preview_fetched": 4}`.
- Preview candidate host policy distribution: `{"allowed_pixiv_image_host": 4, "blocked_unexpected_host": 1}`.
- Preview candidate counts: `{"preview_candidates_attempted_allowed": 4, "preview_candidates_skipped_unexpected_host": 1, "preview_candidates_total": 5}`.

## Correspondence Verification

- Result distribution: `{"auto_rejected_mismatch": 3, "auto_verified_high_confidence": 1, "preview_fetch_blocked": 1}`.
- Threshold policy: `phase44p1-pilot-v1-not-production`.
- Mismatch count changed after reviewer fixes: `True`.
- Previous/current mismatch count: `4` / `3`.

## Manual Validation Pack

- Generated: `True`.
- Items needing manual validation: `4`.
- Reason bucket distribution: `{"page_index_mismatch_possible": 1, "true_mismatch_possible": 2, "unsupported_or_unclear": 1}`.

## Optional No-Upload Booru Lookup

- Status: `no_upload_booru_lookup_policy_blocked`.
- Requests attempted: `0`.
- Reason: P1 inspected the option but did not enable a no-upload booru source-URL lookup route by default; exact source query syntax and disclosure policy should be approved separately before calls.

## Future Persistence Recommendation

- Recommended next route: `Phase 4.4-P2 - Pixiv LocalSourceHint Persistence for Verified Source Priors`.
- Reason: At least one sample reached auto_verified_high_confidence, but non-auto-verified rows still require manual validation before P2.
- P2 should persist LocalSourceHint now: `False`.
- P2 should wait for manual validation: `True`.
- Any P2 persistence still requires explicit DB-write approval and must not treat filename-token-only rows as confirmed evidence.

## Local Artifacts

- report_md: `docs/reports/phase-4.4p1-pixiv-live-reference-metadata-pilot.md`
- report_json: `docs/reports/phase-4.4p1-pixiv-live-reference-metadata-pilot-summary.json`
- reference_details_json: `.local_manifests/phase-4.4p1-pixiv-live-reference-details.json`
- correspondence_details_json: `.local_manifests/phase-4.4p1-pixiv-correspondence-details.json`
- metadata_sheet_md: `.local_manifests/phase-4.4p1-pixiv-metadata-sheet.md`
- metadata_sheet_csv: `.local_manifests/phase-4.4p1-pixiv-metadata-sheet.csv`
- preview_dir: `.local_manifests/phase-4.4p1-pixiv-preview-derived`
- manual_validation_sheet_md: `.local_manifests/phase-4.4p1-pixiv-manual-validation-sheet.md`
- manual_validation_sheet_csv: `.local_manifests/phase-4.4p1-pixiv-manual-validation-sheet.csv`
- manual_validation_contact_sheet_html: `.local_manifests/phase-4.4p1-pixiv-manual-validation-contact-sheet.html`
- manual_validation_contact_sheet_md: `.local_manifests/phase-4.4p1-pixiv-manual-validation-contact-sheet.md`
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
