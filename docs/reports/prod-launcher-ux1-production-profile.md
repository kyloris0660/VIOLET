# PROD-LAUNCHER-UX1/PF1: Electron Production Launcher With Separate Production Profile

## Summary

This repair round keeps PR #122 on the same branch and fixes the current-head
reviewer P1/P2/P3 findings. Production startup is now isolated from
development `.env`, the production profile can be bootstrapped from local
durable evidence, the Electron launcher is zh-CN first, and the launcher can be
packaged as a double-clickable Windows portable executable.

The phase remains implementation-complete but not merge-ready until real user
manual acceptance is completed from the canonical production checkout.

```json
{
  "manual_acceptance_required_before_merge": true,
  "manual_acceptance_completed": false,
  "merge_allowed": false
}
```

## Production / Development Separation

Production uses a local ignored profile:

```text
.local_manifests/production_launcher/production-profile.json
```

The launcher does not modify development `.env`, does not require
`VIOLET_ENV=production` in development `.env`, and does not require
`VIOLET_STORAGE_ROOT` in development `.env`.

Profile launch now sets `VIOLET_SKIP_DOTENV=1`. Both `run.py` and backend
config honor that flag, so the development `.env` is not reloaded after the
controller has constructed the production child environment.

In profile mode the child environment comes from a clean allowlisted baseline
plus profile values. Arbitrary development `BLOMBOORU_*`, `VIOLET_*`, provider,
LLM, Redis, sync, test, and E2E variables are not inherited.

The production profile now carries an explicit `require_auth` policy. Discovery
and repair infer it from the existing local profile, `data/settings.json`, or
local `.env` `BLOMBOORU_REQUIRE_AUTH`; if none exists, repair uses the safe
default `require_auth=true`. Profile startup emits `BLOMBOORU_REQUIRE_AUTH`
from the profile into the child environment, so skipping development `.env`
does not accidentally make the production UI/API unauthenticated.

In production-profile-active mode, backend config now resolves
`BLOMBOORU_REQUIRE_AUTH` from the profile child environment before
`data/settings.json`. This lets the local production profile override a stale
storage `require_auth=false` value. Normal development mode keeps the previous
settings-before-env auth precedence.

## Profile Bootstrap

The controller now supports:

```powershell
python scripts\violet_production_control.py profile-discover --profile production-default --json
python scripts\violet_production_control.py profile-init --profile production-default --json
python scripts\violet_production_control.py profile-repair --profile production-default --json
python scripts\violet_production_control.py profile-status --profile production-default --json
```

`profile-discover` and `profile-repair` inspect local `.env`,
`data/settings.json`, `.local_manifests`, and public reports. Values inferred
from local records are written only to the ignored profile and are not printed
in public JSON.

This CodeX run created the ignored production profile in the canonical checkout.
Public `profile-status` reports `ready`, and public preflight reports `passed`.
The profile file itself was not committed and must stay local.

If a profile is hand-edited incorrectly, repair now forces:

- `profile_id=production-default`
- `env=production`
- `require_auth=true` when the policy was previously unknown
- `safe_startup=true`
- startup automation and destructive flags disabled

If a profile file exists with a mismatched embedded `profile_id`, status shows
`Profile Error`, preflight cannot pass, and start cannot run until repair.

When no profile exists, Create / Repair writes all inferred local values into
the ignored profile instead of treating discovery output as already saved. A
first partial Save Profile also bootstraps the inferred DB/storage/app values
before applying the explicit edit, so selecting storage cannot drop a known DB
host, port, name, user, or local access value.

While a launcher-managed server is running, identity profile edits are blocked:
`repo_root`, `python`, `app_port`, `storage_root`, and DB host/port/name/user.
The launcher returns `请先停止生产服务，再修改生产配置。` and leaves the profile
unchanged. This keeps Stop comparing against the launch-time state safely.

## Electron UI Behavior

The Electron UI remains the primary production launcher. It keeps raw JSON out
of the main screen, keeps Advanced Diagnostics collapsed by default, preserves
missing/incomplete/profile-error states over generic runtime states, and maps
blocked preflight gates to checklist rows.

Visible daily-use UI labels are zh-CN first, including status badges, summary
labels, action buttons, checklist group labels, and common blocker messages.
English technical identifiers remain available inside Advanced Diagnostics.
Copy Diagnostic Summary now copies and refreshes advanced diagnostics without
replacing the current status badge or checklist.

