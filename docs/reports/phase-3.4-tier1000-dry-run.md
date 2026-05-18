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
2. 新建 `tests/test_audit_tier1000.py` — 84 个测试用例，33 个测试类
3. **成功执行实际审计**: 全部 1,000 个文件通过，零不一致
4. 修复 1 个 Codex P1 和 2 个 P2 问题 (Round 1)
5. 修复 5 个 Codex P2 问题 (Round 2, 详见 §6)
6. 修复 5 个 Codex P2 问题 (Round 3, 详见 §6)
7. 修复 1 个 Codex P1 和 7 个 P2/主动修复 (Round 4, 详见 §6)

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
| 重复目标路径 | 0 |
| 无效 size_bytes | 0 |
| 源文件检查 | 已启用 |
| 源文件缺失 | 0 |
| 预期字节 | 3,204,263,387 (2.98 GB) |
| 实际字节 | 3,204,263,387 (2.98 GB) |
| 非预期文件 | 0 |
| **结果** | **PASS** |

---

## 5. 代码变更

### 新增: `scripts/audit_tier1000.py` (~621 行)

| 函数 | 说明 |
|------|------|
| `audit_manifest_vs_disk()` | 核心: 逐行验证 manifest copy-row 与磁盘状态 |
| `scan_unexpected_files()` | 检测目标目录中不在 manifest 中的文件 |
| `generate_audit_csv()` | 生成逐行 CSV 审计日志 |
| `generate_audit_json()` | 生成隐私安全 JSON 摘要 (无绝对路径) |
| `main()` | CLI 入口: `--manifest`, `--target-root`, `--check-source`, `--audit-csv`, `--json`, `--json-output` |

**自包含设计**: 所有辅助函数直接内联，不依赖其他脚本:
`_clean_field`, `_is_under`, `_is_known_exclusion`, `_path_key`, `_row_has_required_values`,
`SUPPORTED_EXTENSIONS`, `_REQUIRED_FIELDS`, `KNOWN_EXCLUSION_CODES`, `KNOWN_EXCLUSION_PREFIXES`

### 新增: `tests/test_audit_tier1000.py` (~1262 行, 84 个测试)

| 测试类 | 测试数 | 说明 |
|--------|--------|------|
| `TestPerfectMatch` | 3 | 全部文件匹配 → exit 0 |
| `TestMissingTarget` | 3 | 目标文件缺失 → `MISSING_TARGET`, exit 4 |
| `TestSizeMismatch` | 2 | 大小不一致 → `SIZE_MISMATCH`, exit 4 |
| `TestExtensionMismatch` | 2 | 扩展名不一致 → `EXT_MISMATCH`, exit 4 |
| `TestUnexpectedFiles` | 3 | 非预期文件 → 报告, exit 4 |
| `TestExcludedRowsSkipped` | 2 | Excluded 行不参与验证 |
| `TestTruncatedRowsSkipped` | 2 | 截断行计数并跳过，触发 exit 4 (P2a 回归) |
| `TestSourceCheck` | 3 | `--check-source` 标志行为 |
| `TestCLISafety` | 4 | CLI 参数缺失 → exit 2; 路径无效 → exit 1 |
| `TestAuditCSVOutput` | 2 | CSV 输出 schema 和行数 |
| `TestJSONOutput` | 2 | JSON stdout 和文件输出 |
| `TestSourceNotModified` | 1 | 目标目录在审计后不变 |
| `TestEmptyManifest` | 1 | 空 manifest (仅 header) → exit 0 |
| `TestTargetEscape` | 1 | 路径越界 → `TARGET_ESCAPE`, exit 4 |
| `TestSelfContained` | 1 | 无 importlib/stage_pilot_files 依赖 (P1 回归) |
| `TestPathKey` | 3 | `_path_key()` Windows 小写/POSIX 保留大小写 |
| `TestTargetResolveError` | 2 | 路径解析异常 → `TARGET_RESOLVE_ERROR`, exit 4 |
| `TestDuplicateTarget` | 3 | 重复目标路径 → `DUPLICATE_TARGET`, exit 4 |
| `TestInvalidSize` | 4 | 空/非整数/负数 size_bytes → `INVALID_SIZE`, exit 4 |
| `TestUnicodeDecodeError` | 2 | 二进制 manifest → 错误报告, exit 1 |
| `TestInvalidExclusionReason` | 3 | 未知 exclusion_reason → `INVALID_EXCLUSION_REASON`, exit 4 |
| `TestBlankSourcePath` | 3 | 空 source_path → 检测计数, exit 4 |
| `TestBlankExtension` | 3 | 空 extension → 检测计数, exit 4 |
| `TestTargetRootResolveError` | 2 | target_root 解析异常 → 错误记录 |
| `TestScanErrors` | 2 | walk/path_key 错误收集 → discrepancy |
| `TestCopyRowsZeroFail` | 3 | copy_rows==0 非空 manifest → FAIL, exit 4 (R4 P1) |
| `TestPrivacySafeJSON` | 3 | JSON 输出无绝对路径, scan_errors 脱敏 |
| `TestUnsupportedExtension` | 3 | .bmp 等不支持扩展名 → exit 4 |
| `TestTargetAccessError` | 2 | tp.is_file() OSError → 结构化错误 |
| `TestSourceAccessError` | 3 | source_access_errors 计数器接线 + CLI exit 4 |
| `TestInvalidSelectionReason` | 4 | 未知 selection_reason → exit 4 |
| `TestZeroSize` | 3 | size_bytes==0 → ZERO_SIZE, exit 4 |
| `TestCLIOutputWriterErrors` | 3 | 输出写入失败无 traceback |
| **合计** | **84** | |

