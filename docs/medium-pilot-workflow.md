# Medium-Scale Pilot Workflow (500 / 1000 / 2000)

> Designed in Phase 3.2c. Execution is a separate phase.

## 1. Purpose

Validate V.I.O.L.E.T. at medium scale (500 -> 1000 -> 2000 media items) before committing to a full personal library import. Each tier runs independently, and advancing to the next tier requires passing the current one.

## 2. Dataset

- **Source**: user-prepared image sets (anime + non-anime mix)
- **Location**: dedicated directory per tier, e.g. `D:\VioletPilotData\500`, `D:\VioletPilotData\1000`, `D:\VioletPilotData\2000`
- **Constraints**: do NOT use iCloud paths, VioletTestFixture, or production storage

## 3. Isolated Environment

Each pilot tier uses a dedicated database and storage root, completely separate from dev (`blombooru`) and unit-test (`blombooru_test`) databases.

| Item | Value |
|------|-------|
| Database | `blombooru_test_medium` (created via `scripts/setup_test_db.py`) |
| Storage | `C:\Users\kyloris\VioletStorage\medium` |
| Port | dynamic probe 8012-8024 (via `APP_PORT` env var) |
| Env | `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test_medium` |
| HF cache | `HF_HUB_OFFLINE=1` — use local model cache only (see § 3.1) |

### 3.1 HuggingFace Hub Offline Mode and Proxy Considerations

**Problem:** The CLIP model (~350 MB) is downloaded from HuggingFace Hub on first use. In proxy environments (e.g. `HTTP_PROXY` / `HTTPS_PROXY` set for GFW bypass), `huggingface_hub` may fail on metadata checks even when the model is already cached locally — the proxy routes the Hub request through an external relay that may time out or return SSL errors.

**Solution:** Set `HF_HUB_OFFLINE=1` in the server environment. This tells `huggingface_hub` to use only the local cache and skip all network requests. The model must have been downloaded at least once before enabling this flag.

```powershell
# Add to your session before starting the server
$env:HF_HUB_OFFLINE = "1"
```

**When to use:**
- Always during medium-pilot tiers (model should already be cached from Phase 3.1+)
- Any environment where `HTTP_PROXY` / `HTTPS_PROXY` is set and HuggingFace Hub requests fail
- Air-gapped or restricted-network deployments

**When NOT to use:**
- First-time model download (the model must be cached locally first)
- Updating to a new model version (disable temporarily to fetch the new version)

**Proxy vs localhost conflict:** External services (HuggingFace, OpenAI) need the proxy; localhost server identity checks must NOT use the proxy. The identity check script (`scripts/check_test_server_identity.py`) sets `session.trust_env = False` to disable proxy inheritance for localhost calls. `HF_HUB_OFFLINE=1` sidesteps the HuggingFace proxy issue entirely by avoiding network calls.

## 4. Dry-Run First (mandatory)

Every tier MUST execute preflight + dry-run import before any real import:

1. `POST /api/admin/scan-local-library` with `dry_run: true` — verify file counts and type distribution
2. Review the dry-run report: total files, supported types, skipped, errors
3. Only proceed to real import after the user confirms the dry-run results are acceptable

## 5. Recommended Starting Values

The following are **recommended starting points** for each pilot tier. Adjust based on observed performance, error rates, and LLM costs. All values are configured via `.env` — nothing is hardcoded.

| Tier | Import | AI Tag batch / auto | Translate batch / bg_max | Classify batch / auto | Daily LLM limit |
|------|--------|---------------------|--------------------------|----------------------|-----------------|
| 500 | 500 | 50 / 20 | 50 / 500 | 100 / 50 | 500 |
| 1000 | 1000 | 100 / 50 | 100 / 1000 | 200 / 100 | 1000 |
| 2000 | 2000 | 200 / 100 | 200 / 2000 | 500 / 200 | 2000 |

## 6. Processing Order

1. Import (local library scan)
2. Content classification (CLIP or heuristic)
3. AI tagging (WDv3) — use `content_class_filter` to scope by content class (see § 6.1)
4. Tag translation (LLM) — only if `AI_TAGGING_AUTO_LOCALIZATION=true` (see § 6.2)
5. Entity alias resolution (LLM, if enabled)

