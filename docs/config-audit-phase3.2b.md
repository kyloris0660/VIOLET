# Configuration Audit — Phase 3.2b

> Audited 2026-05-12 against `main` (commit `1b31ff0`).

## 1. Actionable Audit Table

Classification legend:

| Code | Meaning | Action |
|------|---------|--------|
| `keep_safety_gate` | Safety / sanity upper bound — intentional | None |
| `document_default` | Already env-var driven; document the default | Add to `.env.example` comment |
| `make_test_dynamic` | Test assertion should not assume a specific value | Fix assertion |
| `leave_as_fixture` | Test fixture constant or script default — expected | None |
| `naming_inconsistency` | Env var naming is inconsistent across files | Standardize |

### 1.1 Batch Size / Limit Defaults (config.py)

All values below are already env-var driven via `os.getenv()`. No code change needed — just documentation.

| Env Var | Default | Used By | Classification |
|---------|---------|---------|---------------|
| `AI_TAGGING_BATCH_MAX_ITEMS` | 10 | AI tagging batch endpoint | `document_default` |
| `AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS` | 20 | Auto-tag after import | `document_default` |
| `TAG_TRANSLATION_BATCH_MAX_ITEMS` | 50 | LLM batch translation | `document_default` |
| `TAG_TRANSLATION_AUTO_MAX_ITEMS` | 20 | Auto translation | `document_default` |
| `TAG_TRANSLATION_BACKGROUND_BATCH_SIZE` | 100 | Background worker chunk | `document_default` |
| `TAG_TRANSLATION_BACKGROUND_MAX_PER_RUN` | 500 | Background worker cap | `document_default` |
| `TAG_TRANSLATION_BACKGROUND_DAILY_LIMIT` | 5000 | Daily translation limit | `document_default` |
| `TAG_TRANSLATION_BACKGROUND_ERROR_LIMIT` | 5 | Error threshold before stop | `document_default` |
| `TAG_TRANSLATION_BACKGROUND_INTERVAL_SECONDS` | 300 | Worker polling interval | `document_default` |
| `CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS` | 100 | Classification batch | `document_default` |
| `CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS` | 50 | Auto-classify after import | `document_default` |
| `ENTITY_ALIAS_BATCH_SIZE` | 20 | Entity alias batch | `document_default` |
| `ENTITY_ALIAS_MAX_PER_RUN` | 100 | Entity alias cap | `document_default` |

### 1.2 AI Thresholds (config.py)

| Env Var | Default | Classification |
|---------|---------|---------------|
| `AI_GENERAL_THRESHOLD` | 0.35 | `document_default` |
| `AI_CHARACTER_THRESHOLD` | 0.65 | `document_default` |
| `AI_RATING_THRESHOLD` | 0.50 | `document_default` |
| `AI_SUGGESTION_THRESHOLD` | 0.20 | `document_default` |
| `CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD` | 5 | `document_default` |
| `CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD` | 0.5 | `document_default` |
| `CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN` | 0.005 | `document_default` |

### 1.3 API Route Query Param Limits

These are FastAPI `Query(le=...)` validators — intentional safety gates.

| File | Param | Limit | Classification |
|------|-------|-------|---------------|
| `routes/admin/ai_tagging_jobs.py` | `limit` | `le=500` | `keep_safety_gate` |
| `routes/admin/tag_localization.py` | `max_items` | `le=1000` | `keep_safety_gate` |
| `routes/admin/content_classification.py` | `max_items` | `le=500` | `keep_safety_gate` |
| `routes/admin/entity_alias_resolver.py` | `limit` | `le=200` | `keep_safety_gate` |
| `routes/media.py` | `limit` | `le=200` | `keep_safety_gate` |
| `routes/tags.py` | `limit` | `le=1000` | `keep_safety_gate` |

### 1.4 Fallback HTTP Codes

| Location | Value | Classification |
|----------|-------|---------------|
| `llm_translation_provider.py:26` | `{408, 429, 500, 502, 503, 504}` | `keep_safety_gate` |

Standard server-error + rate-limit codes. Changing these would alter fallback behavior incorrectly.

### 1.5 E2E Test Assertions (FIXED in this PR)

| File | Old | Intermediate | Final | Classification |
|------|-----|-------------|-------|---------------|
| `tag-localization.spec.ts:18-23` | `.toBe(200)` | type + range [1, 10000] | `expectPositiveInteger()` — typeof + isFinite + isInteger + >=1, no upper bound | `make_test_dynamic` — **fixed** |
| `ai-tagging-jobs.spec.ts:17-23` | `.toBe(200)` / `.toBe(true)` | type + range [1, 10000] | `expectPositiveInteger()` — typeof + isFinite + isInteger + >=1, no upper bound | `make_test_dynamic` — **fixed** |

