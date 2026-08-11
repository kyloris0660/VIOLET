# V.I.O.L.E.T. Project Roadmap

## Project Vision

V.I.O.L.E.T. is a personal, local-first anime and illustration library. It
combines safe ingestion, local classification and AI tagging, Danbooru-style
retrieval, Chinese display localization, and provenance-preserving source
evidence without treating weak AI or provider signals as user truth.

## Current Active Roadmap

<!-- CURRENT_PHASE: SCV2-FL1-I1 -->

The authoritative current state is `docs/state/current-phase.json`.

`SCV2-FL1-I1: Read-only Inventory` is the current phase on branch
`codex/scv2-fl1-i1-read-only-inventory-v2`. It began from exact accepted main
`a2f48bdba979f579b7cd1cdd9ef541137b2479c5`, the merge-commit result of
owner-accepted PR #143. PR #143 final HEAD `228983f…` and implementation
evidence `a631160…` are preserved as ancestors.

Final review `4890771735` was owner-adjudicated as five valid use-before
constraints: trusted complete protected roots, merge-topology-safe evidence,
distinct restart invocation provenance, trusted actual Git HEAD binding, and
honest local validation receipts. These constraints are carried into I1; the
owner decision remains a direct human GitHub fact and is not machine-generated
authority.

The current route authorizes the complete reusable I1 scanner, manifest,
operation gateway, private ledger, cross-process resume harness, canonical
public projection, executable contract, CLI, and safety regression suite. All
source-like operations must target only synthetic or newly created temporary
fixtures. `authorized_read_only_source` is an implemented code mode, not
permission to access a real source.

Draft PR #144 review `4891695875` at exact HEAD
`b65c7b84adfe45b92f85dfb72d60920bd1fb0ad3` produced 18 current-I1 findings
(15 P1, 3 P2). The former evidence
`5194a484d0d8fb8dd5e0697cd61054f596aee5ec` (tree
`9b30ba024beb6fcd58709e707d7879887ad7c081`) is preserved as historical and
superseded. The owner authorized one and only one bounded synthetic/temp-fixture
remediation round. That round is frozen at replacement implementation/test
evidence `6992e7f1e5a45857111d15da1ad0274e49008a99` (tree
`6ff185defb150c3751c7433ef635c00a200c44bf`); all 18 adjudications have focused
regression evidence. Real inventory remains unauthorized.

Current state remains:

```text
target_met=false
safe_to_merge=false
route_approved=false
current_status=fl1_i1_bounded_remediation_ready_for_owner_audit
manual_acceptance_status=pending_i1_bounded_remediation_owner_audit
bounded_remediation_round=1_of_1
next_phase_started=true
real_inventory_started=false
real_source_inventory_authorized=false
database_access_authorized=false
app_storage_write_authorized=false
provider_or_llm_authorized=false
production_authorized=false
active_blocker=pending_i1_bounded_remediation_owner_audit
```

PR #142 is closed, unmerged, and non-authoritative. Its patch is inspected only
through a bounded archaeology matrix. No PR #142 governance state, P1
remediation, commit, or wholesale code patch is reused.

## Accepted Mainline Sequence

1. R1R / PR #132.
2. SCV2-A1R / PR #133.
3. SCV2-R2 / PR #134.
4. SCV2-R2R / PR #135.
5. SCV2-ML1 / PR #136.
6. SCV2-ML2 / PR #137.
7. SCV2-SV1-A / PR #138.
8. SCV2-SV1B / PR #139.
9. SCV2-FL1 planning / PR #140.
10. SCV2-FL1-P1 / PR #141.
11. SCV2-FL1-P1-R1 / PR #143, owner-accepted and merge-commit merged at
    `a2f48bdba979f579b7cd1cdd9ef541137b2479c5`.

## Current FL1 Route

1. Preserve replacement implementation evidence `6992e7f1...` and its exact
   validation while retaining `5194a484...` as historical superseded evidence.
2. Request the one authorized terminal post-remediation review and stop for
   owner audit without another fix loop.
3. A later owner decision must name exact source identity, scope, protected
   roots, budgets, Cloud policy, and stop conditions before any real inventory.
4. Import, local classification/AI tagging, database/app-storage use, and FL1-E1
   remain later independent routes.

## Durable Boundaries

- No real source/iCloud listing, stat, attribute observation, opening, read,
  hash, hydration, copy, rename, delete, or mutation.
- No existing or production database/app-storage access or schema change.
- No import, classification, AI tagging, localization, provider/LLM/media,
  Stable Replay, SourceConcept graph/search mutation, Entity/truth, server, or
  production execution.
- Private paths, filenames, content fingerprints, and keyed-label secrets remain
  only in ignored/private artifacts and never enter public summaries or PR text.
- Local test evidence is `local_operator_receipt`; it cannot claim CI or owner
  authority.

## Remote Sync Preflight Policy

Fetch the trusted remote before comparing bases. A safe local base that is only
behind and has no local-only commit is fast-forwarded with `--ff-only` and the
task continues. Divergence, unsafe local-only commits, tracked drift,
behavior-affecting executable/module/config/symlink drift, failed fast-forward,
or any need to reset, rebase, force, rewrite, overwrite, or delete state remains
fail closed. Preserve
unrelated untracked and ignored non-executable user artifacts.

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
