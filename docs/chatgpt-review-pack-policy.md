# ChatGPT Review Pack Policy

This policy defines when V.I.O.L.E.T. phases must produce a privacy-safe,
uploadable review pack for independent ChatGPT audit.

The pack is a local ignored artifact. It may include redacted sample-level
evidence, aggregate JSON, public report copies, a manifest, checksums, and a
redaction report. It must not include originals, thumbnails, local paths,
source roots, filenames, provider credentials, cookies, tokens, API keys, raw
provider stdout/stderr, or unredacted private source labels.

## Review Pack Required

A ChatGPT review pack is required for:

1. route-decision phases, including SCV2-A1, future post-expansion audits, and
   phases deciding between R2, PX1-B, Provider-2, scale-up, SourceConcept
   editing, Entity bridge, or another route.
2. Large-data audit phases where aggregate report metrics may hide
   sample-level issues, including SourceConcept coverage audits, search
   symmetry audits, needs_review triage audits, alias fragmentation audits, and
   source metadata coverage audits.
3. Any phase that recommends or unlocks a higher-risk next step, including
   Entity bridge preview, confirmed assignment design, 5k/10k/full-library
   scale-up, new provider integration, broad provider extraction, broad import
   expansion, or source metadata truth-path/review-promotion work.
4. Any phase whose final approval depends on concrete examples rather than
   aggregate counts, including search seed asymmetry, overmerge/undermerge
   candidates, ambiguous SourceConcept samples, source tag/name unresolved
   samples, or route-decision evidence samples.
5. Any user-requested independent audit milestone.

## Review Pack Recommended

A ChatGPT review pack is recommended, but may be waived by the user/ChatGPT,
for:

- bounded DB-writing source-layer phases with nontrivial sample-level outcomes;
- provider metadata extraction phases where examples help validate correctness;
- UI/browser validation milestones where screenshots, logs, or sample data
  improve judgment;
- import/scale phases where sampled media or item ledgers determine quality.

## Review Pack Not Normally Required

A ChatGPT review pack is not normally required for:

- small bugfix PRs;
- docs-only cleanup with no route decision;
- tests-only changes;
- narrow safety patches that do not produce data or report decisions;
- local formatting or mechanical refactors.

The user/ChatGPT may still require a pack for any phase.

## Decision Rule

Public reports and summary JSON for review-pack-required phases must mark route
recommendations as:

`provisional_pending_chatgpt_pack_audit`

until the user uploads the generated review pack to ChatGPT and ChatGPT
completes independent audit. A runner may recommend a next phase, but final
route approval remains blocked by the pack audit unless the user explicitly
waives that requirement.

If a pipeline fidelity, provenance, privacy, or mutation-safety incident is
open, the incident gate is stronger than the normal pack-audit gate. Public
reports and summary JSON must use an explicit blocked status such as:

`blocked_pending_pipeline_fidelity_remediation`

until the required remediation and rerun evidence exist. Uploading a review
pack does not approve the next route while the incident gate is active.

## Minimum Pack Shape

Required packs should include:

- `manifest.json`
- `checksums.json`
- `README_FOR_CHATGPT_REVIEW.md`
- `public-report-copy/`
- `audit-data/`
- `review-samples/`
- `redaction/`

The redaction scan must cover every file in the pack before the zip is treated
as successful.
