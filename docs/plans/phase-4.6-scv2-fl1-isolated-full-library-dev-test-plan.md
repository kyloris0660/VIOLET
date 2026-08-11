# SCV2-FL1 Isolated Full-Library Dev/Test Plan

## 1. Current Decision And Authority

This document is the canonical public-safe FL1 route plan. The current phase is
`SCV2-FL1-I2: Real-source Read-only Inventory Hardening and Canary Readiness`.
Its status is
`fl1_i2_planning_governance_pr_corrected_ready_for_owner_reaudit`.

PR #144 merged SCV2-FL1-I1 at merge commit
`8955b95e91630d4c5e18e1e2ca252b19754c81d5`. The owner accepted its final
HEAD/tree `2f8d5f8ce6cde9759c530de71d4ddd1893481656` /
`8930a21bdbac037702f92bcb75bd9b8a3632a073` and frozen implementation
evidence/tree `6992e7f1e5a45857111d15da1ad0274e49008a99` /
`6ff185defb150c3751c7433ef635c00a200c44bf` only as a synthetic and newly
created temporary-fixture foundation with use-before gates.

That acceptance does not prove real iCloud/source inventory, import, database,
app storage, classification, AI tagging, localization, provider/LLM behavior,
media download, Stable Replay, UI/runtime, or production readiness. Terminal
review `4897012517` recorded 17 findings at the final HEAD (13 P1, 4 P2).
GitHub exposed zero checks; `github_checks=0` is not CI pass evidence and
`machine_verifiable_ci=false` remains mandatory.

Current authority is limited to governance, public-safe documentation, and the
I2/I3/I4/E1/E2/V1 route plan. It does not authorize I2 implementation or any
source/data/runtime operation:

```text
planning_authorized=true
planning_completed=true
planning_approved=false
merge_authorized=false
implementation_authorized=false
implementation_started=false
target_met=false
safe_to_merge=false
route_approved=false
real_inventory_started=false
real_source_inventory_authorized=false
source_root_access_authorized=false
database_access_authorized=false
app_storage_write_authorized=false
import_authorized=false
classification_or_tagging_execution_authorized=false
provider_or_llm_authorized=false
media_or_thumbnail_download_authorized=false
stable_replay_authorized=false
production_authorized=false
projected_external_cost_usd=0
active_blocker=pending_fl1_i2_plan_owner_audit
```

## 2. Governing Principles

1. Current-phase truth comes from `docs/state/current-phase.json`; this plan is
   its durable human projection.
2. Every implementation, real-source, write-bearing, model/provider, browser,
   or production stage requires a distinct owner authorization.
3. Public documents contain aggregates and public-safe identities only—never a
   private root, path, filename, file contents, keyed label, or content
   fingerprint.
4. Membership is frozen by a manifest cut. A path, row ID, order, or filename
   never establishes content identity.
5. Every discovered item receives one explicit terminal disposition. Deferred
   Cloud files are not silently counted as exact duplicates or content-verified.
6. Local receipts are operator evidence, not CI, owner, route, or
   tamper-resistant attestation.
7. Historical PR #144 threads remain audit records. This route does not reply,
   resolve, reopen, or continue the I1 repair loop.
8. External source/provider/model/media data-plane network operations remain
   zero. Authorized Git/GitHub governance control-plane fetch, push, PR, and
   review operations are allowed and have occurred; they are not provider or
   data execution.

I2 implementation may start only after the owner approves the exact I2 plan
evidence and separately authorizes implementation restricted to synthetic and
adversarial newly created temporary fixtures. Real source/iCloud, DB,
app-storage, import, provider/model/media, and production authority must all
remain false. The 14 engineering findings are I2 implementation deliverables,
not pre-existing results required before this separately authorized coding can
begin.

## 3. PR #144 Terminal Review Use-Before Register

The 17 findings are preserved in full and classified for the next safe use.

