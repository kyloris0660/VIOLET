# SCV2-FL1 Isolated Full-Library Dev/Test Plan

## 1. Owner Decision

The owner-approved planning input remains
`db90457d51a39b5dc930afc2a92a6ef3139a2760`. PR #143 was subsequently
owner-accepted and merge-commit merged at
`a2f48bdba979f579b7cd1cdd9ef541137b2479c5`. The owner now authorizes the
complete `SCV2-FL1-I1` reusable read-only inventory safety implementation and
validation using only synthetic or newly created temporary fixtures.

Canonical active phase:

- ID: `SCV2-FL1-I1`
- Title: `Read-only Inventory`
- Current status: `fl1_i1_read_only_inventory_implementation_in_progress`
- Branch: `codex/scv2-fl1-i1-read-only-inventory-v2`
- Accepted mainline base: `a2f48bdba979f579b7cd1cdd9ef541137b2479c5`
- Current stop: freeze synthetic implementation evidence, create a Draft PR,
  request one final-head review, and stop at owner audit; no real source scope is
  authorized

Final PR #143 review `4890771735` carries forward five owner-adjudicated
constraints: complete protected roots from trusted context, merge-topology-safe
evidence, distinct restart provenance, trusted actual Git HEAD binding, and
local validation receipts that cannot claim CI authority. Direct human owner
acceptance remains outside executable contract authority.

The older `Phase 4.6-FULLLIB-P0 Production Full-Library Import and AI Tagging
Plan` is a historical design input only. Its production route and `FULLLIB-E1`
recommendation are superseded for current planning and provide no authorization.

## 2. Goals

- Define a safe, resumable Dev/Test rehearsal for the full eligible library.
- Establish an exact inventory denominator and complete per-item accounting.
- Prove isolation between source roots, test staging/storage, strict-test
  databases, accepted SV1 evidence databases, and production.
- Reuse compatible accepted SV1-A/SV1B evidence without importing phase-local
  row IDs, weak identity claims, or waivers.
- Define bounded local classification and WD tagging with durable provenance.
- Specify future metadata/localization/source-graph expansion checkpoints
  without entering external routes now.
- Produce executable contract and manual-acceptance requirements before any
  scale or production decision.

## 3. Non-Goals

- No production DB, storage, source root, library snapshot, or production
  comparison.
- No current database creation, schema change, import, classification, tagging,
  localization, graph/search derivation, replay, or cleanup.
- No Pixiv, gallery-dl, Provider-2, reverse search, external LLM, media, or
  thumbnail request.
- No Entity, EntityAlias, confirmed assignment, user truth, or provider-derived
  `media_tags` truth.
- No automatic creator union based on names, placeholders, single media,
  similarity, or model guesses.
- No production readiness claim and no inheritance of B01/B04/B08 waivers.

## 3A. I1 Implementation Contract

I1 produces reusable validation/safety tooling, not a production ingestion
runtime. The implementation is modular: trusted runtime context, complete
protected-root role registry, layered source-operation gateway, scanner and
private artifacts, cross-process resume provenance, and a dedicated I1 phase
contract. The two explicit source modes are `synthetic_fixture` and
`authorized_read_only_source`; during this phase both are validated only with
new temporary roots.

The owner-audit-ready synthetic state remains `target_met=false`,
`safe_to_merge=false`, `route_approved=false`,
`real_source_inventory_authorized=false`, and `real_inventory_started=false`.
No caller boolean can create owner authorization or trusted test PASS.

### PR #142 bounded carry-forward matrix

