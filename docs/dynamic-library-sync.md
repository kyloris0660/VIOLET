# Dynamic Library Sync

Phase 4.7 makes source-library synchronization a durable product feature rather
than a one-off runner. The source library may change daily, so V.I.O.L.E.T.
tracks source roots, source items, update checks, import readiness, AI tagging
readiness, and tag-localization readiness in the application database.

## Source root configuration

Dynamic sync source roots are stored in `blombooru_dynamic_source_roots`.
Each root has a label, absolute root path, private path hash, active flag,
per-root threshold, and timestamps. Source roots must be external directories:
they must not be filesystem roots, project code roots, app storage roots, or
paths overlapping app-managed storage.

Source root identity uses the resolved root path plus platform-aware
`os.path.normcase` normalization. This preserves case on case-sensitive
filesystems while retaining stable case-insensitive behavior on Windows-style
filesystems. This hash semantic is safe to set now because S1 has not been
merged into production and no production dynamic sync state exists yet.

The Admin Content tab includes a Dynamic Library Sync panel for registering
source roots and viewing configured roots. Public reports must not expose raw
source paths or deterministic source-root hash prefixes; use aggregate counts
and run-local opaque labels instead.

## Manual update flow

The default product flow is manual and observable:

1. Register one or more source roots.
2. Click **Check for updates** or **Run dry-run**.
3. The backend performs a metadata-only scan.
4. Source item state is persisted in `blombooru_dynamic_source_items`.
5. A run record is persisted in `blombooru_dynamic_sync_runs`.
6. Per-run observations are persisted in `blombooru_dynamic_sync_run_items`.
7. Pending counts and readiness warnings update in Admin UI.

S1 update checks do not import media, copy files, compute content hashes by
default, run classification, run AI tagging, call providers, call LLMs, mutate
SourceConcept, mutate Entity tables, or touch source files.

`max_files` is an aggregate cap across all selected roots. When an update check
is capped, affected root summaries are explicitly partial. Partial root scans
record `partial_scan=true`, `missing_reconciliation_skipped=true`, and a safe
reason such as `max_files_cap` or `source_walk_error`. Partial checks do not
mark unseen tracked items as missing; only complete root scans may perform
missing reconciliation.

`root_ids=None` means all active roots. An explicit empty selection
`root_ids=[]` is rejected instead of scanning all roots. Non-empty selections
scan only the requested active roots.

The application session uses `autoflush=False`, so update checks explicitly
flush observed item and run-item state before missing reconciliation and before
pending-count snapshots. The embedded pending snapshot for a just-finished run
is built after final run counts/status are flushed, so it does not report the
current run as `running` with zero counts.

## Pending count calculation

Pending state is DB-backed and does not depend on `.local_manifests`.

- `pending_new`: source items with `sync_state='new'` and
  `import_status='pending'`.
- `pending_changed`: source items with `sync_state='changed'` and
  `import_status='pending'`.
- `pending_deferred`: items deferred, failed, or missing because they are not
  eligible for DB import yet.
- `pending_import`: `pending_new + pending_changed`.
- `total_pending`: `pending_import + pending_deferred`.

Source item identity is `source_root_id + relative_path_hash`; `media_id` is a
link after import, not the incremental sync identity. The relative path hash is
computed from the case-preserving normalized relative path, so case-sensitive
roots do not collapse `A.jpg` and `a.jpg` into one source item. This hash
semantic is safe to set now because S1 has not been merged into production and
no production dynamic sync state exists yet.

## Threshold policy

The default threshold is `100`, configured by
`DYNAMIC_LIBRARY_SYNC_THRESHOLD`. Admin UI shows threshold status. Reaching the
threshold is a warning and decision signal; it does not trigger unattended
production writes by itself.

## Default-off production writes

Dynamic sync capability exists, but unattended production writes remain off by
default.

- Manual check-for-updates is available.
- Pending counts are visible.
- Threshold status is visible.
- Manual sync execution is gated by `DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED`,
  default `false`.
- Unattended auto-sync is gated by `DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED`,
  default `false`.

Any threshold-triggered or scheduled production write must require explicit
user opt-in and visible UI/config. S1 exposes a `sync-pending` API surface, but
it fails closed while manual execution is disabled.

## AI tagging and tag localization chain

S2 should treat AI tagging and tag localization as one execution chain:

```text
baseline import
-> AI tagging job
-> new tags collected
-> _schedule_localization
-> background worker / auto translate
-> blombooru_tag_translations
-> frontend Chinese display and trusted search aliases
```

