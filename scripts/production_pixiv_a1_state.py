"""A1 current-state projection; historical PX contracts remain unchanged."""

import json
from pathlib import Path

PHASE = 'PRODUCTION-PIXIV-A1'
BASE = '26a6fc8d30ba2b2eae69f55a8e7c33d5a4b9cdd3'


def validate(state, root):
    def require(condition, reason):
        if not condition:
            from scripts.check_documentation_state import DocumentationStateError
            raise DocumentationStateError('a1_' + reason)
    require(state.get('schema_version') == 'violet.current-phase.v2', 'schema')
    require(state.get('branch') == 'codex/production-pixiv-a1', 'branch')
    require(state.get('accepted_mainline_base') == BASE, 'base')
    require(state.get('previous_phase_merge_commit') == BASE, 'previous_merge')
    require(state.get('planning_approved') is True, 'authorization')
    require(state.get('manual_acceptance_status') == 'pending_project_lead_review', 'owner_boundary')
    require(state.get('safe_to_merge') is False and state.get('route_approved') is False, 'review_boundary')
    require(state.get('next_phase_started') is False, 'no_a2')
    from scripts.check_documentation_state import PUBLIC_FORBIDDEN
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    require('\\u0000' not in serialized and not any(pattern.search(serialized) for pattern in PUBLIC_FORBIDDEN), 'redaction')
    for key in ('merge', 'provider_network', 'llm', 'original_file_mutation', 'truth_mutation'):
        require(state['authorities'].get(key) is False, 'forbidden_' + key)
    for link in state['durable_links']:
        path = Path(link['path'])
        require(not path.is_absolute() and '..' not in path.parts, 'link_path')
        require((root / path).is_file(), 'missing_link')
    if state.get('target_met'):
        from scripts.check_production_pixiv_a1 import check_public_result
        require(state.get('result_path') == 'docs/reports/production-pixiv-a1-summary.json', 'result_path')
        try:
            check_public_result(json.loads((root / state['result_path']).read_text(encoding='utf-8')))
        except (ValueError, OSError, KeyError, TypeError):
            require(False, 'result_evidence')


def render(state):
    lines = [
        '# 当前交接', '', '<!-- GENERATED: docs/state/current-phase.json -->', '',
        '本文件为当前状态的生成投影，不能替代当前任务授权。', '',
        '## 当前任务', '',
        f"- 阶段：`{PHASE}`。",
        f"- 状态：`{state['current_status']}`。",
        f"- 分支：`{state['branch']}`。",
        f"- PR：`{state['pr_number']}`。",
        f"- 起点：`{BASE}`。",
        '- PR #150 已合并，历史恢复副本证据保留。',
        f"- 目标完成：`{state['target_met']}`。",
        '- 项目负责人复审：待完成。',
        '- 产品用户亲自浏览：未据此任务执行作出声明。', '',
        '## 实施顺序', '',
        '1. 修复搜索、绑定版本、历史策略和提交后文件清理。',
        '2. 新鲜备份、独立恢复、迁移与撤回排练。',
        '3. 固定样本原库落地、真实图片和 launcher 验收。', '',
        '## 证据边界', '',
        '- 工程验证不等于 PR 合并。',
        '- 执行代理浏览器验证不等于项目负责人接受或产品用户亲自验收。',
        '- 恢复副本成功不等于原库成功。',
        '- 当前确定性 resolver 不包含 A2 完整模型裁决。',
        '- 本地测试不等于 GitHub CI。',
        '- 所有真实操作保存在本机私有记录。', '',
        '## 当前检查点', '',
    ]
    lines += ['- ' + item for item in state['completed_checkpoints']]
    lines += ['', '## 运行状态', '',
              f"- 生产候选：`{state.get('production_candidate_head') or '尚未部署'}`。",
              f"- 下一检查点：{state['next_required_checkpoint']}。", '',
              '## 授权和保全', '',
              '- 执行代理依据项目所有者授权自动传递真实 dry-run 校验值。',
              '- 必要增量迁移与可撤销样本 apply 已授权。',
              '- 原图、人工标签、相册、确认实体保留。',
              '- 不覆盖恢复原库，不清理无关工作区。',
              '- 不运行新 provider、LLM 或全库导入。',
              '- 不 merge、不推送 main、不额外触发 reviewer。',
              '- A2 和 A3 未执行。', '', '## 持久入口', '']
    lines += [f"- [{link['label']}](../{link['path']})" for link in state['durable_links']]
    lines += ['', '## 后续边界', '',
              '- A2 补齐缺失 metadata、兼容 judgments 与全量选择所有权。',
              '- A3 接通持续同步与队列。',
              '- 工作区对抗、多 worker 和远程证据能力不在本轮扩展。',
              f"- 更新：`{state['updated_at']}`。", '']
    return '\n'.join(lines)