| Candidate behavior | Decision | Reason | New regression |
|---|---|---|---|
| Deterministic tree traversal | Keep conceptually | Stable discovery and duplicate primary selection require deterministic ordering | row-order permutation and deterministic duplicate-primary tests |
| Read-only/no-follow open plus pre/post snapshots | Redesign | POSIX `O_NOFOLLOW` and one tree snapshot do not cover Windows reparse/Cloud Files layers or per-operation evidence | Windows reparse, path escape, open/read signature-race, and source-mutation-zero tests |
| Item/byte/chunk budgets | Redesign | I1 also needs directory-entry, per-file hash, total hash, timeout, failure, systemic, and manual-stop budgets persisted in run identity | every budget and stop reason tested independently |
| Exact-content duplicate accounting and one terminal disposition | Keep conceptually | These are sound denominator primitives | duplicate-reference tamper and every denominator-equation tamper fail closed |
| Public aggregates separated from private manifest | Redesign | Unkeyed per-item labels and editable redaction booleans remain enumerable or forgeable | aggregate-by-default projection plus unknown-field/path/content/key leakage rejection |
| Real-source default deny with temporary fixtures | Keep conceptually | Current authorization is synthetic-first and real source remains external governance | real mode without exact trusted scope fails; temporary trusted scope passes |
| Caller-supplied actual Git HEAD or Python executable | Reject | Actual identities must be derived from repository and current `sys.executable` | fake matching caller identity, old-HEAD artifact, and wrong interpreter tests |
| Caller-supplied `forbidden_roots` completeness | Reject | One irrelevant path cannot prove all production/source/app/repo/evidence roles | missing/unknown/duplicate/alias/overlap/escape role tests |
| Synthetic disposition override as Cloud Files proof | Reject | Synthetic observation is useful only as explicitly labeled fixture evidence | unsupported/unknown attribute never becomes available; recall-risk never opens content |
| Copied before/after snapshot as restart proof | Reject | Restart requires independently observed process and invocation lineage | two-process harness plus same-invocation, copied snapshot, wrong-parent, wrong-HEAD/config negatives |
| In-memory manifest and caller test booleans | Reject | Resumable evidence needs atomic private artifacts and local results need bound receipts | tampered manifest/operation ledger/receipt and `machine_verifiable_ci=true` rejection |

No PR #142 commit, governance state, P1 remediation, or wholesale patch is
cherry-picked. The matrix preserves only independently reimplemented concepts.

## 4. Isolation Design

The future implementation prompt must name all identities exactly. Proposed
shape:

| Surface | Required boundary |
|---|---|
| Working database | Fresh separately owned strict-test DB with a canonical segmented test identity; never production/default/dev or accepted SV1 DB |
| Replay/verification database | Optional only if the approved implementation contract needs independent logical replay; separately owned, strict-test, distinct from working DB |
| Staging/storage | New local non-network test root outside source roots, app production storage, repo tracked paths, and accepted SV1 storage |
| Source input | Immutable manifest or separately approved read-only test fixture; real full source roots remain unavailable until an explicit inventory authorization |
| Evidence output | New repo-local gitignored root with atomic ledgers; public output contains aggregates and safe labels only |
| Process environment | Repo venv, `VIOLET_ENV=test`, exact `TEST_DATABASE_URL`, no production variables, no provider/LLM credential routes |

Mandatory preflight before any future DB connection or filesystem write:

1. Git/branch/HEAD and Python identity.
2. Strict segmented test DB identity and ownership key.
3. Database pairwise distinction and accepted-evidence DB denylist.
4. Test storage containment and non-overlap with source/production/accepted
   roots.
5. No active conflicting runner or server process.
6. External-call route count and projected external cost both zero.

## 5. Full-Library Inventory Denominator

The denominator is a manifest-bound set of stable source-item identities, not a
current DB row count. Each discovered item receives one stable private key and
one public-safe label. The inventory equation must balance:

`discovered = supported + unsupported`

`supported = duplicate + cloud_recall_deferred + unreadable_or_missing + eligible_candidate`

`eligible_candidate = imported + import_deferred + import_failed`

Every item must have exactly one terminal inventory disposition and, when
applicable, separate import/classification/tagging lifecycle states.

Required aggregate dimensions:

- total discovered files and unique stable items;
- supported/unsupported extension;
- exact duplicate, content duplicate, and path-only duplicate signal;
- Cloud Files hydrated/recall-risk/unavailable state;
- missing, unreadable, permission, timeout, size/hash mismatch;
- content eligibility and exclusion reason;
- import/classification/AI-tagging attempted, complete, deferred, failed;
- unresolved count, which must be zero for a completion claim.

Filename, row order, database numeric ID, or display label may not define stable
membership.

## 6. Duplicate, Unsupported, And Cloud-Recall Policy

- Exact content fingerprints are the deduplication authority. Filename/path
  similarity is diagnostic only.
- Duplicate items remain ledgered with their matched stable fingerprint and do
  not count as failed imports.
