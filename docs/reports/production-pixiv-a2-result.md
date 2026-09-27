# VIOLET A2 2026-09-27 执行结果：门禁修复完成，身份纠正未达成

本轮未达到 A2 工程通过。三个发布准入缺口、括号目标纠正边界和真实回答解析已修复并验证；两个真实回答仍将已证实泛称认作作品或角色，不能作为成功纠正发布。保留可用旧生产，target_met / safe_to_merge / route_approved 均为 false。

同一 PR #153 / codex/production-pixiv-a2。业务代码 HEAD：`6d520d88f690b96bb6c93046fa268bd7dfc28ee0`；此前实现提交：`4faa9a69bdee1252a546b4b6ab1f6c4b86465c64`。开工本地/远端 `9676dff65cb6d8deaf6c025097ce40c3b35a6f49`，可信 origin/main `2b742ca3e49d4b7d361300e98e0b2d9c1a0eb63d`。旧84行为候选的80/80属于历史有限口径，当前生产为 `44db0da0c1df2fe38434cacc57308f2c0e33ec0f`。

## 已完成实现与实际验证

- T0 独立锚定：原备份只读导出的 COPY 数据重建38,114 Media、3,201 Pixiv metadata，逐项等于原查询；删除清单行并同步缩分母的反例被拒绝。未用今天数据库充当T0。
- 同名作者：从实际provider+creator ID、当前revision支持和Media重算。クルル的10758468/2505446分别13/1 Media，HAMU的109042349/66884372分别1/1 Media；两侧概念分离，裸名查询各等于14/2 Media并集。伪造附件、重复账户不能通过。
- 替换前闭合：服务事务边界在撤回旧支持之前检查逐Media终态、可靠来源及完整绑定数量。真实PG反例确认同页缺项或未闭合时旧投影不动。现有固定范围归宿为8623 complete、850 terminal remote、1455文件名冲突、27157不适用、29页不匹配。
- 仅本任务两目标可纠正；独立角色事实、作者、范围外目标仍受保护。non_name和unknown的实际角色投影及原始响应来源重放均有回归。保留原括号base/context和原模型事实。
- 精度控制独立于原80例：来源冻结1737反例、784反向独占、5147真实多角色同图，并保持3915/5256/5651/718/714/715。新门禁实际拒绝旧84的nahida误召回；注册契约在缺少本候选完整生产证据时实际拒绝 `a2_behavior_carry_forward`。
- 全量选中31,612判断有界筛查得到5项关键词线索；两项为合法名称形式，已知泛称桥接成立；两条Fate可疑理由经实际副本307条相关绑定复核没有合并为同一概念，未扩展付费目标。

## 交付前新增75线程复核

远端自动新增4条实际意见，未主动触发reviewer。当前全部75线程逐项对账；新增四项已修复：身份判断改从实时数据库重建名称/角色/概念/Media关系并与质量附件按multiset核对；精度来源从证据根限制路径读取，验证实际SHA、每case摘要和数据库身份；隔离库部分演练按选择work计分母并验证实际数据库名，原生产仍禁止部分替换；生产alias与R2R fallback分别受各自开关控制，同类QA路径同步修复。

修前8失败/2通过、修后98受影响检查及63搜索回归均保留。开发PG首次95通过/1失败是新增测试错误把工作910000001的2条Media期望写为3，已依据实际固定映射纠正；不是放宽生产门禁。来源校验首次把observed状态误当作内容不完整而拒绝784/1737，已改为核对原始完整Pixiv response本身的work/page、title/tags/user。冻结来源字节与hash不变，不能用归一化工作流状态否定既存完整原始证据；真实源身份不匹配/字段缺失有独立反例。

当前精确业务HEAD通过515 focused/1权限skip及96真实PG/API。只读实际收集66,572条现有投影，伪造附件立即拒绝；来源hash和数据库身份校验通过，要求的784/1737/5147均有完整原始记录；旧84精度反例及当前缺生产证明的注册契约仍实际拒绝。原模型费用、原逻辑寿命、旧44运行及无新生产apply不变。准确命令使用correction27-review75-source-final前缀；旧450/93和513/96均为相应历史提交，不合并计算。

