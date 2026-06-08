# Phase 4.5-DOC1-R1 Documentation Restructuring and Guard Debt Classification

## Summary

Phase 4.5-DOC1-R1 reworks PR #99 from a light post-SC2 status sync into a real documentation consolidation pass:

- README is rewritten as a mature public project entry point.
- `docs/current-handoff.md` is slimmed into a short operational handoff.
- `docs/project-roadmap.md` now puts the active route and governance standards before historical phase archive content.
- `docs/test-workflow.md` now foregrounds scope-based validation and current SourceConcept validation entry points.
- A focused documentation-state pytest guard was added.
- The guard audit now classifies executable coverage and guard debt by the phase that must encode it.

This remains DOC1 work. It does not implement SCV1, SC3, SourceConcept editing, Entity bridge, provider/source enrichment, imports, LLMs, AI tagging/classification/localization, full-library validation, runtime behavior, UI behavior, or DB writes.

## Context

PR #96 / SC1, PR #97 / SC2-P0, and PR #98 / SC2 are merged into `main`. SC1 delivered the SourceConcept resolver core. SC2 made existing SourceConcepts visible through search expansion and compact evidence UI.

SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.

The next recommended non-doc phase is `Phase 4.5-SCV1: Expanded SourceConcept validation and coverage audit`, before any Entity bridge, promotion, or SourceConcept editing work.

## Documentation Restructure

### README Rewrite

The README was changed from an internal current-state note into a public project README. It now answers:

- what V.I.O.L.E.T. is;
- what problem it solves;
- core capabilities;
- current development status;
- architecture;
- Windows local quick start;
- safety/privacy model;
- documentation map;
- current development focus;
- upstream attribution and license.

It avoids long phase history and avoids treating PR numbers as onboarding material. It mentions SourceConcepts only at a high level as source-layer evidence, not Entity truth.

### Current Handoff Slimming

`docs/current-handoff.md` is now a compact operational handoff. It keeps only:

- canonical repo/local path/current PR context;
- current accepted SC1/SC2 state;
- current route;
- hard non-goals;
- current known validation seeds;
- active governance reminders;
- validation starting points;
- links to roadmap and reports.

Historical phase details are no longer copied into the handoff. They remain discoverable in `docs/project-roadmap.md` and `docs/reports/`.

### Roadmap Restructure

`docs/project-roadmap.md` now starts with:

- project vision;
- current active roadmap;
- near-term route;
- current governance / development standards.

The long historical phase content remains in the same file under `Phase Archive / Historical Traceability` to preserve traceability without adding a separate archive file or rewriting old reports. This keeps churn lower while making the active route visible near the top.

### Test Workflow Restructure

`docs/test-workflow.md` now makes scope-based validation clearer near the top:

- docs-only validation requirements are explicit;
- current DOC1/SC1/SC2/SCV1 validation entry points are summarized;
- test inventory remains as reference;
- docs-only work does not require pytest/E2E/browser/server unless code, tests, runtime, or UI changes are made;
- the new DOC1 documentation-state guard is listed.

## Line Counts

| File | Before DOC1-R1 | After DOC1-R1 | Change |
|------|----------------|---------------|--------|
| `README.md` | 106 | 113 | +7 |
| `docs/current-handoff.md` | 107 | 71 | -36 |
| `docs/project-roadmap.md` | 995 | 1043 | +48 |
| `docs/test-workflow.md` | 409 | 423 | +14 |

No separate archive file was created. The roadmap keeps historical traceability in-place under an archive heading.

## Executable Guards Added In DOC1-R1

Added `tests/test_phase45_doc1_documentation_state.py`.

Artifact lifecycle: reusable validation/safety test for active documentation state. It is intentionally narrow: it reads documentation and summary JSON only, with no DB, server, provider, LLM, runtime, UI, browser, or network dependency.

It checks:

- active docs do not reintroduce stale SC2 planned/prepared language;
- README has public-project sections and is not a phase changelog;
- current handoff remains short and current-route focused;
- DOC1 summary JSON has required machine-readable fields;
- guard debt classifications use the approved enum and include concrete future guard shapes for deferred items;
- docs-only validation policy remains explicit in `docs/test-workflow.md`.

