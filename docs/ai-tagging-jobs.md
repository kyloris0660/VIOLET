# AI Tagging Job System (Phase 2.3)

Background job system for AI tagging, supporting both manual trigger and automatic trigger after local library scan.

## Overview

The AI Tagging Job System extends Phase 2.1's single/batch AI tagging into a managed background job with:

- **Persistent state** — job progress is tracked in the database, survives page refresh
- **Background execution** — daemon threads with independent DB sessions
- **Auto-trigger** — optionally creates an AI tag job when a scan completes
- **Localization integration** — schedules Chinese translations for new tags
- **Cancel support** — graceful cancellation via in-memory flag
- **Stale recovery** — marks interrupted jobs on startup

## S2G-M1 Execution Foundation

S2G-M1 adds a durable AI tagging execution profile in
`backend/app/services/job_control.py`. The profile is used by the local
capability probe and by the manual sync dry-run planner so S3A-M1 can reuse the
same provider/load/provenance defaults instead of inventing a separate sync
configuration.

The profile records:

- local backend type: `onnxruntime`;
- WD model name/repo identity and public-safe model source;
- general/character/rating/suggestion thresholds;
- requested provider preference and CPU fallback policy;
- bounded batch size, concurrency, preprocess workers, per-image timeout, and
  job timeout;
- provenance fields for source, model, provider, confidence, thresholds, job
  id, dry-run/write mode, and fallback decision;
- dry-run/dev-test/production-capable scope flags;
- `production_writes_enabled=false` by default for S2G-M1.

S2G-M1 does not add external/cloud AI providers. Model resolution is
local-files-only in the phase runner, and provider/gallery-dl/Pixiv/SauceNAO/
Google/LLM calls remain forbidden.

## S2G-M1 Load and Safety Defaults

The S2G-M1 report recommends the following safe defaults for the next S3A-M1
manual-sync acceptance phase:

- AI batch size: `2`;
- AI concurrency: `1`;
- per-image timeout: `60` seconds;
- job timeout: `600` seconds;
- one active AI execution at a time;
- per-image failure isolation;
- manual/locked tags are not overwritten by AI;
- AI outputs remain suggestions/AI-sourced tags unless a separately approved
  write policy promotes them.

## Database Schema

### `blombooru_ai_tag_jobs`

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `id` | `integer` PK | auto | Job ID |
| `status` | `varchar(20)` | `'pending'` | Current status (see lifecycle below) |
| `trigger_source` | `varchar(20)` | `'manual'` | `manual` or `scan_job` |
| `scan_job_id` | `integer` FK | `NULL` | References `blombooru_scan_jobs.id` (SET NULL on delete) |
| `media_ids_json` | `text` | `NULL` | JSON array of specific media IDs to process |
| `max_items` | `integer` | `10` | Maximum items to process |
| `dry_run` | `boolean` | `false` | If true, no DB writes |
| `only_without_ai_tags` | `boolean` | `true` | Skip already-tagged media |
| `force_suggestions` | `boolean` | `false` | Write all tags as suggestions |
| `processed` | `integer` | `0` | Items processed so far |
| `tags_added` | `integer` | `0` | Confirmed tags written |
| `suggestions_added` | `integer` | `0` | Suggestion tags written |
| `skipped_locked` | `integer` | `0` | Skipped due to locked manual tags |
| `ignored_low_confidence` | `integer` | `0` | Below suggestion threshold |
| `failed` | `integer` | `0` | Items that errored |
| `failed_items_json` | `text` | `NULL` | JSON array of `{media_id, error}` (max 50) |
| `error_message` | `text` | `NULL` | Fatal error message |
| `localization_status` | `varchar(50)` | `NULL` | Tag translation scheduling result |
| `created_at` | `timestamptz` | `now()` | When job was created |
| `started_at` | `timestamptz` | `NULL` | When processing started |
| `finished_at` | `timestamptz` | `NULL` | When processing ended |
| `updated_at` | `timestamptz` | `now()` | Last update (auto on change) |

