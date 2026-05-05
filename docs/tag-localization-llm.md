# Dynamic Tag Localization — LLM Translation Cache

## Overview

Phase 2.2.2 adds a dynamic tag localization system that supplements the static Chinese dictionary with database-backed translations and optional LLM-generated translations.

**Key principle:** Canonical `tag.name` in the database always remains in English (Danbooru-style). Chinese display names and search aliases are stored separately in the `blombooru_tag_translations` table.

## Dependencies

LLM translation requires `httpx` for async HTTP calls. This is declared in `requirements.txt`:

```
httpx==0.28.1
httpcore==1.0.9
```

Install with: `pip install -r requirements.txt`

## Architecture

```
User sees Chinese display name
         ↑
    Priority lookup:
    1. manual/reviewed DB translation
    2. static dictionary (tag_translations_zh.json)
    3. LLM-generated translation (cached in DB)
    4. fallback: canonical English tag
```

### Components

| Component | Path | Role |
|-----------|------|------|
| TagTranslation model | `backend/app/models.py` | DB schema for translations |
| Migration | `backend/app/database.py` | Creates `blombooru_tag_translations` table |
| Localization service | `backend/app/services/tag_localization_service.py` | Core translation logic |
| LLM provider | `backend/app/services/llm_translation_provider.py` | Abstract LLM API integration |
| Admin API | `backend/app/routes/admin/tag_localization.py` | Admin endpoints |
| Public API | `backend/app/routes/tags.py` | `GET /api/tags/translations/batch` |
| Search parser | `backend/app/utils/search_parser.py` | Chinese alias resolution |
| Frontend | `frontend/static/js/tag-localization.js` | UI display name lookup |
| Config | `backend/app/config.py` | `TAG_TRANSLATION_LLM_*` settings |

## Database Schema

Table `blombooru_tag_translations`:

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `tag_id` | INTEGER | FK to `blombooru_tags.id` (nullable) |
| `canonical_name` | VARCHAR(255) | Danbooru canonical tag name |
| `language` | VARCHAR(10) | Language code (default: `zh-CN`) |
| `display_name` | VARCHAR(500) | Translated display name |
| `aliases_json` | TEXT | JSON array of search aliases |
| `category` | VARCHAR(50) | general/character/copyright/artist/meta |
| `source` | VARCHAR(50) | static/llm/manual/imported |
| `status` | VARCHAR(50) | pending/translated/reviewed/rejected |
| `confidence` | FLOAT | Translation confidence (nullable) |
| `needs_review` | BOOLEAN | Whether human review is needed |
| `provider` | VARCHAR(100) | Translation provider name |
| `created_at` | TIMESTAMPTZ | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | Last update timestamp |

Unique constraint: `(canonical_name, language)`

## Translation Priority

From highest to lowest:

1. **Manual/reviewed** (`source=manual`, `status=reviewed`) — human-confirmed
2. **Static dictionary** — `tag_translations_zh.json` entries (seeded as `source=static`)
3. **LLM cache** (`source=llm`, `status=translated`) — auto-generated, may need review
4. **Canonical fallback** — English tag name

## LLM Configuration

All settings are read from environment variables (`.env`):

```env
# Enable LLM translation (default: false)
TAG_TRANSLATION_LLM_ENABLED=false

# Provider type (currently: openai_compatible)
TAG_TRANSLATION_LLM_PROVIDER=openai_compatible

# API key (NEVER commit this)
TAG_TRANSLATION_LLM_API_KEY=sk-your-api-key

# Model name
TAG_TRANSLATION_LLM_MODEL=gpt-4o-mini

# API base URL
TAG_TRANSLATION_LLM_BASE_URL=https://api.openai.com/v1

# Max items per batch (default: 50)
TAG_TRANSLATION_BATCH_MAX_ITEMS=50
```

### Security

- API keys are **only** read from `.env` file
- `.env` is in `.gitignore` and never committed
- The `example.env` file contains placeholder values only
- If no API key is configured, no external API calls are made
- LLM failure does not crash the application

### Provider Abstraction

The `BaseLLMProvider` abstract class defines the interface:
- `is_available()` — check if provider is ready
- `get_provider_name()` — human-readable name
- `translate_tags(tags)` — batch translate

Built-in providers:
- `DisabledProvider` — returns empty results (default when LLM is disabled)
- `OpenAICompatibleProvider` — works with OpenAI, Azure, local LLM servers

### Translation Strategy

The LLM prompt instructs the model to:

- **general tags**: Translate naturally (e.g., `blue_eyes` → `蓝眼睛`)
- **character tags**: Use common Chinese name if known; keep original if unsure, set `needs_review=true`
- **copyright tags**: Use Chinese title if known; keep original if unsure, set `needs_review=true`
- **artist tags**: Usually keep original name, set `needs_review=true`
- **meta/rating**: Translate descriptively

## API Endpoints

