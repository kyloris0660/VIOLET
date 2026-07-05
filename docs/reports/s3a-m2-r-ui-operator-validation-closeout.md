# S3A-M2-R PR-R2: 操作员 UI、进度与验证收口

## PR / 分支 / Head

- PR: #129
- 分支: `codex/s3a-m2-r-ui-operator-validation-r2`
- 本轮微修输入 head: `004d16411fa9ff7b36dcadb0b547d9f1b1ae8eb3`
- 最终 pushed head: 提交后记录在 PR body 和交付报告中；提交文件无法自引用自身生成后的 Git SHA。
- 状态: PR-R2 operator/proof consistency micro-fix；不合并，不开新 PR。

## 本轮实现

- `retry_source_ready_for_import > 0` 不再序列化为 clean `completed`: 执行 run 顶层为 `completed_with_followup_required`，operator status 为 `completed_with_continuation`，中文标签说明“源文件重试恢复后的导入需要继续计划”。
- 生产 Plan-only 证据拆分为 selected plan item 计数与 broader state 计数: `selected_plan_items=343`，`work_item_counts` 合计 343，`lifecycle_counts` 合计 343，`state_counts_total=357` 明确标记为包含 skipped/failed diagnostics 的 broader planner state rows。
- stage strip 将 `completed_with_localization_failures` 以及其他 `completed_with_*` 终态当作 terminal/warning，不再渲染为 queued。
- advanced full-rescan 被阻断的 `RETRY_SOURCE` 卡片仍可见为 retry-source debt，但显示“当前高级模式不可执行”，不再标成绿色“可执行工作”。normal incremental `RETRY_SOURCE` 仍可执行。

## UI 进度与心跳

Web Admin Plan/Execute 显示 phase、status、elapsed、last heartbeat/update、当前安全标签、粗粒度计数、终态和错误信息。Execute 确认后立即进入 pending/executing 过渡态，禁用重复提交；run id 一旦后端返回即显示；真实 worker heartbeat 到达前显示“等待第一个后端进度心跳”，不伪造导入、分类、AI 标注、本地化或 summary 的细粒度进度。

## 标签映射

operator status、WorkItemKind 和 lifecycle/debt 的中文标签已覆盖 PR-R2 要求。特别是 `completed_with_continuation` 现在明确包括“下一批或源文件重试恢复后的导入需要继续计划”。`IMPORT/FOLLOWUP/RETRY_SOURCE` 是 normal path actionable；`BROKEN_STATE/PLACEHOLDER/NOOP_DIAGNOSTIC` 可见但不可执行。

## WorkItemKind-first 语义

报告和 validator 以 `work_item_kind` 为准: IMPORT count 来自 `IMPORT`，retry count 来自 `RETRY_SOURCE`，legacy `state` 不覆盖 WorkItemKind。`NOOP_DIAGNOSTIC` 与 `PLACEHOLDER` 不显示为可执行工作；`BROKEN_STATE` 保持可见但不可执行；source-missing retry debt 保持可见；缺 Media row / app-managed file 缺失保持为 BROKEN_STATE 诊断。

## 浏览器 / 本地 GUI E2E

- 浏览器: Microsoft Edge via Playwright
- 环境: 隔离 local/test profile，测试 DB，repo-local ignored storage，本地生成图片 fixture，本地 fake LLM
- 入口: 真实 Web Admin UI；Plan 均由页面按钮触发；可执行场景的 Execute 均由真实确认点击触发
- 结果: 完整 12 轮矩阵 passed；测试 server 和 fake LLM 已停止；端口审计 clean

| Round | 场景 | 结果 |
|---:|---|---|
| 0 | Empty baseline Plan | plan items 0，不可执行，无公开路径泄漏 |
| 1 | Initial import | `IMPORT=3`；确认后 pending 立即可见，重复提交禁用，run id 可见，execute response 有 `run_created_at` 且无真实 heartbeat |
| 2 | No-op/stable rerun | 稳定项未作为可执行 import 重复出现 |
| 3 | Incremental add | `IMPORT=2`，通过 GUI Execute 导入/分类/AI/本地化路径 |
| 4 | Duplicate/existing | duplicate/existing 可见，未污染 DB truth |
| 5 | Cap-limited continuation | 2/2/1 三批，前两批 continuation 可见，最后一批结束 |
| 6 | normal `RETRY_SOURCE` | retry-only plan 可执行；retry 成功后 `retry_source_ready_for_import=1`，operator status 为 `completed_with_continuation`，下一次 Plan 显示 `IMPORT=1` |
| 7 | BROKEN_STATE | `BROKEN_STATE=1` 可见、不可执行、不消耗 actionable cap |
| 8 | Placeholder/hydration equivalent | 未触碰真实 iCloud；隔离 ledger placeholder 不可执行，恢复为可读后显示 `IMPORT=1` |
| 9 | Advanced full-rescan retry-source | UI 显示 retry debt 但高级模式不可执行；server Execute 以 `advanced_full_rescan_retry_source_execute_not_validated` 拒绝 |
| 10 | Terminal non-clean rendering | summary/report stage 非 queued，显示 terminal/warning 状态 |
| 11 | Public redaction | committed reports aggregate-only；raw traces/logs/DB evidence 未提交 |

