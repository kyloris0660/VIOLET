# Phase 4.1 - Entity Metadata Foundation

Date: 2026-05-24

## Summary

Phase 4.1 adds the local entity metadata foundation needed before manual entity review, internal candidate generation, external provider pilots, proper-noun localization, or similarity/clustering work.

This phase is intentionally foundation-only:

- No external network calls.
- No reverse image search, crawler, or provider API integration.
- No DB import, classification, AI tagging, localization, staging copy, source/iCloud mutation, app-managed storage mutation, Entity Resolver execution, or similarity/clustering.
- No automatic confirmed entity assignments from tags or AI output.

## Implemented Model Foundation

New additive ORM/migration tables:

| Table | Purpose |
|-------|---------|
| `blombooru_entities` | Canonical entity records for character, work, artist, circle, source, franchise, and unknown entities. |
| `blombooru_entity_aliases` | Entity aliases separate from `TagTranslation`, with language, alias type, source, confidence, review, and primary-display flags. |
| `blombooru_entity_external_identities` | Future provider identity links with candidate/verified/rejected/stale status. |
| `blombooru_entity_evidence` | Privacy-redacted provenance records for internal signals, user confirmation, and future external evidence. |
| `blombooru_media_entity_candidates` | Suggested media/entity candidates that are distinct from confirmed assignments. |
| `blombooru_media_entity_assignments` | Reviewed media/entity assignments with role, confidence, review status, source, lock, candidate, and evidence references. |
| `blombooru_entity_translations` | Proper-noun display names separate from general/meta tag localization. |
| `blombooru_external_sources` | Inactive provider policy placeholders. Providers default to disabled. |
| `blombooru_provider_cache` | Redacted provider response cache placeholder for future opt-in adapters. |
| `blombooru_negative_lookup_cache` | Redacted negative-result cache placeholder for future provider adapters. |

## Migration Details

- Added `migrate_add_entity_metadata_tables` in `backend/app/database.py`.
- Migration is additive-only and creates new tables/indexes if missing.
- No existing tables are altered.
- No existing data is backfilled.
- No destructive operations are performed.
- Fresh metadata creation and legacy DB migration paths are both covered by tests.

Key uniqueness/index policies:

- Entity uniqueness: `(type, normalized_key)`.
- Alias uniqueness: `(entity_id, normalized_alias)`.
- External identity uniqueness: `(provider, external_id)`.
- Media assignment uniqueness: `(media_id, entity_id, role)`.
- Provider cache uniqueness: `(provider, query_hash, query_type)`.
- Negative lookup uniqueness: `(provider, query_hash, query_type)`.

## Service Skeleton

Added `backend/app/services/entity_metadata_service.py` with local-only helpers:

- `normalize_entity_key`
- `hash_provider_query`
- `create_entity`
- `add_alias`
- `add_external_identity`
- `record_evidence`
- `create_candidate`
- `accept_candidate`
- `reject_candidate`
- `create_or_update_assignment`
- `add_entity_translation`
- `list_media_entities`
- `list_entity_aliases`
- `is_external_lookup_allowed`

Service rules:

- Confirmed non-manual assignments require evidence.
- Manual confirmation is explicit and local.
- Candidates remain suggestions until explicitly accepted.
- `accept_candidate` marks a candidate accepted only after assignment creation succeeds.
- Rejected candidates do not create assignments.
- Lower-trust alias suggestions do not silently downgrade manual/trusted aliases.
- Evidence summaries and payload references must be privacy-redacted.
- Service functions perform no network calls.

## Closeout Correctness Fixes

PR closeout fixed two current-head foundation correctness issues:

- `accept_candidate` atomicity: candidate status is no longer changed before `create_or_update_assignment` succeeds. If assignment creation raises, callers that catch the exception and commit other work do not persist a false `accepted` candidate.
- ORM/DB delete consistency: `Entity` relationships now align SQLAlchemy behavior with FK `ondelete` rules. Entity-owned aliases, external identities, and entity translations retain `delete-orphan` plus `passive_deletes=True`; assignments use delete cascade plus `passive_deletes=True`; candidates and evidence remain retained with nullable `SET NULL` semantics.

## Privacy / External Provider Policy

Phase 4.1 stores policy placeholders only. It does not call providers.

