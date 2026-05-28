# CLAUDE.md

This project is V.I.O.L.E.T. (Visual Image Organizer for Local Evaluation & Tagging), a local-first anime image library based on Blombooru.

## Before coding, read these files

- `AGENTS.md` — Agent instructions, architecture, key code locations
- `README.md` — Project overview, feature status, quick start
- `docs/current-handoff.md` — Latest development state
- `docs/project-roadmap.md` — Full phase plan

## Critical workflow rules

### GitHub PR / main protection

Agents may:
- Create feature branches, commit changes, push feature branches
- Create GitHub PRs, update existing PR branches
- Run tests, prepare local validation servers

Agents must NOT:
- Merge PRs
- Push directly to `main`
- Force-push `main`
- Delete `main`
- Treat local commits as PRs
- Claim a PR exists without a real GitHub PR URL (e.g. from `gh pr view` or `gh pr create`)
- Claim a PR is merged without GitHub or `origin/main` verification

The user manually reviews and merges PRs on GitHub.

Default PR lifecycle is a normal open PR. Create a draft PR only when the user/ChatGPT explicitly requests draft, or when the stage is clearly a design draft / not ready for review. Docs-only does not imply draft, and a reviewable plan/design PR may be opened normally. Draft PRs must not become the default way to avoid reviewer or human judgment. Final reports must state whether the PR is draft and why.

### GOV-2 workflow weight reduction policy

Reliability remains mandatory, but process weight must decrease. Prefer executable guards, explicit code assertions, DB constraints, transaction boundaries, enum states, allowlists/denylists, and focused tests over long prompt-only constraints, repeated docs-only gates, overly fragmented phases, or generic frameworks for one-off scripts.

The following durable core areas stay strict and must not be weakened:

1. DB schema and migrations.
2. Provider-neutral evidence contracts.
3. Entity / Alias / Evidence / Candidate / Assignment lifecycle.
4. `ProviderCache`, `EntityEvidence`, `MediaEntityCandidate`, and `NegativeLookupCache` write semantics.
5. External provider upload privacy gates, budget gates, cache/audit/rate-limit design, and separate run approval.
6. Confirmed assignment policy: manual confirmation or explicitly approved policy only.
7. Source/iCloud/app-managed storage mutation safety.
8. Broad or repeated provider runs, which require run ledger discipline.
9. E2E delivery when E2E is in scope: 0 failures required; skipped tests must be explicitly gated and reported.

### Artifact lifecycle classification

Every new script, tool, report, or generated artifact must be classified in the PR body or final report:

1. **Durable production code** - long-term maintained runtime code with strict tests, clear interfaces, and stable semantics.
2. **Reusable validation/safety tool** - cross-phase tooling with a stable safety contract. Review the safety contract strictly, but avoid unnecessary broad platform/framework expansion.
3. **Phase-scoped operational runner** - committed only when needed to reproduce a phase. It must be safe, privacy-preserving, data-integrity-preserving, and truthful for the current phase, but should not be polished into a generic production framework unless user/ChatGPT explicitly promotes it.
4. **One-off local artifact / ignored output** - stays ignored/untracked and should not be over-engineered.
5. **Public report / handoff / roadmap update** - must be privacy-safe and truthful, but should not trigger broad runtime work by itself.

### Reviewer closeout and deferral rule

For implementation PRs, reviewer feedback is a controlled handoff point, not an automatic code-change trigger.

1. After PR creation or a meaningful PR update, CodeX must trigger reviewer with exactly `@codex review`.
2. CodeX may collect reviewer feedback and verify whether it applies to the current PR head.
3. CodeX must summarize current-head P1/P2/P3 findings in the final report.
4. CodeX must not automatically modify code based on reviewer feedback unless user/ChatGPT explicitly authorizes a bounded fix loop for that PR.
5. Default reviewer closeout is **1 to 2 bounded fix rounds per PR**.
6. Severity label alone is not enough. P1/P2 is a signal, not an automatic decision; lifecycle plus current-stage impact decides.
7. Continue fixing after the default closeout only if the finding affects current-stage data corruption, a DB write executed by this PR, API key/local path/source/iCloud/filename/original-image leak, provider upload safety, current-stage report truthfulness, confirmed assignment/media_tags/entity truth pollution, core contract/schema correctness actually consumed by this PR, or irreversible operation safety.
8. Otherwise record the finding as deferred, move it into the phase where it actually matters, and do not block merge solely for future generalization.
9. If a finding only matters in the next DB-writing phase, move it into that phase's acceptance criteria instead of keeping a non-mutating design PR open indefinitely.
10. Even when a fix loop is explicitly authorized, it must never push `main`, merge, run destructive operations, mutate source/iCloud/staging/DB unless explicitly approved, change phase scope, or start a new phase.
11. Before triggering reviewer, CodeX must perform a local pre-review / same-class self-audit so reviewer is not used as a substitute for engineering judgment.

