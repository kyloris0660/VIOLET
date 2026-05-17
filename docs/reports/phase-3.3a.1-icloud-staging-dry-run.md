# Phase 3.3a.1 — iCloud-safe Candidate Selection Dry-run Report

**日期**: 2026-05-17
**阶段**: Phase 3.3a.1 — iCloud-safe 候选选择和暂存 (Dry-run)
**分支**: `phase3.3a.1-icloud-candidate-manifest`
**基线提交**: `eea8363` (origin/main)

---

## 1. 执行摘要

对 iCloud 照片库进行了只读扫描，生成了 Tier-1000 候选清单。
扫描结果通过了暂存验证 (dry-run)，确认 1000 个文件可安全暂存到 `E:\VioletPilotData_1000`。

**本阶段仅 dry-run。未复制、移动、删除任何文件。未修改任何数据库。**

---

## 2. 扫描参数

| 参数 | 值 |
|------|-----|
| 源目录 | iCloud 照片库 (只读扫描) |
| 已有数据 | `E:\VioletPilotData` |
| 暂存目录 | `E:\VioletPilotData_1000` |
| 目标总数 | 1000 |
| 随机种子 | 3301 |
| 选择策略 | `random_seed_3301` |
| 模式 | dry-run |

---

## 3. 源目录扫描结果

| 指标 | 值 |
|------|-----|
| 总扫描文件 | 38,124 |
| 支持格式且合格 | 33,277 |
| 不支持格式 | 4,325 |
| 与 Tier-500 重复 | 522 |
| iCloud 占位符 | 0 |
| stat 错误 | 0 |
| 隐藏文件 | 0 |

---

## 4. 候选选择结果

| 指标 | 值 |
|------|-----|
| 已有 Tier-500 文件 | 522 |
| 需要新增 | 478 |
| 已选新增 | 478 |
| 合计 | 1,000 |
| 新增文件总大小 | 1.17 GB |
| 已有文件总大小 | 1.84 GB |
| 总复制大小 | 3.01 GB |

---

## 5. 暂存验证结果 (dry-run)

| 检查项 | 结果 |
|--------|------|
| 源文件存在 | 1000/1000 ✓ |
| 源文件缺失 | 0 |
| 目标文件名冲突 | 0 |
| 目标路径越界 | 0 |
| 不支持扩展名 | 0 |
| 验证结果 | **VALID** |

---

## 6. Manifest 文件

| 文件 | 路径 | 说明 |
|------|------|------|
| CSV manifest | `.local_manifests/phase-3.3a.1-candidate-manifest.csv` | 含完整路径，已 gitignore，不提交 |
| JSON summary | `docs/reports/phase-3.3a.1-icloud-staging-summary.json` | 仅聚合计数，隐私安全，已提交 |

**CSV manifest 行数**: 5,847 (522 existing + 478 new + 4,847 excluded)

---

## 7. 安全验证

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | 源目录未修改 | ✓ 只读扫描 |
| 2 | 未复制任何文件 | ✓ dry-run 模式 |
| 3 | 未修改任何数据库 | ✓ 无 DB 操作 |
| 4 | iCloud 占位符已检测 | ✓ 0 个占位符 |
| 5 | 重复文件已排除 | ✓ 522 个排除 |
| 6 | 目标路径越界检查 | ✓ 0 个越界 |
| 7 | `--execute` 已阻止 | ✓ 返回错误码 2 |
| 8 | 隐私安全 | ✓ 完整路径仅在 `.local_manifests/` |

---

## 8. 工具验证

| 工具 | 测试数 | 结果 |
|------|--------|------|
| `generate_candidate_manifest.py` | 24 | 全部通过 |
| `stage_pilot_files.py` | 14 | 全部通过 |
| 合计 | 38 | 全部通过 |

---

## 9. 下一步

1. **用户审批候选清单** — 查看 `.local_manifests/phase-3.3a.1-candidate-manifest.csv`
2. **Phase 3.3b Stage A: 创建暂存目录** — `E:\VioletPilotData_1000`
3. **Phase 3.3b Stage B: 执行复制** — `stage_pilot_files.py --execute` (待实现)
4. **Phase 3.3b Stage C: 暂存后验证** — `inspect_pilot_dataset.py --path "E:\VioletPilotData_1000"`
