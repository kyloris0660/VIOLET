# Phase 4.3-B - Source-first Entity Enrichment Policy and Pilot Design

Date: 2026-05-25

## Summary

Phase 4.3-B records the strategy correction after the Phase 4.3-A proper-noun signal audit and designs the next safe provider-backed pilot. This is a policy/design/documentation stage only.

The durable strategy is now source-first / provenance-first entity enrichment:

- Do not use existing AI proper-noun tags as entity truth.
- Do not use visual clustering or whole-image similarity as primary identity truth.
- Prefer source URLs, exact external post IDs, and imported source metadata when available.
- Use source-backed or reverse-search-backed external evidence only under explicit opt-in provider policy, privacy gates, rate limits, budgets, cache-first behavior, and redacted audit output.
- Treat AI proper-noun tags as weak query seeds, statistics, or recall-prioritization context only.
- Treat image/tag similarity as supplementary recall, not authoritative identity.
- Keep manual entity correction sparse, targeted, and durable.
- Block unknown, non_anime, privacy-sensitive, and non-approved illustration content from external providers by default.

This phase performs no provider calls and writes no runtime data.

## Phase 4.3-A Facts Accepted

Phase 4.3-A inspected the current local library signals and found:

| Metric | Count |
|--------|-------|
| Media inspected | 1989 |
| Media-tag rows inspected | 105699 |
| Proper-noun/entity-like rows | 2131 |
| Distinct proper-noun/entity-like tags | 287 |
| Proper-noun tags sourced only from AI | 287 |
| AI proper-noun rows | 2131 |
| Proper-noun suggestion rows | 325 |
| Trusted anchors | 0 |
| Default T0/T1/T2 candidate-source simulation | 0 |
| If T3 AI confirmed tags were included | 1806 |
| If T4 AI suggestions were included | 325 |

Interpretation:

- AI proper-noun tags are weak identity evidence only.
- General/meta visual tags can be reliable visual descriptors, but they are not identity signals.
- Broad internal candidate generation from current AI proper-noun tags would contaminate the entity system.
- The next entity enrichment path should gather source-backed evidence before creating source-backed candidates.

## Strategy Correction

The old near-term path considered guarded internal candidate generation from local tiers first. Phase 4.3-A shows that the available local trusted-anchor tiers are empty and the remaining identity-like signals are AI-derived. Therefore Phase 4.3-B supersedes broad internal candidate generation from current AI proper-noun tags.

New order of operations:

1. Extract or verify known source URLs, external post IDs, and imported source metadata already present in local records.
2. Under explicit opt-in, use provider-specific source/post lookup when an exact provider identity exists.
3. Under stricter opt-in, use reverse search only when privacy policy permits the request shape.
4. Store redacted provider evidence and cache rows.
5. Create `MediaEntityCandidate` rows only when match confidence policy permits.
6. Require manual review for unresolved, low-confidence, conflicting, or privacy-sensitive cases.
7. Use AI proper-noun tags and local similarity only as supplementary recall or prioritization tools.

No automatic confirmed entity assignments should be created in the first provider pilot.

## Provider / Source Category Evaluation

Public documentation and project code were reviewed only as documentation. No real APIs were called.

