import pytest
from app.models import SourceConceptProductRun,SourceConceptProductMediaBinding,SourceMetadataRecord,Media
from app.services import pixiv_product_integration_service as product
from test_production_pixiv_a1 import real_api,ids
from test_production_pixiv_a2 import scope_for,build,apply


def test_cumulative_projection_search_and_owned_rollback_preserve_independent_consumer(real_api):
    client,factory,independent,engine=real_api
    baseline={query:ids(client,query) for query in ('AsterCurrent','AsterHistorical','AsterCurrent MoonGarden','AsterCurrent -MoonGarden')}
    with factory() as db:
        independent_row=db.query(SourceConceptProductRun).filter_by(run_key=independent['run_key']).one()
        independent_fingerprint=product._rollback_ownership_fingerprint(db,independent_row)
        scope=scope_for(db)
        first=apply(db,build(db,['910000001']),scope)
        full=apply(db,build(db),scope)
        assert product._rollback_ownership_fingerprint(db,independent_row)==independent_fingerprint
        owned=db.query(SourceConceptProductRun).filter_by(run_key=full['run_key']).one()
        assert {row.media_id for row in db.query(SourceConceptProductMediaBinding).filter_by(product_run_id=owned.id)}=={1,2,3,4}
    assert ids(client,'AsterHistorical')=={1,2,3}
    assert ids(client,'AsterCurrent MoonGarden')=={1,2}
    assert ids(client,'AsterCurrent -MoonGarden')=={3,4}
    assert client.get('/api/media/1').status_code==200
    with factory() as db:
        product.rollback_pixiv_product_run(db,full['run_key'])
        assert db.query(SourceConceptProductRun).filter_by(run_key=independent['run_key']).one().status=='active'
        assert db.query(SourceConceptProductRun).filter_by(run_key=first['run_key']).one().status=='rolled_back'
        assert product._rollback_ownership_fingerprint(db,independent_row)==independent_fingerprint
    assert {query:ids(client,query) for query in baseline}==baseline


@pytest.mark.parametrize('change',['update','partial_update','delete','transaction_rollback'])
def test_full_scope_source_change_withdrawal_and_formal_replacement(real_api,change):
    client,factory,independent,engine=real_api
    with factory() as db:
        scope=scope_for(db);first=apply(db,build(db),scope)
        independent_key=independent['run_key']
    assert 1 in ids(client,'MoonGarden')
    with factory() as db:
        selected=db.get(SourceMetadataRecord,101)
        if change=='delete':db.delete(selected)
        else:
            selected.title='ReplacementGarden'
            if change=='update':
                for record in db.query(SourceMetadataRecord).filter_by(source_work_id=selected.source_work_id):
                    record.title='ReplacementGarden'
        if change=='transaction_rollback':db.rollback()
        else:db.commit()
    assert (1 in ids(client,'MoonGarden')) is (change=='transaction_rollback')
    with factory() as db:
        withdrawn=product.rollback_pixiv_product_run(db,first['run_key'])
        assert withdrawn['rolled_back']
        assert product.rollback_pixiv_product_run(db,first['run_key'])['idempotent_replay']
        assert db.query(SourceConceptProductRun).filter_by(run_key=independent_key).one().status=='active'
        replacement=apply(db,build(db),scope)
        assert apply(db,build(db),scope)['idempotent_replay']
        assert db.query(Media).count()==4
        owned=db.query(SourceConceptProductRun).filter_by(run_key=replacement['run_key']).one()
        media_ids={b.media_id for b in db.query(SourceConceptProductMediaBinding).filter_by(product_run_id=owned.id)}
        expected={'delete':{2,3,4},'partial_update':{3,4}}.get(change,{1,2,3,4})
        assert media_ids==expected
    if change=='update':assert 1 in ids(client,'ReplacementGarden')
    assert 3 in ids(client,'AsterHistorical')
