import json
import subprocess
import pytest

from test_pixiv_metadata_ingestion_service import db
from app.models import SourceMetadataRecord
from app.services.pixiv_metadata_ingestion_service import queue_media_for_pixiv_metadata, run_bounded_acquisition


@pytest.mark.parametrize('denials',[0,2,8])
def test_progress_publication_retries_transient_denial_and_preserves_old_checkpoint(tmp_path,monkeypatch,denials):
    from scripts import run_production_pixiv_a2_metadata as runner
    path=tmp_path/'progress.json';path.write_text('{"completed": 10}',encoding='utf-8')
    replace=runner.os.replace;calls=[];waits=[]
    def guarded(source,target):
        calls.append((source,target))
        assert json.loads(path.read_text(encoding='utf-8'))=={'completed':10}
        if len(calls)<=denials:raise PermissionError('simulated reader sharing denial')
        return replace(source,target)
    monkeypatch.setattr(runner.os,'replace',guarded)
    monkeypatch.setattr(runner.time,'sleep',waits.append)
    if denials==8:
        with pytest.raises(PermissionError):runner.write(path,{'completed':11})
        assert json.loads(path.read_text(encoding='utf-8'))=={'completed':10}
        assert json.loads(path.with_suffix('.json.tmp').read_text(encoding='utf-8'))=={'completed':11}
    else:
        runner.write(path,{'completed':11})
        assert json.loads(path.read_text(encoding='utf-8'))=={'completed':11}
    assert len(calls)==min(denials+1,8) and len(waits)==min(denials,7)


def prepare(db):
    for media_id, work in enumerate(('123456789', '223456789', '323456789', '423456789'), 1):
        queue_media_for_pixiv_metadata(db, {'id':media_id,'filename':f'{work}_p0.jpg','path':f'media/{media_id}.jpg'})
    db.commit()


def output(work):
    return json.dumps([[3,'url',{'id':int(work),'num':0,'user':{'id':42,'name':'name'},'tags':['tag']} ]])


def run(db, runner, **kwargs):
    return run_bounded_acquisition(db, ['123456789','223456789','323456789','423456789'],
        entrypoint=('gallery-dl',), authentication_passed=True, accept_local_credential_risk=True,
        env={}, command_runner=runner, sleeper=lambda _:None, **kwargs)


def test_exhausted_work_and_isolated_timeout_do_not_starve_healthy_tail(db):
    prepare(db)
    calls=[]; callbacks=[]
    def runner(command, **kwargs):
        work=command[-1].rsplit('/',1)[-1];calls.append(work)
        if work=='223456789':
            raise subprocess.TimeoutExpired('gallery-dl',1)
        return subprocess.CompletedProcess(command,0,output(work),'')
    result=run(db,runner,prior_attempt_counts={'123456789':3},result_callback=callbacks.append)
    assert calls==['223456789']*3+['323456789','423456789']
    assert len(callbacks)==len(result)==4
    assert not any(r.systemic_stop for r in result)
    assert result[0].error_class=='retry_budget_exhausted'
    assert result[1].attempt_count==3
    assert [r.page_count for r in result[2:]]==[1,1]


def test_global_rate_limit_stops_on_first_attempt(db):
    prepare(db);calls=[]
    def runner(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,1,'','429 rate limit')
    result=run(db,runner)
    assert len(calls)==1 and len(result)==1 and result[0].systemic_stop


def test_persistent_network_outage_is_bounded(db):
    prepare(db);calls=[]
    def runner(command,**kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired('gallery-dl',1)
    result=run(db,runner)
    assert len(calls)==9 and len(result)==3 and result[-1].systemic_stop


def test_zero_exit_json_network_outage_uses_same_global_pause(db):
    prepare(db);calls=[]
    def runner(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,0,json.dumps({'error':'connection timed out'}),'')
    result=run(db,runner)
    assert len(calls)==9 and len(result)==3 and result[-1].systemic_stop


def test_saved_metadata_replays_after_attempt_cap_and_excludes_unselected_media(db):
    prepare(db)
    queue_media_for_pixiv_metadata(db,{'id':5,'filename':'123456789_p0.jpg','path':'media/5.jpg'})
    db.commit()
    selected=db.query(SourceMetadataRecord).filter_by(media_id=1).one().id
    result=run(db,lambda *a,**k:(_ for _ in ()).throw(AssertionError('network replay')),
        prior_attempt_counts={'123456789':3},metadata_replay_outputs={'123456789':output('123456789')},
        attempted_record_ids_by_work={'123456789':[selected]})
    assert result[0].request_attempted is False and result[0].attempt_count==3
    assert result[0].page_count==1
    assert db.query(SourceMetadataRecord).filter_by(media_id=5).one().status=='metadata_pending'


def test_nested_series_identity_replays_locally_and_other_artwork_still_rejected(db):
    from app.services.pixiv_metadata_ingestion_service import parse_gallery_dl_stdout,PixivMetadataGateError
    import pytest
    prepare(db)
    record=db.query(SourceMetadataRecord).filter_by(media_id=1).one()
    record.status='provider_identity_mismatch';db.commit()
    raw={'id':123456789,'num':0,'page_count':1,'user':{'id':42,'name':'Artist'},
        'tags':['tag'],'series':{'id':98765,'title':'Series title'}}
    cached=json.dumps([[2,raw],[3,'url',raw]])
    result=run(db,lambda *a,**k:(_ for _ in ()).throw(AssertionError('identity replay network')),
        metadata_replay_outputs={'123456789':cached},attempted_record_ids_by_work={'123456789':[record.id]},
        allow_normalization_replay=True)
    assert result[0].state=='metadata_complete' and not result[0].request_attempted
    assert db.get(SourceMetadataRecord,record.id).status=='metadata_complete'
    with pytest.raises(PixivMetadataGateError,match='provider_identity_mismatch'):
        parse_gallery_dl_stdout(json.dumps([raw,{**raw,'id':999999999}]),'123456789')