- Unsupported extensions and corrupt media are explicit terminal exclusions.
- Cloud recall-risk is a visible per-item state, not silent loss or a permanent
  full-run blocker.
- Default real-source policy is no broad hydration. Any cloud-aware read/copy
  authorization must be separately scoped with bounded retries and source
  mutation still forbidden.
- A systemic cluster of same-reason failures may stop the run even when each
  failure would otherwise be per-item.

## 7. Batch Import And Recovery

Future execution must be finite and restartable:

- immutable run manifest plus run ID and batch IDs;
- deterministic batch ordering and no concurrent duplicate item execution;
- temp-first copy inside test app-managed storage, content verification, then
  atomic finalization;
- small transaction boundaries with DB/file coherence proof;
- checkpoint after every item attempt and batch boundary;
- maximum item attempts fixed by contract; attempts never reset on restart;
- live-child reconciliation before retry;
- idempotency by stable content fingerprint and logical target identity;
- backfill consumes only explicitly deferred/failed membership;
- resume requires exact Git, manifest, DB ownership, storage, model, policy,
  and ledger fingerprints.

Rollback for the rehearsal means abandoning the separately owned test DB and
storage only under a later exact destructive authorization. The default failure
response is preserve-forensics and stop, not cleanup.

## 8. Classification And Local AI Tagging

Classification and WD tagging are separate future substages after import
closure:

- Run local/offline models only; model artifacts must already be available.
- Classify only newly imported, contract-eligible items.
- AI tag only eligible items and prefer compatible stable-fingerprint reuse
  before inference.
- Bind model identity, revision/cache fingerprint, thresholds, code HEAD,
  source (`ai_wd`), confidence, and job/run identity.
- Preserve manual/locked truth. AI suggestions cannot overwrite manual rows.
- Proper-noun AI output is weak evidence/statistics/query seed only and cannot
  create Entity truth, confirmed assignment, or trusted creator identity.
- Disable background localization and all external LLM/provider fallbacks.

Proposed local mutation surface for a later approved Dev/Test run is limited to
new rehearsal rows in `Media`, classification jobs/results, AI-tag jobs, and AI
provenance tag links. Exact table names and pre/post fingerprints must be
declared by the implementation contract before writes.

## 9. SV1-A / SV1-B Evidence Reuse

Accepted evidence may be reused only when schema, stable key, stable
fingerprint, model/provider identity, and lifecycle semantics are compatible.

Allowed candidates for reuse:

- stable media membership and content fingerprints from SV1-A;
- compatible local AI-tag evidence with exact model/threshold provenance;
- accepted non-derived source metadata and translations from SV1B for exact
  stable media/tag/source identities;
- accepted R2R/source-graph evidence as read-only comparison input.

Forbidden inheritance:

- database numeric row IDs or physical insertion order;
- provider execution attempts, credentials, URLs, raw responses, or queue state;
- B01/B04/B08 owner waiver as a real-creator or scale-up policy;
- derived SourceConcept rows without independent replay/derivation proof;
- `Entity`, confirmed assignment, user truth, or provider-derived media-tags
  truth.

The implementation plan must produce a reuse/loss ledger and fail closed on any
unknown graph-effective, trusted-complete, localization, or provenance field.

## 10. Metadata, Localization, And Graph Extension Route

FL1 import/classification/tagging does not require external source metadata.
After the local rehearsal closes, a later proposal may assess coverage gaps:

1. cache-first reuse of accepted Pixiv metadata for exact stable identities;
2. local canonical-tag display fallback and accepted translation reuse;
3. independent source-graph derivation from accepted non-derived packages;
4. search lifecycle and media-level AND validation;
5. provider acquisition only for an exact open universe under a separate
   privacy/budget/credential authorization.

No later route may relabel cached data as newly authenticated provider truth or
promote SourceConcept to Entity/user truth without its own contract.

## 11. Provider And External Request Boundary

Current need: none. Current external-call budget and projected cost: zero.

Any future provider or LLM proposal must independently declare:

- exact finite membership and why local/cache evidence is insufficient;
- provider-specific privacy eligibility and payload projection;
- credential route, redaction, cache, timeout, retry, rate-limit, spacing, and
  subprocess safety;
- cost ceiling and stop conditions;
- metadata/media download policy;
- separate owner authorization before first network request.