Each step's output feeds the next. Do not skip steps or run them out of order.

### 6.1 AI Tagging Scope Control (`content_class_filter`)

The `POST /api/admin/ai-tagging/jobs` endpoint accepts a `content_class_filter` parameter to limit AI tagging to specific content classes. This prevents tagging non-anime media with anime-specific models (WDv3).

```json
{
  "content_class_filter": ["anime", "illustration"],
  "max_items": 50,
  "only_without_ai_tags": true
}
```

Valid values: `"anime"`, `"illustration"`, `"non_anime"`, `"unknown"`.

**Rules:**
- `content_class_filter` and `media_ids` are mutually exclusive — the API returns HTTP 400 if both are provided.
- `null` (default) means no filtering — all content classes are eligible.
- The filter resolves to explicit media IDs at the API route level, so the background job service is unchanged.
- `only_without_ai_tags` is applied *in addition to* the content class filter.

**Recommended pilot usage:** Run classification first (step 2), then tag only `["anime", "illustration"]` to avoid wasting WDv3 inference on photos.

### 6.2 Localization Side-Effect Control (`AI_TAGGING_AUTO_LOCALIZATION`)

After an AI tagging job completes, the system auto-triggers tag localization (LLM translation). This can be disabled for controlled pilot runs:

```powershell
$env:AI_TAGGING_AUTO_LOCALIZATION = "false"
```

When disabled, `_schedule_localization` sets `localization_status = "skipped_auto_localization_disabled"` and returns without invoking LLM.

**Default:** `true` (backward compatible — preserves existing auto-localization behavior).

**When to disable:**
- Controlled pilot runs where LLM costs must be managed separately
- Debugging AI tagging without localization side-effects
- Any run where tag translation should be triggered manually later

**Incident context (Phase 3.2g):** During the first medium-pilot AI tagging run, auto-localization queued 306 LLM translations via OpenAI despite the phase prohibiting LLM usage. This flag was added to prevent such unintended side-effects.

### 6.3 Full AI-Only Isolation (post-incident, Phase 3.2g.2a)

`AI_TAGGING_AUTO_LOCALIZATION=false` alone is **insufficient** for full AI-only isolation. It only gates `_schedule_localization()` inside AI tagging jobs. The server-level background translation worker (`tag_translation_worker.py`) runs independently and will continue translating tags.

**All four env vars must be set for AI-only phases:**

```powershell
$env:AI_TAGGING_AUTO_LOCALIZATION = "false"        # disable AI-job-triggered localization
$env:TAG_TRANSLATION_BACKGROUND_ENABLED = "false"  # disable background translation worker
$env:TAG_TRANSLATION_AUTO_ENABLED = "false"        # disable auto-translate on tag creation
$env:TAG_TRANSLATION_LLM_ENABLED = "false"         # disable LLM translation provider entirely
```

**Post-startup verification:** After starting the server, confirm the translation worker is stopped:

```
GET /api/admin/tag-localization/worker/status
→ response should show "running": false
```

**Active translation jobs check:** Before starting new AI tagging, verify no active or running translation jobs exist from prior phases. If any are found, stop and report them before proceeding.

**Incident context (Phase 3.2g.2):** With only `AI_TAGGING_AUTO_LOCALIZATION=false`, the background worker added 182 translations during an AI-only tagging run. The other three env vars were not set.

**Config precedence fix (Phase 3.2g.5):** `backend/app/config.py` previously called `load_dotenv(override=True)`, which forced `.env` values (e.g. `POSTGRES_DB=blombooru`, `TAG_TRANSLATION_BACKGROUND_ENABLED=true`) to overwrite explicit session env vars. This has been fixed to `override=False` — shell-set env vars now take precedence over `.env` defaults directly, eliminating the need for API-based workarounds to pause the translation worker.

## 7. Preflight Checklist

Complete ALL items before executing any tier:

