# Phase 4.7-S1: Dynamic Sync Product Foundation

## 1. Summary

Phase 4.7-S1 converts the full-library route from a one-off import runner into
a durable product feature foundation. It adds DB-backed dynamic sync state,
admin APIs, an Admin UI panel, default-off production write controls, focused
tests, and long-term documentation.

Contract status: passed `dynamic_library_sync_contract_v1` validation.

## 2. Feature scope

Implemented:

- Durable dynamic source roots, source items, sync runs, and per-run item
  status.
- Manual check-for-updates and dry-run update check.
- Pending new/changed/deferred counts and threshold status.
- Explicit partial scan state for capped checks, with missing reconciliation
  skipped on capped roots.
- Explicit partial scan state for unreadable or skipped subtrees, with missing
  reconciliation skipped on affected roots.
- Case-preserving relative path identity for source item hashes.
- Rollback-before-failed-status handling for update-check failures.
- Default threshold policy: `100`.
- Default-off policy for unattended production writes and S1 manual sync
  execution.
- Admin Dynamic Library Sync UI.
- AI tagging and tag localization readiness reporting for the S2 baseline run.
- Localization gap and proper-noun safeguard reporting.

Not executed:

- No full production import.
- No production DB import.
- No full AI tagging run.
- No full LLM localization batch.
- No provider calls.
- No SourceConcept, R1R, A1R, R2, or Entity bridge work.
- No source/iCloud mutation.
- No destructive cleanup.

## 3. DB/schema changes

Added idempotent DIY migrations in `backend/app/database.py` for:

- `blombooru_dynamic_source_roots`
- `blombooru_dynamic_source_items`
- `blombooru_dynamic_sync_runs`
- `blombooru_dynamic_sync_run_items`

The tables preserve source identity separately from imported media identity.
Indexes cover root/path hash lookup, content hash, import/classification/AI/
localization status, last seen run, media id, and run item status.

Large filesystem metadata fields use big integer storage where needed, including
file size, nanosecond mtime, and copied byte counters.

## 4. Backend/API changes

Added `backend/app/services/dynamic_library_sync_service.py` and
`backend/app/routes/admin/dynamic_library_sync.py`.

Admin endpoints:

- `GET /api/admin/dynamic-library-sync`
- `GET /api/admin/dynamic-library-sync/source-roots`
- `POST /api/admin/dynamic-library-sync/source-roots`
- `POST /api/admin/dynamic-library-sync/check`
- `GET /api/admin/dynamic-library-sync/pending-summary`
- `GET /api/admin/dynamic-library-sync/readiness`
- `POST /api/admin/dynamic-library-sync/sync-pending`

The S1 sync-pending endpoint fails closed by default and does not import media.
If `DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED=true`, the endpoint still returns a
501 S1 boundary response because import execution belongs to approved S2.

Config additions:

- `DYNAMIC_LIBRARY_SYNC_THRESHOLD`, default `100`
- `DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED`, default `false`
- `DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED`, default `false`

Readiness/config diagnostics now include the AI tagging auto-localization and
tag translation background settings needed by S2.

## 5. Frontend/UI changes

The Admin Content tab now includes a Dynamic Library Sync panel. It shows:

- configured source roots;
- production readiness and blockers;
- last sync run;
- pending new/changed/deferred counts;
- threshold reached status;
- Check for updates / Run dry-run / View summary / Sync pending controls;
- AI tagging readiness;
- tag localization readiness and worker status;
- localization gap summary;
- proper-noun safeguard status.

The Sync pending button is disabled unless manual sync execution is explicitly
enabled and safe. The UI does not style blockers as success.

## 6. Sync state model

Source item identity is:

```text
source_root_id + relative_path_hash
```

`media_id` is only a post-import link. The durable state records file size,
mtime, mtime_ns, optional content hash, first/last seen, last checked, imported
media id, import/classification/AI/localization status, deferred/failure reason,
and the last sync run id.

The executable contract now fails unless public S1 summaries declare both
`source_root_id` and `relative_path_hash` as the source item identity
components.

`relative_path_hash` is computed from the case-preserving normalized relative
path. This avoids merging distinct case-sensitive files such as `A.jpg` and
`a.jpg`. The semantic change is safe because S1 has not been merged into
production and no production dynamic sync state exists yet.

## 7. Manual update flow

The S1 flow is metadata-only:

1. Register a source root.
2. Run Check for updates or Run dry-run.
3. Record a `dry_run` sync run.
4. Walk hydrated scannable files only.
5. Upsert source item state.
6. Record per-run item observations with `action='record_only'`.
7. For complete root scans only, mark disappeared historical items as missing.
8. Update pending counts and threshold warning.

No media import, copy, classification, AI tagging, LLM translation, provider
call, SourceConcept mutation, Entity mutation, or source/iCloud mutation occurs.

Capped update checks are partial scans. When `max_files` stops a root scan, the
root summary records `partial_scan=true`,
`missing_reconciliation_skipped=true`, and
`missing_reconciliation_reason=max_files_cap`. Unseen tracked items beyond the
cap are not marked `missing` or `deferred`.

`max_files` is an aggregate cap across all selected roots. It is not interpreted
as a per-root cap.

Unreadable or temporarily unavailable subtrees are also partial scans. Affected
root summaries record `missing_reconciliation_reason=source_walk_error` and do
not run full-root missing reconciliation.

Deferred, failed, or missing items that become eligible again are requeued for
pending import even if file size and mtime did not change. Symlinks and resolved
path escapes are recorded as item-level deferred states with safe reasons such
as `symlink` or `path_escape`.

If a DB write fails during update check processing, the service rolls back the
failed transaction before marking the run `failed` in a clean transaction.

## 8. Threshold policy

