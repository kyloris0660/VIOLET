# V.I.O.L.E.T. Project Roadmap

## Project Vision

V.I.O.L.E.T. is a personal, local-first anime and illustration library. It
combines safe ingestion, local classification and AI tagging, Danbooru-style
retrieval, Chinese display localization, and provenance-preserving source
evidence without treating weak AI or provider signals as user truth.

## Current Active Roadmap

<!-- CURRENT_PHASE: SCV2-FL1-I2 -->

The authoritative current state is `docs/state/current-phase.json`.

PR #144 merged at `8955b95e91630d4c5e18e1e2ca252b19754c81d5` after owner
acceptance of SCV2-FL1-I1 strictly as a synthetic and newly created
temporary-fixture inventory foundation. The accepted final HEAD/tree are
`2f8d5f8ce6cde9759c530de71d4ddd1893481656` /
`8930a21bdbac037702f92bcb75bd9b8a3632a073`; frozen implementation
evidence/tree are `6992e7f1e5a45857111d15da1ad0274e49008a99` /
`6ff185defb150c3751c7433ef635c00a200c44bf`.

That acceptance is deliberately narrow. It does not prove a real iCloud or
source inventory, import, DB/app-storage behavior, classification, AI tagging,
provider/LLM use, media download, UI/runtime behavior, or production readiness.
Terminal review `4897012517` recorded 17 unresolved, non-outdated findings at
the final HEAD (13 P1, 4 P2), and GitHub exposed zero checks. Zero checks is not
a machine-verifiable CI pass.

Current phase `SCV2-FL1-I2` is governance and route planning only:

```text
current_status=fl1_i2_planning_governance_pr_corrected_ready_for_owner_reaudit
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

## Accepted Mainline Sequence

1. R1R through SCV2-SV1B / PRs #132-#139.
2. SCV2-FL1 planning / PR #140.
3. SCV2-FL1-P1 / PR #141.
4. SCV2-FL1-P1-R1 / PR #143.
5. SCV2-FL1-I1 synthetic foundation / PR #144, owner-accepted and merged at
   `8955b95e91630d4c5e18e1e2ca252b19754c81d5`.

PR #142 remains closed, unmerged, and non-authoritative.

## FL1 Route

### I2 - Pre-real hardening

Use only synthetic/adversarial temporary fixtures. Converge canonical source
and Cloud policy, close the 14 implementation delivery gates before I2
completion, target, safe-to-merge, merge, or I3, produce an executable contract
and complete negative suite, then stop after merge pending real canary
authorization. The gates are I2 deliverables; they are not outcomes required
before exact-plan approval plus separate authorization permits synthetic-only
I2 coding.

The sequence is exact plan owner approval, separate I2 implementation
authorization, synthetic-only closure of all 14 gates, I2 owner audit and
merge, separate `FL1_I3_REAL_SOURCE_SCOPE_GATE`, then bounded canary. I3's
private source identity, protected roots, budgets, no-hydration policy, and stop
conditions do not gate synthetic I2 implementation.

Corrected planning evidence remains unaccepted until owner re-audit. Acceptance
then requires a separately authorized governance-only projection commit that
binds the exact approved planning commit/tree and records the owner decision
without changing plan content. It may set planning approval and merge authority
but must keep implementation and real-source authority false before an
expected-head merge.

### I3 - Bounded real-source inventory canary

Require a separate exact private source scope, protected-root registry,
budgets, no-hydration policy, and stop conditions. Perform bounded metadata-only
enumeration, stop for owner audit, then—only if separately authorized—hash and
structurally validate a small stratified AVAILABLE/HYDRATED sample. Recall-risk
objects always defer. Source mutation, DB, app-storage, and import remain zero.

### I4 - Full-library read-only inventory

Freeze manifest membership. Enumerate the full scope but hash only safely
available objects. Report discovered, metadata, hash, and structure-validation
coverage separately; record recall-risk, unsupported, corrupt, unreadable, and
missing dispositions explicitly. Delta files join a later run. Produce
capacity/time/failure/staging evidence, an E1 route decision, and a privacy-safe
review pack whose result remains provisional until owner audit.

### E1 - Isolated import rehearsal

Use a fresh isolated test DB/storage and staging-first copy/hash verification
with atomic finalization. Consume only I4 content-verified eligible membership
and reconcile on stable/content fingerprints, never path or DB row ID. All
writes require separate authorization; production and accepted evidence stores
remain read-only.

### E2 - Local classification and AI tagging

Begin only after import closure. Use offline/cache-only models, explicit
anime/unknown eligibility, separate general/meta and proper-noun evidence, weak
AI proper-noun authority, disabled translation/provider/LLM routes, and
independent ledgers, budgets, and recovery.

### V1 - Product and owner validation

Validate search/filter/media-detail/gallery lifecycle, scale, duplicate,
cloud-deferred, corrupt, and resume scenarios in a controlled real browser,
plus a manifest-bound owner sample. V1 ends at an owner decision about a
separate production plan; it cannot imply production import, watcher, or sync.

## Durable Boundaries

- No real source/iCloud path may be listed, stated, observed, opened, read, or
  hashed in I2 planning or implementation.
- No existing or production DB/app-storage access, import, mutation, replay,
  provider, LLM, media, model download, server, or production execution.
- Public artifacts contain no private roots, paths, filenames, contents, or
  content fingerprints.
- Local receipts remain `local_operator_receipt`; parent-observed child
  identity is not tamper-resistant or OS/TPM/CI attestation.
- I1 terminal threads remain historical: no reply, resolve, reopen, or repair
  loop is implied by this route.

## Remote Sync Preflight Policy

Fetch the trusted remote before comparing bases. A safe local base that is only
behind and has no local-only commit may be fast-forwarded with `--ff-only`.
Divergence, unsafe local-only commits, tracked drift, behavior-affecting
untracked code/configuration, failed fast-forward, or any need for reset,
rebase, force, rewrite, overwrite, or deletion remains fail closed. Preserve
unrelated untracked and ignored user artifacts.

## Documentation Map

- Current route: `docs/state/current-phase.json`
- Generated handoff: `docs/current-handoff.md`
- Current mainline detail: `docs/roadmap/current-mainline-roadmap.md`
- FL1 plan: `docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`
- Executable contract boundary: `docs/phase-contracts.md`
- Historical roadmap through SV1B:
  `docs/roadmap/archive/project-roadmap-through-scv2-sv1b.md`
- Detailed agent runbook: `docs/development/agent-runbook.md`
- Test workflow: `docs/test-workflow.md`

## Governance

Current facts change in `docs/state/current-phase.json` first. Active roadmaps
and the generated handoff must agree with its phase marker. Historical reports
and archives preserve captured semantics and are not rewritten when the active
route advances.
