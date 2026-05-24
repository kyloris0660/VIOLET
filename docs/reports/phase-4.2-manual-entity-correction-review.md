# Phase 4.2 - Manual Entity Correction and Review Foundation

Date: 2026-05-24

PR: pending creation

## Summary

Phase 4.2 adds the smallest useful admin-only foundation for manual entity correction and targeted candidate review on top of the Phase 4.1 entity metadata schema.

The product principle is explicit: V.I.O.L.E.T. is a large-scale local library system and must not rely on exhaustive human review of AI/entity suggestions. Manual interaction is for sparse, targeted, high-impact correction and confirmation. Future automation remains the default path and will consume these durable manual signals.

## Scope

Implemented:

- Admin-only entity lookup/list/search by canonical name or alias.
- Manual entity creation.
- Manual alias creation.
- Entity detail view with aliases, translations, external identities, and confirmed assignment count.
- Manual media/entity assignment with role, manual source, lock handling, and evidence provenance.
- Assignment correction/rejection API for targeted wrong-assignment cleanup.
- Targeted candidate list by status/media/type.
- Candidate accept/reject endpoints. Accept uses the Phase 4.1 `accept_candidate` service path and keeps atomicity.
- Minimal Admin UI section for targeted correction workflows.
- Governance/docs wording correction away from exhaustive manual review.

Not implemented:

- Entity delete.
- Merge/split.
- Bulk operations.
- Automatic candidate generation.
- Automatic confirmed entity assignment.
- External provider calls or reverse image search.
- Entity-backed gallery search/display.

## API Routes Added

All routes are admin-only and live under `/api/admin`:

- `GET /api/admin/entities`
- `POST /api/admin/entities`
- `GET /api/admin/entities/{entity_id}`
- `POST /api/admin/entities/{entity_id}/aliases`
- `POST /api/admin/entities/{entity_id}/translations`
- `GET /api/admin/media/{media_id}/entity-assignments`
- `POST /api/admin/media/{media_id}/entity-assignments`
- `PATCH /api/admin/media/{media_id}/entity-assignments/{assignment_id}`
- `POST /api/admin/media/{media_id}/entity-assignments/{assignment_id}/reject`
- `GET /api/admin/entity-candidates`
- `POST /api/admin/entity-candidates/{candidate_id}/accept`
- `POST /api/admin/entity-candidates/{candidate_id}/reject`

## UI Added

`frontend/templates/admin.html` now includes an "Entity Metadata" section in the Content tab.

The section supports:

- Entity search/list with type filter.
- Create entity.
- Add alias.
- Manual media assignment by media ID and entity ID.
- Load current confirmed assignments for a media ID.
- Targeted candidate list with accept/reject actions.

The UI deliberately does not present a broad infinite queue or imply that the operator should review every candidate.

## Data Integrity and Safety

- Entity metadata stays separate from `TagTranslation`.
- Entity assignments stay separate from `media_tags`.
- Manual assignment writes `source=manual`, confirmed review status, optional lock, and `EntityEvidence` provenance.
- Existing locked assignments are not silently overwritten; correction requires explicit `allow_locked_update=true` at the API layer.
- Candidate acceptance uses `accept_candidate`; candidate status changes only after assignment creation succeeds.
- Candidate rejection does not create assignments.
- API responses include targeted-review metadata: `review_model=targeted_correction` and `exhaustive_review_required=false`.
- No provider cache raw payloads are exposed.
- No provider is enabled.
- No external calls are made by the API path.

## Governance and Docs Alignment