### 新增: `docs/reports/phase-3.4-audit-summary.json`

隐私安全的 JSON 审计摘要，无绝对路径，`paths_redacted: true`。

---

## 6. Codex 修复项

### Round 1

#### P1: importlib 跨脚本依赖 (已修复)

**问题**: 原版通过 `importlib.util.spec_from_file_location` 从 `stage_pilot_files.py` 导入私有辅助函数，当 main 分支代码变更时可能断裂。

**修复**: 将所有辅助函数内联至 `audit_tier1000.py`，完全自包含。

**回归测试**: `TestSelfContained.test_no_importlib_dependency`

#### P2a: 截断行静默通过 (已修复)

**问题**: 截断行 (CSV 缺少必要字段) 被计数但不影响 `has_discrepancy`，导致含截断行的 manifest 仍返回 exit 0。

**修复**: 在 `has_discrepancy` 判断中添加 `result["truncated_rows"] > 0`。

**回归测试**: `TestTruncatedRowsSkipped.test_truncated_row_causes_exit_4`

#### P2b: resolve() 未处理异常 (已修复)

**问题**: `target_root.resolve()` 在路径无效时抛出 `OSError`，产生未处理的 traceback。

**修复**: 包裹在 `try/except OSError`，记录结构化错误信息至 `result["errors"]`。

### Round 2 (5 个 P2 修复)

#### P2-1: 路径键大小写归一化 (已修复)

**问题**: Windows 上 `expected_targets` 集合使用原始路径字符串，大小写不同的相同路径不会被视为重复或匹配，导致 `scan_unexpected_files()` 可能产生误报。

**修复**: 新增 `_path_key(p: Path) -> str` 辅助函数 — 在 Windows (`os.name == "nt"`) 上将 resolved 路径转为小写，POSIX 上保留大小写。所有 `expected_targets` 操作和 `scan_unexpected_files()` 统一使用 `_path_key()`。

**回归测试**: `TestPathKey` (3 个测试: 类型检查、Windows 小写、POSIX 保留)

#### P2-2: 逐行目标路径解析异常 (已修复)

**问题**: `tp.resolve()` 在畸形路径 (极长路径、非法字符) 上可能抛出 `OSError`/`RuntimeError`/`ValueError`，导致未处理异常和 traceback。

**修复**: 包裹在 `try/except (OSError, RuntimeError, ValueError)`，记录为 `TARGET_RESOLVE_ERROR` 状态，计入 `target_escapes`。

**回归测试**: `TestTargetResolveError` (2 个测试: API 记录 + CLI exit 4)

#### P2-3: 重复目标路径检测 (已修复)

**问题**: 如果 manifest 中两行指向同一 `proposed_target_path`，脚本不会检测到，可能导致文件计数不一致。

**修复**: 在验证循环中，先检查 `_path_key(tp)` 是否已在 `expected_targets` 中。若重复，记录 `DUPLICATE_TARGET` 状态，计入 `duplicate_target_paths`。`has_discrepancy` 包含此计数器。

**回归测试**: `TestDuplicateTarget` (3 个测试: 检测重复、exit 4、不同路径无误报)

#### P2-4: 无效 size_bytes 严格验证 (已修复)

**问题**: `size_bytes` 字段为空、非整数或负数时，`int()` 转换可能静默产生 0 或抛异常，导致大小校验逻辑不可靠。

**修复**: 严格验证: 空值 → `ValueError("blank")`，非整数 → 捕获 `ValueError`/`TypeError`，负数 → `ValueError("negative")`。所有无效情况记录为 `INVALID_SIZE` 状态，计入 `invalid_size_rows`。`has_discrepancy` 包含此计数器。

**回归测试**: `TestInvalidSize` (4 个测试: 空值、非整数、负数、CLI exit 4)

#### P2-5: UTF-8 解码失败处理 (已修复)

**问题**: manifest 文件如果包含非 UTF-8 字节 (损坏文件)，`csv.DictReader` 会抛出 `UnicodeDecodeError`，产生未处理异常。

