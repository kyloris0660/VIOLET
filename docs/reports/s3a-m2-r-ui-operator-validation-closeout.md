# S3A-M2-R PR-R2: 操作员 UI、进度与验证收口

## PR / 分支 / Head

- PR: #129
- 分支: `codex/s3a-m2-r-ui-operator-validation-r2`
- 本轮验证基准 head: `c2a7fa1f2004d49be562c9aabc001403c9033ad5`
- 最终 pushed head: 提交后记录在 PR body 和交付报告中；提交文件无法自引用自身生成后的 Git SHA。
- 状态: PR-R2 proof/claim truthfulness closure；不合并，不开新 PR。

## 本轮实现

- 降级 full-chain / full S3A-M2-R completion claim: 当前证据证明 operator path 可以正确暴露并处理非干净状态，但本地 E2E 仍包含 `completed_with_failures`、`localization_failed`、`localization_deferred`、DB truth 的 failed/deferred，因此 `operator_ready=true`、`full_chain_complete=false`、`full_s3a_m2_r_complete=false`。
- GUI acceptance validator 改为按 `imported media + FOLLOWUP WorkItems + downstream_targets` 计算 downstream workload；follow-up-only run 的分类、AI、本地化失败/暂缓不能再被 `imported=0` 绕过。
- Web Admin job status 的“待下一次导入”现在包含 `retry_source_ready_for_import`，retry 成功但产生下一次 IMPORT 工作时不会显示为 0。
- PR-R2 合约会拒绝 required Chinese operator label 的空值、`????????`、替换字符等占位文本；公开 summary 中 `completed_with_continuation` 已恢复为可读中文。
- public redaction 会把 production source-root label / root label 视为私有 provenance；公开 summary 中 production source root 只保留 `[redacted]` 与安全 marker。
- stage strip 将 `skipped_*_run` 作为终止 skipped/stopped 状态显示，不再渲染为 queued。

## Operator Readiness 与 Full Chain

- `operator_ready`: true。证据来自真实 Web Admin Plan/Execute、进度/心跳/终态/错误与债务可见性、WorkItemKind-first summary、生产 Plan-only。
- `full_chain_complete`: false。Round 1/3/5 的本地 E2E 明确有本地化失败/暂缓，local DB truth 也有 failed/deferred。
- `full_s3a_m2_r_complete`: false。PR-R2 证明“操作员路径可信且非干净状态不被隐藏”，不把这等同于全链路干净完成。
- `target_met/safe_to_merge` scope: `operator_ready_visible_non_clean_debt`，仅表示当前 PR-R2 operator-ready 目标达成且债务可见，不表示 clean full-chain。

## UI 进度与心跳

Web Admin Plan/Execute 显示 phase、status、elapsed、last heartbeat/update、当前安全标签、粗粒度计数、终态和错误信息。Execute 确认后立即进入非空 pending/executing 过渡态并禁用重复提交；run id 一旦后端返回即显示；真实 worker heartbeat 到达前显示 starting/waiting 状态，不伪造 import/classification/AI/localization 细粒度进度。

Round 1 rerun2 证据: `non_blank_visible=true`，`duplicate_submit_disabled=true`，`run_created_at_present=true`，`last_heartbeat_at_present_in_execute_response=false`，queued stages 未被渲染成 running。

## 标签映射

operator status、WorkItemKind 和 lifecycle/debt 的中文标签已覆盖 PR-R2 要求。`completed_with_continuation` 当前中文为“已完成当前批次：还有下一批或源文件重试恢复后的导入需要继续计划。”，不再是问号占位。normal incremental `IMPORT/FOLLOWUP/RETRY_SOURCE` 是 actionable；advanced full-rescan 被阻断的 `RETRY_SOURCE` 显示为 retry debt 但当前模式不可执行；`BROKEN_STATE/PLACEHOLDER/NOOP_DIAGNOSTIC` 可见但不可执行。

## WorkItemKind-first 语义

报告和 validator 以 `work_item_kind` 为准: IMPORT count 来自 `IMPORT`，retry count 来自 `RETRY_SOURCE`，legacy `state` 不覆盖 WorkItemKind。`NOOP_DIAGNOSTIC` 与 `PLACEHOLDER` 不显示为可执行工作；`BROKEN_STATE` 保持可见但不可执行；source-missing retry debt 保持可见；缺 Media row / app-managed file 缺失保持为 BROKEN_STATE 诊断。

