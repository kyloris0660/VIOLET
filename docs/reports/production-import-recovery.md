# 生产导入可靠性修复与恢复：PR #152

## 33号交付结论

暂缓、忽略和终止处置在普通更新检查、重新计划及新 Session 后保持，恢复列表与待导入数量一致。更新观察合并来源 JSON，保留真实失败、用户操作及旧哈希版本证据；未知版本首次补齐只建立基线，已证实版本变化和显式 resume 才按规则重入。

已登记历史优先路径现在通过既有可终止 SourceIOWorker 解析并验证归属。异常保留来源身份、目录及恢复条件，健康后继仍能计划和执行；已有应用副本的下游补做不依赖离线来源路径。哈希失败恢复稳定字符串原因，系统码、阶段和耗时保存在私有 JSON，旧直接消费者已通过真实模型落库验证。

原生产已复验本轮候选；537 个来源和 346 个成功来源对应的 315 个 Media 与原必要下游完整保留，待补做 0，1,596 个不同 general/meta 标签已有覆盖。本轮没有新生产导入、失败重试或 AI/LLM 补跑。

远端 main 仍为已接受 A1 基线，PR #152 尚待负责人复审/合并，因此日常入口已指向本轮生产候选，尚未完成“已合并 main”对齐。工程验证、负责人接受和产品用户亲自体验分别记录。

## 身份与状态

