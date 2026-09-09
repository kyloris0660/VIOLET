"""Current repair route, without changing historical A1 acceptance evidence."""

import json
from pathlib import Path

PHASE = "PRODUCTION-IMPORT-RECOVERY"
BASE = "ea4bdd740943b2dad8c4eace88d0b33819d86cb8"


def validate(state, root):
    from scripts.check_documentation_state import DocumentationStateError, PUBLIC_FORBIDDEN

    def require(ok, reason):
        if not ok:
            raise DocumentationStateError("import_recovery_" + reason)

    require(state.get("schema_version") == "violet.current-phase.v2", "schema")
    require(state.get("branch") == "codex/production-import-recovery", "branch")
    require(state.get("accepted_mainline_base") == BASE, "base")
    require(state.get("planning_approved") is True, "authorization")
    require(state.get("safe_to_merge") is False and state.get("route_approved") is False, "owner_boundary")
    require(state.get("next_phase_started") is False, "no_next_phase")
    for key in ("merge", "push_main", "additional_reviewer", "pixiv_apply", "new_provider", "original_file_mutation"):
        require(state["authorities"].get(key) is False, "forbidden_" + key)
    serialized = json.dumps(state, ensure_ascii=False)
    require(not any(p.search(serialized) for p in PUBLIC_FORBIDDEN), "redaction")
    for link in state["durable_links"]:
        path = Path(link["path"])
        require(not path.is_absolute() and ".." not in path.parts and (root / path).is_file(), "link")
    # Positive engineering completion requires the repair's own executable evidence.
    if state.get("target_met"):
        from scripts.check_production_import_recovery import check_public_result
        check_public_result(json.loads((root / state["result_path"]).read_text(encoding="utf-8")), root=root)


def render(state):
    lines = ["# 当前交接", "", "<!-- GENERATED: docs/state/current-phase.json -->", "",
             "当前状态以 docs/state/current-phase.json 为准。", "",
             f"- 阶段：`{PHASE}`。", f"- 状态：`{state['current_status']}`。",
             f"- 分支：`{state['branch']}`；PR：`{state.get('pr_number')}`。",
             f"- 已接受并合并的基线：PR #151 / `{BASE}`。",
             f"- 工程目标完成：`{state['target_met']}`；负责人复审：`{state['manual_acceptance_status']}`。",
             f"- 修复生产候选：`{state.get('production_candidate_head') or '尚未部署'}`。", "",
             "## 当前检查点", ""]
    lines += ["- " + item for item in state["completed_checkpoints"]]
    lines += ["", "## 执行顺序", "",
              "1. 单文件失败有界尝试并继续其他候选。",
              "2. 记录私有原异常、逐项归宿和未执行身份。",
              "3. 公平轮转到期旧重试，新项和队尾均可推进。",
              "4. 隔离证明后部署候选，刷新现场清单并真实恢复。", "",
              "## 生产保全", "",
              "- 原数据库、存储、认证和日常 launcher 不迁移位置。",
              "- 切换前核对运行任务；不终止用户导入。",
              "- 旧运行目录和已适用备份作为恢复材料保留。",
              "- 原图、私有路径和凭据不进入公开 PR。", "",
              "## 验证范围", "",
              "- focused、必要 PostgreSQL、相关契约及文档检查。",
              "- 正常入口、新会话、有界面系统 Edge 验证。",
              "- 不重复完整 non-E2E，不补造历史 AI 证据。", "",
              "## 授权与交付边界", "",
              "三个连续步骤：失败隔离与诊断；公平候选与历史补漏；隔离验证、生产部署与实际恢复。",
              "原库/存储和正常 launcher 保持身份一致。源读取、正常水合/导入及本轮必要下游已授权；不修改源文件。",
              "自动测试、执行代理界面验证、负责人接受、所有者使用、PR 合并和实际运行分别记账。",
              "执行代理不合并、不推 main、不追加 reviewer；不启动 Pixiv A2/A3。", "",
              f"下一检查点：{state['next_required_checkpoint']}", "", "## 持久入口", ""]
    lines += [f"- [{link['label']}](../{link['path']})" for link in state["durable_links"]]
    return "\n".join(lines) + "\n"