### Phase granularity and prompt requirements

Do not split phases unless the split reduces real risk or improves delivery clarity. A phase should produce at least one real value: product capability, data model capability, reusable code constraint, real experimental result, DB state change, or clear route decision. Avoid excessive R/G/S/I subphase proliferation for small tasks. Small docs-only updates should usually be batched unless they unblock current work or remove major contradictions.

Future CodeX prompts should explicitly specify artifact lifecycle, must-fix reviewer categories, deferable reviewer categories, non-goals, whether DB writes are in scope, whether provider calls/uploads are in scope, whether the phase is durable-core or phase-scoped, and max reviewer closeout expectation.

### Agent Engineering Judgment and Bugfix Root-Cause Closure Policy

CodeX must provide a meaningful `Engineering judgment / operator notes` section in final delivery reports. It must assess phase boundary fit, risks, reviewer findings fixed/deferred, artifact lifecycle, prompt quality, what was intentionally left unchanged, and the recommended next step. This section is advisory only; it does not grant authority to expand scope, merge, auto-fix reviewer feedback, or start a new phase.

For every non-trivial bugfix, CodeX must perform a bounded root-cause and pattern audit:

1. Identify the root cause.
2. Decide whether the issue belongs to a broader same-pattern class inside the current PR scope.
3. Fix the pattern within the current PR scope without expanding into unrelated modules or future phases.
4. Add tests for the class of issue when practical.
5. Report what was searched, what was fixed, and what was intentionally deferred.
6. Stop and ask user/ChatGPT if the root-cause fix requires a DB migration, destructive operation, source/iCloud mutation, app-managed storage mutation, large refactor outside current PR scope, new phase, or project strategy change.

### Manual entity correction principle

V.I.O.L.E.T. is a large-scale local library system and must not rely on exhaustive human review of AI/entity suggestions. Human interaction is for sparse, targeted, high-impact correction and confirmation: find a problem, correct entities/aliases/assignments/candidates, preserve provenance, and leave durable evidence, aliases, overrides, assignments, or negative signals for future automation. Automation remains the default path. Manual review UI must be correction-oriented and must not assume the operator will process thousands of suggestions or candidates one by one.

### AI proper-noun signal trust policy

Existing AI-generated character/copyright/artist/proper-noun tags must not be treated as reliable entity truth. General/meta visual tags may remain trusted as visual descriptors, but they are not identity signals. AI proper-noun tags must not automatically create confirmed entity assignments or trusted entities; by default they are weak evidence, statistics, or future query seeds only. High-trust entity assignment requires manual confirmation, trusted external exact metadata with approved provenance, or clearly imported source metadata with provenance. Guarded candidate generation must default to manual/imported/trusted tiers and stay dry-run first unless the user/ChatGPT explicitly approves broader AI-derived candidate sources.

### Source-first entity enrichment policy

Entity enrichment must be source-first / provenance-first. Future reliable entity metadata should prioritize known source URLs, exact external post IDs, imported source metadata, and source-backed external evidence under explicit opt-in provider policy before creating candidates. AI proper-noun tags may help prioritize queries but are not truth. Visual similarity, embeddings, perceptual hashes, and tag co-occurrence are supplementary recall tools only and must not create automatic confirmed assignments. Unknown, non_anime, unapproved illustration, local paths, iCloud paths, filenames, source labels, original image bytes, and privacy-sensitive content must not be sent to external providers by default. External enrichment requires explicit provider policy, privacy eligibility, cache/audit/rate-limit/budget design, redacted reporting, and separate run approval.

### No-source source discovery policy

For the current iCloud-derived library, treat source URLs, external post IDs, and imported exact-source metadata as unavailable unless a future phase proves otherwise as a minor sanity note. Do not make exact-source inventory the next default route. No-source source discovery must start as a tightly controlled anime-only reverse-search/source-discovery pilot design: one provider, cache-first, rate-limited, budgeted, no originals by default, derived thumbnail/resized stripped input only after explicit provider-specific approval, evidence/candidate-only, no confirmed assignments, no broad/full-library scheduling, and no `unknown`, `non_anime`, or unapproved `illustration` content.