**修复**: 在 manifest 读取的 `except` 子句中添加 `UnicodeDecodeError`。

**回归测试**: `TestUnicodeDecodeError` (2 个测试: API 错误记录 + CLI exit 1)

### Round 3 (5 个 P2 修复)

#### P2-R3-1: 未知 `exclusion_reason` 导致 false-PASS (已修复)

**问题**: manifest 行的 `exclusion_reason` 如果包含未知值 (例如拼写错误、新增但未注册的排除码)，`_is_known_exclusion()` 返回 `False`，该行被错误地视为 copy-row 继续验证，可能产生 false PASS 或误导性的 `MISSING_TARGET`。

**修复**: 在排除判断分支中添加 `elif exclusion:` — 当 `exclusion_reason` 非空但未被 `_is_known_exclusion()` 识别时，记录 `INVALID_EXCLUSION_REASON` 状态，计入新增 `invalid_exclusion_reasons` 计数器，并纳入 `has_discrepancy` 判断 (触发 exit 4)。

**回归测试**: `TestInvalidExclusionReason` (3 个测试: API 检测 + CLI exit 4 + 合法码不误报)

#### P2-R3-2: 空 `source_path` 绕过检查 (已修复)

**问题**: copy-row 的 `source_path` 为空时，`--check-source` 的 `Path("").is_file()` 返回 `False`，静默计入 `source_missing` — 但根因是数据错误 (空路径)，非真实文件缺失。无 `--check-source` 时空路径完全不被检测。

**修复**: 在文件存在/大小/扩展名检查之前，新增无条件的 `if not source_path:` 检查。空 `source_path` 记录为失败 (`Blank source_path in copy row`)，计入新增 `blank_source_paths` 计数器，纳入 `has_discrepancy`。

**回归测试**: `TestBlankSourcePath` (3 个测试: API 检测 + CLI exit 4 + 非空不误报)

#### P2-R3-3: 空 `extension` 跳过验证 (已修复)

**问题**: copy-row 的 `extension` 字段为空时，`if extension and actual_ext != extension.lower()` 条件短路为 `False`，扩展名校验被完全跳过。空扩展名在 manifest 中属于数据错误，应触发报告。

**修复**: 新增无条件的 `if not extension:` 检查。空 `extension` 记录为失败 (`Blank extension in copy row`)，计入新增 `blank_extensions` 计数器，纳入 `has_discrepancy`。

**回归测试**: `TestBlankExtension` (3 个测试: API 检测 + CLI exit 4 + 非空不误报)

#### P2-R3-4: `target_root.resolve()` 仅捕获 `OSError` (已修复)

**问题**: `target_root.resolve()` 在某些平台/边界情况下可能抛出 `RuntimeError` 或 `ValueError` (例如循环符号链接)，但 `except` 子句仅捕获 `OSError`，导致未处理 traceback。

**修复**: 将 `except OSError` 扩展为 `except (OSError, RuntimeError, ValueError)`。

**回归测试**: `TestTargetRootResolveError` (2 个测试: RuntimeError + ValueError)

#### P2-R3-5: `scan_unexpected_files()` 静默跳过不可读子目录 (已修复)

**问题**: `os.walk()` 默认静默跳过权限被拒的子目录，导致审计可能遗漏未预期文件。同时 `_path_key()` 在畸形路径上抛异常也未捕获。

**修复**:
1. `os.walk()` 添加 `onerror=_on_walk_error` 回调，收集 walk 错误至 `scan_errors` 列表
2. `_path_key()` 调用包裹在 `try/except (OSError, RuntimeError, ValueError)`，异常也收集至 `scan_errors`
3. 函数返回值从 `list[str]` 改为 `tuple[list[str], list[str]]` (unexpected, scan_errors)
4. `main()` 中 `scan_errors` 长度计入 `has_discrepancy`

**回归测试**: `TestScanErrors` (2 个测试: `_path_key` 异常收集 + walk 错误触发 discrepancy)

### Round 4 (1 个 P1 + 7 个 P2/主动修复)

#### P1: `copy_rows == 0` 非空 manifest 应判定 FAIL (已修复)

**问题**: 全部行被排除时，`copy_rows == 0` 但 `manifest_total_rows > 0`，审计仍返回 exit 0 (PASS)，可能掩盖 manifest 配置错误。

**修复**: 在 `has_discrepancy` 判断中添加 `(result["copy_rows"] == 0 and result["manifest_total_rows"] > 0)`，同时将此情况记录为 warning。

**回归测试**: `TestCopyRowsZeroFail` (3 个测试: API 检测 + CLI exit 4 + 空 manifest 仍 PASS)

#### P2-R4-1: JSON 输出隐私安全 (已修复)

**问题**: `scan_errors` 和 `unexpected_file_samples` 可能泄露绝对路径。

