"""Negative scope and downstream proofs for the existing production contract."""

from copy import deepcopy
import pytest
from scripts.check_production_import_recovery import reconstruct_recovery, check_public_result


@pytest.fixture()
def evidence(tmp_path):
    before, sources, run_items, accounting, media = [], [], [], [], []
    for ident in range(1, 499):
        cohort = ('original_unattempted' if ident <= 108 else 'original_failed' if ident <= 190 else
                  'verified_gap' if ident <= 458 else 'observed_new')
        good = ident > 190 and ident != 458
        source = dict(id=ident, source_root_id=2, relative_path=f'{ident}.png', relative_path_hash=f'{ident:064x}',
            media_id=ident if good else None, content_hash=f'{ident:032x}' if good else None,
            app_media_path=f'{ident}.png' if good else None, app_media_exists=good,
            sync_state='imported' if good else 'failed', classification_status='classified',
            ai_tagging_status='ai_tagged', localization_status='localized',metadata_json={},failure_reason='read_timeout')
        sources.append(source)
        if ident <= 458:
            old = deepcopy(source)
            old['sync_state'] = 'deferred_unprocessed' if ident <= 108 else 'failed' if ident <= 190 else 'skipped_existing_media'
            before.append(old)
        if ident != 458:
            run_items.append(dict(id=ident, source_item_id=ident, sync_run_id=28,
                action='import' if ident <= 190 or ident > 458 else 'skip',
                item_state='failed' if ident <= 190 else 'imported' if ident > 458 else 'skipped_existing_media',
                media_id=ident if good else None))
        outcome = 'unexecuted' if ident == 458 else 'retryable' if not good else 'imported' if ident > 458 else 'existing_media'
        row = dict(source_item_id=ident,cohort=cohort,after=deepcopy(source),outcome=outcome,
            media_id=source['media_id'],app_media_exists=good,reason='read_timeout',reentry_condition='normal_next_plan',
            new_attempt_run_ids=[28],historical_attempt_run_ids=[])
        if ident == 458:
            row.update(metadata_observation={'available':False},boundary_reason='source_missing',reachable_in_next_plan=True)
        accounting.append(row)
        if good:
            path = tmp_path/f'{ident}.png'
            path.write_bytes(b'x')
            media.append(dict(id=ident,path=path.name,resolved_app_path=str(path),file_size=1,hash=source['content_hash'],content_class='anime'))
    missing=[dict(source_item_id=i,relative_path=f'{i}.png',reason='stat_error') for i in [458,90001,90002,90003,90004]]
    policy=[dict(source_item_id=None,relative_path=f'{i}.heic',reason='unsupported_extension') for i in range(173)]
    runs=[dict(id=30,run_type='manual_sync_execute',summary_json={'manual_sync_execute':{
        'private_discovery':{'metadata_dispositions':missing+policy}}})]
    return dict(before={'affected_rows':before},original={'sources':deepcopy(sources),'run_items':deepcopy(run_items),'runs':runs},
        snapshot={'sources':sources,'run_items':run_items,'attempt_history':deepcopy(run_items)},
        accounting={'items':accounting},metadata={'metadata_only_items':missing,'policy_observations':[{'observation':r} for r in policy]},
        downstream={'media':media,'tags':[dict(id=1,canonical_name='fixture',category='general')],
            'media_tags':[dict(media_id=m['id'],tag_id=1,source='ai_wd') for m in media],
            'static_coverage':['fixture'],'translations':[]},storage_root=tmp_path)


def test_contract_reconstructs_all_four_cohorts_and_static_reuse(evidence):
    result = reconstruct_recovery(**evidence)
    assert result['original_total'] == result['total'] == 498
    assert result['verified_gaps'] == 268 and result['observed_new'] == 40
    assert result['downstream_complete'] == 307
    assert result['metadata_missing'] == 5 and result['metadata_overlap'] == 1
    assert result['policy_observations'] == 173


def test_independent_inventory_gap_cannot_disappear_with_current_rows(evidence):
    evidence['history'] = dict(sources=deepcopy(evidence['original']['sources']),sync_run_count=1,
        run_item_count=497,sync_runs=[dict(id=28,run_item_count=497)])
    evidence['inventory'] = dict(enumeration_count=1,content_reads=0,directory_errors=[],entries=[])
    assert reconstruct_recovery(**evidence)['total'] == 498
    # Independent before-inventory proves this extra supported source existed;
    # removing both the later run and accounting cannot make it disappear.
    evidence['inventory']['entries'].append(dict(source_root_id=2,relative_path='new.png',
        relative_path_hash='f'*64,suffix='.png',metadata={'is_file':True,'file_size':10}))
    with pytest.raises(ValueError,match='history_confirmed_gap_not_processed'):
        reconstruct_recovery(**evidence)


@pytest.mark.parametrize('mutation', ['drop_gaps','drop_new','drop_one','replace_one','duplicate_one',
    'drop_source_and_accounting','omit_downstream','classification_pending','tagging_deferred','localization_pending',
    'missing_translation','missing_app_file','summary_mismatch','missing_metadata','duplicate_policy'])
def test_contract_rejects_incomplete_or_substituted_evidence(evidence, mutation):
    rows=evidence['accounting']['items']
    if mutation == 'drop_gaps':
        evidence['accounting']['items']=[r for r in rows if r['cohort'] != 'verified_gap']
    elif mutation == 'drop_new':
        evidence['accounting']['items']=[r for r in rows if r['cohort'] != 'observed_new']
    elif mutation in {'drop_one','drop_source_and_accounting'}:
        rows.pop()
        if mutation == 'drop_source_and_accounting':
            evidence['snapshot']['sources'].pop()
    elif mutation == 'replace_one':
        rows[-1]['source_item_id']=99999
    elif mutation == 'duplicate_one':
        rows.append(deepcopy(rows[0]))
    elif mutation == 'omit_downstream':
        evidence['downstream']['media'].pop()
    elif mutation in {'classification_pending','tagging_deferred','localization_pending'}:
        key={'classification_pending':'classification_status','tagging_deferred':'ai_tagging_status','localization_pending':'localization_status'}[mutation]
        evidence['snapshot']['sources'][-1][key]='deferred'
        rows[-1]['after'][key]='deferred'
        rows[-1]['outcome']='followup_pending'
    elif mutation == 'missing_translation':
        evidence['downstream']['static_coverage']=[]
    elif mutation == 'missing_app_file':
        # Only the fixture-owned one-byte app file is removed.
        (evidence['storage_root']/'498.png').unlink()
    elif mutation == 'summary_mismatch':
        rows[-1]['outcome']='existing_media'
    elif mutation == 'missing_metadata':
        evidence['metadata']['metadata_only_items'].pop()
    elif mutation == 'duplicate_policy':
        evidence['metadata']['policy_observations'].append(evidence['metadata']['policy_observations'][0])
    with pytest.raises(ValueError, match='import_recovery_'):
        reconstruct_recovery(**evidence)
