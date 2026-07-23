# SCV2-SV1B Replay Trusted-Provenance Checkpoint

## 结论

PR #139 的 localization accounting 已闭合，且一个
`manual_localization_review_pending` 不再阻断无关条目处理。不过，独立
Replay SourceConcept 构建暴露了一个更早的 stable-evidence 导出缺陷：
递归 sanitizer 将 Pixiv `stable_identity_key.work_id` 误判为开发数据库
行 ID 并从导出包删除。旧的包级 reconciliation 因此比较了两份同样
缺字段的投影，错误地报告 logical equality。

当前精确状态为：

```text
status = blocked_sv1b_replay_trusted_provenance_reconciliation
target_met = false
safe_to_merge = false
route_approved = false
manual_acceptance_status = not_started_blocked
next_phase_started = false
```

## 当前证据

- Primary / Replay metadata record rows: `17,193 / 17,193`
- Missing / extra replay stable records: `0 / 0`
- Graph-effective projection mismatches: `13,261`
- Exact stable-identity mismatches: `13,261`
- Trusted-complete verdict mismatches: `6,074`
- Primary / Replay trusted-complete rows: `6,605 / 531`
- Primary projection fingerprint:
  `fa2a78d4832e1f7ca9727ec20a3fc4ceadb053662e9681a0ccb73926ca2c4bfb`
- Replay projection fingerprint:
  `0ffebd04784126c311eddcd4d5a438580ea95264318a00c0e91418b69a311f23`
- Mismatch membership fingerprint:
  `efff5ee1746ba961552da57304ef1726250d063921d9e5551ef0fef5c9e92c0d`

旧 acquired package fingerprint
`df6008c1b469beaf9bd7f47e8a9af460188b2ad7e1218366a76e2d17e77d8636`
保持不变。此次再审计没有重新获取 Pixiv 数据，也没有调用 gallery-dl、
Pixiv 或 LLM。

## 修复边界

代码现在：

1. 将 provider `work_id` 保留为 stable provider identity；
2. 在 graph derivation 前直接比较两库的 graph-effective trusted metadata
   输入，不再只依赖 sanitized package equality；
3. 以 hash-only private mismatch ledger 记录精确成员；
4. 在 mismatch 时 fail closed，使用
   `blocked_sv1b_replay_trusted_provenance_reconciliation`。

当前 Replay DB 已包含一次失败的 phase-owned derived graph。无论是清除
这些 derived rows 后重建，还是创建新的 Replay DB，都会扩大或改变本轮
授权。此次 checkpoint 没有执行 delete、truncate、reset、drop、retry3
创建或 provenance 修补。

## Localization 状态

- Eligible: `1,788`
- Accepted new translations: `1,787`
- Explicit proper-noun exclusions: `454`
- Manual review pending: `1`
- Pending canonical tag: `enpera`
- Validator verdict: `untranslated_echo`
- Runtime fallback: canonical English display
- Primary / Replay translation rows: `5,519 / 5,519`
- Translation logical fingerprint:
  `41dcd1db481544dac6805000e678c98af03332cd592c557331c997df2293c3bd`
- Localization accounting closed: `true`
- Localization translation complete: `false`
- Downstream threshold gate: `true` (`1 <= 8`)

## 后续所需授权

若要继续本 PR 的自动执行，需要项目负责人明确选择并授权一个恢复路径：

1. 在 retry2 Replay DB 中仅清除失败的 phase-owned derived SourceConcept/
   graph/search rows，按 Primary 的 exact graph-effective package 补齐 Replay
   trusted provenance，再独立重建；或
2. 明确覆盖当前禁止 retry3 的约束，创建全新的 Replay DB。

在获得该授权前，不生成 40-case harness，不运行 browser acceptance，也不
声称 manual acceptance ready。
