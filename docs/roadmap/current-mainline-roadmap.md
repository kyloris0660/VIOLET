# Current Mainline Roadmap

## Accepted Mainline

`origin/main` is owner-accepted at merge commit
`1913bd27517efc1a6007a202fc9650de4f20fab4`, the merge-commit result of PR
#145. The prior I1 final PR HEAD/tree are
`2f8d5f8ce6cde9759c530de71d4ddd1893481656` /
`8930a21bdbac037702f92bcb75bd9b8a3632a073`; frozen I1 implementation
evidence/tree are `6992e7f1e5a45857111d15da1ad0274e49008a99` /
`6ff185defb150c3751c7433ef635c00a200c44bf`.

1. R1R through SCV2-SV1B merged in PRs #132-#139.
2. SCV2-FL1 planning merged in PR #140.
3. SCV2-FL1-P1 merged in PR #141.
4. SCV2-FL1-P1-R1 was owner-accepted and merged in PR #143.
5. SCV2-FL1-I1 was owner-accepted as a synthetic/new-temporary-fixture
   foundation with use-before gates and merged in PR #144.
6. SCV2-FL1-I2 planning was owner-accepted and merged in PR #145.

The accepted `SCV2-ML1` multilingual-alias/source-metadata closure remains an
independent evidence track; it is not the current FL1 execution route or a
grant of provider, LLM, classification, or real-source authority.

PR #142 remains closed, unmerged, Draft, and non-authoritative. Its patch is an
archaeology input only.

## Current Phase And Stop Boundary

<!-- CURRENT_PHASE: SCV2-FL1-I2 -->

`SCV2-FL1-I2: Real-source Read-only Inventory Hardening and Canary Readiness`
has frozen synthetic pre-real implementation evidence on branch
`codex/scv2-fl1-i2-synthetic-pre-real-hardening`. The machine-readable fact
source is `docs/state/current-phase.json`.

```text
status=fl1_i2_pr146_bounded_correction_ready_for_owner_reaudit
planning_authorized=true
planning_completed=true
planning_approved=true
approved_planning_head=acb12c1db258fdef1d4f063b053d422e0d887abf
approved_planning_tree=fc573c7646ad5edf10c32c7712de7f27ab058a2a
merge_authorized=false
implementation_authorized=true
implementation_started=true
implementation_completed=true
target_met=false
safe_to_merge=false
route_approved=false
real_inventory_started=false
real_source_inventory_authorized=false
projected_external_cost_usd=0
active_blocker=pending_fl1_i2_bounded_followup_review_and_owner_reaudit
```

PR #144 terminal review `4897012517` covered exact final HEAD
`2f8d5f8ce6cde9759c530de71d4ddd1893481656` and produced 17 findings (13 P1,
4 P2). All threads remain historical audit records. Two documentation-governance
findings are closed by this planning PR: the checker binds the actual frozen I1
evidence commit/tree and fails closed unless `machine_verifiable_ci=false`,
`github_checks=0`, and CI authority remains false. Fourteen findings are closed
at corrected frozen implementation evidence HEAD/tree
`8a4801ad216c668ba74b2ed1ddc131de2bbad5de` /
`7cb6a34f603fa70ef2e364ac9295df885b6061bb`. The rejected
`78ccbdc69ee1bf0f51c297435b56e2be868b54e9` evidence is superseded.
Executable contract
`scv2_fl1_i2_pre_real_hardening_contract_v1` re-derives this closure without
accepting caller booleans. The result remains local operator evidence pending
the one authorized follow-up review and owner re-audit, not CI, target,
safe-to-merge, merge, route, or real-source authority.
Parent-observed child identity is
kept as a local-evidence claim boundary, not described as tamper-resistant,
OS/kernel/TPM/remote, or CI attestation.

The owner accepted I1 only as a synthetic/newly-created temporary-fixture
foundation. It does not establish real iCloud inventory, import, database,
app-storage, classification, tagging, provider, LLM, UI/runtime, or production
readiness. GitHub exposed zero checks; this is not a CI pass.

## Canonical Safety Boundary

I2 planning converges policy rather than retaining two independent Cloud/source
authorities:

1. `cloud_files.py` or one explicit shared safety module supplies canonical
   Windows Cloud, handle, and file/change-identity primitives.
