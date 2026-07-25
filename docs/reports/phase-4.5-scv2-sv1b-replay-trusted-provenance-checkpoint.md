# SCV2-SV1B Replay Trusted-Provenance Checkpoint

## 当前结论

PR #139 的 acquisition、localization accounting、R2R exact remap 与 Primary
graph safety 已形成受保护输入。旧 retry2 Replay 的 graph-effective provenance
不可信，状态仍为
`blocked_sv1b_replay_trusted_provenance_reconciliation`。项目负责人现已批准：
保留失败 Replay 为不可变取证证据；完成 schema-aware stable replay package
v2 后，只创建一个 fresh isolated Replay verification database。

当前状态字段仍为：

```text
target_met = false
safe_to_merge = false
route_approved = false
manual_acceptance_status = not_started_replay_recovery
next_phase_started = false
```

## 事故时间线

1. 当时：accepted acquisition package 导入 retry2 Replay，旧 v1 package
   equality 报告 logical equality。
2. 当时：Primary graph 通过安全门；独立 Replay graph 构建后，直接
   graph-effective comparison 暴露不一致。
3. 当时：执行只读 mismatch audit；保存 failed Replay graph，不清理、不
   修补。
4. 当前负责人决策：v1 与 failed Replay 保留；先落地 DOC-GOV-01 与 package
   v2，再创建唯一 fresh Replay。

## 影响与历史证据

- Primary / failed Replay metadata rows: `17,193 / 17,193`
- Missing / extra failed Replay stable records: `0 / 0`
- Graph-effective projection mismatches: `13,261`
- Exact stable-identity mismatches: `13,261`
- Trusted-complete verdict mismatches: `6,074`
- Primary / failed Replay trusted-complete rows: `6,605 / 531`
- Primary projection fingerprint:
  `fa2a78d4832e1f7ca9727ec20a3fc4ceadb053662e9681a0ccb73926ca2c4bfb`
- Failed Replay projection fingerprint:
  `0ffebd04784126c311eddcd4d5a438580ea95264318a00c0e91418b69a311f23`
- Mismatch membership fingerprint:
  `efff5ee1746ba961552da57304ef1726250d063921d9e5551ef0fef5c9e92c0d`

这些数字描述失败 retry2 Replay 的当时取证状态，不预先断言 fresh Replay
结果。旧 acquired package fingerprint
`df6008c1b469beaf9bd7f47e8a9af460188b2ad7e1218366a76e2d17e77d8636`
保持不可变。

## 直接根因与共同失效

旧递归 sanitizer 以字段名推断稳定性，将
`provenance.stable_identity_key.work_id` 误判为 development row ID 并删除。
该字段实际参与 provider stable identity、trusted-complete predicate 与 graph
输入。旧 equality 同时对 Primary export 与 Replay re-export 使用同一有损
投影，因此比较的是两份共同缺字段的包；“相等”不能证明真实
graph-effective equality。

## 为什么旧测试没有发现

- fixture 只覆盖小型平面形状，没有覆盖现实 nested provenance。
- 测试关注包级 deterministic equality，没有独立计算 trusted predicate 与
  graph-effective projection。
- 全局 `*_id` 规则与字段名 allowlist 没有表达 schema/path 语义。
- `compatible_complete_record_reuse` 仍可能携带 development numeric row ID，
  没有强制 stable source-record reference。

## 遏制与负责人批准的恢复

- failed retry2 Replay 保留完整失败 derived graph；禁止 update、delete、
  truncate、reset、drop、cleanup 或 provenance patch。
- Primary、acquisition、localization、R2R 与 v1 package/fingerprint 均为受保护
  输入。
- fresh Replay 只能从 accepted evidence 与 v2 package 重建，不调用 Pixiv、
  gallery-dl、provider、LLM，也不下载 media/thumbnail。
- Primary stable identity 必须先与不可变 acquisition evidence 交叉核验；
  不允许根据 filename、row order 或 numeric DB ID 猜测。

## 修复状态与预防控制

DOC-GOV-01 建立 `docs/state/current-phase.json` 单一事实源、生成式 handoff、
状态检查器与 durable links。Stable replay package v2 依照
[ADR-0001](../decisions/ADR-0001-stable-replay-evidence-v2.md) 实施：
schema/version-aware serializer、stable-reference reuse、字段级
preservation/loss ledger、unknown graph-effective field fail-closed，以及
export → fresh import → re-export、graph-effective、trusted-complete 三重相等门。

## Localization 状态

- Eligible: `1,788`
- Accepted new translations: `1,787`
- Explicit proper-noun exclusions: `454`
- Manual review pending: `1` (`enpera`, `untranslated_echo`)
- Runtime fallback: canonical English display
- Primary / failed Replay translation rows: `5,519 / 5,519`
- Translation fingerprint:
  `41dcd1db481544dac6805000e678c98af03332cd592c557331c997df2293c3bd`

单个 `enpera` 是 manual harness 条目，不是 fresh Replay 恢复 blocker。

## Durable Links

- [Current phase state](../state/current-phase.json)
- [Stable replay evidence v2 ADR](../decisions/ADR-0001-stable-replay-evidence-v2.md)
- [Current handoff](../current-handoff.md)
- [Phase contracts](../phase-contracts.md)

## 2026-07-25 package v2 离线修复检查点

`sv1b.stable-replay-evidence.v2` 已完成 schema-aware 离线导出与真实 Primary
只读核验。导出保留 `17,193` 条 metadata，trusted-complete verdict 保持
`6,605`，未把 Primary 中原本缺失的 nested identity 推断为可信事实。
accepted acquisition package 的 stable-key membership 与顶层 provider facts
逐条交叉核验结果为：missing `0`、extra `0`、accepted provider-fact mutation
`0`、stable-identity conflict `0`、unsupported identity `0`。字段级
preservation/loss ledger 的 silent/graph-effective loss 均为 `0`。

