# Pixiv Metadata Ingestion And Promotion Policy

## Continuous Import Gate

Every new media import runs `phase44p0_pixiv_filename_prior_v1` over preserved
filename/path evidence. Non-Pixiv rows are `not_applicable_non_pixiv`. Every
canonical Pixiv work/page is persisted in the provider-neutral source metadata
registry and deduplicated by distinct work ID.

An import batch is source-metadata complete only when:

`pixiv_candidate_count = metadata_complete_count + terminal_remote_unavailable_count`

Pending, retryable, conflicted, authentication/rate/network, parser, identity,
or normalization failures keep the batch incomplete. Terminal unavailable is
valid only with durable authenticated deleted/private/permanently-unavailable
evidence. The workflow is an explicit observable CLI/run entrypoint with
checkpoint/resume; no hidden daemon is allowed.

## Acquisition Safety

External execution requires rotated credentials, the non-secret
`VIOLET_CREDENTIAL_ROTATION_CONFIRMED=true` confirmation and the one-time
stage-quality owner-sample confirmation
`VIOLET_PIXIV_OWNER_SAMPLE_VALIDATION_CONFIRMED=true`. The owner-sample gate is
checked before credentials or provider profiles are read; it is not a per-item
human dependency in normal ingestion. After both confirmations, execution still
requires a clean scan against known compromised-secret SHA-256 fingerprints and
a redacted authentication preflight. gallery-dl execution is metadata-only (`--dump-json --no-download`),
uses at least two-second request spacing, at most three bounded attempts per
work, stops after exhausted retryable/systemic failures, has no fallback
provider, and never downloads/imports media.

The canonical compatible-complete status policy is shared by import, audit,
runner, closure, tests, and contract. It includes `observed`, `active`,
`accepted`, and `metadata_complete`. Generic retry/failure transitions may only
update the exact attempted open queue rows; `metadata_complete`,
`terminal_remote_unavailable`, and `filename_identity_conflict` are preserved.

## Creator Retention

Raw provider metadata, normalized work/page facts, creator stable ID, display
name, account/handle, profile/provider identity, title, tags, and provenance are
retained. Display/account observations remain source-layer/search evidence and
do not create Entity truth or confirmed assignments.

## Production Promotion

Reusable fixed evidence is keyed by provider, stable work ID, page index,
parser/policy compatibility, and immutable content fingerprint. Development DB
row IDs are never copied. Compatible raw/normalized facts and compatible LLM
judgments may be reused as evidence.

Production must recompute SourceConcept membership/IDs, union-find or cluster
outputs, candidate blocks/partitions, signal links, materialized concepts and
aliases, fallback/search indexes, graph/route/confidence metrics, and search
benchmarks. PR #136 does not authorize production execution.

## Default LLM Budget

One finite, reproducible, primary-provider, cache-first bounded execution with
projected aggregate cost including retries of at most USD 10.00 is
pre-authorized only when it uses the approved model/policy family, atomic
checkpoint/resume, no fallback, no image upload, no production/truth write, and
the current approved semantic scope. Retries share the same cap; splitting runs
to evade it is forbidden. Any projected/actual overrun or provider/model/scope
change requires new approval. This policy does not authorize Pixiv acquisition.