| # | Severity | Finding | Classification |
|---:|:---:|---|---|
| 1 | P1 | Scrub Git control variables before trusted invocations | Must close during I2 before I2 completion/merge or I3: sanitize `GIT_DIR`, `GIT_WORK_TREE`, and equivalent Git control environment across I2 runtime, receipts, and contracts. The current checker-only scrub does not close this overall I2 delivery. |
| 2 | P1 | Validate the parent-observed child identity | Claim boundary: local parent/child provenance is not tamper-resistant, OS/kernel/TPM/remote, or CI attestation. |
| 3 | P1 | Recheck recall attributes before final resolution | Must close during I2 before I2 completion/merge or I3: bind Cloud attributes and final opening to the same object/no-recall decision. |
| 4 | P1 | Allow interrupted attempts before corrupt-media closure | Must close during I2 before I2 completion/merge or I3: interrupted attempts and corrupt-media terminal accounting must remain distinct and complete. |
| 5 | P2 | Enforce the deadline around blocking file operations | Must close during I2 before I2 completion/merge or I3: potentially blocking open/read/hash/structure validation runs in a terminable worker. |
| 6 | P1 | Bind the receipt to one unchanged HEAD | Must close during I2 before I2 completion/merge or I3: validation receipt binds identical pre/post execution HEAD. |
| 7 | P1 | Re-derive the adapter policy during contract validation | Must close during I2 before I2 completion/merge or I3: adapter policy is reconstructed from trusted configuration. |
| 8 | P2 | Stop at the configured failure maximum | Must close during I2 before I2 completion/merge or I3: correct the maximum-failure off-by-one boundary. |
| 9 | P1 | Pin the frozen remediation commit and tree | Closed in this governance PR: documentation checker uses a fixed trusted Git executable, explicit repository root, scrubbed `GIT_*` environment, disabled replace objects/hooks/fsmonitor/caller config, and pins the actual I1 evidence commit/tree. |
| 10 | P1 | Reject CI authority in documentation state | Closed in this governance PR: checker requires `machine_verifiable_ci=false`, `github_checks=0`, and no CI authority. |
| 11 | P1 | Include a change identity in file signatures | Must close during I2 before I2 completion/merge or I3: carry Windows file identity and change identity. |
| 12 | P1 | Reject hard-linked files that alias protected data | Must close during I2 before I2 completion/merge or I3: define hard-link, reparse, symlink, and path-alias policy. |
| 13 | P1 | Confine private artifact reads as well as writes | Must close during I2 before I2 completion/merge or I3: private-artifact reads are no-follow and confined. |
| 14 | P1 | Enumerate directories through a verified no-follow handle | Must close during I2 before I2 completion/merge or I3: enumerate every member from the same verified, no-follow, identity-bound directory handle. Identity-before/after is supplemental drift evidence only; path-based `os.scandir()` plus a post-check cannot close this gate. On Windows, implement a safe same-handle primitive or fail closed. |
| 15 | P1 | Reconcile intents from ended failed invocations | Must close during I2 before I2 completion/merge or I3: recover residual INTENT records from terminated failed invocations. |
| 16 | P2 | Validate media structure beyond boundary markers | Must close during I2 before I2 completion/merge or I3: bounded structural validation must exceed first/last marker checks. |
| 17 | P2 | Handle runtime-context failures in scanner CLI | Must close during I2 before I2 completion/merge or I3: emit a stable privacy-safe JSON error envelope. |

Findings 9 and 10 are governance closures, not retrospective code repairs.
Findings 1, 3-8, and 11-17 are 14 implementation delivery gates with canonical
classification
`must_close_during_i2_before_i2_completion_merge_or_i3`. All must close before
`implementation_completed`, `target_met`, `safe_to_merge`, merge, I3, or any
real source/iCloud listing/stat/attribute/open/read/hash/structure validation.
Finding 2 limits claims and should not turn a personal local inventory tool
into an adversarial forensics system unless a future owner-approved threat
model requires that.

## 4. Canonical Architecture Convergence

I2 must not preserve two independent Cloud/source policy authorities. Before
implementation, audit and design the convergence across:

```text
backend/app/utils/cloud_files.py
backend/app/services/source_ingestion_gate.py
backend/app/utils/local_library_scanner.py
scripts/fl1_i1_operation_gateway.py
scripts/fl1_i1_inventory.py
scripts/phase_contracts/fl1_i1_contract.py
```

The intended architecture is:

1. `cloud_files.py`, or one explicitly named shared safety module, supplies
   canonical Windows Cloud attributes, safe handles, object/file identity, and
   change-identity primitives.
2. `SourceIngestionGate` owns the unified source-kind and Cloud-availability
   policy and emits one canonical decision result.
3. The I1/I2 operation gateway owns write-ahead operation records, budgets,
   manifest and resume control, and contract evidence.
4. The CLI and future runtime scanner consume the same canonical decision
   result rather than copying Cloud flags or availability logic.
5. The legacy `scan_and_import(dry_run=True)` path is not the first full
   read-only inventory runner. It creates a DB dependency, reads and hashes
   source content, treats dry-run as “do not import” rather than metadata-only,
   and lacks I1 manifest/restart/evidence closure.
6. I2 implementation and tests, if separately approved, use only synthetic and
   adversarial newly created temporary fixtures. They do not touch a real
   library.

The design review must assign exactly one authority for each of: source-kind
classification, Cloud availability, no-recall decision, handle identity,
policy/config derivation, operation admission, manifest membership, receipt
closure, and public projection.

## 5. Threat Model

### In scope

