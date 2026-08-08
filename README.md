<p align="center">
  <img src="frontend/static/img/violet-logo.png" alt="V.I.O.L.E.T." width="400">
</p>

<h1 align="center">V.I.O.L.E.T.</h1>

<p align="center">
  <strong>Visual Image Organizer for Local Evaluation &amp; Tagging</strong>
</p>

V.I.O.L.E.T. is a local-first anime and illustration library for people who
want Danbooru-style retrieval over their own image collection. It builds on
[Blombooru](https://github.com/mrblomblo/blombooru), adds Windows/iCloud-aware
local ingestion safety, Chinese UI/localization support, AI-assisted tagging,
source-backed evidence, and provenance-aware workflows for future entity
correction.

Canonical repository: [kyloris0660/VIOLET](https://github.com/kyloris0660/VIOLET).
The historical local folder name may still be `AnimeLocalBooru`.

## Key Features

- Self-hosted gallery for local anime/illustration media.
- Danbooru-style tag search with upload, scan, thumbnail, and media-detail workflows.
- Local library scanning with dry-run, cloud-file safety, deduplication, and per-file failure handling.
- Tag provenance for manual, AI, and imported suggestions; manual/locked tags take priority.
- Opt-in WDv3 AI tagging jobs and Chinese general/meta tag localization.
- Source-layer evidence tables for provider-neutral source names, tags, assertions, and SourceConcepts.
- Targeted manual entity correction foundations without requiring exhaustive review queues.

## Current Status

V.I.O.L.E.T. is a personal/local development project, not a production SaaS.
The single current-route authority is `docs/state/current-phase.json`; the
generated `docs/current-handoff.md` provides the short human-readable view.
Current work is the planning-only `SCV2-FL1` isolated full-library Dev/Test
route. No implementation, database, source-root, production, provider, LLM,
media, classification, AI-tagging, graph/search, localization, or truth
operation is authorized by that planning status.

Accepted SourceConcept evidence remains deliberately not Entity truth. It is
not `EntityAlias` truth, confirmed assignment, or `media_tags` truth, and it
does not approve automatic entity promotion.

## Architecture Overview

| Layer | Technology / Notes |
|-------|--------------------|
| Backend | FastAPI, SQLAlchemy ORM, PostgreSQL 17 |
| Frontend | Jinja2 templates, Tailwind CSS, vanilla JavaScript |
| Runtime entry point | `python run.py --debug` from repo root |
| Local config | `.env` for DB/runtime settings; `data/settings.json` after onboarding |
| AI models | WDv3 ONNX and CLIP ONNX only where explicitly enabled |
| Tests | Pytest tiers plus gated Playwright Edge E2E |

## Quick Start

Windows local development:

```powershell
git clone https://github.com/kyloris0660/VIOLET.git AnimeLocalBooru
cd AnimeLocalBooru

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item example.env .env
# Edit .env for local PostgreSQL, for example:
# POSTGRES_HOST=localhost
# POSTGRES_DB=blombooru
# POSTGRES_USER=<local postgres user>
# POSTGRES_PASSWORD=<local postgres password>

python run.py --debug
```

Open <http://localhost:8000>. First run shows onboarding; later runs load the
gallery. Default local development credentials after onboarding are
`admin` / `admin123`; do not expose those credentials on a public network.

## Production Launcher

The first production launcher is a temporary personal Windows entrypoint for the
current development/production split. It does not convert the development
`.env` into production and does not ask developers or CodeX worktrees to run
with production settings.

Daily production use should be through the generated root entry:

```text
V.I.O.L.E.T. Production Launcher.exe
```

If the root entry is missing, build and install it from the canonical checkout:

```powershell
cd launcher
npm install
npm run package
```

`npm run package` builds the portable Electron launcher and copies it to the
repository root. The window title is `V.I.O.L.E.T. 启动器`, while the stable
root executable keeps its existing filename for compatibility. In normal daily
use, click `启动`: the launcher automatically runs startup preflight first,
continues only if the checks pass, then shows runtime health. Use `打开浏览器`,
`停止`, or `重启` from the same launcher. The manual preflight button is kept as
a secondary diagnostic action, not as a required normal step.

Production startup uses the ignored local profile and runtime anchor under:

```text
.local_manifests/production_launcher/
```

Those files may contain machine-local paths or DB access values and must not be
committed. Build output is ignored as well. Manual acceptance is still required
before merging launcher changes:

```json
{
  "manual_acceptance_required_before_merge": true,
  "manual_acceptance_completed": false,
  "merge_allowed": false
}
```

## Safety And Privacy Model

- Local/source files are never mutated by default.
- iCloud and Windows Cloud Files workflows must use preflight/dry-run safety paths.
- Originals, local paths, filenames, API keys, and private source labels must not be exposed in public reports or sent to external providers by default.
- Provider calls, source enrichment, LLM runs, DB imports, migrations, broad validation, Entity Resolver work, similarity/clustering, and `media_tags` mutation require explicit phase approval.
- Confirmed automatic entity assignment is not approved by current policy; reliable identity work must remain provenance-first and confirmation-aware.

## Documentation Map

| Document | Use it for |
|----------|------------|
| [Current Handoff](docs/current-handoff.md) | Short active state, route, non-goals, and validation starting points |
| [Project Roadmap](docs/project-roadmap.md) | Active roadmap, governance standards, and phase archive |
| [Test Workflow](docs/test-workflow.md) | Scope-based validation policy and current test entry points |
| [Manual Validation](docs/manual-validation.md) | Local development/manual validation runbook |
| [iCloud Safe Ingestion](docs/icloud-safe-ingestion.md) | Source/iCloud scan, staging, and ingestion safety |

Historical phase reports live in `docs/reports/` and are archival traceability,
not active onboarding material.

## Development Focus

Current source-backed work is focused on validating SourceConcept coverage,
alias linking, redaction, and search symmetry before any SourceConcept editing,
Entity bridge, promotion, broad provider run, or full-library scale-up. See the
roadmap and current handoff before starting a new phase.

## Upstream Attribution / License

V.I.O.L.E.T. extends **Blombooru**, a self-hosted media tagging tool.

- Upstream: <https://github.com/mrblomblo/blombooru>
- License: MIT
