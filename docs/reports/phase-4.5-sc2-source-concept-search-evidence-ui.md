# Phase 4.5-SC2 SourceConcept Search Expansion and Evidence UI

## Summary

Phase 4.5-SC2 exposes existing source-layer `SourceConcept` data in two user-facing places:

- normal gallery search can transparently expand matching active/safe SourceConcept aliases through `SourceConceptSearchIndex`;
- media detail pages show unconfirmed SourceConcept grouping, safe evidence summaries, and a disabled/manual-promotion preview.

SC2 does not turn SourceConcept into Entity truth. `SourceConcept`, `SourceConceptAlias`, and `SourceConceptEvidence` remain source-layer evidence only. This phase does not create confirmed assignments and does not mutate `media_tags`.

Implementation branch: `codex/phase45-sc2-source-concept-search-evidence-ui`.

## Scope

Implemented:

- read-only SourceConcept alias expansion in `/api/search`;
- read-only SourceConcept detail and promotion-preview endpoints under `/api/source-concepts`;
- SourceConcept grouping payload in the existing media source-layer API;
- media-detail SourceConcept cards with aliases, status, providers, origins, trust tiers, evidence counts, linked-media counts, safe evidence preview, and disabled promotion preview;
- search-result SourceConcept expansion and `needs_review` hint explanations;
- reviewer closeout fixes for canonicalized key redaction, SourceConcept search-cache invalidation, and uncapped search filtering semantics;
- focused pytest and real Edge browser E2E for the SC2 user flows.

Not implemented:

- Entity bridge or promotion writes;
- DB schema changes or migrations;
- provider/gallery-dl/Pixiv/SauceNAO/Google enrichment;
- LLM extraction/classification/localization;
- source/iCloud/app-managed storage mutation;
- DOC1 documentation consolidation.

## Safety / Hard Constraints

SC2 is read-only over truth paths. It must not create or mutate:

- `Entity`
- `EntityAlias`
- `EntityEvidence`
- `MediaEntityCandidate`
- `MediaEntityAssignment`
- `LocalSourceHint`
- confirmed assignments
- `TagTranslation`
- `media_tags`

The implementation uses existing SC1 tables and `SourceConceptSearchIndex`. No migration was added.

## Implementation

### Backend

- Added `backend/app/services/source_concept_search_service.py` as the read-only SourceConcept search/evidence helper.
- Added `backend/app/routes/source_concepts.py` for safe detail and disabled promotion-preview endpoints.
- Included the route from `backend/app/main.py`.
- Extended `/api/search` to return `source_concept_expansions` and `source_concept_review_hints`.
- Extended the existing source-layer media API payload with `source_concepts` so media detail can render concept grouping without recomputing resolver logic.

Search expansion is term-bounded:

- active SourceConcept aliases can expand ordinary positive search terms as an OR inside that term;
- multiple terms keep the existing AND behavior;
- negative terms remain bounded by applying the same term condition under negation;
- quoted terms are preserved as one exact alias token by the existing parser behavior;
- `needs_review` concepts do not expand by default and surface only as hints unless `include_source_needs_review=1` is explicitly supplied.

### UI

- Media detail source-layer UI now includes SourceConcept cards before lower-level source assertions/source tags.
- SourceConcept chips default to ordinary global `q=` search, preserving the F6 default workflow.
- Search result UI shows which SourceConcept matched, which aliases expanded, status, source-layer/unconfirmed label, provider summary, and evidence count.
- Manual promotion preview is visible only as disabled/no-op UI.

### Redaction

The detail/evidence payload intentionally returns safe summaries only:

- concept id/key/display name/status;
- aliases after redaction checks;
- providers, signal origins, trust tiers;
- evidence and linked-media counts;
- safe evidence row summaries.

Unsafe/path-like strings, filenames, API keys, secrets, and private raw payload fields are redacted or omitted.
Raw `SourceConcept.concept_key` is not returned by the user-facing SourceConcept APIs, media source-layer payload, search expansion payload, or evidence preview.
Canonicalized path-like and filename-like values are also treated as unsafe, even when path separators were removed before persistence.

## Validation

Static and focused checks run during implementation:

```powershell
& "$PY" -m py_compile backend/app/services/source_concept_search_service.py backend/app/routes/source_concepts.py backend/app/services/source_assertion_search_service.py backend/app/routes/search.py backend/app/main.py scripts/seed_phase45_sc2_e2e_fixture.py tests/test_phase45_sc2_source_concept_search_evidence_ui.py
& "$PY" -m pytest tests/test_phase45_sc2_source_concept_search_evidence_ui.py -v
& "$PY" -m pytest tests/test_phase44p2r_f6_source_layer_search.py tests/test_phase45_sc2_source_concept_search_evidence_ui.py -v
& "$PY" -m pytest tests/test_phase45_sc1_source_concept_resolver.py -v
python -m json.tool frontend/static/locales/en.json
python -m json.tool frontend/static/locales/zh-cn.json
```

