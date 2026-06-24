# Current Handoff - V.I.O.L.E.T.

> Last updated for S2G-M1 on `2026-06-24`.
> Active PR branch: `codex/s2g-m1-ai-manual-sync-foundation`.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `[canonical local checkout path]` |
| Current phase | `S2G-M1: AI Tagging Execution and Manual Sync Foundation` |
| Baseline main | PR #123 merge `4724530d83767a62b6525a58bb1a1d04e973d48e` |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript + Electron launcher |
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
- PR #122 / `PROD-LAUNCHER-UX1/PF1` is merged. Merge commit: `aece424df2814ef0d840f9fe472a9d19478d2020`. It added the Electron production launcher, local production profile/runtime config, root-level Windows entrypoint, and production/development runtime separation.
- PR #123 / `PD1-A-R1` is merged. Merge commit: `4724530d83767a62b6525a58bb1a1d04e973d48e`. It reconciled the post-#122 route and made `production_development_separation_contract_v1` the current executable separation gate.
- S2G-M1 is the active feature branch. It adds the AI tagging execution profile, local capability probe/report, provider fallback/load-control/provenance foundation, manual sync dry-run planner, manual sync status/plan API surface, and `s2g_manual_sync_foundation_contract_v1`; it does not run production writes.
- The V.I.O.L.E.T. root-level Windows launcher is now the accepted personal production entrypoint for current daily library operation.
- Current root-level daily entry: root-level `V.I.O.L.E.T. Production Launcher.exe` in the canonical local checkout.
- The launcher uses local ignored production profile/runtime config. Development `.env` must not be converted into production and must not be treated as the production source of truth.
- Production/development split is the current temporary operational policy. Future production execution phases must bind to explicit production profile/runtime config instead of development `.env`.
- Launcher deferred reviewer issues are non-blocking for the current Windows personal launcher path. Track them as future runtime/schema hardening debt, not active blockers for this reconciliation phase.
- The durable route lives in `docs/roadmap/current-mainline-roadmap.md`.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.

## Current Route

`S2G-M1` - AI Tagging Execution and Manual Sync Foundation.

Accepted near-term order:

1. `S2G-M1`: implement AI tagging execution profile, bounded local capability probe, provider fallback, load-control/provenance policy, manual sync dry-run planner, sync job/ledger foundation, controlled dry-run pipeline foundation, and a public-safe report/contract. No production writes.
2. `S3A-M1`: final manual-sync button placement and small production acceptance, with explicit operator approval and production profile/runtime identity gates.
3. `R1R`: SourceConcept route redo under GOV3 contracts; no confirmed Entity assignments and no new provider/Pixiv live calls unless separately approved.
4. `A1R`: rerun route audit after R1R outputs exist.
5. `Pixiv/source metadata strategy polish`: settle Pixiv/source metadata reliability before adding providers.
6. `S3B`: opt-in scheduled/unattended incremental sync, disabled by default until approved.
7. `S2F0`: low-priority desired-media gap audit/support decision report only.

S2G-M1 intentionally does not split the AI benchmark into a separate narrow PR. The benchmark is one input to the broader manual-sync foundation. It still stops before automatic sync, scheduled sync, production execution, final visible production manual-sync acceptance, R1R, A1R, provider expansion, Entity bridge, SourceConcept truth promotion, or any production `media_tags` truth mutation.

SourceConcept/provider/entity work remains separate. R1 target was met for deterministic/source-layer triage evidence, but INC1 identified a pipeline fidelity incident because R1 did not prove the full SC1 resolver chain with bounded LLM pair adjudication. A1 route approval remains blocked pending R1R full-chain remediation and A1R rerun; old R1/A1 evidence must not approve R2. This does not block the product utility route as long as that route does not promote SourceConcept/Entity truth.

## Current Known Observations / Validation Seeds

- SCV1 tested Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus the prompt mojibake seed variants.
- PX1 post-E1 baseline recorded 3750 total media, 3687 eligible media, eligible AI tag coverage 3687/3687, 2287 Pixiv-like candidates, 500 selected metadata requests, 470 metadata successes, and zero exact duplicate dry-run groups.
- PX1 source assertions are intentionally `needs_review` with `requires_review=true`; R1 may consume them as review-scoped SourceConcept input but must not promote them into active search truth or Entity/media_tags truth.
- A1 route approval now uses status `blocked_pending_pipeline_fidelity_remediation`: no R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion may start until R1R full-chain remediation and A1R rerun complete.
- PR #122 production launcher reports establish the temporary Windows personal launcher path and document deferred runtime/schema hardening debt.
- S2G-M1 local capability evidence records DirectML as the recommended provider on the current development machine, CPU fallback completed, AI batch size `2`, concurrency `1`, and public redaction passed. This is development evidence only; it is not production acceptance.

