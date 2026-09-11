from app.models import SourceConceptProductRun,SourceConceptProductMediaBinding
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
