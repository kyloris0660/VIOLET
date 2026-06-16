# Current Handoff - V.I.O.L.E.T.

> Last updated for Phase 4.7-S1 startup on `2026-06-16`.
> Active PR branch: `codex/phase47-s1-dynamic-sync-product-foundation`.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Current PR | Phase 4.7-S1 dynamic sync foundation / product UI / AI-localization readiness |
| Baseline main | PR #111 / FULLLIB-E1a runner dry-run merge `577966b` or later |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript |
| Python | `.\venv\Scripts\python.exe` |

## Current Accepted State

- PR #96, PR #97, PR #98, PR #99, and PR #100 are merged: SC1 delivered the SourceConcept resolver core, SC2-P0 planned SC2, SC2 added search/evidence UI, DOC1 restructured docs, and Phase 4.5-SCV1 generated a read-only coverage/search/gap audit.
- Phase 4.5-SCV2-P0 generated a read-only current-DB inventory and governed split for controlled medium expansion on branch `codex/phase45-scv2-p0-controlled-medium-expansion-policy`.
- PR #102 / Phase 4.5-SCV2-E1 is merged. It expanded the library to 3750 media and completed eligible AI tag coverage without Pixiv/provider/SourceConcept resolver work.
- PR #103 / Phase 4.5-PX1 is merged. It ran the bounded Pixiv/gallery-dl metadata extraction batch: 500 selected, 470 metadata successes, 30 unavailable/private/deleted failures, zero exact duplicate dry-run groups, and source-layer-only metadata/assertion writes.
- PR #104 / Phase 4.5-SCV2-R1 is merged. It consumed PX1 evidence through SourceConcept triage, committed execute transactions, verified post-commit counts on a fresh connection, wrote only allowed SourceConcept tables, and regenerated the R1 public report/summary from current branch code.
- PR #105 / Phase 4.5-SCV2-A1 is merged. It added a read-only post-expansion audit runner, a public A1 report/summary, and durable ChatGPT review pack policy for independent route-decision audit.
- PR #107 transplanted the final INC1 report/summary/runner/tests onto `main` after PR #106 was merged into the stacked A1 branch instead of `main`. INC1 is now available from `main`.
- PR #108 / Phase 4.5-GOV3 is merged. Executable phase contracts and the reusable contract checker are now the baseline governance rule.
- Issue #109 tracks GOV3.1 hardening debt. It does not block plan-only FULLLIB-P0, but later route approval, `safe_to_merge`, or high-risk review-pack proof must account for the relevant issue class.
- PR #110 / Phase 4.6-FULLLIB-P0 is merged. It mapped safe full-library production import, classification, AI tagging, and AI tag reuse for the production utility track.
- PR #111 / Phase 4.6-FULLLIB-E1a is merged. It added and dry-run validated the production full-library runner without DB writes, source/app-storage mutation, provider calls, LLM calls, SourceConcept, Entity, classification execution, or AI tagging execution.
- Phase 4.7-S1 is the current product feature stage. It turns full-library import planning into durable dynamic library synchronization state, Admin UI, and S2 readiness for baseline import + classification + AI tagging + tag localization.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.
## Current Route

`Phase 4.7-S1` - Dynamic Sync Foundation + Product UI + AI/Localization Readiness.

The short-term product route is capped at three stages:

1. Phase 4.7-S1: Dynamic Sync Foundation + Product UI + AI/Localization Readiness.
2. Phase 4.7-S2: Baseline Full Import + Classification + AI Tagging + Tag Localization.
3. Phase 4.7-S3: Incremental Sync Automation + Hardening.

SourceConcept/provider/entity work remains separate. R1 target was met for deterministic/source-layer triage evidence, but INC1 identified a pipeline fidelity incident because R1 did not prove the full SC1 resolver chain with bounded LLM pair adjudication. A1 route approval remains blocked pending R1R full-chain remediation and A1R rerun; old R1/A1 evidence must not approve R2. This does not block the product utility dynamic sync/import/AI/localization route as long as that route does not promote SourceConcept/Entity truth.