Indexes: `id` (PK), `status`, `scan_job_id`.

### `blombooru_scan_job_media`

Links imported media to their source scan job. Used by auto-trigger to determine which media IDs to pass to the AI job.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `integer` PK | Row ID |
| `scan_job_id` | `integer` FK | References `blombooru_scan_jobs.id` (CASCADE) |
| `media_id` | `integer` FK | References `blombooru_media.id` (CASCADE) |
| `created_at` | `timestamptz` | When the link was created |

Indexes: `scan_job_id`, `media_id`.

## Job Statuses and Lifecycle

```
┌─────────┐    start     ┌─────────┐
│ pending │─────────────▶│ running │
└─────────┘              └────┬────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
        ┌───────────┐  ┌───────────┐  ┌────────────┐
        │ completed │  │  failed   │  │ cancelling │
        └───────────┘  └───────────┘  └─────┬──────┘
                                             │
                                             ▼
                                       ┌───────────┐
                                       │ cancelled │
                                       └───────────┘

On unclean shutdown:
  pending / running / cancelling  ──▶  interrupted
```

| Status | Meaning |
|--------|---------|
| `pending` | Job created, waiting to start |
| `running` | Processing media items |
| `cancelling` | Cancel requested, will stop after current item |
| `completed` | All items processed successfully |
| `failed` | Fatal error (not per-item failure) |
| `cancelled` | Stopped by user request |
| `interrupted` | Application stopped while job was active |

## API Endpoints

All endpoints require admin authentication (JWT + `admin_mode` cookie).

### Create AI Tag Job

```
POST /api/admin/ai-tagging/jobs
```

#### Request Body

```json
{
  "media_ids": [1, 2, 3],          // optional — omit to auto-select
  "max_items": 10,                  // default 10, capped by AI_TAGGING_BATCH_MAX_ITEMS
  "dry_run": false,                 // default false
  "only_without_ai_tags": true,     // default true
  "force_suggestions": false        // default false
}
```

#### Behavior

- Returns immediately with the job object (status = `pending`)
- Launches a background daemon thread
- Returns `409` if another AI job is already running
- Returns `400` if AI tagging is disabled or `media_ids` exceeds hard limit
- `max_items` is silently clamped to `AI_TAGGING_BATCH_MAX_ITEMS`

#### Response

```json
{
  "id": 1,
  "status": "pending",
  "trigger_source": "manual",
  "scan_job_id": null,
  "media_ids": [1, 2, 3],
  "max_items": 10,
  "dry_run": false,
  "only_without_ai_tags": true,
  "force_suggestions": false,
  "processed": 0,
  "tags_added": 0,
  "suggestions_added": 0,
  "skipped_locked": 0,
  "ignored_low_confidence": 0,
  "failed": 0,
  "failed_items": [],
  "error_message": null,
  "localization_status": null,
  "created_at": "2026-05-05T10:00:00+00:00",
  "started_at": null,
  "finished_at": null
}
```

### List AI Tag Jobs

```
GET /api/admin/ai-tagging/jobs
```

Returns the 20 most recent jobs (newest first).

### Get Job Status

```
GET /api/admin/ai-tagging/jobs/{job_id}
```

Returns the current state and progress counters. Poll every 2–3 seconds for real-time progress.

### Cancel a Running Job

```
POST /api/admin/ai-tagging/jobs/{job_id}/cancel
```

- Sets status to `cancelling` in DB
- Sets in-memory cancel flag
- Worker checks the flag after each item and stops gracefully
- Already-processed items are kept (cancel does not roll back)
- Returns `400` if job is not in `pending` or `running` status

### Get Auto-Tag Configuration

```
GET /api/admin/ai-tagging/auto-config
```

Returns current auto-tagging configuration (read-only, not modifiable via API):

