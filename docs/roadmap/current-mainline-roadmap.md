# Current Mainline Roadmap

## Accepted Mainline

`origin/main` is owner-accepted at
`a2f48bdba979f579b7cd1cdd9ef541137b2479c5`, the merge-commit result of PR
#143. Merge-commit topology preserves final PR HEAD
`228983f510c975399b53b39dcd7dd170e59b3245` and implementation evidence
`a631160f58e8d5d61998863b5b4d60a549e88151` as ancestors.

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
10. SCV2-FL1-P1 physically merged in PR #141.
11. SCV2-FL1-P1-R1 was owner-accepted and merge-commit merged in PR #143 at
   `a2f48bdba979f579b7cd1cdd9ef541137b2479c5`.

PR #142 remains closed, unmerged, Draft, and non-authoritative. Its code and
tests are read-only archaeology inputs only; no governance state, commit, or
wholesale patch is inherited.

## Current Phase And Stop Boundary

<!-- CURRENT_PHASE: SCV2-FL1-I1 -->

`SCV2-FL1-I1: Read-only Inventory` is active on branch
`codex/scv2-fl1-i1-read-only-inventory-v2`. Its fact source is
`docs/state/current-phase.json` and its planning input is
`docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`.

Draft PR #144 now carries the frozen synthetic implementation and remains at
the owner-audit stop. Current fail-closed state:

- `status=fl1_i1_synthetic_implementation_ready_for_owner_audit`
- `target_met=false`
- `safe_to_merge=false`
- `route_approved=false`
- `manual_acceptance_status=pending_i1_synthetic_implementation_owner_audit`
- `next_phase_started=true`
- `real_inventory_started=false`
- implementation evidence: `5194a484d0d8fb8dd5e0697cd61054f596aee5ec`
  with tree `9b30ba024beb6fcd58709e707d7879887ad7c081`
- blocker:
  `pending_i1_synthetic_implementation_owner_audit_and_real_source_scope`

The current route authorizes reusable inventory safety tooling and validation
only against synthetic or newly created temporary fixtures. It authorizes the
two explicit code modes `synthetic_fixture` and `authorized_read_only_source`,
but the latter may be exercised only against temporary roots under a trusted
test registry. No real source/iCloud root may be listed, stated, observed,
opened, read, or hashed.

The route must deliver trusted runtime/repository identity, a complete
protected-root role registry, layered write-ahead source-operation evidence,
private atomic manifests and ledgers, denominator and exact-duplicate
accounting, Cloud Files recall-risk deferral, bounded race-safe reading,
cross-process restart provenance, a canonical protected public projection, and
`scv2_fl1_i1_read_only_inventory_contract_v1`.

Database/app-storage access, import, classification, AI tagging,
provider/LLM/media/network activity, Stable Replay, graph/search, Entity/truth,
UI/runtime servers, production, and later FL1 phases remain forbidden. The
owner-audit checkpoint cannot claim `target_met`, `safe_to_merge`, or
`route_approved`.

## PR #143 Final Owner Adjudication Carry-Forward

Final review `4890771735` at exact HEAD `228983f…` produced five durable
owner-adjudicated boundaries:

1. complete protected roots must come from trusted private runtime/repository
   context before a real operation;
2. PR #143 used merge-commit topology; a topology-only post-squash check is not
   trusted evidence;
3. restart claims require distinct process/invocation provenance;
4. actual Git HEAD must be derived from the trusted repository and bind all
   run artifacts;
5. local validation may produce only a `local_operator_receipt` with
   `machine_verifiable_ci=false`.

`REAL_OPERATION_GATEWAY_GATE` and `VALIDATION_RECEIPT_GATE` carry these forward.
`OWNER_AUTHORITY_GATE`, `POSIX_LEDGER_DURABILITY_GATE`, and
`STABLE_REPLAY_GATE` remain in force.

## Remote Sync Preflight Policy

Fetch the verified remote before comparing protected base branches. A safe
local base that has no local-only commit and is only behind its remote is
fast-forwarded with `--ff-only`, classified as a preflight self-heal, and the
same task continues. Local/remote inequality alone is not a blocker.

Fail closed for divergence, local-only commits without safe preservation,
tracked drift, behavior-affecting untracked executable/module/config/symlink
drift, failed fast-forward-only, unverified remote identity/authentication, or
any sync requiring reset, rebase, force, overwrite, or deletion. Unrelated
untracked and ignored non-executable user artifacts are preserved and do not
automatically block work.

The entry sync for this phase was
`preflight_remote_sync=self_healed_by_fast_forward`; this is operator
classification only, not self-filled contract authority.

## Delivery Sequence

1. The I1 owner decision and implementation route are persisted.
2. Scanner/gateway/manifest/ledger/contract implementation and temporary-root
   validation are complete at the frozen evidence boundary.
3. Draft PR #144 contains only the owner-audit governance projection after
   implementation evidence `5194a484d0d8fb8dd5e0697cd61054f596aee5ec`.
4. Request one final-head Codex review and stop at owner audit.
6. Any future real inventory requires a separate exact source-scope decision;
   FL1-E1 does not start from this route.

## FL1 Planning Constraints

- Membership is manifest-bound; content fingerprints establish exact
  duplicates; filenames, row order, and DB IDs do not establish identity.
- Public output defaults to aggregates and never contains paths, filenames,
  content fingerprints, keys, or per-item private records.
- Every discovered item has exactly one terminal inventory disposition; an
  owner-audit-ready manifest has `unresolved=0`, `imported=0`,
  `import_failed=0`, and `import_deferred=eligible_candidate`.
- Structural identity, containment, race, ledger, resume-parent, or policy drift
  blocks the run. Bounded per-item failures remain private ledger evidence.
- Browser validation is N/A because I1 changes no UI or runtime server.

## Durable Links

- `docs/state/current-phase.json`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/phase-contracts.md`
- `docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`
- `docs/roadmap/archive/project-roadmap-through-scv2-sv1b.md`
- `docs/development/agent-runbook.md`
- `docs/test-workflow.md`