The default threshold is `100`. Reaching the threshold is a visible warning and
operator decision signal only. It never triggers unattended production writes in
S1.

## 9. AI tagging readiness

S1 reports the AI tagging settings S2 needs, including:

- `AI_TAGGING_ENABLED`
- `AI_AUTO_TAG_AFTER_IMPORT`
- `AI_TAGGING_AUTO_LOCALIZATION`
- AI batch limits and dry-run auto-after-import settings

`AI_TAGGING_AUTO_LOCALIZATION` remains enabled by default unless explicitly
disabled.

## 10. AI tagging -> localization integration proof

S1 verified and exposes this intended S2 chain:

```text
baseline import
-> AI tagging job
-> new tags collected
-> _schedule_localization
-> background worker / auto translate
-> blombooru_tag_translations
-> frontend Chinese display and trusted search aliases
```

Focused tests cover the integration gate and S1 readiness reporting without
running a full AI tagging job or LLM batch.

## 11. Tag localization readiness

S1 reports:

- `TAG_TRANSLATION_LLM_ENABLED`
- `TAG_TRANSLATION_AUTO_ENABLED`
- `TAG_TRANSLATION_BACKGROUND_ENABLED`
- `TAG_TRANSLATION_BACKGROUND_CATEGORIES`
- `TAG_TRANSLATION_BACKGROUND_DAILY_LIMIT`
- `TAG_TRANSLATION_BACKGROUND_BATCH_SIZE`
- `TAG_TRANSLATION_BACKGROUND_MAX_PER_RUN`
- localization gap counts by category
- background worker category safety

Chinese display and canonical fallback remain the frontend behavior for
translated and untranslated tags.

## 12. Proper-noun safeguard policy

General/meta translation and proper-noun aliasing stay separate.

- General/meta tags may be handled by background translation when configured.
- `character`, `copyright`, and `artist` remain proper-noun categories.
- Background translation categories must exclude proper nouns by default.
- LLM proper-noun aliases require review/trusted sources and must not pollute
  Chinese search.
- Trusted proper-noun aliases remain manual/static or reviewed Entity Alias
  Resolver output.

## 13. Production readiness for S2

Ready:

- Dynamic sync DB state.
- Manual update checks.
- Pending count and threshold logic.
- AI tagging readiness reporting.
- Tag localization readiness reporting.
- Proper-noun safeguards.
- Admin UI visibility for blockers and warnings.

Remaining S2 blockers:

- Configure and approve production source roots.
- Capture production DB/storage/app identity and backup proof before execution.
- Explicitly approve baseline import and any manual pending sync execution.
- Enable and verify LLM/background translation settings if S2 will execute
  localization.
- Run S2 dry-run, public redaction checks, and relevant GOV3 contracts before
  production writes.

## 14. Tests and validation

Passed:

- `scripts/check_phase_contract.py --contract dynamic_library_sync_contract_v1 --summary docs/reports/phase-4.7-s1-dynamic-sync-product-foundation-summary.json --explain` -> passed
- `git diff --check`
- `git diff --cached --check`
- `scripts/check_python_env.py --expected-python <repo-venv-python>`
- `python -m py_compile <changed_python_files>`
- `pytest tests/test_phase46_fulllib_e1a_runner_dryrun.py tests/test_phase45_doc1_documentation_state.py -v` -> 39 passed
- `pytest tests/test_dynamic_library_sync.py tests/test_ai_tagging_localization_gate.py tests/test_phase_contracts.py tests/test_server_startup_imports.py tests/test_config_precedence.py -v` -> 134 passed
- `npx playwright test tests/e2e/admin-content.spec.ts --project=edge` -> 6 passed

Real browser validation:

- Method: Playwright Edge plus in-app Browser.
- Test server: `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test`, local test
  storage, port `8012`.
- Validated: Admin login, Content navigation, Dynamic Library Sync panel,
  threshold 100, pending counts, default-off warning, disabled Sync pending
  button, AI/localization readiness, proper-noun safeguard text.
- Browser console errors: `0`.
- Test server was stopped after validation.
- Reviewer fix note: no UI files changed after the original browser validation,
  so targeted browser validation was not rerun for the current-head backend and
  contract fixes.

## 15. Remaining blockers before S2

- S2 execution approval.
- Production identity and backup proof.
- Production source root registration.
- Baseline import dry-run and safety contract proof.
- Localization provider/worker settings reviewed before any LLM batch.
- Proper-noun alias route remains manual/static/reviewed only.
- Localization gap COUNT-query optimization remains deferred.
- Update checks should move off the FastAPI event loop before large-root or
  automated S3 use.
- Broader proper-noun search alias hardening remains deferred unless a small
  S2/S3 change explicitly scopes it.

## 16. Safety confirmation

- No full production import.
- No production DB data import.
- No full AI tagging run.
- No full LLM localization batch.
- No provider/Pixiv/gallery-dl/SauceNAO/Google calls.
- No SourceConcept/R1R/A1R/R2/Entity bridge work.
- No confirmed assignment creation.
- No source metadata mutation.
- No source/iCloud mutation.
- No app-storage mutation.
- No destructive cleanup/delete/reset/drop/truncate.
- No push to main.
- No merge.

## 17. Recommended S2 execution plan

1. Confirm production DB/storage/app identity and backup.
2. Register approved production source roots.
3. Run bounded dynamic update check and review pending counts.
4. Run S2 dry-run import plan with public redaction checks.
5. Execute baseline full import only after explicit approval.
6. Run classification and AI tagging jobs.
7. Let AI tagging schedule localization for newly created tags.
8. Run background/auto translation for approved general/meta categories.
9. Produce localization gap and proper-noun review reports.
10. Preserve proper-noun aliases for manual/static/reviewed entity paths only.
