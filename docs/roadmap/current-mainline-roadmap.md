# Current Mainline Roadmap

## Accepted Mainline

<!-- CURRENT_PHASE: SCV2-PX3 -->

Trusted remote verification established this exact transition:

```text
origin/main=421e2989d274e2dc4492d5bccc10720dcfbbaa4f
origin/main_tree=507a223a9156ff2f9944524303419e85891812fa
pr148_accepted_head=bf8055af61c3a5d32155701ed7110db692047dba
pr148_accepted_tree=507a223a9156ff2f9944524303419e85891812fa
merge_parents=5a8efdaf954ab95bd82f95464af31a7fd0873e5e,bf8055af61c3a5d32155701ed7110db692047dba
accepted_head_is_merge_parent=true
accepted_tree_equals_merge_tree=true
post_merge_commit_audit_count=0
SCV2_PX2_MERGED
```

PR #148 used one expected-head protected merge-commit operation. It was not
squashed, rebased, force-pushed, or followed by a direct main push.

## Current Phase And Stop Boundary

```text
current_status=SCV2_PX3_PIXIV_PRODUCT_INTEGRATION_READY_FOR_OWNER_ACCEPTANCE_AND_CONTROLLED_CANARY
contract_id=scv2_px3_pixiv_product_integration_contract_v1
public_schema=violet.scv2-px3-pixiv-product-integration-result.v1
pr=149
implementation_evidence_head=389ab3994bb81ca772ce491dac88eb1d8b292d3d
implementation_evidence_tree=2bed89386dc89b1231ee30198d447c5d6af23643
px3_started=true
target_met=true
safe_to_merge=false
route_approved=false
px3_owner_accepted=false
px3_merge_authorized=false
real_pixiv_network_execution_authorized=false
existing_database_or_app_storage_mutation_authorized=false
production_authorized=false
active_blocker=pending_scv2_px3_owner_acceptance_and_controlled_canary
```

PX3 is the final implementation phase. Its implementation is complete in
normal Ready PR #149 for owner acceptance and controlled canary decisions. It
does not merge itself or create a fourth phase.

## Product Vertical Slice

```text
existing Pixiv metadata/provider adapter
  -> PX1 canonical aggregate and signal projection
  -> PX2 strict consumer and existing SourceConcept resolver
  -> versioned PX3 integration result
  -> dry-run/apply/rollback product service
  -> SourceConcept-owned durable run/candidate/ambiguity provenance
  -> read API and operable admin UI
  -> synthetic local server/browser proof
```

The real provider adapter is wired at the service boundary, but credential and
network execution remain disabled. Repository migration code may be added and
validated only on a task-owned temporary database. No existing database or app
storage is opened or mutated by PX3 evidence.

The frozen implementation result contains 20 clusters, 34 members, 59 fully
accounted candidate dispositions, and 29 nonblocking ambiguity records. The
task-owned persistence proof covers dry-run, atomic apply with the existing
SourceConcept seam, replay without duplicate rows, rollback, and reapply. Its
business and public canonical fingerprints are respectively
`f1ba10194cd72232b4b8bb2ee24e7fe421d0a18115ac3d4918dc1e8af72ce020`
and `0afd1722d54444710fd80e4446d205a7ebe596e8fc9ce86e38da2c22195bdf59`.

The same-HEAD local receipt passed with a clean worktree before and after. The
executable contract independently rebuilt the repository-owned PX1/PX2 chain,
product projection, temporary persistence, and zero-activity authority facts
with zero errors and zero warnings. The synthetic local browser completed
dry-run, apply, detail/provenance inspection, disposition/conflict filters,
rollback, and reapply; the final run reported zero console errors.

## Fixed Three-Phase Route

1. `SCV2-PX1` — owner accepted and merged; canonical Pixiv metadata input.
2. `SCV2-PX2` — owner accepted and merged; deterministic SourceConcept
   clustering, candidates, ambiguity, and replay.
3. `SCV2-PX3` — final product persistence, API/UI, controlled execution
   boundaries, canary entrypoints, and owner acceptance checkpoint.
   Implementation complete; owner acceptance and controlled canaries pending.

No PX2.1, PX2-hardening, PX3-pre, or fourth phase exists. `phase-4.5-PX1 is
historical`; it remains compatibility evidence rather than current authority.

## Deferred Due-Gate Policy And Controlled Execution Gates

- Synthetic task-owned temporary database and local server/browser E2E are
  authorized for implementation evidence.
- Controlled real-provider smoke remains an owner gate and requires explicit
  credential, network, bounded-budget, and stop authority.
- Existing-database canary remains an owner gate and requires exact DB identity,
  backup/restore proof, dry-run diff, rollback rehearsal, and explicit apply
  authorization.
- The 1%-5% import canary remains an owner gate and requires bounded source
  scope, counters, abort thresholds, and post-run reconciliation.
- Full-library import and production remain unauthorized.

All inherited I2, Stable Replay, POSIX, CI, supply-chain, and attestation gates
retain their exact due conditions. `SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE`
is due before caller-controlled paths, untrusted evidence, existing DB access,
real-source canary, or production. It does not block repository-owned synthetic
temporary paths.

## Validation Route

PX3 validation covers changed Python compilation, PX1/PX2 consumer and
SourceConcept compatibility, additive temporary-schema behavior, content-level
apply/replay idempotence, rollback, API authorization and response safety,
operable UI state, executable contract mutations, tracked JSON, documentation
state, public-safety scans, one complete non-E2E suite, and one synthetic local
server/browser E2E. It never runs a real provider, reads a real source, opens an
existing database, imports user data, invokes an LLM, or performs production
work.

The final runtime non-E2E run reported 4337 passed, 22 skipped, 3 failed, and
15 warnings in 493.36 seconds. Two failures were the expected pre-carry-forward
documentation assertions closed by this final projection. The sole remaining
failure is the exact historical `missing_original_ai_execution_evidence`
private-evidence limitation; no PX3 functional regression was found. The full
suite is not rerun after this docs-only carry-forward.

## Durable Links

- `docs/state/current-phase.json`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/phase-contracts.md`
- `docs/pixiv-metadata-ingestion-and-promotion-policy.md`
- `docs/source-concept-tag-search-semantics.md`
- `docs/development/agent-runbook.md`
- `docs/test-workflow.md`