Default future external eligibility:

- `unknown`: blocked by default.
- `non_anime`: blocked by default.
- `illustration`: blocked by default.
- `anime`: allowed only when an explicit enabled provider policy allows lookup for `anime`.

Additional guardrails:

- No originals or thumbnails are uploaded.
- Provider request/response cache fields are explicitly redaction-oriented.
- Local absolute paths, source/iCloud paths, and secrets are rejected from public evidence summary/reference fields.
- `ExternalSource.enabled` defaults to `false`.

## Handoff PR Link Traceability

`docs/current-handoff.md` was updated as a documentation hygiene fix:

- Recent Phase 3.8c / 3.8d PR references now use clickable GitHub PR links for known PRs `#54` through `#67`.
- The current Phase 4.1 entry links to known [PR #68](https://github.com/kyloris0660/AnimeLocalBooru/pull/68).
- No guessed PR links were added.
- A permanent workflow note was added: future `docs/current-handoff.md` phase entries should link known PR numbers to GitHub PR URLs because the handoff is the primary agent entry point.

This is public report/handoff documentation only and does not affect runtime behavior.

## Validation

Local validation performed during implementation:

- `& "$PY" scripts/check_python_env.py --expected-python "$PY"`: PASS, using `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`.
- `& "$PY" -m py_compile backend/app/enums.py backend/app/models.py backend/app/database.py backend/app/services/entity_metadata_service.py tests/test_entity_metadata_foundation.py tests/test_audit_tier1000.py`: PASS.
- `& "$PY" -m pytest tests/test_entity_metadata_foundation.py -v`: `13 passed`.
- `. "$env:USERPROFILE\.violet\test-env.ps1"; & "$PY" scripts/setup_test_db.py --migrate`: PASS on `blombooru_test`.
- `& "$PY" -m pytest tests/test_audit_tier1000.py::TestCLIOutputWriterErrors tests/test_audit_tier1000.py::TestOutputWriteFailExitCode -v`: `6 passed`.
- `. "$env:USERPROFILE\.violet\test-env.ps1"; & "$PY" -m pytest tests/ -v --ignore=tests/e2e`: `1216 passed, 4 skipped, 12 warnings`.

During full-suite validation, four existing `tests/test_audit_tier1000.py` write-failure tests initially failed because they assumed `Z:\nonexistent_drive\...` was always unwritable on Windows. On this machine that path was writable, so the tests were hardened to use an existing directory as the output target, which reliably triggers a file-write failure without relying on drive layout.

Closeout tests added:

- Assignment-creation failure during `accept_candidate` leaves candidate status unchanged.
- Deleting an `Entity` through the ORM removes cascade-owned alias/external identity/translation/assignment rows and preserves candidate/evidence rows with `entity_id=NULL`.

After user review, the project-level local test output policy was tightened: CodeX/local agent tests must not use `Z:\`, `\\192.168.71.230\Storage`, or any NAS/network-share path as a test output directory, pytest tmpdir, working directory, staging directory, log directory, or default script output directory unless explicitly authorized. Test artifacts must stay in repo-local gitignored directories or local machine temporary directories.

## Deferred Items

- Manual entity review UI.
- Internal candidate generation from tags, AI tags, filenames, or source metadata.
- External provider adapter pilot.
- Proper-noun localization automation and review workflow.
- Entity-backed search/display integration.
- Similarity graph / clustering.
- Production Ingestion Run Ledger / Source Item State Ledger and over-selection buffer.

## Engineering Judgment

The Phase 4.1 scope is appropriate as a foundation PR: it creates the schema and local service boundary needed by later phases without performing enrichment. The model set is intentionally broad enough to avoid baking external provider behavior into media/tag tables, but still inactive: provider/cache tables are policy placeholders, not a crawler or enrichment system.

Phase 3.9 should precede any broad external enrichment, larger library run, or provider-backed batch workflow. Phase 4.2 can proceed before Phase 3.9 if it remains manual/local review UI only. Phase 4.3 can proceed as internal-signal candidate generation if it remains no-network and suggestion-only. Phase 4.4 or any larger external-source pilot should wait for explicit provider policy, per-run budget/audit, and likely Phase 3.9-style ledger discipline.