## 浏览器 / 本地 GUI E2E

- 浏览器: Microsoft Edge via Playwright
- 环境: 隔离 local/test profile，test DB，repo-local ignored storage，本机临时 source root，本地生成图片 fixture，本地 fake LLM
- 入口: 真实 Web Admin UI；Plan 均由页面按钮触发；可执行场景的 Execute 均由真实确认点击触发
- 结果: 完整 12 轮矩阵 passed；test server 和 fake LLM 已停止；端口审计 clean

| Round | 场景 | 真实浏览器点击 | 控制 DB seed/test hook | 结果 |
|---:|---|---|---|---|
| 0 | Empty baseline Plan | 是 | 否 | plan items 0；不可执行；无公开路径泄漏 |
| 1 | Initial import via GUI | 是 | 是 | `IMPORT=3`；Execute 后立即非空进度=true，重复提交禁用=true，first response 有 `run_created_at` 且无真实 heartbeat；终态 `completed_with_failures` / `completed_with_followup_required`，本地化失败/暂缓可见 |
| 2 | No-op stable rerun | 是 | 否 | 稳定项未作为可执行 import 重复出现 |
| 3 | Incremental add via GUI | 是 | 是 | `IMPORT=2`；GUI Execute 终态 `completed_with_failures` / `completed_with_followup_required`，非干净本地化 debt 可见 |
| 4 | Duplicate/existing media | 是 | 是 | duplicate/existing 可见，未污染 DB truth |
| 5 | Cap-limited continuation | 是 | 是 | 2/2/1 三批 continuation；每批 cap/remaining 可见，非干净本地化 debt 不被隐藏 |
| 6 | RETRY_SOURCE normal incremental plan | 是 | 是 | retry-only plan 可执行；`retry_source_ready_for_import=1` -> `completed_with_continuation`；UI `待下一次导入=1`；下一次 Plan 显示 `IMPORT=1`；source-missing debt 保持可见 |
| 7 | BROKEN_STATE missing app media diagnostic | 是 | 是 | `BROKEN_STATE=1` 可见、不可执行、不消耗 actionable cap |
| 8 | Placeholder/deferred and hydration equivalent | 是 | 是 | 未触碰真实 iCloud；隔离 ledger placeholder 不可执行，恢复为可读后显示 `IMPORT=1` |
| 9 | Advanced full-rescan retry-source fail-closed | 是 | 是 | advanced full-rescan retry debt 可见但不可执行；server Execute 409 `advanced_full_rescan_retry_source_execute_not_validated` |
| 10 | Terminal non-clean stage rendering | 是 | 是 | `completed_with_localization_failures`/terminal warning 不渲染为 queued；`skipped_cancelled_run` 显示为“已跳过/已停止” |
| 11 | Public redaction / artifact safety | 否 | 否 | 公开报告 aggregate-only；raw traces/logs/DB evidence 未提交 |

本地最终 DB 聚合: dynamic source items 16，media-linked items 13，`import_status={'deferred': 1, 'failed': 2, 'imported': 13}`，`classification_status={'classified': 13, 'deferred': 3}`，`ai_tagging_status={'ai_tagged': 13, 'deferred': 3}`，`localization_status={'blocked_import_failed': 2, 'deferred': 1, 'localized': 13}`。这些 failed/deferred 是隔离测试中的 operator-visible debt/diagnostic 证据，因此 full-chain completion 保持 false。

## 生产 Plan-only

生产验证通过 launcher-managed Web Admin path，仅运行 Plan-only，未点击 Execute。latest execute run id 保持 `18 -> 18`。

- plan mode: `incremental`
- max_files: 500
- selected_plan_items / plan_items: 347
- WorkItem counts: `IMPORT=316`，`FOLLOWUP=20`，`RETRY_SOURCE=11`
- lifecycle counts: `APP_MEDIA_FOLLOWUP=20`，`CONTINUATION=179`，`IMPORT_CANDIDATE=137`，`RETRYABLE_SOURCE_FAILURE=11`
- broader state counts total: 362；范围说明: broader planner state rows including skipped/failed diagnostics outside selected plan items
- selected production source-root label: `[redacted]`
- production Execute/import/classification/AI/localization: 未运行
- S3B automatic/scheduled/startup/service sync: disabled
- public surface: 未发现本地绝对路径、源文件名、content hash、私有 source-root 标签、DB credential、API key 或 provider secret

