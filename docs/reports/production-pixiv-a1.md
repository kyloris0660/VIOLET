# 生产 Pixiv A1 交付报告

日期：2026-09-06。项目所有者：`kyloris0660`（需求、资产及操作授权）；项目负责人：本次 VIOLET 接手与路线规划的 ChatGPT 会话（路线及交付复审）；执行代理：当前 Codex 会话（实现、已授权操作及工程验证）。本报告不代替项目负责人接受、项目所有者亲自使用验收或审查者意见。

## 交付结果与运行版本

A1 的代码、恢复排练、原库样本生命周期和真实界面验证已完成。产品用户通过原有生产启动器可查看五个已有 Media 的来源概念、按有效来源及原标签搜索，并打开真实图片。候选仍待项目负责人复审；未合并、未推送 main、未触发额外审查。

- 分支：`codex/production-pixiv-a1`。
- 起点：已合并 PR #150 的 `26a6fc8d30ba2b2eae69f55a8e7c33d5a4b9cdd3`。
- 实际运行与本次浏览器验证提交：`8ec23cd7a67230f9a382ba52687daa04f1785999`。
- 后续提交仅整理 AGENTS、runbook、报告及当前状态；执行契约检查相对运行提交的非文档差异必须为空。最终 PR HEAD 记录于 PR 正文和本机交付身份记录，避免文档提交自引用其自身哈希。
- 现有 launcher 的配置锚点指向保留的候选 worktree；继续使用原生产数据库、存储、认证及既有 venv。原生 launcher 首次启动 PID 8592；既有控制器实际重启为 PID 22548；本轮只读核对该进程仍健康，五个样本的新会话搜索一致。
- 产品读取功能开启、临时 apply 关闭；safe-startup 保留，后台维护、自动同步、provider 和 LLM 任务未被启动。候选 worktree、venv、profile、备份及证据保留。

`production_pixiv_a1_v1` 从本机实际备份、恢复、API 响应、运行记录和测试日志生成[脱敏结果](production-pixiv-a1-summary.json)。`target_met` 表示本次工程目标；`safe_to_merge=false`、`route_approved=false`，项目负责人接受仍为 pending。

## 要求与证据核对

| 原 A1 要求 | 本次结果及证据 |
|---|---|
| suggestion 搜索一致 | 精确关联过滤修复，别名、负向、混合条件与 PostgreSQL 通配符通过真实 API 集合测试；合法 source 支持保留 |
| 绑定更新及时失效 | 记录版本、事务触发器、旧观察撤回及缓存版本共同生效；作者、内容、provenance、状态、映射、删除、事务回滚及复用来源均有回归 |
| 历史策略 | 创建时保存 resolver/context/candidate/product 版本；历史常量只在原结果指纹可验证时使用；未知拒绝重解释，旧详情/replay/rollback 与新版本均验证 |
| 导入提交后异常 | 共享保存路径区分 commit 前后；direct/manual-sync/upload 三类调用、flush/refresh/cache/serialization 四种故障在独立 PostgreSQL 验证 |
| 迁移、恢复、幂等与撤销 | 一次备份、独立恢复；四项迁移在副本幂等验证；副本和生产均完成正式 API 完整生命周期 |
| 真实图片、普通界面及跨会话 | 系统 Edge 有界面独立会话；五样本三种图片视图、来源证据展开和 chip 跳转、正向/AND/负向/别名/多语言检查；复用已完成控制器重启 |
| 规模与 A2 输入 | 当前数据库候选分母、453 个兼容作品结构、240-query workload；缺失 metadata、缓存实际用途及策略连接缺口见后文 |
| 交付和角色 | 一个正常 PR、此报告、本机完整操作恢复记录；正式角色规则和工具故障处理规则在同一 PR 内更新 |

## 原库范围、操作和保全

