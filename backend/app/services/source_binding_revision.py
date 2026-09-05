"""Transaction-local source revisions, including bulk SQL writers.

No payload parsing or content hashing is performed by search. Revisions are
advanced by the database when an input changes; bindings capture that revision.
"""

from sqlalchemy import inspect, text

SOURCE = 'blombooru_source_metadata_records'
BINDING = 'blombooru_source_concept_product_media_bindings'
EPOCH = 'blombooru_source_binding_cache_epoch'
INPUT_COLUMNS = (
    'provider', 'provider_record_key', 'provider_run_id', 'media_id',
    'source_work_id', 'source_page_index', 'source_url', 'title', 'artist_name',
    'artist_id', 'confidence', 'similarity', 'metadata_kind', 'data_type_label',
    'raw_metadata_json', 'provenance', 'status',
)
CHILDREN = (
    'blombooru_source_tag_observations',
    'blombooru_source_name_observations',
    'blombooru_source_searchable_name_assertions',
    'blombooru_source_concept_evidence',
    'blombooru_source_concept_signals',
)


def install_source_revision_triggers(connection):
    tables = set(inspect(connection).get_table_names())
    if SOURCE not in tables or 'binding_revision' not in {
        c['name'] for c in inspect(connection).get_columns(SOURCE)
    }:
        return
    connection.execute(text(f'CREATE TABLE IF NOT EXISTS {EPOCH} (id INTEGER PRIMARY KEY CHECK(id=1), revision BIGINT NOT NULL)'))
    connection.execute(text(f'INSERT INTO {EPOCH}(id,revision) VALUES (1,0) ON CONFLICT(id) DO NOTHING'))
    advance = f'UPDATE {EPOCH} SET revision=revision+1 WHERE id=1;'
    invalidate = advance + '\n' + '\n'.join(
        f"UPDATE {table} SET status = 'superseded' "
        "WHERE source_metadata_record_id = OLD.id "
        "AND status NOT IN ('rejected', 'superseded', 'invalid');"
        for table in CHILDREN if table in tables
    )
    # Completed queue records can reuse an original record by stable key.
    # A correction/deletion must also withdraw that derived copy, while an
    # unrelated independent source record remains intact.
    if connection.dialect.name == 'postgresql':
        reuse_key = "provenance->>'source_provider_record_key'"
        legacy_reuse = "provenance->>'source_metadata_record_id'=OLD.id::text"
    else:
        reuse_key = "json_extract(provenance, '$.source_provider_record_key')"
        legacy_reuse = "json_extract(provenance, '$.source_metadata_record_id')=OLD.id"
    invalidate += f"\nUPDATE {SOURCE} SET status='superseded' WHERE id<>OLD.id " + \
        f"AND provider=OLD.provider AND ({reuse_key}=OLD.provider_record_key OR {legacy_reuse}) AND status<>'superseded';"
    if connection.dialect.name == 'postgresql':
        changed = ' OR '.join(
            f'NEW.{column}::text IS DISTINCT FROM OLD.{column}::text'
            for column in INPUT_COLUMNS
        )
        connection.execute(text(f"""
            CREATE OR REPLACE FUNCTION violet_source_binding_revision()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    {advance}
                    RETURN NEW;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    {invalidate}
                    RETURN OLD;
                END IF;
                IF {changed} THEN
                    NEW.binding_revision := OLD.binding_revision + 1;
                    {invalidate}
                ELSE
                    NEW.binding_revision := OLD.binding_revision;
                END IF;
                RETURN NEW;
            END $$
        """))
        connection.execute(text(f'DROP TRIGGER IF EXISTS violet_source_binding_revision ON {SOURCE}'))
        connection.execute(text(f"""
            CREATE TRIGGER violet_source_binding_revision BEFORE INSERT OR UPDATE OR DELETE ON {SOURCE}
            FOR EACH ROW EXECUTE FUNCTION violet_source_binding_revision()
        """))
    elif connection.dialect.name == 'sqlite':
        connection.execute(text('DROP TRIGGER IF EXISTS violet_source_binding_insert'))
        connection.execute(text(f'CREATE TRIGGER violet_source_binding_insert AFTER INSERT ON {SOURCE} BEGIN {advance} END'))
        changed = ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in INPUT_COLUMNS)
        connection.execute(text('DROP TRIGGER IF EXISTS violet_source_binding_revision'))
        connection.execute(text(f"""
            CREATE TRIGGER violet_source_binding_revision AFTER UPDATE ON {SOURCE}
            WHEN {changed}
            BEGIN
                UPDATE {SOURCE} SET binding_revision = OLD.binding_revision + 1 WHERE id = NEW.id;
                {invalidate}
            END
        """))
        connection.execute(text('DROP TRIGGER IF EXISTS violet_source_binding_delete'))
        connection.execute(text(f"""
            CREATE TRIGGER violet_source_binding_delete BEFORE DELETE ON {SOURCE}
            BEGIN
                {invalidate}
            END
        """))


def migrate_source_binding_revisions(engine, inspector=None):
    """Additive; historical bindings fail closed until a new verified apply."""
    with engine.begin() as connection:
        for table, column, default in (
            (SOURCE, 'binding_revision', 0), (BINDING, 'source_revision', -1),
        ):
            if table not in inspect(connection).get_table_names():
                continue
            if column not in {c['name'] for c in inspect(connection).get_columns(table)}:
                connection.execute(text(
                    f'ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT {default}'
                ))
        install_source_revision_triggers(connection)


def after_schema_create(target, connection, **kwargs):
    install_source_revision_triggers(connection)
