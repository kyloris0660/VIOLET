# Current Mainline Roadmap

## Accepted Mainline

<!-- CURRENT_PHASE: SCV2-PX1 -->

Trusted remote preflight established:

```text
origin/main=8a825bcdd12f76d1c2c396b7039bd9e326cd63dc
origin/main_tree=9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71
pr146_accepted_head=914d746c3548241a99333393daa88caefd8b2337
pr146_accepted_tree=9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71
accepted_head_is_merge_ancestor=true
accepted_tree_equals_merge_tree=true
post_merge_commit_audit_count=0
```

This corrects the former I2 pending-merge projection. PR #146 is merged, while
its final review `5031131564` still has ten unresolved, non-outdated findings.
Those findings remain exact-gate deferred debt; merge is not evidence that they
were fixed or adjudicated away.

## Current Phase And Stop Boundary

```text
current_status=SCV2_PX1_BOUNDED_CORRECTION_READY_FOR_FINAL_OWNER_MERGE_AUDIT
contract_id=scv2_px1_pixiv_metadata_consolidation_contract_v1
public_schema=violet.scv2-px1-pixiv-metadata-summary.v1
target_met=true
safe_to_merge=false
route_approved=false
owner_accepted=false
merge_authorized=false
real_source_authorized=false
real_provider_authorized=false
full_import_authorized=false
production_authorized=false
machine_verifiable_ci=false
active_blocker=pending_scv2_px1_final_owner_merge_audit
```

PX1 is an actual implementation phase, but its data plane is restricted to new
repository-owned synthetic fixtures and task-owned temporary SQLite databases.
The phase ends at one normal PR and exact-head owner audit. It cannot merge,
start PX2, or consume any real source/provider/database/media authority.

## PX1 Vertical Slice

The required durable chain is:

```text
repository-owned synthetic Pixiv/gallery-dl metadata
  -> existing canonical normalization and lifecycle
  -> existing SourceMetadataRecord and observation models
  -> canonical deterministic Pixiv work/page aggregate
  -> existing SourceConcept-compatible deterministic signal projection
  -> stable public-safe JSON and replay fingerprint
```

The canonical aggregate keeps creator ID as the stable identity anchor and
represents account name, display name, title, and tags as mutable observations.
It exposes missing, conflict, retryable, terminal, unsupported, and page-mismatch
states explicitly. Business identity excludes database IDs, paths, filenames,
timestamps, run order, and private provider payloads.

PX1 outputs signal input only. Cluster materialization, LLM adjudication,
Entity promotion, and provider-derived `media_tags` truth remain absent.

## Fixed Three-Phase Route

1. `SCV2-PX1` — Pixiv metadata consolidation and runnable offline vertical
   slice. The single owner-adjudicated bounded correction is complete on
   existing PR #147 and stops at final owner merge audit.
2. `SCV2-PX2` — deterministic clustering, identity, candidate explanation,
   ambiguous queue, controlled sample evaluation, and a persistable cluster
   result. Not started.
3. `SCV2-PX3` — real source/provider, any necessary migration, persistence,
   API/UI, dry-run/apply, idempotency, backup/recovery, canary, rollback, and
   the final full-library import checkpoint. Not started.

No other near-term functional phase is active. Safety checks remain internal
gates. `phase-4.5-PX1 is historical`; the historical runner may retain a thin
compatibility projection but cannot become the new production authority.

## Deferred Due-Gate Policy

PR #146 final-review findings stay attached to these future gates:

- listed-member validation, initial enumeration budget, operation admission
  caps, and depth re-derivation: before real-source enumeration or operation;
- JPEG/VP8 content authority: before a positive real-source content-verified
  claim;
- event-time lower bound, nonnegative byte accounting, failed-receipt
  completion, and evidence pre-parse budget: before I2 receipt/positive
  authority reuse;
- dynamic-loader policy: before POSIX, remote-CI, or hostile-local positive
  authority;
- whole-venv supply-chain binding: before CI, tamper-resistant, or reproducible
  environment claims;
- real-source scope, owner authority, receipt, durability, identity-attestation,
  and real-data Stable Replay gates remain independently preserved.
- hostile workspace confinement, including symlink/component-swap races,
  fixed-name evidence symlinks, and SQLite URL-sensitive path characters, is
  due at `SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE` before any caller-
  supplied path, untrusted remote-CI evidence, existing DB/app-storage access,
  real-source canary, or production use.

None of these gates is a reason to access real data during PX1, and none is
marked closed by synthetic PX1 evidence.

## Validation Route

PX1 validation uses the approved repository Python and includes compile,
documentation state, focused Pixiv/SourceConcept compatibility, aggregate and
signal mutation tests, a task-owned temporary-database vertical slice,
deterministic replay, executable contract re-derivation, privacy scans, tracked
JSON parsing, diff checks, and one complete non-E2E suite. Server, browser,
E2E, gallery-dl network, Pixiv network, real library, and production tests are
forbidden.

The corrected implementation checkpoint is HEAD
`782360c04da475cac98f928038f34c5a337c814f`, tree
`1e2ee96bd7cdf40a689fd53bbe90519f5d8b95f3`. Its local receipt passed the
canonical 433 focused tests with clean before/after identity. The synthetic
projection contains 14 aggregates and 40 signals; canonical replay and reversed
input share fingerprint
`c4bf9f62b2e1bec544342717659dea0b697d530a021496f9c8eefdaf3e3bc9f1`.

The final correction closes canonical page identity, recursive public
projection key/value separation, all-marker provider consensus, and historical
`author`/`keywords` compatibility. An exact cross-work regression proves that
same-name Pixiv artist signals without stable creator IDs hit the existing
non-union creator guard, while the same stable creator ID still unions across
name changes. The complete non-E2E run recorded 4238 passed, 22 skipped, and
four raw failures; three were validation-worktree/docs projection conditions
closed by targeted checks, while the sole remaining substantive failure is the
exact-base `missing_original_ai_execution_evidence` limitation.

## Remote Sync Preflight Policy

Fetch the trusted remote before comparing bases. A clean local base with no
local-only commits that is only behind may be fast-forwarded with `--ff-only`.
Divergence, tracked drift, behavior-affecting untracked code/configuration,
failed fast-forward, or any need for reset, rebase, force, overwrite, or
deletion is fail closed. Preserve unrelated user artifacts.

## Durable Links

- `docs/state/current-phase.json`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/phase-contracts.md`
- `docs/pixiv-metadata-ingestion-and-promotion-policy.md`
- `docs/source-concept-tag-search-semantics.md`
- `docs/development/agent-runbook.md`
- `docs/test-workflow.md`