Required readiness switches are reported by the Dynamic Library Sync panel:

- `AI_TAGGING_ENABLED`
- `AI_AUTO_TAG_AFTER_IMPORT`
- `AI_TAGGING_AUTO_LOCALIZATION`
- `TAG_TRANSLATION_LLM_ENABLED`
- `TAG_TRANSLATION_AUTO_ENABLED`
- `TAG_TRANSLATION_BACKGROUND_ENABLED`
- `TAG_TRANSLATION_BACKGROUND_CATEGORIES`
- `TAG_TRANSLATION_BACKGROUND_DAILY_LIMIT`
- `TAG_TRANSLATION_BACKGROUND_BATCH_SIZE`
- `TAG_TRANSLATION_BACKGROUND_MAX_PER_RUN`

`AI_TAGGING_AUTO_LOCALIZATION` defaults to enabled unless explicitly disabled.
LLM and background translation remain disabled unless configured.

## Proper-noun localization safety

General/meta tag localization and proper-noun aliasing are separate concerns.

- Background translation is intended for general/meta tags by default.
- `TAG_TRANSLATION_BACKGROUND_CATEGORIES` defaults to `general,meta`.
- Character, copyright, and artist tags are proper nouns.
- LLM-generated proper-noun aliases must remain reviewed/untrusted unless
  manually approved or provided by static/manual trusted sources.
- Chinese search aliases for proper nouns must continue to rely on trusted
  manual/static aliases or reviewed Entity Alias Resolver output.
- Dynamic sync must not create confirmed Entity assignments or pollute
  `media_tags`/Entity truth.

## Retry, backfill, and resume behavior

Every source item keeps first seen, last seen, last checked, last imported,
last sync run, status, and deferred/failure reason fields. This enables:

- rechecking unchanged pending files without duplicate source item rows;
- detecting changed files before import;
- marking previously seen files as missing when they disappear;
- retrying deferred, failed, or missing items after cloud hydration or operator
  action, even when size and mtime did not change;
- backfilling classification, AI tagging, and localization status after S2/S3
  runs.

Per-item failures are visible and do not automatically block all future checks.
Large or automated imports should still use failure budgets and GOV3 contracts.

Symlinks and resolved path escapes are item-level deferred states for S1
metadata checks. They record safe reasons such as `symlink` or `path_escape`,
remain ineligible for import, and do not crash the whole update check or corrupt
missing reconciliation for unrelated items.

If a DB flush/commit fails during an update check, the service rolls back the
failed transaction before marking the sync run as failed in a clean transaction.
If the failed status itself cannot be persisted, the original exception is still
re-raised and the persistence failure is logged.

The executable dynamic sync contract also requires browser validation status to
be `passed` before an S1 summary may claim `target_met` or another completion
state.

Long-running update checks currently execute through the admin API path. Before
large-root production checks or S3 automation, this should move to an
offloaded/background job model so the FastAPI event loop is not occupied by
large filesystem walks.

## S2 and S3 expectations

S2 should run only after explicit approval and after S1 readiness is reviewed.
It should execute the approved baseline full import, classification, AI tagging,
and tag localization chain with production DB/storage identity, backup proof,
dry-run proof, public redaction checks, and relevant GOV3 contracts.

S2 has a Gate 0 schema/readiness preparation step before Gate 1 execution
readiness. If production has not yet run the S1 dynamic sync migration, the S2
runner must first prove production DB identity, require private backup/recovery
proof, and then reuse the existing additive dynamic sync migration path. Missing
dynamic sync tables without backup proof are an actionable blocked state, not a
completed S2 delivery. Backup proof must validate the expected/actual database
name, successful backup command exit code, dump file existence and non-empty
state, creation time, and recovery notes before schema setup can run.

Source roots may be registered by CLI/Admin/API, or from configured
`LOCAL_LIBRARY_PATHS` when the runner is explicitly asked to register roots.
Registration is allowed only after production env, DB identity, storage identity,
and dynamic sync schema gates are clean. Public reports must expose only
aggregate counts, non-sensitive labels, and run-local opaque root labels.

S2 readiness reports unreviewed LLM-generated proper-noun aliases separately.
Those aliases require manual/static trusted handling or reviewed Entity Alias
Resolver output before they can influence Chinese search behavior. If the search
trust policy excludes unreviewed LLM proper-noun aliases, the gap remains visible
but does not by itself block the dry-run gate.

S3 should add daily/incremental automation and hardening only after S2 baseline
state is validated. Any automatic production write must be opt-in, visibly
configurable, resumable, auditable, and disabled by default.
