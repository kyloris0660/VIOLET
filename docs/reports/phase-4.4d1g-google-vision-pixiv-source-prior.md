# Phase 4.4-D1G - Google Vision Tiny Pilot and Pixiv Source-Prior Audit

Date: 2026-05-29T14:32:19+00:00

## Summary

- Google Vision Web Detection ran on the five approved anime samples using derived/resized/metadata-stripped images only.
- Pixiv filename source-prior audit scanned DB/app-managed metadata strings only and did not touch source roots or iCloud.
- This stage does not persist provider evidence and does not write DB rows.

## Google Setup Confirmation

- gcloud discovery: `tool_found_by_absolute_path` via `explicit_absolute_path`.
- Project: `image-project-497811`; quota project: `image-project-497811`.
- Vision API enabled: `True`.
- ADC token available: `True` (redacted; token not printed).
- GOOGLE_APPLICATION_CREDENTIALS set: `False`; credential path printed: `False`.

## Approved Sample Gate

- Approved sample IDs: `2690, 2687, 2670, 2654, 2647`.
- Found media: `5` / `5`.
- Eligible media: `5`; blocked: `0`.
- Content-class distribution: `{"anime": 5}`.

## Google Vision Results

- Derived upload count: `5`.
- Google Vision request count: `5`.
- Per-item classification counts: `{"exact_source_candidate": 4, "visually_similar_only": 1}`.
- Exact or likely source candidate count: `4`.
- Source-like match count: `4`.
- Artist/work/character clue item count: `5`.
- Top returned hosts: `{"ar.pinterest.com": 1, "arca.live": 4, "cdn.donmai.us": 4, "cdn.imagedeliveries.com": 3, "i.pinimg.com": 3, "pbs.twimg.com": 4, "preview.redd.it": 2, "s1.zerochan.net": 2, "www.facebook.com": 4, "www.instagram.com": 2, "www.reddit.com": 2, "www.tiktok.com": 7, "www.youtube.com": 16, "www.zerochan.net": 2, "x.com": 9}`.

| media_id | classification | source-like hosts | web entities | best guess labels |
| ---: | --- | --- | --- | --- |
| 2690 | `visually_similar_only` | `none` | `Latex clothing, Cartoon, Clothing, Anime, Illustration, Graphics, CG artwork, Latex` | `cg artwork` |
| 2687 | `exact_source_candidate` | `x.com` | `Honkai: Star Rail, Anime, Acheron, JoyReactor, Image, Cartoon, Animation, Illustration` | `anime` |
| 2670 | `exact_source_candidate` | `s1.zerochan.net, static.zerochan.net, www.zerochan.net, x.com` | `Blue Archive, Ryuuge Kisaki, Cartoon, Image, Cartoon Network, Weekend at Benson's, 짤 모음, Curious Pictures` | `cartoon` |
| 2654 | `exact_source_candidate` | `gelbooru.com, x.com` | `Honkai: Star Rail, Sunday, Dan Heng, X, Character, Nymeia, spirited, Mobile app` | `yao guang x sunday` |
| 2647 | `exact_source_candidate` | `s1.zerochan.net, x.com` | `Zenless Zone Zero, Vivian, Genshin Impact, Character, Combo, Illustration, Guide, Fan art` | `zenless zone zero vivian feet` |

## SauceNAO Comparison

- SauceNAO was previously high-confidence correct for `2687` and `2670`, with useful artist/work/character metadata.
- SauceNAO was previously low-confidence wrong or unrelated for `2690`, `2654`, and `2647`.
- Google Vision rescue count for those SauceNAO low-confidence failures: `2`.
- Google Vision does not provide structured booru-style artist/work/character metadata comparable to SauceNAO; any such values are indirect web entity or page clues.

## Pixiv Filename Source-Prior Audit

- Total media inspected: `1989`.
- Media with Pixiv-like filename token: `555` (`27.9`%).
- Distinct candidate Pixiv work IDs: `551`.
- Duplicate work ID count: `3`.
- Page index distribution: `{"0": 498, "1": 40, "2": 11, "3": 2, "4": 1, "5": 3}`.
- Count by content_class: `{"anime": 546, "non_anime": 1, "unknown": 8}`.
- Approved five-sample Pixiv-prior count: `0`.
- Representation note: The approved five samples are not representative for Pixiv-prior coverage.
- Metadata retention assessment: `filename_and_app_managed_basenames_available_but_no_dedicated_original_basename_column`.
- Exact Pixiv IDs, page indexes, and media mappings are kept only in ignored local details.

## Contract Fit

- Google Vision can map conceptually to `ProviderQuery`, `ProviderRunOutcome`, `SourceMatch`, and `ExtractedProviderMetadata`, but D1G keeps `db_write_allowed=false` and does not persist.
- Pixiv filename prior is a local deterministic source hint, not a provider result and not confirmed evidence. It should not be forced into `ProviderCache` without a future `SourcePrior` / `LocalSourceHint` design.

## Next Step

- Recommended: `Phase 4.4-P0 - Pixiv Filename Source-Prior Metadata Lookup Design`.
- TinEye remains rejected/deferred for this phase due to cost and weaker fit than SauceNAO / Google Vision / Pixiv source-prior routes.

## Safety Confirmation

- app_managed_storage_mutation: `False`
- automatic_entity_creation: `False`
- browser_automation: `False`
- confirmed_assignment: `False`
- cookies: `False`
- credential_or_token_content_printed: `False`
- danbooru_gelbooru_call: `False`
- db_migration: `False`
- db_write: `False`
- entity_evidence_write: `False`
- entity_resolver: `False`
- localization_execution: `False`
- media_entity_candidate_write: `False`
- media_tags_mutation: `False`
- original_image_upload: `False`
- pixiv_call: `False`
- provider_cache_write: `False`
- saucenao_call: `False`
- scraping: `False`
- similarity_clustering: `False`
- source_icloud_mutation: `False`
- tag_translation_mutation: `False`
- tineye_call: `False`
- unapproved_sample_upload: `False`

## Public Redaction Confirmation

- api_token: `False`
- credential_path: `False`
- exact_pixiv_id_mapping: `False`
- local_absolute_paths: `False`
- original_filenames: `False`
- provider_urls: `False`
- raw_image_bytes: `False`
- raw_provider_payload: `False`
- source_icloud_paths: `False`