Provider calls, LLM calls, model downloads, and media/thumbnail downloads cannot
be enabled by approving this plan.

## 12. Mutation Allowlist And Forbidden Tables

Future Dev/Test execution must use default-deny table accounting.

Potentially allowlisted only after implementation review:

- newly imported rehearsal `Media` rows;
- phase-owned scan/import item-state rows;
- phase-owned classification job/result rows;
- phase-owned AI-tag job and AI provenance tag links;
- explicitly required canonical `Tag` rows without overriding accepted/manual
  semantics.

Always forbidden in FL1 without a later explicit amendment:

- production and accepted SV1 databases;
- users, API keys, albums, settings, unrelated operational tables;
- provider cache/source acquisition tables;
- source metadata, translation, SourceConcept graph/search tables during local
  import/tagging substages;
- Entity, EntityAlias, evidence/candidate/assignment/truth tables;
- confirmed assignment, user truth, and provider-derived `media_tags` truth;
- delete, truncate, reset, drop, cleanup, or in-place repair.

## 13. Failure Budget And Fail-Closed Conditions

Initial per-batch proposal, subject to owner approval:

- `max_item_failures=20`
- `max_failure_rate=0.05`
- `max_consecutive_failures=10`
- `max_same_reason_failures=20`
- finite per-item timeout and maximum attempts declared by implementation

Per-item failures remain ledgered and excluded from eligible completion.
Structural blockers stop the entire stage:

- Git/Python/DB/storage/source identity mismatch;
- production or accepted-evidence DB/storage resolution;
- path escape or source/app-storage overlap;
- manifest schema, duplicate stable key, or fingerprint conflict;
- unexpected table/filesystem mutation;
- source/iCloud mutation;
- missing/corrupt checkpoint or non-idempotent restart;
- background job or duplicate runner conflict;
- external route entered or projected cost nonzero;
- public redaction failure;
- denominator equation mismatch or unexplained item.

## 14. Manual Acceptance And Stop Points

Proposed owner checkpoints:

1. approve this implementation plan;
2. approve exact read-only inventory identities and source scope;
3. review inventory denominator and failure-risk report before writes;
4. approve bounded Dev/Test import/classification/tagging execution;
5. review a manifest-bound manual sample after automated closure;
6. separately decide whether production planning may begin.

The final sample should be selected from current phase deltas and include:

- imported media and duplicate/exclusion evidence;
- cloud-deferred and recovery cases;
- classification boundary cases;
- AI tag provenance and manual-truth preservation;
- search results using new local tags;
- creator/identity cases demonstrating that weak or placeholder signals remain
  independent.

Automated browser prevalidation is not owner acceptance. Any unresolved real
creator, reliable account, normal search, or truth-path issue is not covered by
the SV1B placeholder waiver.

## 15. Executable Contract And Tests

The accepted P1 safety slice registers
`scv2_fl1_isolated_full_library_dev_test_contract_v1`. I1 separately registers
`scv2_fl1_i1_read_only_inventory_contract_v1` and derives, rather than accepts
as caller claims, at least:

- actual Git/Python safe identities and relevant-worktree cleanliness;
- complete protected-root role registry and exact temporary source scope;
- manifest and denominator equations;
- write-ahead listing/metadata/attribute/read/hash operation evidence;
- item/read/hash/time/failure/manual-stop budget accounting;
- cross-process invocation and parent-checkpoint provenance;
- source mutation and all forbidden-route zero counts;
- public redaction and private-artifact separation;
- manual-acceptance requirement/status;
- validation receipt trust level and CI limitation;
- false `target_met`, `safe_to_merge`, `route_approved`, and real-source authority.

The contract verifies Git objects in a trusted repository. PR audit evidence
requires the actual current PR HEAD and implementation ancestry; squash
carry-forward evidence instead requires the approved base as the squash
commit's sole parent and exact equality with the owner-reviewed final tree.
The documentation checker consumes the same trusted `ImplementationEvidence`
after squash and cannot pass on topology alone. Formal evidence collection and
repository revalidation reject relevant staged, unstaged, deleted, renamed, or
untracked execution drift.
Synthetic invocations reconcile per item against private `RunLedger` attempt
counts. Failure-budget/manual-stop completion is a five-scenario synthetic
matrix with independent run identities and fingerprints, not a property of one
ordinary success run. Phase-level non-action declarations are attestations
only; they cannot replace write-ahead runtime gateway evidence or grant owner
acceptance, merge safety, or route approval.

