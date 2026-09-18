# A2 / 20260917 纠偏执行结果

生成时间：2026-09-18T06:11:57.499266+00:00。工程目标未达成，`target_met=false`；新版未进入原生产。完整副本实际 **77/80**，其余失败保留。已完成本轮可独立继续的修复、完整恢复、实际界面验证和证据对账。

同一 [PR #153](https://github.com/kyloris0660/VIOLET/pull/153)，分支 `codex/production-pixiv-a2`。行为与本次副本执行候选 `a3c46ca22470a8c517295a007db3b19d802a980e`；原生产仍为固定 `44db0da0c1df2fe38434cacc57308f2c0e33ec0f`。最终纯文档提交另列，以现有 carry-forward 校验衔接。未合并、未推 main、未触发 reviewer 或自行关闭线程，未进入 A3；不宣称 Lead 接受或 Owner 验收。

## 实际质量与原十例

原80例身份和旧75/80结果原样保留。仅Media718按负责人具名裁决改为未接受suggestion负对照，714/715继续是已接受标签正对照；其余79例要求未降低。冻结以前实际身份召回集合，当前投影与API同时漏失也会失败。

| 原异名对 | 原缺失（左 / 右） | 本次缺失（左 / 右） | 本次结果 |
| --- | --- | --- | --- |
| bluearchive / ブルーアーカイブ | 1318,1321,1324,2139,2242,2984,2992,3323,3432,3573,3726,3997,4017,4245,4372,4402,4874,5651,5743,5914,6828,6947,7422,7974,8404,8423,8524,8792,8993,9066,9089,9121,9122,9123,9244,9349,9350,9351,9358,9395,9535,10393,10962,10970,11048,11050,11118,11257,11288 / 无 | 无 / 无 | 通过 |
| honkaistarrail / 崩壊:スターレイル | 1340,4461,7773,8878,8909,9855,10102 / 无 | 无 / 无 | 通过 |
| nahida / 纳西妲 | 无 / 784 | 无 / 784 | 失败 |
| nahida / 草神 | 无 / 758,784,1824,2763,2771,4765,5076,5129,5229,5294,5302,5383,5390,5391 | 无 / 784 | 失败 |
| nicoledemara / ニコ_デマラ | 742,833,1297,2770,7031,8058,8068,8073,8253,8274,8324,8331,10720 / 无 | 无 / 无 | 通过 |
| nicoledemara / ニコ_デマラ(ゼンレスゾーンゼロ) | 无 / 无 | 无 / 无 | 通过 |
| zenlesszonezero / ゼンレスゾーンゼロ | 36755 / 10266 | 无 / 无 | 通过 |
| ナヒーダ / 纳西妲 | 无 / 784,5147,5256 | 无 / 784,5147 | 失败 |
| レヴィア(クローザーズ) / 레비아 | 无 / 无 | 无 / 无 | 通过 |
| 纳西妲 / 草神 | 无 / 1830,2763,3915,4803,4804,4902,4903,5029,5076,5129,5196,5383,6900 | 无 / 无 | 通过 |

其余未通过案例（直接来自本次80例，不改写passed）：

- {"names": ["nahida", "纳西妲"], "expected": "must_link", "category": "supported_but_fragmented", "missing_recall_media_ids": [[], [784]], "passed": false}
- {"names": ["nahida", "草神"], "expected": "must_link", "category": "supported_but_fragmented", "missing_recall_media_ids": [[], [784]], "passed": false}
- {"names": ["ナヒーダ", "纳西妲"], "expected": "must_link", "category": "supported_but_fragmented", "missing_recall_media_ids": [[], [784, 5147]], "passed": false}

附加保留样本：3/3。80例类别计数：`{"supported_multilingual_identity": 15, "required_separation": 36, "no_independent_identity_answer": 1, "supported_but_fragmented": 3, "accepted_search_equivalence_only": 8, "media_set_AND": 3, "media_set_negative": 3, "bare_name_distinct_creator_accounts": 2, "suggestion_suggested_positive": 3, "suggestion_suggested_negative": 3, "suggestion_accepted_positive_control": 3}`。独立F6控制与合法同图共现由本次质量重算，反例覆盖“双方/AND均错误并集、排除均为空”的自洽错误。

## 四张真实残留图与有界纠正

Media3915和5256的旧错误角色/上下文已按保存的冲突证据纠正；原先依赖错误输入的判断退出当前有效集合，原付费响应仍保留。完整十例因果附件给出真实输入、缓存、守卫、路径、concept/alias、支持与分页响应。不同图不能用同一条cannot-link概括。
Media784的ナヒーダ已纠正为角色，但来源缺少显式所属关系；另一个来源目标“守りたい、この笑顔”仍未形成可靠处置，已用尽同一逻辑目标三次尝试。不能删掉该不确定来源事实来制造唯一作品上下文。
Media5147的ナヒーダ已纠正为角色，但实际来源中没有有效作品归属上下文；可靠的スカラマシュ与ナヒーダ分离判断继续保留。原始标题/说明只表明两个角色共现，没有新增显式作品归属。不能把它笼统当作中文别名失败的唯一原因。
V1新增的Media5651回退具有原单标签work与全上下文non_name冲突，追加一次有界纠正，实际USD0.001016；旧65/69结果和该失败均保留，V2最终结果以上表与完整80例为准。
本轮没有名称硬编码、手工must_link、确认Entity写入、删除分离约束或反复请求肯定答案。148组角色纠正对应144份独立答案与4组同题复用；145次角色调用含1次真实schema错误重试。另有18次新输入配对调用，旧兼容判断继续复用。
历史角色账本有一个原神目标4次、一个守りたい、この笑顔目标3次；本轮已从新增派发排除，原记录保留，不能宣称全部历史角色均不超过3次，也不将它们改称本轮授权例外。配对现有9556个传递逻辑组件最多3次，超过3次为0。

另对108条历史terminal注释逐项核验，15条曾有4次尝试，当前全部由实际有效non_name答案覆盖，不再作为terminal闭合分母。实际生效26条均与账本完整票据集合相等且恰好3次。历史超限如实保留，不删除票据或伪装为三次。

## F1–F6与新增准入修复

| 项目 | 根因与修复 | 验证边界 |
| --- | --- | --- |
| F1 | 不信任自报收费，从实际usage或保守reservation逐调用复算，共享账本锁内准入。 | 非法/低报费用、未知usage、重复结算和并发预留；原账本离线复算。 |
| F2 | provider返回后本地保存失败与模型失败分开；保存响应恢复凭证，按原attempt幂等结算。 | raw/unit/settle相邻故障及再次恢复，同题不重复付费。 |
| F3 | coverage与实际投影共享已校验逐目标处置，completion单独non_name可撤回unknown。 | 强角色保护、无关aggregate、混合响应与实际API支持变化。 |
| F4 | 重建原始group/context/raw目标并核对terminal逻辑身份与每张票。 | 原26项逐票核验，拒绝借票、重复及未结算票。 |
| F5 | 从实际问题、原响应、缓存和ledger重放角色/配对来源；发布检查完整选中集合。 | 输入/答案篡改、重复凑数、缺项、纠正历史、旧问题来源及零调用再准入。 |
| F6 | 加入独立来源排他样本，保留36分离与合法共现；718按具名裁决更新。 | 修前自洽并集反例必须失败，当前实际API结果重算。 |
| 新增6项 | 冻结旧召回、完整prior与传递尝试上限、迁移事务超时、证据路径、chip真实搜索、17表保护快照。 | 当前候选focused/真实PG；Windows symlink权限项明确跳过。 |
| 后续4项 | prior绑定实际前驱facts/纠正计划/完整来源选择；浏览器必须同次有序；workload绑定候选loopback服务；coverage与apply共享多语言候选匹配。 | 修前9个真实反例保留；当前回归、完整来源再准入及实际新浏览器/工作量收据。完整100285信号含证据新旧指纹一致。 |
| 再新增4项 | 裁决源绑定实际同输入已结算票据，旧无reservation源只接受唯一成功票；当前terminal完整集合恰三次；远端页数有界核验；阶段结果固定路径并绑定候选/实际HEAD。 | 修前11个实质反例及已有绝对路径拒绝分别保留；当前回归、完整输入身份/账目等价、实际26条terminal及7682个原始配对源核验。 |

当前审查快照63条：39条按Lead已处理结论核对承接、1条历史例外保留、23项本轮修复。工程处置不等于远端线程已关闭。

新增三项完成声明/服务身份缺口也已修复：固定本机私有契约重新推导并精确对账公共结果；浏览器和质量与workload共用候选服务身份守卫；质量各组件及每页实际请求绑定同一服务。8个修前反例保留。另修正浏览器截图绘制等待，旧黑帧不当作视觉通过。

## 完整范围、来源与支持对账

固定T0为38114 Media，9502映射；8623完整metadata、850远端不可用、29缺页。8694历史获取记录复用，本轮零新增metadata请求。原31295 selected、8769缺项、680新增、105排除和49227角色出现的原账目保留。
按历史dispatch日志逐条核对8694个不同原始输出，8688个位于metadata-raw、6个认证预检输出位于独立子目录，全部存在并记录文件哈希；最终清单包含两类，未因目录差异漏掉原始结果。
旧31607选中与当前31612比较：31369同位置同输入且答案未变、21输入变化、217旧代表移除/222新增代表。作品9070与剩余22542均有有效判断和实际来源证明。e5aaf7e真实构图100285信号/626779边/17538概念；当前候选对不变核心、候选匹配完整信号与完整实际输入重新核验，保留不同实际执行身份，不伪造原调用SHA。
独立来源重放实际完成候选为bd3ad8b。当前候选仅续接未变化的语义输入与来源校验代码，六次产品plan/apply仍在当前HEAD逐次执行完整来源校验；契约明确核对这条续接链。证据包入口审计覆盖31623个选中缓存链文件及1921个最终角色证明引用的原始文件，均存在；角色校验器检查过的原始envelope计数1922与最终引用文件数不是同一口径，12条确定性记录不冒充付费响应。
实际支持：原生产66572 → 新副本66394；移除316、新增138、净变化-178；绑定Media集合相同，共8623。
原54移除保留54，原5新增保留5；新差额逐项列在support-history。保留支持的投影变化21649条；历史副本revision差异14条，未经解释的身份/revision变化0。

## 完整恢复、性能、浏览器与契约

本候选实际执行完整plan/apply、幂等replay、owned rollback/重复rollback/reapply、逆序分批等价和source update/delete恢复；17表检查点绑定本候选、隔离库和同一次操作。未对原库执行撤回演练，未用T0备份覆盖用户后续数据。
本次首次apply实际idempotent_replay=True；owned撤回后的reapply实际idempotent_replay=False。首次命中相同输入的旧投影属于幂等复用，不冒称首次全新写入；重新写入以撤回后实际reapply回执为准。
真实有界面Edge：原图3、缩略图1，页面错误0；来源chip真实图库查询、DOM/API结果、旧标签、suggestion与只读恢复页有实际收据。代表截图的人工查看结果另行记录，自动采集不冒称Owner验收。

| 完整契约及独立后续门禁 | 实际结果 |
| --- | --- |
| registered_full_contract | failed：a2_behavior_carry_forward |
| current_candidate_behavior | passed |
| backup_restore | passed |
| independent_t0 | passed |
| fixed_mapping_and_complete_accounting | passed |
| metadata_attempts_scope_and_historical_exception | passed |
| forward_metadata_spacing | passed |
| rederived_original_budget_and_settlement | passed |
| selected_judgment_source_receipts | passed |
| current_semantic_input_readmission | passed |
| current_original_production_apply | failed：current_behavior_not_deployed_original_production_manifest_retained |
| full_copy_recovery | passed |
| independent_quality | failed： |
| current_real_browser | passed |
| current_original_production_normal_entry | failed：current_behavior_not_deployed_original_launcher_manifest_retained |
| query_level_performance | passed |
| exact_candidate_validation | passed |

性能原始重算：`{"source": {"p50_ms": 306.7, "p95_ms": 389.606, "max_ms": 897.985}, "http": {"p50_ms": 792.359, "p95_ms": 1414.753, "max_ms": 2608.266}}`。来源层p95≤750ms/max≤3000ms，完整HTTP时延单列。完整注册契约的失败保留，未通过最早门禁不妨碍独立检查后续可核验项；没有改标旧原生产manifest。

## 精确候选测试与费用

focused：388 passed/0 failed/1 skipped；PostgreSQL/API：22 passed。88个历史失败精确节点通过；唯一历史完整non-E2E 4541/89/15原样保留，missing_original_ai_execution_evidence未补造。未重复全套测试，GitHub CI状态以当前PR另存快照为准。

| 调用类型 | 累计尝试 | 成功 / 失败 | 未知usage | 累计USD | 本轮新增尝试 / USD |
| --- | --- | --- | --- | --- | --- |
| role | 1956 | 1911 / 45 | 32 | 9.810893 | 145 / 0.146941 |
| pair | 16471 | 16441 / 30 | 22 | 3.771751 | 18 / 0.004895 |

累计18427调用，USD 13.582644，剩余USD 16.417356；本轮新增163调用/USD 0.151836。状态{"success": 18352, "failed": 75}；54次未知usage按保守额度保留。原6110次/USD9.998387与10→30追加事实未变。

## 日常入口与最小剩余动作

正常无参数EXE仍指向固定旧44db0da目录，原库read ON/apply OFF。新版索引迁移、原生产plan/apply与新候选入口切换因真实质量门禁未执行。纠偏开始时曾恢复旧固定服务可用性；这不是新版上线，也没有覆盖原数据库。
条件发布私有入口已同步实际副本launch和完整副本门禁检查，并为原生产前后保护快照记录操作身份；旧入口备份保留。该准备仅通过语法、UTF-8与帮助入口检查，尚无新版原生产执行或通过声明。
修复私有打包器对非敏感语义授权标记的过度脱敏：旧规则使148条纠正请求指纹变化，新规则为0；8类凭据字段仍脱敏。当前角色事实整体结构及两个纠正计划保持不变，实际打包还强制角色事实/前驱/计划逐字节保留，ZIP核验另存回执。旧档案不改写。
Owner从A2-MANUAL-ACCEPTANCE.zh-CN.md查看实际普通查询和四张来源面板；A2-OPERATIONS.zh-CN.md记录入口、恢复及证据位置。未通过项应继续保留，不能签署A2全量验收。
剩余动作是取得784/5147的可靠所属作品/有效语义处置依据，或由Lead对无法由现有证据支持的目标作具名裁决，再按既有范围复验。不是再次申请预算、Pixiv令牌或一般执行许可；不会靠重置尝试、删约束或降低其余79例要求收口。

## 本轮跟踪变更文件

与上一轮交付 `df6a849ae898e7693cbaba5258053ad1cfaad2b5` 比较，共32个跟踪文件；本机私有采集器和原始证据另外保存在证据包。

- [backend/app/database.py](../../backend/app/database.py)
- [backend/app/services/production_pixiv_corrections.py](../../backend/app/services/production_pixiv_corrections.py)
- [backend/app/services/production_pixiv_pair_correction.py](../../backend/app/services/production_pixiv_pair_correction.py)
- [backend/app/services/production_pixiv_release_inputs.py](../../backend/app/services/production_pixiv_release_inputs.py)
- [backend/app/services/production_pixiv_release_provenance.py](../../backend/app/services/production_pixiv_release_provenance.py)
- [backend/app/services/production_pixiv_role_extraction.py](../../backend/app/services/production_pixiv_role_extraction.py)
- [backend/app/services/production_pixiv_semantics.py](../../backend/app/services/production_pixiv_semantics.py)
- [backend/app/services/source_concept_budget.py](../../backend/app/services/source_concept_budget.py)
- [backend/app/services/source_concept_resolver_service.py](../../backend/app/services/source_concept_resolver_service.py)
- [docs/current-handoff.md](../../docs/current-handoff.md)
- [docs/plans/production-pixiv-a2.md](../../docs/plans/production-pixiv-a2.md)
- [docs/reports/production-pixiv-a2-result.md](../../docs/reports/production-pixiv-a2-result.md)
- [docs/state/current-phase.json](../../docs/state/current-phase.json)
- [scripts/check_production_pixiv_a2.py](../../scripts/check_production_pixiv_a2.py)
- [scripts/production_pixiv_a2_evidence.py](../../scripts/production_pixiv_a2_evidence.py)
- [scripts/production_pixiv_a2_service_evidence.py](../../scripts/production_pixiv_a2_service_evidence.py)
- [scripts/production_pixiv_a2_state.py](../../scripts/production_pixiv_a2_state.py)
- [scripts/run_production_pixiv_a2_concepts.py](../../scripts/run_production_pixiv_a2_concepts.py)
- [scripts/run_production_pixiv_a2_metadata.py](../../scripts/run_production_pixiv_a2_metadata.py)
- [scripts/run_production_pixiv_a2_product.py](../../scripts/run_production_pixiv_a2_product.py)
- [tests/test_production_pixiv_a2.py](../../tests/test_production_pixiv_a2.py)
- [tests/test_production_pixiv_a2_api.py](../../tests/test_production_pixiv_a2_api.py)
- [tests/test_production_pixiv_a2_evidence.py](../../tests/test_production_pixiv_a2_evidence.py)
- [tests/test_production_pixiv_adjudication.py](../../tests/test_production_pixiv_adjudication.py)
- [tests/test_production_pixiv_correction_gates.py](../../tests/test_production_pixiv_correction_gates.py)
- [tests/test_production_pixiv_corrections.py](../../tests/test_production_pixiv_corrections.py)
- [tests/test_production_pixiv_release_inputs.py](../../tests/test_production_pixiv_release_inputs.py)
- [tests/test_production_pixiv_review60.py](../../tests/test_production_pixiv_review60.py)
- [tests/test_production_pixiv_review63.py](../../tests/test_production_pixiv_review63.py)
- [tests/test_production_pixiv_role_coverage.py](../../tests/test_production_pixiv_role_coverage.py)
- [tests/test_production_pixiv_role_extraction.py](../../tests/test_production_pixiv_role_extraction.py)
- [tests/test_source_concept_task_budget.py](../../tests/test_source_concept_task_budget.py)

## 验证命令参数与本机证据索引

以下保留实际命令参数，个人路径以 `$PY`、`$WORKTREE`、`$EVIDENCE` 代称；完整原命令与回执留在本机。运行前已通过项目Python身份预检并加载隔离测试环境。完整88节点命令见原始回执，不重新运行历史完整non-E2E。

focused：

```powershell
& '$PY' '-m' 'pytest' 'tests/test_source_concept_task_budget.py' 'tests/test_production_pixiv_role_extraction.py' 'tests/test_production_pixiv_role_coverage.py' 'tests/test_production_pixiv_semantics.py' 'tests/test_production_pixiv_corrections.py' 'tests/test_production_pixiv_adjudication.py' 'tests/test_production_pixiv_release_inputs.py' 'tests/test_production_pixiv_a2_evidence.py' 'tests/test_production_pixiv_correction_gates.py' 'tests/test_source_concept_withdrawal_indexes.py' 'tests/test_production_pixiv_review60.py' 'tests/test_production_pixiv_metadata_runner.py' 'tests/test_production_pixiv_review63.py' '-v' '--tb=short' '--junitxml=$EVIDENCE\correction17-a3c46ca-focused.xml'
```

命令回执（本机私有证据：`correction17-a3c46ca-focused-command-private.json`）；实际日志（本机私有证据：`correction17-a3c46ca-focused.log`）；JUnit（本机私有证据：`correction17-a3c46ca-focused.xml`）。

postgresql：

```powershell
& '$PY' '-m' 'pytest' 'tests/test_production_pixiv_a2.py' 'tests/test_production_pixiv_a2_api.py' '-v' '--tb=short' '--junitxml=$EVIDENCE\correction17-a3c46ca-postgresql.xml'
```

命令回执（本机私有证据：`correction17-a3c46ca-postgresql-command-private.json`）；实际日志（本机私有证据：`correction17-a3c46ca-postgresql.log`）；JUnit（本机私有证据：`correction17-a3c46ca-postgresql.xml`）。

- 88个历史节点实际命令（本机私有证据：`correction17-a3c46ca-validation-historical-remediation-command-private.json`）
- 完整副本恢复（本机私有证据：`correction17-copy-final-3-recovery-private.json`）
- 80例逐项结果（本机私有证据：`correction17-copy-final-3-surfaces-combined-quality-private.json`）
- 十例完整因果差异（本机私有证据：`correction17-copy-final-3-ten-causal-closeout-private.json`）
- 四张指定样本的原始metadata、角色纠正及响应路径（本机私有证据：`correction17-copy-final-3-four-media-role-lineage-private.json`）
- 支持变化（本机私有证据：`correction17-copy-final-3-support-transition-private.json`）
- 完整契约及独立后续门禁（本机私有证据：`correction17-copy-final-3-contract-private.json`）
- 真实浏览器动作（本机私有证据：`correction17-copy-final-3-surfaces-browser-private.json`）
- 逐查询性能（本机私有证据：`correction17-copy-final-3-surfaces-workload-private.json`）
- 逐线程处置（本机私有证据：`correction17-final-review-dispositions-private.json`）
- 累计费用复算（本机私有证据：`correction17-final-budget-private.json`）
- 语义请求指纹保持与凭据脱敏回归（本机私有证据：`correction17-package-authority-redaction-check-private.json`）
- 8694份历史metadata原始输出逐条索引（本机私有证据：`correction17-metadata-raw-complete-extra-index-private.json`）