盘点来自本次备份快照及其恢复副本，未扫描源目录或计算全库原图摘要。快照于北京时间 2026-09-06 00:12 附近取得；分母计算于 01:21 完成。原生产 Media 为 **37,419**，已有 media_tags 1,976,576，SourceMetadata 2,554，SourceConcept 6,094，来源信号 12,635。真实 Pixiv metadata 覆盖 **526 个 distinct works**，可进入当前本地绑定选择的 eligible works 为 **465**。

数据库 filename/path 规则得到 10,747 个候选 Media、11,482 个候选 work、11,707 个候选 page；其中 1,450 个 Media 存在多个 work 先验。无歧义部分为 9,297 Media、9,004 works、9,207 pages。**这些是数据库路径规则的候选先验，不是确认的 Pixiv 来源，也不是对线上所有页面的完整性承诺。**

最终固定 1% 选择，在第一次生产 apply 前确定。5% 副本预览未增加多页代表性，因此未靠扩大或轮换 active selection 拼接结论。五个选中作品在本地已有可验证映射中各有一页、一个 Media，合计五页五图；页面索引并非都为 0。

| Media | work | 本地 page index | 作者稳定 ID | 显示名 | 原图实际尺寸 |
|---:|---:|---:|---:|---|---|
| 788 | 101402851 | 4 | 59336265 | pottsness | 1900 × 3600 |
| 2431 | 134332480 | 0 | 16034374 | 絆 | 2976 × 4210 |
| 1869 | 91775401 | 0 | 24234 | mignon | 694 × 1176 |
| 842 | 99522365 | 0 | 34054962 | 当前 metadata 无显示名，保留稳定 ID | 3851 × 6350 |
| 846 | 99632451 | 0 | 12229818 | SAW272 | 3720 × 5262 |

执行代理依据项目所有者授权完成以下操作，项目所有者没有被记录为亲自操作：

1. PostgreSQL 17 自定义格式备份成功，大小 59,384,629 字节；独立任务数据库恢复成功，54 张既有表计数一致。备份文件、恢复身份与摘要均在本机保留，本次续接未重做。
2. 四项 additive 迁移：产品持久化、产品 Media 绑定、来源回退搜索索引、来源/绑定版本与事务缓存 epoch。副本重复执行通过；原库执行前后 54 张既有表计数相同。
3. 实际 `POST /api/admin/pixiv-product-integration/source-metadata/run`：新鲜 dry-run，由现有 `accepted_apply_request()` 传递真实校验值后 apply。未要求项目所有者抄写指纹。
4. fresh-session 普通搜索/详情 → replay → owned rollback → repeated rollback → reapply。重放无增量；撤销后既有表计数和样本搜索恢复基线；再次撤销幂等；再应用恢复 **1 active run、51 条绑定、43 clusters、17 candidate pairs、56 ambiguity records**。

原 Media、人工标签、相册、独立来源及确认实体未被本轮操作改写；原图、缩略图未修改或删除。来源展示中的既有导入标识保留，未为了显示 Pixiv 概念覆盖 Media 原有来源字段。生产未做故障注入或作者身份篡改。

## 修复根因与语义结果

精确标签路径原先只判断标签名，没有限定同一 Media 的关联 `is_suggestion`，导致未接受 WD 标签泄漏。现在使用关联条件并过滤 suggestion；SQLite 默认布尔值也改为真正布尔表达式。每个正向词可由有效标签或有效来源支持，多个词在同一 Media 上取交集；负向排除对应有效集合。

旧产品绑定仅依赖 active run，不能反映来源刷新。现在 SourceMetadata 保存 `binding_revision`，绑定保存 `source_revision`，更新输入和删除来源在同一事务撤回旧观察及派生复用来源。搜索、别名传播、Media 详情及全局证据共同使用当前有效性条件。缓存键读取单行 epoch，不逐次扫描或解析全库来源。仅保留 source run 为 active 不再使旧绑定有效。

