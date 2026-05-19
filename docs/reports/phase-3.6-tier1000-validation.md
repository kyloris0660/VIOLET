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