**Codex P2 resolution:** The intermediate `[1, 10000]` upper bound was an artificial limit not backed by any backend config cap. Codex review correctly flagged this as P2. Replaced with `expectPositiveInteger()` that validates only product-guaranteed properties: the value is a finite integer >= 1. No upper bound is asserted because `config.py` imposes no hard maximum on batch/auto-tag limits.

### 1.6 Port Numbers

| Location | Value | Purpose | Classification |
|----------|-------|---------|---------------|
| `config.py` | `APP_PORT=8000` default | Dev server | `document_default` |
| `.env.test.example` | `APP_PORT=8001` | Test server | `leave_as_fixture` |
| `playwright.config.ts` | `localhost:8000` fallback | baseURL if `VIOLET_BASE_URL` unset | `leave_as_fixture` |
| `CLAUDE.md` | `8011` recommended | Agent E2E port | `leave_as_fixture` |
| `setup_test_db.py` | `5432` default | PostgreSQL | `leave_as_fixture` |

### 1.7 Env Flag Naming Inconsistency

| File | Var Name | Purpose |
|------|----------|---------|
| `tag-localization.spec.ts` | `VIOLET_RUN_REAL_LLM_TESTS` | Gate LLM E2E tests |
| `entity-alias-resolver.spec.ts` | `VIOLET_RUN_REAL_LLM_E2E` | Gate LLM E2E tests |

These gate the same category of test (real LLM calls in E2E) but use different env var names. Classification: `naming_inconsistency`.

**Recommendation**: Standardize to `VIOLET_RUN_REAL_LLM_E2E` (matches the `_E2E` suffix convention used by `VIOLET_RUN_REAL_E2E`). This is a Phase 3.3+ cleanup — not changing in this PR to avoid scope creep, but documented for tracking.

### 1.8 Hardcoded Paths

| Location | Value | Classification |
|----------|-------|---------------|
| `.env.test.example` | `C:\Users\kyloris\...` | `leave_as_fixture` — example file, user adjusts |
| `CLAUDE.md` | `C:\Users\kyloris\...` | `leave_as_fixture` — agent instructions |
| `scripts/local_full_pipeline_smoke.py:72` | `FIXTURE_SUBDIRS = ("anime", "non_anime", "mixed")` | `leave_as_fixture` — fixture convention |

### 1.9 Cookie / Session

| Location | Value | Classification |
|----------|-------|---------------|
| `auth.py` | `max_age=86400*30` (30 days) | `keep_safety_gate` |

---

## 2. Content Classification Attribution Analysis

### 2.1 Classification Trigger Paths

Four code paths can set `content_class` on a Media item:

| Path | Trigger | Config Gate | Commits to DB? |
|------|---------|-------------|-----------------|
| **AI tagging inline** | `ai_tagging_job_service.py:156` — `classify_from_predictions()` | `CONTENT_CLASSIFICATION_ENABLED` only | No (parent commits) |
| **Auto-after-import** | `classification_job_service.py:231` — `create_auto_classification_job_after_scan()` | `CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT` AND `CONTENT_CLASSIFICATION_ENABLED` | Yes (worker commits) |
| **Explicit batch** | Admin UI "Classify" button → classification job | `CONTENT_CLASSIFICATION_ENABLED` | Yes (worker commits) |
| **Manual** | Admin sets class directly via media edit | None | Yes (route commits) |

### 2.2 Key Finding: AI Tagging Inline Classification

`ai_tagging_job_service.py` line 156 checks only `CONTENT_CLASSIFICATION_ENABLED`, NOT `AUTO_AFTER_IMPORT`. This means:
- If `CONTENT_CLASSIFICATION_ENABLED=true` and a user runs an AI tagging job, items will also get classified inline — regardless of `AUTO_AFTER_IMPORT` setting.
- This is **intentional design**: inline classification reuses already-loaded WDv3 predictions at zero additional cost. The `AUTO_AFTER_IMPORT` flag controls only the separate post-scan auto-classification job.

### 2.3 Attribution Limitations

The `content_class_source` column tracks classification **method** ("clip", "heuristic", "manual") but NOT which **job** triggered it. The stats API endpoint (`/api/admin/content-classification/stats`) reports totals only — no breakdown by source path.

Exact per-job attribution requires either:
1. DB query correlating `updated_at` timestamps with job execution times
2. Job log analysis (structured logging captures which items each job processed)

This is a known data-model gap, not a bug. Recommended for Phase 4 if detailed attribution becomes needed.

---

## 3. Report Path Mismatch — Root Cause

Phase 3.2a report referenced "batch_a / batch_b / batch_c" fixture paths. Investigation confirms:
- `local_full_pipeline_smoke.py` uses `FIXTURE_SUBDIRS = ("anime", "non_anime", "mixed")` — never "batch_*".
- `_write_report()` uses actual runtime paths from CLI args, not template labels.
- "batch_a/b/c" does NOT appear anywhere in the codebase (grepped all files).

