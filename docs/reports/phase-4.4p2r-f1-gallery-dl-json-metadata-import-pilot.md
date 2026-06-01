# Phase 4.4-P2R-F1 - gallery-dl JSON Metadata Import Pilot and External Adapter Readiness

## Why This Stage Exists

PR #87 chose gallery-dl JSON metadata import as the immediate Pixiv metadata route after PR #86 public-page preview probing proved unsuitable as a durable metadata foundation.

## PR #87 Merge Confirmation

- PR #87 state: `MERGED`.
- PR #87 merged at: `2026-06-01T04:05:37Z`.
- PR #87 merge commit: `d74f8e073c27dec70fd4f6e5df192eb90450c458`.
- PR #86 state: `CLOSED`; treated as superseded diagnostic evidence only.

## gallery-dl Environment

- Available: `True`.
- Version: `1.32.1`.
- Command entrypoint used by this Codex shell: `py -m gallery_dl`.
- Bare `gallery-dl` on PATH: `False`.

## Command Summary

- Metadata-first template: `gallery-dl --dump-json --no-download "https://www.pixiv.net/artworks/<WORK_ID>"`.
- Module fallback template: `py -m gallery_dl --dump-json --no-download "https://www.pixiv.net/artworks/<WORK_ID>"`.
- Metadata command count: `5`.
- Metadata success count: `5`.
- Metadata failure count: `0`.
- Bounded downloads used: `False`.
- Downloaded file count: `0`.
- Downloaded total bytes: `0`.
- Cleanup performed: `False`.

## Input And Records

- Input file count: `6`.
- Raw record count: `16`.
- Raw event count: `16`.
- Directory context event count: `6`.
- URL media event count: `10`.
- Normalized media record count: `10`.
- Invalid JSON count: `0`.
- Unsupported shape count: `0`.
- Raw record shape distribution: `{"gallery_dl_directory_context_event": 6, "gallery_dl_url_media_event": 10}`.
- Normalized media record shape distribution: `{"gallery_dl_url_media_event": 10}`.

## Schema Field Availability

- Field availability: `{"artist_id": 10, "artist_name": 10, "caption": 7, "extractor_category": 10, "gallery_dl_filename": 10, "image_url_kinds": 10, "page_count": 10, "page_index": 10, "tags": 10, "title": 10, "translated_tags": 0, "work_id": 10}`.
- Tags/artist/title/page_count: `{"artist_id": 10, "artist_name": 10, "page_count": 10, "tags": 10, "title": 10, "translated_tags": 0}`.
- Metadata richness distribution: `{"rich_structured_metadata": 10}`.

## Local Source-Prior Join

- Join status counts: `{"metadata_matches_eligible_anime_local_prior": 6, "metadata_work_id_found_no_local_match": 4}`.
- Match content-class counts: `{"anime": 6}`.
- Future eligibility counts: `{"eligible_for_future_entity_candidate": 6, "eligible_for_future_local_source_hint": 6, "ineligible_for_future_entity_candidate": 4, "ineligible_for_future_local_source_hint": 4}`.
- Local prior keys without metadata: `549`.
- Local prior total media inspected: `1989`.

## Page Index Validation

- Page-index status counts: `{"page_index_within_page_count": 10}`.

## Normalized DTO

- DTO name: `PixivGalleryDlMetadataRecord`.
- Source adapter: `gallery_dl_json`.
- DB write allowed: `False`.
- Privacy level: `private_exact_mapping`.

## Correspondence Feasibility

- Visual check performed: `False`.
- Status counts: `{"metadata_work_page_match_no_visual_check": 10}`.
- Image correspondence blocker: `False`.

## External Adapter Readiness

- `command_boundary`: `["Future V.I.O.L.E.T. may invoke user-installed gallery-dl in a separately approved phase.", "This stage proves bounded local JSON import and metadata/reference command shape only."]`.
- `config_boundary`: `["gallery-dl config remains user-managed outside the repo.", "No token, cookie, refresh token, or authorization header is stored in V.I.O.L.E.T. DB or public reports."]`.
- `version_capture`: `"record gallery-dl --version or equivalent module entrypoint version"`.
- `output_contract`: `["JSON/JSONL/NDJSON input lives under ignored .local_manifests for pilots.", "Parser accepts dict records, gallery-dl event arrays, JSON arrays, and JSONL.", "Public reports use aggregate-only redacted output."]`.
- `download_contract`: `["metadata-first", "optional bounded reference download only under phase-specific .local_manifests paths", "no broad original download", "file and byte counts must be reported", "cleanup option must not delete outside phase-specific paths"]`.
- `safety_gates`: `["sample size 5 by default and 10 maximum without renewed approval", "no original image download by default", "request/command count logging", "timeout/retry policy before broad use", "no broad run without a provider run ledger"]`.
- `failure_handling`: `["missing executable", "gallery-dl config/auth blocked", "malformed JSON", "missing work_id", "missing page_index", "ambiguous local match", "secret-like payload detected", "unexpected downloaded image file detected", "output path violation"]`.

## Future Route Recommendation

- Decision: `A_proceed_to_external_gallery_dl_metadata_reference_adapter_pilot`.
- Reason: `gallery-dl JSON provided structured metadata and at least one eligible anime local filename-prior join succeeded`.
- DB persistence: `later_phase_only`.

## Privacy And Safety Confirmation

- `public_report_contains_exact_pixiv_ids`: `False`.
- `public_report_contains_exact_local_filenames`: `False`.
- `public_report_contains_exact_media_id_mapping`: `False`.
- `public_report_contains_raw_gallery_dl_json`: `False`.
- `public_report_contains_raw_image_urls`: `False`.
- `sensitive_material_printed_or_committed`: `False`.
- `db_write`: `False`.
- `db_migration`: `False`.
- `provider_cache_write`: `False`.
- `entity_evidence_write`: `False`.
- `media_entity_candidate_write`: `False`.
- `local_source_hint_write`: `False`.
- `confirmed_assignment`: `False`.
- `automatic_entity`: `False`.
- `source_or_icloud_mutation`: `False`.
- `app_managed_storage_mutation`: `False`.
- `downloaded_artifacts_committed`: `False`.
- `push_main`: `False`.
- `merge`: `False`.
