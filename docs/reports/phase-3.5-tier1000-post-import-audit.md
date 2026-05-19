# Phase 3.5 Tier-1000 Post-import Audit

Date: 2026-05-19

## Backup

The first `pg_dump` attempt timed out while waiting for password input. The exact `pg_dump.exe` process was identified and stopped (`PID 35304`), then the backup was retried with `PGPASSWORD` loaded from local config without printing the value.

Successful backup:

- Path: `backups\phase-3.5-tier1000-before-20260519-161743.dump`
- Format: `pg_dump -Fc`
- Size: 167,099 bytes
- DB: `blombooru`

`backups/` is gitignored and this backup must not be committed.

## Execute Result

Report: `docs/reports/phase-3.5-tier1000-import-summary.json`

| Metric | Result |
|---|---:|
| `manifest_copy_rows` | 1000 |
| `target_files_checked` | 1000 |
| `imported` | 995 |
| `duplicates_by_hash` | 5 |
| `invalid` | 0 |
| `failed` | 0 |
| `media_count.before` | 0 |
| `media_count.after` | 995 |
| `estimated_bytes_to_copy` | 3,204,263,387 |

The 5 duplicate rows were same-hash duplicates within the manifest, detected safely during per-file transaction recheck. No duplicate file copy was kept for those rows.

## Storage And DB Audit

| Check | Result |
|---|---:|
| Imported IDs checked | 995 |
| DB rows found | 995 |
| Original files found | 995 |
| Thumbnails found | 995 |
| Missing original/thumbnail files | 0 |
| Source label mismatches | 0 |

Storage stats:

| Location | Before | After | Delta |
|---|---:|---:|---:|
| `media/original` files | 284 | 1279 | +995 |
| `media/original` bytes | 2,232,405,586 | 5,400,244,517 | +3,167,838,931 |
| `media/thumbnails` files | 283 | 1278 | +995 |
| `media/thumbnails` bytes | 4,727,154 | 21,137,513 | +16,410,359 |

The importer stored `Media.source = "violet:tier1000:phase3.5"` and relative managed paths such as `media/original/...`; it did not store full iCloud/source/staging paths in DB.

## Corrective Fix During Validation

Initial API smoke validation exposed that raw SQL insert had not set Python-side ORM defaults for `is_shared` and `share_ai_metadata`, leaving them NULL for the 995 imported rows and causing Pydantic response validation failures.

Remediation:

- Script fixed to explicitly insert:
  - `is_shared=false`
  - `share_ai_metadata=false`
  - `content_class_locked=false`
  - `content_class_reviewed=false`
- Existing Phase 3.5 imported rows were corrected only where `source='violet:tier1000:phase3.5'`.
- Rows updated: 995
- NULL counts after correction:
  - `is_shared`: 0
  - `share_ai_metadata`: 0
  - `content_class_locked`: 0
  - `content_class_reviewed`: 0

## App-level Validation

Temporary server:

- URL: `http://127.0.0.1:8012`
- Branch: `phase3.5-tier1000-db-import`
- Identity check: PASS
- `VIOLET_ENV`: `development`
- DB: `blombooru`
- Storage root: `C:\Users\kyloris\Documents\AnimeLocalBooru`
- Python executable: `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`
- Server process tree stopped after validation: `PID 24280`, `35072`, `35648`

API/static smoke:

| Check | Result |
|---|---:|
| `GET /api/media?limit=5` total | 995 |
| Returned items | 5 |
| First imported media ID | 712 |
| `GET /api/media/712` | PASS |
| `GET /api/media/712/file` | HTTP 200, 6,353,446 bytes |
| `GET /api/media/712/thumbnail` | HTTP 200, 7,419 bytes |
| `GET /` gallery page | HTTP 200 |

Playwright with system Edge:

| Check | Result |
|---|---:|
| Gallery status | 200 |
| Gallery title | `V.I.O.L.E.T.` |
| Gallery image count | 65 |
| Media detail status | 200 |
| Media detail image count | 3 |

Background side-effect audit since import start:

| Table | Rows since import start |
|---|---:|
| `blombooru_ai_tag_jobs` | 0 |
| `blombooru_classification_jobs` | 0 |
| `blombooru_tag_translation_jobs` | 0 |
| `blombooru_tag_translations` | 0 |

## Test Results

Commands were run with the approved venv Python:

```powershell
& "$PY" scripts/check_python_env.py --expected-python "$PY"
& "$PY" -c "import sys; print(sys.executable); print(sys.version)"
git diff --check
& "$PY" -m py_compile scripts/import_staged_manifest.py
& "$PY" -m pytest tests/test_import_staged_manifest.py tests/test_audit_tier1000.py tests/test_env_safety.py tests/test_destructive_gate.py tests/test_config_precedence.py tests/test_media_processor_mime_magic_cache.py -v
& "$PY" -m pytest tests/ -v --ignore=tests/e2e
```

Results:

- Python preflight: PASS, `sys.executable` matched approved venv Python, Python 3.12.0.
- `git diff --check`: PASS.
- Focused Phase 3.5/import safety suite: PASS.
- Full non-E2E suite: 864 passed, 10 skipped, 3 warnings.
- Warnings: existing Pydantic V2 class-based config deprecation warnings.

## Safety Confirmation

- No source/iCloud/staging mutation.
- No AI tagging, classification, LLM, localization, or Entity Resolver execution.
- No cleanup/reset/drop/truncate.
- No push to `main`.
- No PR merge.