**Root cause**: The "batch_a/b/c" labels originated from external notes or a manually-written summary, not from the smoke script or any codebase artifact. No code fix needed.

---

## 4. `.env.example` Coverage Gap

Phase 3.x added many config keys not yet documented in `.env.example`:

| Missing from `.env.example` | Default | Added in |
|------------------------------|---------|----------|
| `AI_GENERAL_THRESHOLD` | 0.35 | Phase 3.0 |
| `AI_CHARACTER_THRESHOLD` | 0.65 | Phase 3.0 |
| `AI_RATING_THRESHOLD` | 0.50 | Phase 3.0 |
| `AI_SUGGESTION_THRESHOLD` | 0.20 | Phase 3.0 |
| `AI_TAGGING_BATCH_MAX_ITEMS` | 10 | Phase 3.0 |
| `AI_AUTO_TAG_AFTER_IMPORT` | false | Phase 3.0 |
| `AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS` | 20 | Phase 3.0 |
| `AI_MODEL_NAME` | wd-swinv2-tagger-v3 | Phase 3.0 |
| `TAG_TRANSLATION_BATCH_MAX_ITEMS` | 50 | Phase 3.1 |
| `TAG_TRANSLATION_AUTO_ENABLED` | false | Phase 3.1 |
| `TAG_TRANSLATION_AUTO_MAX_ITEMS` | 20 | Phase 3.1 |
| `TAG_TRANSLATION_LLM_FALLBACK_API_KEY` | (empty) | Phase 3.1.2c |
| `TAG_TRANSLATION_LLM_FALLBACK_MODEL` | (empty) | Phase 3.1.2c |
| `TAG_TRANSLATION_LLM_FALLBACK_BASE_URL` | (empty) | Phase 3.1.2c |
| `ENTITY_ALIAS_RESOLVER_ENABLED` | false | Phase 3.1.1b |
| `ENTITY_ALIAS_BATCH_SIZE` | 20 | Phase 3.1.1b |
| `ENTITY_ALIAS_MAX_PER_RUN` | 100 | Phase 3.1.1b |
| `CONTENT_CLASSIFICATION_ENABLED` | false | Phase 3.1 |
| `CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS` | 100 | Phase 3.1 |
| `CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT` | false | Phase 3.1 |
| `CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS` | 50 | Phase 3.1 |
| `CONTENT_CLASSIFICATION_METHOD` | clip | Phase 3.1 |
| `CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN` | 0.005 | Phase 3.1 |

**Action**: Add commented-out entries to `.env.example` and `.env.production.example` in this PR.

---

## 5. Stale Server Root Cause Analysis (Phase 3.2b)

### 5.1 Incident Summary

During Phase 3.2b E2E validation, 3 out of 14 `gallery-content-filter.spec.ts` tests failed when running against port 8011. Investigation revealed the server on port 8011 was serving code from a **different commit/worktree** than the one being tested — a "stale server."

### 5.2 Root Cause

1. A previous agent session started a test server on port 8011 from an older worktree.
2. The current session assumed port 8011 was available and attempted to reuse it without verifying server identity.
3. The stale server did not have the gallery-content-filter feature code, causing 3 E2E failures.
4. Initial analysis incorrectly proposed marking these failures as "pre-existing non-blocking" — this was rejected by the user.

### 5.3 Resolution

1. Started a fresh server on port 8023 from the correct worktree.
2. Ran `scripts/check_test_server_identity.py` to verify `VIOLET_ENV`, `POSTGRES_DB`, `code_root`, and `git_sha`.
3. Re-ran all 14 gallery-content-filter tests — all passed.

### 5.4 Codified Rules (post-incident)

To prevent recurrence, the following rules are now **mandatory** in CLAUDE.md, AGENTS.md, and docs/test-workflow.md:

| Rule | Description |
|------|-------------|
| **Mandatory identity preflight** | `scripts/check_test_server_identity.py` must pass before any E2E tests run. This is a hard gate. |
| **No default port** | Do not default to port 8011 or any fixed port. Probe 8012–8024 for availability. |
| **Singleton policy** | Only one agent-started test server per session. Diagnose conflicts, do not silently pick another port. |
| **Stale server = invalid results** | Never mark E2E failures as "pre-existing" or "non-blocking" if the server identity has not been verified. |
| **Cannot skip E2E** | Port conflicts, stale servers, or identity check failures do not justify skipping E2E. Diagnose and fix. |
| **Windows TCP delay** | Killed processes may leave sockets in LISTENING state for ~60s. Verify port is free before restarting. |
| **APP_PORT env var** | `run.py` reads `APP_PORT` from the environment — it does not accept a `--port` CLI flag. |