## 六次原始响应与零调用恢复

两个批准请求各3次，共6次真实调用，gpt-4.1-mini / fallback OFF。初始及正常schema修复均继承原logical key；累计18,433调用，0在途，54未知usage记录保守记账。新增USD0.006785，累计USD13.589429/30。本轮整体估算USD2.998965，预留USD9，原余额足够，未上调总额度。

提示词要求括号拆分base，旧覆盖检查却只匹配完整字面；这是本轮发现的解析漏接，导致两次不必要的技术重试/目标。保留原失败，修复唯一来源绑定后按原时间顺序零调用恢复首个完整回答，后两次不替换先前回答。六份raw字节、wire/request hash、usage和结算前后账本全部保留。

第一目标实际返回work_title/0.9，第二目标character/0.8。没有返回non_name；不能手工改写或继续追问到期望结果。完整100,285信号离线对比中，直接角色变化2条，作品解析后变化8条，模型payload依赖变化12条；原31,612判断有8条输入失效，31,604条直接依赖不变。失效包括已知3条错误must-link，也包括合法别名与cannot-link，因此不能只手工删3条。旧所有正/负/unknown原回答保留；31,604是直接依赖诊断，不等于最终选择准入通过。

依赖诊断首版漏加production namespace，错误得到0失效；第二次的2条断言未容纳派生上下文变化。两个失败和第三次修正结果都保留，以v3为准。未把诊断当作最终新图、可重用cache或已完成的新副本。

## 精确验证与现状

业务HEAD的focused为515 passed、1 skipped（Windows权限节点），真实PostgreSQL/API为96 passed、0 failed。准确argv、环境身份、退出码和JUnit在 `correction27-review75-source-final-*-command-private.json` 与同名前缀XML/log。此前4faa9a6的443/1和93、开发失败及修前9 failed/1 passed均留存。未重跑全套non-E2E；原历史AI证据例外不扩展。

开工旧服务已停止、投影完整；通过原无参数EXE点击Start恢复可用，profile未改。当前PID52900、8012、旧44、read ON/apply OFF，66572支持/8623绑定Media，38114总Media，无尾部新增/固定缺失/活动工作。实际117全分页查询与旧基线一致，nahida不含1737。系统Edge完成三图原图/缩略图/来源chip、标签/suggestion、搜索及恢复页，0页面错误；截图已实际查看。完整原启动EXE祖先因便携进程退出未留齐，不声称新候选正式正常入口验收。

新角色答案未形成可证明的语义纠正，依据任务2.1停止扩大后续调用。新集中副本plan/apply/replay/rollback/reapply、新生产apply、240 workload和新候选完整产品契约未执行；包内旧完整恢复、80例、性能/正常入口证据均保持原HEAD和历史身份，不改标。旧44本身保留其原有70/80局限，运行可用不等于本轮A2达标。


<!-- CURRENT_PHASE: PRODUCTION-PIXIV-A2 -->



75条线程已逐项对账：原71条保留并更新验证，后续4条实际意见已实现与验证；不resolve、不触发reviewer。单文件包 `correction27-final-review-EVIDENCE.zip` 含原包核心及本轮决定性输入、代码、raw、完整页、失败和独立复算脚本；实际尺寸/hash与干净解压结果在包外同名交付收据及最终回复，避免自引用哈希。

未merge、未推main、未force、未A3；未改Entity、原媒体、缩略图、人工标签、源/iCloud目录或provider路由，未新增Pixiv获取。数据库写入仅隔离测试schema，原生产投影未替换，未覆盖备份；保留debug.log。

下一项需要Lead对已完整披露的work/character实际回答作语义裁决，并给出是否调整这两个目标的纠正方法及原逻辑寿命后续规则。现有授权不允许无限重问或人工补答案。工程判断：门禁可信度已提高，但身份事实没有闭合，继续发布会把未经证明的泛称身份写入生产，维持明确失败更符合本任务约束。


