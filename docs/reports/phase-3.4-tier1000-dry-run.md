# Phase 3.4 — Tier-1000 Pre-import Audit Report

**日期**: 2026-05-18
**阶段**: Phase 3.4 — Tier-1000 暂存验证 (Manifest-vs-Disk Verification)
**分支**: `phase3.4-tier1000-audit-clean`
**基线提交**: `5b06488` (origin/main)

---

## 1. 执行摘要

基于 Phase 3.3b 已暂存的 1,000 个文件 (`E:\VioletPilotData_1000`, 2.98 GB)，
完成了以下只读验证工作:

1. 新建 `scripts/audit_tier1000.py` — 自包含的 manifest-vs-disk 验证脚本
2. 新建 `tests/test_audit_tier1000.py` — 32 个测试用例，15 个测试类
3. **成功执行实际审计**: 全部 1,000 个文件通过，零不一致
4. 修复 1 个 Codex P1 和 2 个 P2 问题 (详见 §6)

**无文件修改、无 import、无 DB、无 LLM、无 AI、无分类。只读验证。**

---

## 2. 事件背景

原始 PR #47 因分支污染被拒: 分支 `phase3.4-tier1000-audit` 错误地从
`phase3.3a.1-icloud-candidate-manifest` 分出，导致 GitHub diff 包含 12 个文件
(预期仅 5 个)。同时存在以下代码问题:

- **Codex P1**: `audit_tier1000.py` 通过 importlib 导入 `stage_pilot_files.py` 私有辅助函数 — 脆弱的跨脚本依赖
- **Codex P2a**: 截断行静默通过，不触发 exit 4
- **Codex P2b**: `target_root.resolve()` 失败产生未处理异常

本次交付基于从 `origin/main` (`5b06488`) 创建的干净分支重新实现。

---

## 3. 安全门控清单

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | Python venv 身份验证 | `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe` (3.12.0) |
| 2 | 脚本为只读操作 | 无 `shutil.copy`, 无 `open(w)`, 无 `os.remove`, 无 `mkdir` (仅输出目录) |
| 3 | 未修改 `E:\VioletPilotData_1000` | 审计前后文件数/大小不变 |
| 4 | 未修改源文件 | 仅 `Path.is_file()` + `Path.stat()` 读取 |
| 5 | 未修改任何数据库 | 无 DB 操作 |
| 6 | 目标路径逃逸检测 | `_is_under()` 校验，0 个逃逸 |
| 7 | 截断行检测 | `_row_has_required_values()` + `has_discrepancy` 触发 exit 4 |
| 8 | 非预期文件扫描 | `scan_unexpected_files()`，0 个非预期 |
| 9 | 分支基线干净 | 从 `origin/main` (`5b06488`) 创建，无污染 |
| 10 | 无 importlib 依赖 | 自包含脚本，无跨脚本私有导入 |

---

## 4. 审计结果

| 指标 | 值 |
|------|-----|
| Manifest 总行 | 5,326 |
| Copy 行 | 1,000 |
| Excluded 行 | 4,326 |
| 截断行 | 0 |
| 目标 PASS | **1,000** |
| 目标 MISSING | 0 |
| 大小不一致 | 0 |
| 扩展名不一致 | 0 |
| 目标路径逃逸 | 0 |
| 源文件检查 | 已启用 |
| 源文件缺失 | 0 |
| 预期字节 | 3,204,263,387 (2.98 GB) |
| 实际字节 | 3,204,263,387 (2.98 GB) |
| 非预期文件 | 0 |
| **结果** | **PASS** |

---

## 5. 代码变更

### 新增: `scripts/audit_tier1000.py` (~310 行)

| 函数 | 说明 |
|------|------|
| `audit_manifest_vs_disk()` | 核心: 逐行验证 manifest copy-row 与磁盘状态 |
| `scan_unexpected_files()` | 检测目标目录中不在 manifest 中的文件 |
| `generate_audit_csv()` | 生成逐行 CSV 审计日志 |
| `generate_audit_json()` | 生成隐私安全 JSON 摘要 (无绝对路径) |
| `main()` | CLI 入口: `--manifest`, `--target-root`, `--check-source`, `--audit-csv`, `--json`, `--json-output` |

**自包含设计**: 所有辅助函数直接内联，不依赖其他脚本:
`_clean_field`, `_is_under`, `_is_known_exclusion`, `SUPPORTED_EXTENSIONS`,
`_REQUIRED_FIELDS`, `_row_has_required_values`, `KNOWN_EXCLUSION_CODES`, `KNOWN_EXCLUSION_PREFIXES`

### 新增: `tests/test_audit_tier1000.py` (~530 行, 32 个测试)

