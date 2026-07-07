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
