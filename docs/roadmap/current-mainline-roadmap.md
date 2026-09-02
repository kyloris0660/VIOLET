# Current Mainline Roadmap

## Accepted Mainline

<!-- CURRENT_PHASE: SCV2-PX2 -->

Trusted remote and post-merge verification established:

```text
origin/main=5a8efdaf954ab95bd82f95464af31a7fd0873e5e
origin/main_tree=480d6a548e6276afeccf49ec75a73d7389b995fe
pr147_accepted_head=15cbb0c71d4b4c6e5ea32c5eb99a1f56e561d65a
pr147_accepted_tree=480d6a548e6276afeccf49ec75a73d7389b995fe
merge_parents=8a825bcdd12f76d1c2c396b7039bd9e326cd63dc,15cbb0c71d4b4c6e5ea32c5eb99a1f56e561d65a
accepted_head_is_merge_ancestor=true
accepted_tree_equals_merge_tree=true
post_merge_commit_audit_count=0
SCV2_PX1_MERGED
```

PR #147 used one expected-head protected merge-commit operation. It was not
squashed, rebased, force-pushed, or followed by a direct main push.

## Current Phase And Stop Boundary

```text
current_status=SCV2_PX2_DETERMINISTIC_PIXIV_CLUSTERING_READY_FOR_OWNER_MERGE_AUDIT
contract_id=scv2_px2_deterministic_pixiv_clustering_contract_v1
public_schema=violet.scv2-px2-pixiv-source-concept-cluster-result.v1
pr=148
implementation_evidence_head=c62d45d58431be0adf09c18bb7f4b203f93ca978
implementation_evidence_tree=d4314b11d2b64b3578935902f547b685cd3682d5
public_summary_fingerprint=1547adcc3dc1b20e7fe3e2a67af43a0238538b59fbd00fc6b6bb84496a58fea6
business_projection_fingerprint=269a1d37ee8fbcb9c9cf86eb71e1163cdd18c478f9cce706458d5ba49dbd3548
px2_started=true
px2_owner_accepted=false
target_met=true
safe_to_merge=false
route_approved=false
px2_merge_authorized=false
px3_started=false
real_source_authorized=false
real_provider_authorized=false
existing_database_authorized=false
migration_authorized=false
full_import_authorized=false
production_authorized=false
machine_verifiable_ci=false
active_blocker=pending_scv2_px2_owner_merge_audit
```

PX2 is restricted to repository-owned synthetic PX1 artifacts and task-owned
temporary SQLite. It ends at one normal Ready PR and owner audit. It cannot
merge itself, start PX3, or consume real source/provider/database/media/model
authority.

## PX2 Vertical Slice

```text
PX1 consumer contract
  -> strict schema and fingerprint validation
  -> canonical SourceConcept signal reconstruction
  -> role-aware Pixiv work/page context projection
  -> existing deterministic SourceConcept resolution
  -> complete candidate dispositions and explanations
  -> clusters plus nonblocking ambiguous ledger
  -> existing SourceConcept models in task-owned temporary SQLite
  -> deterministic public-safe persistable result
```

PX2 reuses `SourceConceptSignalInput`, `SourceConceptSignalDraft`,
`resolve_source_concepts`, existing blocking keys, context compatibility,
creator identity guard, candidate edges, cannot-link-aware union-find,
SourceConcept drafts, aliases, evidence, links, search-index drafts, and the
existing persistence seam. No second clustering engine, resolver, candidate
registry, LLM workflow, migration, or persistence layer is authorized.

Stable Pixiv creator ID is provider-global artist identity. Account/display
name remain mutable observations. Name-only artists do not union. Work-level
tags share `pixiv:work:{work_id}` across pages; page-specific facts preserve
`pixiv:work:{work_id}:page:{page_index}`. Cross-work character/person/name-only
signals stay independent absent stable or approved alias evidence.

Every actual candidate pair has one stable `must_link`, `cannot_link`, or
`deferred_nonblocking` disposition. Cannot-link and deferred candidates never
participate in union, including through transitive paths. Ambiguous candidates,
links, context conflicts, and source-state deferrals remain queryable and
persistent without blocking deterministic clusters.

