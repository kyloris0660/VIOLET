# SCV2-SV1-A：受控媒体/AI 规模与 accepted-source 重建验证

## 结论

本阶段状态为 `partial_sv1_media_ai_scale_and_stable_key_promotion_complete`：`target_met=false`、`safe_to_merge=true`、`route_approved=false`。SV1-A 完成了 12,000 媒体受控导入、本地 AI-tag provenance 全覆盖、stable-key rematerialization/rollback，以及 accepted-source evidence 的实际 R2R/ML2 重建验证；它没有完成新媒体的 Pixiv/provider metadata、localization、全库或生产流程。

## 规模、resume 与 AI provenance

- manifest / DB / import ledger / AI ledger：`{'passed': True, 'manifest_count': 12000, 'database_count': 12000, 'import_ledger_count': 12000, 'ai_ledger_count': 12000}`。
- 本次 resume 新导入/存储写入：`0` / `0`；累计媒体/存储对象：`12000` / `12000`。
- AI coverage=`1.0`，完整 ledger=`12000`，fingerprint=`830f39c850c9faa3eb436d9946989724b3bf67dd8bf00801a9a66731954694f6`；本轮未重新执行 8,580 条 inference。

## accepted media 与 Pixiv denominator

- accepted current 总数/可用/纳入/不可用/fingerprint 不兼容：`3750` / `3452` / `3452` / `298` / `0`。
- All accepted current media that remained available and fingerprint-compatible were included.
- 独立 filename/path canonical Pixiv candidates=`6496`；accepted metadata 已支持=`2372`；SV1-A 未获取=`4124`；明确 non-candidate=`5504`；conflicts=`0`。

## actual evidence rebuild 与图安全

- rebuild DB：`blombooru_scv2_sv1_rebuild_verification_test_20260718`；派生行导入=`0`；actual R2R/ML2 replay=`True`。
- accepted creator family traceability=`1.0`；accepted R2R disposition compatibility=`1.0`。
- scale/promotion/rebuild 的 direct/transitive cannot、deferred union、multi-stable-ID、cross-role、unknown-role、duplicate stable identity 均为 0；component counts 分别为 `1677` / `1677` / `1681`。重建差异来自只重放可比较 accepted evidence、298 个 target-missing references 与 numeric-ID-independent regeneration，不声称 numeric ID 相等。

## true new-media search

- 新媒体 population=`8548`，确定性 cases=`40`，selection fingerprint=`57be69359419edaf65ebd021b4bc276db6803977c461b1f648ef35b8ce4a0166`。
- accepted baseline 缺席=`40`；scale/promotion/rebuild unsupported=`0` / `0` / `0`；leakage=`0`。

## 边界与下一步

provider、Pixiv、gallery-dl、external LLM、localization、production、Entity/assignment、source mutation 均为 0。唯一 canonical final review pack fingerprint：`eb018945dce8d646e87cf9236966d803351d1e2f90f302e16942c052d2befdf9`。建议下一阶段为 `SCV2-SV1B: Controlled Pixiv Metadata, Localization, and Source-Graph Closure`；本阶段未批准也未启动 SV1B，未启动 FL1。
