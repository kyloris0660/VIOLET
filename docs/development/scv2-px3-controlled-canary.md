# SCV2-PX3 controlled canary gates

PX3 is the final SCV2 phase. The gates below are owner checkpoints inside PX3,
not new phases. This follow-up also exercises a private, independently restored
PostgreSQL copy with existing metadata and a fixed 1% work sample.

## Current authority boundary

Real Pixiv/gallery-dl network, provider credentials, original source/iCloud/media
access, original database/app-storage writes, import and production remain
forbidden. Original database read-only identity queries and a local private backup
were explicitly authorized and performed: real database activity is nonzero.
Writes and localhost metadata-only browser execution are authorized only in the
new task PostgreSQL restore, with frozen metadata and isolated storage/cache.
PR #149 is merged; its permission is consumed. The owner authorized one normal
follow-up PR and one conditional expected-head merge commit after verification.
That permission expires at its first successful merge and cannot be reused.
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
uses the separately approved backup/restore procedure. This task independently
restored the original backup into a new PostgreSQL database. Only the copy ran
`migrate_add_source_concept_product_integration`,
`migrate_add_source_concept_product_media_bindings` and the existing optional
`migrate_add_source_concept_fallback_search_index` needed for rollback. Repeated
migration is safe; no infrastructure was installed. The original lacks PX3
tables, so this task's dry-run ran only on the copy. Backup/restore success does
not authorize normal startup, schema migration or apply against the original.

1. `PX3_BACKUP_RESTORE_GATE`: bind an exact database identity, create and hash a
   nonzero `pg_dump` artifact, restore it only into an isolated non-production
   database, and compare schema plus bounded SourceConcept counts. Stop before
   canary apply if restore or comparison differs.
2. `PX3_CONTROLLED_PROVIDER_SMOKE_GATE`: after separate network and credential
   authority, first enforce the one-to-five-work manifest in the ingestion CLI.
   The historical template did not enforce that bound and is now explicitly
   non-executable. Existing metadata was sufficient here; provider execution
   remained zero. Media and thumbnail
   download remain forbidden. Stop on authentication/transport systemic error,
   identity mismatch, raw-secret exposure, or request-accounting drift.
3. `PX3_EXISTING_DATABASE_CANARY_GATE`: call the PX3 source-metadata endpoint in
   dry-run mode with `canary_percent` from 1 through 5. Selection is stable by
   Pixiv work identity, is bound into the scope key and fingerprint, and
   uses the existing complete/trust and local binding provenance checks. Pending,
   non-Pixiv and unverifiable receipt records are excluded. Legacy gallery-dl
   payloads are validated through the existing pure normalizer without rewriting
   PX1/PX2 fingerprints or opening external artifact pointers. The endpoint rejects an omitted percentage, so
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
Another active existing-metadata selection blocks apply until its rollback.
The plan generator requires `--dry-run-result <actual-private-plan.json>` before
emitting apply JSON with real fingerprints; it cannot execute placeholder values.

## Restored-copy result and late PR #149 findings

The restore matched 37,419 Media, 2,554 SourceMetadataRecord, 6,094 SourceConcept,
12,635 signal and 12 resolution-run rows, with validated foreign keys. The fixed
sample is **5 of 465 eligible works (1%, rounded up)**: 5 pages, 5 Media,
51 bindings, 43 clusters and 56 ambiguity records. This is a work proportion,
not a media-file proportion, and SourceConcept materialization is not media import.

Actual apply, fresh-session accounting, SourceConcept-specific ordinary search,
media detail support/provenance, zero-write replay, different-selection 409,
rollback, repeated rollback and reapply passed. Rollback restored all 54 original
table content digests exactly, preserving prior shared data and audit rows.
The selected core had zero preexisting overlap. System Edge used real admin,
gallery and detail templates; original images, thumbnails, files and external
requests were blocked. Admin initialization made zero full-detail requests.
This does not verify actual media display. Private artifacts stay local.

| Finding | Disposition |
| --- | --- |
| 3939389557 | Fixed: actual dry-run checksum and all three fingerprints are carried into apply; edited or missing plans fail closed. |
| 3939389569 | Fixed: assign fresh summary JSON after persistence; response, new-session read and actual binding rows agree. |
| 3939389572 | Fixed: a different active existing-metadata run key returns `px3_other_active_selection_requires_rollback`, regardless of scope. |
| 3939389553 | Deferred to `SCV2_PX3_METADATA_REFRESH_BINDING_GATE`, before metadata refresh or ongoing original-database use with active bindings. Full business/provenance/trust invalidation and cache hooks are not fixed. Copy metadata is frozen and runtime DB grants deny refresh and truth writes. |
| 3939389560 | Deferred to `SCV2_PX3_POLICY_VERSION_CAPTURE_GATE`, before the next PX2 context/candidate policy version change; historical reconstruction still uses current constants. |

