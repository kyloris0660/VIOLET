# Phase 4.4-D0/D1 - Second Provider Scouting and Conditional Tiny Pilot

Date: 2026-05-28

## Why Combined D0/D1 Exists

GOV-2 is active: durable DB/provider/evidence contracts stay strict, while phase-scoped scouting and pilot work should stay lightweight. D0/D1 is combined only when the same stage can safely answer the real question: is there a task-appropriate second provider for source-backed anime/illustration metadata discovery, and can it run a tiny five-sample pilot without expanding scope?

This stage does not choose a provider just because it is easy to call. Provider priority is:

1. Functional fit to local illustration/source-backed metadata discovery.
2. Quality of source-backed metadata.
3. Compatibility with the provider-neutral contract.
4. Privacy, TOS, and upload safety.
5. API stability and automation policy.
6. Credential availability and implementation cost.

## Correction Applied

An initial pre-upload trace.moe readiness check was drafted because trace.moe has an easy public upload API. That selection logic was corrected before any sample upload. The trace.moe path produced only local ignored derived files and a `/me` quota probe; it made `0` search/upload requests and uploaded `0` images.

trace.moe is now classified as `not_selected_for_current_illustration_source_discovery`: useful for anime screenshot / episode scene identification, but not a booru/illustration source-discovery provider and not equivalent to artist/work/character source evidence.

## Current State After C1/HF1

- PR #79 merged the provider-neutral C0 evidence contract.
- PR #81 merged validated high-confidence SauceNAO evidence persistence for `2687` and `2670`.
- PR #82 merged the DB write gate hotfix: `EvidencePersistencePlan.db_write_allowed` is now enforced by the durable persistence service, and the C1 runner explicitly promotes only approved C1 plans to writable.
- This D0/D1 stage did not create a second DB write path and did not write DB rows.

## Official Sources Used

- TinEye reverse image search and privacy: `https://www.tineye.com/`
- TinEye API help center: `https://help.tineye.com/`
- TinEye API product/pricing entry point: `https://services.tineye.com/TinEyeAPI`
- TinEye API signup/search bundle instructions: `https://help.tineye.com/article/275-signing-up`
- TinEye API authentication note: `https://help.tineye.com/article/278-transitioning-authentication-methods`
- TinEye general search tutorial/privacy: `https://help.tineye.com/article/265-tineye-tutorial`
- trace.moe API docs: `https://soruly.github.io/trace.moe-api/`
- trace.moe raw API docs: `https://raw.githubusercontent.com/soruly/trace.moe-api/master/docs/docs.md`
- trace.moe terms/privacy page: `https://trace.moe/terms`
- trace.moe FAQ: `https://trace.moe/faq`
- AniList GraphQL docs: `https://github.com/AniList/ApiV2-GraphQL-Docs`
- Danbooru API docs entry point: `https://danbooru.donmai.us/wiki_pages/help:api` (official site was not reachable from this environment during scouting; no live Danbooru request was made)
- Gelbooru API/DAPI wiki: `https://gelbooru.com/index.php?page=wiki&s=view&id=18780`
- IQDB public site: `https://iqdb.org/`
- ASCII2D public site: `https://ascii2d.net/`

## Revised Candidate Table

Scores are `0-5`, where higher is better. `Access readiness` is intentionally separate from task fit.

| Provider | Task fit | Metadata richness | Source-backed metadata | Contract fit | Privacy/TOS | Access readiness | Required user-provided credentials/setup | Live pilot readiness | Recommendation |
|---|---:|---:|---:|---:|---:|---|---|---:|---|
| TinEye API | 4 | 2 | 3 | 4 | 4 | API key / paid search bundle required | Sign up for TinEye API, purchase/search bundle or plan, provide API key via approved local secret path; approve five derived-image uploads | 0 | best official/API source-discovery candidate, credential_required |
| IQDB-style service | 5 | 3 | 4 | 3 | 1 | unclear / blocked | Need a clear official/public API and automation policy; none confirmed in this stage | 0 | high conceptual task fit, reject_for_now due to API/TOS ambiguity |
| Danbooru API | 2 | 5 | 5 | 4 | 5 | public/optional account, but no reverse-image input | If used later, provide known post IDs/source URLs/MD5s; optional account/API auth may be needed for rate limits | 0 | metadata_only_not_source_discovery / possible_later |
| Gelbooru API / DAPI | 2 | 4 | 4 | 3 | 5 | public/optional account, but no reverse-image input | If used later, provide known post IDs/source URLs/MD5s; policy/rate limits should be rechecked | 0 | metadata_only_not_source_discovery / possible_later |
| AniList API | 1 | 3 | 1 | 2 | 5 | no credentials for basic public GraphQL | Needs known AniList ID/title from another provider; no image search | 0 | metadata_only_not_source_discovery |
| trace.moe | 1 | 2 | 1 | 2 | 4 | public API, but current guest quota unavailable | API key/quota only if user explicitly wants screenshot scene identification | 0 | not_selected_for_current_illustration_source_discovery |
| ASCII2D | 3 | 1 | 1 | 2 | 1 | unclear / blocked | Need official/public API and automation policy; none confirmed in this stage | 0 | reject_for_now |
| Pixiv-related options | 2 | 5 | 4 | 2 | 1 | login/OAuth/unofficial path likely required | Only acceptable if future official/API route is proven and separately approved | 0 | blocked_by_policy_or_tos |

