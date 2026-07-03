# S3A-M2-R PR-R2：操作员 UI、进度与验证收口

## 结论

PR-R2 已把 S3A-M2-R 收口到操作员可用状态：用户可以通过 Web Admin 路径规划、理解、监控并验证手动同步。此报告只记录聚合证据；不包含本地绝对路径、源文件名、content hash、密钥或私有 provider 配置。

本阶段没有启动 S3B，没有回到 Pixiv、SourceConcept 或 Entity 工作，也没有运行生产 Execute。

## 本阶段实现

- Web Admin 手动同步 Plan/Execute 增加可见进度、阶段 strip、elapsed/heartbeat、当前安全标签、粗粒度计数、终态和错误显示。
- Execute 确认后立即进入“提交/验证/创建执行 run/等待首个后端心跳”的过渡态，并禁用重复提交。
- 导入、分类、AI 标签、本地化、摘要/报告阶段都写入可见阶段状态；有精确项时显示当前安全标签，没有时只显示可靠粗粒度进度。
- 新增中文操作员标签 catalog，覆盖 operator status、WorkItemKind、lifecycle/debt 类别。
- UI/报告按 WorkItemKind-first 解释导入、retry、follow-up、broken、placeholder 与 no-op diagnostic，不再让 legacy state 覆盖 WorkItemKind。
- 高级 full-rescan 若出现 retry-source work，会被标记为未验证不可执行；不会绕过 max_files 进入 retry-source 执行。
- 新增 `s3a_m2_r_operator_validation_contract_v1`，防止在浏览器、本地 GUI、生产 Plan-only、redaction 或安全门缺失时过度声称完成。

## 范围边界

未实现、未启动、未验证以下内容：

- S3B 自动、计划、启动、服务或无人值守同步。
- Pixiv、provider、gallery-dl、SauceNAO、Google、SourceConcept、Entity bridge。
- confirmed assignment 或 media_tags truth promotion。
- 大规模生产导入。
- 通用审计平台或生命周期分类器重写。

## 进度与心跳

Plan 流程显示规划阶段、请求 id、elapsed、heartbeat、当前安全标签、已见元数据、批次候选、失败和事件。

Execute 流程在确认点击后立即显示过渡态：请求已提交、验证计划和确认、创建 execute run、准备执行、等待首个后端心跳。后端 run id 返回后立即显示 run id；真实阶段进度到达前不会伪造导入、分类、AI 或本地化的细项进度。

执行阶段会依次显示导入/重试、分类、AI 标签、本地化、摘要/报告状态。终态显示 operator status、WorkItem 汇总、结果拆解和错误码/消息。

## 中文标签映射

Operator status：

- `completed`：已完成：本批次没有剩余操作员动作。
- `completed_with_retryable_failures`：已完成但有可重试源文件债务：稍后可重试源文件读取。
- `completed_with_followup_required`：已完成但需要后续补处理：分类、AI 标签或本地化仍有未完成项。
- `completed_with_continuation`：已完成当前批次：还有下一批需要继续计划。
- `completed_with_retryable_failures_plus_continuation`：已完成当前批次：同时有可重试源文件债务和后续批次。
- `failed_systemic`：系统性失败：需要先排查环境或流程问题。
- `blocked_preflight`：预检阻断：尚未进入执行。
- `cancelled`：已取消：已提交的结果保留，剩余项目需要重新计划。

WorkItemKind：

- `IMPORT`：导入新媒体。
- `FOLLOWUP`：应用媒体后续补处理。
- `RETRY_SOURCE`：重试源文件读取。
- `BROKEN_STATE`：状态异常诊断。
- `PLACEHOLDER`：云占位/暂缓项目。
- `NOOP_DIAGNOSTIC`：无需执行的诊断项。

Lifecycle/debt 类别：

- `APP_MEDIA_FOLLOWUP`：应用内媒体需要补处理。
- `IMPORT_CANDIDATE`：可导入候选。
- `RETRYABLE_SOURCE_FAILURE`：源文件读取可重试失败。
- `PLACEHOLDER_DEFERRED`：云占位暂缓。
- `STABLE_NOOP`：稳定无操作。
- `HISTORICAL_DIAGNOSTIC`：历史诊断记录。
- `CONTINUATION`：批次续跑。
- `BROKEN_STATE`：状态异常。
- `FATAL_BLOCKER`：致命阻断。

## WorkItemKind-first 报告语义