Observed results:

- `tests/test_phase45_sc2_source_concept_search_evidence_ui.py`: `11 passed`.
- `tests/test_phase44p2r_f6_source_layer_search.py` + `tests/test_phase45_sc2_source_concept_search_evidence_ui.py`: `29 passed`.
- `tests/test_phase45_sc1_source_concept_resolver.py`: `47 passed`.
- touched Python files compiled successfully.
- touched locale JSON files parsed successfully.

Real browser validation:

```powershell
& "$PY" scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree
& "$PY" scripts/check_test_server_identity.py --base-url http://127.0.0.1:8012 --expected-env test --expected-db blombooru_test --expected-code-root <repo> --expected-git-sha cba0275 --expected-branch codex/phase45-sc2-source-concept-search-evidence-ui --expected-python <repo-venv-python> --expected-storage-root <dedicated-test-storage> --admin-username admin --admin-password admin123
& "$PY" scripts/seed_phase45_sc2_e2e_fixture.py
npx playwright test tests/e2e/source-concept-search-evidence.spec.ts --project=edge
```

Observed results:

- active server audit before start: `occupied_count=0`, `violet_server_count=0`, `unknown_listener_count=0`.
- controlled test server: port `8012`, `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test`, dedicated test storage.
- identity preflight: `OK: all checks passed`.
- E2E fixture seed: `status=ready`, active concept and needs-review concept created in test DB only.
- Playwright Edge SC2 E2E: `4 passed`.
- In-app browser validation:
  - media detail SourceConcept section visible;
  - evidence preview present;
  - disabled promotion preview button present;
  - mixed normal tag + SourceConcept search showed expansion explanation and one gallery result for the test fixture;
  - console errors: `[]`;
  - unsafe text/path check: `false`.
- server stopped after validation; port `8012` had no listener.

## Test Plan

- [x] Python files compiled
- [x] Focused SC2 pytest passed
- [x] F6 source-layer regression pytest passed
- [x] SC1 resolver focused pytest passed
- [x] Locale JSON parsed
- [x] Controlled test server identity check passed
- [x] Playwright Edge E2E passed
- [x] Real in-app browser validation passed
- [x] No truth-path write tests passed
- [x] Unsafe evidence/path/API-key redaction tests passed
- [ ] Full non-E2E suite not run; not required for this bounded UI/search phase
- [ ] Broad/manual development DB validation not run; SC2 automated validation used test DB fixtures only

## Reviewer / Codex Status

Local pre-review and same-class self-audit found and fixed one PostgreSQL compatibility issue: selecting distinct full `SourceConcept` ORM rows pulled JSON columns into `DISTINCT`, which fails on PostgreSQL. The service now distincts concept IDs first and then loads concept rows by ID.

Latest reviewer status should be checked on the SC2 PR head after PR creation.

## Safety Confirmation

- No push to `main`.
- No merge.
- No DB migration.
- No provider/gallery-dl/Pixiv/SauceNAO/Google enrichment.
- No LLM extraction/classification/localization.
- No image upload.
- No source/iCloud/app-managed storage mutation.
- No development DB writes for manual validation.
- No Entity/EntityAlias/EntityEvidence/MediaEntityCandidate/MediaEntityAssignment/LocalSourceHint writes.
- No `TagTranslation` mutation.
- No `media_tags` mutation.
- Test DB fixture writes were limited to automated SC2 validation.

## Next Step

Open a normal reviewable PR for Phase 4.5-SC2, trigger `@codex review`, and wait for human/reviewer feedback. Do not start DOC1 or the Entity bridge inside SC2.

## Engineering Judgment / Operator Notes

Artifact lifecycle:

- Durable production code: read-only backend service/routes, search integration, and UI rendering.
- Phase-scoped operational runner: `scripts/seed_phase45_sc2_e2e_fixture.py`.
- Validation code: focused pytest and Playwright E2E.
- Public report/handoff/roadmap update: this report plus current handoff, roadmap, and test workflow updates.

Risk assessment:

- Over-broad search risk is controlled by exact search-index lookup, term-bounded OR expansion, preserved AND semantics, and conservative `needs_review` default behavior.
- UI confusion risk is controlled by separate SourceConcept styling and explicit source-layer/unconfirmed labels.
- Performance risk is bounded by using `SourceConceptSearchIndex` and media-link joins instead of full resolver recomputation on ordinary search.
- Redaction risk is covered by tests and browser checks, but future provider payload shapes should keep using allowlisted summary fields rather than raw payload display.
- Entity bridge remains a later phase. It still needs explicit design for manual confirmation, audit trail, write guards, rollback/supersede behavior, and no accidental `media_tags` truth pollution.
- DOC1 remains a separate documentation consolidation phase after SC2 review/merge.
