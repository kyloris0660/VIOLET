# Phase 3.8a - Classification-First Medium E2E Workflow Plan

Date: 2026-05-20
Branch: `phase3.8a-classification-first-e2e-plan`
Base commit: `b41fa4da8831eea857601e43e45200efe99ed90e`
Scope: plan only

## Executive Summary

Phase 3.8a formalizes the next workflow before Phase 4 or any similarity/entity work starts. The current project has validated import, AI tagging, localization, and content classification separately, but the final scalable order has not yet been implemented as one guarded workflow.

Required workflow:

1. candidate manifest / candidate selection
2. staging copy
3. pre-import audit
4. DB import
5. content classification
6. eligible media selection: `anime` + `unknown`
7. AI tagging only eligible media
8. localization only eligible-derived `general` / `meta` tags
9. post-run validation
10. browser/API smoke
11. report

This phase does not implement workflow code and does not run import, classification, AI tagging, localization, Entity Resolver, similarity, cleanup, reset, delete, drop, truncate, or DB/storage/source mutations.

## Source Verification

### Main / PR #51

- `git fetch origin`: updated `origin/main` from `d2020d5` to `b41fa4d`.
- `git switch main`: switched from `phase3.7-tier1000-classification-scope-gate` to `main`.
- `git pull --ff-only origin main`: fast-forwarded local `main` to `b41fa4d`.
- `git log --oneline --decorate -10`: `b41fa4d (HEAD -> main, origin/main, origin/HEAD) Phase 3.7: Tier-1000 classification validation and tag scope gate (#51)`.
- `gh pr view 51 --json number,state,mergedAt,mergeCommit,url,title`: PR #51 is `MERGED`, merged at `2026-05-20T10:10:17Z`, merge commit `b41fa4da8831eea857601e43e45200efe99ed90e`, URL `https://github.com/kyloris0660/AnimeLocalBooru/pull/51`.
- `git rev-parse HEAD` and `git rev-parse origin/main` both returned `b41fa4da8831eea857601e43e45200efe99ed90e`.
- `git status --short`: no tracked modifications; only untracked local `.claude/`, `.codex/`, historical report files, and server logs.

### Python Identity

Commands:

```powershell
& "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe" scripts/check_python_env.py --expected-python "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
& "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe" -c "import sys; print(sys.executable); print(sys.version)"
```

Result:

- `Python env preflight: PASS`
- `sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`
- `python-version: 3.12.0`
- `is-venv: True`

