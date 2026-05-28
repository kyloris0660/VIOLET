<p align="center">
  <img src="frontend/static/img/violet-logo.png" alt="V.I.O.L.E.T." width="400">
</p>

<h1 align="center">V.I.O.L.E.T.</h1>

<p align="center">
  <strong>Visual Image Organizer for Local Evaluation & Tagging</strong>
</p>

V.I.O.L.E.T. is a local-first anime/illustration image library built on top of
[Blombooru](https://github.com/mrblomblo/blombooru). Its core value is
Danbooru-style tag-based retrieval for a personal local collection, with Chinese
UI/localization support and strict safety around local files, iCloud sources,
provider uploads, and database writes.

Canonical GitHub repository: [kyloris0660/VIOLET](https://github.com/kyloris0660/VIOLET).
The historical repository name was `AnimeLocalBooru`; local working directories
may still use that folder name.

## Current State

Start with these active docs instead of old phase reports:

| Document | Purpose |
|----------|---------|
| [Current Handoff](docs/current-handoff.md) | Latest accepted state, current route, and active safety/governance policy |
| [Project Roadmap](docs/project-roadmap.md) | Phase history, near-term route, and development standards |
| [Manual Validation](docs/manual-validation.md) | Local development/manual validation runbook |
| [Test Workflow](docs/test-workflow.md) | Test tiers, environment setup, and scope-based validation guidance |
| [iCloud Safe Ingestion](docs/icloud-safe-ingestion.md) | Source/iCloud scan, staging, and ingestion safety rules |

At a high level:

- Phase 3.8d medium pilot is accepted.
- Phase 4.1 entity metadata foundation and Phase 4.2 manual correction/review foundation are merged.
- SauceNAO high-confidence reverse-search results are viable source-backed evidence candidates; low-confidence results are discarded by default for this workflow.
- PR #79 merged the provider-neutral evidence contract foundation for future C1 persistence.
- GOV-2 workflow policy is active: durable core contracts remain strict, while phase-scoped and one-off tooling should stay lightweight and should not become generic frameworks unless explicitly promoted.

## Quick Start

Windows local development:

```powershell
git clone https://github.com/kyloris0660/VIOLET.git AnimeLocalBooru
cd AnimeLocalBooru
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item example.env .env
python run.py --debug
```

Open <http://localhost:8000>. First run shows onboarding; later runs load the
gallery directly. Repo-root `python run.py --debug` with the project venv is the
current startup path; the old `PYTHONPATH=<repo>\backend` workaround is not
required.

Default local development credentials after onboarding are `admin` / `admin123`.
Do not use those credentials for an exposed deployment.

## Core Capabilities

| Area | Status |
|------|--------|
| Blombooru gallery, upload, search, tags | Done |
| External local library scan and dry-run safety | Done |
| iCloud/Windows Cloud Files scan safety | Done |
| Tag provenance, AI suggestions, and manual lock priority | Done |
| WDv3 AI tagging and background jobs | Done, opt-in |
| Chinese UI and general/meta tag localization | Done |
| Entity metadata foundation and targeted manual correction | Done |
| Provider-neutral reverse-search evidence contract | Done in PR #79 |
| Broad/repeated provider enrichment | Future, requires ledger discipline and explicit approval |
| Confirmed automatic entity assignment | Future, not approved by current policy |

## Safety Basics

- Never commit `.env`, API keys, model files, local manifests, media files, or local database artifacts.
- Run iCloud/source workflows through preflight and dry-run paths first.
- Do not upload originals or privacy-sensitive local/source data to external providers by default.
- Do not perform DB imports, migrations, provider calls, localization execution, Entity Resolver execution, similarity/clustering, source/iCloud mutation, or app-managed storage mutation unless a phase explicitly approves it.
- For workflow and reviewer rules, use `AGENTS.md`, `CLAUDE.md`, and `docs/project-roadmap.md`; this README is intentionally only a concise entry point.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI, SQLAlchemy ORM, PostgreSQL 17 |
| Frontend | Jinja2 templates, Tailwind CSS, vanilla JavaScript |
| AI models | WDv3 ONNX, CLIP ViT-B/32 ONNX where explicitly used |
| Local runtime | Python 3.12 project venv on Windows |

## Upstream Attribution

V.I.O.L.E.T. extends **Blombooru**, a self-hosted media tagging tool.

- Upstream: <https://github.com/mrblomblo/blombooru>
- License: MIT
