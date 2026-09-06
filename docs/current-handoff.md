# 当前交接

<!-- GENERATED: docs/state/current-phase.json -->

当前状态以 docs/state/current-phase.json 为准。

- 阶段：`PRODUCTION-IMPORT-RECOVERY`。
- 状态：`VALIDATING_PRODUCTION_IMPORT_RECOVERY_CANDIDATE`。
- 分支：`codex/production-import-recovery`；PR：`None`。
- 已接受并合并的基线：PR #151 / `ea4bdd740943b2dad8c4eace88d0b33819d86cb8`。
- 工程目标完成：`False`；负责人复审：`pending_project_lead_review`。
- 修复生产候选：`尚未部署`。

## 当前检查点

- 已通读28号任务并记录三个连续实施步骤；A1历史证据保持不变。
- 可信fetch确认PR151已合并；main无本地独有提交，仅落后88提交，隔离main工作区以--ff-only安全同步，preflight_remote_sync=self_healed_by_fast_forward。
- 保留旧规划目录和A1生产工作区；修复分支从已接受主线建立。
- 现场profile/controller核实生产健康，仍运行A1候选，Pixiv读取开启、apply关闭。
- 26号历史覆盖补查和27号接受记录尚未在已查本机位置找到；不推定补查结论。
- 失败隔离、逐项诊断、跨cap公平轮转、历史真实尝试计数及私有恢复入口已实现；复用既有JSON账本，无新增schema。
- 提交前预验证：受影响focused 327 passed / 2 skipped，隔离PostgreSQL 80 passed；后续界面复核修正仍需最终候选验证。
- 独立有界面Edge已验证4个测试导入、1个版本排除及三个恢复动作；截图复核补上单项失败总览与下游待补做列表。

## 执行顺序

1. 单文件失败有界尝试并继续其他候选。
2. 记录私有原异常、逐项归宿和未执行身份。
3. 公平轮转到期旧重试，新项和队尾均可推进。
4. 隔离证明后部署候选，刷新现场清单并真实恢复。

## 生产保全

- 原数据库、存储、认证和日常 launcher 不迁移位置。
- 切换前核对运行任务；不终止用户导入。
- 旧运行目录和已适用备份作为恢复材料保留。
- 原图、私有路径和凭据不进入公开 PR。

## 验证范围

- focused、必要 PostgreSQL、相关契约及文档检查。
- 正常入口、新会话、有界面系统 Edge 验证。
- 不重复完整 non-E2E，不补造历史 AI 证据。

## 授权与交付边界

三个连续步骤：失败隔离与诊断；公平候选与历史补漏；隔离验证、生产部署与实际恢复。
原库/存储和正常 launcher 保持身份一致。源读取、正常水合/导入及本轮必要下游已授权；不修改源文件。
自动测试、执行代理界面验证、负责人接受、所有者使用、PR 合并和实际运行分别记账。
执行代理不合并、不推 main、不追加 reviewer；不启动 Pixiv A2/A3。

下一检查点：冻结修复候选并完成该HEAD验证，再部署日常生产入口和执行真实恢复。

## 持久入口

- [实施与恢复方案](../docs/plans/production-import-recovery.md)
- [工程与恢复报告](../docs/reports/production-import-recovery.md)
- [执行runbook](../docs/development/agent-runbook.md)
- [生产启动器](../docs/production-launcher.md)
- [阶段契约](../docs/phase-contracts.md)
- [当前主线路线](../docs/roadmap/current-mainline-roadmap.md)
