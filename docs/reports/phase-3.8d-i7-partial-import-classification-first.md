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
- App-managed writes in final resume run: `0`
- Prior successful import writes preserved: `994`

## Classification-first Pipeline
- Classification status: `completed`
- Classification processed: `994`
- Classification failed: `0`
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

## Validation
- DB/storage validation: `passed`
- API/browser/admin smoke: `passed`
- Server log scan: `passed`

## Tests
- Diff check: `passed (warnings only: CRLF normalization)`
- Py compile: `passed: scripts/run_phase38d_i7_partial_import_classification_first.py and tests/test_phase38d_i7_partial_import_classification_first.py`
- Focused tests: `passed: 6 passed`
- Full non-E2E suite: `passed: 1166 passed, 12 skipped, 12 warnings`

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
