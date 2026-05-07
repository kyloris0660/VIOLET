# Entity Alias Resolver — Phase 2.3e

## Overview

The entity alias resolver handles proper-noun tags (character, copyright, artist) separately from visual tag translation. These tags need **alias resolution** (finding the established Chinese name), not **translation** (converting meaning).

| Tag Type | Example | Task |
|----------|---------|------|
| Visual (general) | `blue_eyes` → `蓝眼睛` | Translation |
| Character | `hatsune_miku` → `初音未来` | Alias resolution |
| Copyright | `bocchi_the_rock!` → `孤独摇滚!` | Alias resolution |
| Artist | `pixiv_username` → (usually kept as-is) | Alias resolution |

## Why Separate from Translation?

Visual tags have a deterministic translation: `blue_eyes` means "blue eyes" in any language. Proper-noun tags do not — `rem_(re:zero)` has an established Chinese name `蕾姆`, but there is no linguistic rule to derive it. The LLM must **recall** the name, not **invent** one.

A wrong entity alias (e.g., inventing a Chinese name for an obscure artist) is worse than no alias at all, because it would pollute search results. The entity resolver therefore:

1. Uses a dedicated LLM prompt that forbids name invention
2. Marks uncertain results as `needs_review=true`
3. Excludes unreviewed aliases from the Chinese search cache

## Architecture

```
Admin triggers "Resolve Entities"  (POST /entity/resolve)
         ↓
  list_pending_proper_nouns(db)
         ↓
  await resolve_entity_aliases(tags)  ← async, dedicated LLM prompt
         ↓                               chunks by ENTITY_ALIAS_BATCH_SIZE
  upsert_translation(db, ...)  ← source=llm, provider=entity_resolver
         ↓
  Trust policy gate (single-pass filter in _build_alias_cache):
    - needs_review=true AND source not in {manual, static} → excluded from search cache
    - needs_review=false OR trusted source → included in search cache
```

## Components

| Component | Path | Role |
|-----------|------|------|
| Entity resolver service | `backend/app/services/entity_alias_resolver.py` | Core logic + LLM prompt |
| Admin API endpoints | `backend/app/routes/admin/tag_localization.py` | `/entity/status`, `/entity/pending`, `/entity/resolve` |
| Search trust policy | `backend/app/utils/search_parser.py` | Excludes untrusted aliases |
| Background worker | `backend/app/services/tag_translation_worker.py` | Category filtering (skips proper-nouns) |
| Admin UI | `frontend/templates/admin.html` | Entity Alias Resolver section |
| Admin JS | `frontend/static/js/admin.js` | `loadEntityStatus()`, `resolveEntities()`, `loadEntityPending()` |
| E2E Tests | `tests/e2e/entity-alias-resolver.spec.ts` | 10 smoke + 3 real E2E tests |

## LLM Prompt Design

The entity resolver uses `ENTITY_SYSTEM_PROMPT` which differs from the general translation prompt:

- Instructs the LLM to find ESTABLISHED Chinese names, not invent translations
- If uncertain, return the original tag name with `needs_review=true`
- Character names: use established community names (e.g., `hatsune_miku` → `初音未来`)
- Copyright/series: use official or widely-used Chinese titles
- Artist names: almost always keep original, only alias if widely known
- Lower temperature (0.2) for more deterministic output
- Smaller chunk size (configurable via `ENTITY_ALIAS_BATCH_SIZE`, default 10) for better per-entity attention
- Fully async — awaited directly from FastAPI route, no nested event loop

## Trust Policy

The search alias cache in `search_parser.py` enforces a trust policy for proper-noun tags using a single-pass filter during cache construction:

```
proper_noun_cats = {"character", "copyright", "artist"}
trusted_sources = {"manual", "static"}

For each translation row (single loop):
  if category in proper_noun_cats
     AND source not in trusted_sources
     AND needs_review == true:
       → SKIP (excluded from search)
  else:
       → include in cache (with source-priority dedup)
```

This means:
- **Manual** translations are always trusted
- **Static dictionary** translations are always trusted
- **LLM entity resolver** translations are excluded until an admin approves them
- General/meta tags from LLM are trusted (same as before)

