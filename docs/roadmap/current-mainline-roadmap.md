# Current Mainline Roadmap

<!-- CURRENT_PHASE: SCV2-PX3 -->

The current-route authority is `docs/state/current-phase.json`.
The verified incoming mainline was PR #148 / `SCV2_PX2_MERGED` at
`421e2989d274e2dc4492d5bccc10720dcfbbaa4f`, with accepted second parent
`bf8055af61c3a5d32155701ed7110db692047dba` and matching tree
`507a223a9156ff2f9944524303419e85891812fa`. No unreviewed main increment was present.

```text
current_status=scv2_px3_product_integration_in_progress
contract_id=scv2_px3_pixiv_product_integration_contract_v1
public_schema=violet.scv2-px3-pixiv-product-integration-result.v1
pr=149
implementation_evidence_head=389ab3994bb81ca772ce491dac88eb1d8b292d3d
implementation_evidence_tree=2bed89386dc89b1231ee30198d447c5d6af23643
px3_started=true
target_met=false
safe_to_merge=false
route_approved=false
px3_owner_accepted=false
px3_merged=false
px3_merge_authorized=false
real_pixiv_network_execution_authorized=false
existing_database_or_app_storage_mutation_authorized=false
production_authorized=false
active_blocker=scv2_px3_implementation_in_progress
```

## Stop Boundary

1. `SCV2-PX1` — accepted and merged canonical metadata input.
2. `SCV2-PX2` — accepted and merged deterministic clustering.
3. `SCV2-PX3` — final product integration and controlled canary owner checkpoint.

The fixed route contains only SCV2-PX1, SCV2-PX2 and SCV2-PX3. This final
bounded correction stays on PR #149; no PX3.1, PX4 or hardening phase is created.
phase-4.5-PX1 is historical compatibility evidence.

PX1 database-neutral aggregates/signals and PX2 clustering remain unchanged.
PX3 binds verified work/page/provider provenance to every matching current
SourceMetadataRecord and Media using a minimal evidence-media association.
Two duplicate media keep support; names never establish creator identity.
The existing ordinary `/api/search` and media-detail SourceConcept API consume
these edges. Historical creator aliases, tags/titles and creator+work AND
queries are exercised through actual endpoint results, with wrong-work recall zero.

Dry-run reports media/source-record/edge counts with zero writes. Apply requires
the exact accepted selection, product result and local binding fingerprints,
all recomputed before persistence. Row IDs occur only in local binding identity.
Replay adds no duplicate edges. Rollback accepts only an active, wholly owned,
unchanged resolution run, deletes only its support and owned core, retains product
audit rows, and invalidates search caches after successful commit. Existing empty
resolution runs and superseded/shared/changed core fail closed.

Disabled product routes hide runs/detail and return only feature state from
status. Persisted child and whole-projection fingerprints are verified on reads.
Admin initialization requests status and at most 50 run summaries; full detail
loads only on selection/expansion. UI apply requires the currently viewed plan
and blocks repeated clicks. Stable uniqueness conflicts return HTTP 409.

**STOP before normal startup against any existing database.** Normal startup
calls `Base.metadata.create_all()` and schema migration. Backup and successful
restore must precede the first normal startup. The additive association migration
was exercised twice on a task-owned temporary SQLite DB. No configured task-owned
PostgreSQL was available; no existing PostgreSQL connection or infrastructure
installation was attempted. See [controlled canary gates](../development/scv2-px3-controlled-canary.md).

The only next owner authorization package is:
backup/restore -> 1-5 work metadata-only provider smoke -> existing DB read-only
dry-run -> accept exact selection/result fingerprints -> 1% apply canary ->
gallery search/media detail acceptance -> replay/rollback checks.
Every real provider, credential, existing DB/storage, source/iCloud, user import,
production and full-library execution remains unauthorized.

## Deferred Due-Gate Policy

The previously defined 1%-5% import canary gate stays inside PX3; its first
authorized apply, if the owner grants it later, must use 1%.
`SCV2_PX3_MULTIWORKER_APPLY_GATE` is deferred until before multiple workers,
multiple owners or concurrent apply; `run.py` uses the default single worker.
`SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE` keeps its existing exact due
boundary before untrusted paths/evidence or existing DB/real-path execution.
These gates do not create a new phase or block the owner-authorized local merge.

The earlier projection is historical. `px3_target_met=false` remains in force until final binding/search/rollback/accepted-plan evidence and receipt pass.
