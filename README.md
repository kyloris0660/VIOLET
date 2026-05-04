# AnimeLocalBooru

A personal, self-hosted anime image library with Danbooru-style tag-based retrieval. Built for organizing and searching local anime/illustration collections using AI-powered auto-tagging.

Based on [Blombooru](https://github.com/mrblomblo/blombooru) by mrblomblo.

## Current Feature Status

| Feature | Status | Notes |
|---------|--------|-------|
| Blombooru core (gallery, upload, search, tags) | Done | Full upstream functionality |
| Local Library Scan | Done | Import from external directories |
| Dry-run & max_files safety | Done | Preview before committing |
| Scan job progress/history/cancel | Done | Background jobs with Admin UI |
| Tag metadata foundation | Done | Provenance tracking (source, confidence, locked, suggestion) |
| WDv3 AI auto tagging MVP | Done | Manual trigger, dry-run, batch, Admin UI |
| Confirmed / suggestion tags | Done | Dual threshold system |
| Manual/locked tag protection | Done | AI never overwrites human tags |
| AI tag review UI | Phase 2.2 | Confirm/reject suggestions |
| Auto-tag after import | Phase 2.3 | Optional, non-blocking |
| Anime/photo filtering | Phase 3 | Distinguish anime from photos |
| Reverse image search | Phase 3 | SauceNAO/IQDB integration |
| Character/copyright database | Future | External data enrichment |
| Filesystem watcher | Phase 4 | Auto-detect new files |

## Quick Start (Windows Local Development)

### Prerequisites

- Python 3.12+
- PostgreSQL 17
- Git

### Setup

```powershell
git clone https://github.com/kyloris0660/AnimeLocalBooru.git
cd AnimeLocalBooru
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from the example:

```powershell
Copy-Item example.env .env
```

Edit `.env` with your PostgreSQL password. For AI tagging, add:

```env
AI_TAGGING_ENABLED=true
```

### Running

```powershell
.\venv\Scripts\Activate.ps1
python run.py --debug
```

Open http://localhost:8000. First run shows the onboarding page to set up admin credentials and database connection.

### Default Development Credentials

After onboarding: `admin` / `admin123` (local use only, never for production).

## GUI Entry Points

| Page | URL | Purpose |
|------|-----|---------|
| Gallery | `/` | Browse, search, and view media |
| Media Detail | `/media/{id}` | View individual media with tags |
| Admin Panel | `/admin` | System settings, content management, AI tagging |
| Login | `/login` | Admin authentication |

### Admin Panel Tabs

- **System** — App settings, caching, API keys, backup, updates
- **Content** — Media upload, Local Library Scan, AI Tagging, Tags, Albums
- **Stats** — Upload trends, tag distribution charts
- **Account** — Change password/username

## AI Tagging

The AI tagger uses the WDv3 (SmilingWolf) model to predict Danbooru-style tags from anime images. It identifies visual features (hair color, clothing, pose, background) and some popular characters.

### Key Points

- Manually triggered from Admin UI or API — does NOT auto-run
- First use downloads model (~450 MB) from HuggingFace Hub
- Dual threshold: tags become "confirmed" (searchable) or "suggestion" (stored, not searchable)
- Never overwrites manually-added or locked tags
- Always dry-run first when testing

See [AI Tagging Usage Guide](docs/ai-tagging-usage-guide.md) for complete usage instructions.

## Safety Guidelines

| Action | Recommendation |
|--------|---------------|
| Scanning iCloud Photos | **Always** dry-run + max_files=100 first |
| AI tagging | **Always** dry-run single image first |
| Batch AI tagging | Start with max_items=3-5, not full library |
| Model files | Never commit to git (`.gitignore` handles this) |
| `.env` file | Never commit (contains passwords) |
| Full library operations | Only after incremental testing succeeds |

## Documentation

| Document | Contents |
|----------|----------|
| [AI Tagging Usage Guide](docs/ai-tagging-usage-guide.md) | Complete usage guide with examples |
| [AI Auto Tagging Technical](docs/ai-auto-tagging.md) | Architecture, API reference, data model |
| [Local Library Scan](docs/local-library-scan.md) | Scan feature documentation |
| [Tag Metadata Foundation](docs/tag-metadata-foundation.md) | Provenance system design |
| [Project Roadmap](docs/project-roadmap.md) | Full phase plan |
| [Current Handoff](docs/current-handoff.md) | Latest state for dev resumption |
| [Development Log](docs/local-anime-library-devlog.md) | Per-phase technical notes |

## Roadmap

- **Phase 2.2** — AI Tag Review UI (confirm/reject suggestions, bulk operations)
- **Phase 2.3** — Optional Auto Tagging After Import (background job, default off)
- **Phase 3** — Anime Filtering & Source Identification (SauceNAO, IQDB)
- **Phase 4** — Filesystem Watcher & Scheduled Scan

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI (Python 3.12) |
| Frontend | Jinja2 + Tailwind CSS + Vanilla JS |
| Database | PostgreSQL 17 |
| AI Model | WDv3 ONNX (SmilingWolf) via onnxruntime |
| Caching | Redis 7+ (optional) |

## Upstream Attribution

This project is based on **Blombooru** — a self-hosted media tagging tool.

- Upstream repository: https://github.com/mrblomblo/blombooru
- License: MIT

AnimeLocalBooru extends Blombooru with local library scanning, AI auto-tagging, and tag provenance tracking, specifically optimized for anime/illustration collections with Danbooru-style tag retrieval.
