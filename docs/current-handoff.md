# Current Handoff - V.I.O.L.E.T.

> Last updated during Phase 4.5-SCV2-P0 on `2026-06-08T21:42:49+08:00`.
> Active PR branch: `codex/phase45-scv2-p0-controlled-medium-expansion-policy`.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Current PR | Phase 4.5-SCV2-P0 controlled medium expansion policy |
| Baseline main | PR #100 / SCV1 merge commit `c1c2cf3` or later |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript |
| Python | `.\venv\Scripts\python.exe` |

## Current Accepted State

- PR #96 / Phase 4.5-SC1 is merged. It delivered the multi-source SourceConcept resolver core: source signals, aliases, evidence, links, search-preview rows, run ledger, readiness checks, and no-truth-write validation.
- PR #97 / Phase 4.5-SC2-P0 is merged. It documented the post-SC1 handoff and SC2 plan.
- PR #98 / Phase 4.5-SC2 is merged. It delivered SourceConcept search expansion, media-detail chips/grouping, evidence preview, `needs_review` source-layer search behavior, and disabled/no-op promotion preview.
- PR #99 / Phase 4.5-DOC1-R1 is merged. It restructured docs and classified guard debt.
- PR #100 / Phase 4.5-SCV1 is merged. It generated a read-only current-DB coverage audit, search symmetry check, alias-gap analysis, `needs_review` cluster analysis, redaction proof, and decision matrix.
- Phase 4.5-SCV2-P0 generated a read-only current-DB inventory and governed split for controlled medium expansion on branch `codex/phase45-scv2-p0-controlled-medium-expansion-policy`.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.
- Historical details before SC1/SC2/SCV1/SCV2-P0 live in `docs/project-roadmap.md` and `docs/reports/`.

## Current Route

SCV2-P0 recommendation:

`SCV2-E1` - Medium Import + AI Tag Completion, then `PX1`, `SCV2-R1`, and `SCV2-A1`.

P0 confirms current eligible AI tag coverage is complete and that already-imported Pixiv-like media substantially exceed source metadata coverage. The next executable phase should be a controlled medium import with AI tag completion only after target, source roots, staging/import safety, AI job behavior, localization-off behavior, and item ledger are approved. Pixiv/gallery-dl/provider metadata belongs in PX1, not E1.

## Current Known Observations / Validation Seeds

- SCV1 tested Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus the prompt mojibake seed variants.
- Nahida seed matched 10 visible concept IDs and 33 media, so the immediate issue is not a single missing alias; it is broader alias fragmentation and `needs_review` noise.
- Source metadata remains sparse: SCV1 recorded 60 source metadata records linked to 1989 media, so any Pixiv/source metadata expansion must be a separate bounded provider phase with ledger/privacy guards.
- SCV2-P0 recorded 1989 total media, 1936 eligible media, eligible AI tag coverage 1936/1936, 557 DB-derived Pixiv-like media candidates, 60 Pixiv-like candidates with source metadata, and 497 Pixiv-like metadata backlog.

## Hard Non-Goals Without Explicit Approval

- No push to `main`; agents do not merge PRs.
- No DB migration, DB import, DB write, cleanup, drop, truncate, or destructive operation.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment run.
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
- SCV2-P0 runner: `& "$PY" scripts/run_phase45_scv2_p0_controlled_medium_expansion_policy.py --output-dir ".local_manifests\phase-4.5-scv2-p0-controlled-medium-expansion-policy" --write-public-report --read-only`.
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
- Historical reports: `docs/reports/`.
