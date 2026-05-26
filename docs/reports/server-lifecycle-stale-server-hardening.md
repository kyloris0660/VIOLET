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
- supports the current active Windows validation environment through `netstat -ano -p tcp`
- treats non-Windows listener auditing as unsupported unless a tested backend is added later
- reports listener backend status (`listener_backend`, `listener_backend_status`, `listener_backend_error`)
- fails closed for `--fail-if-any` / `--fail-if-stale` when the listener backend is unavailable or unsupported
- reports unknown listener state instead of pretending ports are free when listener detection fails
- reports process backend status (`process_backend`, `process_backend_status`, `process_backend_error`)
- fails closed when a listener exists but process enumeration is unavailable
- reports TCP listener PID
- reports whether process metadata exists
- reports command line, parent PID, and matching child processes when visible
- optionally reads `/api/system/server-identity` with admin credentials
- reports explicit identity statuses such as `unauthorized`, `forbidden`, `connection_failed`, or `unavailable` instead of pretending success when identity cannot be read
- distinguishes `confirmed_violet`, `suspected_violet`, `unknown_listener`, and `non_violet`
- treats identity `401/403` as `unauthorized` / `forbidden`, not as proof that the server is unrelated
- uses process command line, process tree, expected code root, and repo venv evidence to classify suspected V.I.O.L.E.T. servers when identity is unavailable
- classifies stale signals such as `orphan_or_reloader_mismatch`, `unknown_listener`, identity mismatches, and `identity_pid_differs_from_listener_pid`
- emits JSON or text output
- supports `--fail-if-any` and `--fail-if-stale` for preflight gates
- redacts `--admin-password` from reported command lines
- recommends candidate PIDs that may be safe to stop, but does not stop them

## Reviewer Closeout

PR #74 reviewer feedback identified two correctness issues in the first audit implementation:

- P1 false negative: a V.I.O.L.E.T. server whose identity endpoint returns `401/403` could be counted as non-V.I.O.L.E.T. because `is_violet_server` depended only on identity JSON.
- P2 false positive: `stale_server_count` included every occupied port with diagnostic stale reasons, so unrelated services could make `--fail-if-stale` fail.

Both were fixed:

- `is_violet_server` is now true for both `confirmed_violet` and `suspected_violet`.
- `suspected_violet` uses process evidence when identity is unavailable, including repo path, `run.py`, V.I.O.L.E.T./AnimeLocalBooru strings, process tree evidence, expected code root, or repo venv evidence.
- JSON/text output now exposes `server_classification`, `detection_sources`, `is_confirmed_violet`, `is_suspected_violet`, `unknown_listener_count`, and `unrelated_listener_count`.
- `stale_server_count` now counts only confirmed/suspected V.I.O.L.E.T. ports with stale reasons.
- `--fail-if-any` fails only for confirmed/suspected V.I.O.L.E.T. servers, not arbitrary listeners.
- `--fail-if-stale` fails only for confirmed/suspected V.I.O.L.E.T. stale servers.
- Unrelated occupied ports remain visible in the report but do not fail the V.I.O.L.E.T. stale gate.
- No stop/kill functionality was added.

Latest closeout scope correction:

- Windows remains the supported active development/validation environment for this tool.
- Non-Windows listener audit is explicitly unsupported/fail-closed rather than partially parsed.
- Missing or failing listener backends cannot produce a false clean result.
- Listener backend unsupported/unavailable returns non-zero by default, even without fail-gate flags.
- Process enumeration unavailable returns structured `process_backend_unavailable` state and returns non-zero when a listener exists.
- Windows path comparisons for `expected_code_root` and `expected_storage_root` now tolerate case differences, slash direction, and trailing separators while still rejecting different real paths.
- `expected_code_root` process evidence is path-boundary-aware, so sibling paths such as `AnimeLocalBooru_backup` no longer match.
- Bare `violet` in unrelated process names is no longer sufficient V.I.O.L.E.T. evidence.
- `run.py` plus identity `401/403` is no longer sufficient without repo evidence.
- `--admin-password` redaction now handles space-containing and escaped-quote values without leaking suffixes.

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
- listener backend unavailable / unsupported behavior
- fail-closed gates when listener detection is unavailable
- process backend unavailable / fail-closed behavior
- Windows path normalization for expected code root and storage root
- path-boundary expected code root evidence
- unrelated `violet` and unrelated `run.py` false-positive prevention
- escaped-quote admin password redaction
- identity unavailable behavior
- unauthorized identity with process evidence -> `suspected_violet`
- unrelated occupied service does not increment `stale_server_count`
- unrelated occupied service does not trip `--fail-if-stale`
- confirmed V.I.O.L.E.T. test server remains counted as stale when stale signals exist
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