## Required Decision Answers

1. Best fit regardless of current credential availability:
   - Best official/API source-discovery candidate: TinEye API.
   - Best pure functional concept: IQDB-style reverse image search, but it is not selectable because no clear official/public API or automation policy was confirmed.

2. Best fit among currently pilotable providers:
   - None. No provider is both task-appropriate for illustration/source-backed metadata discovery and currently safe/pilotable without missing credentials or blocked API/TOS evidence.

3. Why those differ:
   - TinEye API has an official API and privacy posture, but requires user-provided API access/search bundle and returns web match URLs rather than booru artist/character tags directly.
   - IQDB-style services fit booru-style image matching well, but lack confirmed official API/automation policy for this stage.
   - Danbooru/Gelbooru have excellent metadata quality but are lookup/enrichment APIs after a known source/post/hash exists, not no-source reverse-image discovery providers.
   - trace.moe is easy to call but task-mismatched for local illustration/gallery source discovery.

4. Credentials/API keys/accounts needed for the better provider:
   - For TinEye API: user must sign up through TinEye API, purchase or provision search credits/bundle, obtain an API key, provide it through a local approved secret mechanism, and explicitly approve a derived-image five-sample pilot. TinEye's current help center states API requests authenticate with a documented API credential header.

5. Is it better to stop and ask for credentials rather than run a low-fit pilot?
   - Yes. Running trace.moe would test screenshot scene identification, not the current illustration/source-backed metadata route. D0/D1 should stop and ask for a TinEye API setup decision, an approved IQDB-like official API discovery, or a no-upload Danbooru/Gelbooru metadata-lookup route for already-known source IDs.

## Conditional Tiny Pilot Decision

No live pilot ran.

The only currently easy public upload provider, trace.moe, is not task-appropriate for this stage. It should be reserved for a future anime screenshot scene-identification use case only if the user explicitly wants that.

Result:

- `selected_provider`: none
- `live_pilot_ran`: false
- `reason`: no suitable pilotable provider
- `stop_condition`: no task-appropriate provider was both safe/documented and ready for a five-sample derived-image pilot

## Contract-Fit Analysis

TinEye API:

- `ProviderQuery`: can represent derived-image reverse search with API-authenticated request shape, redacted endpoint, input kind, and query hash.
- `ProviderRunOutcome`: can represent completed, rate-limited, credential-required, paid-quota, and provider-error states.
- `SourceMatch`: can represent match URL/source host/rank/score where available.
- `ExtractedProviderMetadata`: likely sparse; source URL/host first, booru artist/work/character only if a matched URL can later be resolved through a separate official metadata lookup.
- `EvidencePersistencePlan`: should remain non-writable in a future D1 until manual validation and a separate persistence policy approve how web match URLs become source evidence.

Danbooru/Gelbooru API:

- Strong metadata fit once a known post/source/hash exists.
- Weak no-source discovery fit because they do not accept local image uploads for reverse search.
- Good candidates for a future no-upload D2 metadata lookup design using already validated SauceNAO Danbooru IDs, but not a replacement for second-provider reverse-image discovery.

trace.moe:

- Can map scene/work fields into the contract, but not as booru/illustration source evidence.
- Should not be used to judge viability of illustration source discovery.

## Route Decision

D0/D1 stops at scouting. No D1 live pilot is appropriate right now.

Recommended route:

1. If the user wants the best official/API reverse-image source-discovery candidate, provide TinEye API access/search credits and explicit approval for a five-sample derived-image pilot.
2. If the user wants better anime/booru metadata without uploads, run a future Danbooru/Gelbooru metadata lookup stage against already-known validated post IDs/source URLs; keep it non-mutating first.
3. Continue to reject scraping/browser/cookie paths for IQDB, ASCII2D, and Pixiv-related routes unless a clear official API and automation policy is found.

## Local Artifacts

Pre-correction local ignored artifacts exist from the aborted trace.moe readiness path:

- `.local_manifests/phase-4.4d0d1-second-provider-details.json`
- `.local_manifests/phase-4.4d0d1-second-provider-derived/`
- `.local_manifests/phase-4.4d0d1-second-provider-manual-validation-sheet.md`
- `.local_manifests/phase-4.4d0d1-second-provider-manual-validation-sheet.csv`

They are not committed. They show only local preparation and pre-upload block state, not a valid D1 pilot result.

## Safety Confirmation

- No DB write.
- No DB migration.
- No ProviderCache write.
- No EntityEvidence write.
- No MediaEntityCandidate write.
- No MediaEntityAssignment write.
- No confirmed assignment.
- No automatic Entity creation.
- No media_tags mutation.
- No TagTranslation mutation.
- No localization execution.
- No Entity Resolver.
- No similarity/clustering.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No original image upload.
- No derived image upload.
- No SauceNAO API call and no SauceNAO credential use.
- No trace.moe search/upload request.
- No scraping, cookies, browser automation, public hosting, subscription purchase, push to `main`, or merge.