### Cloud-aware ingestion progress and safety policy

Safety gates should make workflows controlled, observable, and recoverable. They must not become infinite blockers for expected iCloud / Windows Cloud Files states. Cloud recall-risk is a risk signal, not a permanent exclusion.

Distinguish structural blockers from per-item failures.

Structural blockers stop the whole run:
- server identity mismatch
- DB identity mismatch
- unsafe staging target
- target path escape
- protected root overlap
- manifest schema invalid
- duplicate target paths
- report generation failure
- privacy leak
- DB/app-storage/source-root confusion
- unexpected DB/app-storage/source mutation

Per-item failures are recorded, excluded from DB import eligibility, and kept visible for retry/backfill/deferred recovery. They do not automatically block the whole batch when they stay within the approved failure budget:
- cloud_hydration_failed
- cloud_network_unavailable
- read_timeout
- source_missing
- permission_denied
- unsupported_extension
- size_mismatch
- unreadable_source

Medium pilot staging copy uses this default failure budget:
- `max_item_failures=20`
- `max_failure_rate=0.05`
- `max_consecutive_failures=10`
- `max_same_reason_failures=20`

Exceeding the budget indicates possible systemic provider/network/workflow failure and should stop the run.

iCloud / Cloud Files policy:
- Phase 2.4 solved scan safety; staging copy also requires ingestion availability.
- Default behavior blocks recall-risk rows.
- An explicit cloud-aware copy policy may allow recall-risk rows into controlled copy with bounded reporting.
- Source/iCloud write mutation remains forbidden unless separately and explicitly approved.
- DB import remains forbidden until staging audit passes and a separate DB import stage is approved.

Every staging/import workflow must produce per-item state records with: row id or safe label, source state, staging status, failure reason, bytes copied, `eligible_for_db_import`, deferred/backfilled/unresolved state, and imported media ID if later imported.

Future full-library import requires a production Ingestion Run Ledger / Source Item State Ledger before execution. Future large imports should support an over-selection buffer: `desired_success_count=N`, `candidate_count=N * buffer_ratio`, accounting for cloud failures, duplicates, unsupported files, non-anime/ineligible classification, and user exclusions.

### PR body format and task checklist standard

Do not invent a new PR body format for each phase. Future PRs must follow the established V.I.O.L.E.T. phase PR format:

1. `# <Phase title>`
2. `## Summary`
3. `## Scope`
4. `## Safety / Hard Constraints`
5. `## Implementation`
6. `## Validation`
7. `## Test plan`
8. `## Reviewer / Codex status`
9. `## Safety confirmation`
10. `## Next step`

The `## Test plan` section must use GitHub task list syntax for major gates, for example:

```markdown
- [x] Python identity checked
- [x] Focused tests or document checks passed
- [ ] Full non-E2E suite passed, if required by lifecycle/scope
- [ ] Real dry-run / real audit / smoke validation passed, if required by lifecycle/scope
- [x] Reviewer re-review requested
- [ ] Manual review / user validation if not yet done
```

Checkboxes must reflect reality: do not mark incomplete items complete, and mark non-applicable gates as not applicable or omit them with a clear reason. Do not run full non-E2E, real dry-run, or E2E gates for docs-only or phase-scoped work unless the lifecycle/scope requires them. The `## Reviewer / Codex status` section must state whether reviewer/Codex reviewed the latest head SHA; if pending, say pending. The `## Safety confirmation` section must explicitly state no push main, no merge, no source/iCloud/staging mutation, no cleanup/reset/drop/truncate, no API key exposure, and no forbidden background systems. PR titles should stay consistent: `Phase X.Y: <clear phase title>` or `feat/fix/docs: <clear scope> (Phase X.Y)`.

### Real browser validation (UI/runtime scope)

For every UI/runtime/browser-visible feature phase, bug fix, or behavior change, the agent must perform real browser validation before delivery. This applies to changes involving Admin UI, gallery/media grid, media detail page, search behavior, tag localization, AI tagging/review UI, local library scan workflow, settings/developer tools, user-visible text, thumbnails/fallback images, routing/navigation, frontend JavaScript behavior, or server behavior that is exercised through the browser.

