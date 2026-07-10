# SCV2-A1R: Route Audit Rerun After R1R

## Summary

- Final route status: `route_partially_approved_for_one_next_phase`.
- Recommended next phase: `SCV2-R2 targeted resolver / gap reduction`.
- Required next contract: `route_audit_contract_v1 plus focused SCV2-R2 resolver/gap contract`.
- R1R merge commit: `7224a61aeaea32cd86a07e8dfd6cf6a6d7fcc0ef`.
- A1R did not start R2, PX1-B, Provider-2, scale-up, Entity bridge, or SourceConcept truth promotion.

## R1R Evidence Intake

- Intake passed: `True`.
- R1R status: `target_met_full_chain`.
- LLM accounting: `{'eligible': 6429, 'selected': 6429, 'judged': 6429, 'all_eligible_pairs_adjudicated': True}`.
- Cache accounting: `{'exact_compatible_cache_hit_count': 6429, 'new_provider_call_count': 0, 'remaining_missing_pair_count': 0}`.

## Read-only Runtime Proof

- DB source: `restored_r1r_db`.
- DB label: `blombooru_r1r_restored_test_20260618`.
- transaction_read_only: `on`.
- transaction isolation: `repeatable read`.
- stable snapshot: `True`.
- mutation proof passed: `True`.

## SourceConcept State After R1R

- Concepts: `2767` total, `1064` active, `1703` needs_review, `0` superseded.
- Aliases/evidence/signal links/search index: `4400` / `8097` / `8097` / `4400`.
- Concepts without media: `558`; active weak evidence: `2`.

## LLM Decision Impact

- Counts: `{'same': 2072, 'cannot': 3815, 'uncertain': 542, 'total': 6429}`.
- Percentages: `{'same': 32.23, 'cannot': 59.34, 'uncertain': 8.43}`.
- Edge-type distribution: `{'llm_blocked_guard': 242, 'llm_needs_review': 542, 'llm_negative_guard': 3815, 'llm_same_concept': 1830}`.

## Gap Audit Rerun

- Total gap signals: `4443`.
- Old A1 comparison: `-179` total delta.
- Route-blocking buckets: `['cjk_alias_without_english_romaji_sibling', 'needs_review_cluster_with_no_active_alias_path', 'same_display_name_split_across_contexts', 'same_normalized_alias_key_split_across_multiple_concepts', 'source_tag_present_no_source_concept_alias']`.

## Search Seed Symmetry Audit Rerun

- Aggregate: `{'groups_tested': 10, 'seeds_tested': 58, 'matched_seeds': 42, 'unmatched_seeds': 16, 'symmetric_groups': 0, 'asymmetric_groups': 10, 'asymmetry_reason_buckets': {'concept_split': 33, 'needs_review_not_included_in_active_search': 9, 'unmatched_alias': 16, 'missing_alias_or_unmatched_seed': 10, 'active_only_vs_needs_review_contrast': 5}, 'unmatched_aliases_count_as_asymmetry': True, 'media_result_overlap_metrics': {'pairwise_jaccard_count': 32, 'average_pairwise_jaccard': 0.3752, 'min_pairwise_jaccard': 0.0}}`.
- Old A1 comparison delta: `{'groups_tested': 0, 'seeds_tested': 0, 'matched_seeds': 0, 'unmatched_seeds': 0, 'symmetric_groups': 0, 'asymmetric_groups': 0}`.

## PX1 / Pixiv / Source Metadata Coverage

- Eligible media: `3687`.
- Source metadata rows: `671`.
- Distinct eligible media with source metadata: `531` (`14.4`%).
- Strict PX1-influenced concepts: `1684`.
- All Pixiv-influenced concepts: `2189`.

## needs_review Triage

- Total needs_review: `1703`.
- With media / high evidence / sharing active aliases / no active alias path: `1463` / `94` / `1124` / `579`.

## Route Decision Matrix

| Candidate | Priority | Recommended | Allowed by A1R | Writes DB | Truth path | Production |
|---|---:|---:|---:|---:|---:|---:|
| `SCV2-R2 targeted resolver / gap reduction` | `P1` | `True` | `True` | `True` | `False` | `False` |
| `PX1-B additional Pixiv/source metadata extraction` | `P2` | `False` | `False` | `True` | `False` | `False` |
| `Provider-2-P0 taxonomy/alias enrichment metadata-only preparation` | `P2` | `False` | `False` | `False` | `False` | `False` |
| `SCV2-E2 controlled scale-up import` | `P3` | `False` | `False` | `True` | `False` | `True` |
| `SourceConcept management/editing UI/design` | `P2` | `False` | `False` | `True` | `False` | `False` |
| `Entity bridge preview` | `P3` | `False` | `False` | `True` | `True` | `False` |
| `DEDUP1 exact duplicate cleanup execution` | `P3` | `False` | `False` | `True` | `False` | `True` |
| `Full-library / 10k or 40k expansion` | `P3` | `False` | `False` | `True` | `False` | `True` |

## Final Route Recommendation

- Status: `route_partially_approved_for_one_next_phase`.
- Exactly one recommended next phase: `SCV2-R2 targeted resolver / gap reduction`.
- Still blocked routes: `['PX1-B additional Pixiv/source metadata extraction', 'Provider-2-P0 taxonomy/alias enrichment metadata-only preparation', 'SCV2-E2 controlled scale-up import', 'SourceConcept management/editing UI/design', 'Entity bridge preview', 'DEDUP1 exact duplicate cleanup execution', 'Full-library / 10k or 40k expansion']`.
- Production/truth-path work remains blocked.

## Validation

- Contract result: `{'contract_id': 'route_audit_contract_v1', 'passed': True, 'returncode': 0, 'result': {'contract_id': 'route_audit_contract_v1', 'details': {'executed_stages': [], 'executed_stages_normalized': [], 'forbidden_stages_present': [], 'missing_required_summary_fields': [], 'route_approved': False, 'route_decision_status': 'route_partially_approved_for_one_next_phase'}, 'error_count': 0, 'errors': [], 'full_chain_complete_claimed': False, 'passed': True, 'route_approved': False, 'safe_to_merge_claimed': False, 'status': 'route_partially_approved_for_one_next_phase', 'target_met_claimed': False, 'warning_count': 0, 'warnings': []}}`.
- Public redaction: `{'passed': True, 'findings': [], 'allowed_findings': [], 'scanned_artifacts': ['docs/reports/phase-4.5-scv2-a1r-route-audit-after-r1r.md', 'docs/reports/phase-4.5-scv2-a1r-route-audit-after-r1r-summary.json']}`.
- Review pack: `{'generated': True, 'manifest_present': True, 'checksums_present': True, 'checksum_count': 13, 'manifest_checksum_count': 13, 'redaction_passed': True, 'redaction_scan_covers_final_file_set': True, 'public_report_copy_current': True, 'zip_generated': True, 'not_committed': True, 'zip_path_label': 'a1r-private-review-pack', 'integrity_hash': '33b0edf63dd88a388ae08332a09e105fa10d39e00f6951e3d50c2d721f197290', 'integrity_passed': True}`.
- Browser/Electron validation: not required; no UI/runtime change.
