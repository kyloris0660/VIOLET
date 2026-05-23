# Manual Validation Workflow

This document is the durable local manual validation workflow for V.I.O.L.E.T. after approved import or pipeline phases. It is not a substitute for test-environment E2E validation, and it must not be silently changed. If the startup flow changes, update this document and obtain user/ChatGPT approval.

## When To Use This Flow

Use this flow for local `development` / `blombooru` validation after an approved import, classification, AI tagging, localization, or similar pipeline phase has already completed.

Do not use `. "$env:USERPROFILE\.violet\test-env.ps1"` for this development validation. That script is for isolated test DB/test storage validation with `VIOLET_ENV=test` and `POSTGRES_DB=blombooru_test`.

This workflow is read-only unless a phase explicitly approves runtime mutation. Manual validation must not run DB import, staging copy, classification, AI tagging, localization, cleanup/delete/reset/drop/truncate, Entity Resolver, similarity/clustering, or source/iCloud mutation.

## Current Startup Requirement

Current development manual validation requires:

```powershell
$env:PYTHONPATH = "<repo>\backend"
```

Reason: `run.py` loads `backend.app.main:app`, while some modules still use `app.*` imports. For example, `backend/app/services/source_ingestion_gate.py` imports `app.utils.cloud_files`. Adding `<repo>\backend` to `PYTHONPATH` makes that top-level `app` package importable.

This is an accepted current manual validation requirement. A later import/startup path consistency hardening phase should remove reliance on operator memory.

## Terminal A: Start The Development Server

```powershell
cd C:\Users\kyloris\Documents\AnimeLocalBooru

$PY = "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
& "$PY" scripts/check_python_env.py --expected-python "$PY"

$env:VIOLET_ENV = "development"
$env:POSTGRES_DB = "blombooru"
$env:APP_PORT = "8012"
$env:VIOLET_BASE_URL = "http://127.0.0.1:$($env:APP_PORT)"

$backendPath = Join-Path (Get-Location) "backend"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$backendPath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $backendPath
}

# Disable uncontrolled automatic translation/background behavior during manual validation.
$env:AI_TAGGING_AUTO_LOCALIZATION = "false"
$env:TAG_TRANSLATION_BACKGROUND_ENABLED = "false"
$env:TAG_TRANSLATION_AUTO_ENABLED = "false"
$env:TAG_TRANSLATION_LLM_ENABLED = "false"

& "$PY" run.py --debug
```

Keep this terminal open while validating. Stop the server with `Ctrl+C`.

## Terminal B: Read-only Validation Shell

```powershell
cd C:\Users\kyloris\Documents\AnimeLocalBooru

$PY = "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
$env:VIOLET_BASE_URL = "http://127.0.0.1:8012"
$env:PYTHONPATH = "C:\Users\kyloris\Documents\AnimeLocalBooru\backend"

git status --short
git log --oneline --decorate -3
& "$PY" scripts/check_python_env.py --expected-python "$PY"

$sha = (git rev-parse --short HEAD).Trim()
& "$PY" scripts/check_test_server_identity.py `
  --base-url $env:VIOLET_BASE_URL `
  --expected-env development `
  --expected-db blombooru `
  --expected-code-root "C:\Users\kyloris\Documents\AnimeLocalBooru" `
  --expected-git-sha $sha `
  --expected-branch main `
  --expected-storage-root "C:\Users\kyloris\Documents\AnimeLocalBooru" `
  --expected-python "$PY" `
  --admin-username "<local-dev-admin-user>" `
  --admin-password "<local-dev-admin-password>"
```

Use the local development credentials documented in the project instructions. Do not paste real secrets into public reports.

## API Smoke

```powershell
$base = $env:VIOLET_BASE_URL
Invoke-RestMethod "$base/api/media/?page=1&limit=1"
Invoke-RestMethod "$base/api/media/?page=1&limit=5&content_class=anime"

$login = Invoke-RestMethod -Method POST "$base/api/admin/login" `
  -ContentType "application/json" `
  -Body '{"username":"<local-dev-admin-user>","password":"<local-dev-admin-password>"}'
$headers = @{ Authorization = "Bearer $($login.access_token)" }

Invoke-RestMethod "$base/api/admin/content-classification/stats" -Headers $headers
Invoke-RestMethod "$base/api/admin/ai-tags/review?limit=5" -Headers $headers
Invoke-RestMethod "$base/api/admin/tag-localization/stats" -Headers $headers
```