Open Browser is now treated as an auxiliary action: on success it opens the
browser and refreshes Advanced Diagnostics without replacing the current
Running/Unhealthy/Blocked status or clearing the checklist.

Packaged Windows portable launches can read the local ignored runtime config and
resolve the canonical checkout even when Electron runs from a temporary
extraction directory.

The Electron window, Windows taskbar entry, and packaged executable now use the
existing V.I.O.L.E.T. icon asset via `launcher/assets/violet.ico`.

Windows venv redirector launches are accepted only when the port listener owner
is a verified child of the launcher-started process and still runs `run.py`;
unrelated owners remain blocked.

After the production service is running, the right-side detail panel switches
from the preflight placeholder to a public-safe runtime panel. It shows health,
port, launcher-managed PID, uptime, DB/schema/storage status, and the latest
public-safe error. While the service is running the renderer polls status every
few seconds, and polling stops after the service is stopped.

The DB access value field preserves existing local credentials by default. To
clear a saved local DB access value, the operator must explicitly check
`清除已保存 DB 访问值`; the empty value is then sent over stdin JSON, never argv.

Observed blocker mapping remains:

- `violet_env_production`: Development `.env` is not used for production.
  Create or repair the production profile.
- `storage_root_explicit`: Production profile is missing storage root.
- `production_storage_root_shape`: Production storage root is invalid or unsafe.
- `db_readonly_reachable`: Database check is skipped until production profile
  and storage gates pass.
- `no_startup_mutation_automation`: Production profile must disable startup
  automation flags.

The renderer now calls `profile-repair` for `Create / Repair Production
Profile`, preserves existing DB users, and only submits changed non-empty form
fields.

## Windows Executable

Daily launch should use the ignored root-level executable:

```text
V.I.O.L.E.T. Production Launcher.exe
```

The packaged executable is also available under:

```text
launcher/dist/V.I.O.L.E.T. Production Launcher.exe
```

Build command:

```powershell
cd launcher
npm install
npm run package
```

`npm run package` builds the portable executable, applies the V.I.O.L.E.T. icon,
and copies the generated executable to the project root. The root executable and
`launcher/dist/` are ignored local outputs and are not committed.

`scripts\start_violet_production_launcher.cmd` remains as a fallback and
development entrypoint, not the preferred daily production path.

## NPM / Electron Proxy

Added:

```text
scripts/setup_launcher_npm_proxy.ps1
```

Usage:

```powershell
.\scripts\setup_launcher_npm_proxy.ps1 -Proxy http://127.0.0.1:7897
.\scripts\setup_launcher_npm_proxy.ps1 -Clear
```

The script writes only ignored `launcher/.npmrc`; personal proxy values are not
committed. Packaging initially stalled before the proxy file existed. After
writing the local proxy/mirror config, `npm install` and `npm run package`
completed successfully.

## Shutdown Investigation

The invalid `--log-config none` command is not documented. `run.py` accepts
only `--debug`; temporary direct uvicorn commands should use ordinary log-level
options or omit log configuration.

The likely shutdown hang risk was an untracked periodic startup task. Safe-start
mode already skips periodic/background jobs; this round also tracks normal
startup background tasks and cancels them during FastAPI shutdown.

Validation performed:

- temporary safe-start service launched on a non-production test port;
- `/api/health` returned 200 from V.I.O.L.E.T.;
- Ctrl+Break shutdown exited without force;
- follow-up active-server audit reported no occupied V.I.O.L.E.T. ports.

Launcher stop remains bounded and refuses unknown processes. If an emergency
stop is needed, the operator should target only the verified launcher-managed
PID:

```powershell
taskkill /PID <launcher-managed-pid> /T
```

Use `/F` only after confirming the same PID is still the launcher-managed
process.

## Reviewer Fix Round

Current-head P1/P2 findings fixed:

- P1: `run.py` and backend config now skip development `.env` when
  `VIOLET_SKIP_DOTENV=1`.
- P2: mismatched profile IDs fail closed for status, preflight, and start.
- P2: forward-slash Windows paths are redacted without corrupting `http://`
  URLs.
- P2: profile repair resets invalid production invariants.
- P2: Start now returns `ok=false`, `status=unhealthy` for an existing
  launcher-managed but unhealthy process; healthy existing managed processes
  still return success.