## Hard Non-Goals Without Explicit Approval

- No push to `main`; agents do not merge PRs.
- No production import, production DB data import, production classification, production AI tagging, production localization, cleanup/drop/truncate, destructive operation, source/iCloud mutation, SourceConcept table mutation, Entity truth write, confirmed assignment write, `media_tags` mutation, or app-managed storage mutation without explicit approval.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment run unless a later phase explicitly approves it.
- No full LLM batch, SourceConcept resolver, Entity bridge, R1R, A1R, R2, confirmed assignment creation, S3A/S3B automatic production sync, desired-media backfill, or full-library production execution during S2G-M1.
- Dynamic sync automatic production writes must remain disabled by default. Manual update checks and pending counts are allowed; manual sync execution requires explicit approved policy/config.
- No source/iCloud/staging/app-managed storage mutation.
- No Entity Resolver, similarity/clustering, SourceConcept editing, Entity bridge, promotion, confirmed assignment, trusted Entity creation, or `media_tags` mutation.

## Active Governance Reminders

- GOV-2 is active: prefer executable guards, focused tests, DB constraints, validation runners, and runtime assertions over repeated long policy text.
- GOV3 is the durable rule that phase completion claims require executable phase contracts. Contract checks must pass before `target_met`, `route_approved`, `full_chain_completed`, or `safe_to_merge` can be claimed.
- PD1-A-R1 uses `production_development_separation_contract_v1` to keep post-#122 production/development separation executable.
- S2G-M1 uses `s2g_manual_sync_foundation_contract_v1` plus `public_redaction_contract_v1` before claiming the AI/manual-sync foundation is target-met.
- Future production execution phases should use explicit production profile/runtime config plus the relevant execution contracts, including `python_env_contract_v1`, `postgres_db_contract_v1`, `media_import_contract_v1`, `classification_contract_v1`, `ai_tagging_contract_v1`, `mutation_safety_contract_v1`, `artifact_lifecycle_contract_v1`, and `public_redaction_contract_v1`.
- AI tagging and tag localization are one S2 chain: baseline import -> AI tagging job -> new tags collected -> `_schedule_localization` -> background worker / auto translate -> `blombooru_tag_translations` -> frontend Chinese display and trusted search aliases.
- Proper-noun localization safety remains strict: background translation is for general/meta by default; character/copyright/artist aliases require manual/static trusted aliases or Entity Alias Resolver review and must not pollute Chinese search from unreviewed LLM output.
- Durable core remains strict: DB/migrations, provider-neutral evidence contracts, Entity/evidence/candidate/assignment lifecycle, provider privacy/budget gates, source/iCloud safety, and in-scope E2E pass requirements.
- Docs-only work should use docs/JSON/static checks and should not start servers or browser validation unless code/runtime/UI changes are made.
- Reviewer feedback is a handoff point. Do not auto-fix reviewer comments after the requested stop line without explicit bounded-fix authorization.

## Validation Starting Points

- Python identity: `& "$PY" scripts/check_python_env.py --expected-python "$PY"`.
- Production launcher reference: `docs/production-launcher.md`.
- Current mainline roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- Post-S2 production roadmap: `docs/roadmap/post-s2-production-roadmap.md`.
- Phase 4.7-S2 public summary: `docs/reports/phase-4.7-s2-baseline-full-import-ai-localization-summary.json`.
- PROD-LAUNCHER-UX1/PF1 public summary: `docs/reports/prod-launcher-ux1-production-profile-summary.json`.
- S2G-M1 public summary: `docs/reports/s2g-m1-ai-manual-sync-foundation-summary.json`.
- GOV3 contract checker: `& "$PY" scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json>`.
- Scope-based test selection: `docs/test-workflow.md`.
- Source/iCloud safety: `docs/icloud-safe-ingestion.md`.
- Server lifecycle preflight for any agent-started server: `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree`.
