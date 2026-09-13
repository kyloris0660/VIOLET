# 当前交接

<!-- GENERATED: docs/state/current-phase.json -->

当前状态以 docs/state/current-phase.json 为准。

- 阶段：`PRODUCTION-PIXIV-A2`；状态：`PRODUCTION_PIXIV_A2_TASK43_QUALITY_BLOCKED_PENDING_PROJECT_LEAD_REVIEW`。
- 分支：`codex/production-pixiv-a2`；PR：`153`。
- 已接受并合并基线：PR #152 / `2b742ca3e49d4b7d361300e98e0b2d9c1a0eb63d`。
- 工程目标完成：`False`；负责人接受：`pending_project_lead_review`。
- 新LLM调用累计上限USD 30；既有消费不清零，原图不下载、不上传。

## 已完成检查点

- 固定T038114 Media；9502可信映射、8623完整metadata、850远端不可用、29缺页；8694获取记录复用，本轮无新metadata。
- 43号修复统一USD30账本、付费缓存/结算恢复、语义输入完整性与版本门禁、前向2秒节流、原子缓存、失败/ERROR及原始证据契约核算。
- 31607 selected/31607有效业务结果，0未补；原8769缺项=8664有效+105有据排除，新候选680，原分母和旧费用保留。
- 角色49227出现=26350候选+22642非名称+209unknown+26尝试上限、0未记账；不代表所有语义正确。
- 累计18264调用/USD13.430808，剩余16.569192，54未知usage/0在途；原6110调用/USD9.998387保持，应用gpt-4.1-mini/fallback关闭。
- 8f10418副本5索引迁移幂等、完整apply/replay/rollback/reapply、分批逆序、来源update/delete恢复和保护通过；8623 Media/66523支持，峰值17365876736 bytes。
- 实际75/80，原十例6闭合/4漏召回；另Media718属性预期与suggestion事实冲突。36分离、作者/suggestion/AND/排除及3保留样本通过。
- 来源性能p95=315.834/max=834.689ms，240HTTP/720来源测量；副本与正常旧生产各3张真实媒体及chip/旧搜索/恢复页通过，代表截图已查看。
- 639 passed/1 skipped和20 PostgreSQL passed绑定8f10418；历史4541/89/15保留，88非历史失败闭合，1项历史AI执行证据缺口不补造。
- 普通启动预检发现原候选目录漂移，已备份并固定44db0da运行目录，普通EXE停止/启动与自动preflight/健康通过；原生产8623/66572、保护不变，新版未部署。
- 完整注册契约实际失败且后续可独立门禁逐项执行；质量与新生产门禁未通过，target/safe_to_merge/route_approved均false。十例中文人工对照指南、操作恢复文档和私有审阅包已生成；上传包361164482 bytes、150505文件，完整档案726782423 bytes、150541文件，ZIP校验与10张指南图片引用通过。仅省去41个有索引的中间文件，完整档案及原失败保留。同一PR153已更新，测试/调试入口关闭，普通旧生产继续可用。

## 后续执行

1. 固定T0、兼容输入和累计所有权。
2. metadata获取、缓存复用和完整概念裁决。
3. 全量原生产落地、搜索/详情、质量与恢复验收。

## 范围和复用

- T0固定Media/work/page对应关系，T0后新增单独记账。
- 单先验不自动等同可信绑定；冲突有明确归宿。
- 真实metadata、保存payload和兼容judgments优先复用。
- 作者稳定ID保留，角色/作品所需context保留。

## 验证

- focused与隔离PostgreSQL覆盖实际变化。
- 一次完整non-E2E；历史缺失AI证据单列。
- 备份恢复、原子replacement、owned rollback及分批等价。
- 真实搜索/详情和正常launcher，记录当前全量性能。
- 工程完成由A2可执行结果契约检查。

## 操作边界

- 原生产在长时间获取和计算期间继续可用。
- 不重复导入、分类、WD标签和本地化基础工作。
- 不修改人工标签、相册、确认Entity或原文件。
- 不合并、不推main、不触发额外reviewer、不进入A3。
- 自动验证、负责人接受及产品用户体验分别记账。

下一检查点：Lead复审75/80质量阻塞：四项异名上下文/cannot约束和Media718旧预期矛盾；先保留旧生产可用，再有界修正并复验后继续已授权发布。

## 持久入口

- [A2实施方案](../docs/plans/production-pixiv-a2.md)
- [A2实际结果与质量缺项报告](../docs/reports/production-pixiv-a2-result.md)
- [执行runbook](../docs/development/agent-runbook.md)
- [生产启动器](../docs/production-launcher.md)
- [阶段契约](../docs/phase-contracts.md)
- [当前主线路线](../docs/roadmap/current-mainline-roadmap.md)
