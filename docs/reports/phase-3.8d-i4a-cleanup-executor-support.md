# Phase 3.8d-I4a Cleanup Executor Support

## Summary

- Phase: `3.8d-I4a`
- Purpose: add reviewed, controlled actual cleanup executor support for the dedicated Phase 3.8d partial staging target.
- Real cleanup performed in this PR: `False`
- Phase 3.8d execute status: `blocked`

## Executor Contract

- Default mode remains dry-run and performs no deletion.
- Execute mode requires `--execute-cleanup`.
- Execute mode requires exact confirmation phrase `DELETE_PHASE38D_PARTIAL_STAGING`.
- A fresh cleanup dry-run proof must pass immediately before execution.
- Cleanup proof is based on explicit target/root inputs, valid protected roots, protected-root disjointness, manifest-derived expected staging files, and actual filesystem scan.
- Staging logs are diagnostic only and cannot authorize deletion.
- No ad-hoc deletion outside the reviewed executor is allowed.

## Deletion Scope

The executor may delete only expected manifest/filesystem-matched regular files under the verified target root.

It must fail closed before deleting when any of these are present:

- invalid or missing protected root
- protected-root overlap
- target outside expected staging root
- unexpected file
- missing expected file
- file size mismatch
- path traversal target
- symlink or Windows reparse-point escape hazard
- wrong or missing confirmation phrase

Parent directories are left in place. Source/iCloud files, repo files, app-managed storage, DB data, staging copy, read-probe/hydration, classification, AI tagging, localization, Entity Resolver, and similarity workflows are out of scope.

## Test Coverage

- default no-delete behavior
- wrong confirmation phrase blocks deletion
- invalid identity proof blocks deletion
- valid proof plus confirmation deletes only temp-dir expected files
- deleted count and bytes are reported
- target directory remains after temp-dir cleanup
- symlink/reparse hazard blocks deletion
- path traversal manifest target blocks deletion
- public report privacy remains safe

## Safety Confirmation

- real partial staging cleanup: `not_run`
- source/iCloud mutation: `False`
- app-managed storage mutation: `False`
- DB import: `False`
- classification: `False`
- AI tagging: `False`
- localization: `False`
- Entity Resolver: `False`
- similarity: `False`
- push main: `False`
- merge: `False`

## Next Step

After this PR is reviewed and merged, Phase 3.8d-I4b may request approval to run the reviewed executor against the already validated dedicated partial staging target.
