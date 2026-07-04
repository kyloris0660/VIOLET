# S3A-M2-R PR-R2：操作员 UI、进度与验证收口

## PR 与边界

- PR：#129
- 分支：`codex/s3a-m2-r-ui-operator-validation-r2`
- 输入 head：`bf7abb9f00347dbd64fc930a2952ec919b3ce6b9`
- 最终 pushed head：提交后记录在 PR body 和交付报告中；提交文件无法自包含自身生成后的 Git SHA。

PR-R2 只做操作员验证正确性收口：Web Admin 手动同步进度、Execute 过渡态、WorkItemKind-first 汇总、中文操作员标签、validator/report/contract 证明。本阶段没有启动 S3B，没有回到 Pixiv、provider、gallery-dl、SauceNAO、Google、SourceConcept、Entity 或 media_tags truth 工作，也没有运行生产 Execute。

## 本轮四项修复

- closeout 与 summary 的本地 GUI、生产 Plan-only 数字已统一到最新证据：本地以增量 GUI E2E 矩阵为准；生产 Plan-only 为 `IMPORT=312`、`FOLLOWUP=20`、`RETRY_SOURCE=11`。
- 普通 Web Admin Start/Execute 使用一致的 actionable 定义：`IMPORT + FOLLOWUP + RETRY_SOURCE`。`BROKEN_STATE`、`PLACEHOLDER`、`NOOP_DIAGNOSTIC` 不可执行；advanced full-rescan 的 retry-source 仍不可执行。
- stage strip 已把 `completed_with_failures`、`completed_with_retryable_failures`、`completed_with_followup_required`、`completed_with_continuation`、`completed_with_retryable_failures_plus_continuation` 视为终态或警告终态，不再渲染成 queued。
- `s3a_m2_r_operator_validation_contract_v1` 同时检查 `production_execute.ran` 与 `safety.production_execute_ran`，并要求两者一致；任一字段显示生产 Execute 已运行时必须有 owner approval 与引用或说明。

## 进度与心跳

Plan 显示当前 phase、status、elapsed、heartbeat、当前安全标签、粗粒度计数、终态和错误信息。Execute 确认后立即进入可见过渡态：请求已提交、验证计划和确认、创建 execute run、准备执行、等待首个真实后端 worker 心跳。run id 一旦后端返回就显示；真实阶段进度到达前不伪造导入、分类、AI 标签、本地化或摘要细项进度。

queued/pending 阶段保持等待态；只有当前 worker 阶段渲染为 running。非干净但已完成的 summary 阶段渲染为 completed 或 warning terminal，不再显示成 queued。

## 中文标签映射

Operator status：`completed`=已完成；`completed_with_retryable_failures`=已完成但有可重试源文件债务；`completed_with_followup_required`=已完成但需要后续补处理；`completed_with_continuation`=当前批次完成且还有下一批；`completed_with_retryable_failures_plus_continuation`=当前批次完成且同时有 retry debt 与 continuation；`failed_systemic`=系统性失败；`blocked_preflight`=预检阻断；`cancelled`=已取消。

WorkItemKind：`IMPORT`=导入新媒体；`FOLLOWUP`=应用内媒体后续补处理；`RETRY_SOURCE`=重试源文件读取；`BROKEN_STATE`=状态异常诊断；`PLACEHOLDER`=占位或暂缓项目；`NOOP_DIAGNOSTIC`=无需执行的诊断项。

Lifecycle/debt：`APP_MEDIA_FOLLOWUP`=应用内媒体补处理；`IMPORT_CANDIDATE`=可导入候选；`RETRYABLE_SOURCE_FAILURE`=可重试源文件失败；`PLACEHOLDER_DEFERRED`=占位暂缓；`STABLE_NOOP`=稳定无操作；`HISTORICAL_DIAGNOSTIC`=历史诊断；`CONTINUATION`=续跑批次；`BROKEN_STATE`=状态异常；`FATAL_BLOCKER`=致命阻断。

## WorkItemKind-first 报告语义

import count 来自 `work_item_kind == IMPORT`；retry count 来自 `work_item_kind == RETRY_SOURCE`；legacy `state` 不覆盖 WorkItemKind。`NOOP_DIAGNOSTIC` 与 `PLACEHOLDER` 不作为可执行工作；`BROKEN_STATE` 可见但不可执行；成功的 `RETRY_SOURCE` 若产生下一轮 import work，会在下一次 Plan 中显示为 pending import；source-missing retry debt 保持可见，不会被隐藏成 clean completed。

## 本地隔离增量 GUI E2E

浏览器：Microsoft Edge + Playwright。环境：隔离 local/test profile、生成图像夹具、独立测试端口、真实 Web Admin UI。Plan 均通过 Web Admin 点击触发；Execute 场景均通过真实确认点击触发。验证结束后已停止测试 server 与 fake local LLM，并完成端口审计，结果 clean。

