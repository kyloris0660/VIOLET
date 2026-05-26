# Server Lifecycle Stale Server Hardening

Phase S1 adds read-only server lifecycle diagnostics and governance after a stale local test server was found on port `8012`.

## Incident Summary

An unexpected V.I.O.L.E.T. server remained active after prior validation work, violating the singleton server policy and making it possible for future API/browser checks to target the wrong process.

Confirmed stale-server facts from the investigation:

- Port `8012` was `LISTENING` when no active server should have existed.
- Server identity confirmed V.I.O.L.E.T.
- `VIOLET_ENV=test`
- `DB=blombooru_test`
- `storage_root=C:\Users\kyloris\VioletStorage\test`
- `code_root=C:\Users\kyloris\Documents\AnimeLocalBooru`
- `git_branch=main`
- `git_sha=63934cb`
- identity PID `10292`
- port `8012`
- TCP listener/reloader PID `39504`, but process table could not find PID `39504`
- visible worker command line:
  `python.exe -c "from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=39504, pipe_handle=776)" "--multiprocessing-fork"`

## Root Cause Assessment

Determined facts:

- The server was a V.I.O.L.E.T. test server, not a development server.
- It used test DB and test storage.
- It was launched with the same repo code root and current `main` git SHA.
- The visible worker process was a `multiprocessing.spawn` child of a reloader/listener PID.

Reasonable inference:

- The stale server likely came from previous CodeX/browser validation/E2E/test-server work or a manually started test server.
- `run.py --debug` uses uvicorn reload, which can leave a worker child if cleanup only stops a wrapper/reloader PID.
- Prior cleanup likely failed to verify the port was free after stopping the recorded PID.

Uncertain points:

- The current evidence does not prove whether CodeX or the user originally started the stale server.
- The current evidence does not explain why Windows TCP state still named PID `39504` while the process table could not find it.
- Existing local logs did not contain the exact 2026-05-25 20:27 startup line for this server.

## Tool Added

Added reusable validation/safety tool:

- `scripts/audit_active_violet_servers.py`

Lifecycle: reusable validation/safety tool.

The tool is read-only by default and has no process stop/kill functionality. It:

- scans common local ports such as `8000,8012-8024`
- reports TCP listener PID
- reports whether process metadata exists
- reports command line, parent PID, and matching child processes when visible
- optionally reads `/api/system/server-identity` with admin credentials
- reports `identity_unavailable` instead of pretending success when identity cannot be read
- classifies stale signals such as `orphan_or_reloader_mismatch`, `unknown_listener`, identity mismatches, and `identity_pid_differs_from_listener_pid`
- emits JSON or text output
- supports `--fail-if-any` and `--fail-if-stale` for preflight gates
- redacts `--admin-password` from reported command lines
- recommends candidate PIDs that may be safe to stop, but does not stop them

## Governance Updates

Updated lifecycle rules in `AGENTS.md`, `CLAUDE.md`, `docs/test-workflow.md`, and `docs/manual-validation.md`:

- Run a no-active-server preflight before starting agent-controlled or manual validation servers.
- Common local ports include at least `8000` and `8012-8024`.
- Do not silently choose another port to bypass a stale server.
- Record server command, `APP_PORT`, `VIOLET_BASE_URL`, parent/reloader PID, worker/identity PID, process tree, code root, git SHA, branch, `VIOLET_ENV`, DB, storage root, and Python executable.
- Identity check is a hard gate before API/browser/E2E validation.
- `run.py --debug` / uvicorn reload requires reloader/worker child awareness.
- Cleanup must stop only the exact process tree started by the current task.
- If a stale server was not started by the current task, agents must report exact PIDs and wait for user approval before stopping it.
- Cleanup reports must verify the port is no longer `LISTENING`.

## Current 8012 Status

At S1 implementation time, a read-only check reported:

- `8012`: not listening / port free

No process was stopped by CodeX during this phase.

## Validation

Focused checks for this PR:

- `python -m py_compile scripts/audit_active_violet_servers.py tests/test_audit_active_violet_servers.py`
- `python -m pytest tests/test_audit_active_violet_servers.py -v`
- `python scripts/audit_active_violet_servers.py --ports 8012 --json --include-process-tree --admin-password <redacted>`

The focused tests cover:

- port range parsing
- password redaction
- JSON output shape
- mocked process-tree classification
- stale server classification
- `--fail-if-any`
- `--fail-if-stale`
- identity unavailable behavior
- absence of stop/kill code paths

## Deferred Items

- Automatic process stop/kill behavior remains intentionally excluded.
- Full non-E2E suite is not required for this isolated diagnostic script/docs stage unless shared test infrastructure changes.
- Phase 4.4-B0 remains blocked until no-active-server preflight is clean and the user provides approved sample media IDs.

## Safety Confirmation

This phase did not:

- stop/kill any process
- start a server
- run Phase 4.4-B0
- call provider APIs
- reverse search
- upload images or thumbnails
- DB import
- classification
- AI tagging
- localization
- staging copy
- source/iCloud mutation
- app-managed storage mutation
- Entity Resolver execution
- similarity/clustering
- cleanup/delete/reset/drop/truncate
