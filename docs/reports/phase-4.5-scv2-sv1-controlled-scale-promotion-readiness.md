# SCV2-SV1-A：最终 GOV-3 安全闭环

## 结论

当前状态为 `partial_sv1_media_ai_scale_and_stable_key_promotion_complete`；`target_met=false`、`safe_to_merge=true`、`route_approved=false`。active blockers=`[]`。本阶段没有启动 SV1B、FL1、provider、localization、Entity、similarity 或生产路线。

## 数据库与 denominator membership

- scale / promotion / rebuild DB：`blombooru_scv2_sv1_controlled_scale_test_20260718` / `blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1` / `blombooru_scv2_sv1_rebuild_verification_test_20260718`；三者均为严格 test identity、两两不同且不属于 accepted predecessor DB。
- manifest / selected scale DB content keys：`12000 / 12000`；missing=`0`，extra=`0`，duplicate manifest=`0`，exact equality=`True`。
- corrected filename/path candidate denominator=`6496`；accepted metadata support=`2372`；unacquired=`4124`；explicit non-candidate=`5504`；conflicts=`0`。

## Stable-key evidence per-table reconciliation

方程顺序为 `exported = inserted + compatible_existing + deferred_target_missing + rejected_incompatible + blocking_failed`。本次为只读 re-audit，因此 inserted 均为 0：

- `source_concept_aliases`: `8124 = 0 + 8124 + 0 + 0 + 0`; target-missing references=`0`.
- `source_concept_evidence`: `12027 = 0 + 12027 + 0 + 0 + 0`; target-missing references=`229`.
- `source_concept_fallback_search_index`: `6596 = 0 + 6336 + 260 + 0 + 0`; target-missing references=`260`.
- `source_concept_resolution_runs`: `4 = 0 + 4 + 0 + 0 + 0`; target-missing references=`0`.
- `source_concept_search_index`: `8124 = 0 + 8124 + 0 + 0 + 0`; target-missing references=`0`.
- `source_concept_signal_links`: `17150 = 0 + 17150 + 0 + 0 + 0`; target-missing references=`0`.
- `source_concept_signals`: `14068 = 0 + 14068 + 0 + 0 + 0`; target-missing references=`228`.
- `source_concepts`: `6007 = 0 + 6007 + 0 + 0 + 0`; target-missing references=`0`.
- `source_metadata_evidence`: `4414 = 0 + 4414 + 0 + 0 + 0`; target-missing references=`0`.
- `source_metadata_records`: `4421 = 0 + 4421 + 0 + 0 + 0`; target-missing references=`298`.
- `source_name_observations`: `7388 = 0 + 7388 + 0 + 0 + 0`; target-missing references=`3`.
- `source_name_registry`: `371 = 0 + 371 + 0 + 0 + 0`; target-missing references=`0`.
- `source_searchable_name_assertions`: `1218 = 0 + 1218 + 0 + 0 + 0`; target-missing references=`0`.
- `source_tag_observations`: `18112 = 0 + 18112 + 0 + 0 + 0`; target-missing references=`10`.
- `source_tag_registry`: `418 = 0 + 418 + 0 + 0 + 0`; target-missing references=`0`.

- fallback exported / materialized / target-missing：`6596` / `6336` / `260`。
- exact stable-key membership=`True`，unexplained=`0`，extra materialized=`0`，current re-audit writes=`0`。
- 实际导入路径在单一事务内完成行导入、per-table accounting、兼容性检查、target-missing 分类、unexplained-loss 与 blocking decision；成功 ledger 仅在 commit 后写入，失败路径验证 rollback fingerprint restoration。

## 三库 graph safety

- scale: DB=`blombooru_scv2_sv1_controlled_scale_test_20260718`, components=`1677`, largest=`88`, all hard violation counts=`0`, giant recurrence=`False`.
- promotion: DB=`blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1`, components=`1677`, largest=`88`, all hard violation counts=`0`, giant recurrence=`False`.
- rebuild: DB=`blombooru_scv2_sv1_rebuild_verification_test_20260718`, components=`1681`, largest=`88`, all hard violation counts=`0`, giant recurrence=`False`.

三库均使用 `active_bipartite_connected_components_v2`，component/pair membership fingerprints 已记录；multi-stable-ID、direct/transitive cannot-link、deferred union、cross-role、unknown-role、duplicate active identity 均为 0。

## Validation、immutable 与 portability debt

- Current-head validation 的 HEAD、changed-file、Python identity 与 ledger fingerprint 均由私有 ledger 验证；py_compile、focused、documentation 与 full non-E2E 均通过。
- Immutable proof=`True`；accepted files、storage membership、scale/promotion protected tables 与 predecessor DB 均未漂移。
- 当前验证环境为 repository-local Windows venv / Python `3.12.0`。`SV1-PORTABILITY-01`（symlinked `venv/bin/python` 与 `.venv`）和 `SV1-PORTABILITY-02`（supported patch-version policy）为明确 nonblocking debt；它们不改变当前数据、写安全、graph safety 或结论，但必须在跨平台或 production rehearsal 前关闭。

## 边界

媒体导入、原始 AI inference、provider、Pixiv、gallery-dl、external LLM、localization、Entity、similarity、production、source/iCloud mutation 均为 0。本阶段不批准也不启动 SV1B 或 FL1。
