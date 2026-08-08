# Current Mainline Roadmap

## Accepted Mainline

`origin/main` is accepted at
`36100bfa0317387e064cd87b2e753eca3a201b5e`, the physical merge of PR #141.
That merge does not erase the eight valid review findings that arrived after
merge; their remediation is the current P1-R1 route.

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
10. SCV2-FL1-P1 physically merged in PR #141; late-review remediation remains
    required.

SV1B completed controlled Pixiv metadata acquisition, localization closure,
stable Replay v2, source-graph/search validation, and owner acceptance. Its
final composite is `37 PASS`, `3 owner-waived nonblocking known limitations`,
`0 PENDING`, and `0 unwaived FAIL`. The B01/B04/B08 waiver is phase-scoped and
does not become a creator-identity or scale-up policy.

## Current Phase And Stop Boundary

<!-- CURRENT_PHASE: SCV2-FL1-P1-R1 -->

`SCV2-FL1-P1-R1: Late Review Safety Remediation And Authority Correction` is
active on Draft PR #143. Its fact source is
`docs/state/current-phase.json` and its planning input is
`docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`.

Current fail-closed state:

- `status=fl1_p1_r1_implementation_ready_for_owner_audit`
- `target_met=false`
- `safe_to_merge=false`
- `route_approved=false`
- `manual_acceptance_status=pending_owner_audit`
- `next_phase_started=false`
- blocker: `pending_owner_audit`

P1-R1 remediates only the eight late PR #141 findings and resets the false
acceptance/route state. PR #142 is a non-authoritative candidate that mixed
unapproved I1 work with P1 remediation; it is not an implementation source of
truth and must not be merged. No data execution has started. This route does
not authorize FL1-I1, production, real
source-root access or inventory, existing database access, import,
classification, AI tagging, provider, Pixiv, gallery-dl, LLM, media, thumbnail,
Stable Replay, localization, graph/search, Entity/truth, or provider-derived
`media_tags` activity.

## Proposed Delivery Sequence

1. Complete P1-R1 Draft PR #143 and stop at independent owner audit.
2. The owner separately decides acceptance and merge authorization; neither is
   implied by tests or automated review.
3. FL1-I1 remains only a future candidate and requires a separate owner scope
   decision after P1-R1 audit.
4. A real read-only inventory dry run requires separate exact source-scope
   authorization and ends at an owner checkpoint.
5. Bounded Dev/Test import and local classification/AI tagging may occur only
   after inventory evidence and an executable contract pass.
6. A representative manual-acceptance sample precedes any scale expansion.
7. Production planning is a later independent phase; it cannot inherit FL1
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
