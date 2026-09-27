import copy
import subprocess
import pytest


@pytest.mark.parametrize('change',['removed','added','run','binding','duplicate','missing'])
def test_final_projection_requires_current_full_state(change):
    from scripts.production_pixiv_a2_evidence import verify_final_projection
    before={'active_runs':1,'run_keys':['run'],'bindings':2,'binding_rows':[[1,1,2,3,4,1],[2,1,3,4,5,1]],
        'bound_media_ids':[4,5],'source_record_ids':[3,4],'duplicate_support_count':0}
    live=copy.deepcopy(before)
    assert verify_final_projection(before,live)==live
    if change=='removed':live.update(active_runs=0,run_keys=[],bindings=0,binding_rows=[])
    elif change=='added':live.update(active_runs=2,run_keys=['run','extra'])
    elif change=='run':live['run_keys']=['replacement']
    elif change=='binding':live['binding_rows'][0][-1]+=1
    elif change=='duplicate':live['duplicate_support_count']=1
    else:before.pop('binding_rows')
    with pytest.raises(ValueError,match='live_final_projection_changed'):
        verify_final_projection(before,live)


def test_old_passing_suite_requires_executable_behavior_carry_forward(tmp_path):
    from scripts.check_production_pixiv_a2 import verify_historical_suite_carry_forward
    def git(*args):return subprocess.check_output(['git','-C',str(tmp_path),*args],text=True).strip()
    git('init','-q');git('config','user.name','fixture');git('config','user.email','fixture@example.invalid')
    p=tmp_path/'docs/state/current-phase.json';p.parent.mkdir(parents=True);p.write_text('{}')
    behavior=tmp_path/'resolver.py';behavior.write_text('v=1')
    git('add','.');git('commit','-qm','old passing suite');old=git('rev-parse','HEAD')
    p.write_text('{"stage":"documented"}');git('add','.');git('commit','-qm','docs')
    verify_historical_suite_carry_forward(old,git('rev-parse','HEAD'),repo=tmp_path)
    behavior.write_text('v=2');git('add','.');git('commit','-qm','behavior')
    with pytest.raises(ValueError,match='historical_passes_not_carried_forward'):
        verify_historical_suite_carry_forward(old,git('rev-parse','HEAD'),repo=tmp_path)
