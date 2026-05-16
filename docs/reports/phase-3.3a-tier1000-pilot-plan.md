# Phase 3.3a — Tier-1000 Pilot Planning Report

**日期**: 2026-05-16
**阶段**: Phase 3.3a — Tier-1000 核心流水线规模验证规划
**分支**: `phase3.3a-tier1000-pilot-plan`
**基线提交**: `143678b` (origin/main)

---

## 1. 执行摘要

Phase 3.2 / Tier-500 中规模试验已完成并通过验收。本文档是 Phase 3.3 的规划阶段（3.3a），
目标是为 Tier-1000 核心流水线验证制定详细的执行计划，包括：

- 独立的 DB / 存储 / 源数据环境设计
- 数据集检查结果与采样策略
- 完整的执行阶段序列
- 预算与规模限制
- 安全门控
- 通过/失败标准
- 短期路线图（3.3 → 3.6）

**本阶段仅产出文档。不执行导入、分类、AI 标注、本地化、LLM 调用或任何破坏性操作。**

---

## 2. Tier-500 已完成状态

### 2.1 数据库快照（blombooru_test_medium，只读查询）

| 指标 | 数值 |
|------|------|
| 总 media | 522 |
| anime | 500 |
| non_anime | 14 |
| unknown | 8 |
| Anime AI 标注覆盖 | 500/500 (100%) |
| 总 tag_translations | 1917 |
| general 翻译覆盖 | 1871/1871 (100%) |
| meta 翻译覆盖 | 4/4 (100%) |
| character 翻译覆盖 | 33/91 (36%) — 58 缺失 |
| copyright / artist 标签 | 0（无此类标签） |
| needs_review | 36 |
| 手动修正 (source=manual) | 2 |
| 翻译来源: llm | 1836 |
| 翻译来源: static | 79 |

### 2.2 作业历史

**Scan jobs**: 1 (completed)

**AI tag jobs**: 9 (1 interrupted, 8 completed)
- 总处理: 500 anime media, failed = 0
- 所有非 dry-run 批次 failed = 0

**Classification jobs**: 3 (all completed)
- Job 3: processed=476, failed=0

**Translation jobs**: 6 (2 interrupted, 4 completed)
- 最终 general 翻译覆盖 100%

### 2.3 功能验证状态

