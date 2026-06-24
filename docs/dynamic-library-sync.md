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

## S2G-M1 manual sync dry-run foundation

S2G-M1 adds a dry-run-only manual sync planner and status API for the later
S3A-M1 execute-path, final controls, and acceptance phase.

Routes:

- `GET /api/admin/dynamic-library-sync/manual-sync/status`
- `POST /api/admin/dynamic-library-sync/manual-sync/plan`

The plan endpoint is intentionally implemented as a normal FastAPI `def`
route, not an `async def` route. FastAPI runs the synchronous filesystem walk,
PIL verification, and hash reads in its worker threadpool so the event loop is
not occupied by manual planning.

The plan endpoint accepts exactly one of a registered `root_id` or an explicit
safe source path. It requires `hydrated_only=true`; cloud-unsafe
`hydrated_only=false` requests fail closed at both route and service level. It
validates source-root policy, walks bounded candidates, checks unsupported
files, iCloud/cloud placeholders, zero-byte files, changing files, corrupt
images, duplicate hashes, and already-imported media, then returns public-safe
counts and per-file safe labels. Per-file image verification and hash reads are
bounded by the scan file-open timeout and report stable reason codes such as
`read_timeout`, `read_error`, `stat_error`, or `corrupted_image`; one item-level
timeout does not abort the whole plan. Scanner policy skips such as `hidden`,
`too_large`, and `not_a_file` remain stable public reason codes and are mapped
to skipped states rather than failed `read_error` rows. Existing-media detection
hashes the bounded candidate set first and queries `Media.hash` only for those
candidate hashes, while same-plan duplicate detection stays in memory. It does
not write the DB, copy/import media, mutate source files, mutate app-managed
storage, run classification, run AI tagging, schedule localization, call
providers, or call LLMs.

The S2G-M1 planner output includes:

- a manual job id, trigger type, mode, and planned state;
- a stable `plan_hash`, expiry timestamp, and exact confirmation phrases;
- per-file public records with safe labels and state/reason only;
- state counts for skipped/planned/failed files;
- estimated import, classification, AI tagging, and localization workload;
- dry-run pipeline stages for candidate discovery, import, classification, AI
  tagging, localization, and summary, all with writes disabled;
- the AI tagging execution profile that S3A-M1 should reuse.

Production execution remains disabled in this phase. S3A-M1 must add the final
visible controls and implement or wire the production execute path behind
explicit operator approval, production identity gates, failure budgets, and a
small first acceptance batch.

## S3A-M1 guarded manual execute

S3A-M1 adds the explicit manual execute surface for dev/test validation and
future production acceptance. It remains manual-only:

- no automatic sync;
- no scheduled sync;
- no startup sync;
- no system service;
- no unattended writes.

Routes:

- `POST /api/admin/dynamic-library-sync/manual-sync/execute`
- `GET /api/admin/dynamic-library-sync/manual-sync/jobs/latest`
- `GET /api/admin/dynamic-library-sync/manual-sync/jobs/{run_id}`
- `POST /api/admin/dynamic-library-sync/manual-sync/jobs/{run_id}/cancel`

Execute requires a registered `root_id`; ad hoc `source_path` is allowed for
planning only. Before any write, the service re-runs the dry-run plan and
requires:

- `DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED=true`;
- `hydrated_only=true`;
- automatic/unattended sync flags disabled;
- the supplied `expected_plan_hash` matches the current plan;
- the plan timestamp is still fresh;
- the confirmation phrase exactly matches the plan's generated phrase.

The execution ledger uses `blombooru_dynamic_sync_runs` and
`blombooru_dynamic_sync_run_items`. Public summaries use safe labels and
aggregate counts only. Source files are never mutated. App-managed storage is
mutated only by the guarded import stage in dev/test or after a separately
approved production acceptance.

The Web Admin panel now supports the manual flow:

1. Select a registered source root.
2. Generate a dry-run plan.
3. Review public-safe counts and the plan hash.
4. Paste the exact confirmation phrase.
5. Start execute and poll the manual sync job.

The production launcher exposes an entry that opens the Web Admin manual sync
section. It does not bypass authentication and does not execute sync itself.

Production acceptance is still pending separate approval. No production
execute, production import, production classification, production AI tag writes,
production localization writes, source/iCloud mutation, LLM calls, provider
calls, or production app-storage mutation are authorized by S3A-M1 implementation
alone.

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

## S2G-M1 and S3 expectations

S2G-M1 is a foundation phase only. It may benchmark local ONNX providers and
plan manual sync dry-runs against safe fixtures/dev-test state, but it must not
execute production import, classification, AI tagging, localization, source
mutation, or app-managed storage mutation.

The S2G-M1 public report runner treats redaction as a publish gate: if the
public JSON/Markdown scan finds unsafe content, it exits non-zero and does not
write the tracked public report artifacts. Provider probe rows also have a
wall-clock timeout around model load and synthetic inference; a timed-out GPU
row is recorded as `timeout` and the runner continues to CPU fallback when
available.

S3A-M1 should run only after explicit approval and after S2G-M1 readiness is
reviewed. It should implement or wire the explicit manual-sync execute path,
add the final visible controls, and perform a small production acceptance batch
with production DB/storage identity, backup proof where applicable, dry-run
proof, public redaction checks, and relevant GOV3 contracts.

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
