# Phase R1 - GitHub Repository Rename Sync and Documentation Update

Date: 2026-05-26

## Summary

The GitHub repository was renamed from historical `kyloris0660/AnimeLocalBooru` to canonical `kyloris0660/VIOLET`.

This stage updates local git `origin` and active documentation to use the canonical VIOLET remote while preserving the existing local working directory:

```text
C:\Users\kyloris\Documents\AnimeLocalBooru
```

The local folder name is intentionally not changed. Runtime package names, source labels, historical phase reports, and old PR links should not be blindly rewritten just because the remote repository was renamed.

## Remote Verification

Read-only verification showed:

- `gh repo view kyloris0660/VIOLET --json nameWithOwner,url` resolves as `kyloris0660/VIOLET`.
- `gh repo view kyloris0660/AnimeLocalBooru --json nameWithOwner,url` resolves to `kyloris0660/VIOLET`, consistent with GitHub repository rename redirects.

Local `origin` was updated to:

```text
https://github.com/kyloris0660/VIOLET.git
```

Verification after the update:

- `git remote -v` reports VIOLET for fetch and push.
- `git fetch origin` succeeds.
- `git ls-remote --heads origin main` returns `342d8d4a98c58f52bb9f9c66b248f0c401b23d28 refs/heads/main`.

## Documentation Updates

Updated active canonical references:

- `README.md`
  - Adds canonical GitHub repository note.
  - Updates clone URL to `https://github.com/kyloris0660/VIOLET.git`.
  - Keeps `cd AnimeLocalBooru` as a valid local folder example.
- `CONTRIBUTING.md`
  - Updates clone URL.
  - Updates issue tracker URL.
- `SECURITY.md`
  - Updates vulnerability reporting issue URL.
- `docs/current-handoff.md`
  - Updates current repository row to `kyloris0660/VIOLET`.
  - Adds canonical URL, historical repo name, local worktree path, and redirect note.
  - Records Phase R1 as a docs-only repo rename sync stage.
  - Updates the current Phase 4.3-B PR entry and future PR traceability example to canonical VIOLET URLs.
- `docs/project-roadmap.md`
  - Adds canonical repository note.
  - Adds Phase R1 roadmap entry with remote/local-path guidance.

Created:

- `docs/reports/repo-rename-violet-docs-sync.md`
- `docs/reports/repo-rename-violet-docs-sync-summary.json`

## Reference Audit

Searched tracked files for:

- `AnimeLocalBooru`
- `github.com/kyloris0660/AnimeLocalBooru`
- `kyloris0660/AnimeLocalBooru`
- `github.com/kyloris0660/VIOLET`
- `kyloris0660/VIOLET`
- `V.I.O.L.E.T.`
- `VIOLET`

Updated:

- Active clone URLs in `README.md` and `CONTRIBUTING.md`.
- Active issue URLs in `CONTRIBUTING.md` and `SECURITY.md`.
- Active canonical repository fields in `docs/current-handoff.md`.
- Active/current roadmap repository guidance in `docs/project-roadmap.md`.

Intentionally retained:

- Local filesystem paths such as `C:\Users\kyloris\Documents\AnimeLocalBooru`.
- Local folder examples such as `cd AnimeLocalBooru`.
- Service/process identification text such as "V.I.O.L.E.T. / AnimeLocalBooru dev server".
- Historical phase report links and old PR links that still include `github.com/kyloris0660/AnimeLocalBooru`, because they are archival traceability and GitHub redirects are expected after the rename.
- Source labels, historical report text, and package/runtime references where the old name is historical or local rather than a canonical remote.

## Safety Confirmation

This stage changed infrastructure metadata and documentation only.

- No runtime behavior changed.
- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No staging copy.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No Entity Resolver execution.
- No similarity/clustering.
- No Phase 4.4 implementation.
- No provider API calls.
- No push to `main`.
- No merge.

## Engineering Judgment

Active docs now point to the canonical VIOLET repository where that matters for clone, issue, repo identity, and future handoff. Historical links remain acceptable because mass-rewriting old phase reports and PR history would add noise without changing operational behavior; GitHub rename redirects are the intended compatibility mechanism.

Recommended follow-up: after this PR merges, future PR links and active workflow docs should use `https://github.com/kyloris0660/VIOLET`. Do not recreate the old `AnimeLocalBooru` GitHub repository name.
