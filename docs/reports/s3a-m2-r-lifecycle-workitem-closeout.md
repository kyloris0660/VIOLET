# S3A-M2-R PR-R1 Lifecycle / WorkItem Closeout

本报告只覆盖 PR-R1 的 lifecycle / WorkItem 后端核心实现，不声明完整
S3A-M2-R 完成。UI 进度、中文操作员文案、浏览器验证和最终 GUI 路径验收
保留给 PR-R2 或后续 closeout。

## 范围

- 新增 `manual_sync_lifecycle` 作为手动同步生命周期的共享解释层。
- 在 planner、execute status mapping、R0 audit/validator debt inventory 的高风险路径接入
  lifecycle decision。
- 保持生产同步仍为人工触发；本 PR 没有运行生产 Execute。
- 没有引入 DB migration、SourceConcept / Entity bridge、provider/source metadata、自动/定时/无人值守同步。

## LifecycleKind 与 WorkItemKind

PR-R1 实现的 `LifecycleKind`：

- `APP_MEDIA_FOLLOWUP`
- `IMPORT_CANDIDATE`
- `RETRYABLE_SOURCE_FAILURE`
- `PLACEHOLDER_DEFERRED`
- `STABLE_NOOP`
- `HISTORICAL_DIAGNOSTIC`
- `CONTINUATION`
- `BROKEN_STATE`
- `FATAL_BLOCKER`

PR-R1 实现的 `WorkItemKind`：

- `FOLLOWUP`
- `IMPORT`
- `RETRY_SOURCE`
- `PLACEHOLDER`
- `NOOP_DIAGNOSTIC`
- `BROKEN_STATE`

核心映射：

- `APP_MEDIA_FOLLOWUP` -> `FOLLOWUP`：已有 app-managed media，但 classification / AI tagging / localization 当前不完整。
- `IMPORT_CANDIDATE` -> `IMPORT`：需要源文件 revalidate/hash/copy/import 的导入候选。
- `RETRYABLE_SOURCE_FAILURE` -> `RETRY_SOURCE`：read error / read timeout / failed cloud hydration / changed-source retry 等 item-level 源侧失败。
- `PLACEHOLDER_DEFERRED` -> `PLACEHOLDER`：真实 cloud / iCloud placeholder evidence，默认不可执行。
- `STABLE_NOOP` / `HISTORICAL_DIAGNOSTIC` -> `NOOP_DIAGNOSTIC`：诊断可见但不消耗 actionable cap。
- `BROKEN_STATE` -> `BROKEN_STATE`：可见、默认不可执行，需要 operator/repair 阶段处理。

## Source-Read 边界

- `FOLLOWUP` 使用 app-managed media，`allowed_source_reads=false`，不依赖原始源文件可读性。
- `IMPORT` 可在 Execute 阶段读源、hash、copy/revalidate。
- `RETRY_SOURCE` 可在 retry policy 下读源，但不阻塞 app-media follow-up。
- `PLACEHOLDER` 默认不读源、不执行、不消耗 cap。
- `NOOP_DIAGNOSTIC` 不读源、不写 DB、不消耗 cap。
- `BROKEN_STATE` 可见但默认不可执行；media-backed item 缺 app-managed media 时即使下游状态看似 terminal 也归为 broken。

## Attempted / Completed / Current Health

`LifecycleDecision` 显式区分：

- `attempted_in_run`
- `current_downstream_complete`
- `attempted_but_current_incomplete`
- `not_processed_continuation`
- `stable_noop`
- `broken_state`

因此，运行中曾经尝试过的 downstream follow-up 不会因为当前 completion false 就被误写成
`not_processed`；cap/budget/cancel 留下的未处理项也会保留为 `CONTINUATION`，而不是 terminal failure。

## 旧状态映射

新增 operator status mapping 保留 legacy `run.status` 兼容，同时在 execute summary 中写入
`operator_status`：

- clean completed -> `completed`
- retryable item-level source failures + 已处理工作完成 -> `completed_with_retryable_failures`
- downstream incomplete -> `completed_with_followup_required`
- unprocessed continuation -> `completed_with_continuation`
- retryable + continuation -> internal combined bucket `completed_with_retryable_failures_plus_continuation`
- systemic/fatal blocker -> `failed_systemic`
- cancelled -> `cancelled`
- preflight blocked -> `blocked_preflight`

## R0 Debt 在 PR-R1 模型中的表示

- 20 older app-media / source-missing / downstream-incomplete rows：
  app storage present 时是 `APP_MEDIA_FOLLOWUP`；app storage missing 时是 `BROKEN_STATE`。
- Run #18 的 75 deferred rows：`CONTINUATION`，不是 failed import。
- Run #18 的 11 retryable source failures：`RETRYABLE_SOURCE_FAILURE`，不阻塞 app-media follow-up。
- Run #18 的 880 historical downstream follow-up rows：当前下游 complete 时是 `STABLE_NOOP` /
  diagnostic，不再消耗 normal actionable cap。

## 验证

PR-R1 增加表驱动 lifecycle 测试、planner service-level 集成测试、execute operator-status
回归测试、phase contract 测试，以及 `s3a_m2_r_lifecycle_workitem_contract_v1`。

公共摘要：

- `docs/reports/s3a-m2-r-lifecycle-workitem-summary.json`

## Reviewer Fix Round

本轮只处理 PR-R1 reviewer 标出的 lifecycle / WorkItem 正确性问题：

- media-backed `read_timeout`、`cloud_hydration_failed`、`content_changed_after_plan` 等源侧 retry 仍归为
  `RETRYABLE_SOURCE_FAILURE` / `RETRY_SOURCE`，不会被 `media_id` 抢先解释成 `APP_MEDIA_FOLLOWUP`。
- `cloud_hydration_failed` 失败源尝试保持 retryable；只有 `skipped_placeholder`、`cloud_placeholder`、
  `icloud_placeholder` 等真实 placeholder evidence 才归为 `PLACEHOLDER_DEFERRED`。
- current downstream complete 且 app media 存在的 media-backed row 会先归为 `STABLE_NOOP`，不会被陈旧
  `not_processed_budget_stop` 继续消耗 cap。
- planner 的 app-media follow-up path 会预取 Media row 和 app-storage existence evidence；缺 Media row 或缺
  app file 时输出 visible / non-executable `BROKEN_STATE`。
- legacy `completed_with_failures` / budget stop status mapping 会先识别混合 non-retryable failures 和
  failure-budget stage failures，避免误报成 retry-only 或 follow-up-required。
- phase contract 已把 `PLACEHOLDER` 加入 source-read boundary：`allowed_source_reads=false`、
  `can_execute=false`、`consumes_actionable_cap=false`。

## 安全确认

- no push main
- no merge
- no production Execute
- no source/iCloud mutation
- no app-storage repair/mutation
- no cleanup/delete/reset/drop/truncate
- no DB import
- no production classification / AI tagging / localization
- no provider/source metadata calls
- no SourceConcept / Entity / media_tags truth writes
- no full S3A-M2-R completion claim

## PR-R2 剩余

- UI progress / heartbeat
- Chinese operator labels and status text
- preflight script polish
- validator/report cleanup
- Markdown/public-redaction hardening if needed
- browser validation on a controlled test server
- final GUI-path acceptance when production operator behavior is in scope