旧 run 重建使用当下策略常量，可能误解历史。现在读取 run 保存的四类版本；缺少旧字段时只能用可验证的历史实现常量重建，并继续验证原结果指纹，无法恢复时明确拒绝。历史原始响应没有重写。

共享导入在 commit 后的 refresh/cache/序列化异常曾可能进入外层文件清理。新增明确的已提交异常边界，传播 Media ID 并保护已提交 original/thumbnail；commit 前失败仍可清理本次临时文件。有限同类路径覆盖 direct、手动同步、upload 和本地 scanner；未建立第二套文件事务平台，未启用 Booru 下载。

| 查询或操作 | 预期与实际 |
|---|---|
| `"Pixiv creator 59336265"` | `{788}` |
| `"Pixiv creator 16034374" "ミカ"` | `{2431}`；稳定作者与标题同时满足 |
| `"Pixiv creator 16034374" -"ミカ"` | 空集，界面显示未找到结果 |
| `"絆"` | 4 个已有合法结果，包含 2431；裸名不被强行收窄成唯一身份 |
| `"Pixiv creator 12229818" "Lumine"` / `"荧"` | 两者均为 `{846}`，依据已有来源与标签，不据此宣称不同角色记录已合并 |
| `"Pixiv creator 12229818" "GenshinImpact"` | `{846}` |
| `"Pixiv creator 34054962" "1girl"` | `{842}`，既有有效标签与来源 AND 正常 |
| Media 842 展开来源证据，再点击稳定作者 chip | 展示 Pixiv 依据及未确认来源层标识，普通搜索返回 `{842}` |

这些是实际界面输入及 DOM/API 对照。另有独立 fixture 覆盖生产样本没有自然涵盖的困难语义：同一稳定作者的 `AsterCurrent`/`AsterHistorical` 跨作品旧名；另一作者的同名 `AsterCurrent` 保持分离；`MoonGarden` 收窄到 `{1,2}`、接受标签交集只取 `{2}`、负向保留 `{3,4}`；中文 `星花旧名`、suggestion-only 中英文别名与 PostgreSQL 通配符均有真实 API 回归。fixture 不是原库数据。

## 真实浏览器验证

使用现有 Playwright 和 **Microsoft Edge 152.0.4191.62，headless=false**，独立非持久浏览器会话、1440 × 1000 视口及正常登录。origin 由当前生产控制器取得；请求限于该本机 origin，未打开私人 profile 或其他标签页，未模拟接口。主验收时间为北京时间 2026-09-06 **11:49:55–11:50:31**（UTC 03:49:55–03:50:31）；来源 chip 与困难组合补充检查随后完成。

五个缩略图、五个详情原图、五个全屏原图均完成实际解码及非零渲染尺寸验证；执行代理逐张查看了 15 张对应截图，确认图像内容与视图一致、无占位图或破图，原有标签及信息面板可见。原图尺寸见样本表。另实际查看别名、负向、中文角色、展开证据和 chip 跳转截图。截图存在本身没有被当作目视通过。

主脚本 12 次搜索通过；补充 7 次搜索通过，来源 chip 跳转补跑 1 次入口搜索及点击通过。补充脚本第一次在 chip 跳转尚未渲染时读取列表产生断言失败；改为等待同一 q 的真实响应及 DOM 后通过，生产代码未改动。普通搜索脚本不写生产数据，只有正常登录 POST。

记录了两次登录前辅助请求 401，以及首轮快速切页取消的辅助 fetch；停留等待后的补充检查仅保留登录前 401。没有页面脚本异常，没有样本图片请求失败或持续主列表 loading。任务专用浏览器已自行关闭；生产进程继续运行。

仍有一个既有低影响 UI 问题：空结果分支跳过热门标签渲染，侧栏保留“正在加载”文本，主搜索已结束并显示无结果。`gallery.js` 本轮未修改，原基线已有该分支。记录为后续日常 UI 修复项，不影响本次集合正确性。

