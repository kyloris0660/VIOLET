# V.I.O.L.E.T. Project Roadmap

## Project Vision

V.I.O.L.E.T. is a personal, local-first anime and illustration library. It
combines safe ingestion, local classification, Danbooru-style retrieval,
Chinese display localization, and provenance-preserving source evidence without
treating provider metadata, SourceConcept, or model output as Entity truth.

## Current Active Roadmap

<!-- CURRENT_PHASE: SCV2-PX2 -->

The only machine-readable current-route truth is
`docs/state/current-phase.json`.

PR #147 / `SCV2-PX1` is owner accepted and merged at
`5a8efdaf954ab95bd82f95464af31a7fd0873e5e`. Its second parent is accepted
PR HEAD `15cbb0c71d4b4c6e5ea32c5eb99a1f56e561d65a`, and both accepted and merge
trees are `480d6a548e6276afeccf49ec75a73d7389b995fe`. No parallel main commit was
present. Status: `SCV2_PX1_MERGED`.

Current projection:

```text
current_status=SCV2_PX2_DETERMINISTIC_PIXIV_CLUSTERING_READY_FOR_OWNER_MERGE_AUDIT
contract_id=scv2_px2_deterministic_pixiv_clustering_contract_v1
public_schema=violet.scv2-px2-pixiv-source-concept-cluster-result.v1
pr=148
implementation_evidence_head=c62d45d58431be0adf09c18bb7f4b203f93ca978
implementation_evidence_tree=d4314b11d2b64b3578935902f547b685cd3682d5
px2_started=true
target_met=true
safe_to_merge=false
route_approved=false
px2_owner_accepted=false
px2_merge_authorized=false
real_source_authorized=false
real_provider_authorized=false
existing_database_authorized=false
migration_authorized=false
production_authorized=false
active_blocker=pending_scv2_px2_owner_merge_audit
```

## Fixed Near-Term Route

1. `SCV2-PX1` — merged. Its frozen work/page aggregate and signal consumer
   contract are the only Pixiv input authority for PX2.
2. `SCV2-PX2` — delivered in normal PR #148 and pending owner merge audit. It
   consumes PX1 artifacts, reconstructs role-aware Pixiv contexts, calls the
   existing deterministic SourceConcept resolver, accounts for every candidate
   disposition, builds a nonblocking ambiguous ledger, and proves a persistable
   public-safe result in task-owned temporary SQLite.
3. `SCV2-PX3` — not started. Real source/provider, any necessary migration,
   production persistence, API/UI, canary, rollback, and final import remain
   behind separate authority gates.

Safety work remains a gate inside these phases and does not create PX1A,
PX1-hardening, PX2-pre, or a fourth phase. `phase-4.5-PX1 is historical`; its
scripts and reports remain historical compatibility evidence.

## PX2 Boundary

PX2 reuses the PX1 aggregate and signal bundle contract,
`SourceConceptSignalInput` / `SourceConceptSignalDraft`,
`resolve_source_concepts`, existing blocking/context/creator guards,
candidate edges, cannot-link-aware union-find, SourceConcept drafts, aliases,
evidence, links, search-index drafts, and existing persistence models/seam.
It does not create a second parser, resolver, graph engine, candidate registry,
review workflow, migration, or persistence layer.

Stable Pixiv creator ID is provider-global artist identity. Account and display
name are mutable observations; name-only artists do not union. Work-level tags
share `pixiv:work:{work_id}` context across pages, while page-specific facts
retain `pixiv:work:{work_id}:page:{page_index}` context. Different works do not
merge character, person, or name-only signals without stable or previously
approved alias evidence.

Conflict, page mismatch, retryable, terminal, and unsupported inputs remain
explainable but cannot synthesize active identity. Every actual candidate pair
is recorded as `must_link`, `cannot_link`, or `deferred_nonblocking`; only
policy-passing active edges may union. Ambiguity remains queryable and
persistent without blocking unambiguous clusters.

