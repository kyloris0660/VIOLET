# 生产导入可靠性修复与实际恢复

PR [#152](https://github.com/kyloris0660/VIOLET/pull/152) 的 30 号续接已完成本轮工程修正、实际恢复及原 production 复验，等待负责人复审/合并。行为候选为 `83d5eda5a9de4622066617206a4b07e86e1bf2a8`，tree `37257541fb95279a374409a0a2c8a0d86876b1ea`；分支为 `codex/production-import-recovery`。PR 最终文档提交及实时 HEAD 另由 GitHub 回读写入当前 PR 正文和本机交付回执，行为结论始终绑定上述候选。

原 main / A1 接受基线仍为 `ea4bdd740943b2dad8c4eace88d0b33819d86cb8`。本轮没有合并、推 main、追加 reviewer 或启动下一阶段。`target_met=true` 是注册契约重建出的工程结果，`safe_to_merge=false`、`route_approved=false`，负责人接受及所有者亲自验收均未发生。

## 修正与审查意见

| 意见 | 根因与本轮处置 |
| --- | --- |
| R1 / 3944187453 | 旧枚举只在阻塞调用前后检查时间，无法中止 scandir/next。现由可终止工作进程执行目录打开、取项和必要源端元数据操作；超时 terminate/join，必要时 kill/join，并确认进程退出。条目、深度、时间、路径内存限制保留。坏子目录记 coverage unknown，保留已确认的健康兄弟候选和应用 Media 下游；共同依赖不可用仍可停止，未知覆盖不会标空或用于缺失调和，保留新计划续接。 |
| R2 / 3944187454 | safe_label 不再依赖枚举序号，改为稳定相对来源身份的摘要。真实公开计划、私有复算与 execute 顺序变化回归通过；版本校验、持久轮转、cap 份额及未尝试优先没有删去。 |
| R5 / 负责人裁决 | stored-hash 关联在当前有界源元数据和计划版本核对之后执行，并要求 hash 绑定版本证据。计划后替换不能关联旧 Media；历史证据不足走普通逐文件读取校验，不做全库 hash。此前 #29 四行关联只保留为历史已有内容关联，未重新应用，也不证明当前来源或新版本可读。 |
| R3 / 3944187455 | 哈希后复制、解码、保存及非重复 HTTPException 的规范化原因与 failed 总数在真实异常分支同步累计，每项一次。回归核实后续正常文件继续；保留 A1 MediaCommittedError 的提交后恢复和唯一 Media 保护。 |
| R4 / 3944187458 | 现有 production_import_recovery_v1 从独立 before 清单、原实际 run-item 和后续实际 run-item 重建身份/cohort/结果；核实全部原 498、原 5 缺失和 173 策略观察，并独立约束当前清单的新确认缺口。成功项必须具有实际应用文件、正确 Media 身份及必要下游/翻译覆盖。完整分类为 unknown 时须有真实 CLIP 来源、模型及置信度，不强行重新分类。 |
| 3944187452 | 按负责人裁决不适用：本轮是真实候选祖先链，没有实现另一套 squash 继承或重签旧证据。 |

同类问题审计覆盖枚举 open/next/成员/stat、来源解析与扫描策略、版本绑定的 hash 重用、复制前后版本、五种导入失败分支、局部错误候选保留、全部 cohort/下游与实际文件的验收。负例删除全部 268 缺口或 40 新增、删/换/重复身份、移除新确认来源、漏必要下游/翻译、删除成功项文件或伪造摘要均被拒绝。本轮未扩展格式支持、全局扫描架构、provider 或 schema。

原公平周期和版本处置继续生效：新导入/未尝试项优先，已有应用下游与到期旧重试有保留份额；入场和真实尝试位置持久化。冷却或同版本反复失败可暂缓，当前内容无法处理才按版本退出；timeout/Errno22 不标永久坏图。来源、版本或支持条件改变可重新进入，主动忽略仍由所有者选择。

## 实际恢复与下游

历史 #28 为 40 新增、263 关联和 190 次实际读取失败；#29 精确修复四个历史关联，总计 267；#30 的五个到期重试失败已留原始证据。旧候选 2b3c075、旧 XML、旧运行及私有包均保留，不把本轮结果回写为历史成功。

本轮 #31 普通 GUI 完整链路选择 39 个新确认导入候选和 10 个到期重试，16 个原暂缓项未盲试。39 项中，35 项来自本轮一次独立纯元数据清单，4 项在后续普通现场计划中新出现。第一次 cap=44 计划因超过预设 35 项保护范围而在 execute 前停止；第二次 cap=49 才实际运行。计划计数与执行结果分别保存。

#31 实际新入库 19 项、核实内容后关联已有 Media 20 项；新入库项实际分类 19、WD 标签 18、本地化复用已有覆盖 18，1 项非目标不适用。20 个本轮关联复用已有完整下游，其中 17 个目标、3 个非目标。10 个旧来源实际失败（4 read_error、6 read_timeout），均由既有同版本重复失败规则进入暂缓，原 190 项现为 164 可重试、26 暂缓。没有为原 16 暂缓项执行重试，也没有伪造主动忽略。

#31 的 421 个需本地化的不同 general/meta 标签全部复用已有中文/静态覆盖，待处理 0，LLM 调用 0、本地化失败 0。最终 346 个成功来源对应 315 个唯一 Media，完整范围的 1,596 个不同 general/meta 标签均有覆盖；这些全范围数量与本轮 421 的工作子集分别记账。

恢复观察器在任务结束后的长页面截图发生超时，原失败日志及已完成的实际 run 保存。随后只读复核正常页面、恢复列表及分页原因通过；没有为截图重复导入或运行下游。

原 498 与后续新增合计 `537` 个唯一来源，本轮契约重建结果如下：

| 结果 | 来源身份数 |
| --- | ---: |
| 实际失败，保留正常到期重试 | 164 |
| 同版本反复失败，暂缓待诊断 | 26 |
| 关联既有 Media 且必要下游完整 | 287 |
| 来源缺失/明确边界未执行 | 1 |
| 实际导入且必要下游完整 | 59 |

各独立 cohort 的结果及下游来源状态（完成、复用、不适用与待补做分开列出）：

- `observed_later`：39 项；结果 `{"imported": 19, "existing_media": 20}`；分类 `{"classified": 39}`；标签 `{"ai_tagged": 35, "ai_tagging_skipped_non_target": 4}`；本地化 `{"localized": 35, "localization_not_applicable_non_target": 4}`。
- `observed_new`：40 项；结果 `{"imported": 40}`；分类 `{"classified": 40}`；标签 `{"ai_tagged": 34, "ai_tagging_skipped_non_target": 6}`；本地化 `{"localized": 34, "localization_not_applicable_non_target": 6}`。
- `original_failed`：82 项；结果 `{"deferred_diagnosis": 26, "retryable": 56}`；分类 `{"deferred": 82}`；标签 `{"deferred": 82}`；本地化 `{"blocked_import_failed": 82}`。
- `original_unattempted`：108 项；结果 `{"retryable": 108}`；分类 `{"deferred": 108}`；标签 `{"deferred": 108}`；本地化 `{"blocked_import_failed": 108}`。
- `verified_gap`：268 项；结果 `{"existing_media": 267, "unexecuted": 1}`；分类 `{"classified": 256, "deferred": 1, "classified_reused": 11}`；标签 `{"ai_tagged": 203, "tagged": 40, "deferred": 1, "tagged_reused": 11, "ai_tagging_skipped_non_target": 13}`；本地化 `{"localized": 254, "deferred": 1, "localization_not_applicable_non_target": 13}`。

新增范围：独立清单 35 项 `{"imported": 15, "existing_media": 20}`；后续 4 项 `{"imported": 4}`。原 40 新增和 267 关联继续完整保护，不重新导入或重打全部标签。原 190 实际失败项没有整体重试；本轮仅按正常到期轮转实际尝试 10 项，其归宿已包含在原 cohort 中。

原 5 个缺失来源单列，其中 1 个与原 498 重合，另 4 个不重复计数；无可靠内容证据的项目等待来源路径恢复。原 173 策略观察从原 run discovery 独立重建，当前普通计划观察的 174 项保留在 #31 原始记录，不替换历史数量。旧诊断四个未保存身份的 stat 错误不能猜测映射。

必要下游未完成会使完整目标不成立；失败读取对应的 blocked/deferred 状态表示尚未形成可处理 Media，与成功项待补做不同。原 307 成功来源对应 296 个唯一 Media，旧 40 项为 34 个目标内容完成和 6 个非目标策略不适用，267 关联为 254 个目标内容完成/复用和 13 个非目标。原范围 1,548 个不同 general/meta 标签实际覆盖完整；旧报告 611 是当时需本地化工作子集，不能相加。静态/已有翻译复用不要求额外模型调用；本轮新增按原配置执行必要分类、WD 和本地化，实际计数见 #31 原始记录。

## 全历史与当前覆盖限制

未找到可复用的 26 号完整补查成果，已在同一任务完成保存的全部 30 个同步 run、287,288 个 run-item、171 个 ScanJob、43,412 个来源账本和 38,095 个 Media 的对账，并仅做一次独立有界纯元数据来源清单：43,370 项，目录/成员错误均 0、内容读取 0。后续 GUI 计划属于实际导入所需正常复算，没有另做一次诊断全量内容扫描。

当前来源 2 已有关联且下游完整的 38,702 个身份中，38,542 当前存在、160 当前来源缺失；另有 4,433 个已登记格式/隐藏策略项、191 个原恢复范围无 Media 项和 5 个其他历史缺失项。81 个其他未启用来源根身份未做源端访问。清单另发现 35 个受支持未登记身份和 170 个不支持格式项，后者按策略保留。

对 16 个已登记版本与当前元数据不同的身份，仅做必要逐文件有界核验：14 个与既有 Media 内容相同，2 个超时，保留其已有应用内容/完整下游，当前源版本仍未核实。没有全库内容 hash。对应用文件路径元数据核对的 38,095 个 Media 中，38,093 正常；另 2 个无来源账本关联的遗留 Media 文件缺失，无法由现有证据确定可恢复源，未删除记录或伪造替代文件，单列在私有历史附件。

171 个 ScanJob 中 162 个 dry-run、8 个真实完成、1 个真实中断；保存的 ScanJobMedia 关联为空。旧扫描部分只有汇总，因此无法重建曾存在、现已移除且从未登记的每个身份；不宣称过去所有时点或其他来源根绝无遗漏。文件 mtime 也不证明 35 项在旧运行时存在。当前覆盖、新发现、实际不可读和上述遗留异常各自保留范围。

## 验证、原 production 与日常入口

候选 `83d5eda5a9de4622066617206a4b07e86e1bf2a8` 的 focused：**475 passed / 3 skipped / 3 warnings，525.22 秒**；隔离 PostgreSQL：**98 passed，330.65 秒**。跳过为两个 Windows 符号链接权限用例和一个 SQLite 不适用的 PostgreSQL 正则用例；三个警告为既有 Pydantic 配置弃用。此前 60957db 的 471/98、预验证断言失败、环境或工具错误均另存，不冒称本候选结果。

测试从独立候选目录运行，使用指定 venv、标准测试环境和专用本机临时存储；PostgreSQL 限定到隔离测试数据库/角色，未在原库执行测试迁移。完整命令、实际 cwd、Python、起止时间、HEAD/tree 和 XML 哈希均在附件。未重跑完整 non-E2E，未付费补造缺失的历史 AI 证明，未把本地验证称为 GitHub CI。

隔离有界面系统 Edge/CDP：4 张真实测试图入库、1 项无效图像单独失败、三个恢复操作和详情通过；0 页面脚本错误，测试服务及调试监听已关闭。原 production 的有界面系统 Edge/CDP：原 5 样本的缩略图/详情/全屏、19 次普通搜索、来源标签跳转及本轮 2 个实际新增 Media 详情/全屏通过，30 张截图，0 页面脚本错误；#31 结束后另以只读 GUI 核实私有恢复列表和原因，195 个来源两页可达，195 条计划观察单独分页，两个集合不相加为唯一来源数。

正常便携 EXE 的任务专用实例通过原 profile 实际启动和重启；当前管理 PID `60440`，原 production 8012、原 blombooru / system id 7635635488443479756、原存储、指定 venv、安全启动和认证保持。5 Media / 51 绑定 / 1 active run 核实，Pixiv 读取开启、apply 关闭。已有启动器窗口保持，任务专用调试实例结束后关闭监听，并验证原 EXE 无参数新启动。具体 PID、进程参数、监听关闭和锚点保存在操作附件。

首次辅助 runtime 探针导入了隔离模块的默认 state_path，检查失败并留证；正式控制器从真实生产目录停止/启动。五类作业只读检查及历史结束时间核实未打断活动工作。文档预检改动造成一次快进拒绝，仅精确撤回本任务临时文档并由快进带回同样内容，未 reset/clean/stash。自动批准审查拒绝了关闭已有窗口的复合命令；保留该窗口后，新的任务专用实例获准，不把拒绝记作成功操作。

现有注册契约及文档检查已通过；完整目标由独立原集合、现场集合、实际文件和下游重建。行为候选到文档后继继续走既有祖先/行为一致性检查，未另建契约平台。收到负责人接受及实际合并结果后，按 30 号原授权继续对齐 main、受控目录/profile 与持久启动锚点；本轮停止在负责人复审/合并检查点。

## 交付与工程判断

本轮使用既有表/JSON、已有备份和模型配置，没有 schema 变更、新全库备份恢复、源文件修改、云保留属性变更、全库预下载、新模型/provider 或 Pixiv apply。恢复记录不得通过旧数据库备份覆盖；任何回退先等待活动任务结束，再经原控制器恢复已保存的代码/profile，保留本轮 Media、来源和历史。

代码修正、原 498 的保护和本轮可执行恢复已经形成可审查证据。剩余不可读来源保留真实原因和重入条件；2 个当前源版本未核实、2 个无来源关联的应用文件缺失及旧汇总历史覆盖限制没有被包装成已修复。建议负责人依据本轮新候选、实际 #31 和加强后的负例复审，接受/合并由负责人作出。

本机交付路径：

- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\reports\production-import-recovery.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\reports\production-import-recovery-summary.json`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\.local_manifests\import-recovery\task30\IMPORT-HISTORY-COVERAGE.zh-CN.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\.local_manifests\import-recovery\task30\OPERATIONS-AND-RECOVERY.zh-CN.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\.local_manifests\import-recovery\task30\final-scope-stats-private.json`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\.local_manifests\import-recovery\task30\final-evidence-83d5eda`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\.local_manifests\import-recovery\task30\PRODUCTION-IMPORT-RECOVERY-PR152-83d5eda-TASK30-PRIVATE.zip`

精确测试命令（运行目录均为 `C:\Users\kyloris\Documents\VIOLET-worktrees\pr152-bounded-candidate`）：

```text
C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_production_import_recovery.py tests/test_pr152_bounded_fix.py tests/test_production_pixiv_a1.py tests/test_import_recovery_contract.py tests/test_s3a_m1_manual_sync_execute.py tests/test_manual_sync_lifecycle.py tests/test_dynamic_library_sync.py tests/test_scanner_icloud.py tests/test_production_pixiv_a1_contract.py tests/test_production_launcher_control.py tests/test_trusted_git.py -q --basetemp=C:\Users\kyloris\AppData\Local\Temp\violet-pr152-83d5eda-focused --junitxml=C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\.local_manifests\import-recovery\task30\candidate-83d5eda\focused.xml
```

```text
C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_production_import_recovery.py tests/test_pr152_bounded_fix.py tests/test_production_pixiv_a1.py -q --basetemp=C:\Users\kyloris\AppData\Local\Temp\violet-pr152-83d5eda-postgresql --junitxml=C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\.local_manifests\import-recovery\task30\candidate-83d5eda\postgresql.xml
```

相对受审 eca27fa 的本轮变更文件：

- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\AGENTS.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\backend\app\services\dynamic_library_sync_service.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\backend\app\services\manual_sync_execute_service.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\backend\app\utils\bounded_source_copy.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\backend\app\utils\bounded_source_io.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\backend\app\utils\bounded_source_walk.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\backend\app\utils\local_library_scanner.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\current-handoff.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\development\agent-runbook.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\plans\production-import-recovery.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\reports\production-import-recovery-summary.json`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\state\current-phase.json`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\docs\reports\production-import-recovery.md`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\scripts\check_production_import_recovery.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\tests\source_io_worker_fixture.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\tests\test_import_recovery_contract.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\tests\test_pr152_bounded_fix.py`
- `C:\Users\kyloris\Documents\VIOLET-worktrees\production-import-recovery\tests\test_production_import_recovery.py`