前次 Computer Use 因无法可靠识别 URL 停止控制。本轮未重试该路线，也未修改安全检查；使用当前规则及任务明确允许的独立 Playwright 目标会话。AGENTS/runbook 已补充不反复重试、明确拒绝不绕过、单项阻塞不搁置其他交付的简洁规则。

## 测试与契约

所有 Python 执行均通过仓库既有 venv 身份预检。runtime 测试使用标准测试环境；PostgreSQL 危险场景使用独立恢复任务库中的独立 schema，未连接原库写测试数据。

| 验证 | 原始记录结果 | 解释 |
|---|---|---|
| 最终 focused | 347 passed / 1 failed / 1 skipped，3 warnings | 唯一失败为旧状态文案断言；针对同一测试补跑 1 passed |
| PostgreSQL A1 回归 | 37 passed，57.16 秒 | suggestion 通配符、刷新与删除、事务、策略和导入故障 |
| 完整 non-E2E 一次 | 4382 passed / 62 failed / 15 skipped，52 warnings，624.86 秒 | 原始结果完整保留，不能写成全套绿色 |
| 原失败项最终补跑 | 70 passed / 1 failed，16.34 秒 | 原 61 个失败已解决；旧导入参数扩展为 12 个 fault/caller 组合；只剩历史 AI 证据缺失 |
| A1 契约与文档门禁测试 | 4 passed | 既有日志保留；实际证据投影与注册契约在交付时再次运行 |
| 真实 Edge | 五样本图片全部通过，20 次搜索输入及一次来源 chip 跳转 | 界面、图片尺寸、API/DOM 集合、实查截图关联同一候选 |

历史失败为 `tests/test_phase45_scv2_sv1_controlled_scale_promotion_readiness.py::test_ai_accounting_keeps_original_and_current_invocation_separate`，明确报 `missing_original_ai_execution_evidence`。相关历史实现和断言相对起点没有修改；A1 不伪造原始 AI 执行证据，也不为清零失败重跑 provider/LLM。其他失败包括当前状态断言落后于 A1、旧 registry 计数与新事务撤回衔接、SQLite 布尔默认值、缓存版本、测试进程环境及临时目录命名约束；修复及针对性补跑已保留。

完整套件实际使用 `-m pytest tests/ --ignore=tests/e2e --basetemp=…/full-suite-temp -q`；完整绝对参数和原日志位置写入本机操作记录。PostgreSQL 使用本机 `run_pg_tests.py`（内部调用本仓库 `tests/test_production_pixiv_a1.py`）；本次补跑脚本按原日志提取失败测试，不重跑整个套件。日志索引及验证计数由 `validation-private.json` 绑定。

交付门禁：`check_production_pixiv_a1.py`、注册 `check_phase_contract.py --contract production_pixiv_a1_v1`（附本机 evidence）、`check_documentation_state.py --check`、JSON/UTF-8/脱敏核对及 Git diff 检查。本地结果不等于 GitHub CI，不赋予合并权限。

## 当前规模

对既有 eligible metadata 作只读结构测量：465 works 中 **453 works / 455 pages / 5,388 signals** 与当前产品构建器兼容；12 works 被 `px1_artist_context_invalid` 拒绝。根因是部分 artist 类型信号仍带页面上下文，而 PX2 作者身份输入契约要求无该上下文。保留拒绝，不放宽身份约束凑全量通过；五个原库样本不在此缺口内。

453 works 结构生成 4,081 clusters、5,092 member signals、15,221 candidate dispositions（3,086 must-link、507 cannot-link、11,628 deferred），28,014 ambiguity records。组件大小分布为 `1:3759, 2:2, 3:241, 4:2, 6:48, 7:1, 9:18, 12:5, 15:4, 21:1`；构建约 15.373 秒，无 OOM，provider/LLM 请求为 0。此处是只读结构测量，**没有把 453 works 全量 apply**。

