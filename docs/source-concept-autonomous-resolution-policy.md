# SourceConcept Autonomous Resolution Policy

## Normal Completion Model

SourceConcept resolution is a fully autonomous source-layer pipeline. Normal
operation and phase completion must not require an operator to inspect or
classify candidate concepts or relations.

Every eligible candidate pair receives exactly one machine disposition:

- `must_link` - eligible for identity materialization only after deterministic
  and component-level guards pass;
- `cannot_link` - a hard negative constraint that prevents direct and
  transitive identity union;
- `deferred_nonblocking` - evidence is insufficient, so the relation is
  retained for future automatic re-evaluation without identity union or a
  blocking human action.

Candidate accounting is complete only when:

```text
total_candidate_pairs
= must_link_count + cannot_link_count + deferred_nonblocking_count
```

Missing, duplicate, or silently discarded relations fail the phase contract.

## Materialization And Evidence Retention

Materialized `SourceConcept` rows contain only identity components that pass
the active materialization policy. `needs_review` is not a human workflow and
must not be used as an operator queue.

Unresolved and rejected signals remain in the source-layer signal projection.
Deferred pair relations live in a versioned, auditable, atomically written
non-materialized overlay. This preserves evidence without manufacturing an
identity concept. Re-evaluation is automatic when compatible evidence, cache,
or model policy becomes available.

## Search Recall

Source-layer search has two distinct paths:

1. the identity index searches aliases of materialized SourceConcepts and may
   project component-safe media;
2. the experimental evidence fallback searches direct source names, source
   tags, AI tags, isolated signals, and eligible deferred alias neighbors only
   when a caller opts in explicitly.

Evidence fallback improves retrieval without changing identity membership. It
must preserve provenance, role, work context, confidence, and cannot-link
boundaries. Fallback traversal is not Entity truth and must not create broad
media unions across constrained components.

The canonical fallback-eligible signal states are `materialized_identity`,
`isolated_evidence`, `active`, and the query-visible legacy compatibility state
`needs_review`. The latter is evidence compatibility only, never a human work
queue; no final materialized SourceConcept may remain in that state. Rejected,
`rejected_evidence`, superseded, invalid, and deleted signals are excluded.
Both endpoints of a relation must be eligible before an active fallback-index
row is written. Ordinary gallery search remains identity-only; fallback is
diagnostic/experimental and disabled by default until context-aware search is
implemented.

## Optional Manual Overrides

A future manual alias override may support rare title, nickname, or correction
exceptions. Such overrides must be explicit, optional, auditable, reversible,
and outside the normal completion path. Their absence must never block the
pipeline.
