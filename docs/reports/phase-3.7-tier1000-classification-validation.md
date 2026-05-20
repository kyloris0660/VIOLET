# Phase 3.7 - Tier-1000 Content Classification Validation

Date: 2026-05-20

## Scope

- Target: `Media.source = "violet:tier1000:phase3.5"`
- Expected target media: 995
- Purpose: validate content classification for the already imported Tier-1000 set before future tag-derived workflows scale further.
- Exclusions: no media import/copy, no AI tagging rerun, no localization rerun, no Entity Resolver, no cleanup/reset/drop/truncate.

## Safety Gates

- Python identity: approved project venv Python.
- DB: `blombooru` on localhost, development mode.
- Source label locked: `violet:tier1000:phase3.5`.
- Classification writes required a non-empty DB backup artifact.
- Classification auto-after-import disabled.
- AI auto-tagging, AI auto-localization, tag translation background/auto/LLM, and Entity Resolver disabled.
- Active AI/classification/translation job gates checked before write mode.
- CLIP readiness preflight passed in cache-only mode.

## Backup

- Backup artifact: `phase-3.7-tier1000-before-20260520-125024.dump`
- Size: 1,392,536 bytes
- Public docs redact the backup path; the backup file remains under gitignored `backups/`.

## Baseline

| Metric | Count |
|--------|-------|
| Target media | 995 |
| Classified before | 0 |
| Unclassified before | 995 |
| AI tag associations before | 53,354 |
| Tag rows before | 3,267 |
| AI jobs before | 46 |
| Translation jobs before | 15 |
| Classification jobs before | 4 |

## Classification Run

Runner: `scripts/run_phase37_tier1000_classification_scope_gate.py`

- Mode: `classify`
- Chunking: 10 explicit-media-ID jobs, chunk size 100 except final 95
- Trigger source: `phase3.7`
- Model/method: `clip`

| Result | Count |
|--------|-------|
| Processed | 995 |
| Failed | 0 |
| Classified after | 995 |
| Unclassified after | 0 |
| `anime` | 948 |
| `unknown` | 21 |
| `non_anime` | 26 |
| `illustration` | 0 |

## Side Effects

| Metric | Delta |
|--------|-------|
| Classification jobs | +10 |
| AI jobs | 0 |
| Translation jobs | 0 |
| Tag rows | 0 |
| Target AI associations | 0 |
| Target translated tag names | 0 |

## App/Admin Validation

Real dev DB smoke validation was run against the actual classified Tier-1000 data.

- Server: `http://127.0.0.1:8012`
- Environment: `VIOLET_ENV=development`
- DB: `blombooru`
- Branch: `phase3.7-tier1000-classification-scope-gate`
- Python: project venv
- Server identity check: PASS
- Browser: Playwright with Edge channel when available

Validated:

- Gallery page loads.
- `/api/media?limit=5&content_class=anime` returns classified imported media.
- Media detail API returns `content_class=anime` for sampled media.
- Original file endpoint returns HTTP 200.
- Thumbnail endpoint returns HTTP 200.
- Media detail page loads.
- Admin page loads after admin login.
- Content Classification stats show total 995, classified 995, unclassified 0, breakdown `anime=948`, `unknown=21`, `non_anime=26`.

No import, AI tagging, localization, Entity Resolver, cleanup, reset, drop, truncate, or delete operation was run during app validation.

## Tests

- `git diff --check` - PASS
- `python -m py_compile scripts/run_phase37_tier1000_classification_scope_gate.py` - PASS
- Focused suite: 193 passed, 1 skipped
- Full non-E2E suite: 915 passed, 10 skipped

## Notes

The 26 `non_anime` results are retained as classified media. Phase 3.7 does not delete tags or media. The follow-up tag scope gate documents that these media must be excluded from future tag statistics, tag-driven similarity, and future AI/localization candidate selection.