- P2: when a newly launched process fails post-start identity or health
  verification, the launcher attempts bounded cleanup of that exact launch and
  clears matching launcher state.
- P2: controller stderr redacts production profile path suffixes with mixed
  separators.
- P2: Electron visible UI is zh-CN first.
- P3: Copy Diagnostic Summary no longer clears the current UI state.
- P2: DB access value clearing is now explicit through a zh-CN checkbox and stdin
  JSON payload.
- P2: Open Browser no longer overwrites health/profile/checklist state on
  success.
- P2: failed-start cleanup reaps the `Popen` child and clears matching state for
  already-exited children.
- P1: failed-start cleanup re-verifies state/process identity before signaling
  non-`Popen` fallback PIDs.
- P2: controller stderr redacts UNC/NAS path forms while preserving normal
  `http://` and `https://` URLs.
- Acceptance-unblocking pass: production auth policy is explicit in the profile
  and child environment.
- Acceptance-unblocking pass: Create / Repair and first partial Save persist
  inferred local profile values.
- Acceptance-unblocking pass: identity profile edits are blocked while a
  launcher-managed server is running.
- Tiny final safety patch: production-profile-active backend config now lets
  profile auth override storage settings, while development auth precedence is
  unchanged.
- Final polish: V.I.O.L.E.T. icon is configured for the window/taskbar/package,
  the running state shows a runtime status panel instead of the stale preflight
  placeholder, and `npm run package` installs an ignored root-level launcher
  executable for daily double-click use.
- Final packaged-path fix: the root-level portable launcher now resolves the
  canonical checkout through the original portable executable directory when
  Electron exposes it, with a Windows Documents checkout fallback.
- Previous P1/P2 fixes are preserved: clean environment allowlist, DB user
  preservation, no-profile state precedence, structured controller errors,
  stderr redaction, stdin JSON profile updates, POSIX fail-closed ownership, and
  validation-status contract requirements.

## Reviewer Ledger Summary

The public-safe reviewer ledger was rebuilt across PR #121 and PR #122 instead
of treating the latest comments as isolated patches. The ledger now covers these
classes:

- startup writes and safe startup;
- health auth exemption and public-safe health payload;
- health table/column schema compatibility;
- process stop verification and stale PID refusal;
- destructive E2E denial and development/test flag stripping;
- start/restart serialization and stale lock reclaim;
- malformed app/database port handling;
- public log tail removal from JSON;
- `run.py` / backend config dotenv skipping;
- development environment inheritance removal;
- profile ID mismatch and invariant repair;
- production auth policy preservation;
- first create/repair inferred-value persistence;
- identity profile edit blocking while a managed process is running;
- DB user preservation and explicit DB access value clearing;
- controller empty stdout / crash errors;
- stderr, drive path, mixed profile path, UNC/NAS path, token, and DB access
  value
  redaction;
- POSIX port ownership lookup / fail-closed behavior;
- existing managed unhealthy Start refusal;
- post-start verification failure cleanup;
- zh-CN-first Electron UI;
- Copy Diagnostics and Open Browser preserving current UI state.

Every ledger item records root cause, fix location, same-class sweep, test
coverage, manual-acceptance impact, and status in the JSON summary. All listed
items are fixed for the current implementation; manual acceptance remains
required before merge.

## State Machine Audit

The production launcher state machine was audited end to end:

- No profile, Profile incomplete, and Profile error stay visible in the UI and
  do not get overwritten by generic Stopped.
- Preflight blocked never spawns a process and keeps the checklist actionable.
- Start requested / Start in progress is serialized by the start lock.
- Child process spawned writes state only for the new PID.
- Health timeout, health-unhealthy, or identity-mismatch after spawn attempts
  bounded cleanup of the same `Popen` child and clears only matching state.
- If cleanup cannot verify the process identity, it refuses to signal and
  returns a safe recovery message.
- Existing managed healthy processes can return Start success.
- Existing managed unhealthy processes return `ok=false`, `status=unhealthy`.
- Running managed processes block identity profile updates until Stop succeeds.
- Stop only targets verified launcher-managed processes; unknown processes are
  refused.
- Restart is stop-then-start under the same serialization gate.
- Copy Diagnostics and Open Browser do not replace the current UI state.

