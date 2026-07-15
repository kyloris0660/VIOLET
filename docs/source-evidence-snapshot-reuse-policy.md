# Source Evidence Snapshot Reuse Policy

## Purpose

Source acquisition and graph resolution are separate lifecycle stages.
Changing aggregation, resolver, candidate-generation, or search logic does not
by itself authorize reacquiring provider data or rewriting observations.

## Durable Evidence Rule

- Provider/source observations are durable facts once acquired with provenance.
- A resolver rerun starts cache-first and input-snapshot-first.
- `gallery-dl`, Pixiv, or another provider is not called again merely because
  SourceConcept aggregation logic changed.
- Raw source observations are not edited, deleted, or normalized in place to
  improve resolver metrics. Corrections require an explicit evidence lifecycle
  with provenance rather than retrospective metric tuning.

## Rebuild Boundary

- Source metadata records, evidence, tag/name observations and registries,
  candidate inputs, media/tag inputs, and provider cache rows are fixed upstream
  evidence for a resolver replay unless the approved phase says otherwise.
- A fixed-input manifest must record table coverage, row counts, schema columns,
  and content fingerprints. Target completion fails if the fixed evidence
  differs before/after.
- SourceConcept-derived concepts, signals, links, aliases, evidence projections,
  search index rows, and resolution-run rows are rebuildable outputs in an
  isolated dev/test database.

## LLM Judgment Reuse

Existing pair judgments are reusable only under explicit compatibility levels:

1. exact-compatible cache reuse;
2. stable pair-identity reuse under a documented compatibility rule;
3. semantic prior for evaluation only;
4. invalidated judgment with a recorded reason;
5. genuinely new/incompatible pair requiring separate approval.

New pairs must not trigger automatic provider calls. Report their count,
projected cost, incompatibility reason, and approval state first.

For SCV2-ML1, the accepted R2R source and working databases are immutable input.
The accepted `3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking`
dispositions are reused and must not be repeated. Canonical Pixiv filename
candidate accounting is recomputed from current rows; already complete metadata
is never reacquired. Missing/retryable Pixiv work IDs and genuinely new alias
pairs produce separate acquisition and LLM approval manifests.

For SCV2-ML2, both the accepted R2R database and accepted ML1 database are
immutable inputs. ML2 clones the ML1 database into a fresh isolated dev/test
database, verifies fixed and forbidden table fingerprints, and writes only
allowlisted source-name observation and SourceConcept-owned output tables.
Metadata acquisition, accepted R2R disposition mutation, and production/source
mutation are separate approvals and are not implied by creator identity closure.

Reusable Pixiv evidence is promoted by stable provider/work/page identity and
content fingerprint, never development row IDs. Raw/normalized provider facts,
creator ID/name/account/profile identity, titles, source tags, parser version,
provenance, and terminal evidence may be reused when compatible. SourceConcept
IDs/components, candidates, signal links, clusters, aliases, search/fallback
indexes, graph metrics, confidence aggregates, and benchmarks must be recomputed
from the production snapshot.

## Full-Library Rule

Full-library work must separate evidence acquisition from graph recomputation in
its contract, run ledger, write allowlist, and reports. Provider collection,
resolver rebuild, truth promotion, and production mutation are independent
approval boundaries; success in one does not authorize another.