2. `SourceIngestionGate` owns the unified source-kind and Cloud-availability
   policy.
3. The operation gateway owns write-ahead operation evidence, budgets,
   manifest/resume controls, and contract evidence.
4. CLI and later runtime scanners consume the same canonical decision result;
   they do not copy Cloud-flag rules.
5. Legacy `scan_and_import(dry_run=True)` is not the first real inventory
   runner: it connects to the database and reads/hashes source content, while
   dry-run only suppresses import and lacks I1 manifest/restart/evidence closure.
6. I2 implementation and validation executed only on synthetic and adversarial
   newly created temporary fixtures; the frozen result now waits for exact-HEAD
   owner audit.

The enforced order is: exact plan owner approval; separate I2 implementation
authorization; synthetic-only I2 implementation closing all 14 gates; I2 owner
audit and merge; separate `FL1_I3_REAL_SOURCE_SCOPE_GATE`; then a bounded
real-source canary. Exact private source identity, protected roots, budgets,
no-hydration policy, and stop conditions belong only to I3 and do not gate the
start of authorized synthetic I2 work.

The owner accepted exact planning HEAD/tree
`acb12c1db258fdef1d4f063b053d422e0d887abf` /
`fc573c7646ad5edf10c32c7712de7f27ab058a2a` under review `4907783329`.
Its P1 thread `PRRT_kwDOSTBMB86YRuq7` is classified
`closed_in_owner_acceptance_projection_exact_binding_contract`: the checker
re-derives the tree, requires ancestry, and permits only the governance
projection allowlist after the accepted plan. PR #145 merged at
`1913bd27517efc1a6007a202fc9650de4f20fab4`; G0 now closes the five accepted
post-merge governance-entry findings. Synthetic implementation authority is
true, but safe-to-merge, merge, real-source, and data authority remain false.

## Delivery Sequence

1. **SCV2-FL1-I2 - Pre-real hardening.** Close all real-source use-before
   gates with executable contracts and negative temporary-fixture tests. Merge
   and stop; no real source is accessed.
2. **SCV2-FL1-I3 - Bounded real-source canary.** Requires separate exact
   private source identity/scope, protected roots, budgets, Cloud/no-hydration
   policy, and stop conditions. Enumerate metadata first; only after owner audit
   may a small AVAILABLE/HYDRATED sample be hashed and structurally validated.
3. **SCV2-FL1-I4 - Full-library read-only inventory.** Freeze one manifest cut,
   separate discovery/metadata/hash/structure coverage, and explicitly account
   for every deferred or failed disposition. The review-pack route remains
   provisional until owner audit.
4. **SCV2-FL1-E1 - Isolated import rehearsal.** Use fresh isolated test DB and
   storage, staging-first atomic copy/verification, and only I4
   content-verified membership. Writes require separate authorization.
5. **SCV2-FL1-E2 - Local classification and AI tagging.** Begin only after
   import closure; use offline/cache-only models, weak-evidence proper nouns,
   separate ledgers/budgets, and disabled translation/provider/LLM routes.
6. **SCV2-FL1-V1 - Product and owner validation.** Exercise real browser
   product flows, scale, failure/resume cases, and manifest-bound manual
   samples. V1 cannot authorize production import, watcher, or automatic sync.

External source/provider/model/media data-plane network operations remain zero.
Authorized Git/GitHub governance control-plane operations, including fetch,
push, PR maintenance, and review requests, are allowed and have occurred; they
are not provider or data execution.

## Remote Sync Preflight Policy

Fetch and authenticate the trusted remote before comparing protected bases. A
local base with no local-only commits that is only behind may be fast-forwarded
with `--ff-only`. Divergence, tracked drift, behavior-affecting untracked code
or configuration, or any need to reset, rebase, force, overwrite, or delete is
fail closed. Preserve unrelated untracked and ignored user artifacts.

The entry sync for this phase was
`preflight_remote_sync=self_healed_by_fast_forward`; that is an operator
classification, not executable contract or CI authority.

## Durable Links

- `docs/state/current-phase.json`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/phase-contracts.md`
- `docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`
- `docs/roadmap/archive/project-roadmap-through-scv2-sv1b.md`
- `docs/development/agent-runbook.md`
- `docs/test-workflow.md`
