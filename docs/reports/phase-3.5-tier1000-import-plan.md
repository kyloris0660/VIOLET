# Phase 3.5 Tier-1000 DB Import Plan

Date: 2026-05-19

## Confirmed Baseline

- Repository: `kyloris0660/AnimeLocalBooru`
- Working branch: `phase3.5-tier1000-db-import`
- Base commit: `6e98df8` (`feat: self-contained Tier-1000 pre-import audit (Phase 3.4) (#48)`)
- PR #45, #46, and #48 were verified merged through GitHub CLI.
- Approved Python: project venv Python, verified by `scripts/check_python_env.py`
- Phase 3.4 audit summary gate:
  - `result=PASS`
  - `expected_copy_count=1000`
  - `copy_rows=1000`
  - `target_pass=1000`
  - `copy_count_matches_expected=true`
- Staging root exists and is represented in public reports as `tier1000_staging`
- Candidate manifest exists: `.local_manifests\phase-3.3a.1-candidate-manifest.csv`

## Architecture Choice

The import uses app-managed copy mode:

1. Read only from the audited staged files.
2. Copy non-duplicate files into `media/original`.
3. Generate thumbnails into `media/thumbnails`.
4. Store only storage-root-relative paths in `blombooru_media.path` and `thumbnail_path`.
5. Store `Media.source = "violet:tier1000:phase3.5"` instead of any full iCloud/source/staging path.

The tool intentionally does not call `run_scan_job()` or `scan_and_import()`, because those paths are directory-scan oriented and can interact with background AI/classification behavior. Phase 3.5 needs a manifest-driven importer with explicit gates.

No DB schema migration is required.

## New Tool

`scripts/import_staged_manifest.py`

Required modes:

- `--dry-run`: validates audit summary, manifest, staged files, DB/storage identity, duplicate hash state, and background-disable flags. It writes reports only and does not write DB, copy files, or generate thumbnails.
- `--execute`: imports after all gates pass. Requires exact confirmation phrase `IMPORT_TIER1000_TO_DB`.

Required output:

- Privacy-safe report JSON under `docs/reports/`.
- Local result CSV under `.local_manifests/`, allowed to contain full local paths and intentionally not committed.

## Transaction And Failure Design

- Per-file DB transaction, not all-or-nothing.
- Copy and thumbnail generation happen before the DB insert.
- DB insert rechecks duplicate hash inside the per-file transaction.
- On unexpected per-file failure, rollback the DB transaction, remove only files created for that failed item, record failure, and stop.
- Known duplicates are skipped without copying.

## Duplicate Policy

- Existing DB hash duplicate: skip, no copy, no media row.
- Internal manifest hash duplicate: skip later duplicate row, no copy, no media row.
- Same staged target path/source path collisions are reported from manifest stats.
- Rerun after import must be idempotent: all 1000 rows should resolve to duplicates and create no new rows.

## Safety Gates

- Approved venv Python preflight must pass.
- `VIOLET_ENV`, DB name, storage root, target root, and background-disable flags are printed in reports.
- Target root must exist and must not be inside app-managed storage.
- App-managed storage must not be inside target root.
- Phase 3.4 audit summary must be PASS and match expected count 1000.
- Execute requires a nonzero DB backup artifact passed with `--db-backup-file`.
- Execute requires exact confirmation flag.
- AI tagging, AI auto-import, AI auto-localization, tag translation background/auto/LLM, Entity Resolver, and content classification flags must all be disabled.

## Test Scope

`tests/test_import_staged_manifest.py` covers:

- CLI mode/confirmation safety.
- Audit summary mismatch failure.
- Target path escape detection.
- Dry-run duplicate detection.
- Internal manifest hash duplicate detection.
- Execute path stores relative managed paths and generates thumbnails.
- Execute treats thumbnail generation failure as a per-file failure and does not insert a row with `thumbnail_path=NULL`.
- Post-import audit counts `thumbnail_path=NULL` as a missing thumbnail.
- Dry-run `estimated_bytes_to_copy` excludes DB duplicates and internal manifest duplicates.
- Public report sanitization redacts Windows and common POSIX absolute paths.
- Rerun duplicate idempotency.
- Per-file failure rollback and cleanup.
- Privacy-safe JSON report behavior.
