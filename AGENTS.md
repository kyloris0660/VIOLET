# AGENTS.md

## Entry point

V.I.O.L.E.T. is a local anime/illustration library built with FastAPI,
PostgreSQL, Jinja2/Tailwind, and vanilla JavaScript. Before acting, read and
validate `docs/state/current-phase.json`, then load only the durable links named
there. The detailed operating runbook lives at
`docs/development/agent-runbook.md`; historical roadmap material lives under
`docs/roadmap/archive/`.

## Current-state protocol

- `docs/state/current-phase.json` is the only machine-readable current-route
  truth. The handoff and active roadmaps are projections, not competing facts.
- Apply `REMOTE_SYNC_PREFLIGHT_POLICY` before comparing a protected local base
  with its trusted remote: fetch the verified remote first. If the worktree is
  safe, the local base has no local-only commit, and it is only behind the
  remote, fast-forward it with `--ff-only`, record the event as a preflight
  self-heal, and continue the same task. Initial local/remote inequality is a
  classification input, not an automatic blocker.
- Fail closed when the base diverged, has unsafely preserved local-only commits,
  fast-forward-only fails, tracked/staged/unstaged/deleted/renamed drift exists,
  behavior-affecting untracked executable/package/module/config/symlink drift
  exists, remote identity or authentication cannot be verified, or syncing
  would require reset, rebase, force, overwrite, or deletion. Preserve unrelated
  untracked and ignored non-executable user artifacts; never clean them merely
  to make preflight pass.
- After a durable checkpoint or state change, update current-phase immediately,
  regenerate `docs/current-handoff.md`, and run
  `scripts/check_documentation_state.py --check`.
- Run the same check before commit, push, stop, handoff, and final response.
- Do not load or rewrite historical reports merely because current state moved.

## Hard authorization boundaries

- Every substantial phase requires an implementation plan and explicit owner
  approval before implementation or execution. Plan-only work does not grant
  execution authority.
- Never merge a PR, push `main`, force-push, start a next phase, or promote a
  Draft PR without explicit current-task authorization.
- Never access or mutate production, source/iCloud roots, app-managed storage,
  databases, provider routes, LLMs, media, thumbnails, Entity/truth paths, or
  provider-derived `media_tags` unless the exact phase authorization includes
  that operation.
- Database schema changes require a reviewed migration plan. Destructive DB or
  filesystem operations require exact-target proof, backup/recovery planning,
  and explicit authorization.
- Preserve unrelated tracked, untracked, and ignored user artifacts. Do not
  clean, stash, reset, move, delete, or overwrite them automatically.
- Use only repo-local ignored or local temporary test outputs; never use NAS or
  network-share paths for agent test artifacts.

## Evidence and phase contracts

- Claims such as `target_met`, `safe_to_merge`, `route_approved`, or reusable
  pipeline completion require a registered executable phase contract.
- Bind reports, ledgers, review packs, PR bodies, and acceptance results to the
  exact live HEAD and protected evidence. Prior evidence is historical unless a
  tested behavior-neutral carry-forward contract applies.
- Keep owner/manual acceptance distinct from automated tests and browser
  prevalidation. Stop at the named owner checkpoint.
- Prefer executable guards, schema constraints, transaction boundaries, and
  focused tests over long prompt-only procedures.

## Development and validation

- Use the repository venv and mandatory Python identity preflight before Python
  execution. Load the standardized test environment when runtime tests require
  it; strict test database and storage identity rules remain mandatory.
- For UI/runtime changes, start a controlled test server and perform real
  browser validation with system Edge/Playwright. Do not substitute a mocked DOM
  for the user flow.
- Report exact commands, passed/failed/skipped counts, warnings, and unavailable
  gates truthfully. Local validation is not GitHub CI.
- For non-trivial bug fixes, identify the root cause, audit the bounded
  same-pattern class, add regression coverage, and state what was intentionally
  deferred.

## GitHub delivery

- Work on the named feature branch and PR. A local commit is not a PR.
- Verify branch, worktree, `origin/main`, PR state, and review threads before
  handoff. Use GraphQL review threads for unresolved inline feedback.
- Do not trigger reviewers unless the current owner instruction authorizes it.
- Final reports are primarily zh-CN and include PR/branch/HEAD, changed files,
  exact validation, artifacts, safety non-actions, current status, next owner
  decision, and engineering judgment.

## Detailed references

- Runtime, architecture, testing tiers, server lifecycle, cloud ingestion,
  provider/entity trust policy, PR body format, and full delivery template:
  `docs/development/agent-runbook.md`
- Active route: `docs/state/current-phase.json`
- Generated handoff: `docs/current-handoff.md`
- Active roadmap: `docs/roadmap/current-mainline-roadmap.md`
- Phase contracts: `docs/phase-contracts.md`
