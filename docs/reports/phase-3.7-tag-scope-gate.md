# Phase 3.7 - Tag Scope Gate

Date: 2026-05-20

## Decision

Future tag-derived workflows must be scoped by content class:

- Eligible: `anime`, `unknown`
- Ineligible: `illustration`, `non_anime`, `unclassified`

This applies to future AI tagging candidate selection, tag localization candidate selection, tag statistics, and tag-driven similarity/clustering. Phase 4 and similarity work have not started.

## Tier-1000 Scope Audit

Target source label: `violet:tier1000:phase3.5`

| Metric | Count |
|--------|-------|
| Target media | 995 |
| Eligible media (`anime` + `unknown`) | 969 |
| Ineligible media | 26 |
| `anime` | 948 |
| `unknown` | 21 |
| `non_anime` | 26 |
| `illustration` | 0 |
| `unclassified` | 0 |

## Current AI Tag Associations

Phase 3.6 ran before Phase 3.7 classification, so some already-created AI tag associations are attached to media that are now classified as ineligible.

| Metric | Count |
|--------|-------|
| AI associations on eligible media | 52,583 |
| AI associations on ineligible media | 771 |
| Ineligible media with AI tags | 26 |
| Distinct AI tags on eligible media | 2,933 |
| Distinct AI tags on ineligible media | 387 |

No cleanup or tag deletion was performed in Phase 3.7. These counts are audit evidence for future gating work.

## Localization Scope

Tag translations are tag-level shared records, not per-media records. Phase 3.7 therefore does not delete or relabel translations that are also attached to ineligible media.

Read-only audit:

- Translated tag names attached to eligible media: 2,832
- Translated tag names attached to ineligible media: 385

Future localization candidate selection should count only tags attached to `anime` or `unknown` media. Proper-noun/entity handling remains deferred to the Entity Metadata phase.

## Future Workflow Requirements

1. Run content classification before future large-scale AI tagging.
2. AI tagging should target only media classified as `anime` or `unknown`.
3. Tag localization should consider only tags attached to `anime` or `unknown` media.
4. Tag statistics used for recommendations/similarity should exclude `illustration`, `non_anime`, and `unclassified` media.
5. Tag-driven similarity/clustering must be scoped to `anime` or `unknown` media.
6. Existing Phase 3.6 ineligible associations should be treated as historical/audit evidence unless a later cleanup plan is explicitly approved.