- import count 来自 `work_item_kind == IMPORT`。
- retry count 来自 `work_item_kind == RETRY_SOURCE`。
- `NOOP_DIAGNOSTIC` 和 `PLACEHOLDER` 不显示为可执行工作。
- broken diagnostics 可见但不可执行。
- 成功恢复的 retry 会显示为 pending import work，不被当成 clean completed 隐藏。
- 源不可读/缺失类 retry debt 保持可见。
- 缺 Media row 或缺 app-managed file 保持为 broken diagnostic 或等价异常，不被吞成 stable no-op。

## 浏览器验证

真实浏览器验证通过，使用 Microsoft Edge + Playwright 对 Web Admin 手动同步流程进行验证。覆盖：

- Plan 从 Web Admin 路径开始，并显示进度。
- Execute 确认后立即出现非空 pending/executing 过渡态。
- Execute 请求在首个后端心跳前保持 elapsed/等待心跳提示。
- 重复 Execute 提交被禁用。
- run id、终态、阶段 strip、WorkItem 汇总和错误显示区域可见。
- UI/report public surface 未泄漏私有本地路径或源文件名。

## 本地图像 GUI 验收

本地/test profile GUI 验收通过。覆盖聚合场景：

- 新图像导入候选：3。
- 已存在或重复媒体：1。
- app-media follow-up：1。
- broken diagnostic：1。
- continuation/cap-limited batch：可见。
- retry-source debt Plan-only：1。
- 历史 retry 恢复后显示 pending import：1。

Execute 只在 local/test profile 运行。执行结果：导入 2，已有/重复 1，分类 3，AI 标签 1，本地化 1，失败 0，operator status 为 `completed`。

## 生产 Plan-only

生产 Web Admin Plan-only 验收通过。该步骤通过生产 launcher 启动 Web Admin，仅运行正常增量 Plan-only，未运行 Execute。

聚合结果：

- max_files：500。
- estimated import：244。
- app-media follow-up：20。
- retry-source debt：11。
- continuation：false。
- cap-limited batch：false。

生产 Execute 未运行，因此没有生产导入、生产分类、生产 AI 标签或生产本地化。

## 公共安全

Public report 和 summary JSON 只保留聚合计数、状态、布尔证明和相对文档路径。不写本地绝对路径、源文件名、content hash、source-root 私有标签、密钥或 provider secret。

本地浏览器/验收原始证据仅作为 ignored 本地 artifact 保留，未提交。

## 合同与测试

需要通过的主要检查：

- Python identity。
- changed Python files `py_compile`。
- focused backend/frontend/static tests。
- browser validation。
- local-image GUI acceptance。
- production GUI Plan-only acceptance。
- `pytest tests/test_phase_contracts.py -q`。
- `s3a_m2_r_operator_validation_contract_v1`。
- `public_redaction_contract_v1`。
- summary JSON parse。
- `git diff --check` 与 `git diff --cached --check`。

最终命令结果以交付报告为准。

## 当前状态

S3A-M2-R 可以声明 operator-ready：本地 GUI Execute、生产 GUI Plan-only、PR-R2 合同和 public redaction 均有对应门控。剩余风险不阻塞当前 PR-R2：

- 高级 full-rescan 的 retry-source 执行仍按“未验证不可执行”处理，后续如要启用，需要单独阶段验证。
- 生产 Execute 仍需要 owner 在合并后按计划手动批准和运行；本 PR 没有越权执行。

## 推荐下一步

PR-R2 合并后，可以回到主线：R1R -> A1R -> Pixiv/source metadata -> SourceConcept/Entity aggregation。不要在 PR-R2 内启动这些工作。

## 工程判断 / 操作员备注

Artifact lifecycle：

- `docs/reports/s3a-m2-r-ui-operator-validation-closeout.md`：Public report / handoff / roadmap update。
- `docs/reports/s3a-m2-r-ui-operator-validation-summary.json`：Public report / handoff / roadmap update。
- 本地浏览器和验收证据：One-off local artifact / ignored output，未提交。

阶段边界合适：PR-R2 专注 operator UI、进度、WorkItemKind-first 报告语义和验收合同，没有扩展到 S3B、provider 或 Entity。

已修复/覆盖的 reviewer debt：高级 full-rescan retry-source 不可绕过 cap；retry 恢复后 pending import 可见；retry debt 和 broken diagnostics 可见；missing app-media 类异常不再隐藏为 stable no-op。

有意不改：生命周期分类器和 WorkItem core 没有重写；只在 PR-R2 operator correctness 需要的地方补 UI、摘要、validator/report 和合同门控。
