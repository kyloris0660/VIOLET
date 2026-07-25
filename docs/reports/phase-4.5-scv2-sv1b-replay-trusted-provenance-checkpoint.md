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