The real copy also exposed queue-placeholder eligibility, rejected popularity/meta
signal compatibility, and PostgreSQL locale collation differences. The bounded
fixes exclude unverified inputs, keep rejected signals rejected, and restore
canonical Python ordering before fingerprint validation. No PX1/PX2 identity is
changed, no source metadata is rewritten, and no second resolver is introduced.

The unsolicited PR #150 feedback at its initial head was also boundedly accepted:
3939783011 removes unrelated queue diagnostics from selection identity;
3939783010 validates the rollback response and runtime isolation/grants directly;
3939783013 checks unique before/after Media sets against actual persisted binding
Media; 3939783014 requires exact identity and AND search result sets. Mutation
regressions cover each case. The prior completed private rehearsal is preserved;
the same five works are revalidated with fresh fingerprints after the identity fix.

The registered `scv2_px3_pixiv_product_integration_contract_v1` retains synthetic
reconstruction and a same-HEAD focused receipt. Its restored-copy checkpoint also
runs `scripts/check_scv2_px3_restored_canary.py --evidence-dir <private-task-dir>
--expected-receipt-sha256 <accepted-local-digest>`. This reads a fixed evidence set,
verifies backup content and independent restore, cross-checks accepted plans,
fresh rows, search, rollback and browser results, and grants no original authority.

**Next original-database stop:** refresh exact identity and backup/restore proof;
freeze metadata and other SourceConcept writers; separately authorize the listed
additive migrations; recompute a fresh 1% dry-run on the original; have the owner
accept its selection/product/binding fingerprints; only then apply, verify
gallery/detail, replay and exact-run rollback. Copy fingerprints cannot authorize
original apply. Recheck ownership and zero preexisting core overlap; stop if the
scope has changed without deleting history. Real media display needs a separate
read grant for only the selected files/thumbnails, with no scan/import/download.

`SCV2_PX3_MULTIWORKER_APPLY_GATE` remains deferred until before multiple Uvicorn
workers, multiple owners, or concurrent canary applies. `run.py` omits a worker
override (the Uvicorn default is one); UI duplicate clicks are blocked and
database uniqueness conflicts return a stable 409. No distributed lock, queue,
or new concurrency framework is introduced. Workspace confinement debt retains
its existing exact use-before gate; the owner explicitly authorized this trusted
fixed-path single-process copy rehearsal without a general hostile-workspace
framework. Workspace, multiworker and remote-CI debt are not falsely closed.

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

## Final local validation and owner stop

Implementation HEAD `306cc811fb0b49a5450ffb419edf115562045515`, tree `98e2df6decea7c5150b83e4fe3c65a8fd4dd5f27`:
852 canonical focused tests passed in 162.52 seconds, with clean before/after
identity and a local operator receipt. Independent synthetic contract: zero errors,
zero warnings. The strengthened restored-copy contract also passed; both receipts
and all private evidence remain local. Docs-only carry-forward is restricted to
the six registered projection documents.

The full non-E2E suite ran **once**: 4,384 passed, 22 skipped, 11 raw failures,
52 warnings (601.13 seconds). Seven failures came from a private test wrapper's
nonstandard Python encoding/environment; three were stale governance assertions.
Their corrected standard-environment targeted run passed 282 tests with one skip.
The final bounded selection/evidence corrections passed another 70 targeted tests,
then the 852-test canonical receipt above. The remaining
`missing_original_ai_execution_evidence` reproduced on exact base
`6db72c73397c17128bd2ce9be54f25233bc853f0`; no private evidence was fabricated.
This does not relabel the original full run as green. Black was unavailable and
was not installed. Local evidence is not hosted CI; no new review was requested.

The final copy retains one active run and 51 bindings. The first complete rehearsal
was privately preserved, then its active run was business-rolled back before the
same five works received the revised exact plan. Both runs' product audit rows
remain. The second complete API/Edge lifecycle and all 54 rollback baseline digests
passed. A final read-only privilege audit corrected a schema-unqualified catalog
lookup in the private helper; no application or database state was changed by that
failed query. The final copy service is stopped.

Final status:
`SCV2_PX3_RESTORED_DATABASE_CANARY_VERIFIED_PENDING_ORIGINAL_DATABASE_APPLY_APPROVAL`.
The original readonly backup is a real database operation (one backup); original
database writes, original storage/media access, provider network/credentials and
media imports remain zero. The original lacks the PX3 tables: its first canary
requires separate additive migration authority after refreshed backup/restore
proof. Normal application startup runs create_all and the wider migration chain;
this rehearsal does not authorize normal startup against the original database.
The precise original target, copied work IDs, expected core writes and rollback
plan are in the local private owner package. Recompute all three fingerprints on
the actual target and obtain owner acceptance; never reuse copy acceptance.
