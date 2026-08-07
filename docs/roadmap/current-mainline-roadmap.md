# Current Mainline Roadmap

## Accepted Mainline

`origin/main` contains SCV2-SV1B through squash merge
`33af4111e1595dac3ece0ac50002556d466f0138` from PR #139.

1. R1R merged in PR #132.
2. SCV2-A1R merged in PR #133.
3. SCV2-R2 merged in PR #134.
4. SCV2-R2R merged in PR #135.
5. SCV2-ML1 merged in PR #136.
6. SCV2-ML2 merged in PR #137.
7. SCV2-SV1-A merged in PR #138.
8. SCV2-SV1B merged in PR #139.

SV1B completed controlled Pixiv metadata acquisition, localization closure,
stable Replay v2, source-graph/search validation, and owner acceptance. Its
final composite is `37 PASS`, `3 owner-waived nonblocking known limitations`,
`0 PENDING`, and `0 unwaived FAIL`. The B01/B04/B08 waiver is phase-scoped and
does not become a creator-identity or scale-up policy.

## Current Phase And Stop Boundary

<!-- CURRENT_PHASE: SCV2-FL1 -->

`SCV2-FL1: Isolated Full-Library Dev/Test Planning` is now active as a
planning-only phase. Its fact source is `docs/state/current-phase.json` and its
plan is `docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`.

Current executable state:

- `status=fl1_planning_ready_owner_approval_pending`
- `target_met=false`
- `safe_to_merge=false`
- `route_approved=false`
- `manual_acceptance_status=not_applicable_planning_only`
- `next_phase_started=true` (planning only)
- gate: `pending_owner_fl1_implementation_plan_approval`

No FL1 implementation or data execution has started. Planning does not
authorize production, source-root access, database creation/write, inventory,
import, classification, AI tagging, provider, Pixiv, gallery-dl, LLM, media,
thumbnail, localization, graph/search, Entity/truth, or provider-derived
`media_tags` activity.

## Proposed Delivery Sequence

1. Owner reviews and approves or revises the FL1 plan.
2. A separate implementation PR adds the reusable safety runner, contracts,
   ledgers, and tests without executing full-library data work.
3. A read-only inventory dry run requires a separate exact authorization and
   ends at an owner checkpoint.
4. Bounded Dev/Test import and local classification/AI tagging may occur only
   after inventory evidence and an executable contract pass.
5. A representative manual-acceptance sample precedes any scale expansion.
6. Production planning is a later independent phase; it cannot inherit FL1
   Dev/Test authorization.

## FL1 Planning Constraints

- Use separately owned strict-test database identities and repo-independent
  local test storage. Never consume production DB/storage/source roots.
- Define the full-library denominator before execution: discovered, supported,
  duplicate, cloud-recall, unreadable, ineligible, imported, classified,
  locally tagged, deferred, failed, and unresolved must reconcile.
- Persist run and item state for restart, idempotency, bounded retry, and
  backfill. Per-item failures remain visible; structural identity/safety failures
  stop the run.
- Local classification and WD tagging are permitted only in a later approved
  execution stage. AI proper-noun output remains weak evidence and cannot write
  Entity truth or confirmed assignment.
- Reuse accepted SV1-A/SV1B evidence only through stable-key, fingerprinted,
  schema-compatible packages. Do not reuse database numeric IDs or phase-scoped
  waivers.
- Provider, external LLM, source discovery, media download, localization, and
  graph expansion require separate privacy/budget/contract authorization.

## DOC-GOV-02

DOC-GOV-02 is complete. Historical project roadmap content is archived, the
detailed AGENTS runbook is split from the root entrypoint, and stale R1R-as-next
language is no longer active. See `docs/governance/doc-gov-02-closeout.md`.

## Durable Links

- `docs/state/current-phase.json`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/phase-contracts.md`
- `docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`
- `docs/roadmap/archive/project-roadmap-through-scv2-sv1b.md`
