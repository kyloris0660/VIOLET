# Pixiv Metadata Ingestion And Promotion Policy

## Continuous Import Gate

Every new media import runs `phase44p0_pixiv_filename_prior_v1` over preserved
filename/path evidence. Non-Pixiv rows are `not_applicable_non_pixiv`. Every
canonical Pixiv work/page is persisted in the provider-neutral source metadata
registry and deduplicated by distinct work ID.

An ordinary import batch is source-metadata complete only when:

`pixiv_candidate_count = metadata_complete_count + terminal_remote_unavailable_count`

Pending, retryable, conflicted, authentication/rate/network, parser, identity,
or normalization failures keep the batch incomplete. Terminal unavailable is
valid only with durable authenticated deleted/private/permanently-unavailable
evidence. The workflow is an explicit observable CLI/run entrypoint with
checkpoint/resume; no hidden daemon is allowed.

The project-lead-governed state
`deferred_nonblocking_source_page_mismatch` is closed but is neither complete nor
terminal. It is valid only for an exact, durably attempted work/page where the
provider returned the correct work but did not provide the requested local page,
after the governed acquisition/replay route is exhausted. It must preserve raw
provider and queue provenance, select no conflict winner, create no unsupported
page link, and use policy `source_page_mismatch_deferred_nonblocking_v1`.
Batch governance may account it separately as:

`candidate_work_count = complete_work_count + terminal_work_count + deferred_nonblocking_work_count`

Generic retry, restart, resume, cache reuse, or unchanged provider metadata may
not reopen this state. Reopening requires separately governed materially stronger
exact-page/source evidence, a corrected filename/page identity, or an explicit
operator identity correction.

## Acquisition Safety

External execution normally requires rotated credentials, the non-secret
`VIOLET_CREDENTIAL_ROTATION_CONFIRMED=true` confirmation, a clean known-secret
fingerprint scan, and a redacted authentication preflight. PR #136 additionally
permits the explicit `operator_accepted_local_credential_risk_v1` waiver only
for the exact isolated ML1 test database, with production/Entity/truth writes
and downloads disabled. The waiver does not claim rotation occurred and does
not weaken the default future-provider gate. The deterministic owner sample
remains optional stage evidence and is not a runtime gate or a per-item human
dependency in normal ingestion. gallery-dl execution is metadata-only (`--dump-json --no-download`),
uses at least two-second request spacing, at most three bounded attempts per
work, stops after exhausted retryable/systemic failures, has no fallback
provider, and never downloads/imports media.

The canonical compatible-complete status policy is shared by import, audit,
runner, closure, tests, and contract. It includes `observed`, `active`,
`accepted`, and `metadata_complete`. Generic retry/failure transitions may only
update the exact attempted open queue rows; `metadata_complete`,
`terminal_remote_unavailable`, and `filename_identity_conflict` are preserved.
`deferred_nonblocking_source_page_mismatch` is also preserved by generic state
transitions and excluded from ordinary pending/retry acquisition.

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
