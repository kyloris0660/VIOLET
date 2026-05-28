# Phase 4.4-C0 - Provider-neutral Evidence Contract

Date: 2026-05-27

## Summary

Phase 4.4-C0 adds a provider-neutral, non-mutating reverse-search evidence contract and a SauceNAO mapper foundation. It uses PR #77/#78 facts only: no provider call, no upload, no DB write, and no rerun occurred in this stage.

C0 exists because SauceNAO is only the first provider. Future source discovery, enrichment, evidence persistence, candidate generation, localization, multi-provider conflict handling, and human correction need a shared evidence/candidate contract instead of a SauceNAO-specific persistence path.

## PR #77 / PR #78 Context

- PR #77 merged the SauceNAO live rerun: `5` live requests attempted, `5` provider responses returned, `2` high-confidence matches, `3` low-confidence matches, `0` DB writes, `0` confirmed assignments, derived/resized/stripped uploads only.
- PR #78 merged the manual validation and metadata extraction audit.
- Manual validation accepted high-confidence matches `2687` and `2670` as correct source matches.
- Manual validation discarded low-confidence matches `2690`, `2654`, and `2647` as unrelated.
- Metadata audit corrected the earlier wrong assumption: SauceNAO API/local metadata can include source-backed character/work/artist fields for high-confidence Danbooru matches.

## Contract Definitions

The internal contract module is `backend/app/services/provider_evidence_contract.py`.

- `ProviderQuery`: provider key/category, media ID, input kind, query hash, query hash status, redacted request shape, request shape status, live-request flag, uploaded input kind, provider policy version, query type.
- `ProviderRunOutcome`: provider key, status, attempted/succeeded/failed request counts, quota observations, stop reason, run timestamp.
- `SourceMatch`: provider result ID, provider index label, source host, source/post URL, source identifier status, rank, provider-native score, score kind, provider minimum similarity, normalized match class, normalized evidence strength, manual validation status, acceptance policy version.
- `ExtractedProviderMetadata`: raw provider artist/work/copyright/character/general tags, source title, provider metadata language/tag style, localization status, raw metadata availability, parser status.
- `EvidencePersistencePlan`: query + source match + metadata plus ProviderCache persistence gating, planned EntityEvidence, MediaEntityCandidate, NegativeLookupCache behavior, blocked reasons, with `confirmed_assignment_allowed=false`, `entity_auto_create_allowed=false`, `localization_pending=true`, and `db_write_allowed=false` in C0.

Public serialization rejects secret-like key names by normalized pattern, including `saucenao_api_key`, `apiKey`, `api-key`, `password`, `token`, `access_token`, `secret`, `authorization`, `bearer`, and `credential`. It also rejects local paths, original filenames, raw image bytes, and provider-returned filename fields.

## SauceNAO Mapper Behavior

The SauceNAO mapper is `backend/app/services/saucenao_evidence_mapper.py`.

| media_id | PR #78 validation | C0 match_class | evidence_strength | positive entity candidates | negative/discard plan |
| ---: | --- | --- | --- | --- | --- |
| 2687 | correct high-confidence source match | `exact_or_near_exact` | `strong` | artist/work/character suggestions planned | no |
| 2670 | correct high-confidence source match | `exact_or_near_exact` | `strong` | artist/work/character suggestions planned | no |
| 2690 | unrelated low-confidence result | `discarded` | `discard` | no | yes |
| 2654 | unrelated low-confidence result | `discarded` | `discard` | no | yes |
| 2647 | unrelated low-confidence result | `discarded` | `discard` | no | yes |

Acceptance policy:

