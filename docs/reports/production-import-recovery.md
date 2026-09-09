# 生产导入可靠性修复与恢复：PR #152

## 36号交付结论

已有应用 Media 的分类、WD 标签和本地化完成事实，在新源版本复制前提交、复制/解码/非重复 HTTP 异常及中断后均保持。失败仍真实记录在来源/run-item/恢复历史，新版本没有被旧 Media 的可用状态冒充完成。成功导入或关联后才切换 Media 并清除版本待处理；新 Media 完成必要下游，已有目标复用其完整或部分完成结果，仅补实际缺口。

本轮限定实现、隔离集成、原 production 服务与数据复验，以及普通EXE内真实停止/启动/重启按钮验证均已完成。按钮验收采用Windows UI Automation InvokePattern，走原页面处理链，未启用CDP。现场仍为537来源、346成功来源对应315 Media，必要下游待补做0，1596个不同general/meta标签已覆盖。部署前后537行和315 Media/标签/本地化数据逐项一致。生产导入/关联/状态修复/分类/WD/翻译新增量全部为0；没有额外生产AI或LLM调用。

工程完成与负责人接受分别记账：PR152仍待负责人复审，没有merge，main仍是已接受A1基线；日常入口指向本轮已验证候选，生产服务PID为37528，尚未完成合并后main对齐。

## 身份、范围和当前状态

