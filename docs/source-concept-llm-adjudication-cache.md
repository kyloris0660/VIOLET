# SourceConcept LLM Adjudication Cache

The SourceConcept full-chain resolver uses a durable local LLM adjudication
cache for pair judgments. This cache is part of the standard SourceConcept
pipeline, not a one-off R1R artifact.

## Policy

- Full-chain SourceConcept phases must run cache-first and checkpointed.
- Every successful pair judgment is written immediately to a durable ignored
  local cache using an atomic temp-file then rename/replace write.
- Failed runs preserve successful judgments. A rerun must reuse exact-compatible
  cached judgments before making provider calls.
- When every selected/eligible pair is exact-compatible cached, a rerun may
  regenerate target evidence without provider availability or new provider
  calls. Provider readiness is required only for cache-missing pairs.
- When any selected/eligible pair is missing from the exact-compatible cache,
  provider readiness applies only to those missing pairs. Missing pairs must be
  judged, budget-blocked, or otherwise explicitly accounted; they must not be
  hidden behind an old fixed call cap.
- Provider failure rows and malformed responses are written to a separate
  failure ledger. They are diagnostic only and do not count as valid judgments.
- Public reports may contain aggregate counts and labels only. Raw local paths,
  filenames, DB URLs, signal payloads, and secrets must stay private.
- The cache root is private and ignored, currently labeled
  `source-concept-llm-adjudication-cache`.

## Compatibility

There are two reuse levels:

- Exact-compatible cache hit: the canonical pair payload hash, resolver version,
  adjudication policy version, prompt template version, decision schema version,
  cache policy version, provider policy version, and model label are compatible.
  These hits count as valid cached judgments for full-chain proof.
- Semantic/prior judgment: the pair identity matches but prompt, schema, context,
  model policy, or payload hash changed. These records remain audit evidence but
  do not count as full-chain proof unless a future explicit compatibility rule
  approves them.

The cache key is order-independent for symmetric pair adjudication. It includes
the canonical ordered signal payload, normalized signal identity, context/work
compatibility payload hash, resolver version, prompt version, decision schema,
adjudication policy, cache policy, provider policy, and model label.

## Budget Behavior

SourceConcept LLM adjudication is budget-driven, not fixed-call-cap-driven.
Before provider calls, the runner must compute:

- eligible pair count;
- selected pair count;
- exact-compatible cache coverage;
- new provider calls required;
- projected new-call cost;
- budget cap.

If projected new-call cost is within the approved budget, the runner should
adjudicate all cache-missing eligible pairs. If it is over budget, it must stop
with `blocked_budget` and report the estimate instead of silently falling back
to a small fixed subset such as 300 pairs.

## Review Evidence

R1R and future full-library SourceConcept phases must report:

- compatible cache hits;
- imported previous judgments;
- new provider calls;
- provider failures;
- remaining missing pairs;
- cost spent this run;
- cost avoided by cache reuse;
- whether all eligible pairs are adjudicated or accounted.

Private review packs should include cache index, compatibility, hit/miss, and
failure-ledger summaries. Raw cache records remain local ignored artifacts.

## Autonomous R2R Passes

R2R retains the cache-first and atomic-write rules while replacing
`needs_review` queue semantics with autonomous dispositions. A successful
first-pass `must_link` or `cannot_link` is final subject to deterministic and
component guards. A first-pass uncertain result receives one richer second
pass over the same fixed evidence. A still-unresolved second pass becomes
`deferred_nonblocking`; it is retained for future automatic re-evaluation and
never requires human action.

First- and second-pass cache records use separate prompt/payload compatibility
versions. Each successful judgment is atomically persisted immediately.
Provider failures go only to the failure ledger, do not overwrite successful
records, and remain unaccounted until retry or an explicitly contracted
machine-defer policy. Final evidence regeneration must be cache-only.

SCV2-ML1 reuses all 3,319 accepted R2R dispositions. Its initial audit must not
initialize or call an LLM. Only genuinely new candidate pairs discovered by the
multilingual recall audit may enter a new exact manifest, cost projection, and
`blocked_llm_approval_required` gate. A Pixiv metadata gap is not an LLM gap.

SCV2-ML2 first resolves creator candidates deterministically from stable provider
creator IDs. Only evidence-insufficient ambiguous pairs may enter a new finite
LLM manifest under the existing primary-provider USD-10 policy. When that
manifest is empty, provider initialization, calls, retries, and spend must all
remain zero; deterministic stable-ID pairs are never sent for LLM adjudication.