## Guard Debt Classification

| Guard | Classification | Current executable evidence / future required guard |
|-------|----------------|-----------------------------------------------------|
| SourceConcept not Entity truth | `executable_now` | SC1 readiness and tests report `forbidden_truth_table_write_count=0`; SC2 read-path no-truth-write test covers search/detail/media/promotion paths. |
| No truth-path writes from SC1/SC2 read paths | `executable_now` | `tests/test_phase45_sc2_source_concept_search_evidence_ui.py::test_source_concept_read_paths_do_not_write_truth_tables`; F6 promotion preview no-op test. |
| Redaction of local paths, filenames, secrets, canonicalized path/filename-like aliases | `executable_now` | SC2 backend redaction tests and E2E cover detail/search/media/promotion payloads and visible UI fixture. |
| SourceConcept visible-status detail gate | `executable_now` | SC2 status-gate test proves `active`/`needs_review` visible and `rejected`/`ambiguous`/`superseded` hidden. |
| SourceConcept search cache invalidation | `executable_now` | SC2 stale-cache and write-path invalidation tests; F6 source registry invalidation test. |
| Alias expansion symmetry | `must_add_in_scv1` | Focused SC2 fixture coverage exists, but broad real-DB/property-style symmetry checks are not proven. SCV1 must add search-symmetry inventory over real current aliases. |
| `needs_review` explicit alias expansion behavior | `executable_now` | SC2 API and E2E tests prove explicit alias `q=` search expands `needs_review` concepts while labeling them unconfirmed/source-layer. |
| F6 global `q=` chip behavior | `executable_now` | F6 tests plus SC2 E2E prove source/concept chips use global `q=` and do not default to scoped source filters. |
| Promotion preview disabled/no-op | `executable_now` | SC2 and F6 no-truth-write preview tests plus E2E promotion-preview API checks. |
| SC2 no provider/LLM/source enrichment | `documented_only_acceptable` | SC2 code scope and reports show no provider/LLM/source enrichment. A generic no-call runtime guard is not worth adding to DOC1 because DOC1 touches docs/tests only and SC2 read routes do not depend on provider adapters. If future read routes become provider-adjacent, add monkeypatch no-call tests or an explicit dependency allowlist in that phase. |
| SCV1 coverage inventory / alias-gap / search-symmetry checks | `must_add_in_scv1` | SCV1 must add a read-only validation runner or focused tests for coverage inventory, larger current-data samples, alias-gap analysis, `needs_review` clusters, redaction, and search symmetry. |
| Entity bridge preview/confirmation/audit/rollback/write guards | `must_add_before_sc3_or_entity_bridge` | Before editing or truth writes, add DB/write-guard tests for preview-only flows, manual confirmation, audit trail, rollback/supersede, and no `media_tags` pollution. |
| Provider/gallery-dl/LLM/broad-run ledger and budget gates | `must_add_before_provider_or_full_library_run` | Before provider/source/LLM runs, add provider policy checks, privacy eligibility tests, budget gates, cache/audit assertions, rate limits, and redacted public report validation. |
| Phase 3.9 ledger prerequisite for broad/repeated provider/full-library scale | `must_add_before_provider_or_full_library_run` | Before 100+/5k/10k/full-library scale, implement production ingestion/source item run ledger with per-item final state, failure reason, retry/defer state, and public/private artifact separation. |
| Docs-only validation policy | `add_in_doc1_r1` | Added `tests/test_phase45_doc1_documentation_state.py` and clarified `docs/test-workflow.md`. |
| Agent no-merge/no-push-main policy | `documented_only_acceptable` | This is process/repository governance, not runtime code. It remains in AGENTS/CLAUDE/roadmap/PR body; stronger enforcement should be GitHub branch protection/rulesets outside DOC1. |

## Known Observation For SCV1

Manual validation after SC2 found:

- `nahida_(genshin_impact)` expands to `Nahida` / `nahida_(genshin_impact)`.
- `纳西妲` appears as separate Pixiv/source evidence and does not currently link to the Nahida concept.

This likely reflects limited sample/source evidence coverage and incomplete cross-language alias evidence. It is not a DOC1 blocker and not an SC2 UI implementation blocker.

SCV1 should investigate this and similar aliases, including:

- Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)`
- other Genshin names
- other cross-language aliases not repeatedly used in golden fixtures

DOC1-R1 does not hardcode a fix, run providers/LLMs/imports, or implement SourceConcept editing.

## Deferred Guards

- `must_add_in_scv1`: coverage inventory, broad alias-gap analysis, real current-data search symmetry checks, redacted larger-sample evidence artifacts.
- `must_add_before_sc3_or_entity_bridge`: SourceConcept editing/management and Entity bridge preview, confirmation, audit, rollback, write guards, and truth-path pollution tests.
- `must_add_before_provider_or_full_library_run`: provider/source/LLM run policies, privacy eligibility, budget/cache/audit/rate-limit gates, Phase 3.9 run ledger/source item ledger.
- `documented_only_acceptable`: agent no-merge/no-push-main remains process/repository governance; SC2 no-provider/no-LLM remains static-scope/documented because DOC1-R1 does not touch runtime/provider dependencies.

## Validation

Observed DOC1-R1 validation:

```powershell
git diff --check
git diff --cached --check
$PY = "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
& "$PY" scripts/check_python_env.py --expected-python "$PY"
& "$PY" -m json.tool docs/reports/phase-4.5-doc1-post-sc2-documentation-consolidation-summary.json
& "$PY" -m py_compile tests/test_phase45_doc1_documentation_state.py
& "$PY" -m pytest tests/test_phase45_doc1_documentation_state.py -v
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD
```

Results before commit:

- Python identity: `PASS`; `sys.executable` was `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`.
- Summary JSON parsed successfully with `json.tool`.
- `tests/test_phase45_doc1_documentation_state.py` compiled successfully.
- Focused DOC1-R1 pytest: `5 passed`.
- `git diff --check` and `git diff --cached --check`: pass.

No server, browser validation, provider, LLM, import, AI tagging/classification/localization, source enrichment, DB import, DB write, or full-library validation is required or allowed for this docs/test-only rework.

## Safety Confirmation

- No runtime/UI behavior changes.
- No DB migration, DB import, DB write, cleanup, drop, truncate, or destructive operation.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment.
- No LLM run.
- No AI tagging, classification, localization, or background job.
- No source/iCloud/staging/app-managed storage mutation.
- No `media_tags` mutation.
- No Entity truth, `EntityAlias` truth, confirmed assignment, SourceConcept editing, Entity bridge, or promotion.
- No SCV1 execution.
- Historical reports were left archival and unchanged.

## Artifact Lifecycle

- README/current handoff/roadmap/test workflow updates: public report / handoff / roadmap update.
- DOC1 report and summary JSON: public report / handoff / roadmap update.
- `tests/test_phase45_doc1_documentation_state.py`: reusable validation/safety test with a narrow documentation-state contract.

## Recommended Next Phase

After DOC1-R1 is reviewed and manually merged, start:

`Phase 4.5-SCV1: Expanded SourceConcept validation and coverage audit`

SCV1 should remain read-only first and should not start Entity bridge, SourceConcept editing, provider/source enrichment, LLM, AI tagging/classification/localization, import, or full-library validation without a separate approved scope.

## Engineering Judgment / Operator Notes

DOC1-R1 now fits the intended consolidation goal better than the initial DOC1 status-sync commit. The README is no longer a handoff file, current handoff is substantially shorter, roadmap active route is visible near the top, test workflow has clearer scope-based validation, and GOV-2 guard debt is classified by future phase.

The remaining risk is not documentation structure; it is SourceConcept coverage breadth. Existing SC2 tests cover focused user-flow and safety invariants, but broad alias coverage and real current-data search symmetry are not proven. That is exactly the SCV1 job and should not be smuggled into DOC1-R1.