Dangerous transitions are covered by focused tests: existing unhealthy start,
post-start verification failure cleanup, health-timeout cleanup, changed state
or unverified PID refusal, profile mismatch, invariant repair, running-process
identity edit blocking, diagnostics state preservation, and open-browser state
preservation.

## Same-Class Sweep

The final sweep checked the adjacent failure classes that caused repeated repair
rounds:

- Environment isolation: `run.py`, backend config, controller env construction,
  Electron runner, and start scripts were checked. Profile startup sets
  `VIOLET_SKIP_DOTENV=1` and uses an explicit allowlist baseline.
- Diagnostics redaction: controller stdout/stderr, Electron IPC responses,
  Advanced Diagnostics, Copy Diagnostics, public reports, and
  `ControlResult.to_public_dict()` were checked. DB access values, tokens,
  home, repo, profile, storage, drive-letter, mixed profile suffix, and UNC/NAS
  path forms are redacted; ordinary URLs are preserved.
- Start/Stop cleanup: every return after `subprocess.Popen` was checked. Failed
  launches either clean up the same child/state or return explicit safe recovery;
  unknown processes are not killed.
- UI mapping: `deriveState`, `applyPayload`, boot precedence, Copy Diagnostics,
  and Open Browser were checked. Unhealthy cannot render as Running, and
  auxiliary minimal payloads do not erase the status/checklist.
- Profile edit safety: `profile-update`, renderer changed-field tracking, and
  launcher-managed state verification were checked. First create/repair
  persists inferred values, partial Save bootstraps inferred values, and
  identity edits are blocked while managed production is running.

## Explicit Deferrals For This Temporary Windows Launcher

The following are intentionally non-blocking for this PR's current acceptance
path, which is the Windows canonical checkout plus packaged Electron launcher:

- POSIX/Linux port owner completeness beyond the existing fail-closed behavior.
- Full schema preflight before spawn; this temporary launcher requires health
  OK during acceptance and keeps public health schema checks.
- Broad multi-user/global production profile architecture.
- Cosmetic diagnostics edge cases that do not leak secrets and do not affect
  Start/Stop.

## Validation

Passed during this round:

```powershell
python -m pytest tests\test_production_launcher_control.py tests\test_phase_contracts.py tests\test_config_precedence.py -v
cd launcher
npm install
npm test
npm run lint
npm audit --json
npm run package
```

Observed results:

- Focused Python tests: `254 passed`
- Electron tests: passed
- Electron lint: passed
- Electron audit: 0 vulnerabilities
- Electron package: passed
- Root-level daily launcher install: passed
- Canonical ignored profile status: ready, auth policy explicit
- Canonical public preflight: passed
- Direct safe-start shutdown validation: passed

The full required validation command set is recorded in the JSON summary and
must remain green before final handoff.

## Safety Confirmation

- No push to `main`.
- No merge.
- No development `.env` modification.
- No source/iCloud/media mutation.
- No cleanup/delete/reset/drop/truncate.
- No DB import.
- No DB migration.
- No classification, AI tagging, localization, sync, provider, SourceConcept,
  Entity bridge, or LLM run.
- Canonical writes were limited to the ignored local production profile.
- Build outputs and local `.npmrc` are ignored and not committed.

## Merge Recommendation

Do not merge until the user completes real Electron launcher acceptance from
the canonical production checkout.

Recommended status:

```text
manual_acceptance_required_before_merge
```

## Artifact Lifecycle

- `scripts/violet_production_control.py`: reusable validation/safety tool.
- `run.py` and `backend/app/config.py`: durable production profile startup
  guard.
- `backend/app/main.py`: durable startup/shutdown safety.
- `launcher/`: durable production launcher UI.
- `launcher/assets/violet.ico`: durable launcher icon asset derived from the
  existing V.I.O.L.E.T. logo.
- `scripts/install_production_launcher_root_entry.ps1`: reusable local install
  helper for the ignored root-level daily executable.
- `scripts/setup_launcher_npm_proxy.ps1`: reusable local setup helper.
- `.local_manifests/production_launcher/production-profile.json`: local/private,
  ignored, not committed.
- `launcher/dist/`: local build output, ignored, not committed.
- `V.I.O.L.E.T. Production Launcher.exe`: local root-level daily launcher output,
  ignored, not committed.
- `docs/production-launcher.md` and this report family: public report/handoff.
