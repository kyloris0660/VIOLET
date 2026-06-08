# Current Handoff - V.I.O.L.E.T.

> Last updated during Phase 4.5-DOC1-R1 on `2026-06-08T12:25:15+08:00`.
> Active PR branch: `codex/phase45-doc1-post-sc2-doc-consolidation` for PR #99.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Current PR | #99 / Phase 4.5-DOC1-R1 documentation restructuring |
| Baseline main | PR #98 / SC2 merge commit `192eba7` or later |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript |
| Python | `.\venv\Scripts\python.exe` |

## Current Accepted State

- PR #96 / Phase 4.5-SC1 is merged. It delivered the multi-source SourceConcept resolver core: source signals, aliases, evidence, links, search-preview rows, run ledger, readiness checks, and no-truth-write validation.
- PR #97 / Phase 4.5-SC2-P0 is merged. It documented the post-SC1 handoff and SC2 plan.
- PR #98 / Phase 4.5-SC2 is merged. It delivered SourceConcept search expansion, media-detail chips/grouping, evidence preview, `needs_review` source-layer search behavior, and disabled/no-op promotion preview.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.
- Historical details before SC1/SC2 live in `docs/project-roadmap.md` and `docs/reports/`.

## Current Route

Next recommended non-doc phase:

`Phase 4.5-SCV1: Expanded SourceConcept validation and coverage audit`

SCV1 should run before any Entity bridge, SourceConcept editing, or promotion work. It should start from current DB data and read-only reporting, then inventory SourceConcept coverage, alias gaps, `needs_review` clusters, redaction, and search symmetry. It should not run new imports, providers, LLMs, AI tagging/classification/localization, source enrichment, or full-library validation unless separately approved.

## Current Known Observations / Validation Seeds

- `nahida_(genshin_impact)` currently expands to Nahida / `nahida_(genshin_impact)`.
- `纳西妲` currently appears as separate Pixiv/source evidence and is not yet linked to the Nahida concept.
- SCV1 should investigate Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus other cross-language aliases, especially names not repeatedly used in golden fixtures.

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
- Scope-based test selection: `docs/test-workflow.md`.
- Manual development validation: `docs/manual-validation.md`.
- Source/iCloud safety: `docs/icloud-safe-ingestion.md`.
- Server lifecycle preflight for any agent-started server: `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree`.

## Links

- Active roadmap and standards: `docs/project-roadmap.md`.
- DOC1 report: `docs/reports/phase-4.5-doc1-post-sc2-documentation-consolidation.md`.
- SC1 report: `docs/reports/phase-4.5-sc1-source-concept-resolver-core.md`.
- SC2 report: `docs/reports/phase-4.5-sc2-source-concept-search-evidence-ui.md`.
- Historical reports: `docs/reports/`.
