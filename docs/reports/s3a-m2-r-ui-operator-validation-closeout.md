# S3A-M2-R PR-R2: 操作员 UI、进度与验证收口

## PR / 分支 / Head

- PR: #129
- 分支: `codex/s3a-m2-r-ui-operator-validation-r2`
- 本轮 post-human-acceptance UI polish 基准 head: `cabb0b019153e5660f084ef6010b323f31301ba6`
- 最终 pushed head: 提交后记录在 PR body 和交付报告中；提交文件无法自引用自身生成后的 Git SHA。
- 状态: final PR-R2 post-human-acceptance confirmation panel placement polish；不合并，不开新 PR。

## 最终人工验收结果

owner 在页面级确认修复后完成了生产 GUI 人工验收。验收结果通过: normal `Start manual sync` 可以完成 Plan，页面级确认出现且不再依赖 browser-native confirm，页面级确认可以执行当前 Plan，Execute 正常完成；owner 抽查 imported media、classification、AI tags 和 localization 行为，结果看起来正确。非干净/debt 状态仍然可见，并且没有被声明为 clean full-chain completion。

owner 后续为了截图又跑了一次 Plan/Execute，页面级确认再次正常工作。本次 Codex closeout 没有运行生产 Execute；生产 Execute 仅由 owner 在人工验收中执行。

## 睡眠中断恢复

恢复后先做了本地状态审计。当前分支仍是 `codex/s3a-m2-r-ui-operator-validation-r2`，HEAD 为 `8844f0eeee31bdb6e686a25c07f40c7491279d97`；tracked diff 只有 `frontend/static/js/admin.js`、`frontend/templates/admin.html`、`tests/test_admin_dynamic_sync_ui.py`。报告文件没有被上一次失败脚本部分写入。

进程审计显示没有遗留 8013 test server 或 8025 fake LLM；只剩既有 launcher-managed production Web Admin listener on 8012，`safe_to_stop=false`，未触碰。恢复过程中第一轮新建的本地 ignored artifact 因 test env 未显式打开 `DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED` / `DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED`，UI 正确保持 Start disabled；我只停止了该轮自己启动的 8013/8025 精确 PID，没有删除生产数据、没有清理 DB、没有动 source/iCloud/app storage。最终有效 E2E 证据来自 ignored artifact marker `ignored-local-pr129-incremental-gui-e2e-20260706-012552`。

## 人工验收失败说明

owner 在生产 launcher/Web Admin 正常 `Start manual sync` 路径上看到 Plan 成功完成且计划可执行，但没有出现可见确认，也没有进入 Execute。证据显示失败尝试没有创建 Execute run，后端安全是 fail-closed；问题在前端确认交接: 长时间 Plan 后依赖 browser-native `window.confirm`，一旦确认框未显示、被压制或不可见，UI 会落到“manual sync not executed”式的不可恢复观感。

## 本轮修复

- 正常 Start flow 改为 `Start manual sync -> Plan -> 页面级 awaiting confirmation -> 页面级 Confirm Execute`。
- Plan 完成且可执行后，页面显示计划 hash 前缀、安全 root marker、selected plan item count、IMPORT/FOLLOWUP/RETRY_SOURCE、不可执行诊断计数、生产写入警告和操作员确认声明。
- 页面级 Confirm Execute 复用当前 Plan，不重新运行 Plan；Execute request in flight 时禁用重复提交。
- 取消/关闭确认只表示暂不 Execute，不创建 Execute，当前 Plan 仍可见且可继续确认或重跑。
- 过期 Plan 禁用 Execute 并要求重新 Plan。
- 正常 Start 不再只依赖 `window.confirm`；advanced exact-phrase Execute 控制保留。
- post-human-acceptance 小修复: 页面级确认面板从长 Plan detail/result block 下方移动到其上方，并在进入 awaiting confirmation 时自动滚动到面板位置，避免操作员必须向下找确认按钮。