Docs-only, governance-only, non-UI phase-scoped runners, isolated backend contract/test changes, and report-only PRs do not require browser validation unless their actual scope touches server/UI behavior. Do not start a server solely to satisfy browser validation for a non-UI docs/governance change.

**Required standard:**

1. Prefer Playwright with system Edge on Windows.
2. Do not rely only on API tests or unit tests when UI behavior is affected.
3. Use a real running local server. **Agents must start a controlled test server themselves** (see "Agent-started test servers" section). Do not ask the user to start the server unless startup fails for a concrete reason.
4. Use the actual app page, not only mocked DOM tests.
5. Verify the relevant user flow end-to-end.
6. If the feature touches local files, scan, thumbnails, or media display, validate with a real local test folder when safe.
7. If real browser validation cannot be run despite best effort, the agent must explicitly explain why (exact error) and provide the closest fallback validation. "Server was not running" is not an acceptable excuse — the agent should have started one.

When browser validation is in scope, the delivery report must include a dedicated section: **真实浏览器验收**, containing: 验收方式, 浏览器/Playwright project, URL tested, pages/flows validated, pass/fail result, skipped or not covered items, fallback explanation if real browser validation could not be completed. A UI/runtime/browser-visible phase is not considered complete without this section. When browser validation is not in scope, say so explicitly and give the reason.

### Phase plan approval rule

For every new major development phase or substantial feature scope, the agent must first produce an implementation plan and wait for explicit user approval before making substantial code changes. Bug fixes and small review-comment fixes may proceed without a separate plan. Major stage-level design changes (classifiers, models, DB schemas, evaluation frameworks) require the plan first.

Plan-only tasks must not create branches, commits, pushes, or PRs unless the user explicitly approves a documentation PR. Deliver plan-only output in chat or as a local untracked `.codex/plans/*.md` draft, then wait for user/ChatGPT approval before implementation.

### Chinese reporting rule

Final user-facing stage summaries and delivery reports must be written in Chinese. This includes stage summaries, delivery reports, test result summaries, risk notes, local validation steps, known limitations, and next-step recommendations.

Section headings in final delivery reports and stage summaries must also be Chinese. Keep technical identifiers in English: file paths, branch names, PR URLs, API routes, config keys, class/function names, commands, commit messages, PR titles. Code comments may remain English when appropriate.

### Final Delivery Report Standard

Every CodeX final report for implementation or review stages must be written in Chinese and include:

1. PR URL, branch, head SHA
2. Whether the PR was created, pushed, and merged
3. Docs/code read
4. Python identity and exact sys.executable
5. Exact files changed
6. Implementation summary
7. Exact tests run and exact results
8. Real validation / dry-run results
9. Reviewer status, including whether the latest head was reviewed
10. Local artifacts generated and confirmation they were not committed
11. Safety confirmation:
    - no push main
    - no merge
    - no source/iCloud mutation unless explicitly approved
    - no cleanup/delete/reset/drop/truncate unless explicitly approved
    - no DB import unless explicitly approved
    - no classification/AI/localization unless explicitly approved
    - no Entity Resolver / similarity unless explicitly approved
12. Current blocked/ready status
13. Recommended next step
14. If stopped by a rule, the exact stop condition
15. Engineering judgment / operator notes:
    - blockers vs deferable issues
    - risks in the current design
    - whether the phase boundary seems too narrow or too broad
    - whether the prompt missed important issues
    - recommended next step

This engineering judgment section is advisory only. Do not expand scope without user/ChatGPT approval.

A short summary alone is not acceptable. If any item is not applicable, say "N/A" and why. Do not force the user to inspect the PR body or old logs to reconstruct test results.

### Test report accuracy

- Do not claim "all tests passed" if any test failed.
- If some tests are skipped, gated, unavailable, or unrelated, the report must say so clearly.
- The final delivery report must include exact commands and exact results.
- If a failing test is pre-existing or unrelated, the agent must either: (1) fix it; (2) gate/skip it intentionally with a clear reason; or (3) document it as non-blocking with evidence.

### Service / dev environment safety

- Never kill arbitrary Python or Node processes.
- Only stop clearly identified V.I.O.L.E.T. / AnimeLocalBooru dev server processes.
- Report PID, command line, and port before stopping.
- Use `scripts/audit_active_violet_servers.py` for read-only no-active-server preflight and stale-server diagnosis on common local ports (`8000`, `8012-8024`) in the current Windows local validation environment. On non-Windows, use a platform-specific equivalent or future tested implementation; an unsupported listener backend must not be treated as a clean preflight.
- Prefer diagnostics-first UI.
- If adding stop/restart UI, restrict it to local debug mode only.
- Do not expose dangerous controls in production mode.

