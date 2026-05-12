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
3. AI tagging (WDv3)
4. Tag translation (LLM)
5. Entity alias resolution (LLM, if enabled)

Each step's output feeds the next. Do not skip steps or run them out of order.

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

- [ ] 9. Server started on dynamic port (probe 8012–8024)
- [ ] 10. Identity check passed with **explicit expected args** (hard gate — do not proceed without this):

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
  --admin-password "<your admin password>"
```

If the endpoint requires admin auth, add `--admin-username` and `--admin-password`. Do NOT commit real credentials. Running without `--expected-*` args is **not sufficient** for medium pilot — the script must explicitly verify env, DB, code root, git SHA, and Python executable. Any identity mismatch is an **immediate fail**: stop, diagnose, do not continue with E2E, import, or any processing.

- [ ] 11. Database backup taken (custom archive format):

```powershell
pg_dump -Fc -f backup_before_tier_N.dump blombooru_test_medium
```

- [ ] 12. Dry-run import completed and results reviewed

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
# Backup — run BEFORE each tier import (see Preflight Checklist item 11)
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
- Server identity check: [pass/fail] (with --expected-env, --expected-db, --expected-code-root, --expected-git-sha, --expected-python)

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