```json
{
  "ai_tagging_enabled": true,
  "auto_tag_after_import": true,
  "auto_tag_max_items": 20,
  "auto_tag_only_new": true,
  "auto_tag_dry_run": false,
  "auto_tag_force_suggestions": false,
  "batch_max_items": 10,
  "tag_translation_auto": true,
  "tag_translation_llm": true
}
```

## Auto-Trigger After Scan

When `AI_AUTO_TAG_AFTER_IMPORT=true`, the scan job worker automatically creates an AI tag job after a successful import.

### Flow

1. Scan job completes with `imported > 0`
2. Scanner calls `create_auto_tag_job_after_scan(scan_job_id, imported_media_ids)`
3. Function checks all preconditions:
   - `AI_AUTO_TAG_AFTER_IMPORT` is `true`
   - `AI_TAGGING_ENABLED` is `true`
   - `imported_media_ids` is not empty
4. Creates a new AI tag job with `trigger_source = "scan_job"` and `scan_job_id` set
5. Starts the background worker thread
6. Returns the new job ID (logged, not exposed to scan API response)

### Media Selection

Controlled by `AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW`:

| Setting | Behavior |
|---------|----------|
| `true` (default) | Only tags media imported in this specific scan |
| `false` | Tags any un-tagged media in the library (up to `max_items`) |

### Concurrency

The auto-trigger uses its own DB session (independent of the scan worker). If another AI job is already running when the scan completes, the auto-trigger will fail gracefully and log a warning.

## Configuration

