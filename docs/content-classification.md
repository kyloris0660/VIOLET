# Content Classification — Phase 3 Documentation

## Status

**Phase 3 = Foundation + Evaluation Harness only.**

The content classification infrastructure (schema, job system, admin UI, search filters, evaluation harness) is complete and working. However, the heuristic classifier has a **97.4% non-anime false positive rate** and is **not suitable for production filtering**.

**Do NOT rely on this feature for:**
- iCloud import gating / filtering
- Full-library automatic classification
- Any workflow requiring reliable non-anime rejection

A model-backed classifier (Phase 3.1) is required before production use. See [Phase 3.1 plan](#phase-31-plan) below.

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

# Heuristic thresholds (will be replaced in Phase 3.1)
CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD=5
CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD=0.5
```

## Evaluation Results

Tested with `scripts/evaluate_content_classification.py` against three real datasets:

| Dataset | Ground Truth | Total | Anime | Non-Anime | Unknown | Key Metric |
|---------|-------------|-------|-------|-----------|---------|------------|
| VioletTest100 | mixed | 145 | 145 (100%) | 0 | 0 | distribution only |
| VioletTest100_2 | anime | 81 | 81 (100%) | 0 | 0 | Anime recall: **100%** |
| VioletPhase3Eval | non_anime | 39 | 38 (97.4%) | 1 (2.6%) | 0 | Non-anime FP rate: **97.4%** |

**Conclusion:** Anime recall is excellent (100%), but non-anime rejection is near-zero (97.4% of non-anime images incorrectly classified as anime). The heuristic classifier cannot be used for filtering.

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

## Phase 3.1 Plan

### Problem

The WD tagger generates many confident tags for any image type. A simple tag-count threshold cannot distinguish anime from non-anime.

### Candidate Approaches

1. **CLIP zero-shot / embedding-based**: Encode images with CLIP, compare cosine similarity to "anime illustration" vs "photograph" text prompts. No training data needed.
2. **Lightweight dedicated anime-vs-photo CNN**: Fine-tune MobileNet/EfficientNet-B0 on anime vs photo datasets. Fast inference, highest expected accuracy.
3. **WD tagger prediction distribution**: Analyze the full WD prediction vector (distribution shape, probability mass, art-style tag presence) rather than simple count.
4. **Manual override workflow**: Use existing PUT endpoint as primary workflow until model is ready.

### Evaluation Targets

- VioletTest100_2 (anime): recall >= 80%
- VioletPhase3Eval (non_anime): false positive rate <= 10-15%
- Evaluation harness is fully reusable

### Infrastructure Reuse

Phase 3 provides: ContentClassEnum, 6 media columns, classification job system, search filters, admin UI, inline classification hook, and evaluation harness. Only `content_classifier.py` needs replacement.

## Key Files

| File | Role |
|------|------|
| `backend/app/enums.py` | `ContentClassEnum` |
| `backend/app/models.py` | 6 `content_class_*` columns, `ClassificationJob` model |
| `backend/app/database.py` | `migrate_add_content_classification` migration |
| `backend/app/config.py` | `CONTENT_CLASSIFICATION_*` settings |
| `backend/app/services/content_classifier.py` | Heuristic classifier (to be replaced in Phase 3.1) |
| `backend/app/services/classification_job_service.py` | Classification job lifecycle |
| `backend/app/routes/admin/content_classification.py` | Admin API endpoints |
| `backend/app/utils/search_parser.py` | `class:` meta filter |
| `frontend/templates/admin.html` | Content Classification admin section |
| `frontend/static/js/admin.js` | Classification UI logic |
| `scripts/evaluate_content_classification.py` | Evaluation harness |
| `eval_results.json` | Latest evaluation results |
