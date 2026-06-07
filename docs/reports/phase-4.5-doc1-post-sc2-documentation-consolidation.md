# Phase 4.5-DOC1 Post-SC2 Documentation Consolidation

## Summary

Phase 4.5-DOC1 consolidates the accepted post-SC2 state across the lightweight entry docs and records an executable-guard audit for SourceConcept safety requirements.

PR #96 / SC1, PR #97 / SC2-P0, and PR #98 / SC2 are merged into `main`. SourceConcept is now usable through search expansion and compact evidence UI, but it remains source-layer evidence only: not Entity truth, not EntityAlias truth, not a confirmed assignment, and not media_tags truth.

The next recommended non-doc phase is `Phase 4.5-SCV1: Expanded SourceConcept validation and coverage audit`, before any Entity bridge or promotion work.

## Scope

Included:

- README current-state cleanup.
- Current handoff update after PR #98.
- Roadmap update marking SC2 complete and placing SCV1 before Entity bridge work.
- Test workflow update for SC2 guard coverage and future SCV1 validation shape.
- Guard audit based on current code/tests/reports.

Not included:

- Product feature implementation.
- Runtime, UI, DB, provider, LLM, source-enrichment, AI tagging/classification, localization, media_tags, or Entity truth changes.
- Historical report rewrites.
- SCV1, SC3, or Entity bridge work.

## Documents and Guards Inspected

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/test-workflow.md`
- `docs/manual-validation.md`
- `docs/reports/phase-4.5-sc1-source-concept-resolver-core.md`
- `docs/reports/phase-4.5-sc1-source-concept-resolver-core-summary.json`
- `docs/reports/phase-4.5-sc2-p0-postmerge-handoff-and-plan.md`
- `docs/reports/phase-4.5-sc2-p0-postmerge-handoff-and-plan-summary.json`
- `docs/reports/phase-4.5-sc2-source-concept-search-evidence-ui.md`
- `docs/reports/phase-4.5-sc2-source-concept-search-evidence-ui-summary.json`
- `tests/test_phase45_sc1_source_concept_resolver.py`
- `tests/test_phase45_sc2_source_concept_search_evidence_ui.py`
- `tests/test_phase44p2r_f6_source_layer_search.py`
- `tests/e2e/source-concept-search-evidence.spec.ts`
- `tests/e2e/source-layer-search.spec.ts`
- `backend/app/services/source_concept_search_service.py`
- `backend/app/routes/source_concepts.py`
- `backend/app/services/source_concept_resolver_service.py`
- `scripts/seed_phase45_sc2_e2e_fixture.py`

## Executable Guard Audit

| Requirement | Current executable coverage | Status | Follow-up |
|-------------|-----------------------------|--------|-----------|
| SourceConcept is not Entity truth | SC1 validation reports `forbidden_truth_table_write_count=0`; SC1 tests cover no Entity truth writes and trust guards; SC2 `test_source_concept_read_paths_do_not_write_truth_tables` checks search/detail/media/promotion read paths; F6 promotion preview test checks no truth-path write. | Enforced for SC1/SC2 touched paths. | Keep carrying no-truth-write assertions into SCV1/SC3 when those phases touch SourceConcept writes or promotion preview. |
| SourceConcept search/evidence UI must not leak local paths, filenames, or secrets | SC2 redaction tests cover detail, media source-layer payload, search payload, canonicalized path/filename keys, filename-like aliases, unsafe primary display fallback, and promotion preview payloads. SC2 E2E checks no visible `C:\`, `Users`, `api_key`, `secret-token`, or private filename text in the exercised UI/API flow. | Enforced for SC2 payload and E2E fixture shapes. | Future provider payload shapes should stay allowlisted; SCV1 should include larger redacted sample artifacts. |
| Detail endpoint visible-status gate | `test_detail_and_promotion_preview_visibility_gate_by_status` proves `active` and `needs_review` are visible while `rejected`, `ambiguous`, and `superseded` return safe not-found and do not expand in search. | Enforced. | Reuse when SourceConcept management or status transitions are added. |
| SourceConcept search cache invalidation | `test_search_cache_is_invalidated_after_source_concept_rows_change` covers stale cached search responses; `test_source_concept_write_paths_invalidate_search_cache` covers resolver persistence and SC2 fixture cleanup/seed paths; F6 source registry cache invalidation remains tested. | Enforced for current write paths. | Future write/edit endpoints must call the same invalidation helper and add focused tests. |
| Alias expansion symmetry | `test_source_concept_aliases_expand_to_same_concept_level_media_set` proves bidirectional concept-level alias closure for linked aliases in the SC2 fixture. | Partially enforced. | SCV1 should add broader search-symmetry inventory/property-style checks across real current DB aliases, not only one golden set. |
| `needs_review` behavior | SC2 API and E2E tests prove explicit alias `q=` search can expand `needs_review` SourceConcepts while labeling them as unconfirmed source-layer; hidden statuses stay gated. F6 scoped/random needs-review opt-in behavior remains tested separately. | Enforced for SC2 behavior. | SCV1 should audit whether large-sample `needs_review` clusters are useful or noisy. |
| F6 global `q=` chip behavior preservation | F6 tests cover source-layer chips/search behavior; SC2 E2E verifies SourceConcept chip click uses global `q=` and does not set scoped `source_assertion` / `source_tag` filters. | Enforced. | Keep regression coverage if chip components are refactored. |
| SC2 no provider/LLM/source enrichment | SC2 report and code/diff scope show no provider, gallery-dl, source enrichment, LLM, classification, localization, or import work. Current tests primarily enforce no truth writes and safe read payloads, not a generic "no provider call" runtime guard. | Mostly documented/static-scope for SC2. | If future read routes get provider-adjacent dependencies, add monkeypatch/no-call tests or an explicit dependency allowlist. |
| Manual promotion preview disabled/no-op | SC2 read-path no-truth-write test checks `preview["disabled"] is True` and `truth_writes_allowed` is false; E2E checks promotion preview API remains disabled, preview-only, and lists forbidden truth paths including `media_tags`; F6 preview no-op remains tested. | Enforced for current preview endpoints. | Real promotion remains a later explicit Entity bridge phase with new write guards. |

## Known Observation for Future Validation

Manual validation after SC2 found:

- `nahida_(genshin_impact)` expands to `Nahida` / `nahida_(genshin_impact)`.
- `纳西妲` appears as separate Pixiv/source evidence and does not currently link to the Nahida concept.

This likely reflects limited sample/source evidence coverage and incomplete cross-language alias evidence. It is not a DOC1 blocker and not an SC2 UI implementation blocker.

SCV1 should investigate this and similar aliases, including:

- Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)`
- other Genshin names
- other cross-language aliases not repeatedly used in the golden set