以下保留历史记录。

## 历史：已撤回84候选与44基线恢复

本轮工程目标未达成。两项适配修复让既定80例通过，但额外命中追溯发现一个原有限精度集未覆盖的真实身份误合并；因此没有宣告工程完成。当前已用新鲜plan/实际apply恢复原A2 `44db0da` 的派生投影和正常EXE入口，未恢复覆盖数据库备份，也未退回A1。`target_met=false`、`safe_to_merge=false`、`route_approved=false`。

同一 [PR #153](https://github.com/kyloris0660/VIOLET/pull/153)、分支 `codex/production-pixiv-a2`；业务候选 `84f999274e0acd04b34ccd1afa1d476a54dc7fce`，当前原生产候选 `44db0da0c1df2fe38434cacc57308f2c0e33ec0f`。报告生成 `2026-09-22T16:55:11.007383+00:00`。最终文档HEAD与这些实际执行身份分开记录。Lead复审及Owner使用验收仍待定。

## 两项适配的实际收益

稳定provider/work来源没有进入数据库无关信号的独立计数、评分使用原始作品上下文而聚类使用已接受作品组件，这两个缺口均成立且共同影响784/5147。四组固定全输入对照分别得到17538、17493、17525、17496个概念，均为100285信号/626779边。单独改一项不足；共同修复让既有日中正向判断通过原短名guard，784/5147各通过三步实际路径进入中文组件。

判断 `75904f904e1441179b43d5ab78a35cc07639c8ccc9d5d54f` 两端作品context均为空、置信度.9；原条件 `ambiguous_short_without_work_context` 拒绝，共同修复后实际union。784描述标签仍是work/needs_review并进入作品候选，5147仍无作品context，未补造所属。完整profile贡献、四组对照、guard值、硬约束及union森林均在包内。

实际旧副本到当前副本，仅“纳西妲”“草神”各127→129，增加784、5147；3915原本已有中文API支持，5256保持，5651为BlueArchive控制。66394条支持总数保持，0新增/0移除，1277条保留支持投影变化，其中16条还改变状态；65117条不变。旧原生产到当前固定输入是66572→66394，316移除、138新增、21836条保留投影变化，分母不同，不归因于本轮两图修复。

## 新发现的实际精度失败

当前组件258条信号中含纳西妲名称，也含57条雷電将軍、10条雷电将军及其他Raiden名称。两条 `魔神(原神)` 来自不同真实作品101686613/128709025，带括号作品上下文；三条旧must_link回答分别把该泛称当成草神/Nahida/Raiden的同一角色名称。同名锚点再连接两条泛称，造成跨角色传递合并。

其中一份原回答明确称魔神是Archons的general term，却仍返回同一角色；另两份也以类别/成员关系支持identity。没有直接Nahida↔Raiden的合并边；包内原/共同修复图各10步实际union路径给出完整桥接，而非仅展示相关标签。四份原pair缓存字节、原问题/回答、置信度、输入、reuse链与哈希已内嵌。

Media 1737 的原来源标题为雷電将軍、当前绑定也是该名称，却因上述组件在“ナヒーダ”查询中新增命中。旧原生产44的该查询67条且不含1737；43号副本已经128条并含1737；当前副本和短暂发布的当前原生产均129条且含1737。故问题在本轮两项适配前的副本已存在，不能归咎于新增784/5147，也不能把所有额外命中都称为合法同名/共现。这里证明的是当前identity桥接依据不足及其实际检索后果，不作全库视觉真值宣称。

当前原生产相对旧原生产有15个冻结身份查询变化、210个唯一变化Media，完整分页与来源已留存，支持revision不匹配为0。历史收据没有顶层total，已如实保存null并另列实际留存ID数；没有补造历史计数。汇总曾因此出现KeyError，原失败日志保留，随后仅兼容证据格式，从该步骤续跑。

## 已通过的检查与未通过的整体结论

| 项目 | 实际结果 |
|---|---|
| focused | 341 passed、1权限skip、0 failed |
| 实际PostgreSQL/API | 89 passed |
| 原历史失败节点精确复验 | 88 passed；集合重叠，不相加 |
| Windows补修 | 双线程20轮通过，真实junction越界仍拒绝 |
| 固定副本 | 六次plan/apply入口、replay、rollback/重复rollback、reapply、逆序分批、source更新/删除恢复、17表保护通过 |
| 质量 | 副本和短暂发布原生产各80/80、各3/3保留样本；18个原有限独立精度控制无禁止命中，但未覆盖本次真实失败 |
| 原生产运行 | 5个非唯一索引迁移及幂等保护；新plan4284.891秒、apply4379.203秒；66394支持/8623 Media/1 active/0重复 |
| 真实界面 | 当前候选普通无参数EXE真实点击、完整来源链，Edge详情/大图/返回、来源chip、旧标签URL、可见suggestion与API、只读恢复页通过，0页面错误 |
| 性能 | 来源层p95 547.011 ms、最大1449.535 ms，低于750/3000门槛；HTTP p95 2119.121 ms、最大4015.258 ms另列 |
| 阶段契约 | 注册契约在有限既定条件上返回true；发现真实精度失败后，停止尚在运行的完整17项复算，没有最终17项全通过文件，整体target仍false |

历史一次full non-E2E保持4541 passed、89 failed、15 skipped；88节点当前精确复验，唯一历史AI原证据缺口保留既有裁决。没有再跑全套或为缺历史证据付费。Python身份预检通过，实际解释器 `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`。精确命令如下，对应.log/.xml在包内：

```text
C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_production_pixiv_correction_gates.py tests/test_production_pixiv_review68.py tests/test_production_pixiv_ambiguity_adapter.py tests/test_production_pixiv_a2_evidence.py tests/test_production_pixiv_review63.py tests/test_production_pixiv_release_inputs.py tests/test_production_pixiv_role_extraction.py tests/test_phase45_scv2_r2_constraint_aware_graph_remediation.py -q --junitxml=production-pixiv-a2（本轮工作树）\.local_manifests\pixiv-a2\correction21-84f9992-focused.xml
```
```text
C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_production_pixiv_a2.py tests/test_production_pixiv_a2_api.py tests/test_production_pixiv_a1.py -q --junitxml=production-pixiv-a2（本轮工作树）\.local_manifests\pixiv-a2\correction21-84f9992-real-pg-v2.xml
```
```text
C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_admin_dynamic_sync_ui.py::test_dynamic_sync_ui_has_persistent_progress_and_confirmation_actions tests/test_current_handoff_freshness.py::test_active_markers_and_contract_commands_are_consistent tests/test_current_handoff_freshness.py::test_conflicting_current_marker_fails_closed tests/test_current_handoff_freshness.py::test_documentation_checker_returns_current_phase_result tests/test_current_handoff_freshness.py::test_handoff_is_exact_generated_projection tests/test_current_handoff_freshness.py::test_live_git_binds_pr148_merge_and_px3_implementation_evidence tests/test_pd1a_mainline_governance.py::test_current_mainline_roadmap_persists_px3_boundary_and_fixed_route tests/test_pd1a_mainline_governance.py::test_handoff_points_to_current_mainline_roadmap tests/test_phase45_doc1_documentation_state.py::test_a1_authority_cannot_expand[llm] tests/test_phase45_doc1_documentation_state.py::test_a1_authority_cannot_expand[provider_network] tests/test_phase45_doc1_documentation_state.py::test_a1_authority_cannot_expand[truth_mutation] tests/test_phase45_doc1_documentation_state.py::test_a2_state_and_active_docs_validate tests/test_phase45_doc1_documentation_state.py::test_current_handoff_is_exact_a2_projection tests/test_phase45_scv2_a1_post_expansion_audit_route_decision.py::test_handoff_roadmap_and_test_workflow_updates_are_factual tests/test_phase45_scv2_ml1_multilingual_alias_source_metadata_closure.py::test_durable_documents_encode_corrected_search_semantics tests/test_phase45_scv2_r1_post_px1_source_concept_triage.py::test_handoff_and_roadmap_follow_current_phase_state_not_r1_history tests/test_pr152_bounded_fix.py::test_bad_subdirectory_preserves_healthy_execution_and_unknown_continuation tests/test_pr152_bounded_fix.py::test_import_exception_reason_once_and_healthy_continues[copy-read_error] tests/test_pr152_bounded_fix.py::test_import_exception_reason_once_and_healthy_continues[decode-import_failed] tests/test_pr152_bounded_fix.py::test_import_exception_reason_once_and_healthy_continues[http-import_failed] tests/test_pr152_bounded_fix.py::test_import_exception_reason_once_and_healthy_continues[process-import_failed] tests/test_pr152_bounded_fix.py::test_import_exception_reason_once_and_healthy_continues[timeout-read_timeout] tests/test_pr152_bounded_fix.py::test_readdir_order_changes_between_public_private_and_execute tests/test_pr152_bounded_fix.py::test_stored_hash_requires_current_bound_version[False-False] tests/test_pr152_bounded_fix.py::test_stored_hash_requires_current_bound_version[True-False] tests/test_pr152_bounded_fix.py::test_stored_hash_requires_current_bound_version[True-True] tests/test_pr152_recovery_state_io.py::test_api_without_attempt_reads_current_metadata_not_stale_columns tests/test_pr152_recovery_state_io.py::test_app_media_followup_does_not_resolve_unneeded_source tests/test_pr152_recovery_state_io.py::test_completed_app_copy_failure_retains_downstream_and_retry tests/test_pr152_recovery_state_io.py::test_completed_media_survives_missing_observation_and_changed_source_reenters tests/test_pr152_recovery_state_io.py::test_defer_after_newer_observation_does_not_bind_old_attempt tests/test_pr152_recovery_state_io.py::test_existing_followup_without_source_hash_executes_only_missing_stage tests/test_pr152_recovery_state_io.py::test_existing_media_copy_precommit_interrupt_recovers_in_new_session[direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_copy_precommit_interrupt_recovers_in_new_session[update] tests/test_pr152_recovery_state_io.py::test_existing_media_downstream_survives_post_hash_failure[copy-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_downstream_survives_post_hash_failure[copy-update] tests/test_pr152_recovery_state_io.py::test_existing_media_downstream_survives_post_hash_failure[decode-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_downstream_survives_post_hash_failure[decode-update] tests/test_pr152_recovery_state_io.py::test_existing_media_downstream_survives_post_hash_failure[http-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_downstream_survives_post_hash_failure[http-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[existing_complete-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[existing_complete-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[existing_gap-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[existing_gap-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[existing_localization_gap-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[existing_localization_gap-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[http409_complete-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[http409_complete-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[http409_gap-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[http409_gap-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[http409_localization_gap-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[http409_localization_gap-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[new-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[new-update] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[post_commit-direct_legacy] tests/test_pr152_recovery_state_io.py::test_existing_media_switch_or_deduplicate_completes_target[post_commit-update] tests/test_pr152_recovery_state_io.py::test_first_import_failure_has_no_completed_media[copy] tests/test_pr152_recovery_state_io.py::test_first_import_failure_has_no_completed_media[http] tests/test_pr152_recovery_state_io.py::test_hash_real_consumers_persist_string_and_diagnostics_and_continue tests/test_pr152_recovery_state_io.py::test_legacy_media_changed_version_reenters_without_update tests/test_pr152_recovery_state_io.py::test_priority_resolve_worker_reaped_identity_retained_and_healthy_executes[failed] tests/test_pr152_recovery_state_io.py::test_priority_resolve_worker_reaped_identity_retained_and_healthy_executes[skipped_duplicate] tests/test_pr152_recovery_state_io.py::test_priority_resolve_worker_reaped_identity_retained_and_healthy_executes[skipped_existing_media] tests/test_pr152_recovery_state_io.py::test_priority_resolve_worker_reaped_identity_retained_and_healthy_executes[unchanged] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[current-defer] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[current-ignore] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[current-terminal] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[null-defer] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[null-ignore] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[null-terminal] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[stale-defer] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[stale-ignore] tests/test_pr152_recovery_state_io.py::test_recovery_api_update_plan_new_session_and_proven_change[stale-terminal] tests/test_pr152_recovery_state_io.py::test_unknown_version_defer_first_fill_then_real_change tests/test_production_import_recovery.py::test_enqueue_then_crash_preserves_every_unattempted_identity tests/test_production_import_recovery.py::test_history_failure_count_is_reconstructed_from_versioned_outcomes tests/test_production_import_recovery.py::test_independent_failures_never_truncate_healthy_candidates[positions0] tests/test_production_import_recovery.py::test_independent_failures_never_truncate_healthy_candidates[positions1] tests/test_production_import_recovery.py::test_independent_failures_never_truncate_healthy_candidates[positions2] tests/test_production_import_recovery.py::test_missing_unlinked_noop_is_observed_and_reenters_when_source_returns tests/test_production_import_recovery.py::test_private_recovery_bounds_large_discovery_and_keeps_missing_link_identity tests/test_production_import_recovery.py::test_private_recovery_endpoint_and_owner_reentry tests/test_production_import_recovery.py::test_production_missing_models_preserves_import_and_pending_downstream tests/test_production_import_recovery.py::test_proven_worker_start_failure_preserves_remaining_work tests/test_production_import_recovery.py::test_real_cap_one_runs_reach_old_retry_tail tests/test_production_import_recovery.py::test_stat_failure_records_exact_listed_identity tests/test_scv2_fl1_i2_validation_receipt.py::test_head_or_tree_drift_never_issues_positive_receipt tests/test_scv2_fl1_i2_validation_receipt.py::test_same_head_receipt_binds_all_evidence -v --tb=short --junitxml=production-pixiv-a2（本轮工作树）\.local_manifests\pixiv-a2\correction21-84f9992-validation-historical-remediation.xml
```

## 原生产安全恢复与当前入口

先验证旧44工作树干净、profile与本轮备份逐字节一致，再复制7份必要旧输入并校验SHA，共231167147字节；这些是回退输入，不是冗余全量压缩包。旧版新鲜计划的run key及业务结果指纹与历史原生产完全相同。确认无活动用户工作后，停止本应用并由旧44产品入口实际apply，恢复66572支持/8623 Media/1 active/0重复；前后17表保护通过。

恢复apply耗时 1644.375 秒。当前run key `scv2-px3:762dd305bc4c468ae65df18213f5861f`。配置锚点回到 `production-pixiv-a2-stable-44db0da`，使用原日常无参数EXE实际启动；当前API PID 43488、端口8012、DB blombooru、read ON/apply OFF。复查全部 117 个旧原生产查询，完整ID集合与旧收据相同，1737不再被“ナヒーダ”命中。恢复后真实系统Edge再次验证，0页面错误。

恢复后首轮Edge采集在详情页networkidle等待30秒超时，原始失败日志和不完整收据保留；其与117查询复核并发，不能据此确定超时根因。查询完成后，同一未改动浏览器驱动单独重试，成功证据使用surface-v2独立编号，不覆盖首轮失败。启动器首次观察为空白，刷新并置前后观察到完整页面，再实际点击一次Start；其现场过程也保留。

当前原A2恢复的是此前已运行版本及其已知70/80边界，不宣称新候选已被接受。旧44和本轮84代码/输入/失败收据均保留。隔离副本8013已停止、端口释放，DB/存储/原始证据保持；生产8012按交付目标继续运行。

## 具体裁决与有界方案

现有 `production_pixiv_corrections.py` 对 `signal.parenthetical_context` 或独立角色提示直接触发 `semantic_correction_cannot_override_independent_strong_fact`。两条泛称均具括号上下文，因此现行纠正路径会保护它们。不能私下绕过保护、手工改缓存或硬写身份关系。

需要裁决的是：是否区分“括号仅给出作品上下文”与“独立来源确认角色类别”，保留作品上下文本身及真正强角色证据，只让这两个有具体冲突的泛称进入现有有界角色纠正路径。方案已构建两个实际文本请求，原逻辑尝试均未耗尽；按请求字节保守估计输入和6000输出上界，合计预留USD 0.026805，实际调用0。

若批准该适用范围纠正，应保存实际新答案及supersedes，仅失效依赖变更角色输入的旧判断；再重建选择、兼容复用和必要缺项，执行受影响完整图、该反例精度控制、副本恢复及原生产门禁。不能预设模型必然给出期望答案，不降低原80例、不删可靠cannot-link、不扩大总预算、不更换provider/model。若维持现保护范围，则保留当前旧A2生产和明确未达标结论。

## 审阅意见、文件与费用

68条线程保持未解决状态，未新增reviewer或自行resolve。63条历史处置保留；本轮5条的指定反例/回归实现已完成，其中精度意见的实际覆盖仍有本次缺口，不能称总体质量闭合；其他4项现场依据已采集。最新候选和最终文档HEAD尚未取得独立复审。

相对79e2b638的冻结18文件如下；最终只续接状态、生成交接、报告和结果JSON：

- `backend/app/services/production_pixiv_pair_correction.py`
- `backend/app/services/production_pixiv_release_provenance.py`
- `backend/app/services/production_pixiv_role_extraction.py`
- `backend/app/services/production_pixiv_service.py`
- `backend/app/services/source_concept_resolver_service.py`
- `docs/current-handoff.md`
- `docs/development/agent-runbook.md`
- `docs/plans/production-pixiv-a2.md`
- `docs/state/current-phase.json`
- `scripts/check_production_pixiv_a2.py`
- `scripts/production_pixiv_a2_evidence.py`
- `scripts/production_pixiv_a2_service_evidence.py`
- `scripts/run_production_pixiv_a2_concepts.py`
- `tests/test_production_pixiv_a2_evidence.py`
- `tests/test_production_pixiv_ambiguity_adapter.py`
- `tests/test_production_pixiv_correction_gates.py`
- `tests/test_production_pixiv_review63.py`
- `tests/test_production_pixiv_review68.py`

31,612对最终问题及回答全部兼容复用：9,070作品阶段+22,542后续阶段，29,006 exact_compatible+2,606 same_decision_input_new_occurrence，无新增请求。45,587初始候选不是最终分母。本轮Pixiv/模型调用均0；账本仍18427次、USD13.582644、54项未知usage，剩余USD16.417356。令牌未轮换但Owner已授权本轮使用，未宣称轮换；A2 fallback OFF，未向应用模型上传图像。

已读取本轮任务/复审报告、既有有效纠偏授权、current-phase及持久计划、runbook、相关实现与测试。持久服务及回归/门禁属于长期代码；本轮恢复、取证与打包脚本属于阶段工具；日志、原metadata、快照、截图、缓存、备份和ZIP属于本地私有证据，不提交Git。完整大文件留本地索引；关键实际记录在单ZIP中，不依赖外部下载。

## 工程判断与停止边界

已修复的漏召回收益成立，但原有限精度控制不足以保证组件正确性。本轮在真正发现额外身份桥接后撤回发布结论、恢复先前A2，没有继续叠加未经裁决的身份规则或反复付费。下一步只需对上述括号强事实适用范围作具体裁决；不是泛化继续审计或增加预算。

没有push main、merge、force-push、进入A3、源/iCloud/staging修改、原图/缩略图/人工标签/相册/Entity真值修改、清理/reset/drop/truncate或覆盖恢复原库。仅按本阶段授权执行owned派生投影切换和非破坏性索引迁移；停止的是本任务测试及验证进程。最终文档提交、同分支push和单ZIP实测结果在最终交付记录另列。

## 实测单文件交付

`correction22-final-review-EVIDENCE.zip`：92019084字节（92.019084 MB），SHA-256 `f53bacb952190de58a6ec833d55e40f95f2d07edc1022fb928705cb3f682cb68`。实际解压、逐文件hash、索引、JSON/JSONL/XML及独立复算通过。