- Configuration mistakes and selection of the wrong path.
- Normal iCloud/Cloud Files concurrency and availability changes.
- Files or directories disappearing, changing, or being replaced during a run.
- Reparse points, symlinks, hard links, and path aliases.
- Worker or parent crash, stale artifacts, and resume/recovery.
- Blocking open/read/hash/structure-validation operations.
- Budget, disk, runtime, and evidence drift.
- Operator or agent error.

### Out of scope

- OS or kernel compromise.
- A malicious same-account process actively replacing every object between
  every syscall.
- TPM, remote, or hardware attestation.
- Presenting a local operator receipt as CI or tamper-resistant evidence.

The out-of-scope line limits claims; it does not waive containment, race,
identity, no-follow, or normal concurrent-change controls.

## 6. SCV2-FL1-I2 - Pre-Real Hardening

### Objective

Produce the design and—only after separate approval—the implementation needed
to make a future bounded real-source canary reviewable and fail closed.

### Inputs and execution boundary

- Synthetic and adversarial newly created temporary fixtures only.
- No real source path discovery, metadata observation, open, read, or hash.
- No DB/app-storage, import, classification/tagging, provider/LLM, media
  download, server, Stable Replay, or production operation.

### Required design and implementation gates

- The 14 implementation findings in Section 3 are closed with focused negative
  tests.
- Canonical source/Cloud policy has one consumer-facing decision result.
- Trusted configuration and repository identity are re-derived at use time.
- Membership, operation admission, budget, worker, receipt, and projection
  records bind one run identity and one unchanged repository HEAD.
- File/directory handle identity and change identity cover mutable traversal,
  aliases, reparse points, and hard links.
- Directory membership is obtained from the same verified, no-follow,
  identity-bound directory handle. Identity-before/after may add drift evidence
  but cannot undo an already out-of-bound path listing and cannot replace
  same-handle enumeration.
- Blocking I/O has parent-enforced termination and bounded cleanup evidence.
- INTENT/recovery and interruption/corruption accounting close exactly once.
- CLI failures remain stable, privacy-safe JSON with no private path leakage.
- A registered executable I2 phase contract and complete positive/negative
  temporary-fixture suite are delivered.

### Exit

Before this exit, all 14 delivery gates must be closed and the executable
contract must prove them. Merge the separately approved implementation and
stop. I3 and all real-source activity remain false until another owner decision
names the exact scope and budgets.

## 7. SCV2-FL1-I3 - Bounded Real-Source Inventory Canary

I3 requires a separate authorization containing all of:

- exact private source identity and bounded scope;
- complete protected-root registry and source role;
- item/byte/time/disk/failure budgets;
- Cloud/no-hydration policy;
- explicit stop conditions and owner checkpoints.

Execution order is fixed:

1. Perform bounded metadata-only enumeration with no content open/read/hash.
2. Stop for owner audit of counts, policy, dispositions, and privacy-safe
   evidence.
3. Only with the next explicit authorization, select a small stratified sample
   from objects already proven AVAILABLE/HYDRATED and perform bounded
   hash/structure validation.
4. Always defer recall-risk or ambiguous Cloud objects; never trigger
   hydration.
5. Keep source mutation, DB access, app-storage access, and import at zero.

No public artifact may include a real root, path, filename, content fragment,
or content fingerprint. A successful I3 canary is not full-library readiness.

## 8. SCV2-FL1-I4 - Full-Library Read-Only Inventory

I4 is another separately approved execution phase. It must:

- freeze a manifest cut before content work;
- enumerate the authorized scope fully but hash only safely available content;
- report discovered, metadata, hash, and structure-validation coverage as
  separate denominators;
- assign explicit recall-risk, unsupported, corrupt, unreadable, missing,
  changed, and eligible dispositions;
- never count an un-hashed Cloud-deferred item as an exact duplicate;
- put newly discovered or changed files into a later delta run rather than
  mutating current membership;
- report capacity, runtime, failure distribution, staging requirements, and an
  E1 route recommendation;
- produce a privacy-safe ChatGPT review pack whose route decision remains
  provisional until owner audit.

Every item closes exactly once. Aggregate sums must reconcile to the frozen
manifest denominator; incomplete hash or structure coverage remains explicit.

## 9. SCV2-FL1-E1 - Isolated Import Rehearsal

E1 begins only after accepted I4 closure and separate write authorization.

- Create a fresh isolated test DB and storage root; accepted evidence and
  production DB/storage remain read-only.
- Copy into staging, verify content and policy, then atomically finalize.
- Consume only I4 content-verified eligible membership.
- Reconcile on stable/content fingerprints, never path, filename, DB row ID,
  or input order.
- Record independent intent, completion, failure, resume, rollback, and budget
  evidence.
- Stop on membership drift, protected-root conflict, content mismatch,
  insufficient recovery space, or destination identity ambiguity.

