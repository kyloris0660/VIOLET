# Current Mainline Roadmap

Status: PR #135 is merged at `5bbbb8ff13b140ea77a839757603714bfdd87181`.
SCV2-R2R is accepted as `partial_autonomous_closure`; SCV2-ML1 is the current
approved phase.

## Accepted Mainline

1. R1R merged in PR #132.
2. SCV2-A1R merged in PR #133.
3. SCV2-R2 merged in PR #134 at `d553a7f51222f2c52c3fe5014e878faed7f7b5a1`.
4. SCV2-R2R merged in PR #135 at `5bbbb8ff13b140ea77a839757603714bfdd87181`.
   It accepted complete autonomous pair disposition, constraint-safe non-human
   materialization, immutable-evidence proof, and a disabled-by-default source
   evidence fallback. It did not authorize production or full-library work.

## Corrected Route Semantics

The former `SCV2-SR1: Context-Aware Disambiguated Source Search` recommendation
is superseded. It incorrectly treated a legitimate shared bare-name result union
as generic contamination.

- Identity union and search-result union are separate.
- `cannot_link` blocks identity union and unsupported alias propagation, not direct
  retrieval of independently supported same-name media.
- Bare names return the union of legitimate matches across concepts/roles/works.
- Additional terms use media-level AND intersection for disambiguation.
- R2R broad-union/cannot-contamination counts remain historical diagnostics and
  are not product-failure gates under the corrected policy.

## Current Approved Phase

`SCV2-ML1: Multilingual Alias and Source-Metadata Closure`

The initial read-only, zero-network audit has completed with
`blocked_pixiv_incremental_acquisition_approval_required`. It fully accounted
`2,285` candidate media/pages and `2,235` distinct works; `1,713` missing-work
requests require a separate private-manifest approval. This blocked result does
not authorize gallery-dl, Pixiv, LLM, or another phase.

ML1 owns:

- canonical Pixiv filename-candidate completeness accounting over actual data;
- existing Pixiv/gallery-dl creator metadata preservation and source-layer search;
- a real fixed-evidence multilingual alias benchmark;
- candidate-generation recall and representative-edge/fresh-schema debt;
- actual application-runtime shared-name union and AND-intersection validation;
- zero-network execution first, with separate Pixiv acquisition and LLM gates.

The primary unresolved quality question is multilingual alias coverage and
candidate-generation recall, not whether one shared bare name returns media from
several correctly separated identities.

## Stop Boundary

Do not start PX1-B broad acquisition, Provider-2, general scale-up, Entity bridge,
production, full-library execution, truth promotion, media import, AI tagging,
classification, localization, or another phase from ML1. Any incremental Pixiv
acquisition or new-pair LLM adjudication requires its own exact manifest and
separate operator approval.
