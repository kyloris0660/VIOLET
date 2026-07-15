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

For multilingual creator closure, `(provider, stable_creator_id, creator_role)`
is the deterministic source-layer identity anchor. Canonical display names,
account handles, and trusted historical names are observations of that anchor,
not independent identities. Candidate generation connects each unique alias to
the anchor in linear star topology; it must not create an all-pairs alias graph.
Shared surface strings across different anchors stay component-local and cannot
authorize a cross-stable-ID union.

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
identity boundaries. A `cannot_link` relation blocks identity materialization
and unsupported alias propagation; it does not globally suppress direct media
that independently carry the same observed name. A supported shared-name result
union across constrained components is valid and does not imply identity union.

The canonical fallback-eligible signal states are `materialized_identity`,
`isolated_evidence`, `active`, and the query-visible legacy compatibility state
`needs_review`. The latter is evidence compatibility only, never a human work
queue; no final materialized SourceConcept may remain in that state. Rejected,
`rejected_evidence`, superseded, invalid, and deleted signals are excluded.
Both endpoints of a propagated relation must be eligible before an active
fallback-index row is written. Direct evidence remains query-visible on the
media that owns it. Each positive query term is applied as a media-level AND
constraint. Ordinary gallery search keeps the fallback disabled by default until
ML1 validates the full runtime path; direct and materialized identity support
still obey the corrected shared-name semantics.

See `docs/source-concept-tag-search-semantics.md` for the durable identity-union,
search-union, multilingual-alias, creator, and AND-intersection rules.

## Optional Manual Overrides

A future manual alias override may support rare title, nickname, or correction
exceptions. Such overrides must be explicit, optional, auditable, reversible,
and outside the normal completion path. Their absence must never block the
pipeline.