| Round | 场景 | UI 点击 | 受控 seeding/test hook | 聚合结果 |
|---:|---|---|---|---|
| 0 | Empty baseline Plan | 是 | 否 | plan items 0；不可执行；无私有路径泄漏 |
| 1 | Initial import via GUI | 是 | downstream 完成标记 | `IMPORT=3`；Execute 后 new items 3；立即 pending 可见；重复提交禁用；run id 可见；`run_created_at` 早于真实 heartbeat；终态 `completed_with_failures` |
| 2 | No-op stable rerun | 是 | 否 | plan items 0；稳定已导入项目未重新出现为 import work |
| 3 | Incremental add | 是 | downstream 完成标记 | `IMPORT=2`；Execute 后 new items 2；旧媒体未重复导入 |
| 4 | Duplicate/existing media | 是 | 否 | Plan 显示 1 个候选；Execute 后 new items 0；未污染 DB truth |
| 5 | Cap-limited continuation | 是 | downstream 完成标记 | 三批：2、2、1；前两批 cap-limited 且 more_batches=true；第三批结束 continuation |
| 6 | RETRY_SOURCE normal incremental | 是 | retry debt 与 source-missing debt seeding | retry-only normal incremental plan 为 `RETRY_SOURCE=1` 且可执行；retry 成功后下一次 Plan 显示 `IMPORT=1`；source-missing debt 仍可见且不执行 |
| 7 | BROKEN_STATE missing app media | 是 | broken diagnostic seeding | `BROKEN_STATE=1`；不可执行；不消耗 actionable cap；核心状态未被 Execute 改写 |
| 8 | Placeholder/hydration equivalent | 是 | placeholder 与 hydration equivalent seeding | 未触碰真实 iCloud；占位态不可执行；恢复为可读后 Plan 显示 `IMPORT=1` 加剩余 diagnostic |
| 9 | Advanced full-rescan retry-source fail-closed | 是 | bounded local negative fixture | UI 标记不可执行；server Execute 以 `advanced_full_rescan_retry_source_execute_not_validated` 拒绝，即使 plan hash 与确认语有效 |
| 10 | Terminal non-clean stage rendering | 是 | terminal non-clean run seeding | summary/report stage 不再 queued，显示为终态/警告终态 |
| 11 | Public redaction/artifact safety | N/A | N/A | committed reports aggregate-only；raw browser/DB/log evidence 未提交 |

本地最终 DB truth 聚合：dynamic source items 15；media-linked items 13；`imported=13`、`failed=1`、`deferred=1`；`classified=13`、`ai_tagged=13`、`localized=13`。本地失败/暂缓只作为隔离测试中的可见 debt/diagnostic 证明，不代表生产 Execute。

## 生产 Plan-only

生产验证通过 launcher-managed Web Admin path，仅运行 Plan-only，没有点击 Execute。latest execute run id 保持 `18 -> 18`，证明生产 Execute 未运行。

- plan mode：normal incremental。
- max_files：500。
- plan items：343。
- WorkItem counts：`IMPORT=312`、`FOLLOWUP=20`、`RETRY_SOURCE=11`。
- lifecycle 聚合：`APP_MEDIA_FOLLOWUP=20`、`CONTINUATION=179`、`IMPORT_CANDIDATE=133`、`RETRYABLE_SOURCE_FAILURE=11`。
- cap-limited：false；more batches：false。
- S3B automatic/scheduled/startup/service sync：disabled。
- 生产 import/classification/AI/localization：未运行。
- public surface：未发现本地绝对路径、源文件名、content hash、source-root 私有标签、密钥或 provider secret。

## Public Safety

公开 markdown 与 summary JSON 只保留聚合计数、状态、布尔证明和 repo-relative 报告路径。浏览器 trace、server log、原始 DB 证据、生成测试图片和本地验证脚本均为 ignored local artifact，未提交。

生产 Execute 未运行；没有生产 import、classification、AI tagging、localization；没有 source/iCloud mutation；没有 app-storage repair/mutation；没有 cleanup/reset/drop/truncate；没有 provider/Pixiv/SourceConcept/Entity/media_tags truth 工作。

## 验证结果

- Python identity：repo venv Python 3.12.0。
- `py_compile` changed Python files：passed。
- `node --check frontend/static/js/admin.js`：passed。
- `pytest tests/test_admin_dynamic_sync_ui.py -q`：10 passed。
- `pytest tests/test_phase_contracts.py -q`：288 passed。
- `pytest tests/test_s3a_m2_delta_e2e.py -q`：30 passed。
- `pytest tests/test_s3a_m1_manual_sync_execute.py -q`：93 passed。
- `pytest tests/test_dynamic_library_sync.py -q`：86 passed。
- `pytest tests/test_manual_sync_lifecycle.py -q`：33 passed。
- isolated incremental GUI E2E browser validation：passed。
- production GUI Plan-only validation：passed；Execute 未运行。
- `s3a_m2_r_operator_validation_contract_v1`、`public_redaction_contract_v1`、summary JSON parse、`git diff --check`、`git diff --cached --check`：提交前最终重跑。

## 状态判断

PR-R2 已达到 final human GUI acceptance 前的 operator-ready 状态：本地隔离 GUI Execute、生产 GUI Plan-only、WorkItemKind-first 报告语义、public redaction 与 PR-R2 contract 都有可执行证据。剩余 out-of-scope 项目不阻塞当前 PR：advanced full-rescan retry-source execution 仍明确不可执行，生产 Execute 仍需 owner 另行审批，S3B 与 provider/SourceConcept/Entity/media_tags truth 工作未启动。

合并后建议回到主线：R1R -> A1R -> Pixiv/source metadata -> SourceConcept/Entity aggregation。不要在 PR-R2 内启动这些工作。

## 工程判断 / 操作员备注

Artifact lifecycle：前端与合同/测试改动是 durable production code / reusable validation safety tool；两个 report 是 public report / handoff；本地 E2E 证据是 ignored one-off artifact。

阶段边界合适：本轮只修正 PR-R2 operator correctness blocker，没有重写 lifecycle classifier 或 WorkItem core。风险剩余为未来阶段风险：advanced full-rescan retry-source execution 未验证不可执行，生产 Execute 未运行且必须继续 owner-approved。
