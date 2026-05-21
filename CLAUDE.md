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

### Reviewer feedback handling policy

For implementation PRs, reviewer feedback is a controlled handoff point, not an automatic code-change trigger.

1. After PR creation or a meaningful PR update, CodeX must trigger reviewer with exactly `@codex review`.
2. CodeX may collect reviewer feedback and verify whether it applies to the current PR head.
3. CodeX must summarize current-head P1/P2/P3 findings in the final report.
4. CodeX must not automatically modify code based on reviewer feedback.
5. CodeX must stop and report reviewer findings to the user/ChatGPT.
6. User/ChatGPT decides whether to fix now, defer, change implementation strategy, split into another PR, or merge.
7. Automatic reviewer-fix loops are disabled by default and may only be used when the user explicitly authorizes them for a specific PR with a specific round limit and scope.
8. Even when explicitly authorized, automatic fix loops must never push `main`, merge, run destructive operations, mutate source/iCloud/staging/DB unless explicitly approved, change phase scope, or start a new phase.
9. Before triggering reviewer, CodeX must perform a local pre-review / same-class self-audit so reviewer is not used as a substitute for engineering judgment.

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
- [x] Unit/focused tests passed
- [x] Full non-E2E suite passed
- [x] Real dry-run / real audit / smoke validation passed
- [x] Reviewer re-review requested
- [ ] Manual review / user validation if not yet done
```

Checkboxes must reflect reality: do not mark incomplete items complete, and do not omit required gates to make the task list look clean. The `## Reviewer / Codex status` section must state whether reviewer/Codex reviewed the latest head SHA; if pending, say pending. The `## Safety confirmation` section must explicitly state no push main, no merge, no source/iCloud/staging mutation, no cleanup/reset/drop/truncate, no API key exposure, and no forbidden background systems. PR titles should stay consistent: `Phase X.Y: <clear phase title>` or `feat/fix/docs: <clear scope> (Phase X.Y)`.

### Real browser validation (mandatory)

For every feature phase, bug fix, or UI-affecting change, the agent must perform real browser validation before delivery. This applies to changes involving: Admin UI, gallery/media grid, media detail page, search behavior, tag localization, AI tagging/review UI, local library scan workflow, settings/developer tools, user-visible text, thumbnails/fallback images, routing/navigation, any frontend JavaScript behavior.

**Required standard:**

1. Prefer Playwright with system Edge on Windows.
2. Do not rely only on API tests or unit tests when UI behavior is affected.
3. Use a real running local server. **Agents must start a controlled test server themselves** (see "Agent-started test servers" section). Do not ask the user to start the server unless startup fails for a concrete reason.
4. Use the actual app page, not only mocked DOM tests.
5. Verify the relevant user flow end-to-end.
6. If the feature touches local files, scan, thumbnails, or media display, validate with a real local test folder when safe.
7. If real browser validation cannot be run despite best effort, the agent must explicitly explain why (exact error) and provide the closest fallback validation. "Server was not running" is not an acceptable excuse — the agent should have started one.

The delivery report must include a dedicated section: **真实浏览器验收**, containing: 验收方式, 浏览器/Playwright project, URL tested, pages/flows validated, pass/fail result, skipped or not covered items, fallback explanation if real browser validation could not be completed. A phase is not considered complete without this section.

### Phase plan approval rule

For every new major development phase or substantial feature scope, the agent must first produce an implementation plan and wait for explicit user approval before making substantial code changes. Bug fixes and small review-comment fixes may proceed without a separate plan. Major stage-level design changes (classifiers, models, DB schemas, evaluation frameworks) require the plan first.

Plan-only tasks must not create branches, commits, pushes, or PRs unless the user explicitly approves a documentation PR. Deliver plan-only output in chat or as a local untracked `.codex/plans/*.md` draft, then wait for user/ChatGPT approval before implementation.

### Chinese reporting rule

Final user-facing stage summaries and delivery reports must be written in Chinese. This includes: 阶段性总结, 交付报告, 测试结果总结, 风险说明, 本地验收步骤, 已知限制, 下一步建议.

Keep technical identifiers in English: file paths, branch names, PR URLs, API routes, config keys, class/function names, commands, commit messages, PR titles. Code comments may remain English when appropriate.

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
5. Choose a free port dynamically — do NOT default to any fixed port (e.g. 8011). Probe candidate ports (8012–8024) for availability before starting.
6. Record the server PID.
7. Start the server from the PR branch/worktree being tested. Use `APP_PORT` env var to set the port (`run.py` does not accept a `--port` CLI flag).
8. Only stop the exact PID the agent started — never kill unknown processes.
9. Do not run import, AI tagging, LLM translation, cleanup, reset, delete, truncate, drop, or bulk-update operations.
10. Do not touch iCloud paths or modify VioletTestFixture.
11. If server startup fails, diagnose and report the exact error — do not skip E2E.
12. **Mandatory identity preflight (hard gate):** After the server starts, run `scripts/check_test_server_identity.py` to verify `VIOLET_ENV`, `POSTGRES_DB`, `code_root`, `git_sha`, and `storage_root` match the current worktree/branch. Use `--expected-storage-root` to verify storage root. **E2E tests MUST NOT run until identity verification passes.** If the identity check fails, the server is stale or misconfigured — stop it, diagnose, and restart. Never skip E2E due to identity check failure.

**Singleton server policy:** Only one agent-started test server may be running at a time per development session. Before starting a new server, verify no previous agent-started server is still running on any port. If a port conflict is detected, diagnose the conflict (PID, command line) — do not silently pick another port without investigating.

**Stale server prevention:** A "stale server" is one serving code from a different commit, branch, or worktree than the current E2E target. Stale servers produce false test results. On Windows, killed processes may leave TCP sockets in LISTENING state for up to 60 seconds. After stopping a server, wait or verify the port is truly free before restarting. Never mark stale-server-induced E2E failures as "pre-existing" or "non-blocking."

The final report must include: working directory, branch, server command, PID, port, `VIOLET_BASE_URL`, environment confirmation (VIOLET_ENV, DB, storage root), identity check result, E2E command, stop/cleanup result.

Clarification: "Do not kill arbitrary processes" means only stop the exact server PID you started. It does **not** mean agents cannot start a test server.

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