- [ ] 0. **Python/venv identity preflight (hard gate):** Verify the approved project venv Python is in use — not the global/system Python. Run: `& "$PY" scripts/check_python_env.py --expected-python "$PY"` where `$PY = C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`. The script must exit 0. If it exits 1, stop and diagnose. Do not proceed with any subsequent preflight items until this passes.
- [ ] 1. `VIOLET_ENV=test` confirmed
- [ ] 2. `POSTGRES_DB=blombooru_test_medium` confirmed
- [ ] 3. `VIOLET_STORAGE_ROOT=C:\Users\kyloris\VioletStorage\medium` confirmed
- [ ] 4. Database created: `& "$PY" scripts/setup_test_db.py` with `POSTGRES_DB=blombooru_test_medium`
- [ ] 5. Schema migrated: `& "$PY" scripts/setup_test_db.py --migrate` with test env
- [ ] 6. Storage directory exists and is empty (or contains only previous tier data if continuing)
- [ ] 7. Dataset directory exists with expected file count
- [ ] 8. Dataset validated with generic pilot inspector (see Section 12). The inspector must exit with code 0 — any non-zero exit means errors were found (unreadable files, traversal failures, missing directory) and the preflight fails:

```powershell
& "$PY" scripts/inspect_pilot_dataset.py --path "D:\VioletPilotData\500"
& "$PY" scripts/inspect_pilot_dataset.py --path "D:\VioletPilotData\500" --json
# Exit code 0 = clean, non-zero = errors found (check "errors" and "stat_errors" in JSON output)
```

> **Note:** `scripts/inspect_test_fixture.py` is for VioletTestFixture (small smoke) only — it requires `anime/non_anime/mixed` subfolders and is NOT suitable for arbitrary pilot datasets.
>
> **Error semantics:** The following are tracked as **errors** (cause non-zero exit, block preflight): stat failures on individual files (`stat_errors`), directory traversal errors (permission denied on subdirectories), and non-existent dataset path. The following are NOT errors: unsupported file types, hidden/system files, duplicate files — these are expected in mixed datasets and do not block preflight.

- [ ] 9. `HF_HUB_OFFLINE=1` set in session (see § 3.1 — ensures CLIP uses local cache only, avoids proxy/network failures)
- [ ] 10. CLIP model readiness verified: `& "$PY" scripts/check_clip_model_ready.py` exits 0 (model cached and loadable). This script is **cache-only by default** — it forces `HF_HUB_OFFLINE=1` internally and never downloads models, regardless of your environment. Note: video-only jobs do not require CLIP readiness (they skip CLIP inference entirely).
- [ ] 11. **Localization side-effect gate set** (if not running tag translation this tier): Set all 4 AI-only isolation env vars (see § 6.3). At minimum: `$env:AI_TAGGING_AUTO_LOCALIZATION = "false"`. For full isolation, also set `TAG_TRANSLATION_BACKGROUND_ENABLED=false`, `TAG_TRANSLATION_AUTO_ENABLED=false`, `TAG_TRANSLATION_LLM_ENABLED=false`. Omit these (or set `"true"`) if you *want* auto-localization.
- [ ] 12. Server started on dynamic port (probe 8012–8024)
- [ ] 13. Identity check passed with **explicit expected args** (hard gate — do not proceed without this):

```powershell
$expectedSha = (git rev-parse --short HEAD)
$expectedRoot = (Get-Location).Path

& "$PY" scripts/check_test_server_identity.py `
  --base-url $env:VIOLET_BASE_URL `
  --expected-env test `
  --expected-db blombooru_test_medium `
  --expected-code-root "$expectedRoot" `
  --expected-git-sha "$expectedSha" `
  --expected-python "$PY" `
  --expected-storage-root "C:\Users\kyloris\VioletStorage\medium" `
  --admin-password "<your admin password>"
```

If the endpoint requires admin auth, add `--admin-username` and `--admin-password`. Do NOT commit real credentials. Running without `--expected-*` args is **not sufficient** for medium pilot — the script must explicitly verify env, DB, code root, git SHA, Python executable, and storage root. Any identity mismatch is an **immediate fail**: stop, diagnose, do not continue with E2E, import, or any processing.

- [ ] 14. Database backup taken (custom archive format):

```powershell
pg_dump -Fc -f backup_before_tier_N.dump blombooru_test_medium
```

- [ ] 15. Dry-run import completed and results reviewed

