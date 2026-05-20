# Phase 3.6 Tier-1000 Validation

## Database Backup

- Backup mode: `pg_dump -Fc`
- Backup artifact: `phase-3.6-tier1000-before-20260519-203857.dump`
- Backup size: `223388` bytes
- Backup path: redacted; stored under gitignored `backups/`

## AI Tagging Result

- Target source label: `violet:tier1000:phase3.5`
- Target media count: `995`
- Processed: `995`
- Failed: `0`
- AI job IDs: `11` through `50`
- Confirmed AI tag associations added: `41416`
- AI suggestion associations added: `11938`
- `media_tags` delta from AI: `53354`
- Tag row delta: `1301`
- Media with AI tags delta: `995`
- Ignored low-confidence predictions: `10750365`
- Classification job delta during AI: `0`
- Translation job delta during AI: `0`
- Auto localization status: `skipped_auto_localization_disabled`

## Localization Result

- Controlled categories: `general`, `meta`
- LLM provider: `openai_compatible`
- LLM host: `api.openai.com`
- LLM model: `gpt-4.1-mini`
- API key: configured yes/no only in JSON report; value not recorded
- Candidates: `1196`
- Translated: `1196`
- Failed: `0`
- Remaining target visual missing translations: `0`
- Proper-noun candidates skipped: `102`
- Translation job ID: `15`
- Translation count delta: `1196`
- Classification job delta during localization: `0`

## Real Dev DB App Smoke

This validates the actual imported and AI-tagged Tier-1000 data in `blombooru`. It is separate from the controlled test-env gate.

- Server environment: development DB, app storage, background/auto translation disabled
- Server identity: PASS
- Server PID: `32172`
- Port: `8012`
- Stop result: exact PID stopped; port released

API checks:

- `GET /api/media?limit=20`: `200`, total `995`
- Imported media visible with tags: media ID `1706`
- `GET /api/media/1706`: `200`
- AI provenance visible on detail API: `79` `ai_wd` rows for sampled media
- `GET /api/media/1706/file`: `200`
- `GET /api/media/1706/thumbnail`: `200`
- `GET /api/tags/translations/batch`: `200`
- Canonical search `hetero`: `66` hits
- Localized search `异性恋`: `66` hits
- `GET /api/admin/ai-tags/review?source=ai_wd`: `200`, total `11938`
- Tag translation worker status: disabled/not running
- Entity Resolver status: `enabled=false`

Playwright Edge checks:

- Gallery page loaded with media links: PASS
- Media detail page loaded: PASS
- Localized tag text visible on detail page: PASS
- AI Tag Review page loaded rows: `50`

## Controlled Test-Env App Smoke

This satisfies the AGENTS.md controlled server rule and does not validate real imported dev data.

- Server environment: `VIOLET_ENV=test`
- DB: `blombooru_test`
- Storage: dedicated test storage label
- Server identity: PASS
- Server PID: `34980`
- Port: `8012`
- Stop result: exact PID stopped; port released

Checks:

- `GET /api/media?limit=5`: `200`, total `112`
- Playwright Edge gallery load: `200`
- Gallery body text length: `267`

## Safety Confirmation

- No source/iCloud/staging mutation
- No media import/copy
- No content classification run
- No Entity Resolver run
- No cleanup/reset/drop/truncate
- No API keys or auth header values recorded
- No push to `main`
- No merge

## Reviewer Closeout Hardening

Implemented after the original Phase 3.6 run; no full AI tagging or full localization rerun was performed.

- AI launch now checks `blombooru_ai_tag_jobs` for DB-backed active statuses: `pending`, `running`, `cancelling`.
- Historical `completed`, `failed`, and `cancelled` AI jobs do not block new runs.
- If classification jobs or translation jobs are created during the AI tagging portion, the runner now marks the report failed and exits nonzero.
- Localization now exits nonzero when the LLM provider is unavailable with candidates, or when any localization candidate fails.
- If translation save/finalization fails after a `TagTranslationJob` row exists, the runner rolls back the current transaction, marks that job `failed`, sets `finished_at`, records a sanitized error, writes the report, and exits nonzero.
- Controlled localization now caps one run to `min(--max-items, TAG_TRANSLATION_BATCH_MAX_ITEMS)` before selecting candidates, and reports `requested_max_items`, `effective_max_items`, `configured_batch_max`, and `candidates_selected`.
- If an AI chunk job fails, the runner writes a failure report before aborting, including completed prior jobs, the failed job entry, the before baseline, and best-effort after/safety deltas.
- `localize --max-items` and `ai-tag --limit` now reject `0` and negative values during argument parsing, before any DB write path can run.

## Manual Inspection Questions

### AI Tagging Admin Panel

The current Admin AI tagging panel is expected to show job creation and job history, not an aggregate dashboard like the tag localization panel. Aggregate Phase 3.6 metrics are currently available in the reports and APIs, including:

- `target_media_with_ai_tags`: `995`
- confirmed AI associations: `41416`
- AI suggestions: `11938`
- media-tag association delta: `53354`

Recommended future task: add a small read-only AI tagging aggregate summary to Admin, showing total scoped media, tagged media, untagged media, confirmed AI tags, and suggestions. This PR intentionally does not expand UI scope.

### Tag Localization Pending Count

Read-only DB validation after closeout:

- total tags: `3267`
- non-rejected `zh-CN` translations: `3162`
- missing translations excluding static dictionary: `106`
- missing breakdown: `character = 106`
- target Phase 3.6 visual/general/meta missing translations: `0`
- target Phase 3.6 proper-noun missing translations: `102` (`character = 102`)
- review pending translations: `91` (`general = 14`, `character = 77`, source `llm`, status `translated`)

The remaining pending count is expected because Phase 3.6 intentionally skipped proper nouns and did not run Entity Alias Resolver. No scoped localization fix is needed for visual/general/meta tags.

### Content Classification Panel

The classification panel showing `995` total, `0` classified, `995` unclassified is expected for Phase 3.6.

- target Phase 3.6 media count: `995`
- target classified count: `0`
- target unclassified count: `995`
- classification job delta during AI tagging: `0`
- classification job statuses in DB: historical `completed = 4`, no new Phase 3.6 classification run

Content classification was intentionally not triggered in Phase 3.6.