## API Endpoints

All endpoints require admin authentication.

### GET `/api/admin/tag-localization/entity/status`

Returns resolver status and statistics.

**Response:**
```json
{
  "enabled": true,
  "llm_available": true,
  "total_proper_noun_tags": 150,
  "resolved": 45,
  "needs_review": 30,
  "no_translation": 75,
  "config": {
    "batch_size": 10,
    "max_per_run": 50
  }
}
```

### GET `/api/admin/tag-localization/entity/pending?limit=100`

Lists proper-noun tags needing resolution, sorted by post count (most popular first).

**Response:**
```json
[
  {
    "tag_id": 42,
    "canonical_name": "hatsune_miku",
    "category": "character",
    "post_count": 150,
    "has_unreviewed_llm": false,
    "current_display": null
  }
]
```

### POST `/api/admin/tag-localization/entity/resolve?limit=50`

Runs entity alias resolution for pending tags. Requires `ENTITY_ALIAS_RESOLVER_ENABLED=true`.

**Response:**
```json
{
  "processed": 10,
  "resolved": 7,
  "kept_original": 2,
  "failed": 1
}
```

## Configuration

All settings are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ENTITY_ALIAS_RESOLVER_ENABLED` | true | Enable entity alias resolution |
| `ENTITY_ALIAS_BATCH_SIZE` | 10 | Tags per LLM API call |
| `ENTITY_ALIAS_MAX_PER_RUN` | 50 | Max tags per resolution run |
| `TAG_TRANSLATION_BG_CATEGORIES` | general,meta | Categories for background worker (proper-nouns excluded) |

The entity resolver reuses the same LLM provider configuration as visual translation:
- `TAG_TRANSLATION_LLM_ENABLED` must be `true`
- `TAG_TRANSLATION_LLM_API_KEY`, `TAG_TRANSLATION_LLM_BASE_URL`, `TAG_TRANSLATION_LLM_MODEL` configure the provider

## Admin UI

The Entity Alias Resolver section in the admin panel shows:

1. **Status panel** — enabled/disabled, LLM available, total proper-noun tags, resolved/needs-review/no-translation counts
2. **Config display** — batch size, max per run
3. **Resolve button** — triggers entity resolution for pending tags
4. **Result display** — shows resolved/kept/failed counts after resolution
5. **Load Pending button** — shows pending proper-noun tags in a table
6. **Pending table** — canonical name, category badge, post count, review status

## Background Worker Category Policy

The background translation worker (`tag_translation_worker.py`) reads `TAG_TRANSLATION_BG_CATEGORIES` to determine which tag categories to translate. By default this is `general,meta`, which means the worker:

- Translates general and meta tags automatically
- Skips character, copyright, and artist tags
- These proper-noun tags must be resolved through the entity alias resolver instead

## Workflow

1. Background worker translates general/meta tags automatically
2. Admin opens Entity Alias Resolver section in admin panel
3. Admin clicks "Load Pending" to see unresolved proper-noun tags
4. Admin clicks "Resolve" to run LLM alias resolution
5. Results are saved with `needs_review=true` (excluded from search)
6. Admin reviews aliases in the Tag Localization review panel
7. Approved aliases become active in Chinese search

## Tests

13 Playwright tests in `tests/e2e/entity-alias-resolver.spec.ts`:

**Smoke tests (always run):**
1. Entity status API returns valid structure
2. Entity pending API returns array with correct fields
3. Entity pending only returns proper-noun categories
4. Entity resolve requires admin authentication (uses fresh HTTP context, no session cookies)
5. Worker status includes categories config (excludes proper-nouns)
6. Entity status does not expose API key
7. Entity section exists in admin UI
8. Proper-noun LLM translations marked needs_review in search trust
9. Search cache excludes untrusted proper-noun LLM aliases
10. Entity status config matches ENTITY_ALIAS_BATCH_SIZE setting

**Real E2E tests (require `VIOLET_RUN_REAL_E2E=1`):**
11. Entity resolve processes pending tags
12. Entity resolve with limit respects max
13. Resolved entity has translation in DB with provider=entity_resolver