## 8. Pass / Fail Criteria

### Pass thresholds

| Metric | Threshold |
|--------|-----------|
| Import success rate | >= 98% |
| AI tagging success rate | >= 95% |
| Translation error rate | <= 5% |
| Classification coverage | >= 90% |

### Failure definitions

Errors are files that fail processing with an exception or timeout. The following are tracked separately and do NOT count as errors:

- **Unsupported**: files with unsupported MIME types (expected for non-image files)
- **Duplicate**: files with identical hashes already in the database
- **Hidden**: dot-files, system files, or files matching skip patterns

### Immediate fail (stop the tier, investigate)

- **Any data loss**: media records deleted, tags lost, or storage files missing after processing
- **Database corruption**: constraint violations, orphaned records, or schema inconsistency
- **Crash**: server process exits unexpectedly during processing
- **Security violation**: operations executed against wrong database, wrong storage root, or wrong env

### Non-blocking issues (document but continue)

- Individual file processing failures within the error threshold
- LLM translation timeouts that self-recover via retry
- CLIP model unavailable (classification falls back to heuristic)

## 9. Rollback Strategy

**CRITICAL**: This section is a **manual runbook for future use**. Rollback is NOT executed during Phase 3.2c (design-only). All destructive operations require explicit user confirmation — never automate rollback.

### Backup format

All backups use PostgreSQL custom archive format (`-Fc`), which supports `pg_restore` with `--exit-on-error` and `--single-transaction` for fail-fast semantics. Plain SQL dumps (`pg_dump > file.sql`) are **not** used because `psql -f` cannot guarantee atomic rollback.

```powershell
# Backup — run BEFORE each tier import (see Preflight Checklist item 14)
pg_dump -Fc -f backup_before_tier_N.dump blombooru_test_medium
```

### Database rollback procedure

**Do NOT run these commands without explicit user confirmation.** This is a controlled, manual runbook.

**Step 1 — Stop V.I.O.L.E.T. server**

The server must be stopped before drop/restore to release active DB connections. Stop only the exact PID you started — do not force-kill arbitrary processes.

```powershell
# Stop the known server PID (recorded at startup)
Stop-Process -Id <recorded-PID>
```

**Step 2 — Confirm target database**

Verify the target is exactly `blombooru_test_medium`. Triple-check — the following commands are irreversible:

```powershell
# MUST be blombooru_test_medium — NEVER blombooru or blombooru_test
$targetDb = "blombooru_test_medium"
```

**Step 3 — Drop, recreate, and restore (fail-fast)**

```powershell
# DESTRUCTIVE — requires explicit user confirmation
# This permanently deletes ALL data in blombooru_test_medium and restores from backup
dropdb $targetDb
createdb $targetDb
pg_restore --exit-on-error --single-transaction -d $targetDb backup_before_tier_N.dump
```

If `dropdb` fails because active sessions are connected, the app server was not fully stopped. Go back to Step 1. Do NOT use `--force` or kill unknown processes.

If `pg_restore` fails (`--exit-on-error` will abort on first error):
- The `--single-transaction` flag ensures the entire restore is rolled back on failure — the DB will be empty, not partially restored.
- **Stop. Report the error. Do not continue the pilot.**
- Investigate the backup file integrity before retrying.

### Storage rollback

```powershell
# DESTRUCTIVE — requires explicit user confirmation
# This permanently deletes ALL imported media and thumbnails in the medium pilot storage
Remove-Item -Recurse -Force C:\Users\kyloris\VioletStorage\medium\*
```

### Isolation guarantee

Pilot operations NEVER touch:
- `blombooru` (dev/production database)
- `blombooru_test` (unit/E2E test database)
- `C:\Users\kyloris\VioletStorage\test` (test storage)
- Any iCloud paths or source dataset directories (e.g., `D:\VioletPilotData\*`)
- VioletTestFixture directory

## 10. LLM Cost Control

