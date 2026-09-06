# 生产 Pixiv A1 交付报告

更新：2026-09-06，PR #151 有界修正完成。项目所有者为 `kyloris0660`，负责需求、资产及操作授权；项目负责人为本次 VIOLET 接手与路线规划的 ChatGPT 会话，负责路线及交付复审；执行代理为当前 Codex 会话，负责实现、已授权操作与工程证据；本轮审查者为 `chatgpt-codex-connector[bot]`。产品用户指实际使用 VIOLET 的主体。

## 当前交付结论

本轮三组修正和新候选的工程验证已完成。来源正常更新或删除后，受控撤回可由正式 rollback 核对归属并清退旧 run，再以新计划 apply/replay；软别名与详情 chip 同步失效。提交成功后的导入可以恢复持久化 Media、登记来源与导入结果，并进入已有下游。候选证据现在要求实际 Git 行为差异及精确测试节点对应。

- 仍使用正常 [PR #151](https://github.com/kyloris0660/VIOLET/pull/151)，分支 `codex/production-pixiv-a1`，起点 `26a6fc8d30ba2b2eae69f55a8e7c33d5a4b9cdd3`。
- **实际运行、最终 focused / PostgreSQL / 精确节点补跑、真实 Edge 的代码候选均为 `7dd8733ef0963458af6611a2d947fb040e4b5412`。** 最终 PR HEAD 在 PR 正文及本机交付身份记录中列明；该候选之后仅有报告、脱敏摘要、state 和生成 handoff 的文档变更，不自引用尚未形成的文档提交 SHA。
- 生产由原日常 launcher 的既有锚点启动，仍使用原数据库、原存储和既有 venv。控制器管理 PID 69328、服务监听 PID 21280，健康和 schema 检查通过。读取开启、apply 关闭，仍为固定 5 works / 5 pages / 5 Media、1 active run、51 条绑定。
- 项目负责人接受仍 **pending**；执行代理不代称项目所有者亲自使用验收、审查者接受或 PR 合并。本地契约 `target_met=true`，`safe_to_merge=false`、`route_approved=false`，不代表完整模型裁决或 GitHub CI。

本报告是唯一持续更新的 A1 报告。审计前 `8ec23cd…` / `9d66aa…` 正文在末尾完整保留为历史；原日志与提交均保留。先前将刷新闭环写为 resolved，仅证明了失效，未覆盖失效后正式撤回及新 apply；先前 70 passed 的文件级汇总也不足以证明原 61 个失败逐项覆盖。**这两项过早结论由本轮实际 API 生命周期和逐 node 对账补齐，不能把新证据倒填给旧候选。**

## 八条审查意见裁决及实现

| 审查意见 ID | 项目负责人裁决与执行代理结果 | 对应代码及验证 |
|---|---|---|
| 3942919558 | 本轮修复：区分系统受控撤回与未知所有权变化 | `source_binding_revision.py` 在既有 run guard 中记录精确 before/after；`pixiv_product_integration_service.py` 仅在每行仍匹配撤回后状态时重建原始所有权比较输入，原 ownership fingerprint 不改写。`test_existing_source_api_refresh_formal_rollback_and_replacement` 在隔离 PostgreSQL 通过真实 existing-source API 验证更新/删除、fresh-session、正式 rollback、新计划 apply/replay、事务回滚、独立支持及消费者拒绝 |
| 3942919569 | 本轮修复：注册表软别名随稳定来源身份同事务撤回 | 触发器按 `SourceNameAliasCandidate.evidence_payload.provider_record_key` 和所属 provider 处理；registry 保留撤回前活跃 ID，正确统计 retired 数。`test_registry_soft_alias_source_withdrawal` 覆盖 ORM、registry、直接 SQL 更新/删除及回滚；独立旧名仍能合法命中，不能借另一个仍有效的名称串到错误 Media |
| 3942919568 | 本轮修复：最后有效别名消失后无旧搜索动作 | `_concept_summary()` 的 search_label/value/url 为 null，显示“当前来源名称已撤回”；`media.js` 渲染无链接、无搜索事件的禁用 chip。API 回归及独立真实 Edge 模板点击均通过 |
| 3942919560 | 本轮修复：已提交导入恢复登记及下游 | `media_commit_boundary.py` 验证 Media ID/hash/path；manual-sync 外层恢复后继续 DynamicSourceItem/run 登记及既有下游；scanner 记录计数和 imported_media_ids；upload/chunked 返回 202 和可恢复状态，direct 异常携带持久化身份。完整外层故障/重复执行回归通过 |
| 3942919562 | 本轮修复：行为相关未跟踪文件阻止证据续用 | `trusted_git.candidate_behavior_carry_forward()` 由 launcher、A1 checker 和 state 共用，复用既有分类器；docs 内 module/package/config 不自动安全，被代码引用的普通材料也拒绝。独立反例通过；无关截图、Markdown、日志保留 |
| 3942919563 | 本轮修复：精确 node、命令和 PASS 对账 | checker 解析原始 FAILED 及补跑逐项 PASSED，核对实际 argv、候选及显式参数映射；同文件任意通过、映射缺项、命令不符均拒绝。下文列出全部 62 个原节点 |
| 3942919566 | 本轮修复：双 state 候选字段与真实证据一致 | state、公开结果、launcher、测试命令、浏览器均绑定本轮代码候选；对象必须真实存在且后继行为差异可续用。伪造对象及任一字段不一致均有拒绝用例 |
| 3942919571 | **本轮不适用**；依据项目负责人明确裁决 | 实际 Git 链仍为 base → 8ec23cd → 3f529f6 → 9d66aa → 本轮候选及文档后继，未丢失祖先。项目负责人后续优先普通 merge；未扩展同树 squash 或通用 ancestry 兼容能力，执行代理未合并 |

危险来源更改和导入故障仅发生在任务测试环境。撤回 journal 保存在现有 guard，没有第二套图或事务平台；PostgreSQL JSONB 对整数值浮点列的类型表示在校验时按原列类型归一，未修改旧验收值。缺少本轮 journal 的既有未知漂移仍拒绝，存在独立消费者仍明确拒绝破坏性撤回。

导入使用既有异常边界。commit 前 flush 故障保持正常失败及本次文件清理；commit 后 refresh/cache/serialization 故障回滚失败事务、重新取得并核对真实 Media，再继续登记；身份不匹配返回明确 recovery-required，不假称成功。manual-sync/scanner 完整外层测试断言原文件及 Media 保留、ID 和计数正确、分类调度与已有禁用模型状态正确、重复执行不重新复制或建 Media。上传 202 表示已保存、后续待恢复，不要求产品用户重传；未调用生产分类、WD、LLM 或本地化。

## 本轮生产操作与恢复证据

执行代理依据项目所有者授权完成：先在现有独立 PostgreSQL 恢复副本安装新触发器函数并重复验证幂等；保存原库旧函数及旧候选 profile 后，仅进行已验证的触发器/函数增量更新，将 candidate pin 改为本轮候选并通过既有 controller 重启。原库没有作者篡改、删除或故障注入，apply 始终关闭。

更新前后只核对本次固定选择、1 active run、51 绑定和 5 Media 一致，未重扫全库、未重算全表或原图摘要。原备份 59,384,629 字节、独立恢复 54 表一致、原候选的副本/生产 apply → replay → rollback → reapply 完整生命周期直接引用历史证据，不声称已在新候选重做。**新候选刷新后生命周期由隔离 PostgreSQL 真实 API 证明，生产只做新函数部署和安全读取复验。**

原库、原图、人工标签、相册及已确认 Entity 保留。当前候选 worktree、venv、profile、备份和私有证据继续保留。任务专用 18184 UI 服务已停止，8012 生产服务持续健康，独立 Edge 会话自行关闭，未操作私人 profile 或无关进程。

完整恢复记录追加到既有本机操作文件：保留本轮前后配置、两个数据库的旧函数 SQL、迁移及重启响应、当前运行身份和正式 rollback 步骤。恢复优先依据新候选正式所有权接口；遇独立消费者仍停止，不删 run 或重写 status，不覆盖恢复原库。不把旧 SHA 直接 pin 到行为已经变化的当前 worktree，旧版本启动必须满足实际代码和配置身份。此次没有执行回退。

## 可交给项目负责人的脱敏界面索引

方法：现有 Playwright + 系统 **Edge 152.0.4191.62，headless=false**，独立非持久会话、1440 × 1000，正常登录和真实生产页面。origin 从本轮 controller/profile 取得，与新候选及原数据库/存储关联。验收时间为北京时间 **2026-09-06 17:34:14–17:35:06**（UTC 09:34:14–09:35:06）。不重试已失败的 Computer Use URL 路线。

| 脱敏截图前缀 / Media | 列表缩略图 | 详情 | 产品全屏原图 | 原图实际尺寸 |
|---|---|---|---|---|
| `sample-788` | 已实查通过 | 已实查通过 | 已实查通过 | 1900 × 3600 |
| `sample-2431` | 已实查通过 | 已实查通过 | 已实查通过 | 2976 × 4210 |
| `sample-1869` | 已实查通过 | 已实查通过 | 已实查通过 | 694 × 1176 |
| `sample-842` | 已实查通过 | 已实查通过 | 已实查通过 | 3851 × 6350 |
| `sample-846` | 已实查通过 | 已实查通过 | 已实查通过 | 3720 × 5262 |

每项记录 complete、自然尺寸、渲染尺寸及同一 Media 的图片 URL；执行代理实际查看以上 15 张截图，确认真实内容显示、无占位图或破图，标签和详情正常。26 张生产截图中，另查看以下 5 张，合计 20 张实查；其他 6 张仅标为已捕获。私有原图、截图和网络 payload 不提交公开 PR。

| 脱敏截图文件 | 普通界面结果 |
|---|---|
| `alias-search.png` | 输入现有别名“絆”，返回 4 个合法 Media，包含样本 2431；未强行收窄成唯一身份 |
| `character-Chinese.png` | 稳定作者与“荧”AND 返回 846；已有 Lumine / GenshinImpact 组合亦通过 DOM/API 核对 |
| `negative.png` | 稳定作者与负向“ミカ”返回空集，主搜索显示未找到结果 |
| `source-evidence-expanded.png` | Media 842 展开来源证据，来源层及未确认实体标识正确 |
| `source-chip-search.png` | 点击同一详情的稳定作者 chip，经正常导航返回 842 |
| `isolated-withdrawn-chip.png` | 单独任务 SQLite、同候选真实模板：最后别名撤回显示禁用 chip，无 href / data-search-chip，点击不导航；已实际查看。fixture 图片区为回退 logo，仅作 chip 证据，不用作生产图片证明 |

生产 19 次普通搜索输入和一次来源 chip 点击均与实际 API / DOM 结果集合一致，覆盖稳定作者、作者+标题、有效别名、负向、中文/英文角色、作品名及原有标签。样本没有自然涵盖的旧名、同名异作者、suggestion-only 和来源失效语义由上述隔离 PostgreSQL 用例提供证据，未临时造原库数据。

记录保留 **0 pageerror、2 个登录前辅助请求 401、5 个切页 ERR_ABORTED 请求**；console 共 5 条，其中 2 条对应 401，另 3 条对应被切页取消的辅助 fetch。没有样本图片请求失败、白屏或持续主列表 loading，不宣称 console/network 全零。空结果侧栏热门标签保留旧“正在加载”文本，依据本轮裁决继续延期。

## 验证结果、精确版本及原始失败

所有 Python 命令使用既有仓库 venv 并完成身份预检，runtime 测试使用标准隔离环境。PostgreSQL 用例在现有任务恢复库的独立 schema 运行。以下新证据的命令记录均含实际 argv、tests、`source_head=7dd8733…` 和行为续用检查结果；未将旧候选证明用于新的行为代码。

| 验证 | 实际结果 | 证据和范围 |
|---|---|---|
| 本轮候选 PostgreSQL | **67 passed / 0 failed / 0 skipped，159.23 秒** | `bounded-pg-candidate.log` + JUnit + command JSON；全部 A1 API、刷新撤回/替换、软别名、策略和导入故障 |
| 本轮候选 focused | **257 passed / 0 failed / 1 skipped，210.76 秒** | `bounded-focused-candidate.log` + JUnit + command JSON；契约 10 例、PX3 API/绑定/持久化、registry、manual-sync、scanner |
| 原失败集合精确补跑 | **70 passed / 1 failed，17.93 秒** | `bounded-exact-failures.log` + JUnit + command JSON；62 个原 node 映射为 71 个本轮 node，逐项列于下文 |
| 历史完整 non-E2E，仅复用 | **4382 passed / 62 failed / 15 skipped，52 warnings，624.86 秒** | `tests-non-e2e.log`；候选 `8ec23cd…`，未整套重跑或改写原始结果 |
| 新候选真实 Edge | 五样本、19 次输入、1 次来源 chip，关键图片实查通过 | 新候选，部署重启后的全新会话；隔离禁用 chip 另列 |
| 本机证据与注册契约 | **通过，0 errors / 0 warnings** | `check_production_pixiv_a1.py` 和 `check_phase_contract.py --contract production_pixiv_a1_v1`，重新从实际文件派生 |
| 文档与静态检查 | 生成 state/handoff 检查、Git diff / UTF-8、两个 JS 的 `node --check` | 仅文档收尾不重复功能测试；提交及推送前检查 |

唯一 skip 为 `tests/test_scanner_icloud.py::TestIsScannableFile::test_symlink_skipped`：Windows 创建 symlink 需要提升权限。唯一失败继续为 `test_ai_accounting_keeps_original_and_current_invocation_separate`，原始错误 `missing_original_ai_execution_evidence`，不是本轮新增失败。没有删除断言、伪造历史调用或再次付费调用。

本轮中间失败日志同样保留：最初 PostgreSQL 5 fail / 47 pass 暴露 JSONB 浮点表达及测试 hash 桩问题，已修复后在候选 67 例通过；私有 PG 启动器缺少 Windows multiprocessing main guard 曾导致递归运行，已修正任务启动器并停止自身测试；focused 的 registry retired 计数失败暴露同事务撤回先于服务计数的问题，修复后精确节点及新候选 focused 通过。没有把这些新失败混入历史 AI 标签。

### 62 个原失败 node 的逐项对账

源为原始 full-suite 的 `FAILED` 行，补跑依据为实际 `-vv` 输出中逐 node `PASSED` 及保存的完整命令。59 个节点保持原 ID（58 PASS、1 历史失败），另 3 个旧导入参数各扩成 4 个 caller（12 PASS），共 71 个实际节点。删除、改名或同文件通过本身均不计解决。

| 原 full-suite 失败 node ID | 本轮逐项结果 |
|---|---|
| `tests/test_audit_tier1000.py::TestCopyRowsZeroFail::test_zero_copy_rows_cli_exit_4` | 同一 node ID：PASS |
| `tests/test_audit_tier1000.py::TestPerfectMatch::test_cli_exit_0` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_active_markers_and_contract_commands_are_consistent` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_authority_and_protected_evidence_mutations_fail_closed` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_baseline_identity_mutation_fails_closed[accepted_mainline_base-ffffffffffffffffffffffffffffffffffffffff]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_baseline_identity_mutation_fails_closed[accepted_mainline_tree-ffffffffffffffffffffffffffffffffffffffff]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_baseline_identity_mutation_fails_closed[branch-codex/wrong]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_baseline_identity_mutation_fails_closed[previous_phase_final_head-ffffffffffffffffffffffffffffffffffffffff]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_baseline_identity_mutation_fails_closed[previous_phase_merge_commit-ffffffffffffffffffffffffffffffffffffffff]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_baseline_identity_mutation_fails_closed[previous_phase_status-pending_merge]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_conflicting_current_marker_fails_closed` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_contract_projection_mutation_fails_closed[contract_id-caller_positive_contract]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_contract_projection_mutation_fails_closed[machine_verifiable_ci-True]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_contract_projection_mutation_fails_closed[owner_authority_machine_verifiable-True]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_contract_projection_mutation_fails_closed[public_schema-caller.schema]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_current_phase_schema_identity_and_boundary_are_exact` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_documentation_checker_returns_current_phase_result` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_fixed_route_cannot_expand_or_unstart_px3` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_handoff_is_exact_generated_projection` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_live_git_binds_pr148_merge_and_px3_implementation_evidence` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_public_state_rejects_nul_and_private_path` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_status_or_owner_authority_mutation_fails_closed[manual_acceptance_status-accepted]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_status_or_owner_authority_mutation_fails_closed[next_phase_started-True]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_status_or_owner_authority_mutation_fails_closed[planning_approved-False]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_status_or_owner_authority_mutation_fails_closed[planning_authorized-False]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_status_or_owner_authority_mutation_fails_closed[planning_completed-False]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_status_or_owner_authority_mutation_fails_closed[route_approved-True]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_status_or_owner_authority_mutation_fails_closed[safe_to_merge-True]` | 同一 node ID：PASS |
| `tests/test_current_handoff_freshness.py::test_target_met_and_contract_flags_track_ready_status` | 同一 node ID：PASS |
| `tests/test_pd1a_mainline_governance.py::test_current_mainline_roadmap_persists_px3_boundary_and_fixed_route` | 同一 node ID：PASS |
| `tests/test_pd1a_mainline_governance.py::test_handoff_points_to_current_mainline_roadmap` | 同一 node ID：PASS |
| `tests/test_phase44p2r_f5_provider_neutral_source_name_registry.py::test_refresh_retires_stale_name_tag_and_evidence_observations` | 同一 node ID：PASS |
| `tests/test_phase44p2r_f5_provider_neutral_source_name_registry.py::test_refresh_with_no_current_assertion_drafts_retires_stale_assertions` | 同一 node ID：PASS |
| `tests/test_phase44p2r_f6_source_layer_search.py::test_linked_parenthetical_tag_lookup_escapes_underscore_wildcards` | 同一 node ID：PASS |
| `tests/test_phase44p2r_f6_source_layer_search.py::test_negated_text_query_excludes_source_layer_matches` | 同一 node ID：PASS |
| `tests/test_phase44p2r_f6_source_layer_search.py::test_search_route_returns_source_filter_metadata` | 同一 node ID：PASS |
| `tests/test_phase44p2r_f6_source_layer_search.py::test_text_and_normal_tag_search_soft_link_to_source_name_concept` | 同一 node ID：PASS |
| `tests/test_phase44p2r_f6_source_layer_search.py::test_text_query_without_source_exact_match_returns_normal_tag_only` | 同一 node ID：PASS |
| `tests/test_phase45_sc2_source_concept_search_evidence_ui.py::test_mixed_normal_tag_and_source_concept_query_preserves_and_semantics` | 同一 node ID：PASS |
| `tests/test_phase45_sc2_source_concept_search_evidence_ui.py::test_negative_and_quoted_query_boundaries` | 同一 node ID：PASS |
| `tests/test_phase45_sc2_source_concept_search_evidence_ui.py::test_normal_tag_results_are_preserved_when_alias_also_matches` | 同一 node ID：PASS |
| `tests/test_phase45_sc2_source_concept_search_evidence_ui.py::test_runtime_shared_name_union_and_media_level_and_disambiguation` | 同一 node ID：PASS |
| `tests/test_phase45_sc2_source_concept_search_evidence_ui.py::test_search_cache_is_invalidated_after_source_concept_rows_change` | 同一 node ID：PASS |
| `tests/test_phase45_sc2_source_concept_search_evidence_ui.py::test_source_concept_filter_uses_all_matching_ids_beyond_display_cap` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_a1_post_expansion_audit_route_decision.py::test_handoff_roadmap_and_test_workflow_updates_are_factual` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_ml1_multilingual_alias_source_metadata_closure.py::test_durable_documents_encode_corrected_search_semantics` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_r1_post_px1_source_concept_triage.py::test_handoff_and_roadmap_follow_current_phase_state_not_r1_history` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_sv1_controlled_scale_promotion_readiness.py::test_ai_accounting_keeps_original_and_current_invocation_separate` | 历史失败保留：`missing_original_ai_execution_evidence` |
| `tests/test_phase45_scv2_sv1b_fresh_replay_v2.py::test_protected_raw_evidence_tamper_changes_v4_manifest[acquired-nonderived-evidence-package-private.json]` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_sv1b_fresh_replay_v2.py::test_protected_raw_evidence_tamper_changes_v4_manifest[canary-route-viability-resume-r1/current-primary-read-only-export/stable-key-evidence-package.json]` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_sv1b_fresh_replay_v2.py::test_protected_raw_evidence_tamper_changes_v4_manifest[candidate-page-media-manifest-private.jsonl]` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_sv1b_fresh_replay_v2.py::test_protected_raw_evidence_tamper_changes_v4_manifest[provider-execution-checkpoint-r2-route-viability/final-work-outcome-ledger.json]` | 同一 node ID：PASS |
| `tests/test_phase45_scv2_sv1b_fresh_replay_v2.py::test_protected_raw_evidence_tamper_changes_v4_manifest[provider-execution-checkpoint-r2-route-viability/route-viability-canary-ledger.json]` | 同一 node ID：PASS |
| `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[cache]` | 映射 M2：4 / 4 PASS |
| `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[refresh]` | 映射 M1：4 / 4 PASS |
| `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[serialization]` | 映射 M3：4 / 4 PASS |
| `tests/test_scv2_fl1_i2_validation_receipt.py::test_head_or_tree_drift_never_issues_positive_receipt` | 同一 node ID：PASS |
| `tests/test_scv2_fl1_i2_validation_receipt.py::test_same_head_receipt_binds_all_evidence` | 同一 node ID：PASS |
| `tests/test_scv2_px3_media_binding.py::test_cached_gallery_response_is_invalidated_only_after_successful_rollback` | 同一 node ID：PASS |
| `tests/test_stage_pilot_files.py::TestPostCopyAuditHardFail::test_audit_count_mismatch_exits_4` | 同一 node ID：PASS |
| `tests/test_stage_pilot_files.py::TestPostCopyAuditHardFail::test_normal_execute_exits_0` | 同一 node ID：PASS |
| `tests/test_stage_pilot_files.py::TestValidManifest::test_cli_dry_run_exits_zero` | 同一 node ID：PASS |

参数化变化独立列示；每个新 node 都在实际命令参数及详细日志中出现，均为 PASS。

**M1**：`tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[refresh]`。理由：原单一直接调用故障扩展为 direct/manual_sync/upload/chunked 四条提交边界；保持原 fault，新增上传 202 恢复状态断言。

- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[refresh-direct]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[refresh-manual_sync]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[refresh-upload]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[refresh-chunked]`：PASS

**M2**：`tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[cache]`。理由：原单一直接调用故障扩展为 direct/manual_sync/upload/chunked 四条提交边界；保持原 fault，新增上传 202 恢复状态断言。

- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[cache-direct]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[cache-manual_sync]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[cache-upload]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[cache-chunked]`：PASS

**M3**：`tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[serialization]`。理由：原单一直接调用故障扩展为 direct/manual_sync/upload/chunked 四条提交边界；保持原 fault，新增上传 202 恢复状态断言。

- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[serialization-direct]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[serialization-manual_sync]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[serialization-upload]`：PASS
- `tests/test_production_pixiv_a1.py::test_import_commit_boundary_preserves_durable_files[serialization-chunked]`：PASS


## 当前规模、A2 输入与本轮边界

本轮没有扩大 active selection 或重跑规模测量。历史 A1 快照的 37,419 Media、526 个真实 metadata works、465 eligible works、453 个兼容 works / 455 pages / 5,388 signals，及 240-query / 720 次结果一致性与 p95 33.377 ms，继续作为**原测量时间与旧候选**的 A2 输入，不称为本轮新性能测量。具体分母、方法及限制保留在下方历史正文。

A2 仍需处理：8,521 个无现有 metadata 的候选 works 的来源确认；1,450 个 Media 多 work 先验；12 个 artist-context 不兼容 works；将已接受的兼容 judgments 与完整裁决链按版本/输入身份接通；更大或累计选择的 overlap、所有权与正式增量策略；依据稳定 ID/职责/上下文评估别名质量。当前 ProviderCache 的两行 SauceNAO 记录不能当作 Pixiv metadata 覆盖。A3 的持续同步及队列整合也没有提前执行；本轮仅修已使用导入路径的 commit 边界。

没有新 Pixiv/gallery-dl 获取、第二 provider、应用 LLM、全量源目录扫描、全库导入/分类/WD/本地化、真值或人工整理改写、原图删除、数据库覆盖恢复。没有 main 推送、force push、merge 或追加审查触发。新增任务脚本及原始记录全部是本机任务证据，不是新的生产工具平台；代码中的共享 guard 和异常恢复函数是现有路径的有界修复。

根 AGENTS 和适用 runbook 已在同一个 PR 中包含正式角色称谓及允许的浏览器替代路径规则，本轮复用，没有新增称谓测试框架或独立文档阶段。实际修订文件与精确 diff 由同一 PR 提供。

## 历史正文与追溯

下方是本轮审计前报告原文，属于 `8ec23cd…` 实测及 `9d66aa…` 交付时的历史快照。其运行 PID、测试计数、完成措辞和链接投影均不得当作当前状态；刷新闭环与失败覆盖的过早结论已在本报告前部明确修正。原文另保存于本机任务目录，未删除原日志或覆盖原始审计证据。

<details>
<summary>展开审计前 A1 报告原文（历史，当前结论以上文为准）</summary>

# 生产 Pixiv A1 交付报告

日期：2026-09-06。项目所有者：`kyloris0660`（需求、资产及操作授权）；项目负责人：本次 VIOLET 接手与路线规划的 ChatGPT 会话（路线及交付复审）；执行代理：当前 Codex 会话（实现、已授权操作及工程验证）。本报告不代替项目负责人接受、项目所有者亲自使用验收或审查者意见。

## 交付结果与运行版本

A1 的代码、恢复排练、原库样本生命周期和真实界面验证已完成。产品用户通过原有生产启动器可查看五个已有 Media 的来源概念、按有效来源及原标签搜索，并打开真实图片。候选仍待项目负责人复审；未合并、未推送 main、未触发额外审查。

- 分支：`codex/production-pixiv-a1`。
- 正常 PR：[151](https://github.com/kyloris0660/VIOLET/pull/151)，等待项目负责人复审。
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

交付门禁：`check_production_pixiv_a1.py`、注册 `check_phase_contract.py --contract production_pixiv_a1_v1`（附本机 evidence）、`check_documentation_state.py --check`、JSON/UTF-8/脱敏核对及 Git diff 检查通过；真实投影正向通过，伪造绑定数、浏览器计数、合并权限和候选身份的四项变体均拒绝。本地结果不等于 GitHub CI，不赋予合并权限。

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

</details>
