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

- [ ] 1. `VIOLET_ENV=test` confirmed
- [ ] 2. `POSTGRES_DB=blombooru_test_medium` confirmed
- [ ] 3. `VIOLET_STORAGE_ROOT=C:\Users\kyloris\VioletStorage\medium` confirmed
- [ ] 4. Database created: `python scripts/setup_test_db.py` with `POSTGRES_DB=blombooru_test_medium`
- [ ] 5. Schema migrated: `python scripts/setup_test_db.py --migrate` with test env
- [ ] 6. Storage directory exists and is empty (or contains only previous tier data if continuing)
- [ ] 7. Dataset directory exists with expected file count
- [ ] 8. Dataset validated: `python scripts/inspect_test_fixture.py --path <dataset_dir>`
- [ ] 9. Server started on dynamic port with identity check passed
- [ ] 10. `scripts/check_test_server_identity.py` confirms correct `VIOLET_ENV`, `POSTGRES_DB`, `code_root`, `git_sha`
- [ ] 11. Database backup taken: `pg_dump blombooru_test_medium > backup_before_tier_N.sql`
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

**CRITICAL**: All destructive operations below require explicit user confirmation. Do NOT automate rollback without user approval.

### Database rollback

```powershell
# Only execute after explicit user confirmation
pg_restore -d blombooru_test_medium backup_before_tier_N.sql
```

If `pg_dump` backup is unavailable, the database can be dropped and recreated:

```powershell
# DESTRUCTIVE — requires user confirmation
# This permanently deletes ALL data in blombooru_test_medium
dropdb blombooru_test_medium
python scripts/setup_test_db.py  # with POSTGRES_DB=blombooru_test_medium
python scripts/setup_test_db.py --migrate
```

### Storage rollback

```powershell
# DESTRUCTIVE — requires user confirmation
# This permanently deletes ALL imported media and thumbnails
Remove-Item -Recurse -Force C:\Users\kyloris\VioletStorage\medium\*
```

### Isolation guarantee

Pilot operations NEVER touch:
- `blombooru` (dev/production database)
- `blombooru_test` (unit/E2E test database)
- `C:\Users\kyloris\VioletStorage\test` (test storage)
- Any iCloud or VioletTestFixture paths

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
- Server identity check: [pass/fail]

### Import
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
