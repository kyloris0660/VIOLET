# E2E Testing Guide

## Overview

V.I.O.L.E.T. uses [Playwright](https://playwright.dev/) for end-to-end browser testing. Tests are split into two tiers:

| Tier | Description | Timeout | Default |
|------|-------------|---------|---------|
| **Smoke** | Fast UI + API validation | 60s / test | Always runs |
| **Real E2E** | Full VioletTest100 workflow (scan, AI tag, LLM translate) | 10–20 min / test | Skipped unless `VIOLET_RUN_REAL_E2E=1` |

## Browser Strategy

### Local Development (Windows)

Use the system-installed Microsoft Edge (preferred) or Chrome — no separate browser download needed:

```powershell
npm run e2e          # smoke tests only, Edge
npm run e2e:chrome   # all tests, system Chrome
```

### CI / Linux

Use Playwright's bundled Chromium:

```bash
npx playwright install chromium
npm run e2e:chromium
```

### Projects in `playwright.config.ts`

| Project | Channel | Use case |
|---------|---------|----------|
| `edge` | `msedge` | Local Windows (default) |
| `chrome` | `chrome` | Local fallback |
| `chromium` | bundled | CI environments |

## Timeout Configuration

| Scope | Value |
|-------|-------|
| Test (smoke) | 60 s |
| Test (real E2E) | 600 s (10 min) |
| Action | 10 s |
| Navigation | 30 s |
| Expect assertion | 10 s |

## npm Scripts

```
npm run e2e          # smoke tests only (Edge)
npm run e2e:all      # all tests including skipped real E2E (Edge)
npm run e2e:real     # real E2E only (requires VIOLET_RUN_REAL_E2E=1)
npm run e2e:edge     # all tests (Edge)
npm run e2e:chrome   # all tests (Chrome)
npm run e2e:chromium # all tests (bundled Chromium)
npm run e2e:headed   # headed mode for debugging
npm run e2e:report   # open HTML report
```

## Running Real E2E

```powershell
$env:VIOLET_RUN_REAL_E2E="1"
$env:VIOLET_TEST_LIBRARY_PATH="C:\Users\kyloris\Pictures\VioletTest100_2"
npm run e2e:real
```

This runs the full workflow: reset data → scan library → wait for AI tagging → check localization → batch translate.

## Test Files

| File | Tier | Coverage |
|------|------|----------|
| `admin-content.spec.ts` | Smoke | Admin panel sections, thumbnail buttons, upload area |
| `dev-tools-config.spec.ts` | Smoke | Config diagnostics, API key masking |
| `tag-localization.spec.ts` | Smoke | LLM status, translation stats, dry-run batch |
| `tag-translation-worker.spec.ts` | Smoke + Real | Worker status, pause/resume, run-now, UI panel |
| `ai-tagging-jobs.spec.ts` | Smoke | Job listing, auto-config |
| `ai-tag-review.spec.ts` | Smoke | Review endpoint |
| `chinese-search.spec.ts` | Smoke | V.I.O.L.E.T. branding, tag search, Chinese search |
| `media-detail-provenance.spec.ts` | Smoke | Media detail page |
| `reset-e2e-dry-run.spec.ts` | Smoke | Reset dry-run, dangerous path rejection |
| `local-library-scan.spec.ts` | Smoke | Scan dry-run |
| `violet-test100-real.spec.ts` | Real | Full workflow with VioletTest100_2 |

## Debugging

- Failed tests save screenshots to `test-results/`
- Use `--headed` to watch the browser: `npx playwright test --project=edge --headed`
- Use `--trace on` for detailed traces: `npx playwright test --project=edge --trace on`
- Console logs: add `page.on('console', msg => console.log(msg.text()))` in tests

Do **not** commit `test-results/`, `playwright-report/`, or `*.trace.zip` to git.