DOC1 does not hardcode a fix, run providers/LLMs/imports, or implement SourceConcept editing.

## Recommended Next Phase

`Phase 4.5-SCV1: Expanded SourceConcept validation and coverage audit`

Likely goals:

- inventory current DB coverage: media count, AI tag coverage, Pixiv/source signal coverage, F7a candidate coverage, SourceConcept coverage, and search expansion coverage;
- identify alias gaps across Japanese/English/Chinese/romaji variants, ordinary tags, Pixiv/source tags, AI tags, and `needs_review` clusters;
- run larger current-data validation without new import first;
- decide whether to expand to 10k/full-library data;
- only then consider controlled broader import, AI tagging, or source metadata extraction.

## Validation

Observed DOC1 validation:

```powershell
& "$PY" scripts/check_python_env.py --expected-python "$PY"
git diff --check
& "$PY" -m json.tool docs/reports/phase-4.5-doc1-post-sc2-documentation-consolidation-summary.json
```

Results:

- Python env preflight: `PASS`; `sys.executable` was `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`.
- New summary JSON parsed successfully.
- `git diff --check` / staged whitespace check: run before commit, no whitespace errors.

No pytest, browser validation, server start, provider, LLM, import, AI tagging/classification/localization, source enrichment, DB import, or media_tags/Entity truth mutation is required for this docs-only PR.

## Safety Confirmation

- No code/runtime/UI changes.
- No DB migration, DB import, DB cleanup, drop, truncate, or destructive operation.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment.
- No LLM run.
- No AI tagging, classification, or localization run.
- No source/iCloud/staging/app-managed storage mutation.
- No `media_tags` mutation.
- No `Entity`, `EntityAlias`, confirmed assignment, or Entity bridge write.
- No full-library validation.
- Historical reports were left archival and unchanged.

## Engineering Judgment / Operator Notes

Artifact lifecycle:

- README/current handoff/roadmap/test workflow updates: public report / handoff / roadmap update.
- DOC1 report and summary JSON: public report / handoff / roadmap update.

Phase boundary fit:

- DOC1 is appropriately docs-only. The guard audit found useful executable coverage for SC2 redaction, visible statuses, cache invalidation, no-truth writes, `needs_review`, F6 q-chip preservation, and promotion no-op.
- The main remaining gap is coverage validation breadth, not immediate UI correctness. That belongs in SCV1, not in a docs consolidation PR.

Risks remaining:

- Alias symmetry is tested with focused fixtures, not broad real-DB/property-style coverage.
- SC2 no-provider/no-LLM/no-source-enrichment is mostly a documented/static scope boundary, not a generic runtime guard.
- Cross-language alias coverage is not proven beyond current samples.

Recommended route:

- Proceed to PR review for DOC1.
- After DOC1 is accepted, run SCV1 before any Entity bridge, SourceConcept editing, or promotion phase.