- `minimum_similarity` alone does not create acceptance.
- High provider score alone does not create acceptance.
- In this pilot, strong evidence requires a high-confidence result plus manual validation and a concrete source identifier such as result/post ID, source/post URL, or provider external ID.
- Low-confidence SauceNAO results are discarded by default unless a future explicit policy approves manual salvage.
- No confirmed `MediaEntityAssignment` and no automatic trusted `Entity` creation are allowed.
- Missing `query_hash` or missing `request_shape_redacted` blocks ProviderCache persistence planning; the mapper reports `query_hash_status=missing`, `request_shape_status=missing`, `provider_cache_persistence_allowed=false`, and a `persistence_blocked_reason`.
- Missing source identifiers block strong evidence and positive entity evidence planning even when the row is high-confidence and manually marked correct.

## High-confidence Mapping Plan

### Media 2687

- Source match: Danbooru-backed exact/near-exact evidence, result ID `7695035`, score `96.2`, minimum similarity `52.0`.
- Source/post: Danbooru host and derived post URL can be represented in the neutral `SourceMatch`.
- Raw provider metadata:
  - artist: `yunkaiming`
  - work/copyright: `honkai: star rail`, `honkai (series)`
  - character: `acheron (honkai: star rail)`
- C1 plan: redacted `ProviderCache`, `EntityEvidence` reverse-search row, nullable-entity `MediaEntityCandidate` suggestions for artist/work/character, only when local details/raw provider artifacts provide real `query_hash`, `request_shape_redacted`, and source identifiers.
- Still blocked: confirmed assignment, automatic Entity creation, media tag mutation, localization execution.

### Media 2670

- Source match: Danbooru-backed exact/near-exact evidence, result ID `9366672`, score `91.96`, minimum similarity `37.66`.
- Source/post: Danbooru host and derived post URL can be represented in the neutral `SourceMatch`.
- Raw provider metadata:
  - artist: `songchuan li`
  - work/copyright: `blue archive`
  - character: `kisaki (blue archive)`
- C1 plan: redacted `ProviderCache`, `EntityEvidence` reverse-search row, nullable-entity `MediaEntityCandidate` suggestions for artist/work/character, only when local details/raw provider artifacts provide real `query_hash`, `request_shape_redacted`, and source identifiers.
- Still blocked: confirmed assignment, automatic Entity creation, media tag mutation, localization execution.

## Low-confidence Discard Plan

- `2690`, `2654`, and `2647` map to `match_class=discarded` and `evidence_strength=discard`.
- They should not create positive `EntityEvidence` or positive `MediaEntityCandidate` rows.
- A later C1 persistence pass may store redacted `ProviderCache` records and `NegativeLookupCache` discard/negative records if negative persistence is explicitly approved and real query metadata is available.
- They must not be treated as character/work recognition or as candidate sources.

## Metadata Preservation

The neutral contract preserves:

- artist raw values
- work/copyright raw values
- character raw values
- general tag raw values when present
- provider result ID and source/post host
- provider-native score and score kind
- provider minimum similarity as provider-specific context
- parser status and raw metadata availability

The C0 mapper intentionally does not translate raw provider metadata and does not create a provider-specific localization path.

## Localization Pending Policy

- External provider metadata is stored first in raw/canonical provider form.
- Translation does not happen inside the SauceNAO mapper.
- No separate localization module is introduced.
- Artist/work/character/general provider metadata should later feed the existing localization, tag translation, and entity translation paths.
- Proper nouns need alias/original-name support, overrideable translations, and preserved provenance.
- `localization_status=pending` is set for extracted high-confidence metadata.

## Schema Fit Audit

Audit module: `backend/app/services/provider_evidence_schema_fit.py`.

Audit script: `scripts/audit_phase44c0_provider_evidence_contract_fit.py`.

Overall status: `sufficient_with_json_payload`.