All settings are via `.env` (no runtime UI modification):

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_TAGGING_ENABLED` | `false` | Master switch for all AI tagging |
| `AI_TAGGING_BATCH_MAX_ITEMS` | `10` | Hard cap on items per job |
| `AI_AUTO_TAG_AFTER_IMPORT` | `false` | Enable auto-trigger after scan |
| `AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS` | `20` | Max items for auto-triggered jobs |
| `AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW` | `true` | Only tag newly imported media |
| `AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN` | `false` | Auto jobs run in dry-run mode |
| `AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS` | `false` | Force all auto-tags as suggestions |

> **安全设计：** 自动打标默认关闭（`AI_AUTO_TAG_AFTER_IMPORT=false`），防止意外对大型图库执行 AI 推理。

## Tag Localization Integration

After an AI tag job completes (not cancelled, not dry-run):

1. Collects all unique tag names that were written (confirmed + suggestions)
2. Checks if auto-translation is configured:
   - `TAG_TRANSLATION_AUTO_ENABLED=true` AND `TAG_TRANSLATION_LLM_ENABLED=true`
3. If yes: calls `schedule_auto_translate(unique_names)` to create LLM translation tasks
4. Records result in `localization_status`:

| Value | Meaning |
|-------|---------|
| `scheduled_N_tags` | Successfully scheduled N tags for translation |
| `skipped_dry_run` | Job was dry-run, no real tags to translate |
| `skipped_no_new_tags` | No new tag names were produced |
| `skipped_llm_disabled` | Auto-translate enabled but LLM disabled |
| `skipped_auto_disabled` | Auto-translate disabled |
| `error: ...` | Scheduling failed (details in value) |

## Stale Recovery

On application startup, `mark_stale_ai_jobs()` is called to handle unclean shutdowns:

- Finds all jobs with status `pending`, `running`, or `cancelling`
- Sets their status to `interrupted` with an error message
- Sets `finished_at` to current time
- Logs the count of recovered jobs

This prevents the Admin UI from showing perpetually "running" jobs after a crash or restart.

**Important:** Stale recovery runs only at startup, not during normal API calls.

## Admin UI

The Admin Panel → Content tab includes an **AI Tagging Jobs** section:

1. **Job History** — table showing recent jobs with status, trigger, counts
2. **Create Job** — form to manually trigger an AI tagging job
   - Optional: specific media IDs
   - Max items (capped)
   - Dry-run toggle
   - Only-without-AI-tags toggle
   - Force-suggestions toggle
3. **Active Job** — real-time progress display (polls every 2–3 seconds)
4. **Cancel** — button to cancel a running job
5. **Auto-Config** — displays current auto-trigger settings (read-only)

## Architecture Notes

### Threading Model

- Each AI tag job runs in a **daemon thread** (`threading.Thread(daemon=True)`)
- Workers create their own `SessionLocal()` — never share the request-scoped session
- The `_active_job_cancel` dict provides thread-safe cancellation signaling
- `_active_job_lock` (threading.Lock) protects all shared state

### Per-Item Error Isolation

- If a single media item fails during processing, the transaction is rolled back
- Processing continues with the next item
- Failed items are recorded in `failed_items_json` (capped at 50 entries)
- This prevents `PendingRollbackError` cascades

### Progress Flushing

- Progress counters are flushed to DB every `_PROGRESS_FLUSH_INTERVAL` (5) items
- Ensures the UI shows near-real-time progress during long jobs
- Final flush happens at job completion

### Single-Job Concurrency

- Only one AI tag job can run at a time
- Enforced by both DB query (checking active statuses) and in-memory lock
- The `409` response tells the client to wait

## Known Limitations

1. **Single concurrency** — only one AI job at a time (by design, to avoid GPU/CPU contention)
2. **No priority queue** — jobs are FIFO, no way to prioritize specific media
3. **No retry** — failed items are not automatically retried
4. **Thread-based** — not process-based; a GIL-heavy model may block other requests during inference
5. **No scheduled/cron jobs** — auto-trigger only fires after scan, not on a timer
6. **Config is env-only** — cannot change auto-tag settings from the Admin UI (requires restart)
7. **max_items hard limit** — `AI_TAGGING_BATCH_MAX_ITEMS` caps both manual and auto jobs; increase in `.env` if needed
8. **No partial resume** — if a job is interrupted, it must be re-created (already-tagged media will be skipped by `only_without_ai_tags`)

## Files

| File | Purpose |
|------|---------|
| `backend/app/services/ai_tagging_job_service.py` | Job creation, worker, cancel, stale recovery, auto-trigger |
| `backend/app/routes/admin/ai_tagging_jobs.py` | API endpoints (CRUD + cancel + auto-config) |
| `backend/app/models.py` | `AITagJob` and `ScanJobMedia` ORM models |
| `backend/app/config.py` | `AI_AUTO_TAG_AFTER_IMPORT_*` settings |
| `backend/app/utils/local_library_scanner.py` | Calls `create_auto_tag_job_after_scan` on completion |
| `frontend/templates/admin.html` | Admin UI (AI Tagging Jobs section) |
| `frontend/static/js/admin.js` | UI polling and job management logic |

## Config Diagnostics

If config values appear incorrect (e.g., `batch_max_items` shows a wrong value), use the config diagnostics endpoint:

```
GET /api/admin/dev/config-diagnostics
```

This returns all runtime config values including AI tagging, auto-tag, tag localization, and path settings. No secrets are exposed.

### Why Config Values Might Be Wrong

Phase 2.3a fixed an issue where `load_dotenv()` could load an `.env` file from the wrong location or fail to override existing environment variables. The fix:

- Before: `load_dotenv()` (searches from `backend/app/` upward, `override=False`)
- After: `load_dotenv(dotenv_path=<project_root>/.env, override=True)`

Always restart the server after changing `.env`.

## Related Documentation

- [AI Auto Tagging (Phase 2.1)](ai-auto-tagging.md) — model, thresholds, single/batch API
- [AI Tag Review (Phase 2.2)](ai-tag-review.md) — review, confirm, reject workflows
- [Local Library Scan](local-library-scan.md) — scan jobs, paths, dry-run
- [E2E Validation Guide](e2e-violet-test-100.md) — full pipeline testing instructions
