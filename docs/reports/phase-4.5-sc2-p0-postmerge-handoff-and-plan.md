# Phase 4.5-SC2-P0 Post-merge Handoff and SC2 Plan

## Summary

PR #96 / Phase 4.5-SC1 is merged into `main`. SC1 delivered the multi-source source-layer `SourceConcept` resolver core: additive schema, source signal adapters, aliases, evidence, links, search-preview rows, run ledger, readiness checks, and no-truth-write validation.

SC1 is not Entity truth. `SourceConcept` rows are source-layer evidence only; they are not `Entity`, `EntityAlias`, confirmed assignment, or `media_tags` truth.

The next planned feature phase is:

`Phase 4.5-SC2: SourceConcept search expansion and evidence UI`

## SC2 Target

SC2 should expose existing `SourceConcept` data to users through transparent search expansion and media-detail evidence UI while preserving the source-layer / unconfirmed boundary.

### Search Expansion

SC2 should allow query by supported `SourceConcept` aliases, for example:

- `q=神里綾華`
- `q=kamisato_ayaka`
- `q=Kamisato Ayaka`

The search path should use `SourceConcept` aliases and `SourceConceptSearchIndex` rows when evidence supports expansion. The UI must explain which `SourceConcept` expanded the query and why.

Required behavior:

- Normal tag search continues to work.
- Mixed normal tag + SourceConcept queries remain sane and bounded.
- Negative and exact query behavior does not become overbroad.
- F6 user-facing chip behavior remains: chips default to ordinary global `q=` search.
- Scoped source filters remain advanced/debug routes, not the primary user workflow.
- `needs_review` concepts are conservative by default: either displayed without expansion or expanded only through an explicit opt-in/control that makes review status clear.

### Media Detail SourceConcept Grouping

The media detail page should group available signals by `SourceConcept` when a concept is available:

- ordinary tags
- AI/model character tags
- source assertions
- source tags

The page should show unconfirmed source concept chips, aliases, and evidence summaries. These chips must be visually distinct from confirmed tags/entities and clearly labeled as source-layer / unconfirmed.

### Evidence Preview

SC2 should provide an expandable read-only evidence preview with:

- aliases
- providers
- signal origins
- trust tiers
- concept status
- evidence count

The preview must not expose local paths, secrets, raw private source labels, filenames, or unsafe internal data.

### Manual Promotion Preview Only

SC2 may show a disabled/no-op promotion preview:

- affected media count
- aliases that would be promoted
- evidence summary

It must not write `Entity`, `EntityAlias`, `EntityEvidence`, `MediaEntityCandidate`, `MediaEntityAssignment`, `LocalSourceHint`, confirmed assignment, `TagTranslation`, or `media_tags`. Actual promotion belongs to a later explicit Entity bridge phase.

## Acceptance Criteria

### Backend / API

- Implement read-only SourceConcept search expansion through an endpoint or integrated search path.
- Implement a read-only SourceConcept detail/evidence endpoint.
- Use `SourceConceptSearchIndex`; do not full-scan large tables for ordinary searches.
- Respect concept status, confidence, trust tier, and conservative `needs_review` behavior.
- Preserve normal tag search semantics.
- Prove no truth-path writes.

### Search Behavior

- Query by linked alias returns related media through SourceConcept search-preview index.
- `q=神里綾華`, `q=kamisato_ayaka`, and `q=Kamisato Ayaka` are supported when evidence links them to a concept.
- Mixed normal tag + SourceConcept search works.
- Exact and negative queries remain bounded.
- Search results show which SourceConcept expanded the query.

### UI

- Media detail page shows SourceConcept grouping for normal tags, AI/model character tags, source assertions, and source tags where available.
- Search results show SourceConcept expansion chips/explanations.
- Evidence preview is understandable and expandable.
- SourceConcept is clearly marked unconfirmed/source-layer.
- The main workflow is media detail and search, not an admin-only/debug-only screen.

### Validation

- Focused pytest for API/search semantics.
- Tests for read-only evidence endpoint redaction and no unsafe/private data exposure.
- Tests proving no writes to Entity/truth tables or `media_tags`.
- Playwright/real browser validation on a controlled test server.
- E2E coverage for media detail SourceConcept section, alias/concept chip click, search expansion, mixed normal tag + SourceConcept query, evidence preview, no console errors, and no truth writes.

## Risks and Mitigations

1. Over-broad expansion: `SourceConcept` is unconfirmed, so expansion must be transparent and conservative. Use status/confidence/trust gates and show the expansion reason.
2. UI confusion: SourceConcept chips must not look like confirmed Entity chips. Use explicit source-layer/unconfirmed labeling and separate styling.
3. Performance: use `SourceConceptSearchIndex` and bounded joins; avoid table-wide scans in the user search path.
4. `needs_review` concepts: default to display-only or opt-in expansion unless a later approved policy says otherwise.
5. F6 regression risk: keep global `q=` chip behavior and keep scoped source filters advanced/debug only.
6. Promotion creep: manual promotion is preview-only in SC2; no Entity/truth writes.
7. Quality claims: SC2 may rely on SC1 output, but must not claim full-library concept quality is proven.

## Non-goals

- No Entity truth writes.
- No `EntityAlias` truth writes.
- No `EntityEvidence`, `MediaEntityCandidate`, `MediaEntityAssignment`, or `LocalSourceHint` writes.
- No confirmed assignment creation.
- No `media_tags` mutation.
- No `TagTranslation` mutation.
- No provider/gallery-dl/source enrichment.
- No LLM runs.
- No source/iCloud/app-managed storage mutation.

## Future Documentation Phase

Recommended follow-up after SC2:

`Phase 4.5-DOC1: Documentation consolidation after SC2`

DOC1 should keep long-term docs concise and avoid duplicating rules across README, AGENTS, handoff, roadmap, reports, and test workflow. Hard safety constraints should move into code, tests, DB constraints, validation runners, and runtime guards where practical. Agent-facing docs should stay short and operational. README should remain a lightweight entry point, not a giant phase ledger.

DOC1 is intentionally not part of this P0 task and should not be smuggled into SC2 implementation.

## Artifact Lifecycle

- This report: public report / handoff / roadmap update.
- Future SC2 API/UI implementation: durable production code with focused tests and real browser validation.
- Future SC2 validation helpers, if any: reusable validation/safety tool only if they define a stable safety contract; otherwise keep phase-scoped.