| Category | Metadata available | Identity usefulness | Auth / keys | Rate / burst concern | Privacy concern | Reliability and failure modes | Suitability |
|----------|--------------------|---------------------|-------------|----------------------|-----------------|-------------------------------|-------------|
| Danbooru-style exact post lookup | Post ID, source field, rating, file metadata, tag strings split by artist/character/copyright/general/meta on Danbooru-style APIs; existing code already parses Danbooru post JSON. | Strong when the local source URL or post ID exactly identifies a post. Can support character/work/artist candidates via source metadata and tag categories. | Public reads may work for some fields; credentials can change limits and access. Do not use credentials in pilot unless explicitly approved. | Needs request budget and provider-specific throttling. Existing booru import code has only basic retry and is not enough for enrichment scale. | Low if querying by public post ID/source URL only; higher if local filenames/paths or image bytes are sent, which must be blocked. | Post deleted, unavailable, private, schema changes, tag vandalism, category ambiguity, source field missing. | Best first provider category when exact source URL/post ID exists. |
| Gelbooru / booru-style DAPI | Post ID, source, rating, tags, image dimensions, file URL. Existing code parses Gelbooru DAPI, but category data may be weaker or absent in post payloads. | Moderate to strong for exact post lookup; weaker for character/work/artist categorization unless tag category metadata is available or provider-specific rules are added. | Some providers allow unauthenticated DAPI; API keys may be required or recommended depending on site. | DAPI variants differ; strict rate limits and schema quirks should be assumed. | Same as Danbooru: safe only for exact source/post lookup, not local paths or image upload. | Booru forks differ, JSON shape may vary, missing categories, deleted posts, provider-specific TOS differences. | Good second exact-source category after a single provider policy is proven. |
| SauceNAO / reverse image search | Candidate source URLs, similarity score, title/member metadata depending on indexed source. | Useful for source discovery when no source URL exists, but it usually requires sending image-derived data and can produce false positives. | API key and account limits are normally required. Official API page must be verified before implementation; access was not called in this phase. | High burst/rate-limit risk; must respect account quotas, backoff, and stop conditions. | High. Original upload is blocked by default; thumbnails/hash-derived requests need explicit policy. Unknown/non_anime/private content must be blocked. | No match, low similarity, wrong source, quota exceeded, auth failure, provider index gaps. | Not first pilot unless the user explicitly approves image/thumbnail upload policy. |
| trace.moe | Anime screenshot scene/source lookup, often with AniList references and timestamps. | Good for anime screenshots, poor as primary identity provider for illustration/post character identity. | Public API docs exist; usage policy and limits must be respected. | Needs throttling and cache. | Image upload/query risk if using screenshots; not suitable for private unknown content. | No match for illustrations, wrong episode/frame, low confidence, content outside indexed anime. | Supplementary only; likely not suitable as Phase 4.4 primary provider. |
| AniList / work metadata APIs | Anime/manga/work metadata, titles, IDs, relations, characters depending on query shape. | Useful to normalize work titles after a source provider yields a work/source hint; not a reverse image or post identity provider. | GraphQL public API with documented rate limits and terms. | Must obey GraphQL rate limits, cost, and Retry-After-style behavior if present. | Low when querying known public work IDs/names; do not send private local source labels or filenames. | Ambiguous titles, missing artworks, language/title variants, work-only not media-post identity. | Good enrichment supplement after source/post evidence, not first truth source. |
| Pixiv / source URL feasibility | Pixiv source URLs may encode artwork IDs and artist pages. | Exact URL/post ID can be strong provenance if already present locally or in a trusted source field. | Public official API support is not available for this project workflow without explicit review; login/cookies/private APIs are blocked. | Scraping/login automation risk is high and out of scope. | High if using user credentials, private pages, local filenames, or image upload. | Deleted/private/age-gated posts, authentication walls, TOS uncertainty, anti-scraping controls. | Exact source URL parsing may be a future local parser; network calls should be blocked until policy/legal review. |
| IQDB-like reverse image search | Similar-image results and source hints. | Useful for source discovery but not authoritative without source verification. | Lack of a stable official API makes automated use risky. | Unknown, potentially scraping-like if no official API. | High if image upload is required. | No official API stability, false matches, unavailable service, no structured guarantees. | Do not use as automated provider unless an official API and policy are confirmed. |
| Local perceptual hash / embedding retrieval | Local-only near-duplicate or visual similarity clusters. | Helps recall similar local items and prioritize review, but does not prove character/work/artist identity. | No external auth. | Local compute/storage budget only. | Low if local-only and public reports are redacted. | Multi-character scenes, same pose/outfit/style, cross-character artist style, false clusters. | Supplementary recall after source-first pilot; not primary truth and no automatic confirmed assignments. |

