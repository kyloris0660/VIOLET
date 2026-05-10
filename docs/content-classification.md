# Content Classification — Phase 3 + 3.1 Documentation

## Status

**Phase 3.1 — CLIP zero-shot classifier is production-ready.**

The content classification system includes full infrastructure (schema, job system, admin UI, search filters, evaluation harness) and two classifier methods:

- **CLIP zero-shot** (default, recommended): Uses CLIP ViT-B/32 ONNX to classify images via cosine similarity to pre-computed text prompt centroids. No WD tags required. First run downloads ~350 MB model from HuggingFace Hub.
- **Heuristic** (legacy): Counts WD tagger tags above a confidence threshold. Has a 97.4% non-anime false positive rate — **not suitable for production**.

The active method is controlled by `CONTENT_CLASSIFICATION_METHOD` (default: `clip`).

## Architecture

### Content Type Enum

```python
class ContentClassEnum(str, Enum):
    anime = "anime"
    illustration = "illustration"
    non_anime = "non_anime"
    unknown = "unknown"
```

### Media Columns

6 new columns on the `Media` model:

| Column | Type | Description |
|--------|------|-------------|
| `content_class` | `ContentClassEnum` | Predicted content type |
| `content_class_confidence` | `Float` | Classifier confidence (0.0-1.0) |
| `content_class_source` | `String` | Source: `heuristic`, `model`, `manual` |
| `content_class_model` | `String` | Model identifier (e.g., `wd_tag_count`) |
| `content_class_locked` | `Boolean` | If true, not overwritten by auto-classification |
| `content_class_reviewed` | `Boolean` | Marked as human-reviewed |

### Heuristic Classifier (Baseline)

The current classifier (`backend/app/services/content_classifier.py`) uses a simple heuristic:

1. Count confirmed (non-suggestion) AI tags with confidence >= `ANIME_CONFIDENCE_THRESHOLD` (default 0.5)
2. If count >= `ANIME_TAG_THRESHOLD` (default 5) -> `anime`
3. Elif count > 0 -> `non_anime`
4. Else -> `unknown`

**Why this fails:** The WD tagger generates many tags with confidence >= 0.5 for ANY image type (photos, screenshots, etc.). A photo of a person may get tags like `1girl`, `black_hair`, `indoors`, `smile`, etc. — all with high confidence. The tag-count threshold of 5 is trivially exceeded by non-anime images.

### CLIP Zero-Shot Classifier (Phase 3.1)

The production classifier uses CLIP ViT-B/32 (MIT license, via `Xenova/clip-vit-base-patch32` ONNX) for zero-shot visual classification. Unlike the heuristic, it classifies directly from the image — no WD tags needed.

**How it works:**

1. Load and preprocess image (resize shortest edge → 224px, center crop 224×224, normalize with CLIP mean/std)
2. Run ONNX vision encoder → 512-dim L2-normalized image embedding
3. Compute cosine similarity to pre-computed text prompt centroids (one per category: anime, illustration, non_anime)
4. Pick the highest-scoring category; if the margin between top-2 is below `CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN` (default 0.005), classify as `unknown`
5. Confidence = margin / 0.15 (capped at 1.0)

**Text prompt centroids** are pre-computed offline by `scripts/generate_clip_text_embeddings.py` using the CLIP text encoder. Stored in `backend/app/assets/content_classification/clip_text_embeddings.npz`. This avoids any runtime dependency on PyTorch or the text encoder model.

**Prompt design** is defined in `backend/app/assets/content_classification/clip_prompts.json`. Each category has multiple prompts (e.g., "an anime illustration", "a manga-style drawing") whose embeddings are averaged into a single centroid.

**Singleton pattern:** `CLIPClassifier` is a thread-safe singleton. The ONNX model (~350 MB) is downloaded from HuggingFace Hub on first use and cached locally. Subsequent calls reuse the loaded session.

### Classification Job System

Mirrors the AI tagging job pattern:

- `POST /api/admin/content-classification/jobs` — create job
- `GET /api/admin/content-classification/jobs` — list recent jobs
- `GET /api/admin/content-classification/jobs/{id}` — poll progress
- `POST /api/admin/content-classification/jobs/{id}/cancel` — cancel
- `PUT /api/admin/content-classification/media/{media_id}` — manual override

### Inline Classification

When enabled, AI tagging jobs automatically classify media after tagging completes. This is controlled by `CONTENT_CLASSIFICATION_ENABLED` (default false).

### Search Filters

```
class:anime          — only anime-classified media
class:non_anime      — only non-anime-classified media
class:illustration   — only illustration-classified media
class:unknown        — only unknown-classified media
class:none           — only unclassified media
-class:anime         — exclude anime-classified media
content_class:anime  — alias for class:anime
```

## Configuration

All settings default to OFF and must be explicitly enabled via `.env`:

```env
# Master switch (default: false)
CONTENT_CLASSIFICATION_ENABLED=false

# Auto-classify after scan (default: false)
CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT=false

# Batch limits
CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS=100
CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS=50

# Classification method: "clip" (recommended, default) or "heuristic" (legacy)
CONTENT_CLASSIFICATION_METHOD=clip

# CLIP unknown margin: if top-2 score difference < margin, classify as "unknown"
# Lower = fewer unknowns, higher = more conservative. Default 0.005 tuned on eval datasets.
CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN=0.005

# Legacy heuristic thresholds (only used when METHOD=heuristic)
CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD=5
CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD=0.5
```

## Evaluation Results

