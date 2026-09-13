"""Legacy migrations must provide the model's owned-withdrawal FK lookups."""
import pytest
from sqlalchemy import create_engine,inspect,text
from app import database

LOOKUPS=(
    ('blombooru_source_concept_signal_links','concept_id'),
    ('blombooru_source_concept_evidence','signal_id'),
    ('blombooru_source_concept_signals','resolution_run_id'),
    ('blombooru_source_concept_fallback_search_index','source_signal_id'),
    ('blombooru_source_concept_fallback_search_index','neighbor_signal_id'),
)

def legacy_engine():
    engine=create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE blombooru_media (id INTEGER PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE blombooru_source_metadata_records (id INTEGER PRIMARY KEY)'))
        conn.execute(text('INSERT INTO blombooru_media VALUES (7)'))
    stale_inspector=inspect(engine)
    stale_inspector.get_table_names()
    database.migrate_add_source_concept_resolver_core(engine,stale_inspector)
    database.migrate_add_source_concept_fallback_search_index(engine,stale_inspector)
    return engine,stale_inspector

@pytest.mark.parametrize('table,column',LOOKUPS)
def test_old_schema_gets_leading_foreign_key_index(table,column):
    engine,stale_inspector=legacy_engine()
    try:
        database.migrate_add_source_concept_withdrawal_indexes(engine,stale_inspector)
        indexes=inspect(engine).get_indexes(table)
        assert any(row['column_names']==[column] and not row['unique'] for row in indexes)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT id FROM blombooru_media')).scalars().all()==[7]
    finally:engine.dispose()

def test_index_repair_is_idempotent_and_preserves_existing_schema():
    engine,stale_inspector=legacy_engine()
    try:
        before_tables=set(inspect(engine).get_table_names())
        database.migrate_add_source_concept_withdrawal_indexes(engine,stale_inspector)
        first={table:inspect(engine).get_indexes(table) for table,_ in LOOKUPS}
        database.migrate_add_source_concept_withdrawal_indexes(engine,inspect(engine))
        assert first=={table:inspect(engine).get_indexes(table) for table,_ in LOOKUPS}
        assert set(inspect(engine).get_table_names())==before_tables
        with engine.connect() as conn:
            assert conn.execute(text('SELECT id FROM blombooru_media')).scalars().all()==[7]
    finally:engine.dispose()

def test_index_repair_accepts_fresh_model_indexes_without_duplicates():
    from app.models import SourceConceptSignal,SourceConceptSignalLink,SourceConceptEvidence,SourceConceptFallbackSearchIndex
    engine=create_engine('sqlite://')
    try:
        models=(SourceConceptSignal,SourceConceptSignalLink,SourceConceptEvidence,SourceConceptFallbackSearchIndex)
        for model in models:model.__table__.create(engine)
        before={model.__tablename__:inspect(engine).get_indexes(model.__tablename__) for model in models}
        database.migrate_add_source_concept_withdrawal_indexes(engine,inspect(engine))
        assert before=={model.__tablename__:inspect(engine).get_indexes(model.__tablename__) for model in models}
    finally:engine.dispose()