本地最终 DB 聚合: dynamic source items 16，media-linked items 13，`imported=13`，`failed=2`，`deferred=1`，`classified=13`，`ai_tagged=13`，`localized=13`。本地失败/暂缓项仅作为隔离测试中的可见 debt/diagnostic 证据，不代表生产 Execute。

## 生产 Plan-only

生产验证通过 launcher-managed Web Admin path，仅运行 Plan-only，未点击 Execute。latest execute run id 保持 `18 -> 18`。

- plan mode: `incremental`
- max_files: 500
- selected_plan_items / plan_items: 343
- WorkItem counts: `IMPORT=312`，`FOLLOWUP=20`，`RETRY_SOURCE=11`
- lifecycle counts: `APP_MEDIA_FOLLOWUP=20`，`CONTINUATION=179`，`IMPORT_CANDIDATE=133`，`RETRYABLE_SOURCE_FAILURE=11`
- broader state counts total: 357，范围说明: 包含 selected plan items 之外的 skipped/failed diagnostics
- production Execute/import/classification/AI/localization: 未运行
- S3B automatic/scheduled/startup/service sync: disabled
- public surface: 未发现本地绝对路径、源文件名、content hash、私有 source-root 标签、DB credential、API key 或 provider secret

## 安全与范围

未运行生产 Execute；未做生产 import/classification/AI/localization；未做 source/iCloud mutation；未做 app-storage repair/mutation；未做 cleanup/reset/drop/truncate；未启动 S3B；未启动 Pixiv/provider/gallery-dl/SauceNAO/Google；未启动 SourceConcept/Entity/media_tags truth 工作。

## 验证

- Python identity: repo venv Python，Python 3.12.0，passed；exact local path 仅在交付报告中记录，不写入 public report。
- changed Python `py_compile`: passed。
- `node --check frontend/static/js/admin.js`: passed。
- `pytest tests/test_admin_dynamic_sync_ui.py -q`: 11 passed。
- `pytest tests/test_manual_sync_lifecycle.py -q`: 33 passed。
- `pytest tests/test_phase_contracts.py -q`: 289 passed。
- `pytest tests/test_s3a_m1_manual_sync_execute.py -q`: 93 passed。
- summary JSON parse: passed。
- `s3a_m2_r_operator_validation_contract_v1`: passed，error_count=0。
- `public_redaction_contract_v1`: passed，error_count=0。
- public-safe fragment scan: passed。
- isolated incremental GUI E2E: passed，12 rounds，Microsoft Edge via Playwright。
- production GUI Plan-only: passed，launcher-managed Web Admin path，latest execute id `18 -> 18`，未运行 Execute。
- post-validation server/port audit: passed，occupied_count=0。
- `git diff --check` / `git diff --cached --check`: 提交前最终运行。

## 状态判断

PR-R2 已达到 final human GUI acceptance 前的 operator-ready 状态: 本地隔离 GUI Execute、生产 GUI Plan-only、WorkItemKind-first 报告语义、public redaction 与 PR-R2 contract 都有可执行证据。剩余 out-of-scope 风险均非当前 PR 阻塞项: advanced full-rescan retry-source execution 仍明确不可执行，生产 Execute 仍需 owner 另行审批，S3B 与 provider/SourceConcept/Entity/media_tags truth 工作未启动。

合并后建议回到主线: R1R -> A1R -> Pixiv/source metadata -> SourceConcept/Entity aggregation。不要在 PR-R2 内启动这些工作。

## 工程判断 / 操作员备注

Artifact lifecycle: 前端/后端修复属于 durable production code；contract/tests 属于 reusable validation/safety tool；本报告和 summary JSON 属于 public report / handoff；本地浏览器脚本、截图、server log、DB evidence 是 ignored one-off local artifact。

阶段边界合适: 本轮只修 PR-R2 operator/proof consistency blocker，没有重写 lifecycle classifier 或 WorkItem core。已修 reviewer-relevant 当前阶段问题: retry-created pending import 不再 clean completed；生产 Plan-only 计数可解释；localization failure terminal 不再 queued；advanced-blocked retry card 不再标成 executable。延后项只属于未来阶段，不影响当前 operator correctness。
