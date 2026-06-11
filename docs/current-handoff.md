# Current Handoff - V.I.O.L.E.T.

> Last updated after Phase 4.5-SCV2-R1 execute on `2026-06-11T23:58:17+08:00`.
> Active PR branch: `codex/phase45-scv2-r1-post-px1-source-concept-triage`.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Current PR | Phase 4.5-SCV2-R1 post-PX1 SourceConcept triage |
| Baseline main | PR #103 / PX1 merge commit `20e31c1` or later |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript |
| Python | `.\venv\Scripts\python.exe` |

## Current Accepted State

- PR #96 / Phase 4.5-SC1 is merged. It delivered the multi-source SourceConcept resolver core: source signals, aliases, evidence, links, search-preview rows, run ledger, readiness checks, and no-truth-write validation.
- PR #97 / Phase 4.5-SC2-P0 is merged. It documented the post-SC1 handoff and SC2 plan.
- PR #98 / Phase 4.5-SC2 is merged. It delivered SourceConcept search expansion, media-detail chips/grouping, evidence preview, `needs_review` source-layer search behavior, and disabled/no-op promotion preview.
- PR #99 / Phase 4.5-DOC1-R1 is merged. It restructured docs and classified guard debt.
- PR #100 / Phase 4.5-SCV1 is merged. It generated a read-only current-DB coverage audit, search symmetry check, alias-gap analysis, `needs_review` cluster analysis, redaction proof, and decision matrix.
- Phase 4.5-SCV2-P0 generated a read-only current-DB inventory and governed split for controlled medium expansion on branch `codex/phase45-scv2-p0-controlled-medium-expansion-policy`.
- PR #102 / Phase 4.5-SCV2-E1 is merged. It expanded the library to 3750 media and completed eligible AI tag coverage without Pixiv/provider/SourceConcept resolver work.
- PR #103 / Phase 4.5-PX1 is merged. It ran the bounded Pixiv/gallery-dl metadata extraction batch: 500 selected, 470 metadata successes, 30 unavailable/private/deleted failures, zero exact duplicate dry-run groups, and source-layer-only metadata/assertion writes.
- Phase 4.5-SCV2-R1 has run dry-run and execute on branch `codex/phase45-scv2-r1-post-px1-source-concept-triage`. It consumed PX1 evidence into SourceConcept triage, wrote only allowed SourceConcept tables, and generated the R1 public report/summary.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.
- Historical details before SC1/SC2/SCV1/SCV2-P0/E1/PX1/R1 live in `docs/project-roadmap.md` and `docs/reports/`.

## Current Route

`SCV2-A1` - post-expansion SourceConcept audit and route decision after R1.

R1 target was met: PX1 source-layer evidence was consumed by SourceConcept resolver/triage, mutation proof and public redaction passed, and no truth-path/source-metadata writes occurred. The next step is not more provider extraction, broader import, DEDUP1, or Entity bridge.

## Current Known Observations / Validation Seeds

- SCV1 tested Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus the prompt mojibake seed variants.
- Nahida seed matched 10 visible concept IDs and 33 media, so the immediate issue is not a single missing alias; it is broader alias fragmentation and `needs_review` noise.
- PX1 post-E1 baseline recorded 3750 total media, 3687 eligible media, eligible AI tag coverage 3687/3687, 2287 Pixiv-like candidates, 500 selected metadata requests, 470 metadata successes, and zero exact duplicate dry-run groups.
- PX1 source assertions are intentionally `needs_review` with `requires_review=true`; R1 may consume them as review-scoped SourceConcept input but must not promote them into active search truth or Entity/media_tags truth.
- R1 SourceConcept counts moved from 4214 to 6094 total, 355 to 1078 active, and 760 to 1809 `needs_review`; 1692 concepts are now influenced by PX1 evidence.
- R1 alias gap deltas improved source assertion/source name/source tag/identity-tag gaps but increased total gap signals by 626 because PX1 added much more review-scoped evidence and fragmentation to triage.
- R1 search seed symmetry checked 10 groups / 67 seeds / 49 matched seeds; all 10 groups remain asymmetric and should be reviewed in A1 before any truth bridge.

## Hard Non-Goals Without Explicit Approval

- No push to `main`; agents do not merge PRs.
- No further DB migration, DB import, DB write, cleanup, drop, truncate, or destructive operation without explicit approval. R1's completed execute writes were limited to the allowed SourceConcept resolver tables.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment run unless a later phase explicitly approves it.
- No LLM, AI tagging, classification, localization, background jobs, or full-library validation.
- No source/iCloud/staging/app-managed storage mutation.
- No Entity Resolver, similarity/clustering, SourceConcept editing, Entity bridge, promotion, confirmed assignment, trusted Entity creation, or `media_tags` mutation.

## Active Governance Reminders

- GOV-2 is active: prefer executable guards, focused tests, DB constraints, validation runners, and runtime assertions over repeated long policy text.
- Durable core remains strict: DB/migrations, provider-neutral evidence contracts, Entity/evidence/candidate/assignment lifecycle, provider privacy/budget gates, source/iCloud safety, and in-scope E2E pass requirements.
- Docs-only work should use docs/JSON/static checks and should not start servers or browser validation unless code/runtime/UI changes are made.
- Reviewer feedback is a handoff point. Do not auto-fix reviewer comments after the requested stop line without explicit bounded-fix authorization.

## Validation Starting Points

- Python identity: `& "$PY" scripts/check_python_env.py --expected-python "$PY"`.
- SCV1 runner: `& "$PY" scripts/run_phase45_scv1_source_concept_coverage_audit.py --output-dir ".local_manifests\phase-4.5-scv1-source-concept-coverage-audit" --write-public-report --read-only`.
- SCV2-R1 runner: `& "$PY" scripts/run_phase45_scv2_r1_post_px1_source_concept_triage.py --dry-run --output-dir ".local_manifests\phase-4.5-scv2-r1-post-px1-source-concept-triage" --write-public-report`, then execute only with `--confirm-execution EXECUTE_PHASE45_SCV2_R1_SOURCE_CONCEPT_TRIAGE`.
- Scope-based test selection: `docs/test-workflow.md`.
- Manual development validation: `docs/manual-validation.md`.
- Source/iCloud safety: `docs/icloud-safe-ingestion.md`.
- Server lifecycle preflight for any agent-started server: `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree`.

## Links

- Active roadmap and standards: `docs/project-roadmap.md`.
- DOC1 report: `docs/reports/phase-4.5-doc1-post-sc2-documentation-consolidation.md`.
- SC1 report: `docs/reports/phase-4.5-sc1-source-concept-resolver-core.md`.
- SC2 report: `docs/reports/phase-4.5-sc2-source-concept-search-evidence-ui.md`.
- SCV1 report: `docs/reports/phase-4.5-scv1-source-concept-coverage-audit.md`.
- SCV2-P0 report: `docs/reports/phase-4.5-scv2-p0-controlled-medium-expansion-policy.md`.
- E1 report: `docs/reports/phase-4.5-scv2-e1-medium-import-ai-tag-completion.md`.
- PX1 report: `docs/reports/phase-4.5-px1-pixiv-metadata-dedup-dry-run.md`.
- R1 report: `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md`.
