<p align="center">
  <img src="frontend/static/img/violet-logo.png" alt="V.I.O.L.E.T." width="400">
</p>

<h1 align="center">V.I.O.L.E.T.</h1>

<p align="center">
  <strong>Visual Image Organizer for Local Evaluation & Tagging</strong>
</p>

<p align="center">
  A local-first anime/illustration image library for intelligent tagging, human review, and structured collection management.
</p>

<p align="center">
  面向动漫/插画图像收藏的本地优先图库系统，专注于智能打标、人工审核与结构化管理。
</p>

---

## Language Policy

| Layer | Language |
|-------|----------|
| User interface | zh-CN first (English fallback) |
| Internal code / API / config / canonical tags | English |
| Core technical docs | English primary |
| Optional user-facing docs | Chinese supplements where appropriate |

## About

V.I.O.L.E.T. is built on top of [Blombooru](https://github.com/mrblomblo/blombooru), providing Danbooru-style tag-based retrieval for personal anime/illustration collections.

## Current Project State

Phase 3.8d medium pilot is accepted. Future agents should start from [Current Handoff](docs/current-handoff.md) for the latest state, then use [Manual Validation](docs/manual-validation.md) and [Project Roadmap](docs/project-roadmap.md) for runbook and next-phase guidance. Do not infer current status from older phase reports alone.

Local development startup is repo-root `python run.py --debug` with the project venv; the old `PYTHONPATH=<repo>\backend` workaround is no longer required.

Core capabilities:
- Scan local image directories (e.g. iCloud Photos sync folder), reliably importing anime/illustration images
- Generate high-quality tags via WDv3 AI model
- Danbooru-style search and filtering
- Track each tag's provenance (AI / manual / booru import), confidence, and lock status
- Human review of AI tag suggestions — manual tags always take priority
- Chinese UI and Chinese tag display names

## Feature Status

| Feature | Status | Notes |
|---------|--------|-------|
| Blombooru core (Gallery, upload, search, tags) | Done | Full upstream features |
| Local Library Scan | Done | Import from external directories |
| dry-run & max_files safety | Done | Preview before import |
| Scan job progress / history / cancel | Done | Background jobs + Admin UI |
| Tag Metadata Foundation | Done | Provenance tracking (source, confidence, locked, suggestion) |
| WDv3 AI Auto Tagging MVP | Done | Manual trigger, dry-run, batch, Admin UI |
| Confirmed / Suggestion tags | Done | Dual-threshold system |
| Manual / locked tag protection | Done | AI never overwrites human tags |
| AI Tag Review UI | Done | Confirm / reject / lock / delete suggestions |
| zh-CN localization foundation | Done | Chinese UI, tag Chinese display, search aliases |
| Dynamic tag localization | Done | DB-backed translations, optional LLM, admin management |
| Background tag translation | Done | Continuous auto-translation of all missing tags via LLM |
| Auto AI tagging after import | Done | Optional, disabled by default |
| AI tagging background jobs | Done | Progress tracking, cancel, history |
| Proper noun alias resolver | Done | Character/copyright/artist alias resolution with LLM + manual review |
| Admin UI closeout | Done | Navigation, i18n, AI tagging consolidation, dark Violet theme (Phase 3.1.2a) |
| Gallery content-class filter | Done | 5-mode filter: all/anime+unknown/anime-only/non-anime/unknown (Phase 3.1.2b) |
| Server identity endpoint | Done | `GET /api/system/server-identity` for dev server validation (Phase 3.1.2c) |
| Unified LLM fallback | Done | `complete_chat`/`complete_json` two-layer API with structured error hierarchy (Phase 3.1.2c) |
| Phase 3.8d medium pilot | Accepted | 994 staged-success rows imported, failed rows deferred, classification-first pipeline completed |
| iCloud large library safety | Done | Preflight scan, hydrated-only, per-file timeout, extended skip stats |
| Content classification foundation | Done | Infrastructure + evaluation harness (heuristic baseline only) |
| CLIP zero-shot content classifier | Done | Anime vs non-anime via CLIP ViT-B/32 ONNX (Phase 3.1) |
| Reverse image search | Future | SauceNAO/IQDB integration |
| Character / copyright database | Future | External data enrichment |
| Filesystem watcher | Future | Auto-detect new files |

## Current Limitations

- No reverse image search
- No source URL auto-detection
- No character/copyright database
- Chinese tag search covers all general/meta tags via LLM translation; character/copyright/artist aliases require manual review

## Quick Start (Windows Local Development)

### Prerequisites

- Python 3.12+
- PostgreSQL 17
- Git

### Install

```powershell
git clone https://github.com/kyloris0660/AnimeLocalBooru.git
cd AnimeLocalBooru   # repo directory name; project display name is V.I.O.L.E.T.
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from the example:

```powershell
Copy-Item example.env .env
```

Edit `.env` to set your PostgreSQL password. Enable AI tagging:

```env
AI_TAGGING_ENABLED=true
```

### Start

```powershell
.\venv\Scripts\Activate.ps1
python run.py --debug
```

Open http://localhost:8000. First run shows the onboarding setup page.

### Default Dev Credentials

After initial setup: `admin` / `admin123` (local dev only — never use in production).

### Docker

Docker deployment files (`Dockerfile`, `docker-compose.yml`, etc.) are inherited from upstream Blombooru and remain in the repository. However, V.I.O.L.E.T. is primarily developed and tested as a local Windows Python application. Docker is not the primary workflow and may not reflect all V.I.O.L.E.T.-specific features.

## iCloud Photos Integration

V.I.O.L.E.T. can scan iCloud Photos sync folders on Windows. Because iCloud uses on-demand file hydration, special safety measures are built in:

- **Preflight scan** — checks file hydration status before import
- **Hydrated-only mode** — skips cloud-only placeholder files to avoid triggering large downloads
- **Per-file timeout** — prevents hangs on slow hydration
- **Extended skip stats** — reports exactly which files were skipped and why

**Recommended workflow:** Always run preflight scan first, then dry-run with hydrated-only mode, then import. See [iCloud Safe Ingestion](docs/icloud-safe-ingestion.md) for details.

## GUI Entry Points

| Page | URL | Purpose |
|------|-----|---------|
| Gallery | `/` | Browse, search, view media |
| Media Detail | `/media/{id}` | View a single media item and its tags |
| Admin Panel | `/admin` | System settings, content management, AI tagging |
| Login | `/login` | Admin authentication |

### Admin Panel Tabs

- **System** — App settings, cache, API keys, backup, updates
- **Content** — Media upload, Local Library Scan, AI tagging, tags management, albums
- **Stats** — Upload trends, tag distribution charts
- **Account** — Change password/username

## AI Tagging

V.I.O.L.E.T. uses the WDv3 (SmilingWolf) model to predict Danbooru-style tags.

### Key Points

- Manually triggered from Admin Panel, or automatically after import when enabled
- First use downloads the model from HuggingFace Hub (~450 MB)
- Dual-threshold system: tags are marked as "confirmed" (searchable) or "suggestion" (pending review)
- Never overwrites manually added or locked tags
- Always test with dry-run first

### Batch Limits

- The "Max Items" field in the UI is not unlimited
- Backend enforces `AI_TAGGING_BATCH_MAX_ITEMS` (default 10)
- This prevents accidental full-library AI tagging
- Adjustable in `.env`: `AI_TAGGING_BATCH_MAX_ITEMS=50`
- Not recommended to remove the limit entirely
- Large batches should use background AI tagging jobs with progress tracking and cancel
- Auto-tagging after import is available via `AI_AUTO_TAG_AFTER_IMPORT=true` (disabled by default)

See [AI Tagging Usage Guide](docs/ai-tagging-usage-guide.md) for details.

## Safety Guidelines

| Operation | Recommendation |
|-----------|----------------|
| Scanning iCloud Photos | **Always** preflight first, then dry-run + hydrated-only |
| AI tagging | **Always** dry-run a single image first |
| Batch AI tagging | Start with max_items=3-5, never full library directly |
| Model files | Do not commit to git (.gitignore handles this) |
| `.env` file | Do not commit (contains passwords) |
| Full library operations | Only after successful incremental tests |

## Documentation

| Document | Content |
|----------|---------|
| [AI Tagging Usage Guide](docs/ai-tagging-usage-guide.md) | Complete usage guide with examples |
| [AI Tag Review](docs/ai-tag-review.md) | Review UI and API documentation |
| [AI Auto Tagging Technical Doc](docs/ai-auto-tagging.md) | Architecture, API reference, data model |
| [AI Tagging Jobs](docs/ai-tagging-jobs.md) | Background AI tagging job system |
| [E2E Validation Guide](docs/e2e-violet-test-100.md) | VioletTest100 end-to-end testing |
| `scripts/reset_e2e_test_data.py` | CLI tool to reset E2E test data |
| [Local Library Scan](docs/local-library-scan.md) | Scan feature documentation |
| [Tag Metadata Foundation](docs/tag-metadata-foundation.md) | Provenance tracking system design |
| [Tag Localization (zh-CN)](docs/tag-localization-zh.md) | Chinese display names and search design |
| [Tag Localization LLM](docs/tag-localization-llm.md) | Dynamic LLM translation cache documentation |
| [Entity Alias Resolver](docs/entity-alias-resolver.md) | Proper-noun alias resolution for character/copyright/artist tags |
| [iCloud Safe Ingestion](docs/icloud-safe-ingestion.md) | Preflight scan, hydrated-only, timeout protection |
| [Content Classification](docs/content-classification.md) | Content classifier design and evaluation harness |
| [Test Workflow](docs/test-workflow.md) | Test tiers, environment setup, Playwright E2E |
| [Project Roadmap](docs/project-roadmap.md) | Full phase plan |
| [Manual Validation](docs/manual-validation.md) | Current development/blombooru manual validation runbook |
| [Current Handoff](docs/current-handoff.md) | Latest state for resuming development |
| [Development Log](docs/local-anime-library-devlog.md) | Per-phase technical notes |

## Roadmap

- **Phase 2.2.2** — Dynamic tag localization / LLM translation cache (done)
- **Phase 2.3** — AI tagging jobs + auto-tag after import (done)
- **Phase 2.3a** — Developer E2E tools + config diagnostics (done)
- **Phase 2.3c** — Full real browser E2E acceptance testing (done)
- **Phase 2.3d** — Continuous background tag translation worker (done)
- **Phase 2.3e** — Proper noun alias resolver foundation (done)
- **Phase 2.4** — iCloud large library readiness / safe ingestion (done)
- **Phase 3** — Content classification foundation + evaluation harness (done — heuristic baseline only)
- **Phase 3.1** — CLIP zero-shot content classifier (done — anime recall >= 80%, non-anime FP <= 15%)
- **Phase 3.1.1a** — Environment/DB/Storage safety foundation (done)
- **Phase 3.1.1b** — Fixture-based test workflow foundation (done)
- **Phase 3.1.2a** — Admin UI closeout (done)
- **Phase 3.1.2b** — Gallery content-class filter (done)
- **Phase 3.1.2c** - Server identity + unified LLM fallback + entity resolver hardening (done)
- **Phase 3.8d** - Medium pilot accepted; 994 staged-success rows imported, six failed rows deferred
- **Next options** - Phase 4.0 plan-only entity/source strategy, Phase 3.9 production ingestion ledger, failed-row recovery, proper-noun localization strategy

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI (Python 3.12) |
| Frontend | Jinja2 + Tailwind CSS + Vanilla JS |
| Database | PostgreSQL 17 |
| AI Model | WDv3 ONNX (SmilingWolf) + CLIP ViT-B/32 ONNX (Xenova) via onnxruntime |
| Cache | Redis 7+ (optional) |

## Upstream Attribution

Built on top of **Blombooru** — a self-hosted media tagging tool.

- Upstream: https://github.com/mrblomblo/blombooru
- License: MIT

V.I.O.L.E.T. extends Blombooru with local library scanning, AI auto-tagging, tag provenance tracking, Chinese localization, iCloud integration, and content classification, optimized for Danbooru-style tag retrieval of anime/illustration collections.
