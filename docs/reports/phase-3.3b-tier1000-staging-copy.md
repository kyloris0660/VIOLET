# Phase 3.3b — Tier-1000 Staging Copy Report

**日期**: 2026-05-18
**阶段**: Phase 3.3b — Tier-1000 暂存复制 (Pre-flight + Execute)
**分支**: `phase3.3b-tier1000-staging-copy`
**基线提交**: `4a73cd7` (origin/main, PR #45 merged)

---

## 1. 执行摘要

基于 Phase 3.3a.1 生成的候选清单 (CSV manifest)，完成了以下工作：

1. 为 `stage_pilot_files.py` 新增受控复制执行功能 (`--execute` 模式)
2. 修复了 2 个 PR #45 遗留的验证器鲁棒性缺陷
3. 新增 20 个测试用例 (含执行器、审计器、安全门控)
4. **成功执行了实际暂存复制**: 1000 个文件 → `E:\VioletPilotData_1000`，总计 2.98 GB
5. **Codex 审查闭环** (P1+P2): 审计不一致硬失败 + `target_root` 文件守卫，新增 8 个测试

**无 import、无 DB、无 LLM、无 AI、无分类、无 iCloud 变更。**

---

## 2. 安全门控清单

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | Python venv 身份验证 | ✓ `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe` (3.12.0) |
| 2 | `--execute` 需 `--confirm-copy-tier1000` | ✓ 不带确认标志 → exit 2 |
| 3 | `--execute` 需 `--source-root` | ✓ 不带 → exit 2 |
| 4 | `--execute` 需 `--existing-root` | ✓ 不带 → exit 2 |
| 5 | `--execute` + `--dry-run` 互斥 | ✓ 同时传入 → exit 2 |
| 6 | 无模式标志 → exit 2 | ✓ |
| 7 | 源路径在批准根目录内 | ✓ 0 个越界 |
| 8 | 目标路径在 target_root 内 | ✓ 0 个逃逸 |
| 9 | 从不覆盖已有目标文件 | ✓ 目标文件已存在 → 拒绝复制 |
| 10 | 首次错误即停止 | ✓ 部分失败保留已复制文件 |
| 11 | 源目录未修改 | ✓ 只读操作 |
| 12 | 未修改任何数据库 | ✓ 无 DB 操作 |

---

## 3. Dry-run 验证结果

| 指标 | 值 |
|------|-----|
| 总 manifest 行 | 5,326 |
| Existing (Tier-500) | 522 |
| New candidates | 478 |
| Excluded | 4,326 |
| 截断行 | 0 |
| 源文件存在 | 1,000/1,000 ✓ |
| 源文件缺失 | 0 |
| 目标路径碰撞 | 0 |
| 目标路径逃逸 | 0 |
| 不支持扩展名 | 0 |
| 空白 source_path | 0 |
| 空白 target_path | 0 |
| 空白 extension | 0 |
| 后缀缺失 | 0 |
| 扩展名不匹配 | 0 |
| 源根越界 | 0 |
| 目标文件已存在 | 0 |
| 总复制大小 | 2.98 GB |
| 验证结果 | **VALID** |

---

## 4. 复制执行结果

| 指标 | 值 |
|------|-----|
| 处理总行 | 5,326 |
| 已复制文件 | 1,000 |
| 已跳过 (excluded) | 4,326 |
| 已跳过 (truncated) | 0 |
| 失败 | 0 |
| 已复制字节 | 2.98 GB |

---

## 5. Post-Copy 审计

| 指标 | 值 |
|------|-----|
| 目标目录 | `E:\VioletPilotData_1000` |
| 目录存在 | ✓ |
| 文件总数 | 1,000 |
| 总字节 | 2.98 GB |
| .jpg | 819 |
| .jpeg | 18 |
| .png | 163 |
| 非预期扩展名 | 0 |
| 结果 | **SUCCESS** |

---

## 6. 代码变更

### `scripts/stage_pilot_files.py` — 重写

| 变更 | 说明 |
|------|------|
| `_clean_field()` | None-safe CSV 字段访问器 |
| `_REQUIRED_FIELDS` | 截断行检测 (缺少必要字段) |
| `validate_manifest()` | 新增: `blank_extensions`, `source_root_violations`, `truncated_rows`, `approved_source_roots` 参数 |
| `_is_under()` | 路径包含关系辅助函数 |
| `execute_copy()` | 受控复制执行器 — 首错即停，不覆盖，验证源根 |
| `post_copy_audit()` | Post-copy 审计 — 文件计数、字节、扩展名分布、抽样 |
| `main()` | 新增 CLI 参数: `--execute`, `--confirm-copy-tier1000`, `--source-root`, `--existing-root`; 3 阶段流程: 验证 → 复制 → 审计 |

### `tests/test_stage_pilot_files.py` — 新增 20 个测试

| 测试类 | 测试数 | 说明 |
|--------|--------|------|
| `TestExecuteCLISafety` | 4 | CLI 安全门控 (替换旧 `TestExecuteBlocked`) |
| `TestCleanField` | 4 | `_clean_field` 辅助函数 |
| `TestBlankExtensionValidation` | 2 | 空白 extension 验证 |
| `TestTruncatedRows` | 1 | 截断行检测 |
| `TestApprovedSourceRoots` | 3 | 批准源根验证 |
| `TestExecuteCopy` | 5 | 复制执行器 (成功/跳过/缺失/覆盖拒绝/根越界) |
| `TestPostCopyAudit` | 3 | 审计器 (计数/不存在/异常扩展名) |
| **合计** | **22** | (4 替换 + 18 新增) |

---

## 7. 测试结果 (初始交付)

| 测试文件 | 测试数 | 结果 |
|----------|--------|------|
| `test_generate_candidate_manifest.py` | 38 | 全部通过 |
| `test_stage_pilot_files.py` | 55 | 全部通过 |
| **合计** | **93** | **全部通过** |

```
命令: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_generate_candidate_manifest.py tests/test_stage_pilot_files.py -v
sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe
Python 3.12.0
93 passed in 1.03s
```

> **注**: Codex closeout 后测试数已增至 101，见 §9.4。

---

## 8. 退出码设计

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 (dry-run valid 或 execute 完成且审计通过) |
| 1 | 验证失败 (manifest invalid) |
| 2 | CLI 参数错误 (缺少必要标志) |
| 3 | 复制失败 (execute 模式中出错，含 copied≠expected / truncated>0) |
| 4 | **审计失败** (文件数不一致 / 非预期扩展名) ← NEW |

---

## 9. Codex Closeout Patch (PR #46 第二轮)

### 9.1 P1 修复: 审计不一致硬失败 (MUST FIX)

**问题**: `post_copy_audit()` 返回文件数与预期不一致时，`main()` 仅打印 WARNING 并 exit 0。
审计形同虚设，无法拦截部分复制或污染。

**修复内容** (4 条硬失败路径):

| # | 检查条件 | 退出码 | 说明 |
|---|----------|--------|------|
| 1 | `copy_res["copied"] != copy_count` | 3 | 复制计数与预期不符 |
| 2 | `copy_res["skipped_truncated"] > 0` | 3 | 执行期间遇到截断行 |
| 3 | `audit["total_files"] != copy_count` | 4 | 审计文件数与预期不符 |
| 4 | `audit["unexpected_extensions"]` 非空 | 4 | 审计发现非预期扩展名 |

### 9.2 P2 修复: `target_root` 文件守卫 (SHOULD FIX)

**问题**: `execute_copy()` 调用 `target_root.mkdir(parents=True, exist_ok=True)` 前未检查
`target_root` 是否已存在为普通文件，导致 `NotADirectoryError` 裸 traceback。

**修复内容**:
- `target_root.exists() and not target_root.is_dir()` → 结构化错误返回 (`failed=1, failed_reason`)
- `target_root.mkdir()` 包裹 `try/except OSError` → 结构化错误返回
- CLI 层收到 `failed > 0` → exit 3

### 9.3 新增测试

| 测试类 | 测试数 | 说明 |
|--------|--------|------|
| `TestExecuteCopyTargetRootGuard` | 2 | target_root 为文件 → 单元返回 failure / CLI exit 3 |
| `TestPostCopyAuditHardFail` | 6 | 审计硬失败路径 (计数不一致/非预期扩展名/copied≠expected/truncated/正常 exit 0) |
| **新增合计** | **8** | |

### 9.4 Closeout 后测试结果

| 测试文件 | 测试数 | 结果 |
|----------|--------|------|
| `test_generate_candidate_manifest.py` | 38 | 全部通过 |
| `test_stage_pilot_files.py` | 63 | 全部通过 |
| **合计** | **101** | **全部通过** |

```
命令: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_generate_candidate_manifest.py tests/test_stage_pilot_files.py -v
sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe
Python 3.12.0
101 passed in 1.44s
```

### 9.5 Closeout 后只读审计验证

| 指标 | 值 |
|------|-----|
| 目标目录 | `E:\VioletPilotData_1000` |
| 目录存在 | ✓ |
| 文件总数 | 1,000 |
| 总字节 | 3,204,263,387 (2.98 GB) |
| .jpg | 819 |
| .jpeg | 18 |
| .png | 163 |
| 非预期扩展名 | 0 |
| 结果 | **SUCCESS** |

**注**: 本次审计为只读操作，未重新执行复制。确认 Phase 3.3b 初次复制结果完好。

### 9.6 停止规则评估

Codex 第二轮修复完成后，剩余潜在建议均为 P2/P3 级别:
- 回滚/事务性 (P3) — 超出 copy 脚本范畴
- 重试逻辑 (P3) — 首错即停是设计选择
- 日志框架 (P3) — 当前 print 足够
- 进度条 (P3) — 低优先级 UX
- 跨平台可移植性 (P3) — 项目限定 Windows

**建议: 合并 PR #46，进入 Phase 3.4。**

---

## 10. Codex Closeout Patch — 第三轮 (P1 ×3)

### 10.1 P1 #1: 父目录 mkdir 结构化失败

**问题**: `execute_copy()` 中 `tp.parent.mkdir(parents=True, exist_ok=True)` 未包裹 try/except。
若中间父路径已存在为普通文件，会导致裸 `NotADirectoryError` traceback 而非结构化 copy 失败。

**修复内容**:
- `tp.parent.mkdir()` 包裹 `try/except OSError`
- 失败时: `copy_result["failed"] += 1`, `failed_path`, `failed_reason`, `errors` 填充
- 立即 `return copy_result`
- CLI 层收到 `failed > 0` → exit 3 (已有逻辑)

### 10.2 P1 #2: csv.DictReader None-fill 截断行检测

**问题**: `validate_manifest()` 和 `execute_copy()` 均使用 `_REQUIRED_FIELDS.issubset(row.keys())`
检测截断行，但 `csv.DictReader` 对短行填充 None 而非移除 key，导致截断行永远不被检测。

**修复内容**:
- 新增共享辅助函数 `_row_has_required_values(row)`:
  - 遍历 `_REQUIRED_FIELDS`，检查 key 是否存在 **且** value 不为 None
  - 空字符串不视为截断 (已有独立 schema 验证器处理)
- 替换两处 `_REQUIRED_FIELDS.issubset(row.keys())` 为 `_row_has_required_values(row)`

### 10.3 P1 #3: 主动审计

搜索全文件所有以下模式:

| 模式 | 搜索结果 | 状态 |
|------|----------|------|
| `_REQUIRED_FIELDS.issubset(row.keys())` | 仅剩 docstring 注释 | ✓ 已全部替换 |
| `.strip()` on potentially None | 仅在 `_clean_field` 内 (已有 None 守卫) | ✓ 安全 |
| 未保护的 `.mkdir()` | `target_root.mkdir` 已有 try/except; `tp.parent.mkdir` 已修复 | ✓ 安全 |
| `row.get()` 直接调用 | 仅在 `_clean_field` 内 (已有 None 守卫) | ✓ 安全 |

### 10.4 新增测试

| 测试类 | 测试数 | 说明 |
|--------|--------|------|
| `TestRowHasRequiredValues` | 4 | 辅助函数单元测试 (完整行/None值/缺失key/空字符串) |
| `TestTruncatedRowNoneFill` | 2 | DictReader None-fill 截断检测 (validate + execute) |
| `TestParentMkdirFailure` | 2 | 父目录 mkdir 失败 (单元 + CLI exit 3) |
| **新增合计** | **8** | |

### 10.5 第三轮后测试结果

| 测试文件 | 测试数 | 结果 |
|----------|--------|------|
| `test_generate_candidate_manifest.py` | 38 | 全部通过 |
| `test_stage_pilot_files.py` | 71 | 全部通过 |
| **合计** | **109** | **全部通过** |

```
命令: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_generate_candidate_manifest.py tests/test_stage_pilot_files.py -v
sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe
Python 3.12.0
109 passed in 1.72s
```

### 10.6 停止规则评估

第三轮修复完成后，所有 copy-safety P1 问题已关闭。剩余潜在建议均为非 copy-safety 级别:
- 回滚/事务性 (P3) — 超出 copy 脚本范畴
- 重试逻辑 (P3) — 首错即停是设计选择
- 日志框架 (P3) — 当前 print 足够
- 进度条 (P3) — 低优先级 UX
- 跨平台可移植性 (P3) — 项目限定 Windows

**建议: 合并 PR #46，进入 Phase 3.4。**

---

## 11. Codex Closeout Patch — 第四轮 (P1 ×1 + P2 ×3)

### 11.1 P1: target_root 与 source/existing 根不相交守卫 (MUST FIX)

**问题**: `execute_copy()` 未检查 `target_root` 是否与 source_root / existing_root 重叠。
若用户误传 `--target` 为 iCloud 照片库或已有暂存目录，暂存复制会污染或覆盖源数据。

**修复内容**:
- 新增辅助函数 `_ensure_target_root_disjoint(target_root, protected_roots)`:
  - 检查三种不安全关系: 精确相等、target 在 protected 内部、protected 在 target 内部
  - 所有路径比较使用 `_is_under()` + case-insensitive 精确匹配
- `execute_copy()` 中 `target_root.resolve()` 后、`mkdir()` 前调用
- 不相交检查失败 → 结构化错误返回 (`failed=1, failed_reason`)
- CLI 层收到 `failed > 0` → exit 3

### 11.2 P2: 执行时 manifest 读取失败结构化

**问题**: `execute_copy()` 中 `open(manifest_path)` 和 `csv.DictReader` 未 try/except。
文件不存在或 CSV 解析错误会导致裸 traceback。

**修复内容**:
- 新增共享辅助函数 `read_manifest_rows(manifest_path)`:
  - 返回 `tuple[list[dict], str | None]`
  - `try/except (OSError, csv.Error)` → 结构化错误消息
- `validate_manifest()` 和 `execute_copy()` 均可接受 `rows` 参数跳过文件读取

### 11.3 P2: `target_root.resolve()` 失败结构化

**问题**: `execute_copy()` 中 `target_root.resolve()` 未包裹 try/except。
畸形路径会导致裸 `RuntimeError` 或 `OSError` traceback。

**修复内容**:
- `execute_copy()` 中 `target_root.resolve()` 包裹 `try/except (RuntimeError, OSError)`
- 失败时 → 结构化错误返回 (`failed=1, failed_reason`)

### 11.4 P2: TOCTOU 防护 — 冻结验证后 manifest

**问题**: `main()` 中 `validate_manifest()` 和 `execute_copy()` 各自独立读取 CSV 文件。
验证通过后、执行前 manifest 文件被修改会导致执行使用不同数据。

**修复内容**:
- `main()` 新增 Phase 0: 调用 `read_manifest_rows()` 一次性读取所有行
- 读取失败 → `sys.exit(1)`
- 读取成功 → 同一 `manifest_rows` 传入 `validate_manifest(rows=...)` 和 `execute_copy(rows=...)`
- `validate_manifest()` 和 `execute_copy()` 新增 `rows: list[dict] | None` 参数

### 11.5 主动审计

| 模式 | 搜索结果 | 状态 |
|------|----------|------|
| `open(manifest_path` | 仅在 `read_manifest_rows()` 内 (try/except 包裹) | ✓ 安全 |
| `target_root.resolve()` | `validate_manifest` + `execute_copy` 均已 try/except | ✓ 安全 |
| `.mkdir(` | `target_root.mkdir` + `tp.parent.mkdir` 均已 try/except | ✓ 安全 |
| `approved_source_roots` | 所有调用点正确传递 | ✓ 安全 |
| `execute_copy(` | `main()` 调用传入 `rows=manifest_rows` | ✓ 安全 |

### 11.6 新增测试

| 测试类 | 测试数 | 说明 |
|--------|--------|------|
| `TestEnsureTargetRootDisjoint` | 4 | 辅助函数: 精确相等/target内部/protected内部/不相交 |
| `TestExecuteDisjointGuard` | 3 | 集成: target==source/target内existing/CLI exit 3 |
| `TestExecuteManifestReadFailure` | 1 | manifest 文件缺失 → 结构化失败 |
| `TestManifestSnapshotReuse` | 4 | read_manifest_rows/validate rows=/execute rows= |
| **新增合计** | **12** | |

附: 修复 1 个因新增不相交守卫而需适配的预有测试 (`TestExecuteCopyTargetRootGuard.test_target_root_is_file_returns_failure` — 改用不相交的 source root)

### 11.7 第四轮后测试结果

| 测试文件 | 测试数 | 结果 |
|----------|--------|------|
| `test_generate_candidate_manifest.py` | 38 | 全部通过 |
| `test_stage_pilot_files.py` | 83 | 全部通过 |
| **合计** | **121** | **全部通过** |

```
命令: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe -m pytest tests/test_generate_candidate_manifest.py tests/test_stage_pilot_files.py -v
sys.executable: C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe
Python 3.12.0
121 passed in 1.56s
```

### 11.8 停止规则评估

第四轮修复完成后，所有 copy-safety P1/P2 问题已关闭:
- target_root 与 source/existing 不相交 ✓
- manifest 读取结构化 ✓
- resolve 失败结构化 ✓
- TOCTOU 单次读取 ✓

剩余潜在建议均为非 copy-safety 级别:
- 回滚/事务性 (P3) — 超出 copy 脚本范畴
- 重试逻辑 (P3) — 首错即停是设计选择
- 日志框架 (P3) — 当前 print 足够
- 进度条 (P3) — 低优先级 UX
- 跨平台可移植性 (P3) — 项目限定 Windows

**建议: 合并 PR #46，进入 Phase 3.4。**

---

## 12. 下一步

1. **用户审查并合并 PR #46**
2. **Phase 3.4: 元数据扫描** — 对 `E:\VioletPilotData_1000` 执行 `inspect_pilot_dataset.py`
3. **Phase 4: 导入与标签** — 将暂存数据导入 V.I.O.L.E.T. 数据库