### Docs Read

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/test-workflow.md`

### Reports Read

- `docs/reports/phase-3.5-tier1000-import-summary.json`
- `docs/reports/phase-3.5-tier1000-post-import-audit.md`
- `docs/reports/phase-3.6-tier1000-ai-tagging-summary.json`
- `docs/reports/phase-3.6-tier1000-localization-summary.json`
- `docs/reports/phase-3.6-tier1000-validation.md`
- `docs/reports/phase-3.7-tier1000-classification-summary.json`
- `docs/reports/phase-3.7-tag-scope-gate.md`
- `docs/reports/phase-3.7-media-api-validation-summary.json`
- `docs/reports/phase-3.7-tier1000-classification-validation.md`

### Code Inspected

- `scripts/import_staged_manifest.py`
- `scripts/run_phase36_tier1000_ai_localization.py`
- `scripts/run_phase37_tier1000_classification_scope_gate.py`
- `scripts/validate_phase37_media_api_smoke.py`
- `backend/app/services/classification_job_service.py`
- `backend/app/routes/admin/content_classification.py`
- `backend/app/services/ai_tagging_job_service.py`
- `backend/app/services/ai_tagging_service.py`
- `backend/app/routes/admin/ai_tagging_jobs.py`
- `backend/app/services/tag_localization_service.py`
- `backend/app/services/tag_translation_worker.py`
- `backend/app/routes/admin/tag_localization.py`
- `backend/app/routes/media.py`
- `backend/app/routes/search.py`
- `backend/app/utils/media_helpers.py`
- `backend/app/utils/search_parser.py`
- related tests: `tests/test_import_staged_manifest.py`, `tests/test_phase36_tier1000_ai_localization.py`, `tests/test_phase37_tier1000_classification_scope_gate.py`, `tests/test_ai_tagging_content_class_filter.py`, `tests/test_ai_tagging_localization_gate.py`, `tests/test_content_classification.py`, `tests/test_media_metadata_serialization.py`

## Current Verified State

### Phase 3.5

Verified from `phase-3.5-tier1000-import-summary.json` and `phase-3.5-tier1000-post-import-audit.md`:

- Expected copy count: `1000`
- Imported media rows: `995`
- Same-hash duplicates skipped: `5`
- Failed: `0`
- Media count: `0 -> 995`
- Post-import audit:
  - DB rows found: `995`
  - Original files found: `995`
  - Thumbnails found: `995`
  - Missing count: `0`
  - Source label mismatches: `0`
- Public DB/storage labels are redacted as `app_storage`; full staging/source paths are not stored in public reports.
- No AI tagging, classification, localization, or Entity Resolver execution during Phase 3.5.

### Phase 3.6

Verified from `phase-3.6-tier1000-ai-tagging-summary.json`, `phase-3.6-tier1000-localization-summary.json`, and `phase-3.6-tier1000-validation.md`:

- AI tagging target: `995` Phase 3.5 media.
- Processed: `995`
- Failed: `0`
- Confirmed AI tag associations added: `41416`
- AI suggestion associations added: `11938`
- `media_tags` delta from AI: `53354`
- Tag row delta: `1301`
- Media with AI tags delta: `995`
- Classification job delta during AI: `0`
- Translation job delta during AI: `0`
- Auto-localization status: `skipped_auto_localization_disabled`
- Controlled localization:
  - Translated: `1196`
  - Failed: `0`
  - Target visual/general/meta missing translations after localization: `0`
  - Remaining proper-noun candidates: `102`
  - Translation job delta: `1`
- Content classification was intentionally not run during Phase 3.6.
- Entity Resolver was not run during Phase 3.6.

### Phase 3.7

Verified from `phase-3.7-tier1000-classification-summary.json`, `phase-3.7-tag-scope-gate.md`, `phase-3.7-media-api-validation-summary.json`, and `phase-3.7-tier1000-classification-validation.md`:

- Target media: `995`
- Classified: `995`
- Unclassified: `0`
- Failed: `0`
- Distribution:
  - `anime`: `948`
  - `unknown`: `21`
  - `non_anime`: `26`
  - `illustration`: `0`
- Tag-derived gate:
  - Eligible: `969` (`anime + unknown`)
  - Ineligible: `26`
- Existing Phase 3.6 AI associations on ineligible media:
  - Ineligible media with AI tags: `26`
  - AI associations on ineligible media: `771`
  - Distinct AI tags on ineligible media: `387`
- No cleanup or tag/media deletion was performed.
- Media API closeout:
  - Metadata endpoint sweep: `995 checked, 995 success, 0 failed`
  - Media detail endpoint sweep: `995 checked, 995 success, 0 failed`
  - Thumbnail endpoint sweep: `995 checked, 995 success, 0 failed`
  - File endpoint sample: `65 checked, 65 success, 0 failed`
  - Content-class filters: `anime`, `unknown`, `non_anime`, `anime,unknown` all passed
  - Search/localization smoke: `0` failures
  - AI review/tag API smoke: `0` failures

## Branch Hygiene Audit

### Summary

- Local branches before cleanup: `51`
- Remote branches before cleanup: `42` excluding `origin/HEAD`
- Open PR heads before cleanup: `[]`
- Local branches after cleanup: `20`
- Remote branches after cleanup: `13` excluding `origin/HEAD`
- Local branches deleted: `31`
- Remote branches deleted: `29`
- Force delete used: `no`
- Open PR branch deleted: `no`
- Worktree branch deleted: `no`
- Unmerged branch deleted: `no`
- `main` touched directly: `no`

### Deleted Local Branches

`claude/brave-fermat-932def`, `claude/eloquent-ishizaka-c15518`, `claude/infallible-pike-f67433`, `claude/infallible-yonath-bdce85`, `claude/naughty-torvalds-0df58c`, `claude/recursing-pike-e3b489`, `claude/relaxed-lewin-148ee5`, `claude/serene-bouman-85dab7`, `claude/vibrant-kare-d3eb80`, `hotfix-unicode-mime-scan-import`, `phase2.4-icloud-safe-ingestion`, `phase3.0.1-repo-hygiene-cleanup`, `phase3.1.1c-local-full-pipeline-smoke`, `phase3.1.2c-server-and-entity-resolver-hardening`, `phase3.1-clip-anime-classifier`, `phase3.2b-pilot-hardening-config-audit`, `phase3.2c-medium-pilot-prep`, `phase3.2d-python-env-hardening`, `phase3.2e-server-runtime-python-identity`, `phase3.2f-model-proxy-runtime-hardening`, `phase3.2g-ai-tagging-scope-localization-hardening`, `phase3.2g-config-precedence-hardening`, `phase3.2j-manual-translation-corrections`, `phase3.3a.1-icloud-candidate-manifest`, `phase3.3a-tier1000-pilot-plan`, `phase3.3b-tier1000-staging-copy`, `phase3.4-tier1000-audit-clean`, `phase3.5-tier1000-db-import`, `phase3.6-tier1000-ai-tagging-localization`, `phase3.7-tier1000-classification-scope-gate`, `phase3-anime-filtering-foundation`

### Deleted Remote Branches

`cursor/setup-dev-env-72e8`, `feat/phase-2.3-auto-tag-after-import`, `hotfix-storage-root-containment`, `hotfix-unicode-mime-scan-import`, `phase2.1.2-ai-tagging-session-hotfix`, `phase2.2.1a-branding-acceptance-cleanup`, `phase2.2.2-dynamic-tag-localization`, `phase2.2.2a-auto-tag-localization`, `phase2.2.2b-localization-dependency-hotfix`, `phase2.3a-dev-e2e-tools-config-diagnostics`, `phase2.4-icloud-safe-ingestion`, `phase2-tag-metadata-foundation`, `phase3.0.1-repo-hygiene-cleanup`, `phase3.1-clip-anime-classifier`, `phase3.1.1c-local-full-pipeline-smoke`, `phase3.2d-python-env-hardening`, `phase3.2e-server-runtime-python-identity`, `phase3.2f-model-proxy-runtime-hardening`, `phase3.2g-ai-run-isolation-storage-identity`, `phase3.2g-ai-tagging-scope-localization-hardening`, `phase3.2g-config-precedence-hardening`, `phase3.2j-manual-translation-corrections`, `phase3.3a-tier1000-pilot-plan`, `phase3.3a.1-icloud-candidate-manifest`, `phase3.3b-tier1000-staging-copy`, `phase3.4-tier1000-audit-clean`, `phase3.5-tier1000-db-import`, `phase3.6-tier1000-ai-tagging-localization`, `phase3.7-tier1000-classification-scope-gate`

### Kept Branches

- `main`: active base branch.
- Worktree-bound local branches and their matching remote branches were kept.
- Local branches with unique or ambiguous commits were kept: `hotfix-storage-root-containment`, `phase3.2g-ai-run-isolation-storage-identity`, `review-pr-21-runtime`, `review-pr-22-runtime`, `review-pr-24-runtime`, `review-pr-25-runtime`, `review-pr-32`.
- `hotfix-storage-root-containment` matched merged PR #27, but `git branch -d` refused because Git did not consider it fully merged locally. It was kept because force delete is forbidden.

### Branch Audit Table

| branch | local/remote | head SHA | open PR? | merged PR? | worktree? | merged into main? | decision | reason |
|---|---|---:|---|---|---|---|---|---|
| claude/brave-fermat-932def | local | 4e11b56 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/eloquent-ishizaka-c15518 | local | fe81f6b | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/gifted-ellis-aef579 | local | 5b12d4d | no | no | yes | yes | KEEP_WORKTREE | branch attached to git worktree |
| claude/infallible-pike-f67433 | local | a044df0 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/infallible-yonath-bdce85 | local | 143678b | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/naughty-torvalds-0df58c | local | e5dfa23 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/nifty-gauss-228de9 | local | ee59f42 | no | #35 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| claude/recursing-pike-e3b489 | local | 94f90f8 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/relaxed-lewin-148ee5 | local | 1e95885 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/serene-bouman-85dab7 | local | ce4369f | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| claude/stoic-fermat-4ea768 | local | 23c4c3b | no | #23 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| claude/vibrant-kare-d3eb80 | local | 5a6ba81 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| fix-llm-proxy-and-deepseek-fallback | local | b25d23a | no | #22 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| hotfix-storage-root-containment | local | 7e2de03 | no | #27 | no | no | KEEP_UNMERGED | non-force `git branch -d` refused; force delete not used |
| hotfix-unicode-mime-scan-import | local | 022f3ec | no | #30 | no | no | DELETE_LOCAL_SAFE | merged PR #30 head matches branch head |
| main | local | b41fa4d | no | no | yes | yes | KEEP_ACTIVE | main branch |
| phase2.3e-proper-noun-alias-resolver | local | 3f34e96 | no | #20 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase2.4-icloud-safe-ingestion | local | 4927740 | no | #21 | no | no | DELETE_LOCAL_SAFE | merged PR #21 head matches branch head |
| phase3.0.1-repo-hygiene-cleanup | local | 3bc2979 | no | #24 | no | no | DELETE_LOCAL_SAFE | merged PR #24 head matches branch head |
| phase3.1.1a-env-db-storage-safety | local | a4031b5 | no | #26 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase3.1.1b-fixture-e2e-workflow | local | 2fdfb85 | no | #28 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase3.1.1c-local-full-pipeline-smoke | local | b7172d5 | no | #29 | no | no | DELETE_LOCAL_SAFE | merged PR #29 head matches branch head |
| phase3.1.2a-admin-ui-closeout | local | 01a36f5 | no | #31 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase3.1.2b-gallery-content-filter | local | 68db4b5 | no | #32 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase3.1.2c-hardening | local | 8c87771 | no | #33 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase3.1.2c-server-and-entity-resolver-hardening | local | b960c7b | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| phase3.1-clip-anime-classifier | local | 50049a2 | no | #25 | no | no | DELETE_LOCAL_SAFE | merged PR #25 head matches branch head |
| phase3.2b-pilot-hardening-config-audit | local | 1b31ff0 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| phase3.2c-medium-pilot-prep | local | 1df5115 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| phase3.2d-python-env-hardening | local | 1549394 | no | #36 | no | no | DELETE_LOCAL_SAFE | merged PR #36 head matches branch head |
| phase3.2e-server-runtime-python-identity | local | 48328d6 | no | #37 | no | no | DELETE_LOCAL_SAFE | merged PR #37 head matches branch head |
| phase3.2f-model-proxy-runtime-hardening | local | 64edb10 | no | #38 | no | no | DELETE_LOCAL_SAFE | merged PR #38 head matches branch head |
| phase3.2g-ai-run-isolation-storage-identity | local | 440a828 | no | no | no | no | KEEP_UNMERGED | local head differs from merged PR #40 head and is not ancestor of origin/main |
| phase3.2g-ai-tagging-scope-localization-hardening | local | f75c2b6 | no | #39 | no | no | DELETE_LOCAL_SAFE | merged PR #39 head matches branch head |
| phase3.2g-config-precedence-hardening | local | 9b8cfc7 | no | #41 | no | no | DELETE_LOCAL_SAFE | merged PR #41 head matches branch head |
| phase3.2j.2-manual-correction-ui-e2e-fix | local | 5c16fbf | no | #43 | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase3.2j-manual-translation-corrections | local | 2989ed4 | no | #42 | no | no | DELETE_LOCAL_SAFE | merged PR #42 head matches branch head |
| phase3.3a.1-icloud-candidate-manifest | local | 60ab1a8 | no | #45 | no | no | DELETE_LOCAL_SAFE | merged PR #45 head matches branch head |
| phase3.3a-tier1000-pilot-plan | local | eea8363 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main; deleted after remote prune retry |
| phase3.3b-tier1000-staging-copy | local | 5e579ad | no | #46 | no | no | DELETE_LOCAL_SAFE | merged PR #46 head matches branch head |
| phase3.4-tier1000-audit | local | 513b715 | no | no | yes | no | KEEP_WORKTREE | branch attached to git worktree |
| phase3.4-tier1000-audit-clean | local | 7af7a3e | no | #48 | no | no | DELETE_LOCAL_SAFE | merged PR #48 head matches branch head |
| phase3.5-tier1000-db-import | local | 8d7edbe | no | #49 | no | no | DELETE_LOCAL_SAFE | merged PR #49 head matches branch head |
| phase3.6-tier1000-ai-tagging-localization | local | d04f87c | no | #50 | no | no | DELETE_LOCAL_SAFE | merged PR #50 head matches branch head |
| phase3.7-tier1000-classification-scope-gate | local | a8b6724 | no | #51 | no | no | DELETE_LOCAL_SAFE | merged PR #51 head matches branch head |
| phase3-anime-filtering-foundation | local | a044df0 | no | no | no | yes | DELETE_LOCAL_SAFE | fully merged into origin/main |
| review-pr-21-runtime | local | 4927740 | no | no | no | no | KEEP_UNMERGED | not ancestor of origin/main and no merged PR head match |
| review-pr-22-runtime | local | b25d23a | no | no | no | no | KEEP_UNMERGED | not ancestor of origin/main and no merged PR head match |
| review-pr-24-runtime | local | add5676 | no | no | no | no | KEEP_UNMERGED | not ancestor of origin/main and no merged PR head match |
| review-pr-25-runtime | local | 1a162a5 | no | no | no | no | KEEP_UNMERGED | not ancestor of origin/main and no merged PR head match |
| review-pr-32 | local | 68db4b5 | no | no | no | no | KEEP_UNMERGED | not ancestor of origin/main and no merged PR head match |
| claude/nifty-gauss-228de9 | remote | ee59f42 | no | #35 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| claude/stoic-fermat-4ea768 | remote | 23c4c3b | no | #23 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| cursor/setup-dev-env-72e8 | remote | cd69b27 | no | #1 | no | yes | DELETE_REMOTE_SAFE | fully merged into origin/main |
| feat/phase-2.3-auto-tag-after-import | remote | 69038ba | no | #17 | no | no | DELETE_REMOTE_SAFE | merged PR #17 head matches branch head |
| fix-llm-proxy-and-deepseek-fallback | remote | b25d23a | no | #22 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| hotfix-storage-root-containment | remote | 7e2de03 | no | #27 | no | no | DELETE_REMOTE_SAFE | merged PR #27 head matches branch head |
| hotfix-unicode-mime-scan-import | remote | 022f3ec | no | #30 | no | no | DELETE_REMOTE_SAFE | merged PR #30 head matches branch head |
| main | remote | b41fa4d | no | no | yes | yes | KEEP_ACTIVE | origin/main |
| phase2.1.2-ai-tagging-session-hotfix | remote | 7eaa451 | no | #10 | no | no | DELETE_REMOTE_SAFE | merged PR #10 head matches branch head |
| phase2.2.1a-branding-acceptance-cleanup | remote | 9293935 | no | #13 | no | no | DELETE_REMOTE_SAFE | merged PR #13 head matches branch head |
| phase2.2.2a-auto-tag-localization | remote | dd69721 | no | #15 | no | no | DELETE_REMOTE_SAFE | merged PR #15 head matches branch head |
| phase2.2.2b-localization-dependency-hotfix | remote | 683b8f9 | no | #16 | no | no | DELETE_REMOTE_SAFE | merged PR #16 head matches branch head |
| phase2.2.2-dynamic-tag-localization | remote | ef431b7 | no | #14 | no | no | DELETE_REMOTE_SAFE | merged PR #14 head matches branch head |
| phase2.3a-dev-e2e-tools-config-diagnostics | remote | 0be121b | no | #18 | no | no | DELETE_REMOTE_SAFE | merged PR #18 head matches branch head |
| phase2.3e-proper-noun-alias-resolver | remote | 3f34e96 | no | #20 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase2.4-icloud-safe-ingestion | remote | 4927740 | no | #21 | no | no | DELETE_REMOTE_SAFE | merged PR #21 head matches branch head |
| phase2-tag-metadata-foundation | remote | 8ec7ecf | no | #6 | no | no | DELETE_REMOTE_SAFE | merged PR #6 head matches branch head |
| phase3.0.1-repo-hygiene-cleanup | remote | 3bc2979 | no | #24 | no | no | DELETE_REMOTE_SAFE | merged PR #24 head matches branch head |
| phase3.1.1a-env-db-storage-safety | remote | a4031b5 | no | #26 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase3.1.1b-fixture-e2e-workflow | remote | 2fdfb85 | no | #28 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase3.1.1c-local-full-pipeline-smoke | remote | b7172d5 | no | #29 | no | no | DELETE_REMOTE_SAFE | merged PR #29 head matches branch head |
| phase3.1.2a-admin-ui-closeout | remote | 01a36f5 | no | #31 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase3.1.2b-gallery-content-filter | remote | 68db4b5 | no | #32 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase3.1.2c-hardening | remote | 8c87771 | no | #33 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase3.1-clip-anime-classifier | remote | 50049a2 | no | #25 | no | no | DELETE_REMOTE_SAFE | merged PR #25 head matches branch head |
| phase3.2d-python-env-hardening | remote | 1549394 | no | #36 | no | no | DELETE_REMOTE_SAFE | merged PR #36 head matches branch head |
| phase3.2e-server-runtime-python-identity | remote | 48328d6 | no | #37 | no | no | DELETE_REMOTE_SAFE | merged PR #37 head matches branch head |
| phase3.2f-model-proxy-runtime-hardening | remote | 64edb10 | no | #38 | no | no | DELETE_REMOTE_SAFE | merged PR #38 head matches branch head |
| phase3.2g-ai-run-isolation-storage-identity | remote | 030613a | no | #40 | no | no | DELETE_REMOTE_SAFE | merged PR #40 head matches branch head |
| phase3.2g-ai-tagging-scope-localization-hardening | remote | f75c2b6 | no | #39 | no | no | DELETE_REMOTE_SAFE | merged PR #39 head matches branch head |
| phase3.2g-config-precedence-hardening | remote | 9b8cfc7 | no | #41 | no | no | DELETE_REMOTE_SAFE | merged PR #41 head matches branch head |
| phase3.2j.2-manual-correction-ui-e2e-fix | remote | 5c16fbf | no | #43 | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase3.2j-manual-translation-corrections | remote | 2989ed4 | no | #42 | no | no | DELETE_REMOTE_SAFE | merged PR #42 head matches branch head |
| phase3.3a.1-icloud-candidate-manifest | remote | 60ab1a8 | no | #45 | no | no | DELETE_REMOTE_SAFE | merged PR #45 head matches branch head |
| phase3.3a-tier1000-pilot-plan | remote | 6e6e4b0 | no | #44 | no | no | DELETE_REMOTE_SAFE | merged PR #44 head matches branch head |
| phase3.3b-tier1000-staging-copy | remote | 5e579ad | no | #46 | no | no | DELETE_REMOTE_SAFE | merged PR #46 head matches branch head |
| phase3.4-tier1000-audit | remote | 513b715 | no | no | yes | no | KEEP_WORKTREE | matching local branch attached to git worktree |
| phase3.4-tier1000-audit-clean | remote | 7af7a3e | no | #48 | no | no | DELETE_REMOTE_SAFE | merged PR #48 head matches branch head |
| phase3.5-tier1000-db-import | remote | 8d7edbe | no | #49 | no | no | DELETE_REMOTE_SAFE | merged PR #49 head matches branch head |
| phase3.6-tier1000-ai-tagging-localization | remote | d04f87c | no | #50 | no | no | DELETE_REMOTE_SAFE | merged PR #50 head matches branch head |
| phase3.7-tier1000-classification-scope-gate | remote | a8b6724 | no | #51 | no | no | DELETE_REMOTE_SAFE | merged PR #51 head matches branch head |

## Workflow Gap Analysis

The current workflow gap is not that individual stage tools are missing. The gap is that the validated pieces are still isolated phase runners with different assumptions, phase labels, and ordering.

Observed gaps:

- Phase 3.6 ran AI tagging and localization before content classification. That was useful validation, but it is not the final production order.
- AI tagging admin `content_class_filter` currently supports arbitrary content classes and treats `unknown` as including `NULL`. That is useful UI behavior, but the formal pipeline needs a stricter eligible helper for tag-derived stages.
- `tag_localization_service.list_missing_translations()` is tag-level and category-scoped, not eligible-media-scoped. The formal pipeline needs localization candidates derived from tags attached to eligible media only.
- Tag statistics and future tag-driven similarity must filter through eligible media. Existing tag counts such as `Tag.post_count` are global and cannot be treated as eligible-only signals.
- Phase reports already sanitize paths/secrets, but the pipeline needs one shared reporting contract so every stage emits failure artifacts consistently.
- The current scripts perform useful safety checks, but no single orchestrator can dry-run and reconcile counts across manifest, staging, audit, import, classification, eligible selection, AI tagging, localization, and smoke validation.

## Existing Script Boundaries

### `scripts/import_staged_manifest.py`

Do not promote directly to production workflow.

Why:

- It is Phase 3.5-specific: `IMPORT_SOURCE_LABEL = "violet:tier1000:phase3.5"` and `CONFIRM_PHRASE = "IMPORT_TIER1000_TO_DB"`.
- It expects Phase 3.4 audit summary semantics and Tier-1000 copy counts.
- It is manifest-driven and storage-safe, but it writes directly through raw SQL to the current media schema.
- Its local full-path CSV and public sanitized JSON report are good patterns, but the formal workflow needs parameterized run IDs/source labels and cross-stage count reconciliation.

Reusable ideas:

- Manifest validation, staged-path containment, source/staging immutability, duplicate-by-hash detection, app-managed storage paths, thumbnail post-audit, backup gate, report privacy redaction.

### `scripts/run_phase36_tier1000_ai_localization.py`

Do not promote directly to production workflow.

Why:

- It is Phase 3.6-specific: source label, confirmation phrase, status labels, expected `995` media, and `phase3.6` trigger source.
- Its AI stage intentionally ran before content classification.
- `select_target_media_ids()` filters by `Media.source` and `only_without_ai_tags`, but not by eligible content class.
- `select_localization_candidates()` limits by tag categories and source label, but not by eligible-media class.

Reusable ideas:

- DB-backed active AI job gate, backup gate, forbidden side-effect job deltas, AI/localization phase isolation, localization batch cap, provider failure behavior, partial failure report generation, secret/path redaction, proper-noun skip policy.

### `scripts/run_phase37_tier1000_classification_scope_gate.py`

Do not promote directly to production workflow.

Why:

- It is Phase 3.7-specific: `EXPECTED_MEDIA_COUNT = 995`, `SOURCE_LABEL = "violet:tier1000:phase3.5"`, `TRIGGER_SOURCE = "phase3.7"`, and a Phase 3.7 confirmation phrase.
- It validates classification and documents the scope gate, but it does not orchestrate import -> classification -> eligible AI -> eligible localization.
- Its current ineligible set treats `NULL` as ineligible/unclassified in the audit, while gallery filtering treats `unknown` as including `NULL`. The formal helper must make this policy explicit.

Reusable ideas:

- Source-label-locked classification chunks, active job gates, side-effect deltas, eligible/ineligible audit, no-cleanup stance for legacy AI associations, report sanitization.

### `scripts/validate_phase37_media_api_smoke.py`

Do not promote directly to production workflow as-is.

Why:

- It is Phase 3.7/Tier-1000-specific: source label and default expected count `995`.
- It assumes an already running server and validates Phase 3.5/3.7 scope.

Reusable ideas:

- Read-only sweeps for metadata, media detail, thumbnails, original file samples, content-class filters, search/localization, AI review/tag APIs, and summary JSON.

## Formal Entrypoint Evaluation

### Option A: one orchestrator CLI

Pros:

- Fastest path for Phase 3.8b.
- Easy to run from controlled shells with explicit env vars and backups.
- Good for exact reports and repeatable local pilots.

Cons:

- If all logic lives in the script, it repeats the Phase 3.5/3.6/3.7 pattern and becomes another phase-specific runner.

Use only as a thin wrapper.

### Option B: admin workflow action

Pros:

- Eventually useful for local operation and visibility.
- Can integrate with existing admin auth, job history, and progress UI.

Cons:

- Higher risk for a first implementation because it exposes a multi-stage write workflow through UI.
- Requires careful debug/local-only controls, CSRF/admin-mode gates, and long-running background orchestration.

Defer until CLI/service pipeline is proven.

### Option C: reusable service-level pipeline

Pros:

- Best long-term architecture.
- Allows tests to target pure helpers and stage contracts without starting a server.
- Keeps business rules, gates, eligible filtering, and reports out of ad hoc scripts.

Cons:

- Needs careful scope to avoid a broad framework refactor.

Recommended as the core.

### Option D: service helpers + CLI wrapper

Pros:

- Best Phase 3.8b balance.
- Service helpers define durable stage contracts and hard gates.
- CLI wrapper gives reproducible local execution and artifact generation.
- Admin UI can call the same service later.

Cons:

- Requires disciplined boundary design so the CLI remains thin.

Recommended formal entrypoint for Phase 3.8b.

### Option E: background job orchestration

Pros:

- Eventually needed for long-running library-scale operations.
- Better progress/cancel/retry UX.

Cons:

- Premature for the first formalization pass.
- Adds persistence/cancel semantics and potential migrations.

Defer until the service/CLI contract passes a medium pilot.

### Recommendation

Implement Phase 3.8b as service helpers plus a thin CLI wrapper:

- `backend/app/services/classification_first_workflow.py` or a similarly scoped module for:
  - stage plan objects
  - identity/gate checks
  - eligible media helper
  - eligible tag candidate helper
  - dry-run count reconciliation
  - sanitized report builder
  - failure artifact writer
- `scripts/run_classification_first_e2e.py` as the CLI wrapper:
  - subcommands such as `plan`, `dry-run`, `execute`
  - explicit source label/run label
  - explicit expected counts
  - explicit backup path for write stages
  - no hidden fallbacks to full-library scopes
- Do not add a DB migration in Phase 3.8b unless a later approved plan proves it necessary.
- Do not add admin UI execution in Phase 3.8b; limit admin validation to smoke/read-only endpoints after implementation.

## Required Workflow Order

1. Candidate manifest / candidate selection
   - Select a bounded candidate set from the approved source directory.
   - Never mutate source/iCloud directories.
   - Emit a private local manifest and a public redacted summary.
2. Staging copy
   - Copy selected hydrated files into a staging directory outside app-managed storage.
   - Preserve source immutability.
   - Verify count, byte size, extension, path containment, and duplicate target paths.
3. Pre-import audit
   - Read staged files only.
   - Verify manifest rows, staged file presence, bytes, hash consistency, allowed extensions, duplicates, and expected copy count.
   - Produce a PASS/FAIL artifact before import is allowed.
4. DB import
   - Require DB backup.
   - Import only audited staged files into app-managed storage.
   - Store relative managed paths.
   - Generate thumbnails.
   - Record a workflow/run-specific `Media.source`.
5. Content classification
   - Classify newly imported media before any tag-derived operation.
   - Use explicit non-empty media ID chunks.
   - Require `CONTENT_CLASSIFICATION_ENABLED=true` and `CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT=false`.
6. Eligible media selection
   - Select only media classified as `anime` or `unknown`.
   - Treat `NULL` only as unknown if the current policy explicitly opts into that behavior; after a successful classification stage, remaining `NULL` should normally be a hard failure.
7. AI tagging only eligible media
   - AI job creation receives explicit non-empty eligible media IDs.
   - No full-scope fallback if eligible selection is empty.
   - Ineligible media must be refused before job creation.
8. Localization only eligible-derived `general` / `meta` tags
   - Candidate tags must be attached to eligible media.
   - Proper-noun categories remain deferred to Entity Resolver/manual review.
   - Tag records are shared, so localization reports must state eligible-derived candidates, not imply per-media translation ownership.
9. Post-run validation
   - Reconcile stage counts, side-effect deltas, eligible/ineligible associations, translations, and failure counts.
10. Browser/API smoke
   - Run against a controlled server only when UI/API validation is required.
   - Server identity must pass before any E2E.
11. Report
   - Write sanitized public JSON/Markdown.
   - Keep private local manifests/CSVs out of git.

## Hard Gates

### Global Gates Before Any Stage

- Python identity:
  - Run `scripts/check_python_env.py --expected-python "$PY"`.
  - Report `sys.executable`.
- Repo identity:
  - Verify branch is not `main`.
  - Verify `HEAD` and expected base SHA.
  - Verify no tracked unrelated modifications.
- GitHub/main identity:
  - Verify previous PR is merged before starting a new phase.
- DB identity:
  - Verify `VIOLET_ENV`, `DB_NAME`, and `DATABASE_URL` redacted.
  - Refuse unexpected DB names for write modes.
- Storage identity:
  - Verify `STORAGE_ROOT`, `ORIGINAL_DIR`, and `THUMBNAIL_DIR`.
  - Refuse source/staging paths inside app-managed storage.
- No stale server:
  - Before server validation, detect existing agent-started server state.
  - If a server is used, run `scripts/check_test_server_identity.py` before tests.
- No destructive operations:
  - No cleanup/delete/reset/drop/truncate.
  - No missing-media cleanup.
  - No source/iCloud/staging mutation beyond authorized staging copy.
- Privacy:
  - Redact Windows/POSIX absolute paths, bearer tokens, API keys, and passwords from public reports.
  - Keep local full-path CSV/manifest artifacts untracked.
- Failure report:
  - Every write-capable stage must write a sanitized failure artifact before exiting nonzero.

### Candidate Manifest Gate

- Source root is explicitly approved and not app-managed storage.
- iCloud/full-library sources require hydrated-only and dry-run/audit first.
- Manifest has required columns and no private paths in public summary.
- Candidate count, duplicate source paths, duplicate target paths, excluded rows, and placeholder rows are reported.
- No source writes.

### Staging Copy Gate

- Target staging root is outside app-managed storage and outside source root.
- Expected copy count is explicit and positive.
- Destination containment is checked with path semantics, not string prefix checks.
- Copy operation never deletes or moves source files.
- Copy result includes bytes, files, skipped placeholders, and per-file errors in private artifacts.

### Pre-Import Audit Gate

- Staged files exist and match manifest size/hash.
- Expected copy count matches target pass count.
- Unsupported extensions, missing files, stat errors, and duplicates fail the audit.
- Public audit report redacts paths.

### DB Import Gate

- Requires non-empty DB backup file.
- Requires explicit confirmation phrase.
- Requires expected copy count.
- Requires background systems disabled:
  - `AI_TAGGING_ENABLED=false` or no auto tag path
  - `AI_AUTO_TAG_AFTER_IMPORT=false`
  - `AI_TAGGING_AUTO_LOCALIZATION=false`
  - `TAG_TRANSLATION_BACKGROUND_ENABLED=false`
  - `TAG_TRANSLATION_AUTO_ENABLED=false`
  - `TAG_TRANSLATION_LLM_ENABLED=false`
  - `ENTITY_ALIAS_RESOLVER_ENABLED=false`
  - `CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT=false`
- Duplicate-by-hash behavior is idempotent and reported.
- Imported media uses app-managed relative paths.
- Thumbnail creation failure rolls back the DB insert and removes created files.

### Content Classification Gate

- Requires DB backup if classification writes to the dev DB.
- Requires explicit media IDs from the current workflow source label.
- Requires no active classification, AI, or translation jobs.
- Requires classification enabled and auto-after-import disabled.
- Requires CLIP readiness if image classification needs CLIP; video-only or heuristic-only paths may skip CLIP preflight only when the classifier says no CLIP inference is required.
- Requires post-classification expected counts:
  - processed equals target count
  - failed equals `0` for pilot success
  - unclassified equals `0` unless the run explicitly permits `NULL` as unknown
- Requires side-effect deltas:
  - AI job delta `0`
  - translation job delta `0`
  - tag row delta `0`
  - media-tag delta `0`

### Eligible Selection Gate

- Eligible classes: `anime`, `unknown`.
- `NULL` content class is included only if an explicit policy flag says to treat `NULL` as unknown.
- Ineligible classes: `non_anime`, `illustration`, `unclassified`, `failed/error`.
- Empty eligible set is a hard stop, not a fallback to all media.
- Counts must reconcile:
  - total target media = eligible + ineligible + failed/unclassified buckets
  - eligible IDs are unique and belong to this workflow source label

### AI Tagging Gate

- Requires explicit non-empty eligible media IDs.
- Refuses any ineligible media ID before creating a job.
- Requires no active AI jobs in memory or DB-backed statuses `pending`, `running`, `cancelling`.
- Requires no active translation/classification jobs that could collide.
- Requires AI model status ready.
- Requires AI-only isolation:
  - `AI_TAGGING_AUTO_LOCALIZATION=false`
  - `TAG_TRANSLATION_BACKGROUND_ENABLED=false`
  - `TAG_TRANSLATION_AUTO_ENABLED=false`
  - `TAG_TRANSLATION_LLM_ENABLED=false`
- Requires job result:
  - status `completed`
  - failed `0` for pilot success
  - processed equals selected eligible count
  - `media_tags` delta equals confirmed + suggestions added
  - report `tags_added`, `suggestions_added`, `media_tags` delta, tag row delta, and media_with_ai_tags delta separately
- Failure in a chunk writes partial report and stops the run.

### Localization Gate

- Candidate selection must join through eligible media only.
- Categories limited to `general` and `meta`.
- Proper-noun categories `character`, `copyright`, `artist` are counted and deferred.
- Requires translation worker stopped or disabled before controlled localization.
- Requires `TAG_TRANSLATION_LLM_ENABLED=true` only for the controlled localization stage.
- Requires candidate cap: `min(--max-items, TAG_TRANSLATION_BATCH_MAX_ITEMS)`.
- Provider unavailable with candidates is a failure, not a silent success.
- Unknown provider outputs are skipped and counted.
- Unsaved selected candidates fail the run.
- Any `TagTranslationJob` created during failure must be marked failed with sanitized error.

### Post-Run Validation Gate

- Reconcile:
  - candidate count
  - staged count
  - audit pass count
  - imported count
  - duplicates/skips/failures
  - classification distribution
  - eligible/ineligible counts
  - AI confirmed/suggestion/media_tags deltas
  - localization candidate/translated/failed/skipped counts
- Verify no forbidden side effects:
  - no Entity Resolver
  - no similarity/clustering
  - no cleanup/delete/reset/drop/truncate
  - no source/iCloud mutation
- Verify public reports do not expose secrets or private paths.

### Browser/API Smoke Gate

- If server is used:
  - Load `. "$env:USERPROFILE\.violet\test-env.ps1"` for controlled test validation.
  - Choose a dynamic free port.
  - Start from the PR branch/worktree.
  - Record PID.
  - Run `scripts/check_test_server_identity.py` with expected env, DB, Python, code root, git SHA, and storage root.
  - Stop only the exact PID started.
- Validate:
  - metadata endpoint sweep
  - media detail endpoint sweep
  - thumbnail sweep
  - file endpoint sample
  - content-class filters
  - search/localization
  - AI Review
  - admin/content-class UI
  - server log scan

## Eligible Policy

Eligible:

- `content_class == anime`
- `content_class == unknown`
- `content_class IS NULL` only if the workflow explicitly opts into treating `NULL` as unknown under the current policy

Ineligible:

- `content_class == non_anime`
- `content_class == illustration`
- `content_class IS NULL` when classification is expected to be complete
- failed/error classification outcomes
- any media outside the workflow source label

Implementation should add a shared helper rather than copying conditions into scripts:

- `eligible_content_class_conditions(include_null_as_unknown: bool)`
- `assert_media_ids_are_eligible(db, media_ids, source_label, include_null_as_unknown=False)`
- `select_eligible_media_ids(db, source_label, only_without_ai_tags=True, limit=None, include_null_as_unknown=False)`
- `select_eligible_localization_candidates(db, source_label, categories=("general", "meta"), lang="zh-CN", limit=None)`

For the classification-first pilot, `include_null_as_unknown` should default to `False` after classification. If any target media remains `NULL`, that should fail the classification stage before AI tagging begins.

## Legacy Phase 3.6 Ineligible Associations

Known verified fact:

- `26` ineligible media already have AI tags from Phase 3.6.
- Those ineligible media have `771` Phase 3.6 AI associations.

Phase 3.8a must not delete or mutate these rows.

Required handling:

- Report them as legacy validation artifacts.
- Keep them visible in scope audits.
- Future tag-derived stats, localization candidate selection, and similarity/clustering must filter through eligible media.
- Do not treat tag-level translations as contaminated solely because a shared tag also appears on ineligible media.
- Design cleanup/review separately only if the user explicitly approves a later cleanup phase.

## Next Pilot Scale

Recommendation: run the next implementation pilot as `+1000` new media to reach about `2k` total media.

Rationale:

- The current `995` validates basic import, AI, localization, classification, and API feasibility.
- A `+1000` incremental workflow tests the missing automation boundary while preserving the existing validated corpus.
- Around `2k` total validates stage orchestration, eligible filtering, count reconciliation, and reporting without jumping too far.
- A `2000` fresh candidate workflow is cleaner if the goal is a from-scratch benchmark, but it costs more staging/storage/time and duplicates some existing validation.
- `3000` or `5000` should wait until the `2k` workflow passes cleanly.
- Do not jump directly to the full library.

Recommended scale ladder:

1. Phase 3.8b implementation plus dry-run/unit/focused validation.
2. `+1000` new media classification-first pilot to reach around `2k` total.
3. If clean, a `3000` pilot.
4. If clean, a `5000` pilot.
5. Only after that, revisit Phase 4 entity/similarity/full-library planning.

## Tests Required For Implementation Phase

Add focused tests before any real pilot execution:

- Eligible scope helper tests:
  - `anime` and `unknown` included.
  - `non_anime`, `illustration`, `NULL` after classification, and failed/error excluded.
  - `NULL` included only when explicit policy says so.
- AI tagging refuses ineligible media:
  - mixed eligible/ineligible explicit IDs fail before job creation.
  - zero eligible IDs do not fall back to full library.
  - only eligible IDs are passed to job creation.
- Localization candidates only from eligible media:
  - candidates are selected by joining through eligible media.
  - `general` and `meta` included.
  - `character`, `copyright`, `artist` counted/deferred.
  - tags attached only to ineligible media are excluded.
- Non-anime excluded from tag-derived stats/similarity:
  - stats helper ignores ineligible media associations.
  - future similarity candidate helper uses eligible media filter.
- Pipeline dry-run count consistency:
  - manifest copy rows, staging rows, audit rows, import would-create/duplicate counts, classification target counts, eligible/ineligible counts reconcile.
- Failure isolation:
  - failed classification chunk writes report and stops before AI.
  - failed AI chunk writes partial report and stops before localization.
  - localization provider unavailable with candidates fails and marks job failed if a job was created.
- No DB/source mutation in dry-run:
  - DB row counts unchanged.
  - media storage stats unchanged.
  - source/staging stats unchanged except explicitly allowed staging copy dry-run outputs.
- No secrets/path leakage:
  - reports redact Windows paths, POSIX paths, bearer tokens, API keys, DB passwords.
- Server identity checks:
  - planned server smoke refuses stale server or wrong Python/storage/root/DB.
- Metadata endpoint sweep:
  - JSON-safe metadata remains validated after pipeline implementation.
- Content-class filter smoke:
  - `anime`, `unknown`, `anime,unknown`, and ineligible filters return correct totals.
- Browser smoke:
  - gallery, media detail, admin content classification, AI review, localization/admin pages load.
- Server log scan:
  - no 500s, tracebacks, stale-server warnings, or background worker side effects.

Suggested focused command after implementation:

```powershell
& "$PY" -m pytest tests/test_classification_first_workflow.py tests/test_ai_tagging_content_class_filter.py tests/test_ai_tagging_localization_gate.py tests/test_phase37_tier1000_classification_scope_gate.py tests/test_phase36_tier1000_ai_localization.py tests/test_import_staged_manifest.py tests/test_media_metadata_serialization.py -v
```

## Real Validation Required After Implementation

For Phase 3.8b and the later medium pilot, validation must include:

- All stage counts:
  - manifest candidates
  - staging copied/skipped/failed
  - audit pass/fail
  - imported/duplicates/failed
  - classification distribution
  - eligible/ineligible media counts
  - AI processed/failed/tags_added/suggestions_added/media_tags delta/tag row delta/media_with_ai_tags delta
  - localization selected/translated/failed/skipped/proper-noun deferred
- Metadata endpoint sweep for the workflow target media.
- Media detail API sweep for the workflow target media.
- Thumbnail sweep for the workflow target media.
- File endpoint sample including anime, unknown, ineligible, and tail samples.
- Content-class filter checks:
  - `anime`
  - `unknown`
  - `non_anime`
  - `anime,unknown`
- Search/localization checks:
  - canonical visual tags
  - localized visual tag search
  - no proper-noun alias trust regression
- AI Review checks:
  - pending suggestions visible
  - counts consistent with AI job deltas
- Server log scan:
  - no `500`
  - no `Traceback`
  - no stale server mismatch
  - no forbidden worker side effects
- Browser smoke with Playwright Edge:
  - gallery/filter pages
  - sampled media detail pages
  - Admin Content / Content Classification
  - AI Review
  - Tag Localization status pages
- App/admin validation:
  - config diagnostics show correct env/DB/storage/server identity
  - tag localization worker status shows expected stopped/disabled/running state per stage
- Failure report artifacts:
  - intentional dry-run/failure fixtures produce sanitized failure reports

## Deferred Items

Defer all of the following:

- Phase 4 entity metadata.
- Tag-driven similarity implementation.
- Entity Resolver execution or expansion.
- Full `5k+` run.
- Cleanup of legacy ineligible AI tags.
- Full branch hygiene beyond the safe cleanup already completed.
- Admin UI workflow execution.
- Background job orchestration persistence.
- DB schema migrations for workflow run history.

## Proposed Phase 3.8b Implementation Scope

Phase 3.8b should implement the reusable service helpers and thin CLI wrapper only.

In scope:

- Shared eligible media helper.
- Eligible-derived localization candidate helper.
- Pipeline stage contract objects.
- Dry-run count reconciliation.
- Report/failure artifact builder with privacy redaction.
- Thin CLI wrapper for formal classification-first workflow dry-run and controlled execution.
- Focused tests.
- Read-only browser/API smoke validation after implementation.

Out of scope:

- New models/tables.
- Admin UI execution button.
- Entity Resolver.
- Similarity/clustering.
- Legacy cleanup.
- Full-library runs.

## Safety Confirmation

Phase 3.8a performed:

- Git/GitHub state verification.
- Python identity verification.
- Safe branch hygiene cleanup.
- Read-only project documentation and code inspection.
- Plan documentation only.

Phase 3.8a did not perform:

- DB mutation.
- Import/copy.
- AI tagging.
- Localization.
- Content classification.
- Entity Resolver.
- Similarity/clustering.
- Cleanup/delete/reset/drop/truncate.
- Source/iCloud/staging mutation.
- Secret exposure.
- Push to `main`.
- Merge.
