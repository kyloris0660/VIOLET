# Phase 3.5 Tier-1000 Import Dry-run

Date: 2026-05-19

## Command

```powershell
& "$PY" scripts/import_staged_manifest.py `
  --manifest .local_manifests\phase-3.3a.1-candidate-manifest.csv `
  --target-root <tier1000_staging> `
  --audit-summary docs\reports\phase-3.4-audit-summary.json `
  --expected-copy-count 1000 `
  --dry-run `
  --report-json docs\reports\phase-3.5-tier1000-dry-run.json `
  --local-result-csv .local_manifests\phase-3.5-tier1000-dry-run-results.csv
```

All Phase 3.5 background side-effect flags were explicitly set to false for the run:

- `AI_TAGGING_ENABLED=false`
- `AI_AUTO_TAG_AFTER_IMPORT=false`
- `AI_TAGGING_AUTO_LOCALIZATION=false`
- `TAG_TRANSLATION_BACKGROUND_ENABLED=false`
- `TAG_TRANSLATION_AUTO_ENABLED=false`
- `TAG_TRANSLATION_LLM_ENABLED=false`
- `ENTITY_ALIAS_RESOLVER_ENABLED=false`
- `CONTENT_CLASSIFICATION_ENABLED=false`
- `CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT=false`

## Current Read-only Idempotency Dry-run Result

Report: `docs/reports/phase-3.5-tier1000-dry-run.json`

| Metric | Result |
|---|---:|
| `db_name` | `blombooru` |
| `storage_root_label` | `app_storage` |
| `target_root_label` | `tier1000_staging` |
| `manifest_copy_rows` | 1000 |
| `target_files_checked` | 1000 |
| `invalid` | 0 |
| `duplicates_by_hash` | 1000 |
| `would_create` | 0 |
| `estimated_bytes_to_copy` | 3,204,263,387 |
| `media_count.before` | 995 |
| `media_count.after` | 995 |

This is a post-import read-only idempotency dry-run against the current DB state. It did not write DB rows, copy files, generate thumbnails, or mutate staging/source files.

## Execute-time Duplicate Discovery

During execute, 5 later manifest rows were safely skipped as duplicate hashes after earlier rows had already been inserted. The importer rechecked hash uniqueness inside each per-file DB transaction, so this did not create duplicate media rows or orphan copies.

The tool was then hardened so dry-run also detects internal manifest hash duplicates before execute. This is covered by `test_dry_run_plan_detects_internal_manifest_hash_duplicate`.

Historical note: the initial pre-import dry-run was run before the DB had these media rows and predicted creates. After the real import completed, the committed public dry-run evidence was refreshed to the current truthful state: `duplicates_by_hash=1000`, `would_create=0`, `media_count` unchanged.

## Post-import Idempotency Dry-run

Report: `docs/reports/phase-3.5-tier1000-post-import-idempotency-dry-run.json`

| Metric | Result |
|---|---:|
| `media_count.before` | 995 |
| `media_count.after` | 995 |
| `target_files_checked` | 1000 |
| `duplicates_by_hash` | 1000 |
| `would_create` | 0 |
| `invalid` | 0 |
| `failed` | 0 |

This confirms the import is idempotent after the first successful run.

## Private Artifacts

The local result CSV files are intentionally under `.local_manifests/` and must not be committed because they include full local source/staging paths.
