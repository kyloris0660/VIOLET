# Current Handoff - V.I.O.L.E.T.

> Last updated for PD1-A startup on `2026-06-20`.
> Active PR branch: `codex/pd1a-post-s2-mainline-gates-roadmap`.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Current PR | PD1-A post-S2 roadmap persistence and production/development executable gate foundation |
| Baseline main | PR #113 / Phase 4.7-S2 merge `17ec3326bc00e4d025bbe4297fad1157b9cda2ff` |
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
- PR #113 / Phase 4.7-S2 is merged. It established the real production baseline library through controlled baseline import, classification, AI tagging, tag localization, public redaction, and real browser validation.
- Post-S2 work must keep production and development lanes separate. Feature branches must use dev/test DB, dev/test storage, fixtures, or restored snapshots instead of production DB/storage/source roots/private ledgers as casual fixtures.
- The durable post-S2 route lives in `docs/roadmap/current-mainline-roadmap.md`.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.
## Current Route

`PD1-A` - Post-S2 Mainline Roadmap Persistence and Production/Development Executable Gate Foundation.

Accepted post-S2 order:

1. `PD1-A`: persist roadmap and add production/development executable gate foundation. No production writes.
2. `S2G-1 GPU AI tagging capability probe and benchmark`: recommended immediate next phase after PD1-A.
3. `S2G-2/3`: GPU/provider abstraction, provenance, batch/concurrency/throttle controls, CPU fallback.
4. `R1R`: SourceConcept route redo under GOV3 contracts; no confirmed Entity assignments and no new provider/Pixiv live calls unless separately approved.
5. `Pixiv/source metadata strategy polish`: settle Pixiv/source metadata reliability before adding providers.
6. `S3A`: controlled incremental sync pipeline after S2G and the R1R/Pixiv route decision unless explicitly reprioritized.
7. `S3B`: opt-in scheduled/unattended incremental sync, disabled by default until approved.
8. `S2F0`: low-priority desired-media gap audit/support decision report only.

SourceConcept/provider/entity work remains separate. R1 target was met for deterministic/source-layer triage evidence, but INC1 identified a pipeline fidelity incident because R1 did not prove the full SC1 resolver chain with bounded LLM pair adjudication. A1 route approval remains blocked pending R1R full-chain remediation and A1R rerun; old R1/A1 evidence must not approve R2. This does not block the product utility dynamic sync/import/AI/localization route as long as that route does not promote SourceConcept/Entity truth.

## Current Known Observations / Validation Seeds

- SCV1 tested Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus the prompt mojibake seed variants.
- PX1 post-E1 baseline recorded 3750 total media, 3687 eligible media, eligible AI tag coverage 3687/3687, 2287 Pixiv-like candidates, 500 selected metadata requests, 470 metadata successes, and zero exact duplicate dry-run groups.
- PX1 source assertions are intentionally `needs_review` with `requires_review=true`; R1 may consume them as review-scoped SourceConcept input but must not promote them into active search truth or Entity/media_tags truth.
- A1 route approval now uses status `blocked_pending_pipeline_fidelity_remediation`: no R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion may start until R1R full-chain remediation and A1R rerun complete.

## Hard Non-Goals Without Explicit Approval

- No push to `main`; agents do not merge PRs.
- No full production import, production DB data import, production classification, production AI tagging, production localization, cleanup/drop/truncate, destructive operation, source/iCloud mutation, SourceConcept table mutation, or app-managed storage mutation without explicit approval.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment run unless a later phase explicitly approves it.
- No full LLM batch, GPU benchmark, provider call, SourceConcept resolver, Entity bridge, R1R, A1R, R2, confirmed assignment creation, S3, automatic production sync, desired-media backfill, or full-library production execution during PD1-A.
- Dynamic sync automatic production writes must remain disabled by default. Manual update checks and pending counts are allowed; manual sync execution requires explicit approved S2 policy/config.
- No source/iCloud/staging/app-managed storage mutation.
- No Entity Resolver, similarity/clustering, SourceConcept editing, Entity bridge, promotion, confirmed assignment, trusted Entity creation, or `media_tags` mutation.

## Active Governance Reminders

- GOV-2 is active: prefer executable guards, focused tests, DB constraints, validation runners, and runtime assertions over repeated long policy text.
- GOV3 is the durable rule that phase completion claims require executable phase contracts. Contract checks must pass before `target_met`, `route_approved`, `full_chain_completed`, or `safe_to_merge` can be claimed.
- PD1-A should use `production_development_separation_contract_v1` to keep post-S2 production/development separation executable.
- Future production execution phases should still use the relevant execution contracts, including `python_env_contract_v1`, `postgres_db_contract_v1`, `media_import_contract_v1`, `classification_contract_v1`, `ai_tagging_contract_v1`, `mutation_safety_contract_v1`, `artifact_lifecycle_contract_v1`, and `public_redaction_contract_v1`.
- AI tagging and tag localization are one S2 chain: baseline import -> AI tagging job -> new tags collected -> `_schedule_localization` -> background worker / auto translate -> `blombooru_tag_translations` -> frontend Chinese display and trusted search aliases.
- Proper-noun localization safety remains strict: background translation is for general/meta by default; character/copyright/artist aliases require manual/static trusted aliases or Entity Alias Resolver review and must not pollute Chinese search from unreviewed LLM output.
- Durable core remains strict: DB/migrations, provider-neutral evidence contracts, Entity/evidence/candidate/assignment lifecycle, provider privacy/budget gates, source/iCloud safety, and in-scope E2E pass requirements.
- Docs-only work should use docs/JSON/static checks and should not start servers or browser validation unless code/runtime/UI changes are made.
- Reviewer feedback is a handoff point. Do not auto-fix reviewer comments after the requested stop line without explicit bounded-fix authorization.

## Validation Starting Points

- Python identity: `& "$PY" scripts/check_python_env.py --expected-python "$PY"`.
- Dynamic library sync product doc: `docs/dynamic-library-sync.md`.
- Current mainline roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- Post-S2 production roadmap: `docs/roadmap/post-s2-production-roadmap.md`.
- Phase 4.7-S2 public summary: `docs/reports/phase-4.7-s2-baseline-full-import-ai-localization-summary.json`.
- FULLLIB-P0 report: `docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan.md`.
- GOV3 contract checker: `& "$PY" scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json>`.
- Scope-based test selection: `docs/test-workflow.md`.
- Source/iCloud safety: `docs/icloud-safe-ingestion.md`.
- Server lifecycle preflight for any agent-started server: `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree`.
