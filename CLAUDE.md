# CLAUDE.md

This project is V.I.O.L.E.T. (Visual Image Organizer for Local Evaluation & Tagging), a local-first anime image library based on Blombooru.

## Before coding, read these files

- `AGENTS.md` — Agent instructions, architecture, key code locations
- `README.md` — Project overview, feature status, quick start
- `docs/current-handoff.md` — Latest development state
- `docs/project-roadmap.md` — Full phase plan

## Critical workflow rules

- Do not merge PRs automatically.
- Claude may commit, push a feature branch, and create a PR.
- The user manually reviews and merges PRs on GitHub.
- Never claim a PR exists without a real GitHub PR URL.
- Never treat a local commit message like "(#19)" as proof of a PR.
- Never claim a PR is merged unless GitHub or `origin/main` verifies it.

## Do not commit

`.env`, API keys, `data/`, `media/`, `storage/`, `backups/`, model files, `node_modules/`, `test-results/`, `playwright-report/`, traces, screenshots.

## Environment

- Windows local dev
- GitHub CLI: `C:\Program Files\GitHub CLI\gh.exe` (usually available as `gh` in PATH)
- If `gh` is not found in PATH, use the absolute path above.
- Dev server: `python run.py --debug` → `http://localhost:8000`
- Database: PostgreSQL 17, `blombooru` on `localhost:5432`

## Language policy

| Layer | Language |
|-------|----------|
| User interface | zh-CN first (English fallback) |
| Internal code, API, config, canonical tags | English |
| Core technical docs | English primary |

## Runtime LLM configuration

Runtime tag translation / alias resolution uses configurable OpenAI-compatible LLM via `.env`:
- `TAG_TRANSLATION_LLM_PROVIDER`, `TAG_TRANSLATION_LLM_BASE_URL`, `TAG_TRANSLATION_LLM_API_KEY`, `TAG_TRANSLATION_LLM_MODEL`
- Do not hardcode any specific runtime LLM model.
