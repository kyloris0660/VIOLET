# Current Handoff - V.I.O.L.E.T.

> Last updated for Phase 4.6-FULLLIB-P0 startup on `2026-06-15`.
> Active PR branch: `codex/phase46-fulllib-p0-production-import-ai-tagging-plan`.
> Read this file first for active state, then use `docs/project-roadmap.md` for phase history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Current PR | Phase 4.6-FULLLIB-P0 production import / AI tagging plan |
| Baseline main | PR #108 / GOV3 merge `6615053` or later |
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
- Phase 4.6-FULLLIB-P0 is the current plan-only branch. It maps safe full-library production import, classification, AI tagging, and AI tag reuse for the production utility track.
- SourceConcept remains source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not a confirmed assignment, and not `media_tags` truth.
- Historical details before SC1/SC2/SCV1/SCV2-P0/E1/PX1/R1 live in `docs/project-roadmap.md` and `docs/reports/`.

## Current Route

`FULLLIB-P0` - production utility full-library import / AI tagging plan and contract mapping.

The roadmap is split into two tracks. Production utility may plan full-library import, classification, AI tagging, and local search/browse value under GOV3 contracts. SourceConcept/provider/entity work remains separate: R1 target was met for deterministic/source-layer triage evidence, but INC1 identified a pipeline fidelity incident because R1 did not prove the full SC1 resolver chain with bounded LLM pair adjudication. A1 route approval remains blocked pending R1R full-chain remediation and A1R rerun; old R1/A1 evidence must not approve R2.

## Current Known Observations / Validation Seeds

- SCV1 tested Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)` plus the prompt mojibake seed variants.
- Nahida seed matched 10 visible concept IDs and 33 media, so the immediate issue is not a single missing alias; it is broader alias fragmentation and `needs_review` noise.
- PX1 post-E1 baseline recorded 3750 total media, 3687 eligible media, eligible AI tag coverage 3687/3687, 2287 Pixiv-like candidates, 500 selected metadata requests, 470 metadata successes, and zero exact duplicate dry-run groups.
- PX1 source assertions are intentionally `needs_review` with `requires_review=true`; R1 may consume them as review-scoped SourceConcept input but must not promote them into active search truth or Entity/media_tags truth.
- R1's trusted transition moved SourceConcept counts from 4214 to 6094 total, 355 to 1078 active, and 760 to 1809 `needs_review`; 1692 concepts are now influenced by PX1 evidence. The final current-head execute rerun was idempotent over the already committed R1 state and verified post-commit counts at 6094 total / 1078 active / 1809 `needs_review`.
- R1 alias gap deltas improved source assertion/source name/source tag/identity-tag gaps but increased total gap signals by 626 because PX1 added much more review-scoped evidence and fragmentation to triage.
- R1 search seed symmetry checked 10 groups / 67 seeds / 49 matched seeds; all 10 groups remain asymmetric and should be reviewed in A1 before any truth bridge.
- A1 route approval now uses status `blocked_pending_pipeline_fidelity_remediation`: no R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion may start until R1R full-chain remediation and A1R rerun complete.

## Hard Non-Goals Without Explicit Approval

- No push to `main`; agents do not merge PRs.
- No further DB migration, DB import, DB write, cleanup, drop, truncate, or destructive operation without explicit approval. R1's completed execute writes were limited to the allowed SourceConcept resolver tables.
- No provider/gallery-dl/Pixiv/SauceNAO/Google/source enrichment run unless a later phase explicitly approves it.
- No LLM, AI tagging execution, classification execution, localization, background jobs, or full-library import/validation during FULLLIB-P0.
- No source/iCloud/staging/app-managed storage mutation.
- No Entity Resolver, similarity/clustering, SourceConcept editing, Entity bridge, promotion, confirmed assignment, trusted Entity creation, or `media_tags` mutation.

## Active Governance Reminders

- GOV-2 is active: prefer executable guards, focused tests, DB constraints, validation runners, and runtime assertions over repeated long policy text.
- GOV3 is the durable rule that phase completion claims require executable phase contracts. Contract checks must pass before `target_met`, `route_approved`, `full_chain_completed`, or `safe_to_merge` can be claimed.
- FULLLIB-E1 should use at least `python_env_contract_v1`, `postgres_db_contract_v1`, `media_import_contract_v1`, `classification_contract_v1`, `ai_tagging_contract_v1`, `mutation_safety_contract_v1`, `artifact_lifecycle_contract_v1`, and `public_redaction_contract_v1`.
- Durable core remains strict: DB/migrations, provider-neutral evidence contracts, Entity/evidence/candidate/assignment lifecycle, provider privacy/budget gates, source/iCloud safety, and in-scope E2E pass requirements.
- Docs-only work should use docs/JSON/static checks and should not start servers or browser validation unless code/runtime/UI changes are made.
- Reviewer feedback is a handoff point. Do not auto-fix reviewer comments after the requested stop line without explicit bounded-fix authorization.

## Validation Starting Points

- Python identity: `& "$PY" scripts/check_python_env.py --expected-python "$PY"`.
- GOV3 contract checker: `& "$PY" scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json>`.
- FULLLIB-P0 report: `docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan.md`.
- SCV1 runner: `& "$PY" scripts/run_phase45_scv1_source_concept_coverage_audit.py --output-dir ".local_manifests\phase-4.5-scv1-source-concept-coverage-audit" --write-public-report --read-only`.
- SCV2-R1 runner: `& "$PY" scripts/run_phase45_scv2_r1_post_px1_source_concept_triage.py --dry-run --output-dir ".local_manifests\phase-4.5-scv2-r1-post-px1-source-concept-triage" --write-public-report`, then execute only with `--confirm-execution EXECUTE_PHASE45_SCV2_R1_SOURCE_CONCEPT_TRIAGE`.
- Scope-based test selection: `docs/test-workflow.md`.
- Manual development validation: `docs/manual-validation.md`.
- Source/iCloud safety: `docs/icloud-safe-ingestion.md`.
- Server lifecycle preflight for any agent-started server: `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree`.

## Links

Use `docs/project-roadmap.md` for phase history and standards. Current report links:
SC1, SC2, SCV1, SCV2-P0, E1, PX1, R1, A1, INC1, and GOV3 live under
`docs/reports/`; review-pack policy is `docs/chatgpt-review-pack-policy.md`.