package fingerprint 为
`640c52445524aa69f540a64a41800b9eb5a746d9a234ba6582b5a2ef1feb7845`；
membership fingerprint 为
`7540ba28da284c99ae835e87b79527dbc4ebf9d28c613add19a9892e11e6869f`。
下一门是创建负责人授权的唯一 fresh Replay v2 并执行
export → import → re-export、graph-effective、trusted verdict 三重相等。
失败 retry2 Replay 至此仍未发生任何写入。

fresh-Replay-v2 runner 与 fail-closed 安全测试已绑定到
`bf3c9e8398073d506ce91cc79632863caa7aad56`；完整 pre-creation non-E2E
结果为 `3,511 passed / 4 approved skips`，外部调用预算保持为零。

## Fresh Replay v2 create/import 结果

负责人授权的唯一 fresh Replay
`blombooru_scv2_sv1b_replay_v2_verification_test_20260725` 已创建。
Primary / fresh Replay metadata 为 `17,193 / 17,193`，missing / extra 为
`0 / 0`，trusted-complete 为 `6,605 / 6,605`，translation rows 为
`5,519 / 5,519`。graph-effective、stable identity 与 trusted verdict
mismatch 均为 `0`；第二次同包导入新增 `0` 行。

create/import proof fingerprint 为
`935f82bdadd502471240c04eac03c400f5c808978d891832b71f95efa2069ab9`。
失败 retry2 Replay 的前后 forensic state fingerprint 均为
`ad30e3c38b254b3290f6b849072270c04e05a843e11c815cedb9c70881780b8f`。
外部 provider / LLM / media 调用计数仍为 `0`。下一门为 fresh Replay
独立 graph derivation 与 Primary logical comparison；当前 pending code 为
`pending_sv1b_fresh_replay_graph_validation`。

## 2026-07-25 fresh Replay v2 首次图派生事故

首次独立 fresh-Replay 图派生保留为失败取证 checkpoint；没有清理、重置或
重试。Package round-trip、trusted-complete equality、R2R accounting、
606/606 accepted-family traceability、cannot-link safety、stable creator
identity 与 large-component safety 均通过，但 graph safety 仍检测到唯一
一个 `deferred_identity_union`。

只读诊断证明存在第二个同类 identity 缺陷：SourceConcept `signal_key`
仍使用 development `source_metadata_record_id`。因此同一个 key 在 Primary
代表 creator account signal，在 fresh Replay 却代表 character
parenthetical signal。这不是可接受的逻辑差异，不能豁免。

当前以
`blocked_sv1b_fresh_replay_graph_signal_identity_collision` fail closed。
失败 graph proof fingerprint 为
`7449ba378e957b76ab04ce721f77d8623acf030903a12c8c870bfc7b5b3e5ad6`。
唯一获授权 fresh database 保持原位，失败 retry2 Replay 保持不变，外部调用
计数继续为零。

修复必须用 schema-aware stable source-record reference 替换数值 source-row
identity，并证明 versioned、idempotent superseding re-derivation。不得写
Primary、清理 derived rows、创建另一个 Replay database，或根据 row order
猜测 provider identity。

## 2026-07-26 stable signal v2 重派生检查点

stable signal identity v2 已将 SourceConcept signal identity 改为
schema-aware stable source-record reference。真实 Primary / fresh Replay
signal projection 为 `126,127 / 126,127`，missing / extra 为 `0 / 0`，
fingerprint 为
`15c3c98a2cfd71933776952fa5bd49563ef808800bac659e45cd7d3763dddacf`。

受控 superseding 重派生已提交到同一个 fresh Replay，未创建第二个数据库。
图安全审计通过：`deferred_identity_union_count=0`、direct/transitive
cannot-link violation 均为 `0`、unsafe large / cross-role / unknown-role
均为 `0`，accepted creator families 为 `606 / 606`。但整体证明按设计
fail closed，因为 persisted core projection 把明确标为 `superseded`
的历史 signal/concept/alias 行也计入当前 run，导致 planned / persisted
signal count 为 `126,127 / 252,164`。evidence 和 link 的当前逻辑投影
已经相等；这不是新的 graph safety 违规。

失败证明 fingerprint 为
`3fade25d12b60601717359af94348ca76f08a6c22d12a829af38b4e5fa459c04`，
保持不重写。当前 blocker 为
`blocked_sv1b_fresh_replay_persisted_projection_scope`。修复仅允许让
checkpoint projection 排除 `superseded` 历史行，并对已提交 graph
执行零数据库写入的作用域 reconciliation；不得再次派生、清理历史、
重建数据库或修改旧 failed retry2 Replay。旧 failed Replay forensic
fingerprint 复核仍为
`ad30e3c38b254b3290f6b849072270c04e05a843e11c815cedb9c70881780b8f`，
外部调用仍为 `0`。

恢复入口也改为 stage-aware：第一次失败 proof 继续按原 fingerprint
验证；一旦精确的 corrected failed-scope proof 已存在，就验证该 proof
及其对应的当前数据库状态，不再错误要求当前数据库永久等于第一次失败图。
这条恢复路径不会再次调用 graph derivation，也不会写数据库。

同理，第一次图的 R2R audit 副本改为 pinned historical artifact：
历史文件按固定 file fingerprint 验证，当前 live audit 可随 stable-signal
阶段演进；不再错误要求两者永久字节相等。其他 accepted artifact copy
gate 未被放宽。
