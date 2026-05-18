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

## 7. 测试结果

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

---

## 8. 退出码设计

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 (dry-run valid 或 execute 完成) |
| 1 | 验证失败 (manifest invalid) |
| 2 | CLI 参数错误 (缺少必要标志) |
| 3 | 复制失败 (execute 模式中出错) |

---

## 9. 下一步

1. **用户审查 PR** — 确认代码和暂存结果
2. **Phase 3.4: 元数据扫描** — 对 `E:\VioletPilotData_1000` 执行 `inspect_pilot_dataset.py`
3. **Phase 4: 导入与标签** — 将暂存数据导入 V.I.O.L.E.T. 数据库