No E1 write is implied by I2/I3/I4 approval.

## 10. SCV2-FL1-E2 - Local Classification And AI Tagging

E2 begins only after E1 import closure and a separate authorization.

- Use offline/cache-only local models; model/provider downloads remain off.
- Define anime and unknown eligibility before classification.
- Keep general/meta tags separate from proper-noun identity evidence.
- Treat AI proper-noun suggestions only as weak evidence; never as confirmed
  assignment or user truth.
- Keep translation workers, providers, and LLMs disabled by default.
- Use independent ledgers, budgets, recovery, and owner acceptance.

E2 must not mutate SourceConcept or Entity truth by inference, and does not
authorize broad localization or provider enrichment.

## 11. SCV2-FL1-V1 - Product And Owner Validation

V1 is a controlled validation phase after E1/E2 closure. It covers:

- search, filter, media-detail, and gallery lifecycle;
- scale and performance;
- duplicate, Cloud-deferred, corrupt, and resume samples;
- controlled real browser validation against the exact test runtime;
- a manifest-bound manual owner sample.

The exit is an owner decision on whether to plan production separately.
Production import, watcher, automatic sync, scheduling, or background ingestion
cannot be inferred from V1 success.

## 12. Coverage, Disposition, And Evidence Model

I3/I4 plans must distinguish:

```text
discovered_coverage
metadata_coverage
hash_coverage
structure_validation_coverage
```

Allowed terminal inventory dispositions must be explicit and mutually
exclusive, including at least:

```text
eligible_content_verified
cloud_recall_risk_deferred
unsupported
corrupt
unreadable
missing
changed_during_run
budget_deferred
policy_deferred
interrupted
```

An exact-duplicate claim requires content verification for both compared
objects under the accepted fingerprint policy. Metadata equality is not enough.
Public projections expose aggregates and keyed labels only; private evidence
retains item-level identities under confined, no-follow access.

## 13. Budgets And Stop Conditions

Each executable phase contract must bind explicit limits for:

- discovered items and directories;
- metadata observations;
- content-open attempts, bytes, and hash operations;
- structure validations;
- wall-clock and per-operation time;
- failures, interruptions, retries, and concurrent workers;
- private-artifact and staging disk use;
- external cost, which remains zero unless separately authorized.

Fail closed on protected-root ambiguity, path/handle identity mismatch,
manifest/config/HEAD drift, Cloud recall risk, deadline escape, receipt
incompleteness, ledger collision/corruption, budget exhaustion, or privacy
projection leakage. Bounded per-item failures may continue only when the
approved contract explicitly permits them and the failure maximum is not
crossed.

## 14. Executable Contracts And Validation

The I2 planning PR changes no I1 executable and does not claim an I2 executable
contract already exists. A later I2 implementation must register and test a
contract that verifies the canonical decisions, all 14 delivery gates,
protected/private evidence, lifecycle closure, budgets, public projection, and
same-HEAD receipt semantics.

Validation for this governance/planning lifecycle is limited to:

1. approved repository-venv Python identity preflight when Python runs;
2. `scripts/check_documentation_state.py --check`;
3. focused documentation, generated-handoff, and governance tests;
4. tracked JSON parsing;
5. `git diff --check` and `git diff --cached --check`;
6. changed-path, privacy/redaction, and forbidden-scope audits;
7. clean intended tracked/staged state and local/remote branch equality.

Raw full non-E2E, browser/E2E, server, DB, source/iCloud, model/provider/LLM,
and media validation are not required and are not authorized for this PR.

## 15. Approval And Stop Boundaries

The corrected current PR may be considered only for owner re-audit of the plan.
It cannot
claim `planning_approved`, `implementation_authorized`, `target_met`,
`safe_to_merge`, or `route_approved`.

The acceptance lifecycle is fixed:

1. present the corrected exact planning evidence commit/tree for owner
   re-audit;
2. only after acceptance and separate authorization, create one governance-only
   projection commit that binds that exact evidence, records the owner decision,
   and sets planning approval, safe-to-merge, and merge authorization while
   implementation and real-source authority remain false;
3. change no accepted plan content in that projection;
4. only then permit an expected-head merge;
5. require separate I2 implementation authorization, synthetic-only closure of
   all 14 gates, I2 owner audit and merge, then the separate
   `FL1_I3_REAL_SOURCE_SCOPE_GATE` before a bounded canary.

This correction must not create that acceptance projection or set positive
authority. After the corrected final HEAD is frozen, request exactly one
replacement Codex review and stop. Do not repair its findings, request a third
review, reply to or resolve threads, merge, start I2 implementation, or begin
I3. The required owner checkpoint is:

```text
SCV2_FL1_I2_PLANNING_GOVERNANCE_PR_CORRECTED_READY_FOR_OWNER_REAUDIT
```
