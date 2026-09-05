# V.I.O.L.E.T. Project Roadmap

<!-- CURRENT_PHASE: SCV2-PX3 -->

The current-route authority is `docs/state/current-phase.json`.
The historical PX3 incoming mainline was PR #148 / `SCV2_PX2_MERGED` at
`421e2989d274e2dc4492d5bccc10720dcfbbaa4f`, with accepted second parent
`bf8055af61c3a5d32155701ed7110db692047dba` and matching tree
`507a223a9156ff2f9944524303419e85891812fa`. No unreviewed main increment was present.

```text
current_status=SCV2_PX3_RESTORED_DATABASE_CANARY_VERIFIED_PENDING_ORIGINAL_DATABASE_APPLY_APPROVAL
contract_id=scv2_px3_pixiv_product_integration_contract_v1
public_schema=violet.scv2-px3-pixiv-product-integration-result.v1
prior_merged_pr=149
followup_pr=150
implementation_evidence_head=306cc811fb0b49a5450ffb419edf115562045515
implementation_evidence_tree=98e2df6decea7c5150b83e4fe3c65a8fd4dd5f27
px3_started=true
target_met=true
safe_to_merge=true
route_approved=false
px3_owner_accepted=true
px3_merged=true
px3_merge_authorized=false
real_pixiv_network_execution_authorized=false
existing_database_or_app_storage_mutation_authorized=false
production_authorized=false
active_blocker=original_database_apply_approval_required
```

## Stop Boundary

1. `SCV2-PX1` — accepted and merged canonical metadata input.
2. `SCV2-PX2` — accepted and merged deterministic clustering.
3. `SCV2-PX3` — final product integration and controlled canary owner checkpoint.

The fixed route contains only SCV2-PX1, SCV2-PX2 and SCV2-PX3. This final
bounded correction uses one normal follow-up PR after merged PR #149; no PX3.1, PX4 or hardening phase is created.
phase-4.5-PX1 is historical compatibility evidence.
Never treat provider metadata, SourceConcept, or model output as Entity truth.

PX1 database-neutral aggregates/signals remain unchanged. PX2 accepts the existing resolver's explicitly rejected popularity/meta signals without promoting them.
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

**STOP before normal startup or writes against the original database.** Normal startup
calls `Base.metadata.create_all()` and schema migration. Original read-only backup
and independent task-owned PostgreSQL restore succeeded in this task; original
writes remain zero. The copy needed the two PX3 additive migrations plus the
existing optional fallback-search-index migration for rollback. All 54 original
table snapshots matched exactly after rollback. Fixed 1% means 5 of 465 eligible
works, covering 5 pages and Media, with 51 bindings, 43 clusters and 56 ambiguity
records. Real Edge exercised actual gallery search and metadata detail without
loading original images, thumbnails or files; reapply restored the same support.

The next owner authorization concerns only original-database schema/startup and
the first frozen-input 1% SourceConcept materialization canary. Recompute all three
fingerprints on that actual target; copy fingerprints cannot authorize it.
Provider smoke is unnecessary for this existing-metadata exercise and the old
unbounded ingestion command is non-executable. See the controlled canary handoff.

## Deferred Due-Gate Policy

The previously defined 1%-5% import canary gate stays inside PX3; its first
authorized apply, if the owner grants it later, must use 1%.
`SCV2_PX3_MULTIWORKER_APPLY_GATE` is deferred until before multiple workers,
multiple owners or concurrent apply; `run.py` uses the default single worker.
`SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE` keeps its existing exact due
boundary before untrusted paths/evidence; current owner explicitly authorizes the trusted fixed-path, single-process database-copy rehearsal without requiring a general hostile-workspace framework.
These gates do not create a new phase or block the owner-authorized local merge.

Historical PR #149 local receipt: **783 passed**, clean before/after,
implementation HEAD `ce5a11f75f13965652cb6f9179bbde45526c6e18`. Command fingerprint
`a018f3496ae0c8b1da960644ba79d4f283b674197699354bbfa3d222c3d1d0de`; stdout fingerprint
`21c338c92293f6e889c6abd10460225257428a6864e8ae24ff2b6e0534e05d8e`. The contract independently rebuilds
all inputs, binding, actual search results, accepted-plan rejection and rollback
proofs, including a second database with shifted row IDs. It reports zero errors
and warnings. The binding fixture has four media, four source records and 16 edges.

Historical PR #149 full non-E2E run (not this follow-up): **4355 passed, 22 skipped, 1 failed,
7 setup errors, 15 warnings** (528.71 s). The one failure,
`missing_original_ai_execution_evidence`, was reproduced on exact base
`421e2989d274e2dc4492d5bccc10720dcfbbaa4f`; no evidence was copied or fabricated.
The seven setup errors shared a late-imported empty Base after environment-safety
module reload. Mapped-model metadata fixes the proof schema; the ordered
environment/contract/binding regression passed **81 tests, 1 skipped**, and the
final focused receipt passes. The full suite was not rerun or relabeled green.

System Edge completed dry-run, accepted-plan apply, gallery alias+title search,
media detail provenance, immediate rollback disappearance and reapply recovery;
admin initialization made zero full-detail requests and the final console had
zero errors. The synthetic server was stopped. Changed Python compile, tracked
JSON, docs checker, diff, UTF-8/NUL and added-diff secret scans passed. Black was
unavailable and was not installed. Hosted checks and a new review were not
requested and are not inferred from local validation.

PR #149 merged once at `6db72c73397c17128bd2ce9be54f25233bc853f0`; parents are `421e2989d274e2dc4492d5bccc10720dcfbbaa4f,496a5fa85bc5e02b6c90331cb19a29169db1d9d0`. Accepted and merge trees both equal `f49a75b1ac2859919d4faae9e81dc437f8cddf89`. Merge authority is consumed. This post-merge governance checkpoint on the same feature branch does not modify the accepted main tree or authorize normal startup. The seven existing threads received one reply each; six accepted threads are resolved and multiworker apply remains deferred.

## Authorized PostgreSQL copy rehearsal within SCV2-PX3

PR #149 merge authority is consumed. Original read-only database backup and independent task PostgreSQL restore are authorized and have occurred; real database activity is not zero. All migration and fixed 1% work materialization writes are confined to that copy. Provider network, credentials, metadata refresh, original database writes and original media/storage access remain forbidden. This is SourceConcept materialization, not media import. No new phase is created.

The next original-database operation requires separate owner approval and fresh actual-target selection, product and binding fingerprints. Copy acceptance never authorizes original apply. The provider-smoke template is non-executable until its work bound is actually enforced. Deferred gates: `SCV2_PX3_METADATA_REFRESH_BINDING_GATE` before metadata refresh/ongoing original use; `SCV2_PX3_POLICY_VERSION_CAPTURE_GATE` before the next PX2 policy version change. Existing multiworker and workspace debt remains open.

Final restored-copy checkpoint: 852 canonical focused tests passed on implementation HEAD `306cc811fb0b49a5450ffb419edf115562045515`; independent synthetic and private PostgreSQL contracts passed. The full non-E2E suite ran once (4384 passed, 22 skipped, 11 raw failures); seven environment failures and three documentation assertions were closed by targeted validation, while missing original AI execution evidence reproduced on exact base. See [controlled-canary evidence and boundaries](development/scv2-px3-controlled-canary.md) for detail. The one-time PR #150 merge permission expires automatically when that PR is merged; original writes and normal startup remain unauthorized.
