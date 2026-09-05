# 生产 Pixiv A1 实施与迁移方案

项目所有者为 `kyloris0660`；项目负责人为本次路线规划的 ChatGPT 会话；
执行代理为当前 Codex 会话。依据项目所有者本次任务及
`17-CODEX-PRODUCTION-PIXIV-MILESTONE-1.zh-CN.md` 的明确范围执行。
起点为已合并 PR #150 的 `26a6fc8d30ba2b2eae69f55a8e7c33d5a4b9cdd3`。

1. 修复标签关联 suggestion 过滤；在来源记录与产品绑定间保存版本。
   PostgreSQL 触发器在来源身份、映射、内容、provenance 或可信状态变化的
   同一事务递增版本；搜索和详情共同比较版本。旧绑定默认失效，不能假定
   旧数据仍与当前事实一致。变化后的旧来源观察及按稳定记录键复用的支持
   在同一事务撤回；别名需保留当前有效来源，新的投影可重新生成。
2. 使用产品 run 的既有 summary JSON 保存 resolver/context/candidate/product
   版本。历史版本仅以历史实现常量重建并通过原结果指纹验证；无法验证时
   标记未知，保留 audit 和正式所有权撤回。修复提交后异常误删文件的边界，
   仅让未提交文件进入失败清理。
3. 新鲜原库只读备份、独立恢复、幂等迁移、真实 API 生命周期和故障测试通过
   后，执行固定 1%–5% 样本原库迁移与 apply，使用实际 dry-run 自动传参。
   通过既有生产 profile 部署候选，验证真实图片与 launcher 重启，关闭临时
   apply 开关，保留可用结果、旧版本恢复配置、备份和私有操作记录。

迁移为 additive：来源记录增加 `binding_revision`（初值 0），产品绑定增加
`source_revision`（历史初值 -1）；创建受影响行触发器和必要索引。
维护单行来源缓存版本，修改、删除和新增来源时在同一事务递增；查询缓存键
包含该版本，无需扫描来源内容。SQLite 标签 suggestion 默认值使用布尔表达式，
避免字符串 `false` 在隔离回归库中被误判。先在恢复库
验证重复执行、事务回退、旧版本兼容。生产不做覆盖恢复，不改人工标签、相册、
确认实体，不删原图。回退通过正式 run rollback 和保留的旧生产 profile 完成；
新列可保留，不需要破坏性降级 schema。

验证覆盖真实 API suggestion/别名/负向/AND 集合，来源变更失效与缓存，旧策略
详情和撤回，提交前后文件异常，PostgreSQL 恢复与 replay/rollback/reapply，
Edge 浏览器与真实图片、launcher 新进程，focused、完整 non-E2E 一次及契约检查。
不执行 A2/A3、新 provider/LLM、全库导入/打标签、merge、main push 或 reviewer。