## Current Known Observations / Validation Seeds

- SCV1 tested Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus the prompt mojibake seed variants.
- PX1 post-E1 baseline recorded 3750 total media, 3687 eligible media, eligible AI tag coverage 3687/3687, 2287 Pixiv-like candidates, 500 selected metadata requests, 470 metadata successes, and zero exact duplicate dry-run groups.
- PX1 source assertions are intentionally `needs_review` with `requires_review=true`; R1 may consume them as review-scoped SourceConcept input but must not promote them into active search truth or Entity/media_tags truth.
- A1 route approval now uses status `blocked_pending_pipeline_fidelity_remediation`: no R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion may start until R1R full-chain remediation and A1R rerun complete.

## Hard Non-Goals Without Explicit Approval

- No push to `main`; agents do not merge PRs.
- No full production import, production DB data import, cleanup/drop/truncate, destructive operation, source/iCloud mutation, SourceConcept table mutation, or app-managed storage mutation without explicit approval.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment run unless a later phase explicitly approves it.
- No full LLM batch, full AI tagging/classification run, provider call, SourceConcept resolver, Entity bridge, R1R, A1R, R2, confirmed assignment creation, or full-library production execution during S1.
- Dynamic sync automatic production writes must remain disabled by default. Manual update checks and pending counts are allowed; manual sync execution requires explicit approved S2 policy/config.
- No source/iCloud/staging/app-managed storage mutation.
- No Entity Resolver, similarity/clustering, SourceConcept editing, Entity bridge, promotion, confirmed assignment, trusted Entity creation, or `media_tags` mutation.

## Active Governance Reminders

- GOV-2 is active: prefer executable guards, focused tests, DB constraints, validation runners, and runtime assertions over repeated long policy text.
- GOV3 is the durable rule that phase completion claims require executable phase contracts. Contract checks must pass before `target_met`, `route_approved`, `full_chain_completed`, or `safe_to_merge` can be claimed.
- Phase 4.7-S2 should use at least `python_env_contract_v1`, `postgres_db_contract_v1`, `media_import_contract_v1`, `classification_contract_v1`, `ai_tagging_contract_v1`, `mutation_safety_contract_v1`, `artifact_lifecycle_contract_v1`, and `public_redaction_contract_v1`.
- AI tagging and tag localization are one S2 chain: baseline import -> AI tagging job -> new tags collected -> `_schedule_localization` -> background worker / auto translate -> `blombooru_tag_translations` -> frontend Chinese display and trusted search aliases.
- Proper-noun localization safety remains strict: background translation is for general/meta by default; character/copyright/artist aliases require manual/static trusted aliases or Entity Alias Resolver review and must not pollute Chinese search from unreviewed LLM output.
- Durable core remains strict: DB/migrations, provider-neutral evidence contracts, Entity/evidence/candidate/assignment lifecycle, provider privacy/budget gates, source/iCloud safety, and in-scope E2E pass requirements.
- Docs-only work should use docs/JSON/static checks and should not start servers or browser validation unless code/runtime/UI changes are made.
- Reviewer feedback is a handoff point. Do not auto-fix reviewer comments after the requested stop line without explicit bounded-fix authorization.

## Validation Starting Points

- Python identity: `& "$PY" scripts/check_python_env.py --expected-python "$PY"`.
- Dynamic library sync product doc: `docs/dynamic-library-sync.md`.
- Phase 4.7-S1 report: `docs/reports/phase-4.7-s1-dynamic-sync-product-foundation.md`.
- FULLLIB-P0 report: `docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan.md`.
- GOV3 contract checker: `& "$PY" scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json>`.
- Scope-based test selection: `docs/test-workflow.md`.
- Source/iCloud safety: `docs/icloud-safe-ingestion.md`.
- Server lifecycle preflight for any agent-started server: `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree`.
