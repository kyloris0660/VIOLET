# Phase 4.4-P2R - Pixiv Authenticated Metadata Route Scouting and Adapter Design

## Purpose

Phase 4.4-P2R is a route-scouting and adapter-design stage. It is not Phase
4.4-P2 persistence. It writes no database rows, makes no authenticated Pixiv
requests, imports no cookies or refresh tokens, downloads no images, and does
not continue polishing the PR #86 public-page / preview runner.

The route decision is needed because PR #86 reached public Pixiv pages and some
preview candidates, but the metadata result stayed `preview_only`, and the
reviewer findings kept exposing public HTML / preview edge cases. That is enough
evidence to stop treating unauthenticated public HTML previews as the durable
Pixiv metadata route.

## Project Context Read

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/test-workflow.md`
- `docs/reports/phase-4.4p0-pixiv-filename-source-prior-auto-verification.md`
- `docs/reports/phase-4.4p0-pixiv-filename-source-prior-auto-verification-summary.json`
- PR #86 public report and summary from branch `codex/phase44p1-pixiv-live-reference-metadata-pilot`
- `backend/app/models.py`
- `backend/app/enums.py`
- `backend/app/database.py`
- `backend/app/services/provider_evidence_contract.py`
- `backend/app/services/provider_evidence_persistence_service.py`

Relevant current constraints:

- Pixiv filename tokens cover `555 / 1989` media records in DB/app-managed
  metadata (`27.9%`), with `551` distinct candidate work IDs.
- Filename source priors are local deterministic hints only. They are not
  provider results, confirmed evidence, confirmed assignments, or trusted
  entities.
- Provider/evidence persistence is already structured around redacted
  `ProviderCache`, `EntityEvidence`, and suggestion-only
  `MediaEntityCandidate` writes, but this P2R stage does not write them.
- Public contract payloads must not leak local paths, secrets, raw image bytes,
  exact private mappings, or secret-like tokens.

## Prior-Art Sources

### gallery-dl

Sources inspected:

- <https://github.com/mikf/gallery-dl>
- <https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/pixiv.py>
- <https://github.com/mikf/gallery-dl/blob/master/docs/configuration.rst>
- <https://github.com/mikf/gallery-dl/blob/master/docs/options.md>

Findings:

- License: GPL-2.0. V.I.O.L.E.T. must not copy gallery-dl implementation code
  into this MIT-derived project. Conceptual reference is acceptable; invoking a
  user-installed executable is a separate design boundary that still needs
  review.
- Pixiv identity model uses the common `{id}_p{num}` filename convention and
  treats a work as `id` plus zero-based page `num`.
- The extractor expands multi-page works using `meta_pages` or `pageCount` and
  per-page URLs.
- The app API path exposes structured fields such as title, user, tags,
  `page_count`, image URLs, comments/bookmark metadata when configured, and
  optional extended user metadata.
- Auth is refresh-token based for the app API. The documented config key is
  `extractor.pixiv.refresh-token`; gallery-dl can obtain one through
  `gallery-dl oauth:pixiv`.
- gallery-dl supports cookie sources, request sleeps, archive databases, JSON
  metadata postprocessors, `--dump-json`, `--no-download`, and
  `--write-metadata`.
- `extractor.*.download=false` says media files are not downloaded while other
  functions/postprocessors still execute. This is promising for a metadata-only
  pilot, but a future pilot must prove the exact command does not fetch image
  bytes.

Assessment:

gallery-dl is the strongest mature implementation reference and the strongest
near-term adapter candidate, especially if V.I.O.L.E.T. consumes JSON generated
by a user-installed gallery-dl rather than reimplementing Pixiv protocol logic.

### pixivpy

Sources inspected:

- <https://github.com/upbit/pixivpy>
- <https://github.com/upbit/pixivpy/blob/master/pixivpy3/aapi.py>
- <https://github.com/upbit/pixivpy/blob/master/pixivpy3/api.py>
- <https://github.com/upbit/pixivpy/blob/master/pixivpy3/models.py>

Findings:

- License: Unlicense.
- `AppPixivAPI.illust_detail(illust_id, req_auth=True)` maps directly to a
  structured Pixiv app API detail route.
- Requests normally use mobile app headers and token-based authorization.
- Auth can use a refresh token to obtain an access token.
- The `IllustrationInfo` model includes `id`, `title`, `type`, `image_urls`,
  `caption`, `user`, `tags`, `page_count`, dimensions, `meta_single_page`, and
  `meta_pages`.

Assessment:

pixivpy has the cleanest structured metadata shape, but it means adopting or
mirroring an unofficial authenticated app API route. It is a useful conceptual
model and possible future optional adapter, but it should not be the first
V.I.O.L.E.T. route unless gallery-dl JSON import cannot satisfy the pilot.

### PixivUtil2

Sources inspected:

- <https://github.com/Nandaka/PixivUtil2>
- <https://github.com/Nandaka/PixivUtil2/blob/master/model/PixivImage.py>
- <https://github.com/Nandaka/PixivUtil2/blob/master/common/PixivHelper.py>
- <https://github.com/Nandaka/PixivUtil2/blob/master/README.md>

Findings:

- License: BSD-2-Clause.
- The model parses Pixiv AJAX payloads and records title, caption, tags,
  artist/user fields, `pageCount`, original/regular URLs, translated
  title/caption, and page-derived URL expansion.
- The project is downloader-oriented and expects login/cookie flows such as
  `PHPSESSID`, OAuth refresh-token state, and browser cookie copying.
- Request helpers use a `Referer` header. That is useful as prior art, but it is
  not suitable for V.I.O.L.E.T.'s default route because the current policy
  forbids hotlink / Referer bypass.

Assessment:

PixivUtil2 confirms the mature field model and page-index behavior, but its
downloader and cookie-centric operational model is a poor direct fit for the
next V.I.O.L.E.T. pilot.

### Official Pixiv Surface

Sources inspected:

- <https://www.pixiv.net/terms/?lang=en>
- <https://policies.pixiv.net/en.html>
- <https://www.pixiv.net/robots.txt>
- bounded search of Pixiv Help / public API references

Findings:

- No official public metadata API for artwork ID to structured tags/artist/page
  metadata was found in this bounded research pass.
- Official terms and individual-service terms exist; this report does not make
  a legal/TOS conclusion.
- `robots.txt` disallows selected Pixiv paths and explicitly blocks several AI
  crawler user agents. This is not a substitute for legal review, but it
  reinforces that broad crawling or automated scraping should not be treated as
  a safe default.

Assessment:

There is no confirmed official public API route to replace the public-page
runner. Any authenticated Pixiv route has policy uncertainty and must remain
opt-in, bounded, rate-limited, redacted, and separately approved.

## Candidate Route Evaluation

Scores use 1 low / 5 high. For risk columns, 5 means lower risk.

| Route | Metadata richness | Reliability | Implementation cost | Maintenance cost | Privacy/secret safety | TOS/policy risk | Contract fit | Small pilot | Broad run | Avoid originals | Metadata-only by work ID | Public redaction |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A. Internal V.I.O.L.E.T. authenticated adapter | 5 | 4 | 1 | 1 | 2 | 2 | 4 | 3 | 2 | 4 | 5 | 4 |
| B. External gallery-dl adapter | 5 | 5 | 3 | 4 | 3 | 3 | 5 | 5 | 3 | 4 | 5 | 5 |
| C. pixivpy-style metadata adapter | 5 | 4 | 3 | 2 | 2 | 2 | 4 | 3 | 2 | 5 | 5 | 4 |
| D. Manual local metadata import | 4 | 4 | 4 | 5 | 5 | 4 | 5 | 5 | 2 | 5 | 4 | 5 |
| E. Continue public-page preview route | 2 | 1 | 2 | 1 | 4 | 2 | 2 | 2 | 1 | 3 | 2 | 4 |

### A. Internal V.I.O.L.E.T. Pixiv Authenticated Adapter

Pros:

- Best control over schema, redaction, query hashing, rate limits, and future
  provider-neutral contract mapping.
- Can look up a specific Pixiv work ID and read structured tags, artist, title,
  page count, and per-page metadata.

Cons:

- Highest maintenance burden. V.I.O.L.E.T. would own Pixiv protocol drift,
  authentication, token refresh, throttling, block handling, and secret storage.
- Requires storing or accessing user credentials/secrets locally.
- Uses an unofficial authenticated surface unless Pixiv publishes an official
  route later.

Decision:

Do not choose first. Keep as a later fallback only if gallery-dl JSON import or
external gallery-dl invocation cannot support reliable metadata-only lookup.

### B. External gallery-dl Adapter

Pros:

- Mature Pixiv implementation with work/page convention, multi-page support,
  metadata export, request pacing, archives, and auth handling.
- V.I.O.L.E.T. can keep a process boundary and consume JSON instead of copying
  GPL implementation code.
- Good fit for a provider-neutral adapter: normalize gallery-dl JSON into a
  local `PixivMetadataRecord`, then later into contract DTOs after validation.

Cons:

- Adds an external executable dependency and version surface.
- GPL implications must be kept at the invocation boundary; no code copying.
- User must configure gallery-dl credentials locally.
- Need to pin/record gallery-dl version and validate output schema drift.

Decision:

Best medium-term route if V.I.O.L.E.T. needs to run lookups itself. The next
pilot should still start with JSON import to avoid pulling secrets into the app.

### C. pixivpy-Style Metadata Adapter

Pros:

- Structured metadata model maps cleanly to tags, user, page count, and
  `meta_pages`.
- Metadata-only lookup by work ID is conceptually direct.

Cons:

- Unofficial authenticated app API route and token handling become part of
  V.I.O.L.E.T.'s operational burden.
- Dependency and API drift risk live inside the app if adopted.

Decision:

Keep as a fallback or comparison route, not the preferred first pilot.

### D. Manual Local Metadata Import

Pros:

- Safest near-term route. V.I.O.L.E.T. receives local JSON only and never sees
  Pixiv credentials.
- No live Pixiv network code in the app.
- Easy to validate schema, redaction, page-index mapping, and provider-neutral
  compatibility without DB writes.

Cons:

- Extra manual export step.
- Does not by itself prove a fully automated adapter lifecycle.

Decision:

Preferred next pilot. Use a tiny manually exported gallery-dl JSON set, validate
schema and mapping locally, generate public/private reports, and keep all exact
IDs and paths private.

### E. Continue Public-Page Preview Route

Pros:

- Does not require Pixiv credentials.
- Can remain useful as a diagnostic or fallback signal when a public page is
  already available.

Cons:

- PR #86 showed metadata stayed `preview_only` and the route is brittle around
  public HTML/previews, redirects, preview hosts, and page-index/crop mismatch.
- Does not provide durable artist/tag/page-count metadata.

Decision:

Do not continue polishing it as the main route. PR #86 should not be merged as a
future metadata-route foundation.

## Recommendation

Recommended route:

1. Close or supersede PR #86 after this P2R design PR is reviewed. Do not merge
   the PR #86 public-page runner as the Pixiv metadata route. It may remain open
   briefly as diagnostic evidence, but it should not receive more public-page
   preview fixes unless a privacy/report-truthfulness issue must be preserved
   before closure.
2. Run the next pilot as a manual gallery-dl JSON import pilot: the user or a
   separately approved local command produces metadata-only JSON for a tiny
   set of Pixiv work IDs; V.I.O.L.E.T. validates and normalizes local JSON only.
3. If the JSON import pilot succeeds, promote to an external gallery-dl adapter
   design. V.I.O.L.E.T. invokes user-installed gallery-dl with a bounded
   metadata-only command, writes output to ignored local artifacts, and imports
   only redacted normalized metadata into public reports.
4. Implement an internal Pixiv adapter or pixivpy-style adapter only if the
   external gallery-dl boundary proves insufficient.

## Credential and Secret Requirements

For manual JSON import:

- V.I.O.L.E.T. needs no Pixiv credential.
- User-generated JSON must not include cookies, refresh tokens, Authorization
  headers, browser profile paths, or local credential paths.

For external gallery-dl adapter:

- User may need a Pixiv refresh token, usually obtained through
  `gallery-dl oauth:pixiv`, or cookies depending on the chosen gallery-dl
  configuration.
- V.I.O.L.E.T. should not store raw Pixiv secrets in the DB or reports.
- Preferred secret model: user-managed gallery-dl config outside the repo;
  V.I.O.L.E.T. receives only a named profile/config path that is itself kept
  private and redacted.
- If V.I.O.L.E.T. later stores secrets directly, use OS-backed local secret
  storage such as Windows Credential Manager / keyring or an encrypted local
  file outside the repo. `.env` is acceptable only as a local operator fallback
  and must never be committed or echoed.

For internal/pixivpy-style adapter:

- Requires refresh token and generated access token handling.
- Must redact token material from logs, exceptions, reports, command lines,
  PRs, `ProviderCache`, and local sheets.

## Metadata-Only Feasibility

Metadata-only is feasible in principle:

- gallery-dl documents `--dump-json`, `--no-download`, `--write-metadata`, and
  metadata postprocessor modes.
- pixivpy-style `illust_detail` returns structured JSON without requiring image
  bytes to be downloaded by the caller.

However, a future pilot must prove the exact command/runtime behavior:

- no original image bytes written,
- no preview/original image fetch unless explicitly approved,
- all output under ignored local artifact paths,
- exact Pixiv IDs and URL mappings private,
- request counts and failures recorded,
- gallery-dl version and config mode recorded,
- no broad run or retry storm.

## Correspondence Strategy

Minimum correspondence for a metadata-only pilot:

1. Extract local filename prior: `work_id` plus zero-based `page_index`.
2. Import Pixiv metadata for the requested `work_id`.
3. Verify `page_count > page_index`.
4. Verify per-page metadata exists for that page when available.
5. Record title/user/tags/page_count as source metadata, not confirmed entity
   truth.
6. Keep the row as `metadata_matched_work_and_page_unverified_visual` unless a
   separate visual gate or manual validation confirms the local image.

For future persistence, metadata-only matching should not automatically create a
confirmed assignment. A later DB-write phase can persist a local source hint or
provider metadata candidate only after it defines:

- required page-index checks,
- optional low-resolution reference preview policy,
- manual validation or auto-correspondence threshold,
- evidence strength mapping,
- redacted query hash,
- rollback/idempotency behavior.

## Minimal Safe Next Pilot

Recommended next pilot name:

`Phase 4.4-P2R-F1 - gallery-dl JSON metadata import pilot`

Scope:

- Input: 5 to 10 private Pixiv work IDs from the existing filename-prior sample.
- Network: none inside V.I.O.L.E.T. for the first pilot.
- User action: produce local gallery-dl JSON with a documented metadata-only
  command or provide pre-existing JSON exported outside V.I.O.L.E.T.
- V.I.O.L.E.T. action: validate JSON schema, normalize records, verify
  work/page ID consistency, generate public-safe report and private ignored
  sheet.
- DB: no writes.
- Images: no downloads and no original reads.
- Output: route readiness decision for an external gallery-dl adapter.

Acceptance criteria:

- JSON records include work ID, title, user/artist, tags, page count, and
  per-page metadata or enough page information to validate `page_index`.
- No credential-like value appears in input/output reports.
- Public report excludes exact IDs, exact URLs, local paths, and filenames.
- Summary states whether external gallery-dl invocation should be designed next.

## Forbidden Until Later Phase

Still forbidden until a later explicitly approved phase:

- DB write or migration.
- `LocalSourceHint` rows.
- `ProviderCache` rows.
- `EntityEvidence` rows.
- `MediaEntityCandidate` rows.
- `NegativeLookupCache` rows.
- confirmed `MediaEntityAssignment`.
- automatic `Entity` or `EntityExternalIdentity`.
- `media_tags` mutation.
- `TagTranslation` mutation or localization execution.
- Entity Resolver or similarity/clustering.
- source/iCloud mutation or app-managed storage mutation.
- broad Pixiv extraction.
- authenticated Pixiv request from V.I.O.L.E.T. without explicit run approval.
- image or original download.

## Broad-Run Prerequisites

Before any broad or repeated Pixiv metadata run:

- provider policy and TOS uncertainty statement,
- user consent for credentialed Pixiv access,
- exact credential storage policy,
- gallery-dl or adapter version pinning/audit,
- metadata-only no-image-download proof,
- run ledger with per-item state/failure/retry/defer fields,
- request budget and rate-limit policy,
- cache/negative-cache policy,
- public/private artifact split,
- redaction tests for IDs, URLs, paths, and secrets,
- rollback/idempotency plan for DB writes,
- manual validation / auto-correspondence acceptance policy,
- explicit DB-write approval if persistence is requested.

## Answers to Required Route Questions

1. PR #86 should not be merged as the Pixiv metadata route. Leave it open only
   as short-lived diagnostic evidence or close it as superseded by P2R after
   this design is reviewed.
2. The best next pilot route is manual gallery-dl JSON metadata import.
3. V.I.O.L.E.T. should import gallery-dl JSON first, then consider invoking
   gallery-dl externally. It should not implement its own Pixiv adapter first.
4. Future credentialed routes likely need a Pixiv refresh token or Pixiv cookies
   managed by gallery-dl; V.I.O.L.E.T. should not need credentials for manual
   JSON import.
5. Secrets should live outside the repo and outside DB rows, preferably in
   gallery-dl's user-managed config or OS-backed local secret storage. Public
   artifacts must never include them.
6. Metadata-only is feasible in principle via gallery-dl JSON / no-download
   modes or app API detail responses, but the exact future command must be
   validated before relying on it.
7. Local image correspondence should start with work ID and page index, then
   require page-count/page metadata consistency, and only become verified after
   manual validation or a separately approved low-resolution visual gate.
8. Minimal next pilot: 5 to 10 item local JSON import, no live Pixiv calls inside
   V.I.O.L.E.T., no DB writes, no images, private exact mapping artifacts only.
9. All DB writes remain forbidden: LocalSourceHint, ProviderCache,
   EntityEvidence, MediaEntityCandidate, confirmed assignments, Entity rows,
   media_tags, TagTranslation, and NegativeLookupCache.
10. Before broad run, document provider policy, credentials, rate limits, run
    ledger, metadata-only proof, redaction, cache/audit, failure handling, and
    DB persistence approval gates.

## Safety Confirmation

- db_write: `False`
- db_migration: `False`
- local_source_hint_write: `False`
- provider_cache_write: `False`
- entity_evidence_write: `False`
- media_entity_candidate_write: `False`
- negative_lookup_cache_write: `False`
- confirmed_assignment: `False`
- automatic_entity_creation: `False`
- media_tags_mutation: `False`
- tag_translation_mutation: `False`
- localization_execution: `False`
- entity_resolver: `False`
- broad_similarity_or_clustering: `False`
- pixiv_login: `False`
- cookie_import: `False`
- refresh_token_use: `False`
- authenticated_pixiv_request: `False`
- gallery_dl_live_run_with_credentials: `False`
- image_download: `False`
- original_image_download: `False`
- source_or_icloud_mutation: `False`
- app_managed_storage_mutation: `False`
- push_main: `False`
- merge: `False`