The second bounded PR #143 review-fix additionally binds audit-ready status to
the trusted repository, main runtime ledger, fixed-schema private
failure-budget scenarios, and private interrupted-reconciliation scenarios.
The reconciliation stage requires independently reconstructed COMMITTED,
UNKNOWN, and NOT_COMMITTED restart behavior. The complete public summary,
including unknown fields, must pass the shared recursive redaction scanner;
caller-supplied booleans or recomputed editable digests are not evidence.

The final bounded PR #143 closure derives actual Python identity only from the
checking process's `sys.executable`, and builder/checker share one complete,
versioned canonical public ledger projection derived from the trusted private
ledger. The generic callback is a synthetic fixture harness, not a DB, source,
provider, LLM, or media gateway and not phase-wide zero-activity proof.
Caller-supplied owner, merge, route, actual Git/Python/path/count, or test-result
JSON cannot produce protected positive claims. I1 closes the reusable temporary
source subset of `REAL_OPERATION_GATEWAY_GATE`; the gate remains due before real
source operations because no complete private real-root registry or real scope
is authorized. `VALIDATION_RECEIPT_GATE`, `OWNER_AUTHORITY_GATE`,
`POSIX_LEDGER_DURABILITY_GATE`, and `STABLE_REPLAY_GATE` retain their due-before
boundaries.

Required test groups for the I1 implementation PR:

- actual repository/Python identity and complete protected-root registry;
- Windows Cloud Files/reparse observation and recall-risk deferral;
- layered operation intents/results, read-only open, containment, and races;
- stable membership/content identity, duplicates, denominator, and private
  manifest/ledger schemas;
- independent item/byte/hash/time/failure/systemic/manual-stop budgets;
- two-real-process stop/resume lineage and tamper rejection;
- canonical projection, recursive redaction, local operator receipt, registry,
  CLI, P1 no-regression, documentation, JSON, compilation, and full non-E2E;
- browser N/A because no UI/runtime-server behavior changes.

## 16. Proposed PR Split

1. **FL1-P1 — Safety/ledger implementation:** owner-accepted and merged through
   P1-R1 / PR #143.
2. **FL1-I1 — Read-only inventory:** currently authorized for reusable tooling
   and synthetic/new temporary fixtures only; stop at Draft owner audit before
   any real source operation.
3. **FL1-E1 — Bounded Dev/Test import:** exact approved subset, isolated DB and
   storage, restart/mutation proofs, stop before classification if required.
4. **FL1-E2 — Local classification and AI tagging:** offline models, reuse-first,
   explicit coverage and manual-truth proof.
5. **FL1-V1 — Logical validation/manual acceptance:** search lifecycle, sample
   harness, owner acceptance, route decision.
6. **Future production planning:** independent scope and authorization; never
   automatic after FL1.

Phases may be combined only if the owner approves and risk/stop points remain
equivalent. No implementation PR may silently include execution authority.

## 17. Risks And Open Decisions

- The real inventory denominator and cloud-recall distribution are unknown
  until separately authorized read-only inventory.
- Stable AI-tag reuse compatibility needs exact model/threshold evidence.
- Storage capacity and runtime bounds need read-only preflight inputs.
- Full-library search/graph scalability has not been proven by planning.
- B01/B04/B08 remain SV1B-only low-value placeholder limitations; FL1 must fail
  closed if the pattern reaches real creators or reliable accounts.
- Decide whether a separate replay database is worth its cost for FL1 logical
  verification.
- Decide the exact inventory failure budget and manual sample size before
  implementation execution.

## 18. Approval Boundary

Owner approval recorded: PR #143 is accepted and merge-commit merged; the I1
reusable scanner/gateway/manifest/ledger/contract/CLI implementation and
synthetic/new temporary-fixture validation are authorized on a fresh branch
from `a2f48bdba979f579b7cd1cdd9ef541137b2479c5`. Tests and automated review do
not create owner acceptance, merge authorization, or real-source authority.

Not approved: permission to connect to an existing database, inspect production, read a
real source root, create storage, run inventory, import, classify, tag, call a
provider/LLM, download media/models, derive graph/search, or begin production.