- `AI_TAGGING_AUTO_LOCALIZATION=false` disables automatic tag translation after AI tagging jobs (see § 6.2) — **recommended for all pilot tiers** to keep LLM costs predictable
- `TAG_TRANSLATION_BACKGROUND_DAILY_LIMIT` caps daily LLM API calls per tier (see Section 5)
- `ENTITY_ALIAS_MAX_PER_RUN` limits alias resolution calls per execution
- Monitor LLM provider dashboard for actual token usage
- If costs exceed expectations, pause the background worker (`TAG_TRANSLATION_BACKGROUND_ENABLED=false`) and review

## 11. Per-Tier Report Template

After completing each tier, fill in this report:

```
## Tier [500/1000/2000] Pilot Report

### Environment
- Database: blombooru_test_medium
- Storage: C:\Users\kyloris\VioletStorage\medium
- Port: [port]
- git_sha: [sha]
- Code root: [path]
- Python executable: [python_executable from server identity]
- Python version: [python_version from server identity]
- is_venv: [true/false from server identity]
- Server identity check: [pass/fail] (with --expected-env, --expected-db, --expected-code-root, --expected-git-sha, --expected-python, --expected-storage-root)

### Pre-Import
- Dataset source path: [exact path, e.g. D:\VioletPilotData\500]
- Dataset inspector: inspect_pilot_dataset.py
- Dataset inspector results: [total/supported/unsupported/hidden/stat_errors]
- Backup file: [path, e.g. backup_before_tier_500.dump]
- Backup format: custom archive (pg_dump -Fc)
- Dry-run scan_job_id: [id]
- Dry-run result: [total files / supported / skipped / errors]

### Import
- scan_job_id: [id]
- Total files scanned: [N]
- Imported (new): [N]
- Skipped (duplicate): [N]
- Skipped (unsupported): [N]
- Errors: [N]
- Duration: [time]

### Content Classification
- Classified: [N]
- Method breakdown: CLIP [N] / heuristic [N]
- Anime: [N] / Non-anime: [N] / Unknown: [N]
- Duration: [time]

### AI Tagging
- Tagged: [N]
- Skipped (already tagged): [N]
- Errors: [N]
- tags_added (new Tag rows): [N]
- suggestions_added (new AI suggestion rows): [N]
- media_tags row delta: [N]
- tag row delta (net change in Tag table rows): [N]
- media_with_ai_tags delta: [N]
- AI-only isolation env vars: [all 4 set / partial / none]
- Translation worker status after startup: [running: false / running: true]
- Duration: [time]

### Tag Translation
- Translated: [N]
- Skipped (already translated): [N]
- Errors: [N]
- Daily limit used: [N/limit]
- Duration: [time]

### Entity Alias Resolution (if enabled)
- Resolved: [N]
- Errors: [N]
- Duration: [time]

### Performance
- Avg time per item (import): [ms]
- Avg time per item (AI tag): [ms]
- Peak memory: [MB]
- DB size after: [MB]

### Error Summary
[List specific errors, if any]

### Pass / Fail
- Import: [pass/fail]
- AI Tagging: [pass/fail]
- Translation: [pass/fail]
- Classification: [pass/fail]
- Data integrity: [pass/fail]
- Overall: [PASS/FAIL]

### Notes
[Any observations, performance concerns, or recommendations for the next tier]
```

## 12. Relationship to Existing Tools

`scripts/local_full_pipeline_smoke.py` validates the full pipeline end-to-end with a small fixture set (VioletTestFixture). It is NOT a substitute for medium-scale pilot testing:

- Smoke script uses a curated ~30 image fixture; pilot uses 500-2000 real images
- Smoke script validates feature correctness; pilot validates scale behavior
- Smoke script runs in minutes; pilot tiers may take hours

The smoke script CAN be used as a pre-pilot sanity check to confirm the pipeline is functional before starting a tier.

### Dataset inspection tools

| Script | Purpose | Directory structure |
|--------|---------|---------------------|
| `scripts/inspect_test_fixture.py` | VioletTestFixture (small smoke) | Requires `anime/non_anime/mixed` subfolders |
| `scripts/inspect_pilot_dataset.py` | Medium-scale pilot datasets | Any flat or nested directory |

Use `inspect_pilot_dataset.py` for all pilot tiers. `inspect_test_fixture.py` is for VioletTestFixture only and will report errors on directories that lack the expected subfolder structure.
