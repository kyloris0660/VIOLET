# AnimeLocalBooru — Project Roadmap

## Project Vision

Build a personal, local anime/illustration image library on top of [Blombooru](https://github.com/mrblomblo/blombooru). The core value is **Danbooru-style tag-based retrieval**, not generic file browsing.

The finished system should:

- Scan a continuously-updating local image directory (e.g. `C:\Users\kyloris\Pictures\iCloud Photos`)
- Reliably import anime/illustration images while skipping duplicates, corrupted files, undownloaded placeholders, and unsupported formats
- Automatically generate high-quality tags via AI (WDv3 / future models)
- Support searching and filtering by tag with full Danbooru syntax
- Cover character, copyright, artist, general, meta, and rating tag namespaces
- Record each tag's origin (AI, manual, booru import), confidence, and lock status
- Allow manual correction, deletion, and locking of tags — manual always wins over AI
- Eventually support tag aliases, tag implications, character/copyright databases, reverse image search, source completion, similar-image detection, and character clustering

---

## Completed Phases

### Phase 0 — Project Bootstrap

**PR:** #1 · **Commit:** `cd69b27`

- Imported Blombooru upstream into the AnimeLocalBooru repository
- Verified local dev environment (Python venv + PostgreSQL)
- Confirmed core functionality: upload, tag CRUD, search, thumbnails, scan-media, admin panel, onboarding

### Phase 1 — Local Library Scan MVP

**PR:** #2 · **Commit:** `46dca33`

- `POST /api/admin/scan-local-library` endpoint
- Recursive scan of external directories with copy-mode import
- Windows path + spaces support, `|`-separated multi-path in `.env`
- JSON body `{"paths": [...]}` override
- MD5 hash dedup, per-file error isolation
- Supports `.jpg/.jpeg/.png/.webp/.gif`; skips `.icloud`, zero-byte, symlinks
- Original files are never moved or deleted
- Original path stored in `Media.source` as `file://` URI
- Full documentation in `docs/local-library-scan.md`

### Phase 1.5 — Scan Safety & UX (PR #4)

**Commit:** `5d025aa`

- `dry_run` mode, `max_files` limit, Admin UI for scan
- Safe preview of large directories before real import

### Phase 1.6 — Scan Job System / Progress / History (PR #5)

**Commit:** `ec2a9a0`

- Background scan jobs with progress polling, cancel, history
- `blombooru_scan_jobs` table, stale recovery, path safety

### Phase 2 — Tag Metadata Foundation

Extended `blombooru_media_tags` with provenance tracking:

- Added `source`, `confidence`, `is_locked`, `is_suggestion`, `created_at`, `updated_at` columns
- Tag service (`backend/app/services/tag_service.py`) with helpers for manual, AI, and booru import tags
- Priority rule: manual/locked tags never overwritten by AI
- Suggestions excluded from search and tag counts
- Existing tags backfilled as `manual/1.0/locked/confirmed`
- Media detail API exposes `tag_provenance` dict
- Full documentation in `docs/tag-metadata-foundation.md`

---

## Upcoming Phases

### Phase 2.1 — AI Auto Tagging

**Goal:** Automatically tag imported images using the existing WDv3 (SmilingWolf) ONNX tagger.

| Feature | Detail |
|---------|--------|
| Reuse `backend/app/services/wd_tagger.py` | Already integrated in Blombooru |
| Call tagger during or after import | Generate `general`, `character`, `rating` tags |
| Stricter character threshold | Character tags need higher confidence than general tags |
| Tag source tracking | All AI tags marked `source=ai_wd` with confidence score |
| Low-confidence = suggestion | Below threshold → stored but not displayed as confirmed |

### Phase 2.2 — Character & Copyright Enrichment

**Goal:** Build richer tag relationships using Danbooru-style data.

- Tag alias support (e.g. `miku` → `hatsune_miku`)
- Tag implication support (e.g. `hatsune_miku` → `vocaloid`)
- Character–copyright linking (Blombooru already has basic infrastructure)
- CSV/external data import for bulk tag enrichment
- Tag descriptions, usage counts, and category metadata

### Phase 3 — Anime Filtering

**Goal:** Automatically detect and optionally skip non-anime images during import.

- Leverage WDv3 confidence as a proxy (very low confidence = likely not anime)
- Or introduce a dedicated anime/photo classifier
- Depends on Phase 2.1 (AI inference pipeline must exist first)

### Phase 4 — iCloud Photos Watcher / Scheduled Scan

**Goal:** Eliminate manual scan triggers.

- Filesystem watcher or periodic cron-style scan
- Requires Phase 1.5 safety controls to be in place
- Must handle iCloud sync edge cases (partial downloads, file locks, .icloud placeholders appearing/disappearing)

### Future Ideas (unscheduled)

- Reverse image search (SauceNAO / IQDB integration)
- Source completion (auto-fetch Pixiv/Twitter source URL)
- Similar image / near-duplicate detection (perceptual hashing)
- Character clustering (group images by character across different art styles)
- Batch tag editor in the UI
- Tag statistics dashboard

---

## Development Standards

### Branching & Delivery

Every phase follows this workflow:

1. Create a feature branch from `main`
2. Plan → implement → test locally
3. Verify: all new features work, no existing features broken, no sensitive files staged
4. Commit with conventional commit message (`feat:`, `fix:`, `docs:`, etc.)
5. Push branch, create PR with summary / scope / testing / limitations
6. Squash merge, delete branch
7. Checkout `main`, pull
8. **Stop.** Output delivery report. Do not auto-start the next phase.

### Safety Rules

**Never commit:**
`.env`, `venv/`, `data/`, `media/`, `storage/`, test images, cache files, tokens, passwords, API keys

**Never do without explicit approval:**
- Delete or move original files in external directories
- Full-scan a real iCloud Photos directory without a prior dry-run
- Implement AI tagging + anime filter + watcher + clustering in a single phase
- Large-scale refactors or frontend framework replacements
- Database migrations (must be planned and reviewed first)
