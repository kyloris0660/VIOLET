# Phase 4.3-A - Proper-Noun Signal Provenance Audit and Trust Policy

Generated: `2026-05-25T12:17:42.738026+00:00`

## Summary

This read-only audit inspects current local DB tag/entity metadata and defines the default trust policy for future entity candidate generation. It makes no candidate, assignment, entity, import, classification, AI-tagging, localization, Entity Resolver, similarity, storage, or source/iCloud writes.

Existing AI-generated character/copyright/artist/proper-noun tags are weak identity evidence by default. General/meta visual tags remain useful visual descriptors, but they are not identity signals.

## Audit Scope

- Source label filter: `all`
- Content-class filter: `all`
- Filtered media count: `1989`
- Media-tag rows inspected: `105699`
- DB access: read-only session; no `commit()` path in the audit runner.
- External calls: none.

## Existing Entity Foundation State

- `blombooru_entities`: `0`
- `blombooru_entity_aliases`: `0`
- `blombooru_entity_external_identities`: `0`
- `blombooru_entity_evidence`: `0`
- `blombooru_media_entity_candidates`: `0`
- `blombooru_media_entity_assignments`: `0`
- `blombooru_entity_translations`: `0`
- `blombooru_external_sources`: `0`
- `blombooru_provider_cache`: `0`
- `blombooru_negative_lookup_cache`: `0`
- `external_sources_enabled`: `0`
- `missing_entity_foundation_tables`: `10`

Note: entity foundation tables are absent in the audited local DB. The audit did not run migrations because Phase 4.3-A is read-only.

## Proper-Noun / Entity-Like Signal Counts

- Proper-noun/entity-like media-tag rows: `2131`
- Distinct proper-noun/entity-like tags: `287`
- Proper-noun tags sourced only from AI: `287`
- Proper-noun AI media-tag rows: `2131`
- Proper-noun suggestion rows: `325`
- Proper-noun rows on `non_anime` / `unknown` / unclassified media: `66`
- General/meta visual context rows: `103568`

## Tag Category Distribution

- `character`: `1622`
- `copyright`: `0`
- `artist`: `0`
- `general`: `102097`
- `meta`: `1980`

## Signal Kind Distribution

- `character`: `1622`
- `copyright`: `0`
- `artist`: `0`
- `uncategorized_proper_noun_like`: `509`
- `general_visual`: `101588`
- `meta_visual`: `1980`

## Provenance Distribution

- `manual`: `0`
- `imported`: `0`
- `ai`: `105699`
- `system`: `0`
- `unknown`: `0`

## Confidence Distribution

- `null`: `0`
- `lt_0.35`: `23954`
- `0.35_to_lt_0.65`: `31655`
- `0.65_to_lt_0.90`: `29556`
- `gte_0.90`: `20534`

## Proper-Noun Content-Class Distribution

- `anime`: `2065`
- `unknown`: `50`
- `non_anime`: `16`
- `illustration`: `0`
- `null_unclassified`: `0`

## Trusted Anchors

- `confirmed_entity_assignments`: `0`
- `manual_confirmed_entity_assignments`: `0`
- `manual_entity_aliases`: `0`
- `manual_confirmed_entity_translations`: `0`
- `verified_external_identities`: `0`
- `imported_or_external_entity_evidence_rows`: `0`
- `manual_or_locked_proper_noun_media_tag_rows`: `0`
- `imported_proper_noun_media_tag_rows`: `0`

## Trust-Tier Policy

- `T0` - Manual confirmed entity assignment / manual alias / manual translation: candidate_source=`True`, statistics_only=`False`, confirmed_assignment=`Only by explicit manual action.`
- `T1` - Trusted external exact metadata with provenance: candidate_source=`True`, statistics_only=`False`, confirmed_assignment=`Future policy only; not available in Phase 4.3-A.`
- `T2` - Imported/manual locked proper-noun metadata with provenance: candidate_source=`True`, statistics_only=`False`, confirmed_assignment=`No automatic confirmed assignment by default.`
- `T3` - AI confirmed proper-noun tag: candidate_source=`False`, statistics_only=`True`, confirmed_assignment=`Blocked by default.`
- `T4` - AI suggestion proper-noun tag: candidate_source=`False`, statistics_only=`True`, confirmed_assignment=`Blocked.`
- `T5` - General/meta visual co-occurrence: candidate_source=`False`, statistics_only=`False`, confirmed_assignment=`Blocked.`