| 检查项 | 状态 |
|--------|------|
| 手动修正 PATCH workflow | 已实现并测试 (PR #42) |
| Admin UI PATCH E2E | 已修复并通过 (PR #43) |
| 浏览器验收 | 通过 |
| config precedence (override=False) | 已修复 (PR #41) |
| Python identity hard gate | 已加固 (PR #36, #37) |
| AI-only run isolation | 已加固 (PR #40) |
| Localization side effect gating | 已加固 (PR #39) |

### 2.4 已知非阻塞问题

- character/entity 翻译缺失 58 个 — 已按设计延迟到 Entity Metadata Resolver（Phase 3.4）
- 5 个 JFIF 文件不受支持 — 格式转换延迟到 Phase 4+
- 3 个 unknown media 有标签（来自早期中断作业）— 不影响核心流水线
- same-character / similar-image 聚类 — 延迟到 Phase 3.5

---

## 3. 为什么现在进行 Tier-1000 规划

- Tier-500 核心流水线已完全通过：import → classify → AI tag → localize → browser acceptance
- 所有安全门控（Python identity、server identity、config precedence、destructive op protection）已加固
- 手动修正工作流已实现并通过 E2E 测试
- 需要在更大规模上验证流水线的稳定性、性能和数据完整性
- Tier-1000 是进入 Entity Resolver 和聚类模块前的最后一次核心流水线规模验证

---

## 4. 推荐的独立 Tier-1000 环境

### 4.1 环境隔离方案

| 配置项 | 值 |
|--------|-----|
| 数据库 | `blombooru_test_1000` |
| 存储根 | `C:\Users\kyloris\VioletStorage\pilot1000` |
| 备份目录 | `C:\Users\kyloris\VioletBackups\pilot1000` |
| 源数据 | `E:\VioletPilotData` (需要用户扩充到 ~1000 支持文件) |
| 端口 | 动态 8012–8024 |
| 环境 | `VIOLET_ENV=test` |

### 4.2 为什么不复用 blombooru_test_medium

1. **避免污染 Tier-500 已验收基线** — blombooru_test_medium 是已经通过验收的可靠参考点
2. **简化回滚** — 如果 Tier-1000 出现问题，可以直接丢弃 blombooru_test_1000，不影响任何已有数据
3. **指标和作业归因清晰** — 全新的 job ID 序列，避免与 Tier-500 的 9 个 AI tag jobs 混淆
4. **可重复性** — 可以多次从头运行 Tier-1000 pilot，不受历史状态影响
5. **存储路径隔离** — pilot1000 存储根完全独立，缩略图和媒体文件不与 Tier-500 交叉

### 4.3 环境变量设置

```powershell
# 加载基础测试环境
. "$env:USERPROFILE\.violet\test-env.ps1"

# Tier-1000 覆盖
$env:POSTGRES_DB = "blombooru_test_1000"
$env:VIOLET_STORAGE_ROOT = "C:\Users\kyloris\VioletStorage\pilot1000"
$env:VIOLET_ENV = "test"
$env:APP_PORT = "<free-port-from-8012-8024>"
$env:VIOLET_BASE_URL = "http://127.0.0.1:$($env:APP_PORT)"
$env:VIOLET_RUN_REAL_E2E = "1"
```

---

## 5. 数据集检查结果

### 5.1 当前 E:\VioletPilotData 检查

检查命令：
```powershell
& "$PY" scripts/inspect_pilot_dataset.py --path "E:\VioletPilotData"
```

| 指标 | 值 |
|------|-----|
| 总文件数（非隐藏） | 527 |
| 支持的图像文件 | 522 |
| 不支持的文件 | 5 |
| 隐藏/系统文件 | 0 |
| 符号链接 | 0 |
| stat 错误 | 0 |
| 总大小 | 1.84 GB |
| 目录结构 | 扁平（无子目录） |
| iCloud 路径涉及 | 否 |
| 源路径为本地 | 是 (E:\ 本地磁盘) |

**扩展名分布**:
| 扩展名 | 数量 |
|--------|------|
| .jpg | 418 |
| .png | 89 |
| .jpeg | 15 |
| .jfif | 5 (不支持，跳过) |

### 5.2 关键发现

**当前数据集不足以支撑 Tier-1000 试验。**

E:\VioletPilotData 中仅有 522 个支持文件 — 与 Tier-500 使用的是同一数据集。
要达到 Tier-1000，**用户需要向 E:\VioletPilotData（或新目录）补充约 500 个额外的支持格式图像文件**。

### 5.3 用户操作要求

在 Phase 3.3b（执行阶段）开始前，用户需要：

1. 向 E:\VioletPilotData 添加约 500+ 个新的支持格式图像（.jpg / .png / .jpeg / .webp / .gif）
2. **或** 提供一个新的源数据目录路径，包含 ~1000 个支持格式图像
3. 重新运行检查脚本确认文件数量：
   ```powershell
   & "$PY" scripts/inspect_pilot_dataset.py --path "E:\VioletPilotData"
   ```
4. 确认所有文件均来自本地磁盘，不涉及 iCloud

---

## 6. 数据集采样策略

### 6.1 方案对比

| 方案 | 描述 | 适用条件 |
|------|------|----------|
| A. 前 1000 个 | 按文件名排序取前 1000 | 简单，但可能有偏差 |
| B. 确定性随机 | 固定种子随机抽样 1000 | 总数 > 1000 时推荐 |
| C. 分层抽样 | 按目录/类别比例抽样 | 目录结构暗示 anime/non_anime |
| D. 全部使用 | 使用所有支持文件 | 总数接近 1000 |

### 6.2 推荐策略

**当前推荐：方案 D — 使用所有支持文件。**

理由：
- 当前 522 支持文件远低于 1000，用户需要补充
- 如果用户补充后总数接近 1000（例如 950–1100），直接使用全部
- 如果用户提供的数据量远超 1000（例如 2000+），改用方案 B（固定种子 `seed=42` 随机抽样 1000）

### 6.3 Manifest 格式

无论使用哪种策略，执行阶段都应生成一个 manifest 文件：

```
# pilot1000-manifest.txt
# Generated: 2026-05-XX
# Strategy: all_supported | random_seed_42
# Source: E:\VioletPilotData
# Total supported files in source: XXXX
# Selected for pilot: 1000
#
E:\VioletPilotData\100002224_p0.jpg
E:\VioletPilotData\100021824_p0.jpg
...
```

- Manifest 在执行阶段（Phase 3.3b）创建，不在本规划阶段
- Manifest 应保存在 `docs/reports/pilot1000-manifest.txt`
- 不复制或移动源文件

---

## 7. 执行阶段计划

以下是 Phase 3.3b 的执行序列。**本文档仅描述计划，不执行。**

### Stage A: Preflight

1. 分支卫生检查
2. Python identity hard gate
3. Config precedence 验证 (`override=False`)
4. 环境变量健全性检查
5. DB / 存储隔离确认
6. 活跃作业检查（确保无残留作业）
7. 数据集检查（确认 ~1000 支持文件可用）
8. 备份目录存在验证
9. 确认 E:\VioletPilotData 无 iCloud 路径

### Stage B: DB 设置 / 迁移

1. 创建 `blombooru_test_1000` 数据库
2. 运行 schema 迁移
3. 验证 schema 完整性
4. 验证数据库为空基线（0 media, 0 jobs）
5. 不触碰 blombooru / blombooru_test / blombooru_test_medium

### Stage C: Server 启动

1. 动态端口探测 8012–8024
2. 使用 approved Python 启动
3. Server identity hard gate:
   - `VIOLET_ENV=test`
   - `POSTGRES_DB=blombooru_test_1000`
   - `code_root` = 当前工作树
   - `git_sha` = 当前 HEAD
   - `sys.executable` = venv Python
   - `VIOLET_STORAGE_ROOT` = `C:\Users\kyloris\VioletStorage\pilot1000`
4. Identity check 通过后才可进行 API/浏览器请求

### Stage D: Import

1. **Dry-run first**: 预检导入范围，报告文件数、跳过数、错误
2. 用户确认后执行真实导入
3. `max_files` 基于 manifest 或全部支持文件数
4. 记录 `scan_job_id`
5. 记录导入的 media IDs
6. 报告: imported / skipped_unsupported / skipped_duplicate / errors
7. 不转换不支持的格式（JFIF 等直接跳过）

### Stage E: Content Classification

1. 对所有导入的 media 进行分类
2. 记录 `classification_job_id`
3. 报告: anime / non_anime / unknown 分布
4. 验证只有导入的 media 被分类（无全库意外运行）
5. 无 job crash

### Stage F: Anime-only AI Tagging

1. **Dry-run first**: 确认范围和预算
2. `content_class_filter=["anime"]`
3. `only_without_ai_tags=true`
4. `phase_total_budget` = anime_without_ai_tags
5. 批次大小: 200–250
6. 每批 `max_items` = min(batch_cap, remaining_budget)
7. failed > 0 时停止并诊断
8. 无 localization 副作用（`localization_status` 应为 skipped）
9. 非 anime / unknown 不被标注

### Stage G: General-only Localization

1. 仅在 AI tagging 完成后执行
2. **Dry-run first**
3. 仅 general 类别
4. LLM 仅在此阶段启用
5. 后台 worker 禁用，除非明确批准
6. `batch_max_items` 安全上限保留
7. `phase_total_budget` = general_missing
8. character / entity 延迟
9. failed = 0 偏好

### Stage H: Manual Correction Smoke

1. 使用 Admin PATCH workflow 修正明显的 general 翻译问题
2. 不做批量编辑
3. 记录修正次数和修正的 canonical_name

### Stage I: Browser Acceptance

1. Gallery 页面正常
2. content_class 筛选器工作
3. 标签显示正确（中文 general 标签）
4. canonical_name 覆盖率
5. 搜索/筛选行为正常
6. Admin 作业历史页面
7. 无控制台/服务器错误
8. 媒体详情页正常
9. 缩略图加载

### Stage J: Closeout Report

1. Pass/fail 判定
2. 回滚需求评估
3. 进入 2000-tier 或 entity/clustering 的阻塞项

---

## 8. 预算与规模限制

### 8.1 Import

| 参数 | 值 |
|------|-----|
| 目标 media 数 | ~1000 支持文件 |
| 不支持格式 | 跳过，不转换 |
| 源路径 | E:\VioletPilotData（或用户指定） |

### 8.2 Classification

| 参数 | 值 |
|------|-----|
| 范围 | 所有导入的 media |
| 失败容忍 | 0 preferred |

### 8.3 AI Tagging

| 参数 | 值 |
|------|-----|
| 范围 | anime only |
| dry-run | 每次真实运行前必须先 dry-run |
| 批次大小 | 200 或 250 |
| phase_total_budget | anime_without_ai_tags |
| 每批 max_items | min(batch_cap, remaining_budget) |
| 停止条件 | failed > 0 or scope leak |

### 8.4 Localization

| 参数 | 值 |
|------|-----|
| 范围 | general only |
| dry-run | 每次真实运行前必须先 dry-run |
| 批次大小 | 200 |
| phase_total_budget | general_missing |
| 排除 | character / copyright / artist |
| Entity Resolver | 不使用 |

### 8.5 LLM

| 参数 | 值 |
|------|-----|
| 使用场景 | 仅 localization 阶段 |
| provider/fallback | 从 .env 配置读取，必须记录 |
| secrets | 不打印 |

### 8.6 Manual Correction

| 参数 | 值 |
|------|-----|
| 规模 | 小量修正 |
| 方式 | Admin PATCH workflow |
| 直接 SQL | 仅紧急情况，需明确批准 |

---

## 9. 安全门控

### 9.1 强制门控清单

| # | 门控 | 触发时机 | 失败处理 |
|---|------|----------|----------|
| 1 | 分支卫生 | 每个 PR / 任务前 | 停止，创建新分支 |
| 2 | Python identity | 任何脚本/服务器前 | 停止，诊断 |
| 3 | Server identity | 任何 API/浏览器请求前 | 停止，重启服务器 |
| 4 | `--expected-python` | 必填 | 不可跳过 |
| 5 | `--expected-storage-root` | 必填 | 不可跳过 |
| 6 | `--expected-code-root` | 必填 | 不可跳过 |
| 7 | `--expected-git-sha` | 必填 | 不可跳过 |
| 8 | 活跃作业检查 | 创建新作业前 | 等待或中止旧作业 |
| 9 | 备份 | 写入阶段前 | 确保备份目录存在 |
| 10 | Dry-run | import / localization / AI tagging 前 | 必须先 dry-run |
| 11 | `job_id` 归因 | 每个作业 | 记录并报告 |
| 12 | `scan_job_id` 归因 | 导入后 | 记录并报告 |
| 13 | imported media IDs | 导入后 | 记录并报告 |
| 14 | 源路径报告 | 使用真实源路径 | 不使用虚假路径 |
| 15 | 无 global Python | 始终 | 报错并停止 |
| 16 | 无 ghost/stale server | 启动前 | 检查并清理 |
| 17 | 无并发 V.I.O.L.E.T. 实例 | 始终 | 单例策略 |
| 18 | 破坏性操作保护 | dry-run + sample + backup + confirm_phrase + 二次确认 | 缺任一则拒绝 |

### 9.2 DB 安全

- 不触碰 `blombooru`（生产）
- 不触碰 `blombooru_test`（通用测试）
- 不触碰 `blombooru_test_medium`（Tier-500 基线）
- 所有写操作仅限 `blombooru_test_1000`

### 9.3 存储安全

- 不触碰 `C:\Users\kyloris\VioletStorage\test`（Tier-500 存储）
- 不修改 E:\VioletPilotData 中的源文件
- 所有存储写入仅限 `C:\Users\kyloris\VioletStorage\pilot1000`
- 不使用 iCloud 路径

---

## 10. 通过/失败标准

### 10.1 Import

| 标准 | 要求 |
|------|------|
| 成功率 | >= 98% |
| 源文件变异 | 无 |
| 存储根不匹配 | 无 |

### 10.2 Classification

| 标准 | 要求 |
|------|------|
| 覆盖率 | >= 95%（或解释 unknown 原因） |
| 作业崩溃 | 0 |

### 10.3 AI Tagging

| 标准 | 要求 |
|------|------|
| failed | 0 preferred |
| 成功率 | >= 95% |
| 范围 | 仅 anime |
| non_anime / unknown | 不被新标注 |

### 10.4 Localization

| 标准 | 要求 |
|------|------|
| 范围 | general-only |
| failed | 0 preferred |
| character/entity 本地化 | 无 |
| tag_translations delta | 准确归因 |

### 10.5 Browser Acceptance

| 标准 | 要求 |
|------|------|
| 阻塞性控制台错误 | 无 |
| 缩略图加载 | 正常 |
| 核心筛选器 | 工作 |
| 详情页 | 正常 |
| Admin 状态 | 可理解 |
| 数据计数 | 浏览器检查后稳定 |

### 10.6 Overall

| 标准 | 要求 |
|------|------|
| 错误 DB/存储 | 无 |
| LLM 副作用 | 无 |
| 破坏性操作 | 无 |
| 回滚路径 | 清晰 |

---

## 11. 报告模板

Phase 3.3b 执行完成后，交付报告应包含以下部分：

```markdown
# Phase 3.3b — Tier-1000 Pilot Execution Report

## Environment
- Working directory:
- Branch:
- Server command:
- PID:
- Port:
- VIOLET_BASE_URL:
- VIOLET_ENV:
- POSTGRES_DB:
- VIOLET_STORAGE_ROOT:
- sys.executable:
- Python version:
- Identity check result:

## Import
- scan_job_id:
- Source path:
- Total files:
- Imported:
- Skipped (unsupported):
- Skipped (duplicate):
- Errors:
- Success rate:

## Classification
- classification_job_id:
- anime:
- non_anime:
- unknown:
- failed:

## AI Tagging
- Job IDs:
- Batches:
- Total processed:
- Tags added:
- Failed:
- Dry-run results:

## Localization
- Job IDs:
- Total processed:
- Translated:
- Failed:
- Remaining after:

## Manual Corrections
- Count:
- Examples:

## 真实浏览器验收
- 验收方式:
- 浏览器/Playwright project:
- URL tested:
- Pages/flows validated:
- Pass/fail:
- Skipped/not covered:

## Pass/Fail Summary
| Stage | Result |
|-------|--------|

## Cleanup
- Server stopped (PID):
- Port freed:
```

---

## 12. 短期路线图

### Phase 3.3: Tier-1000 核心流水线验证
- 3.3a: 规划（本文档） ✅
- 3.3b: 执行（用户提供数据后开始）
- 目标: 验证 import → classify → AI tag → localize → browser acceptance 在 ~1000 规模下的稳定性
- 退出条件: Tier-1000 核心流水线 closeout 通过

### Phase 3.4: Entity Metadata Resolver MVP
- character / copyright / artist 未解析列表
- 规范化实体记录
- 手动确认工作流
- 官方/常用中文名称
- LLM 仅作为候选名称生成器，不具有盲目权威
- 不自动应用 LLM 生成的名称

### Phase 3.5: Similar Image / Same-Character Clustering MVP
- embedding 或感知相似度
- 近重复组
- 标签辅助的角色中心分组
- 手动确认 UI

### Phase 3.6: Tier-2000 Pilot
- 在 3.3 通过且 3.4/3.5 MVP 范围确定后开始
- 更大规模的端到端验证

### Phase 4: Full-library Workflow
- iCloud 安全工作流
- 不支持格式转换（JFIF / HEIC / AVIF → PNG / JPEG）
- 完整库级别操作

### 重要约束
- Phase 3.3 不应无限延续 — 在 Tier-1000 核心流水线 closeout 后退出
- Entity / meta 和聚类是短期内的下一模块，不是被遗忘的
- 格式转换明确延迟到 Phase 4+

---

## 13. 延迟工作清单

| 项目 | 当前状态 | 计划阶段 |
|------|----------|----------|
| character / entity resolver | 58 character 翻译缺失 | Phase 3.4 |
| similar image / character clustering | 未开始 | Phase 3.5 |
| 不支持格式转换 (JFIF/HEIC/AVIF) | 5 JFIF 文件被跳过 | Phase 4+ |
| 中文自动完成 | 待确认状态 | Phase 3.4 或 3.5 |

---

## 14. 下一步操作（需要用户确认）

1. **用户补充数据**: 向 `E:\VioletPilotData` 添加约 500+ 个新的支持格式图像文件（.jpg / .png / .jpeg / .webp / .gif），使总支持文件数达到 ~1000
   - 或提供新的源数据目录路径
2. **确认环境**: 确认 `blombooru_test_1000` 作为 Tier-1000 数据库名称
3. **确认存储**: 确认 `C:\Users\kyloris\VioletStorage\pilot1000` 作为存储路径
4. **批准执行计划**: 确认本文档中的执行阶段序列和安全门控
5. **Phase 3.3b 开始**: 用户确认以上内容后，agent 创建执行分支并开始 Stage A (Preflight)

---

## 附录 A: 预检验证记录

### 分支卫生
```
Base SHA: 143678b (origin/main)
Branch: phase3.3a-tier1000-pilot-plan
Commits ahead of origin/main: 0 (at time of branch creation)
Working tree: clean
```

### Python Identity
```
sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe
Python version: 3.12.0
pip: 26.1 (from venv)
is-venv: True
Preflight result: PASS
```

### Main State
```
PR #42 (07c2cfb): included in main ✓
PR #43 (143678b): included in main ✓
load_dotenv(override=False): confirmed at config.py:12 ✓
PATCH endpoint: confirmed at tag_localization.py:291 ✓
E2E spec: tests/e2e/patch-translation-mode.spec.ts ✓
```

### Dataset Inspection
```
Script: scripts/inspect_pilot_dataset.py --path "E:\VioletPilotData"
Total files: 527
Supported: 522
Unsupported: 5 (all .jfif)
Size: 1.84 GB
Structure: flat
iCloud: no
Local: yes (E:\)
```