## Optional Source-label DB Checks

Use this pattern when a phase report gives a source label, expected imported/staged-success count, excluded row IDs, and expected classification/localization facts. Keeping phase-specific values in the shell is often enough for one-time validation, but helper automation is allowed when it reduces operator error or improves reproducibility.

```powershell
$sourceLabel = "<phase-source-label>"
$expectedCount = <expected-source-label-count>

@"
from sqlalchemy import func
from backend.app.database import SessionLocal
from backend.app.models import Media

source_label = r'''$sourceLabel'''
expected = int($expectedCount)

db = SessionLocal()
try:
    count = db.query(func.count(Media.id)).filter(Media.source == source_label).scalar() or 0
    dist_rows = (
        db.query(Media.content_class, func.count(Media.id))
        .filter(Media.source == source_label)
        .group_by(Media.content_class)
        .all()
    )
    dist = {getattr(key, "value", str(key) if key else "unclassified"): int(value) for key, value in dist_rows}
    print({"source_label": source_label, "count": int(count), "expected": expected, "distribution": dist})
finally:
    db.close()
"@ | & "$PY" -
```

For Phase 3.8d-I7, the source label is `violet:phase3.8d:i7:staged-success` and the accepted source-label count is `994`.

## Browser Manual Checks

Open `$env:VIOLET_BASE_URL` in a browser and verify:

- Gallery loads.
- Media detail page opens.
- Original file endpoint and thumbnail endpoint work.
- Content-class filters work when relevant.
- Admin content classification, AI tag review, and tag localization pages load when relevant.
- Localized tag display/search works when relevant.
- No obvious `500`, traceback, JSON serialization, or console error appears.

## Must-check Items

Adjust counts and source labels to the phase report being validated.

- Source-label count matches the phase report.
- Failed/deferred rows are excluded from DB import and downstream eligibility.
- Classification distribution matches the phase report.
- AI tagging eligible/ineligible split matches the phase report.
- General/meta localization remaining count is `0` when the phase requires completed localization.
- API/browser/admin smoke passes.
- Server logs show no `ERROR`, `Traceback`, `500`, or serialization error.
- No source/iCloud mutation.
- No Entity Resolver, similarity/clustering, or Phase 4 work.

## Stop-and-report Conditions

Stop validation and report if any of these occur:

- Server fails even with `PYTHONPATH=<repo>\backend`.
- Server identity mismatch: wrong `VIOLET_ENV`, DB, branch, git SHA, code root, storage root, or Python executable.
- Source-label count mismatch.
- Failed/deferred rows appear imported or downstream eligible.
- DB-facing or public output shows local absolute paths, `file://`, source/iCloud paths, or secrets.
- API returns `500`, traceback, or JSON serialization errors.
- Browser shows major gallery/detail/admin breakage.

## Stop Server And Confirm Port Release

Stop Terminal A with `Ctrl+C`, then confirm the port is no longer listening:

```powershell
Get-NetTCPConnection -LocalPort 8012 -State Listen -ErrorAction SilentlyContinue
```

## Tooling Lifecycle Note

Manual validation may be supported by one-off or phase-scoped helper scripts, especially when they reduce operator error, catch issues earlier, or make a risky validation step reproducible.

Such helpers must clearly declare whether they are:

- local-only ignored artifacts
- committed phase-scoped operational runners
- reusable validation/safety tools
- production reusable code
- public reports / handoff documentation

If a helper is phase-scoped, reviewer feedback should be evaluated against that lifecycle. It must be safe, privacy-preserving, data-integrity-preserving, and truthful for the current phase, but it does not need to support every future phase or arbitrary parameter combination. Do not promote a phase-scoped helper into reusable tooling unless user/ChatGPT explicitly approves that lifecycle change.
