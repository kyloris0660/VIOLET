# Phase 3.8d-I7 Partial Import Classification-first Pipeline

## Summary
- Status: `completed`
- Success: `True`
- Source label: `violet:phase3.8d:i7:staged-success`
- Scope: import only I6 staged-success rows, then classify first before AI tagging/localization.

## I6 Item Ledger Gate
- Ledger validation: `passed`
- Total rows: `1000`
- Import candidates: `994`
- Excluded failed rows: `[799, 839, 922, 970, 971, 972]`
- Deferred original rows excluded: `[98, 881]`
- Replacement rows present: `[1029, 1041]`

## DB Backup And Import
- Backup: `phase-3.8d-i7-db-backup-20260522T151408Z.dump` / `1468090` bytes
- Dry-run status: `passed`
- Dry-run checked: `994`
- Would create: `0`
- Duplicate by hash: `994`
- Import status: `resumed_after_prior_successful_import`
- Imported media count: `994`
- Import failures: `0`
- Resume note: `previous I7 attempt completed DB import before downstream blocker`
- Closeout resume identity: future resume paths rebuild media IDs from current DB source-label rows and file-hash matching, not local validation details.
- App-managed writes in final resume run: `0`
- Prior successful import writes preserved: `994`

## Classification-first Pipeline
- Classification status: `completed`
- Classification processed: `994`
- Classification failed: `0`
- Closeout classification resume identity: classification resume now requires media ID identity proof or DB-backed content_class state for the same source-label media set.
- Distribution: `{'anime': 934, 'unknown': 33, 'non_anime': 27, 'illustration': 0, 'failed_or_unclassified': 0}`
- AI eligible count: `967`
- AI tagging status: `completed`
- AI processed: `967`
- AI failed: `0`
- Suggestions added: `12058`
- Confirmed tags added: `40287`
- Localization status: `completed`
- Localization candidates: `200`
- Translated: `200`
- Localization failed: `0`
- Skipped proper nouns: `101`
- Localization continuation status: `completed`
- Additional localization candidates: `370`
- Additional translated: `370`
- Additional failed: `0`
- Final remaining general/meta: `0`
- Proper noun categories skipped: `['character', 'copyright', 'artist']`

## PR #64 Closeout Hardening
- Public report privacy gate now fails closed: if summary or rendered Markdown would leak a local absolute path, file URI, or secret-like unsafe field, only a minimal safe blocked report is written.
- Import resume media IDs are rebuilt from current DB rows for `Media.source='violet:phase3.8d:i7:staged-success'` and matched by staged file hash.
- Classification resume no longer trusts processed count alone; it requires an identity proof for the same imported media ID set or DB-backed content_class state for that set.
- Post-import DB/storage validation is now an authoritative success gate: missing managed originals/thumbnails, non-app-relative paths, storage-root escapes, source-label mismatches, DB row mismatches, privacy leaks, or storage probe failures set `blocked_db_storage_validation_failed`.
- Localization continuation success is derived from the current continuation result and final remaining general/meta count, not stale previous summary success.
- Localization continuation was intentionally limited to the current I7 eligible-derived `general`/`meta` candidates. Character/copyright/artist proper-noun categories remained skipped.

## Validation
- DB/storage validation: `passed`
- DB/storage media checked: `994`
- Managed originals present: `994` / missing `0`
- Managed thumbnails present: `994` / missing `0`
- App-relative path containment failures: `0`
- DB public path privacy leaks: `0`
- API/browser/admin smoke: `passed`
- Server log scan: `passed`

## Root-cause Audit
- Root cause: I7 could previously record post-import validation facts without making overall success depend on those facts, and resume/continuation paths could inherit stale success state.
- Related patterns inspected: DB/storage validation status construction, overall `summary.status` / `summary.success` assignment after localization, import resume identity, classification resume identity, and public report privacy writing.
- Fixes applied: DB/storage validation now gates final pipeline success; app-relative path containment is enforced before file probes; missing managed files fail validation; localization continuation sets success from the current continuation outcome.
- Deferred: no broad DB ledger redesign, no Entity Resolver/similarity, and no full E2E rerun in this closeout because the change is runner validation/reporting plus focused read-only DB/storage verification.

## Tests
- Diff check: `passed (warnings only: CRLF normalization)`
- Py compile: `passed: scripts/run_phase38d_i7_partial_import_classification_first.py and tests/test_phase38d_i7_partial_import_classification_first.py`
- Focused tests: `passed: 23 passed`
- Full non-E2E suite: `passed: 1183 passed, 12 skipped, 12 warnings`

## Safety Confirmation
- Failed I6 rows were not imported.
- Raw 1000 manifest was not used for import.
- Source/iCloud write mutation: NO.
- Staging copy rerun: NO.
- Entity Resolver / similarity / clustering: NO.
- App-managed storage writes occurred only through approved DB import.
- Full 1000 DB import remains blocked; downstream planning must use the I7 item ledger.

## Engineering Judgment / Operator Notes
- The phase boundary is appropriate as a recovery path: importing the 994 staged-success rows is safer than retrying cloud failures before every downstream validation.
- Remaining risk is operational: duplicate-by-hash or downstream job failures can reduce newly imported/downstream-eligible counts and must stay item-scoped.
- Failed I6 rows remain deferred; future work should decide retry/backfill/partial-import policy separately.
- This phase intentionally does not start Entity Resolver, similarity, clustering, or Phase 4.

## Privacy
- Public report contains safe labels and aggregate counts only.
- Local full-path manifests/details remain ignored and uncommitted.