复用历史独立 240-query workload，预热一次、测量三次，共 720 次，在同一恢复副本、同样五个持久化样本上比较 A1 查询优化前后。旧 SQL 的复合相关 OR/EXISTS 对全库 Media 重复扫描；拆分直接证据、信号和绑定 Media 子查询后，全部 720 次结果集合不变：

| 指标 | 优化前 | 优化后 |
|---|---:|---:|
| p50 | 6.285 ms | 6.51 ms |
| p95 | 581.703 ms | 33.377 ms |
| 最大 | 3394.554 ms | 72.985 ms |

通过沿用的 p95 ≤ 750 ms、最大 ≤ 3000 ms 标准。历史 workload 的答案不能直接提升为当前全量 metadata 质量；此次证明同库结果保持与性能变化，也不承诺历史 8.6 ms。

## A2 的具体输入及剩余边界

1. **metadata 缺口：**9,004 个无歧义候选 works 中 8,521 尚无现有真实 metadata；这是候选分母，需要先确认来源。另 1,450 个 Media 的多 work 先验须单独裁定，不能直接导入。已确认真实 metadata 的 distinct works 为 526。
2. **缓存：**当前通用 ProviderCache 只有 2 行，均为已有 SauceNAO `reverse_search_derived_image`，没有可直接声称覆盖缺失 Pixiv works 的缓存。A1 复用既有 Pixiv SourceMetadata，未将这两行误算成 Pixiv 完整缓存，也未访问 provider 凭据或发起新请求。
3. **裁决链：**当前 `build_pixiv_clustering()` 仍显式禁用 LLM，`max_calls=0`、`llm_judgments=()`。A2 需按实际版本/输入身份连接已接受的兼容 judgments 与完整裁决步骤，并补齐上述 12 个 artist-context 不兼容 works；不能把 A1 确定性聚类称为完整模型裁决。
4. **选择所有权：**当前仅一个固定 1% active selection。全量或累计选择会遇到 overlap/已有支持所有权限制，需要正式增量或迁移策略，不能直接删 run、改 status、重置旧 core 图，尤其不能撤回已有独立消费者依赖的支持。
5. **别名与身份质量：**实际 UI 中 `絆` 展示多个来源概念并返回 4 个合法 Media；`荧` 的多个观察记录与 `Lumine` 都能在稳定作者约束下返回 Media 846。这些包含作者、标题和角色不同职责，仅凭相同表面词不能合并，也不能称为错误合并。A2 需依据稳定 ID、角色、上下文及已有证据区分：有证据应合并但分裂、真实冲突、只需检索等价、尚无证据。此次五样本中未确认新的错误身份合并；也未把尚缺独立身份依据的候选宣称为已修好的别名碎裂。确定性旧名同作者合并和异作者同名分离已有独立 fixture 证明，不能替代全量质量审查。

A3 才连接日常新增/刷新/失败恢复的既有同步与队列。A1 未执行 A2/A3、第二 provider、视觉相似检索或角色传播。

## 本机证据与恢复

本机操作记录复用唯一 A1 证据目录，含实际数据库/存储身份、备份与恢复位置、迁移、五样本清单、run/selection、API 响应、重放/撤回、launcher profile、精确恢复命令及浏览器索引。其完整绝对路径在本次交付回复提供，私有原始 payload、图片路径、截图和认证资料不提交公开 PR。

正常恢复优先使用候选的正式 owned rollback，再恢复保留的旧 launcher 锚点及原配置；additive schema 可保留，不覆盖恢复原数据库。后续发生独立消费者时以正式接口的所有权拒绝为准，不能强删。具体步骤已写入本机记录，本次交付保持最终可用样本，不执行回退。

工程判断：A1 可以交付项目负责人复审，生产候选与五样本真实使用链路已验证。完整 non-E2E 保留一项明确历史证据缺失，空结果侧栏有低影响旧文案问题；全量 metadata、裁决链、累计选择及日常持续同步仍是 A2/A3，不能由本报告自动授权。