### Public

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tags/translations/batch?names=tag1,tag2` | Batch get display names |

### Admin (requires auth + admin_mode)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/tag-localization/stats` | Translation statistics |
| GET | `/api/admin/tag-localization/missing` | List untranslated tags |
| GET | `/api/admin/tag-localization/translations` | List translations with filters |
| POST | `/api/admin/tag-localization/translations` | Create/update translation |
| DELETE | `/api/admin/tag-localization/translations/{id}` | Delete translation |
| POST | `/api/admin/tag-localization/batch-translate` | Batch LLM translate |
| GET | `/api/admin/tag-localization/llm-status` | Check LLM provider status |
| POST | `/api/admin/tag-localization/translations/{id}/review` | Approve/reject |
| POST | `/api/admin/tag-localization/test-llm` | Test LLM with a known tag |

## Admin UI

The Admin Panel includes a "Tag Localization" section with:

1. **Statistics** — total tags, translated count, missing, needs review, source breakdown
2. **LLM Status** — provider, model, API key configured (yes/no, never shows the key), auto-translate status
3. **Test LLM** — translate a known tag to verify LLM connectivity
4. **Manual edit** — input canonical tag name, Chinese display name, aliases, save
5. **Batch LLM translate** — dry-run toggle, max items, category filter
6. **Missing translations list** — browse untranslated tags by category
7. **Review translations** — filter, search, approve/reject LLM results

## Search Enhancement

Chinese search aliases work through the search parser:

- DB translation cache refreshes every 5 minutes
- Falls back to static JSON dictionary
- Supports positive and negative search: `蓝眼睛` → `blue_eyes`, `-蓝眼睛` → `-blue_eyes`
- Does not break English search or meta qualifiers (`rating:safe`, `width:>1920`)
- Alias conflicts resolved by source priority

## Auto-Translation (Phase 2.2.2a)

New tags can be automatically translated when created, if both LLM and auto-translate are enabled.

### Configuration

```env
# Both must be true for auto-translation
TAG_TRANSLATION_LLM_ENABLED=true
TAG_TRANSLATION_AUTO_ENABLED=true

# Throttle: max tags per auto-translate trigger
TAG_TRANSLATION_AUTO_MAX_ITEMS=20
```

### How it works

1. New tag created (via upload, AI tagging, AI tagging jobs, admin, booru import, etc.)
2. System checks: is `TAG_TRANSLATION_AUTO_ENABLED=true` AND `TAG_TRANSLATION_LLM_ENABLED=true`?
3. If yes: spawns a background thread with independent DB session
4. Thread filters out tags that already have translations (DB or static dict)
5. Sends remaining tags (up to `TAG_TRANSLATION_AUTO_MAX_ITEMS`) to LLM
6. Saves results to DB, invalidates search cache
7. If LLM fails: main operation is NOT affected, tags remain with canonical fallback

### Safety

- Non-blocking: runs in background daemon thread
- Independent DB session (not request-scoped)
- Thread-safe: only one auto-translate worker runs at a time
- Exception isolation: worker catches all errors
- Throttled by `TAG_TRANSLATION_AUTO_MAX_ITEMS`
- Does NOT modify canonical `tag.name`

### AI Tagging Job Integration (Phase 2.3)

AI tagging jobs (background jobs created manually or automatically after scan import) also trigger auto-translation. When an AI tagging job creates new tags, it schedules a localization pass for those tags, following the same auto-translate rules above. This means newly AI-tagged media will have Chinese display names available shortly after tagging completes, without manual intervention.

## Translation Priority & Overwrite Rules

From highest to lowest:

1. **Manual/reviewed** (`source=manual`, `status=reviewed`) — human-confirmed
2. **Static dictionary** — `tag_translations_zh.json` entries (seeded as `source=static`)
3. **LLM cache** (`source=llm`, `status=translated`) — auto-generated, may need review
4. **Canonical fallback** — English tag name

**Overwrite rules:**
- Lower-priority source CANNOT overwrite higher-priority source (e.g., `llm` cannot overwrite `static` or `manual`)
- Same source: always updates
- Higher-priority source can overwrite lower-priority source
- Admin manual operations use `force=True` to bypass priority (explicit admin intent)

## Why Not Real-Time LLM?

- **Latency**: LLM API calls take 100ms–5s
- **Cost**: Per-token billing adds up with every page view
- **Availability**: LLM services can be down
- **UX**: Users should not wait for translations on every render
- **Correct approach**: Admin triggers batch translation OR auto-translate on creation → results cached in DB → instant lookup

## Known Limitations

- Auto-translate requires explicit opt-in (`TAG_TRANSLATION_AUTO_ENABLED=true`)
- character/copyright/artist translations need human review
- Search alias cache refreshes every 5 minutes (immediate after Admin UI / auto-translate changes)
- Only `zh-CN` language supported currently
- Auto-translate throttled to `TAG_TRANSLATION_AUTO_MAX_ITEMS` per trigger
- Phase 2.3 AI tagging jobs automatically schedule localization for new tags after completion
