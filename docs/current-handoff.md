# 当前交接

<!-- GENERATED: docs/state/current-phase.json -->

本文件为当前状态的生成投影，不能替代当前任务授权。

## 当前任务

- 阶段：`PRODUCTION-PIXIV-A1`。
- 状态：`A1_COPY_AND_CODE_VERIFIED_PREPARING_PRODUCTION`。
- 分支：`codex/production-pixiv-a1`。
- PR：`None`。
- 起点：`26a6fc8d30ba2b2eae69f55a8e7c33d5a4b9cdd3`。
- PR #150 已合并，历史恢复副本证据保留。
- 目标完成：`False`。
- 项目负责人复审：待完成。
- 产品用户亲自浏览：未据此任务执行作出声明。

## 实施顺序

1. 修复搜索、绑定版本、历史策略和提交后文件清理。
2. 新鲜备份、独立恢复、迁移与撤回排练。
3. 固定样本原库落地、真实图片和 launcher 验收。

## 证据边界

- 工程验证不等于 PR 合并。
- 执行代理浏览器验证不等于项目负责人接受或产品用户亲自验收。
- 恢复副本成功不等于原库成功。
- 当前确定性 resolver 不包含 A2 完整模型裁决。
- 本地测试不等于 GitHub CI。
- 所有真实操作保存在本机私有记录。

## 当前检查点

- 可信 origin/main 与已合并 PR #150 已核实。
- 保留原目录，建立 A1 隔离分支。
- 实施与增量迁移方案已记录；代码修复进行中。
- 新鲜原库只读备份及独立 PostgreSQL 恢复完成，54 张表计数一致
- 恢复副本四项增量迁移重复执行通过
- 最终 1% 选择：5 作品、5 页面、5 Media；实际 API 生命周期、51 条绑定和按归属撤回已验证
- 执行代理依据项目所有者授权完成副本操作；项目负责人复审仍待进行
- 最终代码副本 fresh-session 搜索/详情复核通过，原有 51 条绑定保持一致
- PostgreSQL 回归 37 通过；focused 的状态文案断言已修正并补跑通过
- 完整 non-E2E 原始结果 4382 通过、62 失败、15 跳过；失败已定位并针对性补跑，保留历史缺失 AI 证据失败
- 240-query、720 次测量结果集保持一致；p50 6.51 ms、p95 33.377 ms、最大 72.985 ms

## 运行状态

- 生产候选：`尚未部署`。
- 下一检查点：固定候选提交，执行原库增量迁移、实际 API 生命周期和启动器重启验证。

## 授权和保全

- 执行代理依据项目所有者授权自动传递真实 dry-run 校验值。
- 必要增量迁移与可撤销样本 apply 已授权。
- 原图、人工标签、相册、确认实体保留。
- 不覆盖恢复原库，不清理无关工作区。
- 不运行新 provider、LLM 或全库导入。
- 不 merge、不推送 main、不额外触发 reviewer。
- A2 和 A3 未执行。

## 持久入口

- [A1 实施与迁移方案](../docs/plans/production-pixiv-a1.md)
- [执行 runbook](../docs/development/agent-runbook.md)
- [生产启动器](../docs/production-launcher.md)
- [来源搜索语义](../docs/source-concept-tag-search-semantics.md)
- [阶段契约](../docs/phase-contracts.md)
- [当前主线路线](../docs/roadmap/current-mainline-roadmap.md)

## 后续边界

- A2 补齐缺失 metadata、兼容 judgments 与全量选择所有权。
- A3 接通持续同步与队列。
- 工作区对抗、多 worker 和远程证据能力不在本轮扩展。
- 更新：`2026-09-05T17:37:59.122221+00:00`。