| Table / model | C1 fit |
| --- | --- |
| `ProviderCache` | Can store redacted `ProviderQuery`, `SourceMatch`, `ExtractedProviderMetadata`, run outcome, and provider fields as JSON. |
| `NegativeLookupCache` | Can store low-confidence wrong/discarded outcomes by provider/query hash. |
| `EntityEvidence` | Can store reverse-search evidence for validated high-confidence matches, pointing `payload_ref` to ProviderCache. |
| `MediaEntityCandidate` | Can create suggestion-only rows with `entity_id=NULL`, `generator=external`, `status=suggested`, and evidence provenance. |
| `MediaEntityAssignment` | Out of scope; confirmed assignments remain blocked. |
| `Entity` | Out of scope; automatic trusted creation remains blocked. |
| `ExternalIdentity` | Defer unless linking to an already approved Entity is separately designed. |
| `EntityAlias` / `EntityTranslation` / `TagTranslation` | Localization/alias work remains pending and should use existing overrideable pipelines later. |

Schema gaps that should stay follow-up design:

- first-class queryable `match_class`
- first-class queryable manual validation status
- first-class queryable localization state for raw provider metadata
- provider-neutral multi-provider conflict/merge table if broad querying becomes necessary

No migration is required for the narrow C1 persistence plan if JSON payloads are acceptable.

## Proposed C1 Persistence Scope

Recommended next phase: Phase 4.4-C1 validated high-confidence evidence persistence.

C1 should persist only reviewed high-confidence sample evidence from local details/raw provider artifacts that preserve real query hashes, redacted request shapes, and source identifiers. Reduced public summaries are acceptable for reporting but are not sufficient to prove ProviderCache persistence readiness.

1. `ProviderCache` redacted provider-neutral payloads for `2687` and `2670` only when `query_hash_status=present`, `request_shape_status=present`, and `provider_cache_persistence_allowed=true`.
2. `EntityEvidence` reverse-search rows for `2687` and `2670`.
3. `MediaEntityCandidate` suggestions with `entity_id=NULL` for artist/work/character metadata from `2687` and `2670`.
4. Optional `NegativeLookupCache` discard records for `2690`, `2654`, and `2647` if C1 explicitly includes negative policy.

C1 must still block confirmed assignments, automatic Entity creation, `ExternalIdentity` rows that require new trusted entities, `media_tags`, `TagTranslation`, localization execution, provider reruns, and image uploads.

## Multi-provider Roadmap

Every future provider must map into the same contract:

- `ProviderQuery`
- `ProviderRunOutcome`
- `SourceMatch`
- `ExtractedProviderMetadata`
- `EvidencePersistencePlan`

Second-provider requirements:

- No provider-specific DB write path.
- Provider-native scores are not directly comparable.
- Each provider needs its own `provider_policy_version` and `acceptance_policy_version`.
- Generic downstream logic should use normalized `match_class` and `evidence_strength`.
- Agreement across providers may strengthen evidence.
- Disagreement becomes `conflict` / `needs_review`, not automatic truth.
- D0 may scout a second provider only against this contract; C0 does not implement or run a second provider.

## Updated Roadmap

- C0 now: provider-neutral contract, SauceNAO mapper, schema-fit audit, docs/tests, no mutation.
- C1 next: validated high-confidence evidence persistence for `2687` and `2670`, still no confirmed assignments or Entity auto-create.
- D0: second-provider scouting against the same contract.
- B2 or D1: sample expansion after C0/C1 decision, still explicit sample approval only.
- Phase 3.9 before repeated, broad, `100+`, 5k/10k, large-cache, or full-library provider runs.

## Validation

- `py_compile` passed for the new Python modules, script, and focused test file.
- Focused unit tests passed: `tests/test_phase44c0_provider_evidence_contract.py` (`21 passed`).
- Schema-fit audit script printed JSON without DB access.

## Safety Confirmation

- No provider call.
- No upload.
- No DB write.
- No DB migration.
- No ProviderCache write.
- No NegativeLookupCache write.
- No EntityEvidence write.
- No MediaEntityCandidate write.
- No MediaEntityAssignment write.
- No automatic Entity creation.
- No confirmed assignment.
- No media_tags mutation.
- No TagTranslation mutation.
- No localization execution.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No Entity Resolver.
- No similarity/clustering.
