# V.I.O.L.E.T. Project Roadmap

## Project Vision

V.I.O.L.E.T. is a personal, local-first anime and illustration library. It
combines safe ingestion, local classification, Danbooru-style retrieval,
Chinese display localization, and provenance-preserving source evidence without
treating provider metadata, SourceConcept, or model output as Entity truth.

## Current Active Roadmap

<!-- CURRENT_PHASE: SCV2-PX3 -->

The only machine-readable current-route truth is
`docs/state/current-phase.json`.

PR #148 / `SCV2-PX2` is owner accepted and merged at
`421e2989d274e2dc4492d5bccc10720dcfbbaa4f`. Its second parent is accepted
PR HEAD `bf8055af61c3a5d32155701ed7110db692047dba`; accepted and merge trees are
`507a223a9156ff2f9944524303419e85891812fa`. No parallel main commit was
present. Status: `SCV2_PX2_MERGED`.

Current projection:

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

## Fixed Route

1. `SCV2-PX1` — merged canonical Pixiv metadata ingestion/projection.
2. `SCV2-PX2` — merged deterministic SourceConcept clustering and persistence.
3. `SCV2-PX3` — implementation complete in normal Ready PR #149: durable
   product run facts, dry-run/apply/rollback, API/UI, and controlled canary
   entrypoints.

PX3 reuses the existing provider metadata adapter, PX1 aggregate/signal contract,
PX2 clustering service, SourceConcept resolver, graph policy, models, persistence
seam, API router, admin page, import boundaries, and feature flags. It must not
create a second parser, resolver, graph engine, candidate registry, or storage
system.

Repository migration code and task-owned temporary database validation are
authorized. Synthetic local server/browser E2E is authorized. Real Pixiv
network or credentials, real source/iCloud access, existing database/app storage
mutation, user-data import, production, full-library import, and PX3 merge are
not authorized.

The real provider path may be connected to the integration seam but cannot run
in this task. Controlled provider smoke, existing-DB canary with backup/restore,
and 1%-5% import canary are explicit owner gates within PX3, not a PX4.

The exact implementation evidence contains 20 clusters, 34 member signals, 59
candidate dispositions (`52 must_link`, `4 cannot_link`, `3
deferred_nonblocking`), and 29 queryable ambiguity records. Apply/replay is
idempotent, rollback/reapply is verified, and only SourceConcept-owned product
tables are added in a task-owned temporary SQLite database. The executable
contract passed with zero errors or warnings on implementation HEAD
`389ab3994bb81ca772ce491dac88eb1d8b292d3d`.

The synthetic browser exercised dry-run, apply, cluster/member/provenance
detail, cannot-link and context-conflict filters, rollback, and reapply against
the real local API/UI. Its final clean run had zero console errors. No real
provider, credential, source, existing database, user import, LLM, or
production activity occurred.

Safety work remains a gate inside the three phases. No PX2.1, hardening phase,
or fourth planning phase exists. `phase-4.5-PX1 is historical`; its artifacts
are compatibility evidence only.

## Documentation Map

- `docs/state/current-phase.json` — current-route fact source.
- `docs/current-handoff.md` — generated public-safe projection.
- `docs/roadmap/current-mainline-roadmap.md` — current implementation route.
- `docs/phase-contracts.md` — executable evidence boundary.
- `docs/pixiv-metadata-ingestion-and-promotion-policy.md` — Pixiv input policy.
- `docs/source-concept-tag-search-semantics.md` — resolver semantics.
- `docs/development/agent-runbook.md` — operating procedure.
- `docs/test-workflow.md` — validation workflow.

## Governance

PX3 stops at normal Ready PR #149 for owner acceptance and controlled canary
decisions. Local evidence cannot grant owner acceptance, hosted CI, real-data
authority, production readiness, or merge authority.
