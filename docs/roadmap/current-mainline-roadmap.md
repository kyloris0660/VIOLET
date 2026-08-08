# Current Mainline Roadmap

## Accepted Mainline

`origin/main` contains the owner-audited FL1-P1 foundation through squash merge
`36100bfa0317387e064cd87b2e753eca3a201b5e` from PR #141.

1. R1R merged in PR #132.
2. SCV2-A1R merged in PR #133.
3. SCV2-R2 merged in PR #134.
4. SCV2-R2R merged in PR #135.
5. SCV2-ML1 merged in PR #136.
6. SCV2-ML2 merged in PR #137.
7. SCV2-SV1-A merged in PR #138.
8. SCV2-SV1B merged in PR #139.
9. SCV2-FL1 planning merged in PR #140 at
   `9ce1128be643c0eaa998ccdff8890d76196ce7db`.
10. SCV2-FL1-P1 merged in PR #141.

SV1B completed controlled Pixiv metadata acquisition, localization closure,
stable Replay v2, source-graph/search validation, and owner acceptance. Its
final composite is `37 PASS`, `3 owner-waived nonblocking known limitations`,
`0 PENDING`, and `0 unwaived FAIL`. The B01/B04/B08 waiver is phase-scoped and
does not become a creator-identity or scale-up policy.

## Current Phase And Stop Boundary

<!-- CURRENT_PHASE: SCV2-FL1-I1 -->

`SCV2-FL1-I1: Read-Only Inventory Planning And Synthetic Foundation` is active
on Draft PR #142. Its fact source is `docs/state/current-phase.json` and its plan is
`docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`.

Current owner-audit stop state:

- `status=fl1_i1_synthetic_implementation_ready_for_owner_audit`
- `target_met=false`
- `safe_to_merge=false`
- `route_approved=false`
- `route_scope=FL1-I1 planning and synthetic read-only inventory implementation only`
- `manual_acceptance_status=pending_owner_audit`
- `next_phase_started=true`
- blocker: `pending_owner_audit_and_real_source_scope_authorization`

I1 has implemented a synthetic-only, bounded read-only scanner with immutable
pre/post source fingerprints, symlink/race rejection, stable private membership,
content duplicate accounting, terminal dispositions, complete denominator
equations, and aggregate-only public evidence. It does not authorize real
source-root access or inventory, any database/app-storage write, import,
classification, AI tagging, production, external calls, Stable Replay,
localization, graph/search, Entity/truth, or provider-derived `media_tags` activity.

## Proposed Delivery Sequence

1. Owner-audit Draft PR #142 and any bounded synthetic-only corrections.
2. Keep `safe_to_merge=false` and do not transition the PR to Ready without a
   later decision.
3. A real read-only inventory dry run requires separate exact source-scope
   authorization and ends at an owner checkpoint.
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
