# S3A-M2-R PR-R1 Lifecycle / WorkItem Closeout

本报告只覆盖 PR-R1 的 lifecycle / WorkItem 后端核心实现与本轮 reviewer 边界修复。它不声明完整 S3A-M2-R 完成；UI 进度、浏览器验证、中文操作员文案、最终 GUI 路径验收保留给 PR-R2 或后续 closeout。

## PR / Branch / Head

- PR: #128
- Branch: `codex/s3a-m2-r-lifecycle-workitem`
- Head: 以最终提交为准
- Base: `main`

## 已实现内容

- 新增 `manual_sync_lifecycle`，作为手动同步 lifecycle / WorkItem 的共享解释层。
- 将 planner、execute 边界、operator status mapping、root-scoped debt inventory 的高风险路径接入 lifecycle decision。
- 明确区分 `attempted_in_run`、`current_downstream_complete`、`attempted_but_current_incomplete`、`not_processed_continuation`、`stable_noop`、`broken_state`。
- 保留 legacy 状态兼容，同时把 `completed_with_failures` 按 item outcome 映射为 retryable、follow-up、continuation 或 systemic failure。

## 未实现内容

- 未实现 PR-R2 UI progress / heartbeat。
- 未实现中文 Web Admin 操作员标签 polish。
- 未运行浏览器最终验收或生产 GUI 路径验收。
- 未启动 S3B、provider/Pixiv/source metadata、SourceConcept、Entity、media_tags truth work。
- 未运行 production Execute、production import、production classification、AI tagging 或 localization。

## LifecycleKind / WorkItemKind 映射

- `APP_MEDIA_FOLLOWUP` -> `FOLLOWUP`: 已有 app-managed media，但 classification / AI tagging / localization 当前未完成。
- `IMPORT_CANDIDATE` -> `IMPORT`: 需要源文件 revalidate/hash/copy/import 的导入候选。
- `RETRYABLE_SOURCE_FAILURE` -> `RETRY_SOURCE`: `read_error`、`read_timeout`、`cloud_hydration_failed`、`content_changed_after_plan` 等源侧可重试失败。
- `PLACEHOLDER_DEFERRED` -> `PLACEHOLDER`: 明确 cloud/iCloud placeholder evidence，默认不可执行。
- `STABLE_NOOP` / `HISTORICAL_DIAGNOSTIC` -> `NOOP_DIAGNOSTIC`: 诊断或稳定 no-op，不消耗 actionable cap。
- `BROKEN_STATE` -> `BROKEN_STATE`: 可见、默认不可执行，需要 operator/repair 阶段处理。

## Source-Read 边界

- `FOLLOWUP`: 只使用 app-managed media，`allowed_source_reads=false`。
- `IMPORT`: 可在 Execute 阶段读取源、hash、copy、revalidate。
- `RETRY_SOURCE`: 只做 retry policy 下的源可读性/hash 检查，不在 `RETRY_SOURCE` 下静默导入；无既有 media 的成功 retry 标记 ready-for-import，由下一轮显式 `IMPORT` 计划处理；已有 `media_id` 且 app media 存在的成功 retry 恢复为 media-backed `imported` 语义，下一轮显式露出 `FOLLOWUP` 或 `STABLE_NOOP`。
- `PLACEHOLDER`: `can_execute=false`，不读源，不消耗 cap，不改写 media-backed imported state。
- `NOOP_DIAGNOSTIC`: 不读源，不改写 `DynamicSourceItem` 核心状态，不消耗 cap。
- `BROKEN_STATE`: 可见但不可执行；缺 Media row 或缺 app media 时暴露为 broken，不隐藏成 follow-up 或 stable no-op。

## Attempted / Completed / Current Health

PR-R1 模型把“本轮是否尝试过”和“当前下游是否健康完成”分开表达。一个已记录为 downstream follow-up 的 run item，不会因为当前 completion false 被误写成 not processed；cap/budget/cancel 留下的未处理项保持 `CONTINUATION`，不会被当作 terminal failure。

## completed_with_failures 映射

- retryable item-level source failures 且已处理工作完成 -> `completed_with_retryable_failures`
- retryable + continuation -> `completed_with_retryable_failures_plus_continuation`
- downstream incomplete -> `completed_with_followup_required`
- unprocessed continuation -> `completed_with_continuation`
- mixed non-retryable failures 或 failure-budget stage failures -> `failed_systemic`
- clean run -> `completed`
- cancelled run -> `cancelled`
- preflight blocked -> `blocked_preflight`

## R0/R1 Debt 表达

- 20 条 older app-media / source-missing / downstream-incomplete rows: app media 存在时是 `APP_MEDIA_FOLLOWUP`；Media row 或 app file 缺失时是 `BROKEN_STATE`。
- 75 条 deferred continuation: 表达为 `CONTINUATION`，保持可见，不作为 terminal import failure。
- 11 条 retryable source failures: 表达为 `RETRYABLE_SOURCE_FAILURE` / `RETRY_SOURCE`，不会阻塞 app-media follow-up。
- 历史 downstream follow-up rows: 当前 downstream complete 且 app media evidence 存在时是 `STABLE_NOOP`，不继续消耗 normal actionable cap。

## 本轮 Reviewer 修复

- `RETRY_SOURCE` 不再 fall through 到 normal import/copy path；成功 retry 后，无既有 media 的行只转为 ready-for-import，下一轮显式 re-plan 为 `IMPORT`；已有 app media 的行恢复为 media-backed imported 语义，下一轮显式 re-plan 为 `FOLLOWUP` 或 `STABLE_NOOP`，不会留下 `media_id + pending` 的隐形 no-op。
- `BROKEN_STATE`、`NOOP_DIAGNOSTIC`、`PLACEHOLDER` 可记录 run-item diagnostic，但不改写 `DynamicSourceItem` 的 import/stage/status 核心字段。
- `BROKEN_STATE` 和 `NOOP_DIAGNOSTIC` 保持可见，但不占用 `max_files` actionable cap。
- cancel、duration budget 或 failure budget 停止后，execute 只把剩余可执行 actionable work 物化为 `deferred_unprocessed`；尾部 `BROKEN_STATE` / `NOOP_DIAGNOSTIC` / `PLACEHOLDER` 诊断不会改写 `DynamicSourceItem` 核心字段。
- stale `skipped_placeholder` 在当前 planner evidence 已可读、import-ready 时可恢复为 `IMPORT_CANDIDATE` / `IMPORT`。
- media-backed stable/no-op 判断在可能隐藏 broken state 时检查 Media row 和 app-storage evidence；缺 Media row 或缺 app-managed original 输出 `BROKEN_STATE`。
- 当前 downstream complete 且 app media 存在的 media-backed 行，即使遗留 `read_timeout` / `source_missing` 等 stale retry reason，也归为 `STABLE_NOOP` / `NOOP_DIAGNOSTIC`，不读源、不消耗 cap。
- plan record 保留 lifecycle `reason_code`，例如 `read_timeout`、`not_processed_budget_stop`、`app_media_missing`。
- `PLACEHOLDER` boundary 已纳入 phase contract: `allowed_source_reads=false`、`can_execute=false`、`consumes_actionable_cap=false`。

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

## PR-R2 剩余范围

- UI progress / heartbeat
- Chinese operator labels and status text
- preflight script polish
- validator/report cleanup
- Markdown/public-redaction hardening if needed
- browser validation
- final GUI-path acceptance when production operator behavior is in scope