**修复**: 新增 `_redact_path()` 函数，对 JSON 输出 (文件和 stdout) 中的路径进行脱敏处理。`scan_errors` 脱敏为 `scan_errors_redacted`。

**回归测试**: `TestPrivacySafeJSON` (3 个测试: 文件输出 + stdout + scan_errors 脱敏)

#### P2-R4-2: 不支持的扩展名应失败 (已修复)

**问题**: `.bmp` 等不在 `SUPPORTED_EXTENSIONS` 中的扩展名静默通过审计。

**修复**: 在扩展名验证分支中，非空扩展名若不在 `SUPPORTED_EXTENSIONS` 中，记录 `UNSUPPORTED_EXT` 状态，计入 `unsupported_extensions` 计数器。

**回归测试**: `TestUnsupportedExtension` (3 个测试: .bmp 检测 + CLI exit 4 + 支持扩展名通过)

#### P2-R4-3: `tp.is_file()` 访问错误结构化 (已修复)

**问题**: 目标文件 `tp.is_file()` 调用在权限拒绝等情况下抛出 `OSError`，产生未处理 traceback。

**修复**: 包裹在 `try/except OSError`，记录为 `TARGET_ACCESS_ERROR` 状态，计入 `target_access_errors`。

**回归测试**: `TestTargetAccessError` (2 个测试: 计数器接线 + CLI exit 4)

#### P2-R4-4: `sp.is_file()` 源文件访问错误结构化 (已修复)

**问题**: `--check-source` 时源文件 `sp.is_file()` 访问错误可能产生未处理 traceback。

**修复**: 包裹在 `try/except OSError`，记录为源访问错误，计入 `source_access_errors`。

**回归测试**: `TestSourceAccessError` (3 个测试: 计数器接线 + CLI exit 4 + 正常访问无误报)

#### P2-R4-5: 未知 `selection_reason` 应失败 (已修复)

**问题**: copy-row 的 `selection_reason` 若非 `existing_tier500` 或 `new_candidate`，静默通过。

**修复**: 新增 `KNOWN_SELECTION_REASONS = {"existing_tier500", "new_candidate"}`，未知值记录为 `INVALID_SELECTION_REASON` 状态，计入 `invalid_selection_reasons`。

**回归测试**: `TestInvalidSelectionReason` (4 个测试: 未知值 + 空值 + CLI exit 4 + 合法值不误报)

#### P2-R4-6: `size_bytes == 0` 应独立于负数单独检测 (已修复)

**问题**: 零字节 size 被归入 "negative" 分支，但零字节可能指示数据错误，应独立检测。

**修复**: 将零字节从 `INVALID_SIZE` 分支独立为 `ZERO_SIZE` 状态，计入 `zero_size_rows`。

**回归测试**: `TestZeroSize` (3 个测试: 零值检测 + CLI exit 4 + 正数通过)

#### P2-R4-7: CLI 输出写入失败无 traceback (已修复)

**问题**: `generate_audit_csv()` 和 `generate_audit_json()` 写入失败时，`main()` 中未捕获异常可能产生 traceback。

**修复**: 在 `main()` 中包裹 `try/except OSError`，捕获写入异常输出结构化错误到 stderr。

**回归测试**: `TestCLIOutputWriterErrors` (3 个测试: CSV 写入错误 + JSON 写入错误 + manifest 访问错误)

---

## 7. 退出码设计

| 退出码 | 含义 |
|--------|------|
| 0 | 全部检查通过 |
| 1 | Manifest 错误 (文件不存在, 解析失败, target_root 非目录) |
| 2 | CLI 参数错误 (缺少必要参数) |
| 4 | 验证不一致 (缺失文件, 大小/扩展名不匹配, 截断行, 重复路径, 无效大小, 非预期文件等) |

---

## 8. 测试结果

| 测试文件 | 测试数 | 结果 |
|----------|--------|------|
| `test_audit_tier1000.py` | 84 | 全部通过 |
| 全套 (`tests/`) | 829 | 全部通过 (10 skipped) |

```
命令: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/ -v
sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe
Python 3.12.0
829 passed, 10 skipped in 24.07s
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

- 84 个单元/集成测试 (tmp_path 隔离, CLI subprocess)
- 实际执行审计: 1,000 个文件全部通过
- JSON/CSV 输出格式验证

---

## 11. 停止规则

本轮 (Round 4) 修复后，若 Codex 仅报告 P3/nit/portability/UX/docs 建议，
且不涉及 false PASS、traceback 或审计数据损坏，则停止扩展 PR #48，建议合并。

---

## 12. 下一步

1. **用户审查并合并 PR #48**
2. **Phase 3.5: 数据库导入** — 将 `E:\VioletPilotData_1000` 的 1,000 个文件导入 V.I.O.L.E.T. 数据库
