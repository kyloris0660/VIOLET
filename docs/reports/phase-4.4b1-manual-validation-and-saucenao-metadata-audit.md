# Phase 4.4-B1V - SauceNAO Manual Validation and Metadata Extraction Audit

Date: 2026-05-27T13:34:28+00:00

## Summary

- Base live rerun: PR #77, Phase 4.4-B1 SauceNAO live rerun.
- Provider: `saucenao`.
- Approved sample media IDs: `2690`, `2687`, `2670`, `2654`, `2647`.
- User manual validation: high-confidence `2/2` correct; low-confidence useful `0/3`.
- Metadata audit result: character metadata was found in SauceNAO API data for both re-queried high-confidence Danbooru results.
- DB writes: `0`; confirmed assignments: `0`; provider persistence: `0`.

## PR #77 Live Rerun Context

- Live requests attempted: `5`; skipped: `0`.
- Derived/resized/stripped images uploaded: `5`; original uploads: `0`.
- Result classes: `high_confidence_match=2`, `low_confidence_match=3`, `no_match=0`.
- Quota did not block the run: no short-window exhaustion, no daily exhaustion, no out-of-searches condition.
- No DB writes, no ProviderCache, no NegativeLookupCache, no EntityEvidence, no MediaEntityCandidate, and no MediaEntityAssignment writes occurred.

## Local Artifact Availability

- Public live rerun report and summary were present.
- Local live rerun details were present under ignored `.local_manifests`.
- Local manual validation sheet was present under ignored `.local_manifests`.
- Five B1 derived-image artifacts were present under ignored `.local_manifests`.
- Local artifacts are not committed by this stage.

## User Manual Validation

| media_id | SauceNAO class | score | minimum_similarity | user judgment | metadata useful | recommended action | notes |
| ---: | --- | ---: | ---: | --- | --- | --- | --- |
| 2690 | low_confidence_match | 34.63 | 35.63 | invalid_completely_unrelated | no | discard | Low-confidence match, unrelated. |
| 2687 | high_confidence_match | 96.2 | 52.0 | correct | yes | keep_as_strong_evidence | Source is correct; author and series/work are useful. Character metadata needed an extraction audit. |
| 2670 | high_confidence_match | 91.96 | 37.66 | correct | yes | keep_as_strong_evidence | Source is correct; author and series/work are useful. Character metadata needed an extraction audit. |
| 2654 | low_confidence_match | 52.3 | 52.0 | wrong_unrelated | no | discard | Low-confidence match, unrelated. |
| 2647 | low_confidence_match | 50.7 | 51.7 | wrong_unrelated | no | discard | Low-confidence match, unrelated. |

Validation implications:

- High-confidence matches were accurate in this tiny pilot: `2/2` validated correct (`2687`, `2670`).
- Low-confidence matches were not useful in this tiny pilot: `0/3` useful (`2690`, `2654`, `2647` discarded).
- SauceNAO is viable for exact or near-exact source discovery evidence, not for low-confidence heuristic character recognition.
- Low-confidence SauceNAO results should be discarded by default unless a later policy explicitly approves manual salvage.

## Metadata Extraction Audit

Important correction: the PR #77 public Markdown report did not show character names, but that omission does not prove SauceNAO API character metadata is unavailable. The original local details artifact preserved only a redacted normalized top-result summary and did not include raw SauceNAO `data` fields.

Because local details were insufficient, B1V performed a metadata-preservation re-query for only the two manually validated high-confidence media IDs: `2687` and `2670`. The discarded low-confidence IDs were not re-queried.

Metadata-preservation re-query gates:

- `.env` was gitignored and `SAUCENAO_API_KEY` presence was verified without printing the key.
- Fresh no-active-server preflight was clean: listener backend `windows_netstat`, occupied ports `0`, confirmed V.I.O.L.E.T. servers `0`, suspected V.I.O.L.E.T. servers `0`.
- Existing B1 derived images were reused from ignored local artifacts.
- Requests attempted in B1V: `2`, only for `2687` and `2670`.
- Discarded low-confidence samples were not re-queried.
- No DB writes occurred.

| media_id | result class | score | source/post | artist | work/copyright | character | general tags | extraction status |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| 2690 | low_confidence_match | 34.63 | present | present | present | unknown | unknown | parser_missing_discarded_low_confidence_not_requeried |
| 2687 | high_confidence_match | 96.2 | present | present | present | present | absent | requery_performed |
| 2670 | high_confidence_match | 91.96 | present | present | present | present | absent | requery_performed |
| 2654 | low_confidence_match | 52.3 | present | present | present | unknown | unknown | parser_missing_discarded_low_confidence_not_requeried |
| 2647 | low_confidence_match | 50.7 | absent | absent | present | unknown | unknown | parser_missing_discarded_low_confidence_not_requeried |