References reviewed include Danbooru public API documentation, Gelbooru DAPI help/wiki pages, trace.moe API docs, AniList API docs, Pixiv public policy pages, and OpenCV image hash docs. SauceNAO and IQDB require explicit implementation-time verification of official API/TOS details before any adapter work.

## Privacy Eligibility Policy

Default external eligibility is fail-closed:

| Local content class | Default external provider eligibility | Rationale |
|---------------------|---------------------------------------|-----------|
| `anime` | Eligible only under explicit provider policy and run approval. Prefer source URL/post ID metadata first. Image or thumbnail upload requires separate explicit provider policy. | Anime content is the intended library domain, but provider calls still leak content/query information. |
| `unknown` | Blocked by default. Can be allowed only after manual classification or explicit approval. | Unknown may contain private screenshots, personal content, or non-library material. |
| `non_anime` | Blocked by default. | Outside the target enrichment domain and may include private/personal media. |
| `illustration` | Blocked by default unless the project explicitly defines a safe anime/illustration policy. | The class is ambiguous until a reviewed policy distinguishes safe illustration content from private or non-target material. |

Data-specific privacy defaults:

- Local absolute paths, iCloud source paths, filenames, directory structure, and source labels must never be sent to providers.
- Local paths, source labels, directory structure, secrets, and raw provider payloads must never appear in public reports.
- Original image upload is blocked by default.
- Derived thumbnails, perceptual hashes, embeddings, or cropped image bytes may be sent only under explicit provider-specific policy.
- Unknown/privacy-sensitive content must not be externally queried without manual approval.
- Public reports must aggregate counts and use safe row IDs or redacted labels only.

## External-call Reliability Policy

Future external provider calls must use opt-in provider enablement and per-run controls designed for tens of thousands of images over long-term use.

Required provider enablement:

- `ExternalSource.enabled` must default to false.
- A provider policy must define allowed content classes, allowed query types, auth mode, terms URL, rate limit, privacy policy, cache TTL, and redaction rules.
- Each run must record provider name, policy version, requested query types, approved budget, and safety gates.

Required per-run budget:

- `max_items`
- `max_requests`
- `max_failures`
- `max_consecutive_failures`
- `max_same_reason_failures`
- `max_runtime`

Required rate-limit controls:

- Requests per minute.
- Burst control.
- Provider-specific backoff.
- Respect `Retry-After` or equivalent headers when present.
- No parallelism unless provider policy explicitly allows it.
- Circuit breaker when rate-limit/auth/schema/privacy failures indicate systemic risk.

Required timeout/retry policy:

- Per-request connect timeout and total timeout.
- Limited retries for transient network/5xx/rate-limit cases.
- No retry for privacy_blocked, auth_failed, forbidden, schema_changed unless manually refreshed.
- Negative-cache no_match and privacy_blocked results where appropriate.

Error classes:

- `rate_limited`
- `auth_failed`
- `forbidden`
- `timeout`
- `network_error`
- `provider_5xx`
- `schema_changed`
- `no_match`
- `low_confidence`
- `conflict`
- `privacy_blocked`

Cache-first behavior:

- Look up `ProviderCache` and `NegativeLookupCache` before making a request.
- Do not repeat a request for the same image/source/query hash unless cache expired or the run explicitly requests refresh.
- Store negative results with TTL to avoid hammering providers.
- Record cache hit/miss counts in each run.

Audit/reporting:

- Maintain a per-run local audit log or future ledger artifact.
- Public report must be aggregate and redacted.
- Provider raw responses must not be exposed publicly.
- Any report generation failure is a structural blocker.

## Cache and Provenance Design

Future provider results should map to Phase 4.1 tables without new tables for the first pilot unless implementation evidence proves a gap.

