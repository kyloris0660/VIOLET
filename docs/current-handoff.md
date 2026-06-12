# Current Handoff - V.I.O.L.E.T.

> Last updated for Phase 4.5-SCV2-A1 implementation on `2026-06-12`.
> Active PR branch: `codex/phase45-scv2-a1-post-expansion-audit-route-decision`.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Current PR | Phase 4.5-SCV2-A1 post-expansion audit / route decision |
| Baseline main | PR #104 / R1 merge commit `e48b6d4` or later |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript |
| Python | `.\venv\Scripts\python.exe` |

## Current Accepted State

- PR #96, PR #97, PR #98, PR #99, and PR #100 are merged: SC1 delivered the SourceConcept resolver core, SC2-P0 planned SC2, SC2 added search/evidence UI, DOC1 restructured docs, and Phase 4.5-SCV1 generated a read-only coverage/search/gap audit.
- Phase 4.5-SCV2-P0 generated a read-only current-DB inventory and governed split for controlled medium expansion on branch `codex/phase45-scv2-p0-controlled-medium-expansion-policy`.
- PR #102 / Phase 4.5-SCV2-E1 is merged. It expanded the library to 3750 media and completed eligible AI tag coverage without Pixiv/provider/SourceConcept resolver work.
- PR #103 / Phase 4.5-PX1 is merged. It ran the bounded Pixiv/gallery-dl metadata extraction batch: 500 selected, 470 metadata successes, 30 unavailable/private/deleted failures, zero exact duplicate dry-run groups, and source-layer-only metadata/assertion writes.
- PR #104 / Phase 4.5-SCV2-R1 is merged. It consumed PX1 evidence through SourceConcept triage, committed execute transactions, verified post-commit counts on a fresh connection, wrote only allowed SourceConcept tables, and regenerated the R1 public report/summary from current branch code.
- Phase 4.5-SCV2-A1 is the current branch. It adds a read-only post-expansion audit runner, a public A1 report/summary, and durable ChatGPT review pack policy for independent route-decision audit.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.
- Historical details before SC1/SC2/SCV1/SCV2-P0/E1/PX1/R1 live in `docs/project-roadmap.md` and `docs/reports/`.

## Current Route

`SCV2-A1` - post-expansion SourceConcept audit and route decision after R1.

R1 target was met: PX1 source-layer evidence was consumed by SourceConcept resolver/triage, mutation proof and public redaction passed, and no truth-path/source-metadata writes occurred. A1 route recommendations are provisional until the generated ChatGPT review pack is uploaded and independently audited.

## Current Known Observations / Validation Seeds

- SCV1 tested Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus the prompt mojibake seed variants.
- Nahida seed matched 10 visible concept IDs and 33 media, so the immediate issue is not a single missing alias; it is broader alias fragmentation and `needs_review` noise.
- PX1 post-E1 baseline recorded 3750 total media, 3687 eligible media, eligible AI tag coverage 3687/3687, 2287 Pixiv-like candidates, 500 selected metadata requests, 470 metadata successes, and zero exact duplicate dry-run groups.
- PX1 source assertions are intentionally `needs_review` with `requires_review=true`; R1 may consume them as review-scoped SourceConcept input but must not promote them into active search truth or Entity/media_tags truth.
- R1's trusted transition moved SourceConcept counts from 4214 to 6094 total, 355 to 1078 active, and 760 to 1809 `needs_review`; 1692 concepts are now influenced by PX1 evidence. The final current-head execute rerun was idempotent over the already committed R1 state and verified post-commit counts at 6094 total / 1078 active / 1809 `needs_review`.
- R1 alias gap deltas improved source assertion/source name/source tag/identity-tag gaps but increased total gap signals by 626 because PX1 added much more review-scoped evidence and fragmentation to triage.
- R1 search seed symmetry checked 10 groups / 67 seeds / 49 matched seeds; all 10 groups remain asymmetric and should be reviewed in A1 before any truth bridge.
- A1 review-pack-required route decisions use status `provisional_pending_chatgpt_pack_audit` until independent pack review completes.

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
- A1 report: `docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision.md`.
- ChatGPT review pack policy: `docs/chatgpt-review-pack-policy.md`.