| 项目 | 结果 |
| --- | --- |
| PR / 分支 | [PR #152](https://github.com/kyloris0660/VIOLET/pull/152) / `codex/production-import-recovery` |
| 被退回HEAD | `69a28c6bd921d4b84ddd95054ebbc925ff572a3b` |
| 本轮实测行为候选 | `81ea89e9bc21eeaff1988d858a24b1243d451e3c` |
| 候选tree | `f7979cdc207325b1b44771b7fea40244d4726934` |
| 最终交付HEAD | 当前PR HEAD及私有DELIVERY-STATE；候选后的提交仅允许原carry-forward契约列出的文档文件 |
| 生产 | 原controller/profile及正常EXE，候选`81ea89e`；原库/存储/认证/模型，Pixiv读取开启/apply关闭 |
| main | `ea4bdd740943b2dad8c4eace88d0b33819d86cb8`，尚未包含本轮修复 |
| 工程契约 / 本任务工程验证 | 原`production_import_recovery_v1`通过；新增实际UIA按钮证据补齐原契约未检查的交互门槛，current-phase `target_met=true`；`safe_to_merge=false`、`route_approved=false`，负责人接受仍待完成 |
| 负责人接受 / 合并 / 入口对齐 / 产品用户体验 | 复审待完成 / 未合并 / 合并后对齐待完成 / 未冒称亲自验收 |

本轮按36号执行版授权开始，时间保存在本机task36记录；一周为上限，完成即交付。R1–R5、R7–R9沿用负责人接受结论；两条3959529522祖先/squash、3959529535无哈希FOLLOWUP意见依36号裁决不适用，当前真实父子提交及独立FOLLOWUP路径保留，并加入不依赖来源哈希的执行回归。开场11条未关闭线程，GitHub check-runs与commit statuses均为0；线程数量不等于未修复数。

## 根因及同一执行器修正

1. `_mark_item_import_in_progress()`在复制之前提交时无条件清空三项下游，异常回滚已无法挽回。现在只有首次导入、无Media时初始化等待状态；旧Media事实继续保存。
2. HTTPException分支先重写下游，再调用有保护的failure helper；普通Exception分支另写一套覆盖逻辑。两处分支统一使用既有`_mark_item_failed()`，去重则直接绑定目标，保留稳定原因、私有诊断、同版本真实失败run、冷却和终止/暂缓。
3. 同链路的retry-ready只表示源读取成功，保持pending，不能因旧应用副本存在写成imported。首次准入在hash/copy之前持久化`current_source_version_pending`，防止直接legacy计划的旧列在更新后丢失待处理证据；`content_hash_version`只证明已读取源版本，`media_id`与三项下游仍指旧应用副本。只有成功导入或可靠关联才清除此标志。
4. 成功新Media正常初始化下游；已有Media去重绑定复用目标来源可证实的完整或部分完成状态。目标只缺WD或本地化时，已完成分类/WD不重跑。未执行保留分支也不再覆盖已有Media下游。核对限于当前执行器的这些写入、提交、恢复、成功/去重和后续目标入口。

## 精确候选验证

| 范围 | 实际结果 |
| --- | --- |
| focused | 525 passed, 3 skipped, 3 warnings in 705.92s (0:11:45) |
| 隔离PostgreSQL | 147 passed in 579.03s (0:09:39) |
| 新增R6矩阵 | 27条，以上两套均通过；原22条R6–R9接入继续通过，共49条由原契约约束 |
| 隔离真实Edge | 普通导入、解码失败与恢复处置跨更新保持通过；7张截图、0页面错误，任务服务器8013与专用浏览器已关闭 |
| 原生产真实Edge/CDP | 恢复列表/原因/分页、19次代表搜索、详情/全屏、原5样本/51绑定及#31两个新增样本通过 |
| 普通EXE真实按钮 | 无参数EXE，UIA InvokePattern依次停止/启动/重启；自动preflight可见，服务PID 48388→停止→25608→37528，最终健康；未启用CDP，18186无监听 |
| 原契约/文档 | 通过；最终文档HEAD再核实行为继承与current-phase/handoff |

两套最终测试均为0失败、0错误。focused的3项跳过是1项PostgreSQL专属正则和2项Windows符号链接条件；3条警告为既有Pydantic class-based config弃用警告。冻结前扩大定向组合为84 passed，不与最终候选的两套结果混算。

27条新增真实接入包括：6条hash成功后的copy/decode/非重复HTTP失败×先update/直接legacy计划；2条复制前已提交后中断→新Session→正常过期运行恢复→成功导入；16条新Media、已有完整目标、仅缺WD、仅缺本地化、HTTP409并发去重及Media提交后响应异常恢复×两种版本发现；2条无旧Media首次失败负例；1条无来源哈希FOLLOWUP只补本地化。失败矩阵同时证明健康后继完成、旧独立来源及真实Media/标签/翻译记录保持，失败仍可暂缓并跨更新保存。

使用指定项目venv Python 3.12.0及身份预检，标准测试环境、独立本地存储和专用PostgreSQL角色/每例独立schema。复制、图片验证、导入事务、执行器、恢复API、新Session和本地化流程均真实执行；分类/WD模型使用确定性测试适配器并写入真实模型表，本地化复用已有静态/翻译结果，测试没有付费AI/LLM。模型推理质量不在本轮验证范围。精确sys.executable、完整命令、开始/结束时间、原始日志/XML在私有包。

- `python -m pytest tests/test_pr152_recovery_state_io.py tests/test_production_import_recovery.py tests/test_pr152_bounded_fix.py tests/test_production_pixiv_a1.py tests/test_import_recovery_contract.py tests/test_s3a_m1_manual_sync_execute.py tests/test_manual_sync_lifecycle.py tests/test_dynamic_library_sync.py tests/test_scanner_icloud.py tests/test_production_pixiv_a1_contract.py tests/test_production_launcher_control.py tests/test_trusted_git.py -q`
- `python -m pytest tests/test_pr152_recovery_state_io.py tests/test_production_import_recovery.py tests/test_pr152_bounded_fix.py tests/test_production_pixiv_a1.py -q`

首次8项定向结果为8失败，其中5项复现持久化下游覆盖，3项为测试查找新来源缺少ID的错误；修正执行器后5通过，3个夹具查找错误已改正。扩大矩阵19通过/4失败，4项证明目标已有分类在部分下游去重时重复执行，已修正并补入只缺本地化用例。后续定向和最终候选结果分别保存，不把内部修正编号为新审查轮。旧a8aeda5的498/120保持为历史结果，不改旧XML，不重跑全部历史non-E2E或补造历史AI证明。

## 36号验收续接：普通 EXE 真实按钮

本次只补按钮验收，原任务从2026-09-09 20:34:25（UTC+8）起的一周上限不重新计时。未改Launcher、R6代码、模型、配置或测试；525/147原XML、运行日期及27条新增/22条原接入证据原样保留。

在普通用户会话1无参数打开日常便携EXE，以父进程74280→Electron窗口进程61868、HWND4853802和“V.I.O.L.E.T. 启动器”标题共同确定目标。初始UIA只有菜单/容器，且前台/根元素焦点探测未发送点击；通过原生WindowPattern正常最大化后，页面按钮变为可见可用并提供InvokePattern。实际发现的AutomationId恰为startButton/stopButton/restartButton，但选择依据为实际Button类型和中文名称，并非预先假定ID。

| 状态 | 实际动作及证据 | 结果 |
| --- | --- | --- |
| 基线 | 窗口/只读身份，五类任务空闲 | 生产PID48388、健康OK |
| 停止 | UIA调用实际“停止”按钮 | 界面已停止，旧PID退出、8012释放 |
| 启动 | UIA调用实际“启动”按钮 | 捕获“正在进行启动前检查...”，随后界面运行中/健康OK，服务PID25608 |
| 重启 | UIA调用实际“重启”按钮一次 | PID25608退出，新服务PID37528、实际监听进程81304，界面最终运行中/健康OK |
| 收尾 | 独立只读server-identity、原A1状态和一张既有图片 | 原profile/库/存储/认证/Python和候选81ea89e一致，读取开启/apply关闭，服务保持可用；任务Edge已关闭 |

方法是实际Windows UI Automation InvokePattern，不是CDP，也不是直接controller、内部IPC/JavaScript或替代页面。窗口截图由PrintWindow限定到已确认HWND；原始中间帧、控件树及最终截图均保留，以最终可见按钮可用、UI显示PID37528和独立健康身份一致为完成依据。私有`task36/launcher-uia/launcher-buttons-private.json`关联动作、截图和运行身份；主要截图为`visible-baseline.png`、`stopped.png`、`start-invoked.png`、`started.png`、`restarted-complete.png`。

上次被拒请求发生于2026-09-09 13:00:32.424 UTC，经functions.exec调用exec_command；13:00:32.492 UTC返回CreateProcess拒绝/blocked by policy，命令未执行，具体审批原因未提供。原始命令、工具和返回已从本任务日志一次性提取保存在私有材料。task33的早期ECONNREFUSED属于已执行后的监听连接失败，随后CDP成功；与本次执行前审批拒绝不同。没有重放该命令、换调试端口/包装或修改审批、安全、代理设置。

本次只读SQL核对537条受保护来源与原保护证据相同，最新业务run仍为31，五类任务新增均0；原315Media下游详细证据继续复用。本次业务写入、导入/关联/状态修复/下游补做、额外生产AI均0，未全量扫描、重做备份、迁移、源哈希或旧190/26恢复。轻量Edge只验证既有Media788详情图片，0页面错误，随后关闭任务浏览器；没有新增TCP调试监听。

## 生产保护、增量和剩余例外

开场及部署后只读核对与task33一致：537来源中59新增、287关联、164可重试、26暂缓、1缺失未执行；346成功来源/315Media完整，下游待补做0。最新真实执行仍是#31，本轮没有新生产run或有证据需要修复的受损来源，因此生产新增/关联/状态修复/下游补做均为0。

原108未执行与82失败均已实际尝试，190项仍不可读；59新增来自另外发现/新增来源，绝非从旧190中恢复。26暂缓每项有3个同版本真实失败run；未全体重试190或resume26，未重复分类、打标签、本地化、备份、迁移或源哈希。原5个缺失来源按身份去重，历史不支持MOV不算新可导入图片。

source40562/40567对应Media36204/36209，旧应用副本和下游完整，新源内容仍未核验；本轮先update和直接legacy计划矩阵确认正常重入及失败保护，没有重复源内容读取。Media35399/35400两个遗留小型应用文件仍无可信恢复来源，保留具名异常，不删除记录或生成替代图。这些原有例外不阻塞本轮生命周期修正。

## 变更文件与证据

- `backend/app/services/manual_sync_execute_service.py`
- `docs/current-handoff.md`
- `docs/plans/production-import-recovery.md`
- `docs/reports/production-import-recovery-summary.json`
- `docs/reports/production-import-recovery.md`
- `docs/state/current-phase.json`
- `scripts/check_production_import_recovery.py`
- `tests/test_pr152_recovery_state_io.py`

同一工程报告保留为唯一入口。私有task36证据包保存本轮XML、对账、身份、截图、命令、原始失败及操作恢复记录；task33原包保留。原完整历史/库存输入通过本机硬链接和哈希引用复用，没有复制或重新打包旧大历史；公开Git不包含私有路径、原图或凭据。35号文件在Downloads未找到，依36号完整缺口继续执行，没有反复抓取旧会话。

## 工程判断与下一步

本轮长期维护代码是执行器生命周期修正及其回归；原契约仅增加27条实际接入的最小约束，专用复验脚本属于本机阶段工具。没有新schema、队列、扫描器、治理平台或产品阶段。原有来源不可读/缺失继续按已知归宿展示，不承诺历史绝无漏图。

普通EXE实际按钮证据现已齐全，本轮工程验证完成并返回项目负责人复审同一PR152。原自动契约通过与本次补充UIA证据分别保留，不冒称契约原已覆盖按钮或项目所有者亲自验收。负责人尚未接受或合并；下一步最小动作是复核更新后的同一私有包和PR，实际接受并合并后再按原授权对齐main/profile/持久入口。

未merge/main push/force/reviewer/关闭线程；未启动Pixiv A2或第二provider，UI美化等延期。收到实际接受及真实合并后，按原授权可信fetch、安全快进并经controller对齐main/profile/持久入口，验证正常停止再启动；若切换失败恢复已验证代码和配置，保留原库及完成数据，不用数据库回滚解决入口问题。