## UI 进度、心跳与确认

Web Admin Plan/Execute 仍显示 phase、status、elapsed、last heartbeat/update、当前安全标签、粗粒度计数、终态和错误信息。Execute 确认后立即进入非空 pending/executing 过渡态并禁用重复提交；run id 一旦后端返回即显示；真实 worker heartbeat 到达前显示 starting/waiting 状态，不伪造 import/classification/AI/localization 细粒度进度。

Round 1 最新证据: delayed Plan first attempt=true，page confirmation visible before execute=true，native dialog seen=false，cancel kept plan visible=true，page confirm button still available=true，Execute first response 有 `run_created_at` 且无真实 `last_heartbeat_at`，pending placeholder visible=true，duplicate submit disabled=true。

post-human-acceptance 轻量浏览器证据: Microsoft Edge via Playwright 在 isolated local/test server 上点击 normal Start 并完成 Plan；页面级确认 DOM 顺序为 progress/stage strip -> page-level confirmation -> detailed Plan result；确认面板在 1366x768 viewport 中无需手动滚动即可可见，rect top=212、bottom=556；Confirm Execute 与 Cancel 按钮均可见，Cancel 后 Plan 保持 recoverable；未出现 native dialog，未发送 Execute POST。

## Operator Readiness 与 Full Chain

- `operator_ready`: true。操作员路径可以计划、理解、确认、监控并验证手动同步；非干净状态可见且不被 clean claim 覆盖。
- `full_chain_complete`: false。最新 isolated matrix 的正常导入轮次可以 clean completed，但同一 acceptance matrix 故意保留 source-missing、placeholder/deferred、BROKEN_STATE/advanced retry-source diagnostic evidence；final DB truth 仍有 failed/deferred 状态。
- `full_s3a_m2_r_complete`: false。PR-R2 当前证明 operator-ready-with-visible-debt，不声明 clean full-chain completion。
- `target_met/safe_to_merge` scope: `operator_ready_visible_non_clean_debt`。

## 标签与 WorkItem 语义

operator status、WorkItemKind 和 lifecycle/debt 的中文标签可读且不是问号占位。`completed_with_continuation` 显示为“已完成当前批次：还有下一批或源文件重试恢复后的导入需要继续计划”。normal incremental `IMPORT/FOLLOWUP/RETRY_SOURCE` 是 actionable；advanced full-rescan 被阻断的 `RETRY_SOURCE` 显示为 retry debt 但当前模式不可执行；`BROKEN_STATE/PLACEHOLDER/NOOP_DIAGNOSTIC` 可见但不可执行。

报告和 validator 以 `work_item_kind` 为准: IMPORT count 来自 `IMPORT`，retry count 来自 `RETRY_SOURCE`，legacy `state` 不覆盖 WorkItemKind。`NOOP_DIAGNOSTIC` 与 `PLACEHOLDER` 不显示为可执行工作；`BROKEN_STATE` 保持可见但不可执行；source-missing retry debt 保持可见。

## 浏览器 / 本地 GUI E2E

- 浏览器: Microsoft Edge via Playwright
- 环境: 隔离 local/test profile，test DB，repo-local ignored storage，本地生成图片 fixture，本地 fake LLM；manual sync execute 仅在 test server env 显式打开
- 入口: 真实 Web Admin UI；Plan 均由页面按钮触发；可执行场景的 Execute 均由页面级确认按钮触发
- 结果: 完整 12 轮矩阵 passed；Codex 启动的 test server 与 fake LLM 已停止；既有 production launcher server 未触碰