## 安全与范围

未运行生产 Execute；未做生产 import/classification/AI/localization；未做 source/iCloud mutation；未做 app-storage repair/mutation；未做 cleanup/reset/drop/truncate；未启动 S3B；未启动 Pixiv/provider/gallery-dl/SauceNAO/Google；未启动 SourceConcept/Entity/media_tags truth 工作。

## 验证

- Python identity: repo venv Python，Python 3.12.0，passed；exact local path 仅在交付报告中记录，不写入 public report。
- changed Python `py_compile`: passed。
- `node --check frontend/static/js/admin.js`: passed。
- `pytest tests/test_admin_dynamic_sync_ui.py -q`: 12 passed。
- focused GUI validator pytest: 11 passed, 21 deselected。
- focused execute/operator pytest: 12 passed, 81 deselected。
- `pytest tests/test_phase_contracts.py -q`: 293 passed。
- isolated incremental GUI E2E: passed，12 rounds，Microsoft Edge via Playwright，rerun2 含 immediate non-blank Execute progress hard assertion。
- production GUI Plan-only: passed，launcher-managed Web Admin path，latest execute id `18 -> 18`，未运行 Execute。
- post-validation server/port audit: passed，occupied_count=0。
- summary JSON parse: passed。
- `s3a_m2_r_operator_validation_contract_v1`: passed；合约明确看到 failed/deferred downstream evidence，但因本报告不再宣称 clean full-chain completion，当前状态为 `operator_ready`。
- `public_redaction_contract_v1`: passed；production source-root label 已公开红acted，仅保留 aggregate counts。
- public-safe fragment scan: passed；仅保留安全边界说明和聚合字段，无本地绝对路径、原始文件名、content hash、生产 source-root label、credential/API key。
- `git diff --check` / `git diff --cached --check`: 最终 staging 后在交付报告记录。

## 状态判断

PR-R2 已达到 final human GUI acceptance 前的 operator-ready 状态: 操作员可以通过真实 Web Admin 计划、理解、监控和验证手动同步；非干净 downstream/retry/debt/diagnostic 状态会被明确显示，不再被 clean completion claim 覆盖。

PR-R2 没有证明 clean full-chain completion；`full_chain_complete=false` 与 `full_s3a_m2_r_complete=false` 是有意的 truthfulness 修正。剩余 out-of-scope 风险均非当前 PR 阻塞项: advanced full-rescan retry-source execution 仍明确不可执行，生产 Execute 仍需 owner 另行审批，S3B 与 provider/SourceConcept/Entity/media_tags truth 工作未启动。

如果人工 GUI acceptance 接受当前 operator-ready 语义，合并后建议回到主线: R1R -> A1R -> Pixiv/source metadata -> SourceConcept/Entity aggregation。不要在 PR-R2 内启动这些工作。

## 工程判断 / 操作员备注

Artifact lifecycle: 前端修复属于 durable production code；validator/contract/tests 属于 reusable validation/safety tool；本报告和 summary JSON 属于 public report / handoff；本地浏览器脚本、截图、server log、DB evidence 是 ignored one-off local artifact。

阶段边界合适: 本轮只修 PR-R2 proof/claim truthfulness 与 operator consistency，没有改 lifecycle classifier 或 WorkItem core。根因是 proof layer 把“operator-ready 且债务可见”误写成“full-chain clean complete”，以及 validator/UI 在 FOLLOWUP、retry-ready、skipped terminal、redaction label 上缺少同类硬门。

当前修复的 reviewer/blocker 类别: full-chain overclaim、FOLLOWUP-only downstream workload、retry-created pending import visibility、Chinese label corruption、production source-root label redaction、skipped terminal stage rendering。刻意不做: S3B、production Execute、Pixiv/provider/SourceConcept/Entity/media_tags truth、advanced full-rescan execution redesign、通用审计平台。

PR-R2 ready for final human GUI acceptance: 是，按 operator-ready-with-visible-debt 语义；不是 clean full-chain completion。
