# SCV2-PX3 controlled canary gates

PX3 is the final SCV2 phase. The gates below are owner checkpoints inside PX3,
not new phases. Current repository evidence exercises only repository-owned
synthetic metadata and task-owned temporary SQLite databases.

## Current authority boundary

The following remain false: real Pixiv/gallery-dl network execution, provider
credentials, real source or iCloud access, existing database/app-storage
access or mutation, user-data import, production, and full-library import.
The owner authorized one conditional expected-head merge commit for PR #149;
that Git control-plane authority does not authorize any canary execution.
`scripts/plan_scv2_px3_controlled_canary.py` emits a canonical plan and returns
exit code 3 if `--execute` is supplied; it never converts a plan into authority.

## Gate sequence

**STOP before the first normal startup against an existing database.**
`backend/app/main.py` calls `init_db()` during normal startup;
`backend/app/database.py::init_db()` runs `Base.metadata.create_all()` followed
by `check_and_migrate_schema()`. Merging the code therefore does not permit
starting it against the existing DB before a successful backup/restore test.
The additive PX3 migration creates only the product evidence/media association
table, its foreign keys and indexes. It does not backfill or rewrite existing
rows. Validate creation twice on a task-owned DB; recovery of a real database
uses the separately approved backup/restore procedure. No task-owned PostgreSQL
was configured for this correction, so no PostgreSQL connection or migration
was attempted and no infrastructure was installed. PostgreSQL migration smoke
remains part of the backup/restore stop, not a code-merge blocker.

1. `PX3_BACKUP_RESTORE_GATE`: bind an exact database identity, create and hash a
   nonzero `pg_dump` artifact, restore it only into an isolated non-production
   database, and compare schema plus bounded SourceConcept counts. Stop before
   canary apply if restore or comparison differs.
2. `PX3_CONTROLLED_PROVIDER_SMOKE_GATE`: after separate network and credential
   authority, run the existing `scripts/run_pixiv_metadata_ingestion.py` route
   against an owner-approved manifest of one to five works. Media and thumbnail
   download remain forbidden. Stop on authentication/transport systemic error,
   identity mismatch, raw-secret exposure, or request-accounting drift.
3. `PX3_EXISTING_DATABASE_CANARY_GATE`: call the PX3 source-metadata endpoint in
   dry-run mode with `canary_percent` from 1 through 5. Selection is stable by
   Pixiv work identity, is bound into the scope key and fingerprint, and does
   not inspect raw payloads. The endpoint rejects an omitted percentage, so
   enabling the canary flags cannot silently expand into a full-library run.
   Stop unless dry-run counts and product fingerprint are owner accepted and
   the backup/restore gate passed.
4. `PX3_1_TO_5_PERCENT_IMPORT_CANARY_GATE`: enable both PX3 feature flags only
   for the accepted database and apply the exact dry-run sample with the phrase
   `APPLY_PIXIV_SOURCE_CONCEPTS`. Replay must add zero rows. Stop and use the
   exact run-key rollback endpoint on any identity, accounting, provenance, or
   non-SourceConcept write discrepancy.

The first apply canary is **1%**. Apply must send
`accepted_selection_fingerprint`, `accepted_product_fingerprint`, and
`accepted_binding_fingerprint` from the dry-run. The server recomputes all
three before writing and returns 409 for drift. The last fingerprint covers
current local row bindings only and never changes PX1/PX2/product identity.
Gallery search and media detail must accept the resulting evidence support,
then replay must add zero bindings and rollback must revoke them immediately.
Rollback is allowed only for the active run with proven creation ownership and
unchanged core/reference rows; product audit rows remain available.

`SCV2_PX3_MULTIWORKER_APPLY_GATE` remains deferred until before multiple Uvicorn
workers, multiple owners, or concurrent canary applies. `run.py` omits a worker
override (the Uvicorn default is one); UI duplicate clicks are blocked and
database uniqueness conflicts return a stable 409. No distributed lock, queue,
or new concurrency framework is introduced. Workspace confinement debt retains
its existing exact use-before gate.

Example plan-only commands:

```powershell
& $PY -B scripts/plan_scv2_px3_controlled_canary.py --gate backup-restore
& $PY -B scripts/plan_scv2_px3_controlled_canary.py --gate provider-smoke --work-limit 1
& $PY -B scripts/plan_scv2_px3_controlled_canary.py --gate existing-db-canary --canary-percent 1
& $PY -B scripts/plan_scv2_px3_controlled_canary.py --gate import-canary --canary-percent 1
```

The API apply and rollback confirmation phrases, exact commands, required
future authorities, success checks, and stop conditions are present in each
machine-readable plan. Full-library import remains a later owner decision at
the end of PX3, not an automatic continuation of a successful canary.
