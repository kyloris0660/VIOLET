# Phase 4.4-C1 - Validated Evidence Persistence

Date: 2026-05-28T09:12:35.142958+00:00

## Summary

Phase 4.4-C1 persisted only the two manually validated high-confidence SauceNAO source matches through the provider-neutral evidence contract.

- Approved media IDs: `2687, 2670`
- Low-confidence excluded IDs: `2690, 2654, 2647`
- Mode/status: `applied`
- Source of truth: `local_ignored_B1_B1V_details_artifacts_plus_C1_approved_manual_validation_scope`

## Lifecycle Classification

- Persistence service code: durable provider/evidence infrastructure.
- C1 runner: phase-scoped operational runner.
- Reports: public report / handoff / roadmap update.
- DB backup and local details: one-off local ignored artifacts.

## DB Identity

- VIOLET_ENV: `development`
- Configured/current DB: `blombooru` / `blombooru`
- DB endpoint: `localhost:5432`
- DB user: `postgres`
- Storage root basename: `AnimeLocalBooru`

## Backup

- Backup created: yes
- Backup basename: `phase-4.4c1-db-backup-20260528T091234Z.dump`
- Backup bytes: `2653422`
- Backup format: `pg_dump -Fc`
- TOC verified: `True`

## Write Results

| Table | Planned | Inserted | Existing | Skipped |
| --- | ---: | ---: | ---: | ---: |
| ProviderCache | 2 | 2 | 0 | 0 |
| EntityEvidence | 2 | 2 | 0 | 0 |
| MediaEntityCandidate | 7 | 7 | 0 | 0 |

- ProviderCache written: `True`
- EntityEvidence written: `True`
- MediaEntityCandidate written: `True`
- MediaEntityCandidate deferred: `False`

## Idempotency Verification

A post-apply dry-run against the same approved scope reported existing rows only:

| Table | Planned | Inserted | Existing | Skipped |
| --- | ---: | ---: | ---: | ---: |
| ProviderCache | 2 | 0 | 2 | 0 |
| EntityEvidence | 2 | 0 | 2 | 0 |
| MediaEntityCandidate | 7 | 0 | 7 | 0 |

## Per-media Outcome

| media_id | result_id | score | source_host | artist | work | character | localization |
| ---: | --- | ---: | --- | --- | --- | --- | --- |
| 2687 | 7695035 | 96.2 | danbooru.donmai.us | yunkaiming | honkai: star rail, honkai (series) | acheron (honkai: star rail) | pending |
| 2670 | 9366672 | 91.96 | danbooru.donmai.us | songchuan li | blue archive | kisaki (blue archive) | pending |

## Post-write Verification

- ProviderCache approved count: `2`
- EntityEvidence approved count: `2`
- MediaEntityCandidate C1 count: `7`
- Confirmed assignment count for approved media: `0`
- Entity count unchanged: `True`
- TagTranslation count unchanged: `True`
- media_tags for approved unchanged: `True`
- Low-confidence positive evidence unchanged: `True`
- Low-confidence candidates unchanged: `True`

## Validation

- Python identity: `PASS` with project venv Python 3.12.0.
- `py_compile`: changed Python files passed.
- `tests/test_phase44c1_validated_evidence_persistence.py -v`: `12 passed`.
- `tests/test_phase44c0_provider_evidence_contract.py -v`: `40 passed`.
- `tests/test_entity_metadata_foundation.py -v`: `38 passed`.
- Summary JSON validation: `python -m json.tool` passed.
- `git diff --check`: passed; PowerShell reported LF-to-CRLF working-copy warnings for existing markdown files only.
- Public report privacy scan: passed; no forbidden private material patterns were found in this report or summary JSON.
- Real browser validation: not applicable; C1 changed backend service/script/tests/docs only, with no UI or API route changes and no server startup required.

## Low-confidence Exclusion

`2690`, `2654`, and `2647` were excluded from positive persistence. No positive EntityEvidence or MediaEntityCandidate rows are written for them in C1.

## Rollback

Prefer restoring from the local ignored backup when a full rollback is needed. The full backup path is recorded only in local details.

Targeted C1 delete SQL:

```sql
BEGIN;
DELETE FROM blombooru_media_entity_candidates
WHERE evidence_id IN (
  SELECT id FROM blombooru_entity_evidence
  WHERE provider = 'saucenao' AND query_hash IN ('07406f04b2b244e5c91846919ca5bd76b62eee27e8be8d31ddbf590f60f5f1aa', 'ae6d19f6e8125433a441b8cdbadac1d08197836938fd6fadc7a0d711a4dca9a7')
    AND payload_ref LIKE 'provider_cache:saucenao:reverse_search_derived_image:%'
    AND summary LIKE 'Phase 4.4-C1 validated provider evidence%'
);
DELETE FROM blombooru_entity_evidence
WHERE provider = 'saucenao' AND query_hash IN ('07406f04b2b244e5c91846919ca5bd76b62eee27e8be8d31ddbf590f60f5f1aa', 'ae6d19f6e8125433a441b8cdbadac1d08197836938fd6fadc7a0d711a4dca9a7')
  AND payload_ref LIKE 'provider_cache:saucenao:reverse_search_derived_image:%'
  AND summary LIKE 'Phase 4.4-C1 validated provider evidence%';
DELETE FROM blombooru_provider_cache
WHERE provider = 'saucenao' AND query_type = 'reverse_search_derived_image'
  AND query_hash IN ('07406f04b2b244e5c91846919ca5bd76b62eee27e8be8d31ddbf590f60f5f1aa', 'ae6d19f6e8125433a441b8cdbadac1d08197836938fd6fadc7a0d711a4dca9a7')
  AND response_json_redacted->>'phase' = '4.4-C1';
COMMIT;
```

## Safety Confirmation

- provider_call: `False`
- upload: `False`
- db_migration: `False`
- source_icloud_mutation: `False`
- app_managed_storage_mutation: `False`
- localization_execution: `False`
- entity_resolver: `False`
- similarity_clustering: `False`
- confirmed_assignment_created: `False`
- automatic_entity_created: `False`
- media_tags_mutated: `False`
- tag_translation_mutated: `False`
- low_confidence_positive_writes: `False`

## Next Step

Recommended next step: review and merge this C1 persistence PR if reviewer finds no current-stage DB correctness, provenance, privacy, confirmed-assignment, or report-truthfulness issue. After merge, choose D0 second-provider scouting or a bounded B2 sample pilot.