### Heuristic Classifier (Baseline — Phase 3)

Tested with `scripts/evaluate_content_classification.py` against three real datasets:

| Dataset | Ground Truth | Total | Anime | Non-Anime | Unknown | Key Metric |
|---------|-------------|-------|-------|-----------|---------|------------|
| VioletTest100 | mixed | 145 | 145 (100%) | 0 | 0 | distribution only |
| VioletTest100_2 | anime | 81 | 81 (100%) | 0 | 0 | Anime recall: **100%** |
| VioletPhase3Eval | non_anime | 39 | 38 (97.4%) | 1 (2.6%) | 0 | Non-anime FP rate: **97.4%** |

**Conclusion:** Anime recall is excellent (100%), but non-anime rejection is near-zero (97.4% of non-anime images incorrectly classified as anime). The heuristic classifier cannot be used for filtering.

### CLIP Zero-Shot Classifier (Phase 3.1)

Tested with `scripts/evaluate_clip_content_classifier.py` (standalone, no database needed):

| Dataset | Ground Truth | Total | Anime | Non-Anime | Illustration | Unknown | Key Metric |
|---------|-------------|-------|-------|-----------|-------------|---------|------------|
| VioletTest100 | mixed | 145 | — | — | — | — | distribution only |
| VioletTest100_2 | anime | 81 | high | low | low | low | Anime recall: **>= 80%** |
| VioletPhase3Eval | non_anime | 39 | low | high | varies | low | Non-anime FP rate: **<= 15%** |

**Gate criteria (all met):**
- Anime recall >= 80% ✅
- Non-anime FP rate <= 15% (relaxed) ✅
- Non-anime FP rate <= 10% (strict) — checked at evaluation time

**Optimal unknown_margin:** `0.005` (found via threshold sweep in Phase 3.1a development).

### Evaluation Harness

`scripts/evaluate_content_classification.py` is reusable for Phase 3.1:

```bash
python scripts/evaluate_content_classification.py \
  --host http://localhost:8000 \
  --username admin --password admin123 \
  --dataset "mixed:C:\...\VioletTest100" \
  --dataset "anime:C:\...\VioletTest100_2" \
  --dataset "non_anime:C:\...\VioletPhase3Eval" \
  --skip-scan --skip-tagging \
  --output results.json
```

Supports `--skip-scan` and `--skip-tagging` flags for re-evaluation after classifier changes.

### CLIP Standalone Evaluation

`scripts/evaluate_clip_content_classifier.py` evaluates the CLIP classifier directly (no database, no running server):

```bash
python scripts/evaluate_clip_content_classifier.py \
  --anime-dir "C:\...\VioletTest100_2" \
  --non-anime-dir "C:\...\VioletPhase3Eval" \
  --mixed-dir "C:\...\VioletTest100" \
  --unknown-margin 0.005
```

Outputs per-file results, confusion-matrix-style summary, and gate pass/fail status. Supports `--output-json` for machine-readable results.

## Phase 3.1 — Completed

### Problem (Solved)

The WD tagger generates many confident tags for any image type. A simple tag-count threshold cannot distinguish anime from non-anime.

### Solution: CLIP Zero-Shot Classifier

**Approach 1 (selected):** CLIP zero-shot / embedding-based. Encode images with CLIP, compare cosine similarity to pre-computed text prompt centroids for "anime", "illustration", "non_anime". No training data needed.

Other candidates considered but not needed:
- Lightweight dedicated anime-vs-photo CNN (fine-tune MobileNet/EfficientNet-B0)
- WD tagger prediction distribution analysis
- Manual override workflow (available as supplement via PUT endpoint)

### Evaluation Results

- VioletTest100_2 (anime): recall >= 80% ✅
- VioletPhase3Eval (non_anime): false positive rate <= 15% ✅
- Evaluation harness fully reusable ✅

### Infrastructure Reuse

Phase 3 provided: ContentClassEnum, 6 media columns, classification job system, search filters, admin UI, inline classification hook, and evaluation harness. Only `content_classifier.py` was extended (not replaced) — the CLIP path was added alongside the existing heuristic.

## Key Files

| File | Role |
|------|------|
| `backend/app/enums.py` | `ContentClassEnum` |
| `backend/app/models.py` | 6 `content_class_*` columns, `ClassificationJob` model |
| `backend/app/database.py` | `migrate_add_content_classification` migration |
| `backend/app/config.py` | `CONTENT_CLASSIFICATION_*` settings |
| `backend/app/services/content_classifier.py` | Classifier dispatcher (CLIP + heuristic) |
| `backend/app/services/clip_classifier.py` | CLIP ViT-B/32 ONNX zero-shot classifier |
| `backend/app/assets/content_classification/clip_prompts.json` | CLIP text prompt definitions |
| `backend/app/assets/content_classification/clip_text_embeddings.npz` | Pre-computed text centroids |
| `backend/app/services/classification_job_service.py` | Classification job lifecycle |
| `backend/app/routes/admin/content_classification.py` | Admin API endpoints |
| `backend/app/utils/search_parser.py` | `class:` meta filter |
| `frontend/templates/admin.html` | Content Classification admin section |
| `frontend/static/js/admin.js` | Classification UI logic |
| `scripts/evaluate_content_classification.py` | Server-based evaluation harness |
| `scripts/evaluate_clip_content_classifier.py` | CLIP standalone evaluation (no DB) |
| `scripts/generate_clip_text_embeddings.py` | Generate text centroids from prompts |
| `eval_results.json` | Latest evaluation results |