| Table | Future use |
|-------|------------|
| `ExternalSource` | Provider policy row: provider name, enabled flag, auth mode, base URL, rate limit policy, privacy policy, terms URL, notes. |
| `ProviderCache` | Redacted successful/error response cache keyed by provider, query type, and query hash. |
| `NegativeLookupCache` | Redacted no_match, privacy_blocked, low_confidence, or unsupported-query cache with TTL. |
| `EntityEvidence` | Redacted evidence rows linking provider/source result to media, entity, tag, or query hash. |
| `EntityExternalIdentity` | Candidate or verified provider identity link for an entity after policy allows identity creation/linking. |
| `MediaEntityCandidate` | Suggested media/entity candidate only when match confidence policy permits candidate creation. |

Required fields and semantics:

- `query_hash`: stable hash of redacted request shape, not raw local path or raw image bytes.
- `provider`: normalized provider key such as `danbooru`, `gelbooru`, `anilist`, or future approved key.
- `query_type`: examples: `exact_post_id`, `source_url_lookup`, `reverse_search_thumbnail`, `work_metadata_lookup`, `local_phash_cluster`.
- `request_shape_redacted`: provider-safe request class, external post ID, URL host/type, hash algorithm label, or source domain category. No local path/filename.
- `response_json_redacted`: normalized safe subset: external IDs, public URLs, tag/category names, work IDs, scores, status. No raw payload dump unless explicitly redacted.
- `response_status`: `ok`, `no_match`, `low_confidence`, `conflict`, `error`.
- `error_class`: one of the provider error classes above.
- `fetched_at` / `expires_at`: cache lifecycle.
- `score`: provider match score or normalized confidence when recording `EntityEvidence`.
- `source_type`: `trusted_external`, `external`, `imported`, `tag`, `manual`, or provider-specific normalized source class.
- `evidence_type`: `external_lookup`, `reverse_search`, `tag_signal`, `manual`, or `user_confirmation`.
- `summary`: short privacy-redacted evidence statement safe for public/debug UI.
- `privacy_redacted`: must be true for provider-derived evidence.

Public report redaction:

- Include aggregate counts, provider names, error classes, cache hit rates, confidence-tier counts, and privacy-blocked counts.
- Exclude local paths, source labels, filenames, raw source URLs if they could disclose private library structure, raw image hashes if policy treats them as sensitive, raw provider payloads, credentials, and query strings containing secrets.

## Match Confidence Policy

The first provider pilot should create evidence and candidates only; it should not create automatic confirmed assignments.

| Tier | Description | Candidate allowed? | Assignment allowed? | Manual review? | Evidence/cache stored? | Confidence range | Risk notes |
|------|-------------|--------------------|---------------------|----------------|------------------------|------------------|------------|
| `exact_source_url_match` | Local media/source metadata contains a provider URL that resolves to one exact post. | Yes, if provider policy allows. | Not automatic in first pilot. Future policy may allow after review. | Yes for first pilot. | Yes. | 0.90-1.00 | Deleted/private posts, reposts, stale source field. |
| `exact_post_id_match` | Local source or imported metadata includes provider and post ID. | Yes, if provider policy allows. | Not automatic in first pilot. Future policy may allow after review. | Yes for first pilot. | Yes. | 0.90-1.00 | Wrong provider domain, post deleted, source replaced. |
| `source_metadata_character_match` | Exact post metadata includes character/work/artist tags with categories. | Yes, as source-backed candidate. | Not automatic in first pilot. | Yes. | Yes. | 0.80-0.95 | Tag vandalism, ambiguous tags, multi-character media. |
| `multi_provider_agreement` | Two or more approved providers agree on source/entity/work after cache-backed lookups. | Yes. | Future policy only; not first pilot. | Yes. | Yes. | 0.85-0.98 | Provider echo/chaining may not be independent. |
| `high_score_reverse_search` | Approved reverse-search provider returns high score and source/post evidence. | Yes only if privacy policy allows the request type. | No automatic assignment. | Yes. | Yes. | 0.75-0.95 | False positives, crops, edits, reposts, upload privacy. |
| `low_score_reverse_search` | Reverse search returns weak/ambiguous score. | Evidence-only by default. | No. | Optional targeted review only. | Cache/evidence yes if useful. | 0.30-0.74 | Likely false positives; avoid candidate spam. |
| `conflicting_metadata` | Providers or source fields disagree on entity/work/artist. | No automatic candidate by default; evidence-only or conflict candidate if explicitly useful. | No. | Required if pursued. | Yes. | 0.00-0.60 | Conflict handling must avoid contaminating entity graph. |
| `no_match` | Provider returns no result. | No. | No. | No. | Negative cache yes. | 0.00 | Avoid repeat queries until TTL expires. |
| `privacy_blocked` | Local policy blocks the query. | No. | No. | Manual approval required to override. | Negative cache or audit entry yes. | N/A | Treat as safety success, not provider failure. |