The exact implementation evidence HEAD/tree is
`c62d45d58431be0adf09c18bb7f4b203f93ca978` /
`d4314b11d2b64b3578935902f547b685cd3682d5`. Its public and business
fingerprints are `1547adcc3dc1b20e7fe3e2a67af43a0238538b59fbd00fc6b6bb84496a58fea6`
and `269a1d37ee8fbcb9c9cf86eb71e1163cdd18c478f9cce706458d5ba49dbd3548`.
Fourteen PX1 bundles contribute 40 signals to 20 concepts. All 59 candidate
pairs are accounted as 52 `must_link`, 4 `cannot_link`, and 3
`deferred_nonblocking`; the ambiguous ledger has 29 records. All 13 compact
acceptance scenarios, deterministic replay, and temporary persistence
idempotence pass.

The same-head receipt passed 572 focused tests and the executable contract
passed with zero errors or warnings. The one final non-E2E run reported 4294
passed, 22 skipped, 1 failed, and 15 warnings; the sole failure is the accepted
historical `missing_original_ai_execution_evidence` limitation rather than a
PX2 regression. Hosted CI is not inferred from local evidence.

## Durable Safety And Evidence Boundaries

- PX2 reads repository-owned synthetic PX1 artifacts and writes only
  task-owned temporary SQLite through existing SourceConcept-owned tables.
- The versioned result excludes row IDs, timestamps as identity, private paths,
  filenames, raw payloads, credentials, and secrets.
- Network/provider, real source, existing database/app storage, migration,
  media, user-data import/tagging, LLM, server/browser/E2E, and production
  activity remain zero and unauthorized.
- `SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE` and all inherited exact due
  gates remain unresolved. They do not block repository-owned synthetic PX2.
- The existing `FL1_I3_REAL_SOURCE_SCOPE_GATE` and `STABLE_REPLAY_GATE` carry
  five findings created after PR #147 had already merged: work-ID and
  creator-ID alias consensus, legacy provenance identity compatibility,
  invalid provider-marker consensus in route fallback, and current
  normalizer-version propagation. They are due before any real provider,
  existing-data, canary, or production path.
- Local tests and contract evidence cannot synthesize owner acceptance, merge
  authority, hosted CI, or production readiness.

The same one-time post-merge adjudication rejected retained SQLite byte binding
as a current contract requirement because retained databases are not verdict
inputs and both contracts regenerate their public result in fresh temporary
databases. It also confirmed that PR #148 closes aggregate stable-key mismatch
at the strict PX2 consumer boundary. The seven late threads are resolved; the
original hostile-workspace thread is intentionally the only unresolved PR #147
thread.

## Remote Sync Preflight Policy

Fetch and authenticate the trusted remote before comparing a protected base.
A safe clean base that is only behind may fast-forward with `--ff-only`.
Divergence, unsafe local-only commits, tracked drift, behavior-affecting
untracked code/configuration, or any need for reset, rebase, force, overwrite,
or deletion remains fail closed. Preserve unrelated user artifacts.

## Documentation Map

- `docs/state/current-phase.json` — only current-route fact source.
- `docs/current-handoff.md` — generated public-safe projection.
- `docs/roadmap/current-mainline-roadmap.md` — active PX1/PX2/PX3 route.
- `docs/phase-contracts.md` — executable contract boundary.
- `docs/pixiv-metadata-ingestion-and-promotion-policy.md` — frozen input policy.
- `docs/source-concept-tag-search-semantics.md` — resolver semantics.
- `docs/development/agent-runbook.md` — operating procedure.
- `docs/test-workflow.md` — validation workflow.
- `docs/roadmap/archive/` — historical roadmaps.

## Governance

PX2 ends at normal PR #148 and exact-head owner merge audit. PX2 is not
owner accepted, is not safe to merge, has no merge authority, and does not
start PX3.