| 测试类 | 测试数 | 说明 |
|--------|--------|------|
| `TestPerfectMatch` | 3 | 全部文件匹配 → exit 0 |
| `TestMissingTarget` | 3 | 目标文件缺失 → `MISSING_TARGET`, exit 4 |
| `TestSizeMismatch` | 2 | 大小不一致 → `SIZE_MISMATCH`, exit 4 |
| `TestExtensionMismatch` | 2 | 扩展名不一致 → `EXT_MISMATCH`, exit 4 |
| `TestUnexpectedFiles` | 3 | 非预期文件 → 报告, exit 4 |
| `TestExcludedRowsSkipped` | 2 | Excluded 行不参与验证 |
| `TestTruncatedRowsSkipped` | 2 | 截断行计数并跳过，触发 exit 4 (P2 回归) |
| `TestSourceCheck` | 3 | `--check-source` 标志行为 |
| `TestCLISafety` | 4 | CLI 参数缺失 → exit 2; 路径无效 → exit 1 |
| `TestAuditCSVOutput` | 2 | CSV 输出 schema 和行数 |
| `TestJSONOutput` | 2 | JSON stdout 和文件输出 |
| `TestSourceNotModified` | 1 | 目标目录在审计后不变 |
| `TestEmptyManifest` | 1 | 空 manifest (仅 header) → exit 0 |
| `TestTargetEscape` | 1 | 路径越界 → `TARGET_ESCAPE`, exit 4 |
| `TestSelfContained` | 1 | 无 importlib/stage_pilot_files 依赖 (P1 回归) |
| **合计** | **32** | |

### 新增: `docs/reports/phase-3.4-audit-summary.json`

隐私安全的 JSON 审计摘要，无绝对路径，`paths_redacted: true`。

---

## 6. Codex 修复项

### P1: importlib 跨脚本依赖 (已修复)

**问题**: 原版通过 `importlib.util.spec_from_file_location` 从 `stage_pilot_files.py` 导入私有辅助函数，当 main 分支代码变更时可能断裂。

**修复**: 将所有辅助函数内联至 `audit_tier1000.py`，完全自包含。

**回归测试**: `TestSelfContained.test_no_importlib_dependency` — 读取脚本源码，断言无 `stage_pilot_files` 或 `importlib.util.spec_from_file_location` 引用。

### P2a: 截断行静默通过 (已修复)

**问题**: 截断行 (CSV 缺少必要字段) 被计数但不影响 `has_discrepancy`，导致含截断行的 manifest 仍返回 exit 0。

**修复**: 在 `has_discrepancy` 判断中添加 `result["truncated_rows"] > 0`。

**回归测试**: `TestTruncatedRowsSkipped.test_truncated_row_causes_exit_4` — 构造仅含部分字段的 CSV 行，断言 exit 4。

### P2b: resolve() 未处理异常 (已修复)

**问题**: `target_root.resolve()` 在路径无效时抛出 `OSError`，产生未处理的 traceback。

**修复**: 包裹在 `try/except OSError`，记录结构化错误信息至 `result["errors"]`。

---

## 7. 退出码设计

| 退出码 | 含义 |
|--------|------|
| 0 | 全部检查通过 |
| 1 | Manifest 错误 (文件不存在, 解析失败, target_root 非目录) |
| 2 | CLI 参数错误 (缺少必要参数) |
| 4 | 验证不一致 (缺失文件, 大小/扩展名不匹配, 截断行, 非预期文件等) |

---

## 8. 测试结果

| 测试文件 | 测试数 | 结果 |
|----------|--------|------|
| `test_audit_tier1000.py` | 32 | 全部通过 |
| `test_stage_pilot_files.py` | 55 | 全部通过 |
| `test_generate_candidate_manifest.py` | 38 | 全部通过 |
| `test_media_processor_mime_magic_cache.py` | 28 | 全部通过 |
| **合计** | **153** | **全部通过** |

```
命令: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/ -v
sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe
Python 3.12.0
153 passed in 3.05s
```

---

## 9. 执行命令

```
C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe scripts/audit_tier1000.py \
    --manifest ".local_manifests/phase-3.3a.1-candidate-manifest.csv" \
    --target-root "E:\VioletPilotData_1000" \
    --check-source \
    --audit-csv ".local_manifests/phase-3.4-audit.csv" \
    --json-output "docs/reports/phase-3.4-audit-summary.json"
```

退出码: 0 (PASS)

---

## 10. 真实浏览器验收

**不适用。** Phase 3.4 为 CLI 脚本 (manifest-vs-disk 验证)，无 UI 组件。
验收通过以下方式完成:

- 32 个单元/集成测试 (tmp_path 隔离, CLI subprocess)
- 实际执行审计: 1,000 个文件全部通过
- JSON/CSV 输出格式验证

---

## 11. 下一步

1. **用户审查并合并 PR**
2. **Phase 3.5: 数据库导入** — 将 `E:\VioletPilotData_1000` 的 1,000 个文件导入 V.I.O.L.E.T. 数据库
