# Phase 3.6 Tier-1000 AI Tagging + Localization Plan

## Scope

Phase 3.6 runs controlled AI tagging and visual tag localization for the Phase 3.5 import set only:

- Target selector: `Media.source = "violet:tier1000:phase3.5"`
- Expected media count: `995`
- No media import, copy, cleanup, reset, truncate, classification, Entity Resolver, or source/staging mutation
- AI tagging and localization are intentionally split into separate guarded steps

## Implementation

Added `scripts/run_phase36_tier1000_ai_localization.py` as a narrow runner instead of extending broad admin defaults.

The script supports:

- `baseline`: read-only DB baseline
- `ai-tag`: explicit `media_ids` AI job chunks selected by source label
- `localize`: controlled LLM translation for target AI tags in `general`/`meta` only

Write modes require:

- `--confirm-phase36 PHASE36_TIER1000_AI_LOCALIZATION`
- `--db-backup-file <nonzero dump>`
- `--expected-media-count 995`
- `CONTENT_CLASSIFICATION_ENABLED=false`
- `CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT=false`
- `ENTITY_ALIAS_RESOLVER_ENABLED=false`
- `TAG_TRANSLATION_BACKGROUND_ENABLED=false`
- `TAG_TRANSLATION_AUTO_ENABLED=false`
- `AI_TAGGING_AUTO_LOCALIZATION=false`

Additional AI gate:

- `AI_TAGGING_ENABLED=true`
- `TAG_TRANSLATION_LLM_ENABLED=false`

Additional localization gate:

- `TAG_TRANSLATION_LLM_ENABLED=true`
- proper-noun categories are not translated

## Execution Strategy

1. Confirm Phase 3.5 imported data and source label.
2. Create `pg_dump -Fc` backup under gitignored `backups/`.
3. Run AI tagging in chunks with explicit non-empty media ID lists.
4. Verify AI run created no classification jobs and no translation jobs.
5. Select newly missing target `general`/`meta` translations.
6. Run controlled LLM localization for those visual tags only.
7. Keep character/copyright/artist tags for manual/entity review in later phases.
8. Validate real dev DB app behavior and controlled test-env app startup/browser smoke.

## Safety Notes

- Public reports use redacted storage/backup labels only.
- API keys, auth header values, local source paths, iCloud paths, staged paths, and full backup paths are not recorded.
- `Media.source` remains `violet:tier1000:phase3.5`.
- AI tag deltas are reported separately as `tags_added`, `suggestions_added`, `media_tags` delta, `tag row delta`, and `media_with_ai_tags` delta.
