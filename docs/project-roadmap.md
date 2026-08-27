# V.I.O.L.E.T. Project Roadmap

## Project Vision

V.I.O.L.E.T. is a personal, local-first anime and illustration library. It
combines safe ingestion, local classification, Danbooru-style retrieval,
Chinese display localization, and provenance-preserving source evidence without
treating provider metadata, SourceConcept, or model output as Entity truth.

## Current Active Roadmap

<!-- CURRENT_PHASE: SCV2-PX1 -->

The only machine-readable current-route truth is
`docs/state/current-phase.json`.

PR #146 / `SCV2-FL1-I2` is merged at
`8a825bcdd12f76d1c2c396b7039bd9e326cd63dc`; its merge tree equals accepted PR
HEAD `914d746c3548241a99333393daa88caefd8b2337` tree
`9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71`. Merge closes the old phase status
but does not silently close its final unresolved review findings. Their exact
future due gates remain in current state and handoff.

Current projection:

```text
current_status=SCV2_PX1_BOUNDED_CORRECTION_READY_FOR_FINAL_OWNER_MERGE_AUDIT
contract_id=scv2_px1_pixiv_metadata_consolidation_contract_v1
public_schema=violet.scv2-px1-pixiv-metadata-summary.v1
target_met=true
safe_to_merge=false
route_approved=false
merge_authorized=false
real_source_authorized=false
real_provider_authorized=false
production_authorized=false
active_blocker=pending_scv2_px1_final_owner_merge_audit
```

## Fixed Near-Term Route

1. `SCV2-PX1` — consolidate the existing Pixiv metadata ingestion, source
   metadata, deterministic work/page aggregate, and SourceConcept signal input
   into one repository-owned offline synthetic vertical slice. Its single
   owner-adjudicated bounded correction is complete on PR #147 and awaits only
   final owner merge audit.
2. `SCV2-PX2` — consume the PX1 artifacts for deterministic clustering,
   identity, candidate explanation, an ambiguous queue, controlled sample
   evaluation, and a persistable cluster result. PX2 is not started.
3. `SCV2-PX3` — cover real source/provider, any necessary migration,
   persistence, API/UI, dry-run/apply, idempotency, backup/recovery, canary,
   rollback, and the final full-library import checkpoint. PX3 is not started.

Safety work is a gate inside these phases and does not create PX1A, PX1B,
PX1-pre, or PX1-hardening phases. `phase-4.5-PX1` is historical; its scripts and
reports remain historical orchestration/evidence and are not renamed or
promoted into SCV2-PX1 authority.

## PX1 Boundary

PX1 reuses the existing Pixiv filename prior, gallery-dl metadata-only design,
canonical ingestion lifecycle, provider cache semantics, source-layer models,
and SourceConcept resolver semantics. It adds only the durable deterministic
aggregate/signal seam, a thin offline runner, synthetic tests, and executable
evidence needed for PX2.

PX1 does not implement clustering, persistent cluster materialization, Entity
promotion, UI, migrations, real provider acquisition, real source inventory,
media download, import, user-data classification/tagging, model execution,
server/browser/E2E validation, or production work.

## Durable Safety And Evidence Boundaries

- Stable Pixiv creator ID is an identity anchor; account and display names are
  mutable observations and cannot merge creators on their own.
- Title and tags remain work/page-context-bound signals. Missing or conflicting
  metadata is explicit and never silently unioned.
- Public projections contain no private path, filename, credential, raw
  provider payload, user database identity, or database row ID.
- The single-command runner creates only task-owned temporary databases and
  blocks network/subprocess activity inside the slice.
- The corrected same-HEAD receipt binds implementation HEAD
  `ea97f0c3dcdc83d7d08eb3e31683a84001a08664`, tree
  `2e35fb0a98b5a6ee23c4685d3cd764d13c85c910`, and 360 canonical focused tests;
  the vertical slice deterministically projects 14 aggregates and 40 signals.
- Local receipt and contract evidence remain local operator evidence:
  `machine_verifiable_ci=false`, `owner_accepted=false`, and
  `safe_to_merge=false`.
- Deferred I2 findings remain due only at their exact real-source,
  positive-authority, POSIX, CI, or hostile-environment gates; they neither
  block synthetic PX1 nor become falsely closed.
- Untrusted workspace confinement is explicitly deferred to
  `SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE` before caller-supplied paths,
  untrusted remote-CI evidence, existing DB/app-storage access, real-source
  canary, or production; it does not create a new phase.

## Remote Sync Preflight Policy

Fetch and authenticate the trusted remote before comparing a protected base.
A clean local base that is only behind may be fast-forwarded with `--ff-only`.
Divergence, unsafe local-only commits, tracked drift, behavior-affecting
untracked code/configuration, or any need for reset, rebase, force, overwrite,
or deletion remains fail closed. Preserve unrelated untracked and ignored user
artifacts.

## Documentation Map

- `docs/state/current-phase.json` — only current-route fact source.
- `docs/current-handoff.md` — generated public-safe projection.
- `docs/roadmap/current-mainline-roadmap.md` — active PX1/PX2/PX3 route.
- `docs/phase-contracts.md` — executable contract boundary.
- `docs/pixiv-metadata-ingestion-and-promotion-policy.md` — existing Pixiv
  ingestion/promotion policy.
- `docs/source-concept-tag-search-semantics.md` — existing SourceConcept signal
  and search semantics.
- `docs/roadmap/archive/` — historical roadmaps.
- `docs/development/agent-runbook.md` — operating procedure.

## Governance

Every completion claim must bind an executable phase contract to exact
repository evidence. Automated checks cannot synthesize owner acceptance or
merge authority. PX1 ends at one normal PR and exact-head owner audit; it never
merges or starts PX2 without a new owner decision.