Updated wording in:

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `docs/test-workflow.md`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/reports/phase-4.1-entity-metadata-foundation.md`
- `docs/reports/phase-4.1-entity-metadata-foundation-summary.json`

The durable principle is now recorded: manual entity work is correction-oriented, sparse, targeted, and high-impact. Automation remains the long-term default. Manual corrections become durable evidence, aliases, overrides, assignments, translations, or negative signals for future automation.

## Validation

Python identity:

- `& "$PY" scripts/check_python_env.py --expected-python "$PY"`: PASS
- `sys.executable`: `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`

Static and focused checks:

- `& "$PY" -c "import json; json.load(open('frontend/static/locales/en.json', encoding='utf-8')); json.load(open('frontend/static/locales/zh-cn.json', encoding='utf-8')); print('locale json ok')"`: PASS
- `& "$PY" -m py_compile backend/app/routes/admin/entities.py tests/test_entity_metadata_admin_api.py`: PASS
- `& "$PY" -m pytest tests/test_entity_metadata_foundation.py tests/test_entity_metadata_admin_api.py -v`: `49 passed`

Full non-E2E suite:

- `. "$env:USERPROFILE\.violet\test-env.ps1"; & "$PY" -m pytest tests/ -v --ignore=tests/e2e`: `1252 passed, 4 skipped, 12 warnings`

Browser validation:

- Test env loaded from `. "$env:USERPROFILE\.violet\test-env.ps1"`.
- `VIOLET_ENV=test`
- `POSTGRES_DB=blombooru_test`
- `VIOLET_STORAGE_ROOT=C:\Users\kyloris\VioletStorage\test`
- `APP_PORT=8012`
- `VIOLET_BASE_URL=http://127.0.0.1:8012`
- Server command: `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe run.py --debug`
- Reloader PID: `26904`
- Server child PID: `27072`
- Identity check: PASS via `scripts/check_test_server_identity.py --expected-python "$PY" --expected-storage-root "C:\Users\kyloris\VioletStorage\test"`
- Browser: Playwright Chromium with system Edge channel `msedge`
- Flow validated: admin login, Entity Metadata section load, create entity, search entity, add alias, alias search, manual media assignment, candidate list load, accept candidate, reject candidate, persisted accepted/rejected/assignment API state.
- Result: PASS, no login-postflow console errors, no HTTP 500 responses.
- Screenshot artifact: `.codex/phase42/entity-metadata-1779634623502.png` (local untracked artifact, not committed).
- Server stop: stopped reloader PID `26904`, then identified and stopped spawned child PID `27072`; port `8012` free after cleanup.

## DB Handling

- No new migration was added.
- Phase 4.2 relies on Phase 4.1 entity metadata tables.
- Test DB migration/setup was invoked in the test environment only.
- Browser validation seeded minimal test DB rows only: one synthetic media row, entities, and two candidates in `blombooru_test`.
- Development DB and imported I7 data were not used for write validation.

## Explicit Non-Goals Confirmed

- No external network calls.
- No reverse image search.
- No crawler.
- No provider API calls.
- No automatic candidate generation.
- No automatic confirmed assignments from AI tags/files/names.
- No broad candidate queue for exhaustive review.
- No bulk auto-confirm.
- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No staging copy.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No Entity Resolver execution.
- No similarity/clustering.
- No production ingestion ledger implementation.

## Deferred Items

- Phase 4.3 internal candidate generation from existing local signals.
- External provider adapter pilot.
- Proper-noun localization strategy.
- Entity-backed gallery search/display.
- Similarity graph / clustering.
- Production Ingestion Run Ledger / Source Item State Ledger.
- Merge/split and bulk entity operations.

## Artifact Lifecycle

- New API route module: production reusable code.
- New focused tests: reusable regression tests for Phase 4.2 API behavior.
- Admin UI section: minimal product UI foundation, intentionally not a broad review workspace.
- Browser validation script: one-off local validation run, not committed.
- Browser screenshot/server logs: one-off local artifacts under `.codex/phase42`, not committed.
- This report and summary JSON: public report / handoff documentation.

## Engineering Judgment

Phase 4.2 scope is appropriate if kept small: the system needed a local correction surface before any internal candidate generation or provider adapter can safely consume human feedback. The API is slightly richer than the UI because it includes assignment patch/reject and translation endpoints, but those are bounded admin-only primitives over existing Phase 4.1 service semantics.

The correction-oriented model is now reflected in code, tests, UI copy, API response flags, and governance docs. It is intentionally not an exhaustive queue. The remaining product risk is discoverability and ergonomics: the current UI is enough for targeted admin correction, not for large-scale entity operations.

Phase 4.3 can come next if it remains internal-signal, suggestion-only, and no-network. Phase 3.9 should precede provider-backed or large-scale ingestion/enrichment work because provider pilots and large library expansion need stronger per-run ledger discipline.