## Phase 4.4 One-provider Pilot Design

Recommended first pilot: exact-source/post lookup against one booru-style provider, preferably Danbooru-style if exact source URLs or post IDs are available in current media/source metadata. If current media lack usable source URLs/post IDs, Phase 4.4 should first be a source-inventory dry-run that counts eligible exact-source opportunities without provider calls.

Why this category first:

- It can avoid image upload.
- It maps cleanly to existing Phase 4.1 provenance/cache tables.
- The existing codebase already has booru post clients, but they are import clients and should not be reused as enrichment adapters without new policy/rate/cache wrappers.
- Exact post lookup provides stronger evidence than AI proper-noun tags or visual similarity.

Sample selection:

- `content_class=anime` only.
- Exclude `unknown`, `non_anime`, and `illustration` by default.
- Small batch size: recommended `max_items=25` for the first live pilot.
- Optional source-label filter to a known validated import label.
- Prioritize media with known source URL/post ID first.
- AI proper-noun weak-signal clusters may prioritize review order only; they must not be used as truth.

Privacy mode:

- Source URL/post ID first.
- No original image upload.
- No thumbnail upload by default.
- No local paths, filenames, source labels, or directory structures in provider requests.
- No reverse image search until explicitly approved.

Initial budget:

- `max_items=25`
- `max_requests=50`
- `max_failures=5`
- `max_consecutive_failures=3`
- `max_same_reason_failures=5`
- `max_runtime=10 minutes`
- Provider rate: conservative default `30 requests/minute` or lower if provider docs require lower.
- Burst: `1` unless provider policy explicitly allows a burst.

Stopping conditions:

- Identity/worktree/server/DB mismatch if a future runner uses runtime state.
- Provider disabled or missing policy.
- Privacy gate blocks selected set above expected threshold.
- `auth_failed`, `forbidden`, or `schema_changed`.
- Rate-limit failures exceed budget or provider asks to slow/stop.
- Consecutive or same-reason failure budget exceeded.
- Public report redaction failure.
- Any unexpected DB/source/app-storage mutation.

Success metrics:

- `attempted`
- `eligible`
- `privacy_blocked_count`
- `cache_hits`
- `cache_misses`
- `requests_sent`
- `no_match`
- `high_confidence_match`
- `low_confidence_match`
- `conflict_count`
- `provider_error_count`
- `average_requests_per_item`
- `estimated_time_for_1k`
- `estimated_time_for_10k`
- `manual_review_burden_estimate`

Expected future writes if implementation is later approved:

- `ProviderCache` rows for cacheable provider responses.
- `NegativeLookupCache` rows for no_match/privacy_blocked/low_confidence failures as policy allows.
- `EntityEvidence` rows for source-backed external evidence.
- `MediaEntityCandidate` rows only for confidence tiers that allow candidates.
- No confirmed `MediaEntityAssignment` rows in the first pilot.

Phase 3.9 must precede scaling:

- Before any 5k/10k import.
- Before broad provider enrichment.
- Before full-library request scheduling.
- Before large cache population where per-item final state must remain observable.

## Role of Manual Seeds and Local Retrieval

Manual seeds:

- Useful as T0 anchors and validation examples.
- Useful for evaluating provider result quality and candidate precision.
- Not the primary path to full coverage.
- Should produce durable assignments, aliases, translations, rejection reasons, and evidence that future automation can consume.

Seed-based local retrieval / clustering:

