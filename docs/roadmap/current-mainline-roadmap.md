# Current Mainline Roadmap

Status: SCV2-R2R is closing in PR #135 as `partial_autonomous_closure`.

## Accepted Mainline

1. R1R merged in PR #132.
2. SCV2-A1R merged in PR #133.
3. SCV2-R2 merged in PR #134 at `d553a7f51222f2c52c3fe5014e878faed7f7b5a1`.
4. SCV2-R2R in PR #135 closes autonomous pair disposition and non-human
   materialization. Its experimental evidence fallback remains disabled by default.

## Sole Recommended Next Phase

`SCV2-SR1: Context-Aware Disambiguated Source Search`

SR1, when separately approved, owns:

- role-aware and work-context-aware search;
- disambiguated candidate concept groups;
- no flat union across cannot-linked concepts;
- safe bare-name ambiguity handling;
- balanced positive/negative search benchmarks;
- identity results separated from source-evidence candidates.

SR1 is not implemented or started in PR #135.

## Stop Boundary

Do not start PX1-B, Provider-2, scale-up, Entity bridge, production,
full-library execution, metadata reacquisition, truth promotion, or another
phase from this PR. The fallback index is SourceConcept/source-layer output,
not Entity truth and not production-search authorization.