| 项目 | 结果 |
| --- | --- |
| PR / 分支 | [PR #152](https://github.com/kyloris0660/VIOLET/pull/152) / `codex/production-import-recovery`，正常非 Draft PR |
| 本轮行为候选 | `a8aeda5fff378392f3a0d599370946dac001258b` |
| 候选 tree | `8eac0cfcf598c249958ccf4113d8ac317fc2b7ee` |
| 已接受 main / A1 | `ea4bdd740943b2dad8c4eace88d0b33819d86cb8` |
| 原生产 | 正常 EXE 启动/重启，生产候选 `a8aeda5`，读取开启 / Pixiv apply 关闭 |
| 最终文档 HEAD | 当前 PR head 及私有 DELIVERY-STATE 记录；仅允许原 carry-forward 契约规定的文档后继 |
| 契约结论 | `production_import_recovery_v1` 的本轮工程范围 `target_met=true`；`safe_to_merge=false`、`route_approved=false` |
| 接受 / 合并 / 产品用户体验 | 负责人复审待完成；未合并；不代称所有者或产品用户亲自验收 |

## 四条意见逐项修正

| 意见 | 修正与真实验证 |
| --- | --- |
| R6 / P1 / [3950226963](https://github.com/kyloris0660/VIOLET/pull/152#discussion_r3950226963) | 更新观察合并模块元数据，保留恢复处置、操作事件、三次失败 run 和 content_hash_version。观察不验证内容；已完成应用副本和下游保留，新版本待处理单独表达。恢复 SQL 列表保留处置，更新后仍可查，受处置限制的项目不计入待导入且 run-item 不可导入。 |
| R7 / P2 / [3950226970](https://github.com/kyloris0660/VIOLET/pull/152#discussion_r3950226970) | 优先取有时间和 run-item 来源的真实尝试版本；较新的真实元数据观察可取代旧尝试，缺证据时有界读取，仍失败保存未知版本。defer/ignore/terminal × 空/旧/当前列值的 9 组真实 API→更新→规划→新 Session 验证；首次补齐、真实变化、resume 和失败去重另有回归。 |
| R8 / P2 / [3950226953](https://github.com/kyloris0660/VIOLET/pull/152#discussion_r3950226953) | 优先路径使用工作进程 resolve，解析前后保持范围验证；失败、未关联 skipped_existing_media / skipped_duplicate / unchanged 四类阻塞回归确认超时终止回收、健康文件实际导入及异常身份保留。应用 Media 后续处理跳过不需要的来源解析；原 open/next/坏子目录/顺序/cap 回归保留。 |
| R9 / P2 / [3950226956](https://github.com/kyloris0660/VIOLET/pull/152#discussion_r3950226956) | 哈希返回稳定字符串 SourceReadReason，诊断另存。旧 Phase47 消费者在隔离数据库真实运行 3 个测试来源，timeout/error 均提交 String(255) 原因和私有诊断，随后成功哈希并复用 Media；手动同步与 scanner 消费者也保留诊断。不在生产运行旧阶段脚本。 |

旧 R1–R5 按33号负责人裁决保留：可终止枚举、稳定标识、逐项计数、完整集合/下游契约及 stored-hash 当前版本证据继续有效；3944187452 的 squash 建议仍不适用。未擅自关闭旧线程或追加 reviewer。

## 本轮实际验证

| 范围 | 结果 |
| --- | --- |
| 冻结候选 focused | 498 passed / 3 skipped，3 条既有 Pydantic 配置弃用警告，541.72 秒 |
| 隔离 PostgreSQL | 120 passed / 0 skipped，无警告，396.04 秒 |
| R6–R9 实际接入回归 | 每套均包含 22 条；原契约现在要求这些 XML 用例存在且通过，旧 XML 不能替代 |
| 独立 Edge / CDP | 实际 4 导入、1 解码失败、3 恢复动作；terminal/defer/ignore 跨更新检查、重新规划和页面刷新仍可见且待导入为 0；7 张截图，0 页面脚本错误 |
| 原生产 Edge / CDP | 原 5 Media、51 绑定、19 普通搜索、详情/全屏及 #31 两个真实新增样本；恢复页、缺失和策略分页复核 |
| Launcher | 原便携 EXE 正常 Start/Restart，PID 实际改变；任务调试监听关闭，正常 EXE 无参数启动 |
| 契约 / 文档 | 原注册契约与 current-phase/handoff 检查通过；最终交付 HEAD 再检查 |

指定项目 venv Python 3.12.0 身份预检通过；标准测试配置及隔离本地存储，PostgreSQL 使用专用测试数据库/角色和每例独立 schema。精确命令、工作目录、Python 绝对路径、开始/结束时间、XML 与日志在私有附件。3 个 focused skip 为既有两个符号链接条件与 SQLite 不支持的 PostgreSQL 正则条件；本地验证不冒称 GitHub CI。

本轮命令范围：

- `-m pytest tests/test_pr152_recovery_state_io.py tests/test_production_import_recovery.py tests/test_pr152_bounded_fix.py tests/test_production_pixiv_a1.py tests/test_import_recovery_contract.py tests/test_s3a_m1_manual_sync_execute.py tests/test_manual_sync_lifecycle.py tests/test_dynamic_library_sync.py tests/test_scanner_icloud.py tests/test_production_pixiv_a1_contract.py tests/test_production_launcher_control.py tests/test_trusted_git.py -q`
- `-m pytest tests/test_pr152_recovery_state_io.py tests/test_production_import_recovery.py tests/test_pr152_bounded_fix.py tests/test_production_pixiv_a1.py -q`

## 读取与实际变更文件

已读取33号完整任务、实际工作目录AGENTS/current-phase/handoff/runbook、同一实施方案/工程报告、task30实际恢复材料和最新GitHub审查线程。沿来源观察、恢复API、生命周期、执行器、有界I/O与三个哈希直接消费者检查同类写入；32号获取限制见下文。

相对本次退回HEAD的文件变更：

- `backend/app/routes/admin/manual_sync_recovery.py`
- `backend/app/services/dynamic_library_sync_service.py`
- `backend/app/services/manual_sync_execute_service.py`
- `backend/app/services/manual_sync_lifecycle.py`
- `backend/app/services/manual_sync_recovery.py`
- `backend/app/utils/local_library_scanner.py`
- `docs/current-handoff.md`
- `docs/plans/production-import-recovery.md`
- `docs/reports/production-import-recovery-summary.json`
- `docs/reports/production-import-recovery.md`
- `docs/state/current-phase.json`
- `scripts/check_production_import_recovery.py`
- `scripts/run_phase47_s2_baseline_full_import_ai_localization.py`
- `tests/source_io_worker_fixture.py`
- `tests/test_import_recovery_contract.py`
- `tests/test_pr152_recovery_state_io.py`

开发预验证原始结果保留：首批18通过；扩大回归发现3失败后修正，141通过；44条接入与契约通过；待导入口径修正后108通过。49b4f7d 和5b225bd 的完整候选测试因真实 UI 发现本轮接入遗漏而中止，未部署、未记为通过。最终结果仅来自上表候选；旧83d5eda的475/98是历史结果。不重跑全部历史 non-E2E，不补造历史 AI 证明。

## 已完成生产结果保护

最新真实执行仍为 #31：49 项中 19 新增、20 关联、10 旧失败进入暂缓。原 498 + 后续39 =537，59 新增、287 关联、164 可重试、26 暂缓、1 缺失未执行，类别互斥。原190失败最新144 timeout /46 error；26暂缓各有3个同版本真实失败run。部署前后537行逐项一致，315 Media/标签/本地化数据一致，未发现需要恢复的被清除处置。

原5个缺失身份与一个交叉项保留并去重；173策略观察和历史不支持MOV不算新增图片。两个旧应用副本仍可用、源新版本未核验的身份保留，使用已有版本证据确认正常规划可重入，未循环读取内容。两个无来源关联的遗留小型应用文件缺失，经一次有界既有任务材料定位仍无可信恢复来源，作为具名私有异常保留；不删除或拿其他图片替换，不阻塞四项修正。

## 生产操作与证据

开工现场服务已停止、8012无监听，生产五类任务空闲；此前记录的PID未被当作现场事实。切换前重新确认导入/扫描/分类/标签/本地化空闲，保存原profile及启动锚点，通过安全快进与原控制器切换。原数据库、存储、认证、模型配置保持；无schema变更，无迁移，无全库备份恢复或全根扫描。生产只做必要登录和只读页面/数据验证。

独立测试目录、旧生产目录、旧备份与task30完整证据均保留。新增材料限定在同一task33私有附件，旧完整历史/库存输入原样复用，没有重新生成大规模历史证据。附件包含 before/after、受影响身份及版本、模型/下游保护、原始失败、命令/XML、启动及CDP清理记录、最终候选/PR/HEAD关系。源路径、凭据和原图不提交公开PR。

## 工程判断与下一检查点

本轮属于现有日常入口的有限接入修复。持久生产代码是来源观察、恢复API/生命周期与I/O兼容；原阶段契约和回归是复用验证工具；旧Phase47只做返回值兼容；本机操作脚本和输出均为私有一次性材料。没有新增处置平台、数据库schema、扫描器或布局重设计。

当前注册契约证明本轮22条实际接入、既有恢复完整性及原生产复验，不能外推为所有日常用法或人工接受。32号文件未在Downloads定位到，负责人会话的只读取回两次超时；33号完整授权与四条实时远端意见已读，具体缺口通过本轮实际入口自行验证。

下一步由项目负责人复审同一PR152并决定接受/合并。收到接受和真实merge后，沿33号原授权可信fetch、安全快进并受控对齐main/profile/持久日常锚点；不自行merge、推main或强推，不重导入/迁移/重做标签。Pixiv A2/A3、provider/truth与完整侧栏布局继续后排。