- Supplementary recall tool only.
- Not primary truth.
- Useful after source-first pilot to find visually similar local media for targeted review.
- Must not create automatic confirmed assignments.
- Whole-image embeddings risk confusing multi-character images, style, pose, clothing, and artist motifs.
- Global similarity graph remains deferred until larger validated scale and stronger ingestion ledger discipline.

## Updated Short-term Roadmap

Recommended near-term order:

1. Merge Phase 4.3-B if accepted as source-first policy/design.
2. Phase 4.4-A: exact-source inventory dry-run. Count current media with provider-recognizable source URLs/post IDs and privacy eligibility. No provider calls if source coverage is unknown.
3. Phase 4.4-B: one-provider exact post/source lookup pilot with cache/evidence/candidate-only writes, no confirmed assignments, and strict redacted reporting.
4. Phase 3.9: production Ingestion Run Ledger / Source Item State Ledger before broad provider enrichment, 5k/10k scale, or full-library scheduling.
5. Later: reverse-search pilot only after explicit image/thumbnail/hash upload policy.
6. Later: local retrieval/clustering as supplementary recall after source-backed evidence exists.

## Public Documentation Research Notes

Public documentation reviewed:

- Danbooru API help and post API documentation: `https://danbooru.donmai.us/wiki_pages/help:api`, `https://danbooru.donmai.us/wiki_pages/api:posts`
- Gelbooru DAPI help and API wiki: `https://gelbooru.com/index.php?page=help&topic=dapi`, `https://gelbooru.com/index.php?page=wiki&s=view&id=18780`
- SauceNAO API documentation entry point: `https://saucenao.com/user.php?page=search-api`
- trace.moe API docs: `https://soruly.github.io/trace.moe-api/`
- AniList API v2 docs and rate limit guidance: `https://anilist.gitbook.io/anilist-apiv2-docs/`, `https://anilist.gitbook.io/anilist-apiv2-docs/docs/guide/rate-limiting`
- Pixiv public policy entry point: `https://policies.pixiv.net/en.html`
- OpenCV image hash docs: `https://docs.opencv.org/4.x/d4/d93/group__img__hash.html`

Research caveats:

- Some provider documentation pages can be access-controlled, dynamic, or unavailable to automated browsing. Implementation must re-verify official provider API/TOS/rate-limit details before adapter work.
- No provider APIs were called.
- No user images, source URLs, credentials, or local filenames were submitted.

## No-runtime-mutation Confirmation

This stage performed only documentation and public-doc research.

- No external provider API calls.
- No authenticated provider calls.
- No scraping.
- No reverse image search execution.
- No image upload.
- No crawler.
- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No staging copy.
- No entity candidate creation.
- No entity assignment creation.
- No entity auto-creation.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No Entity Resolver execution.
- No similarity/clustering.
- No production ingestion ledger implementation.
- No admin UI rewrite.
- No push to main.
- No merge.

## Artifact Lifecycle

- This report and its summary JSON are public report / handoff documentation.
- `docs/current-handoff.md` and `docs/project-roadmap.md` updates are long-term roadmap/handoff documentation.
- No scripts, reusable tooling, provider adapters, runtime code, local reports, or one-off artifacts are introduced.

## Engineering Judgment

The source-first route supersedes internal AI-derived candidate generation for the current library state. Phase 4.3-A found zero trusted anchors and 2131 AI-only proper-noun rows, so generating broad candidates from those signals would move weak model guesses into the entity system. The safer path is to collect source-backed evidence first, then create source-backed candidates under explicit policy.

The external provider pilot is ready to design next, but not ready to implement as a broad enrichment run. A small exact-source inventory/pilot is appropriate if it remains opt-in, cache-first, redacted, and candidate-only. Reverse search should wait for explicit image/thumbnail/hash privacy approval.

Phase 3.9 should precede broad provider work, 5k/10k scale, and full-library scheduling because provider enrichment creates the same long-run observability problem as ingestion: every item needs a final state, failure reason, cache status, retry/defer state, and public/private artifact split.
