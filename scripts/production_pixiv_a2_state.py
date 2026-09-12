"""A2 state projection; earlier phase evidence remains historical."""
import json
from pathlib import Path

PHASE = 'PRODUCTION-PIXIV-A2'
BASE = '2b742ca3e49d4b7d361300e98e0b2d9c1a0eb63d'


def validate(state, root):
    from scripts.check_documentation_state import DocumentationStateError, PUBLIC_FORBIDDEN

    def require(ok, reason):
        if not ok:
            raise DocumentationStateError('pixiv_a2_' + reason)

    require(state.get('schema_version') == 'violet.current-phase.v2', 'schema')
    require(state.get('branch') == 'codex/production-pixiv-a2', 'branch')
    require(state.get('accepted_mainline_base') == BASE, 'base')
    require(state.get('planning_approved') is True, 'authorization')
    require(state.get('llm_budget_usd') in (10, 30), 'budget')
    if state.get('llm_budget_usd') == 30:
        require(state.get('budget_authorization') == {
            'source': '43-CODEX-A2-QUALITY-CLOSEOUT.zh-CN.md',
            'previous_cap_usd': 10, 'cumulative_cap_usd': 30}, 'budget_authorization')
    require(state.get('safe_to_merge') is False and state.get('route_approved') is False, 'owner_boundary')
    require(state.get('next_phase_started') is False, 'no_a3')
    for key in ('merge', 'push_main', 'additional_reviewer', 'original_file_mutation', 'confirmed_entity_write'):
        require(state['authorities'].get(key) is False, 'forbidden_' + key)
    require(state['authorities']=={
        'production':True,'additive_migration':True,'metadata_only_provider':True,
        'bounded_llm_adjudication':True,'pixiv_apply':True,'merge':False,'push_main':False,
        'additional_reviewer':False,'original_file_mutation':False,'confirmed_entity_write':False},'authority_map')
    require(not any(p.search(json.dumps(state, ensure_ascii=False)) for p in PUBLIC_FORBIDDEN), 'redaction')
    for link in state['durable_links']:
        path = Path(link['path'])
        require(not path.is_absolute() and '..' not in path.parts and (root / path).is_file(), 'link')
    if state.get('target_met'):
        from scripts.check_production_pixiv_a2 import check_public_result
        check_public_result(json.loads((root / state['result_path']).read_text(encoding='utf-8')), root=root)


def render(state):
    lines = ['# 当前交接', '', '<!-- GENERATED: docs/state/current-phase.json -->', '',
             '当前状态以 docs/state/current-phase.json 为准。', '',
             f"- 阶段：`{PHASE}`；状态：`{state['current_status']}`。",
             f"- 分支：`{state['branch']}`；PR：`{state.get('pr_number')}`。",
             f'- 已接受并合并基线：PR #152 / `{BASE}`。',
             f"- 工程目标完成：`{state['target_met']}`；负责人接受：`{state['manual_acceptance_status']}`。",
             f"- 新LLM调用累计上限USD {state['llm_budget_usd']}；既有消费不清零，原图不下载、不上传。", '', '## 已完成检查点', '']
    lines += ['- ' + item for item in state['completed_checkpoints']]
    lines += ['', '## 后续执行', '',
              '1. 固定T0、兼容输入和累计所有权。',
              '2. metadata获取、缓存复用和完整概念裁决。',
              '3. 全量原生产落地、搜索/详情、质量与恢复验收。', '',
              '## 范围和复用', '',
              '- T0固定Media/work/page对应关系，T0后新增单独记账。',
              '- 单先验不自动等同可信绑定；冲突有明确归宿。',
              '- 真实metadata、保存payload和兼容judgments优先复用。',
              '- 作者稳定ID保留，角色/作品所需context保留。', '',
              '## 验证', '',
              '- focused与隔离PostgreSQL覆盖实际变化。',
              '- 一次完整non-E2E；历史缺失AI证据单列。',
              '- 备份恢复、原子replacement、owned rollback及分批等价。',
              '- 真实搜索/详情和正常launcher，记录当前全量性能。',
              '- 工程完成由A2可执行结果契约检查。', '',
              '## 操作边界', '',
              '- 原生产在长时间获取和计算期间继续可用。',
              '- 不重复导入、分类、WD标签和本地化基础工作。',
              '- 不修改人工标签、相册、确认Entity或原文件。',
              '- 不合并、不推main、不触发额外reviewer、不进入A3。',
              '- 自动验证、负责人接受及产品用户体验分别记账。', '',
              f"下一检查点：{state['next_required_checkpoint']}", '', '## 持久入口', '']
    lines += [f"- [{link['label']}](../{link['path']})" for link in state['durable_links']]
    return '\n'.join(lines) + '\n'
