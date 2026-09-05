"""Executable PX3 product closure on task-owned databases, rederived by contract."""
from starlette.requests import Request
from contextlib import nullcontext
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from ..models import SourceConceptProductMediaBinding, SourceConceptProductRun
from ..routes import search, source_concepts
from .pixiv_product_binding_fixture import seed_media_binding_fixture
from .pixiv_metadata_projection_service import canonical_fingerprint


def prove_media_binding_search(workspace):
    from .pixiv_product_integration_service import (
        apply_pixiv_product_plan, build_clustering_from_source_metadata_session,
        rollback_pixiv_product_run, PixivProductIntegrationError,
    )
    from .source_concept_search_service import source_layer_search_path_media_ids
    from ..utils.cache import invalidate_source_concept_search_cache

    identities = []
    outcomes = []
    for offset in (0, 5000):
        path = workspace / f'px3-media-binding-{offset}.sqlite3'
        if path.exists():
            raise PixivProductIntegrationError('px3_binding_proof_database_exists')
        engine = create_engine(f'sqlite:///{path.as_posix()}', connect_args={'check_same_thread': False})
        @event.listens_for(engine, 'connect')
        def foreign_keys(conn, _):
            conn.execute('PRAGMA foreign_keys=ON')
        # Bind schema creation to the mapped models. Environment-safety tests
        # reload database.py, which replaces its otherwise empty Base object.
        SourceConceptProductRun.metadata.create_all(engine)
        try:
            with sessionmaker(bind=engine)() as db:
                seed_media_binding_fixture(db, offset)
                run = build_clustering_from_source_metadata_session(db)
                options = dict(scope_key='pixiv:binding-fixture', source_mode='existing_source_metadata')
                plan = apply_pixiv_product_plan(db, run, apply=False, **options)
                acceptance = dict(
                    accepted_selection_fingerprint=plan['selection_fingerprint'],
                    accepted_product_fingerprint=plan['product_result_fingerprint'],
                    accepted_binding_fingerprint=plan['media_binding']['local_binding_fingerprint'],
                )
                rejected = 0
                for field in acceptance:
                    try:
                        apply_pixiv_product_plan(db, run, apply=True, **options,
                                                 **{**acceptance, field: '0' * 64})
                    except PixivProductIntegrationError as exc:
                        if str(exc) != 'px3_accepted_plan_mismatch':
                            raise
                        rejected += 1
                dry_zero = db.query(SourceConceptProductRun).count() == 0 and db.query(SourceConceptProductMediaBinding).count() == 0
                applied = apply_pixiv_product_plan(db, run, apply=True, **options, **acceptance)
                count = db.query(SourceConceptProductMediaBinding).count()
                invalidate_source_concept_search_cache()
                # These read-only async endpoints execute synchronously. Drive
                # them without an event-loop self-pipe (Windows socketpair),
                # keeping the enclosing zero-network guard intact. Real HTTP
                # transport is covered separately by API and browser tests.
                def invoke(coroutine):
                    try:
                        coroutine.send(None)
                    except StopIteration as done:
                        return done.value
                    finally:
                        coroutine.close()
                    raise PixivProductIntegrationError('px3_endpoint_unexpected_async_io')
                with nullcontext():
                    def ids(term):
                        request = Request({'type': 'http', 'method': 'GET', 'path': '/api/search',
                                           'query_string': b'', 'headers': [], 'scheme': 'http',
                                           'server': ('testserver', 80)})
                        response = invoke(search.search_media(
                            request=request, q=term, rating=None, content_class=None,
                            source_assertion=None, source_tag=None, include_source_needs_review=False,
                            sort=None, order=None, page=1, limit=100, db=db,
                        ))
                        return sorted(item.id - offset for item in response['items'])
                    recalls = {term: ids(term) for term in (
                        'AsterHistorical', 'AsterCurrent', 'MoonGarden', 'MoonPetal',
                        'AsterHistorical MoonGarden', 'AsterHistorical OtherGarden',
                    )}
                    identity_recall = sorted(value - offset for value in source_layer_search_path_media_ids(db, 'AsterHistorical')['identity'])
                    def media_detail():
                        return invoke(source_concepts.get_media_source_concepts(offset + 1, db))['source_concepts']
                    detail = media_detail()
                    replay = apply_pixiv_product_plan(db, run, apply=True, **options, **acceptance)
                    replay_zero = db.query(SourceConceptProductMediaBinding).count() == count
                    rollback_pixiv_product_run(db, applied['run_key'])
                    revoked = ids('AsterHistorical MoonGarden') == [] and media_detail() == []
                    apply_pixiv_product_plan(db, run, apply=True, **options, **acceptance)
                    restored = ids('AsterHistorical MoonGarden') == [1, 2] and bool(media_detail())
                identities.append([plan[key] for key in ('px1_input_fingerprint', 'px2_business_projection_fingerprint', 'product_result_fingerprint')])
                outcomes.append({
                    'planned_media_count': plan['media_binding']['planned_media_binding_count'],
                    'planned_source_record_count': plan['media_binding']['planned_source_record_binding_count'],
                    'persisted_binding_count': count,
                    'dry_run_zero_writes': dry_zero,
                    'accepted_plan_mismatch_rejected_count': rejected,
                    'actual_search_results': recalls,
                    'sourceconcept_identity_recall': identity_recall,
                    'detail_alias_and_provenance': any('AsterHistorical' in str(row['aliases']) and 'pixiv' in row['providers'] for row in detail),
                    'replay_zero_duplicates': replay_zero and replay['idempotent_replay'],
                    'rollback_immediately_revoked_search_and_detail': revoked,
                    'reapply_restored_search_and_detail': restored,
                })
        finally:
            engine.dispose()
    expected = {
        'AsterHistorical': [1, 2, 3], 'AsterCurrent': [1, 2, 3, 4],
        'MoonGarden': [1, 2], 'MoonPetal': [1, 2],
        'AsterHistorical MoonGarden': [1, 2], 'AsterHistorical OtherGarden': [],
    }
    proof = {
        'schema_version': 'violet.scv2-px3-media-binding-proof.v1',
        'temporary_database_count': 2,
        'row_id_neutral_business_identity': identities[0] == identities[1],
        'outcomes': outcomes,
    }
    proof['passed'] = identities[0] == identities[1] and all(
        row['actual_search_results'] == expected and row['sourceconcept_identity_recall'] == [1, 2, 3]
        and row['planned_media_count'] == 4 and row['planned_source_record_count'] == 4
        and row['persisted_binding_count'] > 0 and row['accepted_plan_mismatch_rejected_count'] == 3
        and all(row[key] for key in ('dry_run_zero_writes', 'detail_alias_and_provenance',
                    'replay_zero_duplicates', 'rollback_immediately_revoked_search_and_detail', 'reapply_restored_search_and_detail'))
        for row in outcomes
    )
    proof['canonical_fingerprint'] = canonical_fingerprint(proof)
    if not proof['passed']:
        raise PixivProductIntegrationError('px3_media_binding_product_closure_failed')
    return proof