| Round | 场景 | 真实浏览器点击 | 控制 DB seed/test hook | 结果 |
|---:|---|---|---|---|
| 0 | Empty baseline Plan | 是 | 否 | plan items 0；不可执行；无公开路径泄漏 |
| 1 | Initial import + delayed normal Start confirmation | 是 | 是 | `IMPORT=3`；延迟 Plan 后页面确认首次出现；取消不创建 Execute 且保留 Plan；页面确认后 Execute 当前 Plan；native dialog seen=false；终态 `completed` |
| 2 | No-op stable rerun | 是 | 否 | 稳定项未作为可执行 import 重复出现 |
| 3 | Incremental add via GUI | 是 | 是 | `IMPORT=2`；页面确认 Execute；终态可见 |
| 4 | Duplicate/existing media | 是 | 是 | duplicate/existing 可见，未污染 DB truth |
| 5 | Cap-limited continuation | 是 | 是 | 2/2/1 三批 continuation；每批 cap/remaining 可见 |
| 6 | RETRY_SOURCE normal incremental plan | 是 | 是 | retry-only plan 可执行；`retry_source_ready_for_import=1` -> `completed_with_continuation`；UI `待下一次导入=1`；下一次 Plan 显示 `IMPORT=1`；source-missing debt 保持可见 |
| 7 | BROKEN_STATE missing app media diagnostic | 是 | 是 | `BROKEN_STATE=1` 可见、不可执行、不消耗 actionable cap |
| 8 | Placeholder/deferred and hydration equivalent | 是 | 是 | 未触碰真实 iCloud；隔离 ledger placeholder 不可执行，恢复为可读后显示 import-ready |
| 9 | Advanced full-rescan retry-source fail-closed | 是 | 是 | advanced full-rescan retry debt 可见但不可执行；server Execute 409 `advanced_full_rescan_retry_source_execute_not_validated` |
| 10 | Terminal non-clean stage rendering | 是 | 是 | `completed_with_localization_failures`/terminal warning 不渲染为 queued；`skipped_*_run` 显示为已跳过/已停止 |
| 11 | Public redaction / artifact safety | 否 | 否 | 公开报告 aggregate-only；raw traces/logs/DB evidence 未提交 |

本地最终 DB 聚合: dynamic source items 16，media-linked items 13，`import_status={'deferred': 1, 'failed': 2, 'imported': 13}`，`classification_status={'classified': 13, 'deferred': 3}`，`ai_tagging_status={'ai_tagged': 13, 'deferred': 3}`，`localization_status={'blocked_import_failed': 2, 'deferred': 1, 'localized': 13}`。这些 failed/deferred 是隔离测试中有意保留的 operator-visible debt/diagnostic 证据，因此 full-chain completion 保持 false。

## 生产 Plan-only

生产验证通过 launcher-managed Web Admin path，仅运行正常 Start 的 Plan-only；页面级确认出现后未点击 Execute。latest execute run id 保持 `19 -> 19`。

- normal Start clicked: true
- page-level confirmation visible: true
- browser-native dialog seen: false
- Execute POST seen: false
- plan mode: `incremental`
- max_files: 500
- selected_plan_items / plan_items: 255
- WorkItem counts: `IMPORT=235`，`FOLLOWUP=0`，`RETRY_SOURCE=20`，`BROKEN_STATE=0`，`PLACEHOLDER=0`，`NOOP_DIAGNOSTIC=0`
- lifecycle counts: `APP_MEDIA_FOLLOWUP=0`，`CONTINUATION=170`，`IMPORT_CANDIDATE=65`，`RETRYABLE_SOURCE_FAILURE=20`
- broader state counts total: 269；范围说明是 broader planner state rows including skipped/failed diagnostics outside selected plan items
- selected production source-root label: `[redacted]`
- production Execute/import/classification/AI/localization: 未运行
- S3B automatic/scheduled/startup/service sync: disabled
- public surface: 未发现本地绝对路径、源文件名、content hash、私有 source-root 标签、DB credential、API key 或 provider secret

## 安全与范围