## Candidate-Generation Simulation

- `T0_T1_T2_default_candidate_sources`: `0`
- `T3_ai_confirmed_if_included`: `1806`
- `T4_ai_suggestions_if_included`: `325`
- `T5_visual_context_blocked`: `103568`
- Per-media identity-like distribution: media_count=`1352`, max=`10`, p50=`1`, p90=`3`, p95=`3`

Recommended future Phase 4.3-B caps:
- `max_candidates_per_media`: `5`
- `max_total_candidates_per_run`: `500`
- `default_source_tiers`: `['T0', 'T1', 'T2']`
- `dry_run_first`: `True`
- `execute_confirmation_required`: `True`
- `block_t3_by_default_until_user_approval`: `True`
- `block_t4_by_default`: `True`
- `block_t5_as_identity_source`: `True`

## Risk Indicators

Top identity-like tags by media-tag row count, capped:
- `vision_(genshin_impact)` (general): rows=`97`, source_kinds=`ai`, ai_only=`True`, suggestions=`34`, locked=`0`
- `artist_name` (general): rows=`90`, source_kinds=`ai`, ai_only=`True`, suggestions=`66`, locked=`0`
- `ganyu_(genshin_impact)` (character): rows=`87`, source_kinds=`ai`, ai_only=`True`, suggestions=`0`, locked=`0`
- `star_(symbol)` (general): rows=`68`, source_kinds=`ai`, ai_only=`True`, suggestions=`20`, locked=`0`
- `lumine_(genshin_impact)` (character): rows=`64`, source_kinds=`ai`, ai_only=`True`, suggestions=`2`, locked=`0`
- `kisaki_(blue_archive)` (character): rows=`61`, source_kinds=`ai`, ai_only=`True`, suggestions=`0`, locked=`0`
- `nilou_(genshin_impact)` (character): rows=`61`, source_kinds=`ai`, ai_only=`True`, suggestions=`0`, locked=`0`
- `kamisato_ayaka` (character): rows=`57`, source_kinds=`ai`, ai_only=`True`, suggestions=`0`, locked=`0`
- `barbara_(genshin_impact)` (character): rows=`51`, source_kinds=`ai`, ai_only=`True`, suggestions=`1`, locked=`0`
- `shrug_(clothing)` (general): rows=`46`, source_kinds=`ai`, ai_only=`True`, suggestions=`17`, locked=`0`

Media with many proper-noun signals, capped:
- media_id=`1249`, content_class=`anime`, proper_noun_signal_rows=`10`
- media_id=`1241`, content_class=`anime`, proper_noun_signal_rows=`8`
- media_id=`1985`, content_class=`anime`, proper_noun_signal_rows=`7`
- media_id=`1392`, content_class=`anime`, proper_noun_signal_rows=`6`
- media_id=`1753`, content_class=`anime`, proper_noun_signal_rows=`6`
- media_id=`766`, content_class=`unknown`, proper_noun_signal_rows=`5`
- media_id=`767`, content_class=`anime`, proper_noun_signal_rows=`5`
- media_id=`912`, content_class=`anime`, proper_noun_signal_rows=`5`
- media_id=`1108`, content_class=`anime`, proper_noun_signal_rows=`5`
- media_id=`1120`, content_class=`anime`, proper_noun_signal_rows=`5`

## Recommendation

- Phase 4.3-B recommendation: `defer_guarded_candidate_generation_from_ai_t3_by_default`
- Default candidate tiers: `T0, T1, T2`
- T3 AI confirmed proper-noun tags: `weak_evidence_statistics_or_future_query_seed_only_by_default`
- T4 AI suggestions: `statistics_only`
- T5 general/meta visual tags: `context_only_not_identity`
- Proceed condition: Proceed with 4.3-B only for T0/T1/T2 dry-run candidate generation, or after explicit approval to include T3 with caps.

## Explicit Safety Confirmation

- `db_writes`: `False`
- `external_network_calls`: `False`
- `entity_candidate_creation`: `False`
- `entity_assignment_creation`: `False`
- `entity_creation`: `False`
- `db_import`: `False`
- `classification`: `False`
- `ai_tagging`: `False`
- `localization`: `False`
- `source_icloud_mutation`: `False`
- `app_managed_storage_mutation`: `False`
- `entity_resolver_execution`: `False`
- `similarity_or_clustering`: `False`
