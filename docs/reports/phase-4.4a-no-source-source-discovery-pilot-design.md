# Phase 4.4-A - No-source Source Discovery Pilot Design

Date: 2026-05-25

PR: [#72](https://github.com/kyloris0660/VIOLET/pull/72)

Sync note: PR #72 was rebased after Phase R1 / PR #73 so the canonical repository remains `https://github.com/kyloris0660/VIOLET` and the local worktree path may still remain `C:\Users\kyloris\Documents\AnimeLocalBooru`.

## Summary

Phase 4.4-A records the strategy shift after PR #71 / Phase 4.3-B.  The current library should be treated as having no usable traceable source URLs, no reliable external post IDs, and no imported source metadata suitable for exact-source lookup.  The next useful problem is not "find existing exact sources"; it is designing a safe, bounded way to rediscover reliable source/entity evidence for no-source anime images.

This stage is documentation and policy design only:

- No provider adapter implementation.
- No external provider API calls.
- No authenticated provider calls.
- No scraping or crawler work.
- No reverse image search execution.
- No original image, thumbnail, hash, or embedding upload.
- No DB import, classification, AI tagging, localization, Entity Resolver execution, similarity/clustering, staging copy, source/iCloud mutation, app-managed storage mutation, provider cache writes, evidence writes, candidate writes, or assignment writes.

## No-source Assumption

The working assumption for the current library is:

- No usable source URL.
- No reliable external post ID.
- No imported source metadata suitable for exact-source lookup.
- Existing AI proper-noun tags are weak identity evidence only.
- General/meta visual tags can help describe content, but they are not source/entity truth.

Therefore Phase 4.4-A intentionally skips exact-source inventory as the next main stage.  A minor future implementation sanity check may count obvious source-shaped fields to avoid missing a trivial case, but it must not become a phase goal or a blocker for no-source source discovery.

## Core Design Answer

Given no usable source URL or post ID, the safest first source-discovery pilot is a highly controlled, one-provider, anime-only reverse-search/source-discovery pilot that:

- Starts with a small sample, recommended `max_items=25`.
- Uses only already-classified `content_class=anime`.
- Blocks `unknown`, `non_anime`, and `illustration` by default.
- Sends no original image by default.
- Sends a derived thumbnail or resized/stripped derivative only after explicit user/ChatGPT approval for the selected provider and transform.
- Sends no local paths, filenames, source labels, directory names, iCloud metadata, or secrets.
- Is cache-first and negative-cache aware.
- Is sequential and rate-limited.
- Writes evidence/candidate-only data only in a later approved implementation stage.
- Creates no confirmed assignments and no automatic entities.
- Does not schedule broad or full-library runs.

## Provider Research Summary

Public/official documentation research was limited to documentation pages and did not call provider APIs.

| Provider/category | Metadata obtainable | Source/entity usefulness | Auth/API key | Rate/burst constraints | TOS/legal/scraping concern | Privacy risk | Expected failure modes | Suitable first no-source provider? | Input needed |
|---|---|---|---|---|---|---|---|---|---|
| SauceNAO-style reverse image search | Similarity score, index/source, title, creator/author names, source/post URLs depending on index and response. | High relevance for anime illustration source rediscovery when no local source exists. Can yield source URLs/post IDs that later feed booru/Pixiv/source-specific verification. | Official API page exists, but implementation must verify current key/quota/auth requirements directly before any adapter. Automated browsing of the API page can be blocked; do not rely on stale wrapper docs. | Must be provider-specific. Treat as low-throughput by default; respect response headers, documented quota, and `Retry-After` if present. | Official API/TOS details must be reviewed before implementation. Scraping the web UI is not allowed. | High if original or identifiable image bytes are uploaded. Lower but still nonzero with resized/stripped derivative. | False positives, crops/edits, repost chains, deleted/private sources, index gaps, rate limit, auth failure, schema changes. | Best first category if explicit derived-image upload approval and current official API policy are verified. | For local no-source media, a provider-supported image query is required; original is blocked, derived thumbnail/resized stripped image only with approval. |
| IQDB-like reverse image search | Similar-image/source hints where available. | Useful in concept, but not authoritative without source verification. | No stable official automation API was identified for this project workflow. | Not suitable to define until official API exists. | Automated use risks becoming scraping if no official API exists. | High because image upload is typically needed. | Service instability, no structured API, false positives, ambiguous results. | Rejected for first automated provider unless a stable official API and terms are confirmed. | Usually image upload or image URL. |
| trace.moe | Anime/episode/scene candidates, AniList ID, episode, timestamp, similarity, filename-like metadata depending on response. | Good for anime screenshot/scene lookup; weak fit for illustration/source discovery. | Public API docs exist; exact current usage/rate policy must be verified before implementation. | Must obey trace.moe policy and any rate limit headers. | Use only documented API; no scraping. | Requires image URL or image bytes for local no-source media; privacy-sensitive. | Works poorly for illustrations, edits, crops, non-screenshot art, low similarity, wrong episode. | Not primary for this library. Possible later special-case for screenshot-like media. | Image URL or upload/derived image, depending endpoint. |
| Danbooru/Gelbooru/booru-style APIs | Post metadata, tags, tag categories for some providers, source field, rating, file metadata, public post URL. | Strong after a reverse search yields a known provider post/source. Weak as the first step when source/post ID is absent. | Public APIs exist; auth and quotas vary by provider. | Provider-specific limits; use strict budget/cache wrappers if later called. | Use official APIs only. Do not scrape pages. | Low if querying public post IDs/URLs; higher if source queries leak private local info. | Missing/deleted posts, tag/category mismatch, repost/source ambiguity, rate limit. | Not first no-source provider. Use as second-step source verification after reverse search. | Exact post ID, source URL, or safe public provider query. |
| Pixiv | Artwork URL/ID, artist/work metadata if accessible under allowed public policy. | High value when a source URL/artwork ID is already known or returned by reverse search. | No login/cookie/private API workflow is allowed. Public official API feasibility is unclear for this project. | Not designed until policy/legal review. | Scraping, cookies, private API use, and login automation are blocked. | High if credentials or image upload are involved; exact public URL parsing is lower risk. | Private/deleted/age-gated posts, auth walls, anti-scraping, policy uncertainty. | Not recommended as first implementation. Use URL parsing only where source URL is already known, and network access only after explicit policy review. | Source URL/artwork ID if already known. |
| AniList | Work metadata, titles, IDs, relations, characters/staff depending GraphQL query. | Useful work metadata enrichment after source/work is known. Not reverse image search. | Public GraphQL API; no user credentials needed for basic public queries. | Official docs describe rate limiting and 429 behavior; implementation must respect it. | Use documented GraphQL API only. | Low when querying public work IDs/names; do not send local filenames/source labels. | Ambiguous titles, missing work mapping, language/title variants, not media-post identity. | Not first source-discovery provider. Good later enrichment supplement. | Work ID/title/source-backed candidate. |
| Local pHash / image embedding | Local near-duplicate/visual similarity groups, duplicate clusters, internal recall. | Useful supplementary recall and sample selection; not source truth. | No external auth. | Local compute budget only. | No external provider TOS issue when local-only. | Low if reports remain aggregate/redacted. | Multi-character scenes, same artist/style/pose/clothing, false clusters. | Not a source-discovery provider. Safe local supplement, but no confirmed assignment. | Local image/thumbnail bytes only; no external upload. |

References reviewed:

- SauceNAO public site and official API reference entry point: `https://saucenao.com/about.html`, `https://saucenao.com/user.php?page=search-api`, `https://saucenao.com/tools/examples/api/index_details.txt`
- trace.moe API docs: `https://soruly.github.io/trace.moe-api/`
- Danbooru API docs: `https://danbooru.donmai.us/wiki_pages/help:api`, `https://danbooru.donmai.us/wiki_pages/api:posts`
- Gelbooru DAPI docs/wiki: `https://gelbooru.com/index.php?page=help&topic=dapi`, `https://gelbooru.com/index.php?page=wiki&s=view&id=18780`
- AniList API docs and rate-limit guidance: `https://anilist.gitbook.io/anilist-apiv2-docs/`, `https://anilist.gitbook.io/anilist-apiv2-docs/docs/guide/rate-limiting`
- Pixiv public policy/help entry points: `https://policies.pixiv.net/en.html`, `https://www.pixiv.help/hc/en-us`
- OpenCV image hash docs for local-only pHash-like options: `https://docs.opencv.org/4.x/d4/d93/group__img__hash.html`

Implementation-time caveat: provider pages can be dynamic, access-controlled, or changed after this report. Phase 4.4-B must re-verify current official API terms, auth, quota, rate-limit, response fields, and privacy rules before any provider call.

## Privacy Eligibility Policy

Default external eligibility is fail-closed.

| Local class/data | Default policy |
|---|---|
| `anime` | Eligible only under an explicit user/ChatGPT-approved provider pilot. The first sample must be small. Original upload is blocked by default. Derived thumbnail/resized stripped input requires provider-specific approval. |
| `unknown` | Blocked by default. It may contain private screenshots, personal photos, or non-library content. It can be allowed only after manual classification and explicit approval. |
| `non_anime` | Blocked. |
| `illustration` | Blocked by default unless explicitly included under a reviewed anime/illustration policy. |
| Local paths / iCloud paths / directory names / source labels | Never sent to providers and never included in public reports. |
| Filenames | Never sent by default. May be used locally for internal sorting only if privacy-safe. |
| Image bytes | Original image upload blocked by default. Derived thumbnail/crop/hash/embedding only if provider-specific policy explicitly allows. |
| Public reports | Aggregate only. No local paths, raw filenames, raw source labels, secrets, credentials, provider raw payloads, or privacy-sensitive query shapes. |

Privacy-blocked rows are safety successes, not provider failures. They should be counted separately from no-match and provider-error outcomes.

## Pilot Input Type Policy

| Input option | Privacy risk | Provider support | Expected match quality | Implementation complexity | First-pilot suitability |
|---|---|---|---|---|---|
| Original image | Highest. Sends full user image content and possible embedded metadata if not stripped. | Supported by reverse-search providers. | Best. | Moderate, but policy risk is high. | No by default. Requires explicit separate approval and should not be the first default. |
| App-managed thumbnail | Lower than original, but still visual content upload. Thumbnail may preserve enough private/identifying content to be sensitive. | Depends on provider; reverse-search quality may degrade. | Medium to good for anime art if not too small/compressed. | Moderate; must ensure no source path/filename/metadata leaks. | Possible only after explicit approval and transform policy. |
| Resized/stripped derivative | Lower than original if generated from safe app media with metadata stripped and fixed max dimension. Still leaks visual content. | Likely supported where file upload is supported. | Better than tiny thumbnails, worse than original for crops/edits. | Moderate; needs deterministic transform/version and privacy QA. | Conservative recommended query input if Phase 4.4-B is approved to perform live reverse search. |
| Perceptual hash | Low if kept local; potentially sensitive if sent externally. | Most public reverse-search providers do not accept only local pHash. | Good for local near-duplicates, poor for external source discovery unless provider supports same hash index. | Low local complexity, provider mapping unclear. | Local-only supplement. Do not treat as source truth. |
| Embedding | Low if local-only, higher if sent externally. Can encode visual content. | Not a standard input for first provider candidates. | Useful for local recall, weak for source truth. | Moderate to high. | Local-only supplement, no automatic candidate/assignment. |
| No image upload | Lowest privacy risk. | Exact booru/source APIs can work only if post ID/source URL exists. Current library lacks these. | No useful no-source discovery by itself. | Low. | Good as a dry-run/sanity mode, but cannot solve no-source source discovery. |

Default recommendation: do not upload originals. Phase 4.4-B should implement a prepare-only/dry-run mode first and require explicit approval before sending a resized/stripped derivative for a small anime-only batch. Hashes/embeddings remain local unless an approved provider explicitly supports them and policy review accepts the privacy tradeoff.

## Recommended First Provider Category

Recommended Phase 4.4-B category: one SauceNAO-style reverse image search provider for anime illustration source discovery, contingent on current official API/TOS/rate-limit verification and explicit approval for the derived-image input.

Why this category:

- It addresses the actual no-source problem.
- It is more likely than exact booru lookup to rediscover source/post candidates when local source fields are absent.
- It can return source/post URLs that can later feed Danbooru/Gelbooru/Pixiv/source-specific verification.
- It maps naturally to `ProviderCache`, `NegativeLookupCache`, `EntityEvidence`, and optionally `MediaEntityCandidate`.
- It keeps AI proper-noun tags as weak prioritization context rather than truth.

Required query type:

- `reverse_search_derived_image` or equivalent provider-specific upload query.
- The first approved live run should use a deterministic, metadata-stripped derivative, not the original.
- Query hash should be based on a redacted request shape plus transform/input fingerprint, not local path or filename.

Privacy risk:

- The provider receives visual content and can infer that the operator has the image.
- Mitigation: anime-only sample, no unknown/non_anime/illustration, no originals, no local metadata, tiny sample, explicit approval, redacted reports, cache-first/no-repeat behavior, and no public raw payloads.

Rate/budget policy:

- `max_items=25`
- `max_requests=25` for one request per item, or `50` if the provider needs a follow-up source verification request.
- `requests_per_minute=10-30` or provider-specific lower value.
- `concurrency=1` unless provider docs explicitly allow more.
- `max_runtime=10 minutes`.

Expected metadata:

- Provider match score/similarity/confidence.
- Public result/source URL or post URL where available.
- Candidate title/creator/work fields where available.
- Provider index/source category.
- Failure/no-match/error class.

Expected confidence fields:

- Provider score/similarity as raw value.
- Project-normalized confidence tier: `high_confidence_match`, `low_confidence_match`, `conflict`, `no_match`, `privacy_blocked`, or `provider_error`.
- Candidate creation threshold must be conservative and provider-specific. Low-score results should be evidence-only or negative-cache only.

Reasons it may fail:

- The image is cropped, edited, upscaled, compressed, recolored, or meme-modified.
- The source is not indexed or is deleted/private.
- Provider result points to a repost rather than original.
- Multi-character or collage images create ambiguous source/entity data.
- Provider API policy, quota, auth, or schema changes.
- Privacy approval blocks the input type.

Rejected first categories:

- Exact booru/source lookup is rejected as the first no-source stage because the library lacks usable post IDs/source URLs. It becomes valuable after reverse search yields a candidate source.
- trace.moe is rejected as primary because it is mainly anime screenshot/scene lookup, not illustration source discovery.
- IQDB-like automation is rejected unless a stable official API and terms are confirmed.
- Pixiv network integration is rejected for the first stage because login/cookie/private API/scraping paths are blocked and policy feasibility is unclear.

## Phase 4.4-B Pilot Design

Phase 4.4-B should be the first implementation stage only if the user/ChatGPT approves the provider, input transform, and budget. It should not run a broad live pilot by default.

Recommended implementation shape:

1. Add a provider policy row/config path that remains disabled by default.
2. Add a dry-run planner that selects at most `25` already-classified `anime` media and reports privacy eligibility without sending requests.
3. Add deterministic derived-input preparation only after explicit approval. The transform should strip metadata, use fixed max dimension, and not include filenames/paths in multipart metadata.
4. Add cache lookup before any request. If `ProviderCache` or `NegativeLookupCache` has an unexpired entry for the query hash, do not send a request.
5. Send requests sequentially with provider-specific rate limiting.
6. Normalize provider result into redacted cache/evidence/candidate shapes.
7. Write only approved cache/evidence/candidate rows; no assignments.
8. Emit an aggregate public report and a private local audit artifact only if needed and gitignored.

Recommended sample policy:

- `content_class=anime` only.
- Exclude `unknown`, `non_anime`, and `illustration`.
- Exclude rows lacking app-managed media availability or safe thumbnail/derivative generation.
- Exclude files where derivative generation would require source/iCloud reads.
- Prefer small representative media; do not schedule the full library.
- Optional manual seed rows can be included as validation examples, but not as a full-coverage route.

## Run Budget, Rate Limit, and Circuit Breaker

Initial live-pilot budget:

| Control | Default |
|---|---|
| `max_items` | `25` |
| `content_class` | `anime` only |
| `max_requests` | `25` if one request per item; `50` if one approved follow-up per item is needed |
| `requests_per_minute` | `10-30` or provider-specific lower value |
| `max_failures` | `5` |
| `max_consecutive_failures` | `3` |
| `max_same_reason_failures` | `5` |
| `max_runtime` | `10 minutes` |
| `concurrency` | `1` unless official provider policy explicitly allows more |

Retry/backoff:

- Use a connect timeout and total request timeout.
- Retry at most once or twice for transient network/5xx/rate-limit errors, with exponential backoff and jitter.
- Respect `Retry-After` or provider equivalent. If it exceeds the run budget, stop cleanly.
- Do not retry `privacy_blocked`, `auth_failed`, `forbidden`, `schema_changed`, invalid input, or unsupported query.
- No repeated query for the same media/input hash during cache TTL.

Structural stop conditions:

- Provider policy disabled or missing.
- Auth failed / forbidden.
- Schema changed or response cannot be safely normalized.
- Privacy leak detected in request shape, cache payload, evidence summary, audit, or public report.
- Failure budget exceeded.
- Rate limit budget exceeded.
- Unexpected DB/source/app-storage mutation.
- Any attempt to include `unknown`, `non_anime`, unapproved `illustration`, original image upload, local path, filename, source label, iCloud path, or secret.

Failure classes:

- `privacy_blocked`
- `no_match`
- `low_confidence`
- `conflict`
- `rate_limited`
- `auth_failed`
- `forbidden`
- `timeout`
- `network_error`
- `provider_5xx`
- `schema_changed`
- `invalid_input`
- `unsupported_query`
- `normalization_failed`

Negative cache policy:

- Store `no_match`, `privacy_blocked`, `unsupported_query`, and stable low-confidence results with TTL.
- Do not negative-cache transient provider failures for long periods.
- Include `provider`, `query_type`, `query_hash`, `reason`, and `expires_at`.

## Cache, Evidence, and Candidate Mapping

Future Phase 4.4-B writes, if approved:

| Table | Future write policy | State/mapping |
|---|---|---|
| `ExternalSource` | Provider policy row only, disabled by default until explicit approval. | `provider`, `enabled=false`, `auth_mode`, `base_url`, `terms_url`, `rate_limit_policy`, `privacy_policy`, `notes` with policy version. |
| `ProviderCache` | Cache successful normalized responses and cacheable provider errors. | `provider`, `query_type=reverse_search_derived_image`, `query_hash`, `request_shape_redacted`, `response_status`, `response_json_redacted`, `error_class`, `fetched_at`, `expires_at`. No raw payload dump in public docs. |
| `NegativeLookupCache` | Cache `privacy_blocked`, `no_match`, stable `low_confidence`, and `unsupported_query`. | `provider`, `query_type`, `query_hash`, `reason`, `expires_at`. |
| `EntityEvidence` | Evidence rows for redacted reverse-search/source-discovery results. | `evidence_type=reverse_search`, `source_type=external` or `trusted_external` only after verification policy, `provider`, `media_id`, optional `entity_id`, `query_hash`, `payload_ref` to safe cache reference, `score`, `summary`, `privacy_redacted=true`. |
| `MediaEntityCandidate` | Optional only when confidence threshold and provider result mapping are approved. | `generator=external`, `status=suggested`, `entity_type`, `candidate_name`, optional `entity_id`, `score`, `evidence_id`, `review_reason`. |
| `EntityExternalIdentity` | Only after candidate entity linking policy is approved. | `identity_status=candidate` by default, not verified unless source-specific proof exists. |

Future Phase 4.4-B must not write:

- Confirmed `MediaEntityAssignment`.
- Automatic trusted `Entity` as truth without review.
- `media_tags`.
- `TagTranslation`.
- Source/iCloud/app-managed storage files.
- Provider raw payloads to public report.

Cache key/query hash:

- Must be generated from privacy-safe request shape and transform/input fingerprint.
- Must never include local path, filename, source label, iCloud metadata, or raw provider credentials.
- If the fingerprint is derived from image bytes, treat it as internal/private and keep public reports aggregate.

Confidence policy:

- `high_confidence_match`: candidate allowed only if provider-specific threshold and source URL/post evidence are present.
- `low_confidence_match`: evidence-only or negative-cache by default.
- `conflict`: evidence-only and manual review required if pursued.
- `no_match`: negative cache only.
- `privacy_blocked`: audit/negative cache only.

## Success Metrics and Decision Gates

Phase 4.4-B should measure:

- `attempted`
- `eligible`
- `privacy_blocked_count`
- `requests_sent`
- `cache_hits`
- `cache_misses`
- `no_match`
- `high_confidence_match`
- `low_confidence_match`
- `conflict_count`
- `provider_error_count`
- `rate_limit_count`
- `timeout_count`
- `average_requests_per_item`
- `estimated_time_for_1k`
- `estimated_time_for_10k`
- `manual_review_burden_estimate`
- `evidence_rows_created`
- `candidates_created`

Decision gates:

- If high-confidence match rate is high, provider errors are low, and manual burden is manageable, proceed to a larger pilot or candidate generation design.
- If no-match or conflict rate is high, revise provider/input before scaling.
- If rate limits or provider failures are high, stop and do not scale.
- If privacy-blocked count is high, revise eligibility and input policy.
- If manual burden is high, do not scale candidate generation.
- If cache/evidence mapping is unclear, do not write candidates.

Manual review burden estimate:

- Count each high-confidence match as one targeted review item unless exact source-specific verification later proves it can be batched safely.
- Count conflicts and multi-entity results as high-burden cases.
- Do not create a broad exhaustive queue from low-confidence results.

## Phase 3.9 Dependency Boundary

Phase 4.4-B can happen before Phase 3.9 only as a small, bounded pilot, for example:

- `max_items=25`
- anime-only
- one provider
- explicit provider/input approval
- cache-first
- redacted report
- evidence/candidate-only
- no confirmed assignments
- no full-library scheduling

Phase 3.9 is required before:

- Any larger provider pilot.
- Broad enrichment.
- Repeated scheduled source-discovery runs.
- Full-library request scheduling.
- 5k/10k scale provider workflows.
- Large cache population where every item needs durable final state.
- Provider runs that need retry/defer/backfill/import-like per-item lifecycle tracking.

Provider enrichment creates the same per-item final-state problem as ingestion: each item needs a final state, failure reason, cache status, retry/defer decision, privacy-blocked status, evidence/candidate outcome, and public/private artifact split. Scaling should share Phase 3.9-style ledger discipline.

## Manual Seeds and Local Retrieval Role

Manual seeds remain useful, but they are not the primary full-coverage route.

Useful roles:

- Provide validation examples for provider precision/recall.
- Create trusted local anchors for manual correction.
- Expose conflict examples and edge cases.
- Help evaluate whether provider results are worth scaling.

Limits:

- Manual seeds cannot scale to the whole library.
- Seed-based local retrieval and clustering are supplementary recall tools only.
- Whole-image embeddings can confuse multi-character scenes, artist style, pose, clothing, and motif similarity.
- No automatic confirmed assignment may come from clustering or visual similarity.
- Global similarity graph remains deferred.

## Relationship to Exact-source Lookup

Exact-source lookup is still valuable after a source is discovered.

Future flow:

1. Reverse-search/source-discovery provider yields a candidate public source/post URL.
2. Source-specific verifier checks that source/post via official API or approved public policy.
3. Normalized source/entity evidence is cached and recorded.
4. Candidate rows may be created if confidence policy allows.
5. Human review or later approved policy decides assignment; first pilot does not auto-confirm.

This keeps source-first/provenance-first intact while acknowledging that the current library has no usable source fields to start from.

## No-runtime-mutation Confirmation

This stage performed documentation and public-doc research only.

- No external provider API calls.
- No authenticated provider calls.
- No scraping.
- No reverse image search execution.
- No image upload.
- No thumbnail upload.
- No crawler.
- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No staging copy.
- No entity candidate creation.
- No entity assignment creation.
- No entity auto-creation.
- No ProviderCache writes.
- No EntityEvidence writes.
- No Entity Resolver execution.
- No similarity/clustering.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No production ingestion ledger implementation.
- No admin UI rewrite.
- No push to `main`.
- No merge.

## Artifact Lifecycle

- This report and its summary JSON are public report / handoff documentation.
- `docs/current-handoff.md` and `docs/project-roadmap.md` updates are long-term roadmap/handoff documentation.
- `AGENTS.md` and `CLAUDE.md` policy additions are durable project governance documentation.
- No scripts, provider adapters, reusable runtime tooling, local one-off output, or private audit artifacts are introduced.

## Engineering Judgment

The no-source reverse-search pilot is now well-defined as a design object, but it is not yet safe to execute.  The critical unresolved decision is provider/input approval: a SauceNAO-style provider is the right first category only if the user/ChatGPT accepts a small derived-image upload under a reviewed provider policy. If original upload is the only viable provider path, the pilot should stop rather than weaken the privacy default.

The first real pilot can be implemented next as Phase 4.4-B if it starts with dry-run planning, provider-policy verification, deterministic derived-input generation, cache-first behavior, and explicit live-run approval. It should create evidence/candidates only after policy approval and should never create confirmed assignments.

Phase 3.9 does not need to precede a 25-item one-provider pilot because the pilot can be bounded and manually inspected. Phase 3.9 should precede scaling, repeated runs, large cache populations, 5k/10k workflows, and full-library scheduling.

Recommended next phase: Phase 4.4-B - one-provider no-source reverse-search pilot implementation plan and dry-run scaffold, with implementation-time provider policy verification and an explicit stop before any live upload/request execution.