Codex 本次 closeout 未运行生产 Execute，未运行生产 import/classification/AI/localization，未做 source/iCloud mutation，未做 app-storage repair/mutation，未做 cleanup/reset/drop/truncate。生产 Execute 仅由 owner 在 final human GUI acceptance 中执行。Codex 未启动 S3B，未启动 Pixiv/provider/gallery-dl/SauceNAO/Google，未启动 SourceConcept/Entity/media_tags truth 工作。

## 验证

- Python identity: repo venv Python，Python 3.12.0，passed；exact local path 仅在交付报告中记录，不写入 public report。
- changed Python `py_compile`: passed (`tests/test_admin_dynamic_sync_ui.py`)。
- `node --check frontend/static/js/admin.js`: passed。
- `pytest tests/test_admin_dynamic_sync_ui.py -q`: 13 passed。
- `pytest tests/test_phase_contracts.py -q`: 293 passed。
- `pytest tests/test_s3a_m1_manual_sync_execute.py -q`: 93 passed。
- isolated incremental GUI E2E: passed，12 rounds，Microsoft Edge via Playwright，包含延迟 Plan 后首次页面级确认、取消恢复、确认当前 Plan 执行、禁用重复提交、无 native dialog 依赖。
- post-human-acceptance confirmation panel placement browser check: passed，Microsoft Edge via Playwright，isolated local/test normal Start Plan-only 布局检查；面板无需手动滚动即可可见，Confirm/Cancel 可见，Cancel 可恢复，未发送 Execute POST，未出现 native dialog。
- production GUI Plan-only: passed，launcher-managed Web Admin normal Start path，latest execute id `19 -> 19`，页面级确认出现，未运行 Execute。
- post-validation server/port audit: passed；本轮 Codex 启动的 8013 local/test server 已停止；最终审计显示 8000/8012-8024 无活跃 listener。
- summary JSON parse: passed。
- `s3a_m2_r_operator_validation_contract_v1`: passed；状态为 `operator_ready`，且 full-chain completion 未被 overclaim。
- `public_redaction_contract_v1`: passed。
- public-safe fragment scan: passed；公开报告无本地绝对路径、原始源文件名、content hash、生产 source-root label、credential/API key、provider secret 或未脱敏 URL。
- `git diff --check`: 报告更新后最终运行并通过；`git diff --cached --check` 在最终 staging 后由交付报告记录。

## 状态判断

PR-R2 已通过 final human GUI acceptance，并完成最后的确认面板位置微调。当前状态为 operator-ready 且可进入 merge decision: 正常 Start 不再依赖不可持久的 browser-native confirm；Plan 完成会在靠近结果区域顶部留下可恢复、可确认、可取消、可过期保护的页面级确认状态；只有 operator 点击页面级确认后才会 Execute。

PR-R2 没有证明 clean full-chain completion；`full_chain_complete=false` 与 `full_s3a_m2_r_complete=false` 是有意的 truthfulness 边界。剩余 out-of-scope 风险均非当前 PR 阻塞项: advanced full-rescan retry-source execution 仍明确不可执行，生产 Execute 仍需 owner 另行审批，S3B 与 provider/SourceConcept/Entity/media_tags truth 工作未启动。

## 工程判断 / 操作员备注

Artifact lifecycle: 前端修复属于 durable production code；测试/contract 属于 reusable validation/safety tool；本报告和 summary JSON 属于 public report / handoff；本地浏览器脚本、截图、server log、DB evidence 是 ignored one-off local artifact。

阶段边界合适: 本轮只修 PR-R2 manual sync operator confirmation handoff，没有改 lifecycle classifier 或 WorkItem core。根因是正常生产 Start 把长耗时 Plan 之后的关键写入确认交给了 transient native dialog；安全层 fail-closed，但 operator UX 不可恢复。修复把确认状态变成页面内持久、可检查、可取消、可继续的显式状态。

PR-R2 ready to merge: 是，按 operator-ready-with-visible-debt 语义；不是 clean full-chain completion。
