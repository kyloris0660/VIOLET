# SCV2-SV1：受控规模重放与 Promotion-Readiness 验证

## 结论

本阶段达到 `target_met_controlled_scale_promotion_readiness`。该结论仅覆盖隔离测试环境中的真实 10k–15k 受控规模重放、stable-key evidence promotion、回滚、幂等性、图安全与搜索基准；不声称语义完备、全库、生产、provider 或 Entity 就绪。`route_approved=false`。

## 数据规模与导入

- 只读 inventory：20702 项；安全可用真实媒体：20160 项；inventory fingerprint：`e5c97d8d07762073c942e31a5d978b03b9cc30ea4279c76b97cfd0bf5e6446e7`。
- 确定性 manifest：12000 项；manifest fingerprint：`5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f`；accepted current media 全部纳入。
- 导入结果：imported=12000，blocking_failed=0，out_of_manifest=0，source_mutation=0。

## AI provenance

- reused=3420，newly_inferred=8580，coverage=1.000，missing=0。
- 全部使用既有本地模型资产；new model download、external provider 与 external LLM 均为 0。

## Stable-key evidence 与 denominator

- 导出 108442 个 logical evidence items；development row-ID dependency=0；package fingerprint：`559778179b75b2329b88553adda83363814ab458e35a01d7f78d2cd3c895ae4c`。
- import blocking_failed=0，silently_dropped=0。
- mandatory filename/path denominator=6496；supplemental=3452；unclassified=0；未改变 canonical runtime denominator。

## 图与搜索

- signals=14068，active concepts/components=1677，largest component=88，aliases=8124，concept-media support=2065。
- multi-stable-ID、direct/transitive cannot-link、cross-role、unknown-role materialization、deferred union、duplicate active identity 均为 0。
- workload=240 queries；supported=471，unsupported=0，AND leakage=0，search mutation=0。
- accepted P50/P95/max=3.944/8.841/34.344 ms；scale=3.9/8.602/40.645 ms；performance gate=True。

## Promotion rehearsal

- rollback fingerprint restoration=True。
- committed import count=108182。
- second-import mutation count=0。
- cross-database logical mismatch count=0。
- promotion 期间 media/media_tags 与 protected/forbidden tables mutation 均为 0。

## 测试与安全边界

- 初始 default non-E2E：29 failed, 3225 passed, 4 skipped；全部失败已分类并以 bounded fixture/profile/harness 修正收敛。
- 最终 default non-E2E：3270 passed, 4 explained skips, 0 failed。
- provider、Pixiv、gallery-dl、external LLM、production、Entity、confirmed assignment、truth promotion、source mutation 均为 0。
- 未运行浏览器验证：本阶段没有 UI、frontend JavaScript 或 user-visible route 变更。

## 路由

建议项目负责人下一步审议 `SCV2-FL1: Full-Library Dev/Test Replay`，但本阶段不批准也不启动 FL1。