### Agent-started test servers for E2E validation

For **non-destructive** UI/E2E validation, agents **MAY and SHOULD** start a controlled local test server themselves. Do not ask the user to start the server unless startup fails for a concrete reason the agent cannot fix.

All of the following conditions must be met:

1. Use `VIOLET_ENV=test`.
2. Use `POSTGRES_DB=blombooru_test`.
3. Use dedicated test storage (`VIOLET_STORAGE_ROOT`), never development storage.
4. Load the user's test env script first: `. "$env:USERPROFILE\.violet\test-env.ps1"`
5. Before starting, run a no-active-server preflight with `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree`. Do not silently choose another port to bypass a stale server; diagnose and report first.
6. Choose a free port dynamically — do NOT default to any fixed port (e.g. 8011). Probe candidate ports (8012–8024) for availability before starting.
7. Record the full server lifecycle details: command, `APP_PORT`, `VIOLET_BASE_URL`, parent/reloader PID, worker/identity PID, process tree, code root, git SHA, `VIOLET_ENV`, DB, storage root, and Python executable.
8. Start the server from the PR branch/worktree being tested. Use `APP_PORT` env var to set the port (`run.py` does not accept a `--port` CLI flag).
9. Only stop the exact identified process tree the agent started — never kill unknown processes. "Exact PID" includes the reloader parent and worker/identity child when `run.py --debug` spawns both.
10. Do not run import, AI tagging, LLM translation, cleanup, reset, delete, truncate, drop, or bulk-update operations.
11. Do not touch iCloud paths or modify VioletTestFixture.
12. If server startup fails, diagnose and report the exact error — do not skip E2E.
13. **Mandatory identity preflight (hard gate):** After the server starts, run `scripts/check_test_server_identity.py` to verify `VIOLET_ENV`, `POSTGRES_DB`, `code_root`, `git_sha`, `git_branch`, `storage_root`, and `python_executable` match the current worktree/branch. Use `--expected-storage-root` and `--expected-python "$PY"` to verify storage root and venv Python. **E2E tests MUST NOT run until identity verification passes.** If the identity check fails, the server is stale or misconfigured — stop it, diagnose, and restart. Never skip E2E due to identity check failure.

**Singleton server policy:** Only one agent-started test server may be running at a time per development session. Before starting a new server, verify no previous V.I.O.L.E.T. server is still running on common local ports. If a port conflict or stale server is detected, diagnose the conflict (PID, command line, process tree, identity) — do not silently pick another port without investigating.

**`run.py --debug` reloader caution:** Debug mode uses uvicorn reload and may spawn parent/reloader and worker child processes. Stopping only a wrapper or parent PID can leave a worker listening. Cleanup must stop only the complete identified server process tree from the current task, then verify the port is no longer `LISTENING`.

**Stale server prevention:** A "stale server" is one serving code from a different commit, branch, or worktree than the current E2E target. Stale servers produce false test results. On Windows, killed processes may leave TCP sockets in LISTENING state for up to 60 seconds. After stopping a server, wait or verify the port is truly free before restarting. Never mark stale-server-induced E2E failures as "pre-existing" or "non-blocking."

The final report must include: working directory, branch, server command, parent/reloader PID, worker/identity PID, process tree, port, `VIOLET_BASE_URL`, environment confirmation (VIOLET_ENV, DB, storage root), identity check result, E2E command, stop/cleanup result, and port-free verification.

Clarification: "Do not kill arbitrary processes" means only stop the exact identified process tree you started. It does **not** mean agents cannot start a test server.

### Destructive DB operation safety (post-incident, 2026-05-10)

All destructive API endpoints (`reset-e2e-test-data`, `missing-media-cleanup`) are protected by:
1. `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` env flag (HTTP 403 without it)
2. Unique `confirm_phrase` per endpoint
3. `dry_run=true` default
4. `logger.warning(...)` audit log before execution

E2E tests that call destructive endpoints must be gated by `VIOLET_ALLOW_DESTRUCTIVE_E2E=1`. Never run a dev server from a git worktree against the shared production DB for destructive E2E tests.

## Do not commit

`.env`, API keys, `data/`, `media/`, `storage/`, `backups/`, model files, `node_modules/`, `test-results/`, `playwright-report/`, traces, screenshots.

