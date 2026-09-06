"""Transaction-local source revisions, including bulk SQL writers.

No payload parsing or content hashing is performed by search. Revisions are
advanced by the database when an input changes; bindings capture that revision.
"""

from sqlalchemy import JSON, Boolean, inspect, text

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


def _journal_withdrawal(connection, tables, *, deleting):
    """Record only trigger-owned changes in the existing guard, never re-sign it."""
    runs = 'blombooru_source_concept_product_runs'
    if runs not in tables:
        return ''
    pg = connection.dialect.name == 'postgresql'
    entries = []
    for table, owner in (
        ('blombooru_source_concept_signals', 'created_by_run_id'),
        ('blombooru_source_concept_evidence', 'run_id'),
        (BINDING, 'product_run_id'),
    ):
        if table not in tables or (table == BINDING and not deleting):
            continue
        definitions = inspect(connection).get_columns(table)
        columns = [c['name'] for c in definitions
                   if c['name'] not in {'created_at', 'updated_at', 'started_at', 'finished_at'}]
        before, after = [], []
        for column in columns:
            value = f'c.{column}'
            kind = next(c['type'] for c in definitions if c['name'] == column)
            if not pg and isinstance(kind, JSON):
                value = f'json({value})'
            if not pg and isinstance(kind, Boolean):
                value = f"json(CASE WHEN {value} THEN 'true' ELSE 'false' END)"
            before.extend((f"'{column}'", value))
            if column == 'source_metadata_record_id' and deleting:
                value = 'NULL'
            elif column == 'status':
                value = "CASE WHEN c.status IN ('rejected','superseded','invalid') THEN c.status ELSE 'superseded' END"
            after.extend((f"'{column}'", value))
        obj = 'jsonb_build_object' if pg else 'json_object'
        previous = f"{obj}({','.join(before)})"
        following = 'NULL' if table == BINDING else f"{obj}({','.join(after)})"
        item = f"{obj}('table','{table}','before',{previous},'after',{following})"
        owned = 'p.id' if table == BINDING else 'p.resolver_run_id'
        condition = '' if deleting else " AND c.status NOT IN ('rejected','superseded','invalid')"
        entries.append(f'SELECT {item} AS item FROM {table} c WHERE c.source_metadata_record_id=OLD.id AND c.{owner}={owned}{condition}')
    if not entries:
        return ''
    union = ' UNION ALL '.join(entries)
    if pg:
        merged = f"COALESCE(p.rollback_guard_json::jsonb->'source_invalidations','[]'::jsonb) || (SELECT jsonb_agg(item) FROM ({union}) changes)"
        value = f"jsonb_set(p.rollback_guard_json::jsonb,'{{source_invalidations}}',({merged}))"
    else:
        merged = f"SELECT value FROM json_each(COALESCE(json_extract(p.rollback_guard_json,'$.source_invalidations'),'[]')) UNION ALL SELECT item FROM ({union})"
        value = f"json_set(p.rollback_guard_json,'$.source_invalidations',json((SELECT json_group_array(json(value)) FROM ({merged}))))"
    statement = f"UPDATE {runs} AS p SET rollback_guard_json={value} WHERE p.status='active' AND EXISTS(SELECT 1 FROM ({union}) changes);"
    return statement if pg else statement.replace(f'{runs} AS p', runs).replace('p.', runs + '.')


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
    aliases = 'blombooru_source_name_alias_candidates'
    if aliases in tables:
        key = "evidence_payload->>'provider_record_key'" if connection.dialect.name == 'postgresql' else "json_extract(evidence_payload,'$.provider_record_key')"
        invalidate += f"\nUPDATE {aliases} SET status='superseded' WHERE {key}=OLD.provider_record_key AND (evidence_source=OLD.provider || '_provider_canonical' OR (OLD.provider='pixiv' AND evidence_source='pixiv_parenthetical_pattern')) AND status<>'superseded';"
    on_update = _journal_withdrawal(connection, tables, deleting=False) + invalidate
    on_delete = _journal_withdrawal(connection, tables, deleting=True) + invalidate
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
                    {on_delete}
                    RETURN OLD;
                END IF;
                IF {changed} THEN
                    NEW.binding_revision := OLD.binding_revision + 1;
                    {on_update}
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
                {on_update}
            END
        """))
        connection.execute(text('DROP TRIGGER IF EXISTS violet_source_binding_delete'))
        connection.execute(text(f"""
            CREATE TRIGGER violet_source_binding_delete BEFORE DELETE ON {SOURCE}
            BEGIN
                {on_delete}
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