## Fixed Three-Phase Route

1. `SCV2-PX1` — owner accepted and merged.
2. `SCV2-PX2` — deterministic clustering, candidate explanation, ambiguous
   ledger, temporary persistence/replay, and a persistable result. Delivered in
   normal PR #148 and pending owner merge audit.
3. `SCV2-PX3` — real source/provider, necessary migration, production
   persistence, API/UI, canary, rollback, and final import. Not started.

No fourth phase or PX2-pre/hardening phase exists. `phase-4.5-PX1 is
historical`; it is compatibility evidence rather than current authority.

## Deferred Due-Gate Policy

All inherited I2, owner-authority, Stable Replay, POSIX, CI, supply-chain, and
identity-attestation gates remain attached to their exact future conditions.
Hostile workspace confinement remains due at
`SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE` before caller-supplied paths,
untrusted remote-CI evidence, existing DB/app-storage, real-source canary, or
production. These do not block repository-owned synthetic PX2.

Seven automated review threads were created eight minutes after PR #147 had
already merged and were adjudicated once without reopening a review loop. Five
real-path findings are now direct PX3 inputs under the existing
`FL1_I3_REAL_SOURCE_SCOPE_GATE` and `STABLE_REPLAY_GATE`: work-ID alias
consensus, creator-ID alias consensus, legacy stable-provenance compatibility,
invalid-versus-absent provider marker handling, and current normalizer-version
propagation. They are due before real provider, existing-data, canary, or
production execution and are unreachable in repository-owned synthetic PX2. The retained-database
binding finding was rejected because neither PX1 nor PX2 treats retained DB
bytes as verdict input: each contract independently regenerates the result in
fresh task-owned databases. The aggregate stable-key finding is closed at the
PX2 consumer boundary by recomputation plus a rebound-fingerprint mutation
test. All seven late threads were replied to and resolved; the original hostile
workspace thread remains the sole unresolved PR #147 thread as required.

## Validation Route

PX2 validation uses the approved repository Python and includes changed Python
compile, PX1 consumer compatibility, SourceConcept resolver compatibility,
clustering/context/candidate/ambiguous/persistence tests, contract mutation
tests, deterministic replay, tracked JSON, documentation state, diff and
public-safety scans, plus one complete non-E2E suite at final runtime-code HEAD.
Server/browser/E2E, real provider, real source, existing database, migration,
LLM, full import, and production execution remain forbidden.

Exact implementation evidence at `c62d45d58431be0adf09c18bb7f4b203f93ca978`
and tree `d4314b11d2b64b3578935902f547b685cd3682d5` records 14 PX1
aggregates/bundles, 40 canonical signals, 20 concepts, and all 59 candidate
pairs: 52 `must_link`, 4 `cannot_link`, and 3 `deferred_nonblocking`. The
nonblocking ambiguous ledger contains 29 records and all 13 compact acceptance
scenarios pass. The same-head receipt passed 572 focused tests with clean
before/after proof; the executable contract passed with zero errors and zero
warnings. Deterministic replay and task-owned temporary persistence idempotence
are true, while existing DB/app storage, provider network, real-source, LLM,
and production activity are zero.

The one authorized final non-E2E run reported 4294 passed, 22 skipped, 1
failed, and 15 warnings in 507.75 seconds. Its sole failure was the exact
historical `missing_original_ai_execution_evidence` private-evidence limitation;
no evidence was copied or synthesized, and it is not a PX2 functional
regression. Hosted CI remains separate and is not claimed by local evidence.

## Remote Sync Preflight Policy

Fetch the trusted remote before comparing bases. A safe clean base with no
local-only commits that is only behind may fast-forward with `--ff-only`.
Divergence, tracked drift, behavior-affecting untracked code/configuration,
failed fast-forward, or any need for reset, rebase, force, overwrite, or
deletion is fail closed. Preserve unrelated user artifacts.

## Durable Links

- `docs/state/current-phase.json`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/phase-contracts.md`
- `docs/pixiv-metadata-ingestion-and-promotion-policy.md`
- `docs/source-concept-tag-search-semantics.md`
- `docs/development/agent-runbook.md`
- `docs/test-workflow.md`