## Python/venv identity (hard gate)

**All agent workflows** — server start, test execution, script execution, dependency installation — **MUST use the approved project venv Python.** This is a hard gate, not a suggestion.

**Determining `$PY` (the approved venv Python):**

The rule is: "use the repo-local venv Python." The exact path depends on the platform:

| Environment | `$PY` |
|-------------|-------|
| Windows (user local dev) | `<repo>\venv\Scripts\python.exe` |
| Linux / macOS / cloud | `<repo>/venv/bin/python` or `<repo>/.venv/bin/python` |
| Git worktree (no local venv) | Use the main repo's venv explicitly |

For the current Windows local dev setup:

```powershell
$PY = "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
```

`scripts/check_python_env.py` can auto-infer the venv Python from the repo root when `--expected-python` is omitted. It probes `venv/Scripts/python.exe`, `.venv/Scripts/python.exe`, `venv/bin/python`, `.venv/bin/python` in order. You can also set `VIOLET_EXPECTED_PYTHON` as an env var override.

**Rules:**

1. **Never use the global/system Python** (`C:\Python313\python.exe`, `python.exe` from PATH, or any interpreter outside the project venv). This includes `pip install` — never install packages into the global Python.
2. **Preflight is mandatory.** Before any server start, test run, or script execution, run:

```powershell
& "$PY" scripts/check_python_env.py --expected-python "$PY"
```

The script must exit 0. If it exits 1, stop and diagnose — do not proceed.

3. **Worktrees do not have their own venv.** Always use the main repo venv explicitly.
4. **Test execution** must also use `$PY`: `& "$PY" -m pytest tests/ -v`
5. **Include `sys.executable` in all delivery reports** to prove the correct Python was used.

## Environment

- Windows local dev
- GitHub CLI: `C:\Program Files\GitHub CLI\gh.exe` (usually available as `gh` in PATH)
- If `gh` is not found in PATH, use the absolute path above. Do not claim `gh` is unavailable unless both `where gh` and `& "C:\Program Files\GitHub CLI\gh.exe" --version` fail.
- Dev server: `python run.py --debug` → `http://localhost:8000`
- Database: PostgreSQL 17, `blombooru` on `localhost:5432`

## Test environment

Load the standardized test environment (PowerShell):

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
```

This sets core test variables: `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test`, `VIOLET_STORAGE_ROOT=C:\Users\kyloris\VioletStorage\test`, `VIOLET_TEST_FIXTURE_PATH`, and `APP_PORT=8001`. For E2E runs, agents override in the current session:

```powershell
$env:APP_PORT = "<chosen-free-port>"   # probe 8012-8024 for availability
$env:VIOLET_BASE_URL = "http://127.0.0.1:$($env:APP_PORT)"
$env:VIOLET_RUN_REAL_E2E = "1"
```

Test server: `python run.py --debug` (with test env loaded, `APP_PORT` set to the chosen free port). The `run.py` script reads `APP_PORT` from the environment — it does not accept a `--port` CLI flag.

**Local test output path safety:** Unless the user explicitly authorizes otherwise, CodeX/local agent tests must not use `Z:\`, `\\192.168.71.230\Storage`, or any NAS/network-share path as a test output directory, pytest tmpdir, working directory, staging directory, log directory, or default script output directory. Test artifacts must be written only to repo-local gitignored directories or local machine temporary directories. Do not use fake-looking drive letters or UNC paths to simulate write failures; use `tmp_path`, `tempfile`, or an existing directory/file conflict inside local temp instead.

**Playwright base URL variable:** `VIOLET_BASE_URL` (read by `playwright.config.ts`). Do not use `PLAYWRIGHT_BASE_URL`.

See `docs/test-workflow.md` for test tiers and commands.

## Language policy

| Layer | Language |
|-------|----------|
| User interface | zh-CN first (English fallback) |
| Internal code, API, config, canonical tags | English |
| Core technical docs | English primary |
| Delivery reports / stage summaries | Chinese (zh-CN) |

## Runtime LLM configuration

Runtime tag translation / alias resolution uses configurable OpenAI-compatible LLM via `.env`:
- `TAG_TRANSLATION_LLM_PROVIDER`, `TAG_TRANSLATION_LLM_BASE_URL`, `TAG_TRANSLATION_LLM_API_KEY`, `TAG_TRANSLATION_LLM_MODEL`
- Do not hardcode any specific runtime LLM model.