High-confidence re-query details:

- media `2687`: `result_id=7695035`, artist `yunkaiming`, work/material `honkai: star rail, honkai (series)`, characters `acheron (honkai: star rail)`, SauceNAO header.status `0`.
- media `2670`: `result_id=9366672`, artist `songchuan li`, work/material `blue archive`, characters `kisaki (blue archive)`, SauceNAO header.status `0`.

Conclusion: B1 had a parser/report preservation gap. SauceNAO API data for the validated Danbooru matches includes `characters`, `material`, `creator`, source URL fields, and external IDs; these should be preserved in a future evidence/cache design.

## SauceNAO Acceptance Policy

- Strong evidence candidate: manually validated high-confidence exact or near-exact source match with source/post availability, e.g. Danbooru `result_id` plus source-backed metadata.
- Low-confidence default: discard or negative evidence; do not create candidates from low-confidence matches by default.
- `minimum_similarity` alone is not an acceptance threshold; it must be combined with actual similarity, provider index/source consistency, and result metadata shape.
- Similarity around `>=90` with source/post consistency is promising for strong evidence, but this pilot is too small to define a production threshold.
- No automatic confirmed `MediaEntityAssignment`.
- No automatic trusted `Entity` creation.
- Character metadata is source-backed candidate metadata only; it is not final truth and must not create automatic character assignment.

## Localization Integration Policy

- Preserve external provider metadata in raw/canonical provider form first.
- Do not translate in the SauceNAO runner.
- Do not create a separate localization module for provider metadata.
- External artist/work/character/general tags should later feed the existing localization, tag translation, and entity translation pipeline.
- Proper nouns need alias/original-name support, overrideable translations, and preserved provenance.
- Localization of provider metadata is deferred until after evidence persistence design.

## B2 Expansion Plan

- Proposed B2 size: `20-30` anime-only approved samples, or smaller if quota/manual validation burden suggests.
- User approval is required for exact B2 sample IDs; do not auto-select replacements or scan the full library.
- Use one provider only (`saucenao`) with a quota-aware sequential scheduler and redacted per-item state.
- Persist no DB records during B2 unless a separate persistence design is approved first.
- Only high-confidence results should become later persistence candidates; low-confidence results should be discarded or recorded as negative evidence after policy approval.
- Subscription is not recommended solely for quality. It may be useful later for quota/throughput if B2 confirms high-confidence usefulness and quota becomes the bottleneck.
- Phase 3.9 provider/source run ledger is required before any `100+`, repeated, broad, or full-library provider run.

Read-only candidate selection command for a later planning conversation, not executed here:

```sql
SELECT id, content_class, created_at
FROM blombooru_media
WHERE content_class = 'anime'
  AND id NOT IN (2690, 2687, 2670, 2654, 2647)
ORDER BY random()
LIMIT 30;
```

## Subscription Judgment

- Not recommended yet solely for quality.
- Do not assume subscription improves match quality.
- If B2 shows high-confidence matches remain useful and quota blocks throughput, subscription can be reconsidered as a quota/throughput solution.
- No purchase or subscription was performed.

## Recommended Next Phase

- If the priority is durable source-backed records: Phase 4.4-C should design evidence/cache/candidate persistence for high-confidence validated results, including preservation of `characters`, `material`, `creator`, source/post IDs, provenance, and redaction policy.
- If the priority is quality confidence before persistence: Phase 4.4-B2 should run a larger approved-sample pilot first.
- If the priority is repeated or broad provider execution: Phase 3.9 must come first.

## Safety Confirmation

- DB writes: `0`.
- Provider calls in B1V: exactly `2`, limited to metadata-preservation re-query for manually validated high-confidence media IDs `2687` and `2670`.
- No broad sample expansion.
- No original upload.
- No unknown/non_anime/unapproved illustration upload.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No ProviderCache, NegativeLookupCache, EntityEvidence, MediaEntityCandidate, MediaEntityAssignment, Entity, media_tags, or TagTranslation writes.
- No DB import, classification, AI tagging, localization execution, Entity Resolver, or similarity/clustering.
- API key, local paths, original filenames, raw image bytes, and unredacted raw provider payloads are excluded from public reports.
